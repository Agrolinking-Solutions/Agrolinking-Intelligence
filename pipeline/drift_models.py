"""
Simple, fast, backtestable forecasters shared by 13_select_forecaster.py
(which scores them) and 05_forecast.py (which applies the winner).

All of them project from the LAST KNOWN price with a constant weekly
log-growth g:   price(h weeks ahead) = last * exp(g * h)

  naive    g = 0
  infl@k   g = k * ln(1 + food_inflation_yoy/100) / 52
           (naira prices drift up with inflation; k damps it)
  trend@k  g = k * trailing 26-week log change / 26   (clipped to ±1.5%/wk)
  mix      average of infl@0.5 and trend@0.5

They exist because the 12_backtest.py run showed the full ML ensemble loses
to naive on real data; these are the bar any model must clear, and they fix
the main structural gap (flat/declining forecasts while Nigerian prices
rise with inflation).
"""

import numpy as np

CANDIDATES = ["naive", "infl@0.5", "infl@1.0", "trend@0.5", "mix"]
MAX_WEEKLY_G = 0.015


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
    """hist: DataFrame sorted by date, ending at the forecast origin."""
    if method == "naive":
        return 0.0
    kind, _, k = method.partition("@")
    if kind == "infl":
        return _infl_g(hist, float(k))
    if kind == "trend":
        return _trend_g(hist, float(k))
    if kind == "mix":
        return 0.5 * _infl_g(hist, 0.5) + 0.5 * _trend_g(hist, 0.5)
    raise ValueError(f"unknown method {method}")


def project(last_price, g, weeks):
    weeks = np.asarray(weeks, dtype=float)
    return float(last_price) * np.exp(g * weeks)
