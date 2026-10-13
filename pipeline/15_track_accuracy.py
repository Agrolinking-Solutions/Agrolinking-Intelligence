"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Live accuracy tracking — how close were the forecasts clients actually saw?

The backtest (13_select_forecaster.py) replays history. This step scores the
REAL published forecasts instead: every saved daily file in
outputs/forecasts/validated/ (what the dashboard and API served that day) is
compared with the real market price that later arrived for the same date.

Each run rebuilds the record from those files, so nothing has to be kept in
sync between runs:

  outputs/backtest/forecast_log.csv     one row per (forecast day, commodity, horizon)
  outputs/backtest/live_accuracy.json   per commodity and horizon: accuracy,
                                        direction hit-rate, number scored —
                                        overall and split by forecasting method

"Real" means Agricome / WFP / Agrolinking_primary prices only (same as the
backtest). A forecast is scored when a real price exists within MATCH_DAYS of
its target date; until then it waits.

Run: python pipeline/15_track_accuracy.py
"""

import os, sys, json, glob
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALIDATED_DIR = os.path.join(PATHS["forecasts_dir"], "validated")
OUT_DIR       = os.path.join(ROOT, "outputs", "backtest")
LOG_PATH      = os.path.join(OUT_DIR, "forecast_log.csv")
SUMMARY_PATH  = os.path.join(OUT_DIR, "live_accuracy.json")

REAL_SOURCES = {"Agricome", "WFP", "Agrolinking_primary"}
HORIZONS     = {"weekly": "1 week", "monthly": "1 month", "3_months": "3 months", "6_months": "6 months"}
# Real prices are weekly (Agricome) or monthly (WFP, stored on the nearest
# Monday), so allow a few days either side of the target date.
MATCH_DAYS   = 7
MOVE_PCT     = 1.0    # ignore direction when the real price moved less than this


def forecast_method(h_data):
    used = (h_data.get("ensemble") or {}).get("models_used") or []
    if any(str(m).startswith("champion:") for m in used):
        return "champion"
    return "ensemble"


def build_log():
    rows = []
    for f in sorted(glob.glob(os.path.join(VALIDATED_DIR, "forecast_validated_*.json"))):
        made_on = os.path.basename(f)[len("forecast_validated_"):-len(".json")]
        try:
            with open(f) as fh:
                data = json.load(fh)
        except Exception as e:
            logger.warning(f"  skipping unreadable {os.path.basename(f)}: {e}")
            continue
        for commodity, fc in data.items():
            if not isinstance(fc, dict):
                continue
            anchor = fc.get("last_known_price")
            for h_key in HORIZONS:
                h = (fc.get("horizons") or {}).get(h_key) or {}
                end = h.get("forecast_end_detail") or h.get("forecast_end")
                if isinstance(end, dict):
                    target, price = end.get("date"), end.get("price")
                else:
                    vals, dates = (h.get("ensemble") or {}).get("values") or [], h.get("dates") or []
                    target, price = (dates[-1], vals[-1]) if vals and dates else (None, None)
                if not target or price is None or not np.isfinite(price) or price <= 0:
                    continue
                rows.append({"made_on": made_on, "commodity": commodity, "horizon": h_key,
                             "target_date": str(target)[:10], "predicted": float(price),
                             "anchor_price": float(anchor) if anchor else np.nan,
                             "method": forecast_method(h)})
    return pd.DataFrame(rows)


def attach_actuals(log):
    """Nearest real price within MATCH_DAYS of each forecast's target date."""
    master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    real = master[master["data_source"].isin(REAL_SOURCES) & master["price_ngn_mt"].notna()]
    real = (real.groupby(["commodity", "date"], as_index=False)["price_ngn_mt"].mean()
                .rename(columns={"date": "actual_date", "price_ngn_mt": "actual"}))
    log = log.copy()
    log["target_date"] = pd.to_datetime(log["target_date"])
    log["_order"] = range(len(log))
    merged = pd.merge_asof(
        log.sort_values("target_date"), real.sort_values("actual_date"),
        left_on="target_date", right_on="actual_date", by="commodity",
        direction="nearest", tolerance=pd.Timedelta(days=MATCH_DAYS))
    return merged.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def summarise(scored):
    def stats(df):
        if df.empty:
            return None
        ape = (df["predicted"] - df["actual"]).abs() / df["actual"] * 100
        move = (df["actual"] - df["anchor_price"]) / df["anchor_price"] * 100
        moved = move.abs() >= MOVE_PCT
        hit = (np.sign(df["predicted"] - df["anchor_price"]) == np.sign(df["actual"] - df["anchor_price"]))[moved]
        return {"scored": int(len(df)),
                "accuracy_pct": round(float(100 - ape.mean()), 1),
                "median_error_pct": round(float(ape.median()), 1),
                "direction_hit_pct": round(float(hit.mean() * 100), 1) if len(hit) else None,
                "first_forecast": str(df["made_on"].min()), "last_forecast": str(df["made_on"].max())}

    out = {}
    for commodity in COMMODITIES:
        c = scored[scored["commodity"] == commodity]
        out[commodity] = {
            HORIZONS[h]: {"all": stats(c[c["horizon"] == h]),
                          "ensemble": stats(c[(c["horizon"] == h) & (c["method"] == "ensemble")]),
                          "champion": stats(c[(c["horizon"] == h) & (c["method"] == "champion")])}
            for h in HORIZONS
        }
    overall = {HORIZONS[h]: {m: stats(scored[(scored["horizon"] == h) &
                                             ((scored["method"] == m) if m != "all" else True)])
                             for m in ("all", "ensemble", "champion")}
               for h in HORIZONS}
    return out, overall


def run_tracking():
    logger.info("=" * 60)
    logger.info("STEP 15 - LIVE ACCURACY OF PUBLISHED FORECASTS")
    logger.info("=" * 60)
    os.makedirs(OUT_DIR, exist_ok=True)
    log = build_log()
    if log.empty:
        logger.warning("  no saved forecasts found")
        return True
    log = attach_actuals(log)
    # Only genuine forecasts count: some August 2026 files carried target
    # dates in 2023 (the stale-anchor bug), which are not predictions at all.
    log["valid"] = log["target_date"] > pd.to_datetime(log["made_on"])
    log.to_csv(LOG_PATH, index=False)
    scored = log[log["actual"].notna() & log["valid"]]
    per_commodity, overall = summarise(scored)
    with open(SUMMARY_PATH, "w") as f:
        json.dump({"updated": date.today().isoformat(),
                   "what": "Accuracy of forecasts as published (outputs/forecasts/validated), scored "
                           "against the real Agricome/WFP price that later arrived for the target date",
                   "accuracy_definition": "100 minus mean absolute percentage error",
                   "forecasts_logged": int(len(log)), "forecasts_scored": int(len(scored)),
                   "overall": overall, "by_commodity": per_commodity}, f, indent=2)
    logger.info(f"  {len(log):,} published forecasts logged, {len(scored):,} scored against real prices")
    for h in HORIZONS.values():
        for m in ("ensemble", "champion"):
            s = overall[h][m]
            if s:
                logger.info(f"    {h:<9} {m:<9} accuracy {s['accuracy_pct']}%  direction "
                            f"{s['direction_hit_pct']}%  (n={s['scored']})")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_tracking() else 1)
