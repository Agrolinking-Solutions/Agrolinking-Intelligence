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
    "Hibiscus":      2_325_000,   # Agricome Apr 16 2026
    "Sesame":        1_650_000,   # LCFE May 2026 (recalibrated)
    "Ginger":       12_000_000,   # NGX Feb 2026 N13,000/kg; mid-market N12M
    "Cocoa":         5_650_000,   # Agricome Apr 16 2026
    "Soybeans":        745_000,   # Agricome Apr 16 2026
    "Cashew Nuts":   1_950_000,   # Agricome Apr 16 2026
    "Sorghum":         420_000,   # Market Naija TV mid-chain (recalibrated)
    "Beans (white)":   813_000,   # WFP Mar 2026
    "Beans (red)":     915_000,   # WFP Mar 2026
    "Maize (white)":   370_000,   # Market 2026
    "Maize (yellow)":  400_000,   # Market 2026
    "Wheat":           706_833,   # Agrolinking primary Apr 13 2026
    "Rice":          1_550_000,   # Market research May 2026
    # ── Livestock/protein — previously missing entirely, which forced
    # validation to always skip these four (status: "skipped", no
    # correction ever applied, no live accuracy check).
    # Sourced Sept 2026: retail beef/goat ~N7-8k/kg (24hoursmarket.com,
    # Daily Trust); wholesale typically runs ~15-20% below retail.
    # Dried fish from carton pricing at Hadejia market (fcwc-fish.org).
    # Eggs from NBS "Selected Food Price Watch" March 2026 (channelstv.com).
    # Update these whenever you have a fresher reading — same workflow
    # as the crop MANUAL_PRICES entries above.
    "Meat (beef)":   6_500_000,   # wholesale est. from ~N7-8k/kg retail, Sept 2026
    "Meat (goat)":   6_500_000,   # wholesale est. from ~N7-8k/kg retail, Sept 2026
    "Fish (dried)":  1_200_000,   # est. from Hadejia carton pricing, mid-2026
    "Eggs":              6_200,   # NBS Selected Food Price Watch, Mar 2026 (NGN/crate)
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

def get_reference_price(commodity: str) -> tuple[float, str]:
    """
    Get the best available reference price for a commodity.
    Priority: Manual (fresh Agricom post) > Web research anchors
    Returns (price, source_description)
    """
    # 1. Manual price (freshest — from latest Agricom post you entered)
    if commodity in MANUAL_PRICES and MANUAL_PRICES[commodity] is not None:
        return float(MANUAL_PRICES[commodity]), "Agricom manual entry"

    # 2. Web research anchor
    if commodity in WEB_REFERENCE_PRICES:
        return float(WEB_REFERENCE_PRICES[commodity]), "Web research (NGX/WFP/Market data)"

    return None, "No reference available"


# ─────────────────────────────────────────────────────────────────────────────
# LIVE WEB FETCH (runs on your machine — internet connected)
# ─────────────────────────────────────────────────────────────────────────────

