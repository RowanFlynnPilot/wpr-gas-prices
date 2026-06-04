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
    # Central / northern Wisconsin — WPR's core readership area
    "Wausau":           "Wausau, WI",
    "Stevens Point":    "Stevens Point, WI",
    "Wisconsin Rapids": "Wisconsin Rapids, WI",
    "Marshfield":       "Marshfield, WI",
    "Rhinelander":      "Rhinelander, WI",
    "Merrill":          "Merrill, WI",
    "Mosinee":          "Mosinee, WI",
    "Antigo":           "Antigo, WI",
    # Other major Wisconsin metros
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
    "name address { line1 locality } "
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


def parse_station_results(results: list) -> dict | None:
    """Aggregate GasBuddy station price nodes into avg/low/high/count per fuel.

    Pure function (no network) so it can be unit-tested against fixtures.
    Returns None if there are no stations or no usable regular-grade prices.
    """
    if not results:
        return None

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
        return None

    city_data: dict = {"current_avg": {}, "low": {}, "high": {}, "station_count": {}}
    for fuel_key, prices in fuel_prices.items():
        city_data["current_avg"][fuel_key]   = round(statistics.mean(prices), 3)
        city_data["low"][fuel_key]           = round(min(prices), 3)
        city_data["high"][fuel_key]          = round(max(prices), 3)
        city_data["station_count"][fuel_key] = len(prices)
    return city_data


def extract_cheapest_stations(results: list, limit: int = 8) -> list:
    """Cheapest `limit` named stations by regular price, each with its address and
    available fuel prices. Pure function (no network) for unit-testing.
    """
    stations = []
    for st in results:
        name = (st.get("name") or "").strip()
        if not name:
            continue
        prices: dict[str, float] = {}
        for pn in st.get("prices", []):
            fuel_key = FUEL_MAP.get(pn.get("fuelProduct"))
            if not fuel_key:
                continue
            raw = (pn.get("credit") or pn.get("cash") or {}).get("price")
            try:
                p = float(raw)
            except (TypeError, ValueError):
                continue
            if 1.0 < p < 10.0:
                prices[fuel_key] = round(p, 3)
        if "regular" not in prices:
            continue
        addr = st.get("address") or {}
        address = ", ".join(x for x in [(addr.get("line1") or "").strip(),
                                        (addr.get("locality") or "").strip()] if x)
        entry = {"name": name, "prices": prices}
        if address:
            entry["address"] = address
        stations.append(entry)

    stations.sort(key=lambda s: s["prices"]["regular"])
    return stations[:limit]


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

    city_data = parse_station_results(results)
    if city_data is None:
        log.warning("  %s: no usable prices found", city_name)
        return None

    stations = extract_cheapest_stations(results)
    if stations:
        city_data["stations"] = stations

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
    """Fetch the Fuel Insights page and parse statewide historical comparisons."""
    log.info("  Scraping Fuel Insights for Wisconsin historical data...")
    try:
        resp = session.get(FUEL_INSIGHTS_URL, timeout=20)
        text = resp.text
    except Exception as e:
        log.warning("  Fuel Insights fetch failed: %s", e)
        return {}
    return parse_fuel_insights(text)


def parse_fuel_insights(text: str) -> dict:
    """Extract statewide historical comparisons from Fuel Insights HTML.

    Pure function (no network) so the brittle regexes can be unit-tested.
    Returns {} if the page doesn't contain the expected markers.
    """
    if "Yesterday" not in text:
        log.warning("  Fuel Insights: 'Yesterday' not found in response (%d chars)", len(text))
        return {}

    result = {}
    for key, patterns in [
        ("yesterday_avg", [
            r"Yesterday'?s?\s+Avg\*?\s+of\s+\$(\d+\.\d+)",
            r"Yesterday'?s?\s+Avg\*?[^$]*\$(\d+\.\d+)",
            r"from\s+Yesterday[^$]*\$(\d+\.\d+)",
        ]),
        ("week_ago_avg", [
            r"Last\s+Week'?s?\s+Avg\*?\s+of\s+\$(\d+\.\d+)",
            r"Last\s+Week'?s?\s+Avg\*?[^$]*\$(\d+\.\d+)",
            r"from\s+Last\s+Week[^$]*\$(\d+\.\d+)",
        ]),
        ("month_ago_avg", [
            r"Last\s+Month'?s?\s+Avg\*?\s+of\s+\$(\d+\.\d+)",
            r"Last\s+Month'?s?\s+Avg\*?[^$]*\$(\d+\.\d+)",
            r"from\s+Last\s+Month[^$]*\$(\d+\.\d+)",
        ]),
        ("year_ago_avg", [
            r"Last\s+Year'?s?\s+Avg\*?\s+of\s+\$(\d+\.\d+)",
            r"Last\s+Year'?s?\s+Avg\*?[^$]*\$(\d+\.\d+)",
            r"from\s+Last\s+Year[^$]*\$(\d+\.\d+)",
        ]),
    ]:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
            if m:
                result[key] = {"regular": float(m.group(1))}
                break

    live = re.search(r"\$(\d+\.\d+)\s*/gal", text)
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

