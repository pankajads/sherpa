# Sherpa
### Open Source Planning Document

A discovery-first agent for planning M&A cloud migrations — across AWS, Azure, and GCP.

**License:** MIT &nbsp;|&nbsp; **Status:** Planning &nbsp;|&nbsp; **Date:** July 2026

---

## Executive Summary

Every acquisition brings a new technical estate that must eventually move into the acquirer's certified cloud environment — AWS, Azure, or GCP, chosen by vendor relationship rather than by what the acquired company already runs. Today this discovery and planning work is manual: inventory is rebuilt from interviews and spreadsheets, cloud footprint and code and pipelines are assessed in silos, and migration strategy is chosen without a systematic view of time, resourcing, or compliance constraints.

**Sherpa** is a proposed open-source, agent-driven tool that automates this discovery and produces a concrete, constraint-aware migration plan. It scans an acquired company's cloud accounts, code repositories, and CI/CD pipelines; builds a unified inventory; and recommends — per workload — whether to lift-and-shift, re-platform, or re-architect, weighted by the time and resources available and by applicable compliance requirements (GDPR and others). It also accepts the acquirer's own libraries, pipeline templates, and process documentation, so recommendations favor patterns the acquiring company's teams already know and trust.

Publishing it as open source means every company facing this problem — not just ours — can use, audit, and extend it, rather than depend on a single vendor's proprietary migration tooling.

---

## The Problem

- Inventory is reconstructed manually — interviews and spreadsheets, not a shared system of record.
- Cloud footprint, code, and CI/CD pipelines are assessed separately, by different people, with no linkage between them.
- Migration strategy (lift-and-shift vs. re-platform vs. re-architect) is chosen ad hoc, often without regard to the actual time and resourcing constraints of the deal.
- Compliance exposure (GDPR and other regulations) is often checked late — sometimes after a migration plan is already locked in.
- The acquirer's own engineering standards aren't systematically used to steer the acquired estate toward something its teams can operate day one.

This repeats in full for every acquisition — the cost compounds with deal volume.

## Goals

- Automate discovery across three planes: **cloud infrastructure**, **code repositories**, and **CI/CD pipelines**.
- Produce one structured inventory spanning all three, with dependency links between them.
- Recommend a migration path per workload, weighted by user-supplied time and resource constraints.
- Treat compliance (GDPR and other applicable regulations) as a first-class input to every recommendation, not an afterthought.
- Let the acquirer upload its own libraries, pipeline templates, and process docs to bias recommendations toward internal standards.
- Ship fully open source, vendor-neutral across AWS, Azure, and GCP.

---

## How It Works

Sherpa is agent-driven at its core: an LLM orchestrator plans discovery, reasons over the combined findings, and drives recommendations — with a web dashboard as the human interface for review and constraint input.

```
                     Web Dashboard
   Inventory views · migration plans · compliance flags
        constraint inputs · context uploads
                         │
                         ▼
              Agent Orchestrator (LLM core)
   Plans scans · reasons over findings · drives
        recommendations · explains rationale
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
  Cloud Scanner    Code Scanner    Pipeline Scanner
  AWS/Azure/GCP    IaC, SDKs,      Jenkins, GitHub
  inventory        deps, k8s       Actions, etc.
         │               │               │
         └───────┬───────┴───────┬───────┘
                  ▼               ▼
          Inventory Store   Context Store
          Unified model,    Company libraries,
          cross-plane links templates, docs (RAG)
                  └───────┬───────┘
                          ▼
               Recommendation Engine
        Path scoring + compliance checks per workload
```

---

## Migration Recommendations

For each discovered workload, Sherpa recommends one of three paths — the choice is weighted, not fixed, by the constraints the user provides.

| Path | Description |
|---|---|
| **Lift-and-shift (rehost)** | Fastest, lowest short-term effort. Favored under tight time constraints. |
| **Re-platform** | Swaps in managed-service equivalents without a full redesign. A middle ground. |
| **Re-architect** | Adopts target-cloud best practices and the acquirer's own standards. Highest effort, best long-term fit. |

Weighting inputs per workload:

- **Time constraint** the user provides for the integration.
- **Resourcing constraint** — available engineering capacity.
- **Compliance exposure** — a regulated workload may be pushed toward re-platform/re-architect regardless of time pressure, with any conflict surfaced explicitly rather than silently overridden.
- **Fit with the acquirer's uploaded standards** — existing library/pipeline match reduces effort and nudges toward the more modernized option.

## Compliance

GDPR and other applicable regulations are modeled as constraints the recommendation engine checks against — data residency, data-processing-role changes, and cross-border transfer implications of a proposed target region or service. Findings are surfaced as explicit flags on each recommendation, never silently absorbed into an effort score.

---

## Roadmap

| Phase | Duration | Focus |
|---|---|---|
| **Phase 0 — Foundations** | 4–6 wks | Inventory data model, scanner plugin interface, repo scaffolding, orchestrator skeleton. |
| **Phase 1 — Discovery MVP** | 2–3 mo | Cloud connectors (AWS/Azure/GCP), code scanner (Terraform, deps, containers), pipeline scanner (GitHub Actions, Jenkins), unified inventory live end-to-end. |
| **Phase 2 — Recommendation Engine + Compliance** | 2–3 mo | Path scoring, GDPR rule set v1, context store v1 for company docs/libraries. |
| **Phase 3 — Dashboard + Usability** | 1–2 mo | Inventory browser, migration plan view, constraint inputs, audit trail for recommendations. |
| **Phase 4 — Community & Extensibility** | Ongoing | Plugin SDK docs, more connectors, additional compliance rule packs, governance model. |

## Governance & License

Sherpa will be released under the **MIT license** to maximize adoption and community contribution. Every company doing M&A hits this discovery problem — building in the open lets the scanner connectors, recommendation logic, and compliance rules be reviewed and trusted by anyone using it, rather than locked into a single vendor's proprietary migration tooling.
