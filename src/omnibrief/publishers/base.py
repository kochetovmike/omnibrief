"""Publisher contract (design §7a).

static: regenerates an artifact from the digest history every run (idempotent by construction).
push:   sends each digest once; delivery is tracked per digest in Digest.deliveries[publisher_id].
Publishers receive channel-neutral Digests and do their own rendering. They never call the LLM.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from omnibrief.config import Config, PublisherConfig, TopicConfig
from omnibrief.models import Digest, utcnow


@dataclass
class PublishResult:
    status: Literal["ok", "failed", "pending"] = "ok"
    reason: str | None = None
    delivered: list[str] = field(default_factory=list)  # run ids delivered (push publishers)


@dataclass
class PublisherContext:
    cfg: Config
    topic: TopicConfig
    site_dir: Path
    env: Mapping[str, str]
    now: datetime = field(default_factory=utcnow)  # run time; static outputs are a function of it


class Publisher:
    kind: Literal["static", "push"] = "static"
    type: str = ""

    def __init__(self, publisher_id: str, pcfg: PublisherConfig, ctx: PublisherContext):
        self.id = publisher_id
        self.pcfg = pcfg
        self.ctx = ctx

    def is_enabled(self) -> tuple[bool, str]:
        """(enabled, reason). Reads env only; never raises."""
        return True, "always on"

    def publish(self, digests: list[Digest]) -> PublishResult:
        raise NotImplementedError
