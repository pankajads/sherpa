# Sherpa — Requirements Review & Gap Assessment

**Date:** 2026-10-02 · **Scope:** Discovery + Assessment (no implementation) · **Companion:** [02-phased-plan.md](02-phased-plan.md)

---

## 0. Bottom line first

1. **The current requirements describe a product vision, not something a customer can buy, deploy or trust.** There are no user personas, no acceptance criteria, no security/access model, no output formats, no scale targets and no definition of "done" for any phase.
2. **The README omits the hardest real-world M&A constraint: access.** Before the deal closes, the acquirer usually *cannot* scan the target's cloud. Clean-team rules and competition law limit what can be shared. Even after close, the target's security team will not hand over a cross-account role without a published, least-privilege policy and an audit trail. If a tool assumes it can just connect, it fails at the first customer meeting.
3. **The three migration paths are wrong for the stated first target (AWS → AWS).** In a same-cloud acquisition, the most common outcome is *not* lift-and-shift, re-platform or re-architect. It is **relocating the AWS account into the acquirer's AWS Organization** (re-parent), plus remediation against the acquirer's landing-zone guardrails, plus **retire** and **retain**. The path model needs to cover that.
4. **The existing Phase-1 code is a good skeleton but not shippable.** It has a confirmed credential leak, non-deterministic IDs, cross-plane linking that will be close to empty on real estates, and a multi-account mode that cannot work (§3).
5. **The README roadmap contradicts CLAUDE.md.** The README puts AWS, Azure and GCP connectors in Phase 1. CLAUDE.md says "AWS → AWS first, multi-cloud must not drive early API design". This review follows CLAUDE.md, because three clouds in an MVP means none of them is done well.

---

## 1. Discovery — sources reviewed

| Source | Found | Notes |
|---|---|---|
| GitHub wiki | Not used | Confirmed by owner: README.md and CLAUDE.md are the requirement sources. |
| GitHub issues / PRs | None | No backlog exists yet. |
| `README.md` | Yes | Planning document: problem, goals, architecture, paths, compliance, roadmap. |
| `CLAUDE.md` | Yes | Design principles (plugin interface, vendor-neutral, compliance-as-flag, determinism, AWS→AWS first). Its status line ("pre-code") is **stale**. |
| Code (`sherpa/`, `tests/`) | Yes | ~2.1k LOC source, ~1.9k LOC tests. Phase-1 discovery for AWS + GitHub + GitHub Actions. |
| Baseline run | 109 tests pass, 85% coverage on Python 3.12. Ruff is clean. CLI coverage is **0%**. | Local default Python 3.11 can't install (`requires-python >=3.12`). |

---

## 2. Requirements catalogue (extracted) and verdict

`R-xx` = stated in README/CLAUDE.md. Verdict: **OK** (testable as written) · **VAGUE** (needs acceptance criteria) · **WRONG/RISKY** (should change).

| ID | Requirement (as stated) | Verdict | What's missing |
|---|---|---|---|
| R-01 | Automate discovery across cloud, code, CI/CD | VAGUE | Which services/SCMs/CI tools, coverage target (% of resources found vs ground truth), permissions required. |
| R-02 | One structured inventory with cross-plane dependency links | VAGUE | Link types, how links are inferred, confidence/provenance per link, acceptable false-link rate. |
| R-03 | Recommend a path per workload weighted by time + resource constraints | VAGUE / RISKY | How a "workload" is defined and corrected by humans; constraint units (deadline date? FTE-weeks?); path set too narrow (see G-07). |
| R-04 | Compliance (GDPR+) as first-class input, surfaced as flags | VAGUE | How Sherpa learns *where personal data is* (it cannot infer it from ARNs); rule format; which flags block vs warn; legal disclaimer. |
| R-05 | Acquirer uploads libraries/templates/docs to bias recommendations (RAG) | RISKY for MVP | LLM/RAG output conflicts with "deterministic recommendations" unless it is limited to explanation. Should be structured standards (allowed regions, services, tag policy, CIDR plan), not free-text docs, in v1. |
| R-06 | Open source, vendor-neutral AWS/Azure/GCP | OK as principle | Must not be MVP scope. |
| R-07 | LLM orchestrator plans scans and reasons over findings | RISKY | Adds cost, data-egress concerns (deal data sent to an LLM provider) and non-determinism. Needs an opt-in, model-provider choice and "no LLM" mode. |
| R-08 | Web dashboard: inventory, plans, constraints, uploads, audit trail | OK, later | Not needed for a first customer; a report + spreadsheet is what integration PMOs consume. |
| R-09 | Scanner plugin interface | OK | Exists, but the orchestrator hard-codes scanners and bypasses it (§3). |
| R-10 | Deterministic: same inventory + constraints ⇒ same output | OK, testable | Currently violated (§3). |

