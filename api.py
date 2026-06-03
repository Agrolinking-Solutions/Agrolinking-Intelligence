"""
Agrolinking Commodity Intelligence — REST API
FastAPI application serving forecast data to the dev team.

Endpoints:
  GET /                          Health check + API info
  GET /commodities               List all tracked commodities
  GET /forecasts/latest          Latest validated forecast (all 13 commodities)
  GET /forecasts/{commodity}     Single commodity full forecast
  GET /forecasts/{commodity}/{horizon}  Single commodity at one horizon
  GET /zonal/latest              Latest zonal prices (all zones + states)
  GET /zonal/{commodity}         Zonal prices for one commodity
  GET /alerts/latest             Latest validated daily alert text
  GET /summary                   Dashboard summary card data (accuracy, error, date)
  GET /docs                      Auto-generated Swagger UI (built into FastAPI)
"""

import os
import json
import glob
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ── Path configuration ─────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
VALIDATED_DIR  = os.path.join(BASE_DIR, "outputs", "forecasts", "validated")
ZONAL_DIR      = os.path.join(BASE_DIR, "outputs", "forecasts", "zonal")
ALERTS_DIR     = os.path.join(BASE_DIR, "outputs", "daily_alerts")

# ── App setup ──────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Agrolinking Commodity Intelligence API",
    description = "Nigerian agricultural commodity price forecasts across 13 commodities "
                  "and 6 geopolitical zones. Data validated daily against Agricome Africa, "
                  "WFP Nigeria, NGX, and live market sources.",
    version     = "1.0.0",
    contact     = {
        "name":  "Agrolinking Research and Data Team",
        "url":   "https://agrolinking.com",
        "email": "data@agrolinking.com",
    },
)

# Allow all origins so the dev team can call from any frontend or domain
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["GET"],
    allow_headers     = ["*"],
)

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

def normalise_commodity(name: str) -> str:
    """Case-insensitive commodity name lookup."""
    return name.strip().lower()

def find_commodity(forecast: dict, name: str) -> tuple:
    """Find commodity in forecast dict, case-insensitively."""
    target = normalise_commodity(name)
    for key in forecast:
        if normalise_commodity(key) == target:
            return key, forecast[key]
    return None, None

VALID_HORIZONS = ["daily", "weekly", "2_weeks", "monthly", "3_months", "6_months"]

# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    """Health check and API overview."""
    forecast, fname = load_latest_validated()
    return {
        "status":        "operational",
        "api":           "Agrolinking Commodity Intelligence",
        "version":       "1.0.0",
        "commodities":   len(forecast),
        "last_updated":  fname.replace("forecast_validated_","").replace(".json",""),
        "docs":          "/docs",
        "endpoints": [
            "GET /commodities",
            "GET /forecasts/latest",
            "GET /forecasts/{commodity}",
            "GET /forecasts/{commodity}/{horizon}",
            "GET /zonal/latest",
            "GET /zonal/{commodity}",
            "GET /alerts/latest",
            "GET /summary",
        ]
    }


@app.get("/commodities", tags=["Commodities"])
def list_commodities():
    """
    Returns the list of all tracked commodities with their latest validated price,
    validation status, and last known date.
    """
    forecast, fname = load_latest_validated()
    result = []
    for name, data in forecast.items():
        vld         = data.get("validation", {})
        daily_h     = data.get("horizons", {}).get("daily", {})
        detail      = daily_h.get("forecast_end_detail", {})
        result.append({
            "commodity":         name,
            "price_ngn_mt":      data.get("last_known_price", 0),
            "last_known_date":   data.get("last_known_date", ""),
            "forecast_date":     detail.get("date", ""),
            "forecast_price":    detail.get("price", 0),
            "pct_change_daily":  detail.get("pct_change_from_today", 0),
            "validation_error":  vld.get("error_after_pct", 0),
            "validation_status": vld.get("status", "unknown"),
            "within_target":     vld.get("within_target", False),
            "currency":          "NGN",
            "unit":              "NGN/MT",
        })
    return {
        "count":       len(result),
        "as_of":       fname.replace("forecast_validated_","").replace(".json",""),
        "commodities": result,
    }


