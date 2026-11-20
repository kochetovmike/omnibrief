"""Global Critical: fundamental technology AND (exploited OR CVSS >= 9). Conservative on purpose."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest

from omnibrief.config import FilterConfig
from omnibrief.filters import Filter
from omnibrief.global_critical import GLOBAL_CRITICAL, is_global_critical
from omnibrief.models import DigestItem, Package, Vuln
from tests.conftest import NOW
from tests.test_pipeline import FakeSource, go

ATOM = "{http://www.w3.org/2005/Atom}"


def v(**kw) -> Vuln:
    kw.setdefault("id", "CVE-2026-0001")
    return Vuln(**kw)


@pytest.mark.parametrize("record", [
    v(products=["f5 nginx"], cvss=9.8, severity="critical"),                                   # critical nginx
    v(products=["apache log4j"], title="Log4Shell", cvss=10.0),                                # Log4Shell
    v(packages=[Package(ecosystem="maven", name="org.apache.logging.log4j:log4j-core")], cvss=10.0),
    v(packages=[Package(ecosystem="maven", name="org.springframework:spring-beans")], cvss=9.8),  # Spring4Shell
    v(products=["openbsd openssh"], cvss=8.1, kev=True),                                       # exploited OpenSSH
    v(title="Google Chromium V8 Type Confusion Vulnerability", kev=True, cvss=8.8),            # exploited Chrome 0-day
    v(products=["Fortinet FortiOS"], kev=True),
    v(description="A flaw in the Linux kernel netfilter allows root. More text.", cvss=9.1),   # no product named
])
def test_qualifies(record):
    assert is_global_critical(record)


@pytest.mark.parametrize("record", [
    v(products=["f5 nginx"], cvss=7.5, severity="high"),                                     # not severe enough
    v(products=["buddyboss addons"], title="Auth bypass in BuddyBoss plugin for WordPress", cvss=9.8),
    v(packages=[Package(ecosystem="npm", name="fast-xml-parser")], title="fast-xml-parser ReDoS", cvss=9.1,
      description="fast-xml-parser is a Node.js library. It is vulnerable."),                # mentions Node.js only
    v(products=["acme widget"], cvss=9.8, description="Widget, which runs behind nginx, has an RCE."),
    v(products=["tp-link router"], cvss=9.8, title="Command injection in httpd"),             # embedded httpd
    v(products=["synology dsm"], kev=True),                                                  # exploited but niche
])
def test_does_not_qualify(record):
    assert not is_global_critical(record)


def test_always_included_even_without_watchlist():
    flt = Filter(FilterConfig(watchlist_env="WL"), {"WL": ""})
    assert flt.reason(v(products=["openssl openssl"], cvss=9.8)) == "global_critical"


def test_item_level_matching_uses_core_text_with_fallback():
    assert GLOBAL_CRITICAL.matches_item(DigestItem(id="CVE-1", cvss=9.8, core_text="f5 nginx"))
    assert GLOBAL_CRITICAL.matches_item(DigestItem(id="CVE-2", kev=True, product="Microsoft Exchange Server"))
    assert not GLOBAL_CRITICAL.matches_item(DigestItem(id="CVE-3", cvss=9.8, core_text="buddyboss addons",
                                                       match_text="... running on nginx ..."))


def test_global_critical_feed_and_page(cfg, tmp_path):
    env = {"VULNS_WATCHLIST": ""}  # works with no watchlist at all
    nginx = v(id="CVE-2026-0300", products=["f5 nginx"], title="nginx resolver RCE", cvss=9.8, severity="critical",
              published=NOW - timedelta(hours=1), refs=["https://nvd.nist.gov/vuln/detail/CVE-2026-0300"],
              sources=["nvd"])
    plugin = v(id="CVE-2026-0301", products=["acme wp plugin"], title="WordPress plugin RCE", cvss=9.8,
               severity="critical", published=NOW - timedelta(hours=1), sources=["nvd"],
               refs=["https://nvd.nist.gov/vuln/detail/CVE-2026-0301"])
    run, _, opts = go(cfg, tmp_path, [FakeSource("nvd", "nvd", [nginx, plugin])], env)
    assert run.counts["included"] == 1  # nginx included via Global Critical; the plugin needs the watchlist

    feed = ET.parse(opts.site_dir / "feeds" / "vulns-global-critical.atom").getroot()
    entries = feed.findall(f"{ATOM}entry")
    assert [e.findtext(f"{ATOM}id") for e in entries] == ["urn:omnibrief:vulns:alert:20261120T151700Z:CVE-2026-0300"]
    assert feed.findtext(f"{ATOM}title").endswith("· Global Critical")
    page = (opts.site_dir / "cat-global-critical.html").read_text(encoding="utf-8")
    assert "CVE-2026-0300" in page and 'value="cat-global-critical.html" selected' in page


def test_watchlist_cannot_shadow_builtin_slug():
    from omnibrief.categories import parse_categories

    assert parse_categories("Global Critical, microsoft")[0].slug == "microsoft"
