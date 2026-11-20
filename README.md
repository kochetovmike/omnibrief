# Omnibrief

A hub for alerts: openly licensed vulnerability sources in → one merged, filtered, summarised
digest out. Runs every hour on GitHub Actions and publishes a dashboard and a public Atom alert feed on
GitHub Pages: one short entry per critical or actively exploited vulnerability, plus a 12-hour roundup
(07:00 and 19:00 Vienna time) for everything else.
Costs €0: GitHub Free, the Gemini API free tier, and public data APIs. See [design.md](design.md).

**Feed:** <https://kochetovmike.github.io/omnibrief/feeds/vulns.atom> ·
**Sources & licences:** [NOTICE.md](NOTICE.md)

## How it works

```
CISA KEV ─┐
NVD      ─┼─► merge per CVE → EPSS + KEV enrichment → dedupe → filter → Gemini digest ─► Atom feed
GHSA     ─┘                                                                             (Pages)
```

A record is included if it is **actively exploited** (CISA KEV), **or** it has CVSS ≥ 7.0 **and**
matches the watchlist, **or** its EPSS percentile is ≥ 0.99. A record already published comes back
only on a material change (added to KEV, severity raised, fix released), marked "updated".
If Gemini is unavailable, the digest is still published, unsummarised.

## Local development

```bash
py -3.12 -m venv .venv            # or python3.12 -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

pytest                            # offline; tests never hit the network
ruff check .
python -m omnibrief validate-config

# A real run against live sources, writing into throwaway folders:
VULNS_WATCHLIST="microsoft,cisco,wordpress" GEMINI_API_KEY=... \
  python -m omnibrief run --data ./data --site ./site --raw ./raw
# Without GEMINI_API_KEY (or with --no-llm) you get un-summarised fallback digests.

python -m omnibrief render-only --data ./data --site ./site   # rebuild the site from existing data
```

`data/`, `site/` and `raw/` are git-ignored on `main`.

## One-time GitHub setup

1. Repo must be **public** (free Pages and Actions minutes).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Secrets and variables → Actions:**
   - Secret `GEMINI_API_KEY` — from Google AI Studio (free tier).
   - Secret `NVD_API_KEY` — optional, free from NVD; raises the rate limit from 5 to 50 requests / 30 s.
   - Variable `VULNS_WATCHLIST` — comma-separated, e.g. `microsoft,cisco,fortinet,wordpress,linux kernel`.
     Matched case-insensitively on whole words against product, package and description text.
     Keep it broad (well-known vendors/platforms): matches are visible in the public feed.
4. Run **Actions → collect → Run workflow** once. The first run creates the orphan `data` branch.
5. Optional: protect `main` (PRs only).

## Rotating keys

Replace the secret value under **Settings → Secrets and variables → Actions**; the next run picks it up.
A missing or invalid `GEMINI_API_KEY` never fails a run — digests are published un-summarised and
`health.json` reports `llm: fallback` / `not_configured`.

## Adding a source

1. Check its licence explicitly allows reuse and republication (design §5); add it to
   `SOURCE_LICENCES` in `src/omnibrief/attribution.py` with the phrases NOTICE.md must contain.
2. Write an adapter in `src/omnibrief/sources/` (subclass `Source`, implement `discover` and/or
   `enrich`), register it in `sources/__init__.py`, add offline tests with saved fixtures.
3. Add an entry to `sources.yaml` and the attribution to `NOTICE.md`. `validate-config` enforces both.

## Health

`https://kochetovmike.github.io/omnibrief/health.json` — last run/success times, status
(`ok` / `degraded`), failing sources, LLM status and per-publisher status.
