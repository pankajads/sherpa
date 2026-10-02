"""Determinism golden tests (issue #10).

Same inputs ⇒ byte-identical snapshot JSON and report, after masking the run's own
snapshot ID and timestamps — no matter what order the APIs return things in.

When output changes *intentionally*, regenerate the golden files and review the diff:

    pytest tests/golden --update-golden
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from sherpa.core.models import ScanConfig
from sherpa.core.store import InventoryStore
from sherpa.orchestrator.discovery import run_discovery

from .fake_estate import ACCOUNT, ORG, REGIONS, FakeAwsSession, fake_github_client

DATA = Path(__file__).parent / "data"
GOLDEN_SNAPSHOT = DATA / "discovery_snapshot.json"
GOLDEN_REPORT = DATA / "discovery_report.md"

SHUFFLE_SEEDS = [1, 7, 42, 2026]


def _config() -> ScanConfig:
    return ScanConfig(aws_accounts=[ACCOUNT], aws_regions=REGIONS, github_org=ORG)


def mask_snapshot(text: str) -> str:
    data = json.loads(text)
    data["snapshot_id"] = "<snapshot-id>"
    data["started_at"] = "<timestamp>"
    data["completed_at"] = "<timestamp>"
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def mask_report(text: str) -> str:
    text = re.sub(r"(\*\*Snapshot ID:\*\* )`[^`]*`", r"\1`<snapshot-id>`", text)
    return re.sub(r"(\*\*(?:Started|Completed):\*\* ).*", r"\1<timestamp>", text)


async def _discover(tmp_path: Path, shuffle_seed: int | None) -> tuple[str, str]:
    """Run discovery against the fake estate; return masked (snapshot JSON, report)."""
    out = tmp_path / f"out-{shuffle_seed}"
    with (
        patch(
            "sherpa.scanners.cloud.aws.scanner.aioboto3.Session",
            return_value=FakeAwsSession(shuffle_seed),
        ),
        patch(
            "sherpa.scanners.code.github.scanner.Github",
            return_value=fake_github_client(shuffle_seed),
        ),
        patch(
            "sherpa.scanners.pipeline.github_actions.scanner.Github",
            return_value=fake_github_client(shuffle_seed),
        ),
    ):
        snapshot = await run_discovery(_config(), InventoryStore(), out)

    assert snapshot.errors == [], "fixture should scan cleanly"
    snap_text = (out / f"snapshot_{snapshot.snapshot_id}.json").read_text()
    report_text = (out / f"report_{snapshot.snapshot_id}.md").read_text()
    return mask_snapshot(snap_text), mask_report(report_text)


class TestDeterminism:
    async def test_two_runs_are_byte_identical(self, tmp_path):
        first = await _discover(tmp_path / "a", None)
        second = await _discover(tmp_path / "b", None)
        assert first == second

    @pytest.mark.parametrize("seed", SHUFFLE_SEEDS)
    async def test_api_response_order_does_not_change_output(self, tmp_path, seed):
        baseline = await _discover(tmp_path / "base", None)
        shuffled = await _discover(tmp_path / "shuffled", seed)
        assert shuffled[0] == baseline[0], f"snapshot JSON differs with shuffle seed {seed}"
        assert shuffled[1] == baseline[1], f"report differs with shuffle seed {seed}"

    async def test_matches_golden_files(self, tmp_path, update_golden):
        snapshot_json, report = await _discover(tmp_path, None)
        if update_golden:
            DATA.mkdir(parents=True, exist_ok=True)
            GOLDEN_SNAPSHOT.write_text(snapshot_json)
            GOLDEN_REPORT.write_text(report)
            pytest.skip("golden files rewritten — review the diff before committing")
        hint = "Output changed. If intentional: pytest tests/golden --update-golden, review diff."
        assert snapshot_json == GOLDEN_SNAPSHOT.read_text(), hint
        assert report == GOLDEN_REPORT.read_text(), hint

    async def test_fixture_exercises_the_ordering_hazards(self, tmp_path):
        # Guard against the golden test passing vacuously on a too-simple fixture.
        data = json.loads((await _discover(tmp_path, None))[0])
        assert len(data["resources"]) >= 15
        assert {r["region"] for r in data["resources"]} >= {"us-east-1", "eu-west-1", "global"}
        assert any(len(r["dependencies"]) >= 2 for r in data["resources"])
        assert any(len(r["tags"]) >= 2 for r in data["resources"])
        assert len(data["repositories"]) == 3
        assert any(len(r["package_dependencies"]) >= 2 for r in data["repositories"])
        assert len(data["pipelines"]) == 1 and len(data["pipelines"][0]["stages"]) == 2
        assert len(data["workloads"]) >= 3
        assert all(w["id"].startswith("wl-") for w in data["workloads"])
