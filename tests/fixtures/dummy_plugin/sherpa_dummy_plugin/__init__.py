"""A minimal third-party scanner: one fake repository, only for its own org."""

from __future__ import annotations

from sherpa.core.interfaces import ScannerPlane, ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import Repository, ScanConfig

DUMMY_ORG = "sherpa-dummy-org"


class DummyCodeScanner(ScannerPlugin):
    @property
    def scanner_type(self) -> str:
        return "dummy-code"

    @property
    def plane(self) -> ScannerPlane:
        return ScannerPlane.CODE

    def applies_to(self, config: ScanConfig) -> bool:
        # Only for its own org, so installing it never changes other scans.
        return config.github_org == DUMMY_ORG

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        return ValidationResult.ok()

    async def scan(self, config: ScanConfig) -> ScanResult:
        return ScanResult(
            scanner_type=self.scanner_type,
            repositories=[
                Repository(
                    id=f"dummy.example/{DUMMY_ORG}/plugin-repo",
                    url=f"https://dummy.example/{DUMMY_ORG}/plugin-repo.git",
                )
            ],
        )
