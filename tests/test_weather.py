"""
Weather tests: the MET Norway daily summary, the commodity card and its guidance.

    pytest tests/test_weather.py -v
"""

import os, sys, importlib.util
from datetime import date, datetime, timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load(step):
    spec = importlib.util.spec_from_file_location(step, os.path.join(ROOT, "pipeline", f"{step}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def met_body(start, hourly_hours, six_hourly_steps, rain_1h=0.0, rain_6h=0.0, symbol="partlycloudy_day"):
    """A MET Norway-shaped timeseries: hourly steps, then 6-hourly steps (as the real API)."""
    ts, t = [], start
    for _ in range(hourly_hours):
        ts.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_temperature": 25.0, "wind_speed": 3.0, "relative_humidity": 50.0}},
            "next_1_hours": {"summary": {"symbol_code": symbol}, "details": {"precipitation_amount": rain_1h}},
            **({"next_12_hours": {"summary": {"symbol_code": symbol}, "details": {}}} if t.hour == 6 else {})}})
        t += timedelta(hours=1)
    for _ in range(six_hourly_steps):
        ts.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_temperature": 24.0, "wind_speed": 2.0, "relative_humidity": 60.0}},
            "next_6_hours": {"summary": {"symbol_code": symbol},
                             "details": {"precipitation_amount": rain_6h, "air_temperature_max": 33.0,
                                         "air_temperature_min": 19.0}},
            "next_12_hours": {"summary": {"symbol_code": symbol}, "details": {}}}})
        t += timedelta(hours=6)
    return {"properties": {"meta": {"updated_at": start.isoformat()}, "timeseries": ts}}


def test_daily_rain_is_never_counted_twice():
    ef = load("11_external_factors")
    start = datetime(2026, 10, 23, 23, 0, tzinfo=timezone.utc)   # 00:00 in Lagos
    body = met_body(start, hourly_hours=49, six_hourly_steps=12, rain_1h=0.5, rain_6h=3.0)
    rows = {r["date"]: r for r in ef.met_daily(body, today=date(2026, 10, 24))}
    assert rows["2026-10-24"]["rain_mm"] == 12.0, "24 hourly steps x 0.5 mm"
    assert rows["2026-10-24"]["hours_covered"] == 24
    later = rows["2026-10-27"]
    assert later["rain_mm"] == 12.0 and later["tmax_c"] == 33.0 and later["tmin_c"] == 19.0


def test_condition_words_and_day_icon():
    ef = load("11_external_factors")
    start = datetime(2026, 10, 23, 23, 0, tzinfo=timezone.utc)
    rows = ef.met_daily(met_body(start, 30, 4, symbol="rainshowersandthunder_night"), today=date(2026, 10, 24))
    assert rows[0]["condition"] == "Thunderstorms"
    assert rows[0]["symbol"] == "rainshowersandthunder_day"


def card_inputs(rain=0.0, high=32.0, condition="Mostly clear", heavy_day=None):
    days = []
    for k in range(7):
        d = (date(2026, 10, 23) + timedelta(days=k)).isoformat()
        r = 25.0 if k == heavy_day else rain
        days.append({"date": d, "day": ["Today", "Tomorrow", "Sun", "Mon", "Tue", "Wed", "Thu"][k],
                     "condition": "Heavy rain" if k == heavy_day else condition, "symbol": "fair_day",
                     "high_c": high, "low_c": 20.0, "rain_mm": r, "wind_max_kmh": 10, "humidity_pct": 40})
    return days


def test_strip_takes_the_most_common_condition():
    wr = load("weather_report")
    a = card_inputs(condition="Partly cloudy")
    b = card_inputs(condition="Partly cloudy")
    c = card_inputs(heavy_day=0)
    strip = wr.combine({"Ondo": a, "Osun": b, "Cross River": c}, date(2026, 10, 23))
    assert strip[0]["condition"] == "Partly cloudy", "one state's heavy rain doesn't label the whole card"
    assert strip[0]["rain_in"] == ["Cross River"]


def test_outlook_lines():
    wr = load("weather_report")
    today = date(2026, 10, 23)
    dry = {"Kaduna": card_inputs()}
    line, stats = wr.outlook(dry, wr.combine(dry, today))
    assert line == "Dry week ahead."
    wet = {"Ondo": card_inputs(heavy_day=4)}
    line, stats = wr.outlook(wet, wr.combine(wet, today))
    assert line.startswith("Heavy rain or storms on Tue in Ondo")
    hot = {"Kano": card_inputs(high=40.0)}
    line, _ = wr.outlook(hot, wr.combine(hot, today))
    assert "Very hot: up to 40°C in Kano" in line


def test_guidance_follows_the_crop_calendar():
    wr = load("weather_report")
    dry = {"rain_days": 0, "total_rain_mm": 0.0, "heavy_days": 0, "max_high_c": 32.0}
    wet = {"rain_days": 5, "total_rain_mm": 40.0, "heavy_days": 1, "max_high_c": 30.0}
    assert wr.guidance("Maize (white)", dry, 10).startswith("Harvest time: dry days help")
    assert wr.guidance("Maize (white)", wet, 10).startswith("Harvest time: rain can slow")
    assert wr.guidance("Ginger", dry, 10).startswith("Crop maturing"), "no dry-spell alarm when the dry season starts"
    assert wr.guidance("Ginger", dry, 7).startswith("Growing season: a dry spell")
    assert wr.guidance("Rice (foreign)", wet, 10).startswith("Mostly imported")
    assert wr.guidance("Wheat", dry, 10).startswith("Off-season")


def test_every_commodity_has_growing_states_with_weather_points():
    ef, wr = load("11_external_factors"), load("weather_report")
    from config.settings import COMMODITIES
    for c in COMMODITIES:
        states = wr.GROWING_STATES.get(c)
        assert states, f"{c} has no growing states"
        assert all(s in ef.PLACES for s in states), f"{c}: a growing state has no weather point"
        assert c in wr.CROP_CALENDAR
