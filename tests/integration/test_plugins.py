"""Scanner plugin discovery (issue #13).

Plugins are found through the `sherpa.scanners` entry-point group. A test-only plugin
package (tests/fixtures/dummy_plugin) is installed in CI to prove a connector can be added
without touching Sherpa's core; misbehaving plugins are injected as entry points.
"""

from __future__ import annotations

import importlib.metadata
from importlib.metadata import EntryPoint
from unittest.mock import AsyncMock, patch

import pytest

from sherpa.core.interfaces import ENTRY_POINT_GROUP, ScanResult, load_scanners
from sherpa.core.models import ScanConfig
from sherpa.core.store import InventoryStore
from sherpa.orchestrator.discovery import run_discovery

BUILTINS = {"aws-cloud", "github-code", "github-actions-pipeline"}
DUMMY_ORG = "sherpa-dummy-org"


def _ep(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)


def _installed_eps() -> list[EntryPoint]:
    return list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))


def _empty_github_scans():
    """Built-in GitHub scanners also apply to a github_org; keep them off the network."""
    return (
        patch(
            "sherpa.scanners.code.github.scanner.GithubCodeScanner.scan",
            new=AsyncMock(return_value=ScanResult(scanner_type="github-code")),
        ),
        patch(
            "sherpa.scanners.pipeline.github_actions.scanner.GithubActionsScanner.scan",
            new=AsyncMock(return_value=ScanResult(scanner_type="github-actions-pipeline")),
        ),
    )


class TestPackaging:
    def test_builtin_scanners_are_registered_entry_points(self):
        # Regression: pyproject used `entry_points` (ignored by the build) instead of
        # PEP 621's `entry-points`, so the built-ins were never registered at all.
        names = {ep.name for ep in _installed_eps()}
        assert names >= BUILTINS

    def test_loader_finds_builtins_without_errors(self):
        loaded = load_scanners()
        assert {s.scanner_type for s in loaded.scanners} >= BUILTINS
        assert loaded.errors == []


class TestThirdPartyPlugin:
    def test_dummy_plugin_is_installed(self):
        try:
            importlib.metadata.distribution("sherpa-dummy-plugin")
        except importlib.metadata.PackageNotFoundError:
            pytest.fail("Install the test plugin: pip install -e tests/fixtures/dummy_plugin")

    async def test_dummy_plugin_runs_end_to_end_without_core_changes(self):
        code_patch, pipe_patch = _empty_github_scans()
        with code_patch, pipe_patch:
            snapshot = await run_discovery(ScanConfig(github_org=DUMMY_ORG), InventoryStore())

        assert [r.id for r in snapshot.repositories] == [f"dummy.example/{DUMMY_ORG}/plugin-repo"]
        assert snapshot.errors == []

    async def test_dummy_plugin_does_not_apply_to_other_runs(self):
        code_patch, pipe_patch = _empty_github_scans()
        with code_patch, pipe_patch:
            snapshot = await run_discovery(ScanConfig(github_org="acme"), InventoryStore())
        assert snapshot.repositories == []


class TestLoaderRobustness:
    def test_misbehaving_plugins_are_reported_not_raised(self):
        loaded = load_scanners(
            [
                *_installed_eps(),
                _ep("broken", "tests.fixtures.broken_plugin:Anything"),
                _ep("not-a-plugin", "tests.fixtures.misc_plugins:NotAPlugin"),
                _ep("bad-type", "tests.fixtures.misc_plugins:BadTypeScanner"),
                _ep("zz-duplicate", "tests.fixtures.misc_plugins:DuplicateAwsScanner"),
            ]
        )

        assert {s.scanner_type for s in loaded.scanners} >= BUILTINS
        errors = "\n".join(loaded.errors)
        assert len(loaded.errors) == 4
        assert "'broken'" in errors and "failed to load" in errors and "ImportError" in errors
        assert "'not-a-plugin'" in errors and "not a ScannerPlugin" in errors
        assert "'bad-type'" in errors and "invalid scanner_type" in errors
        assert "'zz-duplicate'" in errors and "already provided by 'aws-cloud'" in errors
        aws = [s for s in loaded.scanners if s.scanner_type == "aws-cloud"]
        assert len(aws) == 1 and type(aws[0]).__module__.startswith("sherpa.scanners")

    def test_identical_entry_points_count_once(self):
        ep = _ep("aws-cloud", "sherpa.scanners.cloud.aws.scanner:AwsCloudScanner")
        loaded = load_scanners([ep, ep])
        assert [s.scanner_type for s in loaded.scanners] == ["aws-cloud"]
        assert loaded.errors == []

    def test_order_is_by_stage_then_type(self):
        loaded = load_scanners(list(reversed(_installed_eps())))
        keys = [(s.run_stage, s.scanner_type) for s in loaded.scanners]
        assert keys == sorted(keys)
        assert loaded.scanners[0].scanner_type == "aws-cloud"  # cloud first


class TestOrchestratorResilience:
    async def test_broken_plugin_is_reported_and_run_continues(self):
        eps = [*_installed_eps(), _ep("broken", "tests.fixtures.broken_plugin:Anything")]
        code_patch, pipe_patch = _empty_github_scans()
        with (
            patch("importlib.metadata.entry_points", return_value=eps),
            code_patch,
            pipe_patch,
        ):
            snapshot = await run_discovery(ScanConfig(github_org=DUMMY_ORG), InventoryStore())

        assert len(snapshot.repositories) == 1  # the healthy plugin still ran
        assert any("'broken'" in e for e in snapshot.errors)
        assert any(
            g.severity == "error" and "could not be loaded" in g.description
            for g in snapshot.coverage_gaps
        )

    async def test_crashing_scanner_becomes_error_and_gap(self):
        from tests.fixtures.misc_plugins import CrashingScanner

        snapshot = await run_discovery(
            ScanConfig(github_org="acme"), InventoryStore(), scanners=[CrashingScanner()]
        )
        assert any("crashing-code: scan failed" in e for e in snapshot.errors)
        assert any("Scanner crashing-code failed" in g.description for g in snapshot.coverage_gaps)

    async def test_scanner_coverage_gaps_reach_the_snapshot(self):
        # Regression: the orchestrator used to drop scanner-reported gaps (e.g. "account X
        # was not scanned"); only errors survived.
        from sherpa.core.models import CoverageGap
        from sherpa.scanners.cloud.aws.scanner import AwsCloudScanner

        class GapScanner(AwsCloudScanner):
            async def scan(self, config):
                return ScanResult(
                    scanner_type="aws-cloud",
                    coverage_gaps=[CoverageGap(description="specific gap", severity="error")],
                )

        snapshot = await run_discovery(
            ScanConfig(aws_accounts=["111111111111"], aws_regions=["us-east-1"]),
            InventoryStore(),
            scanners=[GapScanner()],
        )
        assert any(g.description == "specific gap" for g in snapshot.coverage_gaps)
