# Omnibrief — Design v2

> **Audience:** Claude (Claude Code) as the implementing engineer.
> **Owner:** Mike. Personal side project, not client work.
> **Status:** Draft v2. Supersedes `design.md` (v1). v1 decisions not repeated here still apply unless this document changes them (§14 lists what changed).
> **Principle (unchanged):** €0 to run. GitHub Actions + GitHub Pages + free-tier LLM + free channel APIs. Only sources whose licence or terms explicitly allow reuse (§6). Anything that costs money is optional and off by default.

---

## 0. What v2 adds

1. **Many topics in one hub.** Seven topics at launch (§6), each with its own sources, cadence, filters and rendering.
2. **One scheduled run sweeps everything that is due.** A single hourly workflow runs a planner that decides which topics are due, then collects, stores and publishes them all.
3. **Many storage backends.** One primary state store plus any number of optional archive sinks.
4. **Many channels, routed per topic.** Multiple Atom/RSS feeds, multiple Telegram channels, Bluesky and Threads accounts. Each topic chooses where it goes.
5. **Engine vs instance.** The base repo (**engine**) contains no secrets, no landing page and no schedule that does anything. A **fork** (**instance**) adds config, branding, keys and the live schedule. Upstream updates merge into the fork without conflicts (§3).
6. **Not every topic needs an LLM.** Structured alerts (earthquakes, aurora, weather) render from templates; only digest topics call the LLM. This keeps us far inside the free LLM tier.

```
                  ┌──────────────────────── ONE hourly run ────────────────────────┐
 SOURCES (N)      │  PLAN → COLLECT → NORMALISE → DEDUPE → FILTER → RENDER → STORE   │   CHANNELS (M)
 NVD, KEV, GHSA ─►│   │       (per due topic, isolated)            │        │       │──► Atom + RSS per topic
 TED            ─►│   due topics                                   llm |   primary  │──► Telegram channel(s)
 arXiv          ─►│                                                template  + sinks │──► Bluesky account(s)
 Safety Gate    ─►│                                                                  │──► Threads profile(s)
 NOAA SWPC      ─►│                                                                  │──► landing page + OPML
 USGS           ─►│                                                                  │
 GeoSphere      ─►└──────────────────────────────────────────────────────────────────┘
```

---

## 1. Goals and non-goals

### Goals
- **€0 running cost** at the expected volume (§11).
- **One hub, many topics, many channels.** Adding a topic = one YAML file + (if new) a source adapter. Adding a channel = an entry in the instance config.
- **Isolation everywhere.** A failing source, topic, store sink or channel never blocks the others.
- **Runs with zero optional configuration.** With only the required minimum (§4.3), an instance publishes Atom/RSS and the landing page. Every channel and every non-default sink switches on independently when its settings exist.
- **Fork-friendly.** Engine upgrades merge into instances without touching instance files.

### Non-goals
- No news topics (v1 licence research stands: no reuse rights from Austrian private media; Länder press offices need written permission first).
- No HTML scraping. APIs, JSON/XML exports and feeds only.
- No database server, no always-on compute, no paid cloud by default.
- No interactive bot features (no commands, replies or DMs).

---

## 2. Platform (unchanged from v1 unless noted)

| Need | Service | Notes |
|---|---|---|
| Compute + schedule | GitHub Actions, `ubuntu-latest` | **Hourly** cron now (v1: 3-hourly), offset minute. Free on public repos with standard runners. |
| Hosting | GitHub Pages via `actions/deploy-pages` | Landing page, feeds, OPML, health. Public. |
| Primary state | Git, `data` branch | Dedup, digests, deliveries, run records. |
| LLM | Gemini API free tier (Flash / Flash-Lite) | Pluggable provider. Digest topics only (§8). |
| Channels | Telegram Bot API, Bluesky (AT Protocol), Threads API | All optional, all free. |
| Optional archive sinks | Actions artifacts (default), JSONL-in-git, S3-compatible object storage, Azure Blob | Only artifacts and JSONL-in-git are on by default and free. |

---

## 3. Engine vs instance: the fork model

### 3.1 Two repos, one codebase

