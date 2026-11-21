"""`vulns` topic plugin: the whole v1 pipeline (KEV/NVD/GHSA/EPSS merge, watchlist categories,
Global Critical, message-style Atom feeds, the war-room dashboards, Telegram alerts + Global brief),
driven from v2 config. v1 behaviour is reproduced; only the wiring is new.

The v1 code expects its own `Config`; `v1_config_dict()` builds one from the instance (site, llm)
and the topic YAML (`options:` holds the v1 settings). Data layout on the `data` branch is the v1
one, which was already per-topic (`state/seen/vulns.json`, `digests/vulns/…`).
"""

from __future__ import annotations

from typing import Any

from omnibrief import site as v1_site
from omnibrief.attribution import short_rights
from omnibrief.config import Config, PublisherConfig
from omnibrief.engine.config import CATALOG, ChannelSettings, Engine, TopicSpec
from omnibrief.engine.feeds import write_rss
from omnibrief.engine.items import FeedEntry, Item, Outbound
from omnibrief.engine.topics.base import TopicContext, TopicPlugin, TopicResult, run_time
from omnibrief.global_critical import SLUG as GC_SLUG
from omnibrief.models import Digest, DigestItem
from omnibrief.pipeline import HISTORY_DIGESTS, RunOptions, run_topic
from omnibrief.publishers.atom import AtomPublisher
from omnibrief.publishers.base import PublisherContext
from omnibrief.publishers.telegram import TelegramPublisher
from omnibrief.sources import SourceContext, build_source

HUB = "vulns.html"


def v1_config_dict(*, base_url: str, timezone: str, language: str, llm: dict, spec: TopicSpec,
                   prompt_file: str, telegram: dict | None) -> dict:
    o = spec.options
    publishers: dict[str, Any] = {"atom": {"type": "atom"}}
    if telegram is not None:
        publishers["telegram"] = {"type": "telegram", **(o.get("telegram") or {}), **telegram}
    return {
        "defaults": o.get("defaults") or {},
        "site": {"base_url": base_url, "entries_per_feed": o.get("entries_per_feed", 40), "timezone": timezone},
        "llm": {**llm, "max_input_items": o.get("max_input_items", 40)},
        "topics": [{
            "id": spec.id, "title": spec.title, "language": language, "prompt_file": prompt_file,
            "filters": o.get("filters") or {}, "discovery": o.get("discovery") or [],
            "enrichment": o.get("enrichment") or [], "publishers": list(publishers),
        }],
        "publishers": publishers,
    }


def build_v1_config(engine: Engine, spec: TopicSpec) -> Config:
    tg = engine.channels_for(spec.id, "telegram")
    telegram = None
    if tg:
        _, ch = tg[0]  # the v1 code renders for one Telegram target; the channel layer fans out
        telegram = {k: v for k, v in (ch.model_extra or {}).items()
                    if k in ("bot_token_env", "chat_id_env", "url", "disable_link_previews")}
        if "url" in telegram:
            telegram["channel_url"] = telegram.pop("url")
    llm = engine.config.llm
    # Absolute: the v1 code joins prompt_file onto cfg.root (the package catalogue), not the repo root.
    prompt = engine.prompt_path(spec.render.prompt or spec.id)
    if prompt is None:
        raise ValueError(f"prompt for {spec.id!r} not found (expected prompts/{spec.id}.md)")
    prompt = prompt.resolve()
    cfg = Config.model_validate(v1_config_dict(
        base_url=engine.site.base, timezone=engine.site.timezone, language=engine.site.language,
        llm={"provider": llm.provider, "model": llm.model, "api_key_env": llm.api_key_env,
             "max_output_tokens": llm.max_output_tokens, "temperature": llm.temperature},
        spec=spec, prompt_file=str(prompt), telegram=telegram))
    cfg.root = CATALOG  # NOTICE.md (about page, attribution checks) lives in the catalogue
    return cfg


def to_item(d: DigestItem) -> Item:
    link = next((u for u in d.links if u.startswith("https://")), f"https://nvd.nist.gov/vuln/detail/{d.id}")
    sev = (d.severity or "").lower()
    return Item(topic="vulns", key=d.id, source_ids=[], title=d.title or d.id, url=link,
                published=d.published or run_time("19700101T000000Z"),
                severity=sev if sev in ("low", "medium", "high", "critical") else None,
                fields={"cvss": d.cvss, "kev": d.kev, "epss_percentile": d.epss_percentile, "product": d.product},
                status=d.status)


