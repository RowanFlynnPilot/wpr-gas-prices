"""Unit tests for the pure (no-network) logic in scrape_gas_prices.py.

These guard the parts most likely to break silently when GasBuddy changes its
markup or response shape: station-price aggregation, cheapest-station extraction,
history-based comparisons, the newsroom blurb, stale-city preservation, history
capping, and output validation.

Run: pytest -q
"""
import json

import pytest

import scrape_gas_prices as s


# ---------------------------------------------------------------------------
# parse_station_results
# ---------------------------------------------------------------------------

def _station(*prices):
    """Build a GasBuddy station node from (fuelProduct, credit_price) pairs."""
    return {"prices": [{"fuelProduct": fp, "credit": {"price": p}, "cash": None}
                       for fp, p in prices]}


def test_parse_station_results_basic_aggregation():
    results = [
        _station(("regular_gas", 3.00), ("premium_gas", 4.00)),
        _station(("regular_gas", 3.50)),
    ]
    out = s.parse_station_results(results)
    assert out["current_avg"]["regular"] == 3.25
    assert out["low"]["regular"] == 3.0
    assert out["high"]["regular"] == 3.5
    assert out["station_count"]["regular"] == 2
    assert out["current_avg"]["premium"] == 4.0


def test_parse_station_results_filters_out_of_range_prices():
    # 0.00 (placeholder) and 99.99 (garbage) must be dropped; only 3.10 counts.
    results = [_station(("regular_gas", 0.0), ("regular_gas", 99.99), ("regular_gas", 3.10))]
    out = s.parse_station_results(results)
    assert out["station_count"]["regular"] == 1
    assert out["current_avg"]["regular"] == 3.1


def test_parse_station_results_prefers_credit_over_cash():
    node = {"prices": [{"fuelProduct": "regular_gas",
                        "credit": {"price": 3.20}, "cash": {"price": 3.05}}]}
    out = s.parse_station_results([node])
    assert out["current_avg"]["regular"] == 3.2


def test_parse_station_results_falls_back_to_cash():
    node = {"prices": [{"fuelProduct": "regular_gas",
                        "credit": None, "cash": {"price": 3.05}}]}
    out = s.parse_station_results([node])
    assert out["current_avg"]["regular"] == 3.05


def test_parse_station_results_none_when_empty():
    assert s.parse_station_results([]) is None


def test_parse_station_results_none_when_no_regular():
    # Only premium present — widget requires regular, so this city is unusable.
    assert s.parse_station_results([_station(("premium_gas", 4.0))]) is None


# ---------------------------------------------------------------------------
# extract_cheapest_stations
# ---------------------------------------------------------------------------

def _named(name, reg, line1="123 Main St", locality="Wausau", extra=()):
    prices = [{"fuelProduct": "regular_gas", "credit": {"price": reg}, "cash": None}]
    for fp, p in extra:
        prices.append({"fuelProduct": fp, "credit": {"price": p}, "cash": None})
    return {"name": name, "address": {"line1": line1, "locality": locality}, "prices": prices}


def test_extract_cheapest_stations_sorted_with_address():
    results = [_named("Kwik Trip", 3.69), _named("Costco", 3.59), _named("BP", 3.99)]
    out = s.extract_cheapest_stations(results)
    assert [st["name"] for st in out] == ["Costco", "Kwik Trip", "BP"]
    assert out[0]["prices"]["regular"] == 3.59
    assert out[0]["address"] == "123 Main St, Wausau"


def test_extract_cheapest_stations_respects_limit():
    results = [_named(f"Station {i}", 3.0 + i / 100) for i in range(20)]
    assert len(s.extract_cheapest_stations(results, limit=5)) == 5


def test_extract_cheapest_stations_skips_unnamed_and_no_regular():
    results = [
        {"name": "", "address": {}, "prices": [{"fuelProduct": "regular_gas", "credit": {"price": 3.1}}]},
        {"name": "Diesel Only", "prices": [{"fuelProduct": "diesel", "credit": {"price": 4.0}}]},
        _named("Good", 3.5),
    ]
    out = s.extract_cheapest_stations(results)
    assert [st["name"] for st in out] == ["Good"]


