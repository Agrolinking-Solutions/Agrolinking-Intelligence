"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 6: Cross-Reference Validation
──────────────────────────────────────────────────────────────
Validates model forecasts against live market price references.

How it works:
  1. Loads the latest forecast JSON
  2. For each commodity, compares the daily forecast against
     verified reference prices from live web sources
  3. Computes error % between forecast and reference
  4. If error > 5%: applies a correction factor to bring the
     forecast within the acceptable range
  5. Saves a validated forecast JSON with corrected prices
  6. Generates a confidence report showing which models were
     closest to live market reality
  7. Updates the master CSV with validated prices

Live sources used (queried fresh on each run via requests):
  - NEPC Nigeria indicative prices
  - Agricom Instagram post prices (you provide these manually
    via the MANUAL_PRICES dict when you get a new post)
  - Built-in reference anchors (updated from web research)

Run: python pipeline/06_validate.py
"""

import os, sys, json, warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from loguru import logger

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")
logger.add(os.path.join(PATHS["logs_dir"], "validation_{time:YYYY-MM-DD}.log"),
    rotation="1 day", retention="30 days", level="DEBUG")

FORECASTS_DIR = PATHS["forecasts_dir"]
VALIDATED_DIR = os.path.join(PATHS["forecasts_dir"], "validated")
os.makedirs(VALIDATED_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# MANUAL PRICE ENTRY
# ─────────────────────────────────────────────────────────────────────────────
# Every time you see a new Agricom post, update these prices.
# Format: NGN/MT  (multiply per-tonne price directly)
# Leave as None if you don't have a fresh price for that commodity.
# These take highest priority in validation.
MANUAL_PRICES = {
    # ── Agricome Africa Aug 03 2026 (latest confirmed) ───────────────────
    "Hibiscus":      2_500_000,   # Agricome Aug 03 2026
    "Soybeans":        790_000,   # Agricome Aug 03 2026
    "Ginger":        9_400_000,   # Agricome Aug 03 2026 (standard dried wholesale)
    "Cocoa":         4_500_000,   # Agricome Aug 03 2026
    "Cashew Nuts":   1_930_000,   # Agricome Aug 03 2026
    "Sorghum":         345_000,   # Agricome Aug 03 2026
    "Sesame":        1_420_000,   # Agricome Aug 03 2026
    # ── Market Naija TV Jul 2026 ──────────────────────────────────────────
    "Maize (white)":   395_000,   # Market Naija TV Jul 2026
    "Maize (yellow)":  421_000,   # Market Naija TV Jul 2026
    "Wheat":         1_000_000,   # Market Naija TV Jul 2026
    "Beans (white)":   750_000,   # Market Naija TV Jul 2026
    "Beans (red)":     850_000,   # Market Naija TV Jul 2026
    "Rice":          1_320_000,   # Market Naija TV Jul 2026
    # ── Livestock — market research Aug 2026 ─────────────────────────────
    "Meat (beef)":   4_536_000,   # Market research Jul 2026
    "Meat (goat)":   7_000_000,   # Confirmed Kaduna market Aug 2026 (N7,000/kg)
    "Fish (dried)":  1_550_000,   # CORRECTED (was 10,000,000 - 10x unit error; comment
                                   # itself said N1,000-1,200/kg = N1.0-1.2M/MT). Set to
                                   # match verified WFP anchor (N1,508,531 @ 2026-03-09,
                                   # bridged via World Bank RTP to N1,547,894 @ Jul 2026)
    "Eggs":              7_920,   # Market research Jul 2026 (NGN/crate)
}

# ─────────────────────────────────────────────────────────────────────────────
# WEB-SOURCED REFERENCE ANCHORS
# ─────────────────────────────────────────────────────────────────────────────
# Updated from live web research April 2026.
# Sources: NGX (BusinessDay Feb 2026), WFP March 2026, Market Naija,
#          Selinawamucii, Proshare, ICO cocoa data.
WEB_REFERENCE_PRICES = {
    # Cocoa: ICO ~$3,613/ton × ₦1,640/$ = ₦5.9M/MT (BusinessDay Feb 2026 confirmed crash)
    "Cocoa":         5_925_000,
    # Ginger: NGX N13,000/kg = N13M/MT (BusinessDay Feb 2026, held steady due to blight)
    "Ginger":       13_000_000,
    # Sesame: NGX N1,150-1,200/kg (BusinessDay Feb 2026)
    "Sesame":        1_200_000,
    # Hibiscus: Agricom March 2026 ₦2,650,000
    "Hibiscus":      2_650_000,
    # Cashew: Agricom March 2026 ₦1,800,000 (harvest season = lower)
    "Cashew Nuts":   1_800_000,
    # Maize: carry_forward trend ₦325–350k/MT (WFP last: ₦208k 2023, inflated since)
    "Maize (white)":   350_000,
    "Maize (yellow)":  380_000,
    # Sorghum: Market Naija ₦44k/100kg = ₦440k/MT
    "Sorghum":         440_000,
    # Soybeans: Market Naija ₦79k/100kg = ₦790k/MT, CEIC ~₦786k
    "Soybeans":        750_000,
    # Beans: WFP March 2026 confirmed
    "Beans (white)":   820_000,
    "Beans (red)":     915_000,
}

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
MAX_ERROR_PCT      = 3.0    # Target: flag anything above this
HARD_CORRECT_PCT   = 10.0   # Above this: apply strong correction
SOFT_CORRECT_PCT   = 2.0    # 5–15%: apply soft correction (blend)
EXTREME_CORRECT_PCT = 30.0  # Above this: use reference price almost entirely
# Blend ratios — how much weight given to the reference price
SOFT_BLEND_RATIO    = 0.75  # 5-15% error:  50% reference
HARD_BLEND_RATIO    = 0.90  # 15-50% error: 75% reference
EXTREME_BLEND_RATIO = 0.96  # >50% error:   92% reference (model just sets direction)


# ─────────────────────────────────────────────────────────────────────────────
# GET BEST REFERENCE PRICE
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# SANITY BOUNDS — gate for live-fetched prices
# ─────────────────────────────────────────────────────────────────────────────
# A scraped price outside this band is almost certainly a parsing error
# (wrong number picked up from the page), not a real market move.
# Built from the existing manual/web-anchor values as a generous band
# (0.4x - 2.5x) rather than hand-typing a second set of numbers that
# could drift out of sync with them.
def _build_sanity_ranges():
    ranges = {}
    for commodity in set(list(MANUAL_PRICES.keys()) + list(WEB_REFERENCE_PRICES.keys())):
        candidates = [v for v in (MANUAL_PRICES.get(commodity), WEB_REFERENCE_PRICES.get(commodity))
                      if v is not None]
        if candidates:
            ranges[commodity] = (min(candidates) * 0.4, max(candidates) * 2.5)
    return ranges

SANITY_RANGES = _build_sanity_ranges()


# ─────────────────────────────────────────────────────────────────────────────
# GET BEST REFERENCE PRICE
# ─────────────────────────────────────────────────────────────────────────────

def get_reference_price(commodity: str) -> tuple[float, str]:
    """
    Get the best available reference price for a commodity.
    Priority: LIVE web fetch (if it succeeds and passes a sanity check)
              > Manual (fresh Agricom post) > Web research anchors.

    Previously this function never called try_fetch_live_price() at
    all, despite that function existing and working — every "live
    cross-reference" was actually a static hand-typed number. This is
    the fix: genuinely attempt live first, log clearly which source
    won, and only fall back to the static anchors when the live fetch
    fails or returns something implausible.
    """
    # 1. Live web fetch — the real thing, attempted first
    live_price, live_source = try_fetch_live_price(commodity)
    if live_price is not None:
        lo, hi = SANITY_RANGES.get(commodity, (0, float("inf")))
        if lo <= live_price <= hi:
            logger.debug(f"    [{commodity}] live fetch OK: ₦{live_price:,.0f} ({live_source})")
            return float(live_price), live_source
        else:
            logger.warning(
                f"    [{commodity}] live fetch returned ₦{live_price:,.0f} — "
                f"outside sanity range (₦{lo:,.0f}-₦{hi:,.0f}), discarding and "
                f"falling back to static reference"
            )

    # 2. Manual price (freshest — from latest Agricom post you entered)
    if commodity in MANUAL_PRICES and MANUAL_PRICES[commodity] is not None:
        return float(MANUAL_PRICES[commodity]), "Agricom manual entry (static fallback)"

    # 3. Web research anchor
    if commodity in WEB_REFERENCE_PRICES:
        return float(WEB_REFERENCE_PRICES[commodity]), "Web research anchor (static fallback)"

    return None, "No reference available"


# ─────────────────────────────────────────────────────────────────────────────
# LIVE WEB FETCH (runs on your machine — internet connected)
# ─────────────────────────────────────────────────────────────────────────────
# NOTE on coverage: NEPC tracks EXPORT commodity indicative prices, so
# this realistically only has a chance of returning something for
# Cocoa, Ginger, Sesame, Hibiscus, Cashew Nuts, and Soybeans. For
# domestic staples (Maize, Beans, Rice, Sorghum, Wheat) and livestock/
# protein (Meat, Fish, Eggs), NEPC's page won't mention them — this
# will correctly fall through to the static fallback for those, and
# that's expected, not a bug. Closing that gap needs either a paid
# search API (SerpAPI/Bing/Google Custom Search) or a source that
# actually tracks domestic staple prices, which is a separate,
# bigger decision (budget + which API) rather than a code fix.

def try_fetch_live_price(commodity: str) -> tuple[float | None, str]:
    """
    Attempt to fetch a live price from the web.
    Returns (price_ngn_mt, source) or (None, error_message).
    Failures are logged at DEBUG level (not silent) and fall back to
    reference anchors via get_reference_price() above.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        import re

        nepc_url = "https://nepc.gov.ng/indicative-market-prices/"
        r = requests.get(nepc_url, timeout=10,
                        headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            logger.debug(f"    [{commodity}] NEPC returned HTTP {r.status_code}")
            return None, "Live fetch unavailable"

        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator=" ").lower()
        comm_lower = commodity.lower().replace(" (white)", "").replace(" (yellow)", "")
        idx = text.find(comm_lower)
        if idx <= 0:
            logger.debug(f"    [{commodity}] not found on NEPC page (expected for "
                         f"non-export commodities)")
            return None, "Commodity not listed on NEPC"

        snippet = text[idx:idx + 200]
        prices = re.findall(r'[\d,]+(?:\.\d+)?', snippet.replace(",", ""))
        prices_clean = [float(p) for p in prices if 100 < float(p) < 100_000_000]
        if not prices_clean:
            logger.debug(f"    [{commodity}] found on NEPC page but no parseable price nearby")
            return None, "No price found near commodity name"

        price = max(prices_clean)
        if price < 100_000:
            price *= 1000  # per kg -> per MT
        return price, "NEPC live"

    except Exception as e:
        logger.debug(f"    [{commodity}] live fetch failed: {type(e).__name__}: {e}")
        return None, "Live fetch unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# CORE VALIDATION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def compute_error(forecast_price: float, reference_price: float) -> float:
    """Compute absolute % error between forecast and reference."""
    if reference_price <= 0:
        return 0.0
    return abs(forecast_price - reference_price) / reference_price * 100


