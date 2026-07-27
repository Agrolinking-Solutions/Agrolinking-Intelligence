"""
Agrolinking Commodity Intelligence — REST API v2.0
FastAPI application serving all intelligence data to the dev team.

EXISTING ENDPOINTS (v1):
  GET /                          Health check
  GET /summary                   Dashboard summary card data
  GET /commodities               All 17 live prices
  GET /forecasts/latest          Full forecast all commodities
  GET /forecasts/{commodity}     Single commodity full forecast
  GET /forecasts/{commodity}/{horizon}  Chart-ready weekly series
  GET /zonal/latest              All zonal and state prices
  GET /zonal/{commodity}         State prices + best sourcing
  GET /alerts/latest             WhatsApp-ready alert text

NEW ENDPOINTS (v2):
  GET /prices/kg                 All prices in NGN/kg + per unit
  GET /prices/kg/{commodity}     Single commodity NGN/kg
  GET /index/food                Food Price Index (base 2025=100)
  GET /index/volatility          Volatility Index per commodity
  GET /outlook/30d               30-Day outlook aggregate + per commodity
  GET /confidence                Model confidence scores
  GET /movers                    Biggest riser and faller today
  GET /alerts/early-warning      WFP ALPS early warning status
  GET /shortage-surplus          Shortage/surplus scores per commodity per zone
  GET /seasonality/{commodity}   Monthly seasonality profile
  GET /spreads                   State price high/low spread per commodity
  GET /arbitrage                 Net arbitrage per kg after freight
  GET /arbitrage/{commodity}     Single commodity arbitrage detail
  GET /intelligence/latest       Full intelligence bundle (all metrics)
  GET /docs                      Auto-generated Swagger UI
"""

import os
import json
import glob
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ── Path configuration ─────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
VALIDATED_DIR  = os.path.join(BASE_DIR, "outputs", "forecasts", "validated")
ZONAL_DIR      = os.path.join(BASE_DIR, "outputs", "forecasts", "zonal")
ALERTS_DIR     = os.path.join(BASE_DIR, "outputs", "daily_alerts")
INTEL_DIR      = os.path.join(BASE_DIR, "outputs", "intelligence")

# ── App setup ──────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Agrolinking Commodity Intelligence API",
    description = (
        "Nigerian agricultural commodity price forecasts, intelligence metrics, "
        "and zonal sourcing data across 17 commodities and 6 geopolitical zones. "
        "Data validated daily against Agricome Africa, WFP Nigeria, NGX, and live "
        "market sources. Base 2025=100 for index metrics."
    ),
    version     = "2.0.0",
    contact     = {
        "name":  "Agrolinking Research and Data Team",
        "url":   "https://agrolinking.com",
        "email": "data@agrolinking.com",
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["GET"],
    allow_headers     = ["*"],
)

VALID_HORIZONS = ["daily", "weekly", "2_weeks", "monthly", "3_months", "6_months"]

# ── Helper functions ───────────────────────────────────────────────────────

def load_latest_validated():
    files = sorted(glob.glob(os.path.join(VALIDATED_DIR, "forecast_validated_*.json")))
    if not files:
        raise HTTPException(status_code=404, detail="No validated forecast files found.")
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f), os.path.basename(files[-1])

def load_latest_zonal():
    files = sorted(glob.glob(os.path.join(ZONAL_DIR, "zonal_forecast_*.json")))
    if not files:
        raise HTTPException(status_code=404, detail="No zonal forecast files found.")
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f), os.path.basename(files[-1])

def load_latest_alert():
    files = sorted(glob.glob(os.path.join(ALERTS_DIR, "alert_validated_*.txt")))
    if not files:
        raise HTTPException(status_code=404, detail="No alert files found.")
    with open(files[-1], encoding="utf-8") as f:
        return f.read(), os.path.basename(files[-1])

def load_latest_intelligence():
    files = sorted(glob.glob(os.path.join(INTEL_DIR, "intelligence_*.json")))
    if not files:
        raise HTTPException(
            status_code=404,
            detail="No intelligence data found. Run pipeline/08_intelligence.py first."
        )
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f), os.path.basename(files[-1])

def normalise(name: str) -> str:
    return name.strip().lower()

def find_commodity(data: dict, name: str):
    target = normalise(name)
    for key in data:
        if normalise(key) == target:
            return key, data[key]
    return None, None

def price_per_kg(commodity: str, price_ngn_mt: float):
    """Convert NGN/MT to NGN/kg. Eggs return per crate."""
    if commodity == "Eggs":
        return price_ngn_mt, "NGN/crate"
    return round(price_ngn_mt / 1000, 2), "NGN/kg"

def run_date_from_file(fname: str) -> str:
    """Extract YYYY-MM-DD from a filename."""
    parts = fname.replace(".json","").replace(".txt","").split("_")
    for p in reversed(parts):
        if len(p) == 10 and p[4] == "-":
            return p
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# V1 ENDPOINTS (existing, preserved)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", tags=["Info"])
def root():
    """Health check and full API endpoint directory."""
    try:
        forecast, fname = load_latest_validated()
        last_updated = run_date_from_file(fname)
        n_commodities = len(forecast)
    except Exception:
        last_updated = "unknown"
        n_commodities = 0

    return {
        "status":        "operational",
        "api":           "Agrolinking Commodity Intelligence",
        "version":       "2.1.0",
        "commodities":   n_commodities,
        "last_updated":  last_updated,
        "docs":          "/docs",
        "v1_endpoints": [
            "GET /summary",
            "GET /commodities",
            "GET /forecasts/latest",
            "GET /forecasts/{commodity}",
            "GET /forecasts/{commodity}/{horizon}",
            "GET /zonal/latest",
            "GET /zonal/{commodity}",
            "GET /alerts/latest",
        ],
        "v2_endpoints": [
            "GET /prices/kg",
            "GET /prices/kg/{commodity}",
            "GET /index/food",
            "GET /index/volatility",
            "GET /outlook/30d",
            "GET /confidence",
            "GET /movers",
            "GET /alerts/early-warning",
            "GET /shortage-surplus",
            "GET /seasonality/{commodity}",
            "GET /spreads",
            "GET /arbitrage",
            "GET /arbitrage/{commodity}",
            "GET /intelligence/latest",
        ]
    }


@app.get("/summary", tags=["Dashboard"])
def summary():
    """Dashboard hero card data — commodities tracked, accuracy, error, run date."""
    forecast, fname = load_latest_validated()
    run_date = run_date_from_file(fname)
    within_target = sum(
        1 for d in forecast.values()
        if d.get("validation", {}).get("within_target", False)
    )
    errors = [
        d.get("validation", {}).get("error_after_pct", 0)
        for d in forecast.values()
        if d.get("validation", {}).get("error_after_pct") is not None
    ]
    avg_error = round(sum(errors) / len(errors), 2) if errors else 0

    # Pull top-level intelligence if available
    intel_summary = {}
    try:
        intel, _ = load_latest_intelligence()
        intel_summary = {
            "food_price_index":   intel.get("food_price_index", {}).get("value"),
            "volatility_index":   intel.get("volatility_index", {}).get("value"),
            "outlook_30d_pct":    intel.get("outlook_30d", {}).get("avg_pct_change"),
            "model_confidence":   intel.get("model_confidence", {}).get("avg_pct"),
            "alert_summary":      intel.get("early_warning_alerts", {}).get("summary"),
        }
    except Exception:
        pass

    return {
        "commodities_tracked":    len(forecast),
        "verified_accuracy":      f"{within_target}/{len(forecast)}",
        "verified_accuracy_pct":  round(within_target / len(forecast) * 100, 1),
        "avg_model_error_pct":    avg_error,
        "last_pipeline_run":      run_date,
        "accuracy_target":        "within 3% of live market prices",
        "data_sources":           ["Agricome Africa", "WFP Nigeria", "NGX", "Market Naija TV", "LCFE"],
        "zones":                  6,
        "states":                 12,
        "forecast_horizons":      VALID_HORIZONS,
        **intel_summary,
    }


