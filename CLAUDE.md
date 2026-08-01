# CLAUDE.md — WPR Wisconsin Gas Prices Widget

Guidance for Claude Code when working in this repo.

## What this is

A zero-maintenance gas price widget for **Wausau Pilot & Review (WPR)**. A Python
scraper runs on a GitHub Actions cron, writes static JSON to `docs/`, GitHub Pages
serves it, and a WordPress page embeds the widget via `<iframe>`. No servers.

> **Note:** `README.md` is the plain-language operator guide and is current. This
> file is the engineering source of truth; when they disagree, the code wins.

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

1. **GasBuddy GraphQL** (`/graphql`) — per-city station prices and names. Uses
   `curl_cffi` browser impersonation to clear Cloudflare; **no proxy**.
   - Requires a CSRF token scraped from the homepage (`window.gbcsrf = "..."`).
     `establish_session()` retries up to 6× with backoff, rotating both the
     **impersonation profile** (`IMPERSONATE_PROFILES`) and the **entry URL**
     (`GASBUDDY_ENTRY_URLS`) with a fresh session each attempt — Cloudflare scores
     TLS fingerprints and routes independently. The session that clears the wall is
     reused for the GraphQL calls.
   - **Rotation cannot beat a pure IP-reputation block.** Cloudflare hard-403s the
     Actions datacenter IP for stretches at a time (it did so on every run from
     2026-07-28 to 08-01). When that happens the scrape aborts → GasBuddy data is
     carried forward, AAA still refreshes, and the run alerts.
   - Query: `LocationBySearchTerm` → `stations.results[]` (name, address, prices).
2. **AAA** (`gasprices.aaa.com/?state=WI`) — the **statewide trend** source
   (today / yesterday / week / month / year, all four fuels). Server-rendered table,
   parsed with `parse_aaa()` (column order Regular, Mid-Grade, Premium, Diesel).
   - **Deliberately independent of GasBuddy.** `scrape_aaa()` takes no session and
     uses plain `requests` (AAA has no bot wall), and `main()` calls it *before* and
     *outside* the GasBuddy try-block. This is load-bearing: AAA used to be fetched
     inside `scrape_gasbuddy()`, so a Cloudflare block froze the statewide trend too.
     Don't re-couple them.
   - Carried forward by `merge_with_previous()` only if AAA itself fails.
   - Our own `gas_prices_history.json` still backs the GasBuddy day-over-day deltas
     on the hero and per-metro rows (same-source, clean).
3. **EIA API** (`api.eia.gov/v2`) — weekly Midwest (PADD 2, `duoarea=R20`) trend
   series for the chart, plus national reg-gas avg (`duoarea=NUS`) and WTI crude
   (`RWTC`) for the context strip. Requires `EIA_API_KEY`; **skipped silently if unset**.

> **Source split:** GasBuddy = live station/metro/cheapest data + the hero average;
> AAA = the statewide historical trend. Both are labeled in the UI. The two current
> averages differ slightly by methodology — that's expected, not a bug.

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
| `docs/digest.html` | Newsletter digest **card** (reads the JSON) — rendered to a PNG for email | Yes — design |
| `scripts/render-digest.mjs` | Playwright: screenshots `digest.html` → `docs/digest.png` (2×, Central TZ) | Rarely |
| `package.json` / `package-lock.json` | Node deps for the digest renderer (Playwright only) | Rarely |
| `docs/digest.png` | Baked newsletter image (twice-daily) at a stable Pages URL | **Never by hand** — CI owns it |
| `docs/wpr-logo.jpg` | WPR logo asset used by the digest card | Rarely |
| `docs/gas_prices.json` | Main output | **Never by hand** — scraper owns it |
| `docs/gas_prices_history.json` | Daily history, capped at 400 days | **Never by hand** |
| `docs/eia_weekly.json` | EIA weekly Midwest series (trends chart) | **Never by hand** |
| `docs/eia_context.json` | EIA national reg-gas avg + WTI crude (context strip) | **Never by hand** |
| `docs/scrape_status.json` | Per-run heartbeat (gitignored) — read in-job for failure alerting | **Never** — scraper owns it |

## Key constants (top of `scrape_gas_prices.py`)