def test_extract_cheapest_stations_keeps_all_fuel_prices():
    results = [_named("Kwik Trip", 3.69, extra=[("diesel", 4.59), ("premium_gas", 4.29)])]
    st0 = s.extract_cheapest_stations(results)[0]
    assert st0["prices"] == {"regular": 3.69, "diesel": 4.59, "premium": 4.29}


# ---------------------------------------------------------------------------
# parse_aaa (statewide trend: today / yesterday / week / month / year)
# ---------------------------------------------------------------------------

# Mirrors AAA's tag-stripped Wisconsin table: each period label is followed by
# four prices in column order Regular, Mid-Grade, Premium, Diesel.
SAMPLE_AAA = """
<table><tr><td>Current Avg.</td><td>$4.069</td><td>$4.631</td><td>$5.200</td><td>$5.411</td></tr>
<tr><td>Yesterday Avg.</td><td>$4.097</td><td>$4.655</td><td>$5.231</td><td>$5.456</td></tr>
<tr><td>Week Ago Avg.</td><td>$4.330</td><td>$4.886</td><td>$5.458</td><td>$5.669</td></tr>
<tr><td>Month Ago Avg.</td><td>$4.371</td><td>$4.843</td><td>$5.438</td><td>$5.584</td></tr>
<tr><td>Year Ago Avg.</td><td>$2.968</td><td>$3.478</td><td>$3.977</td><td>$3.225</td></tr></table>
"""


def test_parse_aaa_extracts_all_periods_and_fuels():
    out = s.parse_aaa(SAMPLE_AAA)
    assert out["current"] == {"regular": 4.069, "mid_grade": 4.631, "premium": 5.200, "diesel": 5.411}
    assert out["week_ago"]["regular"] == 4.330
    assert out["year_ago"]["regular"] == 2.968   # column order Regular first
    assert out["year_ago"]["diesel"] == 3.225


def test_parse_aaa_empty_without_table():
    assert s.parse_aaa("<html>no table here</html>") == {}


# ---------------------------------------------------------------------------
# merge_with_previous + recalculate_statewide
# ---------------------------------------------------------------------------

def _city(reg):
    return {"current_avg": {"regular": reg}, "low": {"regular": reg},
            "high": {"regular": reg}}


def test_merge_preserves_failed_city_as_stale():
    data = {"metros": {"Wausau": _city(4.0)}, "statewide": {}}
    prev = {"price_date": "06/01/26", "metros": {"Madison": _city(3.6)}}
    s.merge_with_previous(data, prev)

    assert data["metros"]["Madison"]["stale"] is True
    assert data["metros"]["Madison"]["stale_from"] == "06/01/26"
    # statewide recomputed across both cities: mean(4.0, 3.6) = 3.8
    assert data["statewide"]["current_avg"]["regular"] == 3.8


def test_merge_does_not_overwrite_fresh_city():
    data = {"metros": {"Wausau": _city(4.0)}, "statewide": {}}
    prev = {"price_date": "06/01/26", "metros": {"Wausau": _city(9.9)}}
    s.merge_with_previous(data, prev)
    assert data["metros"]["Wausau"]["current_avg"]["regular"] == 4.0
    assert "stale" not in data["metros"]["Wausau"]


def test_merge_noop_without_previous():
    data = {"metros": {"Wausau": _city(4.0)}, "statewide": {}}
    s.merge_with_previous(data, {})
    assert list(data["metros"]) == ["Wausau"]


def test_merge_carries_neighbors_forward_only_when_missing():
    prev_nb = {"as_of": "06/01/26", "states": {"MN": {"name": "Minnesota",
                                                      "current": {"regular": 3.5}}}}
    data = {"metros": {"Wausau": _city(4.0)}, "statewide": {}, "neighbors": {}}
    s.merge_with_previous(data, {"metros": {}, "neighbors": prev_nb})
    assert data["neighbors"] == prev_nb

    fresh_nb = {"as_of": "06/02/26", "states": {"MN": {"name": "Minnesota",
                                                       "current": {"regular": 3.6}}}}
    data = {"metros": {"Wausau": _city(4.0)}, "statewide": {}, "neighbors": fresh_nb}
    s.merge_with_previous(data, {"metros": {}, "neighbors": prev_nb})
    assert data["neighbors"] == fresh_nb


