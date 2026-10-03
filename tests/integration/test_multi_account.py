"""Multi-account AWS access (issue #11).

The fake AWS below decides *whose* data to return from the credentials a client was
created with, like real AWS. If the scanner used one account's credentials for another
account, that account's resources would come back under the wrong label.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from sherpa.core.models import AwsAccountSettings, ScanConfig
from sherpa.orchestrator.coverage_validator import validate_coverage
from sherpa.scanners.cloud.aws.scanner import (
    ROLE_SESSION_NAME,
    AwsCloudScanner,
    _AccountCredentials,
)

A, B, C = "111111111111", "222222222222", "333333333333"
AMBIENT = "999999999999"  # account behind the current (non-assumed) credentials
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class _Pages:
    def __init__(self, pages):
        self._it = iter(pages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeAws:
    """aioboto3.Session stand-in; one SQS queue per (account, region) and one IAM role."""

    def __init__(
        self,
        *,
        now=lambda: T0,
        ttl=timedelta(hours=1),
        fail_roles: set[str] = frozenset(),
        identity_override: dict[str, str] | None = None,
        identity_fails: set[str] = frozenset(),
    ):
        self.now, self.ttl = now, ttl
        self.fail_roles = fail_roles
        self.identity_override = identity_override or {}
        self.identity_fails = identity_fails
        self.assume_calls: list[dict] = []
        self.client_calls: list[tuple[str, str, str]] = []  # (service, region, identity)

    def client(self, service, region_name="us-east-1", aws_access_key_id=None, **_kw):
        identity = aws_access_key_id.removeprefix("AKID") if aws_access_key_id else AMBIENT
        self.client_calls.append((service, region_name, identity))
        return _FakeClient(self, service, region_name, identity)


class _FakeClient:
    def __init__(self, aws: FakeAws, service: str, region: str, identity: str):
        self.aws, self.service, self.region, self.identity = aws, service, region, identity

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def assume_role(self, **params):
        self.aws.assume_calls.append(params)
        account = params["RoleArn"].split(":")[4]
        if params["RoleArn"] in self.aws.fail_roles:
            raise PermissionError("AccessDenied: not authorized to perform sts:AssumeRole")
        account = self.aws.identity_override.get(account, account)
        return {
            "Credentials": {
                "AccessKeyId": f"AKID{account}",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
                "Expiration": self.aws.now() + self.aws.ttl,
            }
        }

    async def get_caller_identity(self):
        if self.identity in self.aws.identity_fails:
            raise ConnectionError("sts endpoint unreachable")
        if self.identity == AMBIENT:
            arn = f"arn:aws:iam::{AMBIENT}:user/alice"
        else:
            arn = f"arn:aws:sts::{self.identity}:assumed-role/SherpaReadOnly/{ROLE_SESSION_NAME}"
        return {"Account": self.identity, "Arn": arn}

    def get_paginator(self, operation):
        if operation == "list_queues":
            url = f"https://sqs.{self.region}.amazonaws.com/{self.identity}/q-{self.identity}"
            pages = [{"QueueUrls": [url]}]
        elif operation == "list_roles":
            arn = f"arn:aws:iam::{self.identity}:role/app-{self.identity}"
            pages = [{"Roles": [{"RoleName": f"app-{self.identity}", "Arn": arn}]}]
        else:
            pages = [{"Topics": []}]
        paginator = MagicMock()
        paginator.paginate.side_effect = lambda **_kw: _Pages(pages)
        return paginator


async def _scan(config: ScanConfig, aws: FakeAws, now=lambda: T0):
    with patch("sherpa.scanners.cloud.aws.scanner.aioboto3.Session", return_value=aws):
        return await AwsCloudScanner(now=now).scan(config)


def _cfg(**kw) -> ScanConfig:
    kw.setdefault("service_categories", ["messaging", "security"])
    return ScanConfig(**kw)


# ------------------------------------------------------------------ attribution


class TestEachAccountGetsItsOwnCredentials:
    async def test_three_accounts_no_cross_contamination(self):
        aws = FakeAws()
        result = await _scan(_cfg(aws_accounts=[C, A, B], aws_regions=["us-east-1"]), aws)

        assert result.errors == []
        by_account: dict[str, set[str]] = {}
        for r in result.resources:
            by_account.setdefault(r.account_id, set()).add(r.name)
        assert by_account == {acct: {f"q-{acct}", f"app-{acct}"} for acct in (A, B, C)}

    async def test_exactly_one_assume_role_per_account(self):
        aws = FakeAws()
        await _scan(_cfg(aws_accounts=[A, B, C], aws_regions=["us-east-1", "eu-west-1"]), aws)

        assert sorted(c["RoleArn"] for c in aws.assume_calls) == [
            f"arn:aws:iam::{acct}:role/SherpaReadOnly" for acct in (A, B, C)
        ]
        assert {c["RoleSessionName"] for c in aws.assume_calls} == {ROLE_SESSION_NAME}

    async def test_single_account_uses_current_credentials(self):
        aws = FakeAws()
        result = await _scan(_cfg(aws_accounts=[AMBIENT], aws_regions=["us-east-1"]), aws)

        assert aws.assume_calls == []
        assert {r.account_id for r in result.resources} == {AMBIENT}

    async def test_role_name_and_per_account_role_arn(self):
        aws = FakeAws()
        config = _cfg(
            aws_accounts=[A, B],
            aws_regions=["us-east-1"],
            aws_role_name="AuditRole",
            aws_external_id="deal-2026",
            aws_account_settings={
                B: AwsAccountSettings(
                    role_arn=f"arn:aws:iam::{B}:role/custom/Legacy", external_id="b-only"
                )
            },
        )
        await _scan(config, aws)

        calls = {c["RoleArn"]: c.get("ExternalId") for c in aws.assume_calls}
        assert calls == {
            f"arn:aws:iam::{A}:role/AuditRole": "deal-2026",
            f"arn:aws:iam::{B}:role/custom/Legacy": "b-only",
        }

    def test_resources_labelled_with_arn_owner(self):
        # Defence in depth for shared resources: a resource listed while scanning B whose ARN
        # names C is labelled C, with a gap naming both accounts.
        from sherpa.core.models import Resource, ResourceType
        from sherpa.scanners.cloud.aws.scanner import _attribute_to_owner

        shared = Resource(
            id=f"arn:aws:iam::{C}:role/shared",
            resource_type=ResourceType.IAM_ROLE,
            region="global",
            account_id=B,
        )
        fixed, gaps = _attribute_to_owner([shared])
        assert fixed[0].account_id == C
        assert len(gaps) == 1 and f"account {B} returned" in gaps[0].description


# ------------------------------------------------------------------ regions


class TestRegions:
    async def test_per_account_regions_and_iam_once_per_account(self):
        aws = FakeAws()
        config = _cfg(
            aws_accounts=[A, B],
            aws_regions=["us-east-1"],
            aws_account_settings={
                A: AwsAccountSettings(regions=["eu-west-1"]),
                B: AwsAccountSettings(regions=["us-east-1", "ap-south-1"]),
            },
        )
        result = await _scan(config, aws)

        sqs = {(region, who) for svc, region, who in aws.client_calls if svc == "sqs"}
        assert sqs == {("eu-west-1", A), ("us-east-1", B), ("ap-south-1", B)}
        iam = [(region, who) for svc, region, who in aws.client_calls if svc == "iam"]
        assert sorted(iam) == [("us-east-1", A), ("us-east-1", B)]  # A has no us-east-1
        assert {r.region for r in result.resources if r.account_id == A} == {"eu-west-1", "global"}

    async def test_validate_config_requires_regions_per_account(self):
        config = ScanConfig(
            aws_accounts=[A, B],
            aws_account_settings={A: AwsAccountSettings(regions=["eu-west-1"])},
        )
        result = await AwsCloudScanner().validate_config(config)
        assert not result.valid
        assert any(B in e for e in result.errors)
        assert not any(A in e for e in result.errors)

    def test_empty_region_gap_is_per_account(self):
        from sherpa.core.models import Resource, ResourceType

        config = ScanConfig(aws_accounts=[A, B], aws_regions=["us-east-1", "eu-west-1"])
        resources = [
            Resource(
                id=f"arn:aws:sqs:{region}:{acct}:q",
                resource_type=ResourceType.SQS_QUEUE,
                region=region,
                account_id=acct,
            )
            for acct, region in [(A, "us-east-1"), (A, "eu-west-1"), (B, "us-east-1")]
        ]
        gaps = [g for g in validate_coverage(config, resources, []) if g.affected_regions]
        assert len(gaps) == 1
        assert B in gaps[0].description and gaps[0].affected_regions == ["eu-west-1"]


# ------------------------------------------------------------------ failures and expiry


class TestFailuresAndExpiry:
    async def test_unreachable_account_is_reported_once_and_others_scanned(self):
        aws = FakeAws(fail_roles={f"arn:aws:iam::{B}:role/SherpaReadOnly"})
        result = await _scan(_cfg(aws_accounts=[A, B, C], aws_regions=["us-east-1"]), aws)

        assert {r.account_id for r in result.resources} == {A, C}
        assert len(result.errors) == 1 and B in result.errors[0]
        assert any(B in g.description and g.severity == "error" for g in result.coverage_gaps)
        assert all(who != B for _, _, who in aws.client_calls)  # no collector ran for B

    async def test_credentials_refresh_before_expiry(self):
        clock = {"now": T0}
        aws = FakeAws(now=lambda: clock["now"], ttl=timedelta(minutes=10))
        target = ScanConfig(aws_accounts=[A, B], aws_regions=["us-east-1"]).aws_targets()[0]
        creds = _AccountCredentials(aws, target, now=lambda: clock["now"])

        await creds.client_kwargs()
        clock["now"] = T0 + timedelta(minutes=4)  # 6 min left: still valid
        await creds.client_kwargs()
        assert len(aws.assume_calls) == 1
        clock["now"] = T0 + timedelta(minutes=6)  # 4 min left: inside the 5-min margin
        await creds.client_kwargs()
        assert len(aws.assume_calls) == 2

    async def test_concurrent_requests_assume_once(self):
        import asyncio

        aws = FakeAws()
        target = ScanConfig(aws_accounts=[A, B], aws_regions=["us-east-1"]).aws_targets()[0]
        creds = _AccountCredentials(aws, target, now=lambda: T0)

        await asyncio.gather(*(creds.client_kwargs() for _ in range(20)))
        assert len(aws.assume_calls) == 1


# ------------------------------------------------------------------ config validation


class TestConfigValidation:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"aws_accounts": ["12345"]}, "12 digits"),
            (
                {"aws_accounts": [A, B], "assume_role_arn": f"arn:aws:iam::{A}:role/x"},
                "single account",
            ),
            ({"aws_accounts": [A], "assume_role_arn": f"arn:aws:iam::{B}:role/x"}, B),
            (
                {
                    "aws_accounts": [A],
                    "aws_account_settings": {A: {"role_arn": f"arn:aws:iam::{C}:role/x"}},
                },
                C,
            ),
            ({"aws_accounts": [A], "assume_role_arn": "not-an-arn"}, "Not an IAM role ARN"),
        ],
    )
    def test_misconfigurations_rejected_before_any_call(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            ScanConfig(**kwargs)

    def test_accounts_named_only_in_settings_are_scanned(self):
        config = ScanConfig(aws_account_settings={A: AwsAccountSettings(regions=["us-east-1"])})
        assert config.aws_accounts == [A]


# ------------------------------------------------------------------ CLI + accounts file


class TestCli:
    def _run(self, args, aws: FakeAws, tmp_path):
        from click.testing import CliRunner

        from sherpa.cli.main import cli

        with patch("sherpa.scanners.cloud.aws.scanner.aioboto3.Session", return_value=aws):
            return CliRunner().invoke(
                cli, ["discover", "--output", str(tmp_path / "out"), *args], env={"COLUMNS": "200"}
            )

    def _accounts_file(self, tmp_path, text):
        path = tmp_path / "accounts.yaml"
        path.write_text(text)
        return str(path)

    def test_accounts_file_drives_accounts_regions_and_roles(self, tmp_path):
        aws = FakeAws(now=lambda: datetime.now(UTC))  # the CLI uses the real clock
        f = self._accounts_file(
            tmp_path,
            f"""
