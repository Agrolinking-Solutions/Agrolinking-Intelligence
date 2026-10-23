"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Daily weather report, and a weather card for every commodity

Reads the latest MET Norway forecast that 11_external_factors.py saves
(data/external/weather_forecast_daily.csv: every place in its PLACES, ~9 days)
and writes outputs/intelligence/weather_YYYY-MM-DD.json with:

  report        today and the next REPORT_DAYS - 1 days for every place, by zone:
                condition (plain words + MET icon symbol), high/low, rain, wind,
                humidity
  commodities   for each commodity, its main producing states (GROWING_STATES):
                a combined day-by-day strip, each state's days, a one-line outlook
                ("Dry week ahead", "Heavy rain on Mon in Kaduna") and a one-line
                guidance for that commodity at this time of year (CROP_CALENDAR)

Weather is shown for context. It is not used in the price forecasts: a backtest
found it adds about 0.4 accuracy points (Oct 2026). The guidance is general
crop-calendar knowledge, not an effect measured on prices, and says so.

Served at /weather/today and /weather/{commodity}. Credit: "Data from MET Norway"
(CC BY 4.0). Never fails the pipeline.

Run: python pipeline/weather_report.py
"""

import os, sys, json
from datetime import datetime, timezone, timedelta

import pandas as pd
from loguru import logger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config.settings import COMMODITIES

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="INFO")

FORECAST_PATH = os.path.join(ROOT, "data", "external", "weather_forecast_daily.csv")
OUT_DIR = os.path.join(ROOT, "outputs", "intelligence")
REPORT_DAYS = 7
LAGOS = timezone(timedelta(hours=1))
SOURCE = "MET Norway Locationforecast (data from MET Norway, CC BY 4.0)"
HEAVY_MM, RAIN_MM, HOT_C = 20.0, 1.0, 38.0
RAINY_MONTHS = {5, 6, 7, 8, 9}   # a dry week is only unusual in the rainy season

ZONE_ORDER = ["North West", "North Central", "North East", "South West", "South East", "South South"]
ZONE_CITY = {"North West": "Kano", "North Central": "Plateau", "North East": "Borno",   # as 11_external_factors
             "South West": "Oyo", "South East": "Anambra", "South South": "Rivers"}

# Main producing states per commodity (general production geography; three each so the
# card stays readable). zones_config.json lists where commodities are TRADED, which is
# not the same (it put cocoa in Lagos and Rivers). Edit here if the team knows better.
GROWING_STATES = {
    "Maize (white)":  ["Kaduna", "Niger", "Plateau"],
    "Maize (yellow)": ["Kaduna", "Niger", "Plateau"],
    "Sorghum":        ["Kano", "Katsina", "Bauchi"],
    "Sesame":         ["Nasarawa", "Benue", "Jigawa"],
    "Soybeans":       ["Benue", "Kaduna", "Niger"],
    "Ginger":         ["Kaduna", "Nasarawa", "Plateau"],
    "Hibiscus":       ["Kano", "Jigawa", "Katsina"],
    "Cocoa":          ["Ondo", "Cross River", "Osun"],
    "Cashew Nuts":    ["Kogi", "Oyo", "Enugu"],
    "Beans (white)":  ["Kano", "Katsina", "Borno"],
    "Beans (red)":    ["Kano", "Katsina", "Borno"],
    "Wheat":          ["Jigawa", "Kano", "Kebbi"],
    "Rice":           ["Kebbi", "Niger", "Ebonyi"],
    "Rice (local)":   ["Kebbi", "Niger", "Ebonyi"],
    "Rice (foreign)": ["Lagos"],
    "Meat (beef)":    ["Kano", "Sokoto", "Borno"],
    "Meat (goat)":    ["Kano", "Sokoto", "Borno"],
    "Fish (dried)":   ["Borno", "Kebbi", "Niger"],
    "Eggs":           ["Ogun", "Oyo", "Plateau"],
}
AREA_LABEL = {"Rice (foreign)": "its main import port and market", "Meat (beef)": "its main livestock states",
              "Meat (goat)": "its main livestock states", "Fish (dried)": "its main fishing states",
              "Eggs": "its main poultry states"}

# General Nigerian crop calendar (months). kind: rainfed / irrigated / tree / livestock / fish / eggs / imported
CROP_CALENDAR = {
    "Maize (white)":  {"kind": "rainfed", "plant": [4, 5, 6], "grow": [6, 7, 8], "harvest": [9, 10, 11]},
    "Maize (yellow)": {"kind": "rainfed", "plant": [4, 5, 6], "grow": [6, 7, 8], "harvest": [9, 10, 11]},
    "Sorghum":        {"kind": "rainfed", "plant": [6, 7], "grow": [7, 8, 9], "harvest": [10, 11, 12]},
    "Sesame":         {"kind": "rainfed", "plant": [6, 7], "grow": [7, 8, 9], "harvest": [10, 11, 12]},
    "Soybeans":       {"kind": "rainfed", "plant": [6, 7], "grow": [7, 8, 9], "harvest": [10, 11, 12]},
    "Hibiscus":       {"kind": "rainfed", "plant": [6, 7], "grow": [7, 8, 9], "harvest": [10, 11, 12]},
    "Beans (white)":  {"kind": "rainfed", "plant": [7, 8], "grow": [8, 9], "harvest": [10, 11, 12]},
    "Beans (red)":    {"kind": "rainfed", "plant": [7, 8], "grow": [8, 9], "harvest": [10, 11, 12]},
    "Ginger":         {"kind": "rainfed", "plant": [4, 5], "grow": [6, 7, 8, 9, 10, 11], "harvest": [12, 1, 2, 3]},
    "Rice":           {"kind": "rainfed", "plant": [5, 6, 7], "grow": [7, 8, 9], "harvest": [10, 11, 12]},
    "Rice (local)":   {"kind": "rainfed", "plant": [5, 6, 7], "grow": [7, 8, 9], "harvest": [10, 11, 12]},
    "Wheat":          {"kind": "irrigated", "plant": [11, 12], "grow": [12, 1, 2], "harvest": [3, 4]},
    "Cocoa":          {"kind": "tree", "harvest": [10, 11, 12, 1, 2, 3]},
    "Cashew Nuts":    {"kind": "tree", "flower": [11, 12, 1], "harvest": [2, 3, 4, 5]},
    "Rice (foreign)": {"kind": "imported"},
    "Meat (beef)":    {"kind": "livestock"},
    "Meat (goat)":    {"kind": "livestock"},
    "Fish (dried)":   {"kind": "fish"},
    "Eggs":           {"kind": "eggs"},
}


def day_name(d, today):
    d = pd.Timestamp(d).date()
    return "Today" if d == today else ("Tomorrow" if d == today + timedelta(days=1) else d.strftime("%a"))


def place_days(fc, state, today, n=REPORT_DAYS):
    sub = fc[(fc["state"] == state) & (fc["date"] >= str(today))].sort_values("date").head(n)
    return [{"date": r["date"], "day": day_name(r["date"], today), "condition": r["condition"], "symbol": r["symbol"],
             "high_c": r["tmax_c"], "low_c": r["tmin_c"], "rain_mm": r["rain_mm"],
             "wind_max_kmh": None if pd.isna(r["wind_max_kmh"]) else int(r["wind_max_kmh"]),
             "humidity_pct": None if pd.isna(r["humidity_pct"]) else int(r["humidity_pct"])}
            for _, r in sub.iterrows()]


SEVERITY = ["Clear", "Mostly clear", "Partly cloudy", "Cloudy", "Fog", "Light showers", "Light rain", "Showers",
            "Rain", "Heavy showers", "Heavy rain", "Thunderstorms", "Heavy thunderstorms"]


def combine(by_state, today):
    """One strip for a commodity: per day the most common condition across its states (the more
    significant one on a tie), average high/low and rain, and which states expect rain. (Taking
    the worst condition labelled a 1.7 mm day "Heavy rain" because of one state.)"""
    days = {}
    for state, rows in by_state.items():
        for r in rows:
            days.setdefault(r["date"], []).append((state, r))
    out = []
    for d in sorted(days):
        rs = days[d]
        sev = lambda c: SEVERITY.index(c) if c in SEVERITY else 0
        counts = {}
        for _, r in rs:
            counts[r["condition"]] = counts.get(r["condition"], 0) + 1
        cond = max(counts, key=lambda c: (counts[c], sev(c)))
        worst = next(r for _, r in rs if r["condition"] == cond)
        out.append({"date": d, "day": day_name(d, today), "condition": worst["condition"], "symbol": worst["symbol"],
                    "high_c": round(sum(r["high_c"] for _, r in rs) / len(rs), 1),
                    "low_c": round(sum(r["low_c"] for _, r in rs) / len(rs), 1),
                    "rain_mm": round(sum(r["rain_mm"] for _, r in rs) / len(rs), 1),
                    "rain_in": [s for s, r in rs if r["rain_mm"] >= RAIN_MM]})
    return out[:REPORT_DAYS]


def outlook(by_state, strip):
    """One plain line about the coming days in the growing states."""
    heavy = [(r["day"], s) for s, rows in by_state.items() for r in rows
             if r["rain_mm"] >= HEAVY_MM or "thunder" in r["condition"].lower()]
    rain_days = sum(1 for d in strip if d["rain_in"])
    total = sum(d["rain_mm"] for d in strip)
    hot = max(((r["high_c"], r["day"], s) for s, rows in by_state.items() for r in rows), default=None)
    if heavy:
        days = sorted({d for d, _ in heavy}, key=[x["day"] for x in strip].index)
        where = sorted({s for _, s in heavy})
        line = f"Heavy rain or storms on {', '.join(days)} in {', '.join(where)}."
    elif rain_days >= 4:
        line = f"Rain on most days ({rain_days} of {len(strip)})."
    elif total < 2:
        line = f"Dry {'week' if len(strip) >= 6 else 'days'} ahead."
    else:
        line = f"Mostly dry, with some rain on {rain_days} day{'s' if rain_days != 1 else ''}."
    if hot and hot[0] >= HOT_C:
        line += f" Very hot: up to {hot[0]:.0f}°C in {hot[2]} on {hot[1]}."
    return line, {"rain_days": rain_days, "total_rain_mm": round(total, 1), "heavy_days": len({d for d, _ in heavy}),
                  "max_high_c": None if not hot else hot[0]}


def guidance(commodity, stats, month):
    """General guidance for this commodity at this time of year. Not a measured price effect."""
    cal = CROP_CALENDAR.get(commodity, {"kind": "rainfed"})
    kind = cal["kind"]
    wet = stats["heavy_days"] > 0 or stats["rain_days"] >= 4
    dry = stats["total_rain_mm"] < 2
    hot = (stats["max_high_c"] or 0) >= HOT_C
    if kind == "imported":
        return "Mostly imported: local weather matters little, except for road transport."
    if kind == "livestock":
        return "Very hot days stress animals." if hot else "Weather has little short-term effect on livestock supply."
    if kind == "fish":
        return "Wet days slow fish drying." if stats["rain_days"] >= 3 else "Dry weather suits fish drying."
    if kind == "eggs":
        return "Heat can reduce egg laying." if hot else "No weather concern for laying flocks this week."
    if month in cal.get("harvest", []):
        if wet:
            return "Harvest time: rain can slow harvesting, drying and transport."
        return "Harvest time: dry days help harvesting and drying." if dry else \
            "Harvest time: mostly good weather for harvesting."
    if kind == "irrigated":
        if month in cal.get("plant", []) + cal.get("grow", []):
            return "Dry-season crop on irrigation: rain matters little; cool nights help it."
        return "Off-season: the crop is planted from November."
    if kind == "tree":
        if month in cal.get("flower", []):
            return "Flowering time: dry, sunny days help the trees set nuts." if not wet else \
                "Flowering time: heavy rain can cut the coming harvest."
        return "Between harvests: weather matters less for prices right now."
    if month in cal.get("plant", []):
        return "Planting time: farmers need steady rain to plant." if dry else "Planting time: rain is helping planting."
    if month in cal.get("grow", []):
        if dry and month in RAINY_MONTHS:
            return "Growing season: a dry spell now can stress the crop."
        if month not in RAINY_MONTHS:
            return "Crop maturing: dry weather is normal at this time of year."
        return "Growing season: heavy rain can flood low fields." if stats["heavy_days"] else \
            "Growing season: normal weather for the crop."
    return "Off-season: weather matters less for prices right now."


def build(fc, today=None):
    today = today or datetime.now(LAGOS).date()
    fc = fc.copy()
    fc["date"] = fc["date"].astype(str)
    places = fc[["state", "city", "zone", "lat", "lon"]].drop_duplicates("state").copy()
    places["z"] = places["zone"].map({z: k for k, z in enumerate(ZONE_ORDER)}).fillna(9)
    places["main"] = ~places["state"].isin(ZONE_CITY.values())
    places = places.sort_values(["z", "main", "state"])
    report = []
    for _, p in places.iterrows():
        days = place_days(fc, p["state"], today)
        if days:
            report.append({"state": p["state"], "city": p["city"], "zone": p["zone"],
                           "zone_main_city": not p["main"], "days": days})
    commodities = {}
    for c in COMMODITIES:
        states = [s for s in GROWING_STATES.get(c, []) if (fc["state"] == s).any()]
        if not states:
            continue
        by_state = {s: place_days(fc, s, today) for s in states}
        strip = combine(by_state, today)
        if not strip:
            continue
        line, stats = outlook(by_state, strip)
        commodities[c] = {
            "areas": states, "area_label": AREA_LABEL.get(c, "its main growing states"),
            "title": f"Weather in {', '.join(states)}",
            "days": strip, "by_state": by_state, "outlook": line,
            "guidance": guidance(c, stats, today.month),
            "guidance_basis": "General Nigerian crop calendar; weather is not used in the price forecast",
            "stats": stats,
        }
    issued = fc["issued_at"].max() if "issued_at" in fc else None
    return {"as_of": str(today), "issued_at": issued, "source": SOURCE,
            "attribution": "Data from MET Norway", "license": "CC BY 4.0",
            "days": REPORT_DAYS, "report": report, "commodities": commodities}


def run():
    logger.info("=" * 60)
    logger.info("WEATHER REPORT (MET Norway forecast)")
    logger.info("=" * 60)
    if not os.path.exists(FORECAST_PATH):
        logger.warning("  no weather forecast file - run 11_external_factors.py first")
        return False
    rep = build(pd.read_csv(FORECAST_PATH))
    if not rep["report"]:
        logger.warning("  forecast file has no days from today - nothing written")
        return False
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"weather_{rep['as_of']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    for c, card in rep["commodities"].items():
        logger.info(f"  {c:<15} {card['outlook']}  |  {card['guidance']}")
    logger.info(f"  {len(rep['report'])} places, {len(rep['commodities'])} commodity cards -> {path}")
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.warning(f"Weather report failed this run (pipeline continues): {e}")
    sys.exit(0)
