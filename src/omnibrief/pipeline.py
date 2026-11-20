"""The collect pipeline (design §3):

discover (concurrent, isolated per source) → merge → enrich → dedupe → filter → raw archive
→ one LLM call (or fallback) → save digest → static publishers → site → run record.

State lives under ./data (the `data` branch); the workflow commits it after this returns.
Only invalid config or a bug in the core fails a run; sources, the LLM and publishers degrade it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from omnibrief import site as site_mod
from omnibrief.config import Config, TopicConfig
from omnibrief.digest import build_digest
from omnibrief.filters import Filter, dedupe, remember, sort_key, touch
from omnibrief.llm import LLMProvider, build_provider
from omnibrief.merge import merge
from omnibrief.models import PublisherStatus, RunRecord, SourceStatus, Vuln, make_run_id, utcnow
from omnibrief.publishers.base import PublisherContext
from omnibrief.publishers.registry import build_publishers
from omnibrief.sources import Source, SourceContext, Window, build_source
from omnibrief.state import Store

log = logging.getLogger(__name__)

SEEN_RETENTION_DAYS = 90
MAX_CATCHUP = timedelta(days=7)
CATCHUP_OVERLAP = timedelta(hours=1)


@dataclass
class RunOptions:
    data_dir: Path
    site_dir: Path
    raw_dir: Path | None = None
    use_llm: bool = True
    env: Mapping[str, str] = field(default_factory=dict)


def make_client(cfg: Config) -> httpx.Client:
    return httpx.Client(
        timeout=cfg.defaults.timeout_seconds,
        headers={"User-Agent": cfg.defaults.user_agent},
        follow_redirects=True,
    )


def _err(e: Exception) -> str:
    """Short, log-safe error description: type + HTTP status, never URLs or bodies."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


def source_window(src: Source, now: datetime, last_ok: datetime | None) -> Window:
    """Normal lookback, widened back to the last successful fetch (minus overlap) after an outage
    or skipped runs, capped at MAX_CATCHUP so a long outage can't trigger a huge pull."""
    start = now - timedelta(hours=src.lookback_hours())
    if last_ok is not None:
        start = min(start, last_ok - CATCHUP_OVERLAP)
    return Window(start=max(start, now - MAX_CATCHUP), end=now)


def _discover(sources: list[Source], now: datetime, run: RunRecord, watermarks: dict[str, datetime]) -> list[Vuln]:
    discoverers = [s for s in sources if s.discovers]
    found: list[Vuln] = []

    def one(src: Source) -> list[Vuln]:
        return src.discover(source_window(src, now, watermarks.get(src.id)))

    with ThreadPoolExecutor(max_workers=max(1, len(discoverers))) as pool:
        futures = {src.id: pool.submit(one, src) for src in discoverers}
        for sid, fut in futures.items():
            try:
                records = fut.result()
                run.sources[sid] = SourceStatus(status="ok", count=len(records))
                found.extend(records)
                watermarks[sid] = now
            except Exception as e:
                log.warning("source %s: failed (%s)", sid, _err(e))
                run.sources[sid] = SourceStatus(status="failed", error=_err(e))
    return found


def _enrich(sources: list[Source], vulns: list[Vuln], run: RunRecord, after_dedupe: bool) -> None:
    for src in sources:
        if not src.enriches or src.enrich_after_dedupe != after_dedupe:
            continue
        try:
            touched = src.enrich(vulns)
            status = run.sources.get(src.id)
            if status is None:
                run.sources[src.id] = SourceStatus(status="ok", count=touched)
        except Exception as e:
            log.warning("source %s: enrichment failed (%s)", src.id, _err(e))
            run.sources[src.id] = SourceStatus(status="failed", error=_err(e))


