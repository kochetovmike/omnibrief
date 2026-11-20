from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx

from omnibrief.config import SourceConfig
from omnibrief.sources import SourceContext, Window
from omnibrief.sources.cisa_kev import CisaKevSource
from omnibrief.sources.epss import EpssSource
from omnibrief.sources.ghsa import GhsaSource, parse_advisory
from omnibrief.sources.nvd import NvdSource, parse_cve
from tests.conftest import NOW, fixture_json

KEV_URL = "https://kev.test/kev.json"
NVD_URL = "https://nvd.test/cves"
GHSA_URL = "https://api.github.test/advisories"
EPSS_URL = "https://epss.test/epss"
WINDOW = Window(start=NOW - timedelta(hours=6), end=NOW)


@pytest.fixture
def ctx():
    sleeps: list[float] = []
    with httpx.Client() as client:
        c = SourceContext(client=client, env={}, sleep=sleeps.append)
        c.sleeps = sleeps  # type: ignore[attr-defined]
        yield c


# --- CISA KEV ---------------------------------------------------------------------------------

@respx.mock
def test_kev_discovers_recent_entries_only(ctx):
    data = fixture_json("cisa_kev", "catalog.json")
    respx.get(KEV_URL).mock(return_value=httpx.Response(200, json=data))
    src = CisaKevSource(SourceConfig(id="kev", type="cisa_kev", url=KEV_URL, lookback_days=2), ctx)
    found = src.discover(Window(start=NOW, end=NOW.replace(day=19)))
    ids = {v.id for v in found}
    assert "CVE-2024-10441" in ids  # added 2026-11-18
    assert "CVE-2021-44228" not in ids  # old entry
    v = next(v for v in found if v.id == "CVE-2024-10441")
    assert v.kev and v.kev_date_added == "2026-11-18" and v.sources == ["cisa_kev"]
    assert "https://www.synology.com/zh-tw/security/advisory/Synology_SA_24_20" in v.refs
    assert all(r.startswith("https://") for r in v.refs)


@respx.mock
def test_kev_enrich_flags_old_entries(ctx):
    from omnibrief.models import Vuln

    respx.get(KEV_URL).mock(return_value=httpx.Response(200, json=fixture_json("cisa_kev", "catalog.json")))
    src = CisaKevSource(SourceConfig(id="kev", type="cisa_kev", url=KEV_URL), ctx)
    vulns = [Vuln(id="CVE-2021-44228", sources=["nvd"]), Vuln(id="CVE-2099-0001", sources=["nvd"])]
    assert src.enrich(vulns) == 1
    assert vulns[0].kev and "cisa_kev" in vulns[0].sources
    assert not vulns[1].kev


@respx.mock
def test_kev_malformed_catalog_raises(ctx):
    respx.get(KEV_URL).mock(return_value=httpx.Response(200, json={"oops": True}))
    src = CisaKevSource(SourceConfig(id="kev", type="cisa_kev", url=KEV_URL), ctx)
    with pytest.raises(ValueError):
        src.discover(WINDOW)


# --- NVD --------------------------------------------------------------------------------------

def test_nvd_parse_real_record():
    page = fixture_json("nvd", "page.json")
    v = parse_cve(page["vulnerabilities"][0]["cve"])
    assert v.id == "CVE-2026-106024"
    assert v.cvss == 6.5 and v.severity == "medium"  # v3.1 Primary preferred over v4.0 Secondary
    assert "cesanta mongoose" in v.products
    assert v.refs[-1] == "https://nvd.nist.gov/vuln/detail/CVE-2026-106024"


