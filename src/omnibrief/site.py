"""Static site pieces: index.html (latest digests), about.html (full attribution from NOTICE.md),
health.json. Everything user-derived inserted here is either escaped or already sanitised."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import markdown as md_lib

from omnibrief.config import Config
from omnibrief.models import Digest, RunRecord
from omnibrief.publishers.atom import entry_anchor, feed_path

CSS = """
:root{--bg:#fff;--fg:#1b1b1b;--muted:#5d5d5d;--accent:#0b57d0;--line:#e3e3e3}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#e8e8e8;--muted:#a0a0a0;--accent:#8ab4f8;--line:#2c2c2c}}
body{background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;
max-width:46rem;margin:0 auto;padding:1.5rem 1rem}
a{color:var(--accent)}h1{font-size:1.6rem}h2{font-size:1.2rem;margin-top:2.2rem}
article{border-top:1px solid var(--line);padding-top:.5rem}.muted,footer{color:var(--muted);font-size:.9rem}
code{font-size:.9em}li{margin:.35rem 0}
"""


def _page(title: str, body: str, head: str = "") -> str:
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{CSS}</style>{head}</head>"
        f"<body>{body}<footer><p><a href=\"about.html\">Sources &amp; licences</a></p></footer>"
        "</body></html>\n"
    )


def write_index(cfg: Config, site_dir: Path, digests_by_topic: dict[str, list[Digest]], latest: int = 10) -> None:
    parts = ["<h1>Omnibrief</h1><p>Openly licensed vulnerability data, merged, filtered and "
             "summarised every three hours.</p>"]
    feeds = []
    for topic in cfg.topics:
        href = feed_path(topic.id)
        feeds.append(f'<link rel="alternate" type="application/atom+xml" title="{escape(topic.title)}" '
                     f'href="{href}">')
        parts.append(f'<p>Subscribe: <a href="{href}">{escape(topic.title)} (Atom)</a></p>')
        digests = digests_by_topic.get(topic.id, [])[:latest]
        if not digests:
            parts.append('<p class="muted">No digests yet.</p>')
        for d in digests:
            parts.append(
                f'<article id="{entry_anchor(d.run_id)}"><h2>{escape(d.title)}</h2>{d.html}</article>'
            )
    html = _page("Omnibrief", "".join(parts), head="".join(feeds))
    (site_dir / "index.html").write_text(html, encoding="utf-8")


def write_about(cfg: Config, site_dir: Path) -> None:
    notice = (cfg.root / "NOTICE.md").read_text(encoding="utf-8")
    # NOTICE.md is our own repository file (trusted), rendered as-is.
    body = '<p><a href="./">← Back</a></p>' + md_lib.markdown(notice, output_format="html")
    html = _page("Omnibrief — Sources & licences", body)
    (site_dir / "about.html").write_text(html, encoding="utf-8")


def health(runs: list[RunRecord], previous: dict) -> dict:
    """Aggregate this run's records (one per topic) into health.json."""
    last = max(r.finished_utc or r.started_utc for r in runs)
    status = "ok"
    if any(r.status == "failed" for r in runs):
        status = "failed"
    elif any(r.status == "degraded" for r in runs):
        status = "degraded"
    publishers: dict[str, str] = {}
    for r in runs:
        for pid, ps in r.publishers.items():
            publishers[pid] = ps.status + (f": {ps.reason}" if ps.status == "failed" and ps.reason else "")
    return {
        "last_run_utc": last.isoformat(),
        "last_success_utc": last.isoformat() if status != "failed" else previous.get("last_success_utc"),
        "status": status,
        "llm": {r.topic: r.llm for r in runs},
        "new_records": sum(r.counts.get("new", 0) + r.counts.get("updated", 0) for r in runs),
        "included": sum(r.counts.get("included", 0) for r in runs),
        "degraded_sources": sorted({sid for r in runs for sid, s in r.sources.items() if s.status != "ok"}),
        "publishers": publishers,
    }


def write_health(site_dir: Path, data: dict) -> None:
    (site_dir / "health.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def write_static(site_dir: Path) -> None:
    # Serve files as-is (no Jekyll processing on Pages).
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
