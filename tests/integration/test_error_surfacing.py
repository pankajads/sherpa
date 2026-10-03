"""Every caught error becomes a structured coverage gap (issue #8: surface, don't swallow).

Faults are injected into the fake estate (tests/golden/fake_estate.py) one at a time, and each
must produce a gap naming the scanner, the scope, the error class and, for AWS, the service
and regions. Expected absence (a repo without workflows) must produce no gap.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from github import GithubException

from sherpa.core.models import CoverageGap, ErrorClass, InventorySnapshot, ScanConfig
from sherpa.orchestrator.discovery import _render_report
from sherpa.scanners.cloud.aws.scanner import AwsCloudScanner
from sherpa.scanners.code.github.scanner import GithubCodeScanner
from sherpa.scanners.pipeline.github_actions.scanner import GithubActionsScanner
from tests.golden.fake_estate import (
    ACCOUNT,
    ORG,
    REGIONS,
    FakeAwsSession,
    fake_github_client,
)

SCOPE = f"aws-account:{ACCOUNT}"


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "injected"}}, "Operation")


ERRORS = [
    pytest.param(
        lambda: _client_error("AccessDeniedException"), ErrorClass.ACCESS_DENIED, id="denied"
    ),
    pytest.param(
        lambda: _client_error("UnauthorizedOperation"), ErrorClass.ACCESS_DENIED, id="unauthorized"
    ),
    pytest.param(
        lambda: _client_error("ThrottlingException"), ErrorClass.THROTTLED, id="throttled"
    ),
    pytest.param(
        lambda: _client_error("ServiceUnavailable"), ErrorClass.UNAVAILABLE, id="unavailable"
    ),
    pytest.param(lambda: RuntimeError("boom"), ErrorClass.OTHER, id="other"),
]


# ------------------------------------------------------------------ AWS


class _FailingClient:
    """Every operation of one service raises the injected error."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_paginator(self, _operation):
        raise self._exc

    async def list_buckets(self):
        raise self._exc


class _Session(FakeAwsSession):
    def __init__(self, failing_service: str | None = None, exc: Exception | None = None, **ops):
        super().__init__()
        self._failing, self._exc, self._ops = failing_service, exc, ops

    def client(self, service, region_name="us-east-1", **kw):
        if service == self._failing:
            return _FailingClient(self._exc)
        client = super().client(service, region_name, **kw)
        for name, fn in self._ops.items():
            setattr(client, name, fn)
        return client


async def _aws_scan(session: FakeAwsSession):
    config = ScanConfig(aws_accounts=[ACCOUNT], aws_regions=REGIONS)
    with patch("sherpa.scanners.cloud.aws.scanner.aioboto3.Session", return_value=session):
        return await AwsCloudScanner().scan(config)


@pytest.mark.parametrize(
    ("service", "regions"),
    [
        ("ec2", REGIONS),
        ("lambda", REGIONS),
        ("s3", REGIONS),
        ("rds", REGIONS),
        ("dynamodb", REGIONS),
        ("sqs", REGIONS),
        ("sns", REGIONS),
        ("ecs", REGIONS),
        ("iam", ["us-east-1"]),  # global service: scanned once per account
    ],
)
@pytest.mark.parametrize(("make_exc", "error_class"), ERRORS)
async def test_failing_collector_becomes_one_structured_gap(
    service, regions, make_exc, error_class
):
    result = await _aws_scan(_Session(service, make_exc()))

    gaps = [g for g in result.coverage_gaps if g.affected_services == [service]]
    assert len(gaps) == 1, result.coverage_gaps  # grouped across regions
    gap = gaps[0]
    assert gap.scanner == "aws-cloud" and gap.scope == SCOPE
    assert gap.error_class == error_class
    assert gap.affected_regions == sorted(regions)
    assert result.errors  # the raw errors are still kept for debugging


async def test_lambda_event_source_failure_keeps_function_and_reports_gap():
    async def denied(**_kw):
        raise _client_error("AccessDeniedException")

    result = await _aws_scan(_Session(list_event_source_mappings=denied))

    assert any(r.name == "payments-processor" for r in result.resources)
    (gap,) = [g for g in result.coverage_gaps if "Event source mappings" in g.description]
    assert gap.error_class == ErrorClass.ACCESS_DENIED
    assert gap.affected_services == ["lambda"] and gap.affected_regions == ["us-east-1"]


async def test_s3_location_failure_never_guesses_region_and_reports_once():
    async def denied(**_kw):
        raise _client_error("AccessDenied")

    result = await _aws_scan(_Session(get_bucket_location=denied))

    buckets = {r.name: r.region for r in result.resources if r.id.startswith("arn:aws:s3")}
    assert buckets == {
        "checkout-assets": "unknown",
        "payments-receipts": "unknown",
        "reporting-exports": "unknown",
    }
    s3_gaps = [g for g in result.coverage_gaps if g.affected_services == ["s3"]]
    assert len(s3_gaps) == 1  # identical across both regional calls: deduplicated
    assert s3_gaps[0].error_class == ErrorClass.ACCESS_DENIED


async def test_healthy_estate_has_no_gaps():
    result = await _aws_scan(FakeAwsSession())
    assert result.coverage_gaps == [] and result.errors == []


# ------------------------------------------------------------------ GitHub


def _repo(gh: MagicMock, name: str) -> MagicMock:
    return next(r for r in gh.get_organization().get_repos() if r.full_name == f"{ORG}/{name}")


