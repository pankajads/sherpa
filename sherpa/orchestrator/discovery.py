"""Deterministic discovery orchestrator — no LLM calls in Phase 1.

Scanners are plugins discovered through the `sherpa.scanners` entry-point group; this module
never imports a concrete scanner (enforced by import-linter in CI).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from itertools import groupby
from pathlib import Path

from sherpa.core.interfaces import ScannerPlugin, ScanResult, load_scanners
from sherpa.core.models import (
    CoverageGap,
    InventorySnapshot,
    Pipeline,
    Repository,
    Resource,
    ScanConfig,
    ScanIdentity,
)
from sherpa.core.store import InventoryStore

from .coverage_validator import validate_coverage
from .cross_plane_linker import link_cross_plane
from .workload_inferrer import infer_workloads


async def _safe_scan(scanner: ScannerPlugin, config: ScanConfig) -> ScanResult:
    """Run one scanner; a crash becomes a recorded error and gap, never a failed run."""
    try:
        return await scanner.scan(config)
    except Exception as exc:  # noqa: BLE001 - isolate plugin crashes; recorded as error + gap
        return ScanResult(
            scanner_type=scanner.scanner_type,
            errors=[f"{scanner.scanner_type}: scan failed: {type(exc).__name__}: {exc}"],
            coverage_gaps=[
                CoverageGap(
                    description=(
                        f"Scanner {scanner.scanner_type} failed: nothing from the "
                        f"{scanner.plane} plane it covers is in this inventory"
                    ),
                    severity="error",
                )
            ],
        )


async def run_discovery(
    config: ScanConfig,
    store: InventoryStore,
    output_dir: Path | None = None,
    scanners: Sequence[ScannerPlugin] | None = None,
) -> InventorySnapshot:
    """Execute a full discovery run and persist the snapshot.

    Sequence:
    1. Load scanner plugins (unless given) and keep those that apply to this config.
    2. Validate their configs (fail fast).
    3. Run them stage by stage (cloud first by default), in parallel within a stage.
    4. Infer workloads (rule-based).
    5. Link cross-plane edges.
    6. Validate coverage.
    7. Close snapshot and persist.
    8. Optionally write output files.
    """
    load_errors: list[str] = []
    if scanners is None:
        loaded = load_scanners()
        scanners, load_errors = loaded.scanners, loaded.errors
    active = sorted(
        (s for s in scanners if s.applies_to(config)),
        key=lambda s: (s.run_stage, s.scanner_type),
    )

    # 2. Validate configs — only scanners relevant to this run
    errors: list[str] = []
    for v in await asyncio.gather(*(s.validate_config(config) for s in active)):
        if not v.valid:
            errors.extend(v.errors)
    if errors:
        raise ValueError(f"Config validation failed: {'; '.join(errors)}")

    snapshot = InventorySnapshot(config=config)

    all_resources: list[Resource] = []
    all_repos: list[Repository] = []
    all_pipelines: list[Pipeline] = []
    all_errors: list[str] = list(load_errors)
    all_gaps: list[CoverageGap] = [
        CoverageGap(
            description=f"A scanner plugin could not be loaded — {err}",
            severity="error",
        )
        for err in load_errors
    ]
    all_identities: list[ScanIdentity] = []

    # 3. Scan, stage by stage
    for _, stage in groupby(active, key=lambda s: s.run_stage):
        results = await asyncio.gather(*(_safe_scan(s, config) for s in stage))
        for result in results:
            all_resources.extend(result.resources)
            all_repos.extend(result.repositories)
            all_pipelines.extend(result.pipelines)
            all_gaps.extend(result.coverage_gaps)
            all_errors.extend(result.errors)
            all_identities.extend(result.scan_identities)

    # 4. Infer workloads using the naming convention from config
    workloads = infer_workloads(all_resources, all_repos, config.naming_convention)

    # 5. Link cross-plane edges
    linked_resources = link_cross_plane(all_resources, all_repos, all_pipelines)

    # 6. Validate coverage
    coverage_gaps = all_gaps + validate_coverage(config, linked_resources, all_errors)

    # 7. Close snapshot. Constructed (not model_copy'd) so the model's canonical ordering
    # validators run on every list.
    closed = InventorySnapshot(
        snapshot_id=snapshot.snapshot_id,
        started_at=snapshot.started_at,
        config=config,
        resources=linked_resources,
        repositories=all_repos,
        pipelines=all_pipelines,
        workloads=workloads,
        coverage_gaps=coverage_gaps,
        errors=all_errors,
        scan_identities=all_identities,
    ).close()

    store.save_snapshot(closed)

    # 8. Write output files
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = output_dir / f"snapshot_{closed.snapshot_id}.json"
        snapshot_path.write_text(closed.to_canonical_json())
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
        for severity, title in (("error", "Errors"), ("warning", "Warnings"), ("info", "Info")):
            gaps = [g for g in snapshot.coverage_gaps if g.severity == severity]
            if not gaps:
                continue
            lines += [f"### {title} ({len(gaps)})", ""]
            for gap in gaps:
                tags = " ".join(f"`{t}`" for t in (gap.error_class.value, gap.scope) if t)
                where = (
                    f" — regions: {', '.join(gap.affected_regions)}" if gap.affected_regions else ""
                )
                lines.append(f"- {tags + ' ' if tags else ''}{gap.description}{where}")
            lines.append("")

    if snapshot.scan_identities:
        lines += ["## Scanned as", ""]
        for ident in snapshot.scan_identities:
            if ident.verified:
                lines.append(f"- ✅ `{ident.scope}` — `{ident.principal}` ({ident.method})")
            else:
                lines.append(f"- ❌ `{ident.scope}` — not scanned: {ident.detail}")
        lines.append("")

    if snapshot.errors:
        lines += ["## Errors", ""]
        for err in snapshot.errors[:20]:
            lines.append(f"- `{err}`")
        if len(snapshot.errors) > 20:
            lines.append(f"- ... and {len(snapshot.errors) - 20} more")

    return "\n".join(lines)
