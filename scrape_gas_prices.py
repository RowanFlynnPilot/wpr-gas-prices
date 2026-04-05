#!/usr/bin/env python3
"""
WPR Gas Price Scraper — GasBuddy GraphQL + EIA Edition
=======================================================
Fetches all fuel types from GasBuddy for Wisconsin cities via their
GraphQL API using curl_cffi (Chrome impersonation — no proxy needed),
plus EIA weekly trend data.
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
    "Wausau":      "Wausau, WI",
    "Eau Claire":  "Eau Claire, WI",
    "Green Bay":   "Green Bay, WI",
    "Appleton":    "Appleton, WI",
    "Madison":     "Madison, WI",
    "Milwaukee":   "Milwaukee, WI",
    "La Crosse":   "La Crosse, WI",
    "Fond du Lac": "Fond du Lac, WI",
    "Janesville":  "Janesville, WI",
    "Kenosha":     "Kenosha, WI",
    "Oshkosh":     "Oshkosh, WI",
    "Racine":      "Racine, WI",
    "Sheboygan":   "Sheboygan, WI",
    "Superior":    "Superior, WI",
    "Waukesha":    "Waukesha, WI",
}

# GasBuddy fuelProduct values → our internal keys
FUEL_MAP = {
    "regular_gas":  "regular",
    "midgrade_gas": "mid_grade",
    "premium_gas":  "premium",
    "diesel":       "diesel",
}

PRIORITY_METROS = ["Wausau", "Eau Claire", "Green Bay", "Appleton", "Madison", "Milwaukee"]
DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "gas_prices.json")

GASBUDDY_HOME    = "https://www.gasbuddy.com/home"
GASBUDDY_GRAPHQL = "https://www.gasbuddy.com/graphql"
FUEL_INSIGHTS_URL = "https://fuelinsights.gasbuddy.com/Home/US/Wisconsin"

# GraphQL query: stations with prices + statewide trend data
LOCATION_QUERY = (
    "query LocationBySearchTerm("
    "$brandId: Int, $cursor: String, $fuel: Int, $lat: Float, "
    "$lng: Float, $maxAge: Int, $search: String) { "
    "locationBySearchTerm(lat: $lat, lng: $lng, search: $search) { "
    "stations(brandId: $brandId cursor: $cursor fuel: $fuel lat: $lat "
    "lng: $lng maxAge: $maxAge) { results { "
    "prices { cash { price } credit { price } fuelProduct } } } "
    "trends { areaName country today todayLow trend } } }"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GasBuddy GraphQL helpers
# ---------------------------------------------------------------------------

def get_csrf_token(session) -> str:
    """Fetch GasBuddy homepage and extract the CSRF token."""
    try:
        resp = session.get(GASBUDDY_HOME, timeout=20)
        resp.raise_for_status()
        match = re.search(r"window\.gbcsrf\s*=\s*[\"'](.*?)[\"']", resp.text)
        if match:
            token = match.group(1)
            log.info("CSRF token obtained (%.10s...)", token)
            return token
        log.warning("CSRF token not found in homepage HTML")
    except Exception as e:
        log.warning("Failed to fetch CSRF token: %s", e)
    return ""


def make_graphql_headers(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "apollo-require-preflight": "true",
        "Origin": "https://www.gasbuddy.com",
        "Referer": GASBUDDY_HOME,
        "gbcsrf": token,
    }


def scrape_city_graphql(session, city_name: str, search_term: str, headers: dict) -> dict | None:
    """Query GasBuddy GraphQL for a single city and return structured price data.

    Retries once on HTTP 429 (rate limit) with a backoff delay.
    """
    payload = {
        "operationName": "LocationBySearchTerm",
        "query": LOCATION_QUERY,
        "variables": {"maxAge": 0, "search": search_term},
    }
    for attempt in range(3):
        try:
            resp = session.post(GASBUDDY_GRAPHQL, json=payload, headers=headers, timeout=20)
            if resp.status_code == 429:
                wait = 8 * (attempt + 1)
                log.warning("  %s: 429 rate limited — waiting %ds (attempt %d/3)",
                            city_name, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
            break
        except Exception as e:
            log.warning("  %s: request failed — %s", city_name, e)
            return None
    else:
        log.warning("  %s: all attempts rate-limited", city_name)
        return None

    try:
        results = body["data"]["locationBySearchTerm"]["stations"]["results"]
    except (KeyError, TypeError):
        log.warning("  %s: unexpected response shape", city_name)
        return None

    if not results:
        log.warning("  %s: no stations returned", city_name)
        return None

    # Collect prices by fuel type
    fuel_prices: dict[str, list[float]] = {}
    for station in results:
        for price_node in station.get("prices", []):
            fp = price_node.get("fuelProduct")
            fuel_key = FUEL_MAP.get(fp)
            if not fuel_key:
                continue
            raw = (price_node.get("credit") or price_node.get("cash") or {}).get("price")
            if raw is not None:
                try:
                    p = float(raw)
                    if 1.0 < p < 10.0:
                        fuel_prices.setdefault(fuel_key, []).append(p)
                except (ValueError, TypeError):
                    pass

    if not fuel_prices.get("regular"):
        log.warning("  %s: no regular prices found", city_name)
        return None

    city_data: dict = {"current_avg": {}, "low": {}, "high": {}, "station_count": {}}
    for fuel_key, prices in fuel_prices.items():
        city_data["current_avg"][fuel_key] = round(statistics.mean(prices), 3)
        city_data["low"][fuel_key]         = round(min(prices), 3)
        city_data["high"][fuel_key]        = round(max(prices), 3)
        city_data["station_count"][fuel_key] = len(prices)

    reg = city_data["current_avg"].get("regular")
    log.info(
        "  %s: reg=$%.3f, mid=$%s, prem=$%s, diesel=$%s (%d stations)",
        city_name, reg,
        f"{city_data['current_avg'].get('mid_grade', 0):.3f}" if city_data["current_avg"].get("mid_grade") else "—",
        f"{city_data['current_avg'].get('premium', 0):.3f}"   if city_data["current_avg"].get("premium")   else "—",
        f"{city_data['current_avg'].get('diesel', 0):.3f}"    if city_data["current_avg"].get("diesel")    else "—",
        len(results),
    )
    return city_data


# ---------------------------------------------------------------------------
# Fuel Insights (historical statewide comparisons — best-effort)
# ---------------------------------------------------------------------------

def scrape_fuel_insights(session) -> dict:
    """Scrape statewide historical comparisons from GasBuddy Fuel Insights."""
    log.info("  Scraping Fuel Insights for Wisconsin historical data...")
    try:
        resp = session.get(FUEL_INSIGHTS_URL, timeout=20)
        text = resp.text
    except Exception as e:
        log.warning("  Fuel Insights fetch failed: %s", e)
        return {}

    if "Yesterday" not in text:
        log.warning("  Fuel Insights: 'Yesterday' not found in response (%d chars)", len(text))
        return {}

    result = {}
    for key, patterns in [
        ("yesterday_avg", [
            r"Yesterday'?s?\s+Avg\*?\s+of\s+\$([\d.]+)",
            r"Yesterday'?s?\s+Avg\*?[^$]*\$([\d.]+)",
            r"from\s+Yesterday[^$]*\$([\d.]+)",
        ]),
        ("week_ago_avg", [
            r"Last\s+Week'?s?\s+Avg\*?\s+of\s+\$([\d.]+)",
            r"Last\s+Week'?s?\s+Avg\*?[^$]*\$([\d.]+)",
            r"from\s+Last\s+Week[^$]*\$([\d.]+)",
        ]),
        ("month_ago_avg", [
            r"Last\s+Month'?s?\s+Avg\*?\s+of\s+\$([\d.]+)",
            r"Last\s+Month'?s?\s+Avg\*?[^$]*\$([\d.]+)",
            r"from\s+Last\s+Month[^$]*\$([\d.]+)",
        ]),
        ("year_ago_avg", [
            r"Last\s+Year'?s?\s+Avg\*?\s+of\s+\$([\d.]+)",
            r"Last\s+Year'?s?\s+Avg\*?[^$]*\$([\d.]+)",
            r"from\s+Last\s+Year[^$]*\$([\d.]+)",
        ]),
    ]:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
            if m:
                result[key] = {"regular": float(m.group(1))}
                break

    live = re.search(r"\$([\d.]+)\s*/gal", text)
    if live:
        result["gasbuddy_live_avg"] = {"regular": float(live.group(1))}

    log.info(
        "  Fuel Insights: yest=$%s, week=$%s, month=$%s, year=$%s",
        result.get("yesterday_avg", {}).get("regular", "—"),
        result.get("week_ago_avg",  {}).get("regular", "—"),
        result.get("month_ago_avg", {}).get("regular", "—"),
        result.get("year_ago_avg",  {}).get("regular", "—"),
    )
    return result


# ---------------------------------------------------------------------------
# Main GasBuddy scrape
# ---------------------------------------------------------------------------

def scrape_gasbuddy() -> dict:
    """Scrape all Wisconsin cities from GasBuddy via GraphQL (no proxy needed)."""
    try:
        import curl_cffi.requests as cffi_req
    except ImportError:
        log.error("curl_cffi is not installed. Run: pip install curl_cffi")
        sys.exit(1)

    log.info("Scraping GasBuddy GraphQL for %d Wisconsin cities...", len(CITIES))

    # One shared session with Chrome impersonation (bypasses Cloudflare)
    session = cffi_req.Session(impersonate="chrome")

    # Get CSRF token (required for GraphQL requests)
    token = get_csrf_token(session)
    if not token:
        log.error("Could not obtain CSRF token — aborting GasBuddy scrape")
        raise RuntimeError("No CSRF token")

    headers = make_graphql_headers(token)

    # Scrape Fuel Insights for historical comparisons
    insights = scrape_fuel_insights(session)

    # Scrape all cities sequentially with paced delays.
    # GasBuddy rate-limits datacenter IPs (GitHub Actions uses Azure).
    # A 60s warmup + 5s between cities keeps us within the rate limit window.
    metros: dict = {}
    log.info("Scraping %d cities sequentially (60s warmup + 5s between)...", len(CITIES))
    log.info("Waiting 60s for rate-limit window to reset...")
    time.sleep(60)
    for i, (city_name, search_term) in enumerate(CITIES.items()):
        data = scrape_city_graphql(session, city_name, search_term, headers)
        if data:
            metros[city_name] = data
        else:
            log.warning("  %s: skipped (no usable data)", city_name)
        if i < len(CITIES) - 1:
            time.sleep(5)

    # Compute statewide averages across all scraped cities
    statewide: dict = {"current_avg": {}, "low": {}, "high": {}}
    for fuel_key in ["regular", "mid_grade", "premium", "diesel"]:
        avgs  = [m["current_avg"][fuel_key] for m in metros.values() if fuel_key in m.get("current_avg", {})]
        lows  = [m["low"][fuel_key]         for m in metros.values() if fuel_key in m.get("low", {})]
        highs = [m["high"][fuel_key]        for m in metros.values() if fuel_key in m.get("high", {})]
        if avgs:
            statewide["current_avg"][fuel_key] = round(statistics.mean(avgs), 3)
            statewide["low"][fuel_key]         = round(min(lows), 3)
            statewide["high"][fuel_key]        = round(max(highs), 3)

    # Merge Fuel Insights historical data (cache on success, load cache on failure)
    insights_cache_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "docs", "fuel_insights_cache.json"
    )
    if insights and insights.get("yesterday_avg"):
        try:
            with open(insights_cache_path, "w", encoding="utf-8") as f:
                json.dump(insights, f, separators=(",", ":"), ensure_ascii=False)
            log.info("Saved Fuel Insights to cache")
        except Exception:
            pass
    elif os.path.exists(insights_cache_path):
        try:
            with open(insights_cache_path, "r", encoding="utf-8") as f:
                insights = json.load(f)
            log.info("Loaded Fuel Insights from cache (fallback)")
        except (json.JSONDecodeError, OSError):
            pass

    for period in ["yesterday_avg", "week_ago_avg", "month_ago_avg", "year_ago_avg", "gasbuddy_live_avg"]:
        if period in insights:
            statewide[period] = insights[period]

    reg = statewide["current_avg"].get("regular")
    log.info("Statewide avg: reg=$%s (%d/%d cities scraped)",
             f"{reg:.3f}" if reg else "—", len(metros), len(CITIES))

    today = datetime.now(timezone.utc).strftime("%m/%d/%y")
    return {
        "source":      "GasBuddy",
        "source_url":  "https://www.gasbuddy.com/gasprices/wisconsin",
        "state":       "Wisconsin",
        "price_date":  today,
        "scraped_at":  datetime.now(timezone.utc).isoformat(),
        "statewide":   statewide,
        "metros":      metros,
        "priority_metros": PRIORITY_METROS,
    }


# ---------------------------------------------------------------------------
# EIA trend data
# ---------------------------------------------------------------------------

def fetch_eia_data(out_dir: str) -> None:
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
            url = (
                f"{base}?api_key={api_key}&frequency=weekly&data[0]=value"
                f"&facets[duoarea][]=R20&facets[product][]={code}"
                f"&sort[0][column]=period&sort[0][direction]=asc&length=5000"
            )
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

def update_history(data: dict, out_dir: str) -> None:
    history_path = os.path.join(out_dir, "gas_prices_history.json")
    today_key = data.get("price_date", datetime.now(timezone.utc).strftime("%m/%d/%y"))
    history = {}
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = {}

    entry: dict = {}
    sw = data.get("statewide", {}).get("current_avg", {})
    if sw:
        entry["statewide"] = sw
    for name, md in data.get("metros", {}).items():
        # Don't record stale (preserved) entries in history
        if md.get("stale"):
            continue
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
# Data preservation — merge stale cities from previous run
# ---------------------------------------------------------------------------

def recalculate_statewide(data: dict) -> None:
    """Recompute statewide averages from all metros (fresh + preserved stale)."""
    metros = data.get("metros", {})
    sw = data.get("statewide", {})
    for fuel_key in ["regular", "mid_grade", "premium", "diesel"]:
        avgs  = [m["current_avg"][fuel_key] for m in metros.values() if fuel_key in m.get("current_avg", {})]
        lows  = [m["low"][fuel_key]         for m in metros.values() if fuel_key in m.get("low", {})]
        highs = [m["high"][fuel_key]        for m in metros.values() if fuel_key in m.get("high", {})]
        if avgs:
            sw.setdefault("current_avg", {})[fuel_key] = round(statistics.mean(avgs), 3)
            sw.setdefault("low", {})[fuel_key]         = round(min(lows), 3)
            sw.setdefault("high", {})[fuel_key]        = round(max(highs), 3)
    data["statewide"] = sw


def merge_with_previous(data: dict, previous_data: dict) -> None:
    """Preserve city data from the previous run for any cities that failed today."""
    if not previous_data or "metros" not in previous_data:
        return

    prev_metros = previous_data["metros"]
    prev_date   = previous_data.get("price_date", "unknown")
    fresh       = data.get("metros", {})
    preserved   = 0

    for city_name, city_data in prev_metros.items():
        if city_name not in fresh:
            stale = dict(city_data)
            stale["stale"]      = True
            stale["stale_from"] = prev_date
            fresh[city_name]    = stale
            preserved += 1

    if preserved:
        log.info("Preserved %d stale cities from previous data (%s)", preserved, prev_date)
        recalculate_statewide(data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)

    # Load previous data for stale-city preservation
    previous_data: dict = {}
    if os.path.exists(args.output):
        try:
            with open(args.output, "r", encoding="utf-8") as f:
                previous_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            previous_data = {}

    gb_success = False
    try:
        data = scrape_gasbuddy()

        fresh_count = len(data.get("metros", {}))
        merge_with_previous(data, previous_data)
        total_count = len(data.get("metros", {}))

        log.info("Cities: %d fresh, %d stale preserved, %d total",
                 fresh_count, total_count - fresh_count, total_count)

        if fresh_count > 0 and data.get("statewide", {}).get("current_avg"):
            gb_success = True
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info("Wrote gas prices to %s", args.output)
            update_history(data, out_dir)
        else:
            log.warning("No fresh city data — preserving previous file unchanged")

    except Exception:
        log.exception("GasBuddy scrape failed — will still update EIA data")

    fetch_eia_data(out_dir)

    if not gb_success:
        log.warning("GasBuddy scrape failed but EIA data was updated.")
    log.info("Done!")


if __name__ == "__main__":
    main()
