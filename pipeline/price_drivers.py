"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Price drivers: what is happening to the factors behind each commodity's price

For every commodity, a card per factor - weather, season, climate, fuel,
exchange rate, inflation, and the world price where one exists - with today's reading, its recent change, its source and date, and
why it matters for that commodity. Served by the API at /drivers.

Honesty rules (agreed with John, 16 Oct 2026):
  - Every reading names its source and date; a reading older than its
    STALE_DAYS limit is marked stale instead of shown as current.
  - "Pushes the price up/down" is only stated where it is MEASURED on our
    real prices. Today that is the seasonal pattern. For the other factors
    the card shows the factor's own movement (fuel +5%) and how much it
    matters for this commodity, not a claimed price effect.
  - These cards describe the market; the forecasts do not use weather, fuel
    or the exchange rate (the backtest found they did not improve accuracy),
    so nothing here says the forecast moved "because of" a factor. Re-tested
    16 Oct 2026 for climate (scripts/research/climate_signal_backtest.py):
    adding rainy-season rainfall or the El Nino index made 3- and 6-month
    forecasts worse (e.g. 6 months 22.7% -> 27.8% error), so they stay
    context only.

Inputs (all real): weather_daily.csv (NASA POWER history + MET Norway
forecast per zone), cbn_usd_daily.csv and cbn_inflation_monthly.csv (CBN),
wfp_petrol_monthly.csv (WFP), the master dataset's real prices (seasonal
pattern), zones_config.json (where each commodity is mainly grown).

