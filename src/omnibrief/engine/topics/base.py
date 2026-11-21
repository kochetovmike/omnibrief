"""Topic plugin contract. The engine calls, per enabled topic and isolated from the others:

  collect(ctx)        only when the topic is due: COLLECT → NORMALISE → DEDUPE → FILTER → RENDER → STORE
  feed_entries(ctx)   every run: entries for its feeds, its page and the combined feed
  write_static(ctx)   every run: its own feeds (and any extra pages, e.g. the vulns dashboards)
  outbound(ctx, ch)   in `deliver`: messages for one push channel the topic is routed to
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx

from omnibrief.engine.config import ChannelSettings, Engine, TopicSpec
from omnibrief.engine.items import FeedEntry, Item, Outbound, TopicDigest
from omnibrief.engine.render import Renderer
from omnibrief.engine.store import EngineStore
from omnibrief.llm import LLMProvider

MAX_CATCHUP = timedelta(days=7)


@dataclass
class TopicContext:
    engine: Engine
    spec: TopicSpec
    store: EngineStore
    client: httpx.Client
    env: dict
    now: datetime
    run_id: str
    renderer: Renderer
    site_dir: Path
    provider: LLMProvider | None = None
    schedule_entry: dict = field(default_factory=dict)
    raw_dir: Path | None = None

    def since(self) -> datetime:
        """Lookback: the topic window, widened to the last success (minus 1 h overlap), capped."""
        start = self.now - self.spec.cadence.window_td
        if last := self.schedule_entry.get("last_success"):
            start = min(start, datetime.fromisoformat(last) - timedelta(hours=1))
        return max(start, self.now - max(MAX_CATCHUP, self.spec.cadence.window_td))


@dataclass
class TopicResult:
    status: Literal["ok", "degraded", "failed", "skipped"] = "ok"
    error: str | None = None
    sources: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    emitted: list[Item] = field(default_factory=list)
    digest: TopicDigest | None = None
    llm: str = "n/a"


def run_time(run_id: str) -> datetime:
    return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


class TopicPlugin:
    def __init__(self, spec: TopicSpec):
        self.spec = spec

    @property
    def page(self) -> str:
        return f"topics/{self.spec.id}.html"

    def feed_paths(self) -> dict[str, str]:
        return {"atom": f"feeds/{self.spec.id}.atom", "rss": f"feeds/{self.spec.id}.rss"}

    def collect(self, ctx: TopicContext) -> TopicResult:
        raise NotImplementedError

    def feed_entries(self, ctx: TopicContext) -> list[FeedEntry]:
        raise NotImplementedError

    def write_static(self, ctx: TopicContext, entries: list[FeedEntry]) -> None:
        raise NotImplementedError

    def outbound(self, ctx: TopicContext, channel_id: str, channel: ChannelSettings) -> list[Outbound]:
        return []

    def card_extra(self, ctx: TopicContext) -> dict[str, Any]:
        """Extra links/info for the landing page card."""
        return {}
