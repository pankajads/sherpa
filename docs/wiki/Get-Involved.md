# Get Involved

## Design partners wanted

Sherpa is only as good as the acquisitions it is tested against. We're looking for organisations that:
- have a **current or upcoming AWS → AWS acquisition**, or
- completed one recently and can share (anonymised) **actual migration effort**, so we can back-test the effort model.

As a design partner you get early access, direct influence on the rule packs and the paved-road catalog format, and a plan for your deal produced with your team. Everything runs **self-hosted in your environment**; no deal data leaves it.

Interested? Use the [design partner form](https://github.com/pankajads/sherpa/issues/new?template=design_partner.yml). It is public, so never name a deal or target that isn't already public; if you prefer to stay private, contact the maintainer via their [GitHub profile](https://github.com/pankajads).

## Contributors

Good places to start:
- **Phase 0 hardening** — issues labelled [`phase-0`](https://github.com/pankajads/sherpa/issues?q=label%3Aphase-0)
- **Reference estate** — Terraform and test fixtures (Phase 1, epic [#6](https://github.com/pankajads/sherpa/issues/6))
- **Rule packs** — landing-zone conformance and GDPR rules are plain, versioned, testable rules
- **Scanner plugins** — new connectors implement the scanner plugin interface without touching the core

Start with [CONTRIBUTING.md](https://github.com/pankajads/sherpa/blob/main/CONTRIBUTING.md): setup, how to claim an issue, and the non-negotiables. Also read [[Philosophy]] and [[Architecture and Data Flow|Architecture-and-Data-Flow]]. Every change needs tests that prove its success criteria, keeps output deterministic, and never serialises credentials.

## Practitioners

If you've run M&A cloud integrations (integration leads, migration architects, ex-consultancy delivery leads), we'd value a review of the **option decision table** and the **effort model** before they ship.

## License

MIT. Use it, audit it, extend it.
