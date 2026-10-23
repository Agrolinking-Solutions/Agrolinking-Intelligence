"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Live External Factors (FX + weather)

Pulls daily factors that drive Nigerian commodity prices and stores them in
their own files under data/external/. Like 10_web_price_search.py, this is
deliberately ISOLATED: it does not touch fx_rates.csv, inflation.csv,
fuel_prices.csv, or any model input. Wiring a factor into the forecast
regressors is a separate step, taken only after a backtest shows it helps
(weather did not: +0.4 points, Oct 2026).

Outputs:
  data/external/live_fx.csv                 date, ngn_per_usd, source            (one row/day)
  data/external/weather_daily.csv           date, zone, precip_mm, tmax_c, tmin_c, source
                                            (per zone: observed history + the latest forecast)
  data/external/weather_forecast_daily.csv  the latest ~9-day daily forecast for every place in
                                            PLACES: condition, icon symbol, high/low, rain, wind,
                                            humidity (rewritten each run; weather_report.py reads it)

Sources (all free, commercial use allowed):
  - FX:       open.er-api.com (mid-market USD->NGN, updated daily; free with attribution)
  - Weather:  NASA POWER - observed/satellite daily history (lags a few days; NASA data
              are free to use). The last 45 days are refreshed every run.
              MET Norway Locationforecast 2.0 - forecast ~9 days ahead, updated several
              times a day. Free, no key, CC BY 4.0 / NLOD 2.0: credit "Data from MET
              Norway". Their terms require an identifying User-Agent and no more than
              20 requests/second; we make one request per place per run.
  Open-Meteo was used until Oct 2026; its free service is for non-commercial use only.

Run:
  python pipeline/11_external_factors.py              # daily: FX + weather
  python pipeline/11_external_factors.py --backfill   # one-off: NASA POWER history since 2018
