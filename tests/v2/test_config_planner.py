"""Instance config, overlay, validation (design-v2 §3–4, §12) and the planner (§5.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnibrief.engine.config import ConfigError, load_engine
from omnibrief.engine.planner import is_due
from tests.v2.helpers import engine_for

ROOT = Path(__file__).resolve().parents[2]


def test_example_instance_validates():
    engine = load_engine(ROOT / "instance.example")
    assert [s.id for s in engine.enabled_topics()] == ["vulns", "tenders", "arxiv_ai", "safety_gate",
                                                       "space_weather", "earthquakes", "weather_at"]
    assert [cid for cid, _ in engine.channels_for("earthquakes", "telegram")] == ["tg_alerts"]


def test_engine_repo_has_no_instance_dir():
    assert not (ROOT / "instance").exists(), "the engine repo must never contain instance/ (design-v2 §3.2)"


def test_rejects_non_allowlisted_source_in_instance_topic(tmp_path):
    with pytest.raises(ConfigError, match="not on the licence allowlist"):
        engine_for(tmp_path, topics={"news": {"publish_to": ["feed"]}}, files={"topics/news.yaml": """
id: news
title: News
kind: digest
sources: [{ id: n, type: derstandard_rss }]
"""})


def test_rejects_credential_in_yaml(tmp_path):
    with pytest.raises(ConfigError, match="credential-like value"):
        engine_for(tmp_path, channels={"feed": {"type": "feed"},
                                       "tg": {"type": "telegram", "bot_token_env": "123456789:AAH" + "x" * 33}},
                   topics={"earthquakes": {"publish_to": ["feed", "tg"]}})


def test_rejects_unknown_channel_planned_type_and_bad_schema(tmp_path):
    with pytest.raises(ConfigError) as e:
        engine_for(tmp_path, topics={"earthquakes": {"publish_to": ["feed", "nowhere"]}},
                   channels={"feed": {"type": "feed"}, "bsky": {"type": "bluesky"}},
                   extra={"schema_version": 1})
    msg = str(e.value)
    assert "undefined channel 'nowhere'" in msg and "'bluesky' is planned" in msg and "schema_version must be 2" in msg


def test_instance_topic_override_is_deep_merged(tmp_path):
    engine = engine_for(tmp_path, topics={"earthquakes": {"publish_to": ["feed"]}},
                        files={"topics/earthquakes.yaml": "filters: { min_fields: { magnitude: 5.5 } }\n"})
    spec = engine.topics["earthquakes"]
    assert spec.filters["min_fields"] == {"magnitude": 5.5}
    assert spec.sources[0].type == "usgs_fdsn"  # untouched keys come from the catalogue


def test_prompt_and_notice_overlay(tmp_path):
    engine = engine_for(tmp_path, files={"prompts/tenders.md": "custom {records}"})
    assert engine.prompt_path("tenders").read_text() == "custom {records}"
    assert "catalog" in str(engine.prompt_path("arxiv_ai"))


def test_missing_notice_attribution_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match=r"NOTICE\.md is missing attribution for usgs_fdsn"):
        engine_for(tmp_path, topics={"earthquakes": {"publish_to": ["feed"]}}, files={"NOTICE.md": "# empty\n"})


# --- planner ---------------------------------------------------------------------------------------

def spec(tmp_path, tid):
    return engine_for(tmp_path).topics[tid]


def at(day, hh, mm=0):  # UTC
    return datetime(2026, 11, day, hh, mm, tzinfo=UTC)


def test_hourly_topic_due_once_per_clock_hour_despite_late_cron(tmp_path):
    s = spec(tmp_path, "earthquakes")
    assert is_due(s, None, at(20, 10), "Europe/Vienna")[0]
    # GitHub started the 10:00 run at 10:22 and the 11:00 run at 11:03: still due (review finding #4)
    assert is_due(s, {"last_success": at(20, 10, 22).isoformat()}, at(20, 11, 3), "Europe/Vienna")[0]
    assert not is_due(s, {"last_success": at(20, 10, 3).isoformat()}, at(20, 10, 55), "Europe/Vienna")[0]


def test_tenders_daily_after_0930_on_business_days(tmp_path):
    s = spec(tmp_path, "tenders")  # Fri 20 Nov 2026
    assert not is_due(s, None, at(20, 8, 0), "Europe/Vienna")[0]  # 09:00 CET: too early
    assert is_due(s, None, at(20, 8, 31), "Europe/Vienna")[0]  # 09:31 CET
    done = {"last_success": at(20, 8, 31).isoformat()}
    assert not is_due(s, done, at(20, 15), "Europe/Vienna")[0]  # once per day
    assert not is_due(s, done, at(21, 9), "Europe/Vienna")[0]  # Saturday
    assert is_due(s, done, at(23, 9), "Europe/Vienna")[0]  # Monday 10:00 CET
    assert is_due(s, done, at(21, 9), "Europe/Vienna", forced=True)[0]