---

## 3. Assessment of the existing code (confirmed, not guessed)

Each item was verified by reading code and/or executing it.

| # | Finding | Evidence | Impact |
|---|---|---|---|
| C-1 | **GitHub token is persisted in plaintext** into snapshot JSON and the SQLite `config_json`. | `ScanConfig.github_token` serialised by `model_dump_json()`. Executed: token string present in snapshot JSON → `True`. | Security blocker. A shared output file leaks an org-wide token. |
| C-2 | **Workload IDs are random UUIDs.** | `Workload(name='a').id == Workload(name='a').id` → `False`. | Violates the determinism principle. Snapshot-to-snapshot diffs and human overrides can't key on workloads. |
| C-3 | **Multi-account scanning can't work.** One `assume_role_arn` is used for every account, and `account_id` is just a label stamped on results. | `scanner.py:121-128`. | Scanning N accounts returns N copies of one account's resources, labelled with N different account IDs. Data is silently wrong. |
| C-4 | STS `AssumeRole` is called once **per collector × region × account**. | `_run_collector`. | Throttling on real orgs; noisy CloudTrail. |
| C-5 | **Code→cloud linking relies on literal ARNs in files.** | `_extract_arns`. | Terraform/CDK rarely contain literal ARNs, so links will be close to empty on real repos. The "cross-plane" value proposition doesn't hold. |
| C-6 | **Pipeline→cloud links every resource in an account** when any 12-digit number appears in a workflow. | `cross_plane_linker.py`, `_AWS_ACCOUNT_PATTERN`. | Massive false-positive edges; the dependency graph is unusable for wave planning. |
| C-7 | Workload `pipeline_ids` is never populated; repos are attached only via ARN match. | `workload_inferrer.py`. | Workloads are cloud-only in practice. |
| C-8 | The plugin interface is bypassed: `load_scanners()` is unused, and the orchestrator imports the three scanners directly. `load_scanners` swallows all exceptions. | `discovery.py`, `grep load_scanners`. | Breaks the "add a connector without touching core" principle. |
| C-9 | Code scanner fetches **every** YAML/JSON file via the API, and `content_map` is keyed by filename, not path (overwrites). | `github/scanner.py`. | GitHub rate limits are exhausted on medium orgs; CloudFormation detection is unreliable. |
| C-10 | Collectors exist for 11 services. Enum/LocalStack declare EKS, ELB, ElastiCache, Route53, EventBridge, ECS services/task defs with **no collectors**. The `networking` category maps to nothing. | `collector.py`, `coverage_validator.py`. | Load balancers, DNS and Kubernetes are core to any migration plan and are missing. |
| C-11 | S3 tags are not collected (`tags={}`). | `collect_s3`. | Tag-based workload inference fails for every bucket; buckets are often where personal data lives. |
| C-12 | Silent `except: pass` in several collectors (Lambda ESM, repo tree, file fetch). | Multiple. | Coverage gaps are hidden — the opposite of the "surface explicitly" principle. |
| C-13 | CLI defaults `--db :memory:`; the inventory is lost after the run. CLI has 0% test coverage. | `cli/main.py`. | No rescans/diffs by default; the user-facing surface is untested. |
| C-14 | No enumeration of AWS Organizations accounts or enabled regions; the user must list them by hand. | `ScanConfig`. | Real targets have 20–300 accounts; manual lists miss some. |

**Keep:** frozen Pydantic models, snapshot-as-append-only, naming-convention config (a good idea that will matter), sorted outputs, moto/LocalStack test approach, CI with coverage gate.

---

## 4. Gaps for industry use (missing requirements)

Grouped by what blocks adoption. **Bold = needed for MVP.**

### 4.1 Access, trust & legal (adoption blockers)
- **G-01 Read-only guarantee + published least-privilege IAM policy.** Provide a CloudFormation StackSet / Terraform module for the cross-account role. Prove "no write calls" with CloudTrail evidence.
- **G-02 Target-run / offline mode.** The target (or a clean team) runs a collector, reviews the bundle, and hands over an export. The acquirer imports it. This covers pre-close and air-gapped cases.
- **G-03 Data minimisation & redaction.** Never collect secret values, env vars or object contents. Optional hashing of names/tags for pre-close sharing.
- **G-04 Data lifecycle.** Encryption at rest for the store, a `purge` command (deal falls through ⇒ delete everything), no telemetry by default.
- **G-05 Secrets handling.** Credentials only from env/credential providers; never serialised (fixes C-1).
- G-06 Legal disclaimer: compliance flags are decision support, not legal advice.

