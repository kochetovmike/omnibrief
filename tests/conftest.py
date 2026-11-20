from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnibrief.config import load_config

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 20, 15, 17, tzinfo=UTC)


def fixture_json(*parts: str):
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A copy of the real config, prompt and NOTICE in a temp dir (so tests can edit them)."""
    shutil.copy(ROOT / "sources.yaml", tmp_path / "sources.yaml")
    shutil.copy(ROOT / "NOTICE.md", tmp_path / "NOTICE.md")
    (tmp_path / "prompts").mkdir()
    shutil.copy(ROOT / "prompts" / "vulns.md", tmp_path / "prompts" / "vulns.md")
    return tmp_path


@pytest.fixture
def cfg(repo: Path):
    return load_config(repo / "sources.yaml")
