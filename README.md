# Sherpa
### Open Source M&A Cloud Migration Planning

A discovery-first tool for planning M&A cloud migrations. **The MVP targets AWS → AWS**; Azure and GCP come in later iterations.

**License:** MIT &nbsp;|&nbsp; **Status:** Early development — Phase 0 (hardening) / Phase 1 (MVP) &nbsp;|&nbsp; **Updated:** October 2026

> Detailed requirements review and phased plan (epics, tasks, success criteria, test methods): [`docs/planning/`](docs/planning/)

---

## Executive Summary

Every acquisition brings a new technical estate that must eventually move into the acquirer's certified cloud environment. Today this discovery and planning work is semi-automated but takes a lot of time and effort. Inventory is rebuilt from interviews and spreadsheets, cloud footprint, code and pipelines are assessed in silos, and migration strategy is chosen without a systematic view of time, resourcing or compliance constraints.

**Sherpa** is an open-source tool that automates this discovery and produces a concrete, constraint-aware migration plan. It:

- scans an acquired company's AWS accounts, GitHub repositories and GitHub Actions pipelines;
- builds one inventory linked across all three;
- assesses each workload against the acquirer's landing-zone guardrails, its **paved road** (golden paths) and compliance requirements such as GDPR;
- presents **migration options with effort estimates** per workload: relocate, lift-and-shift, re-platform onto the paved road, modernize, retire or retain. It recommends one; the team decides.

Publishing it as open source means every company facing this problem — not just ours — can use, audit and extend it, rather than depend on a single vendor's proprietary migration tooling.

---

## The Problem

- Inventory is reconstructed manually or semi-automatically — interviews and spreadsheets, not a shared system of record.
- Cloud footprint, code and CI/CD pipelines are assessed separately, by different people, with no linkage between them.
- Migration strategy is chosen ad hoc, often without regard to the actual time and resourcing constraints of the deal.
- Compliance exposure (GDPR and other regulations) is often checked late — sometimes after a migration plan is already locked in.
- The acquirer's own engineering standards aren't systematically used to steer the acquired estate toward something its teams can operate from day one.
- Access is hard: before close the acquirer usually can't scan the target's environment at all.

This repeats in full for every acquisition — the cost compounds with deal volume.

## Goals

- Automate discovery across three planes: **cloud infrastructure (AWS)**, **code repositories (GitHub)** and **CI/CD pipelines (GitHub Actions)**.
- Work both **online** (post-close, cross-account read-only access) and **offline** (the target runs the collector and hands over a reviewed, signed bundle).
- Produce one structured inventory spanning all three planes, with dependency links and provenance.
- Present per-workload **options with effort ranges**, paved-road alignment, compliance flags and risk, weighted by time and resource constraints.
- Treat compliance as a first-class constraint: findings are explicit flags, never hidden in a score.
- Keep every decision reviewable: stage gates, approvals, history and an audit trail.
- Ship fully open source and self-hosted. Deal data never leaves the acquirer's environment.

---

## How It Works

**CLI to collect, GUI to decide.** Collection is a headless, read-only CLI/container. Review, approval and planning happen in a self-hosted web app (Phase 1.5) used by the acquirer's team.

```
   ONLINE (post-close)                    OFFLINE (pre-close / no connectivity)
   sherpa discover ──► AWS + GitHub       target runs collector ─► reviews bundle ─► signed bundle
            │                                                                             │
            └──────────────────────► Snapshot (same format either way) ◄── sherpa import ─┘
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
              Inventory Store        Acquirer Target Profile     Paved-Road Catalog
              resources, repos,      regions, guardrails, CIDRs,  standard platforms,
              pipelines, workloads,  compliance, team capacity,   templates, modules +
              cross-plane edges      deadline                     mapping rules
                    └─────────────────────────┼─────────────────────────┘
                                              ▼
                              Assess (deterministic rule packs)
                  landing-zone conformance · GDPR v1 · paved-road fit · complexity
                                              ▼
                              Plan (deterministic options engine)
                 options O1–O6 per workload · effort P50/P80 · waves · feasibility
                                              ▼
              Review & Approve — stage gates, overrides, history, audit trail
                       (CLI + XLSX/reports in MVP; web GUI in Phase 1.5)
```

Every scan is stored as a snapshot, so the team can compare estates over the deal timeline. Recommendations are **deterministic**: the same inventory and constraints always produce the same output. An LLM layer (Phase 2, opt-in) explains results and answers questions, grounded in the data with citations. It never changes scores or decisions.

### Workflow (fixed stage gates)

