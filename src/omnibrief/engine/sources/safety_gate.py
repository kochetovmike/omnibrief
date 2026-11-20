"""EU Safety Gate weekly reports (XML export). Credit "© European Union, Safety Gate"; link each alert.

The list endpoint indexes every weekly report (a new one each Friday); we fetch the detail XML
of reports published inside the window (at most `max_reports`, newest first).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from omnibrief.engine.items import Item
from omnibrief.engine.sources.base import SourceAdapter

LIST_URL = "https://ec.europa.eu/safety-gate-alerts/api/download/weeklyReport/list/xml/en"
LEVEL_SEVERITY = {"serious risk": "high", "high risk": "high", "medium risk": "medium", "low risk": "low"}


def _t(el, tag: str) -> str:
    found = el.find(tag)
    return " ".join((found.text or "").split()) if found is not None and found.text else ""


class SafetyGateSource(SourceAdapter):
    type = "safety_gate"

    def reports(self, since: datetime) -> list[tuple[datetime, str]]:
        root = ET.fromstring(self.get(self.params.get("url", LIST_URL)).content)
        out = []
        for rep in root.findall("weeklyReport"):
            try:
                day = datetime.strptime(_t(rep, "publicationDate"), "%d/%m/%Y").replace(tzinfo=UTC)
            except ValueError:
                continue
            url = _t(rep, "URL")
            if url.startswith("https://") and day >= since.replace(hour=0, minute=0, second=0, microsecond=0):
                out.append((day, url))
        return sorted(out, reverse=True)[: int(self.params.get("max_reports", 2))]

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        items = []
        for day, url in self.reports(since):
            root = ET.fromstring(self.get(url).content)
            items += [it for n in root.findall("notifications") if (it := parse_notification(topic_id, n, day))]
        return items


def parse_notification(topic_id: str, n, report_day: datetime) -> Item | None:
    case = _t(n, "caseNumber")
    ref = _t(n, "reference")
    if not case or not ref.startswith("https://"):
        return None
    try:
        published = datetime.strptime(_t(n, "publicationDate"), "%d-%m-%Y").replace(tzinfo=UTC)
    except ValueError:
        published = report_day
    product, brand = _t(n, "product"), _t(n, "brand")
    level = _t(n, "level")
    return Item(
        topic=topic_id,
        key=f"SG:{case}",
        source_ids=["safety_gate"],
        title=f"{product}" + (f" — {brand}" if brand and brand.lower() not in ("unknown", "") else ""),
        url=ref,
        published=published,
        summary=_t(n, "danger")[:1200],
        severity=LEVEL_SEVERITY.get(level.lower(), "medium"),
        fields={"category": _t(n, "category"), "risk_type": _t(n, "riskType"), "level": level,
                "notifying_country": _t(n, "notifyingCountry"), "origin": _t(n, "countryOfOrigin"),
                "measures": _t(n, "measures")[:300], "case_number": case},
    )
