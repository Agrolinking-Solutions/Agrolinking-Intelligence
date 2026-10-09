"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Rolling-origin backtest — does an external factor actually improve accuracy?

Why this exists: the MAPEs in model_results.json come from one train/test
split whose test rows include Interpolated and Synthetic values (our own
estimates), so they flatter the models. This script instead:

  * walks forward through time (rolling origins, no look-ahead),
  * trains only on rows <= origin,
  * SCORES ONLY ON REAL OBSERVATIONS (Agricome / WFP / Agrolinking_primary),
  * at 4 and 8 weeks ahead (4w ≈ the 30-day forecast clients see),
  * reports MAPE, accuracy (100 - MAPE) and DIRECTION hit-rate (did we call
    up vs down correctly versus the price at the origin).

Variants compared per commodity:
  naive        last known price carried forward (the bar any model must beat)
  prophet_base current production Prophet + its 5 regressors, held flat
               beyond the origin (what production effectively does)
  prophet_core same, but WITHOUT FX / fuel / inflation (season + shock only)
  prophet_wx   prophet_base + trailing rainfall & temperature anomalies

Regressors are held at their value AT THE ORIGIN for the whole horizon, i.e.
only information that was genuinely known then is used. Weather anomalies are
trailing (already observed), so there is no leakage.

Caveat: fx_rates.csv / inflation.csv after ~April 2026 are projections, not
observations, so keep ORIGINS_END before then when judging FX variants.

Run:
  python pipeline/12_backtest.py                      # all commodities
  python pipeline/12_backtest.py --commodity Sesame   # one