def test_nvd_prefers_primary_over_secondary():
    cve = {
        "id": "CVE-2026-0001",
        "metrics": {"cvssMetricV31": [
            {"type": "Secondary", "cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}},
            {"type": "Primary", "cvssData": {"baseScore": 7.5, "baseSeverity": "HIGH"}},
        ]},
    }
    assert parse_cve(cve).cvss == 7.5


def test_nvd_skips_rejected():
    assert parse_cve({"id": "CVE-2026-0002", "vulnStatus": "Rejected"}) is None


@respx.mock
def test_nvd_paginates_and_sleeps_between_pages(ctx):
    rec = fixture_json("nvd", "page.json")["vulnerabilities"]
    route = respx.get(NVD_URL).mock(side_effect=[
        httpx.Response(200, json={"totalResults": 4, "vulnerabilities": rec[:2]}),
        httpx.Response(200, json={"totalResults": 4, "vulnerabilities": rec[2:4]}),
    ])
    src = NvdSource(SourceConfig(id="nvd", type="nvd", url=NVD_URL, api_key_env="NVD_API_KEY"), ctx)
    found = src.discover(WINDOW)
    assert len(found) == 4 and route.call_count == 2
    assert route.calls[1].request.url.params["startIndex"] == "2"
    assert route.calls[0].request.url.params["pubStartDate"] == "2026-11-20T09:17:00.000Z"
    assert ctx.sleeps == [6.5]  # no key -> stay under 5 requests / 30 s
    assert "apiKey" not in route.calls[0].request.headers


@respx.mock
def test_nvd_empty_window(ctx):
    respx.get(NVD_URL).mock(return_value=httpx.Response(200, json={"totalResults": 0, "vulnerabilities": []}))
    src = NvdSource(SourceConfig(id="nvd", type="nvd", url=NVD_URL), ctx)
    assert src.discover(WINDOW) == []


@respx.mock
def test_nvd_rate_limited_raises(ctx):
    respx.get(NVD_URL).mock(return_value=httpx.Response(403))
    src = NvdSource(SourceConfig(id="nvd", type="nvd", url=NVD_URL), ctx)
    with pytest.raises(httpx.HTTPStatusError):
        src.discover(WINDOW)


@respx.mock
def test_nvd_api_key_sent_as_header(ctx):
    ctx.env = {"NVD_API_KEY": "secret-key"}
    route = respx.get(NVD_URL).mock(return_value=httpx.Response(200, json={"totalResults": 0}))
    NvdSource(SourceConfig(id="nvd", type="nvd", url=NVD_URL, api_key_env="NVD_API_KEY"), ctx).discover(WINDOW)
    req = route.calls[0].request
    assert req.headers["apiKey"] == "secret-key" and "secret-key" not in str(req.url)


# --- GHSA -------------------------------------------------------------------------------------

def test_ghsa_parse_real_advisory():
    adv = fixture_json("ghsa", "page.json")[0]
    v = parse_advisory(adv)
    assert v.id == "CVE-2026-35376" and v.aliases == ["GHSA-9rv8-9hvg-92mq"]
    assert v.cvss == 4.5 and v.severity == "medium"
    assert v.packages[0].name == "uu_chcon" and v.packages[0].patched == "0.10.0"
    assert "https://github.com/advisories/GHSA-9rv8-9hvg-92mq" in v.refs


def test_ghsa_without_cve_keyed_by_ghsa_and_withdrawn_skipped():
    adv = dict(fixture_json("ghsa", "page.json")[0], cve_id=None)
    assert parse_advisory(adv).id == "GHSA-9rv8-9hvg-92mq"
    assert parse_advisory(dict(adv, withdrawn_at="2026-11-20T00:00:00Z")) is None


@respx.mock
def test_ghsa_follows_link_cursor_and_uses_token(ctx):
    ctx.env = {"GITHUB_TOKEN": "ghs_test"}
    page = fixture_json("ghsa", "page.json")
    next_url = f"{GHSA_URL}?after=abc"
    respx.get(next_url).mock(return_value=httpx.Response(200, json=page[2:]))
    first = respx.get(GHSA_URL, params={"type": "reviewed"}).mock(
        return_value=httpx.Response(200, json=page[:2], headers={"Link": f'<{next_url}>; rel="next"'})
    )
    src = GhsaSource(SourceConfig(id="ghsa", type="ghsa", url=GHSA_URL, advisory_type="reviewed"), ctx)
    found = src.discover(WINDOW)
    assert len(found) == len(page)
    req = first.calls[0].request
    assert req.headers["Authorization"] == "Bearer ghs_test"
    assert req.url.params["updated"] == ">2026-11-20T09:17:00Z"


# --- EPSS -------------------------------------------------------------------------------------

@respx.mock
def test_epss_batches_and_enriches(ctx):
    from omnibrief.models import Vuln

    rows = fixture_json("epss", "batch.json")["data"]

    def answer(request):
        wanted = request.url.params["cve"].split(",")
        return httpx.Response(200, json={"data": [r for r in rows if r["cve"] in wanted]})

    route = respx.get(EPSS_URL).mock(side_effect=answer)
    vulns = [Vuln(id="CVE-2024-3400"), Vuln(id="CVE-2021-44228"), Vuln(id="CVE-2023-4863"),
             Vuln(id="GHSA-xxxx-xxxx-xxxx")]
    src = EpssSource(SourceConfig(id="epss", type="epss", url=EPSS_URL, batch_size=2), ctx)
    assert src.enrich(vulns) == 3
    assert route.call_count == 2  # 3 CVEs in batches of 2; GHSA-only ids are skipped
    assert vulns[0].epss_percentile == 1.0 and "epss" in vulns[0].sources
    assert vulns[3].epss is None


def test_nvd_links_ghsa_alias_from_references():
    v = parse_cve({"id": "CVE-2026-0003", "references": [
        {"url": "https://github.com/cesanta/mongoose/security/advisories/GHSA-8r69-8vm6-fv67"}]})
    assert v.aliases == ["GHSA-8r69-8vm6-fv67"]


@respx.mock
def test_nvd_retries_rate_limit_then_succeeds(ctx):
    route = respx.get(NVD_URL).mock(side_effect=[httpx.Response(403), httpx.Response(200, json={"totalResults": 0})])
    assert NvdSource(SourceConfig(id="nvd", type="nvd", url=NVD_URL), ctx).discover(WINDOW) == []
    assert route.call_count == 2 and ctx.sleeps == [30.0]
