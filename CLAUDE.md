# CLAUDE.md — WPR Wisconsin Gas Prices Widget

Guidance for Claude Code when working in this repo.

## What this is

A zero-maintenance gas price widget for **Wausau Pilot & Review (WPR)**. A Python
scraper runs on a GitHub Actions cron, writes static JSON to `docs/`, GitHub Pages
serves it, and a WordPress page embeds the widget via `<iframe>`. No servers.

> **Note:** The `README.md` is out of date — it describes an old AAA + Playwright +
> Webshare-proxy approach. The actual scraper uses **GasBuddy GraphQL + EIA + Fuel
> Insights**. Treat this file (and the code) as the source of truth, not the README.

## Architecture (standard WPR widget pattern)

```
Python scraper  ──▶  GitHub Actions cron  ──▶  static JSON in /docs
                                                      │
                                              GitHub Pages (public URL)
                                                      │
                                          React/static widget (docs/index.html)
                                                      │
                                            WordPress <iframe> embed on wausaupilotandreview.com
```

## Data sources

1. **GasBuddy GraphQL** (`/graphql`) — per-city station prices. Uses `curl_cffi`
   with `impersonate="chrome"` to clear Cloudflare; **no proxy needed**.
   - Requires a CSRF token scraped from `https://www.gasbuddy.com/home`
     (`window.gbcsrf = "..."`). If that token can't be found, the scrape aborts.
   - Query: `LocationBySearchTerm` → `stations.results[].prices[]`.
2. **GasBuddy Fuel Insights** (`fuelinsights.gasbuddy.com/Home/US/Wisconsin`) —
   statewide historical comparisons (yesterday / last week / last month / last year).
   Best-effort, regex-scraped; cached to `docs/fuel_insights_cache.json` and reloaded
   from cache if a run fails to parse it.
3. **EIA API** (`api.eia.gov/v2/petroleum/pri/gnd`) — weekly Midwest (PADD 2,
   `duoarea=R20`) trend series. Requires `EIA_API_KEY` env var; **skipped silently
   if unset**.

## File map

| Path | Purpose | Edit? |
|------|---------|-------|
| `scrape_gas_prices.py` | The scraper — all logic lives here | Yes |
| `requirements.txt` | `requests`, `curl_cffi` (pinned) | Rarely |
| `requirements-dev.txt` | Adds `pytest` for the test suite | Rarely |
| `tests/test_scrape.py` | Unit tests for the pure (no-network) scraper logic | Yes — when changing logic |
| `.github/workflows/update-gas-prices.yml` | Cron schedule (fixed UTC 12:00 & 17:00 — see note) + failure alerting | To change timing |
| `.github/workflows/tests.yml` | CI: runs pytest on push/PR | Rarely |
| `docs/index.html` | The widget UI, full 720px layout (reads the JSON) | Yes — design/colors |
| `docs/index-compact.html` | Compact 360px widget variant for narrow embeds (same JSON) | Yes — keep in sync with index.html |
| `docs/gas_prices.json` | Main output | **Never by hand** — scraper owns it |
| `docs/gas_prices_history.json` | Daily history, capped at 400 days | **Never by hand** |
| `docs/eia_weekly.json` | EIA weekly series | **Never by hand** |
| `docs/fuel_insights_cache.json` | Fuel Insights cache/fallback | **Never by hand** |
| `docs/scrape_status.json` | Per-run heartbeat (gitignored) — read in-job for failure alerting | **Never** — scraper owns it |

## Key constants (top of `scrape_gas_prices.py`)

- `CITIES` — 15 Wisconsin cities scraped.
- `PRIORITY_METROS` — `["Wausau", "Eau Claire", "Green Bay", "Appleton", "Madison", "Milwaukee"]`
  (ordering shown first in the widget).
- `FUEL_MAP` — GasBuddy `fuelProduct` → internal keys: `regular`, `mid_grade`,
  `premium`, `diesel`.

## Output schema (`docs/gas_prices.json`)

