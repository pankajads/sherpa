"""Misbehaving plugins for loader tests; referenced only through injected entry points."""

from __future__ import annotations

from sherpa.core.interfaces import ScannerPlane, ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import ScanConfig


class NotAPlugin:
    pass


class _Base(ScannerPlugin):
    @property
    def plane(self) -> ScannerPlane:
        return ScannerPlane.CODE

    def applies_to(self, config: ScanConfig) -> bool:
        return True

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        return ValidationResult.ok()

    async def scan(self, config: ScanConfig) -> ScanResult:
        return ScanResult(scanner_type=self.scanner_type)


class BadTypeScanner(_Base):
    @property
    def scanner_type(self) -> str:
        return "Bad Type!"


class DuplicateAwsScanner(_Base):
    @property
    def scanner_type(self) -> str:
        return "aws-cloud"


class CrashingScanner(_Base):
    @property
    def scanner_type(self) -> str:
        return "crashing-code"

    async def scan(self, config: ScanConfig) -> ScanResult:
        raise RuntimeError("simulated scanner bug")
