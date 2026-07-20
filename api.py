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
        "version":       "2.0.0",
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
    if not key:
        raise HTTPException(status_code=404,
            detail=f"No seasonality data for '{commodity}'. "
                   f"Available: {list(profiles.keys())}")
    profile = profiles[key]
    current_month = datetime.now().month
    months = {
        1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
        7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"
    }
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
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
