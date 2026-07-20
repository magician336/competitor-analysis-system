from __future__ import annotations

import json
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "network: opt-in smoke test that may access public official sources",
    )


@pytest.fixture(autouse=True)
def disable_live_agent_llm(monkeypatch) -> None:
    """Unit tests never inherit a developer's real model key or LLM mode."""

    monkeypatch.setenv("CODERADAR_AGENT_MODE", "rules")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def fixture_text():
    def _read(relative_path: str) -> str:
        return (FIXTURES_DIR / relative_path).read_text(encoding="utf-8")

    return _read


@pytest.fixture
def fixture_json():
    def _read(relative_path: str):
        return json.loads((FIXTURES_DIR / relative_path).read_text(encoding="utf-8"))

    return _read
