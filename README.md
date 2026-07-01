# WPR Wisconsin Gas Prices Widget

**A zero-maintenance gas price widget for Wausau Pilot & Review.**

A scraper runs automatically in the cloud (GitHub Actions), updates a set of JSON
files twice daily, and the widget on the website reads that data. No servers to
manage, nothing to run by hand.

---

## How It Works

```
  GitHub Actions               GitHub Pages              WPR Website
  (runs twice daily)           (hosts the data)          (shows the widget)
 ┌────────────────┐          ┌────────────────┐         ┌──────────────────┐
 │ Scrapes        │          │ gas_prices.json│         │                  │
 │  • GasBuddy    │──saves──▶ │ + history      │◀─reads──│ Embedded widget  │
 │  • EIA trends  │          │ + EIA + cache  │         │ in WordPress     │
 └────────────────┘          └────────────────┘         └──────────────────┘
```

**Day-to-day involvement: none.** It just runs. If prices look stale on the widget,
check the **Actions** tab on GitHub.

---

## Where the Data Comes From

The scraper pulls from the cloud — no proxies, no paid services beyond a free API key:

1. **GasBuddy** — per-city station prices and names for 22 Wisconsin cities, via
   GasBuddy's GraphQL API. Uses `curl_cffi` (Chrome impersonation) to fetch like a
   real browser, so no proxy is required.
2. **AAA** — Wisconsin's statewide price trend (today / yesterday / week / month /
   year, all fuels), from AAA's public state page. GasBuddy gives the live station
   detail; AAA gives the historical trend.
3. **EIA** (U.S. Energy Information Administration) — weekly Midwest fuel-price
   trends, plus the U.S. national average and WTI crude for context. Requires a free
   `EIA_API_KEY`; if it's missing, this part is simply skipped and everything else
   still works.

If a city fails on a given run, its **previous price is carried forward** and marked
stale, so the widget never shows blank cities.

---

## One-Time Setup

> Already set up and running. This section is for rebuilding from scratch or moving
> the project to a new account.

### 1. Create the repository

Create a **public** repo named `wpr-gas-prices` (public is required for free GitHub
Pages) and push all the files in this project to it.

### 2. Enable GitHub Pages

Settings → **Pages** → Source: **Deploy from a branch** → branch `main`, folder
`/docs` → **Save**. After a minute the widget is live at:

`https://rowanflynnpilot.github.io/wpr-gas-prices/`

### 3. Enable Actions write permissions

Settings → **Actions** → **General** → **Workflow permissions** → **Read and write
permissions** → **Save**. This lets the scheduled scraper commit fresh data back to
the repo.

### 4. Add the EIA API key

