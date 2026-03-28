#!/usr/bin/env python3
"""
WPR Gas Price Scraper — GasBuddy + EIA Edition
================================================
Scrapes all fuel types from GasBuddy for Wisconsin cities using
Playwright + Webshare residential proxy, plus EIA weekly trend data.
Fully automated via GitHub Actions.
"""

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CITIES = {
    "Wausau": "https://www.gasbuddy.com/gasprices/wisconsin/wausau",
    "Eau Claire": "https://www.gasbuddy.com/gasprices/wisconsin/eau-claire",
    "Green Bay": "https://www.gasbuddy.com/gasprices/wisconsin/green-bay",
    "Appleton": "https://www.gasbuddy.com/gasprices/wisconsin/appleton",
    "Madison": "https://www.gasbuddy.com/gasprices/wisconsin/madison",
    "Milwaukee": "https://www.gasbuddy.com/gasprices/wisconsin/milwaukee",
    "La Crosse": "https://www.gasbuddy.com/gasprices/wisconsin/la-crosse",
    "Fond du Lac": "https://www.gasbuddy.com/gasprices/wisconsin/fond-du-lac",
    "Janesville": "https://www.gasbuddy.com/gasprices/wisconsin/janesville",
    "Kenosha": "https://www.gasbuddy.com/gasprices/wisconsin/kenosha",
    "Oshkosh": "https://www.gasbuddy.com/gasprices/wisconsin/oshkosh",
    "Racine": "https://www.gasbuddy.com/gasprices/wisconsin/racine",
    "Sheboygan": "https://www.gasbuddy.com/gasprices/wisconsin/sheboygan",
    "Superior": "https://www.gasbuddy.com/gasprices/wisconsin/superior",
    "Waukesha": "https://www.gasbuddy.com/gasprices/wisconsin/waukesha",
}

FUEL_TYPES = {
    "1": "regular",
    "2": "mid_grade",
    "3": "premium",
    "4": "diesel",
}

PRIORITY_METROS = ["Wausau", "Eau Claire", "Green Bay", "Appleton", "Madison", "Milwaukee"]
DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "gas_prices.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

def get_proxy_config():
    user = os.environ.get("WEBSHARE_PROXY_USER", "")
    pwd = os.environ.get("WEBSHARE_PROXY_PASS", "")
    if user and pwd:
        return {"server": "http://p.webshare.io:80", "username": user, "password": pwd}
    return None

# ---------------------------------------------------------------------------
# GasBuddy scraper (Playwright)
# ---------------------------------------------------------------------------

def get_prices_from_page(page):
    """Extract visible station prices from the current page state."""
    prices = []
    elements = page.query_selector_all('[class*="StationDisplayPrice-module__price"]')
    for el in elements:
        try:
            text = el.inner_text()
            match = re.search(r'\$?([\d]+\.[\d]+)', text)
            if match:
                price = float(match.group(1))
                if 1.0 < price < 10.0:
                    prices.append(price)
        except Exception:
            pass
    return prices


def switch_fuel_type(page, fuel_value):
    """Switch the fuel type dropdown via JavaScript injection."""
    page.evaluate(f"""
        (() => {{
            const select = document.querySelector('#fuelType');
            if (!select) return;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLSelectElement.prototype, 'value'
            ).set;
            setter.call(select, '{fuel_value}');
            select.dispatchEvent(new Event('input', {{ bubbles: true }}));
            select.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }})()
    """)


