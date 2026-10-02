# Sherpa — Phased Implementation Plan

**Date:** 2026-10-02 · **Status:** Proposal for review (no implementation started) · **Input:** [01-requirements-review.md](01-requirements-review.md)

---

## 0. How this plan is structured

- **Phase 0 — Harden the foundation** (2–3 wks): fix the confirmed defects in the existing code before anyone builds on them.
- **Phase 1 — MVP "Discover → Assess → Plan" for AWS→AWS** (12–13 wks): headless (CLI + XLSX + reports), online **and** offline collection, stage gates and approvals in the backend. It's the first version a customer can run on a real acquisition and get a plan they can present.
- **Phase 1.5 — Review & Approval GUI** (≈ 6 wks): a self-hosted web app for the acquirer's team, with read-only inventory access for the target's team. It's a thin client over the Phase 1 backend.
- **Phase 2 — Breadth & intelligence (incl. grounded chat)** · **Phase 3 — Collaboration extensions** · **Phase 4 — Multi-cloud & community**: epic level only, to be detailed once Phase 1 feedback is in.

### Decisions recorded (2026-10-02)
| # | Decision | Consequence |
|---|---|---|
| D-1 | **AWS only** in MVP; Azure/GCP in later iterations. | README roadmap (Phase 1 = three clouds) is superseded. |
| D-2 | **Both online and offline collection.** Online once the deal has closed and cross-account access is established; offline (target-run bundle) otherwise. | Both modes produce the *same* snapshot format; everything downstream is mode-agnostic. |
| D-3 | **CLI to collect, GUI to decide.** Collection stays a headless CLI/container. Review, approval, history and planning go in the GUI. | GUI ships as fast follow (Phase 1.5) after data quality is validated with the CLI MVP. |
| D-4 | **Fixed stage gates, not user-defined workflows**, in MVP: `Discover → Review inventory → Assess → Review findings → Plan → Approve plan`. | Configurable workflows deferred to Phase 3, only if customers ask. |
| D-5 | **GUI is acquirer-only for now.** Target-team access is a later, per-engagement decision based on the deal situation. When enabled, the target sees inventory + curation only — never the assessment, options, plan, acquirer profile or approvals. | No target role in Phase 1.5. The authz model is built so a target role can be added later (Phase 3) without redesign. The target still gets the offline bundle preview before export (P1.5-7.2) — that's their own data, on their own machine. |
| D-8 | **The tool presents options with effort, not a single answer.** Per workload: relocate, lift-and-shift, re-platform onto the acquirer's paved road, modernize, retire, retain — each with effort range, duration, paved-road alignment, compliance flags and risk. Sherpa marks a recommended option given constraints; a human chooses. | P1-E8 redesigned as an options matrix; new paved-road catalog (P1-6.3/6.4). |
| D-6 | **Chat is Phase 2, read-only, grounded and opt-in**: answers via tool queries over the store, cites resource IDs, can't change scores/paths/approvals, with a local-model option. | Determinism principle preserved; no deal data leaves the environment unless opted in. |
| D-7 | **Self-hosted only** (single Docker Compose deployment in the acquirer's environment); no Sherpa-operated SaaS. | No outbound network calls from the app other than to configured AWS/GitHub/LLM endpoints. |

The MVP is three CLI modes, each producing a durable artifact the next one consumes:

```
sherpa discover  ──► snapshot (inventory + graph)        "What do they have?"
sherpa assess    ──► assessment (facts, gaps, flags)     "What's the risk and what must change?"
sherpa plan      ──► plan (path per workload, waves)     "What do we do, in what order, by when?"
```

Each task has: **ID · What · Success criteria (measurable) · Test method**. A task is done only when its tests are in CI and its success criteria are demonstrated.

### Global Definition of Done (applies to every task)
1. Code + tests merged; CI green (ruff, format, pytest, coverage ≥ 80% on changed packages).
2. The determinism golden test still passes (P0-E2) — no output drift unless intentional and reviewed.
3. No secrets in any output (the secret-scan test, P0-E1, passes).
4. User-facing behaviour documented in `docs/`.

### Test layers referenced below
| Layer | Tool | Purpose |
|---|---|---|
| Unit | pytest, moto | Pure logic, collectors against mocked AWS. |
| Property | hypothesis | Inference/linking rules hold for generated inputs (e.g. idempotence, order-independence). |
| Contract | pytest | Every `ScannerPlugin` / rule pack satisfies the interface spec. |
| Golden | pytest + checked-in fixtures | Same input ⇒ byte-identical output (determinism). |
| Integration | LocalStack, recorded GitHub API fixtures (`responses`) | Scanner ↔ API behaviour. |
| **Reference estate E2E** | Terraform-deployed AWS sandbox org (3 accounts, 2 regions, ~300 resources, 20 repos with known ground truth) | Measures recall/precision of discovery, linking and workload inference against a known answer key. Nightly + pre-release. |
| Security | CloudTrail log analysis, secret scanning, `pip-audit` | Read-only proof, no secret leakage, dependency CVEs. |
| Performance | Synthetic 10k-resource fixture + rate-limit simulator | Runtime and API-budget targets. |
| Usability | Moderated pilot with design partner | Real-user success criteria. |

> **The reference estate is the single most important test asset in this plan.** Without a ground-truth answer key, "recall", "precision" and "plan quality" are opinions. It is built in P1-E0, before feature work.

---

## Phase 0 — Harden the foundation (2–3 weeks)

**Phase exit criteria:** every finding C-1…C-14 in the review is closed or explicitly deferred with a ticket; determinism and secret-leak tests run in CI.

### P0-E1 Secrets & security hygiene
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P0-1.1 | Remove credentials from `ScanConfig` serialisation. Tokens come from env/credential providers only (`SecretStr`, `exclude=True`). | No token/key string appears in snapshot JSON, report or DB for any config. | Unit test with sentinel secrets ⇒ grep all outputs. Run `gitleaks`/`detect-secrets` over the test output dir in CI. |
| P0-1.2 | Replace silent `except: pass` with recorded `CoverageGap`s carrying the scanner, service, region and error class. | Every caught exception yields a gap entry; zero bare `pass` handlers (lint rule). | Fault-injection unit tests per collector (moto raising `AccessDenied`, throttling) assert a gap is emitted. Ruff `S110`/`BLE` rules enabled. |
| P0-1.3 | Add `pip-audit` and dependency pinning (lock file). | CI fails on known high CVEs. | CI job. |

### P0-E2 Determinism
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P0-2.1 | Content-derived IDs for workloads (hash of name + convention) and stable ordering of all lists, edges and gaps. | Two runs on identical inputs ⇒ byte-identical outputs after masking `snapshot_id`/timestamps. | Golden test: run discovery on a fixed fixture twice, and with shuffled API response order ⇒ diff is empty. Hypothesis: permuting input order doesn't change output. |

### P0-E3 Correct multi-account access
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P0-3.1 | Per-account role ARN template (`arn:aws:iam::{account}:role/SherpaReadOnly`). One AssumeRole per account, cached and refreshed before expiry. | N accounts ⇒ exactly N AssumeRole calls per run. Resources are labelled with the account that actually owns them (taken from the ARN, not the loop variable). | moto multi-account test: 3 accounts with distinct resources ⇒ no cross-contamination. Call-count assertion on STS. |
| P0-3.2 | Validation: assert `sts:GetCallerIdentity` account == expected account after assuming. | Mismatch ⇒ hard error, not silently wrong data. | Unit test with a mismatched role. |

### P0-E4 Plugin interface actually used
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P0-4.1 | Orchestrator resolves scanners via `load_scanners()` + scanner-declared capability (plane, required config). No direct scanner imports in `orchestrator/`. Load failures are reported, not swallowed. | A dummy plugin registered via entry point in a test package runs end-to-end without core changes. | Contract test suite every plugin must pass; import-linter rule forbids `orchestrator → scanners.*` imports. |

### P0-E5 Persistence & CLI baseline
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P0-5.1 | Default DB path to `./.sherpa/sherpa.db`; add schema version + migrations. | Rescans append snapshots; `sherpa snapshots list` shows history. | CLI tests via `click.testing.CliRunner` (CLI coverage from 0% to ≥ 80%). |
| P0-5.2 | Update CLAUDE.md status (no longer "pre-code"). | Docs reflect reality. | Review. |

---

## Phase 1 — MVP: Discover → Assess → Plan, AWS→AWS (10–12 weeks)

### MVP scope (in / out)
**In:** AWS source, AWS target (the acquirer's landing zone); GitHub (cloud + Enterprise Server) and GitHub Actions; online and offline collection; CLI + reports + XLSX; deterministic rules engine; human overrides; engagement, stage gates, approvals and an immutable audit log (headless, CLI-operable).
**Out (deferred):** Azure/GCP (Phase 4), web GUI (Phase 1.5), GitLab/Bitbucket/Jenkins, LLM/RAG/chat (Phase 2), configurable workflows and Jira integration (Phase 3).

**Architecture rule for the whole MVP:** all business logic (curation, findings review, gates, approvals) lives in a core service layer exposed through one versioned HTTP API, plus the CLI calling the same layer. The Phase 1.5 GUI must be a pure client of that API. This is what makes a 6-week GUI realistic.

### MVP exit criteria (measured on the reference estate + 1 design partner)
| # | Criterion | Target |
|---|---|---|
| X-1 | Resource recall vs ground truth (in-scope services) | ≥ 98% |
| X-2 | Workload assignment accuracy, before / after ≤ 1 h of human curation | ≥ 70% / ≥ 95% |
| X-3 | Cross-plane link precision / recall (repo↔resource, pipeline↔account/role) | ≥ 90% / ≥ 75% |
| X-4 | Zero write API calls during a run | 0 non-read events in CloudTrail |
| X-5 | Full run: 50 accounts × 4 regions × ~10k resources + 300 repos | < 60 min, resumable |
| X-6 | Determinism | Byte-identical outputs on rerun |
| X-7 | Design partner produces a plan the integration lead accepts for a steering-committee review | Yes, with ≤ 20% of workload paths manually overridden |
| X-8 | Time from access granted to first plan | ≤ 1 working day |

---

### P1-E0 Reference estate & answer key (build first)
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-0.1 | Terraform for a sandbox "acquired company": 3 accounts in an AWS Org, 2 regions, ~300 resources across all in-scope services, intentional messiness (untagged resources, inconsistent names, idle resources, a public bucket, a CIDR overlapping the "acquirer" plan, PII-classified buckets in a non-allowed region). | `terraform apply` from scratch in < 30 min; `destroy` leaves zero resources; monthly cost ≤ agreed budget. | Nightly apply/scan/destroy job; a cost alarm on the sandbox account. |
| P1-0.2 | 20 GitHub repos (Terraform, CDK, CloudFormation, plain app code, workflows with OIDC role assumption) in a test org. | Repos are reproducible from a seed script. | Script idempotence check. |
| P1-0.3 | Machine-readable **answer key**: expected resources, workload membership, links, conformance gaps, compliance flags, expected path per workload. | Every MVP exit metric can be computed automatically against it. | A scoring script outputs X-1…X-3 metrics; it runs in the nightly job, with trend tracking. |

---

### MODE 1 — DISCOVER

#### P1-E1 Secure onboarding & access
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-1.1 | Publish a least-privilege IAM policy (+ CloudFormation StackSet and Terraform module) for `SherpaReadOnly`. | Policy grants no `Put*/Create*/Delete*/Update*`; every API Sherpa calls is covered. | Static check: parse the policy and assert only read verbs. Run the full scan with *only* this policy on the reference estate ⇒ zero AccessDenied. |
| P1-1.2 | `sherpa preflight`: checks credentials, role assumption, per-service permissions, enabled regions, and GitHub token scopes. Prints a go/no-go table. | Detects each deliberately removed permission and names it. | Matrix test: remove one permission at a time (moto/IAM simulator) ⇒ correct missing permission reported. |
| P1-1.3 | AWS Organizations account + enabled-region enumeration (opt-in), with include/exclude filters. | Finds 100% of accounts in the reference org. | Reference E2E. |
| P1-1.4 | Read-only proof: post-run report section listing API calls made; documented CloudTrail query. | X-4 holds. | Nightly job queries CloudTrail for the Sherpa session name ⇒ asserts `readOnly=true` for all events. |

#### P1-E2 AWS discovery coverage (assessment-grade attributes)
Collect what Assess and Plan actually need, not just names.
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-2.1 | Add collectors: ECS services/task defs, EKS (clusters, node groups), ELBv2 + target groups, Auto Scaling groups, EBS, ElastiCache, Route53 zones/records, API Gateway, CloudFront, EventBridge, KMS keys (metadata), Secrets Manager (metadata only, never values), VPC peering/TGW attachments/VPN, ACM certs. | Each in-scope type is discovered with recall ≥ 98% on the reference estate. | Per-collector moto unit tests + reference E2E recall per type. |
| P1-2.2 | Collect assessment attributes: encryption status, public exposure (SG 0.0.0.0/0, public buckets/ACLs, public RDS), engine/runtime versions (EOL detection), instance families, VPC CIDRs, S3 tags. | Attributes populated for ≥ 95% of resources where the API exposes them. | Schema-completeness check in the scoring script. |
| P1-2.3 | Intra-cloud dependency edges: ELB→targets, ECS service→task def→image, Lambda→event sources/role, SG references, Route53 record→target. | Edge precision ≥ 95% against the answer key. | Reference E2E edge scoring. |
| P1-2.4 | Rate-limit handling (adaptive retry, per-service concurrency caps) + resumable runs (checkpoint per account/region/service). | Killing a run mid-way and resuming ⇒ same final snapshot as an uninterrupted run. Zero unhandled throttling errors at 10k resources. | Fault-injection test (kill at random checkpoint); throttling simulator in the performance suite. |
| P1-2.5 | Cost per account/service/tag via Cost Explorer (last 3 months); idle signals via CloudWatch (CPU/requests over 14 days) for EC2/RDS/ELB/Lambda. | Cost totals within 1% of the Cost Explorer console for the sandbox. | Reference E2E comparison; moto/stub for unit tests. |

#### P1-E3 Code & pipeline linkage that works on real repos
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-3.1 | Link by **CloudFormation/CDK stack tags** (`aws:cloudformation:stack-name`) ↔ templates in repos. | Every CFN/CDK-managed resource in the reference estate is linked to its repo. | Reference E2E link recall. |
| P1-3.2 | Link by **Terraform**: parse `.tf` resource blocks + optional state file / remote-state metadata import; match on type+name/tags. | Terraform-managed links recall ≥ 75%, precision ≥ 90%. | Fixture repos with known mappings; reference E2E. |
| P1-3.3 | Pipeline linking via **OIDC role ARN** in `configure-aws-credentials`, ECR image push → ECS/Lambda image URI, and explicit stack/function names. Remove the "any 12-digit number ⇒ link all account resources" rule. | False pipeline edges reduced to < 10% of edges. | Reference E2E edge precision; regression test asserting no account-wide fan-out. |
| P1-3.4 | Every edge carries `method` + `confidence` (high/medium/low). | 100% of edges have provenance. | Schema test. |
| P1-3.5 | GitHub scanner efficiency: tree-based filtering, only fetch candidate files, per-path content map, ETag caching, GHES base-URL support. | 300 repos within GitHub's hourly rate limit, at ≤ 40% of budget. | Recorded-response test counting API calls; performance suite. |

#### P1-E4 Collection modes: online & offline
**Online:** used post-close, once the cross-account role (P1-E1) is established; Sherpa scans directly and writes the snapshot to the acquirer's store.
**Offline:** used pre-close or when no connectivity is agreed; the target runs the collector in its own environment, reviews the bundle, and hands it over. Both modes must produce an identical snapshot for the same estate.

| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-4.0 | Mode parity: one collector code path; online writes to the store, offline writes a bundle. Snapshot records `collection_mode` + collector version. | Online scan and offline export→import of the same reference estate ⇒ identical snapshots (excluding mode/ID/timestamps). | Reference E2E parity test, nightly. |
| P1-4.1 | `sherpa discover --export bundle.sherpa`: a signed, encrypted (age/GPG recipient = acquirer key) archive of the snapshot with a human-readable manifest the target can review before sending. | The target can inspect the full contents in plain text before encrypting. The bundle is tamper-evident. | Unit: tamper ⇒ import refused. Round-trip test: export → import ⇒ identical snapshot. |
| P1-4.2 | Redaction profile (`--redact names,tags,accounts`) with stable pseudonyms for pre-close sharing. | No original identifiers in a redacted bundle; the graph shape is preserved; pseudonyms are stable across runs. | Property test: redact(snapshot) has the same topology; secret-scan for original values ⇒ none. |
| P1-4.3 | `sherpa import` from bundle + CSV/CMDB import for non-scannable assets (on-prem, SaaS). | CSV rows appear as inventory entities with `source=manual`. | Fixture CSV round-trip. |

#### P1-E5 Workload modelling & human curation
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-5.1 | Inference precedence: explicit tags → CFN stack / Terraform module → naming convention → graph clustering (connected components over high-confidence edges) → `unassigned`. | X-2 "before curation" ≥ 70%. | Reference E2E accuracy; hypothesis tests for idempotence. |
| P1-5.2 | `overrides.yaml`: move resources between workloads, rename/merge/split, set owner, data classification, criticality, forced path. Keyed by stable IDs; survives rescans; conflicts reported. | Overrides applied on rescan with 0 losses; a deleted resource referenced by an override ⇒ warning, not crash. | Rescan test with mutated estate. |
| P1-5.3 | `sherpa workloads review` exports an unassigned/low-confidence triage list (XLSX) and re-imports decisions. | X-2 "after curation" ≥ 95% in ≤ 1 h (measured in the pilot). | Usability session with the design partner (timed). |
| P1-5.4 | `sherpa diff <snapA> <snapB>`: added/removed/changed resources and workloads. | Detects 100% of seeded changes in the reference estate between two runs. | Reference E2E with a scripted change set. |

---

### MODE 2 — ASSESS

The assessment is deterministic: rules are versioned YAML/Python packs, and every finding cites rule ID + evidence.

#### P1-E6 Acquirer target profile (structured "context store v0")
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-6.1 | `target-profile.yaml` schema: allowed regions, approved services, required tags, encryption/logging baseline, reserved CIDR ranges, SCP list (or exported SCP JSON), standard runtimes/versions, team capacity (teams, FTE-weeks, skills), deadline(s). | Schema validated with clear errors; example profiles shipped. | Schema tests; docs example passes validation in CI. |
| P1-6.2 | Import acquirer SCPs/Config rules from an AWS org export (read-only) to auto-fill the profile. | Imported profile matches a hand-written one for the reference "acquirer". | Fixture comparison. |
| P1-6.3 | **Paved-road catalog** (`paved-road.yaml`): the acquirer's golden paths as structured capabilities — compute platforms (e.g. "internal EKS platform", "ECS Fargate standard"), data-store standards (engines/versions/managed services), CI/CD templates (e.g. GitHub Actions reusable workflows, OIDC deploy role pattern), IaC module registry, observability, identity/SSO, networking/account vending. Each capability declares **mapping rules** (which source patterns it replaces, e.g. `EC2 + ASG + ALB web app → EKS platform`, `self-managed Postgres on EC2 → RDS Postgres ≥15`, `Jenkins/ad-hoc deploy → standard GHA template`) and an adoption-effort coefficient. Ships with an example catalog for a "typical AWS landing zone". | Schema validates; the example catalog covers every source pattern in the reference estate; an acquirer can describe its paved road in ≤ 1 day using the docs (pilot measure). | Schema tests; coverage check (every reference workload component maps to a capability or an explicit "no equivalent"); pilot timing. |
| P1-6.4 | **Paved-road fit per workload**: classify each component as *aligned* (already matches), *mappable* (equivalent exists, effort X) or *gap* (no paved-road equivalent ⇒ needs exception or new capability). Output an alignment % and gap list per workload. | Component classification matches the answer key for ≥ 90% of reference components; every *gap* is listed explicitly (never dropped). | Reference E2E; unit test per mapping rule (positive + negative); property test: adding a capability to the catalog never lowers any workload's alignment. |

#### P1-E7 Rule engine + rule packs
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-7.1 | Rule engine: rules declare inputs, scope (resource/workload/account), severity (`blocker`/`warning`/`info`), and remediation text. Outputs `Finding` objects with evidence. Compliance findings are never folded into scores (CLAUDE.md principle). | Same inputs ⇒ same findings, in stable order. A rule can't access the network. | Golden tests; contract test per rule; sandbox test that rules are pure. |
| P1-7.2 | **Landing-zone conformance pack**: region not allowed, CIDR overlap with acquirer ranges, unencrypted storage, public exposure, missing required tags, SCP-incompatible usage (e.g. denied services), EOL runtimes/engines, IAM users with keys, cross-account trusts to unknown accounts. | Detects 100% of seeded violations in the reference estate; false positives ≤ 5%. | Reference E2E scoring; one positive + one negative unit test per rule. |
| P1-7.3 | **GDPR pack v1**: personal-data resources (from classification tags/overrides/optional Macie) in non-EEA regions or regions not in the allowed list; cross-border replication (S3 CRR, DynamoDB global tables, RDS cross-region replicas); controller/processor change flag at account re-parent. Each finding carries a "not legal advice" disclaimer. | All seeded GDPR scenarios flagged; unclassified data stores are listed as "classification unknown" (never assumed clean). | Reference E2E + unit tests per rule; test asserting that unknown classification ⇒ explicit finding. |
| P1-7.4 | Complexity & readiness signals per workload: # resources, # edges crossing workloads, IaC coverage %, CI/CD presence, containerised?, stateful stores, EOL count, cost, idle indicators. | Signals computed for 100% of workloads; values match the answer key. | Reference E2E. |
| P1-7.5 | `sherpa assess` output: assessment JSON + report section per workload and per account. | Every finding links to evidence (resource IDs, attribute values, rule ID). | Schema test; report snapshot test. |

---

### MODE 3 — PLAN

#### P1-E8 Migration options & effort per workload (deterministic)

Every workload gets an **options matrix**, not a single answer. Sherpa recommends one option given the constraints; a human chooses, and the choice is recorded.

| Option | What it means in an AWS→AWS acquisition | Typical trigger signals |
|---|---|---|
| **O1 Relocate** | Move the account(s) as-is into the acquirer's AWS Organization; remediate guardrail violations only. | Clean account boundaries, few blockers, tight deadline. |
| **O2 Lift-and-shift (rehost)** | Recreate the same architecture in an acquirer-vended account (landing zone); data copied. | Shared/messy accounts, CIDR overlap, account can't be re-parented. |
| **O3 Re-platform onto paved road** | Swap components for paved-road equivalents (container platform, managed DB standard, CI templates, IaC modules) without redesign. | High *mappable* share in P1-6.4; moderate time. |
| **O4 Modernize / re-architect** | Redesign to target best practice and full paved-road alignment. | Many *gaps*, EOL tech, strategic workload, time available. |
| **O5 Retire** | Decommission (idle, or duplicated by an acquirer capability). | Idle signals (P1-2.5), human marking. *Duplicate-capability detection needs an acquirer service catalog — Phase 2.* |
| **O6 Retain** | Leave in place for now (e.g. under a TSA), with a revisit date. | Blockers with no viable option inside the deadline. |

Each option card shows: **viability** (viable / viable-with-conflict / not viable + why), **effort range** (P50 / P80 FTE-weeks), **calendar duration** given team capacity, **effort breakdown**, **paved-road alignment after migration** (% + remaining gaps), **compliance flags**, **risk level**, **current run cost** (target-state cost estimate is Phase 2), **key assumptions**, **confidence**.

| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-8.1 | Option model + **decision table**: signals/findings/paved-road fit → viability and preference per option; constraints (deadline, capacity) as weights; compliance blockers as hard constraints that make an option *not viable* or *viable-with-conflict* (never a score penalty). | Decision table documented and reviewed by ≥ 2 practitioners (integration leads / migration architects). | Design review sign-off; table is data (versioned YAML), not code. |
| P1-8.2 | **Effort model v1**: component-based. Infra build/move, data migration (volume, engine change, replication), app change (per paved-road mapping), CI/CD migration to acquirer templates, compliance/guardrail remediation, test & cutover. Drivers come from discovery signals (resource counts, data volume, IaC coverage, EOL count, cross-workload edges, *gap* count). Coefficients live in a versioned `effort-model.yaml` the acquirer can tune. Output is a P50/P80 range, never a single number. | Fully reproducible; every estimate shows its breakdown and drivers; changing a coefficient changes only the affected estimates. | Unit tests per component; golden tests; sensitivity test (±20% coefficient ⇒ bounded, explained change). |
| P1-8.3 | **Calibration**: initial coefficients from expert elicitation (≥ 2 practitioners); back-test against the design partner's completed migration (P1-E12) and adjust. | Back-test actual effort falls within the P50–P80 band for ≥ 60% of workloads and within the P80 bound for ≥ 80% of workloads (v1 target; tighten as data accumulates). Calibration report published with the release. | Back-test harness: actuals CSV vs estimates ⇒ hit-rate report. |
| P1-8.4 | Recommendation: pick the preferred viable option per workload under the given constraints, with a rationale listing the top factors and any **conflict** (e.g. "deadline favours O2, but GDPR-R03 blocks the target region; O6 Retain until region exception approved"). | Recommended option matches the answer key for ≥ 80% of reference workloads; 100% of seeded conflicts surfaced. | Reference E2E; golden tests; property tests — monotonicity: tightening the deadline never moves the recommendation to a *higher*-effort option unless a blocker forces it; adding a paved-road capability never increases O3 effort. |
| P1-8.5 | Human selection/override of the option per workload (with reason), recorded in the audit log and reflected in waves/feasibility. | Selections never lost on rescan; always visible as "chosen by X, recommended was Y". | Rescan + replan test. |

#### P1-E9 Wave planning & feasibility
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-9.1 | Move groups: workloads with high-confidence cross-workload edges are grouped; dependency-ordered waves (topological sort; cycles reported and grouped). | No wave depends on a later wave; cycles explicitly listed. | Property tests on random DAGs/cyclic graphs; reference E2E. |
| P1-9.2 | Capacity fit: schedule waves against team capacity + deadline; output "feasible / infeasible by X FTE-weeks" with the workloads at risk. | Infeasibility correctly detected on the seeded "too-tight deadline" scenario. | Scenario tests (loose / tight / impossible). |
| P1-9.3 | `sherpa plan --scenario` to compare 2–3 constraint scenarios side by side. | Scenario diff shows path/wave changes per workload. | Golden tests. |

#### P1-E10 Reports & exports
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-10.1 | Executive report (Markdown + self-contained HTML): estate summary, cost, top risks/blockers, path distribution, waves, feasibility, coverage gaps, assumptions. | A 2-page summary a non-engineer can read; every number traceable to the data. | Snapshot tests; pilot reviewer feedback (≥ 4/5 usefulness). |
| P1-10.2 | XLSX workbook: Workloads, Options (O1–O6 per workload with effort/alignment/flags), Paved-road gaps, Resources, Findings, Waves, Overrides, Coverage Gaps tabs. | Opens cleanly in Excel/Sheets; filterable; round-trips into overrides. | openpyxl read-back test; manual check in Excel + Google Sheets. |
| P1-10.3 | Versioned JSON schemas (snapshot, assessment, plan) published in `schemas/`. | Backward-compatibility check in CI. | JSON-Schema validation tests; schema-diff gate. |

#### P1-E11 Packaging, docs, release
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-11.1 | `pip install sherpa-migrate` + Docker image (non-root, read-only FS); signed releases + SBOM. | Fresh machine to first `preflight` in < 15 min following the docs only. | Clean-room install test in CI (container); doc walkthrough by someone outside the team. |
| P1-11.2 | Docs: quickstart, IAM setup, offline mode, profile/overrides reference, rule catalogue, threat model, data handling & `sherpa purge`. | Every CLI command and rule documented (generated from code where possible). | Docs build fails on undocumented commands/rules. |
| P1-11.3 | `sherpa purge` removes all local data for an engagement. | No residual files/DB rows. | Filesystem diff test. |

#### P1-E13 Engagement, stage gates & audit (headless backend for the GUI)
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-13.1 | `Engagement` entity (one per acquisition): its snapshots, profile, overrides, findings decisions and plans. Data is isolated per engagement. | No query can return data across engagements. | Isolation tests over every store/API method with two seeded engagements. |
| P1-13.2 | Fixed stage-gate state machine: `Discover → Review inventory → Assess → Review findings → Plan → Approve plan`. A gate is passed by a named approver with a comment and is **bound to the exact snapshot/assessment/plan version**. New data after approval marks downstream gates "stale" (never silently re-approved). | Illegal transitions rejected; rescans invalidate downstream approvals and show why. | State-machine unit tests (all transitions, positive + negative); property test: no path reaches "Plan approved" without every prior gate approved on the same lineage. |
| P1-13.3 | Findings review: accept / waive (reason + expiry required) / mark false-positive. Waived compliance blockers stay visible as "waived by X", never deleted (compliance-as-flag principle). | 100% of waivers carry approver, reason and timestamp; waived blockers appear in the report. | Unit + report snapshot tests. |
| P1-13.4 | Append-only, hash-chained audit log of every override, waiver, approval and import (who / when / what / which version). | Tampering with any entry is detected by `sherpa audit verify`. | Tamper test (edit/delete a row ⇒ verify fails). |
| P1-13.5 | Versioned HTTP API (OpenAPI) over the core service layer; the CLI uses the same service layer. Auth-ready (identity on every call) even before SSO lands. | OpenAPI spec generated in CI; every CLI write operation has an API equivalent. | Contract tests (schemathesis) against the spec; parity check CLI↔API. |

#### P1-E12 Design-partner pilot
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-12.1 | Run the full Discover→Assess→Plan on one real (or recently completed, for back-testing) acquisition. | X-1…X-8 measured and reported; issues triaged. | Structured pilot protocol: timed tasks, comparison against the partner's own manual plan (back-test), exit interview. |

**Suggested sequencing (12–13 wks, 2–3 engineers):** wk 1–2 P1-E0 + P1-E1 · wk 2–6 P1-E2, P1-E3, P1-E5 · wk 5–7 P1-E4 · wk 6–9 P1-E6, P1-E7, P1-E13 · wk 8–11 P1-E8, P1-E9, P1-E10 · wk 10–13 P1-E11, P1-E12.
Critical path: **reference estate → discovery coverage → workload model → rules → planning.** Delays in E0 delay every measurable criterion.

---

## Phase 1.5 — Review & Approval GUI (≈ 6 weeks)

**Who:** acquirer team only (D-5). Target access is deferred to Phase 3 as a per-engagement option.
**Shape:** self-hosted web app (same Docker Compose as the API), thin client over the P1-E13 API, no business logic in the frontend.
**Suggested stack (proposal, decide at kick-off):** existing Python core + FastAPI for the API; React + TypeScript frontend; Postgres for multi-user deployments (SQLite stays for single-user CLI).

### Roles (MVP)
| Role | Can see | Can do |
|---|---|---|
| Acquirer admin | Everything in the engagement | Manage users, profile, imports, all actions |
| Acquirer reviewer | Inventory, findings, plans, history | Curate workloads, review findings, propose plans |
| Acquirer approver | Same as reviewer | Pass stage gates, approve the plan |

The authz model must be **role- and field-scoped from day one**, so a future target role (Phase 3) is a configuration change, not a redesign. The target's only Phase 1.5 touchpoint is the offline local preview of its own bundle (P1.5-7.2).

### Phase 1.5 exit criteria
| # | Criterion | Target |
|---|---|---|
| G-X1 | Design-partner users complete the full gate sequence in the GUI without the CLI | 100% of gates, unassisted |
| G-X2 | Workload curation time vs XLSX round-trip | ≥ 30% faster (timed, same dataset) |
| G-X3 | Authorization matrix: every API endpoint × every role tested | 100% coverage, 0 leaks |
| G-X4 | Zero business logic in the frontend | All decisions reproducible via API/CLI with identical results |

| Epic | ID | What | Success criteria | Test method |
|---|---|---|---|---|
| **P1.5-E1 Deployment & identity** | P1.5-1.1 | Docker Compose (API, UI, DB), TLS, OIDC SSO (Okta/Entra/Google) + local admin bootstrap; no outbound calls except configured endpoints. | Fresh install to first login in < 30 min following docs. | Clean-room install test; egress test (network policy blocks all, app still works offline). |
| **P1.5-E2 Authorization** | P1.5-2.1 | Server-side, deny-by-default RBAC per engagement with the roles above, field-level scoping enforced in the service layer (ready for a future target role). | G-X3. | Generated authz test matrix (endpoint × role × engagement) in CI; a test-only "restricted" role proves field scoping works. |
| **P1.5-E3 Engagement home & gate tracker** | P1.5-3.1 | Engagement dashboard: current gate, who must act, stale approvals, coverage gaps, last scan per account. | Users can find "what's blocking us" in < 10 s (usability task). | Playwright E2E; moderated usability test. |
| **P1.5-E4 Inventory browser & curation** | P1.5-4.1 | Search/filter resources, workload view, dependency graph (per workload, bounded), unassigned triage queue, bulk move/merge/split, provenance + confidence shown on every inferred fact. | Handles 10k resources with p95 interaction < 1 s; G-X2. | Performance test with the synthetic 10k fixture; Playwright E2E. |
| **P1.5-E5 Findings review** | P1.5-5.1 | Findings by workload/rule/severity; accept / waive (reason + expiry) / false-positive; evidence links to resources. | Blockers can't be bulk-waived without individual reasons. | E2E + API negative tests. |
| **P1.5-E6 Options, plan view & approval** | P1.5-6.1 | Per-workload options matrix (O1–O6 side by side: viability, P50/P80 effort, duration, paved-road alignment and gaps, compliance flags, risk) with the recommended option marked and a "choose option + reason" action; waves (timeline), feasibility vs capacity, scenario compare; approve gate with comment. | Every number on screen matches the CLI/XLSX output for the same version. | Cross-check test: API/CLI output vs UI-rendered values (Playwright extraction). |
| **P1.5-E7 History, diff & offline** | P1.5-7.1 | Snapshot timeline, diff between any two snapshots, "what changed since approval", audit-log viewer. | 100% of seeded changes between two reference snapshots shown. | Reference E2E through the UI. |
| | P1.5-7.2 | Offline bundle upload via UI; **local read-only preview** (`sherpa view bundle.sherpa`) so the target can inspect exactly what it is sending before export, with no server and no network. | Preview works fully offline; shows identical inventory to the post-import view. | Air-gapped container test; parity test preview vs imported view. |
| **Cross-cutting** | P1.5-X.1 | Accessibility (WCAG 2.1 AA) and export of any table to CSV/XLSX. | 0 critical axe violations. | axe-core in Playwright CI. |

---

## Phase 2 — Breadth & intelligence (≈ 3 months)
| Epic | Success criterion (headline) | Test method |
|---|---|---|
| GitLab + Bitbucket code/pipeline scanners; Jenkins pipeline scanner | Link recall within 10 pts of GitHub on equivalent fixture repos | Plugin contract suite + fixture E2E |
| LLM explanation layer (opt-in, provider-pluggable, local-model option) — narrates rationale; never changes scores | Disabling the LLM leaves all scores/paths byte-identical; explanations cite evidence IDs only (no hallucinated resources) | Golden test with LLM on/off; evidence-ID validation; human eval rubric on 50 samples |
| **Grounded chat in the GUI** (D-6): read-only tools over the engagement store (query inventory, findings, plans, diffs); every answer cites resource/finding IDs; no write tools; opt-in per engagement; local-model option; respects RBAC | ≥ 90% answer accuracy on a 100-question eval set built from the reference estate; 0 uncited factual claims; 0 RBAC leaks via chat | Automated eval harness (questions + expected answers from the answer key); citation validator; adversarial prompts trying to reach data outside the user's role/engagement |
| Target-state run-cost estimate per option (pricing API, rightsizing from utilisation) | Within ±20% of actual post-migration cost on back-test | Back-test against design-partner billing |
| Acquirer service catalog → "duplicate capability" Retire suggestions | ≥ 70% of suggestions accepted by architects | Labelled eval set |
| Context store v1: RAG over acquirer docs/templates to *suggest* standard modules per workload | ≥ 70% of suggestions rated relevant by architects | Labelled eval set |
| Compliance packs: PCI-DSS scope hints, HIPAA, regional data-residency laws (e.g. DPDP, LGPD); Security Hub/Config findings import | All seeded scenarios flagged | Reference E2E per pack |
| Licensing exposure (Windows/SQL/Oracle, Marketplace) | Seeded licensed instances detected | Reference E2E |

## Phase 3 — Collaboration extensions (≈ 2 months)
| Epic | Success criterion | Test method |
|---|---|---|
| Configurable workflows (custom gates, parallel reviewers, per-gate approver rules), only if ≥ 2 customers ask | Custom workflow can't bypass the audit log or compliance-blocker visibility | State-machine property tests over generated workflows |
| Notifications (email/Slack) on gate changes and stale approvals | Delivered within 1 min; no deal data in notification body beyond links | Integration tests; content-leak test |
| Jira/Linear export of waves/tasks | Round-trip of IDs | Integration tests against sandbox projects |
| **Target-team access (opt-in per engagement)**: target viewer role (own inventory, workloads, coverage gaps, history; suggest corrections) and later remediation tasks assigned to them. Never sees profile, findings, options, plan or approvals. | Explicit per-field allow-list; authz matrix still 100%; enabling is an admin action recorded in the audit log | Authz matrix; pen-test of target role before first use |

## Phase 4 — Multi-cloud & community (ongoing)
Azure and GCP as *sources* and *targets* (requires path model generalisation), plugin SDK docs + cookie-cutter, a community rule-pack registry, and a governance model. Success: an external contributor ships a connector without core changes (proved by the P0-E4 contract suite).

---

## Risks to this plan
| Risk | Likelihood | Mitigation |
|---|---|---|
| No design partner ⇒ MVP validated only on synthetic data | High | Start partner outreach now; accept back-testing on a completed acquisition. |
| Reference estate cost/maintenance | Medium | Nightly destroy; budget alarm; minimal instance sizes. |
| Workload inference accuracy is low on messy estates | High | Curation UX (P1-5.3) is MVP-critical, not polish. |
| Path decision table seen as arbitrary | Medium | Practitioner review (P1-8.1), transparent coefficients, overrides. |
| **Effort estimates are wrong and kill credibility** | High | Ranges not points, visible breakdown/drivers, tunable coefficients, calibration back-test (P1-8.3), confidence shown on every estimate. |
| **Acquirer can't describe its paved road** (tribal knowledge, wiki pages) | High | Example catalog + ≤ 1-day authoring target (P1-6.3); start with 5–10 capabilities, not a complete catalog; RAG-assisted catalog drafting in Phase 2. |
| Security teams refuse to run third-party tool | Medium | Read-only proof, offline mode, signed releases, SBOM, threat model. |
| Target viewer sees sensitive acquirer conclusions (e.g. "retire" ⇒ job implications) when target access is enabled later | Medium | Acquirer-only GUI in Phase 1.5 (D-5); field-scoped server-side RBAC; pen-test before enabling target role. |
| GUI scope creep (workflow builder, dashboards) delays validation | High | Fixed gates (D-4); GUI only after Phase 1 data quality is proven; G-X4 "no logic in frontend". |