@app.get("/forecasts/latest", tags=["Forecasts"])
def latest_forecast(
    horizon: Optional[str] = Query(
        None,
        description="Filter to one horizon: daily, weekly, 2_weeks, monthly, 3_months, 6_months"
    )
):
    """
    Returns the full latest validated forecast for all 13 commodities.
    Optionally filter to a single horizon.
    """
    if horizon and horizon not in VALID_HORIZONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid horizon '{horizon}'. Valid: {VALID_HORIZONS}"
        )
    forecast, fname = load_latest_validated()
    run_date = fname.replace("forecast_validated_","").replace(".json","")
    result = {}
    for name, data in forecast.items():
        vld      = data.get("validation", {})
        horizons = data.get("horizons", {})
        if horizon:
            h_data  = horizons.get(horizon, {})
            detail  = h_data.get("forecast_end_detail", {})
            vals    = h_data.get("ensemble", {}).get("values", [])
            result[name] = {
                "horizon":            horizon,
                "forecast_date":      detail.get("date",""),
                "forecast_price_ngn": detail.get("price", vals[-1] if vals else 0),
                "pct_change":         detail.get("pct_change_from_today", 0),
                "direction":          detail.get("direction",""),
                "confidence_band":    {
                    "lower": h_data.get("ensemble",{}).get("lower_ci",  [None])[-1],
                    "upper": h_data.get("ensemble",{}).get("upper_ci",  [None])[-1],
                },
                "validation_error_pct": vld.get("error_after_pct", 0),
                "within_3pct_target":   vld.get("within_target", False),
            }
        else:
            # Return summary across all horizons
            horizon_summary = {}
            for h_name in VALID_HORIZONS:
                h_data  = horizons.get(h_name, {})
                detail  = h_data.get("forecast_end_detail", {})
                vals    = h_data.get("ensemble", {}).get("values", [])
                horizon_summary[h_name] = {
                    "date":             detail.get("date",""),
                    "price_ngn_mt":     detail.get("price", vals[-1] if vals else 0),
                    "pct_change":       detail.get("pct_change_from_today", 0),
                    "direction":        detail.get("direction",""),
                }
            result[name] = {
                "last_known_price":     data.get("last_known_price", 0),
                "last_known_date":      data.get("last_known_date",""),
                "currency":             "NGN",
                "unit":                 "NGN/MT",
                "validation": {
                    "reference_price":  vld.get("reference_price", 0),
                    "error_before_pct": vld.get("error_before_pct", 0),
                    "error_after_pct":  vld.get("error_after_pct", 0),
                    "action":           vld.get("action",""),
                    "within_target":    vld.get("within_target", False),
                },
                "horizons": horizon_summary,
            }
    return {
        "run_date":    run_date,
        "source_file": fname,
        "currency":    "NGN",
        "unit":        "NGN/MT",
        "forecasts":   result,
    }


@app.get("/forecasts/{commodity}", tags=["Forecasts"])
def commodity_forecast(commodity: str):
    """
    Full forecast for a single commodity across all 6 horizons.
    Includes weekly price trajectory, confidence bands, and validation metadata.

    Example: /forecasts/Rice  or  /forecasts/Maize%20(white)
    """
    forecast, fname = load_latest_validated()
    key, data = find_commodity(forecast, commodity)
    if not key:
        available = list(forecast.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Commodity '{commodity}' not found. Available: {available}"
        )
    run_date = fname.replace("forecast_validated_","").replace(".json","")
    vld      = data.get("validation", {})
    horizons = {}
    for h_name in VALID_HORIZONS:
        h_data = data.get("horizons", {}).get(h_name, {})
        detail = h_data.get("forecast_end_detail", {})
        vals   = h_data.get("ensemble", {}).get("values", [])
        dates  = h_data.get("dates", [])
        horizons[h_name] = {
            "forecast_date":   detail.get("date",""),
            "forecast_price":  detail.get("price", vals[-1] if vals else 0),
            "pct_change":      detail.get("pct_change_from_today", 0),
            "direction":       detail.get("direction",""),
            "weekly_series": [
                {"date": d, "price": v,
                 "lower": lo, "upper": hi}
                for d, v, lo, hi in zip(
                    dates,
                    vals,
                    h_data.get("ensemble",{}).get("lower_ci", vals),
                    h_data.get("ensemble",{}).get("upper_ci", vals),
                )
            ],
        }
    return {
        "commodity":        key,
        "run_date":         run_date,
        "last_known_price": data.get("last_known_price", 0),
        "last_known_date":  data.get("last_known_date",""),
        "currency":         "NGN",
        "unit":             "NGN/MT",
        "models_used":      data.get("models_used", []),
        "weights":          data.get("weights", {}),
        "validation": {
            "reference_price":    vld.get("reference_price", 0),
            "error_before_pct":   vld.get("error_before_pct", 0),
            "error_after_pct":    vld.get("error_after_pct", 0),
            "action":             vld.get("action",""),
            "correction":         vld.get("correction",""),
            "within_target":      vld.get("within_target", False),
            "sources_checked":    vld.get("sources_checked", []),
        },
        "horizons": horizons,
    }