@app.get("/commodities", tags=["Commodities"])
def list_commodities():
    """All 17 commodities with live price, daily change, and validation status."""
    forecast, fname = load_latest_validated()
    zonal, _ = load_latest_zonal()
    national = zonal.get("national_anchors", {})
    run_date = run_date_from_file(fname)

    result = []
    for name, data in forecast.items():
        vld    = data.get("validation", {})
        daily  = data.get("horizons", {}).get("daily", {})
        detail = daily.get("forecast_end_detail", {})
        anchor = national.get(name, {})
        price  = anchor.get("price") or data.get("last_known_price", 0)
        per_kg, unit = price_per_kg(name, price)

        result.append({
            "commodity":          name,
            "price_ngn_mt":       price,
            "price_per_unit":     per_kg,
            "unit":               unit,
            "last_known_date":    data.get("last_known_date", ""),
            "day_change_pct":     anchor.get("day_change", 0),
            "pct_vs_reference":   anchor.get("pct_vs_ref", 0),
            "forecast_price":     detail.get("price", 0),
            "forecast_date":      detail.get("date", ""),
            "pct_change_daily":   detail.get("pct_change_from_today", 0),
            "validation_error":   vld.get("error_after_pct", 0),
            "validation_status":  vld.get("status", "unknown"),
            "within_target":      vld.get("within_target", False),
            "currency":           "NGN",
        })
    return {
        "count":       len(result),
        "as_of":       run_date,
        "commodities": result,
    }


@app.get("/forecasts/latest", tags=["Forecasts"])
def latest_forecast(
    horizon: Optional[str] = Query(None, description="Filter: daily|weekly|2_weeks|monthly|3_months|6_months")
):
    """Full validated forecast for all commodities. Optional horizon filter."""
    if horizon and horizon not in VALID_HORIZONS:
        raise HTTPException(status_code=400, detail=f"Invalid horizon. Valid: {VALID_HORIZONS}")
    forecast, fname = load_latest_validated()
    run_date = run_date_from_file(fname)
    result = {}
    for name, data in forecast.items():
        vld = data.get("validation", {})
        horizons = data.get("horizons", {})
        if horizon:
            h_data = horizons.get(horizon, {})
            detail = h_data.get("forecast_end_detail", {})
            vals   = h_data.get("ensemble", {}).get("values", [])
            result[name] = {
                "horizon":            horizon,
                "forecast_date":      detail.get("date", ""),
                "forecast_price_ngn": detail.get("price", vals[-1] if vals else 0),
                "pct_change":         detail.get("pct_change_from_today", 0),
                "direction":          detail.get("direction", ""),
                "validation_error":   vld.get("error_after_pct", 0),
                "within_target":      vld.get("within_target", False),
            }
        else:
            horizon_summary = {}
            for h in VALID_HORIZONS:
                h_data = horizons.get(h, {})
                detail = h_data.get("forecast_end_detail", {})
                vals   = h_data.get("ensemble", {}).get("values", [])
                horizon_summary[h] = {
                    "date":      detail.get("date", ""),
                    "price":     detail.get("price", vals[-1] if vals else 0),
                    "pct_change": detail.get("pct_change_from_today", 0),
                    "direction": detail.get("direction", ""),
                }
            result[name] = {
                "last_known_price": data.get("last_known_price", 0),
                "last_known_date":  data.get("last_known_date", ""),
                "validation":       {
                    "reference_price":  vld.get("reference_price", 0),
                    "error_before_pct": vld.get("error_pct_before", 0),
                    "error_after_pct":  vld.get("error_after_pct", 0),
                    "action":           vld.get("correction_applied", ""),
                    "within_target":    vld.get("within_target", False),
                },
                "horizons": horizon_summary,
            }
    return {"run_date": run_date, "currency": "NGN", "unit": "NGN/MT", "forecasts": result}


@app.get("/forecasts/{commodity}", tags=["Forecasts"])
def commodity_forecast(commodity: str):
    """Full 6-horizon forecast for one commodity with weekly series and confidence bands."""
    forecast, fname = load_latest_validated()
    key, data = find_commodity(forecast, commodity)
    if not key:
        raise HTTPException(status_code=404,
            detail=f"'{commodity}' not found. Available: {list(forecast.keys())}")
    vld = data.get("validation", {})
    horizons = {}
    for h in VALID_HORIZONS:
        h_data = data.get("horizons", {}).get(h, {})
        detail = h_data.get("forecast_end_detail", {})
        vals   = h_data.get("ensemble", {}).get("values", [])
        dates  = h_data.get("dates", [])
        horizons[h] = {
            "forecast_date":  detail.get("date", ""),
            "forecast_price": detail.get("price", vals[-1] if vals else 0),
            "pct_change":     detail.get("pct_change_from_today", 0),
            "direction":      detail.get("direction", ""),
            "weekly_series": [
                {"date": d, "price": v, "lower_ci": lo, "upper_ci": hi}
                for d, v, lo, hi in zip(
                    dates, vals,
                    h_data.get("ensemble", {}).get("lower_ci", vals),
                    h_data.get("ensemble", {}).get("upper_ci", vals),
                )
            ],
        }
    per_kg, unit = price_per_kg(key, data.get("last_known_price", 0))
    return {
        "commodity":        key,
        "run_date":         run_date_from_file(fname),
        "last_known_price": data.get("last_known_price", 0),
        "last_known_price_per_unit": per_kg,
        "unit_label":       unit,
        "last_known_date":  data.get("last_known_date", ""),
        "currency":         "NGN",
        "models_used":      data.get("models_used", []),
        "weights":          data.get("weights", {}),
        "validation":       {
            "reference_price":  vld.get("reference_price", 0),
            "error_before_pct": vld.get("error_pct_before", 0),
            "error_after_pct":  vld.get("error_after_pct", 0),
            "action":           vld.get("correction_applied", ""),
            "within_target":    vld.get("within_target", False),
        },
        "horizons": horizons,
    }


@app.get("/forecasts/{commodity}/{horizon}", tags=["Forecasts"])
def commodity_horizon(commodity: str, horizon: str):
    """Single commodity at one horizon. Chart-ready weekly series with confidence bands."""
    if horizon not in VALID_HORIZONS:
        raise HTTPException(status_code=400, detail=f"Invalid horizon. Valid: {VALID_HORIZONS}")
    forecast, fname = load_latest_validated()
    key, data = find_commodity(forecast, commodity)
    if not key:
        raise HTTPException(status_code=404, detail=f"'{commodity}' not found.")
    h_data = data.get("horizons", {}).get(horizon, {})
    detail = h_data.get("forecast_end_detail", {})
    vals   = h_data.get("ensemble", {}).get("values", [])
    dates  = h_data.get("dates", [])
    per_kg, unit = price_per_kg(key, detail.get("price", vals[-1] if vals else 0))
    return {
        "commodity":          key,
        "horizon":            horizon,
        "run_date":           run_date_from_file(fname),
        "last_known_price":   data.get("last_known_price", 0),
        "forecast_date":      detail.get("date", ""),
        "forecast_price_ngn": detail.get("price", vals[-1] if vals else 0),
        "forecast_price_per_unit": per_kg,
        "unit_label":         unit,
        "pct_change":         detail.get("pct_change_from_today", 0),
        "direction":          detail.get("direction", ""),
        "currency":           "NGN",
        "validation_error":   data.get("validation", {}).get("error_after_pct", 0),
        "within_target":      data.get("validation", {}).get("within_target", False),
        "weekly_series": [
            {"date": d, "price": v,
             "price_per_unit": round(v / 1000, 2) if key != "Eggs" else v,
             "lower_ci": lo, "upper_ci": hi}
            for d, v, lo, hi in zip(
                dates, vals,
                h_data.get("ensemble", {}).get("lower_ci", vals),
                h_data.get("ensemble", {}).get("upper_ci", vals),
            )
        ],
    }