| | **Engine** (`<owner>/omnibrief`) | **Instance** (fork, e.g. `<owner>/omnibrief-live`) |
|---|---|---|
| Purpose | Reusable hub code, default topics, default theme | The thing that actually runs and publishes |
| Contains | `src/`, `topics/` (catalogue), `themes/default/`, `instance.example/`, workflows, tests, docs | Everything from the engine **plus** `instance/` |
| Secrets | None, ever | LLM key, channel tokens (Actions secrets) |
| Schedule | Present but **inert**: exits immediately if `instance/omnibrief.yaml` doesn't exist | Active |
| Pages | Optional demo of the default theme, built from `instance.example/` with no channels | The real public site |

### 3.2 The overlay rule (this is what keeps merges conflict-free)
- **The engine never creates, edits or deletes anything under `instance/`.** It isn't gitignored (the fork must commit it); the engine repo simply never contains an `instance/` directory, and `engine-guard.yml` fails CI upstream if one appears.
- **Instances never edit engine-owned paths** (`src/`, `topics/`, `themes/default/`, `.github/workflows/`, `tests/`). All customisation goes through `instance/`:
  - `instance/omnibrief.yaml`: which topics are on, channel routing, sinks, site settings
  - `instance/topics/*.yaml`: overrides or extra topics (merged over `topics/`)
  - `instance/prompts/*.md`: prompt overrides
  - `instance/site/`: landing page templates, CSS, images, `CNAME` (overrides `themes/default/`)