Writes outputs/intelligence/drivers_YYYY-MM-DD.json. Never fails the run.
Run: python pipeline/price_drivers.py
"""

import os, sys, json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, REAL_SOURCES
from pipeline.drift_models import seasonal_index

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT     = os.path.dirname(PATHS["inflation"])
OUT_DIR = os.path.join(BASE, "outputs", "intelligence")
WEATHER_DAYS = 14
STALE_DAYS = {"weather": 3, "exchange_rate": 10, "inflation": 75, "fuel": 90, "climate": 21, "world_price": 45}
# World benchmark (Mansa Markets page) per commodity, and why it matters.
WORLD_OF = {
    "Wheat":          ("wheat",   "high",   "Most wheat is imported, so the world price sets the floor for local prices"),
    "Rice (foreign)": ("rice",    "high",   "Imported rice is priced off the world market"),
    "Cocoa":          ("cocoa",   "high",   "Nigerian cocoa is sold abroad at close to the world price"),
    "Rice (local)":   ("rice",    "medium", "Competes with imported rice"),
    "Soybeans":       ("soybean", "medium", "Traded regionally and used in feed; the world price is a reference"),
    "Maize (white)":  ("maize",   "low",    "Little maize crosses the border; the world price is a loose reference"),
    "Maize (yellow)": ("maize",   "low",    "Little maize crosses the border; the world price is a loose reference"),
}
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

# How much each factor matters for each kind of commodity - our assessment,
# shown as "why it matters", never as a measured price effect.
GROUPS = {
    "local crop":  ["Maize (white)", "Maize (yellow)", "Sorghum", "Beans (white)", "Beans (red)",
                    "Rice (local)", "Soybeans"],
    "imported":    ["Rice (foreign)", "Wheat"],
    "export crop": ["Cocoa", "Sesame", "Cashew Nuts", "Ginger", "Hibiscus"],
    "livestock":   ["Meat (beef)", "Meat (goat)", "Fish (dried)", "Eggs"],
}
RELEVANCE = {
    "local crop":  {"climate": ("high", "A dry or late rainy season shrinks the next harvest"),
                    "weather": ("high", "Grown in Nigeria: rain and heat decide how big the harvest is"),
                    "fuel": ("high", "Bulky and moved long distances by road, so transport is a big share of its price"),
                    "exchange_rate": ("low", "Mostly grown and eaten locally"),
                    "inflation": ("medium", "Moves with general price levels over time")},
    "imported":    {"climate": ("low", "Mostly imported, so the local rainy season matters little"),
                    "weather": ("low", "Mostly imported, so local weather has little effect"),
                    "fuel": ("medium", "Trucked inland from the ports"),
                    "exchange_rate": ("high", "Bought abroad in dollars: a weaker naira raises its price"),
                    "inflation": ("medium", "Moves with general price levels over time")},
    "export crop": {"climate": ("medium", "The rainy season affects yields in the growing areas"),
                    "weather": ("medium", "Weather in the growing areas affects how much reaches the market"),
                    "fuel": ("medium", "Moved by road to buyers and ports"),
                    "exchange_rate": ("high", "Sold abroad in dollars: a weaker naira raises local prices"),
                    "inflation": ("low", "Priced mainly by export demand")},
    "livestock":   {"climate": ("medium", "A poor rainy season means less pasture and dearer feed"),
                    "weather": ("low", "Heat and pasture matter, but less than for crops"),
                    "fuel": ("high", "Animals and products are trucked long distances"),
                    "exchange_rate": ("medium", "Feed and inputs are partly imported"),
                    "inflation": ("high", "Shop prices move closely with general inflation")},
}
GROUP_OF = {c: g for g, cs in GROUPS.items() for c in cs}


def producing_zones(commodity, zones_cfg):
    out = []
    for zone, z in zones_cfg.items():
        primary = z.get("state_primary", {})
        if any(commodity in comms for comms in primary.values()):
            out.append(zone)
    return out


def describe_rain(total, days):
    if days <= 2 and total < 10:
        return "Mostly dry"
    if days >= 8 or total >= 80:
        return "Wet"
    return "Some rain"


def weather_card(commodity, zones, weather, today):
    rel, why = RELEVANCE[GROUP_OF.get(commodity, "local crop")]["weather"]
    if not zones:
        zones = sorted(weather["zone"].unique())
        where = "across Nigeria"
    elif len(zones) <= 2:
        where = "in " + " and ".join(zones)
    else:
        where = f"across its {len(zones)} main growing zones"
    fc = weather[(weather["source"] == "met-norway") & (weather["date"] >= today)
                 & (weather["date"] < today + timedelta(days=WEATHER_DAYS)) & weather["zone"].isin(zones)]
    if fc.empty:
        return {"factor": "weather", "status": "no recent data", "relevance": rel, "why_it_matters": why,
                "reading": "No weather forecast available", "source": "MET Norway"}
    days_covered = fc["date"].nunique()
    per_zone = fc.groupby("zone").agg(rain=("precip_mm", "sum"), rain_days=("precip_mm", lambda s: int((s >= 1).sum())),
                                      tmax=("tmax_c", "mean"), tmax_hi=("tmax_c", "max"))
    rain, rain_days = per_zone["rain"].mean(), per_zone["rain_days"].mean()
    # normal for the same days of the year, NASA POWER 2018 onwards
    hist = weather[(weather["source"] == "nasa-power") & weather["zone"].isin(zones)].copy()
    doy0 = today.timetuple().tm_yday
    hist["doy"] = hist["date"].dt.dayofyear
    win = hist[(hist["doy"] >= doy0) & (hist["doy"] < doy0 + days_covered)]
    normal = win.groupby([win["date"].dt.year, "zone"]).agg(
        rain=("precip_mm", "sum"), rain_days=("precip_mm", lambda s: int((s >= 1).sum()))).groupby(level=0).mean()
    n_rain, n_days = (float(normal["rain"].mean()), float(normal["rain_days"].mean())) if len(normal) else (None, None)
    if n_rain is None:
        vs = None
    elif rain < 0.6 * n_rain and rain_days < n_days:
        vs = "drier than usual"
    elif rain > 1.4 * n_rain and rain_days > n_days:
        vs = "wetter than usual"
    else:
        vs = "about normal"
    issued = fc["date"].min()
    return {
        "factor": "weather", "status": "current",
        "outlook": describe_rain(rain, rain_days), "where": where,
        "reading": (f"{describe_rain(rain, rain_days)} {where}: about {rain_days:.0f} rain days and "
                    f"{rain:.0f} mm over the next {days_covered} days, highs around {per_zone['tmax'].mean():.0f}°C"),
        "vs_normal": None if vs is None else
                     f"{vs} for this time of year (normally {n_days:.0f} rain days, {n_rain:.0f} mm)",
        "details": {"zones": zones, "rain_mm": round(rain, 1), "rain_days": round(rain_days, 1),
                    "normal_rain_mm": None if n_rain is None else round(n_rain, 1),
                    "normal_rain_days": None if n_days is None else round(n_days, 1),
                    "max_temp_c": round(float(per_zone["tmax_hi"].max()), 1), "days": int(days_covered)},
        "relevance": rel, "why_it_matters": why, "price_effect": None,
        "source": "MET Norway forecast (data from MET Norway); normal from NASA POWER 2018 onwards",
        "as_of": str(issued.date()),
    }


def season_card(commodity, master, today):
    sub = master[master["commodity"] == commodity][["date", "price_ngn_mt", "data_source"]]
    idx = seasonal_index(sub)
    m = today.month
    if idx is None:
        return {"factor": "season", "status": "not measured",
                "reading": "No measured seasonal pattern yet (needs 3+ years of real prices covering every month)",
                "relevance": None, "why_it_matters": None, "price_effect": None,
                "source": "Agrolinking real prices", "measured": False}
    ahead = (m + 2) % 12 + 1          # three months ahead
    move = float(np.exp(idx.loc[ahead] - idx.loc[m]) - 1) * 100
    order = idx.sort_values()
    if m in order.index[:3]:
        phase = "usually one of the cheapest times of the year"
    elif m in order.index[-3:]:
        phase = "usually one of the dearest times of the year"
    else:
        phase = "usually close to its average for the year"
    effect = "up" if move >= 2 else ("down" if move <= -2 else "flat")
    cheapest, dearest = MONTHS[int(order.index[0])], MONTHS[int(order.index[-1])]
    return {
        "factor": "season", "status": "current", "measured": True,
        "reading": f"{MONTHS[m]} is {phase}",
        "price_effect": effect,
        "price_effect_text": (f"Prices usually {'rise' if move > 0 else 'fall'} about {abs(move):.0f}% "
                              f"from {MONTHS[m]} to {MONTHS[ahead]}" if effect != "flat"
                              else f"Prices usually change little from {MONTHS[m]} to {MONTHS[ahead]}"),
        "details": {"cheapest_month": cheapest, "dearest_month": dearest,
                    "monthly_pattern_pct": {MONTHS[int(k)][:3]: round(float(np.exp(v) - 1) * 100, 1) for k, v in idx.items()}},
        "relevance": "high", "why_it_matters": "The harvest and lean-season cycle, measured on this commodity's real prices",
        "source": "Agrolinking real prices (seasonal pattern)",
    }


def enso_status(oni):
    v = float(oni)
    strength = "very strong" if abs(v) >= 2 else "strong" if abs(v) >= 1.5 else "moderate" if abs(v) >= 1 else "weak"
    if v >= 0.5:
        return f"{strength} El Niño"
    if v <= -0.5:
        return f"{strength} La Niña"
    return "neutral (no El Niño or La Niña)"


def climate_card(commodity, zones, weather, enso, today):
    rel, why = RELEVANCE[GROUP_OF.get(commodity, "local crop")]["climate"]
    zones = zones or sorted(weather["zone"].unique())
    obs = weather[(weather["source"] == "nasa-power") & weather["zone"].isin(zones)].copy()
    last = obs["date"].max()
    year = last.year if last.month >= 4 else last.year - 1
    start = pd.Timestamp(f"{year}-04-01")
    end = min(last, pd.Timestamp(f"{year}-10-31"))

    def season_total(y):
        s, e = start.replace(year=y), end.replace(year=y)
        w = obs[(obs["date"] >= s) & (obs["date"] <= e)]
        return w.groupby("zone")["precip_mm"].sum().mean() if len(w) else None

    this = season_total(year)
    normals = [t for t in (season_total(y) for y in range(2018, year)) if t is not None]
    reading, details = None, {}
    if this is not None and normals:
        normal = float(np.mean(normals))
        pct = (this / normal - 1) * 100
        word = "below" if pct < 0 else "above"
        reading = (f"This rainy season ({start:%d %b} to {end:%d %b}): {this:.0f} mm, "
                   f"{abs(pct):.0f}% {word} normal in its growing areas")
        details = {"season_rain_mm": round(float(this), 1), "normal_rain_mm": round(normal, 1),
                   "vs_normal_pct": round(pct, 1), "normal_years": f"2018-{year - 1}"}
    e = enso.iloc[-1]
    status = enso_status(e["oni"])
    enso_text = f"Pacific: {status} (NOAA index {e['oni']:+.1f}, {e['season']} {int(e['year'])})"
    note = None
    if "El Niño" in status and "weak" not in status:
        note = ("El Niño years have often brought drier rainy seasons to the Sahel, including northern Nigeria "
                "(general climate link, not measured on our prices)")
    elif "La Niña" in status and "weak" not in status:
        note = ("La Niña years have often brought wetter rainy seasons to the Sahel "
                "(general climate link, not measured on our prices)")
    age = (today - last).days
    return {"factor": "climate", "status": "current" if age <= STALE_DAYS["climate"] else "stale",
            "reading": reading or "Rainy-season total not available", "change": enso_text, "enso_note": note,
            "details": {**details, "enso_oni": float(e["oni"]), "enso_status": status},
            "relevance": rel, "why_it_matters": why, "price_effect": None,
            "source": "NASA POWER rainfall; NOAA Oceanic Niño Index", "as_of": str(last.date())}


def world_card(commodity, world, fx_rate, today):
    if commodity not in WORLD_OF:
        return None
    slug, rel, why = WORLD_OF[commodity]
    w = world[world["mansa_commodity"] == slug].sort_values("as_of_date")
    if w.empty:
        return {"factor": "world_price", "status": "no recent data", "relevance": rel, "why_it_matters": why,
                "reading": "No world benchmark price yet", "source": "Mansa Markets global benchmark"}
    cur = w.iloc[-1]
    ago = w[w["as_of_date"] <= cur["as_of_date"] - timedelta(days=28)]
    if len(ago):
        pct = (cur["usd_per_tonne"] / ago["usd_per_tonne"].iloc[-1] - 1) * 100
        change = f"{'Up' if pct > 0 else 'Down'} {abs(pct):.0f}% in a month" if abs(pct) >= 1 else "Steady over a month"
        fdir = direction(pct)
    else:
        change, fdir = "Change shown once we have a month of daily records", None
    age = (today - cur["as_of_date"]).days
    return {"factor": "world_price", "status": "current" if age <= STALE_DAYS["world_price"] else "stale",
            "reading": (f"World price ${cur['usd_per_tonne']:,.0f}/tonne, about "
                        f"N{cur['usd_per_tonne'] * fx_rate:,.0f}/tonne at the official rate"),
            "change": change, "factor_direction": fdir, "relevance": rel, "why_it_matters": why,
            "price_effect": None, "source": "Mansa Markets global benchmark (monthly)",
            "as_of": str(cur["as_of_date"].date())}


def series_card(factor, commodity, value_text, change_text, direction, as_of, source, today):
    rel, why = RELEVANCE[GROUP_OF.get(commodity, "local crop")][factor]
    age = (today - pd.Timestamp(as_of)).days
    return {"factor": factor, "status": "current" if age <= STALE_DAYS[factor] else "stale",
            "reading": value_text, "change": change_text, "factor_direction": direction,
            "relevance": rel, "why_it_matters": why, "price_effect": None,
            "source": source, "as_of": str(pd.Timestamp(as_of).date())}


def direction(pct, tol=1.0):
    return "up" if pct > tol else ("down" if pct < -tol else "steady")


def macro_readings(today):
    out = {}
    fx = pd.read_csv(os.path.join(EXT, "cbn_usd_daily.csv"), parse_dates=["date"]).sort_values("date")
    last = fx.iloc[-1]
    before = fx[fx["date"] <= last["date"] - timedelta(days=30)]
    pct = (last["ngn_per_usd"] / before["ngn_per_usd"].iloc[-1] - 1) * 100 if len(before) else 0.0
    out["exchange_rate"] = (f"N{last['ngn_per_usd']:,.0f} per US$ (official rate)",
                            f"{'Weaker' if pct > 0 else 'Stronger'} naira: {abs(pct):.1f}% in 30 days" if abs(pct) >= 0.5
                            else "Steady over the last 30 days",
                            direction(pct), last["date"], "Central Bank of Nigeria")
    inf = pd.read_csv(os.path.join(EXT, "cbn_inflation_monthly.csv"), parse_dates=["period", "published"]).sort_values("period")
    cur, prev = inf.iloc[-1], inf.iloc[-2]
    d = cur["food_yoy"] - prev["food_yoy"]
    out["inflation"] = (f"Food inflation {cur['food_yoy']:.1f}% a year ({cur['period']:%B %Y}); headline {cur['headline_yoy']:.1f}%",
                        f"{'Up' if d > 0 else 'Down'} {abs(d):.1f} points on the month" if abs(d) >= 0.1 else "Unchanged on the month",
                        direction(d, 0.2), cur["published"], "Central Bank of Nigeria (NBS figures)")
    pet = pd.read_csv(os.path.join(EXT, "wfp_petrol_monthly.csv"), parse_dates=["date"]).sort_values("date")
    cur, ago = pet.iloc[-1], pet[pet["date"] <= pet["date"].iloc[-1] - timedelta(days=85)]
    pct = (cur["petrol_ngn_litre"] / ago["petrol_ngn_litre"].iloc[-1] - 1) * 100 if len(ago) else 0.0
    out["fuel"] = (f"Petrol N{cur['petrol_ngn_litre']:,.0f}/litre ({cur['date']:%B %Y}, {int(cur['markets'])} markets)",
                   f"{'Up' if pct > 0 else 'Down'} {abs(pct):.0f}% in 3 months" if abs(pct) >= 1 else "Steady over 3 months",
                   direction(pct), cur["date"], "WFP market prices (North-East markets)")
    return out


def summary_line(cards):
    parts = []
    s = cards.get("season", {})
    if s.get("measured"):
        parts.append(s["price_effect_text"] + ".")
    w = cards.get("weather", {})
    if w.get("status") == "current" and w.get("vs_normal"):
        parts.append(f"Next {w['details']['days']} days: {w['outlook'].lower()} {w['where']}, "
                     f"{w['vs_normal'].split(' for ')[0]}.")
    cl = cards.get("climate", {})
    pct = cl.get("details", {}).get("vs_normal_pct")
    if cl.get("relevance") in ("high", "medium") and pct is not None and abs(pct) >= 10:
        parts.append(f"Rainy season {abs(pct):.0f}% {'below' if pct < 0 else 'above'} normal.")
    for f, label in (("world_price", "World price"), ("fuel", "Fuel"), ("exchange_rate", "The naira"),
                     ("inflation", "Food inflation")):
        c = cards.get(f, {})
        if c.get("relevance") == "high" and c.get("status") == "current" and c.get("factor_direction"):
            parts.append(f"{label}: {c['change'][0].lower() + c['change'][1:]}.")
    return " ".join(parts)


def build(today=None):
    today = pd.Timestamp(today or datetime.now().date())
    weather = pd.read_csv(os.path.join(EXT, "weather_daily.csv"), parse_dates=["date"])
    zones_cfg = json.load(open(os.path.join(EXT, "zones_config.json")))
    master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    master = master[master["data_source"].isin(REAL_SOURCES)]
    macro = macro_readings(today)
    enso = pd.read_csv(os.path.join(EXT, "enso_oni.csv"))
    world_path = os.path.join(BASE, "data", "raw", "world_prices.csv")
    world = (pd.read_csv(world_path, parse_dates=["as_of_date"]) if os.path.exists(world_path)
             else pd.DataFrame(columns=["as_of_date", "mansa_commodity", "usd_per_tonne"]))
    fx_rate = pd.read_csv(os.path.join(EXT, "cbn_usd_daily.csv"))["ngn_per_usd"].iloc[-1]
    out = {"as_of": str(today.date()), "commodities": {}}
    for c in COMMODITIES:
        zones = [] if GROUP_OF.get(c) in ("imported", "livestock") else producing_zones(c, zones_cfg)
        cards = {"weather": weather_card(c, zones, weather, today), "season": season_card(c, master, today),
                 "climate": climate_card(c, zones, weather, enso, today)}
        wc = world_card(c, world, fx_rate, today)
        if wc:
            cards["world_price"] = wc
        for f, args in macro.items():
            cards[f] = series_card(f, c, *args, today=today)
        out["commodities"][c] = {"group": GROUP_OF.get(c), "factors": cards, "summary": summary_line(cards)}
    return out


def run():
    logger.info("=" * 60)
    logger.info("PRICE DRIVERS - weather, season, fuel, exchange rate, inflation per commodity")
    logger.info("=" * 60)
    try:
        rep = build()
    except Exception as e:
        logger.warning(f"  Price drivers could not be built this run: {e}")
        return True
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"drivers_{rep['as_of']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False, default=str)
    for c, e in rep["commodities"].items():
        logger.info(f"  {c:<15} {e['summary'][:110]}")
    logger.success(f"PRICE DRIVERS DONE -> {os.path.relpath(path, BASE)}")
    return True


if __name__ == "__main__":
    run()
    sys.exit(0)
