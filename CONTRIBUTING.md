# Contributing to Sherpa

Thanks for your interest. Sherpa is early, and contributions shape it more than at any later point. There are three useful ways to help, and only one of them is code.

## 1. Practitioners: review the thinking

If you've run M&A cloud integrations (integration lead, migration architect, tech due diligence), the most valuable thing you can do is challenge our assumptions:
- the [migration options and decision rules](docs/wiki/How-It-Works.md);
- the effort model and the [phased plan](docs/planning/02-phased-plan.md).

Open an issue or a discussion. Disagreement is welcome.

## 2. Design partners: test it on a real deal

See [Get Involved](docs/wiki/Get-Involved.md). Please **don't post confidential deal details in public issues**; use the private contact described there.

## 3. Engineers: pick up an issue

### Find something to work on
- [`good first issue`](https://github.com/pankajads/sherpa/issues?q=is%3Aopen+label%3A%22good+first+issue%22) — small and well scoped
- [`help wanted`](https://github.com/pankajads/sherpa/issues?q=is%3Aopen+label%3A%22help+wanted%22) — larger, still clearly specified
- [`phase-0`](https://github.com/pankajads/sherpa/issues?q=is%3Aopen+label%3Aphase-0) — the current focus

Every task issue has **success criteria** and a **test method**. Comment on the issue to claim it before starting, so two people don't build the same thing.

### Set up

Requires Python 3.12+.

```bash
git clone https://github.com/pankajads/sherpa && cd sherpa
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

No AWS account or GitHub token is needed for the test suite. AWS is mocked with moto or async mocks, and GitHub with mocked PyGithub objects.

### Make a change
1. Fork, then branch from `main`.
2. Write the tests that prove the issue's success criteria. **A change is done when those tests are in CI and green**, not when the code compiles.
3. Keep the PR focused on one issue. Use the PR template and link the issue (`Closes #N`).
4. CI must pass: ruff lint + format, pytest with coverage, and the secret scan of Sherpa outputs.

### Non-negotiables
These come from [CLAUDE.md](CLAUDE.md#design-principles). A PR that breaks one will be asked to change, however good it is otherwise.

- **Read-only.** Collectors only call read APIs. Never collect secret values, environment variables or data contents.
- **Never serialise credentials** — not into snapshots, reports, the store, bundles or logs.
- **Deterministic.** The same inputs produce byte-identical outputs: content-derived IDs, stably sorted lists. See [docs/determinism.md](docs/determinism.md); if the golden test fails, regenerate only when the change is intentional.
- **Surface, don't swallow.** Every caught error becomes a recorded coverage gap; no `except: pass`.
- **Compliance is a flag, never a score.**
- **No cloud SDK in the core.** Cloud-specific code lives in scanner plugins.

### Adding a scanner (connector)
Implement `ScannerPlugin` (`sherpa/core/interfaces/scanner.py`) and register it under `[project.entry_points."sherpa.scanners"]`. A new connector must not require changes to core or orchestrator code. The MVP is AWS + GitHub only; for other connectors, open a *connector request* issue first, so we can agree on scope and timing.

## Questions
Open a [discussion](https://github.com/pankajads/sherpa/discussions) or an issue.

## Code of conduct and license
By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are accepted under the project's [MIT license](LICENSE).

## Security
Don't open public issues for vulnerabilities; see [SECURITY.md](SECURITY.md).
