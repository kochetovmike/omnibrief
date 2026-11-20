"""Landing page, topic pages, status page, about page, health.json, OPML, combined feeds.

Theme: `catalog/theme/` (engine default) overlaid by `instance/site/` — any single file can be
overridden without copying the rest; extra instance files (e.g. CNAME, images) are copied as-is.
Static HTML + CSS only: no trackers, no third-party scripts, no cookies (design-v2 §10).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import markdown as md_lib

from omnibrief.engine.config import CATALOG, Engine
from omnibrief.engine.feeds import write_atom, write_opml, write_rss
from omnibrief.engine.items import FeedEntry
from omnibrief.engine.render import make_env

COMBINED_ENTRIES = 100
STATIC_FILES = ("style.css", "favicon.svg")


def _telegram_links(engine: Engine, topic_id: str) -> list[str]:
    return [str(ch.option("url")) for _, ch in engine.channels_for(topic_id, "telegram")
            if str(ch.option("url") or "").startswith("https://t.me/")]


def write_site(engine: Engine, site_dir: Path, *, now: datetime, cards: list[dict], entries: dict[str, list[FeedEntry]],
               health: dict) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    # theme "default": engine theme with per-file overrides from instance/site/; "instance": instance/site/ only
    env = make_env_overlay(engine) if engine.site.theme == "default" else make_env(engine, "site")
    common = {"site": engine.site, "now": now, "root": "", "nav_topics": cards}

    # static assets (instance files win; extra instance files are copied too, e.g. CNAME)
    for name in STATIC_FILES:
        src = engine.instance_dir / "site" / name
        src = src if src.is_file() else CATALOG / "theme" / name
        shutil.copyfile(src, site_dir / name)
    inst_site = engine.instance_dir / "site"
    if inst_site.is_dir():
        for f in inst_site.iterdir():
            if f.is_file() and not f.name.endswith(".j2"):
                shutil.copyfile(f, site_dir / f.name)
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")

    (site_dir / "index.html").write_text(env.get_template("index.html.j2").render(**common, cards=cards),
                                         encoding="utf-8")
    (site_dir / "topics").mkdir(exist_ok=True)
    for card in cards:
        if card["page"].startswith("topics/"):
            html = env.get_template("topic.html.j2").render(**{**common, "root": "../"}, card=card,
                                                            entries=entries.get(card["id"], [])[:20])
            (site_dir / card["page"]).write_text(html, encoding="utf-8")
    (site_dir / "status.html").write_text(env.get_template("status.html.j2").render(**common, health=health),
                                          encoding="utf-8")
    notice = engine.overlay_file("NOTICE.md")
    about = md_lib.markdown(notice.read_text(encoding="utf-8"), output_format="html") if notice else ""
    (site_dir / "about.html").write_text(env.get_template("about.html.j2").render(**common, about=about),
                                         encoding="utf-8")
    (site_dir / "health.json").write_text(json.dumps(health, indent=1, sort_keys=True, default=str) + "\n",
                                          encoding="utf-8")
    _combined(engine, site_dir, entries, now)
    _opml(engine, site_dir, cards, now)


def make_env_overlay(engine: Engine):
    """Default theme with per-file overrides from instance/site/."""
    from jinja2 import ChoiceLoader, FileSystemLoader

    env = make_env(engine, "theme")
    inst = engine.instance_dir / "site"
    if inst.is_dir():
        env.loader = ChoiceLoader([FileSystemLoader(str(inst)), env.loader])
    return env


def _combined(engine: Engine, site_dir: Path, entries: dict[str, list[FeedEntry]], now: datetime) -> None:
    base = engine.site.base
    merged = sorted((e for es in entries.values() for e in es), key=lambda e: e.published, reverse=True)
    merged = merged[:COMBINED_ENTRIES]
    tagged = [e.model_copy(update={"title": f"[{e.topic}] {e.title}"}) for e in merged]
    rights = "Data from the sources listed at " + f"{base}about.html" + ". Machine-generated; may contain errors."
    write_atom(site_dir / "feeds" / "all.atom", feed_id="urn:omnibrief:all", title=f"{engine.site.title} · all topics",
               subtitle=engine.site.tagline, self_url=f"{base}feeds/all.atom", site_url=base, rights=rights,
               entries=tagged, now=now, icon=f"{base}favicon.svg", language=engine.site.language)
    write_rss(site_dir / "feeds" / "all.rss", title=f"{engine.site.title} · all topics",
              description=engine.site.tagline,
              self_url=f"{base}feeds/all.rss", site_url=base, rights=rights, entries=tagged, now=now,
              language=engine.site.language)


def _opml(engine: Engine, site_dir: Path, cards: list[dict], now: datetime) -> None:
    base = engine.site.base
    feeds = [(f"{engine.site.title} · all topics", f"{base}feeds/all.atom", base)]
    feeds += [(f"{engine.site.title} · {c['title']}", f"{base}{c['atom']}", f"{base}{c['page']}") for c in cards]
    for c in cards:
        feeds += [(f"{engine.site.title} · {name}", f"{base}{path}", f"{base}{c['page']}")
                  for name, path in c.get("extra_feeds", [])]
    write_opml(site_dir / "feeds" / "omnibrief.opml", title=engine.site.title, feeds=feeds, now=now)


def card(engine: Engine, spec, plugin, entries: list[FeedEntry], sched: dict, now: datetime, extra: dict) -> dict:
    week = [e for e in entries if e.published >= now - timedelta(days=7)]
    return {
        "id": spec.id, "title": spec.title, "description": spec.description, "kind": spec.kind,
        "page": plugin.page, "atom": plugin.feed_paths()["atom"], "rss": plugin.feed_paths()["rss"],
        "telegram": _telegram_links(engine, spec.id), "status": sched.get("status", "pending"),
        "last_success": sched.get("last_success"), "week": len(week), "latest": entries[:3],
        "cadence": spec.cadence.every, "links": extra.get("links", []), "extra_feeds": extra.get("extra_feeds", []),
    }
