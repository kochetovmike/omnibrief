"""Shared helpers for v2 engine tests: temp instances and mocked source APIs (offline)."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from omnibrief.engine.sources.arxiv import URL as ARXIV
from omnibrief.engine.sources.geosphere import URL as GEOSPHERE
from omnibrief.engine.sources.safety_gate import LIST_URL as SG_LIST
from omnibrief.engine.sources.swpc import URL as SWPC
from omnibrief.engine.sources.ted import URL as TED
from omnibrief.engine.sources.usgs import URL as USGS

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def fixture_bytes(*parts: str) -> bytes:
    return FIX.joinpath(*parts).read_bytes()


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
