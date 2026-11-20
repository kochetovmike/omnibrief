"""Shared helpers for v2 engine tests: temp instances and mocked source APIs (offline)."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx
import yaml

from omnibrief.engine.config import load_engine
from omnibrief.engine.sources.arxiv import URL as ARXIV
from omnibrief.engine.sources.geosphere import URL as GEOSPHERE
from omnibrief.engine.sources.safety_gate import LIST_URL as SG_LIST
from omnibrief.engine.sources.swpc import URL as SWPC
from omnibrief.engine.sources.ted import URL as TED
from omnibrief.engine.sources.usgs import URL as USGS

FIX = Path(__file__).resolve().parent.parent / "fixtures"
GENERIC = ["tenders", "arxiv_ai", "safety_gate", "space_weather", "earthquakes", "weather_at"]


def fixture_bytes(*parts: str) -> bytes:
    return FIX.joinpath(*parts).read_bytes()


def make_instance(tmp: Path, topics: dict | None = None, channels: dict | None = None, llm: dict | None = None,
                  extra: dict | None = None, files: dict[str, str] | None = None) -> Path:
    inst = tmp / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    cfg = {
        "schema_version": 2,
        "site": {"base_url": "https://example.github.io/omnibrief-live/", "timezone": "Europe/Vienna"},
        "llm": llm or {"model": "m", "max_calls_per_run": 6, "max_calls_per_day": 40},
        "topics": topics if topics is not None else {t: {"enabled": True, "publish_to": ["feed"]} for t in GENERIC},
        "channels": channels or {"feed": {"type": "feed"}},
        "storage": {"sinks": [{"type": "artifact"}, {"type": "jsonl_git", "branch": "archive"}]},
        **(extra or {}),
    }
    (inst / "omnibrief.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    for rel, text in (files or {}).items():
        path = inst / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return inst


def engine_for(tmp: Path, **kw):
    return load_engine(make_instance(tmp, **kw))


def mock_sources(router: respx.Router, *, usgs="usgs/events.json", geosphere="geosphere/warnstatus.json",
                 fail: set[str] | None = None) -> None:
    """Route every generic source API to its saved fixture (or a 503 for names in `fail`)."""
    fail = fail or set()

    def resp(name: str, content: bytes) -> httpx.Response:
        return httpx.Response(503) if name in fail else httpx.Response(200, content=content)

    router.get(url__startswith=USGS).mock(return_value=resp("usgs", fixture_bytes(usgs)))
    router.get(SWPC).mock(return_value=resp("swpc", fixture_bytes("swpc", "alerts.json")))
    router.get(GEOSPHERE).mock(return_value=resp("geosphere", fixture_bytes(geosphere)))
    router.get(url__startswith=ARXIV).mock(return_value=resp("arxiv", fixture_bytes("arxiv", "query.xml")))
    router.post(TED).mock(return_value=resp("ted", fixture_bytes("ted", "search.json")))
    router.get(SG_LIST).mock(return_value=resp("safety_gate", fixture_bytes("safety_gate", "list.xml")))
    router.get(url__startswith="https://ec.europa.eu/safety-gate-alerts/api/download/weeklyReport/detail/").mock(
        return_value=resp("safety_gate", fixture_bytes("safety_gate", "report.xml")))
