"""Static `feed` channel (design-v2 §9): Atom + RSS 2.0 per topic, combined feed, OPML."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

from omnibrief.engine.items import FeedEntry

ATOM_NS = "http://www.w3.org/2005/Atom"


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _clean(text: str) -> str:
    return _ILLEGAL.sub("", text)


def _el(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: _clean(str(v)) for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = _clean(str(text))
    return el


def _write(tree_root: ET.Element, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(tree_root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_atom(path: Path, *, feed_id: str, title: str, subtitle: str, self_url: str, site_url: str,
               rights: str, entries: list[FeedEntry], now: datetime, icon: str | None = None,
               language: str = "en") -> None:
    feed = ET.Element("feed", {"xmlns": ATOM_NS, "xml:lang": language})
    _el(feed, "id", feed_id)
    _el(feed, "title", title)
    _el(feed, "subtitle", subtitle)
    _el(feed, "updated", _ts(entries[0].published if entries else now))
    author = _el(feed, "author")
    _el(author, "name", "Omnibrief")
    _el(author, "uri", site_url)
    _el(feed, "link", href=self_url, rel="self", type="application/atom+xml")
    _el(feed, "link", href=site_url, rel="alternate", type="text/html")
    _el(feed, "rights", rights)
    _el(feed, "generator", "Omnibrief")
    if icon:
        _el(feed, "icon", icon)
    for e in entries:
        entry = _el(feed, "entry")
        _el(entry, "id", e.id)
        _el(entry, "title", e.title)
        _el(entry, "published", _ts(e.published))
        _el(entry, "updated", _ts(e.published))
        _el(entry, "link", href=e.link, rel="alternate", type="text/html")
        for url in e.related:
            _el(entry, "link", href=url, rel="related")
        _el(entry, "content", e.html, type="html")
    _write(feed, path)


def write_rss(path: Path, *, title: str, description: str, self_url: str, site_url: str, rights: str,
              entries: list[FeedEntry], now: datetime, language: str = "en") -> None:
    rss = ET.Element("rss", {"version": "2.0", "xmlns:atom": ATOM_NS})
    ch = _el(rss, "channel")
    _el(ch, "title", title)
    _el(ch, "link", site_url)
    _el(ch, "description", description)
    _el(ch, "language", language)
    _el(ch, "copyright", rights)
    _el(ch, "generator", "Omnibrief")
    _el(ch, "lastBuildDate", format_datetime((entries[0].published if entries else now).astimezone(UTC)))
    _el(ch, "atom:link", href=self_url, rel="self", type="application/rss+xml")
    for e in entries:
        it = _el(ch, "item")
        _el(it, "title", e.title)
        _el(it, "link", e.link)
        _el(it, "guid", e.id, isPermaLink="false")
        _el(it, "pubDate", format_datetime(e.published.astimezone(UTC)))
        _el(it, "category", e.topic)
        _el(it, "description", e.html)
    _write(rss, path)


def write_opml(path: Path, *, title: str, feeds: list[tuple[str, str, str]], now: datetime) -> None:
    """feeds: [(title, xml_url, html_url)]"""
    opml = ET.Element("opml", {"version": "2.0"})
    head = _el(opml, "head")
    _el(head, "title", title)
    _el(head, "dateCreated", format_datetime(now.astimezone(UTC)))
    body = _el(opml, "body")
    for name, xml_url, html_url in feeds:
        _el(body, "outline", type="rss", text=name, title=name, xmlUrl=xml_url, htmlUrl=html_url)
    _write(opml, path)
