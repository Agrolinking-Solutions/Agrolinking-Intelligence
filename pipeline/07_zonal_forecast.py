"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 7: Zonal & State-Level Forecasting

DAILY DRIFT: Each day this runs, it interpolates the national price
from the forecast horizon values so the output genuinely changes day-to-day
even without a full retrain. The drift follows the model's predicted
trajectory between today and the 6-month horizon.

Run daily: python pipeline/07_zonal_forecast.py
Run for specific date: python pipeline/07_zonal_forecast.py --date 2026-05-15
"""
import os, sys, json, glob, argparse, warnings
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
try:
    logger.add(os.path.join(PATHS["logs_dir"], "zonal_{time:YYYY-MM-DD}.log"),
               rotation="1 day", retention="30 days", level="DEBUG")
except Exception:
    pass

BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIFF_PATH  = os.path.join(BASE, "data", "external", "state_price_differentials.csv")
ZONES_PATH = os.path.join(BASE, "data", "external", "zones_config.json")
VAL_DIR    = os.path.join(BASE, "outputs", "forecasts", "validated")
ZONAL_DIR  = os.path.join(BASE, "outputs", "forecasts", "zonal")
ALERT_DIR  = os.path.join(BASE, "outputs", "daily_alerts")
os.makedirs(ZONAL_DIR, exist_ok=True)
os.makedirs(ALERT_DIR, exist_ok=True)

ZONE_ORDER = ["North West","North Central","North East",
              "South West","South East","South South"]

# Horizon offsets in days from forecast_start
HORIZON_DAYS = {
    "daily":     1,
    "weekly":    7,
    "2_weeks":  14,
    "monthly":  28,
    "3_months": 91,
    "6_months": 182,
}


def fmt(n):
    if n is None: return "--"
    if n >= 1_000_000: return f"N{n/1_000_000:.2f}M"
    return f"N{n/1_000:.0f}K"


def get_daily_price(fc_item, run_date):
    """
    Interpolate today's national price from the forecast horizon curve.
    This is what makes the output change day-to-day.

    Method: linear interpolation between the two horizon points that
    bracket today's date. Uses the validated reference price as day-0 anchor.
    """
    vld        = fc_item.get("validation", {})
    ref_price  = (vld.get("reference_price") or
                  vld.get("validated_price") or
                  fc_item.get("last_known_price", 0))
    ref_price  = float(ref_price)

    # Parse forecast start date
    fc_start_str = fc_item.get("forecast_start") or fc_item.get("run_date", "")
    try:
        fc_start = datetime.strptime(fc_start_str[:10], "%Y-%m-%d")
    except Exception:
        fc_start = run_date

    days_elapsed = (run_date - fc_start).days

    # Build (days, price) anchor points from all horizons
    points = [(0, ref_price)]
    for h_name, h_days in HORIZON_DAYS.items():
        h_data = fc_item.get("horizons", {}).get(h_name, {})
        if not h_data:
            continue
        vals = h_data.get("ensemble", {}).get("values", [])
        if vals:
            points.append((h_days, float(vals[-1])))

    points.sort(key=lambda x: x[0])

    if days_elapsed <= 0:
        return ref_price, "ref_price (day 0)"

    # Find bracketing points
    for i in range(len(points) - 1):
        d0, p0 = points[i]
        d1, p1 = points[i + 1]
        if d0 <= days_elapsed <= d1:
            # Linear interpolation
            t       = (days_elapsed - d0) / (d1 - d0) if d1 != d0 else 0
            price   = round(p0 + t * (p1 - p0), 2)
            pct_chg = round((price - ref_price) / ref_price * 100, 2) if ref_price > 0 else 0
            return price, f"interp d{days_elapsed} ({pct_chg:+.2f}% vs ref)"

    # Beyond last horizon — use last horizon value
    last_price = points[-1][1]
    pct_chg    = round((last_price - ref_price) / ref_price * 100, 2) if ref_price > 0 else 0
    return last_price, f"beyond horizon ({pct_chg:+.2f}% vs ref)"


def load_all():
    df   = pd.read_csv(DIFF_PATH)
    diff = {}
    for _, row in df.iterrows():
        diff.setdefault(row["state"], {})[row["commodity"]] = float(row["price_factor"])

    with open(ZONES_PATH) as f:
        zones = json.load(f)

    files = sorted(glob.glob(os.path.join(VAL_DIR, "forecast_validated_*.json")))
    if not files:
        raise FileNotFoundError(
            f"No validated forecast in {VAL_DIR}. Run 06_validate.py first.")
    with open(files[-1]) as f:
        forecast = json.load(f)

    return diff, zones, forecast, os.path.basename(files[-1])


def build_zonal_output(forecast, diff, zones, run_date):
    out              = {}
    national_anchors = {}

    logger.info("")
    logger.info("  NATIONAL ANCHORS — today's interpolated prices:")
    logger.info(f"  {'Commodity':<22} {'Today Price':>14}  {'vs Ref':>10}  {'Source'}")
    logger.info("  " + "-" * 72)

    # Load previous day's zonal for change calculation
    prev_anchors = {}
    prev_files = sorted(glob.glob(os.path.join(ZONAL_DIR, "zonal_forecast_*.json")))
    if prev_files:
        try:
            with open(prev_files[-1]) as f:
                prev_data = json.load(f)
            prev_nat = prev_data.get("national_anchors", {})
            for c, info in prev_nat.items():
                prev_anchors[c] = info.get("price", 0)
        except Exception:
            pass

    for commodity in COMMODITIES:
        if commodity not in forecast:
            national_anchors[commodity] = {"price": 0, "source": "missing"}
            continue

        fc_item            = forecast[commodity]
        today_price, src   = get_daily_price(fc_item, run_date)
        ref_price          = float(
            fc_item.get("validation", {}).get("reference_price") or
            fc_item.get("last_known_price", 0)
        )
        pct_vs_ref = round((today_price - ref_price) / ref_price * 100, 2) if ref_price > 0 else 0
        prev_p     = prev_anchors.get(commodity, today_price)
        day_chg    = round((today_price - prev_p) / prev_p * 100, 2) if prev_p > 0 else 0

        national_anchors[commodity] = {
            "price":       today_price,
            "ref_price":   ref_price,
            "pct_vs_ref":  pct_vs_ref,
            "day_change":  day_chg,
            "source":      src,
        }

        ar = "+" if pct_vs_ref >= 0 else ""
        logger.info(
            f"  {commodity:<22} N{today_price:>12,.0f}  "
            f"{ar}{pct_vs_ref:>+6.2f}%  {src}"
        )

    logger.info("")

    for zone in ZONE_ORDER:
        if zone not in zones:
            continue
        zone_info = zones[zone]
        out[zone] = {"description": zone_info["description"], "states": {}}

        for state in zone_info["states"]:
            primary   = zone_info.get("state_primary", {}).get(state, [])
            state_data = {}

            for commodity in COMMODITIES:
                if commodity not in forecast:
                    continue
                anchor     = national_anchors[commodity]["price"]
                if anchor <= 0:
                    continue
                prev_anchor = prev_anchors.get(commodity, anchor)
                factor     = diff.get(state, {}).get(commodity, zone_info["zone_default"])
                state_price = round(anchor * factor, 2)
                prev_state  = round(prev_anchor * factor, 2)
                day_chg     = round((state_price - prev_state) / prev_state * 100, 2) if prev_state > 0 else 0

                # Build horizon forecasts for all 6 periods
                fc_item    = forecast[commodity]
                horizons_out = {}
                for h_name, h_days in HORIZON_DAYS.items():
                    h_data = fc_item.get("horizons", {}).get(h_name, {})
                    if not h_data:
                        continue
                    nat_vals = h_data.get("ensemble", {}).get("values", [])
                    nat_lo   = h_data.get("ensemble", {}).get("lower_ci", nat_vals)
                    nat_hi   = h_data.get("ensemble", {}).get("upper_ci", nat_vals)
                    dates    = h_data.get("dates", [])
                    s_vals   = [round(v * factor, 2) for v in nat_vals]
                    s_lo     = [round(v * factor, 2) for v in nat_lo]
                    s_hi     = [round(v * factor, 2) for v in nat_hi]
                    ep       = s_vals[-1] if s_vals else state_price
                    h_pct    = round((ep - state_price) / state_price * 100, 2) if state_price > 0 else 0
                    horizons_out[h_name] = {
                        "dates":     dates,
                        "values":    s_vals,
                        "lower_ci":  s_lo,
                        "upper_ci":  s_hi,
                        "end_price": ep,
                        "pct_change": h_pct,
                    }

                state_data[commodity] = {
                    "national_price": round(anchor, 2),
                    "state_price":    state_price,
                    "price_factor":   round(factor, 3),
                    "day_change_pct": day_chg,
                    "is_primary":     commodity in primary,
                    "horizons":       horizons_out,
                }

            out[zone]["states"][state] = state_data

    return out, national_anchors


def build_best_market(zonal_out):
    best = {}
    for commodity in COMMODITIES:
        prices = {}
        for zone, zd in zonal_out.items():
            for state, comms in zd["states"].items():
                if commodity in comms:
                    prices[f"{state} ({zone})"] = comms[commodity]["state_price"]
        if not prices:
            continue
        cheapest = min(prices, key=prices.get)
        dearest  = max(prices, key=prices.get)
        spread   = round(
            (prices[dearest] - prices[cheapest]) / prices[cheapest] * 100, 1)
        best[commodity] = {
            "best_buy":           cheapest,
            "best_buy_price":     prices[cheapest],
            "highest_price":      dearest,
            "highest_price_val":  prices[dearest],
            "national_spread_pct": spread,
            "all_state_prices":   prices,
        }
    return best


def generate_alert(zonal_out, best_market, national_anchors, run_date, zones):
    lines = [
        "================================================",
        "AGROLINKING ZONAL COMMODITY INTELLIGENCE",
        f"Date: {run_date.strftime('%A, %d %B %Y')}",
        "6 Zones | 12 States | 12 Commodities",
        "================================================",
    ]

    for zone in ZONE_ORDER:
        if zone not in zonal_out:
            continue
        states = list(zonal_out[zone]["states"].keys())
        lines += [
            "",
            f"{zone.upper()} ({' & '.join(states)})",
            zones[zone]["description"],
            "",
            f"  {'Commodity':<20} {'':2} " + "  ".join(f"{s:<16}" for s in states),
            "  " + "-" * 58,
        ]
        for commodity in COMMODITIES:
            row_parts = [f"  {commodity:<20}"]
            for state in states:
                cd    = zonal_out[zone]["states"].get(state, {}).get(commodity)
                if not cd:
                    row_parts.append(f"  {'--':<16}")
                    continue
                price   = cd["state_price"]
                day_chg = cd.get("day_change_pct", 0)
                star    = "* " if cd.get("is_primary") else "  "
                ar      = "+" if day_chg > 0.05 else ("-" if day_chg < -0.05 else " ")
                chg_str = f"({ar}{abs(day_chg):.1f}%)" if abs(day_chg) >= 0.05 else ""
                price_str = fmt(price)
                row_parts.append(f"  {star}{price_str:<10} {chg_str:<6}")
            lines.append("".join(row_parts))

    lines += [
        "",
        "================================================",
        "* = State's primary commodity (production advantage)",
        "(+/-%) = day-on-day change vs yesterday",
        "",
        "NATIONAL PRICE CHANGES TODAY:",
        f"  {'Commodity':<22} {'Today':>10}  {'Day Chg':>8}  {'vs Ref':>8}",
        "  " + "-" * 54,
    ]
    for commodity in COMMODITIES:
        if commodity not in national_anchors:
            continue
        na     = national_anchors[commodity]
        price  = na["price"]
        d_chg  = na.get("day_change", 0)
        r_chg  = na.get("pct_vs_ref", 0)
        d_ar   = "+" if d_chg > 0 else ""
        r_ar   = "+" if r_chg > 0 else ""
        lines.append(
            f"  {commodity:<22} {fmt(price):>10}  "
            f"{d_ar}{d_chg:>+.2f}%  {r_ar}{r_chg:>+.2f}%"
        )

    lines += [
        "",
        "BEST SOURCING NATIONALLY:",
        f"  {'Commodity':<22} {'Best State':<14} {'Price':>10}  Spread",
        "  " + "-" * 58,
    ]
    for commodity in COMMODITIES:
        if commodity not in best_market:
            continue
        bm   = best_market[commodity]
        name = bm["best_buy"].split(" (")[0]
        exp  = bm["highest_price"].split(" (")[0]
        lines.append(
            f"  {commodity:<22} {name:<14} {fmt(bm['best_buy_price']):>10}"
            f"  {bm['national_spread_pct']:.0f}% vs {exp}"
        )

    lines += [
        "",
        "================================================",
        "Source: Agrolinking Intelligence Platform",
        f"Next update: {(run_date + timedelta(days=1)).strftime('%d %b %Y')}",
    ]
    return "\n".join(lines)


def run_zonal_forecast(run_date=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None,
                        help="Override run date YYYY-MM-DD")
    args, _ = parser.parse_known_args()

    if run_date is None:
        run_date = (datetime.strptime(args.date, "%Y-%m-%d")
                    if args.date else datetime.now())

    date_str = run_date.strftime("%Y-%m-%d")

    logger.info("=" * 65)
    logger.info("STEP 7 — ZONAL & STATE-LEVEL FORECASTING")
    logger.info(f"  Date  : {run_date.strftime('%A, %d %B %Y  %H:%M')}")
    logger.info("  Anchor: DAILY INTERPOLATED FORECAST (prices drift each day)")
    logger.info("=" * 65)

    diff, zones, forecast, fc_file = load_all()
    logger.info(f"  Source: {fc_file}")

    zonal_out, national_anchors = build_zonal_output(forecast, diff, zones, run_date)
    best_market  = build_best_market(zonal_out)

    # Log primary prices
    logger.info(f"  {'Zone':<16} {'State':<12} {'Commodity':<22} {'State Price':>14}  Day Chg")
    logger.info("  " + "-" * 72)
    for zone in ZONE_ORDER:
        if zone not in zonal_out:
            continue
        for state, comms in zonal_out[zone]["states"].items():
            for comm, cd in comms.items():
                if cd["is_primary"]:
                    d = cd.get("day_change_pct", 0)
                    ar = "+" if d > 0 else ""
                    logger.info(
                        f"  {zone:<16} {state:<12} * {comm:<21} "
                        f"N{cd['state_price']:>12,.0f}/MT  {ar}{d:.2f}%"
                    )

    # Save JSON
    json_path = os.path.join(ZONAL_DIR, f"zonal_forecast_{date_str}.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_date":        run_date.isoformat(),
            "source_file":     fc_file,
            "anchor_method":   "daily_interpolated",
            "national_anchors": national_anchors,
            "zones":           zonal_out,
            "best_market":     best_market,
        }, f, indent=2, default=str)
    logger.success(f"  Zonal JSON  -> {json_path}")

    # Save alert
    alert_txt  = generate_alert(zonal_out, best_market, national_anchors, run_date, zones)
    alert_path = os.path.join(ALERT_DIR, f"alert_zonal_{date_str}.txt")
    with open(alert_path, "w", encoding="utf-8") as f:
        f.write(alert_txt)
    logger.success(f"  Zonal alert -> {alert_path}")

    # Print alert
    logger.info("")
    logger.info("=" * 65)
    for line in alert_txt.split("\n"):
        logger.info(f"  {line}")
    logger.info("=" * 65)
    logger.success(
        f"ZONAL FORECASTING COMPLETE — "
        f"6 zones | 12 states | {len(COMMODITIES)} commodities"
    )
    return zonal_out, best_market


if __name__ == "__main__":
    run_zonal_forecast()