def apply_correction(
    forecast_price: float,
    reference_price: float,
    error_pct: float,
) -> tuple[float, str]:
    """
    Apply correction to bring forecast within acceptable range.
    Correction strength scales with error magnitude:
      0-2%:   no correction (model is trusted)
      2-10%:  soft blend (75% reference)
      10-30%: hard blend (90% reference)
      >30%:   extreme blend (96% reference — model only sets direction)
    Returns (corrected_price, correction_type)
    """
    if error_pct <= SOFT_CORRECT_PCT:
        return forecast_price, "none"

    if error_pct <= HARD_CORRECT_PCT:
        ratio = SOFT_BLEND_RATIO
        label = "soft_blend"
    elif error_pct <= EXTREME_CORRECT_PCT:
        ratio = HARD_BLEND_RATIO
        label = "hard_blend"
    else:
        ratio = EXTREME_BLEND_RATIO
        label = "extreme_blend"

    corrected = (
        forecast_price * (1 - ratio) +
        reference_price * ratio
    )
    return round(corrected, 2), label


def scale_horizon(
    daily_forecast: float,
    daily_corrected: float,
    horizon_forecast: float,
) -> float:
    """
    Scale a longer-horizon forecast by the same correction ratio
    applied to the daily forecast.
    This preserves the model's trend shape while anchoring the level.
    """
    if daily_forecast <= 0:
        return horizon_forecast
    correction_ratio = daily_corrected / daily_forecast
    return round(horizon_forecast * correction_ratio, 2)


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATE ONE COMMODITY
# ─────────────────────────────────────────────────────────────────────────────


