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
   detail; AAA gives the historical trend. AAA is fetched **independently**, so it
   keeps updating even on days GasBuddy can't be reached. The same AAA pages also
   supply the widget's **"Across the border"** strip (Minnesota, Iowa, Illinois,
   Michigan vs. Wisconsin).
3. **EIA** (U.S. Energy Information Administration) — weekly Midwest fuel-price
   trends, plus the U.S. national average and WTI crude for context. Requires a free
   `EIA_API_KEY`; if it's missing, this part is simply skipped and everything else
   still works.

If a city fails on a given run, its **previous price is carried forward** and marked
stale, so the widget never shows blank cities.

### Where it runs

The update runs **in the cloud**, twice a day, on GitHub Actions. It scrapes every
source, re-renders the newsletter image and publishes; the site updates a minute or so
later. Nothing on your machine has to be awake for it.

Your machine is the **standby**. Windows Task Scheduler (`WPRGasPrices-Update`) still
starts `scripts/update-gas-prices.ps1` at 7am and 7pm Central, but it now looks at the
published data first and exits in a second or two when that data is fresh. It only
does the full scrape when the cloud run hasn't delivered — data older than 10 hours,
or cities missing.

Why the standby exists: GasBuddy used to block GitHub's servers, which froze the
widget for five days in July 2026, and a home connection got through. That block has
been gone since mid-August. Running both anyway meant scraping four times a day, and
on September 17 GasBuddy rate-limited the home connection — the 7pm run got only 4 of
22 cities. The standby arrangement keeps the fallback without the extra load, and
switches over on its own if the cloud run ever stops delivering.

If a run is anything less than healthy — a scraper error, a failed push, or a
*partial* scrape where most cities fell back to carried-forward prices — a GitHub
issue is opened ("Local gas-price runner failing" for your machine, "Gas scraper needs
attention" for the cloud run), and closed again after the next healthy run. Task
Scheduler hides the console, so the issue is how a local problem reaches you.

**Story nudges:** when the statewide average moves enough to be newsworthy (5¢+ in a
day or 10¢+ in a week, per AAA), a GitHub issue titled **"Fuel Watch: notable
gas-price move"** appears with a ready-to-quote sentence. If it's still open when a
*different* move happens, the new figure is added as a comment, so a bigger move can
never be swallowed by an unread issue. Closing it means you're done with that move —
it won't come back for the same one, only for the next new move. It's a heads-up, not
an error.

To run it by hand at any time:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update-gas-prices.ps1
```

Add `-NoPush` to do everything except publish, which is useful for a dry run.

### When GasBuddy can't be reached

GasBuddy sits behind Cloudflare, which sometimes blocks GitHub's servers outright for
days at a stretch. When that happens the widget does **not** go blank or silently show
old numbers as if they were new:

- AAA's statewide trend and the newsroom blurb still refresh normally.
- Station-level and per-metro prices hold their last known values.
- The header switches to an amber "Updated N days ago", so the staleness is visible.
- An issue is opened on the repo saying which sources were reachable; it closes
  itself once a healthy run succeeds.

Nothing needs to be done by hand — it recovers on its own when the block lifts.

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

In WordPress, add a **Custom HTML** block where the widget should appear.

**Full widget** (720px, tabs, trends chart):

```html
<iframe src="https://rowanflynnpilot.github.io/wpr-gas-prices/" title="Wisconsin Gas Prices from Wausau Pilot &amp; Review" style="display:block;width:100%;max-width:720px;height:clamp(930px, calc(1960px - 159vw), 1400px);margin:0 auto;border:0;"></iframe>
```

**Compact widget** (360px, for sidebars and narrow spots):

```html
<iframe src="https://rowanflynnpilot.github.io/wpr-gas-prices/index-compact.html" title="Wisconsin Gas Prices from Wausau Pilot &amp; Review" style="display:block;width:100%;max-width:360px;height:610px;margin:0 auto;border:0;"></iframe>
```

Both were tested on wausaupilotandreview.com (Sept 2026): they save without error and
render in a post on the live theme.

> **Never put any `<script>` tag in a WPR embed — not even `<script src>`.**
> Cloudflare's firewall in front of wausaupilotandreview.com blocks the save request
> for any post containing one, and WordPress shows *"Updating failed. The response is
> not a valid JSON response."* That's what caused the intermittent JSON errors.
> So these snippets are bare iframes with no script:
>
> - The **compact** widget is 599px tall at every width, so it gets a fixed 610px.
> - The **full** widget grows taller as screens get narrower. The `clamp()` height is
>   930px on desktop and grows to ~1360px on phones. It was measured to fit the
>   default Statewide tab at 375, 430, 500 and 720px wide. Taller tabs (By Metro Area)
>   scroll inside the frame.
>
> `docs/embed.js` can resize the frame to the exact height on every tab, but it needs
> a `<script src>` tag. It only becomes usable if the site admin adds a Cloudflare
> WAF exception for logged-in post saves.

### 7. Newsletter image (email digest)

Email can't run the live widget, so the automation also bakes a fresh **PNG** twice a
day — the same data as a self-contained card — at a stable URL:

`https://rowanflynnpilot.github.io/wpr-gas-prices/digest.png`

Drop it into the newsletter as a normal image, with a text link below it to the full
tracker:

```html
<a href="https://wausaupilotandreview.com/wausau-gas-price-tracker/">
  <img src="https://rowanflynnpilot.github.io/wpr-gas-prices/digest.png"
       alt="Wisconsin gas prices" width="480" style="max-width:100%;height:auto;border:0;">
</a>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:14px 0 0;">
  <tr><td align="center">
    <a href="https://wausaupilotandreview.com/wausau-gas-price-tracker/"
       style="display:inline-block;padding:12px 28px;background:#3e847a;color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:bold;line-height:1;text-decoration:none;border-radius:6px;">Access the full gas price tracker&nbsp;&rarr;</a>
  </td></tr>
</table>
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