# ---------------------------------------------------------------------------
# update_history
# ---------------------------------------------------------------------------

def test_update_history_excludes_stale_and_records_statewide(tmp_path):
    data = {
        "price_date": "06/03/26",
        "statewide": {"current_avg": {"regular": 3.9}},
        "metros": {
            "Wausau": _city(4.0),
            "Madison": {**_city(3.6), "stale": True, "stale_from": "06/01/26"},
        },
    }
    s.update_history(data, str(tmp_path))
    hist = json.loads((tmp_path / "gas_prices_history.json").read_text())
    assert hist["06/03/26"]["statewide"]["regular"] == 3.9
    assert "Wausau" in hist["06/03/26"]
    assert "Madison" not in hist["06/03/26"]  # stale excluded


def test_update_history_caps_at_400_days(tmp_path):
    from datetime import date, timedelta

    hist_path = tmp_path / "gas_prices_history.json"
    start = date(2025, 1, 1)
    days = [start + timedelta(days=i) for i in range(405)]
    seed = {d.strftime("%m/%d/%y"): {"statewide": {"regular": 3.0}} for d in days}
    hist_path.write_text(json.dumps(seed))

    today = (days[-1] + timedelta(days=1)).strftime("%m/%d/%y")
    data = {"price_date": today, "statewide": {"current_avg": {"regular": 3.9}}, "metros": {}}
    s.update_history(data, str(tmp_path))
    hist = json.loads(hist_path.read_text())

    assert len(hist) == 400
    assert today in hist                                # newest kept
    assert days[0].strftime("%m/%d/%y") not in hist     # oldest trimmed
    # Trimming must be chronological: the 6 oldest days go, nothing newer.
    assert days[6].strftime("%m/%d/%y") in hist
    assert days[5].strftime("%m/%d/%y") not in hist


def test_update_history_trims_by_date_not_string(tmp_path):
    """A string sort would keep December and drop January of the following year."""
    hist_path = tmp_path / "gas_prices_history.json"
    seed = {
        "12/30/25": {"statewide": {"regular": 3.0}},   # oldest by date, LAST by string
        "01/02/26": {"statewide": {"regular": 3.1}},
        "01/03/26": {"statewide": {"regular": 3.2}},
    }
    hist_path.write_text(json.dumps(seed))

    data = {"price_date": "01/04/26", "statewide": {"current_avg": {"regular": 3.3}},
            "metros": {}}
    s.update_history(data, str(tmp_path))
    hist = json.loads(hist_path.read_text())
    assert len(hist) == 4  # under the 400 cap, nothing trimmed
    # The real guarantee: chronological order is recoverable from the keys.
    ordered = sorted(hist, key=s.history_key_date)
    assert ordered == ["12/30/25", "01/02/26", "01/03/26", "01/04/26"]


def test_normalize_history_keys_pads_legacy_and_drops_junk():
    normalized = s.normalize_history_keys({
        "3/18/26": {"statewide": {"regular": 3.0}},    # legacy unpadded
        "07/27/26": {"statewide": {"regular": 3.8}},   # already canonical
        "not-a-date": {"statewide": {"regular": 9.9}}, # unplaceable on a timeline
    })
    assert set(normalized) == {"03/18/26", "07/27/26"}
    assert normalized["03/18/26"]["statewide"]["regular"] == 3.0


def test_history_key_date_orders_across_years():
    keys = ["3/18/26", "12/30/25", "07/27/26", "01/02/26"]
    assert sorted(keys, key=s.history_key_date) == \
        ["12/30/25", "01/02/26", "3/18/26", "07/27/26"]


def test_update_history_normalizes_existing_file(tmp_path):
    hist_path = tmp_path / "gas_prices_history.json"
    hist_path.write_text(json.dumps({"3/18/26": {"statewide": {"regular": 3.0}}}))
    data = {"price_date": "07/27/26", "statewide": {"current_avg": {"regular": 3.8}},
            "metros": {}}
    s.update_history(data, str(tmp_path))
    hist = json.loads(hist_path.read_text())
    assert set(hist) == {"03/18/26", "07/27/26"}  # legacy key self-healed


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------

