from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class NamingConvention(BaseModel):
    """Describes how an acquired company names and tags its resources.

    All fields have defaults that match the most common AWS tagging practice.
    Load from a YAML/JSON file to override for a specific company.

    Priority order for workload resolution per resource:
      1. First matching tag key from ``workload_tag_keys``
      2. ``name_regex`` match against the resource name (if set)
      3. Segment of the resource name split by ``name_separator``, at ``name_part``
      4. ``unassigned_label``
    """

    # ------------------------------------------------------------------ tags
    workload_tag_keys: list[str] = Field(
        default=[
            "Application",
            "application",
            "app",
            "Service",
            "service",
            "Team",
            "team",
            "Project",
            "project",
            "Component",
            "component",
        ],
        description="Tag keys checked in order; first match wins.",
    )

    # ------------------------------------------------------------ name parsing
    name_separator: str = Field(
        default="-",
        description="Character used to split resource names into segments.",
    )
    name_part: Literal["first", "last"] | int = Field(
        default="first",
        description=(
            "'first' = first segment, 'last' = last segment, int = zero-based segment index."
        ),
    )
    name_regex: str | None = Field(
        default=None,
        description=(
            "Optional regex with a named group 'workload'. "
            "When set, overrides name_separator/name_part. "
            "Example: r'(?P<workload>[^-]+)-.*'"
        ),
    )

    # ----------------------------------------------------------------- labels
    unassigned_label: str = Field(
        default="unassigned",
        description="Workload name used when no rule matches.",
    )

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _validate_regex(self) -> NamingConvention:
        if self.name_regex is not None:
            compiled = re.compile(self.name_regex)
            if "workload" not in compiled.groupindex:
                raise ValueError("name_regex must contain a named group called 'workload'")
        return self

    # ----------------------------------------------------------------- helpers

    def resolve_workload_name(self, resource_name: str, tags: dict[str, str]) -> tuple[str, str]:
        """Return (workload_name, inferred_from) for a resource.

        inferred_from is one of: 'tag', 'name_regex', 'name_segment', 'unassigned'.
        """
        # 1. Tag lookup (priority-ordered)
        for key in self.workload_tag_keys:
            value = tags.get(key)
            if value:
                return value, "tag"

        # 2. Regex on resource name
        if self.name_regex:
            m = re.match(self.name_regex, resource_name)
            if m:
                return m.group("workload"), "name_regex"

        # 3. Name segment
        segment = self._extract_segment(resource_name)
        if segment:
            return segment, "name_segment"

        return self.unassigned_label, "unassigned"

    def _extract_segment(self, name: str) -> str | None:
        if not name or not self.name_separator:
            return None
        parts = name.split(self.name_separator)
        if len(parts) < 2:
            return None
        if self.name_part == "first":
            return parts[0] or None
        if self.name_part == "last":
            return parts[-1] or None
        # integer index
        idx = int(self.name_part)
        if 0 <= idx < len(parts):
            return parts[idx] or None
        return None

    # ----------------------------------------------------------------- loaders

    @classmethod
    def from_file(cls, path: str) -> NamingConvention:
        """Load from a YAML or JSON file."""
        import json
        from pathlib import Path

        text = Path(path).read_text()
        if path.endswith((".yaml", ".yml")):
            try:
                import yaml

                data = yaml.safe_load(text)
            except ImportError as exc:
                raise ImportError("PyYAML is required to load .yaml files") from exc
        else:
            data = json.loads(text)
        return cls.model_validate(data)

    @classmethod
    def default(cls) -> NamingConvention:
        return cls()
