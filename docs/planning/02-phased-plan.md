# Sherpa — Phased Implementation Plan

**Date:** 2026-10-02 · **Status:** Proposal for review (no implementation started) · **Input:** [01-requirements-review.md](01-requirements-review.md)

---

## 0. How this plan is structured

- **Phase 0 — Harden the foundation** (2–3 wks): fix the confirmed defects in the existing code before anyone builds on them.
- **Phase 1 — MVP "Discover → Assess → Plan" for AWS→AWS** (10–12 wks): the first version a customer can run on a real acquisition and get a plan they can present.
- **Phase 2 — Breadth & intelligence** · **Phase 3 — Dashboard & collaboration** · **Phase 4 — Multi-cloud & community**: epic level only, to be detailed once Phase 1 feedback is in.

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
**In:** AWS source, AWS target (the acquirer's landing zone); GitHub (cloud + Enterprise Server) and GitHub Actions; CLI + reports + XLSX; target-run offline bundle; deterministic rules engine; human overrides.
**Out (deferred):** Azure/GCP, GitLab/Bitbucket/Jenkins, LLM/RAG, web dashboard, multi-user RBAC, Jira integration.

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

#### P1-E4 Target-run offline mode
| ID | What | Success criteria | Test method |
|---|---|---|---|
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

#### P1-E8 Path recommendation (deterministic)
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-8.1 | Path model: **Relocate (account re-parent into acquirer Org)**, Rehost, Replatform, Refactor, Retire, Retain. Document a decision table: which signals/findings push toward which path; constraints (deadline, capacity) as weights; blockers as hard constraints. | The decision table is documented and reviewed by ≥ 2 practitioners (e.g. ex-AWS ProServe / integration leads). | Design review sign-off. |
| P1-8.2 | Scoring implementation: per-workload scores per path, with a rationale listing the top contributing factors. Compliance blockers produce an explicit **conflict** (e.g. "deadline favours Rehost, but GDPR-R03 blocks the target region") — never silently resolved. | Matches the answer-key path for ≥ 80% of reference workloads; 100% of seeded conflicts surfaced. | Reference E2E; golden tests; property test: tightening the deadline never moves a workload to a *higher*-effort path unless a blocker forces it (monotonicity). |
| P1-8.3 | Effort estimate per workload per path (t-shirt + FTE-week range), from signals with transparent coefficients the user can override in the profile. | Estimates reproducible; coefficient changes reflected immediately. | Unit tests; sensitivity test. |
| P1-8.4 | Human override of path (from P1-5.2) shown as "overridden by user, reason", kept in the audit log. | Overrides never lost and always visible. | Rescan + replan test. |

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
| P1-10.2 | XLSX workbook: Workloads, Resources, Findings, Waves, Overrides, Coverage Gaps tabs. | Opens cleanly in Excel/Sheets; filterable; round-trips into overrides. | openpyxl read-back test; manual check in Excel + Google Sheets. |
| P1-10.3 | Versioned JSON schemas (snapshot, assessment, plan) published in `schemas/`. | Backward-compatibility check in CI. | JSON-Schema validation tests; schema-diff gate. |

#### P1-E11 Packaging, docs, release
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-11.1 | `pip install sherpa-migrate` + Docker image (non-root, read-only FS); signed releases + SBOM. | Fresh machine to first `preflight` in < 15 min following the docs only. | Clean-room install test in CI (container); doc walkthrough by someone outside the team. |
| P1-11.2 | Docs: quickstart, IAM setup, offline mode, profile/overrides reference, rule catalogue, threat model, data handling & `sherpa purge`. | Every CLI command and rule documented (generated from code where possible). | Docs build fails on undocumented commands/rules. |
| P1-11.3 | `sherpa purge` removes all local data for an engagement. | No residual files/DB rows. | Filesystem diff test. |

#### P1-E12 Design-partner pilot
| ID | What | Success criteria | Test method |
|---|---|---|---|
| P1-12.1 | Run the full Discover→Assess→Plan on one real (or recently completed, for back-testing) acquisition. | X-1…X-8 measured and reported; issues triaged. | Structured pilot protocol: timed tasks, comparison against the partner's own manual plan (back-test), exit interview. |

**Suggested sequencing (12 wks, 2–3 engineers):** wk 1–2 P1-E0 + P1-E1 · wk 2–6 P1-E2, P1-E3, P1-E5 · wk 5–7 P1-E4 · wk 6–9 P1-E6, P1-E7 · wk 8–11 P1-E8, P1-E9, P1-E10 · wk 10–12 P1-E11, P1-E12.
Critical path: **reference estate → discovery coverage → workload model → rules → planning.** Delays in E0 delay every measurable criterion.

---

## Phase 2 — Breadth & intelligence (≈ 3 months)
| Epic | Success criterion (headline) | Test method |
|---|---|---|
| GitLab + Bitbucket code/pipeline scanners; Jenkins pipeline scanner | Link recall within 10 pts of GitHub on equivalent fixture repos | Plugin contract suite + fixture E2E |
| LLM explanation layer (opt-in, provider-pluggable, local-model option) — narrates rationale; never changes scores | Disabling the LLM leaves all scores/paths byte-identical; explanations cite evidence IDs only (no hallucinated resources) | Golden test with LLM on/off; evidence-ID validation; human eval rubric on 50 samples |
| Context store v1: RAG over acquirer docs/templates to *suggest* standard modules per workload | ≥ 70% of suggestions rated relevant by architects | Labelled eval set |
| Compliance packs: PCI-DSS scope hints, HIPAA, regional data-residency laws (e.g. DPDP, LGPD); Security Hub/Config findings import | All seeded scenarios flagged | Reference E2E per pack |
| Licensing exposure (Windows/SQL/Oracle, Marketplace) | Seeded licensed instances detected | Reference E2E |

## Phase 3 — Dashboard & collaboration (≈ 2 months)
| Epic | Success criterion | Test method |
|---|---|---|
| Read-only web UI over snapshots/assessments/plans | Pilot users complete key tasks without the CLI | Usability test, Playwright E2E |
| Editing overrides/constraints in UI + audit trail | Every change attributable (who/when/why) and replayable | Audit-log integrity tests |
| Multi-user + SSO + RBAC (deal team vs target team separation) | Target users can't see acquirer profile/plan | Authorization test matrix |
| Jira/Linear export of waves/tasks | Round-trip of IDs | Integration tests against sandbox projects |

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
| Security teams refuse to run third-party tool | Medium | Read-only proof, offline mode, signed releases, SBOM, threat model. |
