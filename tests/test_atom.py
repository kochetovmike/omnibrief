"""The alert feed: one entry per critical/exploited vulnerability, a 12-hour roundup for the rest."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

from omnibrief.models import Digest, DigestItem
from omnibrief.publishers.atom import AtomPublisher, rollup_window
from omnibrief.publishers.base import PublisherContext

ATOM = "{http://www.w3.org/2005/Atom}"
TZ = "Europe/Vienna"


def item(id_, severity, cvss=None, kev=False, **kw):
    return DigestItem(id=id_, severity=severity, cvss=cvss, kev=kev, title=f"title of {id_}",
                      links=[f"https://nvd.nist.gov/vuln/detail/{id_}"], **kw)


def digest(at: datetime, *items, html="", summarised=False):
    run_id = at.strftime("%Y%m%dT%H%M%SZ")
    return Digest(topic="vulns", run_id=run_id, created_utc=at, title="t", summarised=summarised,
                  markdown="", html=html, items=list(items))


def publish(cfg, tmp_path, digests, now):
    ctx = PublisherContext(cfg=cfg, topic=cfg.topic("vulns"), site_dir=tmp_path, env={}, now=now)
    AtomPublisher("atom", cfg.publishers["atom"], ctx).publish(sorted(digests, key=lambda d: d.created_utc,
                                                                      reverse=True))
    return ET.parse(tmp_path / "feeds" / "vulns.atom").getroot()


def entries(feed):
    return [(e.findtext(f"{ATOM}id"), e.findtext(f"{ATOM}title"), e) for e in feed.findall(f"{ATOM}entry")]


def test_rollup_windows_are_08_and_20_vienna_time_dst_safe():
    winter = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)  # 11:00 CET
    assert rollup_window(winter, TZ) == (datetime(2026, 11, 20, 7, tzinfo=UTC), datetime(2026, 11, 20, 19, tzinfo=UTC))
    night = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)  # 04:00 CET -> window started 20:00 the day before
    assert rollup_window(night, TZ) == (datetime(2026, 11, 19, 19, tzinfo=UTC), datetime(2026, 11, 20, 7, tzinfo=UTC))
    summer = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)  # CEST
    assert rollup_window(summer, TZ)[0] == datetime(2026, 7, 1, 6, tzinfo=UTC)  # 08:00 CEST


def test_one_alert_per_critical_or_exploited_vulnerability(cfg, tmp_path):
    at = datetime(2026, 11, 20, 9, 17, tzinfo=UTC)
    d = digest(at, item("CVE-1", "critical", 9.8), item("CVE-2", "high", 8.1, kev=True,
                                                       kev_date_added="2026-11-19"), item("CVE-3", "high", 7.5))
    feed = publish(cfg, tmp_path, [d], now=at)
    got = entries(feed)
    assert len(got) == 2  # CVE-3 waits for the roundup; its window hasn't closed
    titles = [t for _, t, _ in got]
    assert any(t.startswith("🚨 Exploited · CVE-2") for t in titles)
    assert any(t.startswith("🔴 Critical · CVE-1") for t in titles)
    content = next(e for i, _, e in got if "CVE-2" in i).findtext(f"{ATOM}content")
    assert "Actively exploited" in content and "since 2026-11-19" in content and "about.html" in content


def test_moderate_items_batched_into_one_roundup_after_window_closes(cfg, tmp_path):
    a = digest(datetime(2026, 11, 20, 7, 17, tzinfo=UTC), item("CVE-10", "high", 8.0))
    b = digest(datetime(2026, 11, 20, 9, 17, tzinfo=UTC), item("CVE-11", "medium", 5.4), item("CVE-12", "high", 7.2))
    c = digest(datetime(2026, 11, 20, 19, 17, tzinfo=UTC), item("CVE-13", "low", 3.1))  # next window
    feed = publish(cfg, tmp_path, [a, b, c], now=datetime(2026, 11, 20, 19, 17, tzinfo=UTC))
    got = entries(feed)
    assert len(got) == 1
    entry_id, title, entry = got[0]
    assert entry_id == "urn:omnibrief:vulns:roundup:20261120T0700Z"
    assert title.startswith("🟠 Roundup · 20 Nov, 08:00–20:00") and "2 high" in title and "1 medium" in title
    html = entry.findtext(f"{ATOM}content")
    assert html.index("CVE-10") < html.index("CVE-11")  # highest severity first
    assert "CVE-13" not in html
    assert entry.findtext(f"{ATOM}published") == "2026-11-20T19:00:00Z"


def test_feed_is_rebuilt_without_old_or_stale_entries(cfg, tmp_path):
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "vulns.atom").write_text("<feed><entry><id>old-digest-entry</id></entry></feed>")
    now = datetime(2026, 11, 20, 9, 17, tzinfo=UTC)
    old = digest(now - timedelta(days=20), item("CVE-OLD", "critical", 9.9))
    new = digest(now, item("CVE-NEW", "critical", 9.9))
    feed = publish(cfg, tmp_path, [old, new], now=now)
    ids = [i for i, _, _ in entries(feed)]
    assert ids == ["urn:omnibrief:vulns:alert:20261120T091700Z:CVE-NEW"]


def test_alert_uses_llm_bullet_when_summarised(cfg, tmp_path):
    at = datetime(2026, 11, 20, 9, 17, tzinfo=UTC)
    html = "<ul><li><strong>CVE-1</strong> lets attackers run code; update to 2.1.</li><li>CVE-9 other</li></ul>"
    feed = publish(cfg, tmp_path, [digest(at, item("CVE-1", "critical", 9.8), html=html, summarised=True)], now=at)
    content = entries(feed)[0][2].findtext(f"{ATOM}content")
    assert "update to 2.1" in content and "CVE-9" not in content


def test_untrusted_text_escaped_in_alerts_and_roundups(cfg, tmp_path):
    at = datetime(2026, 11, 20, 6, 17, tzinfo=UTC)
    evil = '<script>alert(1)</script>'
    d = digest(at, item("CVE-1", "critical", 9.8, product=evil), item("CVE-2", "high", 8.0))
    d.items[0].title = evil
    d.items[1].title = evil
    d.items[1].links = ["javascript:alert(2)"]
    feed = publish(cfg, tmp_path, [d], now=at + timedelta(hours=12))
    for _, _title, e in entries(feed):
        content = e.findtext(f"{ATOM}content")
        assert "<script>" not in content and "javascript:" not in content
