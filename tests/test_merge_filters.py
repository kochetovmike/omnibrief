from __future__ import annotations

from datetime import timedelta

from omnibrief.config import FilterConfig
from omnibrief.filters import Filter, dedupe, remember, sort_key
from omnibrief.merge import merge
from omnibrief.models import Package, Vuln
from tests.conftest import NOW


def nvd(id_, cvss=7.5, sev="high", desc="desc from nvd", products=("acme widget",)):
    return Vuln(id=id_, cvss=cvss, severity=sev, description=desc, products=list(products),
                refs=[f"https://nvd.nist.gov/vuln/detail/{id_}"], sources=["nvd"])


def kev(id_):
    return Vuln(id=id_, title="KEV title", description="kev desc", kev=True, kev_date_added="2026-11-19",
                products=["Acme Widget"], sources=["cisa_kev"])


def ghsa(ghsa_id, cve=None, cvss=9.0, patched="1.2.3"):
    return Vuln(id=cve or ghsa_id, aliases=[ghsa_id] if cve else [], title="GHSA summary",
                description="ghsa desc", cvss=cvss, severity="critical",
                packages=[Package(ecosystem="pip", name="acme", vulnerable_range="< 1.2.3", patched=patched)],
                refs=[f"https://github.com/advisories/{ghsa_id}"], sources=["ghsa"])


# --- merge ------------------------------------------------------------------------------------

def test_merge_precedence_across_sources():
    [m] = merge([ghsa("GHSA-aaaa", cve="CVE-2026-1"), nvd("CVE-2026-1"), kev("CVE-2026-1")])
    assert m.id == "CVE-2026-1" and m.aliases == ["GHSA-aaaa"]
    assert m.cvss == 7.5  # NVD beats GHSA for scores
    assert m.description == "desc from nvd"
    assert m.title == "KEV title"  # KEV vulnerabilityName beats GHSA summary
    assert m.kev and m.kev_date_added == "2026-11-19"
    assert m.packages[0].patched == "1.2.3"  # GHSA owns packages
    assert m.sources == ["nvd", "cisa_kev", "ghsa"]
    assert len(m.refs) == 2


def test_merge_ghsa_only_keyed_by_ghsa_and_falls_back_to_ghsa_score():
    [m] = merge([ghsa("GHSA-bbbb")])
    assert m.id == "GHSA-bbbb" and m.cvss == 9.0


def test_merge_keeps_distinct_vulns_apart():
    assert len(merge([nvd("CVE-2026-1"), nvd("CVE-2026-2")])) == 2


# --- filter -----------------------------------------------------------------------------------

def make_filter(watchlist="widget, Fortinet", **kw):
    return Filter(FilterConfig(watchlist_env="WL", **kw), {"WL": watchlist})


def test_kev_always_included():
    assert make_filter("").reason(Vuln(id="CVE-1", kev=True)) == "kev"


def test_watchlist_needs_cvss_and_word_boundary_match():
    f = make_filter()
    assert f.reason(nvd("CVE-1", cvss=8.0, products=["acme widget"])) == "watchlist"
    assert f.reason(nvd("CVE-1", cvss=6.9, products=["acme widget"])) is None  # below min_cvss
    assert f.reason(nvd("CVE-1", cvss=9.0, products=["acme widgets"], desc="")) is None  # no word match
    assert f.reason(nvd("CVE-1", cvss=8.0, products=[], desc="A flaw in FORTINET FortiWeb")) == "watchlist"


def test_empty_watchlist_matches_nothing():
    assert make_filter("").reason(nvd("CVE-1", cvss=10.0)) is None


def test_epss_override():
    v = nvd("CVE-1", cvss=3.0, products=["unrelated"], desc="")
    v.epss_percentile = 0.995
    assert make_filter().reason(v) == "epss"


def test_sort_kev_then_severity_then_epss():
    a, b, c = nvd("CVE-a", cvss=9.8, sev="critical"), kev("CVE-b"), nvd("CVE-c", cvss=7.5)
    assert [v.id for v in sorted([a, b, c], key=sort_key)] == ["CVE-b", "CVE-a", "CVE-c"]


# --- dedupe / re-emit -------------------------------------------------------------------------

def test_seen_record_is_dropped_unless_material_change():
    seen = {}
    v = nvd("CVE-2026-1")
    assert dedupe([v], seen) == [v] and v.status == "new"
    remember(v, seen, NOW)

    again = nvd("CVE-2026-1")
    assert dedupe([again], seen) == []

    exploited = merge([nvd("CVE-2026-1"), kev("CVE-2026-1")])[0]
    [out] = dedupe([exploited], seen)
    assert out.status == "updated" and "added to CISA KEV" in out.changes[0]


def test_severity_raise_and_fix_release_re_emit():
    seen = {}
    v = ghsa("GHSA-c", cve="CVE-2026-3", patched=None)
    v.severity = "medium"
    remember(v, seen, NOW)
    raised = ghsa("GHSA-c", cve="CVE-2026-3", patched="2.0.0")
    [out] = dedupe([raised], seen)
    assert any("severity raised" in c for c in out.changes)
    assert any("fix released: pip:acme@2.0.0" in c for c in out.changes)


def test_ghsa_entry_migrates_to_cve_key_without_duplicate():
    seen = {}
    remember(ghsa("GHSA-d"), seen, NOW)
    later = ghsa("GHSA-d", cve="CVE-2026-4")
    assert dedupe([later], seen) == []  # same vuln, recognised via alias
    remember(later, seen, NOW + timedelta(hours=3))
    assert list(seen) == ["CVE-2026-4"] and "GHSA-d" in seen["CVE-2026-4"].aliases