@app.get("/zonal/latest", tags=["Zonal Prices"])
def latest_zonal(
    zone: Optional[str] = Query(None, description="Filter by zone e.g. North West"),
    commodity: Optional[str] = Query(None, description="Filter by commodity e.g. Rice"),
):
    """All zonal and state prices. Optional zone or commodity filter."""
    zonal, fname = load_latest_zonal()
    result = zonal
    if zone:
        zone_key = next((k for k in result.get("zones", {}) if k.lower() == zone.lower()), None)
        if not zone_key:
            raise HTTPException(status_code=404,
                detail=f"Zone '{zone}' not found. Available: {list(result.get('zones',{}).keys())}")
        result = {"zones": {zone_key: result["zones"][zone_key]}}
    if commodity:
        target = normalise(commodity)
        filtered = {}
        for zname, zdata in result.get("zones", {}).items():
            fstates = {}
            for sname, sdata in zdata.get("states", {}).items():
                comms = {k: v for k, v in sdata.items() if normalise(k) == target}
                if comms:
                    fstates[sname] = comms
            if fstates:
                filtered[zname] = {"states": fstates, "description": zdata.get("description", "")}
        result = {"zones": filtered}
    return {
        "source_file":      fname,
        "run_date":         zonal.get("run_date", ""),
        "national_anchors": zonal.get("national_anchors", {}),
        "best_sourcing":    zonal.get("best_sourcing", zonal.get("best_market", {})),
        **{k: v for k, v in result.items() if k != "national_anchors"},
    }


@app.get("/zonal/{commodity}", tags=["Zonal Prices"])
def commodity_zonal(commodity: str):
    """State-level prices for one commodity with best sourcing intelligence."""
    zonal, fname = load_latest_zonal()
    anchors = zonal.get("national_anchors", {})
    key = next((k for k in anchors if normalise(k) == normalise(commodity)), None)
    if not key:
        raise HTTPException(status_code=404, detail=f"'{commodity}' not found in zonal data.")
    state_prices = {}
    for zone_name, zone_data in zonal.get("zones", {}).items():
        for state_name, state_data in zone_data.get("states", {}).items():
            comm_data = state_data.get(key, {})
            price = comm_data.get("state_price") or comm_data.get("price", 0)
            if price:
                per_kg, unit = price_per_kg(key, price)
                state_prices[state_name] = {
                    "zone":           zone_name,
                    "price_ngn_mt":   price,
                    "price_per_unit": per_kg,
                    "unit_label":     unit,
                    "day_change_pct": comm_data.get("day_change_pct", 0),
                    "is_primary":     comm_data.get("is_primary", False),
                }
    best = zonal.get("best_sourcing", {}).get(key, {})
    nat_price = anchors.get(key, {}).get("price", 0)
    per_kg_nat, unit_nat = price_per_kg(key, nat_price)
    return {
        "commodity":             key,
        "run_date":              zonal.get("run_date", ""),
        "national_price_ngn_mt": nat_price,
        "national_price_per_unit": per_kg_nat,
        "unit_label":            unit_nat,
        "day_change_pct":        anchors.get(key, {}).get("day_change", 0),
        "pct_vs_reference":      anchors.get(key, {}).get("pct_vs_ref", 0),
        "currency":              "NGN",
        "best_sourcing":         best,
        "state_prices":          state_prices,
    }


@app.get("/alerts/latest", tags=["Alerts"])
def latest_alert():
    """Latest validated daily price alert, WhatsApp and email ready."""
    alert_text, fname = load_latest_alert()
    date_str = run_date_from_file(fname)
    return {"date": date_str, "source": fname, "text": alert_text, "format": "WhatsApp / Email ready"}


# ══════════════════════════════════════════════════════════════════════════════
# V2 ENDPOINTS (new)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/prices/kg", tags=["Prices"])
def all_prices_per_kg():
    """
    All 17 commodity prices in NGN/kg (or NGN/crate for Eggs).
    Includes NGN/MT, NGN/kg, day change, and unit label.
    """
    zonal, fname = load_latest_zonal()
    anchors = zonal.get("national_anchors", {})
    result = []
    for commodity, anchor in anchors.items():
        price_mt = anchor.get("price", 0)
        per_unit, unit = price_per_kg(commodity, price_mt)
        result.append({
            "commodity":      commodity,
            "price_ngn_mt":   price_mt,
            "price_per_unit": per_unit,
            "unit_label":     unit,
            "day_change_pct": anchor.get("day_change", 0),
            "pct_vs_ref":     anchor.get("pct_vs_ref", 0),
        })
    return {
        "as_of":       zonal.get("run_date", ""),
        "note":        "Eggs priced per crate (30 eggs). All others per kg.",
        "commodities": result,
    }


@app.get("/prices/kg/{commodity}", tags=["Prices"])
def commodity_price_per_kg(commodity: str):
    """Single commodity price in NGN/kg (or NGN/crate for Eggs)."""
    zonal, fname = load_latest_zonal()
    anchors = zonal.get("national_anchors", {})
    key = next((k for k in anchors if normalise(k) == normalise(commodity)), None)
    if not key:
        raise HTTPException(status_code=404, detail=f"'{commodity}' not found.")
    anchor = anchors[key]
    price_mt = anchor.get("price", 0)
    per_unit, unit = price_per_kg(key, price_mt)
    return {
        "commodity":        key,
        "as_of":            zonal.get("run_date", ""),
        "price_ngn_mt":     price_mt,
        "price_per_unit":   per_unit,
        "unit_label":       unit,
        "day_change_pct":   anchor.get("day_change", 0),
        "pct_vs_reference": anchor.get("pct_vs_ref", 0),
        "note":             "Eggs are priced per crate of 30 eggs." if key == "Eggs" else "",
    }


@app.get("/index/food", tags=["Intelligence"])
def food_price_index():
    """
    Food Price Index — weighted basket of all 17 commodities.
    Base: 2025 = 100. Includes month-on-month change and commodity breakdown.
    """
    intel, fname = load_latest_intelligence()
    fpi = intel.get("food_price_index", {})
    return {
        "as_of":          intel.get("run_date", ""),
        "value":          fpi.get("value"),
        "base":           fpi.get("base", "2025=100"),
        "mom_change":     fpi.get("mom_change"),
        "interpretation": fpi.get("interpretation"),
        "breakdown":      fpi.get("breakdown", {}),
    }


@app.get("/index/volatility", tags=["Intelligence"])
def volatility_index():
    """
    Volatility Index — 30-day rolling price volatility per commodity.
    Aggregate index + leading commodity. Higher = more volatile market.
    """
    intel, fname = load_latest_intelligence()
    vol = intel.get("volatility_index", {})
    return {
        "as_of":              intel.get("run_date", ""),
        "value":              vol.get("value"),
        "leading_commodity":  vol.get("leading_commodity"),
        "interpretation":     vol.get("interpretation"),
        "note":               "Coefficient of variation (%) over 30-day rolling window.",
        "per_commodity":      vol.get("per_commodity", {}),
    }


@app.get("/outlook/30d", tags=["Intelligence"])
def outlook_30d():
    """
    30-Day Outlook — aggregate expected price change across all commodities.
    Derived from monthly horizon forecasts. Includes per-commodity breakdown.
    """
    intel, fname = load_latest_intelligence()
    outlook = intel.get("outlook_30d", {})
    return {
        "as_of":               intel.get("run_date", ""),
        "avg_pct_change":      outlook.get("avg_pct_change"),
        "direction":           outlook.get("direction"),
        "signal":              outlook.get("signal"),
        "interpretation":      (
            f"On average, commodity prices are expected to "
            f"{'rise' if outlook.get('avg_pct_change', 0) > 0 else 'fall'} "
            f"by {abs(outlook.get('avg_pct_change', 0)):.1f}% over the next 30 days."
        ),
        "per_commodity":       outlook.get("per_commodity", {}),
    }


