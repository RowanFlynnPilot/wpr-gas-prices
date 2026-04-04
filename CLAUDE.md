# WPR Gas Price Widget — Project Context

## Repository
- **GitHub:** https://github.com/RowanFlynnPilot/wpr-gas-prices
- **Live widget:** https://rowanflynnpilot.github.io/wpr-gas-prices/
- **WordPress embed:** https://wausaupilotandreview.com/wausau-gas-price-tracker/

## Architecture

### Data Pipeline
```
GasBuddy (Playwright + Webshare proxy) → gas_prices.json
GasBuddy Fuel Insights (Playwright)    → fuel_insights_cache.json → merged into gas_prices.json
EIA API (requests)                     → eia_weekly.json
Daily snapshots                        → gas_prices_history.json
GitHub Actions (2x daily)              → GitHub Pages (static JSON)
Widget (HTML/CSS/JS)                   → loads JSON via fetch → renders in iframe
```

### Scraper (`scrape_gas_prices.py`)
- **Playwright + Webshare rotating residential proxy** ($3.50/month, p.webshare.io:80)
- Proxy creds in GitHub Secrets: `WEBSHARE_PROXY_USER` (ihxbejbk-US-rotate), `WEBSHARE_PROXY_PASS`
- Scrapes 15 Wisconsin cities × 4 fuel types (Regular, Mid-Grade, Premium, Diesel)
- **Fuel type switching**: GasBuddy uses a React-controlled hidden `<select>`. We inject JavaScript to set the native value + dispatch React events:
  ```js
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
  setter.call(select, fuelValue);
  select.dispatchEvent(new Event('input', { bubbles: true }));
  select.dispatchEvent(new Event('change', { bubbles: true }));
  ```
- **Fuel Insights** (`fuelinsights.gasbuddy.com/Home/US/Wisconsin`): Scraped for yesterday/week/month/year ago comparisons (Regular only). Requires geolocation denied. Results cached to `docs/fuel_insights_cache.json` so stale data is used when scraping fails.
- **EIA API**: Weekly Midwest/PADD 2 data for all 4 fuel types. Key in `EIA_API_KEY` secret.
- **Retry logic**: Per-city retry with fresh browser context on failure. Auto-refreshes context after 2 consecutive failures (proxy IP rotation).
- **Stale price detection**: If first 3 prices match previous fuel type, waits longer before reading (catches fuel dropdown not updating).

### Known Scraper Quirks
- GasBuddy's fuel selection persists between page navigations via cookie/session — scraper must explicitly switch to each fuel type including Regular
- Fuel Insights page requires JS rendering + geolocation denial — sometimes fails in headless mode, hence the cache
- Some cities occasionally missing individual fuel types (crowd-sourced data gaps)
- Milwaukee mid_grade sometimes missing from GasBuddy

