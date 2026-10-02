"""Credentials must never reach any Sherpa output (issue #7).

Runs the real scanners end to end — GitHub mocked at the PyGithub client, AWS mocked
at the aioboto3 session (which hands out sentinel assumed-role credentials) — then
greps every artifact Sherpa produces for the sentinel secrets: snapshot JSON, Markdown
report, SQLite DB file, captured logs and CLI output.

If SHERPA_LEAK_ARTIFACT_DIR is set, all artifacts are copied there so CI can run an
independent secret scanner (gitleaks) over them.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from sherpa.cli.main import cli
from sherpa.core.models import InventorySnapshot, ScanConfig
from sherpa.core.store import InventoryStore
from sherpa.orchestrator.discovery import run_discovery

from .conftest import make_org, make_repo

ACCOUNT = "123456789012"
REGION = "us-east-1"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/SherpaReadOnly"

# Built at runtime so the source file itself doesn't look like it contains real secrets.
# Formats match real tokens so pattern-based scanners (gitleaks) would flag a leak too.
GH_TOKEN = "ghp" + "_" + "Sh3rpaL3akS3ntin3l0123456789abcdefgh"
AWS_KEY_ID = "AS" + "IA" + "SHERPALEAKSENTNL"
AWS_SECRET = "Sherpa" + "LeakSentinelSecretKey0123456789abcdef/+X"
AWS_SESSION_TOKEN = "Sherpa" + "LeakSentinelSessionToken" + "Q" * 40
SENTINELS = [GH_TOKEN, AWS_KEY_ID, AWS_SECRET, AWS_SESSION_TOKEN]


# ------------------------------------------------------------------ fakes


class _EmptyAsyncIter:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeAwsClient:
    """Async-context-manager client: empty paginators, empty dict responses."""

    def __init__(self, service: str) -> None:
        self._service = service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_paginator(self, _name):
        paginator = MagicMock()
        paginator.paginate.side_effect = lambda **_kw: _EmptyAsyncIter()
        return paginator

    async def assume_role(self, **_kw):
        return {
            "Credentials": {
                "AccessKeyId": AWS_KEY_ID,
                "SecretAccessKey": AWS_SECRET,
                "SessionToken": AWS_SESSION_TOKEN,
            }
        }

    def __getattr__(self, _name):
        return AsyncMock(return_value={})


class _FakeAwsSession:
    def __init__(self) -> None:
        self.client_calls: list[tuple[str, dict]] = []

    def client(self, service: str, **kwargs):
        self.client_calls.append((service, kwargs))
        return _FakeAwsClient(service)


def _github_mock() -> MagicMock:
    repo = make_repo(
        "acme/payments",
        tree_paths=["main.tf", "requirements.txt"],
        file_contents={
            "main.tf": b'resource "aws_s3_bucket" "data" {}',
            "requirements.txt": b"boto3>=1.35\n",
        },
    )
    gh = MagicMock()
    gh.get_organization.return_value = make_org([repo])
    return gh


@pytest.fixture
def fake_backends():
    """Patch AWS and GitHub clients; yield the mocks for call assertions."""
    session = _FakeAwsSession()
    gh_code = MagicMock(return_value=_github_mock())
    gh_pipe = MagicMock(return_value=_github_mock())
    with (
        patch("sherpa.scanners.cloud.aws.scanner.aioboto3.Session", return_value=session),
        patch("sherpa.scanners.code.github.scanner.Github", gh_code),
        patch("sherpa.scanners.pipeline.github_actions.scanner.Github", gh_pipe),
    ):
        yield session, gh_code, gh_pipe


def _assert_no_sentinels(label: str, data: bytes) -> None:
    for secret in SENTINELS:
        assert secret.encode() not in data, f"credential leaked into {label}"


def _export_artifacts(paths: list[Path], extra_texts: dict[str, str]) -> None:
    target = os.environ.get("SHERPA_LEAK_ARTIFACT_DIR")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for p in paths:
        shutil.copy(p, out / p.name)
    for name, text in extra_texts.items():
        (out / name).write_text(text)


def _config() -> ScanConfig:
    return ScanConfig(
        aws_accounts=[ACCOUNT],
        aws_regions=[REGION],
        assume_role_arn=ROLE_ARN,
        github_org="acme",
        github_token=GH_TOKEN,
    )


# ------------------------------------------------------------------ tests


class TestCredentialsNeverPersisted:
    async def test_discovery_outputs_contain_no_credentials(
        self, fake_backends, tmp_path, caplog, capsys
    ):
        session, gh_code, gh_pipe = fake_backends
        caplog.set_level(logging.DEBUG)
        db_path = tmp_path / "sherpa.db"
        out_dir = tmp_path / "out"

        snapshot = await run_discovery(_config(), InventoryStore(db_path), out_dir)

        # Credentials were actually used to authenticate...
        gh_code.assert_called_once_with(GH_TOKEN)
        gh_pipe.assert_called_once_with(GH_TOKEN)
        service_calls = [kw for svc, kw in session.client_calls if svc != "sts"]
        assert service_calls, "AWS collectors never ran"
        assert all(kw.get("aws_secret_access_key") == AWS_SECRET for kw in service_calls)

        # ...but appear in no artifact.
        artifacts = sorted(out_dir.iterdir()) + [db_path]
        assert len(artifacts) == 3  # snapshot JSON, report, DB
        for path in artifacts:
            _assert_no_sentinels(path.name, path.read_bytes())

        captured = capsys.readouterr()
        _assert_no_sentinels("logs", caplog.text.encode())
        _assert_no_sentinels("stdout/stderr", (captured.out + captured.err).encode())
        _assert_no_sentinels("snapshot repr", repr(snapshot).encode())

        _export_artifacts(
            artifacts,
            {"discovery-logs.txt": caplog.text, "discovery-stdio.txt": captured.out + captured.err},
        )

    async def test_reloaded_snapshot_has_no_token(self, fake_backends, tmp_path):
        store = InventoryStore(tmp_path / "sherpa.db")
        snapshot = await run_discovery(_config(), store)

        loaded = store.load_snapshot(snapshot.snapshot_id)

        assert loaded is not None
        assert loaded.config.github_token is None
        assert loaded.config.github_org == "acme"

    def test_cli_discover_outputs_contain_no_credentials(self, fake_backends, tmp_path, caplog):
        caplog.set_level(logging.DEBUG)
        db_path = tmp_path / "cli.db"
        out_dir = tmp_path / "cli-out"

        result = CliRunner().invoke(
            cli,
            [
                "discover",
                "--aws-account",
                ACCOUNT,
                "--regions",
                REGION,
                "--assume-role",
                ROLE_ARN,
                "--github-org",
                "acme",
                "--db",
                str(db_path),
                "--output",
                str(out_dir),
            ],
            env={"GITHUB_TOKEN": GH_TOKEN},
        )

        assert result.exit_code == 0, result.output
        _, gh_code, _ = fake_backends
        gh_code.assert_called_once_with(GH_TOKEN)
        artifacts = sorted(out_dir.iterdir()) + [db_path]
        for path in artifacts:
            _assert_no_sentinels(path.name, path.read_bytes())
        _assert_no_sentinels("CLI output", result.output.encode())
        _assert_no_sentinels("logs", caplog.text.encode())

        _export_artifacts(
            artifacts,
            {"cli-output.txt": result.output, "cli-logs.txt": caplog.text},
        )


class TestScanConfigSecretHandling:
    def test_token_excluded_from_dump(self):
        config = _config()
        assert "github_token" not in config.model_dump()
        assert GH_TOKEN not in config.model_dump_json()
        assert GH_TOKEN not in InventorySnapshot(config=config).model_dump_json()

    def test_token_masked_in_repr_and_str(self):
        config = _config()
        assert GH_TOKEN not in repr(config)
        assert GH_TOKEN not in str(config)

    def test_raw_token_available_for_clients(self):
        assert _config().github_token_value() == GH_TOKEN
        assert ScanConfig(github_org="acme").github_token_value() is None
