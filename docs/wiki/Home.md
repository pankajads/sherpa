# Sherpa

**Open-source discovery and migration planning for M&A cloud integrations.**

Every acquisition brings a technical estate that has to be understood, assessed and moved into the acquirer's environment, usually against a deadline set by the deal rather than by engineering reality. Today that work runs on interviews, spreadsheets and heroics, and it is repeated from scratch for every deal.

Sherpa turns it into a repeatable, auditable process:

> **Discover** what the acquired company runs → **Assess** it against your guardrails, paved road and compliance rules → **Plan** per-workload migration options with effort, then **review and approve** with your team.

---

## The problem

- **Inventory is rebuilt by hand** for every deal — interviews and spreadsheets, never a system of record.
- **Cloud, code and pipelines are assessed in silos**, by different people, with no links between them.
- **Migration strategy is chosen ad hoc**, without a systematic view of deadline, team capacity or risk.
- **Compliance is checked late**, sometimes after the plan is locked.
- **The acquirer's standards are ignored** until day-one operations hit them.
- **Access is hard.** Before close you usually can't scan the target's environment at all, and after close their security team needs proof that a tool is safe.

The cost compounds with every acquisition.

## What Sherpa does

| | |
|---|---|
| **Discovers** | AWS accounts, GitHub repositories and GitHub Actions pipelines, linked into one inventory with dependencies and provenance. |
| **Works online or offline** | Online after close via a read-only cross-account role. Offline before close: the target runs the collector, reviews exactly what it will share, and hands over a signed bundle. |
| **Assesses** | Each workload against your landing-zone guardrails (regions, encryption, exposure, CIDR overlap, tagging), your **paved road** (golden paths) and GDPR rules. |
| **Plans** | Shows **options side by side** for every workload — relocate the account, lift-and-shift, re-platform onto your paved road, modernize, retire, retain — each with an effort range, duration, paved-road alignment, compliance flags and risk. |
| **Keeps humans in charge** | Sherpa recommends; your team decides. Effort estimates can be adjusted. Stage gates, approvals, history and an audit trail make every decision traceable. |

## Who it is for

- **Integration leads / PMO** — a defensible plan, waves and feasibility for the steering committee.
- **Cloud architects** — inventory, dependencies, conformance gaps and the reasoning behind each option.
- **Security & compliance** — explicit data-residency and exposure findings, with evidence.
- **The acquired company's engineers** — a read-only, inspectable collector they can trust.

## Status

**Early development.** A discovery skeleton exists for AWS, GitHub and GitHub Actions and is being hardened (Phase 0). The MVP targets **AWS → AWS** acquisitions. See [[Roadmap]].

## Learn more

- [[Philosophy]] — the principles behind the design
- [[How It Works|How-It-Works]] — workflow, collection modes, migration options
- [[Architecture and Data Flow|Architecture-and-Data-Flow]] — components, data flow, trust boundaries
- [[Roadmap]] — phases and exit criteria
- [[Get Involved|Get-Involved]] — design partners and contributors wanted

**License:** MIT · **Repository:** [pankajads/sherpa](https://github.com/pankajads/sherpa)
