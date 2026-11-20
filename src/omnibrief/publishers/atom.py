"""Atom feed on GitHub Pages: a short, message-style alert feed. Always on.

Two kinds of entries, rebuilt from scratch every run from the digest history (no XML editing,
so re-running a window never duplicates anything):

  alert    one entry per vulnerability that is CRITICAL or ACTIVELY EXPLOITED (CISA KEV),
           published as soon as a run finds it. Re-emitted ("updated") items get a new alert.
  roundup  one entry per 12-hour window (08:00–20:00 and 20:00–08:00, site timezone) listing
           every other included vulnerability (high / medium / low). Published only once the
           window has closed, so a reader sees at most two roundups a day.

Feeds: feeds/vulns.atom ("general", everything) plus feeds/vulns-<slug>.atom per VULNS_WATCHLIST
category (see categories.py), each with the same alert/roundup rules applied to its own items.

Written with xml.etree rather than feedgen: feedgen 1.0 drops `rel`/`type` from entry links.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from omnibrief.attribution import short_rights
from omnibrief.categories import Category, categories_for
from omnibrief.global_critical import SLUG as GC_SLUG
from omnibrief.models import Digest, DigestItem, severity_rank
from omnibrief.publishers.base import Publisher, PublishResult

ATOM_NS = "http://www.w3.org/2005/Atom"
ROLLUP_HOURS_LOCAL = (8, 20)  # 12-hour roundup windows (and the Telegram Global brief) start here
FEED_DAYS = 14  # entries older than this drop out of the feed
TITLE_CHARS = 70
_LI = re.compile(r"<li>(.*?)</li>", re.DOTALL)


def feed_path(topic_id: str, slug: str | None = None) -> str:
    """General feed: feeds/vulns.atom (stable URL); category feeds: feeds/vulns-<slug>.atom."""
    return f"feeds/{topic_id}.atom" if not slug else f"feeds/{topic_id}-{slug}.atom"


def page_path(slug: str | None = None) -> str:
    """Dashboard page for a view (flat files so relative links keep working). index.html is the hub."""
    return "general.html" if not slug else f"cat-{slug}.html"


def entry_anchor(run_id: str) -> str:
    return f"run-{run_id}"


def is_alert(item: DigestItem) -> bool:
    """Gets its own entry immediately: critical severity, or known to be exploited."""
    return item.kev or (item.severity or "").lower() == "critical"


# --- 12-hour windows ------------------------------------------------------------------------------

def rollup_window(dt: datetime, tz: str) -> tuple[datetime, datetime]:
    """The [start, end) roundup window containing `dt`, as UTC datetimes (DST-safe)."""
    zone = ZoneInfo(tz)
    local = dt.astimezone(zone)
    first, second = ROLLUP_HOURS_LOCAL
    day = local.date()
    if local.hour < first:
        start_day, start_h, end_day, end_h = day - timedelta(days=1), second, day, first
    elif local.hour < second:
        start_day, start_h, end_day, end_h = day, first, day, second
    else:
        start_day, start_h, end_day, end_h = day, second, day + timedelta(days=1), first
    start = datetime.combine(start_day, time(start_h), zone)
    end = datetime.combine(end_day, time(end_h), zone)
    return start.astimezone(UTC), end.astimezone(UTC)


# --- entry model ---------------------------------------------------------------------------------

@dataclass
class Entry:
    id: str
    title: str
    published: datetime
    html: str
    alternate: str
    related: list[str] = field(default_factory=list)


def _clip(text: str, n: int = TITLE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _https(urls: list[str]) -> list[str]:
    return [u for u in urls if u.startswith("https://")]


def _links_html(urls: list[str]) -> str:
    return " · ".join(
        f'<a href="{escape(u)}" rel="noopener noreferrer nofollow">{escape(urlsplit(u).hostname or "link")}</a>'
        for u in _https(urls)
    )


def _llm_sentence(digest: Digest, item_id: str) -> str | None:
    """The (already sanitised) summary bullet the LLM wrote for this item, if any."""
    if not digest.summarised:
        return None
    for li in _LI.findall(digest.html):
        if item_id in li:
            return li.strip()
    return None


def _facts(item: DigestItem) -> str:
    bits = []
    if item.kev:
        since = f" since {item.kev_date_added}" if item.kev_date_added else ""
        bits.append(f"<strong>Actively exploited</strong> (CISA KEV{escape(since)})")
    if item.severity:
        bits.append(escape(item.severity.capitalize()) + (f" · CVSS {item.cvss:.1f}" if item.cvss is not None else ""))
    if item.epss_percentile is not None:
        bits.append(f"EPSS percentile {item.epss_percentile * 100:.0f}")
    if item.changes:
        bits.append("Changed: " + escape("; ".join(item.changes)))
    return " · ".join(bits)


class AtomPublisher(Publisher):
    kind = "static"
    type = "atom"

    # --- entries ---------------------------------------------------------------------------------
    def _footer(self, anchor: str) -> str:
        base = self.ctx.cfg.site.base
        return (f'<p><small>Machine-generated from open data · <a href="{base}about.html">Sources &amp; '
                f'licences</a> · <a href="{base}{page_path()}#{anchor}">Dashboard</a></small></p>')

    def alert_entry(self, digest: Digest, item: DigestItem) -> Entry:
        icon, label = ("🚨", "Exploited") if item.kev else ("🔴", "Critical")
        if item.status == "updated":
            label = "Update · " + label
        subject = item.product or item.title or ""
        title = f"{icon} {label} · {item.id}" + (f" · {_clip(subject)}" if subject else "")
        body = _llm_sentence(digest, item.id) or f"<strong>{escape(item.id)}</strong> — {escape(item.title)}"
        links = _links_html(item.links)
        html = (f"<p>{body}</p><p>{_facts(item)}</p>"
                + (f"<p>{links}</p>" if links else "")
                + self._footer(entry_anchor(digest.run_id)))
        topic = self.ctx.topic.id
        fallback = f"{self.ctx.cfg.site.base}{page_path()}#{entry_anchor(digest.run_id)}"
        primary = next(iter(_https(item.links)), fallback)
        return Entry(
            id=f"urn:omnibrief:{topic}:alert:{digest.run_id}:{item.id}",
            title=title,
            published=digest.created_utc,
            html=html,
            alternate=primary,
            related=_https(item.links)[1:3],
        )

    def rollup_entry(self, start: datetime, end: datetime, rows: list[tuple[Digest, DigestItem]],
                     category: Category | None = None) -> Entry:
        tz = ZoneInfo(self.ctx.cfg.site.timezone)
        rows = sorted(rows, key=lambda r: (-severity_rank(r[1].severity), -(r[1].cvss or 0), r[1].id))
        counts: dict[str, int] = {}
        for _, item in rows:
            sev = (item.severity or "unrated").lower()
            counts[sev] = counts.get(sev, 0) + 1
        summary = " · ".join(f"{n} {sev}" for sev, n in sorted(counts.items(), key=lambda kv: -severity_rank(kv[0])))
        span = f"{start.astimezone(tz):%d %b, %H:%M}–{end.astimezone(tz):%H:%M}"
        lis = "".join(
            f"<li><strong>{escape(item.id)}</strong> · {escape((item.severity or 'unrated').upper())}"
            + (f" {item.cvss:.1f}" if item.cvss is not None else "")
            + (" · updated" if item.status == "updated" else "")
            + f" — {escape(_clip(item.title or item.product, 140))}"
            + (f" — {_links_html(item.links[:1])}" if _https(item.links[:1]) else "")
            + "</li>"
            for _, item in rows
        )
        last_run = max(rows, key=lambda r: r[0].created_utc)[0].run_id
        html = (f"<p>{len(rows)} vulnerabilit{'y' if len(rows) == 1 else 'ies'} below critical in this "
                f"12-hour window.</p><ul>{lis}</ul>" + self._footer(entry_anchor(last_run)))
        return Entry(
            id=f"{self._feed_id(category)}:roundup:{start:%Y%m%dT%H%MZ}",
            title=(f"🟣 Global brief · {span} · {summary}" if category is not None and category.slug == GC_SLUG
                   else f"🟠 Roundup{' · ' + category.label if category else ''} · {span} · {summary}"),
            published=end,
            html=html,
            alternate=f"{self.ctx.cfg.site.base}{page_path(category.slug if category else None)}",
        )

    def _feed_id(self, category: Category | None) -> str:
        return f"urn:omnibrief:{self.ctx.topic.id}" + (f":{category.slug}" if category else "")

    def entries(self, digests: list[Digest], category: Category | None = None) -> list[Entry]:
        """Entries for the general feed, or only items matching `category`."""
        now = self.ctx.now
        cutoff = now - timedelta(days=FEED_DAYS)
        tz = self.ctx.cfg.site.timezone
        alerts: list[Entry] = []
        windows: dict[tuple[datetime, datetime], list[tuple[Digest, DigestItem]]] = {}
        for d in digests:
            if d.created_utc < cutoff:
                continue
            for item in d.items:
                if category is not None and not category.matches_item(item):
                    continue
                if is_alert(item):
                    alerts.append(self.alert_entry(d, item))
                else:
                    windows.setdefault(rollup_window(d.created_utc, tz), []).append((d, item))
        rollups = [self.rollup_entry(s, e, rows, category) for (s, e), rows in windows.items() if e <= now]
        out = sorted(alerts + rollups, key=lambda e: (e.published, e.id), reverse=True)
        return out[: self.ctx.cfg.site.entries_per_feed]

    # --- XML ---------------------------------------------------------------------------------------
    def publish(self, digests: list[Digest]) -> PublishResult:
        """Writes the general feed plus one feed per watchlist category."""
        for category in [None, *categories_for(self.ctx.topic, self.ctx.env)]:
            self._write_feed(digests, category)
        return PublishResult(status="ok")

    def _write_feed(self, digests: list[Digest], category: Category | None) -> None:
        cfg, topic = self.ctx.cfg, self.ctx.topic
        base = cfg.site.base
        rel = feed_path(topic.id, category.slug if category else None)
        entries = self.entries(digests, category)

        feed = ET.Element("feed", {"xmlns": ATOM_NS, "xml:lang": topic.language})
        _el(feed, "id", self._feed_id(category))
        _el(feed, "title", f"Omnibrief · {topic.title}" + (f" · {category.label}" if category else ""))
        _el(feed, "subtitle", "Instant alerts for critical and actively exploited vulnerabilities; "
                              "a 12-hour roundup for the rest.")
        _el(feed, "updated", _ts(entries[0].published if entries else self.ctx.now))
        author = _el(feed, "author")
        _el(author, "name", "Omnibrief")
        _el(author, "uri", base)
        _el(feed, "link", href=f"{base}{rel}", rel="self", type="application/atom+xml")
        _el(feed, "link", href=f"{base}{page_path(category.slug if category else None)}", rel="alternate",
            type="text/html")
        _el(feed, "rights", short_rights(topic.source_types()))
        _el(feed, "generator", "Omnibrief")
        _el(feed, "icon", f"{base}favicon.svg")

        for e in entries:
            entry = _el(feed, "entry")
            _el(entry, "id", e.id)
            _el(entry, "title", e.title)
            _el(entry, "published", _ts(e.published))
            _el(entry, "updated", _ts(e.published))
            _el(entry, "link", href=e.alternate, rel="alternate", type="text/html")
            for url in e.related:
                _el(entry, "link", href=url, rel="related")
            _el(entry, "content", e.html, type="html")

        tree = ET.ElementTree(feed)
        ET.indent(tree, space="  ")
        out = self.ctx.site_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        tree.write(out, encoding="utf-8", xml_declaration=True)


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _el(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = text
    return el
