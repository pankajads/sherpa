# Architecture and Data Flow

> **Status legend:** ✅ exists today (being hardened) · 🛠️ MVP (Phase 1) · 🖥️ Phase 1.5 · 🔭 later.
> Only the discovery skeleton exists today. Everything else here is the target design.

## Component view

```mermaid
flowchart TB
    subgraph Collect["Collect — CLI / container, read-only"]
        CS["Cloud scanner — AWS ✅"]
        GS["Code scanner — GitHub ✅"]
        PS["Pipeline scanner — GitHub Actions ✅"]
        PL(["Scanner plugin interface ✅<br/>(Azure, GCP, GitLab, Jenkins 🔭)"])
        CS --- PL
        GS --- PL
        PS --- PL
    end

    subgraph Core["Sherpa core — one service layer, versioned API"]
        INV[("Inventory store<br/>snapshots, append-only ✅")]
        WL["Workload inference + linking ✅ → 🛠️"]
        PROF["Acquirer target profile 🛠️"]
        PRC["Paved-road catalog 🛠️"]
        ASSESS["Assess — rule packs 🛠️<br/>conformance · GDPR v1 · paved-road fit · complexity · cost"]
        PLAN["Plan — options engine 🛠️<br/>O1–O6 · effort P50/P80 · waves · feasibility"]
        ENG["Engagement · stage gates · approvals · audit log 🛠️"]
    end

    subgraph Surfaces["Interfaces"]
        CLI["CLI ✅"]
        RPT["Reports + Excel 🛠️"]
        GUI["Web GUI — acquirer team 🖥️"]
        CHAT["Grounded chat, opt-in 🔭"]
    end

    Collect --> INV
    INV --> WL --> INV
    INV --> ASSESS
    PROF --> ASSESS
    PRC --> ASSESS
    ASSESS --> PLAN
    PROF --> PLAN
    PLAN --> ENG
    ASSESS --> ENG
    ENG --> CLI
    ENG --> RPT
    ENG --> GUI
    INV --> CHAT
```

## Data flow: online and offline

Both paths end in the **same snapshot format**, so everything downstream is identical.

```mermaid
flowchart LR
    subgraph T["Target company environment"]
        TA[("AWS accounts")]
        TG[("GitHub org")]
        COL["Sherpa collector<br/>(run by target)"]
        MAN["Human-readable manifest<br/>review + optional redaction"]
        BUN[/"Signed, encrypted bundle"/]
        TA --> COL
        TG --> COL
        COL --> MAN --> BUN
    end

    subgraph A["Acquirer environment (self-hosted)"]
        ON["Sherpa collector<br/>(online, read-only role)"]
        IMP["sherpa import<br/>verify signature, decrypt"]
        SNAP[("Snapshot")]
        ON --> SNAP
        IMP --> SNAP
    end

    TA -. "online: read-only cross-account role (post-close)" .-> ON
    TG -. "online: read-only token / GitHub App" .-> ON
    BUN == "offline: hand-over (pre-close)" ==> IMP
```

## Data flow: from snapshot to approved plan

```mermaid
sequenceDiagram
    autonumber
    participant S as Snapshot
    participant H as Team (acquirer)
    participant ASM as Assess
    participant PLN as Plan
    participant G as Gates + audit log

    S->>H: Inventory, workloads, links, coverage gaps
    H->>G: Curate workloads, owners, classification (overrides)
    G-->>G: Gate "Review inventory" approved on snapshot vN
    S->>ASM: Inventory + overrides
    Note over ASM: + target profile + paved-road catalog
    ASM->>H: Findings with evidence (blockers, warnings)
    H->>G: Accept / waive with reason / false positive
    G-->>G: Gate "Review findings" approved on assessment vN
    ASM->>PLN: Findings + paved-road fit + complexity + cost
    Note over PLN: + deadline + team capacity + effort model
    PLN->>H: Options O1–O6 per workload, recommendation, waves, feasibility
    H->>G: Choose options, adjust effort (tool estimate kept)
    G-->>G: Gate "Approve plan" on plan vN
    Note over S,G: A rescan creates snapshot vN+1 and marks later gates stale
```

## Trust boundaries

```mermaid
flowchart LR
    subgraph TB1["Boundary 1: target environment"]
        T1["Only read APIs called<br/>No secret values or data contents"]
    end
    subgraph TB2["Boundary 2: hand-over"]
        T2["Target reviews manifest<br/>Optional redaction<br/>Signed + encrypted to acquirer key"]
    end
    subgraph TB3["Boundary 3: acquirer environment"]
        T3["Self-hosted, no telemetry<br/>Data isolated per engagement<br/>Encrypted at rest, purge on demand"]
    end
    subgraph TB4["Boundary 4: people"]
        T4["Acquirer roles only (Phase 1.5)<br/>Target access opt-in, inventory only (later)<br/>Every decision in the audit log"]
    end
    TB1 --> TB2 --> TB3 --> TB4
```

## Key rules for contributors

- Collectors call **read-only** APIs and never serialise credentials.
- **No cloud SDK** in the core — cloud-specific code lives in scanner plugins.
- **Deterministic** output: content-derived IDs, stably sorted lists.
- **Errors become coverage gaps** — never swallowed.
- **All business logic is in the core service layer.** The CLI and GUI are thin clients.

Full design principles: [`CLAUDE.md`](https://github.com/pankajads/sherpa/blob/main/CLAUDE.md) · Detailed plan: [`docs/planning/02-phased-plan.md`](https://github.com/pankajads/sherpa/blob/main/docs/planning/02-phased-plan.md)
