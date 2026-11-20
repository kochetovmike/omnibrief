# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Omnibrief is a scheduled collector: openly licensed vulnerability sources in → merged, filtered,
LLM-summarised digest → dashboard + Atom alert feed on GitHub Pages, hourly (+ optional push channels later).
`design.md` is the approved spec and build order (§11 phases); read the relevant section before
changing behaviour. Hard constraints from it: **€0 running cost (free tiers only, no Azure/paid
services), and only licence-allowlisted sources (§5).**

## Commands

```bash
py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # Windows dev box
.venv/Scripts/python -m pytest                                  # all tests, offline
.venv/Scripts/python -m pytest tests/test_pipeline.py -k kev    # single test / filter
.venv/Scripts/python -m ruff check .                            # lint (CI runs this)
.venv/Scripts/python -m omnibrief validate-config               # sources.yaml + NOTICE.md coverage
.venv/Scripts/python -m omnibrief run --data ./data --site ./site --raw ./raw [--no-llm]
.venv/Scripts/python -m omnibrief render-only --data ./data --site ./site
```

A live `run` hits real APIs; env vars: `GEMINI_API_KEY`, `NVD_API_KEY`, `GITHUB_TOKEN`,
`VULNS_WATCHLIST` (all optional locally — without the Gemini key you get fallback digests).
`data/`, `site/`, `raw/` are git-ignored; point them at a scratch dir for experiments.

## Architecture

Pipeline (`pipeline.py`, one `run_topic` per topic in `sources.yaml`):
discover (sources run concurrently, each isolated) → `merge.py` (group by id/alias, per-field
source precedence) → enrich (KEV flags + EPSS, batched) → `filters.dedupe` against the seen index
→ `filters.Filter` → raw archive → `digest.build_digest` (one LLM call, or fallback) → save digest
→ static publishers → `site.py` (index, about, health.json) → run record.

Key cross-file contracts:
- **`Vuln` (models.py)** is both the per-source partial record and the merged record. `sources`
  holds source *types* and drives attribution. Canonical id = CVE if known, else GHSA.
- **Source adapters** (`sources/`) subclass `Source` and set `discovers`/`enriches`. They must
  raise on errors — the pipeline catches per source and marks only that source `failed`.
  `cisa_kev` does both: discovers recent additions and flags any merged CVE in the catalog.
- **Licence allowlist** lives in `attribution.SOURCE_LICENCES`. Config validation rejects any
  source type not in it; `check_notice` makes `validate-config` fail if NOTICE.md lacks a
  required phrase for an enabled source. `about.html` is NOTICE.md rendered — keep them in sync
  by editing NOTICE.md only.
- **Seen index only holds emitted records.** A filtered-out record that later matters (e.g. added
  to KEV) appears as "new"; an emitted one re-appears only on a material change
  (`filters.material_changes`: KEV added, severity up, new patched version) as "updated".
  Unseen records must also be fresh (`filters.is_fresh`: published or KEV-added ≤ 14 days ago),
  so edits to years-old advisories don't resurface as news.
- **Discovery windows** are `lookback_hours`, widened back to each source's last successful
  fetch (`state/sources/{topic}.json`, capped at 7 days) so outages and skipped cron runs lose
  nothing. Enrichers with `enrich_after_dedupe = True` (EPSS) only see dedupe candidates.
- The raw artifact holds **included records only** — the full merged set would let anyone infer
  the private watchlist by diffing it against the feed.
- **Digest is channel-neutral** (structured `items` + sanitised `html`). Publishers render it and
  never call the LLM. `Digest.deliveries` is reserved for push publishers (Telegram/Bluesky/Threads,
  phases 3b/3c, not built yet) which will run in a separate `deliver` step after state is pushed.
- **Publishers**: `publishers/registry.py` builds topic.publishers × `is_enabled()`; a disabled
  publisher is recorded `disabled`, never an error. Adding one = subclass + register in
  `PUBLISHER_TYPES` + `publishers:` entry.

State is plain JSON on the orphan `data` branch; layout is documented in `state.py`.
`collect.yml` is three least-privilege jobs: `collect` (contents: read + API secrets; reads the
data branch into `./data`, runs the pipeline, uploads state/site/raw artifacts) → `push-state`
(contents: write, no secrets, runs no project code) → `deploy` (pages/id-token only). Keep
secrets and write permissions out of the job that runs pip-installed code.

## Rules that are easy to break

- **All rendered HTML goes through `render.sanitise`** with a URL allowlist built from the input
  records' links plus our own site URLs; it also rewrites link text to the hostname so injected
  text can't label a reference "Official patch". Record text inserted into markdown we generate
  must go through `render.escape_md`. Advisory text is untrusted (prompt injection).
- The LLM summary is checked for completeness: `finishReason != STOP` → fallback, and item ids
  missing from the output are appended un-summarised (a record is marked seen once emitted, so a
  dropped item would otherwise be lost forever).
- **Logs and run records are public.** Log counts/ids/status only — never URLs, API bodies,
  prompts or watchlist terms. Error strings go through `pipeline._err` (type + HTTP status).
  httpx is held at WARNING because it logs request URLs.
- API keys go in headers, never URLs (Gemini `x-goog-api-key`, NVD `apiKey`).
- Never attribute a summary to NVD/CISA/GitHub; sources are credited for data only.
- The LLM must never fail a run: 429 → immediate fallback (no retry), 5xx/timeouts → retry ×3.
- Tests never hit the network: use `respx` and the real-response samples in `tests/fixtures/`.
- Workflow actions are pinned to full commit SHAs (resolve with `git ls-remote --tags`);
  `collect.yml` triggers only on `schedule` + `workflow_dispatch`.
- No `feedgen`: v1.0 drops `rel` on entry links; the Atom XML is written with `xml.etree`.
- The Atom feed is a **message feed**, not the digests (`publishers/atom.py`): one alert entry per
  critical-or-KEV item (`is_alert`), and one roundup per closed 12-hour window (07/19 local,
  `rollup_window`) for everything else. It is a pure function of the digest history + run time
  (`PublisherContext.now`), rebuilt every run — never edit entries in place.
- `index.html` is the "war room" dashboard (`site.py`): health strip (same data as health.json,
  persisted in `data/state/health.json` so `render-only` can redraw it), KPI tiles, criticality
  lanes and a by-date-of-issue timeline built from the last 14 days of digests, then the digest
  log (its `#run-…` anchors are linked from feed entries' footers — keep them). Record fields are
  HTML-escaped there and only `https://` links are rendered; theme colours are CSS variables
  (violet accent) at the top of `site.py`.
