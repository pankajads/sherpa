## Summary

<!-- What changed and why. -->

Closes #

## Success criteria

<!-- Copy the success criteria from the linked issue and show how each is met. -->

- [ ] ...

## How it was tested

<!-- Tests added or changed, and how you proved they catch the problem (e.g. they fail without the fix). -->

## Checklist

- [ ] Tests prove the issue's success criteria and run in CI
- [ ] `ruff check .`, `ruff format --check .` and `pytest` pass locally
- [ ] Output stays deterministic: golden tests pass, or `tests/golden/data/` was regenerated with `--update-golden` and the diff is explained above
- [ ] No credentials or secret values can reach snapshots, reports, the store or logs
- [ ] Errors are recorded as coverage gaps, not swallowed
- [ ] Docs updated for any user-facing change