def _valid_output():
    return {
        "source": "GasBuddy", "price_date": "06/03/26", "scraped_at": "2026-06-03T00:00:00+00:00",
        "statewide": {"current_avg": {"regular": 3.9}},
        "metros": {"Wausau": _city(3.9)},
    }


def test_validate_output_accepts_good_data():
    s.validate_output(_valid_output())  # should not raise


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("metros"),
    lambda d: d.update(metros={}),
    lambda d: d["statewide"]["current_avg"].update(regular=0.0),
    lambda d: d["statewide"]["current_avg"].update(regular="N/A"),
    lambda d: d.pop("scraped_at"),
])
def test_validate_output_rejects_bad_data(mutate):
    d = _valid_output()
    mutate(d)
    with pytest.raises(ValueError):
        s.validate_output(d)


# ---------------------------------------------------------------------------
# compute_statewide (station-count weighted)
# ---------------------------------------------------------------------------

def test_compute_statewide_weights_by_station_count():
    metros = {
        "Big":   {"current_avg": {"regular": 4.00}, "low": {"regular": 3.9},
                  "high": {"regular": 4.1}, "station_count": {"regular": 30}},
        "Small": {"current_avg": {"regular": 3.00}, "low": {"regular": 2.9},
                  "high": {"regular": 3.1}, "station_count": {"regular": 10}},
    }
    sw = s.compute_statewide(metros)
    # weighted: (4.00*30 + 3.00*10) / 40 = 3.75  (unweighted would be 3.50)
    assert sw["current_avg"]["regular"] == 3.75
    assert sw["low"]["regular"] == 2.9   # absolute min across cities
    assert sw["high"]["regular"] == 4.1  # absolute max across cities


def test_compute_statewide_defaults_weight_when_count_missing():
    metros = {
        "A": _city(4.0),  # no station_count -> weight defaults to 1
        "B": _city(3.0),
    }
    sw = s.compute_statewide(metros)
    assert sw["current_avg"]["regular"] == 3.5


# ---------------------------------------------------------------------------
# build_summary (newsroom blurb)
# ---------------------------------------------------------------------------

def test_build_summary_full():
    data = {
        "price_date": "06/04/26",
        "statewide": {"current_avg": {"regular": 3.92}},   # GasBuddy headline number
        "aaa": {                                            # AAA-internal trend
            "current": {"regular": 4.07}, "week_ago": {"regular": 4.33},
            "month_ago": {"regular": 4.37}, "year_ago": {"regular": 2.97},
        },
        "metros": {"Madison": _city(3.68), "Wausau": _city(4.41)},
    }
    out = s.build_summary(data)
    assert out["as_of"] == "June 4, 2026"
    assert out["headline"] == "Wisconsin gas averages $3.92/gal"
    b = out["blurb"]
    assert "averaging $3.92 a gallon across Wisconsin" in b
    assert "according to GasBuddy" in b
    assert "AAA figures put the statewide average" in b
    assert "26¢ lower than a week ago" in b       # 4.07 vs 4.33 (AAA-internal)
    assert "$1.10 higher than a year ago" in b    # 4.07 vs 2.97 -> dollars, not "110¢"
    assert "lowest metro average in Madison ($3.68)" in b
    assert "highest in Wausau ($4.41)" in b


def test_build_summary_no_trend_without_aaa():
    data = {
        "price_date": "06/04/26",
        "statewide": {"current_avg": {"regular": 3.50}},
        "metros": {"Madison": _city(3.50)},  # one metro -> no metro clause; no aaa -> no trend
    }
    out = s.build_summary(data)
    assert "AAA figures" not in out["blurb"]
    assert "metro average" not in out["blurb"]
    assert out["blurb"].endswith("according to GasBuddy.")


def test_build_summary_empty_without_regular():
    assert s.build_summary({"statewide": {"current_avg": {}}}) == {}


# ---------------------------------------------------------------------------
# latest_eia_value
# ---------------------------------------------------------------------------

def test_latest_eia_value_picks_most_recent():
    rows = [
        {"period": "2026-05-18", "value": "4.05"},
        {"period": "2026-06-01", "value": "4.10"},
        {"period": "2026-05-25", "value": "4.08"},
    ]
    assert s.latest_eia_value(rows) == ("2026-06-01", 4.10)


