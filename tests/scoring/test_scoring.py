"""The reference-estate scorer (issue #17): answer-key validation and exact metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from testing.reference_estate.answer_key import AnswerKey, check_scenario_coverage
from testing.reference_estate.score import EXIT_REGRESSION, main, score

ROOT = Path(__file__).parent.parent.parent
GOLDEN_SNAPSHOT = ROOT / "tests" / "golden" / "data" / "discovery_snapshot.json"
GOLDEN_KEY = ROOT / "testing" / "reference_estate" / "answer_keys" / "golden-fake-estate.yaml"

ACCT = "111111111111"


def _arn(name: str) -> str:
    return f"arn:aws:sqs:us-east-1:{ACCT}:{name}"


def _key(**overrides) -> AnswerKey:
    data = {
        "version": 1,
        "estate": "tiny",
        "scenarios": [{"id": "trap", "description": "false-link trap"}],
        "resources": [
            {
                "key": "q-a",
                "type": "aws::sqs::queue",
                "match": {"id": _arn("a")},
                "workload": "pay",
            },
            {
                "key": "q-b",
                "type": "aws::sqs::queue",
                "match": {"id": _arn("b")},
                "workload": "pay",
            },
            {
                "key": "web",
                "type": "aws::ec2::instance",
                "match": {"name": "web-1"},
                "workload": "shop",
            },
            {
                "key": "batch",
                "type": "aws::ec2::instance",
                "match": {"tag": {"Team": "rep"}},
                "workload": "rep",
            },
        ],
        "links": [
            {"source": "repo", "target": "q-a", "type": "declares"},
            {"source": "repo", "target": "q-b", "type": "declares"},
            {
                "source": "pipe",
                "target": "web",
                "type": "deploys_to",
                "present": False,
                "scenario": "trap",
            },
        ],
    }
    data.update(overrides)
    return AnswerKey.model_validate(data)


def _resource(rid, rtype="aws::sqs::queue", name="", tags=None, links=()):
    return {
        "id": rid,
        "resource_type": rtype,
        "name": name,
        "tags": tags or {},
        "dependencies": [
            {"source_id": s, "target_id": rid, "dependency_type": t, "plane": "cross"}
            for s, t in links
        ],
    }


def _snapshot(resources, workloads):
    return {
        "resources": resources,
        "workloads": [{"name": n, "resource_ids": ids} for n, ids in workloads.items()],
        "coverage_gaps": [],
    }


# ------------------------------------------------------------------ metrics


class TestScore:
    def test_perfect_snapshot_scores_100(self):
        web = "arn:aws:ec2:us-east-1:111111111111:instance/i-1"
        batch = "arn:aws:ec2:eu-west-1:111111111111:instance/i-2"
        snap = _snapshot(
            [
                _resource(_arn("a"), links=[("repo", "declares")]),
                _resource(_arn("b"), links=[("repo", "declares")]),
                _resource(web, "aws::ec2::instance", name="web-1"),
                _resource(batch, "aws::ec2::instance", tags={"Team": "rep"}),
            ],
            {"pay": [_arn("a"), _arn("b")], "shop": [web], "rep": [batch]},
        )
        result = score(snap, _key())
        assert {k: v["percent"] for k, v in result["headline"].items()} == {
            "resource_recall": 100.0,
            "workload_accuracy": 100.0,
            "link_precision": 100.0,
            "link_recall": 100.0,
        }
        assert result["unexpected_resources"] == result["false_links"] == []

    def test_known_imperfections_give_exact_numbers(self):
        web = "arn:aws:ec2:us-east-1:111111111111:instance/i-1"
        snap = _snapshot(
            [
                _resource(_arn("a"), links=[("repo", "declares")]),  # q-b missing
                _resource(web, "aws::ec2::instance", name="web-1", links=[("pipe", "deploys_to")]),
                _resource(
                    "arn:aws:ec2:eu-west-1:111111111111:instance/i-2",
                    "aws::ec2::instance",
                    tags={"Team": "rep"},
                    links=[("pipe", "deploys_to")],
                ),
                _resource(_arn("stray")),  # unexpected
            ],
            {
                "pay": [_arn("a")],
                "wrong": [web],
                "rep": ["arn:aws:ec2:eu-west-1:111111111111:instance/i-2"],
            },
        )
        r = score(snap, _key())

        assert r["headline"]["resource_recall"] == {"hit": 3, "total": 4, "percent": 75.0}
        assert r["headline"]["workload_accuracy"] == {"hit": 2, "total": 3, "percent": 66.7}
        # actual cross links: repo->a (TP), pipe->web (forbidden), pipe->batch (false)
        assert r["headline"]["link_precision"] == {"hit": 1, "total": 3, "percent": 33.3}
        # expected: repo->a (found), repo->b (target missing)
        assert r["headline"]["link_recall"] == {"hit": 1, "total": 2, "percent": 50.0}
        assert r["missing_resources"] == ["q-b"]
        assert r["unexpected_resources"] == [_arn("stray")]
        assert r["wrong_workloads"] == {"web": {"expected": "shop", "actual": "wrong"}}
        assert (
            len(r["forbidden_links_found"]) == 1
            and "pipe -[deploys_to]->" in r["forbidden_links_found"][0]
        )
        assert r["resource_recall_by_type"]["aws::sqs::queue"] == {
            "hit": 1,
            "total": 2,
            "percent": 50.0,
        }
        assert r["link_by_type"]["declares"]["recall"] == {"hit": 1, "total": 2, "percent": 50.0}
        assert r["link_by_type"]["deploys_to"]["precision"] == {
            "hit": 0,
            "total": 2,
            "percent": 0.0,
        }

    def test_name_and_tag_match_only_within_type(self):
        # A queue named web-1 must not satisfy the EC2 "web" expectation.
        snap = _snapshot([_resource(_arn("x"), name="web-1", tags={"Team": "rep"})], {})
        result = score(snap, _key())
        assert set(result["missing_resources"]) >= {"web", "batch"}

    def test_ambiguous_match_is_reported(self):
        a = _resource(
            "arn:aws:ec2:us-east-1:111111111111:instance/i-1", "aws::ec2::instance", name="web-1"
        )
        b = _resource(
            "arn:aws:ec2:us-east-1:111111111111:instance/i-9", "aws::ec2::instance", name="web-1"
        )
        result = score(_snapshot([a, b], {}), _key())
        assert result["ambiguous_matches"] == {"web": sorted([a["id"], b["id"]])}

    def test_empty_ratios_are_not_applicable(self):
        result = score(_snapshot([], {}), _key(links=[], scenarios=[]))
        assert result["headline"]["link_precision"]["percent"] is None


# ------------------------------------------------------------------ answer key validation


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"links": [{"source": "r", "target": "nope", "type": "declares"}], "scenarios": []},
            "link target 'nope'",
        ),
        (
            {"resources": [_key().resources[0].model_dump()] * 2, "links": [], "scenarios": []},
            "duplicate resource key 'q-a'",
        ),
        ({"scenarios": []}, "scenario 'trap' is used but not declared"),
        (
            {
                "scenarios": [
                    {"id": "trap", "description": "x"},
                    {"id": "unused", "description": "y"},
                ]
            },
            "scenario 'unused' is declared but never used",
        ),
        (
            {
                "resources": [{"key": "Bad_Key", "type": "t", "match": {"id": "x"}}],
                "links": [],
                "scenarios": [],
            },
            "not kebab-case",
        ),
        (
            {
                "resources": [{"key": "a", "type": "t", "match": {"id": "x", "name": "y"}}],
                "links": [],
                "scenarios": [],
            },
            "exactly one of",
        ),
        ({"surprise": True}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_answer_keys_are_rejected(change, message):
    with pytest.raises(ValueError, match=message):
        _key(**change)


def test_scenario_coverage_against_terraform(tmp_path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_instance" "web" {\n  tags = {\n    "sherpa-ref:scenario" = "trap"\n  }\n}\n'
        'resource "aws_s3_bucket" "b" {\n  tags = { "sherpa-ref:scenario" = "public-bucket" }\n}\n'
    )
    problems = check_scenario_coverage(_key(), tmp_path)
    assert problems == [
        "scenario 'public-bucket' is built in Terraform but missing from the answer key"
    ]
    (tmp_path / "main.tf").write_text("")
    assert check_scenario_coverage(_key(), tmp_path) == [
        "scenario 'trap' is in the answer key but not built in Terraform"
    ]


# ------------------------------------------------------------------ CLI and regression gate


def _write_snapshot(tmp_path: Path, recall_hit: int) -> Path:
    resources = [_resource(_arn("a")), _resource(_arn("b"))][:recall_hit]
    path = tmp_path / f"snap{recall_hit}.json"
    path.write_text(json.dumps(_snapshot(resources, {})))
    return path


def _key_file(tmp_path: Path) -> Path:
    path = tmp_path / "key.yaml"
    path.write_text(json.dumps(_key(links=[], scenarios=[]).model_dump(exclude_none=True)))
    return path


def test_cli_writes_outputs(tmp_path, capsys):
    out_json, out_md = tmp_path / "s.json", tmp_path / "s.md"
    code = main(
        [
            str(_write_snapshot(tmp_path, 2)),
            str(_key_file(tmp_path)),
            "--json",
            str(out_json),
            "--markdown",
            str(out_md),
        ]
    )
    assert code == 0
    assert json.loads(out_json.read_text())["headline"]["resource_recall"]["hit"] == 2
    assert "X-1 Resource recall" in out_md.read_text()


def test_cli_bad_input_exits_1(tmp_path):
    assert main([str(tmp_path / "missing.json"), str(_key_file(tmp_path))]) == 1


def test_regression_gate(tmp_path, capsys):
    key, history = _key_file(tmp_path), tmp_path / "history.jsonl"
    args = ["--history", str(history), "--max-drop", "2"]

    assert main([str(_write_snapshot(tmp_path, 2)), str(key), *args]) == 0  # baseline 50%
    assert main([str(_write_snapshot(tmp_path, 1)), str(key), *args]) == EXIT_REGRESSION  # 25%
    assert "resource_recall: 50.0% -> 25.0%" in capsys.readouterr().err
    # A regressed run never becomes the baseline: the next bad run still fails.
    assert main([str(_write_snapshot(tmp_path, 1)), str(key), *args]) == EXIT_REGRESSION
    assert len(history.read_text().splitlines()) == 3


# ------------------------------------------------------------------ baseline on the golden estate


def test_golden_estate_baseline():
    """Today's scanner against the golden estate's answer key. Update deliberately.

    History: the first baseline was 16/17 recall and 3/4 link recall, from an SQS ARN bug this
    scorer exposed. Then finding C-6 (#27) linked the deploy workflow to every resource in the
    account: link precision 4/20, deploys_to 1/17, both trap links present.
    """
    snapshot = json.loads(GOLDEN_SNAPSHOT.read_text())
    result = score(snapshot, AnswerKey.from_file(GOLDEN_KEY))

    assert {k: (v["hit"], v["total"]) for k, v in result["headline"].items()} == {
        "resource_recall": (17, 17),
        "workload_accuracy": (17, 17),
        "link_precision": (4, 4),
        "link_recall": (4, 4),
    }
    assert result["missing_resources"] == []
    assert result["unexpected_resources"] == []
    assert result["forbidden_links_found"] == []
    assert result["link_by_type"]["deploys_to"]["precision"] == {
        "hit": 1,
        "total": 1,
        "percent": 100.0,
    }
