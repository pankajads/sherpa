# Reference estates and scoring

Sherpa's accuracy claims (MVP exit criteria X-1 to X-3 in [the plan](../../docs/planning/02-phased-plan.md)) are measured against **reference estates**: environments whose correct inventory is written down in advance as an **answer key**. A score is only as honest as its answer key.

## Answer keys

An answer key (`answer_keys/*.yaml`) records what a *correct* discovery must produce:

- **resources:** each with a stable `key`, its Sherpa `type`, how to find it (`match` by `id`, by `name`, or by `tag`; use name or tag for resources whose IDs are generated on creation), and the `workload` it belongs to;
- **links:** cross-plane edges (repository/pipeline → resource) that must exist, and known traps that must **not** (`present: false`);
- **scenarios:** the deliberate situations the estate exercises. Each must be used, and each used one must be declared.

**Write it from the estate's design, never by copying Sherpa's output.** The first key here was briefly written with Sherpa's SQS ID format; correcting it to the real ARN is what exposed Sherpa's SQS bug.

`AnswerKey.from_file()` validates a key: unknown fields, dangling link targets, duplicate keys and unused or undeclared scenarios are all rejected. For a Terraform estate, `check_scenario_coverage()` confirms every `"sherpa-ref:scenario"` tag in the Terraform has an answer-key entry and vice versa, so the estate can't change without its key.

## Scoring

```bash
python -m testing.reference_estate.score SNAPSHOT.json answer_keys/<estate>.yaml \
    [--json score.json] [--markdown score.md] [--history history.jsonl --max-drop 2]
```

| Metric | Meaning |
|---|---|
| X-1 resource recall | Expected resources found, overall and per type; unexpected resources listed |
| X-2 workload accuracy | Found resources assigned to the expected workload |
| X-3 link precision / recall | Cross-plane links, overall and per link type; trap links found are listed separately |

With `--history`, each run is appended to a JSONL trend file. The command exits `3` if any headline metric drops more than `--max-drop` points below the last accepted run. A regressed run never becomes the baseline. Bad input exits `1`.

## Estates

| Estate | Answer key | Status |
|---|---|---|
| Golden fake estate (`tests/golden/fake_estate.py`): 1 account, 2 regions, 17 resources, 3 repos | `answer_keys/golden-fake-estate.yaml` | Scored in CI (`tests/scoring`) |
| Terraform sandbox estate: 3 accounts, ~300 resources, 20 repos ([#15](https://github.com/pankajads/sherpa/issues/15), [#16](https://github.com/pankajads/sherpa/issues/16)) | not yet written | Waiting on the sandbox AWS Organization and GitHub org; the nightly scoring job will be added with it |

### Baseline (golden fake estate, October 2026)

| Metric | Score | Why |
|---|---|---|
| X-1 resource recall | 94.1% (16/17) | SQS IDs built as `…:queue/<name>` instead of the real ARN `…:<name>` |
| X-2 workload accuracy | 100% (16/16) | |
| X-3 link precision | 15.8% (3/19) | Finding C-6: the deploy workflow is linked to every resource in the account |
| X-3 link recall | 75% (3/4) | The Terraform → SQS link is missed (same SQS ID bug) |

`tests/scoring/test_golden_estate_baseline` pins these numbers, so any change to them shows up in review.