- `CITIES` — 22 Wisconsin cities scraped (central/northern WI listed first for WPR's
  readership; the rest are the other major metros).
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
    "low": { ... }, "high": { ... }
    // No time comparisons here — the statewide trend lives in "aaa" below.
  },
  "aaa": {                                      // AAA statewide trend (or {} / carried-forward)
    "as_of": "mm/dd/yy",
    "current":   { "regular": 0.0, "mid_grade": 0.0, "premium": 0.0, "diesel": 0.0 },
    "yesterday": { ... }, "week_ago": { ... }, "month_ago": { ... }, "year_ago": { ... }
  },
  "metros": {
    "Wausau": {
      "current_avg": { ... }, "low": { ... }, "high": { ... },
      "station_count": { "regular": 12, ... },
      "stations": [                              // up to 8 cheapest by regular (for the Metro tab)
        { "name": "Costco", "address": "423 N 17th Ave, Wausau",
          "prices": { "regular": 3.59, "diesel": 4.49 } }
      ],
      "stale": true, "stale_from": "mm/dd/yy"   // only present if preserved from a prior run
    }
  },
  "priority_metros": [ ... ],
  "summary": {                                 // auto-generated newsroom blurb
    "as_of": "June 4, 2026",
    "headline": "Wisconsin gas averages $3.92/gal",
    "blurb": "As of June 4, 2026, regular unleaded in Wisconsin averages $3.92 ..."
  }
}
```

## Behaviors worth knowing before editing

- **Rate limiting is deliberate.** GasBuddy throttles datacenter IPs
  (GitHub Actions / Azure) to ~7 requests/min. The scraper runs cities in **batches
  of 7**: 60s wait before batch 1, 90s between batches, 5s between cities, plus a
  429-retry with backoff. Don't "optimize" these delays away — that's what makes the
  Actions run succeed. (22 cities ⇒ 4 batches, ~8–10 min per run.)
- **Cheapest stations.** `extract_cheapest_stations()` keeps the 8 lowest-priced
  named stations per city (by regular) with their address + all fuel prices, stored
  under `metros[city].stations`. The Metro tab makes each city row expandable to show
  them. Excluded from `gas_prices_history.json` (only `current_avg` is recorded).
- **National + crude context.** `fetch_eia_context()` writes `docs/eia_context.json`
  (national regular avg via `duoarea=NUS`; WTI crude via the `RWTC` series). Best-effort
  and **independent per series** — if EIA renames a code, the other still lands. The
  widget shows a "U.S. avg / WTI crude" strip on the Statewide tab (WI-vs-US only for
  Regular, since the national series is regular gasoline).
- **Newsroom blurb.** `build_summary()` writes a quotable `summary` blurb into
  `gas_prices.json` each run; the Statewide tab shows it with a Copy button. Headline
  average is GasBuddy; the trend ("down 26¢ from a week ago … per AAA") is AAA-internal.
- **Statewide trend = AAA.** `parse_aaa()`/`scrape_aaa()` populate `data["aaa"]`; the
  Statewide "over time" bars and the blurb's trend both read it. The hero "vs
  yesterday" stays GasBuddy-history (same source as the hero number).
- **Statewide average is station-count weighted.** `compute_statewide()` weights
  each city's average by how many stations it reported, so the figure equals the
  pooled mean of all stations (market-weighted) rather than an equal-per-city mean.
  `low`/`high` remain the absolute min/max across cities. Both `scrape_gasbuddy()`
  and `recalculate_statewide()` go through this one helper.
- **Stale-city preservation.** If a city fails this run, `merge_with_previous()`
  carries forward the previous value, tags it `stale` + `stale_from`, and
  `recalculate_statewide()` recomputes averages. Stale entries are excluded from
  `gas_prices_history.json`.
- **Fail-soft on GasBuddy; AAA and EIA still land.** If the GasBuddy scrape throws,
  `publish_aaa_only()` rewrites `gas_prices.json` with **just** the refreshed AAA
  block (plus a rebuilt `summary`, which quotes AAA's legs). `statewide`, `metros`,
  `price_date` and `scraped_at` are left byte-identical, so the widget's "Updated N
  ago" label keeps telling the truth about the station data while the statewide trend
  stays current. If AAA is down too, the file is not touched at all. EIA always runs.
- **History keys are dates, never strings.** Keys are `mm/dd/yy`; a string sort
  orders them month-major and mixes years (legacy entries were also written unpadded,
  which once sorted March *after* July and made the hero compare against a
  four-month-old price labeled "vs yesterday"). Use `history_key_date()` in Python
  and `historyKeyTime()` in the widgets. `normalize_history_keys()` self-heals legacy
  keys on write.
- **Day-over-day deltas name their real comparison.** Runs can miss days, so the
  widgets label the hero/metro delta "vs yesterday" only when the previous reading
  actually is the prior day; otherwise they name the date ("vs Jul 24").
- **History cap.** `gas_prices_history.json` is trimmed to the most recent 400 days,
  oldest-first **by parsed date**.
- **Cron is fixed UTC, not Central.** The workflow runs at `12:00` and `17:00` UTC.
  GitHub Actions cron ignores DST, so local times drift: 7 AM / 12 PM during CDT,
  6 AM / 11 AM during CST. Don't describe the schedule as a fixed Central time.
- **Output is validated before write.** `validate_output()` raises if the assembled
  data is missing keys or has an implausible statewide regular avg; the live file is
  preserved on failure (caught like any scrape error).
- **Run heartbeat + alerting.** `main()` always writes `docs/scrape_status.json`
  (gitignored) with `gasbuddy_success`, `degraded`, fresh/stale counts, and failed
  cities. The workflow's "Alert on scrape failure" step reads it and opens (or
  auto-closes) one GitHub issue. It alerts on **failure** (0 fresh) *and* on a
  **degraded** run — `is_degraded()` flags when fewer than half the cities scraped
  fresh (rest carried forward as stale); the file is still written either way.
  `run_health` is assembled in `scrape_gasbuddy()` and popped before `gas_prices.json`
  is written — it never lands in the live file. `write_status()` always seeds
  `cities_fresh`/`cities_total`/`failed_cities` so a scrape that aborts before
  reaching any city reports `0/22` rather than `?/?`, and records `aaa_updated` /
  `aaa_only` so the alert can say whether the statewide trend still refreshed.
- **Parsing is extracted for testability.** `parse_station_results()`,
  `extract_cheapest_stations()`, `parse_aaa()`, `build_summary()`,
  `history_key_date()`, `normalize_history_keys()`, and `latest_eia_value()` are pure
  functions covered by `tests/test_scrape.py`; run `pytest -q` after touching scraper
  logic. **Tests must not hit the network** — the `main()` tests stub `scrape_aaa`
  alongside `scrape_gasbuddy` and the EIA fetchers; if the suite suddenly takes
  seconds instead of ~0.5s, something is making a real request.
- **Widget fails honestly.** If `gas_prices.json` can't be fetched, the widget shows
  a quiet "temporarily unavailable" state (no stale baked-in snapshot). The header
  shows a relative "Updated Nh ago" that turns amber past ~26h.
- **Iframe auto-resize contract.** The widget posts `{type:'wpr-gas-height',height}`
  to `window.parent` on every render/resize; the WordPress embed snippet (in the
  README) listens and resizes the iframe. JSON fetches are cache-busted in 10-min
  buckets, and fonts load non-blocking — keep these when editing `index.html`.
- **Newsletter digest image.** Email can't embed the live widget, so the update
  workflow renders `docs/digest.html` (a self-contained card reading the same JSON)
  to `docs/digest.png` via Playwright/Chromium, then commits it — served at a stable
  URL (`.../wpr-gas-prices/digest.png`) the newsletter `<img>`-embeds. The renderer
  waits for `body[data-ready]` (set after the card's fetch/render) so it never races
  the data. It's an **image for email**, not a data cache. Keep `digest.html` fonts
  non-blocking and the `data-ready` signal intact.
  - **Re-renders on both daily runs.** The Node / Playwright / render / commit steps
    carry `if: ${{ !cancelled() }}` so a hard scraper failure can't skip the digest —
    AAA and EIA may have refreshed even when GasBuddy didn't. The PNG only *commits*
    when its pixels actually change, so an unchanged day is a no-op, not churn.
  - **Mixed freshness is stated on the card.** It carries two sources with independent
    freshness; when `aaa.as_of` differs from `price_date` (GasBuddy blocked) an amber
    notice names both dates. Email readers can't check a timestamp — don't drop it.

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

- [x] Rewrite `README.md` to match the GasBuddy/curl_cffi reality.
- [x] Add a `.gitignore`.
- [x] **Port enhancements to `docs/index-compact.html`.** Now at parity: honest
  error state, relative-time freshness label, auto-resize `postMessage`,
  non-blocking fonts, cache-busting, ARIA, no-JS fallback. Also fixed its stale
  "Data via AAA" footer credit → GasBuddy. (It has no tabs/trends/sparklines by
  design.) Keep the two in sync going forward.
- [x] ~~Harden the Fuel Insights price regex.~~ Superseded: Fuel Insights is defunct
  (page no longer serves data) and was removed entirely; statewide comparisons now
  come from our own history.
- [x] **Decouple AAA from GasBuddy** so a Cloudflare block can't freeze the
  statewide trend (`scrape_aaa()` + `publish_aaa_only()`).
- [x] **Fix date-ordering of history keys** in the scraper and both widgets.
- [ ] **Metro + per-station data still goes stale during a GasBuddy IP block.** AAA
  only covers statewide. The known fix is routing GasBuddy through a residential
  proxy (~$3–6/mo, one new repo secret) — deferred by choice, not blocked. If metro
  staleness starts mattering to the newsroom, that's the lever.
