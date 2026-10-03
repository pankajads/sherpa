"""Contract every scanner plugin must satisfy (issue #13).

Runs over every installed `sherpa.scanners` plugin. Structural checks apply to any plugin,
including third-party ones. Behavioural checks (sorted output, no credentials in results,
errors always accompanied by a coverage gap) run for plugins with a scenario below; add one
when you add a scanner.
"""

from __future__ import annotations

import re
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from github import GithubException

from sherpa.core.interfaces import ScannerPlane, ScannerPlugin, ScanResult, load_scanners
from sherpa.core.models import ScanConfig
from tests.golden.fake_estate import ACCOUNT, ORG, REGIONS, FakeAwsSession, fake_github_client

SCANNERS = load_scanners().scanners
TOKEN = "ghp" + "_" + "C0ntractSentinelToken0123456789abcdefg"  # built at runtime


def _ids(scanners: list[ScannerPlugin]) -> list[str]:
    return [s.scanner_type for s in scanners]


# ------------------------------------------------------------------ scenarios


class _FailingAwsSession(FakeAwsSession):
    """The fake estate, except every Lambda call is denied."""

    def client(self, service, region_name="us-east-1", **kw):
        client = super().client(service, region_name, **kw)
        if service == "lambda":
            client.get_paginator = MagicMock(side_effect=PermissionError("AccessDenied: lambda"))
        return client


def _github_down() -> MagicMock:
    gh = MagicMock()
    gh.get_organization.side_effect = GithubException(404, "Not Found", None)
    return gh


def _aws(session):
    return [patch("sherpa.scanners.cloud.aws.scanner.aioboto3.Session", return_value=session)]


def _github(module: str, client):
    return [patch(f"sherpa.scanners.{module}.Github", return_value=client)]


_AWS_CONFIG = {"aws_accounts": [ACCOUNT], "aws_regions": REGIONS}
_GH_CONFIG = {"github_org": ORG, "github_token": TOKEN}

# scanner_type -> {scenario: (config kwargs, patches factory)}
SCENARIOS = {
    "aws-cloud": {
        "ok": (_AWS_CONFIG, lambda: _aws(FakeAwsSession())),
        "failing": (_AWS_CONFIG, lambda: _aws(_FailingAwsSession())),
    },
    "github-code": {
        "ok": (_GH_CONFIG, lambda: _github("code.github.scanner", fake_github_client())),
        "failing": (_GH_CONFIG, lambda: _github("code.github.scanner", _github_down())),
    },
    "github-actions-pipeline": {
        "ok": (
            _GH_CONFIG,
            lambda: _github("pipeline.github_actions.scanner", fake_github_client()),
        ),
        "failing": (
            _GH_CONFIG,
            lambda: _github("pipeline.github_actions.scanner", _github_down()),
        ),
    },
    "dummy-code": {"ok": ({"github_org": "sherpa-dummy-org"}, lambda: [])},
}

BEHAVIOURAL = [
    pytest.param(s, name, id=f"{s.scanner_type}-{name}")
    for s in SCANNERS
    for name in SCENARIOS.get(s.scanner_type, {})
]


async def _run(scanner: ScannerPlugin, scenario: str) -> tuple[ScanConfig, ScanResult]:
    kwargs, patches = SCENARIOS[scanner.scanner_type][scenario]
    config = ScanConfig(**kwargs)
    with ExitStack() as stack:
        for p in patches():
            stack.enter_context(p)
        return config, await scanner.scan(config)


# ------------------------------------------------------------------ structural


def test_builtin_scanners_have_contract_scenarios():
    missing = {"aws-cloud", "github-code", "github-actions-pipeline"} - set(SCENARIOS)
    assert not missing


@pytest.mark.parametrize("scanner", SCANNERS, ids=_ids(SCANNERS))
class TestStructure:
    def test_scanner_type_is_kebab_case(self, scanner):
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", scanner.scanner_type)

    def test_plane_and_stage(self, scanner):
        assert ScannerPlane(scanner.plane) in set(ScannerPlane)
        assert isinstance(scanner.run_stage, int)

    def test_applies_to_returns_bool(self, scanner):
        for config in (
            ScanConfig(aws_accounts=[ACCOUNT], aws_regions=["us-east-1"]),
            ScanConfig(github_org="some-org"),
        ):
            assert isinstance(scanner.applies_to(config), bool)


def test_scanner_types_are_unique():
    assert len(_ids(SCANNERS)) == len(set(_ids(SCANNERS)))


# ------------------------------------------------------------------ behavioural


@pytest.mark.parametrize(("scanner", "scenario"), BEHAVIOURAL)
class TestBehaviour:
    async def test_applies_and_validates_for_its_scenario(self, scanner, scenario):
        kwargs, _ = SCENARIOS[scanner.scanner_type][scenario]
        config = ScanConfig(**kwargs)
        assert scanner.applies_to(config)
        assert (await scanner.validate_config(config)).valid

    async def test_result_identifies_scanner_and_lists_are_sorted(self, scanner, scenario):
        _, result = await _run(scanner, scenario)
        assert result.scanner_type == scanner.scanner_type
        for items in (result.resources, result.repositories, result.pipelines):
            ids = [i.id for i in items]
            assert ids == sorted(ids)

    async def test_no_credentials_in_result(self, scanner, scenario):
        _, result = await _run(scanner, scenario)
        assert TOKEN not in result.model_dump_json()

    async def test_every_error_comes_with_a_coverage_gap(self, scanner, scenario):
        _, result = await _run(scanner, scenario)
        if scenario == "failing":
            assert result.errors, "failing scenario should produce errors"
        if result.errors:
            assert result.coverage_gaps, f"{scanner.scanner_type}: errors without a gap"
