"""v3 configuration (design-v3): `omnibrief.yaml` + one self-contained `briefs/<id>.yaml` per pipeline.

The loader validates both, resolves secret *references* ({secret: NAME} / {variable: NAME}) to env var
names, and compiles everything into the engine's internal model (Engine/InstanceConfig/TopicSpec —
"topic" internally == "brief"; "channel" internally == a push output). Nothing else in the engine
needs to know about v3's file layout.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from omnibrief.attribution import SOURCE_LICENCES
from omnibrief.engine import expr as expr_mod
from omnibrief.engine.config import (
    CATALOG,
    Cadence,
    ChannelSettings,
    ConfigError,
    Engine,
    InstanceConfig,
    LLMSettings,
    RenderSpec,
    SinkSettings,
    SiteSettings,
    SourceSpec,
    StorageSettings,
    TopicRouting,
    TopicSpec,
    _scan_for_tokens,
    parse_duration,
)

SCHEMA_VERSION = 3
VULNS_SOURCE_TYPES = {"nvd", "cisa_kev", "ghsa"}
GENERIC_SOURCE_TYPES = {"rss", "atom", "json_api", "xml", "geojson"}
OUTPUT_TYPES = {"feed", "telegram"}
PLANNED_OUTPUTS = {"bluesky", "threads"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
PRIVATE_DOMAINS = ("orf.at", "apa.at", "ots.at", "derstandard.at", "krone.at", "diepresse.com", "kurier.at")


class Ref(BaseModel):
    """A secret reference: the NAME of a GitHub Actions secret or variable, never a value."""

    model_config = ConfigDict(extra="forbid")
    secret: str | None = None
    variable: str | None = None
    optional: bool = False

    @model_validator(mode="after")
    def _one(self) -> Ref:
        if (self.secret is None) == (self.variable is None):
            raise ValueError("a reference needs exactly one of `secret:` or `variable:`")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError(f"invalid secret/variable name {self.name!r}")
        return self

    @property
    def name(self) -> str:
        return self.secret or self.variable or ""

    @property
    def kind(self) -> str:
        return "secret" if self.secret else "variable"


# --- briefs -------------------------------------------------------------------------------------

class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every: str = "1h"
    not_before: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] | None = None
    lookback: str | None = None  # default: 2 × every


class LicenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    url: str = Field(pattern=r"^https://")
    reviewed_on: date
    reviewed_by: str
    notes: str = ""


class BriefSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    licence: Literal["builtin"] | LicenceBlock = "builtin"
    secrets: dict[str, Ref] = Field(default_factory=dict)


class Enrich(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class Filters(BaseModel):
    """Known keys + strategy-specific extras (e.g. the `cve` merge's min_cvss), and v2's declarative ones."""

    model_config = ConfigDict(extra="allow")
    include_keywords_any: list[str] = Field(default_factory=list)
    exclude_keywords_any: list[str] = Field(default_factory=list)
    expr: str | None = None
    max_items: int | None = Field(None, ge=1)
    watchlist: Ref | None = None


class Dedupe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = "{key}"
    material_fields: list[str] = Field(default_factory=list)


class Render(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["template", "llm_digest", "hybrid"]
    template: str = "item"
    prompt: str | None = None
    prompt_file: str | None = None
    language: str = "en"
    fallback: Literal["titles_and_links"] = "titles_and_links"
    max_input_items: int = Field(40, ge=1)
    overflow: Literal["list", "drop"] = "list"
    llm_selects: bool = False


class Output(BaseModel):
    """`type` + type-specific params. Outputs with the same `id` in several briefs are ONE channel
    (shared caps/quiet hours); default id is `<brief>-<type>`."""

    model_config = ConfigDict(extra="allow")
    type: str
    id: str | None = Field(None, pattern=r"^[a-z0-9_-]+$")
    connection: str | None = None
    chat_id: Ref | None = None
    url: str | None = Field(None, pattern=r"^https://")
    hashtags: list[str] = Field(default_factory=list)


class Attribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    licence_url: str | None = Field(None, pattern=r"^https://")


class BriefStorage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sinks: list[str] = Field(default_factory=lambda: ["default"])


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[3]
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    description: str = ""
    enabled: bool
    schedule: Schedule
    sources: list[BriefSource] = Field(min_length=1)
    enrich: list[Enrich] = Field(default_factory=list)
    merge: Literal["cve"] | None = None
    filters: Filters = Field(default_factory=Filters)
    dedupe: Dedupe
    render: Render
    outputs: list[Output] = Field(min_length=1)
    storage: BriefStorage = Field(default_factory=BriefStorage)
    attribution: Attribution
    feed_entries: int = Field(30, ge=1, le=200)
    deliver_window_hours: int = Field(24, ge=1)


# --- omnibrief.yaml -----------------------------------------------------------------------------------

class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["telegram", "bluesky", "threads"]
    bot_token: Ref | None = None
    handle: Ref | None = None
    app_password: Ref | None = None
    user_id: Ref | None = None
    access_token: Ref | None = None
    refresh_pat: Ref | None = None


class GlobalLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "gemini"
    model: str = "gemini-flash-lite-latest"
    api_key: Ref = Field(default_factory=lambda: Ref(secret="GEMINI_API_KEY", optional=True))
    max_calls_per_run: int = Field(6, ge=0)
    max_calls_per_day: int = Field(40, ge=0)
    max_output_tokens: int = Field(4000, ge=100)
    temperature: float = 0.2


class GlobalSink(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str


class GlobalStorage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: GlobalSink = Field(default_factory=lambda: GlobalSink(type="git_branch", branch="data"))
    default_sinks: list[str] = Field(default_factory=lambda: ["artifact", "jsonl_git"])
    sinks: dict[str, GlobalSink] = Field(default_factory=lambda: {
        "artifact": GlobalSink(type="artifact"), "jsonl_git": GlobalSink(type="jsonl_git", branch="archive")})


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_time_budget_seconds: int = Field(600, ge=60)
    max_concurrent_briefs: int = Field(4, ge=1, le=16)
    max_http_connections: int = Field(8, ge=1, le=32)


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[3]
    site: SiteSettings
    llm: GlobalLLM = Field(default_factory=GlobalLLM)
    connections: dict[str, Connection] = Field(default_factory=dict)
    storage: GlobalStorage = Field(default_factory=GlobalStorage)
    limits: Limits = Field(default_factory=Limits)


# --- loading ------------------------------------------------------------------------------------------

class Project(Engine):
    """The compiled engine plus v3 extras (briefs, secret references, generated NOTICE)."""

    def __init__(self, root: Path, config: InstanceConfig, topics: dict[str, TopicSpec],
                 global_cfg: GlobalConfig, briefs: dict[str, Brief], refs: list[tuple[str, str, Ref]]):
        super().__init__(root, config, topics)
        self.global_cfg = global_cfg
        self.briefs = briefs
        self.refs = refs  # (brief or "omnibrief.yaml", where, ref)

    def referenced_names(self) -> set[str]:
        return {r.name for _, _, r in self.refs}

    def notice_markdown(self) -> str:
        return notice_markdown(self)


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path.name}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


def _err(where: str, e: ValidationError) -> list[str]:
    return [f"{where}: {'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]


def _specialised_types() -> set[str]:
    from omnibrief.engine.sources import SOURCE_TYPES
    from omnibrief.sources import SOURCE_TYPES as V1_TYPES

    return set(SOURCE_TYPES) | set(V1_TYPES)


def load_project(root: str | Path, enabled_only: bool = False) -> Project:
    root = Path(root)
    problems: list[str] = []
    gpath = root / "omnibrief.yaml"
    if not gpath.is_file():
        raise ConfigError(f"no omnibrief.yaml in {root}")
    graw = _read(gpath)
    problems += [f"credential-like literal in omnibrief.yaml at {w}" for w in _scan_for_tokens(graw, "")]
    try:
        gcfg = GlobalConfig.model_validate(graw)
    except ValidationError as e:
        raise ConfigError("\n".join(problems + _err("omnibrief.yaml", e))) from e

    briefs: dict[str, Brief] = {}
    for path in sorted((root / "briefs").glob("*.yaml")):
        raw = _read(path)
        problems += [f"credential-like literal in briefs/{path.name} at {w}" for w in _scan_for_tokens(raw, "")]
        try:
            b = Brief.model_validate(raw)
        except ValidationError as e:
            problems += _err(f"briefs/{path.name}", e)
            continue
        if b.id != path.stem:
            problems.append(f"briefs/{path.name}: id {b.id!r} must equal the file name")
        if b.id in briefs:
            problems.append(f"duplicate brief id {b.id!r}")
        briefs[b.id] = b
    for sub in ("prompts", "templates", "site"):
        for p in sorted((root / sub).rglob("*")) if (root / sub).is_dir() else []:
            if p.is_file() and p.stat().st_size < 1_000_000 and \
                    _scan_for_tokens(p.read_text(encoding="utf-8", errors="ignore"), ""):
                problems.append(f"credential-like literal in {p.relative_to(root)}")
    if not any(b.enabled for b in briefs.values()):
        problems.append("no enabled brief in briefs/")

    refs: list[tuple[str, str, Ref]] = [("omnibrief.yaml", "llm.api_key", gcfg.llm.api_key)]
    for cname, conn in gcfg.connections.items():
        for field in ("bot_token", "handle", "app_password", "user_id", "access_token"):  # refresh_pat: never here
            if (r := getattr(conn, field)) is not None:
                refs.append(("omnibrief.yaml", f"connections.{cname}.{field}", r))

    specialised = _specialised_types()
    channels: dict[str, ChannelSettings] = {"feed": ChannelSettings(type="feed")}
    topics: dict[str, TopicSpec] = {}
    routing: dict[str, TopicRouting] = {}
    for b in briefs.values():
        where = f"briefs/{b.id}.yaml"
        problems += _check_brief(b, where, root, gcfg, specialised)
        try:
            topics[b.id] = compile_brief(b, root, gcfg)
        except (ValueError, ConfigError) as e:
            problems.append(f"{where}: {e}")
            continue
        publish = []
        for i, out in enumerate(b.outputs):
            if out.type == "feed":
                publish.append("feed")
                continue
            if out.type != "telegram":
                continue
            cid = out.id or f"{b.id}-{out.type}" + (f"-{i}" if sum(o.type == out.type for o in b.outputs) > 1 else "")
            conn = gcfg.connections.get(out.connection or "")
            extra = {k: v for k, v in (out.model_extra or {}).items()}
            settings = ChannelSettings(type="telegram", **extra,
                                       bot_token_env=conn.bot_token.name if conn and conn.bot_token else "",
                                       chat_id_env=out.chat_id.name if out.chat_id else "",
                                       **({"url": out.url} if out.url else {}))
            if cid in channels and channels[cid].model_dump() != settings.model_dump():
                problems.append(f"{where}: output id {cid!r} is reused with different settings")
            channels[cid] = settings
            publish.append(cid)
            if b.enabled and out.chat_id:
                refs.append((b.id, f"outputs[{i}].chat_id", out.chat_id))
        routing[b.id] = TopicRouting(enabled=b.enabled, publish_to=publish)
        if b.enabled:
            refs += [(b.id, f"sources.{s.id}.secrets.{k}", r) for s in b.sources for k, r in s.secrets.items()]
            if b.filters.watchlist:
                refs.append((b.id, "filters.watchlist", b.filters.watchlist))

    sink_types = {"artifact", "jsonl_git"}
    for name, s in gcfg.storage.sinks.items():
        if s.type not in sink_types and s.type not in ("s3", "azure_blob"):
            problems.append(f"omnibrief.yaml: sink {name!r} has unknown type {s.type!r}")
    used = set(gcfg.storage.default_sinks) | {n for b in briefs.values() if b.enabled for n in b.storage.sinks}
    for name in used - {"default"}:
        if name not in gcfg.storage.sinks:
            problems.append(f"sink {name!r} is used but not defined in omnibrief.yaml storage.sinks")
        elif gcfg.storage.sinks[name].type not in sink_types:
            problems.append(f"sink {name!r} ({gcfg.storage.sinks[name].type}) is planned, not in this version yet")
    if problems:
        raise ConfigError("\n".join(problems))

    config = InstanceConfig(
        schema_version=SCHEMA_VERSION, site=gcfg.site,
        llm=LLMSettings(provider=gcfg.llm.provider, model=gcfg.llm.model, api_key_env=gcfg.llm.api_key.name,
                        max_calls_per_run=gcfg.llm.max_calls_per_run, max_calls_per_day=gcfg.llm.max_calls_per_day,
                        max_output_tokens=gcfg.llm.max_output_tokens, temperature=gcfg.llm.temperature),
        topics=routing, channels=channels,
        storage=StorageSettings(primary=SinkSettings(**gcfg.storage.primary.model_dump()),
                                sinks=[SinkSettings(**s.model_dump()) for n, s in gcfg.storage.sinks.items()
                                       if s.type in sink_types]))
    return Project(root, config, topics, gcfg, briefs, refs)


def _check_brief(b: Brief, where: str, root: Path, g: GlobalConfig, specialised: set[str]) -> list[str]:
    p = []
    try:
        if parse_duration(b.schedule.every) < timedelta(hours=1):
            p.append(f"{where}: schedule.every must be at least 1h")
        if b.schedule.lookback:
            parse_duration(b.schedule.lookback)
    except ValueError as e:
        p.append(f"{where}: {e}")
    for s in b.sources:
        if s.type in GENERIC_SOURCE_TYPES:
            if s.licence == "builtin":
                p.append(f"{where}: source {s.id!r} uses generic type {s.type!r} and needs a licence block "
                         "(id, url, reviewed_on, reviewed_by)")
            url = str(s.params.get("url", ""))
            if not url.startswith("https://"):
                p.append(f"{where}: source {s.id!r} needs an https params.url")
            if any(d in url for d in PRIVATE_DOMAINS):
                p.append(f"{where}: source {s.id!r} points at an excluded domain (no reuse licence)")
        elif s.type in specialised or s.type == "epss":
            if s.licence != "builtin":
                p.append(f"{where}: source {s.id!r} is a specialised adapter: use `licence: builtin`")
            for phrase in SOURCE_LICENCES[s.type].required_phrases if s.type in SOURCE_LICENCES else ():
                if phrase not in b.attribution.text:
                    p.append(f"{where}: attribution.text must contain {phrase!r} ({s.type} licence)")
        else:
            p.append(f"{where}: unknown source type {s.type!r}")
    if b.merge == "cve" and not {s.type for s in b.sources} <= VULNS_SOURCE_TYPES:
        p.append(f"{where}: merge: cve works with nvd, cisa_kev and ghsa sources only")
    if b.merge != "cve" and {s.type for s in b.sources} & VULNS_SOURCE_TYPES:
        p.append(f"{where}: nvd/cisa_kev/ghsa sources need `merge: cve`")
    for e in b.enrich:
        if e.type != "epss" or b.merge != "cve":
            p.append(f"{where}: enrich type {e.type!r} is only available with merge: cve (epss)")
    if b.render.mode != "template" and not (b.render.prompt or b.render.prompt_file):
        p.append(f"{where}: render.mode {b.render.mode} needs `prompt` or `prompt_file`")
    if b.render.prompt_file and not (root / b.render.prompt_file).is_file():
        p.append(f"{where}: prompt_file {b.render.prompt_file!r} not found")
    if b.render.mode == "template" and b.merge is None:
        for ext in ("html", "telegram"):
            if not any((d / "templates" / f"{b.render.template}.{ext}.j2").is_file() for d in (root, CATALOG)):
                p.append(f"{where}: template {b.render.template}.{ext}.j2 not found")
    if b.filters.expr:
        try:
            expr_mod.compile_expr(b.filters.expr)
        except ValueError as e:
            p.append(f"{where}: filters.expr: {e}")
    for i, o in enumerate(b.outputs):
        if o.type in PLANNED_OUTPUTS:
            p.append(f"{where}: outputs[{i}] type {o.type!r} is planned, not in this version yet")
        elif o.type not in OUTPUT_TYPES:
            p.append(f"{where}: outputs[{i}] unknown output type {o.type!r}")
        elif o.type == "telegram":
            conn = g.connections.get(o.connection or "")
            if conn is None or conn.type != "telegram":
                p.append(f"{where}: outputs[{i}] needs `connection:` naming a telegram connection in omnibrief.yaml")
            if o.chat_id is None:
                p.append(f"{where}: outputs[{i}] telegram needs `chat_id: {{variable: NAME}}`")
            qh = (o.model_extra or {}).get("quiet_hours")
            if qh is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d", str(qh)):
                p.append(f"{where}: outputs[{i}] quiet_hours must look like 23:00-06:00")
    return p


def compile_brief(b: Brief, root: Path, g: GlobalConfig) -> TopicSpec:
    every = b.schedule.every
    lookback = b.schedule.lookback or f"{int(parse_duration(every).total_seconds() // 3600) * 2}h"
    prompt_text = b.render.prompt or ((root / b.render.prompt_file).read_text(encoding="utf-8")
                                      if b.render.prompt_file else None)
    f = b.filters
    filters: dict[str, Any] = {k: v for k, v in (f.model_extra or {}).items()}
    if f.include_keywords_any:
        filters["keywords_any"] = f.include_keywords_any
    if f.watchlist:
        filters["keywords_env"] = f.watchlist.name
    if f.exclude_keywords_any:
        filters["exclude_keywords_any"] = f.exclude_keywords_any
    if f.expr:
        filters["expr"] = f.expr
    sinks = g.storage.default_sinks if b.storage.sinks == ["default"] else \
        [s for n in b.storage.sinks for s in (g.storage.default_sinks if n == "default" else [n])]
    hashtags = [t for o in b.outputs for t in o.hashtags]
    options: dict[str, Any] = {
        "prompt_text": prompt_text, "attribution": b.attribution.model_dump(), "dedupe_key": b.dedupe.key,
        "max_items": f.max_items, "sinks": [g.storage.sinks[n].type for n in sinks if n in g.storage.sinks],
        "licences": {s.id: (s.licence if s.licence == "builtin" else s.licence.model_dump(mode="json"))
                     for s in b.sources},
    }
    plugin = "generic"
    if b.merge == "cve":
        plugin = "vulns"
        options.update(_vulns_options(b, filters))
    days = b.schedule.days
    return TopicSpec(
        id=b.id, title=b.title, description=b.description,
        kind="alert" if b.render.mode == "template" else "digest", plugin=plugin,
        cadence=Cadence(every=every, window=lookback, not_before=b.schedule.not_before,
                        business_days=bool(days) and set(days) == set(DAYS[:5])),
        render=RenderSpec(mode=b.render.mode, template=b.render.template, prompt=None, hashtags=hashtags,
                          max_input_items=b.render.max_input_items, overflow=b.render.overflow,
                          llm_selects=b.render.llm_selects),
        sources=[] if plugin == "vulns" else [SourceSpec(id=s.id, type=s.type, params=s.params) for s in b.sources],
        filters={} if plugin == "vulns" else filters,
        material_fields=b.dedupe.material_fields,
        attribution=sorted({s.type for s in b.sources} | {e.type for e in b.enrich}),
        feed_entries=b.feed_entries, deliver_window_hours=b.deliver_window_hours,
        options=options | {"days": days},
    )


def _vulns_options(b: Brief, filters: dict) -> dict:
    """The `cve` merge strategy runs the v1 pipeline; build its settings from the brief."""
    discovery = []
    for s in b.sources:
        d = {"id": s.id, "type": s.type, **s.params}
        if "api_key" in s.secrets:
            d["api_key_env"] = s.secrets["api_key"].name
        if "token" in s.secrets:
            d["token_env"] = s.secrets["token"].name
        discovery.append(d)
    v1_filters = {k: filters[k] for k in ("min_cvss", "always_include_if", "epss_percentile_override") if k in filters}
    if b.filters.watchlist:
        v1_filters["watchlist_env"] = b.filters.watchlist.name
    tg = next((o for o in b.outputs if o.type == "telegram"), None)
    return {"defaults": {"timeout_seconds": 20, "user_agent": "Omnibrief/0.3"},
            "entries_per_feed": b.feed_entries, "max_input_items": b.render.max_input_items,
            "filters": v1_filters, "discovery": discovery,
            "enrichment": [{"id": e.id or e.type, "type": e.type, **e.params} for e in b.enrich],
            "telegram": {k: v for k, v in (tg.model_extra or {}).items()} if tg else {}}


# --- NOTICE / attribution -----------------------------------------------------------------------------

def notice_markdown(project: Project) -> str:
    lines = ["# Sources & licences", "",
             "Omnibrief publishes automated alerts and digests built only from sources whose licence or terms "
             "allow reuse. Sources are credited for the **data**; summaries are machine-generated by Omnibrief, "
             "may contain errors, and are not endorsed by any source. *(Generated from the brief files.)*", ""]
    for b in sorted(project.briefs.values(), key=lambda x: x.title):
        if not b.enabled:
            continue
        lines += [f"## {b.title}", "", b.attribution.text]
        if b.attribution.licence_url:
            lines.append(f"Licence: <{b.attribution.licence_url}>")
        lines.append("")
        for s in b.sources:
            if s.licence == "builtin":
                lic = SOURCE_LICENCES.get(s.type)
                lines.append(f"- `{s.id}` — {lic.name}: {lic.licence}" if lic else f"- `{s.id}` ({s.type})")
            else:
                lines.append(f"- `{s.id}` — {s.licence.id}, <{s.licence.url}> (reviewed {s.licence.reviewed_on} "
                             f"by {s.licence.reviewed_by}){': ' + s.licence.notes if s.licence.notes else ''}")
        lines.append("")
    lines += ["Licence readings are a good-faith reading of each provider's published terms, not legal advice.", ""]
    return "\n".join(lines)


def secrets_table(project: Project, env: dict[str, str]) -> list[tuple[str, str, str, str, str]]:
    """(brief, where, kind, name, present|missing|optional-missing) — names only, never values."""
    rows = []
    for brief, where, ref in project.refs:
        present = bool((env.get(ref.name) or "").strip())
        rows.append((brief, where, ref.kind, ref.name,
                     "present" if present else ("optional-missing" if ref.optional else "missing")))
    return rows