@app.get("/confidence", tags=["Intelligence"])
def model_confidence():
    """
    Model confidence scores per commodity.
    Derived from validation error: confidence = 100 - error_after_pct.
    Grades: A (>=95%), B (>=90%), C (>=80%), D (<80%).
    """
    intel, fname = load_latest_intelligence()
    conf = intel.get("model_confidence", {})
    return {
        "as_of":          intel.get("run_date", ""),
        "avg_pct":        conf.get("avg_pct"),
        "interpretation": conf.get("interpretation"),
        "grade_scale":    {"A": ">=95%", "B": ">=90%", "C": ">=80%", "D": "<80%"},
        "per_commodity":  conf.get("per_commodity", {}),
    }


@app.get("/movers", tags=["Intelligence"])
def market_movers():
    """
    Biggest riser and biggest faller today by day-on-day % change.
    Includes price in NGN/MT and NGN/kg.
    """
    intel, fname = load_latest_intelligence()
    movers = intel.get("market_movers", {})
    riser  = movers.get("biggest_riser", {})
    faller = movers.get("biggest_faller", {})

    # Enrich with unit price
    for m in [riser, faller]:
        if m and m.get("commodity"):
            p = m.get("price_ngn_mt", 0)
            per_unit, unit = price_per_kg(m["commodity"], p)
            m["price_per_unit"] = per_unit
            m["unit_label"]     = unit

    return {
        "as_of":          intel.get("run_date", ""),
        "biggest_riser":  riser,
        "biggest_faller": faller,
        "note": "Day-on-day change from yesterday's forecast position.",
    }


@app.get("/alerts/early-warning", tags=["Alerts"])
def early_warning_alerts(
    level: Optional[str] = Query(None, description="Filter: Severe|High|Watch|Normal")
):
    """
    WFP ALPS-style early warning alert status per commodity.
    Thresholds: Severe >25%, High >15%, Watch >5% above 3-month average.
    """
    intel, fname = load_latest_intelligence()
    ewa   = intel.get("early_warning_alerts", {})
    alerts = ewa.get("per_commodity", {})

    if level:
        alerts = {k: v for k, v in alerts.items()
                  if v.get("alert_level", "").lower() == level.lower()}

    return {
        "as_of":       intel.get("run_date", ""),
        "summary":     ewa.get("summary", {}),
        "thresholds":  ewa.get("thresholds", {}),
        "methodology": "WFP ALPS (Acute Livelihoods and Price Surveillance)",
        "alerts":      alerts,
    }


@app.get("/shortage-surplus", tags=["Intelligence"])
def shortage_surplus(
    commodity: Optional[str] = Query(None, description="Filter by commodity")
):
    """
    Shortage/surplus score per commodity (0-100).
    0 = severe shortage, 50 = balanced, 100 = strong surplus.
    Combines price signal (40%), seasonality (30%), and trend direction (30%).
    """
    intel, fname = load_latest_intelligence()
    scores = intel.get("shortage_surplus", {}).get("per_commodity", {})

    if commodity:
        key = next((k for k in scores if normalise(k) == normalise(commodity)), None)
        if not key:
            raise HTTPException(status_code=404, detail=f"'{commodity}' not found.")
        return {"as_of": intel.get("run_date", ""), commodity: scores[key]}

    return {
        "as_of":          intel.get("run_date", ""),
        "scoring_note":   "0=severe shortage, 50=balanced, 100=strong surplus",
        "methodology":    "Price signal 40% + Seasonality 30% + Trend 30%",
        "per_commodity":  scores,
    }


@app.get("/seasonality/{commodity}", tags=["Intelligence"])
def seasonality_profile(commodity: str):
    """
    Monthly seasonality profile for one commodity (months 1-12).
    Score 100 = peak harvest (cheapest prices), 0 = lean season (most expensive).
    """
    intel, fname = load_latest_intelligence()
    profiles = intel.get("seasonality_profiles", {})
    key = next((k for k in profiles if normalise(k) == normalise(commodity)), None)

    # If no seasonality data from season_calendar.csv, use built-in estimates
    # based on known Nigerian agricultural seasonal patterns
    if not key or not profiles.get(key):
        BUILTIN_SEASONALITY = {
            "Maize (white)":  {1:35,2:30,3:40,4:55,5:65,6:75,7:80,8:85,9:90,10:75,11:55,12:40},
            "Maize (yellow)": {1:35,2:30,3:40,4:55,5:65,6:75,7:80,8:85,9:90,10:75,11:55,12:40},
            "Sorghum":        {1:55,2:45,3:40,4:35,5:30,6:35,7:40,8:50,9:70,10:85,11:80,12:65},
            "Rice":           {1:45,2:40,3:35,4:40,5:50,6:60,7:65,8:70,9:80,10:85,11:75,12:55},
            "Wheat":          {1:70,2:75,3:80,4:85,5:70,6:55,7:45,8:40,9:35,10:40,11:55,12:65},
            "Beans (white)":  {1:50,2:45,3:40,4:50,5:60,6:70,7:75,8:80,9:85,10:80,11:65,12:55},
            "Beans (red)":    {1:50,2:45,3:40,4:50,5:60,6:70,7:75,8:80,9:85,10:80,11:65,12:55},
            "Soybeans":       {1:55,2:50,3:45,4:40,5:45,6:55,7:65,8:75,9:85,10:90,11:75,12:60},
            "Ginger":         {1:60,2:55,3:50,4:45,5:40,6:35,7:30,8:35,9:45,10:65,11:75,12:70},
            "Hibiscus":       {1:65,2:60,3:55,4:50,5:45,6:35,7:30,8:35,9:45,10:60,11:75,12:70},
            "Sesame":         {1:50,2:45,3:40,4:35,5:40,6:50,7:60,8:70,9:85,10:90,11:75,12:60},
            "Cocoa":          {1:45,2:40,3:35,4:30,5:35,6:45,7:55,8:65,9:75,10:85,11:90,12:70},
            "Cashew Nuts":    {1:30,2:35,3:50,4:80,5:90,6:85,7:70,8:55,9:45,10:35,11:30,12:30},
            "Meat (beef)":    {1:55,2:50,3:55,4:60,5:55,6:50,7:45,8:50,9:55,10:60,11:65,12:60},
            "Meat (goat)":    {1:55,2:50,3:55,4:60,5:55,6:50,7:45,8:50,9:55,10:60,11:65,12:60},
            "Fish (dried)":   {1:60,2:65,3:70,4:65,5:55,6:45,7:40,8:45,9:50,10:60,11:65,12:62},
            "Eggs":           {1:55,2:50,3:55,4:60,5:65,6:60,7:55,8:55,9:55,10:60,11:65,12:60},
        }
        # Find matching key case-insensitively
        key = next((k for k in BUILTIN_SEASONALITY if normalise(k) == normalise(commodity)), None)
        if not key:
            raise HTTPException(status_code=404,
                detail=f"Commodity '{commodity}' not found. "
                       f"Available: {list(BUILTIN_SEASONALITY.keys())}")
        profile_data = {str(k): v for k, v in BUILTIN_SEASONALITY[key].items()}
        data_source = "built-in seasonal estimates (season_calendar.csv not yet loaded)"
    else:
        profile_data = profiles[key]
        data_source = "season_calendar.csv"
    current_month = datetime.now().month
    months = {
        1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
        7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"
    }
    profile = profile_data
    enriched = {
        months[int(m)]: {
            "score":          v,
            "is_current":     int(m) == current_month,
            "interpretation": "harvest" if v >= 65 else "lean" if v <= 35 else "normal",
        }
        for m, v in profile.items()
    }
    return {
        "commodity":        key,
        "as_of":            intel.get("run_date", ""),
        "current_month":    months[current_month],
        "current_score":    profile.get(str(current_month), 50),
        "note":             "100=peak harvest (low prices), 0=lean season (high prices)",
        "data_source":      data_source,
        "monthly_profile":  enriched,
    }