def scrape_city(page, city_name, city_url):
    """Navigate to a city page and scrape all fuel types."""
    log.info("  %s: loading...", city_name)
    try:
        page.goto(city_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log.warning("  %s: page load failed — %s", city_name, e)
        return None

    # Wait for initial prices
    try:
        page.wait_for_selector('[class*="StationDisplayPrice"]', timeout=15000)
    except Exception:
        time.sleep(3)

    city_data = {"current_avg": {}, "low": {}, "high": {}, "station_count": {}}

    for fuel_value, fuel_key in FUEL_TYPES.items():
        if fuel_value != "1":
            switch_fuel_type(page, fuel_value)
            time.sleep(4)

        prices = get_prices_from_page(page)
        if prices:
            city_data["current_avg"][fuel_key] = round(statistics.mean(prices), 3)
            city_data["low"][fuel_key] = round(min(prices), 3)
            city_data["high"][fuel_key] = round(max(prices), 3)
            city_data["station_count"][fuel_key] = len(prices)

    # Log summary
    reg = city_data["current_avg"].get("regular")
    prem = city_data["current_avg"].get("premium")
    diesel = city_data["current_avg"].get("diesel")
    log.info("  %s: reg=$%s, prem=$%s, diesel=$%s",
             city_name,
             f"{reg:.3f}" if reg else "—",
             f"{prem:.3f}" if prem else "—",
             f"{diesel:.3f}" if diesel else "—")

    return city_data if city_data["current_avg"] else None


def scrape_gasbuddy():
    """Scrape all Wisconsin cities from GasBuddy using Playwright."""
    from playwright.sync_api import sync_playwright

    proxy_config = get_proxy_config()
    log.info("Scraping GasBuddy for %d Wisconsin cities (all fuel types)...", len(CITIES))

    metros = {}

    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled",
                     "--disable-dev-shm-usage"],
        }
        if proxy_config:
            launch_args["proxy"] = proxy_config

        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        for city_name, city_url in CITIES.items():
            data = scrape_city(page, city_name, city_url)
            if data:
                metros[city_name] = data
            time.sleep(1)

        browser.close()

    # Compute statewide averages across all cities
    statewide = {"current_avg": {}, "low": {}, "high": {}}
    for fuel_key in ["regular", "mid_grade", "premium", "diesel"]:
        all_avgs = [m["current_avg"][fuel_key] for m in metros.values()
                    if fuel_key in m.get("current_avg", {})]
        all_lows = [m["low"][fuel_key] for m in metros.values()
                    if fuel_key in m.get("low", {})]
        all_highs = [m["high"][fuel_key] for m in metros.values()
                     if fuel_key in m.get("high", {})]
        if all_avgs:
            statewide["current_avg"][fuel_key] = round(statistics.mean(all_avgs), 3)
            statewide["low"][fuel_key] = round(min(all_lows), 3)
            statewide["high"][fuel_key] = round(max(all_highs), 3)

    reg = statewide["current_avg"].get("regular")
    log.info("Statewide avg: reg=$%s (%d cities)",
             f"{reg:.3f}" if reg else "—", len(metros))

    today = datetime.now(timezone.utc).strftime("%m/%d/%y")
    return {
        "source": "GasBuddy",
        "source_url": "https://www.gasbuddy.com/gasprices/wisconsin",
        "state": "Wisconsin",
        "price_date": today,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "statewide": statewide,
        "metros": metros,
        "priority_metros": PRIORITY_METROS,
    }

# ---------------------------------------------------------------------------
# EIA trend data
# ---------------------------------------------------------------------------

def fetch_eia_data(out_dir):
    eia_path = os.path.join(out_dir, "eia_weekly.json")
    api_key = os.environ.get("EIA_API_KEY", "")
    if not api_key:
        log.warning("EIA_API_KEY not set, skipping.")
        return

    log.info("Fetching EIA weekly data (Midwest/PADD 2)...")
    products = {"regular": "EPMR", "mid_grade": "EPMM", "premium": "EPMP", "diesel": "EPD2D"}
    base = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
    result = {}

    for fuel, code in products.items():
        try:
            url = (f"{base}?api_key={api_key}&frequency=weekly&data[0]=value"
                   f"&facets[duoarea][]=R20&facets[product][]={code}"
                   f"&sort[0][column]=period&sort[0][direction]=asc&length=5000")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            entries = []
            for row in resp.json().get("response", {}).get("data", []):
                val = row.get("value")
                if val is not None:
                    try:
                        entries.append({"date": row["period"], "price": float(val)})
                    except (ValueError, KeyError):
                        pass
            entries.sort(key=lambda e: e["date"])
            result[fuel] = entries
            log.info("  EIA %s: %d data points", fuel, len(entries))
        except Exception:
            log.exception("  EIA fetch failed for %s", fuel)

    if result:
        with open(eia_path, "w", encoding="utf-8") as f:
            json.dump(result, f, separators=(",", ":"), ensure_ascii=False)
        log.info("Wrote EIA data to %s", eia_path)

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def update_history(data, out_dir):
    history_path = os.path.join(out_dir, "gas_prices_history.json")
    today_key = data.get("price_date", datetime.now(timezone.utc).strftime("%m/%d/%y"))
    history = {}
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = {}

    entry = {}
    sw = data.get("statewide", {}).get("current_avg", {})
    if sw:
        entry["statewide"] = sw
    for name, md in data.get("metros", {}).items():
        c = md.get("current_avg", {})
        if c:
            entry[name] = c
    if entry:
        history[today_key] = entry

    if len(history) > 400:
        for k in sorted(history.keys())[: len(history) - 400]:
            del history[k]

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, separators=(",", ":"), ensure_ascii=False)
    log.info("Updated history (%d days)", len(history))

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)

    gb_success = False
    try:
        data = scrape_gasbuddy()
        if data.get("statewide", {}).get("current_avg"):
            gb_success = True
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info("Wrote gas prices to %s", args.output)
            update_history(data, out_dir)
        else:
            log.warning("No statewide data from GasBuddy")
    except Exception:
        log.exception("GasBuddy scrape failed — will still update EIA data")

    fetch_eia_data(out_dir)

    if not gb_success:
        log.warning("GasBuddy failed but EIA data was updated.")
    log.info("Done!")


if __name__ == "__main__":
    main()
