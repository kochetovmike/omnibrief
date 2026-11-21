"""Telegram channel publisher (push, optional): the Global Critical category only.

What it posts (to TELEGRAM_CHAT_ID, e.g. "@vulnerabilitiesbrief"):
  alert   one message per Global Critical vulnerability (critical tier: exploited or CVSS >= 9 in a
          fundamental technology), sent by the first `deliver` after the run that found it.
  brief   the "Global brief" twice a day (after 08:00 and 20:00 site time): the notable tier —
          fundamental technologies at high severity — from the 12-hour window that just closed.
          Empty windows are skipped (nothing is posted).

Delivery is tracked in data/state/deliveries/<publisher id>.json, committed after every deliver:
at-least-once with idempotent retry. Only alerts from the last `deliver_window_hours` and only a
brief whose window closed within `brief_grace_hours` are ever sent, so enabling the channel or
recovering from an outage never floods it.

Security: the bot token is part of the API URL, so URLs are never logged or put in error strings
(httpx is held at WARNING by the CLI). The bot only posts; it never reads updates.
"""

from __future__ import annotations

import html
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from omnibrief.global_critical import SLUG as GC_SLUG
from omnibrief.global_critical import item_tier
from omnibrief.models import Digest, DigestItem, severity_rank
from omnibrief.publishers.atom import _llm_sentence, page_path, rollup_window
from omnibrief.publishers.base import Publisher, PublishResult
from omnibrief.render import html_to_text

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
KEEP_DAYS = 30  # delivery records older than this are pruned


class TelegramError(Exception):
    def __init__(self, kind: str, reason: str, retry_after: float | None = None, status: int | None = None):
        super().__init__(reason)
        self.kind = kind  # "rate_limited" | "rejected" | "transient"
        self.reason = reason
        self.retry_after = retry_after
        self.status = status  # HTTP status for "rejected": 400 = this message is bad; 401/403 = channel is


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _links(urls: list[str], limit: int = 3) -> str:
    good = [u for u in urls if u.startswith("https://")][:limit]
    return " · ".join(f'<a href="{_e(u)}">{_e(urlsplit(u).hostname or "link")}</a>' for u in good)


def send_message(client: httpx.Client, token: str, chat_id: str, text: str, disable_previews: bool = True) -> int:
    """Bot API sendMessage (HTML). Returns message_id; raises TelegramError (kind = rate_limited |
    rejected | transient). Errors never contain the request URL, which embeds the bot token."""
    try:
        resp = client.post(
            f"{API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": disable_previews}},
            timeout=20,
        )
    except httpx.TransportError as e:
        raise TelegramError("transient", f"transport error: {type(e).__name__}") from None
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code == 200 and data.get("ok"):
        return int(data["result"]["message_id"])
    desc = _clip(str(data.get("description") or f"HTTP {resp.status_code}"), 200)
    if resp.status_code == 429:
        retry = (data.get("parameters") or {}).get("retry_after")
        raise TelegramError("rate_limited", desc, float(retry) if retry is not None else None)
    if resp.status_code >= 500:
        raise TelegramError("transient", desc)
    raise TelegramError("rejected", desc, status=resp.status_code)  # 400 bad chat/markup, 401 token, 403 admin