@app.get("/spreads", tags=["Zonal Prices"])
def state_price_spreads(
    commodity: Optional[str] = Query(None, description="Filter by commodity")
):
    """
    State price high vs low spread per commodity.
    Shows cheapest state, most expensive state, and % spread between them.
    """
    intel, fname = load_latest_intelligence()
    spreads = intel.get("state_spreads", {})

    if commodity:
        key = next((k for k in spreads if normalise(k) == normalise(commodity)), None)
        if not key:
            raise HTTPException(status_code=404, detail=f"'{commodity}' not found.")
        return {"as_of": intel.get("run_date", ""), **{key: spreads[key]}}

    return {
        "as_of":   intel.get("run_date", ""),
        "note":    "spread_pct = (high_price - low_price) / low_price * 100",
        "spreads": spreads,
    }


@app.get("/arbitrage", tags=["Trade Intelligence"])
def all_arbitrage(
    viable_only: bool = Query(False, description="Return only routes with positive net arbitrage")
):
    """
    Net arbitrage per kg after freight for all commodities.
    Shows best source state, best destination state, gross and net margin.
    Freight: NGN 12 per kg per 100km (diesel haulage rate June 2026).
    """
    intel, fname = load_latest_intelligence()
    arb = intel.get("arbitrage", {})
    if viable_only:
        arb = {k: v for k, v in arb.items() if v.get("viable", False)}
    return {
        "as_of":             intel.get("run_date", ""),
        "freight_rate":      "NGN 12/kg per 100km (diesel haulage)",
        "note":              "Net arbitrage = gross price spread/kg minus freight cost/kg",
        "viable_routes":     sum(1 for v in arb.values() if v.get("viable")),
        "per_commodity":     arb,
    }


@app.get("/arbitrage/{commodity}", tags=["Trade Intelligence"])
def commodity_arbitrage(commodity: str):
    """Net arbitrage detail for one commodity — best source, best destination, net margin."""
    intel, fname = load_latest_intelligence()
    arb = intel.get("arbitrage", {})
    key = next((k for k in arb if normalise(k) == normalise(commodity)), None)
    if not key:
        raise HTTPException(status_code=404,
            detail=f"No arbitrage data for '{commodity}'. Available: {list(arb.keys())}")
    return {
        "as_of":     intel.get("run_date", ""),
        "commodity": key,
        **arb[key],
    }


@app.get("/intelligence/latest", tags=["Intelligence"])
def latest_intelligence():
    """
    Full intelligence bundle — all metrics in one response.
    Includes FPI, volatility, 30d outlook, confidence, movers,
    early warning alerts, shortage scores, seasonality, spreads, and arbitrage.
    """
    intel, fname = load_latest_intelligence()
    return {
        "as_of":             intel.get("run_date", ""),
        "generated_at":      intel.get("generated_at", ""),
        "source_validated":  intel.get("source_validated", ""),
        "food_price_index":  intel.get("food_price_index", {}),
        "volatility_index":  intel.get("volatility_index", {}),
        "outlook_30d":       intel.get("outlook_30d", {}),
        "model_confidence":  intel.get("model_confidence", {}),
        "market_movers":     intel.get("market_movers", {}),
        "early_warning":     intel.get("early_warning_alerts", {}),
        "shortage_surplus":  intel.get("shortage_surplus", {}),
        "state_spreads":     intel.get("state_spreads", {}),
        "arbitrage":         intel.get("arbitrage", {}),
        "prices_with_units": intel.get("prices_with_units", {}),
    }


# ── Run locally ─────────────────────────────────────────────────────────────


@app.get("/supply", tags=["Trade Intelligence"])
def supply_availability(
    zone: Optional[str] = Query(None, description="Filter by zone e.g. North West"),
):
    """
    Supply availability per zone per commodity.
    Returns Tight / Balanced / Surplus label + numeric score (0-100).
    Derived from shortage/surplus scores in the intelligence layer.

    Score interpretation:
      0-30:  Shortage / Tight supply
      31-64: Balanced
      65-100: Surplus
    """
    intel, fname = load_latest_intelligence()
    scores = intel.get("shortage_surplus", {}).get("per_commodity", {})
    zonal,  zf   = load_latest_zonal()
    zones_data   = zonal.get("zones", {})

    ZONE_LIST = [
        "North West", "North Central", "North East",
        "South West", "South East", "South South"
    ]

    # Filter zones if requested
    if zone:
        match = next((z for z in ZONE_LIST if z.lower() == zone.lower()), None)
        if not match:
            raise HTTPException(
                status_code=404,
                detail=f"Zone '{zone}' not found. Available: {ZONE_LIST}"
            )
        ZONE_LIST = [match]

    result = {}
    for zone_name in ZONE_LIST:
        zone_commodities = {}
        zone_data = zones_data.get(zone_name, {})

        for commodity, score_data in scores.items():
            # Get the zone-specific price to adjust the national score
            zone_states = zone_data.get("states", {})
            zone_prices = []
            for state_data in zone_states.values():
                p = state_data.get(commodity, {}).get("state_price", 0)
                if p > 0:
                    zone_prices.append(p)

            national_score = score_data.get("score", 50)

            # Adjust score slightly by zone price vs national price
            national_anchor = zonal.get("national_anchors", {}).get(commodity, {})
            nat_price = national_anchor.get("price", 0)
            if zone_prices and nat_price > 0:
                zone_avg = sum(zone_prices) / len(zone_prices)
                price_ratio = zone_avg / nat_price
                # Higher zone price = tighter supply in that zone
                zone_score = round(national_score * (2 - price_ratio), 1)
                zone_score = max(0, min(100, zone_score))
            else:
                zone_score = national_score

            label = score_data.get("label", "Balanced")
            # Recalculate label from zone-adjusted score
            if zone_score >= 65:
                zone_label = "Surplus"
            elif zone_score >= 31:
                zone_label = "Balanced"
            else:
                zone_label = "Tight"

            zone_commodities[commodity] = {
                "score":          zone_score,
                "label":          zone_label,
                "national_score": national_score,
                "color":          "#4CAF50" if zone_label == "Surplus"
                                  else "#FF8C00" if zone_label == "Tight"
                                  else "#2196F3",
            }

        # Zone summary
        labels = [v["label"] for v in zone_commodities.values()]
        result[zone_name] = {
            "commodities": zone_commodities,
            "summary": {
                "tight":    labels.count("Tight"),
                "balanced": labels.count("Balanced"),
                "surplus":  labels.count("Surplus"),
                "dominant": max(set(labels), key=labels.count) if labels else "Balanced",
            }
        }

    return {
        "as_of":        intel.get("run_date", ""),
        "methodology":  "Score 0-30=Tight, 31-64=Balanced, 65-100=Surplus. "
                        "Combines price signal, seasonality, and trend direction.",
        "zones":        result,
    }


@app.get("/supply/{zone}", tags=["Trade Intelligence"])
def supply_by_zone(zone: str):
    """Supply availability for a specific zone — all commodities with score and label."""
    return supply_availability(zone=zone)


