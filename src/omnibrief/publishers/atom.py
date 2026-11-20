"""Atom feed on GitHub Pages: always on, rebuilt every run from the last N digests (no XML editing).

Written with xml.etree rather than feedgen: feedgen 1.0 drops `rel`/`type` from entry links
(variable shadowing in FeedEntry.atom_entry), which turns every related link into an alternate
link and makes the feed invalid.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from omnibrief.attribution import short_rights
from omnibrief.models import Digest, utcnow
from omnibrief.publishers.base import Publisher, PublishResult

ATOM_NS = "http://www.w3.org/2005/Atom"
RELATED_LINKS_CAP = 20


def feed_path(topic_id: str) -> str:
    return f"feeds/{topic_id}.atom"


def entry_anchor(run_id: str) -> str:
    return f"run-{run_id}"


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _el(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = text
    return el


def build_feed(*, feed_id: str, title: str, subtitle: str, base: str, self_href: str, language: str,
               rights: str, digests: list[Digest], entry_prefix: str) -> ET.ElementTree:
    feed = ET.Element("feed", {"xmlns": ATOM_NS, "xml:lang": language})
    _el(feed, "id", feed_id)
    _el(feed, "title", title)
    _el(feed, "subtitle", subtitle)
    _el(feed, "updated", _ts(digests[0].created_utc if digests else utcnow()))
    author = _el(feed, "author")
    _el(author, "name", "Omnibrief")
    _el(author, "uri", base)
    _el(feed, "link", href=self_href, rel="self", type="application/atom+xml")
    _el(feed, "link", href=base, rel="alternate", type="text/html")
    _el(feed, "rights", rights)
    _el(feed, "generator", "Omnibrief")

    for d in digests:  # newest first
        entry = _el(feed, "entry")
        _el(entry, "id", f"{entry_prefix}{d.run_id}")
        _el(entry, "title", d.title + ("" if d.summarised else " (un-summarised)"))
        _el(entry, "published", _ts(d.created_utc))
        _el(entry, "updated", _ts(d.created_utc))
        _el(entry, "link", href=f"{base}#{entry_anchor(d.run_id)}", rel="alternate", type="text/html")
        related = list(dict.fromkeys(i.links[0] for i in d.items if i.links))[:RELATED_LINKS_CAP]
        for url in related:
            _el(entry, "link", href=url, rel="related")
        _el(entry, "content", d.html, type="html")

    tree = ET.ElementTree(feed)
    ET.indent(tree, space="  ")
    return tree


class AtomPublisher(Publisher):
    kind = "static"
    type = "atom"

    def publish(self, digests: list[Digest]) -> PublishResult:
        cfg, topic = self.ctx.cfg, self.ctx.topic
        base = cfg.site.base
        rel = feed_path(topic.id)
        tree = build_feed(
            feed_id=f"urn:omnibrief:{topic.id}",
            title=topic.title,
            subtitle="Openly licensed vulnerability data, filtered and summarised every 3 hours.",
            base=base,
            self_href=f"{base}{rel}",
            language=topic.language,
            rights=short_rights(topic.source_types()),
            digests=digests,
            entry_prefix=f"urn:omnibrief:{topic.id}:",
        )
        out = self.ctx.site_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        tree.write(out, encoding="utf-8", xml_declaration=True)
        return PublishResult(status="ok")
