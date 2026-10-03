"""Shared pytest options."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite tests/golden/data/* from current output instead of comparing against it.",
    )


@pytest.fixture(autouse=True)
def _isolated_sherpa_db(tmp_path, monkeypatch):
    """CLI commands default to ./.sherpa/sherpa.db; never let a test write into the repo."""
    monkeypatch.setenv("SHERPA_DB", str(tmp_path / "sherpa-test.db"))


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