class TelegramPublisher(Publisher):
    kind = "push"
    type = "telegram"

    # --- configuration -------------------------------------------------------------------------
    def _env(self, option: str, default_name: str) -> str:
        name = self.pcfg.option(option, default_name)
        return (self.ctx.env.get(name) or "").strip()

    @property
    def token(self) -> str:
        return self._env("bot_token_env", "TELEGRAM_BOT_TOKEN")

    @property
    def chat_id(self) -> str:
        return self._env("chat_id_env", "TELEGRAM_CHAT_ID")

    def is_enabled(self) -> tuple[bool, str]:
        if not self.token:
            return False, "TELEGRAM_BOT_TOKEN not set"
        if not self.chat_id:
            return False, "TELEGRAM_CHAT_ID not set"
        return True, "configured"

    def publish(self, digests: list[Digest]) -> PublishResult:  # static step: nothing to do
        return PublishResult(status="pending")

    @property
    def base(self) -> str:
        return self.ctx.cfg.site.base

    # --- rendering -----------------------------------------------------------------------------
    def _footer(self) -> str:
        return (f'<i><a href="{self.base}{page_path(GC_SLUG)}">Global Critical</a> · '
                f'<a href="{self.base}about.html">Sources &amp; licences</a> · machine-generated</i>')

    def render_alert(self, digest: Digest, item: DigestItem) -> str:
        icon, label = ("🚨", "Actively exploited") if item.kev else ("🔴", "Critical")
        if item.status == "updated":
            label = "Update · " + label
        sentence = _llm_sentence(digest, item.id)
        impact = html_to_text(sentence).replace(item.id, "").strip(" —:-") if sentence else item.title
        facts = []
        if item.cvss is not None:
            facts.append(f"CVSS {item.cvss:.1f}")
        if item.epss_percentile is not None and item.epss_percentile >= 0.5:
            facts.append(f"EPSS {item.epss_percentile * 100:.0f}th pct")
        if item.kev:
            facts.append("in CISA KEV" + (f" since {item.kev_date_added}" if item.kev_date_added else ""))
        lines = [f"{icon} <b>{_e(label)}</b> · <code>{_e(item.id)}</code>"]
        if item.product:
            lines.append(f"<b>{_e(_clip(item.product, 100))}</b>")
        if impact:
            lines.append(_e(_clip(impact, 300)))
        if item.changes:
            lines.append("Changed: " + _e(_clip("; ".join(item.changes), 200)))
        if facts:
            lines.append(_e(" · ".join(facts)))
        if links := _links(item.links):
            lines.append(links)
        lines.append(self._footer())
        return "\n".join(lines)

    def render_brief(self, start: datetime, end: datetime, rows: list[DigestItem], alerts_in_window: int) -> str:
        tz = ZoneInfo(self.ctx.cfg.site.timezone)
        span = f"{start.astimezone(tz):%d %b, %H:%M}–{end.astimezone(tz):%H:%M %Z}"
        rows = sorted(rows, key=lambda i: (-(i.cvss or 0), -severity_rank(i.severity), i.id))
        limit = int(self.pcfg.option("brief_max_items", 10))
        max_chars = int(self.pcfg.option("max_message_chars", 4000))
        head = [f"🟣 <b>Global brief</b> · {_e(span)}"]
        if alerts_in_window:
            head.append(f"🔴 {alerts_in_window} critical alert{'s' if alerts_in_window != 1 else ''} "
                        "already sent in this window.")
        head.append(f"{len(rows)} more in fundamental technologies worth knowing:\n")

        def bullet(i: DigestItem) -> str:
            score = f"CVSS {i.cvss:.1f}" if i.cvss is not None else (i.severity or "").upper()
            what = " — ".join(x for x in (_clip(i.product, 60), _clip(i.title, 140)) if x)
            link = _links(i.links, 1)
            return f"• <code>{_e(i.id)}</code> · {_e(what)} · {_e(score)}" + (f" · {link}" if link else "")

        shown = rows[:limit]
        while True:
            more = len(rows) - len(shown)
            tail = ([f'+{more} more → <a href="{self.base}{page_path(GC_SLUG)}">dashboard</a>'] if more else [])
            text = "\n".join([*head, *(bullet(i) for i in shown), *tail, "", self._footer()])
            if len(text) <= max_chars or len(shown) <= 1:
                return text
            shown = shown[:-1]

    # --- sending -------------------------------------------------------------------------------
    def send(self, client: httpx.Client, text: str) -> int:
        """Send one message; returns its message_id. Never puts the URL (contains the token) in errors."""
        return send_message(client, self.token, self.chat_id, text,
                            disable_previews=bool(self.pcfg.option("disable_link_previews", True)))

    def _send_paced(self, client: httpx.Client, text: str, sleep: Callable[[float], None]) -> int:
        try:
            return self.send(client, text)
        except TelegramError as e:
            if e.kind == "rate_limited" and e.retry_after is not None and e.retry_after <= 30:
                sleep(e.retry_after + 0.5)  # honour retry_after once, then give up for this run
                return self.send(client, text)
            raise

    def send_test(self, client: httpx.Client) -> int:
        tz = self.ctx.cfg.site.timezone
        return self.send(client, (
            "✅ <b>Omnibrief is connected.</b>\n"
            "This channel gets an instant alert for every Global Critical vulnerability and a "
            f"<b>Global brief</b> at 08:00 and 20:00 ({_e(tz)}).\n" + self._footer()))

    # --- delivery --------------------------------------------------------------------------------
    def plan(self, digests: list[Digest], state: dict, now: datetime) -> list[tuple[str, str, str]]:
        """What is due: [(kind, key, text)], oldest first. Pure; used by deliver() and tests."""
        alerts_sent = state.get("alerts", {})
        briefs_sent = state.get("briefs", {})
        window = timedelta(hours=float(self.pcfg.option("deliver_window_hours", 24)))
        due: list[tuple[str, str, str]] = []

        for d in sorted(digests, key=lambda d: d.created_utc):
            if d.created_utc < now - window:
                continue
            for item in d.items:
                key = f"{d.run_id}:{item.id}"
                if item_tier(item) == "critical" and key not in alerts_sent:
                    due.append(("alert", key, self.render_alert(d, item)))

        # The Global brief: the most recent closed 12-hour window, if it closed recently.
        tz = self.ctx.cfg.site.timezone
        current_start, _ = rollup_window(now, tz)
        start, end = rollup_window(current_start - timedelta(seconds=1), tz)
        grace = timedelta(hours=float(self.pcfg.option("brief_grace_hours", 6)))
        key = start.isoformat()
        if now - end <= grace and key not in briefs_sent:
            notable: dict[str, DigestItem] = {}
            critical = set()
            for d in digests:
                if not (start <= d.created_utc < end):
                    continue
                for item in d.items:
                    tier = item_tier(item)
                    if tier == "notable":
                        notable.setdefault(item.id, item)
                    elif tier == "critical":
                        critical.add(item.id)
            text = self.render_brief(start, end, list(notable.values()), len(critical)) if notable else ""
            due.append(("brief", key, text))  # empty text => record as skipped, send nothing
        return due

    def deliver(self, digests: list[Digest], state: dict, client: httpx.Client, now: datetime,
                sleep: Callable[[float], None] = time.sleep) -> tuple[PublishResult, dict]:
        state = {"alerts": dict(state.get("alerts", {})), "briefs": dict(state.get("briefs", {}))}
        interval = float(self.pcfg.option("min_interval_seconds", 1.1))
        sent = 0
        result = PublishResult(status="ok")
        for kind, key, text in self.plan(digests, state, now):
            bucket = state["alerts" if kind == "alert" else "briefs"]
            if not text:
                bucket[key] = {"skipped": "empty", "at_utc": now.isoformat()}
                continue
            if sent:
                sleep(interval)
            try:
                message_id = self._send_paced(client, text, sleep)
            except TelegramError as e:
                status = "failed" if e.kind == "rejected" else "pending"
                log.warning("%s: %s %s not sent (%s: %s)", self.id, kind, key, e.kind, e.reason)
                result = PublishResult(status=status, reason=f"{e.kind}: {e.reason}", delivered=result.delivered)
                break  # later messages would hit the same problem; retry next run
            bucket[key] = {"message_id": message_id, "sent_utc": now.isoformat()}
            result.delivered.append(key)
            sent += 1
        _prune(state, now)
        state.update(status=result.status, reason=result.reason, last_attempt_utc=now.isoformat())
        log.info("%s: %d message(s) sent, status %s", self.id, sent, result.status)
        return result, state


def _prune(state: dict, now: datetime) -> None:
    cutoff = now - timedelta(days=KEEP_DAYS)
    for bucket in ("alerts", "briefs"):
        for key, rec in list(state.get(bucket, {}).items()):
            stamp = rec.get("sent_utc") or rec.get("at_utc")
            try:
                if stamp and datetime.fromisoformat(stamp) < cutoff:
                    del state[bucket][key]
            except ValueError:
                continue
