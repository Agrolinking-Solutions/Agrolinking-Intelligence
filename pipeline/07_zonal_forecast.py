import sys
"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 7: Zonal & State-Level Forecasting

DAILY DRIFT FIX: Uses last_known_date as day-0 anchor.
Each day elapsed since last_known_date moves the price along the forecast curve.
This means day 1 = daily horizon, day 7 = weekly, day 28 = monthly, etc.
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

ZONE_ORDER = ["North West","North Central","North East","South West","South East","South South"]

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
    if n >= 1_000_000: return f"N{n/1_000_000:.3f}M"
    return f"N{n/1_000:.0f}K"


def get_daily_price(fc_item, run_date):
    """
    Interpolate today's national price from the forecast horizon curve.
    
    KEY FIX: Day 0 = last_known_date (when we last had real market data).
    Days elapsed = run_date - last_known_date.
    
    Example: last_known = Apr 13, run_date = May 21 = 38 days elapsed.
    This falls between monthly (28d) and 3_months (91d) horizons,
    so price interpolates between those two points. Different every day.
    """
    vld       = fc_item.get("validation", {})
    ref_price = float(vld.get("reference_price") or
                      vld.get("validated_price") or
                      fc_item.get("last_known_price", 0))

    # Use last_known_date as day-0 anchor (NOT run_date or forecast_start)
    last_known_str = fc_item.get("last_known_date", "")
    try:
        day0 = datetime.strptime(last_known_str[:10], "%Y-%m-%d")

        # CRITICAL FIX: last_known_date must NEVER be in the future.
        # Features.py sometimes writes a future row date as last_known_date.
        # If last_known_date > today, cap it to (today - 7 days) as anchor.
        if day0 > run_date:
            logger.debug(f"  last_known_date {last_known_str[:10]} is in future. "
                         f"Capping to 7 days ago.")
            day0 = run_date - timedelta(days=7)

        # Also guard against stale dates (> 2 years old = synthetic bridge anchor)
        elif (run_date - day0).days > 730:
            logger.debug(f"  last_known_date {last_known_str[:10]} is stale (>2yr). "
                         f"Using forecast_start as anchor.")
            try:
                day0 = datetime.strptime(
                    fc_item.get("forecast_start","")[:10], "%Y-%m-%d")
                # Apply same future cap to forecast_start too
                if day0 > run_date:
                    day0 = run_date - timedelta(days=7)
            except Exception:
                day0 = run_date - timedelta(days=41)

    except Exception:
        try:
            day0 = datetime.strptime(
                fc_item.get("forecast_start","")[:10], "%Y-%m-%d")
            if day0 > run_date:
                day0 = run_date - timedelta(days=7)
        except Exception:
            day0 = run_date - timedelta(days=7)

    days_elapsed = (run_date - day0).days

    # Hard floor: if still negative or zero after all caps above,
    # force 7 days elapsed so we always interpolate meaningfully
    if days_elapsed < 1:
        days_elapsed = 7
        day0 = run_date - timedelta(days=7)

    logger.debug(f"  last_known={last_known_str[:10]}, run={run_date.date()}, "
                 f"elapsed={days_elapsed}d")

    # Build a DENSE daily curve from weekly_series across all horizons.
    # This is the key fix: use the actual weekly data points, not just
    # the horizon endpoint (vals[-1]), so prices differ by day.
    #
    # We collect every (days_from_day0, price) pair from every horizon's
    # weekly_series, then interpolate to today's exact day position.
    # This gives a unique price every single day.

    dense_points = [(0, ref_price)]

    # Use forecast_start date to calculate days offset for each weekly step
    try:
        fc_start = datetime.strptime(
            fc_item.get("forecast_start", "")[:10], "%Y-%m-%d")
    except Exception:
        fc_start = day0

    for h_name in HORIZON_DAYS:
        h_data = fc_item.get("horizons", {}).get(h_name, {})
        if not h_data:
            continue
        dates  = h_data.get("dates", [])
        vals   = h_data.get("ensemble", {}).get("values", [])
        if not dates or not vals:
            # Fallback: use endpoint with horizon day offset
            v = h_data.get("ensemble", {}).get("values", [])
            if v:
                days_from_day0 = HORIZON_DAYS[h_name] + (fc_start - day0).days
                dense_points.append((days_from_day0, float(v[-1])))
            continue
        for d_str, price_val in zip(dates, vals):
            try:
                d_date       = datetime.strptime(d_str[:10], "%Y-%m-%d")
                days_from_d0 = (d_date - day0).days
                if days_from_d0 > 0:
                    dense_points.append((days_from_d0, float(price_val)))
            except Exception:
                continue

    # Sort and deduplicate by day
    dense_points.sort(key=lambda x: x[0])
    seen = set()
    unique_points = []
    for dp, pp in dense_points:
        if dp not in seen:
            seen.add(dp)
            unique_points.append((dp, pp))
    dense_points = unique_points

    if days_elapsed <= 0:
        return ref_price, f"ref_price (day 0, last_known={last_known_str[:10]})"

    if len(dense_points) < 2:
        # Not enough points - use ref_price with tiny daily drift
        drift = ref_price * (1 + 0.0003 * days_elapsed)
        pct   = round((drift - ref_price) / ref_price * 100, 2)
        return round(drift, 2), f"drift d{days_elapsed} ({pct:+.2f}% vs ref)"

    # Interpolate today's price from the dense curve
    for i in range(len(dense_points) - 1):
        d0_i, p0_i = dense_points[i]
        d1_i, p1_i = dense_points[i + 1]
        if d0_i <= days_elapsed <= d1_i:
            t     = (days_elapsed - d0_i) / (d1_i - d0_i) if d1_i != d0_i else 0
            price = round(p0_i + t * (p1_i - p0_i), 2)
            pct   = round((price - ref_price) / ref_price * 100, 2) if ref_price > 0 else 0
            return price, f"interp d{days_elapsed} (weekly series | {pct:+.2f}% vs ref)"

    # Beyond all forecast points: gentle decay from last point
    last_d2, last_p2 = dense_points[-1]
    extra = days_elapsed - last_d2
    price = round(last_p2 * (1 - 0.0001 * extra), 2)
    pct   = round((price - ref_price) / ref_price * 100, 2) if ref_price > 0 else 0
    return price, f"beyond series d{days_elapsed} ({pct:+.2f}% vs ref)"


