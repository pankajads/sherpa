from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from sherpa.core.models import (
    CoverageGap,
    InventorySnapshot,
    PackageDependency,
    Pipeline,
    PipelineStage,
    Repository,
    Resource,
    ResourceDependency,
    ScanConfig,
    ScanIdentity,
    Workload,
)
from sherpa.core.models.enums import DependencyPlane, DependencyType, IaCType, MigrationPath

from .migrations import ensure_schema
from .schema import dependencies, pipelines, repositories, resources, scan_runs, workloads


def _make_transactions_real(engine: sa.Engine) -> None:
    """Make SQLite transactions cover DDL too, so a failed migration leaves no trace.

    Python's sqlite3 driver only opens a transaction before DML, so ALTER TABLE would
    otherwise auto-commit. This is SQLAlchemy's documented recipe for pysqlite.
    """

    @sa.event.listens_for(engine, "connect")
    def _no_driver_transactions(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @sa.event.listens_for(engine, "begin")
    def _begin(conn: sa.Connection) -> None:
        conn.exec_driver_sql("BEGIN")


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; Sherpa stores UTC, so restore the zone on read."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _dump(items: list[Any]) -> str:
    return json.dumps([i.model_dump(mode="json") for i in items], sort_keys=True)


class InventoryStore:
    """SQLite-backed store for inventory snapshots.

    Snapshots are append-only; a closed snapshot is never mutated. Saving and reloading a
    snapshot is lossless. Opening a database written by an older Sherpa migrates it (after a
    backup copy); one written by a newer Sherpa is refused (see migrations.py).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        in_memory = str(db_path) == ":memory:"
        db_file = None if in_memory else Path(db_path)
        if db_file is not None:
            db_file.parent.mkdir(parents=True, exist_ok=True)
        url = "sqlite://" if in_memory else f"sqlite:///{db_file}"
        self._engine = sa.create_engine(url, future=True)
        _make_transactions_real(self._engine)
        self.migrated_from = ensure_schema(self._engine, db_file)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_snapshot(self, snapshot: InventorySnapshot) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                scan_runs.insert().values(
                    snapshot_id=snapshot.snapshot_id,
                    started_at=snapshot.started_at,
                    completed_at=snapshot.completed_at,
                    config_json=snapshot.config.model_dump_json(),
                    coverage_gaps_json=_dump(snapshot.coverage_gaps),
                    errors_json=json.dumps(snapshot.errors),
                    scan_identities_json=_dump(snapshot.scan_identities),
                )
            )
            for r in snapshot.resources:
                conn.execute(
                    resources.insert().values(
                        id=r.id,
                        snapshot_id=snapshot.snapshot_id,
                        resource_type=str(r.resource_type),
                        region=r.region,
                        account_id=r.account_id,
                        name=r.name,
                        tags_json=json.dumps(r.tags, sort_keys=True),
                        metadata_json=json.dumps(r.metadata, sort_keys=True),
                    )
                )
                for dep in r.dependencies:
                    conn.execute(
                        dependencies.insert().values(
                            snapshot_id=snapshot.snapshot_id,
                            owner_id=r.id,
                            source_id=dep.source_id,
                            target_id=dep.target_id,
                            dependency_type=str(dep.dependency_type),
                            plane=str(dep.plane),
                            metadata_json=json.dumps(dep.metadata, sort_keys=True),
                        )
                    )
            for repo in snapshot.repositories:
                conn.execute(
                    repositories.insert().values(
                        id=repo.id,
                        snapshot_id=snapshot.snapshot_id,
                        url=repo.url,
                        iac_type=str(repo.iac_type),
                        declared_resource_ids_json=json.dumps(repo.declared_resource_ids),
                        package_deps_json=json.dumps(
                            [p.model_dump() for p in repo.package_dependencies]
                        ),
                        has_dockerfile=repo.has_dockerfile,
                        has_docker_compose=repo.has_docker_compose,
                        default_branch=repo.default_branch,
                        metadata_json=json.dumps(repo.metadata, sort_keys=True),
                    )
                )
            for pipe in snapshot.pipelines:
                conn.execute(
                    pipelines.insert().values(
                        id=pipe.id,
                        snapshot_id=snapshot.snapshot_id,
                        pipeline_type=pipe.pipeline_type,
                        repo_id=pipe.repo_id,
                        stages_json=json.dumps([s.model_dump() for s in pipe.stages]),
                        deploys_to_resource_ids_json=json.dumps(pipe.deploys_to_resource_ids),
                        deploys_to_accounts_json=json.dumps(pipe.deploys_to_accounts),
                    )
                )
            for wl in snapshot.workloads:
                conn.execute(
                    workloads.insert().values(
                        id=wl.id,
                        snapshot_id=snapshot.snapshot_id,
                        name=wl.name,
                        resource_ids_json=json.dumps(wl.resource_ids),
                        repo_ids_json=json.dumps(wl.repo_ids),
                        pipeline_ids_json=json.dumps(wl.pipeline_ids),
                        inferred_from=wl.inferred_from,
                        migration_path=str(wl.migration_path),
                        metadata_json=json.dumps(wl.metadata, sort_keys=True),
                    )
                )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load_snapshot(self, snapshot_id: str) -> InventorySnapshot | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                scan_runs.select().where(scan_runs.c.snapshot_id == snapshot_id)
            ).fetchone()
            if row is None:
                return None

            config = ScanConfig.model_validate_json(row.config_json)

            res_rows = conn.execute(
                resources.select().where(resources.c.snapshot_id == snapshot_id)
            ).fetchall()

            dep_rows = conn.execute(
                dependencies.select().where(dependencies.c.snapshot_id == snapshot_id)
            ).fetchall()

            # Group dependencies by the resource they are attached to
            deps_by_owner: dict[str, list[ResourceDependency]] = {}
            for d in dep_rows:
                dep = ResourceDependency(
                    source_id=d.source_id,
                    target_id=d.target_id,
                    dependency_type=DependencyType(d.dependency_type),
                    plane=DependencyPlane(d.plane),
                    metadata=json.loads(d.metadata_json),
                )
                deps_by_owner.setdefault(d.owner_id, []).append(dep)

            loaded_resources = [
                Resource(
                    id=r.id,
                    resource_type=r.resource_type,
                    region=r.region,
                    account_id=r.account_id,
                    name=r.name,
                    tags=json.loads(r.tags_json),
                    metadata=json.loads(r.metadata_json),
                    dependencies=deps_by_owner.get(r.id, []),
                )
                for r in sorted(res_rows, key=lambda r: r.id)
            ]

            repo_rows = conn.execute(
                repositories.select().where(repositories.c.snapshot_id == snapshot_id)
            ).fetchall()
            loaded_repos = [
                Repository(
                    id=r.id,
                    url=r.url,
                    iac_type=IaCType(r.iac_type),
                    declared_resource_ids=json.loads(r.declared_resource_ids_json),
                    package_dependencies=[
                        PackageDependency(**p) for p in json.loads(r.package_deps_json)
                    ],
                    has_dockerfile=r.has_dockerfile,
                    has_docker_compose=r.has_docker_compose,
                    default_branch=r.default_branch,
                    metadata=json.loads(r.metadata_json),
                )
                for r in sorted(repo_rows, key=lambda r: r.id)
            ]

            pipe_rows = conn.execute(
                pipelines.select().where(pipelines.c.snapshot_id == snapshot_id)
            ).fetchall()
            loaded_pipelines = [
                Pipeline(
                    id=p.id,
                    pipeline_type=p.pipeline_type,
                    repo_id=p.repo_id,
                    stages=[PipelineStage(**s) for s in json.loads(p.stages_json)],
                    deploys_to_resource_ids=json.loads(p.deploys_to_resource_ids_json),
                    deploys_to_accounts=json.loads(p.deploys_to_accounts_json),
                )
                for p in sorted(pipe_rows, key=lambda p: p.id)
            ]

            wl_rows = conn.execute(
                workloads.select().where(workloads.c.snapshot_id == snapshot_id)
            ).fetchall()
            loaded_workloads = [
                Workload(
                    id=w.id,
                    name=w.name,
                    resource_ids=json.loads(w.resource_ids_json),
                    repo_ids=json.loads(w.repo_ids_json),
                    pipeline_ids=json.loads(w.pipeline_ids_json),
                    inferred_from=w.inferred_from,
                    migration_path=MigrationPath(w.migration_path),
                    metadata=json.loads(w.metadata_json),
                )
                for w in sorted(wl_rows, key=lambda w: w.name)
            ]

            return InventorySnapshot(
                snapshot_id=row.snapshot_id,
                started_at=_utc(row.started_at),
                completed_at=_utc(row.completed_at),
                config=config,
                resources=loaded_resources,
                repositories=loaded_repos,
                pipelines=loaded_pipelines,
                workloads=loaded_workloads,
                coverage_gaps=[
                    CoverageGap.model_validate(g) for g in json.loads(row.coverage_gaps_json)
                ],
                errors=json.loads(row.errors_json),
                scan_identities=[
                    ScanIdentity.model_validate(i) for i in json.loads(row.scan_identities_json)
                ],
            )

    def snapshot_summaries(self) -> list[dict[str, Any]]:
        """One row per snapshot, oldest first: identity, timing, sources and counts."""
        counts = {}
        with self._engine.connect() as conn:
            for name, table in (
                ("resources", resources),
                ("repositories", repositories),
                ("pipelines", pipelines),
                ("workloads", workloads),
            ):
                counts[name] = dict(
                    conn.execute(
                        sa.select(table.c.snapshot_id, sa.func.count()).group_by(
                            table.c.snapshot_id
                        )
                    ).all()
                )
            runs = conn.execute(scan_runs.select().order_by(scan_runs.c.started_at)).fetchall()
        summaries = []
        for run in runs:
            config = json.loads(run.config_json)
            gaps = json.loads(run.coverage_gaps_json)
            identities = json.loads(run.scan_identities_json)
            summaries.append(
                {
                    "snapshot_id": run.snapshot_id,
                    "started_at": _utc(run.started_at),
                    "completed_at": _utc(run.completed_at),
                    "aws_accounts": config.get("aws_accounts") or [],
                    "github_org": config.get("github_org"),
                    **{name: counts[name].get(run.snapshot_id, 0) for name in counts},
                    "coverage_gaps": len(gaps),
                    "errors": len(json.loads(run.errors_json)),
                    "unverified_scopes": sum(1 for i in identities if not i.get("verified")),
                }
            )
        return summaries

    def resolve_snapshot_id(self, prefix: str) -> list[str]:
        """Snapshot IDs starting with `prefix` (so users can type a short unique prefix)."""
        return [sid for sid in self.list_snapshots() if sid.startswith(prefix)]

    def list_snapshots(self) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(scan_runs.c.snapshot_id).order_by(scan_runs.c.started_at)
            ).fetchall()
            return [r.snapshot_id for r in rows]

    # ------------------------------------------------------------------
    # Graph queries
    # ------------------------------------------------------------------

    def dependency_graph(self, snapshot_id: str) -> dict[str, list[str]]:
        """Return adjacency list: resource_id → [target_ids]."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                dependencies.select().where(dependencies.c.snapshot_id == snapshot_id)
            ).fetchall()
        graph: dict[str, list[str]] = {}
        for row in rows:
            graph.setdefault(row.source_id, []).append(row.target_id)
        return graph

    def bfs_dependencies(self, snapshot_id: str, root_id: str) -> list[str]:
        """Return all resource IDs reachable from root_id via dependency edges."""
        graph = self.dependency_graph(snapshot_id)
        visited: list[str] = []
        queue: deque[str] = deque([root_id])
        seen: set[str] = {root_id}
        while queue:
            node = queue.popleft()
            visited.append(node)
            for neighbor in sorted(graph.get(node, [])):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return visited

    def orphan_resources(self, snapshot_id: str) -> list[str]:
        """Return resource IDs not assigned to any workload."""
        with self._engine.connect() as conn:
            wl_rows = conn.execute(
                workloads.select().where(workloads.c.snapshot_id == snapshot_id)
            ).fetchall()
            all_res = conn.execute(
                sa.select(resources.c.id).where(resources.c.snapshot_id == snapshot_id)
            ).fetchall()

        assigned: set[str] = set()
        for row in wl_rows:
            assigned.update(json.loads(row.resource_ids_json))

        return sorted(r.id for r in all_res if r.id not in assigned)
