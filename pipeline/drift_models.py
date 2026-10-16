"""
Simple, fast, backtestable forecasters shared by 13_select_forecaster.py
(which scores them) and 05_forecast.py (which applies the winner).

All of them project from the LAST KNOWN price:

  price(target) = last * exp(g * weeks_ahead + seasonal_shift)

  naive        g = 0
  infl@k       g = k * ln(1 + food_inflation_yoy/100) / 52
               (naira prices drift up with inflation; k damps it)
  trend@k      g = k * trailing 26-week log change / 26   (clipped to ±1.5%/wk)
  mix          average of infl@0.5 and trend@0.5
  season@k     g = 0, seasonal_shift = k * (index[target month] - index[origin month])
  season+infl  season@0.5 plus infl@0.5

The seasonal index is each commodity's typical log-price deviation by
calendar month, learned only from its REAL prices (Agricome/WFP/primary) up to
the forecast origin, so the backtest never sees the future. It needs at least
MIN_SEASON_YEARS of monthly real data with every month covered; otherwise the
season candidates fall back to naive (and can't win).

They exist because the 12_backtest.py run showed the full ML ensemble loses
to naive on real data; these are the bar any model must clear.
"""

import numpy as np
import pandas as pd

CANDIDATES = ["naive", "infl@0.5", "infl@1.0", "trend@0.5", "mix",
              "season@0.5", "season@1.0", "season+infl"]
MAX_WEEKLY_G = 0.015
from config.settings import PARTNER_SOURCES as REAL_SOURCES   # partner data only, no web prices
MIN_SEASON_YEARS = 3

_season_cache = {}


def _infl_g(hist, k):
    if "food_inflation_yoy" not in hist.columns:
        return 0.0
    v = hist["food_inflation_yoy"].dropna()
    if v.empty:
        return 0.0
    return k * np.log1p(float(v.iloc[-1]) / 100.0) / 52.0


def _trend_g(hist, k, lookback=26):
    p = hist["price_ngn_mt"].dropna()
    if len(p) < 8:
        return 0.0
    n = min(lookback, len(p) - 1)
    a, b = float(p.iloc[-1 - n]), float(p.iloc[-1])
    if a <= 0 or b <= 0:
        return 0.0
    return float(np.clip(k * np.log(b / a) / n, -MAX_WEEKLY_G, MAX_WEEKLY_G))


def weekly_growth(method, hist):
    """Constant weekly log-growth part of a method. hist ends at the forecast origin."""
    if method in ("naive", "season@0.5", "season@1.0"):
        return 0.0
    if method == "season+infl":
        return _infl_g(hist, 0.5)
    kind, _, k = method.partition("@")
    if kind == "infl":
        return _infl_g(hist, float(k))
    if kind == "trend":
        return _trend_g(hist, float(k))
    if kind == "mix":
        return 0.5 * _infl_g(hist, 0.5) + 0.5 * _trend_g(hist, 0.5)
    raise ValueError(f"unknown method {method}")


def seasonal_index(hist):
    """
    Typical log deviation of price by calendar month (Series indexed 1..12,
    mean 0), from real prices in hist only. None if there isn't enough data.
    """
    if "data_source" not in hist.columns:
        return None
    real = hist[hist["data_source"].isin(REAL_SOURCES) & (hist["price_ngn_mt"] > 0)]
    if real.empty:
        return None
    key = (real["date"].max(), len(real), round(float(real["price_ngn_mt"].sum()), 2))
    if key in _season_cache:
        return _season_cache[key]
    monthly = np.log(real.set_index("date")["price_ngn_mt"]).resample("MS").mean()
    monthly = monthly.interpolate(limit=2, limit_area="inside")
    result = None
    if monthly.notna().sum() >= 12 * MIN_SEASON_YEARS:
        # deviation from a centred 12-month average removes trend/inflation
        dev = (monthly - monthly.rolling(12, center=True, min_periods=12).mean()).dropna()
        by_month = dev.groupby(dev.index.month)
        if len(by_month) == 12 and by_month.size().min() >= MIN_SEASON_YEARS:
            idx = by_month.mean()
            result = idx - idx.mean()
    _season_cache[key] = result
    return result


