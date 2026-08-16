from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from sherpa.core.models import (
    CoverageGap,
    Pipeline,
    Repository,
    Resource,
    ScanConfig,
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
    metadata: dict[str, Any] = {}


class ScannerPlugin(ABC):
    """Base class every Sherpa scanner must implement.

    Registration: declare in pyproject.toml under [project.entry_points."sherpa.scanners"].
    """

    @property
    @abstractmethod
    def scanner_type(self) -> str:
        """Unique identifier, e.g. 'aws-cloud', 'github-code'."""

    @abstractmethod
    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        """Return ok() when this scanner can run with the given config."""

    @abstractmethod
    async def scan(self, config: ScanConfig) -> ScanResult:
        """Execute the scan and return all discovered entities."""


def load_scanners() -> dict[str, type[ScannerPlugin]]:
    """Discover scanner plugins registered via entry_points."""
    import importlib.metadata

    scanners: dict[str, type[ScannerPlugin]] = {}
    try:
        eps = importlib.metadata.entry_points(group="sherpa.scanners")
        for ep in eps:
            cls = ep.load()
            instance_type: str = cls().scanner_type if callable(cls) else ep.name
            scanners[instance_type] = cls
    except Exception:
        pass
    return scanners
