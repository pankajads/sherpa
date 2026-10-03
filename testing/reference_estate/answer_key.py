"""The answer key: what a correct discovery of a reference estate must produce.

An answer key is written from the estate's *design*, never copied from Sherpa's output, so
scoring measures how far Sherpa is from the truth. See README.md in this directory.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

SCENARIO_TAG = "sherpa-ref:scenario"
_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Match(BaseModel):
    """How to find the expected resource in a snapshot. Exactly one way must be given.

    `id` suits resources whose IDs are known up front (named ARNs). `name` or `tag` (within
    the resource's type) suit resources whose IDs are generated on creation (EC2 instances).
    """

    id: str | None = None
    name: str | None = None
    tag: dict[str, str] | None = None

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _one_way(self) -> Match:
        if sum(x is not None for x in (self.id, self.name, self.tag)) != 1:
            raise ValueError("give exactly one of: id, name, tag")
        return self


class ExpectedResource(BaseModel):
    key: str  # stable label used by links, e.g. "payments-db"
    type: str  # Sherpa resource type, e.g. aws::rds::db-instance (needed for per-type recall)
    match: Match
    workload: str | None = None  # the workload a correct tool assigns it to
    scenario: str | None = None

    model_config = {"extra": "forbid"}


class ExpectedLink(BaseModel):
    """A cross-plane edge: repository or pipeline → resource."""

    source: str  # repository or pipeline ID, e.g. github.com/acme/api
    target: str  # an ExpectedResource key
    type: str  # dependency type, e.g. declares, deploys_to
    present: bool = True  # False: this link must NOT be found (a known false-link trap)
    scenario: str | None = None

    model_config = {"extra": "forbid"}


class Scenario(BaseModel):
    id: str
    description: str

    model_config = {"extra": "forbid"}


class AnswerKey(BaseModel):
    version: Literal[1]
    estate: str
    description: str = ""
    scenarios: list[Scenario] = Field(default_factory=list)
    resources: list[ExpectedResource]
    links: list[ExpectedLink] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _consistent(self) -> AnswerKey:
        problems: list[str] = []
        keys = [r.key for r in self.resources]
        for key in keys:
            if not _KEY.match(key):
                problems.append(f"resource key '{key}' is not kebab-case")
        problems += [
            f"duplicate resource key '{k}'" for k in sorted({k for k in keys if keys.count(k) > 1})
        ]
        for link in self.links:
            if link.target not in keys:
                problems.append(f"link target '{link.target}' is not a resource key")
        scenario_ids = [s.id for s in self.scenarios]
        problems += [
            f"duplicate scenario '{s}'"
            for s in sorted({s for s in scenario_ids if scenario_ids.count(s) > 1})
        ]
        used = {r.scenario for r in self.resources} | {lk.scenario for lk in self.links}
        used.discard(None)
        problems += [
            f"scenario '{s}' is used but not declared" for s in sorted(used - set(scenario_ids))
        ]
        problems += [
            f"scenario '{s}' is declared but never used" for s in sorted(set(scenario_ids) - used)
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> AnswerKey:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


_TF_SCENARIO = re.compile(r'"sherpa-ref:scenario"\s*=\s*"([^"]+)"')


def scenario_tags_in_terraform(terraform_dir: str | Path) -> set[str]:
    """Scenario IDs tagged on resources in a Terraform tree (`"sherpa-ref:scenario" = "..."`)."""
    found: set[str] = set()
    for tf in sorted(Path(terraform_dir).rglob("*.tf")):
        found.update(_TF_SCENARIO.findall(tf.read_text()))
    return found


def check_scenario_coverage(key: AnswerKey, terraform_dir: str | Path) -> list[str]:
    """Problems when the Terraform estate and the answer key disagree on scenarios.

    Every scenario built into the estate must be in the answer key, and vice versa, so the
    estate can't change without the answer key being updated.
    """
    built = scenario_tags_in_terraform(terraform_dir)
    declared = {s.id for s in key.scenarios}
    return [
        f"scenario '{s}' is built in Terraform but missing from the answer key"
        for s in sorted(built - declared)
    ] + [
        f"scenario '{s}' is in the answer key but not built in Terraform"
        for s in sorted(declared - built)
    ]
