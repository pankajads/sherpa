"""Persistent store, schema migrations and the snapshots CLI (issue #14)."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sherpa.cli.main import DEFAULT_DB, cli
from sherpa.core.models import InventorySnapshot, ScanConfig
from sherpa.core.store import CURRENT_SCHEMA_VERSION, InventoryStore, SchemaVersionError, migrations
from sherpa.orchestrator.discovery import run_discovery
from tests.golden.fake_estate import ACCOUNT, ORG, REGIONS, FakeAwsSession, fake_github_client

V1_FIXTURE = Path(__file__).parent.parent / "fixtures" / "db" / "sherpa_v1.db"
# Row counts of the v1 fixture: 2 snapshots of the golden fake estate, written by the v1 store.
V1_COUNTS = {
    "scan_runs": 2,
    "resources": 34,
    "dependencies": 46,
    "repositories": 6,
    "pipelines": 2,
    "workloads": 10,
}
CONFIG = ScanConfig(aws_accounts=[ACCOUNT], aws_regions=REGIONS, github_org=ORG)


def _fakes():
    return (
        patch(
            "sherpa.scanners.cloud.aws.scanner.aioboto3.Session",
            return_value=FakeAwsSession(),
        ),
        patch("sherpa.scanners.code.github.scanner.Github", return_value=fake_github_client()),
        patch(
            "sherpa.scanners.pipeline.github_actions.scanner.Github",
            return_value=fake_github_client(),
        ),
    )


async def _discover(store: InventoryStore) -> InventorySnapshot:
    a, b, c = _fakes()
    with a, b, c:
        return await run_discovery(CONFIG, store)


def _table_hash(db: Path, table: str, columns: list[str], where: str = "") -> str:
    con = sqlite3.connect(db)
    if columns == ["*"]:
        columns = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    rows = con.execute(
        f"SELECT {', '.join(columns)} FROM {table} {where} ORDER BY {', '.join(columns)}"
    ).fetchall()
    con.close()
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


def _v1_copy(tmp_path: Path) -> Path:
    db = tmp_path / "sherpa.db"
    shutil.copy2(V1_FIXTURE, db)
    return db


# ------------------------------------------------------------------ store fidelity


class TestRoundTrip:
    async def test_reloaded_snapshot_is_byte_identical(self, tmp_path):
        # Regression: v1 lost 19 of 23 dependency edges (all cross-plane ones), the coverage
        # gaps, errors and scan identities, and the timestamps' time zone.
        store = InventoryStore(tmp_path / "sherpa.db")
        snapshot = await _discover(store)

        reloaded = store.load_snapshot(snapshot.snapshot_id)

        assert reloaded is not None
        assert reloaded.to_canonical_json() == snapshot.to_canonical_json()
        assert sum(len(r.dependencies) for r in reloaded.resources) == 24

    async def test_gaps_errors_and_identities_survive(self, tmp_path):
        from sherpa.core.models import CoverageGap, ErrorClass, ScanIdentity

        snapshot = InventorySnapshot(
            config=CONFIG,
            coverage_gaps=[
                CoverageGap(
                    description="g", scope="s", error_class=ErrorClass.THROTTLED, severity="error"
                )
            ],
            errors=["boom"],
            scan_identities=[ScanIdentity(scope="aws-account:1", detail="why")],
        ).close()
        store = InventoryStore(tmp_path / "sherpa.db")
        store.save_snapshot(snapshot)

        reloaded = InventoryStore(tmp_path / "sherpa.db").load_snapshot(snapshot.snapshot_id)
        assert reloaded is not None
        assert reloaded.to_canonical_json() == snapshot.to_canonical_json()

    async def test_append_only_and_ordered(self, tmp_path):
        db = tmp_path / "sherpa.db"
        store = InventoryStore(db)
        first = await _discover(store)
        where = f"WHERE snapshot_id = '{first.snapshot_id}'"
        before = {
            t: _table_hash(db, t, ["*"], where) for t in ("resources", "dependencies", "workloads")
        }

        second = await _discover(InventoryStore(db))

        assert InventoryStore(db).list_snapshots() == [first.snapshot_id, second.snapshot_id]
        after = {t: _table_hash(db, t, ["*"], where) for t in before}
        assert after == before  # the first snapshot's rows are untouched


# ------------------------------------------------------------------ migrations


class TestMigrations:
    def test_v1_fixture_is_really_v1(self):
        con = sqlite3.connect(V1_FIXTURE)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "schema_version" not in tables
        assert "owner_id" not in [r[1] for r in con.execute("PRAGMA table_info(dependencies)")]

    def test_v1_database_migrates_without_data_loss(self, tmp_path):
        db = _v1_copy(tmp_path)
        original_bytes = db.read_bytes()
        v1_columns = {
            t: [r[1] for r in sqlite3.connect(db).execute(f"PRAGMA table_info({t})")]
            for t in V1_COUNTS
        }
        before = {t: _table_hash(db, t, cols) for t, cols in v1_columns.items()}

        store = InventoryStore(db)

        assert store.migrated_from == 1
        con = sqlite3.connect(db)
        assert con.execute("SELECT version FROM schema_version").fetchall() == [
            (CURRENT_SCHEMA_VERSION,)
        ]
        assert {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in V1_COUNTS} == (
            V1_COUNTS
        )
        after = {t: _table_hash(db, t, cols) for t, cols in v1_columns.items()}
        assert after == before  # every original value preserved
        backup = db.with_name("sherpa.db.bak-v1")
        assert backup.read_bytes() == original_bytes

    def test_migration_recovers_cross_plane_edges(self, tmp_path):
        store = InventoryStore(_v1_copy(tmp_path))
        for snapshot_id in store.list_snapshots():
            snapshot = store.load_snapshot(snapshot_id)
            assert snapshot is not None
            assert sum(len(r.dependencies) for r in snapshot.resources) == 23  # v1 code: 4

    def test_reopening_does_not_migrate_again(self, tmp_path):
        db = _v1_copy(tmp_path)
        InventoryStore(db)
        assert InventoryStore(db).migrated_from == CURRENT_SCHEMA_VERSION

    def test_failed_migration_leaves_database_untouched(self, tmp_path):
        # Regression: pysqlite auto-committed DDL, so a failure left a half-migrated file.
        db = _v1_copy(tmp_path)

        def half_migration(conn):
            conn.exec_driver_sql("ALTER TABLE dependencies ADD COLUMN owner_id VARCHAR")
            raise RuntimeError("simulated failure")

        with (
            patch.dict(migrations.MIGRATIONS, {1: half_migration}),
            pytest.raises(RuntimeError),
        ):
            InventoryStore(db)

        cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(dependencies)")]
        assert "owner_id" not in cols
        assert InventoryStore(db).migrated_from == 1  # a retry succeeds

    def test_newer_schema_is_refused(self, tmp_path):
        db = tmp_path / "future.db"
        InventoryStore(db)
        sqlite3.connect(db).execute("UPDATE schema_version SET version = 99").connection.commit()

        with pytest.raises(SchemaVersionError, match="schema v99.*newer Sherpa.*up to v2"):
            InventoryStore(db)


# ------------------------------------------------------------------ CLI


def _cli(*args: str, env: dict | None = None):
    a, b, c = _fakes()
    with a, b, c:
        return CliRunner().invoke(cli, list(args), env={"COLUMNS": "250", **(env or {})})


def _discover_cli(db: Path, tmp_path: Path):
    return _cli(
        "discover",
        "--aws-account",
        ACCOUNT,
        "--regions",
        ",".join(REGIONS),
        "--github-org",
        ORG,
        "--db",
        str(db),
        "--output",
        str(tmp_path / "out"),
    )


class TestSnapshotsCli:
    def test_two_discover_runs_then_list(self, tmp_path):
        db = tmp_path / "s.db"
        assert _discover_cli(db, tmp_path).exit_code == 0
        assert _discover_cli(db, tmp_path).exit_code == 0

        result = _cli("snapshots", "list", "--db", str(db))

        assert result.exit_code == 0, result.output
        ids = InventoryStore(db).list_snapshots()
        assert len(ids) == 2 and all(i in result.output for i in ids)
        assert "AWS ×1, GitHub acme" in result.output

    def test_show_by_prefix_and_as_json(self, tmp_path):
        db = tmp_path / "s.db"
        _discover_cli(db, tmp_path)
        (snapshot_id,) = InventoryStore(db).list_snapshots()

        shown = _cli("snapshots", "show", snapshot_id[:8], "--db", str(db))
        assert shown.exit_code == 0, shown.output
        assert snapshot_id in shown.output and f"aws-account:{ACCOUNT}" in shown.output

        as_json = _cli("snapshots", "show", snapshot_id, "--db", str(db), "--json")
        assert as_json.exit_code == 0
        stored = InventoryStore(db).load_snapshot(snapshot_id)
        assert stored is not None and as_json.output == stored.to_canonical_json()

    def test_unknown_and_ambiguous_ids_exit_1(self, tmp_path):
        db = tmp_path / "s.db"
        store = InventoryStore(db)
        for sid in ("abc-1", "abc-2"):
            store.save_snapshot(InventorySnapshot(snapshot_id=sid, config=CONFIG).close())

        unknown = _cli("snapshots", "show", "zzz", "--db", str(db))
        ambiguous = _cli("snapshots", "show", "abc", "--db", str(db))

        assert unknown.exit_code == 1 and "No snapshot matches" in unknown.output
        assert ambiguous.exit_code == 1 and "abc-1" in ambiguous.output

    def test_empty_database_list(self, tmp_path):
        result = _cli("snapshots", "list", "--db", str(tmp_path / "empty.db"))
        assert result.exit_code == 0 and "No snapshots" in result.output

    def test_default_database_is_persistent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SHERPA_DB", raising=False)
        monkeypatch.chdir(tmp_path)
        a, b, c = _fakes()
        with a, b, c:
            for _ in range(2):
                result = CliRunner().invoke(
                    cli,
                    ["discover", "--aws-account", ACCOUNT, "--regions", "us-east-1"],
                    env={"SHERPA_DB": None},
                )
                assert result.exit_code == 0, result.output

        assert DEFAULT_DB == "./.sherpa/sherpa.db"
        assert len(InventoryStore(tmp_path / ".sherpa" / "sherpa.db").list_snapshots()) == 2

    def test_cli_migrates_and_says_so(self, tmp_path):
        db = _v1_copy(tmp_path)
        result = _cli("snapshots", "list", "--db", str(db))
        assert result.exit_code == 0
        assert "Upgraded database" in result.output and "v1 to v2" in result.output

    def test_cli_refuses_newer_database(self, tmp_path):
        db = tmp_path / "future.db"
        InventoryStore(db)
        sqlite3.connect(db).execute("UPDATE schema_version SET version = 99").connection.commit()

        for args in (("snapshots", "list"), ("snapshots", "show", "x")):
            result = _cli(*args, "--db", str(db))
            assert result.exit_code == 1 and "newer Sherpa" in result.output
