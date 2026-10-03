# Scanner plugins

Every scanner, built-in or not, is a plugin. Sherpa finds plugins through the **`sherpa.scanners`** entry-point group, so you can add a connector from your own package without changing Sherpa's code.

## Write one

```python
from sherpa.core.interfaces import ScannerPlane, ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import ScanConfig


class GitlabCodeScanner(ScannerPlugin):
    @property
    def scanner_type(self) -> str:  # unique, kebab-case
        return "gitlab-code"

    @property
    def plane(self) -> ScannerPlane:  # cloud | code | pipeline
        return ScannerPlane.CODE

    def applies_to(self, config: ScanConfig) -> bool:
        return ...  # does this run ask for what you discover?

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        return ValidationResult.ok()

    async def scan(self, config: ScanConfig) -> ScanResult:
        return ScanResult(scanner_type=self.scanner_type, repositories=[...])
```

`run_stage` is optional. Scanners run stage by stage, lowest first, and in parallel within a stage. By default cloud scanners are stage 0 and everything else is stage 1.

## Register it

In your package's `pyproject.toml`:

```toml
[project.entry-points."sherpa.scanners"]
"gitlab-code" = "my_package.scanner:GitlabCodeScanner"
```

> **Spell it `entry-points`, with a hyphen** (PEP 621). With an underscore, build backends such as hatchling silently ignore the table and your plugin is never registered. Sherpa's own built-ins were missing for exactly this reason until #13.

Install the package next to Sherpa (`pip install my-package`), and the next `sherpa discover` picks the plugin up.

## The contract

Every plugin must follow these rules. `tests/contract/test_scanner_contract.py` checks them for every installed scanner:

- **Structure:** a unique kebab-case `scanner_type`, a valid `plane`, an integer `run_stage`, and an `applies_to` that returns a bool.
- **Sorted output:** resources, repositories and pipelines sorted by `id`.
- **No credentials in results**, ever. See [SECURITY.md](../SECURITY.md).
- **Every error comes with at least one coverage gap**, so a failure is never silent. Fill in the gap's structured fields:
  - `scanner`: your `scanner_type`;
  - `scope`: what you were reading, e.g. `github.com/acme/api`;
  - `error_class`: `access_denied`, `throttled`, `not_found`, `unavailable`, `invalid_content` or `other`;
  - for cloud scanners, `affected_services` and `affected_regions`.

  "Expected absence", such as a repo with no workflows, is not a gap. Ruff enforces this (`BLE001`, `S110`, `S112`): a deliberate catch-all needs `# noqa: BLE001 - <why>` and must record the failure.
- **Read-only,** with no secret values or data contents collected (see CLAUDE.md).

Structural checks run for any plugin. To get the behavioural checks too, add a scenario for your `scanner_type` to `SCENARIOS` in the contract suite, ideally a success case and a failure case.

## What Sherpa does when a plugin misbehaves

| Situation | Result |
|---|---|
| Plugin fails to import, isn't a `ScannerPlugin`, can't be created, has an invalid or duplicate `scanner_type` | Left out; error + `error` coverage gap in the snapshot; the run continues |
| Plugin's `scan()` raises | Error + `error` coverage gap ("nothing from the <plane> plane it covers is in this inventory"); other scanners' results are kept |
| `validate_config()` fails | The run stops before scanning (configuration errors are fixed up front) |

## Architecture rule

`sherpa.core` and `sherpa.orchestrator` must never import a concrete scanner or a cloud/SCM SDK (`boto3`, `aioboto3`, `github`, …). `lint-imports` (import-linter, configured in `pyproject.toml`) enforces this in CI.

## Example

`tests/fixtures/dummy_plugin/` is a complete, separately installed plugin package that CI uses to prove the mechanism end to end.
