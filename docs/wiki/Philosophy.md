# Philosophy

Sherpa handles some of the most sensitive data a company has during a deal: the full technical estate of a business that may not even be acquired yet. The principles below follow from that, and from how M&A integrations actually go wrong.

## 1. Read-only, provably

Sherpa never changes anything in the environments it scans. It is not enough to say so:
- it ships a published least-privilege IAM policy;
- every run reports the APIs it called;
- CloudTrail evidence shows that no write calls were made.

Secret values, environment variables and data contents are never collected.

## 2. Access is a first-class problem

Before close, the acquirer usually can't touch the target's environment. So Sherpa has two collection modes that produce **the same snapshot**:
- **Online** — after close, via a read-only cross-account role.
- **Offline** — the target runs the collector, reviews a human-readable manifest of exactly what will be shared, optionally redacts names, and hands over a signed, encrypted bundle.

## 3. Options, not answers — and humans decide

There is rarely one right migration path. For every workload, Sherpa shows the options side by side, marks the one that best fits your constraints, and explains why. Your team chooses, and the choice and its reason are recorded.

## 4. Effort is a starting point

Effort estimates come from the assessment, as ranges (P50/P80) with a visible breakdown — never a single magic number. Your team can adjust any estimate. Sherpa keeps both its own estimate and yours. The difference, plus actuals when they arrive, is how the estimates get better over time.

## 5. Compliance is a constraint, not a score

A GDPR or guardrail problem is never quietly averaged into an effort score. It is an explicit flag that can make an option non-viable or "viable with conflict". Waived findings stay visible, with who waived them and why. Sherpa can't guess where personal data lives, so unclassified data stores are reported as "classification unknown", never assumed clean. Findings are decision support, not legal advice.

## 6. Deterministic and explainable

The same inventory and constraints always produce the same output, byte for byte. Every inferred fact (a workload grouping, a code-to-cloud link, a recommendation) records how it was derived and with what confidence. AI may later help *explain* results and answer questions, grounded in the data with citations. It never changes scores or decisions.

## 7. Your paved road is the destination

Migrating "to AWS" isn't the goal when both companies already run on AWS. The goal is that the acquired workloads run the way **your** teams operate: your landing zone, platforms, pipeline templates and modules. Sherpa measures each workload against your paved road and shows what's aligned, what maps with effort, and where the gaps are.

## 8. Self-hosted, no telemetry

Deal data stays in the acquirer's environment. There is no Sherpa SaaS and no phone-home. Everything for an engagement can be purged when a deal closes or falls through.

## 9. Do one cloud well first

The MVP targets **AWS → AWS** with GitHub. Azure, GCP and other source-control and CI systems come later, through a plugin interface that doesn't require changes to the core.

## 10. Open source, so it can be trusted

Every company doing M&A has this problem. Building in the open lets anyone review the scanners, rules and recommendation logic, instead of trusting a vendor's black box with deal-sensitive data.