def compute_statewide(metros: dict) -> dict:
    """Statewide current_avg / low / high across metros.

    current_avg is weighted by station count, so a city's influence scales with how
    many stations it contributes — this equals the pooled mean of every station's
    price (a market-weighted figure), rather than treating each city equally.
    low/high stay as the absolute min/max across cities.
    """
    sw: dict = {"current_avg": {}, "low": {}, "high": {}}
    for fuel_key in ["regular", "mid_grade", "premium", "diesel"]:
        weighted_sum = 0.0
        weight = 0
        lows, highs = [], []
        for m in metros.values():
            avg = m.get("current_avg", {}).get(fuel_key)
            if avg is None:
                continue
            count = m.get("station_count", {}).get(fuel_key) or 1
            weighted_sum += avg * count
            weight += count
            if fuel_key in m.get("low", {}):
                lows.append(m["low"][fuel_key])
            if fuel_key in m.get("high", {}):
                highs.append(m["high"][fuel_key])
        if weight:
            sw["current_avg"][fuel_key] = round(weighted_sum / weight, 3)
            if lows:
                sw["low"][fuel_key] = round(min(lows), 3)
            if highs:
                sw["high"][fuel_key] = round(max(highs), 3)
    return sw


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

    # Scrape all cities in two batches with a mid-run pause.
    # GasBuddy rate-limits datacenter IPs (GitHub Actions / Azure) to ~7 requests
    # per minute. We scrape batch 1, wait 90s for the window to reset, then batch 2.
    metros: dict = {}
    city_items = list(CITIES.items())
    batch_size = 7

    for batch_num, batch_start in enumerate(range(0, len(city_items), batch_size)):
        batch = city_items[batch_start: batch_start + batch_size]
        if batch_num == 0:
            log.info("Batch 1/%d: waiting 60s for rate-limit window...",
                     -(-len(city_items) // batch_size))
            time.sleep(60)
        else:
            log.info("Batch %d: waiting 90s for rate-limit window to reset...", batch_num + 1)
            time.sleep(90)

        for i, (city_name, search_term) in enumerate(batch):
            data = scrape_city_graphql(session, city_name, search_term, headers)
            if data:
                metros[city_name] = data
            else:
                log.warning("  %s: skipped (no usable data)", city_name)
            if i < len(batch) - 1:
                time.sleep(5)

    # Compute statewide averages (station-count weighted) across all scraped cities
    statewide = compute_statewide(metros)

    # Merge Fuel Insights historical data (cache on success, load cache on failure)
    insights_cache_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "docs", "fuel_insights_cache.json"
    )
    insights_from_cache = False
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
            insights_from_cache = True
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
        # Transient run health — popped before gas_prices.json is written, then
        # finalized into docs/scrape_status.json by main().
        "run_health": {
            "cities_total":        len(CITIES),
            "cities_fresh":        len(metros),
            "failed_cities":       sorted(c for c in CITIES if c not in metros),
            "insights_from_cache": insights_from_cache,
        },
    }


# ---------------------------------------------------------------------------
# EIA trend data
# ---------------------------------------------------------------------------

def fetch_eia_data(out_dir: str) -> bool:
    """Fetch EIA weekly trend data. Returns True if eia_weekly.json was written."""
    eia_path = os.path.join(out_dir, "eia_weekly.json")
    api_key = os.environ.get("EIA_API_KEY", "")
    if not api_key:
        log.warning("EIA_API_KEY not set, skipping.")
        return False

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
        return True
    return False


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
# Newsroom summary — a quotable, auto-generated blurb for WPR
# ---------------------------------------------------------------------------

