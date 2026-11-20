"""arXiv API (Atom). Metadata incl. abstracts is CC0 via the API; never fetch PDFs/full text.

One polite request per run (arXiv asks for no more than one request every 3 seconds).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

from omnibrief.engine.items import Item
from omnibrief.engine.sources.base import SourceAdapter

URL = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivSource(SourceAdapter):
    type = "arxiv"

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        """Newest first, page by page until entries are older than `since` (cap: max_total)."""
        cats = self.params.get("categories") or ["cs.AI"]
        page = int(self.params.get("page_size", 300))
        max_total = int(self.params.get("max_total", 1500))
        out: list[Item] = []
        for start in range(0, max_total, page):
            if start:
                self.ctx.sleep(3.0)  # arXiv API etiquette: no more than one request every 3 seconds
            params = {"search_query": " OR ".join(f"cat:{c}" for c in cats), "sortBy": "submittedDate",
                      "sortOrder": "descending", "start": start, "max_results": page}
            entries = ET.fromstring(self.get(self.params.get("url", URL), params=params).content).findall("a:entry", NS)
            items = [it for e in entries if (it := parse_entry(topic_id, e))]
            out += [it for it in items if it.published >= since]
            if len(entries) < page or not items or min(it.published for it in items) < since:
                break
        return out


def _text(el, path: str) -> str:
    found = el.find(path, NS)
    return " ".join((found.text or "").split()) if found is not None else ""


def parse_entry(topic_id: str, entry) -> Item | None:
    raw_id = _text(entry, "a:id")
    m = re.search(r"abs/([^v\s]+(?:/\d+)?)(v\d+)?$", raw_id)
    if not m:
        return None
    arxiv_id = m.group(1)
    try:
        published = datetime.fromisoformat(_text(entry, "a:published").replace("Z", "+00:00"))
    except ValueError:
        return None
    authors = [_text(a, "a:name") for a in entry.findall("a:author", NS)]
    cats = [c.get("term") for c in entry.findall("a:category", NS) if c.get("term")]
    primary = entry.find("arxiv:primary_category", NS)
    return Item(
        topic=topic_id,
        key=f"arxiv:{arxiv_id}",
        source_ids=["arxiv"],
        title=_text(entry, "a:title"),
        url=f"https://arxiv.org/abs/{arxiv_id}",
        published=published,
        summary=_text(entry, "a:summary")[:2000],
        severity=None,
        fields={"authors": authors[:6], "more_authors": max(0, len(authors) - 6), "categories": cats,
                "primary_category": primary.get("term") if primary is not None else (cats[0] if cats else None)},
    )