Get a free key at [eia.gov/opendata](https://www.eia.gov/opendata/), then add it
under Settings → **Secrets and variables** → **Actions** → **New repository secret**:

- Name: `EIA_API_KEY`
- Value: *(your key)*

### 5. Test the scraper

**Actions** tab → **Update Gas Prices** → **Run workflow**. Wait 2–5 minutes (the
scraper paces itself to respect GasBuddy's rate limits). A green check means
`docs/gas_prices.json` now has fresh prices.

### 6. Embed on the WPR website

In WordPress, add a **Custom HTML** block where the widget should appear:

```html
<div style="max-width:720px;margin:0 auto;">
  <iframe
    id="wpr-gas-iframe"
    src="https://rowanflynnpilot.github.io/wpr-gas-prices/"
    width="100%"
    height="560"
    frameborder="0"
    style="border:none;border-radius:6px;overflow:hidden;width:100%;"
    title="Wisconsin Gas Prices"
    loading="lazy"
  ></iframe>
</div>
<script>
  // Auto-resize the iframe to fit the widget (height varies by tab).
  window.addEventListener('message', function (e) {
    if (e.origin !== 'https://rowanflynnpilot.github.io') return;
    if (e.data && e.data.type === 'wpr-gas-height' && e.data.height) {
      var f = document.getElementById('wpr-gas-iframe');
      if (f) f.style.height = e.data.height + 'px';
    }
  });
</script>
```

> The `height="560"` is just an initial value; the `<script>` resizes the iframe to
> the exact widget height as the reader switches tabs. If your WordPress setup strips
> `<script>` from Custom HTML blocks, the widget still works — it just keeps the fixed
> height (set it tall enough for the Price Trends tab, ~600px).

### 7. Newsletter image (email digest)

Email can't run the live widget, so the automation also bakes a fresh **PNG** twice a
day — the same data as a self-contained card — at a stable URL:

`https://rowanflynnpilot.github.io/wpr-gas-prices/digest.png`

Drop it into the newsletter as a normal image (link it to the full tracker):

```html
<a href="https://wausaupilotandreview.com/wausau-gas-price-tracker/">
  <img src="https://rowanflynnpilot.github.io/wpr-gas-prices/digest.png"
       alt="Wisconsin gas prices" width="480" style="max-width:100%;height:auto;border:0;">
</a>
```

The image is regenerated on every scheduled run (via a headless Chromium screenshot of
`docs/digest.html`), so it always shows the latest prices. Design lives in
`docs/digest.html`; the renderer is `scripts/render-digest.mjs`.

---

## Schedule

The scraper runs automatically twice daily on a **fixed UTC schedule** (12:00 and
17:00 UTC). GitHub Actions cron does not observe daylight saving, so the Central
local times shift with the season:

| | Central Daylight (Mar–Nov) | Central Standard (Nov–Mar) |
| --- | --- | --- |
| First run | 7:00 AM CDT | 6:00 AM CST |
| Second run | 12:00 PM CDT | 11:00 AM CST |

You can also trigger it anytime from the **Actions** tab. To change the timing, edit
the cron expressions in `.github/workflows/update-gas-prices.yml`
([crontab.guru](https://crontab.guru/) helps).

---

## What the Owner Needs to Know

**Day-to-day: nothing.** The widget updates itself — no logins, no buttons.

**If something seems wrong:**

1. **Prices look old?** → On github.com, open the repo → **Actions** tab. Green checks
   = fine. A red X = a run failed (send Rowan a screenshot).
2. **Widget not showing?** → Confirm the iframe embed is still in the WordPress page;
   WordPress updates sometimes drop Custom HTML blocks.
3. **Force an update?** → **Actions** → **Update Gas Prices** → **Run workflow**.

---

## Local Development

```bash
pip install -r requirements.txt          # requests + curl_cffi

python scrape_gas_prices.py              # writes docs/gas_prices.json
python scrape_gas_prices.py -o out.json  # custom output path

# EIA trends (optional)
export EIA_API_KEY=...                    # Windows cmd: set EIA_API_KEY=...

# preview the widget
cd docs && python -m http.server 8000     # → http://localhost:8000
```

A local run takes a few minutes by design — the scraper batches cities and waits
between batches to avoid GasBuddy rate limits.

---

## Customization

**Which metros appear first** — edit `PRIORITY_METROS` near the top of
`scrape_gas_prices.py`:

```python
PRIORITY_METROS = ["Wausau", "Eau Claire", "Green Bay", "Appleton", "Madison", "Milwaukee"]
```

**Which cities are scraped** — edit the `CITIES` dictionary in the same file.

**Widget appearance** — edit the `:root` CSS variables at the top of
`docs/index.html`.

**Schedule** — edit the cron lines in `.github/workflows/update-gas-prices.yml`.

---

## Troubleshooting

| Problem | Likely cause / fix |
| --- | --- |
| Actions run shows a red X | Open the failed run's log. A "No CSRF token" error means GasBuddy changed their homepage and the scraper needs an update. |
| Some cities show as stale | Those cities failed this run; their last-known price is preserved. Usually self-corrects on the next run. |
| EIA trend data missing | Confirm the `EIA_API_KEY` secret is set. Without it, EIA data is skipped (everything else still works). |
| Prices unchanged for days | Check the Actions tab. If runs are green but data is flat, GasBuddy genuinely hasn't moved. |
| Widget won't load on WPR site | Check the browser console for errors and confirm GitHub Pages is enabled with the `/docs` folder. |

---

## Files Overview

| File | Purpose | Who edits it |
| --- | --- | --- |
| `scrape_gas_prices.py` | The scraper (GasBuddy + EIA) | Rowan |
| `requirements.txt` | Python dependencies (`requests`, `curl_cffi`) | Rarely |
| `.github/workflows/update-gas-prices.yml` | Automation schedule | Rowan |
| `docs/index.html` | The widget UI | Rowan |
| `docs/gas_prices.json` | Live price data | **Never by hand** — the scraper owns it |
| `docs/gas_prices_history.json` | Daily history (last 400 days) | **Never by hand** |
| `docs/eia_weekly.json` | EIA weekly trend series | **Never by hand** |
| `docs/eia_context.json` | EIA national avg + WTI crude | **Never by hand** |
