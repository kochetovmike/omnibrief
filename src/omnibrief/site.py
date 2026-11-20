"""Static site: index.html (the "war room" dashboard), about.html (full attribution from NOTICE.md)
and health.json.

Every value that comes from a source record is untrusted: it is HTML-escaped here, and only
https:// links are rendered. Digest bodies (`Digest.html`) were already sanitised by render.py.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import markdown as md_lib

from omnibrief.config import Config
from omnibrief.models import Digest, DigestItem, RunRecord, severity_rank, utcnow
from omnibrief.publishers.atom import entry_anchor, feed_path

BOARD_DAYS = 14  # the dashboard shows items from digests of the last two weeks
LANE_CAP = 12  # cards per criticality lane; the rest are in the timeline

CSS = """
:root{
  --bg:#05030b;--panel:rgba(16,10,30,.78);--panel-2:rgba(28,16,52,.7);--line:rgba(176,38,255,.22);
  --line-2:rgba(0,240,255,.16);--fg:#e9e4ff;--muted:#8d82b3;
  --accent:#c77dff;--accent-strong:#b026ff;--accent-soft:rgba(176,38,255,.14);--cyan:#00f0ff;
  --exploited:#ff2a6d;--critical:#ff5f1f;--high:#ffb000;--medium:#d4ff3a;--low:#3ad4ff;--unknown:#8a86a8;
  --ok:#00ffa3;--warn:#ffb000;--bad:#ff2a6d;
  --cut:10px;
  --sans:"Chakra Petch","Segoe UI Light","Segoe UI",system-ui,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"Cascadia Mono",Consolas,monospace;
  color-scheme:dark;
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;min-height:100vh;color:var(--fg);font:300 15px/1.55 var(--sans);letter-spacing:.01em;
  background:
    radial-gradient(900px 420px at 88% -8%,rgba(176,38,255,.22),transparent 65%),
    radial-gradient(700px 360px at -5% 30%,rgba(0,240,255,.08),transparent 60%),
    linear-gradient(rgba(176,38,255,.05) 1px,transparent 1px) 0 0/100% 32px,
    linear-gradient(90deg,rgba(176,38,255,.05) 1px,transparent 1px) 0 0/32px 100%,
    var(--bg)}
body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;
  background:repeating-linear-gradient(0deg,rgba(255,255,255,.018) 0 1px,transparent 1px 3px)}
