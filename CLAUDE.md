# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Sherpa** is an open-source M&A cloud migration planning tool. It discovers an acquired company's cloud infrastructure, code repositories and CI/CD pipelines, assesses them against the acquirer's guardrails, paved road and compliance requirements, and presents per-workload migration options with effort estimates.

**Status (October 2026):** early development. A Phase-1 discovery skeleton exists (AWS, GitHub, GitHub Actions) and is being hardened (Phase 0). The source of truth for scope, epics, success criteria and test methods is [`docs/planning/02-phased-plan.md`](docs/planning/02-phased-plan.md); the requirements review and known defects are in [`docs/planning/01-requirements-review.md`](docs/planning/01-requirements-review.md).

## MVP scope (decided)

- **AWS → AWS only.** Azure/GCP are Phase 4 and must not drive MVP API design.
- **GitHub (cloud + Enterprise Server) is the only SCM; GitHub Actions the only CI/CD scanner.**
- **Online and offline collection.** Online = post-close cross-account read-only access. Offline = the target runs the collector and hands over a reviewed, signed bundle. Both produce the same snapshot format.
- **CLI to collect, GUI to decide.** MVP is headless (CLI + reports + XLSX); the web GUI is Phase 1.5, for the **acquirer's team only**.
- **No LLM in the MVP.** Grounded, read-only chat/explanations come in Phase 2, opt-in.

## Architecture

```
Collect (CLI/container, read-only)
  ├── Cloud scanner    (AWS)
  ├── Code scanner     (GitHub: IaC, deps, containers)
  └── Pipeline scanner (GitHub Actions)
        │  online: write to store   offline: signed bundle → sherpa import
        ▼
Inventory Store (snapshots, append-only)  +  Acquirer Target Profile  +  Paved-Road Catalog
        ▼
Assess  — deterministic rule packs (landing-zone conformance, GDPR v1, paved-road fit, complexity)
        ▼
Plan    — deterministic options engine (O1–O6 per workload, effort P50/P80, waves, feasibility)
        ▼
Engagement + stage gates + audit log  ──►  CLI / reports / XLSX  (GUI in Phase 1.5 via the same API)
```

### Key domain concepts

- **Engagement** — one acquisition. All data is isolated per engagement.
- **Snapshot** — immutable result of one discovery run (online or offline); snapshots can be diffed.
- **Workload** — the unit of assessment and planning. Inferred from tags → stack/module → naming convention → graph clustering; humans can override (overrides survive rescans).
- **Acquirer Target Profile** — allowed regions, guardrails/SCPs, reserved CIDRs, required tags, team capacity, deadlines.
- **Paved-Road Catalog** — the acquirer's standard platforms, templates and modules, with mapping rules from source patterns. Each workload component is classified as aligned / mappable / gap.
- **Options** — per workload: O1 Relocate (account into acquirer Org), O2 Lift-and-shift, O3 Re-platform onto paved road, O4 Modernize/re-architect, O5 Retire, O6 Retain. Each has viability, effort range + breakdown, duration, paved-road alignment, compliance flags, risk and assumptions. One is marked recommended; humans choose.
- **Effort** — the tool estimate is reproducible and never overwritten; the team can add an adjusted estimate (with author + reason). Plans use the team estimate when present; the delta feeds calibration.
- **Stage gates** — fixed: `Discover → Review inventory → Assess → Review findings → Plan → Approve plan`. Approvals bind to an exact data version; a rescan marks downstream approvals stale.

## Design Principles

- **Scanner plugin interface** — scanners are discovered via the `sherpa.scanners` entry point. The orchestrator must not import concrete scanners. A new connector must not require core changes.
- **Vendor-neutral core** — no cloud SDK in shared orchestration, assessment or planning logic; cloud-specific code stays in connector plugins.
- **Read-only, always** — collectors only call read APIs. Never collect secret values, env vars or object contents.
- **Never serialise credentials** — tokens/keys come from env or credential providers and must never appear in snapshots, reports, bundles, logs or the DB.
- **Compliance as constraint, not score** — compliance findings are explicit flags that can make an option non-viable or "viable with conflict"; they are never silently downweighted. Waivers keep the finding visible.
- **Deterministic** — the same inventory + profile + catalog + constraints produce byte-identical output (excluding snapshot IDs/timestamps). IDs are content-derived; all lists are stably sorted. LLMs (Phase 2) may explain, never score.
- **Surface, don't swallow** — every caught error becomes a recorded coverage gap; no silent `except: pass`.
- **Provenance everywhere** — every inferred fact/edge carries its method and confidence.
- **One service layer** — business logic lives in the core and is exposed via one versioned API; the CLI and the future GUI are thin clients. No business logic in the frontend.
- **Self-hosted, no telemetry** — deal data never leaves the acquirer's environment unless explicitly configured.

## Development

- Python **3.12+** required (`requires-python >=3.12`).
- Install: `pip install -e ".[dev]"`
- Lint/format: `ruff check . && ruff format --check .`
- Tests: `pytest` (CI enforces coverage ≥ 70%; the plan targets ≥ 80% on changed packages)
- AWS integration tests use moto; LocalStack is available via `docker-compose.yml`.
- Every task in the plan has explicit success criteria and a test method; a change is done only when those tests are in CI.

## Roadmap

| Phase | Focus |
|---|---|
| 0 — Harden foundation | Secrets, determinism, per-account access, plugin loading, persistence |
| 1 — MVP (AWS → AWS) | Discover → Assess → Plan, online + offline, options + effort, stage gates, reports/XLSX |
| 1.5 — GUI | Acquirer-only review & approval web app over the same API |
| 2 — Breadth & intelligence | GitLab/Bitbucket/Jenkins, grounded chat (opt-in), context store, more compliance packs |
| 3 — Collaboration | Opt-in target-team access, configurable workflows, notifications, Jira export |
| 4 — Multi-cloud & community | Azure/GCP, plugin SDK, community rule packs |