### Widget (`docs/index.html`)
- Single-file HTML/CSS/JS, ~60KB with embedded assets
- **WPR design system**: teal (#3e847a), cream backgrounds, Playfair Display / DM Sans / Source Sans 3 / JetBrains Mono, WPR circular logo as base64
- **Three tabs**: Statewide (time comparison bars OR low/avg/high cards with city dropdown), By Metro Area (15 city cards), Price Trends (EIA SVG chart)
- **Fuel toggle**: Regular / Mid-Grade / Premium / Diesel — works across all tabs
- **Statewide tab**: Shows bar chart (Today/Yesterday/Week Ago/Month Ago/Year Ago) when historical data available for selected fuel; falls back to card layout (Lowest/State Avg/Highest) with city comparison dropdown and range bar
- **City comparison**: Dropdown selector adds a teal dot to the range bar and updates the middle card
- **Daily deltas**: Uses `getHistoricalPrice()` which checks Fuel Insights data first, then falls back to `gas_prices_history.json`
- **Price Trends**: Interactive SVG chart with hover tooltips, 6/12 month toggle, built from EIA weekly data

### Build Process for Widget
The widget template is at `/home/claude/widget_template.html` (during build sessions). It uses placeholder tokens that get replaced by a Python build script:
- `/*LOGO_LINE*/` → WPR logo base64 const
- `/*FALLBACK_LINE*/` → Fallback JSON data const
- `/*EIA_SAMPLE_LINE*/` → EIA sample data const
- `/*BUILDCHART_FN*/` → SVG chart builder function

### Data Format (`gas_prices.json`)
```json
{
  "source": "GasBuddy",
  "price_date": "03/29/26",
  "statewide": {
    "current_avg": {"regular": 3.497, "mid_grade": 3.946, "premium": 4.449, "diesel": 4.628},
    "low": {"regular": 3.19, ...},
    "high": {"regular": 3.63, ...},
    "yesterday_avg": {"regular": 3.638},
    "week_ago_avg": {"regular": 3.646},
    "month_ago_avg": {"regular": 2.737},
    "year_ago_avg": {"regular": 3.026}
  },
  "metros": {
    "Wausau": {
      "current_avg": {"regular": 3.606, "mid_grade": 3.997, "premium": 4.555, "diesel": 4.737},
      "low": {"regular": 3.57, ...},
      "high": {"regular": 3.62, ...},
      "station_count": {"regular": 20, ...}
    }
  },
  "priority_metros": ["Wausau", "Eau Claire", "Green Bay", "Appleton", "Madison", "Milwaukee"]
}
```

### History Format (`gas_prices_history.json`)
```json
{
  "03/28/26": {
    "statewide": {"regular": 3.497, "mid_grade": 3.946, ...},
    "Wausau": {"regular": 3.606, ...},
    "Eau Claire": {"regular": 3.282, ...}
  },
  "03/29/26": { ... }
}
```

## Files
```
scrape_gas_prices.py              — Main scraper (Playwright + GasBuddy + EIA)
requirements.txt                  — requests, playwright
.github/workflows/update-gas-prices.yml — GitHub Actions (7AM + 12PM CT)
docs/index.html                   — Full-size widget
docs/gas_prices.json              — Current price data
docs/gas_prices_history.json      — Rolling daily history (400 days max)
docs/eia_weekly.json              — EIA weekly Midwest data
docs/fuel_insights_cache.json     — Cached Fuel Insights historical comparisons
```

## GitHub Secrets
- `EIA_API_KEY` — U.S. Energy Information Administration API key
- `WEBSHARE_PROXY_USER` — Webshare rotating residential proxy username
- `WEBSHARE_PROXY_PASS` — Webshare proxy password

## 15 Wisconsin Cities
Wausau, Eau Claire, Green Bay, Appleton, Madison, Milwaukee, La Crosse, Fond du Lac, Janesville, Kenosha, Oshkosh, Racine, Sheboygan, Superior, Waukesha

## Local Development
```bash
# Run scraper locally (needs proxy env vars)
export WEBSHARE_PROXY_USER=ihxbejbk-US-rotate
export WEBSHARE_PROXY_PASS=ewb0kjzvd35v
export EIA_API_KEY=Afh70JnU1jwu7MecgjqfgSAbwjBjseIvf4cj0g6T
pip install requests playwright
python -m playwright install chromium
python scrape_gas_prices.py --output docs/gas_prices.json

# Serve widget locally
cd docs && python -m http.server 8000
# Open http://localhost:8000
```

## WPR Design System (shared across all WPR widgets)
- **Teal**: #0d7377 (brand), #3e847a (widget accent), #5ab8ad (accent-light)
- **Cream**: #f5f0e8 (brand background)
- **Ink**: #1c1917 (brand text)
- **Typography**: Playfair Display (headlines), Source Sans 3 (body), DM Sans (UI), JetBrains Mono (code/data)
- **Logo**: WPR circular logo, embedded as base64 PNG in widgets
