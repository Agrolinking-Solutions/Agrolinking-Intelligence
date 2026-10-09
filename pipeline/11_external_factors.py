"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Live External Factors (FX + weather)

Pulls daily factors that drive Nigerian commodity prices and stores them in
their own files under data/external/. Like 10_web_price_search.py, this is
deliberately ISOLATED: it does not touch fx_rates.csv, inflation.csv,
fuel_prices.csv, or any model input. Wiring a factor into the forecast
regressors is a separate step, taken only after a backtest shows it helps.

Outputs:
  data/external/live_fx.csv        date, ngn_per_usd, source          (one row/day)
  data/external/weather_daily.csv  date, zone, precip_mm, tmax_c, tmin_c, source

Sources (all free, no API key):
  - FX:       open.er-api.com (mid-market USD->NGN, updated daily)
  - Weather:  NASA POWER (observed/satellite daily history, lags a few days)
              Open-Meteo (last 14 days + 16-day forecast, bridges the lag)
  Note: Open-Meteo's free tier is non-commercial. Check their commercial
  terms/pricing before relying on it in the paid product.

Each zone uses one representative city (see ZONE_POINTS).

Run:
  python pipeline/11_external_factors.py              # daily: FX + weather forecast
  python pipeline/11_external_factors.py --backfill   # one-off: NASA POWER history since 2018
"""

import os, sys, argparse
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

EXT_DIR      = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "external")
FX_PATH      = os.path.join(EXT_DIR, "live_fx.csv")
WEATHER_PATH = os.path.join(EXT_DIR, "weather_daily.csv")

BACKFILL_START = "20180101"
TIMEOUT        = 60

# One representative point per zone in zones_config.json
ZONE_POINTS = {
    "North West":    (12.00,  8.52),   # Kano
    "North Central": ( 9.90,  8.90),   # Jos (Plateau)
    "North East":    (11.85, 13.16),   # Maiduguri
    "South West":    ( 7.38,  3.95),   # Ibadan
    "South East":    ( 6.21,  7.07),   # Awka
    "South South":   ( 4.82,  7.05),   # Port Harcourt
}

WEATHER_COLS = ["date", "zone", "precip_mm", "tmax_c", "tmin_c", "source"]
# Higher wins when the same (date, zone) arrives from two sources:
# observed history beats model output.
SOURCE_RANK = {"nasa-power": 2, "open-meteo": 1}


# ── FX ───────────────────────────────────────────────────────────────────────
def fetch_fx():
    r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if body.get("result") != "success":
        raise RuntimeError(f"FX API returned {body.get('result')}")
    rate = float(body["rates"]["NGN"])
    day = datetime.fromtimestamp(body["time_last_update_unix"], tz=timezone.utc).date()
    return day, rate


def update_fx():
    day, rate = fetch_fx()
    row = pd.DataFrame([{"date": day.isoformat(), "ngn_per_usd": round(rate, 2),
                         "source": "open.er-api.com"}])
    if os.path.exists(FX_PATH):
        old = pd.read_csv(FX_PATH)
        old = old[old["date"] != day.isoformat()]
        row = pd.concat([old, row], ignore_index=True)
    row.sort_values("date").to_csv(FX_PATH, index=False)
    logger.info(f"  FX: 1 USD = {rate:,.2f} NGN ({day})")


# ── Weather ──────────────────────────────────────────────────────────────────
def fetch_power(zone, lat, lon, start, end):
    r = requests.get(
        "https://power.larc.nasa.gov/api/temporal/daily/point",
        params={"start": start, "end": end, "latitude": lat, "longitude": lon,
                "community": "ag", "format": "json",
                "parameters": "PRECTOTCORR,T2M_MAX,T2M_MIN"},
        timeout=TIMEOUT * 3)
    r.raise_for_status()
    p = r.json()["properties"]["parameter"]
    df = pd.DataFrame({"precip_mm": p["PRECTOTCORR"], "tmax_c": p["T2M_MAX"],
                       "tmin_c": p["T2M_MIN"]})
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.mask(df <= -990)            # POWER fill value is -999
    df = df.dropna(how="all").reset_index(names="date")
    df["zone"], df["source"] = zone, "nasa-power"
    return df[WEATHER_COLS]


def fetch_open_meteo(zone, lat, lon):
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon, "past_days": 14, "forecast_days": 16,
                "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
                "timezone": "Africa/Lagos"},
        timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()["daily"]
    df = pd.DataFrame({"date": pd.to_datetime(d["time"]),
                       "precip_mm": d["precipitation_sum"],
                       "tmax_c": d["temperature_2m_max"],
                       "tmin_c": d["temperature_2m_min"]})
    df["zone"], df["source"] = zone, "open-meteo"
    return df[WEATHER_COLS]


def merge_weather(new):
    if os.path.exists(WEATHER_PATH):
        old = pd.read_csv(WEATHER_PATH, parse_dates=["date"])
        new = pd.concat([old, new], ignore_index=True)
    new["rank"] = new["source"].map(SOURCE_RANK).fillna(0)
    # observed beats model; for equal rank the later row (fresher forecast) wins
    new = (new.reset_index().sort_values(["rank", "index"])
              .drop_duplicates(["date", "zone"], keep="last")
              .drop(columns=["rank", "index"])
              .sort_values(["zone", "date"]))
    new.to_csv(WEATHER_PATH, index=False)
    return new


def update_weather(backfill=False):
    frames = []
    for zone, (lat, lon) in ZONE_POINTS.items():
        try:
            if backfill:
                end = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
                frames.append(fetch_power(zone, lat, lon, BACKFILL_START, end))
            frames.append(fetch_open_meteo(zone, lat, lon))
        except Exception as e:
            logger.warning(f"  Weather: {zone} failed — {e}")
    if not frames:
        raise RuntimeError("no weather data fetched for any zone")
    out = merge_weather(pd.concat(frames, ignore_index=True))
    logger.info(f"  Weather: {out['zone'].nunique()} zones, "
                f"{out['date'].min().date()} → {out['date'].max().date()}, {len(out):,} rows")


def run_external_factors(backfill=False):
    logger.info("Live external factors")
    os.makedirs(EXT_DIR, exist_ok=True)
    ok = True
    for name, fn in (("FX", update_fx), ("Weather", lambda: update_weather(backfill))):
        try:
            fn()
        except Exception as e:
            ok = False
            logger.error(f"  {name} update failed — {e}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="also pull NASA POWER daily history since 2018")
    sys.exit(0 if run_external_factors(ap.parse_args().backfill) else 1)
