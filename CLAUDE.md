# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Sherpa** is an open-source, agent-driven M&A cloud migration planning tool. It automates discovery across an acquired company's cloud infrastructure, code repositories, and CI/CD pipelines, then produces constraint-aware migration plans. Status: **pre-code / planning phase** (as of August 2026).

## Architecture

Three-plane scanner feeds into a unified inventory; an LLM orchestrator reasons over it and drives recommendations; a web dashboard is the human interface.

```
Web Dashboard
    │
    ▼
Agent Orchestrator (LLM core)
    │
    ├── Cloud Scanner   (AWS / Azure / GCP)
    ├── Code Scanner    (IaC, deps, containers)
    └── Pipeline Scanner (GitHub Actions, Jenkins, …)
            │
    ┌───────┴────────┐
    ▼                ▼
Inventory Store   Context Store (RAG over acquirer's docs/libs)
    └───────┬────────┘
            ▼
   Recommendation Engine
   (path scoring + compliance checks per workload)
```

### Key domain concepts

- **Workload** — the unit of recommendation. Each workload gets one of: lift-and-shift, re-platform, or re-architect.
- **Inventory Store** — unified model linking cloud resources, code, and pipelines across all three planes with dependency edges.
- **Context Store** — RAG index over acquirer-uploaded libraries, pipeline templates, and process docs. Used to bias recommendations toward internal standards.
- **Recommendation Engine** — scores paths per workload against four inputs: time constraint, resourcing constraint, compliance exposure, and fit with acquirer standards.
- **Compliance layer** — GDPR and other regulations are first-class inputs. Conflicts are surfaced as explicit flags, never silently absorbed into an effort score.

### Phased roadmap

| Phase | Focus |
|---|---|
| 0 — Foundations | Inventory data model, scanner plugin interface, orchestrator skeleton |
| 1 — Discovery MVP | Cloud connectors (AWS/Azure/GCP), code/pipeline scanners, live end-to-end inventory |
| 2 — Recommendation + Compliance | Path scoring, GDPR rule set v1, context store v1 |
| 3 — Dashboard | Inventory browser, migration plan view, constraint inputs, audit trail |
| 4 — Community | Plugin SDK, more connectors, additional compliance rule packs |

## Design Principles

- **Scanner plugin interface** — scanners are pluggable. A new connector (cloud, SCM, CI/CD) should implement the plugin interface without touching core logic.
- **Vendor-neutral** — no AWS/Azure/GCP SDK should leak into shared orchestration or recommendation logic. Cloud-specific code stays in connector plugins.
- **Compliance as constraint, not score** — compliance findings must be surfaced as explicit flags on each recommendation; they must never be silently downweighted.
- **Deterministic recommendations** — given the same inventory + constraints, the engine must produce the same output. LLM reasoning is used for explanation and planning, not non-deterministic scoring.
- **AWS → AWS first** — the first integration target is same-cloud (AWS to AWS). Multi-cloud and on-premise come later and must not drive early API design.
