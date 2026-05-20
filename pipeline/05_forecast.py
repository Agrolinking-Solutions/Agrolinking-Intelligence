"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 5: Forecasting Engine
─────────────────────────────────────────────────────────────
Generates forecasts for all 11 commodities across 6 horizons:
  daily (1d), weekly (7d), 2_weeks (14d), monthly (30d),
  3_months (90d), 6_months (180d)

For each commodity:
  1. Loads trained ARIMA, Prophet, XGBoost models
  2. Generates forecasts from each model
  3. Blends using ensemble weights
  4. Computes daily % change vs previous day/week
  5. Saves forecast JSON + appends to master CSV

Run: python pipeline/05_forecast.py
"""

import os, sys, json, warnings
import pandas as pd
import numpy as np
import joblib
from datetime import datetime, timedelta
from loguru import logger

warnings.filterwarnings("ignore")
import logging
logging.getLogger("prophet").setLevel(logging.WARNING)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")
logger.add(os.path.join(PATHS["logs_dir"], "forecast_{time:YYYY-MM-DD}.log"),
    rotation="1 day", retention="30 days", level="DEBUG")

FEATURES_DIR  = os.path.join(os.path.dirname(PATHS["master"]), "features")
MODELS_DIR    = PATHS["models_dir"]
FORECASTS_DIR = PATHS["forecasts_dir"]
os.makedirs(FORECASTS_DIR, exist_ok=True)

HORIZONS = {
    "daily":    1,
    "weekly":   7,
    "2_weeks":  14,
    "monthly":  30,
    "3_months": 90,
    "6_months": 180,
}

PROPHET_REGRESSORS = [
    "fx_rate_usd_ngn","fuel_cost_index",
    "food_inflation_yoy","shock_active","commodity_season_score",
]

XGB_EXCLUDE = [
    "date","commodity","currency","unit","source","market_type","region",
    "data_source","record_type","notes","is_validated","outlier_flag",
    "outlier_reason","price_raw_ngn_mt","price_ngn_mt","log_price",
    "price_usd_mt","price_real_ngn_mt","fx_rate","rainfall_index",
]


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def safe_name(c):
    return c.lower().replace(" ","_").replace("(","").replace(")","")

def get_model_path(mtype, commodity):
    return os.path.join(MODELS_DIR, mtype, f"{safe_name(commodity)}.pkl")

def load_features(commodity):
    path = os.path.join(FEATURES_DIR, f"features_{safe_name(commodity)}.csv")
    return pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)

def load_external_future(start_date, n_weeks):
    """Build a future date spine with all external features filled."""
    dates = pd.date_range(start=start_date, periods=n_weeks, freq="W-MON")
    future = pd.DataFrame({"date": dates})

    seasons = pd.read_csv(PATHS["season_calendar"], parse_dates=["date"])
    fx      = pd.read_csv(PATHS["fx_rates"],        parse_dates=["date"])
    fuel    = pd.read_csv(PATHS["fuel_prices"],      parse_dates=["date"])
    inf_    = pd.read_csv(PATHS["inflation"],        parse_dates=["date"])

    for df_ext, cols in [
        (seasons, [c for c in seasons.columns if c != "date"]),
        (fx,      ["fx_rate_usd_ngn"]),
        (fuel,    ["fuel_diesel_ngn_litre","fuel_petrol_ngn_litre"]),
        (inf_,    ["headline_inflation_yoy","food_inflation_yoy"]),
    ]:
        df_ext["date"] = pd.to_datetime(df_ext["date"])
        df_ext["date"] = df_ext["date"] - pd.to_timedelta(df_ext["date"].dt.dayofweek, unit="D")
        future = future.merge(df_ext[["date"]+cols], on="date", how="left")

    # Forward fill any remaining gaps
    for col in future.columns:
        if col != "date":
            future[col] = future[col].ffill().bfill()

    # Fuel cost index
    d0 = fuel["fuel_diesel_ngn_litre"].dropna().iloc[0] if fuel["fuel_diesel_ngn_litre"].notna().any() else 200.0
    p0 = fuel["fuel_petrol_ngn_litre"].dropna().iloc[0] if fuel["fuel_petrol_ngn_litre"].notna().any() else 165.0
    future["fuel_cost_index"] = (
        0.70 * future["fuel_diesel_ngn_litre"] / d0 +
        0.30 * future["fuel_petrol_ngn_litre"] / p0
    ).round(3)

    # Shock: Middle East war still active April 2026
    future["shock_active"]   = future["date"].apply(lambda d: 1 if d >= pd.Timestamp("2026-03-13") else 0)
    future["shock_severity"] = future["date"].apply(lambda d: 2 if d >= pd.Timestamp("2026-03-13") else 0)

    future["year"]    = future["date"].dt.year
    future["quarter"] = future["date"].dt.quarter

    # Hard fill: replace any remaining NaNs with safe defaults
    safe_defaults = {
        "fx_rate_usd_ngn":       1640.0,
        "fuel_diesel_ngn_litre": 1420.0,
        "fuel_petrol_ngn_litre": 1260.0,
        "fuel_cost_index":       5.5,
        "headline_inflation_yoy":25.6,
        "food_inflation_yoy":    27.5,
        "shock_active":          0,
        "shock_severity":        0,
        "commodity_season_score":0.3,
    }
    for col, default in safe_defaults.items():
        if col in future.columns:
            future[col] = future[col].fillna(default)

    return future


def build_commodity_season_score(future, commodity):
    """Add commodity-specific season score to future dataframe."""
    season_map = {
        "Cashew Nuts":    ["cashew_harvest","season_dry","is_lean_season"],
        "Cocoa":          ["cocoa_main_crop","cocoa_mid_crop","season_rainy"],
        "Sesame":         ["sesame_harvest","season_harvesting","season_planting"],
        "Ginger":         ["ginger_harvest","season_rainy","season_planting"],
        "Hibiscus":       ["hibiscus_harvest","season_harvesting","season_rainy"],
        "Soybeans":       ["soybean_harvest","season_planting","season_harvesting"],
        "Sorghum":        ["sorghum_harvest","season_planting","season_dry"],
        "Maize (white)":  ["maize_harvest","season_planting","is_lean_season"],
        "Maize (yellow)": ["maize_harvest","season_planting","is_lean_season"],
        "Beans (white)":  ["season_harvesting","season_planting","is_lean_season"],
        "Beans (red)":    ["season_harvesting","season_planting","is_lean_season"],
        "Wheat":          ["season_dry","season_planting_early","is_festive_period"],
    }
    cols = season_map.get(commodity, ["season_harvesting","season_planting"])
    avail = [c for c in cols if c in future.columns]
    future["commodity_season_score"] = future[avail].mean(axis=1).round(3) if avail else 0.0
    return future


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST FROM EACH MODEL
# ─────────────────────────────────────────────────────────────────────────────

def forecast_arima(commodity, n_weeks):
    """Generate n_weeks forecast from saved ARIMA model."""
    path = get_model_path("arima", commodity)
    if not os.path.exists(path):
        return None
    try:
        obj = joblib.load(path)
        # Handle both dict-wrapped and bare ARIMA objects
        model = obj["model"] if isinstance(obj, dict) else obj
        preds, conf_int = model.predict(n_periods=n_weeks, return_conf_int=True)
        preds = np.clip(preds, 0, None)
        return {
            "values":   preds.tolist(),
            "lower_ci": np.clip(conf_int[:,0], 0, None).tolist(),
            "upper_ci": conf_int[:,1].tolist(),
        }
    except Exception as e:
        logger.warning(f"  [{commodity}] ARIMA forecast failed: {e}")
        return None


def forecast_prophet(commodity, future_df, hist_df):
    """Generate forecast from saved Prophet model using future regressors."""
    path = get_model_path("prophet", commodity)
    if not os.path.exists(path):
        return None
    try:
        obj        = joblib.load(path)
        model      = obj["model"]
        regressors = obj.get("regressors", [])

        future_p = future_df[["date"]].rename(columns={"date":"ds"}).copy()
        future_p["ds"] = pd.to_datetime(future_p["ds"])
        for r in regressors:
            if r in future_df.columns:
                future_p[r] = future_df[r].values

        fc    = model.predict(future_p)
        preds = np.clip(fc["yhat"].values, 0, None)
        lower = np.clip(fc["yhat_lower"].values, 0, None)
        upper = fc["yhat_upper"].values
        return {
            "values":   preds.tolist(),
            "lower_ci": lower.tolist(),
            "upper_ci": upper.tolist(),
        }
    except Exception as e:
        logger.warning(f"  [{commodity}] Prophet forecast failed: {e}")
        return None


def forecast_xgboost(commodity, hist_df, future_df, n_weeks):
    """
    Recursive XGBoost forecast.
    Each step: use last known prices as lag features → predict next week.
    Repeat n_weeks times.
    """
    path = get_model_path("xgboost", commodity)
    if not os.path.exists(path):
        return None
    try:
        obj          = joblib.load(path)
        model        = obj["model"]
        scaler       = obj["scaler"]
        feature_cols = obj["features"]

        # Build a rolling window of the last 52 weeks of history
        numeric_types = ["float64","float32","int64","int32","bool","int8","uint8"]
        hist_numeric = hist_df.copy()
        for col in feature_cols:
            if col not in hist_numeric.columns:
                hist_numeric[col] = 0.0

        # We need the last row's features as our starting point
        last_row = hist_numeric[feature_cols].iloc[-1:].copy()
        for col in last_row.columns:
            last_row[col] = last_row[col].fillna(last_row[col].median() if not last_row[col].isna().all() else 0)

        preds         = []
        rolling_prices = list(hist_df["price_ngn_mt"].values[-52:])

        for i in range(n_weeks):
            row = last_row.copy()

            # Update lag features with rolling predictions
            lag_map = {
                "price_lag_1w":  1,  "price_lag_2w":  2,
                "price_lag_4w":  4,  "price_lag_8w":  8,
                "price_lag_12w": 12, "price_lag_26w": 26,
                "price_lag_52w": 52,
            }
            for feat, lag in lag_map.items():
                if feat in row.columns and len(rolling_prices) >= lag:
                    row[feat] = rolling_prices[-lag]

            # Update rolling stats
            rp = np.array(rolling_prices)
            for w, col in [(4,"rolling_mean_4w"),(8,"rolling_mean_8w"),
                           (12,"rolling_mean_12w"),(26,"rolling_mean_26w")]:
                if col in row.columns and len(rp) >= 2:
                    row[col] = rp[-min(w,len(rp)):].mean()
            for w, col in [(4,"rolling_std_4w"),(8,"rolling_std_8w"),
                           (12,"rolling_std_12w"),(26,"rolling_std_26w")]:
                if col in row.columns and len(rp) >= 2:
                    row[col] = rp[-min(w,len(rp)):].std() if len(rp) >= 2 else 0

            # Update external features from future_df
            if i < len(future_df):
                future_row = future_df.iloc[i]
                for ext_col in ["fx_rate_usd_ngn","fuel_cost_index",
                                 "food_inflation_yoy","headline_inflation_yoy",
                                 "shock_active","shock_severity",
                                 "commodity_season_score","year","quarter",
                                 "month_sin","month_cos","week_sin","week_cos",
                                 "season_dry","season_rainy","season_planting",
                                 "season_harvesting","is_lean_season",
                                 "is_ramadan","is_festive_period",
                                 "cashew_harvest","cocoa_main_crop","cocoa_mid_crop",
                                 "sesame_harvest","ginger_harvest","hibiscus_harvest",
                                 "sorghum_harvest","maize_harvest","soybean_harvest"]:
                    if ext_col in row.columns and ext_col in future_row.index:
                        row[ext_col] = future_row[ext_col]

            row_scaled = scaler.transform(row[feature_cols])
            raw_pred = float(np.clip(model.predict(row_scaled)[0], 0, None))
            # Stability cap: limit week-on-week change to ±20% to prevent runaway recursion
            if rolling_prices:
                max_change = 0.20
                prev_p = rolling_prices[-1]
                raw_pred = np.clip(raw_pred,
                    prev_p * (1 - max_change),
                    prev_p * (1 + max_change))
            pred = float(raw_pred)
            preds.append(pred)
            rolling_prices.append(pred)
            if len(rolling_prices) > 52:
                rolling_prices.pop(0)

            # Update lag_1w for next iteration
            if "price_lag_1w" in last_row.columns:
                last_row["price_lag_1w"] = pred

        # Simple confidence interval: ±10% for short horizon, ±20% for long
        ci_pct = np.linspace(0.10, 0.22, n_weeks)
        lower  = [max(0, p * (1 - ci)) for p, ci in zip(preds, ci_pct)]
        upper  = [p * (1 + ci) for p, ci in zip(preds, ci_pct)]

        return {
            "values":   preds,
            "lower_ci": lower,
            "upper_ci": upper,
        }
    except Exception as e:
        logger.warning(f"  [{commodity}] XGBoost forecast failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ENSEMBLE BLEND
# ─────────────────────────────────────────────────────────────────────────────

def blend_forecasts(model_forecasts, weights, n_weeks):
    """Weighted average of all available model forecasts."""
    available = {m: f for m, f in model_forecasts.items()
                 if f is not None and len(f.get("values",[])) >= n_weeks}

    if not available:
        return None

    # Renormalise weights to available models only
    w_avail = {m: weights.get(m, 1/len(available)) for m in available}
    w_total = sum(w_avail.values())
    if w_total == 0:
        w_avail = {m: 1/len(available) for m in available}
        w_total = 1.0
    w_norm = {m: v/w_total for m, v in w_avail.items()}

    blended = np.zeros(n_weeks)
    lower   = np.zeros(n_weeks)
    upper   = np.zeros(n_weeks)

    for m, fc in available.items():
        w = w_norm[m]
        blended += w * np.array(fc["values"][:n_weeks])
        lower   += w * np.array(fc.get("lower_ci", fc["values"])[:n_weeks])
        upper   += w * np.array(fc.get("upper_ci", fc["values"])[:n_weeks])

    return {
        "values":        np.clip(blended, 0, None).tolist(),
        "lower_ci":      np.clip(lower,   0, None).tolist(),
        "upper_ci":      upper.tolist(),
        "models_used":   list(available.keys()),
        "weights_used":  {m: round(w_norm[m], 3) for m in available},
    }


# ─────────────────────────────────────────────────────────────────────────────
# DAILY % CHANGE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def compute_price_changes(commodity, last_price, forecast_values, forecast_dates):
    """
    For each forecast day, compute % change vs previous day.
    Also computes change vs last known price for first forecast point.
    """
    changes = []
    prev = last_price

    for i, (price, date) in enumerate(zip(forecast_values, forecast_dates)):
        if prev and prev > 0:
            pct = (price - prev) / prev * 100
        else:
            pct = 0.0

        direction = "↑" if pct > 0 else ("↓" if pct < 0 else "→")
        changes.append({
            "date":        str(date.date()) if hasattr(date, "date") else str(date),
            "price":       round(price, 2),
            "prev_price":  round(prev, 2),
            "pct_change":  round(pct, 2),
            "direction":   direction,
            "label":       (
                f"{commodity}: ₦{price:,.0f}/MT  "
                f"{direction} {abs(pct):.1f}% vs prev"
            ),
        })
        prev = price

    return changes


# ─────────────────────────────────────────────────────────────────────────────
# DAILY ALERT FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

def generate_daily_alert(all_forecasts, run_date):
    """
    Generate the daily price change report for all commodities.
    This is designed to be turned into a social media post.
    """
    lines = [
        f"📊 AGROLINKING COMMODITY INTELLIGENCE ALERT",
        f"📅 {run_date.strftime('%A, %d %B %Y')}",
        f"{'─' * 45}",
    ]

    for commodity in COMMODITIES:
        if commodity not in all_forecasts:
            continue
        fc = all_forecasts[commodity]
        daily = fc.get("horizons", {}).get("daily", {})
        if not daily or not daily.get("ensemble"):
            continue

        price     = daily["ensemble"]["values"][0]
        last_p    = fc.get("last_known_price", price)
        pct       = (price - last_p) / last_p * 100 if last_p > 0 else 0
        direction = "▲" if pct > 0.5 else ("▼" if pct < -0.5 else "→")
        color_tag = "+" if pct > 0 else ""

        lines.append(
            f"{direction} {commodity:<18} "
            f"₦{price:>13,.0f}/MT  "
            f"{color_tag}{pct:+.1f}%"
        )

    lines.append(f"{'─' * 45}")
    lines.append(f"Source: Agrolinking Intelligence Platform")
    lines.append(f"Next update: {(run_date + timedelta(days=1)).strftime('%d %b %Y')}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# APPEND FORECASTS TO MASTER CSV
# ─────────────────────────────────────────────────────────────────────────────

def append_to_master(all_forecasts, run_date):
    """
    Append weekly forecast rows to the master CSV.
    Only appends the weekly horizon (one row per commodity per week).
    Skips dates already present in master.
    """
    master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    new_rows = []

    for commodity, fc in all_forecasts.items():
        weekly = fc.get("horizons", {}).get("weekly", {})
        if not weekly or not weekly.get("ensemble"):
            continue

        ensemble = weekly["ensemble"]
        dates    = weekly.get("dates", [])
        values   = ensemble.get("values", [])

        for date_str, price in zip(dates, values):
            date = pd.Timestamp(date_str)
            # Skip if date already in master for this commodity
            exists = master[
                (master["commodity"] == commodity) &
                (master["date"] == date)
            ]
            if len(exists) > 0:
                continue

            new_rows.append({
                "commodity":          commodity,
                "date":               date,
                "price_ngn_mt":       round(price, 2),
                "currency":           "NGN",
                "unit":               "NGN/MT",
                "source":             "Agrolinking Intelligence Platform",
                "market_type":        "wholesale",
                "region":             "National",
                "fx_rate":            np.nan,
                "rainfall_index":     np.nan,
                "data_quality_score": 0.75,
                "is_validated":       False,
                "notes":              f"Ensemble forecast — {run_date.date()}",
                "data_source":        "Agrolinking_forecast",
                "record_type":        "forecast",
                "outlier_flag":       False,
                "outlier_reason":     "",
                "price_raw_ngn_mt":   round(price, 2),
            })

    if new_rows:
        new_df  = pd.DataFrame(new_rows)
        updated = pd.concat([master, new_df], ignore_index=True)
        updated = updated.sort_values(["commodity","date"]).reset_index(drop=True)
        updated.to_csv(PATHS["master"], index=False)
        logger.info(f"  Master updated: +{len(new_rows)} forecast rows appended")
    else:
        logger.info("  Master: no new rows to append (all dates already present)")

    return len(new_rows)


# ─────────────────────────────────────────────────────────────────────────────
# PER-COMMODITY FORECAST
# ─────────────────────────────────────────────────────────────────────────────

def forecast_commodity(commodity, run_date):
    """Generate full forecast for one commodity across all horizons."""

    hist_df = load_features(commodity)

    # Use REAL data sources only for price anchor — never validated forecast rows.
    # Validated forecast rows create a feedback loop if used as training anchors.
    REAL_SOURCES = {"Agricome", "Agrolinking_primary", "WFP"}
    master_df = pd.read_csv(PATHS["master"], parse_dates=["date"])
    comm_master = master_df[master_df["commodity"] == commodity].sort_values("date")

    # Try real source first (Agricome/WFP/primary)
    real_rows = comm_master[comm_master["data_source"].isin(REAL_SOURCES)]
    if len(real_rows) > 0:
        last_price = float(real_rows["price_ngn_mt"].iloc[-1])
        last_date  = real_rows["date"].iloc[-1]
    elif len(comm_master) > 0:
        # Fall back to any row (carry_forward etc) but never validated forecast
        non_fc = comm_master[comm_master["record_type"] != "forecast"]
        if len(non_fc) > 0:
            last_price = float(non_fc["price_ngn_mt"].iloc[-1])
            last_date  = non_fc["date"].iloc[-1]
        else:
            last_price = float(comm_master["price_ngn_mt"].iloc[-1])
            last_date  = comm_master["date"].iloc[-1]
    else:
        last_price = float(hist_df["price_ngn_mt"].iloc[-1])
        last_date  = hist_df["date"].iloc[-1]

    # Always forecast from the next Monday after today's run date.
    # This ensures forecasts are always future-facing, even if data lags.
    days_ahead = (7 - run_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    forecast_start = (run_date + pd.Timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    forecast_start = pd.Timestamp(forecast_start)

    # We need 26 weeks of future data (6 months)
    N_WEEKS = 26
    future_df = load_external_future(forecast_start, N_WEEKS)
    future_df = build_commodity_season_score(future_df, commodity)

    # Load ensemble weights
    ens_path = get_model_path("ensemble", commodity)
    weights  = joblib.load(ens_path) if os.path.exists(ens_path) else {}

    # Generate forecasts from each model (full 26 weeks)
    model_forecasts = {
        "arima":   forecast_arima(commodity, N_WEEKS),
        "prophet": forecast_prophet(commodity, future_df, hist_df),
        "xgboost": forecast_xgboost(commodity, hist_df, future_df, N_WEEKS),
    }

    n_available = sum(1 for f in model_forecasts.values() if f is not None)
    logger.debug(f"  [{commodity}] {n_available}/3 models generated forecasts")

    # Blend
    ensemble = blend_forecasts(model_forecasts, weights, N_WEEKS)
    if ensemble is None:
        logger.warning(f"  [{commodity}] No forecasts available — skipping")
        return None

    # Build per-horizon outputs
    future_dates = list(future_df["date"])
    horizons     = {}

    for horizon_name, horizon_days in HORIZONS.items():
        n_h = max(1, round(horizon_days / 7))  # convert days to weeks

        h_dates  = [str(d.date()) if hasattr(d,"date") else str(d)
                    for d in future_dates[:n_h]]
        h_vals   = ensemble["values"][:n_h]
        h_lower  = ensemble["lower_ci"][:n_h]
        h_upper  = ensemble["upper_ci"][:n_h]

        # Per-model values for this horizon
        per_model = {}
        for m, fc in model_forecasts.items():
            if fc is not None and len(fc.get("values",[])) >= n_h:
                per_model[m] = {
                    "values":   [round(v,2) for v in fc["values"][:n_h]],
                    "lower_ci": [round(v,2) for v in fc.get("lower_ci",fc["values"])[:n_h]],
                    "upper_ci": [round(v,2) for v in fc.get("upper_ci",fc["values"])[:n_h]],
                }

        # Price change report for this horizon
        price_changes = compute_price_changes(
            commodity, last_price,
            h_vals,
            future_dates[:n_h]
        )

        horizons[horizon_name] = {
            "n_weeks":       n_h,
            "dates":         h_dates,
            "ensemble": {
                "values":   [round(v,2) for v in h_vals],
                "lower_ci": [round(v,2) for v in h_lower],
                "upper_ci": [round(v,2) for v in h_upper],
                "models_used":  ensemble.get("models_used",[]),
                "weights_used": ensemble.get("weights_used",{}),
            },
            "per_model":     per_model,
            "price_changes": price_changes,
            # Summary: last point of horizon
            "forecast_end": {
                "date":  h_dates[-1] if h_dates else None,
                "price": round(h_vals[-1],2) if h_vals else None,
                "pct_change_from_today": round(
                    (h_vals[-1]-last_price)/last_price*100, 2
                ) if h_vals and last_price > 0 else None,
            }
        }

    result = {
        "commodity":         commodity,
        "run_date":          str(run_date.date()),
        "last_known_price":  round(last_price, 2),
        "last_known_date":   str(last_date.date()),
        "forecast_start":    str(forecast_start.date()),
        "currency":          "NGN",
        "unit":              "NGN/MT",
        "horizons":          horizons,
    }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_forecasting():
    run_date     = datetime.now()
    date_str     = run_date.strftime("%Y-%m-%d")
    all_forecasts = {}

    logger.info("=" * 60)
    logger.info("STEP 5 — FORECASTING ENGINE")
    logger.info(f"  Run date  : {run_date.strftime('%A, %d %B %Y')}")
    logger.info(f"  Commodities: {len(COMMODITIES)}")
    logger.info(f"  Horizons  : {list(HORIZONS.keys())}")
    logger.info("=" * 60)

    for i, commodity in enumerate(COMMODITIES, 1):
        logger.info(f"\n[{i}/{len(COMMODITIES)}] Forecasting {commodity}...")
        try:
            result = forecast_commodity(commodity, run_date)
            if result:
                all_forecasts[commodity] = result
                # Print summary per horizon
                for h_name, h_data in result["horizons"].items():
                    end = h_data["forecast_end"]
                    pct = end.get("pct_change_from_today",0) or 0
                    dir_symbol = "↑" if pct > 0 else ("↓" if pct < 0 else "→")
                    logger.info(
                        f"  {h_name:<12} → "
                        f"₦{end['price']:>13,.0f}/MT  "
                        f"{dir_symbol} {pct:+.1f}%  "
                        f"({end['date']})"
                    )
                logger.info(f"  ✅ {commodity}")
        except Exception as e:
            logger.error(f"  ❌ {commodity}: {e}")
            import traceback; traceback.print_exc()

    # ── Save forecast JSON ────────────────────────────────────────────────────
    json_path = os.path.join(FORECASTS_DIR, f"forecast_{date_str}.json")
    with open(json_path, "w") as f:
        json.dump(all_forecasts, f, indent=2, default=str)
    logger.info(f"\n  Forecast JSON saved → {json_path}")

    # ── Daily alert ───────────────────────────────────────────────────────────
    alert_text = generate_daily_alert(all_forecasts, run_date)
    alert_path = os.path.join(PATHS["daily_alerts_dir"],
                              f"alert_{date_str}.txt")
    with open(alert_path, "w", encoding="utf-8") as f:
        f.write(alert_text)
    logger.info(f"  Daily alert saved → {alert_path}")

    # ── Print the alert ───────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("  DAILY PRICE ALERT")
    logger.info("=" * 60)
    for line in alert_text.split("\n"):
        logger.info(f"  {line}")

    # ── Append to master ──────────────────────────────────────────────────────
    # validate.py (Step 6) appends CORRECTED validated prices to master.
    logger.info("\n  Note: master append handled by 06_validate.py (corrected prices).")
    n_appended = 0

    # ── Final summary table ───────────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  FORECAST SUMMARY — ALL COMMODITIES")
    logger.info("=" * 70)
    logger.info(
        f"  {'Commodity':<20} {'Today':>14} "
        f"{'1 Week':>14} {'1 Month':>14} "
        f"{'3 Months':>14} {'6 Months':>14}"
    )
    logger.info(f"  {'-'*82}")
    for commodity in COMMODITIES:
        if commodity not in all_forecasts:
            continue
        fc  = all_forecasts[commodity]
        hs  = fc["horizons"]
        def end_price(h):
            return hs.get(h,{}).get("forecast_end",{}).get("price") or 0
        today_p   = end_price("daily")
        week_p    = end_price("weekly")
        month_p   = end_price("monthly")
        three_p   = end_price("3_months")
        six_p     = end_price("6_months")
        logger.info(
            f"  {commodity:<20} "
            f"₦{today_p:>12,.0f}  "
            f"₦{week_p:>12,.0f}  "
            f"₦{month_p:>12,.0f}  "
            f"₦{three_p:>12,.0f}  "
            f"₦{six_p:>12,.0f}"
        )

    logger.info("=" * 70)
    logger.success(
        f"FORECASTING COMPLETE — "
        f"{len(all_forecasts)}/{len(COMMODITIES)} commodities"
    )
    logger.info("=" * 70)

    return all_forecasts


if __name__ == "__main__":
    run_forecasting()
