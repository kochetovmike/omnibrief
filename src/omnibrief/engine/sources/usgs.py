"""USGS FDSN event service (GeoJSON). US federal public domain; credit USGS as courtesy."""

from __future__ import annotations

from datetime import UTC, datetime

from omnibrief.engine.items import GeoPoint, Item
from omnibrief.engine.sources.base import SourceAdapter

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def magnitude_severity(mag: float | None) -> str:
    if mag is None:
        return "info"
    return "critical" if mag >= 7 else "high" if mag >= 6 else "medium" if mag >= 5 else "low"


class UsgsSource(SourceAdapter):
    type = "usgs_fdsn"

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        p = self.params
        bbox = p.get("bbox")  # [min_lat, min_lon, max_lat, max_lon]
        params = {
            "format": "geojson",
            "starttime": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "minmagnitude": p.get("minmagnitude", 3.0),
            "orderby": "time",
            "limit": p.get("limit", 200),
        }
        if bbox:
            params.update(minlatitude=bbox[0], minlongitude=bbox[1], maxlatitude=bbox[2], maxlongitude=bbox[3])
        data = self.get(p.get("url", URL), params=params).json()
        return [it for f in data.get("features") or [] if (it := parse_feature(topic_id, f))]


def parse_feature(topic_id: str, f: dict) -> Item | None:
    props, geom = f.get("properties") or {}, f.get("geometry") or {}
    eid, url = f.get("id"), props.get("url")
    if not eid or not url or props.get("type", "earthquake") != "earthquake":
        return None
    mag = props.get("mag")
    place = props.get("place") or "Unknown location"
    coords = geom.get("coordinates") or [None, None, None]
    country = place.rsplit(", ", 1)[-1].strip() if ", " in place else place.split(" of ")[-1].strip()
    return Item(
        topic=topic_id,
        key=f"usgs:{eid}",
        source_ids=["usgs_fdsn"],
        title=f"M{mag:.1f} earthquake — {place}" if mag is not None else f"Earthquake — {place}",
        url=url.replace("http://", "https://"),
        published=datetime.fromtimestamp(props.get("time", 0) / 1000, UTC),
        updated=datetime.fromtimestamp(props["updated"] / 1000, UTC) if props.get("updated") else None,
        severity=magnitude_severity(mag),
        geo=GeoPoint(lat=coords[1], lon=coords[0], depth_km=coords[2]) if coords[0] is not None else None,
        fields={
            "magnitude": mag,
            "mag_type": props.get("magType"),
            "place": place,
            "country": country,
            "depth_km": coords[2],
            "tsunami_flag": bool(props.get("tsunami")),
            "alert": props.get("alert"),
            "felt": props.get("felt"),
        },
    )
