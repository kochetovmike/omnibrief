"""GeoSphere Austria warnings (Warn API getWarnstatus). CC BY 4.0: credit
"Datenquelle: GeoSphere Austria", link the licence, state modification.

One item per warning id; level/time changes re-emit as "updated" (material_fields).
Municipality numbers (Gemeindekennziffer) give the Bundesland by their first digit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from omnibrief.engine.items import Item
from omnibrief.engine.sources.base import SourceAdapter

URL = "https://warnungen.zamg.at/wsapp/api/getWarnstatus"
PAGE = "https://warnungen.geosphere.at/"
TYPES = {1: "storm", 2: "rain", 3: "snow", 4: "black ice", 5: "thunderstorm", 6: "heat", 7: "cold"}
LEVELS = {1: "yellow", 2: "orange", 3: "red"}
LEVEL_SEVERITY = {1: "low", 2: "medium", 3: "high"}
LAENDER = {"1": "Burgenland", "2": "Kärnten", "3": "Niederösterreich", "4": "Oberösterreich", "5": "Salzburg",
           "6": "Steiermark", "7": "Tirol", "8": "Vorarlberg", "9": "Wien"}


class GeoSphereWarnSource(SourceAdapter):
    type = "geosphere_warnings"

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        data = self.get(self.params.get("url", URL)).json()  # no parameters (OpenAPI: getWarnstatus)
        return [it for f in data.get("features") or [] if (it := parse_warning(topic_id, f, now))]


def _ts(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError):
        return None


def parse_warning(topic_id: str, feature: dict, now: datetime) -> Item | None:
    p = feature.get("properties") or {}
    wid = p.get("warnid")
    wtype, level = int(p.get("wtype") or 0), int(p.get("wlevel") or 0)
    if not wid or not level:
        return None
    start, end = _ts(p.get("start")), _ts(p.get("end"))
    if end and end < now:
        return None  # expired
    laender = sorted({LAENDER[str(g)[0]] for g in p.get("gemeinden") or [] if str(g)[:1] in LAENDER})
    what = TYPES.get(wtype, "weather")
    title = f"{LEVELS.get(level, '').capitalize()} {what} warning — {', '.join(laender) or 'Austria'}"
    return Item(
        topic=topic_id,
        key=f"geosphere:{wid}",
        source_ids=["geosphere_warnings"],
        title=title,
        url=PAGE,
        published=start or now,
        severity=LEVEL_SEVERITY.get(level, "info"),
        fields={"warn_type": what, "level": level, "level_name": LEVELS.get(level), "start": start, "end": end,
                "laender": laender, "municipalities": len(p.get("gemeinden") or [])},
    )
