# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Omnibrief v2 is a hub: many licence-clean sources → per-topic filter/dedupe → template alerts or
AI digests → Atom/RSS feeds, a static site and Telegram channels. One hourly GitHub Actions run; a
planner picks the due topics. The spec is `design-v2 (2).md` (supersedes `design.md`; v1 rules it
doesn't change still apply). Hard constraints: **€0 running cost** and **only licence-allowlisted
sources**.

**Engine vs instance:** this repo is the engine. It must never contain `instance/` (`engine-guard.yml`
fails CI) and its workflow is inert without one. A fork adds `instance/` (copy of `instance.example/`).
Never make normal customisation require editing engine files — add a config knob instead.

## Commands

```bash
.venv/Scripts/python -m pip install -e ".[dev]"                     # Windows dev box (py -3.12 venv)
.venv/Scripts/python -m pytest                                      # all tests, offline (~40 s)
.venv/Scripts/python -m pytest tests/v2/test_channels.py -k quiet   # single test
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m omnibrief --instance instance.example validate-config
.venv/Scripts/python -m omnibrief --instance instance.example run --data D --site S --out O --archive A --topics all
.venv/Scripts/python -m omnibrief --instance instance.example {plan|render-only|deliver [--test]} --data D [--site S]
```
Live runs hit real APIs (point dirs at a scratch folder). Env: `GEMINI_API_KEY`, `NVD_API_KEY`,
`GITHUB_TOKEN`, `VULNS_WATCHLIST`, `ARXIV_KEYWORDS`, Telegram token + chat-id vars named in the instance.

## Architecture

- `src/omnibrief/engine/` — topic-agnostic v2 engine:
  `config.py` (instance + topic schema, overlay = deep-merge `instance/topics/*.yaml` over
  `catalog/topics/*.yaml`, file-wins for prompts/templates/site/NOTICE, validation incl. licence
  allowlist, routing, token-in-YAML), `planner.py` (cadence, `not_before`, business days, forced),
  `runner.py` (`run`: plan → per-topic collect in a thread pool, isolated → sinks → static outputs for
  EVERY enabled topic → site; `deliver`: push channels), `store.py` (`EngineStore` extends v1 `Store`;
  layout in its docstring), `budget.py` (LLM caps per run/UTC day → `BudgetExhausted` = fallback),
  `render.py` (Jinja with overlay; generic LLM digest; Telegram HTML), `feeds.py` (Atom/RSS/OPML),
  `channels.py` (named Telegram channels: caps, quiet hours, delivery records, v1-state migration),
  `sinks.py` (artifact, jsonl_git), `site.py` (landing/topic/status/about pages from `catalog/theme/`).
- **Topic plugins** (`engine/topics/`): `generic` (YAML-driven: `sources` → `Item`s → dedupe by
  `key` + `material_hash` → declarative `filters` → template items (alert) or one digest (digest))
  and `vulns`, which runs the **whole v1 pipeline unchanged** (`pipeline.run_topic`, `merge`,
  `filters`, `digest`, `global_critical`, `categories`, `publishers/atom|telegram`, v1 `site.py`
  dashboards written as `vulns.html`/`general.html`/`cat-*.html`) via `build_v1_config()`, which turns
  instance + `catalog/topics/vulns.yaml` `options:` into a v1 `Config`. v1 tests run against that.
- **Sources** (`engine/sources/`): usgs_fdsn, noaa_swpc, geosphere_warn, arxiv, ted, safety_gate.
  Adapters raise on errors; the topic isolates them. Fixtures in `tests/fixtures/<source>/` are real
  samples (GeoSphere's is synthetic from its OpenAPI spec — no live warning when captured).
- **Catalogue** (`src/omnibrief/catalog/`, packaged): `topics/*.yaml`, `prompts/*.md`,
  `templates/<name>.{html,telegram}.j2`, `theme/*`, `NOTICE.md` (about page; root `NOTICE.md` points here).
- **Workflows**: `run.yml` (check → run [read + secrets as JSON] → push-state [write; data + archive
  branches] → deploy; deliver [read + secrets] → push-deliveries [write]), `ci.yml`, `engine-guard.yml`.
  Secrets/vars reach the CLI as `OMNIBRIEF_SECRETS_JSON`/`OMNIBRIEF_VARS_JSON` so instances name env
  vars in YAML without editing the workflow. Actions are pinned to commit SHAs.

## Rules that are easy to break

- **Untrusted text everywhere.** Jinja autoescape is on for all templates; `|safe` only for sanitised
  digest HTML and the trusted NOTICE. LLM output goes through `render.sanitise` (URL allowlist = input
  records' links + own site). Item URLs must be https (validated in `Item`).
- **Logs, run records, health.json and the data branch are public**: counts/ids/status only, never URLs,
  bodies, prompts or watchlist terms. Telegram puts the bot token in the URL — never log URLs; httpx is
  held at WARNING.
- **Nothing is ever silently lost**: a record is marked seen once emitted, so digests must show every
  emitted item (overflow beyond `max_input_items` is listed unless `overflow: drop`; the LLM-missing check
  re-appends skipped items unless `llm_selects: true`, e.g. arXiv's top-10 pick).
- **Isolation**: a topic, source, sink, static output or channel failure marks only itself; only invalid
  config or a failed push/deploy fails the workflow. The LLM never fails a run.
- **Static outputs are rebuilt every run for every enabled topic** (the site dir is fresh in CI); a topic
  that isn't due still renders its feeds/pages from stored state.
- Every source needs an allowlist entry (`attribution.SOURCE_LICENCES`) with phrases that must appear in
  the NOTICE; `validate-config` enforces it for enabled topics.
- Tests are offline: `respx` + `tests/fixtures`. `tests/v2/helpers.py` builds temp instances and mocks
  every source API.
- v1 specifics still hold for `vulns` (see `global_critical.py`, `categories.py`, `publishers/atom.py`,
  `publishers/telegram.py` docstrings): Global Critical tiers, watchlist categories, message-style feeds,
  08:00/20:00 windows (`rollup_window`), Global brief.