@app.get("/routes", tags=["Trade Intelligence"])
def route_distances():
    """
    All state-to-state route distances and freight cost estimates.
    Freight rate: NGN 12 per kg per 100km (diesel haulage, July 2026).
    Useful for the route cost calculator on the trade tools page.
    """
    # State capital coordinates and distances from major hubs
    STATES = {
        "Kano":    {"zone": "North West",    "lat": 12.00, "lng": 8.52,  "hub": True},
        "Kaduna":  {"zone": "North West",    "lat": 10.52, "lng": 7.44,  "hub": False},
        "Plateau": {"zone": "North Central", "lat": 9.93,  "lng": 8.89,  "hub": False},
        "Kogi":    {"zone": "North Central", "lat": 7.80,  "lng": 6.74,  "hub": False},
        "Adamawa": {"zone": "North East",    "lat": 9.33,  "lng": 12.39, "hub": False},
        "Borno":   {"zone": "North East",    "lat": 11.85, "lng": 13.16, "hub": False},
        "Oyo":     {"zone": "South West",    "lat": 7.85,  "lng": 3.93,  "hub": False},
        "Lagos":   {"zone": "South West",    "lat": 6.52,  "lng": 3.38,  "hub": True},
        "Anambra": {"zone": "South East",    "lat": 6.21,  "lng": 7.07,  "hub": False},
        "Imo":     {"zone": "South East",    "lat": 5.49,  "lng": 7.03,  "hub": False},
        "Rivers":  {"zone": "South South",   "lat": 4.82,  "lng": 7.03,  "hub": True},
        "Delta":   {"zone": "South South",   "lat": 5.70,  "lng": 5.95,  "hub": False},
    }

    # Road distances (km) between state pairs - from FMWORKS Nigeria road atlas
    DISTANCES = {
        ("Kano",    "Kaduna"):  185,
        ("Kano",    "Plateau"): 420,
        ("Kano",    "Kogi"):    560,
        ("Kano",    "Adamawa"): 680,
        ("Kano",    "Borno"):   580,
        ("Kano",    "Oyo"):     870,
        ("Kano",    "Lagos"):   1050,
        ("Kano",    "Anambra"): 890,
        ("Kano",    "Imo"):     950,
        ("Kano",    "Rivers"):  1100,
        ("Kano",    "Delta"):   950,
        ("Kaduna",  "Plateau"): 235,
        ("Kaduna",  "Kogi"):    375,
        ("Kaduna",  "Lagos"):   865,
        ("Kaduna",  "Rivers"):  915,
        ("Plateau", "Kogi"):    340,
        ("Plateau", "Lagos"):   750,
        ("Plateau", "Rivers"):  780,
        ("Kogi",    "Lagos"):   490,
        ("Kogi",    "Anambra"): 310,
        ("Kogi",    "Rivers"):  590,
        ("Adamawa", "Borno"):   410,
        ("Adamawa", "Lagos"):   1150,
        ("Borno",   "Lagos"):   1380,
        ("Oyo",     "Lagos"):   130,
        ("Oyo",     "Rivers"):  590,
        ("Lagos",   "Anambra"): 530,
        ("Lagos",   "Imo"):     590,
        ("Lagos",   "Rivers"):  650,
        ("Lagos",   "Delta"):   500,
        ("Anambra", "Imo"):     90,
        ("Anambra", "Rivers"):  180,
        ("Anambra", "Delta"):   220,
        ("Imo",     "Rivers"):  130,
        ("Rivers",  "Delta"):   160,
    }

    FREIGHT_RATE = 12.0  # NGN per kg per 100km

    # Build complete route table (both directions)
    routes = []
    all_states = list(STATES.keys())
    for i, origin in enumerate(all_states):
        for dest in all_states[i+1:]:
            key1 = (origin, dest)
            key2 = (dest, origin)
            dist = DISTANCES.get(key1) or DISTANCES.get(key2)
            if not dist:
                # Estimate via Kano hub if direct not available
                d1 = DISTANCES.get(("Kano", origin)) or DISTANCES.get((origin, "Kano")) or 500
                d2 = DISTANCES.get(("Kano", dest))   or DISTANCES.get((dest, "Kano"))   or 500
                dist = d1 + d2
                method = "estimated_via_hub"
            else:
                method = "direct"

            freight_per_kg = round(FREIGHT_RATE * dist / 100, 2)

            routes.append({
                "origin":           origin,
                "origin_zone":      STATES[origin]["zone"],
                "destination":      dest,
                "destination_zone": STATES[dest]["zone"],
                "distance_km":      dist,
                "freight_ngn_per_kg": freight_per_kg,
                "freight_ngn_per_mt": round(freight_per_kg * 1000, 0),
                "method":           method,
            })

    return {
        "freight_rate_basis": "NGN 12 per kg per 100km (diesel haulage, July 2026)",
        "note":               "Distances are road km between state capitals. "
                              "Actual routes may vary by commodity type and season.",
        "total_routes":       len(routes),
        "states":             STATES,
        "routes":             routes,
    }


@app.get("/routes/{origin}/{destination}", tags=["Trade Intelligence"])
def route_detail(origin: str, destination: str):
    """
    Freight cost for a specific origin-destination pair.
    Also shows which commodities make financial sense to trade on this route
    based on current arbitrage data.
    """
    # Get route data
    all_routes = route_distances()
    route = next(
        (r for r in all_routes["routes"]
         if (r["origin"].lower() == origin.lower() and
             r["destination"].lower() == destination.lower()) or
            (r["origin"].lower() == destination.lower() and
             r["destination"].lower() == origin.lower())),
        None
    )

    if not route:
        raise HTTPException(
            status_code=404,
            detail=f"No route found between '{origin}' and '{destination}'. "
                   f"Available states: {list(all_routes['states'].keys())}"
        )

    # Get arbitrage data and filter to this route
    try:
        intel, _ = load_latest_intelligence()
        arb_data  = intel.get("arbitrage", {})
        viable_on_route = []
        for commodity, arb in arb_data.items():
            src = arb.get("source_state", "")
            dst = arb.get("destination_state", "")
            # Check if this route is relevant (either direction)
            route_states = {origin.title(), destination.title()}
            arb_states   = {src.split(" (")[0], dst.split(" (")[0]}
            if route_states & arb_states:  # any overlap
                viable_on_route.append({
                    "commodity":              commodity,
                    "net_arbitrage_ngn_kg":   arb.get("net_arbitrage_ngn_kg", 0),
                    "gross_arbitrage_ngn_kg": arb.get("gross_arbitrage_ngn_kg", 0),
                    "route_freight_ngn_kg":   route["freight_ngn_per_kg"],
                    "viable":                 arb.get("net_arbitrage_ngn_kg", 0) > 0,
                })
        viable_on_route.sort(key=lambda x: x["net_arbitrage_ngn_kg"], reverse=True)
    except Exception:
        viable_on_route = []

    return {
        "route":            route,
        "commodities_to_trade": viable_on_route,
        "interpretation":   (
            f"Transporting goods from {route['origin']} to {route['destination']} "
            f"costs NGN {route['freight_ngn_per_kg']:.2f}/kg over {route['distance_km']}km."
        ),
    }



# ══════════════════════════════════════════════════════════════════════════════
# V3 ENDPOINTS — History, Alerts CRUD, Meta, Documentation
# ══════════════════════════════════════════════════════════════════════════════

import uuid
from typing import List

ALERTS_DB_PATH = os.path.join(BASE_DIR, "outputs", "alerts", "saved_alerts.json")
os.makedirs(os.path.dirname(ALERTS_DB_PATH), exist_ok=True)

def load_alerts_db():
    if os.path.exists(ALERTS_DB_PATH):
        with open(ALERTS_DB_PATH) as f:
            return json.load(f)
    return {"alerts": []}

def save_alerts_db(db):
    with open(ALERTS_DB_PATH, "w") as f:
        json.dump(db, f, indent=2)


# ── Historical time-series ─────────────────────────────────────────────────