def test_latest_eia_value_skips_nulls_and_handles_empty():
    assert s.latest_eia_value([{"period": "2026-06-01", "value": None}]) is None
    assert s.latest_eia_value([]) is None


# ---------------------------------------------------------------------------
# is_degraded
# ---------------------------------------------------------------------------

def test_is_degraded_thresholds():
    assert s.is_degraded({"cities_total": 22, "cities_fresh": 1}) is True
    assert s.is_degraded({"cities_total": 22, "cities_fresh": 10}) is True   # < half
    assert s.is_degraded({"cities_total": 22, "cities_fresh": 11}) is False  # exactly half
    assert s.is_degraded({"cities_total": 22, "cities_fresh": 20}) is False
    assert s.is_degraded(None) is False
    assert s.is_degraded({}) is False


def test_build_summary_metro_clause_ranks_plausible_incl_stale():
    data = {
        "price_date": "06/04/26",
        "statewide": {"current_avg": {"regular": 3.92}},
        "metros": {
            "Madison": _city(3.68),
            "Wausau": {**_city(4.41), "stale": True},   # stale but plausible -> still ranked
            "Ghost": _city(0.50),                       # implausible price -> excluded
            "NoReg": {"current_avg": {}},               # no regular -> excluded
        },
    }
    b = s.build_summary(data)["blurb"]
    assert "Ghost" not in b and "NoReg" not in b
    assert "lowest metro average in Madison ($3.68)" in b
    assert "highest in Wausau ($4.41)" in b   # stale city still appears


# ---------------------------------------------------------------------------
# history_extreme_note — "highest since ..." milestone for the blurb
# ---------------------------------------------------------------------------

def _synth_history(end_key, days, price, overrides=None):
    """`days` daily statewide readings ending at end_key, all at `price`."""
    from datetime import timedelta
    end = s.history_key_date(end_key)
    hist = {}
    for i in range(days):
        d = end - timedelta(days=i)
        hist[d.strftime("%m/%d/%y")] = {"statewide": {"regular": price}}
    for k, v in (overrides or {}).items():
        hist[k] = {"statewide": {"regular": v}}
    return hist


def test_extreme_note_highest_since_named_date():
    hist = _synth_history("06/03/26", 90, 3.50, overrides={"04/20/26": 4.05})
    assert s.history_extreme_note(hist, 4.00, "06/04/26") == "the highest since April 20"


def test_extreme_note_alltime_high_names_tracking_span():
    hist = _synth_history("06/03/26", 90, 3.50)
    assert s.history_extreme_note(hist, 4.00, "06/04/26") == \
        "the highest in WPR's 3 months of tracking"


def test_extreme_note_quiet_when_milestone_is_recent_or_history_thin():
    # Yesterday was higher, and lower days are 2 days back — nothing to say.
    hist = _synth_history("06/03/26", 90, 3.50, overrides={"06/03/26": 4.05})
    assert s.history_extreme_note(hist, 4.00, "06/04/26") == ""
    # Under 60 days of history: stay quiet even at an all-time high.
    hist = _synth_history("06/03/26", 30, 3.50)
    assert s.history_extreme_note(hist, 4.00, "06/04/26") == ""


def test_build_summary_includes_extreme_clause():
    data = {
        "price_date": "06/04/26",
        "statewide": {"current_avg": {"regular": 4.00}},
        "metros": {"Wausau": _city(4.0), "Madison": _city(3.9)},
    }
    hist = _synth_history("06/03/26", 90, 3.50)
    blurb = s.build_summary(data, hist)["blurb"]
    assert "according to GasBuddy — the highest in WPR's 3 months of tracking." in blurb
    # And without history the clause is simply absent (legacy behavior).
    assert "months of tracking" not in s.build_summary(data)["blurb"]


# ---------------------------------------------------------------------------
# detect_notable_move — the story nudge
# ---------------------------------------------------------------------------

def test_detect_notable_move_week_jump():
    aaa = {"current": {"regular": 3.97}, "week_ago": {"regular": 3.85}}
    move = s.detect_notable_move(aaa)
    assert move["period"] == "week"
    assert move["delta"] == 0.12
    assert "jumped 12¢ in the past week" in move["text"]
    assert "$3.85 -> $3.97" in move["text"]


