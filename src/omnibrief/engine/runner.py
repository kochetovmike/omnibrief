"""One hourly run (design-v2 §5): PLAN → per due topic (isolated, concurrent) COLLECT…STORE →
sinks → static outputs for every enabled topic → site → schedule + run record.
And `deliver`: push channels, after the workflow has pushed state.

Only invalid config (raised before this module runs) or a failed push/deploy (workflow) fail a
run. Topics, sources, sinks, the LLM and channels each fail on their own.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from omnibrief.engine import site as site_mod
from omnibrief.engine.budget import BudgetedProvider
from omnibrief.engine.channels import TelegramChannel, migrate_v1_telegram_state
from omnibrief.engine.config import Engine
from omnibrief.engine.items import FeedEntry
from omnibrief.engine.planner import plan
from omnibrief.engine.render import Renderer
from omnibrief.engine.sinks import build_sinks
from omnibrief.engine.store import EngineStore
from omnibrief.engine.topics import TopicContext, TopicResult, plugin_for
from omnibrief.llm import build_provider
from omnibrief.models import make_run_id, utcnow

log = logging.getLogger(__name__)
MAX_TOPIC_WORKERS = 4


@dataclass
class RunPaths:
    data_dir: Path
    site_dir: Path
    out_dir: Path | None = None
    archive_dir: Path | None = None


@dataclass
class EngineRun:
    run_id: str
    started_utc: datetime
    due: dict[str, str] = field(default_factory=dict)
    topics: dict[str, dict] = field(default_factory=dict)
    sinks: dict[str, str] = field(default_factory=dict)
    static: dict[str, str] = field(default_factory=dict)
    llm_calls: int = 0
    status: str = "ok"


def _err(e: Exception) -> str:
    return f"HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else type(e).__name__


def make_client(engine: Engine) -> httpx.Client:
    g = getattr(engine, "global_cfg", None)
    return httpx.Client(timeout=30, follow_redirects=True,
                        headers={"User-Agent": f"Omnibrief/0.3 (+{engine.site.base})"},
                        limits=httpx.Limits(max_connections=g.limits.max_http_connections if g else 8))


def run(engine: Engine, paths: RunPaths, env: Mapping[str, str], now: datetime | None = None,
        force: set[str] | None = None, use_llm: bool = True, client: httpx.Client | None = None,
        collect: bool = True) -> EngineRun:
    """collect=False (render-only): rebuild feeds and site from stored state, fetch nothing."""
    now = now or utcnow()
    store = EngineStore(paths.data_dir)
    paths.site_dir.mkdir(parents=True, exist_ok=True)
    rec = EngineRun(run_id=make_run_id(now), started_utc=now)
    schedule = store.load_schedule()
    specs = engine.enabled_topics()
    decisions = plan(specs, schedule, now, engine.site.timezone, force)
    rec.due = {tid: (("due: " if d else "skipped: ") + why) if collect else "render-only"
               for tid, (d, why) in decisions.items()}

    own_client = client is None
    client = client or make_client(engine)
    renderer = Renderer(engine)
    budget_state = store.load_budget()
    provider = None
    llm = engine.config.llm
    inner = build_provider(llm.provider, llm.model, (env.get(llm.api_key_env) or "").strip() or None, client,
                           max_output_tokens=llm.max_output_tokens, temperature=llm.temperature) if use_llm else None
    budget = BudgetedProvider(inner, budget_state, now, llm.max_calls_per_run, llm.max_calls_per_day) if inner else None
    provider = budget

    def ctx_for(spec) -> TopicContext:
        return TopicContext(engine=engine, spec=spec, store=store, client=client, env=dict(env), now=now,
                            run_id=rec.run_id, renderer=renderer, site_dir=paths.site_dir, provider=provider,
                            schedule_entry=schedule.get(spec.id, {}),
                            raw_dir=(paths.out_dir / "raw") if paths.out_dir else None)

    try:
        # --- COLLECT … STORE for due topics, isolated --------------------------------------------
        due_specs = [s for s in specs if decisions[s.id][0]] if collect else []

        def collect(spec) -> TopicResult:
            try:
                return plugin_for(spec).collect(ctx_for(spec))
            except Exception as e:
                log.exception("topic %s failed", spec.id)
                return TopicResult(status="failed", error=_err(e))

        limits = getattr(engine, "global_cfg", None)
        workers = limits.limits.max_concurrent_briefs if limits else MAX_TOPIC_WORKERS
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = dict(zip([s.id for s in due_specs], pool.map(collect, due_specs), strict=True))

        sinks = build_sinks(engine.config.storage.sinks, paths.out_dir, paths.archive_dir)
        for tid, res in results.items():
            entry = schedule.setdefault(tid, {})
            entry["last_attempt"] = now.isoformat()
            entry["status"] = res.status
            entry["error"] = res.error
            if res.status in ("ok", "degraded"):
                entry["last_success"] = now.isoformat()
            rec.topics[tid] = {"status": res.status, "error": res.error, "sources": res.sources,
                               "counts": res.counts, "llm": res.llm}
            log.info("topic %s: %s %s", tid, res.status, res.counts)
            allowed_sinks = engine.topics[tid].options.get("sinks")
            for sink in sinks:
                if allowed_sinks is not None and sink.type not in allowed_sinks:
                    continue
                ok, why = sink.is_enabled()
                if not ok:
                    rec.sinks[sink.id] = f"disabled: {why}"
                    continue
                try:
                    if res.emitted or res.digest:
                        sink.write(tid, rec.run_id, res.emitted, res.digest)
                    rec.sinks.setdefault(sink.id, "ok")
                except Exception as e:
                    log.warning("sink %s failed for %s (%s)", sink.id, tid, _err(e))
                    rec.sinks[sink.id] = f"failed: {_err(e)}"

        # --- static outputs for EVERY enabled topic (the site dir is rebuilt from scratch) -------------
        entries: dict[str, list[FeedEntry]] = {}
        cards = []
        for spec in specs:
            plugin = plugin_for(spec)
            ctx = ctx_for(spec)
            try:
                entries[spec.id] = plugin.feed_entries(ctx)
                plugin.write_static(ctx, entries[spec.id])
                rec.static[spec.id] = "ok"
            except Exception as e:
                log.exception("static outputs for %s failed", spec.id)
                entries.setdefault(spec.id, [])
                rec.static[spec.id] = f"failed: {_err(e)}"
            try:
                cards.append(site_mod.card(engine, spec, plugin, entries[spec.id], schedule.get(spec.id, {}), now,
                                           plugin.card_extra(ctx)))
            except Exception as e:
                log.exception("landing card for %s failed", spec.id)
                rec.static[spec.id] = f"failed: {_err(e)}"
    finally:
        if own_client:
            client.close()

    store.save_schedule(schedule)
    if budget:
        store.save_budget(budget.state)
        rec.llm_calls = budget.run_calls
    bad = [t for t, r in rec.topics.items() if r["status"] in ("failed", "degraded")]
    bad += [s for s, v in rec.sinks.items() if v.startswith("failed")]
    bad += [t for t, v in rec.static.items() if v != "ok"]
    rec.status = "degraded" if bad else "ok"
    health = build_health(engine, store, rec, schedule, now, budget.state if budget else store.load_budget())
    store.save_engine_health(health)
    try:
        site_mod.write_site(engine, paths.site_dir, now=now, cards=cards, entries=entries, health=health)
    except Exception as e:  # e.g. a broken instance template: state is already saved; report, don't crash
        log.exception("site pages failed")
        rec.static["site"] = f"failed: {_err(e)}"
        rec.status = "degraded"
    store.save_engine_run({"run_id": rec.run_id, "started_utc": now.isoformat(), "finished_utc": utcnow().isoformat(),
                           "status": rec.status, "due": rec.due, "topics": rec.topics, "sinks": rec.sinks,
                           "static": rec.static, "llm_calls": rec.llm_calls})
    return rec


def build_health(engine: Engine, store: EngineStore, rec: EngineRun, schedule: dict, now: datetime,
                 budget: dict) -> dict:
    previous = store.load_engine_health()
    channels = {}
    for cid, ch in engine.config.channels.items():
        if ch.type == "feed":
            channels[cid] = {"type": "feed", "status": "ok", "reason": None, "last_attempt_utc": None}
            continue
        st = store.load_channel_state(cid)
        channels[cid] = {"type": ch.type, "status": st.get("status", "pending"), "reason": st.get("reason"),
                         "last_attempt_utc": st.get("last_attempt_utc")}
    return {
        "last_run_utc": now.isoformat(),
        "last_success_utc": now.isoformat() if rec.status != "failed" else previous.get("last_success_utc"),
        "status": rec.status,
        "run_id": rec.run_id,
        "topics": {s.id: {**schedule.get(s.id, {}), "due": rec.due.get(s.id), "static": rec.static.get(s.id)}
                   for s in engine.enabled_topics()},
        "channels": channels,
        "sinks": rec.sinks,
        "llm": {"calls_today": budget.get("calls", 0), "per_day": engine.config.llm.max_calls_per_day,
                "per_run": engine.config.llm.max_calls_per_run, "this_run": rec.llm_calls},
    }


def deliver(engine: Engine, data_dir: Path, env: Mapping[str, str], now: datetime | None = None,
            client: httpx.Client | None = None, test: bool = False,
            sleep: Callable[[float], None] = time.sleep) -> dict[str, str]:
    """Send pending messages on every push channel. Returns {channel id: status}. Never raises."""
    now = now or utcnow()
    store = EngineStore(data_dir)
    statuses: dict[str, str] = {}
    own_client = client is None
    client = client or make_client(engine)
    renderer = Renderer(engine)
    try:
        for cid, ch in engine.config.channels.items():
            if ch.type != "telegram":
                continue
            state: dict = {}
            try:  # a push channel must never fail the workflow or stop the other channels
                channel = TelegramChannel(cid, ch, env, engine.site.base, engine.site.timezone)
                topics = [s for s in engine.enabled_topics() if cid in engine.routing(s.id)]
                enabled, why = channel.is_enabled()
                state = store.load_channel_state(cid)
                if not enabled:
                    state = {**state, "status": "disabled", "reason": why}
                    store.save_channel_state(cid, state)
                    statuses[cid] = "disabled"
                    continue
                v1 = store.load_delivery("telegram") if cid != "telegram" else {}
                if "sent" not in state and any(s.id == "vulns" for s in topics) and v1.get("alerts") is not None:
                    state = {**state, **migrate_v1_telegram_state(v1)}  # no re-posts after the v2 upgrade
                outbound = []
                for spec in topics:
                    ctx = TopicContext(engine=engine, spec=spec, store=store, client=client, env=dict(env), now=now,
                                       run_id=make_run_id(now), renderer=renderer, site_dir=data_dir / ".unused")
                    try:
                        outbound += plugin_for(spec).outbound(ctx, cid, ch)
                    except Exception as e:
                        log.warning("%s: outbound for %s failed (%s)", cid, spec.id, _err(e))
                if test:
                    channel.send_test(client, [s.id for s in topics])
                state = channel.deliver(outbound, state, client, now, sleep)
            except Exception as e:
                log.warning("channel %s failed (%s)", cid, _err(e))
                state = {**state, "status": "failed", "reason": _err(e), "last_attempt_utc": now.isoformat()}
            store.save_channel_state(cid, state)
            statuses[cid] = state.get("status", "ok")
    finally:
        if own_client:
            client.close()
    return statuses