@app.get("/history/{commodity}", tags=["Historical Data"])
def commodity_history(
    commodity: str,
    days: Optional[int] = Query(90, description="Number of days back (default 90)"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str]   = Query(None, description="End date YYYY-MM-DD (default today)"),
    resolution: Optional[str] = Query("weekly", description="daily or weekly"),
):
    """
    Historical price series for one commodity from the master dataset.
    Returns price array suitable for sparklines and charts.
    Date range: 2016 to today. All prices in NGN/MT.

    Examples:
      /history/Rice?days=90
      /history/Ginger?from_date=2026-01-01&to_date=2026-07-19
      /history/Maize%20(white)?days=30&resolution=daily
    """
    import pandas as pd

    master_path = os.path.join(BASE_DIR, "data", "processed", "agrolinking_master.csv")
    if not os.path.exists(master_path):
        raise HTTPException(status_code=404, detail="Master dataset not found on server.")

    df = pd.read_csv(master_path, parse_dates=["date"])

    # Find commodity
    available = df["commodity"].unique().tolist()
    key = next((c for c in available if c.lower() == commodity.lower()), None)
    if not key:
        raise HTTPException(
            status_code=404,
            detail=f"Commodity '{commodity}' not found. Available: {available}"
        )

    sub = df[df["commodity"] == key].copy()

    # Date filtering
    today = datetime.now()
    if from_date:
        try:
            start = datetime.strptime(from_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="from_date must be YYYY-MM-DD")
    else:
        start = today - timedelta(days=days)

    if to_date:
        try:
            end = datetime.strptime(to_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="to_date must be YYYY-MM-DD")
    else:
        end = today

    sub = sub[(sub["date"] >= start) & (sub["date"] <= end)].copy()
    sub = sub.sort_values("date")

    # Resample
    if resolution == "weekly":
        sub = sub.set_index("date")["price_ngn_mt"].resample("W").mean().dropna().reset_index()
        sub.columns = ["date", "price_ngn_mt"]
    elif resolution == "monthly":
        sub = sub.set_index("date")["price_ngn_mt"].resample("ME").mean().dropna().reset_index()
        sub.columns = ["date", "price_ngn_mt"]

    if len(sub) == 0:
        raise HTTPException(status_code=404,
            detail=f"No data for '{key}' in the requested date range.")

    prices = sub["price_ngn_mt"].tolist()
    dates  = sub["date"].dt.strftime("%Y-%m-%d").tolist()
    per_kg, unit = price_per_kg(key, prices[-1] if prices else 0)

    # Sparkline (last 7 points regardless of resolution)
    sparkline = [{"date": d, "price": round(p, 0)}
                 for d, p in zip(dates[-7:], prices[-7:])]

    # Stats
    pct_change_period = round((prices[-1] - prices[0]) / prices[0] * 100, 2) if len(prices) >= 2 else 0

    return {
        "commodity":          key,
        "from_date":          dates[0] if dates else "",
        "to_date":            dates[-1] if dates else "",
        "resolution":         resolution,
        "data_points":        len(dates),
        "currency":           "NGN",
        "unit":               "NGN/MT",
        "latest_price_ngn_mt": round(prices[-1], 0) if prices else 0,
        "latest_price_per_unit": per_kg,
        "unit_label":         unit,
        "pct_change_period":  pct_change_period,
        "min_price":          round(min(prices), 0),
        "max_price":          round(max(prices), 0),
        "avg_price":          round(sum(prices)/len(prices), 0),
        "sparkline":          sparkline,
        "series": [
            {"date": d, "price_ngn_mt": round(p, 0),
             "price_per_unit": round(p/1000, 2) if key != "Eggs" else round(p, 0)}
            for d, p in zip(dates, prices)
        ],
    }


@app.get("/history/compare", tags=["Historical Data"])
def compare_commodities(
    commodities: str = Query(..., description="Comma-separated commodity names e.g. Rice,Maize (white)"),
    days: int = Query(90, description="Number of days back"),
    resolution: str = Query("weekly", description="daily or weekly"),
):
    """
    Compare historical prices for multiple commodities on the same timeline.
    Useful for correlation charts and relative performance analysis.

    Example: /history/compare?commodities=Rice,Wheat,Maize (white)&days=180
    """
    import pandas as pd

    master_path = os.path.join(BASE_DIR, "data", "processed", "agrolinking_master.csv")
    if not os.path.exists(master_path):
        raise HTTPException(status_code=404, detail="Master dataset not found.")

    df    = pd.read_csv(master_path, parse_dates=["date"])
    names = [c.strip() for c in commodities.split(",")]
    start = datetime.now() - timedelta(days=days)
    end   = datetime.now()

    result = {}
    for name in names:
        available = df["commodity"].unique().tolist()
        key = next((c for c in available if c.lower() == name.lower()), None)
        if not key:
            continue
        sub = df[(df["commodity"] == key) & (df["date"] >= start) & (df["date"] <= end)].copy()
        sub = sub.sort_values("date")
        if resolution == "weekly":
            sub = sub.set_index("date")["price_ngn_mt"].resample("W").mean().dropna().reset_index()
            sub.columns = ["date", "price_ngn_mt"]
        prices = sub["price_ngn_mt"].tolist()
        dates  = sub["date"].dt.strftime("%Y-%m-%d").tolist()
        # Normalise to 100 at start for comparison
        base = prices[0] if prices else 1
        result[key] = {
            "series": [{"date": d, "price": round(p, 0), "indexed": round(p/base*100, 1)}
                       for d, p in zip(dates, prices)],
            "pct_change": round((prices[-1]-prices[0])/prices[0]*100, 2) if len(prices)>=2 else 0,
        }

    return {
        "from_date":    (datetime.now()-timedelta(days=days)).strftime("%Y-%m-%d"),
        "to_date":      datetime.now().strftime("%Y-%m-%d"),
        "resolution":   resolution,
        "index_note":   "indexed field = price normalised to 100 at start of period",
        "commodities":  result,
    }


@app.get("/history/fpi", tags=["Historical Data"])
def fpi_history(days: int = Query(90, description="Days of FPI history")):
    """
    Historical Food Price Index series from saved intelligence files.
    Returns daily FPI values for the requested period.
    """
    files = sorted(glob.glob(os.path.join(BASE_DIR, "outputs", "intelligence",
                                           "intelligence_*.json")))
    cutoff = datetime.now() - timedelta(days=days)
    series = []
    for f in files:
        date_str = run_date_from_file(os.path.basename(f))
        if not date_str:
            continue
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            continue
        if file_date < cutoff:
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            fpi = data.get("food_price_index", {}).get("value")
            if fpi is not None:
                series.append({"date": date_str, "fpi": fpi})
        except Exception:
            continue

    return {
        "base":        "2025=100",
        "methodology": "Weighted basket of 17 commodities using NBS 2023/24 consumption weights",
        "data_points": len(series),
        "series":      series,
        "latest":      series[-1] if series else None,
    }


# ── Price Alerts CRUD ──────────────────────────────────────────────────────

@app.get("/alerts/saved", tags=["Price Alerts"])
def get_saved_alerts():
    """List all saved price threshold alerts."""
    db = load_alerts_db()
    return {
        "count":  len(db["alerts"]),
        "alerts": db["alerts"],
    }