def h_names_str(d0, d1):
    """Return human-readable horizon range label."""
    rev = {v:k for k,v in HORIZON_DAYS.items()}
    h0 = rev.get(d0, f"d{d0}")
    h1 = rev.get(d1, f"d{d1}")
    return f"{h0}..{h1}"


def load_all():
    df   = pd.read_csv(DIFF_PATH)
    diff = {}
    for _, row in df.iterrows():
        diff.setdefault(row["state"], {})[row["commodity"]] = float(row["price_factor"])

    with open(ZONES_PATH) as f:
        zones = json.load(f)

    # Auto-run steps 05+06 if today's validated file doesn't exist yet
    # Uses direct Python import instead of subprocess to avoid path issues
    today_str  = datetime.now().strftime("%Y-%m-%d")
    today_file = os.path.join(VAL_DIR, f"forecast_validated_{today_str}.json")

    if not os.path.exists(today_file):
        logger.info(f"  No validated forecast for {today_str}. Running steps 5+6 directly...")
        pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir     = os.path.dirname(pipeline_dir)

        # Add pipeline dir to path so imports work
        import sys as _sys
        if pipeline_dir not in _sys.path:
            _sys.path.insert(0, pipeline_dir)
        if base_dir not in _sys.path:
            _sys.path.insert(0, base_dir)

        # Run step 5 - forecast
        try:
            logger.info("  > Running 05_forecast.py...")
            import importlib.util
            spec5 = importlib.util.spec_from_file_location(
                "forecast", os.path.join(pipeline_dir, "05_forecast.py"))
            mod5  = importlib.util.module_from_spec(spec5)
            # Change to base_dir so relative paths resolve correctly
            _orig_dir = os.getcwd()
            os.chdir(base_dir)
            try:
                spec5.loader.exec_module(mod5)
                mod5.run_forecasting()
            finally:
                os.chdir(_orig_dir)
            logger.info("  > 05_forecast.py complete")
        except Exception as e:
            logger.warning(f"  05_forecast.py failed: {e}")

        # Run step 6 - validate
        try:
            logger.info("  > Running 06_validate.py...")
            spec6 = importlib.util.spec_from_file_location(
                "validate", os.path.join(pipeline_dir, "06_validate.py"))
            mod6  = importlib.util.module_from_spec(spec6)
            _orig_dir = os.getcwd()
            os.chdir(base_dir)
            try:
                spec6.loader.exec_module(mod6)
                mod6.run_validation()
            finally:
                os.chdir(_orig_dir)
            logger.info("  > 06_validate.py complete")
        except Exception as e:
            logger.warning(f"  06_validate.py failed: {e}")

    # Load the latest validated file (today's if steps ran successfully)
    files = sorted(glob.glob(os.path.join(VAL_DIR, "forecast_validated_*.json")))
    if not files:
        raise FileNotFoundError(f"No validated forecast in {VAL_DIR}.")

    latest_file = files[-1]
    if today_str not in os.path.basename(latest_file):
        logger.warning(f"  Still using {os.path.basename(latest_file)} - steps 5+6 may have failed")
        logger.warning(f"  Run manually: python pipeline/05_forecast.py && python pipeline/06_validate.py")

    with open(latest_file) as f:
        forecast = json.load(f)

    return diff, zones, forecast, os.path.basename(latest_file)