default_regions: [us-east-1]
role_name: AuditRole
accounts:
  - id: "{A}"
    name: prod
    regions: [eu-west-1]
  - id: "{B}"
    role_arn: arn:aws:iam::{B}:role/Legacy
""",
        )
        result = self._run(["--accounts-file", f], aws, tmp_path)

        assert result.exit_code == 0, result.output
        assert sorted(c["RoleArn"] for c in aws.assume_calls) == [
            f"arn:aws:iam::{A}:role/AuditRole",
            f"arn:aws:iam::{B}:role/Legacy",
        ]
        sqs = {(region, who) for svc, region, who in aws.client_calls if svc == "sqs"}
        assert sqs == {("eu-west-1", A), ("us-east-1", B)}
        assert "AWS scan plan" in result.output and "prod" in result.output

    def test_regions_flag_overrides_file_default(self, tmp_path):
        aws = FakeAws()
        f = self._accounts_file(
            tmp_path, f'default_regions: [us-east-1]\naccounts:\n  - id: "{AMBIENT}"\n'
        )
        result = self._run(["--accounts-file", f, "--regions", "ap-south-1"], aws, tmp_path)

        assert result.exit_code == 0, result.output
        assert {region for svc, region, _ in aws.client_calls if svc == "sqs"} == {"ap-south-1"}

    @pytest.mark.parametrize(
        "raw_id",
        [
            "123456789012",  # plain YAML integer
            "012345670123",  # YAML 1.1 octal: would silently become a different number
        ],
    )
    def test_unquoted_account_id_rejected(self, tmp_path, raw_id):
        f = self._accounts_file(tmp_path, f"accounts:\n  - id: {raw_id}\n")
        result = self._run(["--accounts-file", f], FakeAws(), tmp_path)

        assert result.exit_code == 1
        assert "must be quoted" in result.output

    def test_assume_role_with_several_accounts_rejected(self, tmp_path):
        args = ["--aws-account", A, "--aws-account", B, "--assume-role", f"arn:aws:iam::{A}:role/x"]
        result = self._run(args, FakeAws(), tmp_path)

        assert result.exit_code == 1
        assert "single account" in result.output


# ------------------------------------------------------------------ identity verification (#12)


class TestIdentityVerification:
    async def test_wrong_profile_skips_account_instead_of_mislabelling(self):
        # Told to scan A, but the current credentials (e.g. a stale AWS_PROFILE) belong to
        # AMBIENT. Without the check, AMBIENT's SQS queue would be labelled A: SQS and EC2
        # IDs are built from the requested account, so the ARN check cannot catch it.
        aws = FakeAws()
        result = await _scan(_cfg(aws_accounts=[A], aws_regions=["us-east-1"]), aws)

        assert result.resources == []
        assert [svc for svc, _, _ in aws.client_calls] == ["sts"]  # no collector ran
        (identity,) = result.scan_identities
        assert identity.scope == f"aws-account:{A}" and not identity.verified
        assert f"expected account {A}" in identity.detail
        assert f"belong to {AMBIENT}" in identity.detail
        assert f"arn:aws:iam::{AMBIENT}:user/alice" in identity.detail
        assert "AWS_PROFILE" in identity.detail
        assert any(g.severity == "error" and A in g.description for g in result.coverage_gaps)

    async def test_role_landing_in_wrong_account_is_skipped(self):
        aws = FakeAws(identity_override={B: C})  # B's role yields credentials in C
        result = await _scan(_cfg(aws_accounts=[A, B], aws_regions=["us-east-1"]), aws)

        assert {r.account_id for r in result.resources} == {A}
        assert all(who != C for svc, _, who in aws.client_calls if svc != "sts")
        failed = [i for i in result.scan_identities if not i.verified]
        assert [i.scope for i in failed] == [f"aws-account:{B}"]
        assert "role mapping" in failed[0].detail

    async def test_unconfirmable_identity_fails_closed(self):
        aws = FakeAws(identity_fails={B})
        result = await _scan(_cfg(aws_accounts=[A, B], aws_regions=["us-east-1"]), aws)

        assert {r.account_id for r in result.resources} == {A}
        failed = [i for i in result.scan_identities if not i.verified]
        assert len(failed) == 1 and "cannot confirm" in failed[0].detail

    async def test_verified_identities_are_recorded(self):
        result = await _scan(_cfg(aws_accounts=[A, B], aws_regions=["us-east-1"]), FakeAws())

        assert [(i.scope, i.method, i.verified) for i in result.scan_identities] == [
            (f"aws-account:{A}", "assumed-role", True),
            (f"aws-account:{B}", "assumed-role", True),
        ]
        assert result.scan_identities[0].principal == (
            f"arn:aws:sts::{A}:assumed-role/SherpaReadOnly/{ROLE_SESSION_NAME}"
        )

    def test_cli_exits_2_and_still_saves_snapshot(self, tmp_path):
        from click.testing import CliRunner

        from sherpa.cli.main import EXIT_INCOMPLETE_SCAN, cli

        out = tmp_path / "out"
        with patch("sherpa.scanners.cloud.aws.scanner.aioboto3.Session", return_value=FakeAws()):
            result = CliRunner().invoke(
                cli, ["discover", "--aws-account", A, "--output", str(out)], env={"COLUMNS": "200"}
            )

        assert result.exit_code == EXIT_INCOMPLETE_SCAN == 2, result.output
        assert "Incomplete scan" in result.output and A in result.output
        assert len(list(out.glob("snapshot_*.json"))) == 1
        report = next(out.glob("report_*.md")).read_text()
        assert f"❌ `aws-account:{A}`" in report
