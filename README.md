# Omnibrief

A hub for licence-clean alerts and digests: many openly licensed sources in → filtered, de-duplicated,
optionally AI-summarised → many channels out (Atom/RSS feeds, a website, Telegram channels).
One hourly GitHub Actions run; a planner decides which topics are due. Costs €0: GitHub Free,
the Gemini API free tier, public data APIs. Design: [design-v2](design-v2%20(2).md) (supersedes [design.md](design.md)).

| Topic | Kind | Cadence | Source |
|---|---|---|---|
| `vulns` | digest + Global Critical alerts | hourly | CISA KEV, NVD, GitHub Advisory DB, EPSS |
| `tenders` | AI digest | daily ≥ 09:30, business days | EU TED (AT, DE, CH; IT/data CPV codes) |
| `arxiv_ai` | AI digest (top 10 picks) | daily ≥ 07:00 | arXiv API (cs.AI, cs.LG, cs.CL + keywords) |
| `safety_gate` | AI digest | daily (new report each Friday) | EU Safety Gate weekly report |
| `space_weather` | template alerts | hourly | NOAA SWPC (G2 / Kp 6 or stronger) |
| `earthquakes` | template alerts | hourly | USGS (M4+ Europe & Alps, M3+ Austria) |
| `weather_at` | template alerts | hourly | GeoSphere Austria (orange/red) |

Alert topics never call the AI; digest topics make at most one call per run, under a per-run and
per-day budget. Without an AI key everything still publishes, digests as titles + links.

## Engine vs instance

This repo is the **engine**: code, the topic catalogue (`src/omnibrief/catalog/`), the default theme,
workflows and tests. It contains **no secrets and no `instance/`**, so its hourly workflow does nothing.
An **instance** is a **fork** that adds `instance/` and its own secrets; that's what runs and publishes.

Instances customise only through `instance/`, never engine files, so `git merge upstream/<release-tag>`
(or GitHub's "Sync fork") never conflicts:

| `instance/…` | Purpose |
|---|---|
| `omnibrief.yaml` | which topics are on, where each goes (channels), sinks, site settings — see `instance.example/` |
| `topics/<id>.yaml` | deep-merged over the catalogue topic (thresholds, CPV codes, keywords…), or a new topic |
| `prompts/<name>.md` | replaces a catalogue prompt |
| `templates/<name>.{html,telegram}.j2` | replaces an alert template |
| `site/<file>` | replaces any theme file (`index.html.j2`, `style.css`, …); extra files (e.g. `CNAME`) are copied |
| `NOTICE.md` | replaces the attribution page text (validate-config checks it still credits every source) |

### Create your instance (checklist)

1. **Fork** this repo (it stays public: free Actions minutes and Pages). In the fork, **enable Actions**
   (GitHub doesn't run workflows in a new fork until you do; scheduled ones need this explicitly).
2. Copy `instance.example/` to `instance/`, set `site.base_url` to your fork's Pages URL, choose topics and
   channels. Commit. (`engine-guard` allows `instance/` because the repo is a fork.)
3. **Settings → Pages → Source: GitHub Actions.**
4. **Settings → Secrets and variables → Actions**, as needed (everything is optional):
   - Secrets: `GEMINI_API_KEY` (AI digests), `NVD_API_KEY` (faster NVD), `TELEGRAM_BOT_TOKEN`.
   - Variables: `VULNS_WATCHLIST` (vulns categories, see below), `ARXIV_KEYWORDS` (extra keywords),
     one chat id per Telegram channel you configured, e.g. `TELEGRAM_CHAT_ID=@vulnerabilitiesbrief`,
     `TG_ALERTS_CHAT_ID`, `TG_TENDERS_CHAT_ID`. Names come from your `instance/omnibrief.yaml`; the workflow
     passes all secrets/variables through, so you never edit the workflow.
5. **Actions → run → Run workflow** with `topics = all` (and tick the Telegram test if you set channels).
   The first run creates the `data` (state) and `archive` (JSONL history) branches.
6. Upgrades: merge an engine **release tag** into your fork; CI runs `validate-config` on your instance.

## Channels

- **Feeds (always on):** `feeds/<topic>.atom` + `.rss` per topic, `feeds/all.atom`/`.rss` (latest 100 across
  topics), `feeds/omnibrief.opml` (subscribe to everything). `vulns` additionally has one feed per watchlist
  category and `feeds/vulns-global-critical.atom`.
- **Telegram (optional):** any number of named channels sharing one bot; each topic lists the channels it
  goes to. Per channel: `max_posts_per_run` (overflow → one "+N more" post) and `quiet_hours`
  (non-critical posts wait). Alerts post one message each; digests post once per run. `vulns` posts
  Global Critical alerts + the twice-daily Global brief (08:00/20:00). Delivery records in
  `state/deliveries/<channel>.json` make re-runs idempotent; only the last 24 h is ever sent.
  Bot setup: @BotFather `/newbot`; add the bot to each channel as admin with **only** "Post Messages".
  Set each channel's description to credit its sources and link `about.html`.
- **Bluesky, Threads, S3/Azure sinks:** in the design, not built yet (validate-config says so).

### vulns: watchlist categories and Global Critical
`VULNS_WATCHLIST` = comma-separated categories, each a term (`microsoft`) or `label=t1|t2`
(`programming languages=python|pip|npm|maven|rust`). Each category gets a dashboard view and a feed;
**category names are public** — keep them broad. **Global Critical** is built in: fundamental
technologies (OpenSSL, Log4j, nginx, Linux kernel, Windows, Chrome, edge VPNs…) that are exploited or
CVSS ≥ 9 (`src/omnibrief/global_critical.py`).

## Local development

```bash
py -3.12 -m venv .venv && .venv/Scripts/activate      # Linux/macOS: python3.12 -m venv .venv; source .venv/bin/activate
pip install -e ".[dev]"
pytest                                                # offline; tests never hit the network
ruff check .
python -m omnibrief --instance instance.example validate-config
python -m omnibrief --instance instance.example plan --data ./data
# Live run of every topic into throwaway folders (no AI key → un-summarised digests):
python -m omnibrief --instance instance.example run --data ./data --site ./site --out ./out --archive ./archive --topics all
python -m omnibrief --instance instance.example render-only --data ./data --site ./site
python -m omnibrief --instance instance.example deliver --data ./data --test   # needs Telegram env vars
```

## Adding a topic or source

1. Check the licence explicitly allows reuse and republication; add the source type to `SOURCE_LICENCES`
   in `src/omnibrief/attribution.py` with the phrases the NOTICE must contain, and the text to
   `src/omnibrief/catalog/NOTICE.md`.
2. New source: an adapter in `src/omnibrief/engine/sources/` returning `Item`s, registered in its
   `__init__.py`, with a saved live sample in `tests/fixtures/<source>/` and offline tests.
3. New topic: `src/omnibrief/catalog/topics/<id>.yaml` (sources, cadence, filters, material fields,
   render mode) plus templates (alerts) or a prompt (digests). An instance can add a topic with just
   `instance/topics/<id>.yaml`.

## Health

`<site>/status.html` and `health.json`: last run, per-topic status and last success, per-channel status,
sinks, and AI calls used today vs the budget.
