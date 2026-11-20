"""Dedupe/re-emit and inclusion filter (design §6). Both run BEFORE the LLM."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta

from omnibrief.categories import compile_terms, parse_categories, vuln_text
from omnibrief.config import FilterConfig
from omnibrief.global_critical import is_global_critical, is_global_notable
from omnibrief.models import Vuln, severity_rank
from omnibrief.state import SeenEntry

# --- watchlist ------------------------------------------------------------------------------


class Filter:
    def __init__(self, cfg: FilterConfig, env: Mapping[str, str]):
        self.cfg = cfg
        categories = parse_categories(env.get(cfg.watchlist_env, "") if cfg.watchlist_env else "")
        self.watchlist = [t for c in categories for t in c.terms]  # any category term is a hit
        self._pattern = compile_terms(self.watchlist)

    def reason(self, v: Vuln) -> str | None:
        """Why `v` is included, or None if it is filtered out."""
        if "kev" in self.cfg.always_include_if and v.kev:
            return "kev"
        if "eu_exploited" in self.cfg.always_include_if and v.eu_exploited:
            return "eu_exploited"
        if is_global_critical(v):
            return "global_critical"
        if is_global_notable(v):
            return "global_notable"
        if (
            self._pattern is not None
            and v.cvss is not None
            and v.cvss >= self.cfg.min_cvss
            and self._pattern.search(vuln_text(v))
        ):
            return "watchlist"
        if (
            self.cfg.epss_percentile_override is not None
            and v.epss_percentile is not None
            and v.epss_percentile >= self.cfg.epss_percentile_override
        ):
            return "epss"
        return None


# --- dedupe / re-emit -----------------------------------------------------------------------


def find_seen(v: Vuln, seen: dict[str, SeenEntry]) -> tuple[str, SeenEntry] | None:
    for i in v.all_ids():
        if i in seen:
            return i, seen[i]
    for key, entry in seen.items():
        if set(entry.aliases) & set(v.all_ids()):
            return key, entry
    return None


def material_changes(v: Vuln, entry: SeenEntry) -> list[str]:
    changes = []
    if v.kev and not entry.kev:
        changes.append("added to CISA KEV (actively exploited)")
    if severity_rank(v.severity) > severity_rank(entry.severity) and entry.severity is not None:
        changes.append(f"severity raised from {entry.severity} to {v.severity}")
    new_fixes = sorted(set(v.patched_versions()) - set(entry.patched))
    if new_fixes:
        changes.append("fix released: " + ", ".join(new_fixes))
    return changes


FRESH_DAYS = 14


def is_fresh(v: Vuln, now: datetime) -> bool:
    """Unseen records only count as news if they were published, or added to KEV, recently.
    Stops edits to years-old advisories (GHSA `updated` window) resurfacing as "new"."""
    cutoff = now - timedelta(days=FRESH_DAYS)
    if v.kev_date_added:
        try:
            if date.fromisoformat(v.kev_date_added) >= cutoff.date():
                return True
        except ValueError:
            pass
    return v.published is None or v.published >= cutoff


def dedupe(vulns: list[Vuln], seen: dict[str, SeenEntry], now: datetime | None = None) -> list[Vuln]:
    """Return records that are new or materially changed; sets status/changes on them.

    `seen` only holds records that were emitted, so a record that was filtered out before simply
    shows up as new the day it starts to matter (e.g. added to KEV). With `now`, unseen records
    that are not fresh (see is_fresh) are dropped.
    """
    out = []
    for v in vulns:
        hit = find_seen(v, seen)
        if hit is None:
            if now is not None and not is_fresh(v, now):
                continue
            v.status, v.changes = "new", []
            out.append(v)
            continue
        changes = material_changes(v, hit[1])
        if changes:
            v.status, v.changes = "updated", changes
            out.append(v)
    return out


def remember(v: Vuln, seen: dict[str, SeenEntry], now: datetime) -> None:
    """Record an emitted record. Migrates a GHSA-keyed entry to the CVE key when one appears."""
    hit = find_seen(v, seen)
    if hit:
        old_key, entry = hit
        if old_key != v.id:
            del seen[old_key]
    else:
        entry = SeenEntry(first_seen=now, last_seen=now, last_material_change=now)
    entry.last_seen = now
    if v.status == "updated" or hit is None:
        entry.last_material_change = now
    entry.aliases = sorted(set(entry.aliases) | set(v.aliases))
    entry.kev = entry.kev or v.kev
    if severity_rank(v.severity) >= severity_rank(entry.severity):
        entry.severity = v.severity
    entry.patched = sorted(set(entry.patched) | set(v.patched_versions()))
    seen[v.id] = entry


def touch(vulns: list[Vuln], seen: dict[str, SeenEntry], now: datetime) -> None:
    """Refresh last_seen for already-known records still showing up, so pruning keeps them."""
    for v in vulns:
        hit = find_seen(v, seen)
        if hit:
            hit[1].last_seen = now


def sort_key(v: Vuln) -> tuple:
    """KEV first, then severity, then EPSS (design §7)."""
    return (not v.kev, -severity_rank(v.severity), -(v.cvss or 0), -(v.epss_percentile or 0), v.id)
