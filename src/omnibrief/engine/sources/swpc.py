"""NOAA SWPC alerts/watches/warnings (products/alerts.json). Public information; credit NOAA SWPC.

Each message carries its K-index and NOAA G-scale in free text; we parse both so filters can
post only on thresholds (e.g. Kp >= 6 or G2+), never every hour.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from omnibrief.engine.items import Item
from omnibrief.engine.sources.base import SourceAdapter

URL = "https://services.swpc.noaa.gov/products/alerts.json"
PAGE = "https://www.swpc.noaa.gov/products/alerts-watches-and-warnings"
_CODE = re.compile(r"Space Weather Message Code:\s*(\w+)")
_SERIAL = re.compile(r"Serial Number:\s*(\d+)")
_K = re.compile(r"K-index of (\d)")
_G = re.compile(r"NOAA Scale:\s*G(\d)", re.IGNORECASE)
_HEAD = re.compile(r"^(ALERT|WARNING|WATCH|EXTENDED WARNING|SUMMARY|CANCEL[A-Z ]*):\s*(.+)$", re.MULTILINE)
G_SEVERITY = {1: "low", 2: "medium", 3: "high", 4: "critical", 5: "critical"}
G_NAME = {1: "Minor", 2: "Moderate", 3: "Strong", 4: "Severe", 5: "Extreme"}


class SwpcSource(SourceAdapter):
    type = "noaa_swpc"

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        rows = self.get(self.params.get("url", URL)).json()
        out = []
        for row in rows if isinstance(rows, list) else []:
            item = parse_alert(topic_id, row)
            if item and item.published >= since:
                out.append(item)
        return out


def parse_alert(topic_id: str, row: dict) -> Item | None:
    msg = (row.get("message") or "").replace("\r\n", "\n")
    issued = row.get("issue_datetime")
    if not msg or not issued:
        return None
    try:
        published = datetime.fromisoformat(issued.replace(" ", "T")).replace(tzinfo=UTC)
    except ValueError:
        return None
    code = (m.group(1) if (m := _CODE.search(msg)) else row.get("product_id", "SWPC"))
    serial = m.group(1) if (m := _SERIAL.search(msg)) else issued
    k = int(m.group(1)) if (m := _K.search(msg)) else None
    g = int(m.group(1)) if (m := _G.search(msg)) else None
    if g is None and k is not None and k >= 5:
        g = k - 4  # NOAA: K5 = G1 … K9 = G5
    head = _HEAD.search(msg)
    kind, what = (head.group(1).title(), head.group(2).strip()) if head else ("Message", code)
    title = f"{kind}: {what}" + (f" (G{g} {G_NAME.get(g, '')})" if g else "")
    return Item(
        topic=topic_id,
        key=f"swpc:{code}:{serial}",
        source_ids=["noaa_swpc"],
        title=title.strip(),
        url=PAGE,
        published=published,
        summary=msg[:1500],
        severity=G_SEVERITY.get(g or 0, "info"),
        fields={"code": code, "serial": serial, "kp": k, "g_scale": g or 0, "kind": kind},
    )
