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
# statewide_from_history (comparison baselines come from our own history)
# ---------------------------------------------------------------------------

def _hist(*pairs):
    """Build a history dict from (mm/dd/yy, statewide_regular) pairs."""
    return {k: {"statewide": {"regular": v}} for k, v in pairs}


def test_statewide_from_history_nearest_within_tolerance():
    hist = _hist(("05/28/26", 4.21), ("05/05/26", 4.29), ("06/03/26", 3.95))
    # 7 days before 06/04/26 = 05/28 -> 4.21
    assert s.statewide_from_history(hist, "06/04/26", 7, 4) == 4.21
    # 30 days before -> ~05/05 -> 4.29
    assert s.statewide_from_history(hist, "06/04/26", 30, 10) == 4.29


def test_statewide_from_history_none_when_outside_tolerance():
    hist = _hist(("01/01/26", 3.00))  # ~a year-ish off any recent target
    assert s.statewide_from_history(hist, "06/04/26", 7, 4) is None


def test_statewide_from_history_none_when_empty():
    assert s.statewide_from_history({}, "06/04/26", 7, 4) is None


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
    hist_path = tmp_path / "gas_prices_history.json"
    seed = {f"{d:04d}": {"statewide": {"regular": 3.0}} for d in range(405)}
    hist_path.write_text(json.dumps(seed))

    data = {"price_date": "9999", "statewide": {"current_avg": {"regular": 3.9}}, "metros": {}}
    s.update_history(data, str(tmp_path))
    hist = json.loads(hist_path.read_text())
    assert len(hist) == 400
    assert "9999" in hist  # newest kept
    assert "0000" not in hist  # oldest trimmed


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
        "statewide": {"current_avg": {"regular": 3.92}},
        "metros": {"Madison": _city(3.68), "Wausau": _city(4.41)},
    }
    history = _hist(("05/28/26", 3.88), ("05/05/26", 3.80), ("06/04/25", 3.32))
    out = s.build_summary(data, history)
    assert out["as_of"] == "June 4, 2026"
    assert out["headline"] == "Wisconsin gas averages $3.92/gal"
    b = out["blurb"]
    assert "$3.92 per gallon" in b
    assert "up 4¢ from a week ago" in b
    assert "up 60¢ from a year ago" in b
    assert "Madison is cheapest ($3.68)" in b
    assert "Wausau priciest ($4.41)" in b


def test_build_summary_skips_unchanged_and_missing_comparisons():
    data = {
        "price_date": "06/04/26",
        "statewide": {"current_avg": {"regular": 3.50}},
        "metros": {"Madison": _city(3.50)},  # only one metro -> no metro clause
    }
    history = _hist(("05/28/26", 3.50))  # week-ago unchanged -> skipped; no month/year
    out = s.build_summary(data, history)
    assert "from a week ago" not in out["blurb"]
    assert "Among metro areas" not in out["blurb"]
    assert out["blurb"].endswith("$3.50 per gallon.")


def test_build_summary_empty_without_regular():
    assert s.build_summary({"statewide": {"current_avg": {}}}, {}) == {}


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


def test_build_summary_excludes_stale_metros():
    data = {
        "price_date": "06/04/26",
        "statewide": {"current_avg": {"regular": 3.92}},
        "metros": {
            "Madison": _city(3.68),
            "Wausau": _city(4.41),
            "Ghost": {**_city(1.50), "stale": True},  # implausible + stale -> ignored
        },
    }
    b = s.build_summary(data, {})["blurb"]
    assert "Ghost" not in b
    assert "Madison is cheapest ($3.68)" in b


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
    monkeypatch.setattr(s, "fetch_eia_data", lambda out_dir: True)
    monkeypatch.setattr(s, "fetch_eia_context", lambda out_dir: None)
    monkeypatch.setattr(s.sys, "argv", ["scrape_gas_prices.py", "-o", str(out)])
    s.main()

    assert not out.exists()  # live file untouched on total failure
    status = json.loads((tmp_path / "scrape_status.json").read_text(encoding="utf-8"))
    assert status["gasbuddy_success"] is False
    assert status["eia_updated"] is True