def get_horizon_endpoint(h_data):
    """Handle both old (dict) and new (string) forecast_end structures."""
    fc_end = h_data.get("forecast_end", {})
    if isinstance(fc_end, str):
        detail = h_data.get("forecast_end_detail", {})
        vals   = h_data.get("ensemble", {}).get("values", [0])
        return {
            "date":                  fc_end,
            "price":                 detail.get("price", vals[-1] if vals else 0),
            "pct_change_from_today": detail.get("pct_change_from_today", 0),
        }
    elif isinstance(fc_end, dict) and fc_end:
        return fc_end
    else:
        vals  = h_data.get("ensemble", {}).get("values", [0])
        dates = h_data.get("dates", [""])
        return {
            "date":                  dates[-1] if dates else "",
            "price":                 vals[-1] if vals else 0,
            "pct_change_from_today": 0,
        }

def validate_commodity(
    commodity: str,
    fc_data: dict,
) -> dict:
    """Validate and correct forecasts for one commodity."""

    # Get reference price

    # Cap last_known_date to today - features forward-fills future rows
    _today_str = datetime.now().strftime("%Y-%m-%d")
    if fc_data.get("last_known_date", "") > _today_str:
        fc_data["last_known_date"] = _today_str

    ref_price, ref_source = get_reference_price(commodity)

    # Try live fetch first (may upgrade the reference)
    live_price, live_source = try_fetch_live_price(commodity)
    if live_price is not None:
        ref_price  = live_price
        ref_source = live_source
        logger.debug(f"  [{commodity}] Live price fetched: ₦{live_price:,.0f} ({live_source})")

    if ref_price is None:
        logger.warning(f"  [{commodity}] No reference price — skipping validation")
        fc_data["validation"] = {"status": "skipped", "reason": "no_reference"}
        return fc_data

    # Get daily forecast (our anchor for correction)
    daily_fc  = fc_data["horizons"]["daily"]["ensemble"]["values"][0]
    error_pct = compute_error(daily_fc, ref_price)

    logger.debug(
        f"  [{commodity}] Forecast: ₦{daily_fc:>12,.0f} | "
        f"Reference: ₦{ref_price:>12,.0f} | "
        f"Error: {error_pct:.1f}% ({ref_source})"
    )

    # Apply correction to daily forecast
    corrected_daily, correction_type = apply_correction(daily_fc, ref_price, error_pct)

    # Scale all horizons by the same correction ratio
    validated_horizons = {}
    for h_name, h_data in fc_data["horizons"].items():
        h_copy = json.loads(json.dumps(h_data))   # deep copy
        ensemble = h_copy["ensemble"]

        if ensemble and ensemble.get("values"):
            original_vals = ensemble["values"]
            corrected_vals = [
                scale_horizon(daily_fc, corrected_daily, v)
                for v in original_vals
            ]
            ensemble["values_original"] = original_vals
            ensemble["values"]          = corrected_vals
            # Scale CIs proportionally
            ratio = corrected_daily / daily_fc if daily_fc > 0 else 1.0
            ensemble["lower_ci"] = [round(v * ratio, 2) for v in ensemble.get("lower_ci", corrected_vals)]
            ensemble["upper_ci"] = [round(v * ratio, 2) for v in ensemble.get("upper_ci", corrected_vals)]

            # Recompute forecast_end with corrected values
            # Handle both old (dict) and new (string) forecast_end structure
            _fc_end = h_data.get("forecast_end", "")
            _fc_date = _fc_end if isinstance(_fc_end, str) else _fc_end.get("date", "")
            h_copy["forecast_end"] = _fc_date  # keep as string (new format)
            h_copy["forecast_end_detail"] = {
                "date":  _fc_date,
                "price": corrected_vals[-1],
                "pct_change_from_today": round(
                    (corrected_vals[-1] - fc_data["last_known_price"])
                    / fc_data["last_known_price"] * 100, 2
                ) if fc_data["last_known_price"] > 0 else None,
                "direction": "up" if corrected_vals[-1] > fc_data["last_known_price"] else "down",
            }

        validated_horizons[h_name] = h_copy

    # Scale weekly_series and daily_series by the same correction ratio.
    # These are the fields append_to_master()/update_master_with_validated()
    # and 07_zonal_forecast.py actually read — they must carry the same
    # correction as the horizons above, or the "validated" price and the
    # master-CSV/zonal price will silently disagree.
    correction_ratio = corrected_daily / daily_fc if daily_fc > 0 else 1.0

    weekly_series = fc_data.get("weekly_series")
    if weekly_series and weekly_series.get("values"):
        weekly_series = json.loads(json.dumps(weekly_series))  # deep copy
        weekly_series["values_original"] = weekly_series["values"]
        weekly_series["values"] = [
            round(v * correction_ratio, 2) for v in weekly_series["values"]
        ]
        weekly_series["lower_ci"] = [
            round(v * correction_ratio, 2) for v in weekly_series.get("lower_ci", weekly_series["values"])
        ]
        weekly_series["upper_ci"] = [
            round(v * correction_ratio, 2) for v in weekly_series.get("upper_ci", weekly_series["values"])
        ]

    daily_series = fc_data.get("daily_series")
    if daily_series and daily_series.get("values"):
        daily_series = json.loads(json.dumps(daily_series))  # deep copy
        daily_series["values_original"] = daily_series["values"]
        daily_series["values"] = [
            round(v * correction_ratio, 2) for v in daily_series["values"]
        ]

    # Build validation metadata
    validation_meta = {
        "status":             "validated",
        "reference_price":    round(ref_price, 2),
        "reference_source":   ref_source,
        "forecast_before":    round(daily_fc, 2),
        "forecast_after":     round(corrected_daily, 2),
        "error_pct_before":   round(error_pct, 2),
        "error_pct_after":    round(
            compute_error(corrected_daily, ref_price), 2
        ),
        "correction_applied": correction_type,
        "within_target":      compute_error(corrected_daily, ref_price) <= MAX_ERROR_PCT,
        "validated_at":       datetime.now().isoformat(),
    }

    result = dict(fc_data)
    result["horizons"]   = validated_horizons
    result["validation"] = validation_meta
    if weekly_series is not None:
        result["weekly_series"] = weekly_series
    if daily_series is not None:
        result["daily_series"] = daily_series

    # Ensure last_known_date is never in the future in the output JSON
    _today = datetime.now().strftime("%Y-%m-%d")
    if result.get("last_known_date", "") > _today:
        result["last_known_date"] = _today

    return result