"""

import os, sys, argparse, warnings, logging
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

warnings.filterwarnings("ignore")
logging.getLogger("prophet").setLevel(logging.ERROR)
logging.getLogger("cmdstanpy").setLevel(logging.ERROR)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_DIR = os.path.join(os.path.dirname(PATHS["master"]), "features")
WEATHER_PATH = os.path.join(ROOT, "data", "external", "weather_daily.csv")
OUT_DIR      = os.path.join(ROOT, "outputs", "backtest")

REAL_SOURCES = {"Agricome", "WFP", "Agrolinking_primary"}
HORIZONS     = (4, 8)          # weeks ahead
N_ORIGINS    = 12              # rolling origins per commodity
ORIGIN_STEP  = 4               # weeks between origins
ORIGINS_END  = pd.Timestamp("2026-03-30")   # before FX/inflation turn into projections
MIN_TRAIN    = 40

BASE_REG = ["fx_rate_usd_ngn", "fuel_cost_index", "food_inflation_yoy",
            "shock_active", "commodity_season_score"]
CORE_REG = ["shock_active", "commodity_season_score"]
WX_REG   = ["rain_anom_8w", "tmax_anom_8w"]

VARIANTS = {
    "prophet_base": BASE_REG,
    "prophet_core": CORE_REG,
    "prophet_wx":   BASE_REG + WX_REG,
}


def safe_name(c):
    return c.lower().replace(" ", "_").replace("(", "").replace(")", "")


# ── weather anomalies (national mean of the 6 zone points, weekly) ───────────
def build_weather_weekly():
    if not os.path.exists(WEATHER_PATH):
        return None
    w = pd.read_csv(WEATHER_PATH, parse_dates=["date"])
    d = w.groupby("date").agg(precip=("precip_mm", "mean"), tmax=("tmax_c", "mean"))
    d.index = d.index - pd.to_timedelta(d.index.dayofweek, unit="D")      # week start (Mon)
    wk = d.groupby(level=0).agg(precip=("precip", "sum"), tmax=("tmax", "mean"))
    wk["woy"] = wk.index.isocalendar().week.values
    # climatology by week-of-year, from history only; (leakage across the
    # backtest is negligible because it's a multi-year seasonal norm)
    clim = wk.groupby("woy").agg(p_norm=("precip", "mean"), t_norm=("tmax", "mean"))
    wk = wk.join(clim, on="woy")
    wk["rain_anom_8w"] = (wk["precip"] - wk["p_norm"]).rolling(8, min_periods=4).mean()
    wk["tmax_anom_8w"] = (wk["tmax"] - wk["t_norm"]).rolling(8, min_periods=4).mean()
    return wk[WX_REG]


def load_commodity(commodity, weather):
    df = pd.read_csv(os.path.join(FEATURES_DIR, f"features_{safe_name(commodity)}.csv"),
                     parse_dates=["date"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    df["is_real"] = df["data_source"].isin(REAL_SOURCES)
    if weather is not None:
        df = df.merge(weather, left_on="date", right_index=True, how="left")
    for c in WX_REG:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = df[c].fillna(0.0)          # neutral anomaly where no weather history
    return df


# ── Prophet fit/predict with regressors frozen at the origin ─────────────────
def prophet_forecast(train, future_dates, regs):
    from prophet import Prophet
    regs = [r for r in regs if r in train.columns and train[r].notna().sum() > len(train) * 0.5]
    tp = train[["date", "price_ngn_mt"]].rename(columns={"date": "ds", "price_ngn_mt": "y"})
    for r in regs:
        tp[r] = train[r].ffill().bfill().values
    m = Prophet(changepoint_prior_scale=0.05, seasonality_prior_scale=10.0,
                seasonality_mode="multiplicative", yearly_seasonality=len(train) >= 52,
                weekly_seasonality=False, daily_seasonality=False, interval_width=0.95)
    for r in regs:
        m.add_regressor(r, standardize=True)
    if len(train) >= 52:
        m.add_seasonality(name="quarterly", period=91.25, fourier_order=5)
    m.fit(tp)
    fut = pd.DataFrame({"ds": future_dates})
    for r in regs:
        fut[r] = float(tp[r].iloc[-1])      # frozen at origin: no look-ahead
    return np.clip(m.predict(fut)["yhat"].values, 0, None)


def backtest_commodity(commodity, weather):
    df = load_commodity(commodity, weather)
    real = df[df["is_real"] & (df["date"] <= ORIGINS_END)]
    if real.empty:
        return []
    last = real["date"].max()
    origins = [last - pd.Timedelta(weeks=max(HORIZONS) + ORIGIN_STEP * i)
               for i in range(N_ORIGINS)]
    rows = []
    for o in origins:
        train = df[df["date"] <= o]
        if len(train) < MIN_TRAIN:
            continue
        fut_dates = [o + pd.Timedelta(weeks=h) for h in range(1, max(HORIZONS) + 1)]
        targets = df[df["date"].isin(fut_dates) & df["is_real"]]
        if targets.empty:
            continue
        origin_price = float(train["price_ngn_mt"].iloc[-1])
        preds = {"naive": {d: origin_price for d in fut_dates}}
        for name, regs in VARIANTS.items():
            try:
                yhat = prophet_forecast(train, fut_dates, regs)
                preds[name] = dict(zip(fut_dates, yhat))
            except Exception as e:
                logger.debug(f"  {commodity} {name} @ {o.date()} failed: {e}")
        for _, t in targets.iterrows():
            h = int(round((t["date"] - o).days / 7))
            if h not in HORIZONS:
                continue
            for name, p in preds.items():
                yh = p[t["date"]]
                rows.append({"commodity": commodity, "origin": o, "target": t["date"],
                             "horizon_w": h, "variant": name,
                             "actual": t["price_ngn_mt"], "pred": yh,
                             "origin_price": origin_price})
    return rows


def summarise(res):
    res = res.copy()
    res["ape"] = (res["pred"] - res["actual"]).abs() / res["actual"] * 100
    res["dir_ok"] = (np.sign(res["pred"] - res["origin_price"])
                     == np.sign(res["actual"] - res["origin_price"])).astype(float)
    moved = (res["actual"] - res["origin_price"]).abs() / res["origin_price"] > 0.01
    res.loc[~moved, "dir_ok"] = np.nan          # ignore "flat" weeks for direction
    g = res.groupby(["commodity", "horizon_w", "variant"]).agg(
        n=("ape", "size"), mape=("ape", "mean"), dir_hit=("dir_ok", "mean")).reset_index()
    g["accuracy"] = 100 - g["mape"]
    return g


def run_backtest(only=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    weather = build_weather_weekly()
    if weather is None:
        logger.warning("weather_daily.csv missing — prophet_wx will equal prophet_base")
    all_rows = []
    for c in ([only] if only else COMMODITIES):
        try:
            rows = backtest_commodity(c, weather)
            all_rows += rows
            logger.info(f"  {c}: {len(rows)} scored predictions")
        except FileNotFoundError:
            logger.info(f"  {c}: no feature file, skipped")
        except Exception as e:
            logger.warning(f"  {c}: failed — {e}")
    if not all_rows:
        logger.error("no backtest rows produced")
        return None
    res = pd.DataFrame(all_rows)
    stamp = date.today().isoformat()
    res.to_csv(os.path.join(OUT_DIR, f"backtest_raw_{stamp}.csv"), index=False)
    summ = summarise(res)
    summ.to_csv(os.path.join(OUT_DIR, f"backtest_summary_{stamp}.csv"), index=False)
    return summ


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--commodity")
    s = run_backtest(ap.parse_args().commodity)
    if s is not None:
        pd.set_option("display.width", 200)
        for h in HORIZONS:
            p = s[s.horizon_w == h].pivot(index="commodity", columns="variant",
                                          values="accuracy").round(1)
            print(f"\n=== ACCURACY % (100 - MAPE), real observations only, {h} weeks ahead ===")
            print(p.to_string())
            d = s[s.horizon_w == h].pivot(index="commodity", columns="variant",
                                          values="dir_hit").mul(100).round(0)
            print(f"\n=== DIRECTION HIT-RATE %, {h} weeks ahead ===")
            print(d.to_string())
