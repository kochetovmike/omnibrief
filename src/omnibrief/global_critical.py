"""Built-in "Global Critical" category: the few vulnerabilities everyone has to act on.

Think Log4Shell, Spring4Shell, the regreSSHion OpenSSH bug, the xz backdoor, or a critical nginx bug.
A record qualifies only if BOTH hold:

  1. it affects a FUNDAMENTAL technology — something deployed across most of the internet or most
     companies (FUNDAMENTALS below), matched on product/package names and the title (the first
     sentence of the description only if no product is named) — never the full text, so a plugin
     that merely *mentions* nginx doesn't count; and
  2. it is SEVERE — actively exploited (CISA KEV) or critical (CVSS >= 9.0 / severity "critical").

A second, lower tier — GLOBALLY NOTABLE — is a fundamental technology at high severity
(CVSS 7.0–8.9 or severity "high"/"critical") that is not severe enough for the first tier.

The Global Critical view/feed/Telegram channel carries both tiers:
  critical tier  → an instant alert each (it is critical or exploited, so `is_alert` is true);
  notable tier   → the twice-daily "Global brief" (08:00 and 20:00 local, the roundup windows).
Both tiers are always included by the filter (watchlist or not). This drives a high-signal public
channel, so the list is deliberately conservative: when in doubt, leave a technology out. Edit
FUNDAMENTALS to tune it; the tests pin the intended behaviour.
"""

from __future__ import annotations

import re

from omnibrief.categories import Category, compile_terms
from omnibrief.models import DigestItem, Vuln

SLUG = "global-critical"
LABEL = "Global Critical"
MIN_CVSS = 9.0
NOTABLE_MIN_CVSS = 7.0

# Whole-word, case-insensitive. Prefer specific product names over generic words
# ("apache http server", not "apache"; "openjdk", not "java"; no "php"/"python"/"git"/"wordpress",
# which would pull in every plugin or library that uses them).
FUNDAMENTALS: dict[str, list[str]] = {
    "crypto & remote access": [
        "openssl", "openssh", "libssh", "libssh2", "gnutls", "regresshion", "heartbleed",
    ],
    "web servers & proxies": [
        "nginx", "apache http server", "apache httpd", "apache tomcat", "tomcat",
        "microsoft iis", "envoy", "haproxy", "squid",
    ],
    "frameworks & runtimes": [
        "log4j", "log4j2", "log4j-core", "log4shell", "apache struts", "struts", "struts2-core",
        "spring framework", "spring boot", "spring-beans", "spring-webmvc", "spring-core", "spring-web",
        "spring cloud", "spring4shell", "openjdk", "java se", "oracle java", "node.js", "nodejs", "cpython",
        ".net framework", "asp.net core", "jackson-databind",
        "php group", "php php",  # PHP itself (CISA / NVD naming) — not "php", which hits every plugin
    ],
    "operating systems & core libraries": [
        "linux kernel", "glibc", "gnu c library", "systemd", "sudo", "polkit", "bash", "xz utils", "liblzma",
        "zlib", "libwebp", "curl", "libcurl", "microsoft windows", "windows server", "windows kernel",
        "windows smb", "remote desktop", "netlogon", "active directory", "ios", "ipados", "macos",
        "android", "freebsd",
        "apple multiple products",  # CISA's label for iOS/macOS/Safari zero-days
    ],
    "browsers & engines": ["google chrome", "chromium", "mozilla firefox", "firefox", "webkit", "safari", "v8"],
    "containers & cloud": ["kubernetes", "runc", "containerd", "docker engine", "docker desktop", "moby"],
    "enterprise & edge (mass-exploited)": [
        "microsoft exchange", "exchange server", "sharepoint", "fortios", "fortigate", "fortiproxy",
        "fortinet multiple products",
        "pan-os", "globalprotect", "cisco ios xe", "ios xe", "cisco asa", "netscaler", "citrix adc",
        "ivanti connect secure", "pulse connect secure", "vmware esxi", "esxi", "vcenter", "big-ip",
        "confluence", "gitlab", "moveit",
    ],
    "data & infrastructure services": [
        "postgresql", "mysql", "redis", "mongodb", "elasticsearch", "isc bind", "bind 9", "samba", "exim",
        "postfix", "openvpn", "wireguard",
    ],
}

_PATTERN = compile_terms([t for terms in FUNDAMENTALS.values() for t in terms])
_FIRST_SENTENCE = re.compile(r"^(.{0,240}?[.!?])(\s|$)", re.DOTALL)


def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    m = _FIRST_SENTENCE.match(text)
    return m.group(1) if m else text[:240]


def core_text(v: Vuln) -> str:
    """The text we trust to say WHAT is affected: products, packages and title. The description's
    first sentence is used only when a record names no product or package at all (otherwise every
    npm library "for Node.js" would count as Node.js)."""
    named = [*v.products, *(f"{p.ecosystem} {p.name}" for p in v.packages)]
    parts = [*named, v.title] + ([] if named else [_first_sentence(v.description)])
    return "\n".join(p for p in parts if p)


def touches_fundamental(text: str) -> bool:
    return _PATTERN is not None and _PATTERN.search(text) is not None


def is_severe(kev: bool, cvss: float | None, severity: str | None) -> bool:
    return kev or (cvss is not None and cvss >= MIN_CVSS) or (cvss is None and (severity or "").lower() == "critical")


def is_high(cvss: float | None, severity: str | None) -> bool:
    if cvss is not None:
        return cvss >= NOTABLE_MIN_CVSS
    return (severity or "").lower() in ("high", "critical")


def is_global_critical(v: Vuln) -> bool:
    return is_severe(v.kev or v.eu_exploited, v.cvss, v.severity) and touches_fundamental(core_text(v))


def is_global_notable(v: Vuln) -> bool:
    """Fundamental technology at high severity, but not global-critical (→ twice-daily brief)."""
    kev = v.kev or v.eu_exploited
    return (not is_severe(kev, v.cvss, v.severity) and is_high(v.cvss, v.severity)
            and touches_fundamental(core_text(v)))


def item_tier(item: DigestItem) -> str | None:
    """'critical' | 'notable' | None for a digest item (uses the stored core_text)."""
    text = item.core_text or f"{item.product}\n{item.title}"
    if not touches_fundamental(text):
        return None
    if is_severe(item.kev, item.cvss, item.severity):
        return "critical"
    return "notable" if is_high(item.cvss, item.severity) else None


class GlobalCriticalCategory(Category):
    """Hardcoded category: both tiers (critical → alerts, notable → the twice-daily brief)."""

    def __init__(self) -> None:
        super().__init__(slug=SLUG, label=LABEL, terms=[])

    def matches_item(self, item: DigestItem) -> bool:
        return item_tier(item) is not None


GLOBAL_CRITICAL = GlobalCriticalCategory()