`Discover → Review inventory → Assess → Review findings → Plan → Approve plan`

Each gate is approved by a named person and bound to the exact data version it approved. A rescan marks later approvals as stale.

---

## Migration Options

For each workload, Sherpa shows every option side by side and marks a recommended one given the constraints. **The team chooses.**

| Option | Description |
|---|---|
| **O1 Relocate** | Move the AWS account(s) as-is into the acquirer's AWS Organization; remediate guardrail violations only. Often the fastest path in AWS → AWS deals. |
| **O2 Lift-and-shift (rehost)** | Recreate the same architecture in an acquirer-vended account; copy data. |
| **O3 Re-platform onto paved road** | Swap components for the acquirer's standard equivalents (container platform, managed DB standard, CI templates, IaC modules) without a full redesign. |
| **O4 Modernize / re-architect** | Redesign to best practice and full paved-road alignment. Highest effort, best long-term fit. |
| **O5 Retire** | Decommission idle or redundant workloads. |
| **O6 Retain** | Leave in place for now (e.g. under a transition services agreement), with a revisit date. |

Each option shows viability, **effort (P50/P80 engineer-weeks) with a breakdown**, calendar duration given team capacity, paved-road alignment and remaining gaps, compliance flags, risk and key assumptions.

**Effort numbers are a starting point, not a final answer.** The team can adjust any estimate with a reason. Sherpa keeps both the tool estimate and the team estimate, and uses the difference (plus actuals, when available) to calibrate future estimates.

Inputs that weight the recommendation:

- **Time constraint** — integration deadline(s).
- **Resourcing constraint** — teams and available capacity.
- **Compliance exposure** — a blocker can make an option non-viable; conflicts are surfaced explicitly, never silently overridden.
- **Paved-road fit** — how much of the workload already matches, or maps to, the acquirer's standards.

## Compliance

GDPR and other regulations are modeled as constraints the engine checks: data residency, cross-border replication and data-processing-role changes implied by a proposed target. Sherpa can't infer where personal data lives, so it relies on data classification input (tags, a classification file, or optionally Amazon Macie). Unclassified data stores are flagged as "classification unknown", never assumed clean. Findings are explicit flags on each option. They are decision support, not legal advice.

## Security & Trust

- **Read-only.** A published least-privilege IAM policy, with a run report and CloudTrail evidence that no write calls were made.
- **Offline mode** with a human-readable manifest the target reviews before export, optional redaction, and signed, encrypted bundles.
- **No secrets collected or stored**; no telemetry; a `purge` command to delete all engagement data.
- **Self-hosted only.** The GUI is for the acquirer's team. Target-team access is an opt-in per engagement, planned for a later phase.

---

## Roadmap

| Phase | Duration | Focus |
|---|---|---|
| **Phase 0 — Harden the foundation** | 2–3 wks | Fix existing discovery code: secret handling, determinism, per-account access, plugin loading, persistence. |
| **Phase 1 — MVP: Discover → Assess → Plan (AWS → AWS)** | 12–13 wks | Online + offline collection, AWS discovery coverage, GitHub code/pipeline linking, workload curation, acquirer profile + paved-road catalog, conformance + GDPR v1 rule packs, options and effort engine, waves, stage gates and audit, reports + XLSX. Validated on a reference estate and a design partner. |
| **Phase 1.5 — Review & Approval GUI** | ~6 wks | Self-hosted web app for the acquirer's team: inventory and curation, findings review, options matrix, plan approval, history and diff, SSO + RBAC. |
| **Phase 2 — Breadth & intelligence** | ~3 mo | GitLab / Bitbucket / Jenkins, grounded chat and LLM explanations (opt-in), context store over acquirer docs, more compliance packs, target-state cost estimates. |
| **Phase 3 — Collaboration extensions** | ~2 mo | Opt-in target-team access, configurable workflows (on demand), notifications, Jira/Linear export. |
| **Phase 4 — Multi-cloud & community** | Ongoing | Azure and GCP as sources and targets, plugin SDK docs, community rule packs, governance model. |

## Getting Started (development)

Requires Python 3.12+.

```bash
pip install -e ".[dev]"
pytest
sherpa discover --aws-account 123456789012 --regions us-east-1 --github-org my-org
```

## Governance & License

Sherpa is released under the **MIT license** to maximize adoption and community contribution. Every company doing M&A hits this discovery problem. Building in the open lets anyone review and trust the scanner connectors, recommendation logic and compliance rules, rather than being locked into a single vendor's proprietary migration tooling.