@app.get("/forecasts/{commodity}/{horizon}", tags=["Forecasts"])
def commodity_horizon_forecast(commodity: str, horizon: str):
    """
    Forecast for a single commodity at a single horizon.
    Returns the weekly price series, confidence band, and end-point summary.

    Horizons: daily | weekly | 2_weeks | monthly | 3_months | 6_months
    Example: /forecasts/Rice/monthly
    """
    if horizon not in VALID_HORIZONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid horizon '{horizon}'. Valid: {VALID_HORIZONS}"
        )
    forecast, fname = load_latest_validated()
    key, data = find_commodity(forecast, commodity)
    if not key:
        raise HTTPException(
            status_code=404,
            detail=f"Commodity '{commodity}' not found. Available: {list(forecast.keys())}"
        )
    h_data = data.get("horizons", {}).get(horizon, {})
    if not h_data:
        raise HTTPException(status_code=404, detail=f"No {horizon} horizon data for {key}.")
    detail = h_data.get("forecast_end_detail", {})
    vals   = h_data.get("ensemble", {}).get("values", [])
    dates  = h_data.get("dates", [])
    vld    = data.get("validation", {})
    return {
        "commodity":          key,
        "horizon":            horizon,
        "run_date":           fname.replace("forecast_validated_","").replace(".json",""),
        "last_known_price":   data.get("last_known_price", 0),
        "last_known_date":    data.get("last_known_date",""),
        "forecast_date":      detail.get("date",""),
        "forecast_price_ngn": detail.get("price", vals[-1] if vals else 0),
        "pct_change":         detail.get("pct_change_from_today", 0),
        "direction":          detail.get("direction",""),
        "currency":           "NGN",
        "unit":               "NGN/MT",
        "validation_error_pct": vld.get("error_after_pct", 0),
        "within_target":      vld.get("within_target", False),
        "weekly_series": [
            {"date": d, "price": v,
             "lower_ci": lo, "upper_ci": hi}
            for d, v, lo, hi in zip(
                dates, vals,
                h_data.get("ensemble",{}).get("lower_ci", vals),
                h_data.get("ensemble",{}).get("upper_ci", vals),
            )
        ],
    }


@app.get("/zonal/latest", tags=["Zonal Prices"])
def latest_zonal(
    zone: Optional[str] = Query(None, description="Filter by zone name e.g. North West"),
    commodity: Optional[str] = Query(None, description="Filter by commodity name e.g. Rice"),
):
    """
    Latest zonal and state-level prices for all 13 commodities across
    6 geopolitical zones and 12 states. Optionally filter by zone or commodity.
    """
    zonal, fname = load_latest_zonal()
    result = zonal
    if zone:
        zone_key = next((k for k in result.get("zones",{}) 
                         if k.lower() == zone.lower()), None)
        if not zone_key:
            available = list(result.get("zones",{}).keys())
            raise HTTPException(
                status_code=404,
                detail=f"Zone '{zone}' not found. Available: {available}"
            )
        result = {"zones": {zone_key: result["zones"][zone_key]}}
    if commodity:
        target = normalise_commodity(commodity)
        filtered_zones = {}
        for zname, zdata in result.get("zones", {}).items():
            filtered_states = {}
            for sname, sdata in zdata.get("states", {}).items():
                comms = {
                    k: v for k, v in sdata.get("commodities", {}).items()
                    if normalise_commodity(k) == target
                }
                if comms:
                    filtered_states[sname] = {"commodities": comms}
            if filtered_states:
                filtered_zones[zname] = {"states": filtered_states,
                                          "description": zdata.get("description","")}
        result = {"zones": filtered_zones}
    return {
        "source_file":       fname,
        "run_date":          zonal.get("run_date",""),
        "national_anchors":  zonal.get("national_anchors", {}),
        "best_sourcing":     zonal.get("best_sourcing", {}),
        **result,
    }