_UNSET = object()


def forecast_path(method, hist, last_price, last_date, target_dates, season_idx=_UNSET):
    """
    Forecast prices for each target date from last_price at last_date.
    season_idx: pass a precomputed seasonal_index(hist) to skip recomputing it
    (the backtest calls this ~60k times).
    """
    target_dates = pd.to_datetime(pd.Series(target_dates)).reset_index(drop=True)
    last_date = pd.Timestamp(last_date)
    weeks = np.maximum((target_dates - last_date).dt.days.values / 7.0, 0)
    log_shift = weekly_growth(method, hist) * weeks
    if method.startswith("season"):
        idx = seasonal_index(hist) if season_idx is _UNSET else season_idx
        if idx is not None:
            k = 0.5 if method == "season+infl" else float(method.split("@")[1])
            log_shift = log_shift + k * (idx.loc[target_dates.dt.month].values
                                         - float(idx.loc[last_date.month]))
    return float(last_price) * np.exp(log_shift)


# Long-range blend: some methods win near term and others at 6 months
# (white maize, Oct 2026: "price holds" 5.0% error at 1 month vs seasonal
# 7.8%, but 31.5% vs 26.2% at 6 months). The forecast follows the short-range
# champion up to BLEND_FROM_WEEKS, then moves linearly to the long-range one
# by BLEND_TO_WEEKS, so measured accuracy at 4/8/13 weeks is the short
# method's and at 26 weeks the long method's.
BLEND_FROM_WEEKS = 13
BLEND_TO_WEEKS = 26


def blend_weight(weeks):
    """0 up to BLEND_FROM_WEEKS, 1 from BLEND_TO_WEEKS, linear in between."""
    w = (np.asarray(weeks, dtype=float) - BLEND_FROM_WEEKS) / (BLEND_TO_WEEKS - BLEND_FROM_WEEKS)
    return np.clip(w, 0.0, 1.0)


def blended_path(short_method, long_method, hist, last_price, last_date, target_dates):
    """Short-range champion near term, long-range champion from 6 months; one path if no long method."""
    short = forecast_path(short_method, hist, last_price, last_date, target_dates)
    if not long_method or long_method == short_method:
        return short
    longp = forecast_path(long_method, hist, last_price, last_date, target_dates)
    weeks = np.maximum((pd.to_datetime(pd.Series(target_dates)).reset_index(drop=True)
                        - pd.Timestamp(last_date)).dt.days.values / 7.0, 0)
    w = blend_weight(weeks)
    return (1 - w) * short + w * longp


def project(last_price, g, weeks):
    """Constant-growth projection (kept for callers that only need the trend part)."""
    weeks = np.asarray(weeks, dtype=float)
    return float(last_price) * np.exp(g * weeks)


def point_forecasts(hist, last_price, last_date, target_date, season_idx=_UNSET):
    """
    All CANDIDATES for one target date at once (scalar maths, no pandas per
    candidate) - same results as forecast_path, ~10x faster for the backtest.
    """
    last_date, target_date = pd.Timestamp(last_date), pd.Timestamp(target_date)
    w = max((target_date - last_date).days / 7.0, 0.0)
    i5, i10, t5 = _infl_g(hist, 0.5), _infl_g(hist, 1.0), _trend_g(hist, 0.5)
    idx = seasonal_index(hist) if season_idx is _UNSET else season_idx
    s = 0.0 if idx is None else float(idx.loc[target_date.month] - idx.loc[last_date.month])
    p = float(last_price)
    return {
        "naive":       p,
        "infl@0.5":    p * np.exp(i5 * w),
        "infl@1.0":    p * np.exp(i10 * w),
        "trend@0.5":   p * np.exp(t5 * w),
        "mix":         p * np.exp((0.5 * i5 + 0.5 * t5) * w),
        "season@0.5":  p * np.exp(0.5 * s),
        "season@1.0":  p * np.exp(s),
        "season+infl": p * np.exp(i5 * w + 0.5 * s),
    }
