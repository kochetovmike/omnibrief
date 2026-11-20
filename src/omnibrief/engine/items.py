"""Generic item model (design-v2 §5.3) and the small value types shared across the engine."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["info", "low", "medium", "high", "critical"]
SEVERITY_RANK: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def sev_rank(severity: str | None) -> int:
    return SEVERITY_RANK.get(severity or "", -1)


class GeoPoint(BaseModel):
    lat: float
    lon: float
    depth_km: float | None = None


class Item(BaseModel):
    """One record of any topic, normalised. `summary` and `title` are untrusted source text."""

    topic: str
    key: str  # stable dedup key: "usgs:us7000abcd", "TED:796664-2026", "arxiv:2611.26624", …
    source_ids: list[str] = Field(default_factory=list)  # source TYPES (drive attribution)
    title: str
    url: str  # canonical https link to the original
    published: datetime
    updated: datetime | None = None
    summary: str | None = None
    severity: Severity | None = None
    geo: GeoPoint | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    material_hash: str = ""
    status: Literal["new", "updated"] = "new"

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("item url must be https://")
        return v

    def delivery_key(self) -> str:
        """Unique per material version, so an 'updated' item is delivered again."""
        return f"{self.key}#{self.material_hash[:10]}" if self.material_hash else self.key


def material_hash(item: Item, names: list[str]) -> str:
    """Hash of the material fields; a change re-emits the item as 'updated'."""
    payload = {n: item.fields.get(n, getattr(item, n, None)) for n in sorted(names)} if names else {"title": item.title}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class TopicDigest(BaseModel):
    """A digest topic's output for one run (llm_digest or template fallback)."""

    topic: str
    run_id: str
    created_utc: datetime
    title: str
    summarised: bool
    markdown: str
    html: str  # sanitised
    items: list[Item]


class FeedEntry(BaseModel):
    """One entry for Atom/RSS feeds, topic pages and the combined feed."""

    id: str
    topic: str
    title: str
    link: str
    published: datetime
    html: str  # sanitised / escaped HTML content
    related: list[str] = Field(default_factory=list)


class Outbound(BaseModel):
    """One message a topic wants to post on a push channel (rendered for that channel type)."""

    topic: str
    key: str  # unique within the topic; the channel prefixes the topic id
    text: str  # empty text = record as handled without posting (e.g. empty brief window)
    severity: Severity | None = None
    created_utc: datetime