def test_detect_notable_move_bigger_day_drop_beats_qualifying_week():
    aaa = {"current": {"regular": 3.60},
           "yesterday": {"regular": 3.72},
           "week_ago": {"regular": 3.70}}
    move = s.detect_notable_move(aaa)
    assert move["period"] == "day"
    assert move["delta"] == -0.12
    assert "dropped 12¢ since yesterday" in move["text"]


def test_detect_notable_move_none_below_thresholds_or_without_aaa():
    aaa = {"current": {"regular": 3.90}, "yesterday": {"regular": 3.87},
           "week_ago": {"regular": 3.82}}
    assert s.detect_notable_move(aaa) is None
    assert s.detect_notable_move({}) is None
    assert s.detect_notable_move(None) is None


# ---------------------------------------------------------------------------
# main() integration (offline — scrape_gasbuddy / fetch_eia_data stubbed)
# ---------------------------------------------------------------------------

def test_main_writes_status_and_strips_run_health(tmp_path, monkeypatch):
    out = tmp_path / "gas_prices.json"

    def fake_scrape():
        return {
            "source": "GasBuddy", "source_url": "x", "state": "Wisconsin",
            "price_date": "06/03/26", "scraped_at": "2026-06-03T00:00:00+00:00",
            "statewide": {"current_avg": {"regular": 3.9}, "low": {"regular": 3.8},
                          "high": {"regular": 4.0}},
            "metros": {"Wausau": _city(3.9)},
            "priority_metros": ["Wausau"],
            "run_health": {"cities_total": 15, "cities_fresh": 1,
                           "failed_cities": ["Madison"]},
        }

    monkeypatch.setattr(s, "scrape_gasbuddy", fake_scrape)
    monkeypatch.setattr(s, "scrape_aaa", lambda: {})
    monkeypatch.setattr(s, "scrape_neighbors", lambda: {})
    monkeypatch.setattr(s, "fetch_eia_data", lambda out_dir: False)
    monkeypatch.setattr(s, "fetch_eia_context", lambda out_dir: None)
    monkeypatch.setattr(s.sys, "argv", ["scrape_gas_prices.py", "-o", str(out)])
    s.main()

    live = json.loads(out.read_text(encoding="utf-8"))
    assert "run_health" not in live  # transient key stripped from the live file
    assert live["statewide"]["current_avg"]["regular"] == 3.9

    status = json.loads((tmp_path / "scrape_status.json").read_text(encoding="utf-8"))
    assert status["gasbuddy_success"] is True
    assert status["cities_fresh"] == 1
    assert status["cities_stale_preserved"] == 0
    assert status["eia_updated"] is False


def test_main_reports_failure_when_scrape_raises(tmp_path, monkeypatch):
    out = tmp_path / "gas_prices.json"

    def boom():
        raise RuntimeError("No CSRF token")

    monkeypatch.setattr(s, "scrape_gasbuddy", boom)
    monkeypatch.setattr(s, "scrape_aaa", lambda: {})
    monkeypatch.setattr(s, "scrape_neighbors", lambda: {})
    monkeypatch.setattr(s, "fetch_eia_data", lambda out_dir: True)
    monkeypatch.setattr(s, "fetch_eia_context", lambda out_dir: None)
    monkeypatch.setattr(s.sys, "argv", ["scrape_gas_prices.py", "-o", str(out)])
    s.main()

    assert not out.exists()  # live file untouched on total failure
    status = json.loads((tmp_path / "scrape_status.json").read_text(encoding="utf-8"))
    assert status["gasbuddy_success"] is False
    assert status["eia_updated"] is True
    # A hard failure must report real numbers, not "?/?" — the alert step prints these.
    assert status["cities_fresh"] == 0
    assert status["cities_total"] == len(s.CITIES)


# ---------------------------------------------------------------------------
# AAA independence — a GasBuddy block must not freeze the statewide trend
# ---------------------------------------------------------------------------

_AAA_FRESH = {
    "as_of": "08/01/26",
    "current":   {"regular": 3.97},
    "week_ago":  {"regular": 3.89},
    "year_ago":  {"regular": 2.96},
}