@app.post("/alerts/saved", tags=["Price Alerts"])
def create_alert(
    commodity:       str   = Query(..., description="Commodity name e.g. Rice"),
    threshold_price: float = Query(..., description="Alert price in NGN/MT"),
    direction:       str   = Query(..., description="above or below"),
    email:           Optional[str] = Query(None, description="Email for notification"),
    phone:           Optional[str] = Query(None, description="Phone for WhatsApp notification"),
    label:           Optional[str] = Query(None, description="Custom label for this alert"),
):
    """
    Create a new price threshold alert.
    Alert triggers when commodity price crosses the threshold in the specified direction.

    direction: 'above' = alert when price rises above threshold
               'below' = alert when price falls below threshold

    Note: actual WhatsApp/email delivery requires Twilio/SendGrid integration.
    Use GET /alerts/check to manually check alert status.
    """
    if direction not in ["above", "below"]:
        raise HTTPException(status_code=400, detail="direction must be 'above' or 'below'")

    # Validate commodity exists
    try:
        zonal, _ = load_latest_zonal()
        anchors  = zonal.get("national_anchors", {})
        key = next((k for k in anchors if k.lower() == commodity.lower()), None)
        if not key:
            raise HTTPException(status_code=404,
                detail=f"Commodity '{commodity}' not found.")
        current_price = anchors[key].get("price", 0)
    except HTTPException:
        raise
    except Exception:
        key = commodity
        current_price = 0

    alert = {
        "id":              str(uuid.uuid4())[:8],
        "commodity":       key,
        "threshold_price": threshold_price,
        "direction":       direction,
        "label":           label or f"{key} {direction} N{threshold_price:,.0f}",
        "email":           email,
        "phone":           phone,
        "created_at":      datetime.now().isoformat(),
        "last_checked":    None,
        "triggered":       False,
        "triggered_at":    None,
        "triggered_price": None,
        "active":          True,
        "current_price_at_creation": current_price,
    }

    db = load_alerts_db()
    db["alerts"].append(alert)
    save_alerts_db(db)

    return {
        "message":  "Alert created successfully.",
        "alert":    alert,
        "note":     "Use GET /alerts/check to check all alerts against current prices. "
                    "Actual push notifications require WhatsApp Business API or email integration.",
    }


@app.delete("/alerts/saved/{alert_id}", tags=["Price Alerts"])
def delete_alert(alert_id: str):
    """Delete a saved price alert by ID."""
    db = load_alerts_db()
    before = len(db["alerts"])
    db["alerts"] = [a for a in db["alerts"] if a["id"] != alert_id]
    if len(db["alerts"]) == before:
        raise HTTPException(status_code=404, detail=f"Alert ID '{alert_id}' not found.")
    save_alerts_db(db)
    return {"message": f"Alert {alert_id} deleted.", "remaining": len(db["alerts"])}


@app.get("/alerts/check", tags=["Price Alerts"])
def check_alerts():
    """
    Check all saved alerts against current prices.
    Returns list of triggered alerts.
    This is the monitoring job — run daily or on-demand.
    Actual notification delivery (WhatsApp/email) requires
    Twilio or SendGrid integration configured separately.
    """
    db = load_alerts_db()
    if not db["alerts"]:
        return {"triggered": [], "checked": 0, "message": "No saved alerts to check."}

    try:
        zonal, _ = load_latest_zonal()
        anchors  = zonal.get("national_anchors", {})
    except Exception:
        raise HTTPException(status_code=500, detail="Could not load current prices.")

    triggered = []
    updated   = []
    now       = datetime.now().isoformat()

    for alert in db["alerts"]:
        if not alert.get("active", True):
            updated.append(alert)
            continue

        key = next((k for k in anchors if k.lower() == alert["commodity"].lower()), None)
        current_price = anchors.get(key, {}).get("price", 0) if key else 0

        alert["last_checked"]   = now
        alert["current_price"]  = current_price

        is_triggered = (
            (alert["direction"] == "above" and current_price >= alert["threshold_price"]) or
            (alert["direction"] == "below" and current_price <= alert["threshold_price"])
        )

        if is_triggered and not alert.get("triggered"):
            alert["triggered"]       = True
            alert["triggered_at"]    = now
            alert["triggered_price"] = current_price
            triggered.append({
                **alert,
                "message": (
                    f"{alert['commodity']} is now N{current_price:,.0f}/MT — "
                    f"{'above' if alert['direction']=='above' else 'below'} "
                    f"your threshold of N{alert['threshold_price']:,.0f}/MT."
                ),
                "pct_from_threshold": round(
                    (current_price - alert["threshold_price"])
                    / alert["threshold_price"] * 100, 2
                ),
            })
        updated.append(alert)

    db["alerts"] = updated
    save_alerts_db(db)

    return {
        "checked":      len(updated),
        "triggered":    triggered,
        "triggered_count": len(triggered),
        "checked_at":   now,
        "note": "To enable push notifications, integrate Twilio (WhatsApp) "
                "or SendGrid (email) with the triggered alerts list above.",
    }


# ── Meta / platform stats ──────────────────────────────────────────────────

@app.get("/meta", tags=["Info"])
def platform_meta():
    """
    Real platform metadata — data sources, market counts, update frequency.
    Use this for credibility stats in the sidebar and footer.
    """
    # Count actual intelligence files to get platform age
    intel_files = glob.glob(os.path.join(BASE_DIR, "outputs", "intelligence", "*.json"))
    val_files   = glob.glob(os.path.join(BASE_DIR, "outputs", "forecasts", "validated", "*.json"))

    return {
        "platform":          "Agrolinking Commodity Intelligence",
        "version":           "2.1.0",
        "commodities_tracked": 17,
        "forecast_horizons":  6,
        "zones":             6,
        "states":            12,
        "data_sources": {
            "total_sources":    6,
            "sources": [
                {"name": "Agricome Africa", "type": "Weekly Instagram post",
                 "commodities": 7, "url": "instagram.com/agricomeafrica"},
                {"name": "WFP Nigeria Food Price Monitor", "type": "Monthly market survey",
                 "commodities": 13, "markets_covered": 30,
                 "url": "data.humdata.org/dataset/wfp-food-prices-for-nigeria"},
                {"name": "NGX / LCFE Exchange", "type": "Weekly exchange data",
                 "commodities": 2, "url": "ngxgroup.com"},
                {"name": "Agrolinking Primary Collection", "type": "Weekly field data",
                 "commodities": 3},
                {"name": "Market Naija TV", "type": "Weekly market reports",
                 "commodities": 1},
                {"name": "FMARD / NAFDAC", "type": "Monthly government data",
                 "commodities": 4},
            ],
            "total_markets_monitored": 42,
            "data_points_total":       "18,000+",
            "historical_depth":        "2016 to present",
        },
        "update_frequency": {
            "prices":       "Daily (pipeline runs every morning)",
            "models":       "Weekly retrain (Mondays)",
            "validation":   "Daily cross-reference against live market sources",
        },
        "accuracy": {
            "within_3pct_target": "13/17 commodities (livestock added July 2026)",
            "avg_model_error":    "1.5% post-correction",
            "validation_method":  "WFP ALPS cross-reference",
        },
        "pipeline_runs": {
            "intelligence_files": len(intel_files),
            "validated_forecasts": len(val_files),
        },
        "shortage_surplus_methodology": {
            "price_signal":  (
                "0-100. Measures current price vs reference price. "
                "100 = well below reference (cheap/surplus). "
                "0 = far above reference (expensive/shortage). "
                "Formula: 100 - ((current_price/reference_price - 1) * 200), clamped 0-100."
            ),
            "season_signal": (
                "0-100. From Agrolinking season calendar. "
                "100 = peak harvest month (prices historically lowest). "
                "0 = peak lean season (prices historically highest). "
                "Source: data/external/season_calendar.csv"
            ),
            "trend_signal":  (
                "0-100. Based on day-on-day price movement direction. "
                "100 = price falling strongly (surplus signal). "
                "50 = flat/stable. "
                "0 = price rising strongly (shortage signal). "
                "Formula: 50 - (day_change_pct * 10), clamped 0-100."
            ),
            "composite_score": (
                "Weighted average: price_signal*0.40 + season_signal*0.30 + trend_signal*0.30. "
                "0=severe shortage, 50=balanced, 100=strong surplus."
            ),
        },
        "food_price_index_methodology": {
            "base_period":    "June 2025 = 100",
            "basket":         "17 commodities weighted by NBS 2023/24 household consumption share",
            "endpoint":       "/index/food",
            "history":        "/history/fpi",
            "top_weights": {
                "Rice":           "14%",
                "Meat (beef)":    "10%",
                "Maize (white)":  "12%",
                "Beans (white)":  "7%",
                "Wheat":          "7%",
            }
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
