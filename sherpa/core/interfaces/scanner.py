from __future__ import annotations

import importlib.metadata
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from sherpa.core.models import (
    CoverageGap,
    Pipeline,
    Repository,
    Resource,
    ScanConfig,
    ScanIdentity,
)


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []

    @classmethod
    def ok(cls) -> ValidationResult:
        return cls(valid=True)

    @classmethod
    def fail(cls, *errors: str) -> ValidationResult:
        return cls(valid=False, errors=list(errors))


class ScanResult(BaseModel):
    scanner_type: str
    resources: list[Resource] = []
    repositories: list[Repository] = []
    pipelines: list[Pipeline] = []
    coverage_gaps: list[CoverageGap] = []
    errors: list[str] = []
    scan_identities: list[ScanIdentity] = []
    metadata: dict[str, Any] = {}


ENTRY_POINT_GROUP = "sherpa.scanners"
_SCANNER_TYPE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ScannerPlane(StrEnum):
    CLOUD = "cloud"
    CODE = "code"
    PIPELINE = "pipeline"


class ScannerPlugin(ABC):
    """Base class every Sherpa scanner must implement.

    Registration: declare the class in your package's pyproject.toml under
    [project.entry_points."sherpa.scanners"]. The orchestrator discovers it there; no core
    change is needed. See docs/plugins.md.
    """

    @property
    @abstractmethod
    def scanner_type(self) -> str:
        """Unique kebab-case identifier, e.g. 'aws-cloud', 'github-code'."""

    @property
    @abstractmethod
    def plane(self) -> ScannerPlane:
        """Which plane this scanner discovers: cloud, code or pipeline."""

    @property
    def run_stage(self) -> int:
        """Scanners run stage by stage (lowest first), in parallel within a stage.

        Cloud scanners run first by default.
        """
        return 0 if self.plane == ScannerPlane.CLOUD else 1

    @abstractmethod
    def applies_to(self, config: ScanConfig) -> bool:
        """True when this run's config asks for what this scanner discovers."""

    @abstractmethod
    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        """Return ok() when this scanner can run with the given config."""

    @abstractmethod
    async def scan(self, config: ScanConfig) -> ScanResult:
        """Execute the scan and return all discovered entities.

        Contract (tests/contract): lists sorted by id, no credentials in the result, and
        every error accompanied by at least one coverage gap.
        """


@dataclass(frozen=True)
class LoadedScanners:
    scanners: list[ScannerPlugin] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_scanners(
    entry_points: Iterable[importlib.metadata.EntryPoint] | None = None,
) -> LoadedScanners:
    """Discover scanner plugins registered under the `sherpa.scanners` entry-point group.

    Never raises: a plugin that fails to import, isn't a ScannerPlugin, can't be
    instantiated, declares an invalid or duplicate scanner_type is reported in `errors`
    and left out. Order is deterministic: (run_stage, scanner_type).
    """
    if entry_points is None:
        entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    # Editable installs can expose the same entry point twice; identical ones are one plugin.
    unique = sorted({(ep.name, ep.value): ep for ep in entry_points}.items())

    scanners: list[ScannerPlugin] = []
    errors: list[str] = []
    seen: dict[str, str] = {}
    for (name, value), ep in unique:
        where = f"scanner plugin '{name}' ({value})"
        try:
            obj = ep.load()
        except Exception as exc:
            errors.append(f"{where} failed to load: {type(exc).__name__}: {exc}")
            continue
        if not (isinstance(obj, type) and issubclass(obj, ScannerPlugin)):
            errors.append(f"{where} is not a ScannerPlugin subclass")
            continue
        try:
            instance = obj()
            scanner_type = instance.scanner_type
            ScannerPlane(instance.plane)
        except Exception as exc:
            errors.append(f"{where} could not be initialised: {type(exc).__name__}: {exc}")
            continue
        if not _SCANNER_TYPE.match(scanner_type):
            errors.append(f"{where} declares invalid scanner_type '{scanner_type}'")
            continue
        if scanner_type in seen:
            errors.append(
                f"{where} declares scanner_type '{scanner_type}', already provided by "
                f"'{seen[scanner_type]}'; ignored"
            )
            continue
        seen[scanner_type] = name
        scanners.append(instance)
    scanners.sort(key=lambda s: (s.run_stage, s.scanner_type))
    return LoadedScanners(scanners=scanners, errors=errors)
