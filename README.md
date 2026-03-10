# WPR Wisconsin Gas Prices Widget

**A zero-maintenance gas price widget for Wausau Pilot & Review.**

The scraper runs automatically in the cloud (GitHub Actions), updates a JSON file twice daily, and the widget on your website reads that data. No servers to manage, no code to run.

---

## How It Works

```
  GitHub Actions             GitHub Pages            Your WPR Website
  (runs daily)               (hosts the data)        (shows the widget)
 ┌──────────────┐          ┌──────────────┐         ┌──────────────────┐
 │ Scrapes AAA  │──saves──▶│gas_prices.json│◀─reads──│ Embedded widget  │
 │ gas prices   │          │ (public URL)  │         │ in WordPress     │
 └──────────────┘          └──────────────┘         └──────────────────┘
```

**Your mother's involvement: None.** It just runs. If prices look stale on the widget, check the Actions tab on GitHub.

---

## One-Time Setup (15–20 minutes)

### Step 1: Create a GitHub Account

If you don't already have one, sign up at [github.com](https://github.com/signup). The free tier is all you need.

### Step 2: Create a New Repository

1. Go to [github.com/new](https://github.com/new)
2. Name it: `wpr-gas-prices`
3. Set it to **Public** (required for free GitHub Pages)
4. Click **Create repository**

### Step 3: Upload the Files

Upload ALL the files from this project folder to your new repository. The structure should look like this:

```
wpr-gas-prices/
├── .github/
│   └── workflows/
│       └── update-gas-prices.yml   ← The automated schedule
├── docs/
│   ├── index.html                  ← The widget (also viewable directly)
│   └── gas_prices.json             ← Gas price data (updated by scraper)
├── scrape_gas_prices.py            ← The scraper script
├── requirements.txt                ← Python dependencies
└── README.md                       ← This file
```

**How to upload:** On your repo page, click **"Add file" → "Upload files"**, then drag the entire folder contents in. Or use Git from the command line if you prefer.

### Step 4: Enable GitHub Pages

1. Go to your repo → **Settings** → **Pages** (in the left sidebar)
2. Under **Source**, select **Deploy from a branch**
3. Set branch to `main` and folder to `/docs`
4. Click **Save**
5. After a minute, your widget will be live at:
   `https://YOUR-USERNAME.github.io/wpr-gas-prices/`

### Step 5: Enable Actions Permissions

1. Go to your repo → **Settings** → **Actions** → **General**
2. Under **Workflow permissions**, select **"Read and write permissions"**
3. Click **Save**

This allows the scraper to commit updated data back to the repo.

### Step 6: Test the Scraper

1. Go to your repo → **Actions** tab
2. Click **"Update Gas Prices"** in the left sidebar
3. Click **"Run workflow"** → **"Run workflow"** (the green button)
4. Wait 1–2 minutes for it to complete (green checkmark = success)
5. Check `docs/gas_prices.json` — it should now have today's real prices

### Step 7: Embed on the WPR Website

In WordPress, add a **Custom HTML** block wherever you want the widget to appear. Paste this code, replacing `YOUR-USERNAME` with your actual GitHub username:

```html
<div style="max-width:680px;margin:0 auto;">
  <iframe
    src="https://YOUR-USERNAME.github.io/wpr-gas-prices/"
    width="100%"
    height="520"
    frameborder="0"
    style="border:none;border-radius:6px;overflow:hidden;"
    title="Wisconsin Gas Prices"
    loading="lazy"
  ></iframe>
</div>
```

**That's it. You're done.** The widget will update itself every day.

---

## Schedule

The scraper runs automatically at:
- **7:00 AM Central Time** (daily)
- **12:00 PM Central Time** (daily)

You can also trigger it manually anytime from the Actions tab.

---

## What Your Mother Needs to Know

### Day-to-day: Nothing!

The widget updates itself. No logins, no buttons to press.

### If something seems wrong:

1. **Prices look old?** → Go to github.com, open the repository, click the **Actions** tab. Green checkmarks = everything's fine. Red X = something failed (share a screenshot with Rowan).
2. **Widget isn't showing?** → Check that the iframe embed code is still in the WordPress page. Sometimes WordPress updates can remove Custom HTML blocks.
3. **Need to run it manually?** → Actions tab → "Update Gas Prices" → "Run workflow"

---

## Customization

### Change Which Metros Appear First

Edit `scrape_gas_prices.py`, find the `PRIORITY_METROS` line near the top, and reorder or swap cities:

```python
PRIORITY_METROS = ["Wausau", "Eau Claire", "Green Bay", "Appleton", "Madison", "Milwaukee-Waukesha"]
```

### Change the Widget Colors

Edit `docs/index.html`, find the `:root` CSS variables near the top:

```css
--accent: #b8232a;   /* Red accent — change to match WPR branding */
--ink: #1a1a1a;      /* Dark text */
--paper: #fafaf7;    /* Background */
```

### Change the Schedule

Edit `.github/workflows/update-gas-prices.yml` and adjust the cron expressions. Use [crontab.guru](https://crontab.guru/) to help write cron schedules.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Actions tab shows red X | Click into the failed run to see the error log. Usually means AAA changed their page layout. |
| Widget shows "sample data" in footer | The JSON fetch failed — check that GitHub Pages is enabled and the URL is correct. |
| Prices haven't changed in days | Check the Actions tab. If runs are green but data is unchanged, AAA may not have updated. |
| Widget doesn't load on WPR site | Check browser console for CORS errors. GitHub Pages should handle CORS correctly for public repos. |

---

## Files Overview

| File | Purpose | Who Edits It |
|---|---|---|
| `scrape_gas_prices.py` | Fetches prices from AAA | Rowan (if AAA changes their site) |
| `requirements.txt` | Python packages needed | Rarely changes |
| `.github/workflows/update-gas-prices.yml` | Automation schedule | Rowan (to change timing) |
| `docs/index.html` | The widget itself | Rowan (to change design/colors) |
| `docs/gas_prices.json` | Live price data | **Never edit manually** — the scraper updates this |
