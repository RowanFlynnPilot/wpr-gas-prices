#!/usr/bin/env python3
"""
Wisconsin Gas Price Scraper for Wausau Pilot & Review
=====================================================
Scrapes AAA gas price data for Wisconsin and saves it as a JSON file
that the frontend widget reads. Run daily via cron.

Usage:
    python scrape_gas_prices.py [--output /path/to/gas_prices.json]

Cron example (runs daily at 7:00 AM):
    0 7 * * * /usr/bin/python3 /opt/gas-widget/scrape_gas_prices.py --output /var/www/html/data/gas_prices.json
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AAA_URL = "https://gasprices.aaa.com/?state=WI"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Metros of particular interest to central Wisconsin readers
PRIORITY_METROS = ["Wausau", "Eau Claire", "Green Bay", "Appleton", "Madison", "Milwaukee-Waukesha"]

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "gas_prices.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scraper helpers
# ---------------------------------------------------------------------------

def fetch_page(url: str) -> str:
    """Fetch the AAA gas prices page HTML, trying multiple methods."""
    import time

    # Method 1: curl_cffi (impersonates Chrome TLS fingerprint — best Cloudflare bypass)
    try:
        from curl_cffi import requests as cffi_requests
        log.info("Fetching %s via curl_cffi (Chrome impersonation)", url)
        resp = cffi_requests.get(url, impersonate="chrome131", timeout=30)
        if resp.status_code == 200 and "Current Avg" in resp.text:
            log.info("curl_cffi succeeded (%d bytes)", len(resp.text))
            return resp.text
        log.warning("curl_cffi got status %d or no price data", resp.status_code)
    except ImportError:
        log.info("curl_cffi not installed, skipping")
    except Exception as e:
        log.warning("curl_cffi failed: %s", e)

    # Method 2: Playwright headless browser
    try:
        from playwright.sync_api import sync_playwright
        log.info("Fetching %s via headless browser", url)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.goto(url, wait_until="commit", timeout=60000)
            for attempt in range(6):
                time.sleep(5)
                html = page.content()
                if "Current Avg" in html:
                    log.info("Playwright succeeded after %ds (%d bytes)", (attempt+1)*5, len(html))
                    browser.close()
                    return html
            browser.close()
            log.warning("Playwright: price data not found after 30s")
    except ImportError:
        log.info("Playwright not installed, skipping")
    except Exception as e:
        log.warning("Playwright failed: %s", e)

    # Method 3: Plain requests (unlikely to work if others failed, but try)
    try:
        log.info("Fetching %s via plain requests", url)
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200 and "Current Avg" in resp.text:
            return resp.text
        log.warning("Plain requests got status %d", resp.status_code)
    except Exception as e:
        log.warning("Plain requests failed: %s", e)

    raise RuntimeError("All fetch methods failed for AAA page")


def fetch_state_averages() -> dict:
    """Fallback: scrape the AAA state averages page for WI data only."""
    log.info("Attempting fallback: AAA state averages page...")
    state_url = "https://gasprices.aaa.com/state-gas-price-averages/"

    html = None

    # Try curl_cffi first
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.get(state_url, impersonate="chrome131", timeout=30)
        if resp.status_code == 200 and "Wisconsin" in resp.text:
            html = resp.text
            log.info("State averages via curl_cffi succeeded")
    except ImportError:
        pass
    except Exception as e:
        log.warning("curl_cffi state averages failed: %s", e)

    # Try Playwright if curl_cffi didn't work
    if not html:
        try:
            import time
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                )
                page = context.new_page()
                page.goto(state_url, wait_until="commit", timeout=60000)
                for attempt in range(6):
                    time.sleep(5)
                    content = page.content()
                    if "Wisconsin" in content:
                        html = content
                        log.info("State averages via Playwright after %ds", (attempt+1)*5)
                        break
                browser.close()
        except Exception as e:
            log.warning("Playwright state averages failed: %s", e)

    if not html:
        log.warning("Could not fetch state averages page")
        return {}

    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        state_text = cells[0].get_text(strip=True)
        if "Wisconsin" in state_text and len(cells) >= 5:
            regular = parse_price(cells[1].get_text(strip=True))
            midgrade = parse_price(cells[2].get_text(strip=True))
            premium = parse_price(cells[3].get_text(strip=True))
            diesel = parse_price(cells[4].get_text(strip=True))

            today = datetime.now(timezone.utc).strftime("%m/%d/%y")
            result = {
                "source": "AAA Gas Prices",
                "source_url": AAA_URL,
                "state": "Wisconsin",
                "price_date": today,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "statewide": {
                    "current_avg": {
                        "regular": regular,
                        "mid_grade": midgrade,
                        "premium": premium,
                        "diesel": diesel,
                    },
                },
                "metros": {},
                "priority_metros": PRIORITY_METROS,
            }
            log.info("Fallback got WI prices: regular=%s mid=%s premium=%s diesel=%s", regular, midgrade, premium, diesel)
            return result

    log.warning("Wisconsin row not found in state averages table")
    return {}


def parse_price(text: str) -> float | None:
    """Extract a numeric price from text like '$2.445'."""
    match = re.search(r"\$?([\d]+\.[\d]+)", text.strip())
    if match:
        return float(match.group(1))
    return None


def parse_price_table(table) -> dict:
    """Parse a standard AAA price comparison table into a dict."""
    rows = table.find_all("tr")
    data = {}
    for row in rows:
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        if not label or label in ("", "Regular", "Mid-Grade", "Mid", "Premium", "Diesel"):
            continue
        prices = {}
        fuel_types = ["regular", "mid_grade", "premium", "diesel"]
        for i, fuel in enumerate(fuel_types):
            cell_idx = i + 1
            if cell_idx < len(cells):
                price = parse_price(cells[cell_idx].get_text(strip=True))
                if price is not None:
                    prices[fuel] = price
        if prices:
            # Normalize label
            key = label.lower().replace(" ", "_").replace(".", "")
            data[key] = prices
    return data


def parse_date_from_page(soup) -> str:
    """Try to extract the 'Price as of' date from the page."""
    text = soup.get_text()
    match = re.search(r"Price as of\s+(\d{1,2}/\d{1,2}/\d{2,4})", text)
    if match:
        return match.group(1)
    return datetime.now(timezone.utc).strftime("%m/%d/%y")


# ---------------------------------------------------------------------------
# Main scrape logic
# ---------------------------------------------------------------------------

def scrape_gas_prices() -> dict:
    """Scrape AAA Wisconsin gas prices and return structured data."""
    html = fetch_page(AAA_URL)
    soup = BeautifulSoup(html, "html.parser")

    price_date = parse_date_from_page(soup)

    # --- State-wide averages ---
    # The first large table is the statewide average
    tables = soup.find_all("table")
    statewide = {}
    if tables:
        statewide = parse_price_table(tables[0])

    # --- Metro averages ---
    # Each metro is inside a section with an <h3> header
    metros = {}
    metro_headers = soup.find_all("h3")
    for header in metro_headers:
        metro_name = header.get_text(strip=True)
        if not metro_name:
            continue
        # Find the next table after this header
        table = header.find_next("table")
        if table:
            metro_data = parse_price_table(table)
            if metro_data:
                metros[metro_name] = metro_data

    # --- Build output ---
    result = {
        "source": "AAA Gas Prices",
        "source_url": AAA_URL,
        "state": "Wisconsin",
        "price_date": price_date,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "statewide": statewide,
        "metros": metros,
        "priority_metros": PRIORITY_METROS,
    }

    # Quick validation
    if not statewide:
        log.warning("No statewide data found — page structure may have changed.")
    if not metros:
        log.warning("No metro data found — page structure may have changed.")

    log.info(
        "Scraped: statewide rows=%d, metros=%d, date=%s",
        len(statewide), len(metros), price_date,
    )
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape Wisconsin gas prices from AAA")
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_OUTPUT,
        help="Path to write JSON output (default: docs/gas_prices.json)",
    )
    args = parser.parse_args()

    aaa_success = False
    try:
        data = scrape_gas_prices()
        # Check if we actually got data
        if data.get("statewide") and len(data["statewide"]) > 0:
            aaa_success = True
        else:
            log.warning("Main scrape returned no data, trying state averages fallback...")
            data = fetch_state_averages()
            if data.get("statewide"):
                aaa_success = True
    except Exception:
        log.exception("Failed to scrape AAA detail page, trying state averages fallback...")
        try:
            data = fetch_state_averages()
            if data.get("statewide"):
                aaa_success = True
        except Exception:
            log.exception("State averages fallback also failed")
            data = None

    # Ensure output directory exists
    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)

    if aaa_success and data:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info("Wrote gas prices to %s", args.output)
    else:
        log.warning("Skipping AAA JSON write — using previous data")

    # ── Append to rolling history ──
    if aaa_success and data:
        history_path = os.path.join(out_dir, "gas_prices_history.json")
        today_key = data.get("price_date", datetime.now(timezone.utc).strftime("%m/%d/%y"))

        # Load existing history or start fresh
        history = {}
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, OSError):
                log.warning("Could not read history file, starting fresh.")
                history = {}

        # Build today's entry: statewide + each metro, current_avg only
        entry = {}
        sw = data.get("statewide", {}).get("current_avg", {})
        if sw:
            entry["statewide"] = sw

        for metro_name, metro_data in data.get("metros", {}).items():
            current = metro_data.get("current_avg", {})
            if current:
                entry[metro_name] = current

        if entry:
            history[today_key] = entry

        # Trim to last 400 days to keep file size manageable
        if len(history) > 400:
            sorted_keys = sorted(history.keys(), key=lambda k: k)
            for old_key in sorted_keys[: len(history) - 400]:
                del history[old_key]

        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, separators=(",", ":"), ensure_ascii=False)

        log.info("Updated history (%d days) at %s", len(history), history_path)

    # ── Fetch EIA weekly statewide data ──
    eia_path = os.path.join(out_dir, "eia_weekly.json")
    eia_api_key = os.environ.get("EIA_API_KEY", "")

    if eia_api_key:
        log.info("Fetching EIA weekly data (Midwest/PADD 2)...")

        # Wisconsin doesn't have its own EIA weekly retail series.
        # We use Midwest (PADD 2, code R20) which includes Wisconsin.
        eia_duoarea = "R20"

        eia_products = {
            "regular": "EPMR",
            "mid_grade": "EPMM",
            "premium": "EPMP",
            "diesel": "EPD2D",
        }
        eia_base = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
        eia_result = {}

        for fuel, product_code in eia_products.items():
            try:
                url = (
                    f"{eia_base}"
                    f"?api_key={eia_api_key}"
                    f"&frequency=weekly"
                    f"&data[0]=value"
                    f"&facets[duoarea][]={eia_duoarea}"
                    f"&facets[product][]={product_code}"
                    f"&sort[0][column]=period"
                    f"&sort[0][direction]=asc"
                    f"&length=5000"
                )
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                eia_json = resp.json()

                if "error" in eia_json:
                    log.error("  EIA %s API error: %s", fuel, eia_json["error"])
                    continue

                entries = []
                for row in eia_json.get("response", {}).get("data", []):
                    val = row.get("value")
                    if val is not None:
                        try:
                            entries.append({
                                "date": row["period"],
                                "price": float(val),
                            })
                        except (ValueError, KeyError):
                            pass

                entries.sort(key=lambda e: e["date"])
                eia_result[fuel] = entries
                log.info("  EIA %s: %d data points", fuel, len(entries))

            except Exception:
                log.exception("  EIA fetch failed for %s", fuel)

        if eia_result:
            with open(eia_path, "w", encoding="utf-8") as f:
                json.dump(eia_result, f, separators=(",", ":"), ensure_ascii=False)
            log.info("Wrote EIA data to %s", eia_path)
    else:
        log.warning("EIA_API_KEY not set, skipping EIA data fetch.")


if __name__ == "__main__":
    main()
