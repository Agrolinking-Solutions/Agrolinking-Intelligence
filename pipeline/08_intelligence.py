"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 8: Intelligence Layer

Computes all derived intelligence metrics from validated forecast
and zonal output. Runs after Step 7 daily.

Outputs: outputs/intelligence/intelligence_YYYY-MM-DD.json

Metrics computed:
  - Price per KG and per unit (Eggs = per crate)
  - Food Price Index (weighted basket, base 2025=100)
  - Volatility Index per commodity + aggregate
  - 30-Day Outlook score (aggregate + per commodity)
  - Model confidence score per commodity + aggregate
  - Biggest riser and faller today
  - Early warning alert status (WFP ALPS thresholds)
  - Shortage/surplus score per commodity per zone (0-100)
  - State price high/low spread per commodity
  - Seasonality score per commodity per month
"""

import os, sys, json, glob
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from loguru import logger

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

# pipeline/ is one level below project root — go up one directory
BASE           = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAL_DIR        = os.path.join(BASE, "outputs", "forecasts", "validated")
ZONAL_DIR      = os.path.join(BASE, "outputs", "forecasts", "zonal")
INTEL_DIR      = os.path.join(BASE, "outputs", "intelligence")
MASTER_PATH    = os.path.join(BASE, "data", "processed", "agrolinking_master.csv")
SEASON_PATH    = os.path.join(BASE, "data", "external", "season_calendar.csv")
FEATURES_DIR   = os.path.join(BASE, "data", "processed", "features")
os.makedirs(INTEL_DIR, exist_ok=True)

try:
    sys.path.insert(0, BASE)
    from config.settings import COMMODITIES
except Exception:
    COMMODITIES = [
        "Hibiscus","Sesame","Ginger","Cocoa","Soybeans","Cashew Nuts",
        "Sorghum","Beans (white)","Beans (red)","Maize (white)",
        "Maize (yellow)","Wheat","Rice",
        "Meat (beef)","Meat (goat)","Fish (dried)","Eggs",
    ]

# ── Unit configuration ────────────────────────────────────────────────────────
UNIT_CONFIG = {
    # commodity: (display_unit, divisor_from_MT, unit_label)
    "Eggs":          ("crate",  1000/7.2,   "NGN/crate"),   # ~7.2 crates per kg equivalent
    "Meat (beef)":   ("kg",     1000,        "NGN/kg"),
    "Meat (goat)":   ("kg",     1000,        "NGN/kg"),
    "Fish (dried)":  ("kg",     1000,        "NGN/kg"),
    "default":       ("kg",     1000,        "NGN/kg"),
}

# ── Commodity basket weights for Food Price Index ─────────────────────────────
# Based on Nigerian household consumption expenditure share (NBS 2023/2024)
# Weights sum to 1.0 across all 17 commodities
FPI_WEIGHTS = {
    "Maize (white)":   0.12,
    "Maize (yellow)":  0.08,
    "Rice":            0.14,
    "Wheat":           0.07,
    "Sorghum":         0.06,
    "Beans (white)":   0.07,
    "Beans (red)":     0.05,
    "Soybeans":        0.04,
    "Ginger":          0.03,
    "Hibiscus":        0.02,
    "Sesame":          0.03,
    "Cocoa":           0.03,
    "Cashew Nuts":     0.02,
    "Meat (beef)":     0.10,
    "Meat (goat)":     0.06,
    "Fish (dried)":    0.05,
    "Eggs":            0.03,
}
# Base prices (June 2025 = 100 baseline, NGN/MT equivalent)
FPI_BASE_2025 = {
    "Maize (white)":   285_000,
    "Maize (yellow)":  310_000,
    "Rice":            1_200_000,
    "Wheat":           580_000,
    "Sorghum":         320_000,
    "Beans (white)":   650_000,
    "Beans (red)":     730_000,
    "Soybeans":        580_000,
    "Ginger":          9_500_000,
    "Hibiscus":        1_900_000,
    "Sesame":          1_300_000,
    "Cocoa":           4_800_000,
    "Cashew Nuts":     1_600_000,
    "Meat (beef)":     3_200_000,
    "Meat (goat)":     3_400_000,
    "Fish (dried)":    2_100_000,
    "Eggs":            5_500,
}

# ── WFP ALPS alert thresholds ─────────────────────────────────────────────────
ALPS_THRESHOLDS = {
    "Severe": 25,   # price > 25% above 3-month average
    "High":   15,   # price > 15% above 3-month average
    "Watch":   5,   # price > 5%  above 3-month average
}

# ── Freight cost assumptions (NGN per kg per 100km) ──────────────────────────
FREIGHT_NGN_PER_KG_PER_100KM = 12.0   # based on diesel haulage rates June 2026

# State capital distances from Kano (km) - major sourcing hub
STATE_DISTANCES_FROM_KANO = {
    "Kano":    0, "Kaduna":  185, "Plateau":  420, "Kogi":    480,
    "Adamawa": 680, "Borno":  580, "Oyo":     870, "Lagos":   1050,
    "Anambra": 890, "Imo":   950, "Rivers":  1100, "Delta":   950,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_latest(directory, pattern):
    files = sorted(glob.glob(os.path.join(directory, pattern)))
    if not files:
        return None, None
    with open(files[-1]) as f:
        return json.load(f), os.path.basename(files[-1])


def price_per_unit(commodity, price_ngn_mt):
    """Convert NGN/MT to NGN/kg or NGN/crate depending on commodity."""
    if commodity == "Eggs":
        # Eggs: price_ngn_mt field actually stores NGN/crate in our system
        return round(price_ngn_mt, 0), "NGN/crate"
    else:
        return round(price_ngn_mt / 1000, 2), "NGN/kg"


def compute_food_price_index(national_anchors):
    """Compute weighted Food Price Index. Base 2025 = 100."""
    weighted_ratio = 0.0
    total_weight   = 0.0
    breakdown      = {}

    for commodity, weight in FPI_WEIGHTS.items():
        current_price = national_anchors.get(commodity, {}).get("price", 0)
        base_price    = FPI_BASE_2025.get(commodity, 0)
        if current_price > 0 and base_price > 0:
            ratio = current_price / base_price
            weighted_ratio += ratio * weight
            total_weight   += weight
            breakdown[commodity] = {
                "weight":        weight,
                "base_price":    base_price,
                "current_price": current_price,
                "price_ratio":   round(ratio, 4),
                "contribution":  round(ratio * weight, 4),
            }

    if total_weight > 0:
        fpi = round((weighted_ratio / total_weight) * 100, 1)
    else:
        fpi = 100.0

    return fpi, breakdown


def compute_volatility(run_date, window_days=30):
    """
    Compute 30-day rolling price volatility per commodity
    from master CSV. Returns coefficient of variation (CV) as %.
    """
    try:
        master = pd.read_csv(MASTER_PATH, parse_dates=["date"])
        cutoff = run_date - timedelta(days=window_days)
        recent = master[master["date"] >= cutoff]

        volatility = {}
        for commodity in COMMODITIES:
            sub = recent[recent["commodity"] == commodity]["price_ngn_mt"].dropna()
            if len(sub) >= 3:
                cv = (sub.std() / sub.mean() * 100) if sub.mean() > 0 else 0
                volatility[commodity] = round(cv, 2)
            else:
                volatility[commodity] = None

        # Aggregate volatility index = weighted average of commodity CVs
        valid = [(COMMODITIES.index(c) if c in COMMODITIES else 0,
                  FPI_WEIGHTS.get(c, 0.05), v)
                 for c, v in volatility.items() if v is not None]
        if valid:
            total_w = sum(w for _, w, _ in valid)
            agg_vol = sum(w * v for _, w, v in valid) / total_w if total_w > 0 else 0
        else:
            agg_vol = 0

        # Leading commodity (highest volatility)
        if volatility:
            leader = max((c for c in volatility if volatility[c] is not None),
                         key=lambda c: volatility[c] or 0, default=None)
        else:
            leader = None

        return round(agg_vol, 1), leader, volatility

    except Exception as e:
        logger.warning(f"  Volatility computation failed: {e}")
        return 0.0, None, {}


def compute_30day_outlook(validated_forecast):
    """
    Aggregate 30-day outlook from monthly horizon forecasts.
    Returns overall % change + per commodity.
    """
    outlooks  = {}
    total_pct = 0.0
    count     = 0

    for commodity in COMMODITIES:
        fc = validated_forecast.get(commodity, {})
        monthly = fc.get("horizons", {}).get("monthly", {})
        detail  = monthly.get("forecast_end_detail", {})
        pct     = detail.get("pct_change_from_today")
        if pct is None:
            vals = monthly.get("ensemble", {}).get("values", [])
            lkp  = fc.get("last_known_price", 0)
            if vals and lkp > 0:
                pct = round((vals[-1] - lkp) / lkp * 100, 2)

        if pct is not None:
            # Cap at realistic 30-day movement limits for Nigerian commodities
            # Grains: max +/-15% in 30 days
            # Export crops: max +/-25% in 30 days
            # Livestock: max +/-10% in 30 days
            GRAIN_CAPS    = ["Maize (white)","Maize (yellow)","Sorghum","Wheat","Rice",
                             "Beans (white)","Beans (red)","Soybeans"]
            LIVESTOCK     = ["Meat (beef)","Meat (goat)","Fish (dried)","Eggs"]
            if commodity in GRAIN_CAPS:
                max_move = 15.0
            elif commodity in LIVESTOCK:
                max_move = 10.0
            else:
                max_move = 25.0

            pct_capped = max(-max_move, min(max_move, float(pct)))

            outlooks[commodity] = {
                "pct_change_30d": round(pct_capped, 2),
                "direction":      "up" if pct_capped > 0 else "down",
                "signal":         "bullish" if pct_capped > 3 else
                                  "bearish" if pct_capped < -3 else "neutral",
                "model_raw_pct":  round(float(pct), 2),
                "capped":         abs(float(pct)) > max_move,
            }
            total_pct += pct_capped
            count += 1

    avg_outlook = round(total_pct / count, 2) if count > 0 else 0.0
    return avg_outlook, outlooks


def compute_confidence_scores(run_date):
    """
    Derive model confidence directly from the validated forecast JSON.
    Confidence = 100 - error_after_pct, floored at 0, capped at 99.
    Reads from the validated forecast file, not a separate report file.
    """
    # Load latest validated forecast
    val_files = sorted(glob.glob(os.path.join(VAL_DIR, "forecast_validated_*.json")))
    if not val_files:
        logger.warning("  No validated forecast found for confidence scores")
        return 0, {}

    with open(val_files[-1]) as f:
        validated = json.load(f)

    scores = {}
    for commodity, fc_data in validated.items():
        vld = fc_data.get("validation", {})
        # Try multiple field names for error_after
        err = (vld.get("error_after_pct") or
               vld.get("error_pct_after") or
               vld.get("error_after") or 0)
        err = float(err) if err else 0
        within = vld.get("within_target", False)

        # Only include commodities with real validation data
        if err == 0 and not within and not vld:
            continue

        score = round(max(0, min(99, 100 - err)), 1)
        scores[commodity] = {
            "confidence_pct":  score,
            "error_after_pct": round(err, 2),
            "within_target":   within,
            "grade":           "A" if score >= 95 else
                               "B" if score >= 90 else
                               "C" if score >= 80 else "D",
        }

    avg_confidence = round(
        sum(v["confidence_pct"] for v in scores.values()) / len(scores), 1
    ) if scores else 0

    return avg_confidence, scores


def compute_alert_status(national_anchors, validated_forecast, run_date):
    """
    WFP ALPS-style alert thresholds.
    Compare current price to 3-month rolling average from master CSV.
    Severe: >25% above average
    High:   >15% above average
    Watch:  >5%  above average
    Normal: within 5% of average
    """
    try:
        master = pd.read_csv(MASTER_PATH, parse_dates=["date"])
        cutoff = run_date - timedelta(days=90)
        recent = master[master["date"] >= cutoff]
    except Exception:
        recent = pd.DataFrame()

    alerts = {}
    for commodity in COMMODITIES:
        current = national_anchors.get(commodity, {}).get("price", 0)
        if current <= 0:
            continue

        # 3-month average from master
        sub = recent[recent["commodity"] == commodity]["price_ngn_mt"].dropna()
        if len(sub) >= 3:
            avg_3m = sub.mean()
            pct_above = (current - avg_3m) / avg_3m * 100
        else:
            # Fall back to reference price
            ref = national_anchors.get(commodity, {}).get("ref_price", current)
            avg_3m    = ref
            pct_above = (current - ref) / ref * 100 if ref > 0 else 0

        if pct_above >= ALPS_THRESHOLDS["Severe"]:
            level = "Severe"
            color = "#FF0000"
        elif pct_above >= ALPS_THRESHOLDS["High"]:
            level = "High"
            color = "#FF8C00"
        elif pct_above >= ALPS_THRESHOLDS["Watch"]:
            level = "Watch"
            color = "#FFD700"
        elif pct_above <= -ALPS_THRESHOLDS["Watch"]:
            level = "Below Average"
            color = "#2196F3"
        else:
            level = "Normal"
            color = "#4CAF50"

        alerts[commodity] = {
            "alert_level":     level,
            "color":           color,
            "current_price":   current,
            "avg_3m_price":    round(avg_3m, 0),
            "pct_vs_avg_3m":   round(pct_above, 2),
            "threshold_used":  "WFP ALPS",
        }

    # Summary
    severe_count = sum(1 for a in alerts.values() if a["alert_level"] == "Severe")
    high_count   = sum(1 for a in alerts.values() if a["alert_level"] == "High")
    watch_count  = sum(1 for a in alerts.values() if a["alert_level"] == "Watch")

    return alerts, {
        "severe": severe_count,
        "high":   high_count,
        "watch":  watch_count,
        "normal": len(alerts) - severe_count - high_count - watch_count,
    }


def compute_shortage_surplus_scores(zonal_data, national_anchors, run_date):
    """
    Compute shortage/surplus score per commodity per zone (0-100).
    0   = severe shortage  (high price, low season, rising trend)
    50  = balanced
    100 = strong surplus   (low price, harvest season, falling trend)

    Components:
      40% price signal    (vs 3-month average)
      30% season position (from season_calendar.csv)
      30% trend direction (30-day outlook)
    """
    # Load season calendar
    season_scores = {}
    try:
        season = pd.read_csv(SEASON_PATH)
        current_month = run_date.month
        month_col = season.columns[current_month]   # month columns 1-12
        for _, row in season.iterrows():
            comm = row.get("commodity", "")
            val  = row.get(month_col, 50)
            try:
                season_scores[comm] = float(val)
            except Exception:
                season_scores[comm] = 50.0
    except Exception:
        pass

    scores = {}
    for commodity in COMMODITIES:
        anchor = national_anchors.get(commodity, {})
        current_price = anchor.get("price", 0)
        ref_price     = anchor.get("ref_price", current_price)
        day_chg       = anchor.get("day_change", 0)

        if current_price <= 0:
            continue

        # Price signal: below average = surplus (high score), above = shortage (low score)
        if ref_price > 0:
            price_ratio = current_price / ref_price
            price_score = max(0, min(100, 100 - (price_ratio - 1) * 200))
        else:
            price_score = 50.0

        # Season score (higher = more harvest availability)
        season_score = season_scores.get(commodity, 50.0)

        # Trend score: falling price = surplus signal
        trend_score = max(0, min(100, 50 - day_chg * 10))

        composite = round(
            price_score * 0.40 +
            season_score * 0.30 +
            trend_score  * 0.30, 1
        )

        if composite >= 65:
            label = "Surplus"
        elif composite >= 45:
            label = "Balanced"
        elif composite >= 30:
            label = "Tight"
        else:
            label = "Shortage"

        scores[commodity] = {
            "score":         composite,
            "label":         label,
            "price_signal":  round(price_score, 1),
            "season_signal": round(season_score, 1),
            "trend_signal":  round(trend_score, 1),
        }

    return scores


def compute_seasonality_profile():
    """
    Output seasonality score per commodity per month (1-12).
    Uses season_calendar.csv.
    Score: 100 = peak harvest (cheapest), 0 = lean season (most expensive).
    """
    try:
        season = pd.read_csv(SEASON_PATH)
        profile = {}
        for _, row in season.iterrows():
            comm = row.get("commodity", "")
            if not comm:
                continue
            monthly = {}
            for m in range(1, 13):
                col = str(m) if str(m) in season.columns else season.columns[m] if m < len(season.columns) else None
                if col:
                    try:
                        monthly[m] = float(row[col])
                    except Exception:
                        monthly[m] = 50.0
                else:
                    monthly[m] = 50.0
            profile[comm] = monthly
        return profile
    except Exception as e:
        logger.warning(f"  Seasonality profile failed: {e}")
        return {}


def compute_arbitrage(zonal_data, national_anchors):
    """
    Compute net arbitrage per kg after freight for each commodity.
    Arbitrage = (destination_price - source_price) - freight_cost
    """
    arbitrage = {}
    for commodity in COMMODITIES:
        state_prices = {}
        for zone, zone_data in zonal_data.get("zones", {}).items():
            for state, state_data in zone_data.get("states", {}).items():
                p = state_data.get(commodity, {}).get("state_price", 0)
                if p > 0:
                    state_prices[state] = p

        if len(state_prices) < 2:
            continue

        best_source = min(state_prices, key=state_prices.get)
        best_dest   = max(state_prices, key=state_prices.get)
        gross_arb   = (state_prices[best_dest] - state_prices[best_source]) / 1000  # per kg

        # Freight cost estimate
        d_source = STATE_DISTANCES_FROM_KANO.get(best_source, 500)
        d_dest   = STATE_DISTANCES_FROM_KANO.get(best_dest, 500)
        distance = abs(d_dest - d_source) if d_dest != d_source else max(d_source, d_dest)
        freight_per_kg = FREIGHT_NGN_PER_KG_PER_100KM * distance / 100

        net_arb = gross_arb - freight_per_kg
        spread_pct = round((state_prices[best_dest] - state_prices[best_source])
                           / state_prices[best_source] * 100, 1)

        arbitrage[commodity] = {
            "source_state":         best_source,
            "source_price_ngn_mt":  state_prices[best_source],
            "source_price_ngn_kg":  round(state_prices[best_source] / 1000, 2),
            "destination_state":    best_dest,
            "dest_price_ngn_mt":    state_prices[best_dest],
            "dest_price_ngn_kg":    round(state_prices[best_dest] / 1000, 2),
            "gross_arbitrage_ngn_kg": round(gross_arb, 2),
            "distance_km":          distance,
            "freight_cost_ngn_kg":  round(freight_per_kg, 2),
            "net_arbitrage_ngn_kg": round(net_arb, 2),
            "spread_pct":           spread_pct,
            "viable":               net_arb > 0,
        }

    return arbitrage


def compute_state_spread(zonal_data):
    """State price high vs low spread % per commodity."""
    spreads = {}
    for commodity in COMMODITIES:
        prices = {}
        for zone, zone_data in zonal_data.get("zones", {}).items():
            for state, state_data in zone_data.get("states", {}).items():
                p = state_data.get(commodity, {}).get("state_price", 0)
                if p > 0:
                    prices[state] = p
        if len(prices) >= 2:
            hi = max(prices.values())
            lo = min(prices.values())
            spreads[commodity] = {
                "high_state":   max(prices, key=prices.get),
                "high_price":   hi,
                "low_state":    min(prices, key=prices.get),
                "low_price":    lo,
                "spread_pct":   round((hi - lo) / lo * 100, 1),
            }
    return spreads


def compute_price_per_unit(national_anchors):
    """Add NGN/kg and unit-specific pricing to national anchors."""
    result = {}
    for commodity, anchor in national_anchors.items():
        price_mt = anchor.get("price", 0)
        per_unit, unit_label = price_per_unit(commodity, price_mt)
        result[commodity] = {
            **anchor,
            "price_ngn_kg":    round(price_mt / 1000, 2) if commodity != "Eggs" else None,
            "price_per_unit":  per_unit,
            "unit_label":      unit_label,
        }
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def run_intelligence():
    run_date = datetime.now()
    date_str = run_date.strftime("%Y-%m-%d")

    logger.info("=" * 65)
    logger.info("STEP 8 — INTELLIGENCE LAYER")
    logger.info(f"  Date: {run_date.strftime('%A, %d %B %Y')}")
    logger.info("=" * 65)

    # Load validated forecast and zonal output
    validated, vf = load_latest(VAL_DIR, "forecast_validated_*.json")
    zonal,     zf = load_latest(ZONAL_DIR, "zonal_forecast_*.json")

    if not validated:
        logger.error("  No validated forecast found. Run steps 5-7 first.")
        return {}
    if not zonal:
        logger.warning("  No zonal forecast found. Some metrics will be incomplete.")
        zonal = {}

    logger.info(f"  Validated: {vf}")
    logger.info(f"  Zonal:     {zf}")

    national_anchors = zonal.get("national_anchors", {})

    # ── 1. Price per unit ────────────────────────────────────────────────────
    logger.info("  Computing price per unit...")
    prices_with_units = compute_price_per_unit(national_anchors)

    # ── 2. Food Price Index ──────────────────────────────────────────────────
    logger.info("  Computing Food Price Index...")
    fpi, fpi_breakdown = compute_food_price_index(national_anchors)
    logger.info(f"    FPI: {fpi} (base 2025=100)")

    # MoM change: compare to 30 days ago
    fpi_30d_ago = None
    try:
        old_date  = (run_date - timedelta(days=30)).strftime("%Y-%m-%d")
        old_files = sorted(glob.glob(os.path.join(INTEL_DIR, "intelligence_*.json")))
        for f in reversed(old_files):
            if old_date[:7] in f:  # same month last month
                with open(f) as fh:
                    old = json.load(fh)
                fpi_30d_ago = old.get("food_price_index", {}).get("value")
                break
    except Exception:
        pass

    fpi_mom_change = round(fpi - fpi_30d_ago, 1) if fpi_30d_ago else None

    # ── 3. Volatility Index ──────────────────────────────────────────────────
    logger.info("  Computing volatility index...")
    vol_index, vol_leader, vol_per_commodity = compute_volatility(run_date)
    logger.info(f"    Volatility Index: {vol_index} | Leader: {vol_leader}")

    # ── 4. 30-Day Outlook ────────────────────────────────────────────────────
    logger.info("  Computing 30-day outlook...")
    outlook_avg, outlook_per_commodity = compute_30day_outlook(validated)
    logger.info(f"    30-Day Outlook: {outlook_avg:+.2f}%")

    # ── 5. Model Confidence ──────────────────────────────────────────────────
    logger.info("  Computing model confidence scores...")
    avg_confidence, confidence_per_commodity = compute_confidence_scores(run_date)
    logger.info(f"    Avg Confidence: {avg_confidence}%")

    # ── 6. Biggest riser / faller ────────────────────────────────────────────
    logger.info("  Computing biggest riser/faller...")
    valid_anchors = {c: v for c, v in national_anchors.items() if v.get("day_change") is not None}
    if valid_anchors:
        riser_name = max(valid_anchors, key=lambda c: valid_anchors[c]["day_change"])
        faller_name= min(valid_anchors, key=lambda c: valid_anchors[c]["day_change"])
        riser = {
            "commodity":      riser_name,
            "price_ngn_mt":   valid_anchors[riser_name]["price"],
            "price_ngn_kg":   round(valid_anchors[riser_name]["price"] / 1000, 2),
            "day_change_pct": valid_anchors[riser_name]["day_change"],
        }
        faller = {
            "commodity":      faller_name,
            "price_ngn_mt":   valid_anchors[faller_name]["price"],
            "price_ngn_kg":   round(valid_anchors[faller_name]["price"] / 1000, 2),
            "day_change_pct": valid_anchors[faller_name]["day_change"],
        }
    else:
        riser = faller = {}

    logger.info(f"    Riser: {riser.get('commodity')} {riser.get('day_change_pct',0):+.2f}%")
    logger.info(f"    Faller: {faller.get('commodity')} {faller.get('day_change_pct',0):+.2f}%")

    # ── 7. Early warning alerts ──────────────────────────────────────────────
    logger.info("  Computing early warning alerts...")
    alerts, alert_summary = compute_alert_status(national_anchors, validated, run_date)
    logger.info(f"    Alerts: {alert_summary}")

    # ── 8. Shortage/surplus scores ───────────────────────────────────────────
    logger.info("  Computing shortage/surplus scores...")
    shortage_scores = compute_shortage_surplus_scores(zonal, national_anchors, run_date)

    # ── 9. Seasonality profile ───────────────────────────────────────────────
    logger.info("  Computing seasonality profiles...")
    seasonality = compute_seasonality_profile()

    # ── 10. State spread ─────────────────────────────────────────────────────
    logger.info("  Computing state price spreads...")
    state_spreads = compute_state_spread(zonal)

    # ── 11. Arbitrage ────────────────────────────────────────────────────────
    logger.info("  Computing net arbitrage per kg...")
    arbitrage = compute_arbitrage(zonal, national_anchors)

    # ── Assemble output ───────────────────────────────────────────────────────
    output = {
        "run_date":      date_str,
        "generated_at":  run_date.isoformat(),
        "source_validated": vf,
        "source_zonal":     zf,

        "food_price_index": {
            "value":          fpi,
            "base":           "2025=100",
            "mom_change":     fpi_mom_change,
            "interpretation": "above average" if fpi > 105 else
                              "below average" if fpi < 95 else "normal",
            "breakdown":      fpi_breakdown,
        },

        "volatility_index": {
            "value":           vol_index,
            "leading_commodity": vol_leader,
            "interpretation":  "high" if vol_index > 15 else
                               "moderate" if vol_index > 8 else "low",
            "per_commodity":   vol_per_commodity,
        },

        "outlook_30d": {
            "avg_pct_change": outlook_avg,
            "direction":      "up" if outlook_avg > 0 else "down",
            "signal":         "bullish" if outlook_avg > 3 else
                              "bearish" if outlook_avg < -3 else "neutral",
            "per_commodity":  outlook_per_commodity,
        },

        "model_confidence": {
            "avg_pct":       avg_confidence,
            "interpretation": "high" if avg_confidence >= 90 else
                              "moderate" if avg_confidence >= 75 else "low",
            "per_commodity":  confidence_per_commodity,
        },

        "market_movers": {
            "biggest_riser":  riser,
            "biggest_faller": faller,
        },

        "early_warning_alerts": {
            "summary":      alert_summary,
            "thresholds":   ALPS_THRESHOLDS,
            "per_commodity": alerts,
        },

        "shortage_surplus": {
            "per_commodity": shortage_scores,
            "scoring_note":  "0=severe shortage, 50=balanced, 100=strong surplus",
        },

        "seasonality_profiles": seasonality,

        "state_spreads":   state_spreads,

        "arbitrage":       arbitrage,

        "prices_with_units": prices_with_units,
    }

    # Save
    out_path = os.path.join(INTEL_DIR, f"intelligence_{date_str}.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.success(f"  Intelligence saved -> {out_path}")
    logger.info("")
    logger.info("  SUMMARY:")
    logger.info(f"    Food Price Index:    {fpi} (base 2025=100)"
                + (f", {fpi_mom_change:+.1f} MoM" if fpi_mom_change else ""))
    logger.info(f"    Volatility Index:    {vol_index} (leader: {vol_leader})")
    logger.info(f"    30-Day Outlook:      {outlook_avg:+.2f}%")
    logger.info(f"    Model Confidence:    {avg_confidence}%")
    logger.info(f"    Biggest Riser:       {riser.get('commodity')} "
                f"{riser.get('day_change_pct',0):+.2f}%")
    logger.info(f"    Biggest Faller:      {faller.get('commodity')} "
                f"{faller.get('day_change_pct',0):+.2f}%")
    logger.info(f"    Alerts - Severe:{alert_summary['severe']} "
                f"High:{alert_summary['high']} Watch:{alert_summary['watch']}")
    logger.success("STEP 8 COMPLETE")

    return output


if __name__ == "__main__":
    run_intelligence()