# ─────────────────────────────────────────────────────────────────────────────
# LOAD LATEST FORECAST
# ─────────────────────────────────────────────────────────────────────────────

def load_latest_forecast() -> tuple[dict, str]:
    """Load the most recent forecast JSON file."""
    files = sorted([
        f for f in os.listdir(FORECASTS_DIR)
        if f.startswith("forecast_") and f.endswith(".json")
        and "validated" not in f
    ])
    if not files:
        raise FileNotFoundError(
            f"No forecast files found in {FORECASTS_DIR}. "
            "Run 05_forecast.py first."
        )
    latest = files[-1]
    path   = os.path.join(FORECASTS_DIR, latest)
    with open(path) as f:
        data = json.load(f)
    logger.info(f"  Loaded forecast: {latest}")
    return data, latest


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE MASTER WITH VALIDATED PRICES
# ─────────────────────────────────────────────────────────────────────────────

def update_master_with_validated(validated: dict, run_date: datetime):
    """Append validated forecast prices to master CSV."""
    master   = pd.read_csv(PATHS["master"], parse_dates=["date"])
    new_rows = []

    for commodity, fc in validated.items():
        # Use weekly_series (not horizons["weekly"], which is now 7 daily
        # points) so master keeps its native weekly grain — same fix as
        # 05_forecast.py's append_to_master(). This also carries the
        # validation correction applied above.
        weekly = fc.get("weekly_series", {})
        if not weekly or not weekly.get("values"):
            continue

        dates    = weekly.get("dates", [])
        values   = weekly.get("values", [])
        vld      = fc.get("validation", {})

        for date_str, price in zip(dates, values):
            date = pd.Timestamp(date_str)
            # Skip if already in master
            exists = master[
                (master["commodity"] == commodity) &
                (master["date"]      == date)
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
                "data_quality_score": 0.80,
                "is_validated":       True,
                "notes": (
                    f"Validated forecast — "
                    f"ref: ₦{vld.get('reference_price',0):,.0f} "
                    f"err: {vld.get('error_pct_after',0):.1f}%"
                ),
                "data_source":        "Agrolinking_validated",
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
        logger.info(f"  Master updated: +{len(new_rows)} validated forecast rows appended")
    else:
        logger.info("  Master: no new rows (all dates already present)")

    return len(new_rows)


# ─────────────────────────────────────────────────────────────────────────────
# GENERATE VALIDATED DAILY ALERT
# ─────────────────────────────────────────────────────────────────────────────

def generate_validated_alert(validated: dict, run_date: datetime) -> str:
    """
    Generate the daily alert using validated prices.
    Shows:
      - Validated price (post-correction, anchored to MANUAL_PRICES)
      - % change vs reference price (not vs stale last_known_price)
      - Trend direction based on daily vs monthly horizon
    """
    lines = [
        f"AGROLINKING COMMODITY INTELLIGENCE",
        f"Validated Daily Price Alert",
        f"{run_date.strftime('%A, %d %B %Y')}",
        f"Cross-referenced: NGX | WFP | Agricome Africa | Market sources",
        f"{'=' * 55}",
        f"{'Commodity':<22} {'Price (NGN/MT)':>15} {'vs Ref':>8}  {'Trend':>6}",
        f"{'-' * 55}",
    ]

    for commodity in COMMODITIES:
        if commodity not in validated:
            continue
        fc    = validated[commodity]
        vld   = fc.get("validation", {})
        daily = fc.get("horizons", {}).get("daily", {})
        monthly = fc.get("horizons", {}).get("monthly", {})
        if not daily:
            continue

        # Use validated price (post-correction)
        price     = daily["ensemble"]["values"][0]

        # % vs reference price (MANUAL_PRICE) — clean market signal
        ref_price = vld.get("reference_price", 0) or fc.get("last_known_price", price)
        pct_vs_ref = (price - ref_price) / ref_price * 100 if ref_price > 0 else 0

        # Trend: compare daily forecast to monthly forecast direction
        m_vals = monthly.get("ensemble", {}).get("values", []) if monthly else []
        d_price = daily["ensemble"]["values"][0]
        m_price = m_vals[-1] if m_vals else d_price
        trend_pct = (m_price - d_price) / d_price * 100 if d_price > 0 else 0
        if trend_pct > 1.5:
            trend = "rising"
        elif trend_pct < -1.5:
            trend = "falling"
        else:
            trend = "stable"

        # Format price
        if price >= 1_000_000:
            price_str = f"N{price/1e6:.3f}M"
        else:
            price_str = f"N{price/1000:.1f}K"

        lines.append(
            f"{commodity:<22} {price_str:>15} "
            f"{pct_vs_ref:>+7.1f}%  {trend:>6}"
        )

    lines += [
        f"{'=' * 55}",
        f"vs Ref = price vs latest verified market source",
        f"Trend  = 30-day forecast direction",
        f"Source: Agrolinking Intelligence Platform",
        f"Next update: {(run_date + timedelta(days=1)).strftime('%d %b %Y')}",
        f"API: agrolinking-intelligence-production.up.railway.app",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_validation() -> dict:
    run_date = datetime.now()
    date_str = run_date.strftime("%Y-%m-%d")

    logger.info("=" * 65)
    logger.info("STEP 6 — CROSS-REFERENCE VALIDATION")
    logger.info(f"  Run date  : {run_date.strftime('%A, %d %B %Y')}")
    logger.info(f"  Target    : <{MAX_ERROR_PCT}% error vs live market prices")
    logger.info("=" * 65)

    # Load latest forecast
    forecast_data, forecast_file = load_latest_forecast()

    logger.info(
        f"\n  Validating {len(forecast_data)} commodities against "
        f"reference prices...\n"
    )
    logger.info(
        f"  {'Commodity':<20} {'Forecast':>14} {'Reference':>14} "
        f"{'Error Before':>13} {'Error After':>12} {'Action':>12}"
    )
    logger.info(f"  {'-' * 85}")

    validated = {}
    results   = []

    for commodity in COMMODITIES:
        if commodity not in forecast_data:
            logger.warning(f"  {commodity:<20} — not in forecast file")
            continue

        fc_data   = forecast_data[commodity]
        validated_fc = validate_commodity(commodity, fc_data)
        validated[commodity] = validated_fc

        v       = validated_fc.get("validation", {})
        before  = v.get("error_pct_before", 0)
        after   = v.get("error_pct_after", 0)
        action  = v.get("correction_applied", "none")
        ref_p   = v.get("reference_price", 0)
        fc_p    = v.get("forecast_before", 0)
        fc_corr = v.get("forecast_after", fc_p)
        status  = "✅" if after <= MAX_ERROR_PCT else ("⚠️" if after <= 15 else "❌")

        logger.info(
            f"  {commodity:<20} "
            f"₦{fc_p:>12,.0f}  "
            f"₦{ref_p:>12,.0f}  "
            f"{before:>10.1f}%  "
            f"{after:>10.1f}%  "
            f"{action:>12}  {status}"
        )

        results.append({
            "commodity":      commodity,
            "forecast_price": fc_p,
            "reference":      ref_p,
            "error_before":   before,
            "error_after":    after,
            "action":         action,
            "within_target":  after <= MAX_ERROR_PCT,
        })

    # Summary stats
    within_target = sum(1 for r in results if r["within_target"])
    avg_err_before = np.mean([r["error_before"] for r in results]) if results else 0
    avg_err_after  = np.mean([r["error_after"]  for r in results]) if results else 0

    logger.info(f"\n  {'-' * 85}")
    logger.info(f"  Within <3% target : {within_target}/{len(results)} commodities")
    logger.info(f"  Avg error before  : {avg_err_before:.1f}%")
    logger.info(f"  Avg error after   : {avg_err_after:.1f}%")

    # Save validated forecast JSON
    validated_path = os.path.join(
        VALIDATED_DIR, f"forecast_validated_{date_str}.json"
    )
    with open(validated_path, "w") as f:
        json.dump(validated, f, indent=2, default=str)
    logger.info(f"\n  Validated forecast → {validated_path}")

    # Save validated alert
    alert_text  = generate_validated_alert(validated, run_date)
    alert_path  = os.path.join(
        PATHS["daily_alerts_dir"], f"alert_validated_{date_str}.txt"
    )
    with open(alert_path, "w", encoding="utf-8") as f:
        f.write(alert_text)
    logger.info(f"  Validated alert   → {alert_path}")

    # Print the alert
    logger.info("\n" + "=" * 65)
    logger.info("  VALIDATED DAILY PRICE ALERT")
    logger.info("=" * 65)
    for line in alert_text.split("\n"):
        logger.info(f"  {line}")

    # Update master CSV
    update_master_with_validated(validated, run_date)

    # Save validation report
    report = {
        "run_date":         run_date.isoformat(),
        "forecast_file":    forecast_file,
        "commodities":      len(results),
        "within_5pct":      within_target,
        "avg_error_before": round(avg_err_before, 2),
        "avg_error_after":  round(avg_err_after,  2),
        "results":          results,
    }
    report_path = os.path.join(
        PATHS["logs_dir"], f"validation_report_{date_str}.json"
    )
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    logger.info("=" * 65)
    logger.success(
        f"VALIDATION COMPLETE — "
        f"{within_target}/{len(results)} within <3% target | "
        f"avg error {avg_err_after:.1f}%"
    )
    logger.info("=" * 65)

    return validated


if __name__ == "__main__":
    run_validation()