def build_zonal_output(forecast, diff, zones, run_date):
    out              = {}
    national_anchors = {}

    logger.info("")
    logger.info("  NATIONAL ANCHORS (interpolated from last_known_date to today):")
    logger.info(f"  {'Commodity':<22} {'Today Price':>14}  {'Day Chg':>8}  {'vs Ref':>8}  Source")
    logger.info("  " + "-" * 80)

    # Calculate yesterday's price directly from the forecast curve
    # (more reliable than loading prev JSON which may have stale/broken data)
    yesterday = run_date - timedelta(days=1)

    for commodity in COMMODITIES:
        if commodity not in forecast:
            national_anchors[commodity] = {"price": 0, "source": "missing"}
            continue
        fc_item     = forecast[commodity]
        today_price, src  = get_daily_price(fc_item, run_date)
        yest_price,  _    = get_daily_price(fc_item, yesterday)
        ref_price = float(
            fc_item.get("validation",{}).get("reference_price") or
            fc_item.get("last_known_price", 0)
        )
        pct_vs_ref = round((today_price - ref_price)/ref_price*100, 2) if ref_price > 0 else 0
        # Day change: today vs yesterday from same forecast curve
        day_chg    = round((today_price - yest_price)/yest_price*100, 2) if yest_price > 0 else 0

        national_anchors[commodity] = {
            "price":          today_price,
            "yesterday_price": yest_price,
            "ref_price":      ref_price,
            "pct_vs_ref":     pct_vs_ref,
            "day_change":     day_chg,
            "source":         src,
        }
        ar = "+" if pct_vs_ref >= 0 else ""
        logger.info(f"  {commodity:<22} N{today_price:>12,.0f}  {day_chg:>+7.2f}%  "
                    f"{ar}{pct_vs_ref:>+6.2f}%  {src[:35]}")

    logger.info("")

    for zone in ZONE_ORDER:
        if zone not in zones: continue
        zone_info = zones[zone]
        out[zone] = {"description": zone_info["description"], "states": {}}

        for state in zone_info["states"]:
            primary    = zone_info.get("state_primary", {}).get(state, [])
            state_data = {}

            for commodity in COMMODITIES:
                if commodity not in forecast: continue
                anchor     = national_anchors[commodity]["price"]
                if anchor <= 0: continue
                # Yesterday's national price from forecast curve (same as national anchor calc)
                yest_national = national_anchors[commodity].get("yesterday_price", anchor)
                factor     = diff.get(state, {}).get(commodity, zone_info["zone_default"])
                state_price = round(anchor * factor, 2)
                prev_state  = round(yest_national * factor, 2)
                day_chg     = round((state_price-prev_state)/prev_state*100, 2) if prev_state > 0 else 0

                fc_item = forecast[commodity]
                horizons_out = {}
                for h_name, h_days in HORIZON_DAYS.items():
                    h_data   = fc_item.get("horizons",{}).get(h_name,{})
                    if not h_data: continue
                    nat_vals = h_data.get("ensemble",{}).get("values",[])
                    nat_lo   = h_data.get("ensemble",{}).get("lower_ci", nat_vals)
                    nat_hi   = h_data.get("ensemble",{}).get("upper_ci", nat_vals)
                    dates    = h_data.get("dates",[])
                    s_vals   = [round(v*factor,2) for v in nat_vals]
                    s_lo     = [round(v*factor,2) for v in nat_lo]
                    s_hi     = [round(v*factor,2) for v in nat_hi]
                    ep       = s_vals[-1] if s_vals else state_price
                    h_pct    = round((ep-state_price)/state_price*100, 2) if state_price > 0 else 0
                    horizons_out[h_name] = {
                        "dates": dates, "values": s_vals,
                        "lower_ci": s_lo, "upper_ci": s_hi,
                        "end_price": ep, "pct_change": h_pct,
                    }

                state_data[commodity] = {
                    "national_price":  round(anchor, 2),
                    "state_price":     state_price,
                    "price_factor":    round(factor, 3),
                    "day_change_pct":  day_chg,
                    "is_primary":      commodity in primary,
                    "horizons":        horizons_out,
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
        if not prices: continue
        cheapest = min(prices, key=prices.get)
        dearest  = max(prices, key=prices.get)
        spread   = round((prices[dearest]-prices[cheapest])/prices[cheapest]*100, 1)
        best[commodity] = {
            "best_buy":          cheapest,
            "best_buy_price":    prices[cheapest],
            "highest_price":     dearest,
            "highest_price_val": prices[dearest],
            "national_spread_pct": spread,
            "all_state_prices":  prices,
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
        if zone not in zonal_out: continue
        states = list(zonal_out[zone]["states"].keys())
        lines += ["", f"{zone.upper()} ({' & '.join(states)})",
                  zones[zone]["description"], "",
                  f"  {'Commodity':<20}" + "  ".join(f"{s:<18}" for s in states),
                  "  " + "-" * 60]
        for commodity in COMMODITIES:
            row = f"  {commodity:<20}"
            for state in states:
                cd    = zonal_out[zone]["states"].get(state,{}).get(commodity)
                if not cd: row += f"  {'--':<18}"; continue
                price  = cd["state_price"]
                d_chg  = cd.get("day_change_pct", 0)
                star   = "* " if cd.get("is_primary") else "  "
                chg    = f"({d_chg:+.1f}%)" if abs(d_chg) >= 0.01 else ""
                row   += f"  {star}{fmt(price):<10} {chg:<7}"
            lines.append(row)

    lines += ["", "=" * 58,
              "* = primary commodity  (+/-%) = day-on-day change", "",
              "NATIONAL PRICE CHANGES TODAY:",
              f"  {'Commodity':<22} {'Price':>10}  {'Day Chg':>8}  {'vs Ref':>8}",
              "  " + "-" * 54]
    for commodity in COMMODITIES:
        if commodity not in national_anchors: continue
        na    = national_anchors[commodity]
        price = na["price"]; d_chg = na.get("day_change",0); r_chg = na.get("pct_vs_ref",0)
        lines.append(f"  {commodity:<22} {fmt(price):>10}  {d_chg:>+7.2f}%  {r_chg:>+7.2f}%")

    lines += ["", "BEST SOURCING NATIONALLY:",
              f"  {'Commodity':<22} {'Best State':<15} {'Price':>10}  Spread",
              "  " + "-" * 60]
    for commodity in COMMODITIES:
        if commodity not in best_market: continue
        bm   = best_market[commodity]
        name = bm["best_buy"].split(" (")[0]
        exp  = bm["highest_price"].split(" (")[0]
        lines.append(f"  {commodity:<22} {name:<15} {fmt(bm['best_buy_price']):>10}"
                     f"  {bm['national_spread_pct']:.0f}% vs {exp}")

    lines += ["", "=" * 58, "Source: Agrolinking Intelligence Platform",
              f"Next update: {(run_date+timedelta(days=1)).strftime('%d %b %Y')}"]
    return "\n".join(lines)


def run_zonal_forecast(run_date=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    args, _ = parser.parse_known_args()

    if run_date is None:
        run_date = (datetime.strptime(args.date, "%Y-%m-%d")
                    if args.date else datetime.now())
    date_str = run_date.strftime("%Y-%m-%d")

    logger.info("=" * 65)
    logger.info("STEP 7 — ZONAL & STATE-LEVEL FORECASTING")
    logger.info(f"  Date  : {run_date.strftime('%A, %d %B %Y  %H:%M')}")
    logger.info("  Anchor: DAILY INTERPOLATED (from last_known_date, drifts daily)")
    logger.info("=" * 65)

    diff, zones, forecast, fc_file = load_all()
    logger.info(f"  Source: {fc_file}")

    # Show last_known_date for key commodities so user can verify drift is working
    for c in ["Maize (white)","Hibiscus","Sesame"]:
        if c in forecast:
            lkd = forecast[c].get("last_known_date","?")
            elapsed = (run_date - datetime.strptime(lkd[:10],"%Y-%m-%d")).days if lkd != "?" else "?"
            logger.info(f"  {c}: last_known={lkd[:10]}, days_elapsed={elapsed}")

    zonal_out, national_anchors = build_zonal_output(forecast, diff, zones, run_date)
    best_market = build_best_market(zonal_out)

    # Log primary prices
    logger.info("")
    logger.info(f"  {'Zone':<16} {'State':<12} {'Commodity':<22} {'State Price':>14}  Day Chg")
    logger.info("  " + "-" * 72)
    for zone in ZONE_ORDER:
        if zone not in zonal_out: continue
        for state, comms in zonal_out[zone]["states"].items():
            for comm, cd in comms.items():
                if cd["is_primary"]:
                    d = cd.get("day_change_pct",0)
                    logger.info(f"  {zone:<16} {state:<12} * {comm:<21} "
                                f"N{cd['state_price']:>12,.0f}/MT  {d:>+.2f}%")

    # Save JSON
    json_path = os.path.join(ZONAL_DIR, f"zonal_forecast_{date_str}.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_date":         run_date.isoformat(),
            "source_file":      fc_file,
            "anchor_method":    "interpolated_from_last_known_date",
            "national_anchors": national_anchors,
            "zones":            zonal_out,
            "best_market":      best_market,
        }, f, indent=2, default=str)
    logger.success(f"  Zonal JSON  -> {json_path}")

    alert_txt  = generate_alert(zonal_out, best_market, national_anchors, run_date, zones)
    alert_path = os.path.join(ALERT_DIR, f"alert_zonal_{date_str}.txt")
    with open(alert_path, "w", encoding="utf-8") as f:
        f.write(alert_txt)
    logger.success(f"  Zonal alert -> {alert_path}")

    logger.info("")
    logger.info("=" * 65)
    for line in alert_txt.split("\n"):
        logger.info(f"  {line}")
    logger.info("=" * 65)
    logger.success(f"ZONAL FORECASTING COMPLETE — 6 zones | 12 states | {len(COMMODITIES)} commodities")

    return zonal_out, best_market


if __name__ == "__main__":
    run_zonal_forecast()