```json
{
  "source": "GasBuddy",
  "source_url": "...",
  "state": "Wisconsin",
  "price_date": "mm/dd/yy",
  "scraped_at": "<ISO 8601 UTC>",
  "statewide": {
    "current_avg": { "regular": 0.0, "mid_grade": 0.0, "premium": 0.0, "diesel": 0.0 },
    "low": { ... }, "high": { ... },
    "yesterday_avg": { "regular": 0.0 },   // from Fuel Insights, may be absent
    "week_ago_avg": { ... }, "month_ago_avg": { ... },
    "year_ago_avg": { ... }, "gasbuddy_live_avg": { ... }
  },
  "metros": {
    "Wausau": {
      "current_avg": { ... }, "low": { ... }, "high": { ... },
      "station_count": { "regular": 12, ... },
      "stale": true, "stale_from": "mm/dd/yy"   // only present if preserved from a prior run
    }
  },
  "priority_metros": [ ... ]
}
```

## Behaviors worth knowing before editing

- **Rate limiting is deliberate.** GasBuddy throttles datacenter IPs
  (GitHub Actions / Azure) to ~7 requests/min. The scraper runs cities in **batches
  of 7**: 60s wait before batch 1, 90s between batches, 5s between cities, plus a
  429-retry with backoff. Don't "optimize" these delays away — that's what makes the
  Actions run succeed.
- **Stale-city preservation.** If a city fails this run, `merge_with_previous()`
  carries forward the previous value, tags it `stale` + `stale_from`, and
  `recalculate_statewide()` recomputes averages. Stale entries are excluded from
  `gas_prices_history.json`.
- **Fail-soft on GasBuddy, continue to EIA.** If the GasBuddy scrape throws, the
  previous `gas_prices.json` is left untouched and EIA still updates.
- **History cap.** `gas_prices_history.json` is trimmed to the most recent 400 days.
- **Cron is fixed UTC, not Central.** The workflow runs at `12:00` and `17:00` UTC.
  GitHub Actions cron ignores DST, so local times drift: 7 AM / 12 PM during CDT,
  6 AM / 11 AM during CST. Don't describe the schedule as a fixed Central time.
- **Output is validated before write.** `validate_output()` raises if the assembled
  data is missing keys or has an implausible statewide regular avg; the live file is
  preserved on failure (caught like any scrape error).
- **Run heartbeat + alerting.** `main()` always writes `docs/scrape_status.json`
  (gitignored) with `gasbuddy_success`, fresh/stale counts, and failed cities. The
  workflow's "Alert on scrape failure" step reads it and opens (or auto-closes) a
  GitHub issue. `run_health` is assembled in `scrape_gasbuddy()` and popped from the
  dict before `gas_prices.json` is written — it never lands in the live file.
- **Parsing is extracted for testability.** `parse_station_results()` and
  `parse_fuel_insights()` are pure functions covered by `tests/test_scrape.py`;
  run `pytest -q` after touching scraper logic.
- **Widget fails honestly.** If `gas_prices.json` can't be fetched, the widget shows
  a quiet "temporarily unavailable" state (no stale baked-in snapshot). The header
  shows a relative "Updated Nh ago" that turns amber past ~26h.

## Commands

```bash
# install
pip install -r requirements.txt

# run the scraper (writes to docs/gas_prices.json by default)
python scrape_gas_prices.py

# custom output path
python scrape_gas_prices.py -o path/to/out.json

# EIA trend data (optional — skipped if key is unset)
export EIA_API_KEY=...        # Windows (cmd): set EIA_API_KEY=...

# preview the widget locally
cd docs && python -m http.server 8000   # then open http://localhost:8000
```

## Deployment

- **GitHub Pages**: Settings → Pages → Deploy from branch `main`, folder `/docs`.
- **Actions permissions**: Settings → Actions → General → Workflow permissions →
  "Read and write" (so the cron can commit updated JSON back).
- **Secret**: `EIA_API_KEY` in repo Settings → Secrets and variables → Actions.

## Development principles (apply to all changes here)

- Surgical changes only — minimal, focused fixes; fix root causes, not symptoms.
- One correct path, no fallbacks; one way to do a thing, not several.
- Throw/fail fast when preconditions aren't met (e.g. missing CSRF token already does this).
- Each function = one responsibility.
- Evidence-based debugging: add minimal, targeted logging (the script uses the
  `log` logger — reuse it rather than `print`).
- Clarity over backward-compatibility.

## Known follow-ups

- [ ] Rewrite `README.md` to match the GasBuddy/curl_cffi reality (currently describes AAA).
- [ ] Add a `.gitignore` (`.venv/`, `__pycache__/`, `*.pyc`).