### 4.2 Domain model gaps
- **G-07 Path set.** Replace the three paths with an AWS-relevant subset of the 7Rs: **Relocate (account re-parent), Rehost, Replatform, Refactor/Re-architect, Retire, Retain**. Repurchase (SaaS) comes later.
- **G-08 Landing-zone conformance.** For AWS→AWS, the main question is "what must change for this account/workload to meet the acquirer's guardrails?" That means region allowlist, SCP compatibility, encryption, public exposure, tagging policy, logging, and **CIDR overlap with the acquirer's network** (a classic merger blocker).
- **G-09 Cost.** Current monthly cost per account/workload (Cost Explorer / CUR). Synergy cases are built on cost; a plan without cost won't be read by the deal team.
- **G-10 Utilisation.** Basic CloudWatch utilisation for rightsizing and retire candidates (idle resources).
- **G-11 Data classification input.** Sherpa can't infer PII. It needs tags, a classification file, or optional Macie findings, mapped per resource. Without this, GDPR flags are guesses.
- **G-12 Human curation.** Override workload grouping, classification, ownership and path. Overrides persist across rescans and are recorded in an audit log.
- **G-13 Ownership & people.** Owner/team per workload, plus key-person risk. The "resourcing constraint" needs teams with capacity, not a single FTE number.
- G-14 Licensing exposure (Windows/SQL Server/Oracle BYOL, Marketplace subscriptions) — affects rehost cost and legality.
- G-15 Identity: IAM users, external IdP federation, cross-account trust relationships (also a security finding).
- G-16 Dependencies on third parties/external endpoints (VPC peering, VPN, PrivateLink, DNS, certificates).

### 4.3 Outputs people actually use
- **G-17 Exports: XLSX/CSV for the PMO, stable JSON schema for tooling, Markdown/HTML report for leadership.**
- **G-18 Wave/move-group sequencing** from the dependency graph and constraints.
- **G-19 Confidence & provenance** on every inferred fact (where it came from, which rule).
- **G-20 Snapshot diff:** the estate changes during a 3–9-month deal timeline.
- G-21 Integrations: Jira/Linear export, AWS Migration Hub / Application Discovery Service formats.

### 4.4 Non-functional requirements (none stated today)
- **G-22 Scale:** 10k resources, 50 accounts × 10 regions, and 1,000 repos in under 60 min; resumable after failure; respects API rate limits.
- **G-23 Determinism:** byte-identical outputs for identical inputs (excluding timestamps/snapshot IDs).
- **G-24 Distribution:** `pip install` and a Docker image; runs on a laptop or in a CI job with no server.
- G-25 Observability: structured logs, per-collector timings and error counts in the report.
- G-26 Supply chain: signed releases, SBOM, pinned dependencies (security teams will ask before running it in a target's account).

### 4.5 Product definition gaps
- **G-27 Personas & jobs-to-be-done** (proposed):
  - *Integration Lead / PMO* — needs the plan, waves, effort and risks for the steering committee.
  - *Cloud Architect (acquirer)* — needs the inventory, dependencies, conformance gaps and path rationale.
  - *Security/Compliance officer* — needs the data-residency and exposure flags, plus evidence.
  - *Target's platform engineer* — runs the collector; needs to trust it's read-only.
- **G-28 Success metrics for the product:** e.g. time from access-granted to first plan (< 1 day vs weeks today); % of resources assigned to a workload after curation; plan accepted by the integration lead with ≤ N manual edits.

---

## 5. Decisions

**Resolved 2026-10-02** (details in [02-phased-plan.md §0](02-phased-plan.md)): AWS-only MVP; online + offline collection; CLI to collect, GUI to decide (GUI as Phase 1.5 fast follow); fixed stage gates; GUI acquirer-only (target access later, per engagement); options matrix with effort + paved-road alignment per workload; chat in Phase 2, read-only and grounded; self-hosted only. Requirement sources confirmed as README.md + CLAUDE.md.

**Still open:**
- Is the first SCM GitHub only (item 3 below)?
- Is there a design partner or a completed acquisition to test against (item 4 below)?

Original questions:

1. **Who runs it in the MVP — the acquirer post-close, or the target pre-close?** I recommend supporting both via G-02, because pre-close is when planning value is highest.
2. **Is an LLM in the MVP at all?** I recommend **no**: deterministic rules first, with LLM explanations as an opt-in in Phase 2. That avoids sending deal data to a third party and keeps outputs reproducible.
3. **Is the first SCM GitHub only?** Many acquired companies use GitLab or Bitbucket. Recommend GitHub for MVP, with GitLab first in Phase 2, validated with design partners.
4. **Is there a design-partner acquisition (real or historical) to pilot against?** Without one, the MVP is validated only against a synthetic estate.
