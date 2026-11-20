"""NVD CVE API 2.0 (NIST, public domain; API terms apply — see NOTICE.md).

Pulls CVEs published inside the window, paginating with startIndex and sleeping between pages to
stay under the rolling 30-second limit (5 requests without a key, 50 with one).
`include_modified: true` also pulls the lastMod window; off by default because NVD re-touches
thousands of old CVEs a day.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from omnibrief.models import Vuln, severity_from_cvss
from omnibrief.sources.base import Source, Window

GHSA_ID = re.compile(r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}")
PAGE_SIZE = 2000
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}  # NVD answers 403 when rate limited
RETRY_SLEEP = 30.0
# Seconds between requests: 30s / 5 requests without a key, 30s / 50 with one (+ headroom).
SLEEP_NO_KEY = 6.5
SLEEP_WITH_KEY = 0.8
# Preference among CVSS versions when picking one score.
METRIC_ORDER = ["cvssMetricV31", "cvssMetricV40", "cvssMetricV30", "cvssMetricV2"]


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class NvdSource(Source):
    type = "nvd"
    discovers = True

    def discover(self, window: Window) -> list[Vuln]:
        windows = [("pubStartDate", "pubEndDate")]
        if self.cfg.option("include_modified", False):
            windows.append(("lastModStartDate", "lastModEndDate"))
        seen: dict[str, Vuln] = {}
        first_request = True
        for start_key, end_key in windows:
            start_index = 0
            while True:
                if not first_request:
                    self.ctx.sleep(self._sleep_seconds())
                first_request = False
                params = {
                    start_key: _fmt(window.start),
                    end_key: _fmt(window.end),
                    "resultsPerPage": PAGE_SIZE,
                    "startIndex": start_index,
                }
                headers = {"apiKey": key} if (key := self.env_value("api_key_env")) else {}
                resp = self._get(params, headers)
                data = resp.json()
                items = data.get("vulnerabilities") or []
                for item in items:
                    v = parse_cve(item.get("cve") or {})
                    if v:
                        seen[v.id] = v
                total = int(data.get("totalResults", 0))
                start_index += len(items)
                if not items or start_index >= total:
                    break
        return list(seen.values())

    def _get(self, params: dict, headers: dict):
        """GET with two paced retries on rate limiting / server errors (NVD is often flaky)."""
        for attempt in range(3):
            resp = self.ctx.client.get(self.cfg.url, params=params, headers=headers)
            if resp.status_code not in RETRY_STATUSES or attempt == 2:
                break
            self.ctx.sleep(RETRY_SLEEP)
        resp.raise_for_status()
        return resp

    def _sleep_seconds(self) -> float:
        return SLEEP_WITH_KEY if self.env_value("api_key_env") else SLEEP_NO_KEY


def _pick_metric(metrics: dict) -> dict | None:
    """Prefer NVD's Primary score, else the CNA's Secondary score (design §6)."""
    for wanted in ("Primary", "Secondary"):
        for key in METRIC_ORDER:
            for m in metrics.get(key) or []:
                if m.get("type") == wanted and m.get("cvssData", {}).get("baseScore") is not None:
                    return m
    return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _products(cve: dict) -> list[str]:
    out: list[str] = []
    for aff in cve.get("affected") or []:
        for d in aff.get("affectedData") or []:
            name = f"{d.get('vendor') or ''} {d.get('product') or ''}".strip()
            if name and name.lower() not in ("n/a n/a", "n/a"):
                out.append(name)
    for conf in cve.get("configurations") or []:
        for node in conf.get("nodes") or []:
            for match in node.get("cpeMatch") or []:
                if not match.get("vulnerable"):
                    continue
                parts = (match.get("criteria") or "").split(":")
                if len(parts) > 4:
                    out.append(f"{parts[3]} {parts[4]}".replace("_", " "))
    return list(dict.fromkeys(out))


def parse_cve(cve: dict) -> Vuln | None:
    cve_id = (cve.get("id") or "").strip().upper()
    if not cve_id.startswith("CVE-") or cve.get("vulnStatus") == "Rejected":
        return None
    desc = next((d.get("value", "") for d in cve.get("descriptions") or [] if d.get("lang") == "en"), "")
    metric = _pick_metric(cve.get("metrics") or {})
    score = vector = severity = None
    if metric:
        data = metric["cvssData"]
        score = float(data["baseScore"])
        vector = data.get("vectorString")
        severity = (data.get("baseSeverity") or metric.get("baseSeverity") or "").lower() or None
    refs = [r["url"] for r in cve.get("references") or [] if str(r.get("url", "")).startswith("https://")]
    refs.append(f"https://nvd.nist.gov/vuln/detail/{cve_id}")
    # Link to an earlier GHSA-only record (same vuln) via advisory URLs in the references.
    ghsa_ids = sorted({m.group(0) for r in refs if (m := GHSA_ID.search(r))})
    return Vuln(
        id=cve_id,
        aliases=ghsa_ids,
        description=desc.strip(),
        products=_products(cve),
        cvss=score,
        cvss_vector=vector,
        severity=severity or severity_from_cvss(score),
        published=_parse_dt(cve.get("published")),
        modified=_parse_dt(cve.get("lastModified")),
        refs=list(dict.fromkeys(refs)),
        sources=["nvd"],
    )
