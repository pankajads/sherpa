# Roadmap

Durations assume 2–3 engineers and will be revisited after the MVP pilot. The detailed plan, with every epic, task, success criterion and test method, is in [`docs/planning/02-phased-plan.md`](https://github.com/pankajads/sherpa/blob/main/docs/planning/02-phased-plan.md). Work is tracked in [GitHub issues](https://github.com/pankajads/sherpa/issues).

| Phase | Duration | Focus | Done when |
|---|---|---|---|
| **0 — Harden the foundation** | 2–3 wks | Fix the existing discovery code: credential handling, deterministic output, correct multi-account access, real plugin loading, persistent storage. | Known defects closed; determinism and secret-leak tests in CI. |
| **1 — MVP: Discover → Assess → Plan (AWS → AWS)** | 12–13 wks | Online + offline collection, AWS discovery coverage, GitHub code/pipeline linking, workload curation, target profile + paved-road catalog, conformance + GDPR rule packs, options and effort engine, waves, stage gates, reports + Excel. | Measured on a reference estate and a design partner: ≥ 98% resource recall, zero write API calls, deterministic output, plan accepted by an integration lead. |
| **1.5 — Review & Approval GUI** | ~6 wks | Self-hosted web app for the acquirer's team: inventory and curation, findings review, options matrix, plan approval, history and diff, SSO. | Design-partner users complete every gate without the CLI; zero access-control leaks. |
| **2 — Breadth & intelligence** | ~3 mo | GitLab, Bitbucket, Jenkins; grounded chat and explanations (opt-in, local-model option); context store over your docs; more compliance packs; target-state cost estimates. | Chat ≥ 90% accurate on an evaluation set, every claim cited. |
| **3 — Collaboration** | ~2 mo | Opt-in target-team access (inventory only), configurable workflows if customers ask, notifications, Jira/Linear export. | Target access passes field-level access tests and a pen-test. |
| **4 — Multi-cloud & community** | Ongoing | Azure and GCP as sources and targets, plugin SDK, community rule packs, governance model. | An external contributor ships a connector without core changes. |

## How we measure

Every MVP claim is measured against a **reference estate**: a sandbox "acquired company" built with Terraform (3 AWS accounts, ~300 resources, 20 GitHub repos, deliberately messy) plus a machine-readable answer key. It is rebuilt, scanned and scored nightly.

## Current focus

Phase 0 hardening and building the reference estate. See issues labelled [`phase-0`](https://github.com/pankajads/sherpa/issues?q=label%3Aphase-0) and [`phase-1`](https://github.com/pankajads/sherpa/issues?q=label%3Aphase-1).
