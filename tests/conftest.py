from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from omnibrief.config import load_config
from omnibrief.engine.brief import load_project
from omnibrief.engine.config import CATALOG
from omnibrief.engine.topics.vulns import v1_config_dict

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 20, 15, 17, tzinfo=UTC)
BASE_URL = "https://kochetovmike.github.io/omnibrief/"


def fixture_json(*parts: str):
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


def vulns_v1_dict() -> dict:
    """The v1 config the vulns plugin builds from the catalogue topic (v1 tests run against it)."""
    spec = load_project(ROOT).topics["vulns"]  # compiled from briefs/vulns.yaml
    return v1_config_dict(
        base_url=BASE_URL, timezone="Europe/Vienna", language="en",
        llm={"provider": "gemini", "model": "gemini-flash-lite-latest", "api_key_env": "GEMINI_API_KEY",
             "max_output_tokens": 4000},
        spec=spec, prompt_file="prompts/vulns.md",
        telegram={"bot_token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TELEGRAM_CHAT_ID",
                  "channel_url": "https://t.me/vulnerabilitiesbrief"})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A v1-style config dir (sources.yaml, NOTICE.md, prompt) built from the catalogue, editable by tests."""
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump(vulns_v1_dict(), sort_keys=False), encoding="utf-8")
    shutil.copy(CATALOG / "NOTICE.md", tmp_path / "NOTICE.md")
    (tmp_path / "prompts").mkdir()
    shutil.copy(ROOT / "prompts" / "vulns.md", tmp_path / "prompts" / "vulns.md")
    return tmp_path


@pytest.fixture
def cfg(repo: Path):
    return load_config(repo / "sources.yaml")
