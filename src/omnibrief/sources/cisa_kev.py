"""CISA Known Exploited Vulnerabilities catalog (CC0).

Discovery: entries whose `dateAdded` falls within `lookback_days` (the catalog only has dates).
Enrichment: flags any merged record whose CVE is in the catalog, however old the entry is.
"""

from __future__ import annotations

from datetime import date, timedelta

from omnibrief.models import Vuln
from omnibrief.sources.base import Source, Window


class CisaKevSource(Source):
    type = "cisa_kev"
    discovers = True
    enriches = True

    _catalog: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._catalog is None:
            resp = self.ctx.client.get(self.cfg.url)
            resp.raise_for_status()
            data = resp.json()
            entries = data.get("vulnerabilities")
            if not isinstance(entries, list):
                raise ValueError("KEV catalog has no 'vulnerabilities' list")
            self._catalog = {
                e["cveID"].strip().upper(): e for e in entries if isinstance(e, dict) and e.get("cveID")
            }
        return self._catalog

    def discover(self, window: Window) -> list[Vuln]:
        catalog = self._load()
        days = int(self.cfg.option("lookback_days", 2))
        cutoff = window.end.date() - timedelta(days=days)
        out = []
        for entry in catalog.values():
            try:
                added = date.fromisoformat(entry.get("dateAdded", ""))
            except ValueError:
                continue
            if added >= cutoff:
                out.append(to_vuln(entry))
        return out

    def enrich(self, vulns: list[Vuln]) -> int:
        catalog = self._load()
        touched = 0
        for v in vulns:
            entry = next((catalog[i] for i in v.all_ids() if i in catalog), None)
            if entry and not v.kev:
                kev = to_vuln(entry)
                v.kev, v.kev_date_added = True, kev.kev_date_added
                v.kev_ransomware, v.kev_required_action = kev.kev_ransomware, kev.kev_required_action
                if "cisa_kev" not in v.sources:
                    v.sources.append("cisa_kev")
                touched += 1
        return touched


def to_vuln(e: dict) -> Vuln:
    cve = e["cveID"].strip().upper()
    vendor, product = (e.get("vendorProject") or "").strip(), (e.get("product") or "").strip()
    refs = [p.strip() for p in (e.get("notes") or "").split(" ; ") if p.strip().startswith("https://")]
    # Notes items look like "Label: https://..." too; keep only the URL part.
    for part in (e.get("notes") or "").split(" ; "):
        if ": https://" in part:
            refs.append(part.split(": ", 1)[1].strip())
    return Vuln(
        id=cve,
        title=(e.get("vulnerabilityName") or "").strip(),
        description=(e.get("shortDescription") or "").strip(),
        products=[f"{vendor} {product}".strip()] if vendor or product else [],
        kev=True,
        kev_date_added=e.get("dateAdded"),
        kev_ransomware=(e.get("knownRansomwareCampaignUse") or "").lower() == "known",
        kev_required_action=(e.get("requiredAction") or "").strip() or None,
        refs=list(dict.fromkeys(refs)),
        sources=["cisa_kev"],
    )