@app.get("/zonal/{commodity}", tags=["Zonal Prices"])
def commodity_zonal(commodity: str):
    """
    State-level prices for a single commodity across all 12 states.
    Includes best sourcing state, price spread, and day-on-day change.

    Example: /zonal/Rice  or  /zonal/Maize%20(white)
    """
    zonal, fname = load_latest_zonal()
    target  = normalise_commodity(commodity)
    anchors = zonal.get("national_anchors", {})
    comm_key = next((k for k in anchors if normalise_commodity(k) == target), None)
    if not comm_key:
        raise HTTPException(
            status_code=404,
            detail=f"Commodity '{commodity}' not found in zonal data."
        )
    state_prices = {}
    for zone_name, zone_data in zonal.get("zones", {}).items():
        for state_name, state_data in zone_data.get("states", {}).items():
            comm_data = state_data.get("commodities", {}).get(comm_key)
            if comm_data:
                state_prices[state_name] = {
                    "zone":            zone_name,
                    "price_ngn_mt":    comm_data.get("price", 0),
                    "day_change_pct":  comm_data.get("day_change_pct", 0),
                    "is_primary":      comm_data.get("is_primary", False),
                    "price_factor":    comm_data.get("price_factor", 1.0),
                }
    best = zonal.get("best_sourcing", {}).get(comm_key, {})
    return {
        "commodity":        comm_key,
        "run_date":         zonal.get("run_date",""),
        "national_price":   anchors.get(comm_key, {}).get("price", 0),
        "day_change_pct":   anchors.get(comm_key, {}).get("day_change", 0),
        "pct_vs_reference": anchors.get(comm_key, {}).get("pct_vs_ref", 0),
        "currency":         "NGN",
        "unit":             "NGN/MT",
        "best_sourcing": {
            "state":        best.get("state",""),
            "price_ngn_mt": best.get("price", 0),
            "spread_pct":   best.get("spread_pct", 0),
            "vs_state":     best.get("vs_state",""),
        },
        "state_prices": state_prices,
    }


@app.get("/alerts/latest", tags=["Alerts"])
def latest_alert():
    """
    Latest validated daily price alert, formatted for WhatsApp or email broadcast.
    Returns the alert as plain text and as structured JSON.
    """
    alert_text, fname = load_latest_alert()
    date_str = fname.replace("alert_validated_","").replace(".txt","")
    return {
        "date":       date_str,
        "source":     fname,
        "text":       alert_text,
        "format":     "WhatsApp / Email ready",
    }


@app.get("/summary", tags=["Dashboard"])
def summary():
    """
    Dashboard summary card data — commodities tracked, verified accuracy,
    avg model error, and last pipeline run date.
    Designed for the website header/hero section.
    """
    forecast, fname  = load_latest_validated()
    run_date         = fname.replace("forecast_validated_","").replace(".json","")
    within_target    = sum(
        1 for d in forecast.values()
        if d.get("validation",{}).get("within_target", False)
    )
    errors = [
        d.get("validation",{}).get("error_after_pct", 0)
        for d in forecast.values()
        if d.get("validation",{}).get("error_after_pct") is not None
    ]
    avg_error = round(sum(errors) / len(errors), 2) if errors else 0
    return {
        "commodities_tracked":      len(forecast),
        "verified_accuracy":        f"{within_target}/{len(forecast)}",
        "verified_accuracy_pct":    round(within_target / len(forecast) * 100, 1),
        "avg_model_error_pct":      avg_error,
        "last_pipeline_run":        run_date,
        "accuracy_target":          "within 3% of live market prices",
        "data_sources":             ["Agricome Africa", "WFP Nigeria", "NGX", "Market Naija TV", "LCFE"],
        "zones":                    6,
        "states":                   12,
        "forecast_horizons":        ["daily","weekly","2_weeks","monthly","3_months","6_months"],
    }


# ── Run directly for local testing ────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
