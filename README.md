# Omnibrief

Licence-clean alerts and digests: many open sources in → filtered, de-duplicated, optionally
AI-summarised → feeds, a website and Telegram. One repo, one hourly GitHub Actions run,
**every pipeline is one YAML file in `briefs/`**. €0 to run. Spec: `design-v3 (1).md`.

| Brief | Kind | Source |
|---|---|---|
| `vulns` | digest + Global Critical alerts | CISA KEV, NVD, GitHub Advisory DB, EPSS |
| `tenders` | AI digest, weekdays ≥ 09:30 | EU TED |
| `arxiv-ai` | AI digest (top 10) | arXiv API |
| `safety-gate` | AI digest | EU Safety Gate weekly report |
| `space-weather`, `earthquakes`, `weather-at` | template alerts | NOAA SWPC, USGS, GeoSphere Austria |

## Files you edit
- `omnibrief.yaml` — site, LLM, **connections** (shared credentials, e.g. one Telegram bot), storage, limits.
- `briefs/<id>.yaml` — one pipeline: schedule, sources, filters (`expr`, keywords), dedupe, render
  (`template` | `llm_digest`), outputs (`feed`, `telegram`), attribution. Editors get autocomplete from
  `schema/brief.schema.json`. Examples: `examples/briefs/`.
- `prompts/`, `templates/`, `site/` — optional prompts, alert templates and theme overrides.

Secrets are **references**, never values: `{ secret: NAME }` / `{ variable: NAME }`. A missing output
secret disables that output only; no LLM key → digests publish as titles + links.

## Add a pipeline (no code)
```bash
python -m omnibrief new-brief my-feed --type rss   # generic: rss | atom | json_api | xml | geojson
# edit briefs/my-feed.yaml (a generic source needs a licence review block), then:
python -m omnibrief check          # schemas, licences, secret references table
python -m omnibrief dry-run my-feed
python -m omnibrief notice         # regenerate NOTICE.md (CI checks it)
```
Specialised sources (`licence: builtin`): `nvd`, `cisa_kev`, `ghsa`, `epss`, `ted_search`, `arxiv_api`,
`safety_gate_weekly`, `noaa_swpc`, `usgs_fdsn`, `geosphere_warnings`.
Excluded (no reuse licence): Austrian private media, ORF/APA/OTS, Länder press offices.

## Setup (this repo or "Use this template")
1. Public repo; **Pages → Source: GitHub Actions**; enable Actions.
2. Add the secrets/variables your config references (`python -m omnibrief check` lists them), e.g.
   `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TG_ALERTS_CHAT_ID`, `TG_TENDERS_CHAT_ID`, `VULNS_WATCHLIST`.
3. Actions → **run** → Run workflow (`briefs = all`, optional Telegram test).
Engine updates for template users: copy `src/`, `schema/`, `.github/` from a newer release.

## Local
```bash
pip install -e ".[dev]" && pytest && ruff check .
python -m omnibrief run --data ./data --site ./_site --briefs all     # live run into scratch dirs
```
