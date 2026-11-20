"""Source protocol. A source may discover records, enrich merged records, or both.

Adapters must not swallow their own errors: the pipeline isolates each source with try/except and
records a failure as `degraded` for that source only.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from omnibrief.config import SourceConfig
from omnibrief.models import Vuln


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


@dataclass
class SourceContext:
    client: httpx.Client
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    sleep: Callable[[float], None] = time.sleep


class Source:
    type: str = ""
    discovers: bool = False
    enriches: bool = False
    enrich_after_dedupe: bool = False  # True: enrich only dedupe candidates (saves API calls)

    def __init__(self, cfg: SourceConfig, ctx: SourceContext):
        self.cfg = cfg
        self.ctx = ctx

    @property
    def id(self) -> str:
        return self.cfg.id

    def discover(self, window: Window) -> list[Vuln]:
        return []

    def enrich(self, vulns: list[Vuln]) -> int:
        """Mutate `vulns` in place; return how many records were touched."""
        return 0

    def lookback_hours(self, default: float = 6) -> float:
        return float(self.cfg.option("lookback_hours", default))

    def env_value(self, option: str) -> str | None:
        name = self.cfg.option(option)
        value = self.ctx.env.get(name, "") if name else ""
        return value.strip() or None