def test_main_refreshes_aaa_when_gasbuddy_fails(tmp_path, monkeypatch):
    """GasBuddy blocked + AAA reachable → AAA trend updates, station data untouched."""
    out = tmp_path / "gas_prices.json"
    previous = {
        "source": "GasBuddy", "price_date": "07/27/26",
        "scraped_at": "2026-07-27T18:24:37+00:00",
        "statewide": {"current_avg": {"regular": 3.823}},
        "metros": {"Wausau": _city(3.9), "Madison": _city(3.7)},
        "aaa": {"as_of": "07/27/26", "current": {"regular": 3.884}},
    }
    out.write_text(json.dumps(previous), encoding="utf-8")

    def boom():
        raise RuntimeError("No CSRF token")

    monkeypatch.setattr(s, "scrape_gasbuddy", boom)
    monkeypatch.setattr(s, "scrape_aaa", lambda: dict(_AAA_FRESH))
    monkeypatch.setattr(s, "scrape_neighbors", lambda: {})
    monkeypatch.setattr(s, "fetch_eia_data", lambda out_dir: True)
    monkeypatch.setattr(s, "fetch_eia_context", lambda out_dir: None)
    monkeypatch.setattr(s.sys, "argv", ["scrape_gas_prices.py", "-o", str(out)])
    s.main()

    live = json.loads(out.read_text(encoding="utf-8"))
    assert live["aaa"]["current"]["regular"] == 3.97   # AAA refreshed
    assert live["aaa"]["as_of"] == "08/01/26"
    # GasBuddy-derived fields stay exactly as they were, so the widget's freshness
    # label keeps telling the truth about the station data.
    assert live["price_date"] == "07/27/26"
    assert live["scraped_at"] == "2026-07-27T18:24:37+00:00"
    assert live["statewide"]["current_avg"]["regular"] == 3.823
    assert set(live["metros"]) == {"Wausau", "Madison"}
    # The blurb quotes AAA's legs, so it must be rebuilt against the fresh AAA.
    assert "8¢ higher than a week ago" in live["summary"]["blurb"]

    status = json.loads((tmp_path / "scrape_status.json").read_text(encoding="utf-8"))
    assert status["gasbuddy_success"] is False
    assert status["aaa_updated"] is True
    assert status["aaa_only"] is True


def test_publish_aaa_only_refreshes_neighbors_when_fetched(tmp_path):
    out = tmp_path / "gas_prices.json"
    previous = {
        "source": "GasBuddy", "price_date": "07/27/26",
        "statewide": {"current_avg": {"regular": 3.823}},
        "metros": {"Wausau": _city(3.9)},
        "neighbors": {"as_of": "07/27/26", "states": {"MN": {"name": "Minnesota",
                                                             "current": {"regular": 3.5}}}},
    }
    fresh_nb = {"as_of": "08/01/26", "states": {"MN": {"name": "Minnesota",
                                                       "current": {"regular": 3.6}}}}
    assert s.publish_aaa_only(str(out), dict(_AAA_FRESH), previous, fresh_nb) is True
    live = json.loads(out.read_text(encoding="utf-8"))
    assert live["neighbors"] == fresh_nb

    # An empty neighbors fetch keeps the carried-forward block instead.
    assert s.publish_aaa_only(str(out), dict(_AAA_FRESH), previous, {}) is True
    live = json.loads(out.read_text(encoding="utf-8"))
    assert live["neighbors"]["as_of"] == "07/27/26"


def test_main_leaves_file_untouched_when_gasbuddy_and_aaa_both_fail(tmp_path, monkeypatch):
    out = tmp_path / "gas_prices.json"
    previous = {"source": "GasBuddy", "price_date": "07/27/26",
                "statewide": {"current_avg": {"regular": 3.823}},
                "metros": {"Wausau": _city(3.9)}}
    original = json.dumps(previous)
    out.write_text(original, encoding="utf-8")

    def boom():
        raise RuntimeError("No CSRF token")

    monkeypatch.setattr(s, "scrape_gasbuddy", boom)
    monkeypatch.setattr(s, "scrape_aaa", lambda: {})
    monkeypatch.setattr(s, "scrape_neighbors", lambda: {})
    monkeypatch.setattr(s, "fetch_eia_data", lambda out_dir: True)
    monkeypatch.setattr(s, "fetch_eia_context", lambda out_dir: None)
    monkeypatch.setattr(s.sys, "argv", ["scrape_gas_prices.py", "-o", str(out)])
    s.main()

    assert out.read_text(encoding="utf-8") == original  # byte-for-byte untouched
    status = json.loads((tmp_path / "scrape_status.json").read_text(encoding="utf-8"))
    assert status["aaa_updated"] is False
    assert status["aaa_only"] is False


