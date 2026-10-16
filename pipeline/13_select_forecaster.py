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

Rule: pick the lowest weighted MAPE across 4/8/13/26 weeks. A challenger must
beat naive by MIN_GAIN points, otherwise naive stays champion — with small
samples we don't switch on noise. Tie-break: if naive wins but half-inflation
drift scores within TIE_MARGIN of it, use the drift instead. The two are
equally accurate on the record, and Nigerian prices drift up with inflation;
a flat 6-month line reads as "no forecast" to customers.

Run: python pipeline/13_select_forecaster.py
"""

import os, sys, json
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, PARTNER_SOURCES
from pipeline.drift_models import CANDIDATES, point_forecasts

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_DIR = os.path.join(os.path.dirname(PATHS["master"]), "features")
OUT_DIR      = os.path.join(ROOT, "outputs", "backtest")
SELECTION    = os.path.join(OUT_DIR, "model_selection.json")

REAL_SOURCES = PARTNER_SOURCES
# 1, 2, 3 and 6 months ahead - the dashboard shows all of these, and
# seasonal effects only show up beyond a few weeks. Nearer horizons weigh more.
HORIZONS     = (4, 8, 13, 26)
HORIZON_W    = {4: 0.4, 8: 0.3, 13: 0.2, 26: 0.1}
WINDOW_WEEKS = 104
MIN_TARGETS  = 6
MIN_GAIN     = 0.3        # accuracy points a challenger must beat naive by
# Rows that did not exist yet at a past forecast date: prices interpolated
# towards the NEXT real price, and the platform's own model rows. Starting a
# test forecast from them leaked the future into the score (white beans'
# "price holds" looked 87.4% accurate vs 80.2% fairly; scripts/research/
# forecast_audit.py, Oct 2026).
NOT_KNOWN_YET = {"Interpolated", "Agrolinking_validated"}
FRESH_WEEKS  = 4          # "fresh data": latest real price at most this old at the forecast date
# The METHOD is chosen on test forecasts made in normal conditions (latest
# real price at most GAP_WEEKS old): a handful made across a data gap (FEWS
# NET's 2025 pause left white maize 10 months without a real price) swamped
# the comparison. Reported accuracy still counts every test forecast, and
# the forecast ranges widen when data is old (drift_models.range_halfwidth).
GAP_WEEKS    = 12
TIE_METHOD   = "infl@0.5"
LONG_H        = 26       # the 6-month horizon a long-range method is judged on
LONG_MIN_GAIN = 1.0      # error points it must beat the champion by there (avoid noise)
TIE_MARGIN   = 0.3        # ...and how close it must be to naive to win a tie


def safe_name(c):
    return c.lower().replace(" ", "_").replace("(", "").replace(")", "")


def score_commodity(commodity):
    path = os.path.join(FEATURES_DIR, f"features_{safe_name(commodity)}.csv")
    df = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    df = df.drop_duplicates("date", keep="last").reset_index(drop=True)
    # Only what the forecasters read: slicing the full ~80-column feature
    # table for every origin made this step take minutes instead of seconds.
    df = df[[c for c in ("date", "price_ngn_mt", "data_source", "food_inflation_yoy") if c in df.columns]]
    real = df[df["data_source"].isin(REAL_SOURCES)]
    if real.empty:
        return None
    cutoff = real["date"].max() - pd.Timedelta(weeks=WINDOW_WEEKS)
    rows = []
    for _, t in real[real["date"] >= cutoff].iterrows():
        for h in HORIZONS:
            o = t["date"] - pd.Timedelta(weeks=h)
            hist = df[df["date"] <= o]
            real_before = hist[hist["data_source"].isin(REAL_SOURCES)]
            if len(hist) < 20 or real_before.empty:
                continue
            last_real = real_before["date"].max()
            hist = hist[~(hist["data_source"].isin(NOT_KNOWN_YET) & (hist["date"] > last_real))]
            last = float(hist["price_ngn_mt"].iloc[-1])
            last_date = hist["date"].iloc[-1]
            preds = point_forecasts(hist, last, last_date, t["date"])
            age = (o - last_real).days / 7.0
            for m in CANDIDATES:
                rows.append({"method": m, "h": h, "actual": float(t["price_ngn_mt"]),
                             "pred": float(preds[m]), "origin": last, "age_weeks": age})
    return pd.DataFrame(rows) if rows else None


def summarise(res):
    res = res.copy()
    res["ape"] = (res.pred - res.actual).abs() / res.actual * 100
    moved = (res.actual - res.origin).abs() / res.origin > 0.01
    res["dir_ok"] = np.where(
        moved, (np.sign(res.pred - res.origin) == np.sign(res.actual - res.origin)).astype(float), np.nan)
    return res.groupby(["method", "h"]).agg(
        n=("ape", "size"), mape=("ape", "mean"), dir_hit=("dir_ok", "mean")).reset_index()


def fresh_err(res, method, h=4):
    """Mean error (%) of the test forecasts made with real data at most FRESH_WEEKS old."""
    r = res[(res["method"] == method) & (res["h"] == h) & (res["age_weeks"] <= FRESH_WEEKS)]
    if len(r) < MIN_TARGETS:
        return None
    return round(float(((r["pred"] - r["actual"]).abs() / r["actual"] * 100).mean()), 2)


def fresh_acc(res, method):
    e = fresh_err(res, method, 4)
    return None if e is None else round(100 - e, 1)


def choose(summ):
    def wscore(m):
        s = summ[summ.method == m].set_index("h")
        if not all(h in s.index for h in HORIZONS):
            return np.inf
        return sum(HORIZON_W[h] * s.loc[h, "mape"] for h in HORIZONS)

    scores = {m: wscore(m) for m in CANDIDATES}
    best = min(scores, key=scores.get)
    # Near-tie: methods within MIN_GAIN of the best overall score are equally
    # good within noise, so take the one most accurate at 1 month. White beans:
    # full seasonal 15.20 overall but 9.5% at 1 month, half seasonal 15.28 and
    # 8.8% - and real Sep->Oct moves (-6% median) sat closer to the half.
    m4 = summ[summ.h == 4].set_index("method")["mape"]
    close = [m for m in CANDIDATES if scores[m] - scores[best] < MIN_GAIN and m in m4.index]
    if close:
        best = min(close, key=lambda m: m4[m])
    if best != "naive" and scores["naive"] - scores[best] < MIN_GAIN:
        best = "naive"
    tie_break = bool(best == "naive"
                 and scores.get(TIE_METHOD, np.inf) - scores["naive"] <= TIE_MARGIN)
    if tie_break:
        best = TIE_METHOD
    return best, scores, tie_break


def choose_long(summ, short):
    """A different method for months 4-6 if it beats the champion at 6 months by LONG_MIN_GAIN."""
    s = summ[(summ.h == LONG_H) & (summ.n >= MIN_TARGETS)].set_index("method")["mape"]
    if short not in s.index or len(s) < 2:
        return None
    # "naive" is never a long-range method: blending a drift into it bends the
    # path back to today's price (rise for 3 months, then fall) - a shape no
    # one expects; it only happened where the tie-break had picked the drift.
    short_mape = s[short]
    # ...nor anything that IS naive in practice: seasonal methods fall back to
    # it when there is too little real history for a seasonal pattern.
    by_h = summ.pivot_table(index="method", columns="h", values="mape")
    same_as_naive = [m for m in by_h.index if m != "naive" and "naive" in by_h.index
                     and np.allclose(by_h.loc[m].values, by_h.loc["naive"].values, equal_nan=True)]
    s = s.drop(index=[m for m in ["naive", short, *same_as_naive] if m in s.index])
    if s.empty:
        return None
    best = s.idxmin()
    return best if short_mape - s[best] >= LONG_MIN_GAIN else None


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
        normal = res[res["age_weeks"] <= GAP_WEEKS]
        summ_choice = summarise(normal) if len(normal) else summ
        if (summ_choice[(summ_choice.method == "naive") & (summ_choice.h == 4)].n.sum() < MIN_TARGETS
                or summ_choice.h.nunique() < len(HORIZONS)):
            summ_choice = summ
        if n4 < MIN_TARGETS:
            logger.warning(f"  {c}: only {n4} real targets — keeping naive, low confidence")
            best, scores, tie_break = "naive", {}, False
        else:
            best, scores, tie_break = choose(summ_choice)
        long_method = choose_long(summ_choice, best) if n4 >= MIN_TARGETS else None
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
            "accuracy_13w": None if one(best, 13, "mape") is None else round(100 - one(best, 13, "mape"), 1),
            # 6 months is the long-range method's when there is one (see drift_models.blended_path)
            "accuracy_26w": (None if one(long_method or best, 26, "mape") is None
                             else round(100 - one(long_method or best, 26, "mape"), 1)),
            "long_method": long_method,
            "accuracy_26w_short_method": None if one(best, 26, "mape") is None else round(100 - one(best, 26, "mape"), 1),
            "naive_accuracy_26w": None if one("naive", 26, "mape") is None else round(100 - one("naive", 26, "mape"), 1),
            "weighted_mape": {m: (None if not np.isfinite(v) else round(float(v), 2)) for m, v in scores.items()},
            "naive_accuracy_4w": None if one("naive", 4, "mape") is None else round(100 - one("naive", 4, "mape"), 1),
            "direction_hit_4w": one(best, 4, "dir_hit"),
            "low_confidence": n4 < 12,
            # same, only where the latest real price was at most FRESH_WEEKS old
            "accuracy_4w_fresh_data": fresh_acc(res, best),
            # the forecast ranges' starting point when today's data is fresh (05_forecast)
            "error_fresh_data_pct": {str(h): fresh_err(res, long_method if (h == LONG_H and long_method) else best, h)
                                     for h in HORIZONS},
            "tie_break": tie_break,
            "updated": today,
        }
        if n4 < MIN_TARGETS:
            # One or two real prices can't measure accuracy (Yellow maize had
            # 1 AFEX price on 15 Oct 2026 and scored "99.1%"). Report none, so
            # the dashboard confidence and the averages leave it out.
            for k in ("mape_4w", "mape_8w", "accuracy_4w", "accuracy_4w_fresh_data", "accuracy_8w", "accuracy_13w",
                      "accuracy_26w", "accuracy_26w_short_method", "naive_accuracy_4w", "naive_accuracy_26w",
                      "direction_hit_4w"):
                out[c][k] = None
            out[c]["error_fresh_data_pct"] = {}
            out[c]["note"] = f"only {n4} real price(s) to test against - accuracy not measured yet"
        logger.info(f"  {c:<16} -> {best:<12}{' (tie)' if tie_break else ''}"
                    f"{f' then {long_method} (6 mo)' if long_method else ''} acc 4w={out[c]['accuracy_4w']}% 26w={out[c]['accuracy_26w']}% "
                    f"(naive {out[c]['naive_accuracy_4w']}% / {out[c]['naive_accuracy_26w']}%) n={n4}")
    with open(SELECTION, "w") as f:
        json.dump(out, f, indent=2)
    accs = [v["accuracy_4w"] for v in out.values() if v["accuracy_4w"] is not None]
    if accs:
        logger.info(f"  Mean 4-week accuracy on real prices: {np.mean(accs):.1f}% "
                    f"({sum(a >= 95 for a in accs)}/{len(accs)} commodities >=95%)")
    return out


if __name__ == "__main__":
    run_selection()