a{color:var(--cyan);text-decoration:none;border-bottom:1px solid rgba(0,240,255,.3)}
a:hover{color:#fff;border-bottom-color:var(--cyan);text-shadow:0 0 8px var(--cyan)}
b,strong{font-weight:500}
.wrap{max-width:1280px;margin:0 auto;padding:0 16px}
.narrow{max-width:46rem}
.mono,.vid{font-family:var(--mono);font-weight:400}
.label{font-size:.68rem;letter-spacing:.22em;text-transform:uppercase;color:var(--muted);font-weight:400}
.panel-cut,.health,.kpi,.lane,.day,.log article,.prose,.quiet{
  clip-path:polygon(0 0,calc(100% - var(--cut)) 0,100% var(--cut),100% 100%,var(--cut) 100%,0 calc(100% - var(--cut)))}

/* top bar */
.top{border-bottom:1px solid var(--line);background:rgba(5,3,11,.72);backdrop-filter:blur(10px);
  position:sticky;top:0;z-index:5;box-shadow:0 1px 0 rgba(0,240,255,.12),0 8px 30px rgba(176,38,255,.08)}
.top .wrap{display:flex;align-items:center;gap:16px;min-height:58px;flex-wrap:wrap}
.brand{font-weight:300;letter-spacing:.42em;font-size:1rem;display:flex;align-items:center;gap:12px;
  color:#fff;text-shadow:0 0 12px rgba(176,38,255,.9)}
.brand .mark{width:11px;height:11px;border:1px solid var(--accent);transform:rotate(45deg);
  box-shadow:0 0 12px var(--accent-strong),inset 0 0 6px var(--accent-strong)}
.brand small{font-family:var(--mono);font-weight:300;letter-spacing:.14em;color:var(--cyan);font-size:.7rem;
  text-shadow:none;opacity:.85}
.top nav{margin-left:auto;display:flex;gap:18px;font-size:.78rem;flex-wrap:wrap;letter-spacing:.14em;
  text-transform:uppercase}
.top nav a{color:var(--muted);border:0}.top nav a:hover{color:var(--accent);text-shadow:0 0 10px var(--accent)}

section{margin:26px 0}
h2{font-size:.74rem;font-weight:400;letter-spacing:.32em;text-transform:uppercase;color:var(--accent);
  margin:0 0 14px;display:flex;align-items:center;gap:12px;text-shadow:0 0 10px rgba(176,38,255,.6)}
h2::before{content:"//";color:var(--cyan);letter-spacing:0;text-shadow:0 0 8px var(--cyan)}
h2::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}
h2 .hint{letter-spacing:.02em;text-transform:none;color:var(--muted);text-shadow:none;font-weight:300}

/* health strip */
.health{display:grid;grid-template-columns:auto repeat(5,minmax(0,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);margin-top:20px;box-shadow:0 0 24px rgba(176,38,255,.08)}
.health>div{background:rgba(10,6,20,.92);padding:13px 15px;min-width:0}
.health .v{font-size:.92rem;margin-top:3px;overflow-wrap:anywhere;font-family:var(--mono);font-weight:300}
.pill{display:inline-flex;align-items:center;gap:9px;font-family:var(--mono);font-weight:400;letter-spacing:.2em;
  font-size:.8rem;padding:6px 14px;border:1px solid currentColor;white-space:nowrap;
  box-shadow:0 0 14px -4px currentColor,inset 0 0 12px -6px currentColor;text-shadow:0 0 8px currentColor;
  clip-path:polygon(0 0,calc(100% - 7px) 0,100% 7px,100% 100%,7px 100%,0 calc(100% - 7px))}
.pill::before{content:"";width:7px;height:7px;background:currentColor;box-shadow:0 0 10px currentColor}
.st-ok{color:var(--ok)}.st-degraded,.st-pending,.st-fallback,.st-not_configured{color:var(--warn)}
.st-failed,.st-stale{color:var(--bad)}.st-disabled,.st-skipped{color:var(--muted)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:5px}
.chip{font-family:var(--mono);font-size:.7rem;font-weight:300;padding:2px 8px;background:transparent;
  border:1px solid color-mix(in srgb,currentColor 45%,transparent)}
.chip b{font-weight:400;color:var(--fg)}
@media (max-width:900px){.health{grid-template-columns:repeat(2,minmax(0,1fr))}
  .health>div:first-child,.health>div:last-child{grid-column:1/-1}}

/* KPI tiles */
.kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}
.kpi{background:var(--panel);border:1px solid color-mix(in srgb,var(--c,var(--accent)) 35%,transparent);
  padding:14px 18px 12px;position:relative;
  box-shadow:inset 0 0 30px -18px var(--c,var(--accent))}
.kpi::before{content:"";position:absolute;left:0;top:0;width:36px;height:2px;background:var(--c,var(--accent));
  box-shadow:0 0 12px var(--c,var(--accent))}
.kpi .n{font-family:var(--mono);font-size:2.6rem;font-weight:200;line-height:1.05;color:var(--c,var(--fg));
  font-variant-numeric:tabular-nums;text-shadow:0 0 18px color-mix(in srgb,var(--c,var(--accent)) 70%,transparent)}
@media (max-width:900px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}

/* lanes */
.lanes{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;align-items:start}
@media (max-width:1100px){.lanes{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:640px){.lanes{grid-template-columns:1fr}}
.lane{background:var(--panel);border:1px solid color-mix(in srgb,var(--c) 30%,transparent)}
.lane>header{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;
  border-bottom:1px solid color-mix(in srgb,var(--c) 30%,transparent);
  background:linear-gradient(90deg,color-mix(in srgb,var(--c) 16%,transparent),transparent 80%)}
.lane>header .t{font-weight:400;letter-spacing:.24em;text-transform:uppercase;font-size:.74rem;color:var(--c);
  text-shadow:0 0 10px var(--c)}
.lane>header .count{font-family:var(--mono);font-weight:300;color:var(--c)}
.lane .cards{padding:10px;display:flex;flex-direction:column;gap:8px}
.lane .empty,.lane .more{color:var(--muted);font-size:.82rem;padding:4px 6px;font-family:var(--mono);font-weight:300}
.card{background:var(--panel-2);border:1px solid rgba(255,255,255,.05);border-left:2px solid var(--c);
  padding:9px 11px;box-shadow:-6px 0 14px -10px var(--c);transition:background .15s,box-shadow .15s}
.card:hover{background:rgba(40,22,72,.8);box-shadow:-8px 0 20px -8px var(--c)}
.card .row1{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.card .vid{font-size:.82rem;color:#fff;border:0}
.card .vid:hover{color:var(--cyan)}
.card .score{font-family:var(--mono);font-weight:300;font-size:.9rem;color:var(--c);text-shadow:0 0 8px var(--c)}
.card .label{letter-spacing:.08em}
.card .title{font-size:.84rem;margin:3px 0 7px;color:#cfc7ef;display:-webkit-box;-webkit-line-clamp:3;
  -webkit-box-orient:vertical;overflow:hidden}
.card .meta{display:flex;gap:7px;flex-wrap:wrap;align-items:center;font-size:.7rem;color:var(--muted);
  font-family:var(--mono);font-weight:300}
.tag{font-family:var(--mono);font-size:.64rem;font-weight:400;letter-spacing:.12em;text-transform:uppercase;
  padding:1px 6px;border:1px solid currentColor;text-shadow:0 0 6px currentColor}
.tag.kev{color:var(--exploited)}.tag.upd{color:var(--cyan)}.tag.epss{color:var(--high)}
.pulse{display:inline-block;width:6px;height:6px;background:var(--exploited);
  box-shadow:0 0 0 0 var(--exploited);animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--exploited) 80%,transparent)}
  70%{box-shadow:0 0 0 7px transparent}100%{box-shadow:0 0 0 0 transparent}}
@media (prefers-reduced-motion:reduce){.pulse{animation:none}}

/* timeline */
.timeline{display:flex;flex-direction:column;gap:10px}
.day{background:var(--panel);border:1px solid var(--line-2)}
.day>header{display:grid;grid-template-columns:150px 1fr auto;gap:16px;align-items:center;padding:10px 14px;
  border-bottom:1px solid var(--line-2)}
.day .d{font-weight:400;letter-spacing:.12em;text-transform:uppercase;font-size:.85rem;color:#fff}
.day .d small{display:block;color:var(--cyan);font-family:var(--mono);font-weight:300;font-size:.72rem;
  letter-spacing:.06em;text-transform:none}
.bar{display:flex;height:4px;overflow:hidden;background:rgba(255,255,255,.06);gap:2px}
.bar span{display:block;height:100%;box-shadow:0 0 10px var(--c)}
.day .tot{font-family:var(--mono);font-weight:300;color:var(--muted);font-size:.8rem}
.rows{list-style:none;margin:0;padding:4px 0}
.rows li{display:grid;grid-template-columns:96px 190px 1fr auto;gap:12px;align-items:baseline;padding:6px 14px;
  border-top:1px solid rgba(255,255,255,.04)}
.rows li:first-child{border-top:0}
.rows li:hover{background:rgba(176,38,255,.07)}
.sev{font-family:var(--mono);font-size:.66rem;font-weight:400;letter-spacing:.14em;text-transform:uppercase;
  color:var(--c);text-shadow:0 0 8px var(--c)}
.rows .vid{font-size:.82rem;color:#fff;border:0}
.rows .vid:hover{color:var(--cyan)}
.rows .what{font-size:.84rem;color:#cfc7ef;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rows .src{font-family:var(--mono);font-weight:300;font-size:.7rem;color:var(--muted);white-space:nowrap}
@media (max-width:760px){.day>header{grid-template-columns:1fr auto}.day .bar{grid-column:1/-1;order:3}
  .rows li{grid-template-columns:80px 1fr;row-gap:2px}.rows .what{grid-column:1/-1;white-space:normal}
  .rows .src{grid-column:1/-1}}

/* digest log */
.log article{background:var(--panel);border:1px solid var(--line);padding:6px 20px 12px;margin-bottom:10px;
  scroll-margin-top:76px}
.log article:target{border-color:var(--cyan);box-shadow:0 0 0 1px var(--cyan),0 0 26px -6px var(--cyan)}
.log h3{font-size:.95rem;font-weight:400;letter-spacing:.06em;margin:12px 0 6px;color:#fff}
.log li{margin:.35rem 0;color:#d6cff2}.log em{color:var(--muted)}
.log strong{font-family:var(--mono);font-weight:400;color:#fff}

.c-exploited{--c:var(--exploited)}.c-critical{--c:var(--critical)}.c-high{--c:var(--high)}
.c-medium{--c:var(--medium)}.c-low{--c:var(--low)}.c-unknown{--c:var(--unknown)}.c-accent{--c:var(--accent)}
.quiet{background:var(--panel);border:1px dashed var(--line);padding:30px;text-align:center;color:var(--muted);
  font-family:var(--mono);font-weight:300;letter-spacing:.06em}
footer.site{border-top:1px solid var(--line);margin-top:40px;padding:18px 0 30px;color:var(--muted);
  font-size:.78rem;letter-spacing:.04em}
.prose{background:var(--panel);border:1px solid var(--line);padding:8px 26px 20px;margin:26px 0}
.prose h1{font-size:1.35rem;font-weight:300;letter-spacing:.2em;text-transform:uppercase;color:#fff;
  text-shadow:0 0 14px rgba(176,38,255,.8)}
.prose li{margin:.5rem 0}
.topic-h{font-size:1rem;font-weight:300;letter-spacing:.3em;text-transform:uppercase;margin:30px 0 0}
::selection{background:var(--accent-strong);color:#fff}
"""

# Thin display fonts (OFL, Google Fonts); falls back to system fonts if blocked.
FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@300;400;500'
    '&amp;family=JetBrains+Mono:wght@200;300;400&amp;display=swap">'
)

# Progressive enhancement only: relative times, and a STALE flag if the collector stopped.
JS = """
(function(){
  var now=Date.now();
  function ago(ms){var m=Math.round(ms/60000);if(m<1)return'just now';if(m<60)return m+' min ago';
    var h=Math.round(m/60);if(h<48)return h+' h ago';return Math.round(h/24)+' days ago';}
  document.querySelectorAll('[data-ts]').forEach(function(el){
    var t=Date.parse(el.getAttribute('data-ts'));if(!isNaN(t))el.setAttribute('title',el.textContent),
    el.textContent=ago(now-t);});
  var pill=document.getElementById('status-pill');
  if(pill){var t=Date.parse(pill.getAttribute('data-last-run'));
    if(!isNaN(t)&&now-t>3*3600*1000){pill.className='pill st-stale';pill.textContent='STALE';}}
})();
"""

LANES = [
    ("exploited", "Actively exploited"),
    ("critical", "Critical"),
    ("high", "High"),
    ("other", "Medium & low"),
]


# --- data shaping -------------------------------------------------------------------------------

@dataclass
class BoardItem:
    item: DigestItem
    run_id: str
    seen: datetime  # when the digest that carried it was published
    issued: date
    issued_kind: str  # "KEV added" | "published" | "first seen"

    @property
    def severity(self) -> str:
        sev = (self.item.severity or "").lower()
        return "medium" if sev == "moderate" else (sev if sev in {"critical", "high", "medium", "low"} else "unknown")

    @property
    def lane(self) -> str:
        if self.item.kev:
            return "exploited"
        return self.severity if self.severity in ("critical", "high") else "other"

    @property
    def color(self) -> str:
        return "exploited" if self.item.kev else self.severity


def _issued(item: DigestItem, seen: datetime) -> tuple[date, str]:
    """Date of issue: when CISA flagged it (for exploited items), else when it was published."""
    if item.kev_date_added:
        try:
            return date.fromisoformat(item.kev_date_added), "KEV added"
        except ValueError:
            pass
    if item.published:
        return item.published.astimezone(UTC).date(), "published"
    return seen.astimezone(UTC).date(), "first seen"


def board_items(digests: list[Digest], now: datetime, days: int = BOARD_DAYS) -> list[BoardItem]:
    """Unique items from recent digests (newest digest wins), sorted by urgency."""
    cutoff = now - timedelta(days=days)
    out: dict[str, BoardItem] = {}
    for d in digests:  # newest first
        if d.created_utc < cutoff:
            continue
        for item in d.items:
            if item.id in out:
                continue
            issued, kind = _issued(item, d.created_utc)
            out[item.id] = BoardItem(item, d.run_id, d.created_utc, issued, kind)
    return sorted(out.values(), key=_urgency)


def _urgency(b: BoardItem) -> tuple:
    i = b.item
    return (not i.kev, -severity_rank(i.severity), -(i.cvss or 0), -(i.epss_percentile or 0), i.id)


# --- small renderers -----------------------------------------------------------------------------

def _https(url: str) -> str | None:
    return url if url.startswith("https://") else None


def _primary_link(b: BoardItem) -> str:
    link = next((u for u in b.item.links if _https(u)), None)
    return escape(link) if link else f"#{entry_anchor(b.run_id)}"


def _fmt_local(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%d %b %Y, %H:%M %Z")


def _ts(value: str | None, tz: str) -> str:
    if not value:
        return '<span class="v">—</span>'
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return f'<span class="v">{escape(value)}</span>'
    return f'<span class="v" data-ts="{escape(dt.isoformat())}">{escape(_fmt_local(dt, tz))}</span>'


def _status_class(status: str) -> str:
    return "st-" + status.split(":", 1)[0].strip().replace(" ", "_")


def _health_html(health: dict | None, tz: str) -> str:
    if not health:
        return ('<div class="health"><div><div class="label">System</div>'
                '<span class="pill st-skipped">NO DATA</span></div></div>')
    status = str(health.get("status", "unknown"))
    sources = health.get("sources") or {
        s: "failed" for s in health.get("degraded_sources", [])
    }
    src_chips = "".join(
        f'<span class="chip {_status_class(str(st))}"><b>{escape(sid)}</b> {escape(str(st))}</span>'
        for sid, st in sorted(sources.items())
    ) or '<span class="chip st-ok">all ok</span>'
    llm = health.get("llm") or {}
    llm_status = ", ".join(sorted({str(v) for v in llm.values()})) or "—"
    pubs = "".join(
        f'<span class="chip {_status_class(str(st))}"><b>{escape(pid)}</b> {escape(str(st))}</span>'
        for pid, st in sorted((health.get("publishers") or {}).items())
    ) or "—"
    return (
        '<div class="health">'
        f'<div><div class="label">System status</div><div class="v">'
        f'<span id="status-pill" class="pill {_status_class(status)}" '
        f'data-last-run="{escape(str(health.get("last_run_utc", "")))}">{escape(status.upper())}</span>'
        f'</div></div>'
        f'<div><div class="label">Last run</div>{_ts(health.get("last_run_utc"), tz)}</div>'
        f'<div><div class="label">Last success</div>{_ts(health.get("last_success_utc"), tz)}</div>'
        f'<div><div class="label">Sources</div><div class="chips">{src_chips}</div></div>'
        f'<div><div class="label">Summariser · last run</div><div class="v">'
        f'<span class="{_status_class(llm_status)}">{escape(llm_status)}</span> · '
        f'{int(health.get("new_records", 0))} new, {int(health.get("included", 0))} published</div></div>'
        f'<div><div class="label">Channels</div><div class="chips">{pubs}</div>'
        f'<div style="margin-top:6px;font-size:.8rem"><a href="health.json">health.json →</a></div></div>'
        "</div>"
    )


def _kpis_html(items: list[BoardItem]) -> str:
    counts = Counter(b.lane for b in items)
    tiles = [
        ("exploited", "Actively exploited", counts["exploited"]),
        ("critical", "Critical", counts["critical"]),
        ("high", "High", counts["high"]),
        ("medium", "Medium & low", counts["other"]),
        ("accent", f"Tracked · {BOARD_DAYS} days", len(items)),
    ]
    return '<div class="kpis">' + "".join(
        f'<div class="kpi c-{c}"><div class="label">{escape(label)}</div><div class="n">{n}</div></div>'
        for c, label, n in tiles
    ) + "</div>"


def _card(b: BoardItem) -> str:
    i = b.item
    score = f"{i.cvss:.1f}" if i.cvss is not None else (b.severity.upper() if b.severity != "unknown" else "")
    title = i.title or i.product or ""
    tags = []
    if i.kev:
        tags.append('<span class="tag kev"><span class="pulse"></span> exploited</span>')
    if i.status == "updated":
        tags.append('<span class="tag upd">updated</span>')
    if i.epss_percentile is not None and i.epss_percentile >= 0.9:
        tags.append(f'<span class="tag epss">EPSS p{i.epss_percentile * 100:.0f}</span>')
    meta = "".join(tags) + (
        f'<span>{escape(b.issued_kind)} {b.issued.strftime("%d %b")}</span>'
        f'<a href="#{entry_anchor(b.run_id)}">digest</a>'
    )
    product = f'<div class="label" style="letter-spacing:.04em">{escape(i.product)}</div>' if i.product else ""
    return (
        f'<div class="card c-{b.color}"><div class="row1">'
        f'<a class="vid" href="{_primary_link(b)}" rel="noopener noreferrer nofollow">{escape(i.id)}</a>'
        f'<span class="score">{escape(score)}</span></div>'
        f'{product}<div class="title">{escape(title)}</div><div class="meta">{meta}</div></div>'
    )


def _lanes_html(items: list[BoardItem]) -> str:
    out = ['<div class="lanes">']
    for key, label in LANES:
        lane = [b for b in items if b.lane == key]
        color = "medium" if key == "other" else key
        cards = "".join(_card(b) for b in lane[:LANE_CAP])
        if not lane:
            cards = '<div class="empty">Nothing here. Good.</div>'
        elif len(lane) > LANE_CAP:
            cards += f'<div class="more">+{len(lane) - LANE_CAP} more in the timeline below</div>'
        out.append(
            f'<div class="lane c-{color}"><header><span class="t">{escape(label)}</span>'
            f'<span class="count">{len(lane)}</span></header><div class="cards">{cards}</div></div>'
        )
    return "".join(out) + "</div>"


def _timeline_html(items: list[BoardItem]) -> str:
    by_day: dict[date, list[BoardItem]] = {}
    for b in items:
        by_day.setdefault(b.issued, []).append(b)
    out = ['<div class="timeline">']
    for day in sorted(by_day, reverse=True):
        group = sorted(by_day[day], key=_urgency)
        counts = Counter(b.color for b in group)
        bar = "".join(
            f'<span class="c-{c}" style="width:{counts[c] / len(group) * 100:.2f}%;background:var(--c)"></span>'
            for c in ("exploited", "critical", "high", "medium", "low", "unknown") if counts[c]
        )
        rows = "".join(
            f'<li class="c-{b.color}"><span class="sev">{"exploited" if b.item.kev else b.severity}</span>'
            f'<a class="vid mono" href="{_primary_link(b)}" rel="noopener noreferrer nofollow">{escape(b.item.id)}</a>'
            f'<span class="what">{escape(b.item.title or b.item.product)}</span>'
            f'<span class="src">{escape(b.issued_kind)} · '
            f'{escape(urlsplit(b.item.links[0]).hostname or "") if b.item.links else ""}</span></li>'
            for b in group
        )
        out.append(
            f'<div class="day"><header><div class="d">{day.strftime("%a %d %b")}'
            f'<small>{day.isoformat()}</small></div><div class="bar">{bar}</div>'
            f'<div class="tot">{len(group)} item{"s" if len(group) != 1 else ""}</div></header>'
            f'<ul class="rows">{rows}</ul></div>'
        )
    return "".join(out) + "</div>"


# --- pages ----------------------------------------------------------------------------------------

def _page(title: str, body: str, head: str = "", script: bool = False) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"theme-color\" content=\"#05030b\">"
        f"<title>{escape(title)}</title>{FONTS}<style>{CSS}</style>{head}</head><body>"
        '<div class="top"><div class="wrap"><div class="brand"><span class="mark"></span>OMNIBRIEF'
        "<small>vulnerability war room</small></div><nav>"
        '<a href="./">Dashboard</a><a href="feeds/vulns.atom">Atom feed</a>'
        '<a href="health.json">health.json</a><a href="about.html">Sources &amp; licences</a>'
        "</nav></div></div>"
        f"{body}"
        '<footer class="site"><div class="wrap">Automated digest of openly licensed vulnerability data · '
        'summaries are machine-generated and may contain errors · '
        '<a href="about.html">Sources &amp; licences</a></div></footer>'
        + (f"<script>{JS}</script>" if script else "")
        + "</body></html>\n"
    )


def write_index(
    cfg: Config,
    site_dir: Path,
    digests_by_topic: dict[str, list[Digest]],
    health: dict | None = None,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    tz = cfg.site.timezone
    parts = ['<main class="wrap">', _health_html(health, tz)]
    feeds = []
    multi = len(cfg.topics) > 1
    for topic in cfg.topics:
        href = feed_path(topic.id)
        feeds.append(f'<link rel="alternate" type="application/atom+xml" title="{escape(topic.title)}" '
                     f'href="{href}">')
        digests = digests_by_topic.get(topic.id, [])
        items = board_items(digests, now)
        if multi:
            parts.append(f'<h1 class="topic-h">{escape(topic.title)}</h1>')
        parts.append(f'<section>{_kpis_html(items)}</section>')
        if items:
            parts.append(
                '<section><h2>By criticality <span class="hint">· most urgent first</span></h2>'
                f"{_lanes_html(items)}</section>"
                '<section><h2>By date of issue <span class="hint">· exploited items dated by when CISA '
                f'flagged them</span></h2>{_timeline_html(items)}</section>'
            )
        else:
            parts.append(f'<div class="quiet">All quiet — nothing published in the last {BOARD_DAYS} days.</div>')
        if digests:
            log = "".join(
                f'<article id="{entry_anchor(d.run_id)}"><h3>{escape(d.title)}'
                f'{"" if d.summarised else " <span class=\"tag upd\">un-summarised</span>"}</h3>{d.html}</article>'
                for d in digests
            )
            parts.append(
                f'<section class="log"><h2>Digest log <span class="hint">· full digests; the '
                f'<a href="{href}">Atom feed</a> carries short alerts and 12-hour roundups</span></h2>{log}</section>'
            )
    parts.append("</main>")
    html = _page("Omnibrief — vulnerability war room", "".join(parts), head="".join(feeds), script=True)
    (site_dir / "index.html").write_text(html, encoding="utf-8")


def write_about(cfg: Config, site_dir: Path) -> None:
    notice = (cfg.root / "NOTICE.md").read_text(encoding="utf-8")
    # NOTICE.md is our own repository file (trusted), rendered as-is.
    body = (f'<main class="wrap narrow"><div class="prose">'
            f'{md_lib.markdown(notice, output_format="html")}</div></main>')
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
        "sources": {sid: s.status for r in runs for sid, s in sorted(r.sources.items())},
        "degraded_sources": sorted({sid for r in runs for sid, s in r.sources.items() if s.status != "ok"}),
        "publishers": publishers,
    }


def write_health(site_dir: Path, data: dict) -> None:
    (site_dir / "health.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def write_static(site_dir: Path) -> None:
    # Serve files as-is (no Jekyll processing on Pages).
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