def test_main_refreshes_aaa_when_every_city_comes_back_empty(tmp_path, monkeypatch):
    """GasBuddy answers but returns no cities — AAA must still be published."""
    out = tmp_path / "gas_prices.json"
    out.write_text(json.dumps({
        "source": "GasBuddy", "price_date": "07/27/26",
        "scraped_at": "2026-07-27T18:24:37+00:00",
        "statewide": {"current_avg": {"regular": 3.823}},
        "metros": {"Wausau": _city(3.9)},
        "aaa": {"as_of": "07/27/26", "current": {"regular": 3.884}},
    }), encoding="utf-8")

    monkeypatch.setattr(s, "scrape_gasbuddy", lambda: {
        "source": "GasBuddy", "source_url": "x", "state": "Wisconsin",
        "price_date": "08/01/26", "scraped_at": "2026-08-01T13:00:00+00:00",
        "statewide": {"current_avg": {}}, "metros": {}, "priority_metros": [],
        "run_health": {"cities_total": 22, "cities_fresh": 0,
                       "failed_cities": ["Wausau"]},
    })
    monkeypatch.setattr(s, "scrape_aaa", lambda: dict(_AAA_FRESH))
    monkeypatch.setattr(s, "scrape_neighbors", lambda: {})
    monkeypatch.setattr(s, "fetch_eia_data", lambda out_dir: False)
    monkeypatch.setattr(s, "fetch_eia_context", lambda out_dir: None)
    monkeypatch.setattr(s.sys, "argv", ["scrape_gas_prices.py", "-o", str(out)])
    s.main()

    live = json.loads(out.read_text(encoding="utf-8"))
    assert live["aaa"]["as_of"] == "08/01/26"          # AAA published
    assert live["price_date"] == "07/27/26"            # station data preserved
    assert live["statewide"]["current_avg"]["regular"] == 3.823
    status = json.loads((tmp_path / "scrape_status.json").read_text(encoding="utf-8"))
    assert status["gasbuddy_success"] is False
    assert status["aaa_only"] is True


def test_publish_aaa_only_noop_without_previous_file(tmp_path):
    out = tmp_path / "gas_prices.json"
    assert s.publish_aaa_only(str(out), dict(_AAA_FRESH), {}) is False
    assert not out.exists()


def test_fresh_aaa_overrides_carried_forward_aaa(tmp_path, monkeypatch):
    """When GasBuddy succeeds, a fresh AAA must win over the previous run's."""
    out = tmp_path / "gas_prices.json"
    out.write_text(json.dumps({
        "metros": {}, "aaa": {"as_of": "07/27/26", "current": {"regular": 3.884}},
    }), encoding="utf-8")

    monkeypatch.setattr(s, "scrape_gasbuddy", lambda: {
        "source": "GasBuddy", "source_url": "x", "state": "Wisconsin",
        "price_date": "08/01/26", "scraped_at": "2026-08-01T13:00:00+00:00",
        "statewide": {"current_avg": {"regular": 3.9}},
        "metros": {"Wausau": _city(3.9)}, "priority_metros": ["Wausau"],
        "run_health": {"cities_total": 22, "cities_fresh": 22, "failed_cities": []},
    })
    monkeypatch.setattr(s, "scrape_aaa", lambda: dict(_AAA_FRESH))
    monkeypatch.setattr(s, "scrape_neighbors", lambda: {})
    monkeypatch.setattr(s, "fetch_eia_data", lambda out_dir: False)
    monkeypatch.setattr(s, "fetch_eia_context", lambda out_dir: None)
    monkeypatch.setattr(s.sys, "argv", ["scrape_gas_prices.py", "-o", str(out)])
    s.main()

    live = json.loads(out.read_text(encoding="utf-8"))
    assert live["aaa"]["as_of"] == "08/01/26"
    assert live["aaa"]["current"]["regular"] == 3.97
