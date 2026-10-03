"""Score a Sherpa snapshot against an answer key.

    python -m testing.reference_estate.score SNAPSHOT.json ANSWER_KEY.yaml \\
        [--json OUT.json] [--markdown OUT.md] [--history HISTORY.jsonl --max-drop 2.0]

Metrics (docs/planning/02-phased-plan.md, MVP exit criteria):
  X-1 resource recall (overall and per type), plus unexpected resources found
  X-2 workload accuracy: matched resources assigned to the expected workload
  X-3 cross-plane link precision and recall (overall and per link type)

The snapshot is read as plain JSON, so masked golden files and real snapshot files both work.

Exit codes: 0 scored, 1 bad input, 3 a headline metric dropped more than --max-drop points
versus the last accepted run in --history.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .answer_key import AnswerKey, ExpectedResource

HEADLINE = ("resource_recall", "workload_accuracy", "link_precision", "link_recall")
EXIT_REGRESSION = 3


@dataclass(frozen=True)
class Ratio:
    hit: int
    total: int

    @property
    def percent(self) -> float | None:
        return None if self.total == 0 else round(100 * self.hit / self.total, 1)

    def as_dict(self) -> dict[str, Any]:
        return {"hit": self.hit, "total": self.total, "percent": self.percent}

    def __str__(self) -> str:
        return "n/a" if self.percent is None else f"{self.percent}% ({self.hit}/{self.total})"


def _matches(expected: ExpectedResource, actual: dict[str, Any]) -> bool:
    m = expected.match
    if m.id is not None:
        return actual["id"] == m.id
    if actual["resource_type"] != expected.type:
        return False
    if m.name is not None:
        return actual.get("name") == m.name
    tags = actual.get("tags") or {}
    return all(tags.get(k) == v for k, v in (m.tag or {}).items())


def score(snapshot: dict[str, Any], key: AnswerKey) -> dict[str, Any]:
    actual_resources = snapshot.get("resources", [])

    # ---- X-1: which expected resources were found
    matched: dict[str, str] = {}  # expected key -> actual resource id
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for exp in key.resources:
        hits = sorted(r["id"] for r in actual_resources if _matches(exp, r))
        if not hits:
            missing.append(exp.key)
            continue
        if len(hits) > 1:
            ambiguous[exp.key] = hits
        matched[exp.key] = hits[0]
    matched_ids = set(matched.values())
    unexpected = sorted(r["id"] for r in actual_resources if r["id"] not in matched_ids)

    by_type: dict[str, list[int]] = {}
    for exp in key.resources:
        hit_total = by_type.setdefault(exp.type, [0, 0])
        hit_total[1] += 1
        hit_total[0] += exp.key in matched
    recall_by_type = {t: Ratio(h, n) for t, (h, n) in sorted(by_type.items())}

    # ---- X-2: workload assignment of the resources that were found
    workload_of: dict[str, str] = {}
    for wl in snapshot.get("workloads", []):
        for rid in wl.get("resource_ids", []):
            workload_of[rid] = wl["name"]
    with_workload = [e for e in key.resources if e.workload and e.key in matched]
    wrong_workload = {
        e.key: {"expected": e.workload, "actual": workload_of.get(matched[e.key])}
        for e in with_workload
        if workload_of.get(matched[e.key]) != e.workload
    }

    # ---- X-3: cross-plane links
    actual_links = {
        (dep["source_id"], dep["target_id"], dep["dependency_type"])
        for r in actual_resources
        for dep in r.get("dependencies", [])
        if dep.get("plane") == "cross"
    }
    expected_links, forbidden_links = set(), set()
    unresolved: list[str] = []
    for link in key.links:
        if link.target not in matched:
            if link.present:
                unresolved.append(f"{link.source} -[{link.type}]-> {link.target}")
            continue
        triple = (link.source, matched[link.target], link.type)
        (expected_links if link.present else forbidden_links).add(triple)
    true_pos = actual_links & expected_links
    false_pos = actual_links - expected_links
    false_neg = len(expected_links - actual_links) + len(unresolved)

    link_types = sorted(
        {t for _, _, t in actual_links | expected_links}
        | {lk.type for lk in key.links if lk.present}
    )
    by_link_type = {}
    for t in link_types:
        tp = sum(1 for x in true_pos if x[2] == t)
        fp = sum(1 for x in false_pos if x[2] == t)
        exp = sum(1 for x in expected_links if x[2] == t) + sum(
            1 for lk in key.links if lk.present and lk.type == t and lk.target not in matched
        )
        by_link_type[t] = {"precision": Ratio(tp, tp + fp), "recall": Ratio(tp, exp)}

    metrics = {
        "resource_recall": Ratio(len(matched), len(key.resources)),
        "workload_accuracy": Ratio(len(with_workload) - len(wrong_workload), len(with_workload)),
        "link_precision": Ratio(len(true_pos), len(actual_links)),
        "link_recall": Ratio(len(true_pos), len(true_pos) + false_neg),
    }
    gaps = snapshot.get("coverage_gaps", [])
    return {
        "estate": key.estate,
        "headline": {name: metrics[name].as_dict() for name in HEADLINE},
        "resource_recall_by_type": {t: r.as_dict() for t, r in recall_by_type.items()},
        "link_by_type": {
            t: {k: v.as_dict() for k, v in d.items()} for t, d in by_link_type.items()
        },
        "missing_resources": missing,
        "ambiguous_matches": ambiguous,
        "unexpected_resources": unexpected,
        "wrong_workloads": wrong_workload,
        "false_links": sorted(f"{s} -[{t}]-> {d}" for s, d, t in false_pos),
        "forbidden_links_found": sorted(
            f"{s} -[{t}]-> {d}" for s, d, t in actual_links & forbidden_links
        ),
        "missing_links": sorted(
            [f"{s} -[{t}]-> {d}" for s, d, t in expected_links - actual_links] + unresolved
        ),
        "coverage_gaps": len(gaps),
    }


def _ratio(d: dict[str, Any]) -> str:
    return str(Ratio(d["hit"], d["total"]))


def render_markdown(result: dict[str, Any]) -> str:
    h = result["headline"]
    lines = [
        f"# Reference estate score: {result['estate']}",
        "",
        "| Metric | Score |",
        "|---|---|",
        f"| X-1 Resource recall | {_ratio(h['resource_recall'])} |",
        f"| X-2 Workload accuracy | {_ratio(h['workload_accuracy'])} |",
        f"| X-3 Link precision | {_ratio(h['link_precision'])} |",
        f"| X-3 Link recall | {_ratio(h['link_recall'])} |",
        f"| Unexpected resources | {len(result['unexpected_resources'])} |",
        f"| Coverage gaps in snapshot | {result['coverage_gaps']} |",
        "",
        "## Resource recall by type",
        "",
        "| Type | Recall |",
        "|---|---|",
        *[f"| `{t}` | {_ratio(r)} |" for t, r in result["resource_recall_by_type"].items()],
        "",
        "## Links by type",
        "",
        "| Type | Precision | Recall |",
        "|---|---|---|",
        *[
            f"| `{t}` | {_ratio(d['precision'])} | {_ratio(d['recall'])} |"
            for t, d in result["link_by_type"].items()
        ],
        "",
    ]
    for title, field in (
        ("Missing resources", "missing_resources"),
        ("Unexpected resources", "unexpected_resources"),
        ("Forbidden links found (known false-link traps)", "forbidden_links_found"),
        ("Missing links", "missing_links"),
        ("False links", "false_links"),
    ):
        if result[field]:
            lines += [
                f"## {title} ({len(result[field])})",
                "",
                *[f"- `{x}`" for x in result[field]],
                "",
            ]
    if result["wrong_workloads"]:
        lines += [f"## Wrong workloads ({len(result['wrong_workloads'])})", ""]
        lines += [
            f"- `{k}`: expected `{v['expected']}`, got `{v['actual']}`"
            for k, v in sorted(result["wrong_workloads"].items())
        ]
        lines.append("")
    return "\n".join(lines)


def check_regression(
    result: dict[str, Any], history: Path, max_drop: float
) -> tuple[list[str], dict[str, Any]]:
    """Compare headline metrics with the last accepted run; return (regressions, entry)."""
    baseline = None
    if history.exists():
        for line in history.read_text().splitlines():
            entry = json.loads(line)
            if not entry.get("regressed"):
                baseline = entry
    regressions = []
    if baseline is not None:
        for name in HEADLINE:
            before = baseline["headline"][name]["percent"]
            now = result["headline"][name]["percent"]
            if before is not None and now is not None and before - now > max_drop:
                regressions.append(f"{name}: {before}% -> {now}% (more than {max_drop} points)")
    entry = {
        "estate": result["estate"],
        "headline": result["headline"],
        "regressed": bool(regressions),
    }
    return regressions, entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("answer_key", type=Path)
    parser.add_argument("--json", dest="json_out", type=Path)
    parser.add_argument("--markdown", dest="md_out", type=Path)
    parser.add_argument(
        "--history", type=Path, help="JSONL trend file; enables the regression gate"
    )
    parser.add_argument("--max-drop", type=float, default=2.0)
    args = parser.parse_args(argv)

    try:
        snapshot = json.loads(args.snapshot.read_text())
        key = AnswerKey.from_file(args.answer_key)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result = score(snapshot, key)
    markdown = render_markdown(result)
    print(markdown)
    if args.json_out:
        args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.md_out:
        args.md_out.write_text(markdown + "\n")

    if args.history:
        regressions, entry = check_regression(result, args.history, args.max_drop)
        with args.history.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        if regressions:
            print("REGRESSION:\n  " + "\n  ".join(regressions), file=sys.stderr)
            return EXIT_REGRESSION
    return 0


if __name__ == "__main__":
    sys.exit(main())
