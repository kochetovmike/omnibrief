"""PLAN stage (design-v2 §5.2): which enabled topics are due in this hourly run.

due = enabled ∧ (forced ∨ never succeeded ∨ cadence elapsed) ∧ not_before satisfied.
Sub-daily cadences compare UTC time buckets of length `every` (an hourly topic is due once per
clock hour, however late GitHub starts the cron); daily-or-longer cadences compare local calendar
days, so "daily at 09:30" means once per day on the first run at/after 09:30.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from omnibrief.engine.config import TopicSpec


def _parse_hhmm(text: str) -> time:
    hh, mm = str(text).split(":")
    return time(int(hh), int(mm))


def is_due(spec: TopicSpec, entry: dict | None, now: datetime, tz: str, forced: bool = False) -> tuple[bool, str]:
    if forced:
        return True, "forced"
    local = now.astimezone(ZoneInfo(tz))
    cad = spec.cadence
    if cad.business_days and local.weekday() >= 5:
        return False, "weekend"
    days = (spec.options or {}).get("days")
    if days and ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][local.weekday()] not in days:
        return False, "not a scheduled day"
    if cad.not_before and local.time() < _parse_hhmm(cad.not_before):
        return False, f"before {cad.not_before}"
    last = (entry or {}).get("last_success")
    if not last:
        return True, "first run"
    last_dt = datetime.fromisoformat(last)
    every = cad.every_td
    if every >= timedelta(days=1):
        days = every.days
        if local.date() >= last_dt.astimezone(ZoneInfo(tz)).date() + timedelta(days=days):
            return True, "cadence"
        return False, "done for this period"
    size = every.total_seconds()
    due = int(now.timestamp() // size) > int(last_dt.timestamp() // size)
    return due, ("cadence" if due else "not yet")


def plan(specs: list[TopicSpec], schedule: dict, now: datetime, tz: str,
         force: set[str] | None = None) -> dict[str, tuple[bool, str]]:
    force = force or set()
    return {s.id: is_due(s, schedule.get(s.id), now, tz, forced=("all" in force or s.id in force)) for s in specs}
