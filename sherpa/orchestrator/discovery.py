"""Deterministic discovery orchestrator — no LLM calls in Phase 1."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sherpa.core.models import InventorySnapshot, Pipeline, Repository, Resource, ScanConfig
from sherpa.core.store import InventoryStore
from sherpa.scanners.cloud.aws.scanner import AwsCloudScanner
from sherpa.scanners.code.github.scanner import GithubCodeScanner
from sherpa.scanners.pipeline.github_actions.scanner import GithubActionsScanner

from .coverage_validator import validate_coverage
from .cross_plane_linker import link_cross_plane
from .workload_inferrer import infer_workloads


async def run_discovery(
    config: ScanConfig,
    store: InventoryStore,
    output_dir: Path | None = None,
) -> InventorySnapshot:
    """Execute a full discovery run and persist the snapshot.

    Sequence:
    1. Validate all scanner configs (fail fast).
    2. Open snapshot.
    3. Run cloud scanner first (sequential), then code + pipeline in parallel.
    4. Infer workloads (rule-based).
    5. Link cross-plane edges.
    6. Validate coverage.
    7. Close snapshot and persist.
    8. Optionally write output files.
    """
    aws_scanner = AwsCloudScanner()
    code_scanner = GithubCodeScanner()
    pipeline_scanner = GithubActionsScanner()

    # 1. Validate configs — only validate scanners relevant to this run
    errors: list[str] = []
    coros = []
    if config.aws_accounts:
        coros.append(aws_scanner.validate_config(config))
    if config.github_org:
        coros.append(code_scanner.validate_config(config))
        coros.append(pipeline_scanner.validate_config(config))
    for v in await asyncio.gather(*coros):
        if not v.valid:
            errors.extend(v.errors)
    if errors:
        raise ValueError(f"Config validation failed: {'; '.join(errors)}")

    # 2. Open snapshot
    snapshot = InventorySnapshot(config=config)

    all_resources: list[Resource] = []
    all_repos: list[Repository] = []
    all_pipelines: list[Pipeline] = []
    all_errors: list[str] = []

    # 3a. Cloud scanner first
    if config.aws_accounts:
        cloud_result = await aws_scanner.scan(config)
        all_resources.extend(cloud_result.resources)
        all_errors.extend(cloud_result.errors)

    # 3b. Code + pipeline scanners in parallel
    if config.github_org:
        code_result, pipeline_result = await asyncio.gather(
            code_scanner.scan(config),
            pipeline_scanner.scan(config),
        )
        all_repos.extend(code_result.repositories)
        all_pipelines.extend(pipeline_result.pipelines)
        all_errors.extend(code_result.errors)
        all_errors.extend(pipeline_result.errors)

    # 4. Infer workloads using the naming convention from config
    workloads = infer_workloads(all_resources, all_repos, config.naming_convention)

    # 5. Link cross-plane edges
    linked_resources = link_cross_plane(all_resources, all_repos, all_pipelines)

    # 6. Validate coverage
    coverage_gaps = validate_coverage(config, linked_resources, all_errors)

    # 7. Close snapshot
    closed = snapshot.model_copy(
        update={
            "resources": sorted(linked_resources, key=lambda r: r.id),
            "repositories": sorted(all_repos, key=lambda r: r.id),
            "pipelines": sorted(all_pipelines, key=lambda p: p.id),
            "workloads": workloads,
            "coverage_gaps": coverage_gaps,
            "errors": all_errors,
        }
    ).close()

    store.save_snapshot(closed)

    # 8. Write output files
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = output_dir / f"snapshot_{closed.snapshot_id}.json"
        snapshot_path.write_text(closed.model_dump_json(indent=2))
        report_path = output_dir / f"report_{closed.snapshot_id}.md"
        report_path.write_text(_render_report(closed))

    return closed


def _render_report(snapshot: InventorySnapshot) -> str:
    lines = [
        "# Sherpa Discovery Report",
        "",
        f"**Snapshot ID:** `{snapshot.snapshot_id}`",
        f"**Started:** {snapshot.started_at.isoformat()}",
        f"**Completed:** {snapshot.completed_at.isoformat() if snapshot.completed_at else 'N/A'}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Resources | {len(snapshot.resources)} |",
        f"| Repositories | {len(snapshot.repositories)} |",
        f"| Pipelines | {len(snapshot.pipelines)} |",
        f"| Workloads | {len(snapshot.workloads)} |",
        f"| Coverage gaps | {len(snapshot.coverage_gaps)} |",
        f"| Errors | {len(snapshot.errors)} |",
        "",
    ]
    if snapshot.workloads:
        lines += ["## Workloads", ""]
        for wl in snapshot.workloads:
            lines.append(
                f"- **{wl.name}** — {len(wl.resource_ids)} resource(s), "
                f"{len(wl.repo_ids)} repo(s), inferred from: {wl.inferred_from}"
            )
        lines.append("")

    if snapshot.coverage_gaps:
        lines += ["## Coverage Gaps", ""]
        for gap in snapshot.coverage_gaps:
            severity_marker = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(gap.severity, "•")
            lines.append(f"- {severity_marker} {gap.description}")
        lines.append("")

    if snapshot.errors:
        lines += ["## Errors", ""]
        for err in snapshot.errors[:20]:
            lines.append(f"- `{err}`")
        if len(snapshot.errors) > 20:
            lines.append(f"- ... and {len(snapshot.errors) - 20} more")

    return "\n".join(lines)
