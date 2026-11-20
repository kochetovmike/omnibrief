"""Watchlist categories: parsing, filter inclusion, per-category feeds and dashboard pages."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import timedelta

from omnibrief.categories import item_categories, parse_categories
from omnibrief.config import FilterConfig
from omnibrief.filters import Filter
from omnibrief.models import DigestItem, Package, Vuln
from tests.conftest import NOW
from tests.test_pipeline import FakeSource, go

ATOM = "{http://www.w3.org/2005/Atom}"
WATCHLIST = "general, Microsoft, oracle,  Programming Languages = python | npm | golang , microsoft=windows"


def test_parse_categories_plain_grouped_merged_and_general_ignored():
    cats = parse_categories(WATCHLIST)
    assert [(c.slug, c.label) for c in cats] == [
        ("microsoft", "Microsoft"), ("oracle", "oracle"), ("programming-languages", "Programming Languages")]
    assert cats[0].terms == ["Microsoft", "windows"]  # duplicate label merges its terms
    assert cats[2].terms == ["python", "npm", "golang"]
    assert parse_categories("") == [] and parse_categories(" , =x, a=") == []


def test_categories_match_whole_words_case_insensitively():
    cats = parse_categories(WATCHLIST)
    item = DigestItem(id="CVE-1", match_text="Remote code execution in Microsoft Windows SMB")
    assert item_categories(item, cats) == ["microsoft"]
    pkg = DigestItem(id="GHSA-1", match_text="Prototype pollution\nnpm lodash")
    assert item_categories(pkg, cats) == ["programming-languages"]
    assert item_categories(DigestItem(id="CVE-2", match_text="pythonic microsofty"), cats) == []
    # Old digests without match_text fall back to title + product
    assert item_categories(DigestItem(id="CVE-3", title="Oracle WebLogic flaw"), cats) == ["oracle"]


def test_any_grouped_term_counts_for_inclusion():
    flt = Filter(FilterConfig(watchlist_env="WL"), {"WL": WATCHLIST})
    v = Vuln(id="GHSA-x", cvss=8.0, packages=[Package(ecosystem="pip", name="jose")], title="Python JWT bypass")
    assert flt.reason(v) == "watchlist"


def _run_with_watchlist(cfg, tmp_path):
    env = {"VULNS_WATCHLIST": "microsoft, programming languages=npm|python"}
    ms = Vuln(id="CVE-2026-0101", title="Windows flaw", description="Microsoft Windows RCE", cvss=9.8,
              severity="critical", published=NOW - timedelta(hours=1),
              refs=["https://nvd.nist.gov/vuln/detail/CVE-2026-0101"], sources=["nvd"])
    npm = Vuln(id="GHSA-aaaa-bbbb-cccc", title="Prototype pollution", cvss=7.5, severity="high",
               packages=[Package(ecosystem="npm", name="lodash")], published=NOW - timedelta(hours=1),
               refs=["https://github.com/advisories/GHSA-aaaa-bbbb-cccc"], sources=["ghsa"])
    kev = Vuln(id="CVE-2026-0202", kev=True, kev_date_added="2026-11-19", title="Exploited router bug",
               refs=["https://nvd.nist.gov/vuln/detail/CVE-2026-0202"], sources=["cisa_kev"])
    _, _, opts = go(cfg, tmp_path, [FakeSource("nvd", "nvd", [ms, npm, kev])], env)
    # Close the 12-hour window so roundups exist.
    later = NOW + timedelta(hours=13)
    go(cfg, tmp_path, [FakeSource("nvd", "nvd", [])], env, later)
    return opts


def test_one_feed_per_category_plus_general(cfg, tmp_path):
    opts = _run_with_watchlist(cfg, tmp_path)
    feeds = opts.site_dir / "feeds"
    assert sorted(p.name for p in feeds.glob("*.atom")) == [
        "vulns-global-critical.atom", "vulns-microsoft.atom", "vulns-programming-languages.atom", "vulns.atom"]

    def ids(name):
        root = ET.parse(feeds / name).getroot()
        return root, [e.findtext(f"{ATOM}id") for e in root.findall(f"{ATOM}entry")]

    _, g_ids = ids("vulns.atom")
    ms, ms_ids = ids("vulns-microsoft.atom")
    lang, lang_ids = ids("vulns-programming-languages.atom")
    assert len(g_ids) == 3  # 2 alerts (critical, exploited) + 1 roundup (high npm item)
    assert ms_ids == ["urn:omnibrief:vulns:alert:20261120T151700Z:CVE-2026-0101"]
    assert lang_ids == ["urn:omnibrief:vulns:programming-languages:roundup:20261120T0600Z"]
    assert ms.findtext(f"{ATOM}id") == "urn:omnibrief:vulns:microsoft"
    assert ms.findtext(f"{ATOM}title").endswith("· microsoft")
    assert "Roundup · programming languages" in lang.find(f"{ATOM}entry").findtext(f"{ATOM}title")


def test_dashboard_pages_and_dropdown(cfg, tmp_path):
    opts = _run_with_watchlist(cfg, tmp_path)
    index = (opts.site_dir / "general.html").read_text(encoding="utf-8")  # the full "General" dashboard
    ms = (opts.site_dir / "cat-microsoft.html").read_text(encoding="utf-8")
    assert (opts.site_dir / "cat-programming-languages.html").is_file()
    # dropdown with counts, current view selected, per-view feed link
    assert '<select id="cat"' in index
    assert '<option value="general.html" selected>General — all · 3</option>' in index
    assert '<option value="cat-microsoft.html">microsoft · 1</option>' in index
    assert '<option value="cat-microsoft.html" selected>microsoft · 1</option>' in ms
    assert 'href="feeds/vulns-microsoft.atom">Subscribe' in ms
    # category view only shows its items; digest log stays on the general page
    assert "CVE-2026-0101" in ms and "CVE-2026-0202" not in ms and "GHSA-aaaa-bbbb-cccc" not in ms
    assert 'class="log"' in index and 'class="log"' not in ms
    # feed autodiscovery for every feed
    assert index.count('type="application/atom+xml"') == 4
    # Global Critical is built in and listed right after General
    assert index.index("General — all") < index.index("Global Critical · ") < index.index("microsoft · ")


def test_removed_category_page_and_feed_disappear(cfg, tmp_path):
    opts = _run_with_watchlist(cfg, tmp_path)
    from omnibrief.pipeline import render_site
    from omnibrief.state import Store

    render_site(cfg, Store(tmp_path / "data"), opts.site_dir, env={"VULNS_WATCHLIST": "microsoft"})
    assert not (opts.site_dir / "cat-programming-languages.html").exists()
    assert (opts.site_dir / "cat-microsoft.html").exists()
