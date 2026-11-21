"""Generic adapters (design-v3 §5.1): config-only sources. A brief using one MUST carry a human
licence review block (validated in engine/brief.py).

  rss / atom   params: url
  json_api     params: url, method (GET|POST), query (dict), body (dict), items_path ("data.items"),
               fields: {key, title, url, published, summary, <any extra>: "dot.path"}
  geojson      json_api with items_path "features" by default (paths like "properties.title")
  xml          params: url, items_path (ElementTree path, e.g. ".//item"), fields: {name: "child/path"}
Paths are simple dot (JSON) / ElementTree (XML) paths, not full JSONPath/XPath.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from omnibrief.engine.items import Item
from omnibrief.engine.sources.base import SourceAdapter

ATOM = "{http://www.w3.org/2005/Atom}"


def _date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
    text = str(value).strip()
    for parse in (lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            dt = parse(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
    return None


def _make(source_type: str, topic: str, key: str | None, title: str, url: str, published, summary=None,
          fields=None) -> Item | None:
    url = (url or "").strip()
    if not url.startswith("https://") or not title:
        return None
    key = key or hashlib.sha1(url.encode()).hexdigest()[:16]
    return Item(topic=topic, key=f"{source_type}:{key}", source_ids=[source_type], title=" ".join(title.split()),
                url=url, published=_date(published) or datetime.now(UTC), summary=(summary or "")[:2000] or None,
                fields=fields or {})


class RssSource(SourceAdapter):
    type = "rss"

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        root = ET.fromstring(self.get(self.params["url"]).content)
        out = []
        for it in root.iter("item"):
            out.append(_make(self.type, topic_id, it.findtext("guid"), it.findtext("title") or "",
                             it.findtext("link") or "", it.findtext("pubDate"), it.findtext("description")))
        for e in root.iter(f"{ATOM}entry"):
            link = next((lnk.get("href") for lnk in e.findall(f"{ATOM}link")
                         if lnk.get("rel", "alternate") == "alternate"), "")
            out.append(_make(self.type, topic_id, e.findtext(f"{ATOM}id"), e.findtext(f"{ATOM}title") or "", link,
                             e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated"),
                             e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content")))
        return [i for i in out if i and i.published >= since]


class AtomSource(RssSource):
    type = "atom"


def _path(obj: Any, path: str) -> Any:
    for part in [p for p in str(path).split(".") if p]:
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


class JsonApiSource(SourceAdapter):
    type = "json_api"
    default_items_path = ""

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        p = self.params
        if str(p.get("method", "GET")).upper() == "POST":
            resp = self.ctx.client.post(p["url"], params=p.get("query"), json=p.get("body"))
            resp.raise_for_status()
        else:
            resp = self.get(p["url"], params=p.get("query"))
        rows = _path(resp.json(), p.get("items_path", self.default_items_path))
        fmap = p.get("fields") or {}
        out = []
        for row in rows if isinstance(rows, list) else []:
            vals = {k: _path(row, v) for k, v in fmap.items()}
            extra = {k: v for k, v in vals.items() if k not in ("key", "title", "url", "published", "summary")}
            out.append(_make(self.type, topic_id, str(vals["key"]) if vals.get("key") is not None else None,
                             str(vals.get("title") or ""), str(vals.get("url") or ""), vals.get("published"),
                             vals.get("summary"), extra))
        return [i for i in out if i and i.published >= since]


class GeoJsonSource(JsonApiSource):
    type = "geojson"
    default_items_path = "features"


class XmlSource(SourceAdapter):
    type = "xml"

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        p = self.params
        root = ET.fromstring(self.get(p["url"]).content)
        fmap = p.get("fields") or {}
        out = []
        for el in root.findall(p.get("items_path", ".//item")):
            vals = {k: (el.findtext(v) or "").strip() for k, v in fmap.items()}
            extra = {k: v for k, v in vals.items() if k not in ("key", "title", "url", "published", "summary")}
            out.append(_make(self.type, topic_id, vals.get("key") or None, vals.get("title", ""), vals.get("url", ""),
                             vals.get("published"), vals.get("summary"), extra))
        return [i for i in out if i and i.published >= since]


GENERIC_ADAPTERS = (RssSource, AtomSource, JsonApiSource, GeoJsonSource, XmlSource)