class VulnsTopic(TopicPlugin):
    @property
    def page(self) -> str:
        return HUB

    def _v1(self, ctx: TopicContext) -> tuple[Config, Any]:
        cfg = build_v1_config(ctx.engine, self.spec)
        return cfg, cfg.topic(self.spec.id)

    def _digests(self, ctx: TopicContext, limit: int = HISTORY_DIGESTS) -> list[Digest]:
        return ctx.store.recent_digests(self.spec.id, limit)

    def collect(self, ctx: TopicContext) -> TopicResult:
        cfg, topic = self._v1(ctx)
        raw = ctx.raw_dir / "vulns" if ctx.raw_dir else None
        opts = RunOptions(data_dir=ctx.store.root, site_dir=ctx.site_dir, raw_dir=raw, env=ctx.env)
        sctx = SourceContext(client=ctx.client, env=ctx.env)
        sources = [build_source(s, sctx) for s in topic.enabled_sources()]
        run = run_topic(cfg, topic, ctx.store, opts, sources, ctx.provider, ctx.now)
        health = v1_site.health([run], ctx.store.load_health_state())
        ctx.store.save_health_state(health)
        result = TopicResult(status="degraded" if run.status == "degraded" else "ok", llm=run.llm,
                             counts=dict(run.counts),
                             sources={k: v.status + (f": {v.error}" if v.error else "")
                                      for k, v in run.sources.items()})
        if run.digest_written:
            latest = self._digests(ctx, 1)
            result.emitted = [to_item(i) for i in latest[0].items] if latest else []
        return result

    def _atom(self, ctx: TopicContext, cfg: Config, topic) -> AtomPublisher:
        pctx = PublisherContext(cfg=cfg, topic=topic, site_dir=ctx.site_dir, env=ctx.env, now=ctx.now)
        return AtomPublisher("atom", cfg.publishers["atom"], pctx)

    def feed_entries(self, ctx: TopicContext) -> list[FeedEntry]:
        cfg, topic = self._v1(ctx)
        return [FeedEntry(id=e.id, topic=self.spec.id, title=e.title, link=e.alternate, published=e.published,
                          html=e.html, related=e.related)
                for e in self._atom(ctx, cfg, topic).entries(self._digests(ctx))]

    def write_static(self, ctx: TopicContext, entries: list[FeedEntry]) -> None:
        cfg, topic = self._v1(ctx)
        digests = self._digests(ctx)
        self._atom(ctx, cfg, topic).publish(digests)  # feeds/vulns.atom + one per category + Global Critical
        base = ctx.engine.site.base
        write_rss(ctx.site_dir / "feeds" / "vulns.rss", title=f"{ctx.engine.site.title} · {self.spec.title}",
                  description=self.spec.description or self.spec.title, self_url=f"{base}feeds/vulns.rss",
                  site_url=f"{base}{HUB}", rights=short_rights(topic.source_types()), entries=entries, now=ctx.now,
                  language=ctx.engine.site.language)
        v1_site.write_index(cfg, ctx.site_dir, {self.spec.id: digests}, health=ctx.store.load_health_state() or None,
                            now=ctx.now, env=ctx.env, hub_path=HUB)

    def outbound(self, ctx: TopicContext, channel_id: str, channel: ChannelSettings) -> list[Outbound]:
        """Telegram: v1 behaviour — Global Critical alerts + the twice-daily Global brief."""
        if channel.type != "telegram":
            return []
        cfg, topic = self._v1(ctx)
        extra = {k: v for k, v in (channel.model_extra or {}).items() if k in ("bot_token_env", "chat_id_env")}
        pcfg = PublisherConfig.model_validate({"type": "telegram", **(self.spec.options.get("telegram") or {}),
                                               **extra})
        pub = TelegramPublisher(channel_id, pcfg, PublisherContext(cfg, topic, ctx.site_dir, ctx.env, ctx.now))
        out = []
        for kind, key, text in pub.plan(self._digests(ctx, 24 * 3), {}, ctx.now):
            created = run_time(key.split(":", 1)[0]) if kind == "alert" else ctx.now
            out.append(Outbound(topic=self.spec.id, key=f"{kind}:{key}", text=text,
                                severity="critical" if kind == "alert" else None, created_utc=created))
        return out

    def card_extra(self, ctx: TopicContext) -> dict[str, Any]:
        return {"links": [("Global Critical", f"cat-{GC_SLUG}.html"), ("All vulnerabilities", "general.html")],
                "extra_feeds": [("Global Critical (Atom)", f"feeds/vulns-{GC_SLUG}.atom")]}
