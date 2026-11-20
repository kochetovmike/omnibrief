"""Load and validate sources.yaml. Fails fast on anything structurally wrong (design §5).

Missing *environment* values (API keys, optional channel credentials) are never config errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from omnibrief.attribution import SOURCE_LICENCES


class ConfigError(Exception):
    pass


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: float = 20
    user_agent: str = "Omnibrief/0.1"


class SiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: HttpUrl
    entries_per_feed: int = Field(50, ge=1, le=500)
    timezone: str = "Europe/Vienna"

    @property
    def base(self) -> str:
        url = str(self.base_url)
        return url if url.endswith("/") else url + "/"


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "gemini"
    model: str
    api_key_env: str = "GEMINI_API_KEY"
    max_input_items: int = Field(40, ge=1)
    max_output_tokens: int = Field(1500, ge=100)
    temperature: float = 0.2


class SourceConfig(BaseModel):
    """Common fields; adapter-specific options are kept as extras and read by the adapter."""

    model_config = ConfigDict(extra="allow")
    id: str
    type: str
    url: str | None = None
    enabled: bool = True

    def option(self, name: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(name, default)


class FilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_cvss: float = 7.0
    watchlist_env: str | None = None
    always_include_if: list[str] = Field(default_factory=lambda: ["kev", "eu_exploited"])
    epss_percentile_override: float | None = 0.99

    @field_validator("always_include_if")
    @classmethod
    def _known_flags(cls, v: list[str]) -> list[str]:
        unknown = set(v) - {"kev", "eu_exploited"}
        if unknown:
            raise ValueError(f"unknown always_include_if flags: {sorted(unknown)}")
        return v


class TopicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    language: str = "en"
    prompt_file: str
    filters: FilterConfig = Field(default_factory=FilterConfig)
    discovery: list[SourceConfig]
    enrichment: list[SourceConfig] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=lambda: ["atom"])

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in [*self.discovery, *self.enrichment] if s.enabled]

    def source_types(self) -> list[str]:
        return sorted({s.type for s in self.enabled_sources()})


class PublisherConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str

    def option(self, name: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(name, default)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defaults: Defaults = Field(default_factory=Defaults)
    site: SiteConfig
    llm: LLMConfig
    topics: list[TopicConfig]
    publishers: dict[str, PublisherConfig] = Field(default_factory=lambda: {"atom": PublisherConfig(type="atom")})
    root: Path = Path(".")  # directory of the config file; set by load_config

    @model_validator(mode="after")
    def _cross_checks(self) -> Config:
        # Local imports: the registries import config types.
        from omnibrief.publishers.registry import PUBLISHER_TYPES
        from omnibrief.sources import SOURCE_TYPES

        topic_ids = [t.id for t in self.topics]
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("duplicate topic ids")
        for topic in self.topics:
            ids = [s.id for s in [*topic.discovery, *topic.enrichment]]
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            if dupes:
                raise ValueError(f"topic {topic.id}: duplicate source ids {dupes}")
            for src in [*topic.discovery, *topic.enrichment]:
                if src.type not in SOURCE_LICENCES:
                    raise ValueError(
                        f"topic {topic.id}: source {src.id!r} has type {src.type!r}, "
                        "which is not on the licence allowlist (design §5)"
                    )
                if src.enabled and src.type not in SOURCE_TYPES:
                    raise ValueError(f"topic {topic.id}: source type {src.type!r} has no adapter yet")
            for pub in topic.publishers:
                if pub not in self.publishers:
                    raise ValueError(f"topic {topic.id}: publisher {pub!r} not defined under publishers:")
        for pid, pub in self.publishers.items():
            if pub.type not in PUBLISHER_TYPES:
                raise ValueError(f"publisher {pid!r}: unknown type {pub.type!r}")
        return self

    def topic(self, topic_id: str) -> TopicConfig:
        for t in self.topics:
            if t.id == topic_id:
                return t
        raise KeyError(topic_id)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    try:
        cfg = Config.model_validate(raw)
    except Exception as e:
        raise ConfigError(str(e)) from e
    cfg.root = path.resolve().parent
    for topic in cfg.topics:
        if not (cfg.root / topic.prompt_file).is_file():
            raise ConfigError(f"topic {topic.id}: prompt file not found: {topic.prompt_file}")
    return cfg


def check_notice(cfg: Config) -> list[str]:
    """Return the attribution phrases NOTICE.md is missing for the enabled sources."""
    notice_path = cfg.root / "NOTICE.md"
    text = notice_path.read_text(encoding="utf-8") if notice_path.is_file() else ""
    missing = []
    for topic in cfg.topics:
        for stype in topic.source_types():
            for phrase in SOURCE_LICENCES[stype].required_phrases:
                if phrase not in text:
                    missing.append(f"{stype}: {phrase!r}")
    return sorted(set(missing))
