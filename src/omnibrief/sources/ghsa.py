"""GitHub Advisory Database via the global advisories REST API (CC-BY 4.0).

Pulls reviewed advisories updated inside the window (so newly patched versions show up too),
following the `Link: rel="next"` cursor. Uses GITHUB_TOKEN if present for a higher rate limit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from omnibrief.models import Package, Vuln, severity_from_cvss
from omnibrief.sources.base import Source, Window

MAX_PAGES = 20


class GhsaSource(Source):
    type = "ghsa"
    discovers = True

    def discover(self, window: Window) -> list[Vuln]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = self.ctx.env.get(self.cfg.option("token_env", "GITHUB_TOKEN"), "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        since = window.start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        url: str | None = self.cfg.url
        params: dict | None = {
            "type": self.cfg.option("advisory_type", "reviewed"),
            "updated": f">{since}",
            "per_page": 100,
            "sort": "updated",
            "direction": "desc",
        }
        out: dict[str, Vuln] = {}
        for _ in range(MAX_PAGES):
            if not url:
                break
            resp = self.ctx.client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                raise ValueError("GHSA response is not a list")
            for adv in items:
                v = parse_advisory(adv)
                if v:
                    out[v.id] = v
            url = resp.links.get("next", {}).get("url")
            params = None  # the next URL carries the cursor and all filters
        if url:
            # Fail loudly (source marked failed -> run degraded) rather than silently truncating.
            raise RuntimeError(f"GHSA pagination exceeded {MAX_PAGES} pages")
        return list(out.values())


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def parse_advisory(adv: dict) -> Vuln | None:
    if not isinstance(adv, dict) or adv.get("withdrawn_at"):
        return None
    ghsa_id = (adv.get("ghsa_id") or "").strip()
    if not ghsa_id:
        return None
    cve_id = (adv.get("cve_id") or "").strip().upper() or None
    # Prefer CVSS v3.x (consistent with NVD's preferred version), then v4, then the legacy field.
    sev = adv.get("cvss_severities") or {}
    candidates = [(sev.get("cvss_v3") or {}).get("score"), (sev.get("cvss_v4") or {}).get("score"),
                  (adv.get("cvss") or {}).get("score")]
    score = next((float(s) for s in candidates if s), None)
    severity = (adv.get("severity") or "").lower() or None
    if severity == "unknown":
        severity = None
    packages = []
    for item in adv.get("vulnerabilities") or []:
        pkg = item.get("package") or {}
        if pkg.get("name"):
            packages.append(
                Package(
                    ecosystem=pkg.get("ecosystem") or "",
                    name=pkg["name"],
                    vulnerable_range=item.get("vulnerable_version_range"),
                    patched=item.get("first_patched_version"),
                )
            )
    refs = [r for r in adv.get("references") or [] if isinstance(r, str) and r.startswith("https://")]
    html_url = adv.get("html_url") or f"https://github.com/advisories/{ghsa_id}"
    refs.append(html_url)
    return Vuln(
        id=cve_id or ghsa_id,
        aliases=[ghsa_id] if cve_id else [],
        title=(adv.get("summary") or "").strip(),
        description=(adv.get("description") or "").strip(),
        products=[f"{p.ecosystem}:{p.name}" for p in packages],
        packages=packages,
        cvss=score,
        cvss_vector=((adv.get("cvss_severities") or {}).get("cvss_v3") or {}).get("vector_string"),
        severity=severity or severity_from_cvss(score),
        published=_dt(adv.get("published_at")),
        modified=_dt(adv.get("updated_at")),
        refs=list(dict.fromkeys(refs)),
        sources=["ghsa"],
    )
