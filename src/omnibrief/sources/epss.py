"""EPSS (FIRST): exploit-probability enrichment, batched lookups only (the API is not for bulk sync)."""

from __future__ import annotations

from omnibrief.models import Vuln
from omnibrief.sources.base import Source


class EpssSource(Source):
    type = "epss"
    enriches = True
    enrich_after_dedupe = True

    def enrich(self, vulns: list[Vuln]) -> int:
        batch_size = min(int(self.cfg.option("batch_size", 100)), 100)
        by_cve: dict[str, list[Vuln]] = {}
        for v in vulns:
            cve = next((i for i in v.all_ids() if i.startswith("CVE-")), None)
            if cve:
                by_cve.setdefault(cve, []).append(v)
        cves = sorted(by_cve)
        touched = 0
        for i in range(0, len(cves), batch_size):
            batch = cves[i : i + batch_size]
            resp = self.ctx.client.get(self.cfg.url, params={"cve": ",".join(batch), "limit": batch_size})
            resp.raise_for_status()
            for row in resp.json().get("data") or []:
                try:
                    epss, pct = float(row["epss"]), float(row["percentile"])
                except (KeyError, TypeError, ValueError):
                    continue
                for v in by_cve.get(str(row.get("cve", "")).upper(), []):
                    v.epss, v.epss_percentile = epss, pct
                    if "epss" not in v.sources:
                        v.sources.append("epss")
                    touched += 1
        return touched