async def _gh_scan(scanner_cls, gh: MagicMock):
    module = scanner_cls.__module__
    with patch(f"{module}.Github", return_value=gh):
        return await scanner_cls().scan(ScanConfig(github_org=ORG))


def _org_down(status: int, data=None) -> MagicMock:
    gh = MagicMock()
    gh.get_organization.side_effect = GithubException(status, data or {"message": "x"}, None)
    return gh


@pytest.mark.parametrize("scanner_cls", [GithubCodeScanner, GithubActionsScanner])
@pytest.mark.parametrize(
    ("status", "data", "error_class"),
    [
        (403, {"message": "Must have admin rights"}, ErrorClass.ACCESS_DENIED),
        (403, {"message": "You have exceeded a secondary rate limit"}, ErrorClass.THROTTLED),
        (404, {"message": "Not Found"}, ErrorClass.NOT_FOUND),
        (502, {"message": "Bad Gateway"}, ErrorClass.UNAVAILABLE),
    ],
)
async def test_org_failure_is_classified(scanner_cls, status, data, error_class):
    result = await _gh_scan(scanner_cls, _org_down(status, data))
    (gap,) = result.coverage_gaps
    assert gap.error_class == error_class and gap.severity == "error"
    assert gap.scope == f"github.com/{ORG}"


async def test_unreadable_tree_is_reported_not_shown_as_empty_repo():
    gh = fake_github_client()
    _repo(gh, "payments-service").get_git_tree.side_effect = GithubException(500, {}, None)

    result = await _gh_scan(GithubCodeScanner, gh)

    assert any(r.id == f"github.com/{ORG}/payments-service" for r in result.repositories)
    (gap,) = result.coverage_gaps
    assert "File list" in gap.description and gap.error_class == ErrorClass.UNAVAILABLE
    assert gap.scope == f"github.com/{ORG}/payments-service"


async def test_unreadable_file_is_reported():
    gh = fake_github_client()
    repo = _repo(gh, "payments-service")
    original = repo.get_contents.side_effect

    def contents(path, ref=None):
        if path == "requirements.txt":
            raise GithubException(403, {"message": "Forbidden"}, None)
        return original(path, ref)

    repo.get_contents.side_effect = contents
    result = await _gh_scan(GithubCodeScanner, gh)

    (gap,) = result.coverage_gaps
    assert "1 file(s)" in gap.description and "requirements.txt" in gap.description
    assert gap.error_class == ErrorClass.ACCESS_DENIED


async def test_malformed_package_json_is_invalid_content():
    gh = fake_github_client()
    repo = _repo(gh, "checkout-web")
    original = repo.get_contents.side_effect

    def contents(path, ref=None):
        if path == "package.json":
            f = MagicMock()
            f.decoded_content = b'{"dependencies": '  # truncated JSON
            return f
        return original(path, ref)

    repo.get_contents.side_effect = contents
    result = await _gh_scan(GithubCodeScanner, gh)

    (gap,) = result.coverage_gaps
    assert gap.error_class == ErrorClass.INVALID_CONTENT and "package.json" in gap.description


async def test_malformed_workflow_is_reported_and_others_still_parse():
    gh = fake_github_client()
    repo = _repo(gh, "payments-service")
    original = repo.get_contents.side_effect

    def contents(path, ref=None):
        if path == ".github/workflows":
            good = original(path, ref)
            bad = MagicMock()
            bad.name, bad.decoded_content = "broken.yml", b"jobs: [unclosed"
            return [*good, bad]
        return original(path, ref)

    repo.get_contents.side_effect = contents
    result = await _gh_scan(GithubActionsScanner, gh)

    assert [p.id.rsplit("/", 1)[-1] for p in result.pipelines] == ["deploy.yml"]
    (gap,) = result.coverage_gaps
    assert gap.error_class == ErrorClass.INVALID_CONTENT and "broken.yml" in gap.description


async def test_repos_without_workflows_are_not_gaps():
    result = await _gh_scan(GithubActionsScanner, fake_github_client())
    assert result.coverage_gaps == [] and result.errors == []  # 2 of 3 repos have none


async def test_forbidden_workflows_directory_is_reported():
    gh = fake_github_client()
    repo = _repo(gh, "payments-service")
    original = repo.get_contents.side_effect

    def contents(path, ref=None):
        if path == ".github/workflows":
            raise GithubException(403, {"message": "Forbidden"}, None)
        return original(path, ref)

    repo.get_contents.side_effect = contents
    result = await _gh_scan(GithubActionsScanner, gh)

    (gap,) = result.coverage_gaps
    assert gap.error_class == ErrorClass.ACCESS_DENIED and "could not be listed" in gap.description


# ------------------------------------------------------------------ report


def test_report_groups_gaps_by_severity_with_class_and_scope():
    snapshot = InventorySnapshot(
        config=ScanConfig(github_org=ORG),
        coverage_gaps=[
            CoverageGap(
                description="org down",
                severity="error",
                scope="github.com/acme",
                error_class=ErrorClass.ACCESS_DENIED,
            ),
            CoverageGap(
                description="lambda missing",
                severity="warning",
                scope=SCOPE,
                error_class=ErrorClass.THROTTLED,
                affected_regions=["us-east-1"],
            ),
        ],
    )
    report = _render_report(snapshot)
    errors_at, warnings_at = report.index("### Errors (1)"), report.index("### Warnings (1)")
    assert errors_at < warnings_at
    assert "`access_denied` `github.com/acme` org down" in report
    assert f"`throttled` `{SCOPE}` lambda missing — regions: us-east-1" in report
