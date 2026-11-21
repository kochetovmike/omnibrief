"""TED (Tenders Electronic Daily) Search API v3 — anonymous POST, expert query.

Licence: CC BY 4.0 under the EU reuse policy. Credit "© European Union, TED", link each notice,
state that the content was modified (summarised).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from omnibrief.engine.items import Item
from omnibrief.engine.sources.base import SourceAdapter

log = logging.getLogger(__name__)
URL = "https://api.ted.europa.eu/v3/notices/search"
FIELDS = ["publication-number", "notice-title", "buyer-name", "buyer-country", "classification-cpv",
          "publication-date", "deadline-receipt-tender-date-lot", "estimated-value-lot", "notice-type", "links"]
LANG_ORDER = ["eng", "deu", "fra"]


def _pick(value, prefer=LANG_ORDER):
    """TED returns {lang: text | [text]}; pick English, then German, French, anything."""
    if isinstance(value, dict):
        for lang in [*prefer, *value.keys()]:
            if lang in value:
                return _pick(value[lang])
        return ""
    if isinstance(value, list):
        return _pick(value[0]) if value else ""
    return str(value or "")


class TedSource(SourceAdapter):
    type = "ted_search"

    def build_query(self, since: datetime) -> str:
        p = self.params
        parts = []
        if countries := p.get("countries"):
            parts.append(f"buyer-country IN ({' '.join(countries)})")
        if cpv := p.get("cpv"):
            parts.append(f"classification-cpv IN ({' '.join(str(c) for c in cpv)})")
        # Publication dates are days; look back one extra day so nothing slips between runs.
        parts.append(f"publication-date>={(since - timedelta(days=1)).astimezone(UTC):%Y%m%d}")
        return " AND ".join(parts) + " SORT BY publication-number DESC"

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        """Newest first, page by page (verified live: `SORT BY … DESC` + `page`), up to max_pages."""
        limit, max_pages = int(self.params.get("limit", 100)), int(self.params.get("max_pages", 5))
        query = self.build_query(since)
        notices: list[dict] = []
        total = 0
        for page in range(1, max_pages + 1):
            body = {"query": query, "fields": FIELDS, "limit": limit, "page": page}
            resp = self.ctx.client.post(self.params.get("url", URL), json=body)
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("notices") or []
            total = int(data.get("totalNoticeCount") or 0)
            notices += batch
            if not batch or len(notices) >= total:
                break
            self.ctx.sleep(1.0)
        self.truncated = total > len(notices)
        if self.truncated:
            log.warning("ted: %d of %d notices fetched (raise params.max_pages)", len(notices), total)
        return [it for n in notices if (it := parse_notice(topic_id, n))]


def parse_notice(topic_id: str, n: dict) -> Item | None:
    pub = n.get("publication-number")
    if not pub:
        return None
    raw_date = str(n.get("publication-date") or "")[:10]
    try:
        published = datetime.fromisoformat(raw_date).replace(tzinfo=UTC)
    except ValueError:
        published = datetime.now(UTC)
    links = (n.get("links") or {}).get("html") or {}
    url = links.get("ENG") or next(iter(links.values()), None) or f"https://ted.europa.eu/en/notice/-/detail/{pub}"
    title = _pick(n.get("notice-title")) or f"Tender {pub}"
    cpv = list(dict.fromkeys(n.get("classification-cpv") or []))
    return Item(
        topic=topic_id,
        key=f"TED:{pub}",
        source_ids=["ted_search"],
        title=title,
        url=url,
        published=published,
        fields={
            "buyer": _pick(n.get("buyer-name")),
            "country": ", ".join(n.get("buyer-country") or []),
            "cpv": cpv[:8],
            "deadline": _pick(n.get("deadline-receipt-tender-date-lot")) or None,
            "estimated_value": _pick(n.get("estimated-value-lot")) or None,
            "notice_type": _pick(n.get("notice-type")) or None,
        },
    )
