"""Property tests: orchestrator outputs don't depend on input order (issue #10)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from sherpa.core.models import (
    CoverageGap,
    DeployTarget,
    InventorySnapshot,
    Pipeline,
    PipelineStage,
    Repository,
    Resource,
    ResourceDependency,
    ScanConfig,
)
from sherpa.core.models.enums import DependencyPlane, DependencyType, ResourceType
from sherpa.orchestrator.coverage_validator import validate_coverage
from sherpa.orchestrator.cross_plane_linker import link_cross_plane
from sherpa.orchestrator.workload_inferrer import infer_workloads

ACCOUNTS = ["111111111111", "222222222222"]
REGIONS = ["us-east-1", "eu-west-1"]
WORDS = ["payments", "checkout", "auth", "api", "db", "worker"]

_names = st.lists(st.sampled_from(WORDS), min_size=1, max_size=3).map("-".join)
_tags = st.dictionaries(
    st.sampled_from(["Application", "Team", "env", "Name"]), st.sampled_from(WORDS), max_size=3
)


@st.composite
def inventories(draw):
    n = draw(st.integers(min_value=1, max_value=12))
    ids = [f"arn:aws:ec2:{REGIONS[i % 2]}:{ACCOUNTS[i % 2]}:instance/i-{i:04d}" for i in range(n)]
    resources = []
    for i, rid in enumerate(ids):
        targets = draw(st.lists(st.sampled_from(ids), max_size=3))
        deps = [
            ResourceDependency(
                source_id=rid,
                target_id=t,
                dependency_type=draw(st.sampled_from(list(DependencyType))),
                plane=DependencyPlane.CLOUD,
            )
            for t in targets
        ]
        resources.append(
            Resource(
                id=rid,
                resource_type=ResourceType.EC2_INSTANCE,
                region=REGIONS[i % 2],
                account_id=ACCOUNTS[i % 2],
                name=draw(_names),
                tags=draw(_tags),
                dependencies=deps,
            )
        )
    repos = [
        Repository(
            id=f"github.com/acme/repo-{j}",
            url=f"https://github.com/acme/repo-{j}.git",
            declared_resource_ids=draw(st.lists(st.sampled_from(ids), max_size=4)),
        )
        for j in range(draw(st.integers(min_value=0, max_value=4)))
    ]
    pipelines = [
        Pipeline(
            id=f"github.com/acme/repo-{j}/.github/workflows/deploy.yml",
            pipeline_type="github_actions",
            repo_id=f"github.com/acme/repo-{j}",
            deploys_to_accounts=draw(st.lists(st.sampled_from(ACCOUNTS), max_size=2, unique=True)),
            stages=[
                PipelineStage(
                    name=job,
                    deploy_targets=[
                        # By ARN, or by a name that may match several resources (ambiguity).
                        DeployTarget(
                            resource_type=ResourceType.EC2_INSTANCE,
                            value=draw(st.sampled_from([*ids, *WORDS])),
                            method="test",
                        )
                        for _ in range(draw(st.integers(min_value=0, max_value=3)))
                    ],
                )
                for job in draw(st.lists(st.sampled_from(["build", "deploy"]), unique=True))
            ],
        )
        for j in range(draw(st.integers(min_value=0, max_value=3)))
    ]
    return resources, repos, pipelines


def _shuffled(draw_fn, items):
    return draw_fn(st.permutations(items))


@settings(max_examples=150, deadline=None)
@given(inv=inventories(), data=st.data())
def test_infer_workloads_is_order_independent(inv, data):
    resources, repos, _ = inv
    expected = infer_workloads(resources, repos)
    actual = infer_workloads(_shuffled(data.draw, resources), _shuffled(data.draw, repos))
    assert actual == expected


@settings(max_examples=150, deadline=None)
@given(inv=inventories(), data=st.data())
def test_link_cross_plane_is_order_independent(inv, data):
    resources, repos, pipelines = inv
    expected = link_cross_plane(resources, repos, pipelines)
    actual = link_cross_plane(
        _shuffled(data.draw, resources),
        _shuffled(data.draw, repos),
        _shuffled(data.draw, pipelines),
    )
    assert actual == expected


@settings(max_examples=100, deadline=None)
@given(
    inv=inventories(),
    errors=st.lists(st.sampled_from(["AccessDenied: ec2", "Throttling: s3", "boom"]), max_size=4),
    data=st.data(),
)
def test_validate_coverage_is_order_independent(inv, errors, data):
    resources, _, _ = inv
    config = ScanConfig(aws_accounts=ACCOUNTS, aws_regions=REGIONS + ["ap-south-1"])
    expected = validate_coverage(config, resources, errors)
    actual = validate_coverage(
        config, _shuffled(data.draw, resources), _shuffled(data.draw, errors)
    )
    assert actual == expected


@settings(max_examples=100, deadline=None)
@given(inv=inventories(), data=st.data())
def test_snapshot_serialisation_is_order_independent(inv, data):
    resources, repos, pipelines = inv
    config = ScanConfig(aws_accounts=ACCOUNTS, aws_regions=REGIONS)
    gaps = [
        CoverageGap(description="b", severity="info", affected_regions=["us-east-1", "eu-west-1"]),
        CoverageGap(description="a", severity="error"),
    ]

    def build(res, rep, pip, gp, errs):
        return InventorySnapshot(
            snapshot_id="fixed",
            started_at="2026-01-01T00:00:00Z",
            config=config,
            resources=res,
            repositories=rep,
            pipelines=pip,
            workloads=infer_workloads(res, rep),
            coverage_gaps=gp,
            errors=errs,
        ).to_canonical_json()

    expected = build(resources, repos, pipelines, gaps, ["x", "y"])
    actual = build(
        _shuffled(data.draw, resources),
        _shuffled(data.draw, repos),
        _shuffled(data.draw, pipelines),
        _shuffled(data.draw, gaps),
        ["y", "x"],
    )
    assert actual == expected