- Result: `git merge upstream/main` (or GitHub's "Sync fork") never conflicts. Instances should sync from **engine release tags**, not arbitrary `main` commits.

### 3.3 Config resolution order
`topics/<id>.yaml` (engine default) → `instance/topics/<id>.yaml` (deep-merge override) → environment variables (secrets/variables only, never behaviour that belongs in YAML). Same for prompts and templates: the instance file wins if present.

### 3.4 Fork realities (document in README)
- A fork of a public repo is public. That's fine: the design assumes public repos, public logs (§12).
- GitHub doesn't run Actions in a new fork until the owner enables them; scheduled workflows need that explicit enable. The README setup checklist starts there.
- Pages, secrets and variables are per repo: the fork configures its own.
- Version contract: the engine's `instance/omnibrief.yaml` schema has a `schema_version`. Engine releases that change it ship a migration note; `validate-config` names the exact fields to change.

### 3.5 Alternative considered (not chosen for now)
Engine as a Python package + reusable workflow (`uses: <owner>/omnibrief/.github/workflows/run.yml@v2`), instance as a tiny config-only repo. Cleaner upgrades, no fork. Not chosen because Mike wants the fork model, and the overlay rule gives most of the same benefit. Revisit if more than ~3 instances exist.

---

## 4. Instance configuration

### 4.1 `instance/omnibrief.yaml`

```yaml
schema_version: 2

site:
  base_url: https://<owner>.github.io/omnibrief-live/
  title: "Omnibrief"
  tagline: "Licence-clean alerts and digests, delivered everywhere."
  language: en
  timezone: Europe/Vienna
  theme: default                 # or "instance" to use instance/site/ only

llm:
  provider: gemini
  model: <exact id from Google AI Studio>
  api_key_env: GEMINI_API_KEY
  max_calls_per_run: 6           # hard cap; extra digest topics fall back to template rendering
  max_calls_per_day: 40          # tracked in state; protects the free-tier quota

topics:                          # which catalogue topics are on, and where they go
  vulns:        { enabled: true, publish_to: [feed, tg_security, bsky_main, threads_main] }
  tenders:      { enabled: true, publish_to: [feed, tg_tenders] }
  arxiv_ai:     { enabled: true, publish_to: [feed, bsky_main, threads_main] }
  safety_gate:  { enabled: true, publish_to: [feed, tg_alerts] }
  space_weather:{ enabled: true, publish_to: [feed, tg_alerts, bsky_main] }
  earthquakes:  { enabled: true, publish_to: [feed, tg_alerts] }
  weather_at:   { enabled: true, publish_to: [feed, tg_alerts] }

channels:                        # named channel instances; each is optional (§9)
  feed:          { type: feed }                     # Atom + RSS per topic; always on
  tg_security:   { type: telegram, bot_token_env: TELEGRAM_BOT_TOKEN, chat_id_env: TG_SECURITY_CHAT_ID }
  tg_tenders:    { type: telegram, bot_token_env: TELEGRAM_BOT_TOKEN, chat_id_env: TG_TENDERS_CHAT_ID }
  tg_alerts:     { type: telegram, bot_token_env: TELEGRAM_BOT_TOKEN, chat_id_env: TG_ALERTS_CHAT_ID,
                   max_posts_per_run: 10, quiet_hours: "23:00-06:00" }
  bsky_main:     { type: bluesky, handle_env: BLUESKY_HANDLE, app_password_env: BLUESKY_APP_PASSWORD }
  threads_main:  { type: threads, user_id_env: THREADS_USER_ID, access_token_env: THREADS_ACCESS_TOKEN,
                   refresh_pat_env: THREADS_REFRESH_PAT }

storage:
  primary: { type: git_branch, branch: data }       # exactly one; required
  sinks:                                            # zero or more; each optional (§7)
    - { type: artifact }                            # default on
    - { type: jsonl_git, branch: archive }          # default on
    # - { type: s3, endpoint_env: S3_ENDPOINT, bucket_env: S3_BUCKET, key_env: S3_KEY, secret_env: S3_SECRET }
    # - { type: azure_blob, connection_string_env: AZURE_BLOB_CONN, container: omnibrief }
```

One Telegram bot can post to several channels; one Bluesky/Threads account can carry several topics. Hashtags per topic (`#CVE`, `#tender`, `#arXiv`, `#aurora`…) keep a shared account readable.

### 4.2 Topic definition (`topics/<id>.yaml`, catalogue in the engine)

```yaml
id: earthquakes
title: "Earthquakes — Europe & Alps"
kind: alert                      # alert | digest (§8)
cadence:
  every: 1h                      # planner granularity is 1h
  window: 3h                     # lookback; overlaps cadence so missed runs lose nothing
render:
  mode: template                 # template | llm_digest | hybrid
  template: earthquake           # templates/<name>.<channel-type>.j2
  hashtags: ["#earthquake"]
sources:
  - { id: usgs, type: usgs_fdsn, params: { minmagnitude: 4.0, bbox: [34, -11, 72, 40] } }
filters:
  min_magnitude: 4.0
  always_include: { country_in: [AT], min_magnitude: 3.0 }
material_fields: [magnitude, place, tsunami_flag]   # change → re-emit as "updated"
attribution: usgs
```

### 4.3 Required minimum for an instance to run
`instance/omnibrief.yaml` with `site.base_url` and at least one enabled topic. **Nothing else.** If `GEMINI_API_KEY` is missing, `llm_digest` topics fall back to template rendering (titles + links), marked as un-summarised. No channel credentials → feeds only.

---

## 5. Run architecture

### 5.1 One workflow, one run

```
.github/workflows/run.yml
  on: schedule (cron "17 * * * *", UTC), workflow_dispatch (inputs: topics=all|<ids>, force=false)
  concurrency: { group: omnibrief-run, cancel-in-progress: false }
  if: hashFiles('instance/omnibrief.yaml') != ''           ← inert in the engine repo
  permissions: contents: write, pages: write, id-token: write

  steps:
   1. checkout main; checkout data branch → ./data
   2. python -m omnibrief run --data ./data --site ./site --out ./out
   3. sinks that need git (jsonl_git) → commit to their branch
   4. commit + push ./data (state durable BEFORE push-style channels)
   5. upload-artifact ./out/raw   (artifact sink)
   6. upload-pages-artifact ./site → deploy-pages
   7. python -m omnibrief deliver --data ./data             (push channels)
   8. commit + push ./data (delivery records)
```

Separate workflows: `ci.yml` (tests, lint, `validate-config` against `instance.example/`), `refresh-threads-token.yml` (weekly, no-op unless configured), `engine-guard.yml` (engine repo only: fails if `instance/` exists).

### 5.2 Inside `omnibrief run`

```
PLAN      read state/schedule.json → due topics = enabled ∧ (now ≥ last_success + cadence.every)
          ∧ (not_before rule satisfied, e.g. TED after 09:30 Europe/Vienna on business days)
          workflow_dispatch can force topics.
COLLECT   per due topic, concurrently (asyncio, bounded: max 4 topics, max 8 HTTP connections total);
          per-source timeout + try/except; per-topic time budget (default 120 s)
NORMALISE source records → Item (§5.3)
DEDUPE    against state/seen/<topic>.json; re-emit only on change of material_fields
FILTER    topic filters (before any LLM call)
RENDER    alert topics: template per item; digest topics: one LLM call per topic (budgeted, §8)
STORE     primary store (digests, items, run record) + every enabled sink (§7)
PUBLISH   static channels: rebuild feeds (Atom + RSS per topic + combined), OPML, landing page, health.json
SCHEDULE  update state/schedule.json (last_attempt, last_success per topic)
```

Each stage of each topic is isolated: an exception marks that topic `failed` in the run record; other topics continue. The run fails only on invalid config or a failed push/deploy.

### 5.3 Generic item model

```python
class Item(BaseModel):
    topic: str
    key: str                     # stable dedup key, e.g. "CVE-2026-1234", "TED:123456-2026", "usgs:us7000abcd"
    source_ids: list[str]        # which sources contributed (drives attribution)
    title: str
    url: HttpUrl                 # canonical link to the original
    published: datetime
    updated: datetime | None
    summary: str | None          # source-provided text (untrusted)
    severity: Literal["info","low","medium","high","critical"] | None
    geo: GeoPoint | None
    fields: dict[str, Any]       # topic-specific (cvss, magnitude, cpv_codes, kp_index, …)
    material_hash: str           # hash of material_fields → drives "updated" re-emits
```

v1's `Vuln` becomes the `vulns` topic's `fields` schema plus its merge logic (`topics/vulns/merge.py`). Topics with special logic ship a small Python module next to their YAML; plain topics are YAML-only.

---

## 6. Topics and sources (launch set)

**Licence allowlist rule from v1 still applies:** `validate-config` rejects any source type not listed here. *(A reading of each provider's published terms, not legal advice. Phase 0 re-verifies every row; see §13.)*

| Topic | Kind / render | Cadence | Source(s) | Access | Licence / terms | Obligations |
|---|---|---|---|---|---|---|
| `vulns` | digest / llm_digest | 3h | CISA KEV, NVD API 2.0, GitHub Advisory DB, EPSS (enrich) | As v1 §5 | As v1 §5 (CC0; public domain + NVD ToU; CC-BY 4.0; attribution requested) | As v1 §5, incl. NVD notice and "never attribute summaries to NVD" |
| `tenders` | digest / llm_digest | daily, `not_before: 09:30` Vienna, business days | **TED** (Tenders Electronic Daily) Search API v3 | Anonymous search API; filter buyer country (e.g. AT, DE, CH) + CPV codes (IT/software/data services) | Editorial content CC BY 4.0 (EU reuse policy) | Credit "© European Union, TED", link to each notice, state modification |
| `arxiv_ai` | digest / llm_digest | daily | **arXiv API** (Atom), categories e.g. cs.AI, cs.LG, cs.CL | arXiv API, polite rate (verify current guidance in Phase 0) | Descriptive metadata (incl. abstracts) CC0 1.0 via the arXiv API ToU | **Never** fetch/summarise PDFs or full text. Use the API, not the RSS feeds. Credit arXiv per its ToU. |
| `safety_gate` | digest / llm_digest | weekly, Friday after report publication (planner retries daily until the new report appears) | **EU Safety Gate** weekly report XML export | Public, no key | Commission reuse policy, CC BY 4.0 (Phase 0: confirm; one secondary source says CC0) | Credit "© European Union, Safety Gate"; link each alert |
| `space_weather` | alert / template | 1h | **NOAA SWPC** JSON (`alerts.json`, planetary K-index) | Public JSON, no key | NOAA data open to the public, usable as desired | Credit NOAA SWPC as courtesy; don't imply endorsement |
| `earthquakes` | alert / template | 1h | **USGS FDSN event service** (GeoJSON), Europe/Alps bounding box | Public, no key | US federal public domain | Credit USGS as courtesy |
| `weather_at` | alert / template (hybrid optional) | 1h | **GeoSphere Austria** warnings API | Public, no key | CC BY 4.0 | Credit "Datenquelle: GeoSphere Austria", link licence, state modification |

Per-topic notes:
- **`vulns`**: unchanged from v1 apart from moving into the topic plugin structure.
- **`tenders`**: the most valuable digest. CPV code list and countries are instance-configurable. Summarise each notice in 1–2 lines (buyer, scope, value if stated, deadline). Deadline field drives an optional "closing soon" reminder (later).
- **`arxiv_ai`**: high volume (hundreds/day). Filter by instance keyword list first, cap at 40 records, then the LLM selects and summarises the top 10. Abstracts are untrusted text (prompt injection rules apply).
- **`safety_gate`**: filter option `notifying_country_in: [AT]` or `all`; group by product category.
- **`space_weather`**: post only on thresholds (e.g. Kp ≥ 6 or NOAA G2+ watch/warning); one post per alert id; never every hour by default.
- **`earthquakes`**: thresholds per region; include "felt in Austria" heuristic later; template includes magnitude, depth, place, time (Vienna), link.
- **`weather_at`**: per-Land grouping; warning level ≥ configurable; updates/cancellations re-emit as "updated"/"lifted".

Deferred (licence unclear, ask first): avalanche.report (no licence found; Swiss SLF is CC BY 4.0 if a CH topic is wanted), E-Control fuel prices (no licence published), Länder press services.

---

## 7. Storage: one primary, many sinks

### 7.1 Contracts

```python
class StateStore(Protocol):          # exactly one: the source of truth
    def load_seen(topic) -> SeenIndex
    def save_seen(topic, SeenIndex)
    def save_digest(topic, Digest) / list_digests(topic, limit)
    def save_run(RunRecord)
    def load_schedule() / save_schedule()
    def record_delivery(digest_or_item_id, channel_id, DeliveryInfo)

class ArchiveSink(Protocol):         # zero or more: write-only replicas
    id: str
    def is_enabled() -> tuple[bool, str]     # reads env; never raises
    def write(topic, items: list[Item], digest: Digest | None, run: RunRecord) -> SinkResult
```

### 7.2 Implementations

| Type | Role | Default | Cost | Notes |
|---|---|---|---|---|
| `git_branch` | **Primary** | ✅ | €0 | v1's `data` branch layout, now per topic: `state/seen/<topic>.json`, `digests/<topic>/…`, `items/<topic>/<yyyy>/<mm>/…`, `runs/…`, `state/schedule.json`, `state/llm_budget.json` |
| `artifact` | Sink | ✅ | €0 | Raw source payloads + normalised items per run; retained per GitHub default |
| `jsonl_git` | Sink | ✅ | €0 | Append-only monthly JSONL per topic on an `archive` branch: a permanent, queryable history (DuckDB reads it directly) |
| `s3` | Sink | off | free tiers exist (e.g. Cloudflare R2, Backblaze B2: verify current limits) | Any S3-compatible endpoint; same JSONL layout |
| `azure_blob` | Sink | off | cents/month (not free) | For Mike's Azure habit; same JSONL layout |

Rules: sinks are written **after** the primary store succeeds; a sink failure marks it `failed` in the run record and never fails the run. Sinks are idempotent (object keys include run id). The primary store is the only thing dedup and delivery logic reads.

Growth control: `items/` in the data branch keeps 90 days; older months live only in `archive` (JSONL) and optional object storage. If the `archive` branch passes ~200 MB, rotate to yearly branches.

---

## 8. Rendering and the LLM budget

| Mode | Used by | LLM calls | Output |
|---|---|---|---|
| `template` | alert topics | 0 | One post per item, Jinja templates per channel type (`templates/<name>.{atom,telegram,shortform}.j2`) |
| `llm_digest` | digest topics | 1 per due topic | One digest per run (v1 behaviour) |
| `hybrid` | optional | ≤ 1 per day | Template posts per item + one daily LLM roundup |

Budget: `max_calls_per_run` and `max_calls_per_day` (tracked in `state/llm_budget.json`). When exhausted, digest topics fall back to template rendering (title, key fields, link), marked "un-summarised". Expected use: vulns 8 + tenders 1 + arxiv 1 + safety gate ≤ 1 ≈ **≤ 11 calls/day**.

All v1 LLM safety rules apply to every topic: inputs are untrusted; no tools; sanitise output; only links from inputs; never send non-public data; per-topic prompts live in `topics/<id>/prompt.md`, overridable in `instance/prompts/`.

---

## 9. Channels

Same contracts as v1 §7a (`Publisher`, `static` vs `push`, `is_enabled()`, per-digest/per-item delivery tracking, resume, isolation), extended:

- **Named channel instances** (§4.1): several Telegram channels share one bot token; several topics share one Bluesky/Threads account. Delivery state is keyed by `(item_or_digest_id, channel_id)`.
- **Static `feed` channel** (always on), per enabled topic:
  - `feeds/<topic>.atom` and `feeds/<topic>.rss` (RSS 2.0, same entries)
  - `feeds/all.atom` / `feeds/all.rss` (combined, last 100 entries across topics)
  - `feeds/omnibrief.opml` (subscribe to everything in one click)
  - Alert topics: one entry per item; digest topics: one entry per digest
- **Push channels** (Telegram, Bluesky, Threads):
  - `max_posts_per_run` per channel (overflow becomes one "+N more, see feed" post)
  - optional `quiet_hours` (Europe/Vienna): held items post after quiet hours unless severity ≥ `critical`
  - per-topic hashtags; short-form renderer (v1 §7a) for Bluesky/Threads
- **Attribution:** every topic declares an `attribution` id; `about.html` lists all enabled topics' sources and licences; feed `<rights>` per topic; short-form posts link to the entry; account bios point to `about.html` (v1 rule, now covering all topics).

---

## 10. Landing page (instance-customisable)

Generated each run into `site/` from templates: `themes/default/` (engine) overlaid by `instance/site/` (instance).

Default pages:
- `index.html`: what Omnibrief is, one card per enabled topic (description, last update, links: Atom, RSS, Telegram, Bluesky, Threads where configured), OPML link
- `topics/<id>.html`: latest 20 entries rendered as HTML (no JS needed)
- `about.html`: sources, licences, attribution, AI-summary disclaimer, NVD notice
- `health.json` + a small `status.html`: last run, per-topic and per-channel status

Rules: static HTML + CSS only; no trackers, no third-party scripts, no cookies. Instance may set `CNAME` for a custom domain. Templates are Jinja2; the instance can override any single template file without copying the rest.

---

## 11. Cost and limits check (free-tier fit)

| Resource | Expected use | Fit |
|---|---|---|
| Actions minutes | 24 runs/day × ~2–4 min | Free on a public repo with standard runners |
| Pages | Small static site, feeds | Well within Pages limits |
| Gemini free tier | ≤ ~11 calls/day (§8) | Far inside; budget guard enforces it |
| Telegram / Bluesky / Threads | Tens of posts/day | Inside free limits (v1 research) |
| Source APIs | ~7 topics, ≤ ~30 requests/run | Respect each source's published rate limits; NVD key optional |
| Git growth | Digests + 90 days items + monthly JSONL | Rotation rules in §7 |

The hourly schedule increases repo activity, so the 60-day inactivity risk (v1 §8) effectively disappears; still keep the Phase 1 check.

---

## 12. Security (public engine, public instance)

v1 §9 applies to both repos. Additions:
- **Engine repo has no secrets and no live schedule**; `engine-guard.yml` prevents an `instance/` directory from ever being committed upstream.
- **Instance secrets** are per fork. The README lists every env name with its scope; nothing in YAML may contain a credential (validator rejects values that look like tokens).
- **Watchlists and filters** that reveal private interests (e.g. vulns watchlist) stay in Actions variables, not `instance/` YAML.
- **Sink credentials** (S3, Azure) are optional secrets; sinks log only counts and status.
- **Threads refresh PAT** remains the most powerful credential; it's used only by `refresh-threads-token.yml`.
- **Upstream merges** are reviewed like any PR: instances sync from engine release tags, and CI runs `validate-config` on the instance before the next scheduled run uses new code.

---

## 13. Build plan and subagents

### 13.1 Use subagents where they help
Same rules as v1 §10: the main agent owns contracts and the pipeline (`Item`, `Source`, `StateStore`, `ArchiveSink`, `Publisher`, topic YAML schema, planner). Subagents get bounded work against those contracts.

| When | Subagent task |
|---|---|
| Phase 0 | **One verification subagent per new source** (TED, arXiv, Safety Gate, NOAA SWPC, USGS, GeoSphere): confirm endpoint + parameters against official docs, fetch a live sample into `tests/fixtures/<source>/`, quote the exact licence/terms obligation, note rate limits and publication schedule. Return a table. No guessing. |
| Phase 1 | **Storage subagent:** `git_branch` primary + `artifact` + `jsonl_git` sinks against the contracts, with idempotency tests. |
| Phase 2 | **One source-adapter subagent per source**, offline tests against Phase 0 fixtures (pagination, empty windows, malformed records, rate-limit responses). |
| Phase 3 | **Template subagent:** alert templates per channel type (feed, Telegram, short-form) for earthquakes, space weather, weather warnings. |
| Phase 3 | **Prompt subagent:** digest prompts for tenders, arxiv_ai, safety_gate against real fixtures; before/after samples for Mike. |
| Phase 4 | **Site subagent:** default theme, landing page, topic pages, about page, OPML, status page. |
| Before each phase is marked done | **Independent review subagent** (hasn't seen the code): v1 checklist (injection/XSS, secret leakage, workflow hardening, licence compliance, cost/free-tier, isolation) **plus** engine/instance separation (no instance files upstream, no engine edits needed downstream) and per-topic licence obligations on every channel. |

### 13.2 Phases

**Phase 0: contracts + source verification**
- [ ] `Item`, `Source`, `StateStore`, `ArchiveSink`, `Publisher`, topic schema, instance schema (`schema_version: 2`), overlay resolver
- [ ] Six new sources verified (subagents), fixtures saved, licence quotes recorded in `NOTICE.md`
- ✅ Done when: offline tests pass; `validate-config` passes on `instance.example/` and rejects a non-allowlisted source and a YAML-embedded token

**Phase 1: planner + storage + migration of `vulns`**
- [ ] Planner with cadence/`not_before`, run record, schedule state; `vulns` ported to the topic plugin structure; primary store + default sinks
- ✅ Done when: v1 behaviour for `vulns` is reproduced under v2; a forced run of all topics in an empty instance completes; a failing sink doesn't fail the run

**Phase 2: all source adapters**
- [ ] TED, arXiv, Safety Gate, NOAA SWPC, USGS, GeoSphere adapters + dedup/material-change rules per topic
- ✅ Done when: two consecutive runs show zero duplicates; a material change re-emits as "updated"; one source down degrades only its topic

**Phase 3: rendering + channels**
- [ ] Templates, LLM digests with budget guard, named channel routing, `max_posts_per_run`, quiet hours, Atom + RSS + combined feeds + OPML
- ✅ Done when: with **no** channel settings and **no** LLM key, an instance publishes all feeds (digests as un-summarised fallbacks); with settings, each topic reaches exactly its configured channels; LLM budget exhaustion falls back cleanly

**Phase 4: landing page + fork workflow**
- [ ] Default theme, instance overlay, `engine-guard.yml`, README "create your instance" checklist, release tagging
- ✅ Done when: Mike forks the engine, adds `instance/`, enables Actions and Pages, sets secrets, and the site is live without editing any engine file; a later engine release merges into the fork with **zero conflicts**

**Phase 5: tune**
- [ ] Thresholds (magnitude, Kp, warning level), tender CPV list, arXiv keywords, posting caps
- ✅ Done when: Mike says every channel has more signal than noise

---

## 14. Changes from v1

| Area | v1 | v2 |
|---|---|---|
| Topics | 1 (vulns) | 7, plugin-based (YAML + optional module) |
| Schedule | 3-hourly, everything every run | Hourly run; planner runs only due topics |
| Item model | `Vuln` | Generic `Item` + per-topic `fields` |
| Rendering | LLM digest only | `template` / `llm_digest` / `hybrid`, with LLM budget |
| Storage | Git `data` branch | Primary store + pluggable sinks (artifact, JSONL-in-git, optional S3/Azure) |
| Channels | One of each, topic-wide | Named channel instances, per-topic routing, caps, quiet hours |
| Feeds | Atom per topic | Atom + RSS per topic, combined feed, OPML |
| Repos | One | Engine + forked instance with overlay rule |
| Site | index/about/health | Customisable landing page, topic pages, status page |

---

## 15. Open questions for Mike (ask, don't assume)

1. Repo names: engine `omnibrief` + instance `omnibrief-live` (or another name)? Custom domain for the instance?
2. Channel layout: one Telegram channel per topic family (security / tenders / alerts), or one channel with hashtags?
3. Tenders: which countries and CPV codes? (Suggested start: AT, DE, CH; IT services, software, data/analytics, cloud.)
4. arXiv: which categories and keywords?
5. Alert thresholds: earthquake magnitude (Europe vs Austria), Kp/G-level for aurora, GeoSphere warning level.
6. Quiet hours for alert channels: yes/no?
7. Optional sinks: enable S3-compatible (R2/B2) or Azure Blob, or stay with git + artifacts only?