"""

import os, sys, argparse, time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

EXT_DIR       = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "external")
FX_PATH       = os.path.join(EXT_DIR, "live_fx.csv")
WEATHER_PATH  = os.path.join(EXT_DIR, "weather_daily.csv")
FORECAST_PATH = os.path.join(EXT_DIR, "weather_forecast_daily.csv")

BACKFILL_START = "20180101"
RECENT_DAYS    = 45          # NASA POWER days refreshed every run
TIMEOUT        = 60
MET_URL        = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
MET_HEADERS    = {"User-Agent": "AgrolinkingPIS/1.0 https://pis.agrolinking.com"}
LAGOS          = timezone(timedelta(hours=1))   # WAT, no daylight saving

# Weather points: state capitals of the main growing states (weather_report.GROWING_STATES)
# plus each zone's representative city. {state: (lat, lon, zone, city)}
PLACES = {
    "Kano":        (12.00,  8.52, "North West",    "Kano"),
    "Kaduna":      (10.52,  7.44, "North West",    "Kaduna"),
    "Katsina":     (12.99,  7.60, "North West",    "Katsina"),
    "Jigawa":      (11.76,  9.34, "North West",    "Dutse"),
    "Kebbi":       (12.45,  4.20, "North West",    "Birnin Kebbi"),
    "Sokoto":      (13.06,  5.24, "North West",    "Sokoto"),
    "Plateau":     ( 9.90,  8.90, "North Central", "Jos"),
    "Niger":       ( 9.61,  6.56, "North Central", "Minna"),
    "Benue":       ( 7.73,  8.54, "North Central", "Makurdi"),
    "Nasarawa":    ( 8.49,  8.52, "North Central", "Lafia"),
    "Kogi":        ( 7.80,  6.74, "North Central", "Lokoja"),
    "Borno":       (11.85, 13.16, "North East",    "Maiduguri"),
    "Bauchi":      (10.31,  9.84, "North East",    "Bauchi"),
    "Oyo":         ( 7.38,  3.95, "South West",    "Ibadan"),
    "Lagos":       ( 6.60,  3.35, "South West",    "Ikeja"),
    "Ogun":        ( 7.15,  3.35, "South West",    "Abeokuta"),
    "Ondo":        ( 7.25,  5.19, "South West",    "Akure"),
    "Osun":        ( 7.77,  4.56, "South West",    "Osogbo"),
    "Anambra":     ( 6.21,  7.07, "South East",    "Awka"),
    "Enugu":       ( 6.44,  7.50, "South East",    "Enugu"),
    "Ebonyi":      ( 6.32,  8.11, "South East",    "Abakaliki"),
    "Rivers":      ( 4.82,  7.05, "South South",   "Port Harcourt"),
    "Cross River": ( 4.95,  8.32, "South South",   "Calabar"),
}
# One representative point per zone in zones_config.json (zone-level history and the
# price-driver weather card)
ZONE_PLACE = {"North West": "Kano", "North Central": "Plateau", "North East": "Borno",
              "South West": "Oyo", "South East": "Anambra", "South South": "Rivers"}
ZONE_POINTS = {z: PLACES[s][:2] for z, s in ZONE_PLACE.items()}

WEATHER_COLS = ["date", "zone", "precip_mm", "tmax_c", "tmin_c", "source"]
# Higher wins when the same (date, zone) arrives from two sources:
# observed history beats model output.
SOURCE_RANK = {"nasa-power": 2, "met-norway": 1, "open-meteo": 1}

# MET Norway symbol codes (suffix _day/_night removed) in plain words
CONDITION = {
    "clearsky": "Clear", "fair": "Mostly clear", "partlycloudy": "Partly cloudy", "cloudy": "Cloudy",
    "fog": "Fog", "lightrain": "Light rain", "rain": "Rain", "heavyrain": "Heavy rain",
    "lightrainshowers": "Light showers", "rainshowers": "Showers", "heavyrainshowers": "Heavy showers",
    "lightrainandthunder": "Thunderstorms", "rainandthunder": "Thunderstorms",
    "heavyrainandthunder": "Heavy thunderstorms", "lightrainshowersandthunder": "Thunderstorms",
    "rainshowersandthunder": "Thunderstorms", "heavyrainshowersandthunder": "Heavy thunderstorms",
}
SEVERITY = ["clearsky", "fair", "partlycloudy", "cloudy", "fog", "lightrainshowers", "lightrain",
            "rainshowers", "rain", "heavyrainshowers", "heavyrain", "lightrainshowersandthunder",
            "lightrainandthunder", "rainshowersandthunder", "rainandthunder",
            "heavyrainshowersandthunder", "heavyrainandthunder"]
DAY_SYMBOL = {"clearsky", "fair", "partlycloudy", "lightrainshowers", "rainshowers", "heavyrainshowers",
              "lightrainshowersandthunder", "rainshowersandthunder", "heavyrainshowersandthunder"}


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


# ── Weather: observed history ────────────────────────────────────────────────
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


# ── Weather: forecast ────────────────────────────────────────────────────────
def base_symbol(code):
    return str(code or "").split("_")[0]


def met_daily(body, tz=LAGOS, today=None):
    """
    MET Norway Locationforecast timeseries -> one row per local day.

    Rain tiles the timeline without overlap: next_1_hours while the steps are
    hourly, then next_6_hours. High/low: instant temperatures plus the 6-hour
    max/min. The day's condition is MET's 12-hour symbol starting 07:00 local
    (06 UTC), which MET designs as the daytime summary; failing that, the
    most significant daytime symbol. Days the forecast covers for under 18
    hours are dropped, except today.
    """
    ts = sorted(body["properties"]["timeseries"], key=lambda s: s["time"])
    times = [datetime.fromisoformat(s["time"].replace("Z", "+00:00")) for s in ts]
    days, covered_until = {}, None
    today = today or datetime.now(tz).date()

    def day_of(t):
        return days.setdefault(t.astimezone(tz).date(), {"temps": [], "rain": 0.0, "hours": 0, "wind": [],
                                                         "rh": [], "symbols": [], "summary": None})

    for k, (t, s) in enumerate(zip(times, ts)):
        d, inst = s["data"], s["data"]["instant"]["details"]
        rec = day_of(t)
        if "air_temperature" in inst:
            rec["temps"].append(inst["air_temperature"])
        if "wind_speed" in inst:
            rec["wind"].append(inst["wind_speed"])
        if "relative_humidity" in inst:
            rec["rh"].append(inst["relative_humidity"])
        local = t.astimezone(tz)
        if "next_12_hours" in d and local.hour == 7:
            rec["summary"] = d["next_12_hours"]["summary"]["symbol_code"]
        if covered_until is not None and t < covered_until:
            continue
        hourly = k + 1 < len(times) and times[k + 1] - t == timedelta(hours=1)
        # 6-hour blocks only from their 00/06/12/18 UTC boundaries, so the hand-over
        # from hourly steps never counts an hour twice
        one_hour = "next_1_hours" in d and (hourly or t.hour % 6 != 0)
        block = d.get("next_1_hours") if one_hour else d.get("next_6_hours")
        if not block:
            continue
        span = 1 if block is d.get("next_1_hours") else 6
        mid = day_of(t + timedelta(hours=span / 2))
        mid["rain"] += float(block.get("details", {}).get("precipitation_amount", 0.0) or 0.0)
        mid["hours"] += span
        det = block.get("details", {})
        for key in ("air_temperature_max", "air_temperature_min"):
            if key in det:
                mid["temps"].append(det[key])
        if 7 <= (t + timedelta(hours=span / 2)).astimezone(tz).hour < 19:
            mid["symbols"].append(block.get("summary", {}).get("symbol_code"))
        covered_until = t + timedelta(hours=span)

    rows = []
    for day, rec in sorted(days.items()):
        if not rec["temps"] or (rec["hours"] < 18 and day != today) or day < today:
            continue
        sym = rec["summary"]
        if not sym:
            cand = [base_symbol(x) for x in rec["symbols"] if x]
            sym = max(cand, key=lambda c: SEVERITY.index(c) if c in SEVERITY else 0) if cand else "cloudy"
        b = base_symbol(sym)
        rows.append({"date": day.isoformat(), "condition": CONDITION.get(b, b.replace("and", " and ").title()),
                     "symbol": f"{b}_day" if b in DAY_SYMBOL else b,
                     "tmax_c": round(max(rec["temps"]), 1), "tmin_c": round(min(rec["temps"]), 1),
                     "rain_mm": round(rec["rain"], 1),
                     "wind_max_kmh": round(max(rec["wind"]) * 3.6) if rec["wind"] else None,
                     "humidity_pct": round(sum(rec["rh"]) / len(rec["rh"])) if rec["rh"] else None,
                     "hours_covered": rec["hours"]})
    return rows


def fetch_met(state, lat, lon, zone, city):
    r = requests.get(MET_URL, params={"lat": f"{lat:.2f}", "lon": f"{lon:.2f}"},
                     headers=MET_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    issued = body["properties"]["meta"]["updated_at"]
    rows = met_daily(body)
    for row in rows:
        row.update({"issued_at": issued, "state": state, "city": city, "zone": zone,
                    "lat": lat, "lon": lon, "source": "met-norway"})
    return rows


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
    frames, forecast = [], []
    start = BACKFILL_START if backfill else (date.today() - timedelta(days=RECENT_DAYS)).strftime("%Y%m%d")
    end = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    for zone, (lat, lon) in ZONE_POINTS.items():
        try:
            frames.append(fetch_power(zone, lat, lon, start, end))
        except Exception as e:
            logger.warning(f"  NASA POWER: {zone} failed - {e}")
    for state, (lat, lon, zone, city) in PLACES.items():
        try:
            forecast += fetch_met(state, lat, lon, zone, city)
        except Exception as e:
            logger.warning(f"  MET Norway: {state} failed - {e}")
        time.sleep(0.5)          # well under MET's 20 requests/second
    if forecast:
        fc = pd.DataFrame(forecast)
        cols = ["issued_at", "date", "state", "city", "zone", "lat", "lon", "condition", "symbol",
                "tmax_c", "tmin_c", "rain_mm", "wind_max_kmh", "humidity_pct", "hours_covered", "source"]
        fc[cols].to_csv(FORECAST_PATH, index=False)
        logger.info(f"  Forecast: {fc['state'].nunique()} places, {fc['date'].min()} to {fc['date'].max()} "
                    f"(MET Norway, issued {fc['issued_at'].max()})")
        zone_fc = fc[fc["state"].isin(ZONE_PLACE.values())].rename(columns={"rain_mm": "precip_mm"})
        frames.append(zone_fc.assign(date=pd.to_datetime(zone_fc["date"]))[WEATHER_COLS])
    if not frames:
        raise RuntimeError("no weather data fetched for any zone")
    out = merge_weather(pd.concat(frames, ignore_index=True))
    logger.info(f"  Weather history: {out['zone'].nunique()} zones, "
                f"{out['date'].min().date()} to {out['date'].max().date()}, {len(out):,} rows")


def run_external_factors(backfill=False):
    logger.info("Live external factors")
    os.makedirs(EXT_DIR, exist_ok=True)
    ok = True
    for name, fn in (("FX", update_fx), ("Weather", lambda: update_weather(backfill))):
        try:
            fn()
        except Exception as e:
            ok = False
            logger.error(f"  {name} update failed - {e}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="pull NASA POWER daily history since 2018 instead of the last 45 days")
    sys.exit(0 if run_external_factors(ap.parse_args().backfill) else 1)