def run_topic(
    cfg: Config,
    topic: TopicConfig,
    store: Store,
    opts: RunOptions,
    sources: list[Source],
    provider: LLMProvider | None,
    now: datetime,
) -> RunRecord:
    run = RunRecord(run_id=make_run_id(now), topic=topic.id, started_utc=now)

    watermarks = store.load_watermarks(topic.id)
    discovered = _discover(sources, now, run, watermarks)
    merged = merge(discovered)
    _enrich(sources, merged, run, after_dedupe=False)  # KEV flags feed the re-emit rules

    seen = store.load_seen(topic.id)
    candidates = dedupe(merged, seen, now)
    touch(merged, seen, now)
    _enrich(sources, candidates, run, after_dedupe=True)  # EPSS: only what can still be included
    flt = Filter(topic.filters, opts.env)
    included = sorted((v for v in candidates if flt.reason(v)), key=sort_key)
    run.counts = {
        "discovered": len(discovered),
        "merged": len(merged),
        "new": sum(1 for v in candidates if v.status == "new"),
        "updated": sum(1 for v in candidates if v.status == "updated"),
        "included": len(included),
    }
    log.info("topic %s: %s", topic.id, json.dumps(run.counts))

    if opts.raw_dir is not None and included:
        # Included records only: the full merged set, compared with the feed, would let anyone
        # infer the private watchlist from a public Actions artifact (design §9).
        opts.raw_dir.mkdir(parents=True, exist_ok=True)
        (opts.raw_dir / f"{topic.id}-{run.run_id}.json").write_text(
            json.dumps([v.model_dump(mode="json") for v in included], indent=1), encoding="utf-8"
        )

    if included:
        digest, run.llm = build_digest(cfg, topic, included, provider, run.run_id, now)
        store.save_digest(digest)
        run.digest_written = True
        for v in included:
            remember(v, seen, now)
    else:
        run.llm = "skipped"

    Store.prune_seen(seen, now, SEEN_RETENTION_DAYS)
    store.save_seen(topic.id, seen)
    store.save_watermarks(topic.id, watermarks)

    _publish_static(cfg, topic, store, opts, run)

    degraded = (
        any(s.status != "ok" for s in run.sources.values())
        or run.llm in ("fallback", "not_configured")
        or any(p.status == "failed" for p in run.publishers.values())
    )
    run.status = "degraded" if degraded else "ok"
    run.finished_utc = utcnow()
    store.save_run(run)
    return run


def _publish_static(cfg: Config, topic: TopicConfig, store: Store, opts: RunOptions, run: RunRecord) -> None:
    ctx = PublisherContext(cfg=cfg, topic=topic, site_dir=opts.site_dir, env=opts.env)
    publishers, statuses = build_publishers(ctx)
    run.publishers.update(statuses)
    digests = store.recent_digests(topic.id, cfg.site.entries_per_feed)
    for pub in publishers:
        if pub.kind != "static":
            # Push publishers run later, in `deliver`, after state is pushed (design §3).
            run.publishers[pub.id] = PublisherStatus(status="pending")
            continue
        try:
            result = pub.publish(digests)
            run.publishers[pub.id] = PublisherStatus(status=result.status, reason=result.reason)
        except Exception as e:
            log.warning("publisher %s: failed (%s)", pub.id, _err(e))
            run.publishers[pub.id] = PublisherStatus(status="failed", reason=_err(e))


def render_site(cfg: Config, store: Store, site_dir: Path, runs: list[RunRecord] | None = None) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    digests = {t.id: store.recent_digests(t.id, cfg.site.entries_per_feed) for t in cfg.topics}
    site_mod.write_static(site_dir)
    site_mod.write_index(cfg, site_dir, digests, latest=cfg.site.entries_per_feed)
    site_mod.write_about(cfg, site_dir)
    if runs:
        data = site_mod.health(runs, store.load_health_state())
        site_mod.write_health(site_dir, data)
        store.save_health_state({"last_success_utc": data["last_success_utc"]})


def run(cfg: Config, opts: RunOptions, now: datetime | None = None) -> list[RunRecord]:
    now = now or utcnow()
    store = Store(opts.data_dir)
    opts.site_dir.mkdir(parents=True, exist_ok=True)
    runs = []
    with make_client(cfg) as client:
        provider = None
        if opts.use_llm:
            provider = build_provider(
                cfg.llm.provider,
                cfg.llm.model,
                opts.env.get(cfg.llm.api_key_env, "").strip() or None,
                client,
                max_output_tokens=cfg.llm.max_output_tokens,
                temperature=cfg.llm.temperature,
            )
            if provider is None:
                log.warning("llm: %s not set; digests will be un-summarised", cfg.llm.api_key_env)
        sctx = SourceContext(client=client, env=opts.env)
        for topic in cfg.topics:
            sources = [build_source(s, sctx) for s in topic.enabled_sources()]
            runs.append(run_topic(cfg, topic, store, opts, sources, provider, now))
    render_site(cfg, store, opts.site_dir, runs)
    return runs
