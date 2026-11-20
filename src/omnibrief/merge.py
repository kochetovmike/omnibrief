"""Merge partial records from several sources into one Vuln per vulnerability (design §6).

Grouping: records sharing any id or alias are the same vulnerability. Canonical key is the CVE id
if any record in the group has one, otherwise the GHSA id.

Field precedence (first source type with a non-empty value wins):
  exploitation facts  ← cisa_kev
  cvss/vector/severity← nvd (Primary, else CNA Secondary — chosen in the adapter), then ghsa
  packages / patched  ← ghsa
  description         ← nvd, ghsa, cisa_kev
  title               ← cisa_kev (vulnerabilityName), ghsa (summary)
  products, refs      ← union of all
  epss                ← epss enrichment (applied after merge)
"""

from __future__ import annotations

from omnibrief.models import Vuln

PRECEDENCE = {
    "title": ["cisa_kev", "ghsa", "nvd"],
    "description": ["nvd", "ghsa", "cisa_kev"],
    "score": ["nvd", "ghsa"],
    "packages": ["ghsa"],
    "union": ["nvd", "cisa_kev", "ghsa"],
}


def _rank(v: Vuln, order: list[str]) -> int:
    src = v.sources[0] if v.sources else ""
    return order.index(src) if src in order else len(order)


def _canonical(ids: set[str]) -> str:
    cves = sorted(i for i in ids if i.startswith("CVE-"))
    if cves:
        return cves[0]
    return sorted(ids)[0]


def group(records: list[Vuln]) -> list[list[Vuln]]:
    """Union-find over ids/aliases."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in records:
        ids = r.all_ids()
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])
    groups: dict[str, list[Vuln]] = {}
    for r in records:
        groups.setdefault(find(r.id), []).append(r)
    return list(groups.values())


def merge_group(parts: list[Vuln]) -> Vuln:
    ids = {i for p in parts for i in p.all_ids()}
    key = _canonical(ids)

    def first(field_order: str, attr: str):
        for p in sorted(parts, key=lambda p: _rank(p, PRECEDENCE[field_order])):
            value = getattr(p, attr)
            if value:
                return p
        return None

    merged = Vuln(id=key, aliases=sorted(ids - {key}))
    if p := first("title", "title"):
        merged.title = p.title
    if p := first("description", "description"):
        merged.description = p.description
    if p := first("score", "cvss"):
        merged.cvss, merged.cvss_vector, merged.severity = p.cvss, p.cvss_vector, p.severity
    elif p := first("score", "severity"):
        merged.severity = p.severity
    if p := first("packages", "packages"):
        merged.packages = list(p.packages)

    ordered = sorted(parts, key=lambda p: _rank(p, PRECEDENCE["union"]))
    merged.products = list(dict.fromkeys(x for p in ordered for x in p.products))
    merged.refs = list(dict.fromkeys(x for p in ordered for x in p.refs))
    merged.sources = list(dict.fromkeys(s for p in ordered for s in p.sources))

    for p in parts:
        if p.kev:
            merged.kev, merged.kev_date_added = True, p.kev_date_added
            merged.kev_ransomware = merged.kev_ransomware or p.kev_ransomware
            merged.kev_required_action = p.kev_required_action
        merged.eu_exploited = merged.eu_exploited or p.eu_exploited
        if p.epss is not None:
            merged.epss, merged.epss_percentile = p.epss, p.epss_percentile
    published = [p.published for p in parts if p.published]
    modified = [p.modified for p in parts if p.modified]
    merged.published = min(published) if published else None
    merged.modified = max(modified) if modified else None
    return merged


def merge(records: list[Vuln]) -> list[Vuln]:
    return [merge_group(g) for g in group(records)]
