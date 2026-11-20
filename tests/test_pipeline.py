"""End-to-end pipeline tests with fake sources: no network, real state/feed/site output."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path

import pytest

from omnibrief.models import Vuln
from omnibrief.pipeline import RunOptions, render_site, run_topic
from omnibrief.sources.base import Source
from omnibrief.state import Store
from tests.conftest import NOW

ATOM = "{http://www.w3.org/2005/Atom}"


class FakeSource(Source):
    discovers = True

    def __init__(self, sid: str, stype: str, records=None, fail: bool = False, kev_ids=()):
        self._id, self.type, self.records, self.fail, self.kev_ids = sid, stype, records or [], fail, set(kev_ids)
        self.enriches = bool(kev_ids)
        self.cfg = None

    @property
    def id(self):
        return self._id

    def lookback_hours(self, default=6):
        return 6

    def discover(self, window):
        if self.fail:
            raise ConnectionError("simulated outage")
        return [r.model_copy(deep=True) for r in self.records]

    def enrich(self, vulns):
        for v in vulns:
            if v.id in self.kev_ids:
                v.kev = True
        return 0


def nvd_rec(i: int, cvss=9.1, product="acme widget") -> Vuln:
    return Vuln(id=f"CVE-2026-{i:04d}", description=f"Flaw {i} in {product}", products=[product],
                cvss=cvss, severity="critical" if cvss >= 9 else "high",
                refs=[f"https://nvd.nist.gov/vuln/detail/CVE-2026-{i:04d}"], sources=["nvd"])


@pytest.fixture
def env():
    return {"VULNS_WATCHLIST": "widget"}


def go(cfg, tmp_path: Path, sources, env, now=NOW):
    opts = RunOptions(data_dir=tmp_path / "data", site_dir=tmp_path / "site", raw_dir=tmp_path / "raw", env=env)
    opts.site_dir.mkdir(parents=True, exist_ok=True)
    store = Store(opts.data_dir)
    run = run_topic(cfg, cfg.topic("vulns"), store, opts, sources, None, now)
    render_site(cfg, store, opts.site_dir, [run])
    return run, store, opts


def test_two_consecutive_runs_no_duplicates(cfg, tmp_path, env):
    records = [nvd_rec(1), nvd_rec(2), nvd_rec(3, product="other thing")]  # 3rd fails the watchlist
    run1, store, _ = go(cfg, tmp_path, [FakeSource("nvd", "nvd", records)], env)
    assert run1.counts["included"] == 2 and run1.digest_written

    run2, store, opts = go(cfg, tmp_path, [FakeSource("nvd", "nvd", records)], env, NOW + timedelta(hours=3))
    assert run2.counts["included"] == 0 and not run2.digest_written and run2.llm == "skipped"
    assert len(store.recent_digests("vulns", 50)) == 1

    feed = ET.parse(opts.site_dir / "feeds" / "vulns.atom").getroot()
    assert len(feed.findall(f"{ATOM}entry")) == 1


def test_kev_addition_for_seen_cve_re_emits_as_updated(cfg, tmp_path, env):
    go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nvd_rec(1)])], env)
    later = NOW + timedelta(hours=3)
    run, store, _ = go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nvd_rec(1)]),
                                       FakeSource("cisa-kev", "cisa_kev", kev_ids=["CVE-2026-0001"])], env, later)
    assert run.counts["updated"] == 1 and run.counts["included"] == 1
    latest = store.recent_digests("vulns", 1)[0]
    assert latest.items[0].status == "updated" and latest.items[0].kev
    assert "added to CISA KEV" in latest.items[0].changes[0]


def test_nvd_outage_degrades_only_nvd(cfg, tmp_path, env):
    kev = Vuln(id="CVE-2026-0042", kev=True, title="Exploited thing", sources=["cisa_kev"],
               refs=["https://vendor.example/a"])
    run, _, opts = go(cfg, tmp_path, [FakeSource("nvd", "nvd", fail=True),
                                      FakeSource("cisa-kev", "cisa_kev", [kev])], env)
    assert run.sources["nvd"].status == "failed" and run.sources["nvd"].error == "ConnectionError"
    assert run.sources["cisa-kev"].status == "ok"
    assert run.status == "degraded" and run.counts["included"] == 1
    health = json.loads((opts.site_dir / "health.json").read_text())
    assert health["degraded_sources"] == ["nvd"] and health["publishers"]["atom"] == "ok"


def test_nothing_passes_no_digest_but_run_record_written(cfg, tmp_path, env):
    run, _store, opts = go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nvd_rec(1, cvss=5.0)])], env)
    assert not run.digest_written and run.llm == "skipped"
    assert list((tmp_path / "data" / "runs").rglob("*.json"))
    assert (opts.site_dir / "feeds" / "vulns.atom").is_file()


def test_feed_rights_entry_shape_and_site_attribution(cfg, tmp_path, env):
    _, _, opts = go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nvd_rec(1)])], env)
    feed = ET.parse(opts.site_dir / "feeds" / "vulns.atom").getroot()
    assert feed.findtext(f"{ATOM}id") == "urn:omnibrief:vulns"
    assert "not endorsed or certified by the NVD" in feed.findtext(f"{ATOM}rights")
    entry = feed.find(f"{ATOM}entry")
    assert entry.findtext(f"{ATOM}id").startswith("urn:omnibrief:vulns:20261120T151700Z")
    assert entry.find(f"{ATOM}content").get("type") == "html"
    rels = [link.get("rel") for link in entry.findall(f"{ATOM}link")]
    assert "related" in rels and "alternate" in rels
    about = (opts.site_dir / "about.html").read_text(encoding="utf-8")
    assert "creativecommons.org/licenses/by/4.0" in about and "See EPSS at" in about
    index = (opts.site_dir / "index.html").read_text(encoding="utf-8")
    assert 'id="run-20261120T151700Z"' in index and "feeds/vulns.atom" in index


def test_logs_and_run_record_do_not_contain_watchlist(cfg, tmp_path, caplog):
    env = {"VULNS_WATCHLIST": "secretinternalproduct"}
    run, _, _ = go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nvd_rec(1)])], env)
    assert "secretinternalproduct" not in caplog.text
    assert "secretinternalproduct" not in run.model_dump_json()
    assert "watchlist" not in json.dumps(run.counts)


def test_seen_pruned_after_90_days(cfg, tmp_path, env):
    go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nvd_rec(1)])], env)
    _, store, _ = go(cfg, tmp_path, [FakeSource("nvd", "nvd", [])], env, NOW + timedelta(days=91))
    assert store.load_seen("vulns") == {}


def test_outage_widens_next_window_to_last_success(cfg, tmp_path, env):
    from omnibrief.pipeline import source_window

    windows = []

    class Recording(FakeSource):
        def discover(self, window):
            windows.append(window)
            return super().discover(window)

    go(cfg, tmp_path, [Recording("nvd", "nvd")], env)                                   # ok at NOW
    go(cfg, tmp_path, [FakeSource("nvd", "nvd", fail=True)], env, NOW + timedelta(hours=3))  # outage
    go(cfg, tmp_path, [Recording("nvd", "nvd")], env, NOW + timedelta(hours=9))
    assert windows[-1].start == NOW - timedelta(hours=1)  # back to last success minus overlap
    far = source_window(Recording("nvd", "nvd"), NOW + timedelta(days=30), NOW)
    assert far.start == NOW + timedelta(days=23)  # capped catch-up


def test_old_advisory_edit_is_not_news(cfg, tmp_path, env):
    old = nvd_rec(7)
    old.published = NOW - timedelta(days=900)
    old.kev, old.kev_date_added = True, "2021-12-10"
    run, _, _ = go(cfg, tmp_path, [FakeSource("ghsa", "ghsa", [old])], env)
    assert run.counts["included"] == 0


def test_raw_archive_holds_included_records_only(cfg, tmp_path, env):
    go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nvd_rec(1), nvd_rec(2, product="unwatched")])], env)
    [raw] = list((tmp_path / "raw").glob("*.json"))
    assert [r["id"] for r in json.loads(raw.read_text())] == ["CVE-2026-0001"]
