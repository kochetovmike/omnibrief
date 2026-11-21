from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from omnibrief.engine.config import SourceSpec
from omnibrief.engine.items import Item


@dataclass
class SourceCtx:
    client: httpx.Client
    env: Mapping[str, str] = field(default_factory=dict)
    sleep: Callable[[float], None] = time.sleep


class SourceAdapter:
    type: str = ""

    def __init__(self, spec: SourceSpec, ctx: SourceCtx):
        self.spec = spec
        self.ctx = ctx

    @property
    def params(self) -> dict:
        return self.spec.params

    def fetch(self, topic_id: str, since: datetime, now: datetime) -> list[Item]:
        raise NotImplementedError

    def get(self, url: str, **kw) -> httpx.Response:
        resp = self.ctx.client.get(url, **kw)
        resp.raise_for_status()
        return resp
