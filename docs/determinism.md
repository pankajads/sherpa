# Determinism

**Guarantee:** the same inputs (what the APIs return, plus the scan configuration) produce byte-identical snapshot JSON and reports, apart from each run's own `snapshot_id`, `started_at` and `completed_at`. The order in which AWS or GitHub return things never matters.

Later features depend on this: snapshot diffs, human overrides that survive rescans, and approvals bound to an exact data version.

## How it is enforced

| Mechanism | Where |
|---|---|
| Every list in a persisted model is sorted when the model is constructed (resources, repos, pipelines by `id`; workloads by `name`; dependency edges, package dependencies, stages, coverage gaps, errors, ID lists by stable keys) | `sherpa/core/models/inventory.py` field validators |
| Tags are stored with sorted keys; snapshot files and DB JSON columns are written with `sort_keys=True` | `InventorySnapshot.to_canonical_json()`, `sherpa/core/store/store.py` |
| Policy documents are stored as key-sorted JSON text | `_policy_text()` in the AWS collector |
| When a workload's resources were grouped by different methods, `inferred_from` reports the strongest (`tag` > `name_regex` > `name_segment` > `unassigned`) | `workload_inferrer.py` |

**Pitfall:** pydantic's `model_copy(update=...)` skips validation, so it skips the sorting too. When you change a list, construct a new model, or sort explicitly as `cross_plane_linker.py` does.

## ID scheme

| Entity | ID |
|---|---|
| Resource | Provider ID (the ARN for AWS) |
| Repository | `{host}/{org}/{repo}` |
| Pipeline | `{repo_id}/.github/workflows/{file}` |
| Workload | `wl-` + first 16 hex characters of SHA-256 of the workload name (`workload_id()`) |
| Snapshot | Random UUID per run (it identifies the run, so it is masked in comparisons) |

Workload IDs depend only on the exact name. That keeps them stable when the naming convention changes, so future overrides keyed on a workload survive a convention change. Names are case-sensitive: `Payments` and `payments` are different workloads today. Merging such variants is a workload-inference decision (P1-5.1), not an ID decision.

## Tests

- `tests/golden/test_determinism.py` runs real scanners against a fixed fake estate (`tests/golden/fake_estate.py`, 1 account, 2 regions, 11 AWS services, 3 repos with a workflow):
  - two runs are byte-identical;
  - runs with responses shuffled under 4 seeds (item order, page boundaries, tag and dict key order, repo, tree and directory order) are byte-identical to the baseline;
  - output matches the checked-in golden files in `tests/golden/data/`.
- `tests/unit/orchestrator/test_order_independence.py`: Hypothesis property tests that workload inference, cross-plane linking, coverage validation and snapshot serialisation give equal results for any permutation of their inputs.

## When output changes on purpose

```bash
pytest tests/golden --update-golden   # rewrites tests/golden/data/*
git diff tests/golden/data/           # review: every change should be explained by your PR
```

The golden files currently capture a known defect: finding C-6, where a pipeline gets linked to every resource in an account. Fixing it will remove those edges from the golden snapshot. That diff is the evidence the fix works.
