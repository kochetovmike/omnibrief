"""Push channels (design-v2 §9): named Telegram channel instances, routed per topic.

Delivery records: state/deliveries/<channel_id>.json → {"sent": {"<topic>:<key>": {...}}, status…}.
At-least-once with idempotent retry, like v1. Per channel:
  max_posts_per_run   overflow becomes one "+N more" post (those N count as delivered)
  quiet_hours         "23:00-06:00" site time: non-critical messages wait until it ends
Several channels may share one bot token; one topic may go to several channels.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import httpx

from omnibrief.engine.config import ChannelSettings
from omnibrief.engine.items import Outbound
from omnibrief.engine.render import escape
from omnibrief.publishers.telegram import TelegramError, send_message

log = logging.getLogger(__name__)
KEEP_DAYS = 30


def in_quiet_hours(spec: str | None, now: datetime, tz: str) -> bool:
    if not spec:
        return False
    start_s, end_s = [p.strip() for p in spec.split("-")]
    start = dtime(*map(int, start_s.split(":")))
    end = dtime(*map(int, end_s.split(":")))
    t = now.astimezone(ZoneInfo(tz)).time()
    return (start <= t < end) if start < end else (t >= start or t < end)


class TelegramChannel:
    type = "telegram"

    def __init__(self, channel_id: str, settings: ChannelSettings, env: Mapping[str, str], site_base: str, tz: str):
        self.id = channel_id
        self.settings = settings
        self.env = env
        self.site_base = site_base
        self.tz = tz

    def _env(self, option: str, default: str) -> str:
        return (self.env.get(self.settings.option(option, default)) or "").strip()

    @property
    def token(self) -> str:
        return self._env("bot_token_env", "TELEGRAM_BOT_TOKEN")

    @property
    def chat_id(self) -> str:
        return self._env("chat_id_env", "TELEGRAM_CHAT_ID")

    def is_enabled(self) -> tuple[bool, str]:
        if not self.token:
            return False, f"{self.settings.option('bot_token_env', 'TELEGRAM_BOT_TOKEN')} not set"
        if not self.chat_id:
            return False, f"{self.settings.option('chat_id_env', 'TELEGRAM_CHAT_ID')} not set"
        return True, "configured"

    def _send(self, client: httpx.Client, text: str, sleep: Callable[[float], None]) -> int:
        previews = bool(self.settings.option("disable_link_previews", True))
        try:
            return send_message(client, self.token, self.chat_id, text, previews)
        except TelegramError as e:
            if e.kind == "rate_limited" and e.retry_after is not None and e.retry_after <= 30:
                sleep(e.retry_after + 0.5)
                return send_message(client, self.token, self.chat_id, text, previews)
            raise

    def send_test(self, client: httpx.Client, topics: list[str]) -> int:
        return send_message(client, self.token, self.chat_id,
                            f"✅ <b>Omnibrief is connected</b> · channel <code>{escape(self.id)}</code>\n"
                            f"Topics: {escape(', '.join(topics))}\n"
                            f'<i><a href="{self.site_base}about.html">Sources &amp; licences</a></i>')

    def deliver(self, outbound: list[Outbound], state: dict, client: httpx.Client, now: datetime,
                sleep: Callable[[float], None] = time.sleep) -> dict:
        sent_map = dict(state.get("sent", {}))
        interval = float(self.settings.option("min_interval_seconds", 1.1))
        cap = int(self.settings.option("max_posts_per_run", 20))
        quiet = in_quiet_hours(self.settings.option("quiet_hours"), now, self.tz)
        status, reason, posted = "ok", None, 0

        due = [o for o in sorted(outbound, key=lambda o: o.created_utc) if f"{o.topic}:{o.key}" not in sent_map]
        for o in [o for o in due if not o.text]:  # nothing to post (e.g. empty brief window)
            sent_map[f"{o.topic}:{o.key}"] = {"skipped": "empty", "at_utc": now.isoformat()}
        due = [o for o in due if o.text]
        held = [o for o in due if quiet and o.severity != "critical"]
        due = [o for o in due if o not in held]
        overflow: list[Outbound] = []
        if len(due) > cap:  # keep the last slot for one "+N more" post covering the rest
            due, overflow = due[: max(cap - 1, 0)], due[max(cap - 1, 0):]

        for o in due:
            if posted:
                sleep(interval)
            try:
                mid = self._send(client, o.text, sleep)
            except TelegramError as e:
                reason = f"{e.kind}: {e.reason}"
                log.warning("%s: %s:%s not sent (%s)", self.id, o.topic, o.key, reason)
                if e.kind == "rejected" and e.status == 400 and "chat not found" not in e.reason.lower():
                    # this one message is malformed: record it and carry on, don't block the channel
                    sent_map[f"{o.topic}:{o.key}"] = {"failed": e.reason, "at_utc": now.isoformat()}
                    status = "degraded"
                    continue
                status = "failed" if e.kind == "rejected" else "pending"
                break
            sent_map[f"{o.topic}:{o.key}"] = {"message_id": mid, "sent_utc": now.isoformat()}
            posted += 1
        else:
            if overflow:
                topics = sorted({o.topic for o in overflow})
                links = " · ".join(f'<a href="{self.site_base}">{escape(t)}</a>' for t in topics)
                try:
                    if posted:
                        sleep(interval)
                    mid = self._send(client, f"➕ <b>{len(overflow)} more</b> — see the feeds: {links}", sleep)
                    for o in overflow:
                        sent_map[f"{o.topic}:{o.key}"] = {"overflow_of": mid, "sent_utc": now.isoformat()}
                    posted += 1
                except TelegramError as e:
                    status, reason = ("failed" if e.kind == "rejected" else "pending"), f"{e.kind}: {e.reason}"

        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        sent_map = {k: v for k, v in sent_map.items() if (v.get("sent_utc") or v.get("at_utc") or "") >= cutoff}
        log.info("%s: %d message(s) posted, %d held for quiet hours, status %s", self.id, posted, len(held), status)
        return {**state, "sent": sent_map, "status": status, "reason": reason, "last_attempt_utc": now.isoformat(),
                "held": len(held), "posted_last_run": posted}


def migrate_v1_telegram_state(v1_state: dict) -> dict:
    """v1 kept Global Critical deliveries in state/deliveries/telegram.json as {alerts, briefs}."""
    sent = {f"vulns:alert:{k}": v for k, v in (v1_state.get("alerts") or {}).items()}
    sent |= {f"vulns:brief:{k}": v for k, v in (v1_state.get("briefs") or {}).items()}
    return {"sent": sent, "migrated_from": "telegram.json"}
