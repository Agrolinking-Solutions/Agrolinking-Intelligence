"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Champion selection — per commodity, which forecaster is actually most accurate?

Scores every candidate in drift_models.CANDIDATES with a rolling-origin
backtest on REAL observations only (Agricome / WFP / Agrolinking_primary),
at 4 and 8 weeks ahead, over the last 104 weeks of real targets. These
candidates need no model fitting, so this runs in seconds and can re-run
every day — accuracy is re-measured as each new real price arrives.

Output: outputs/backtest/model_selection.json — read by 05_forecast.py.
  { commodity: { method, accuracy_4w, accuracy_8w, naive_accuracy_4w,
                 direction_hit_4w, n_4w, mape_4w, mape_8w, updated } }

Rule: pick the lowest weighted MAPE (60% @4w, 40% @8w). A challenger must
beat naive by MIN_GAIN points, otherwise naive stays champion — with small
samples we don't switch on noise.

Run: python pipeline/13_select_forecaster.py
"""

import os, sys, json
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES
from pipeline.drift_models import CANDIDATES, weekly_growth, project

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_DIR = os.path.join(os.path.dirname(PATHS["master"]), "features")
OUT_DIR      = os.path.join(ROOT, "outputs", "backtest")
SELECTION    = os.path.join(OUT_DIR, "model_selection.json")

REAL_SOURCES = {"Agricome", "WFP", "Agrolinking_primary"}
HORIZONS     = (4, 8)
HORIZON_W    = {4: 0.6, 8: 0.4}
WINDOW_WEEKS = 104
MIN_TARGETS  = 6
MIN_GAIN     = 0.3        # accuracy points a challenger must beat naive by


def safe_name(c):
    return c.lower().replace(" ", "_").replace("(", "").replace(")", "")


def score_commodity(commodity):
    path = os.path.join(FEATURES_DIR, f"features_{safe_name(commodity)}.csv")
    df = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    df = df.drop_duplicates("date", keep="last").reset_index(drop=True)
    real = df[df["data_source"].isin(REAL_SOURCES)]
    if real.empty:
        return None
    cutoff = real["date"].max() - pd.Timedelta(weeks=WINDOW_WEEKS)
    rows = []
    for _, t in real[real["date"] >= cutoff].iterrows():
        for h in HORIZONS:
            o = t["date"] - pd.Timedelta(weeks=h)
            hist = df[df["date"] <= o]
            if len(hist) < 20:
                continue
            last = float(hist["price_ngn_mt"].iloc[-1])
            for m in CANDIDATES:
                pred = float(project(last, weekly_growth(m, hist), h))
                rows.append({"method": m, "h": h, "actual": float(t["price_ngn_mt"]),
                             "pred": pred, "origin": last})
    return pd.DataFrame(rows) if rows else None


def summarise(res):
    res = res.copy()
    res["ape"] = (res.pred - res.actual).abs() / res.actual * 100
    moved = (res.actual - res.origin).abs() / res.origin > 0.01
    res["dir_ok"] = np.where(
        moved, (np.sign(res.pred - res.origin) == np.sign(res.actual - res.origin)).astype(float), np.nan)
    return res.groupby(["method", "h"]).agg(
        n=("ape", "size"), mape=("ape", "mean"), dir_hit=("dir_ok", "mean")).reset_index()


def choose(summ):
    def wscore(m):
        s = summ[summ.method == m].set_index("h")
        if not all(h in s.index for h in HORIZONS):
            return np.inf
        return sum(HORIZON_W[h] * s.loc[h, "mape"] for h in HORIZONS)

    scores = {m: wscore(m) for m in CANDIDATES}
    best = min(scores, key=scores.get)
    if best != "naive" and scores["naive"] - scores[best] < MIN_GAIN:
        best = "naive"
    return best, scores


def run_selection():
    os.makedirs(OUT_DIR, exist_ok=True)
    out, today = {}, date.today().isoformat()
    for c in COMMODITIES:
        try:
            res = score_commodity(c)
        except FileNotFoundError:
            logger.info(f"  {c}: no feature file, skipped")
            continue
        if res is None:
            logger.info(f"  {c}: no real observations, skipped")
            continue
        summ = summarise(res)
        n4 = int(summ[(summ.method == "naive") & (summ.h == 4)].n.sum())
        if n4 < MIN_TARGETS:
            logger.warning(f"  {c}: only {n4} real targets — keeping naive, low confidence")
            best, scores = "naive", {}
        else:
            best, scores = choose(summ)
        g = lambda m, h, col: summ[(summ.method == m) & (summ.h == h)][col]
        def one(m, h, col):
            s = g(m, h, col)
            return None if s.empty or pd.isna(s.iloc[0]) else round(float(s.iloc[0]), 2)
        out[c] = {
            "method": best,
            "n_4w": n4,
            "mape_4w": one(best, 4, "mape"), "mape_8w": one(best, 8, "mape"),
            "accuracy_4w": None if one(best, 4, "mape") is None else round(100 - one(best, 4, "mape"), 1),
            "accuracy_8w": None if one(best, 8, "mape") is None else round(100 - one(best, 8, "mape"), 1),
            "naive_accuracy_4w": None if one("naive", 4, "mape") is None else round(100 - one("naive", 4, "mape"), 1),
            "direction_hit_4w": one(best, 4, "dir_hit"),
            "low_confidence": n4 < 12,
            "updated": today,
        }
        logger.info(f"  {c:<16} -> {best:<10} acc4w={out[c]['accuracy_4w']}% "
                    f"(naive {out[c]['naive_accuracy_4w']}%) n={n4}")
    with open(SELECTION, "w") as f:
        json.dump(out, f, indent=2)
    accs = [v["accuracy_4w"] for v in out.values() if v["accuracy_4w"] is not None]
    if accs:
        logger.info(f"  Mean 4-week accuracy on real prices: {np.mean(accs):.1f}% "
                    f"({sum(a >= 95 for a in accs)}/{len(accs)} commodities >=95%)")
    return out


if __name__ == "__main__":
    run_selection()
