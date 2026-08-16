"""End-to-end integration tests for run_discovery().

Scanners are patched to return pre-built ScanResults so that these tests
exercise the orchestrator's composition logic (workload inference, cross-plane
linking, coverage validation, store persistence, and file output) without
requiring live cloud credentials or HTTP mocking.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from sherpa.core.interfaces import ScanResult
from sherpa.core.models import (
    NamingConvention,
    Pipeline,
    Repository,
    Resource,
    ScanConfig,
)
from sherpa.core.models.enums import DependencyPlane, DependencyType, IaCType, ResourceType
from sherpa.core.store import InventoryStore
from sherpa.orchestrator.discovery import run_discovery

ACCOUNT = "123456789012"
REGION = "us-east-1"


# ------------------------------------------------------------------ helpers


def _aws_config(**kwargs) -> ScanConfig:
    kwargs.setdefault("aws_accounts", [ACCOUNT])
    kwargs.setdefault("aws_regions", [REGION])
    return ScanConfig(**kwargs)


def _github_config(**kwargs) -> ScanConfig:
    return ScanConfig(github_org="acme", **kwargs)


def _full_config(**kwargs) -> ScanConfig:
    return ScanConfig(aws_accounts=[ACCOUNT], aws_regions=[REGION], github_org="acme", **kwargs)


def _resource(name: str, tags: dict | None = None, rid: str | None = None) -> Resource:
    return Resource(
        id=rid or f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{name}",
        resource_type=ResourceType.EC2_INSTANCE,
        region=REGION,
        account_id=ACCOUNT,
        name=name,
        tags=tags or {},
    )


def _sqs(name: str) -> Resource:
    return Resource(
        id=f"arn:aws:sqs:{REGION}:{ACCOUNT}:queue/{name}",
        resource_type=ResourceType.SQS_QUEUE,
        region=REGION,
        account_id=ACCOUNT,
        name=name,
    )


def _empty_cloud() -> ScanResult:
    return ScanResult(scanner_type="aws-cloud")


def _empty_code() -> ScanResult:
    return ScanResult(scanner_type="github-code")


def _empty_pipe() -> ScanResult:
    return ScanResult(scanner_type="github-actions-pipeline")


class _patch_scanners(ExitStack):
    """ExitStack that patches all three scanners with fixed ScanResults."""

    def __init__(
        self,
        cloud_result: ScanResult | None = None,
        code_result: ScanResult | None = None,
        pipeline_result: ScanResult | None = None,
    ) -> None:
        super().__init__()
        self.enter_context(
            patch(
                "sherpa.orchestrator.discovery.AwsCloudScanner.scan",
                new=AsyncMock(return_value=cloud_result or _empty_cloud()),
            )
        )
        self.enter_context(
            patch(
                "sherpa.orchestrator.discovery.GithubCodeScanner.scan",
                new=AsyncMock(return_value=code_result or _empty_code()),
            )
        )
        self.enter_context(
            patch(
                "sherpa.orchestrator.discovery.GithubActionsScanner.scan",
                new=AsyncMock(return_value=pipeline_result or _empty_pipe()),
            )
        )


# ------------------------------------------------------------------ snapshot lifecycle


class TestSnapshotLifecycle:
    async def test_snapshot_closed_after_run(self):
        with _patch_scanners():
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store)
        assert snapshot.is_closed
        assert snapshot.completed_at is not None

    async def test_snapshot_persisted_and_loadable(self):
        resources = [_resource("checkout-api", tags={"Application": "checkout"})]
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store)

        loaded = store.load_snapshot(snapshot.snapshot_id)
        assert loaded is not None
        assert loaded.snapshot_id == snapshot.snapshot_id
        assert len(loaded.resources) == 1
        assert loaded.resources[0].name == "checkout-api"

    async def test_snapshot_ids_listed_in_creation_order(self):
        with _patch_scanners():
            store = InventoryStore()
            ids = [(await run_discovery(_aws_config(), store)).snapshot_id for _ in range(3)]
        assert store.list_snapshots() == ids

    async def test_two_snapshots_are_independent(self):
        r1 = _resource("alpha")
        r2 = _resource("beta")
        store = InventoryStore()

        with _patch_scanners(cloud_result=ScanResult(scanner_type="aws-cloud", resources=[r1])):
            snap1 = await run_discovery(_aws_config(), store)
        with _patch_scanners(cloud_result=ScanResult(scanner_type="aws-cloud", resources=[r1, r2])):
            snap2 = await run_discovery(_aws_config(), store)

        loaded1 = store.load_snapshot(snap1.snapshot_id)
        loaded2 = store.load_snapshot(snap2.snapshot_id)
        assert len(loaded1.resources) == 1
        assert len(loaded2.resources) == 2


# ------------------------------------------------------------------ output files


class TestOutputFiles:
    async def test_json_and_report_written(self, tmp_path):
        with _patch_scanners():
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store, output_dir=tmp_path)

        assert (tmp_path / f"snapshot_{snapshot.snapshot_id}.json").exists()
        assert (tmp_path / f"report_{snapshot.snapshot_id}.md").exists()

    async def test_json_contains_required_keys(self, tmp_path):
        resources = [_sqs("orders")]
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store, output_dir=tmp_path)

        data = json.loads((tmp_path / f"snapshot_{snapshot.snapshot_id}.json").read_text())
        for key in (
            "snapshot_id",
            "resources",
            "workloads",
            "repositories",
            "pipelines",
            "coverage_gaps",
        ):
            assert key in data

    async def test_report_contains_summary_and_snapshot_id(self, tmp_path):
        with _patch_scanners():
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store, output_dir=tmp_path)

        report = (tmp_path / f"report_{snapshot.snapshot_id}.md").read_text()
        assert "## Summary" in report
        assert snapshot.snapshot_id in report

    async def test_report_lists_discovered_workloads(self, tmp_path):
        resources = [_resource("checkout-api", tags={"Application": "checkout"})]
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store, output_dir=tmp_path)

        report = (tmp_path / f"report_{snapshot.snapshot_id}.md").read_text()
        assert "checkout" in report


# ------------------------------------------------------------------ workload inference


class TestWorkloadInference:
    async def test_grouped_by_application_tag(self):
        resources = [
            _resource("checkout-api", tags={"Application": "checkout"}),
            _resource("checkout-worker", tags={"Application": "checkout"}),
            _resource("auth-service", tags={"Application": "auth"}),
        ]
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store)

        names = {w.name for w in snapshot.workloads}
        assert "checkout" in names
        assert "auth" in names
        checkout_wl = next(w for w in snapshot.workloads if w.name == "checkout")
        assert len(checkout_wl.resource_ids) == 2

    async def test_custom_naming_convention_team_tag(self):
        resources = [
            _resource("svc-1", tags={"Team": "platform"}),
            _resource("svc-2", tags={"Team": "payments"}),
        ]
        naming = NamingConvention(workload_tag_keys=["Team"])
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(naming_convention=naming), store)

        names = {w.name for w in snapshot.workloads}
        assert "platform" in names
        assert "payments" in names

    async def test_name_segment_fallback(self):
        resources = [_resource("payments-api"), _resource("payments-worker")]
        naming = NamingConvention(workload_tag_keys=[], name_separator="-", name_part="first")
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(naming_convention=naming), store)

        names = {w.name for w in snapshot.workloads}
        assert "payments" in names

    async def test_unassigned_resources_grouped_under_label(self):
        resources = [_resource("singleword")]
        naming = NamingConvention(workload_tag_keys=[], unassigned_label="orphan")
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(naming_convention=naming), store)

        assert any(w.name == "orphan" for w in snapshot.workloads)

    async def test_inferred_from_field_populated(self):
        resources = [_resource("checkout-api", tags={"Application": "checkout"})]
        with _patch_scanners(
            cloud_result=ScanResult(scanner_type="aws-cloud", resources=resources)
        ):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store)

        wl = next(w for w in snapshot.workloads if w.name == "checkout")
        assert wl.inferred_from == "tag"


# ------------------------------------------------------------------ GitHub-only


class TestGithubOnlyDiscovery:
    async def test_github_only_scan_produces_repos(self):
        repos = [
            Repository(id=f"github.com/acme/svc-{i}", url=f"https://github.com/acme/svc-{i}")
            for i in range(3)
        ]
        code_result = ScanResult(scanner_type="github-code", repositories=repos)
        with _patch_scanners(code_result=code_result):
            store = InventoryStore()
            snapshot = await run_discovery(_github_config(), store)

        assert len(snapshot.repositories) == 3
        assert snapshot.resource_count == 0

    async def test_pipelines_stored_in_snapshot(self):
        pipe = Pipeline(
            id="github.com/acme/svc/.github/workflows/deploy.yml",
            pipeline_type="github_actions",
            repo_id="github.com/acme/svc",
        )
        pipe_result = ScanResult(scanner_type="github-actions-pipeline", pipelines=[pipe])
        with _patch_scanners(pipeline_result=pipe_result):
            store = InventoryStore()
            snapshot = await run_discovery(_github_config(), store)

        assert len(snapshot.pipelines) == 1
        assert snapshot.pipelines[0].pipeline_type == "github_actions"


# ------------------------------------------------------------------ cross-plane linking


class TestCrossPlaneLinks:
    async def test_repo_declaring_resource_gets_dependency_edge(self):
        queue_id = f"arn:aws:sqs:{REGION}:{ACCOUNT}:queue/events"
        queue = _sqs("events")
        queue = queue.model_copy(update={"id": queue_id})

        repo = Repository(
            id="github.com/acme/infra",
            url="https://github.com/acme/infra",
            iac_type=IaCType.TERRAFORM,
            declared_resource_ids=[queue_id],
        )

        cloud_result = ScanResult(scanner_type="aws-cloud", resources=[queue])
        code_result = ScanResult(scanner_type="github-code", repositories=[repo])

        with _patch_scanners(cloud_result=cloud_result, code_result=code_result):
            store = InventoryStore()
            snapshot = await run_discovery(_full_config(), store)

        q = next(r for r in snapshot.resources if "events" in r.id)
        cross_deps = [d for d in q.dependencies if d.plane == DependencyPlane.CROSS]
        assert len(cross_deps) >= 1
        assert any("acme/infra" in d.source_id for d in cross_deps)

    async def test_pipeline_deploying_to_account_gets_linked(self):
        resource = _resource("checkout-api")
        pipe = Pipeline(
            id="github.com/acme/infra/.github/workflows/deploy.yml",
            pipeline_type="github_actions",
            repo_id="github.com/acme/infra",
            deploys_to_accounts=[ACCOUNT],
        )

        cloud_result = ScanResult(scanner_type="aws-cloud", resources=[resource])
        pipe_result = ScanResult(scanner_type="github-actions-pipeline", pipelines=[pipe])

        with _patch_scanners(cloud_result=cloud_result, pipeline_result=pipe_result):
            store = InventoryStore()
            snapshot = await run_discovery(_full_config(), store)

        r = snapshot.resources[0]
        deploy_deps = [d for d in r.dependencies if d.dependency_type == DependencyType.DEPLOYS_TO]
        assert len(deploy_deps) >= 1


# ------------------------------------------------------------------ coverage validation


class TestCoverageValidation:
    async def test_scanner_errors_surfaced_as_coverage_gap(self):
        cloud_result = ScanResult(
            scanner_type="aws-cloud",
            resources=[],
            errors=["AccessDenied: ec2:DescribeInstances"],
        )
        with _patch_scanners(cloud_result=cloud_result):
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(), store)

        assert any(gap.severity == "error" for gap in snapshot.coverage_gaps)

    async def test_snapshot_completes_cleanly_with_empty_estate(self):
        with _patch_scanners():
            store = InventoryStore()
            snapshot = await run_discovery(_aws_config(aws_regions=[REGION, "us-west-2"]), store)
        assert snapshot.is_closed