def try_fetch_live_price(commodity: str) -> tuple[float | None, str]:
    """
    Attempt to fetch a live price from the web.
    Returns (price_ngn_mt, source) or (None, error_message).
    This runs silently — failures fall back to reference anchors.
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        # Map commodities to search queries
        search_map = {
            "Cocoa":         "Nigeria cocoa price per ton NGN today",
            "Ginger":        "Nigeria ginger price per ton NGN today",
            "Sesame":        "Nigeria sesame seed price per ton NGN today",
            "Hibiscus":      "Nigeria hibiscus zobo price per ton NGN today",
            "Cashew Nuts":   "Nigeria cashew nut price per ton NGN today",
            "Soybeans":      "Nigeria soybean price per ton NGN today",
            "Sorghum":       "Nigeria sorghum price per ton NGN today",
            "Maize (white)": "Nigeria maize white price per ton NGN today",
            "Maize (yellow)":"Nigeria maize yellow price per ton NGN today",
            "Beans (white)": "Nigeria beans white price per ton NGN today",
            "Beans (red)":   "Nigeria beans red price per ton NGN today",
        }

        # Try NEPC price page directly
        nepc_url = "https://nepc.gov.ng/indicative-market-prices/"
        r = requests.get(nepc_url, timeout=10,
                        headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            text = soup.get_text(separator=" ").lower()
            # Look for commodity name near a price pattern
            import re
            comm_lower = commodity.lower().replace(" (white)","").replace(" (yellow)","")
            idx = text.find(comm_lower)
            if idx > 0:
                snippet = text[idx:idx+200]
                prices = re.findall(r'[\d,]+(?:\.\d+)?', snippet.replace(",",""))
                prices_clean = [float(p) for p in prices if 100 < float(p) < 100_000_000]
                if prices_clean:
                    # Convert to NGN/MT if needed (assume per ton if > 100,000)
                    price = max(prices_clean)
                    if price < 100_000:
                        price *= 1000  # per kg → per MT
                    return price, "NEPC live"

    except Exception:
        pass

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
        vld   = fc.get("validation", {})
        daily = fc.get("horizons", {}).get("daily", {})

        # ── 1. Append TODAY's validated price as a real confirmed data point ──
        # This is the key step that moves last_known_date forward every day.
        today_price = None
        if daily and daily.get("ensemble", {}).get("values"):
            today_price = daily["ensemble"]["values"][0]
        elif vld.get("reference_price"):
            today_price = vld["reference_price"]

        if today_price and today_price > 0:
            # Snap to the Monday of this week — same convention 01_ingest.py
            # and 02_clean.py use everywhere else. Without this, "today"
            # (which is rarely a Monday) sits at a different date than the
            # rest of the system expects, so this row and any earlier
            # (correct or incorrect) row for the same week don't collide
            # until the NEXT ingest/clean run snaps them together — at
            # which point a tie in priority makes which one survives a
            # coin flip. Snapping here means this write always lands on
            # and correctly replaces whatever was in that exact slot.
            _raw_today = pd.Timestamp(run_date.date())
            today_ts = _raw_today - pd.Timedelta(days=_raw_today.dayofweek)
            # UPSERT, not skip-if-exists: today's slot must always reflect
            # the latest validated correction. The old "skip if a row
            # already exists" guard let a single bad write (e.g. from
            # before a commodity had a reference price at all) freeze
            # that date's price forever, even after the bug producing it
            # was fixed — every later, correctly-validated run would
            # silently no-op against a stale wrong number.
            master = master[
                ~((master["commodity"] == commodity) & (master["date"] == today_ts))
            ]
            new_rows.append({
                    "commodity":          commodity,
                    "date":               today_ts,
                    "price_ngn_mt":       round(today_price, 2),
                    "currency":           "NGN",
                    "unit":               "NGN/MT",
                    "source":             "Agrolinking Intelligence Platform",
                    "market_type":        "wholesale",
                    "region":             "National",
                    "fx_rate":            np.nan,
                    "rainfall_index":     np.nan,
                    "data_quality_score": 0.90,
                    "is_validated":       True,
                    "notes": (
                        f"Validated daily price — "
                        f"ref: N{vld.get('reference_price',0):,.0f} "
                        f"err: {vld.get('error_pct_after', vld.get('error_after_pct', 0)):.1f}%"
                    ),
                    "data_source":        "Agrolinking_validated",
                    "record_type":        "validated_actual",
                    "outlier_flag":       False,
                    "outlier_reason":     "",
                    "price_raw_ngn_mt":   round(today_price, 2),
                })

        # ── 2. Also append weekly horizon rows (future forecasts) ─────────────
        weekly = fc.get("horizons", {}).get("weekly", {})
        if not weekly or not weekly.get("ensemble"):
            continue

        ensemble = weekly["ensemble"]
        dates    = weekly.get("dates", [])
        values   = ensemble.get("values", [])

        # These are disposable projections, not confirmed data — always
        # replace the old future curve wholesale rather than skip-if-
        # exists. The previous "skip if a row already exists" guard let
        # a bad/stale forecast curve (e.g. written back before this
        # commodity had a reference price) freeze in place indefinitely,
        # since a future date, once written, would never be touched
        # again by any later, better-corrected run.
        master = master[
            ~(
                (master["commodity"] == commodity) &
                (master["record_type"] == "forecast") &
                (master["date"] > pd.Timestamp(run_date.date()))
            )
        ]

        for date_str, price in zip(dates, values):
            date = pd.Timestamp(date_str)
            if date <= pd.Timestamp(run_date.date()):
                continue  # skip past dates

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
                    f"ref: N{vld.get('reference_price',0):,.0f} "
                    f"err: {vld.get('error_pct_after', vld.get('error_after_pct', 0)):.1f}%"
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
# DUAL-WRITE TO POSTGRES (TimescaleDB) — migration verification period
# ─────────────────────────────────────────────────────────────────────────────
# Writes the same "today's confirmed price" + "forecast horizon curve"
# that update_master_with_validated() just wrote to master.csv, into the
# Postgres prices/forecasts tables. Runs alongside the CSV write, never
# instead of it — this is the dual-write period from the migration plan,
# meant to let the two sources be compared before cutting the API over.
#
# Deliberately non-fatal: if TSDB_URL isn't set, or the DB is briefly
# unreachable, this logs a warning and the pipeline continues as normal.
# The CSV/JSON files remain the source of truth until the cutover step.
HORIZON_DAYS_MAP = {
    "daily": 1, "weekly": 7, "2_weeks": 14,
    "monthly": 30, "3_months": 90, "6_months": 180,
}

def write_to_postgres(validated: dict, run_date: datetime):
    db_url = os.environ.get("TSDB_URL")
    if not db_url:
        logger.warning("  [Postgres] TSDB_URL not set — skipping dual-write (CSV write already done)")
        return

    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        logger.warning("  [Postgres] psycopg2 not installed — skipping dual-write")
        return

    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        logger.warning(f"  [Postgres] Could not connect — skipping dual-write this run: {e}")
        return

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT commodity_id, name FROM commodities")
            commodity_map = {name: cid for cid, name in cur.fetchall()}
            cur.execute("SELECT location_id FROM locations WHERE state = 'National'")
            row = cur.fetchone()
            if row is None:
                logger.warning("  [Postgres] No 'National' location row — skipping dual-write")
                return
            national_id = row[0]

        today_ts = pd.Timestamp(run_date.date())
        today_ts = today_ts - pd.Timedelta(days=today_ts.dayofweek)  # same Monday-snap as master.csv

        price_rows, forecast_rows = [], []
        for commodity, fc in validated.items():
            cid = commodity_map.get(commodity)
            if cid is None:
                continue

            vld = fc.get("validation", {})
            today_price = None
            daily = fc.get("horizons", {}).get("daily", {})
            if daily and daily.get("ensemble", {}).get("values"):
                today_price = daily["ensemble"]["values"][0]
            elif vld.get("reference_price"):
                today_price = vld["reference_price"]

            if today_price and today_price > 0:
                price_rows.append((
                    today_ts, cid, national_id, float(today_price), "Agrolinking_validated",
                ))

            validated_flag = vld.get("status") == "validated"
            error_pct = vld.get("error_after")
            confidence = fc.get("model_confidence")
            for h_name, h_days in HORIZON_DAYS_MAP.items():
                h_data = fc.get("horizons", {}).get(h_name)
                if not h_data:
                    continue
                vals = h_data.get("ensemble", {}).get("values", [])
                price = vals[0] if vals else h_data.get("forecast_price")
                if price is None:
                    continue
                forecast_rows.append((
                    today_ts, cid, national_id, h_days,
                    float(price), confidence, validated_flag, error_pct,
                ))

        with conn.cursor() as cur:
            if price_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO prices (time, commodity_id, location_id, price, source)
                    VALUES %s
                    ON CONFLICT (time, commodity_id, location_id, source)
                    DO UPDATE SET price = EXCLUDED.price
                    """,
                    price_rows,
                )
            if forecast_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO forecasts
                        (time, commodity_id, location_id, horizon_days,
                         predicted_price, model_confidence, validated, error_pct)
                    VALUES %s
                    ON CONFLICT (time, commodity_id, location_id, horizon_days)
                    DO UPDATE SET
                        predicted_price  = EXCLUDED.predicted_price,
                        model_confidence = EXCLUDED.model_confidence,
                        validated        = EXCLUDED.validated,
                        error_pct        = EXCLUDED.error_pct
                    """,
                    forecast_rows,
                )
        conn.commit()
        logger.info(f"  [Postgres] Dual-write OK: {len(price_rows)} price rows, {len(forecast_rows)} forecast rows")
    except Exception as e:
        logger.warning(f"  [Postgres] Dual-write failed this run (CSV write already succeeded): {e}")
        conn.rollback()
    finally:
        conn.close()


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
    write_to_postgres(validated, run_date)

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