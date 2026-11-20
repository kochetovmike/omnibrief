"""Instance + topic configuration (design-v2 §3–4): schema, overlay resolution, validation.

Resolution order (§3.3): engine catalogue (`omnibrief/catalog/…`, ships with the package) →
`instance/…` overlay (deep-merged for YAML, file-wins for prompts/templates/site) → environment
variables for secrets and private settings only.

The engine never reads or writes anything under `instance/` except to load it.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator

from omnibrief.attribution import SOURCE_LICENCES

CATALOG = Path(__file__).resolve().parent.parent / "catalog"
SCHEMA_VERSION = 2
CHANNEL_TYPES = {"feed", "telegram"}
PLANNED_CHANNEL_TYPES = {"bluesky", "threads"}  # in the design; not built yet
SINK_TYPES = {"artifact", "jsonl_git"}
PLANNED_SINK_TYPES = {"s3", "azure_blob"}

# Values that look like credentials must never be in YAML (§12). Conservative patterns.
TOKEN_PATTERNS = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),        # Telegram bot token
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),              # Google API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),          # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{30,}\b"),               # common API-key prefix
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                     # AWS access key id
    re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}"),           # Azure connection string
    re.compile(r"\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b"),  # Bluesky app password
    re.compile(r"\b(EAA|THAA|IGAA)[A-Za-z0-9]{40,}\b"),     # Meta / Threads / Instagram tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
    re.compile(r"(?i)(secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_-]{24,}"),  # key = long value
]


class ConfigError(Exception):
    pass


# --- instance ------------------------------------------------------------------------------------

class SiteSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: HttpUrl
    title: str = "Omnibrief"
    tagline: str = "Licence-clean alerts and digests, delivered everywhere."
    language: str = "en"
    timezone: str = "Europe/Vienna"
    theme: Literal["default", "instance"] = "default"

    @property
    def base(self) -> str:
        url = str(self.base_url)
        return url if url.endswith("/") else url + "/"


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "gemini"
    model: str = "gemini-flash-lite-latest"
    api_key_env: str = "GEMINI_API_KEY"
    max_calls_per_run: int = Field(6, ge=0)
    max_calls_per_day: int = Field(40, ge=0)
    max_output_tokens: int = Field(4000, ge=100)
    temperature: float = 0.2


class TopicRouting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    publish_to: list[str] = Field(default_factory=lambda: ["feed"])


class ChannelSettings(BaseModel):
    """`type` plus type-specific options (env var NAMES, never values)."""

    model_config = ConfigDict(extra="allow")
    type: str

    def option(self, name: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(name, default)


class SinkSettings(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str

    def option(self, name: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(name, default)


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: SinkSettings = Field(default_factory=lambda: SinkSettings(type="git_branch", branch="data"))
    sinks: list[SinkSettings] = Field(
        default_factory=lambda: [SinkSettings(type="artifact"), SinkSettings(type="jsonl_git", branch="archive")]
    )


class InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    site: SiteSettings
    llm: LLMSettings = Field(default_factory=LLMSettings)
    topics: dict[str, TopicRouting]
    channels: dict[str, ChannelSettings] = Field(default_factory=lambda: {"feed": ChannelSettings(type="feed")})
    storage: StorageSettings = Field(default_factory=StorageSettings)


# --- topics --------------------------------------------------------------------------------------

def parse_duration(text: str) -> timedelta:
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", str(text))
    if not m:
        raise ValueError(f"bad duration {text!r} (use e.g. 30m, 1h, 3h, 1d, 7d)")
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]


class Cadence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every: str = "1h"
    window: str = "3h"  # lookback; overlaps `every` so a missed run loses nothing
    not_before: str | None = None  # "HH:MM" local (site timezone)
    business_days: bool = False

    @field_validator("not_before")
    @classmethod
    def _hhmm(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("not_before must be HH:MM (24 h)")
        return v

    @field_validator("every", "window")
    @classmethod
    def _duration(cls, v: str) -> str:
        parse_duration(v)
        return v

    @property
    def every_td(self) -> timedelta:
        return parse_duration(self.every)

    @property
    def window_td(self) -> timedelta:
        return parse_duration(self.window)


class RenderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["template", "llm_digest", "hybrid"] = "template"
    template: str = "item"  # templates/<name>.{html,telegram}.j2
    prompt: str | None = None  # prompts/<name>.md (llm_digest)
    hashtags: list[str] = Field(default_factory=list)
    max_input_items: int = Field(40, ge=1)
    overflow: Literal["list", "drop"] = "list"  # items beyond max_input_items: listed as links, or dropped
    llm_selects: bool = False  # the prompt asks the LLM to pick a subset: don't re-append skipped items


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class TopicSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    description: str = ""
    kind: Literal["alert", "digest"]
    plugin: str = "generic"
    cadence: Cadence = Field(default_factory=Cadence)
    render: RenderSpec = Field(default_factory=RenderSpec)
    sources: list[SourceSpec] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    material_fields: list[str] = Field(default_factory=list)
    attribution: list[str] = Field(default_factory=list)  # source types credited for this topic
    feed_entries: int = Field(30, ge=1, le=200)
    deliver_window_hours: int = Field(24, ge=1)
    options: dict[str, Any] = Field(default_factory=dict)  # plugin-specific (vulns: the v1 settings)

    def source_types(self) -> list[str]:
        return sorted({s.type for s in self.sources} | set(self.attribution))


# --- loading ------------------------------------------------------------------------------------

def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def _scan_for_tokens(obj: Any, where: str) -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += _scan_for_tokens(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _scan_for_tokens(v, f"{where}[{i}]")
    elif isinstance(obj, str) and any(p.search(obj) for p in TOKEN_PATTERNS):
        hits.append(where)
    return hits


class Engine:
    """A loaded, validated instance: config + resolved topic specs + overlay lookups."""

    def __init__(self, instance_dir: Path, config: InstanceConfig, topics: dict[str, TopicSpec]):
        self.instance_dir = instance_dir
        self.config = config
        self.topics = topics  # every known topic (catalogue + instance), resolved

    @property
    def site(self) -> SiteSettings:
        return self.config.site

    def enabled_topics(self) -> list[TopicSpec]:
        return [self.topics[tid] for tid, r in self.config.topics.items() if r.enabled and tid in self.topics]

    def routing(self, topic_id: str) -> list[str]:
        return list(self.config.topics[topic_id].publish_to)

    def channels_for(self, topic_id: str, ctype: str) -> list[tuple[str, ChannelSettings]]:
        return [(cid, self.config.channels[cid]) for cid in self.routing(topic_id)
                if self.config.channels[cid].type == ctype]

    def overlay_file(self, *parts: str) -> Path | None:
        """Instance file wins over the engine catalogue (prompts, templates, site assets)."""
        for base in (self.instance_dir, CATALOG):
            p = base.joinpath(*parts)
            if p.is_file():
                return p
        return None

    def prompt_path(self, name: str) -> Path | None:
        return self.overlay_file("prompts", f"{name}.md")


def load_topic_specs(instance_dir: Path) -> dict[str, dict]:
    raw: dict[str, dict] = {}
    for path in sorted((CATALOG / "topics").glob("*.yaml")):
        raw[path.stem] = _read_yaml(path)
    for path in sorted((instance_dir / "topics").glob("*.yaml")) if (instance_dir / "topics").is_dir() else []:
        raw[path.stem] = deep_merge(raw.get(path.stem, {}), _read_yaml(path))
    return raw


def load_engine(instance_dir: str | Path) -> Engine:
    """Load and validate an instance directory. Raises ConfigError with every problem found."""
    from omnibrief.engine.sources import SOURCE_TYPES  # local: avoid import cycle
    from omnibrief.engine.topics import PLUGINS

    instance_dir = Path(instance_dir)
    cfg_path = instance_dir / "omnibrief.yaml"
    if not cfg_path.is_file():
        raise ConfigError(f"no instance config at {cfg_path}")
    raw_cfg = _read_yaml(cfg_path)
    raw_topics = load_topic_specs(instance_dir)

    problems = [f"credential-like value in YAML at {w} — use an env var name instead"
                for w in _scan_for_tokens(raw_cfg, "omnibrief.yaml")]
    for tid, t in raw_topics.items():
        problems += [f"credential-like value in YAML at topics/{tid}.yaml{w}" for w in _scan_for_tokens(t, "")]
    text_suffixes = (".md", ".j2", ".html", ".css", ".txt")
    text_files = [p for p in sorted(instance_dir.rglob("*"))
                  if p.is_file() and p.suffix in text_suffixes and p.stat().st_size < 1_000_000]
    for path in text_files:
        if _scan_for_tokens(path.read_text(encoding="utf-8", errors="ignore"), ""):
            problems.append(f"credential-like value in {path.relative_to(instance_dir)}")

    if raw_cfg.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION} (got {raw_cfg.get('schema_version')!r}); "
                        "see the engine release notes for the migration")
    try:
        config = InstanceConfig.model_validate(raw_cfg)
    except ValidationError as e:
        raise ConfigError("\n".join([*problems, f"omnibrief.yaml: {e}"])) from e

    topics: dict[str, TopicSpec] = {}
    for tid, t in raw_topics.items():
        try:
            spec = TopicSpec.model_validate(t)
        except ValidationError as e:
            problems.append(f"topics/{tid}.yaml: {e}")
            continue
        if spec.id != tid:
            problems.append(f"topics/{tid}.yaml: id {spec.id!r} must match the file name")
        topics[tid] = spec

    for cid, ch in config.channels.items():
        qh = ch.option("quiet_hours")
        if qh is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d", str(qh)):
            problems.append(f"channel {cid!r}: quiet_hours must look like 23:00-06:00")
        if ch.type in PLANNED_CHANNEL_TYPES:
            problems.append(f"channel {cid!r}: type {ch.type!r} is planned but not in this engine version yet")
        elif ch.type not in CHANNEL_TYPES:
            problems.append(f"channel {cid!r}: unknown type {ch.type!r}")
    for sink in config.storage.sinks:
        if sink.type in PLANNED_SINK_TYPES:
            problems.append(f"sink {sink.type!r} is planned but not in this engine version yet")
        elif sink.type not in SINK_TYPES:
            problems.append(f"unknown sink type {sink.type!r}")
    if config.storage.primary.type != "git_branch":
        problems.append("storage.primary must be type git_branch")

    enabled = [tid for tid, r in config.topics.items() if r.enabled]
    if not enabled:
        problems.append("enable at least one topic")
    for tid, routing in config.topics.items():
        if tid not in topics:
            problems.append(f"topic {tid!r} is not in the catalogue or instance/topics/")
            continue
        for cid in routing.publish_to:
            if cid not in config.channels:
                problems.append(f"topic {tid!r} publishes to undefined channel {cid!r}")
        spec = topics[tid]
        if not routing.enabled:
            continue
        if spec.plugin not in PLUGINS:
            problems.append(f"topic {tid!r}: unknown plugin {spec.plugin!r}")
        for st in spec.source_types():
            if st not in SOURCE_LICENCES:
                problems.append(f"topic {tid!r}: source type {st!r} is not on the licence allowlist")
        for src in spec.sources:
            if spec.plugin == "generic" and src.type not in SOURCE_TYPES:
                problems.append(f"topic {tid!r}: source type {src.type!r} has no adapter")
        engine_probe = Engine(instance_dir, config, topics)
        if spec.render.mode in ("llm_digest", "hybrid") and spec.render.prompt and \
                not engine_probe.prompt_path(spec.render.prompt):
            problems.append(f"topic {tid!r}: prompt {spec.render.prompt!r} not found")
        for ext in ("html", "telegram"):
            template = f"{spec.render.template}.{ext}.j2"
            needs_template = spec.plugin == "generic" and spec.kind == "alert"
            if needs_template and not engine_probe.overlay_file("templates", template):
                problems.append(f"topic {tid!r}: template {spec.render.template}.{ext}.j2 not found")

    engine = Engine(instance_dir, config, topics)
    problems += check_notice(engine)
    if problems:
        raise ConfigError("\n".join(problems))
    return engine


def check_notice(engine: Engine) -> list[str]:
    """Every enabled topic's sources must have their attribution phrases in the NOTICE."""
    notice_path = engine.overlay_file("NOTICE.md")
    text = notice_path.read_text(encoding="utf-8") if notice_path else ""
    missing = set()
    for spec in engine.enabled_topics():
        for st in spec.source_types():
            lic = SOURCE_LICENCES.get(st)
            for phrase in lic.required_phrases if lic else ():
                if phrase not in text:
                    missing.add(f"NOTICE.md is missing attribution for {st}: {phrase!r}")
    return sorted(missing)