def _format_long_date(price_date: str) -> str:
    """'mm/dd/yy' -> 'June 4, 2026' (no leading zero, cross-platform)."""
    try:
        dt = datetime.strptime(price_date, "%m/%d/%y")
    except (ValueError, TypeError):
        return price_date or ""
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def build_summary(data: dict) -> dict:
    """A quotable, plain-language summary the WPR newsroom can drop into articles.

    Pure function over the assembled output. Returns {} if there's no usable average.
    """
    sw = data.get("statewide", {})
    cur = sw.get("current_avg", {}).get("regular")
    if cur is None:
        return {}

    def cents(d):
        return f"{abs(round(d * 100))}¢"  # e.g. "12¢"

    comps = []
    for key, phrase in [("week_ago_avg", "a week ago"),
                        ("month_ago_avg", "a month ago"),
                        ("year_ago_avg", "a year ago")]:
        prev = sw.get(key, {}).get("regular")
        if prev is not None and abs(cur - prev) >= 0.005:
            direction = "up" if cur > prev else "down"
            comps.append(f"{direction} {cents(cur - prev)} from {phrase}")

    # Cheapest / priciest metro (fresh cities only)
    reg = {
        name: md["current_avg"]["regular"]
        for name, md in data.get("metros", {}).items()
        if not md.get("stale") and "regular" in md.get("current_avg", {})
    }
    metro_clause = ""
    if len(reg) >= 2:
        low_city = min(reg, key=reg.get)
        high_city = max(reg, key=reg.get)
        metro_clause = (f" Among metro areas, {low_city} is cheapest "
                        f"(${reg[low_city]:.2f}) and {high_city} priciest (${reg[high_city]:.2f}).")

    as_of = _format_long_date(data.get("price_date", ""))
    comp_clause = (" — " + ", ".join(comps)) if comps else ""
    blurb = (f"As of {as_of}, regular unleaded in Wisconsin averages "
             f"${cur:.2f} per gallon{comp_clause}.{metro_clause}")

    return {
        "as_of": as_of,
        "headline": f"Wisconsin gas averages ${cur:.2f}/gal",
        "blurb": blurb,
    }


# ---------------------------------------------------------------------------
# Data preservation — merge stale cities from previous run
# ---------------------------------------------------------------------------

def recalculate_statewide(data: dict) -> None:
    """Recompute statewide avg/low/high from all metros (fresh + preserved stale).

    Preserves any Fuel Insights comparison periods already on data["statewide"].
    """
    recomputed = compute_statewide(data.get("metros", {}))
    sw = data.get("statewide", {})
    sw["current_avg"] = recomputed["current_avg"]
    sw["low"]         = recomputed["low"]
    sw["high"]        = recomputed["high"]
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
# Output validation + run-status heartbeat
# ---------------------------------------------------------------------------

def validate_output(data: dict) -> None:
    """Raise ValueError if `data` is unsafe to write as the live gas_prices.json.

    Guards the live file from being clobbered by a malformed scrape.
    """
    for key in ("source", "price_date", "scraped_at", "statewide", "metros"):
        if key not in data:
            raise ValueError(f"missing required key: {key}")
    if not data["metros"]:
        raise ValueError("no metros in output")
    reg = data.get("statewide", {}).get("current_avg", {}).get("regular")
    if not isinstance(reg, (int, float)) or not (1.0 < reg < 10.0):
        raise ValueError(f"statewide regular avg out of plausible range: {reg!r}")


def write_status(out_dir: str, *, gasbuddy_success: bool,
                 run_health: dict | None, eia_updated: bool) -> None:
    """Always-written per-run heartbeat consumed by the failure-alert workflow step.

    Not committed (gitignored) — it is read in-job, after the scraper, to decide
    whether to open a GitHub issue.
    """
    status = {
        "last_run":         datetime.now(timezone.utc).isoformat(),
        "gasbuddy_success": gasbuddy_success,
        "eia_updated":      eia_updated,
    }
    if run_health:
        status.update(run_health)
    path = os.path.join(out_dir, "scrape_status.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
        log.info("Wrote run status to %s (gasbuddy_success=%s, fresh=%s/%s)",
                 path, gasbuddy_success,
                 status.get("cities_fresh", "?"), status.get("cities_total", "?"))
    except OSError as e:
        log.warning("Failed to write run status: %s", e)


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
    run_health: dict | None = None
    try:
        data = scrape_gasbuddy()
        run_health = data.pop("run_health", None)  # transient — not persisted in gas_prices.json

        fresh_count = len(data.get("metros", {}))
        merge_with_previous(data, previous_data)
        total_count = len(data.get("metros", {}))
        if run_health is not None:
            run_health["cities_stale_preserved"] = total_count - fresh_count

        log.info("Cities: %d fresh, %d stale preserved, %d total",
                 fresh_count, total_count - fresh_count, total_count)

        if fresh_count > 0 and data.get("statewide", {}).get("current_avg"):
            validate_output(data)
            summary = build_summary(data)
            if summary:
                data["summary"] = summary
                log.info("Summary: %s", summary["blurb"])
            gb_success = True
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info("Wrote gas prices to %s", args.output)
            update_history(data, out_dir)
        else:
            log.warning("No fresh city data — preserving previous file unchanged")

    except Exception:
        log.exception("GasBuddy scrape failed — will still update EIA data")

    eia_updated = fetch_eia_data(out_dir)

    write_status(out_dir, gasbuddy_success=gb_success,
                 run_health=run_health, eia_updated=eia_updated)

    if not gb_success:
        log.warning("GasBuddy scrape failed but EIA data was updated.")
    log.info("Done!")


if __name__ == "__main__":
    main()
