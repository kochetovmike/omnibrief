"""Source adapters against live samples saved in Phase 0 (tests/fixtures/<source>/), offline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from omnibrief.engine.config import SourceSpec
from omnibrief.engine.sources import SOURCE_TYPES, SourceCtx
from omnibrief.engine.sources.swpc import parse_alert
from tests.v2.helpers import mock_sources

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def fetch(stype, params=None, since=LONG_AGO, fail=None):
    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        mock_sources(router, fail=fail)
        adapter = SOURCE_TYPES[stype](SourceSpec(id=stype, type=stype, params=params or {}), SourceCtx(client=client))
        items = adapter.fetch("t", since, NOW)
        return items, [c.request for c in router.calls]  # respx clears calls when the context exits


def test_usgs():
    items, _ = fetch("usgs_fdsn", {"bbox": [34, -11, 72, 40]})
    assert items and all(i.key.startswith("usgs:") and i.url.startswith("https://") for i in items)
    i = items[0]
    assert i.fields["magnitude"] is not None and i.fields["country"] and i.geo is not None
    assert i.title.startswith(f"M{i.fields['magnitude']:.1f} earthquake")


def test_swpc_parses_k_and_g_scale():
    items, _ = fetch("noaa_swpc")
    k5 = next(i for i in items if i.fields["code"] == "ALTK05")
    assert k5.fields["kp"] == 5 and k5.fields["g_scale"] == 1 and k5.severity == "low"
    g2 = parse_alert("t", {"issue_datetime": "2026-11-19 03:00:00.000", "message":
                           "Space Weather Message Code: WARK06\r\nSerial Number: 99\r\n\r\n"
                           "WARNING: Geomagnetic K-index of 6 expected\r\nNOAA Scale: G2 - Moderate"})
    assert g2.fields["g_scale"] == 2 and g2.severity == "medium" and "G2 Moderate" in g2.title
    assert g2.key == "swpc:WARK06:99"


def test_geosphere_groups_by_bundesland():
    items, _ = fetch("geosphere_warn")
    storm = next(i for i in items if i.key == "geosphere:w4147c0v2")
    assert storm.title == "Orange storm warning — Salzburg, Tirol"
    assert storm.fields["level"] == 2 and storm.severity == "medium"


def test_arxiv_never_links_pdfs_and_strips_versions():
    items, _ = fetch("arxiv", {"categories": ["cs.AI"]})
    assert items and all(i.url.startswith("https://arxiv.org/abs/") and "pdf" not in i.url for i in items)
    assert all("v" not in i.key.split(":", 1)[1] for i in items)
    assert all(i.summary for i in items)


def test_arxiv_window_filters_old_entries():
    items, _ = fetch("arxiv", {"categories": ["cs.AI"]}, since=NOW + timedelta(days=1))
    assert items == []


def test_ted_query_and_parse():
    items, requests = fetch("ted", {"countries": ["AUT", "DEU"], "cpv": [72000000]}, since=NOW - timedelta(days=2))
    body = requests[0].content.decode()
    assert "buyer-country IN (AUT DEU)" in body and "classification-cpv IN (72000000)" in body
    assert "publication-date>=20261118" in body
    assert items and items[0].key.startswith("TED:") and items[0].url.startswith("https://ted.europa.eu/")
    assert items[0].fields["buyer"] and items[0].fields["country"]


def test_safety_gate_reports_in_window():
    items, requests = fetch("safety_gate", {"max_reports": 1}, since=datetime(2026, 11, 19, tzinfo=UTC))
    assert len(items) == 6 and all(i.key.startswith("SG:") for i in items)
    assert any(i.fields["notifying_country"] == "Austria" for i in items)
    assert sum(1 for r in requests if "detail" in str(r.url)) == 1


@pytest.mark.parametrize("stype", list(SOURCE_TYPES))
def test_source_outage_raises(stype):
    name = {"usgs_fdsn": "usgs", "noaa_swpc": "swpc", "geosphere_warn": "geosphere"}.get(stype, stype)
    with pytest.raises(httpx.HTTPStatusError):
        fetch(stype, fail={name})
