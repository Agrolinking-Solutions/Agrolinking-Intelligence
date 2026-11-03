"""
Research: do world prices improve the export-crop forecasts?

Idea (Nov 2026): Nigerian prices of export crops should follow the world
price times the naira exchange rate, with a lag - and the platform already
reads world benchmarks daily. This is a backtest only - nothing here changes
the live forecasts.

World prices: IMF monthly averages in US$/tonne (via FRED), cached in
data/research/world_prices_imf_monthly.csv. Only cocoa (PCOCOUSDM) and
soybeans (PSOYBUSDM) have one: sesame, cashew and ginger are not traded on
an exchange and have no free world price history, so they can't be tested.
Exchange rate: CBN official (data/external/fx_rates.csv).

No look-ahead: a month's world average counts as known PUBLISH_LAG_DAYS
after the month ends; the exchange rate is the latest weekly rate on or
before the forecast date.

Variants, all with fixed coefficients (Agricome history starts Jan 2025 -
far too short to estimate any):
  catchup L,b  the gap between world x FX's move and our price's move over
               the last L months closes by b: if world x FX rose 20% and
               our price 5%, expect +b x 15%
  parity k,a   our price reverts towards its usual ratio to world x FX
               (median over the past 26 weeks), a of the gap per month

Scored on the fair audit's own rows (outputs/backtest/forecast_audit_rows.csv,
from scripts/research/forecast_audit.py: same start price, same targets, same
real prices) against "price holds" and the method the audit chose at the time.
Adopt only if a variant beats that method by at least 1 point at 1 AND 3
months, in both halves of the period.

Writes outputs/backtest/world_price_test.json.
Run: python scripts/research/world_price_backtest.py [--refresh]
"""

import os, sys, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from config.settings import PATHS

CACHE = os.path.join(ROOT, "data", "research", "world_prices_imf_monthly.csv")
AUDIT_ROWS = os.path.join(ROOT, "outputs", "backtest", "forecast_audit_rows.csv")
OUT = os.path.join(ROOT, "outputs", "backtest", "world_price_test.json")
SERIES = {"Cocoa": "PCOCOUSDM", "Soybeans": "PSOYBUSDM"}
UNTESTABLE = {"Sesame": "no exchange-traded world price", "Cashew Nuts": "no exchange-traded world price",
              "Ginger": "no exchange-traded world price"}
PUBLISH_LAG_DAYS = 15
LOCAL_SOURCES = {"Agricome"}       # the only real price these crops have
VARIANTS = [("catchup", 1, 0.5), ("catchup", 1, 1.0), ("catchup", 3, 0.5), ("catchup", 3, 1.0),
            ("parity", 26, 0.25), ("parity", 26, 0.5)]
ADOPT_GAIN = 1.0


def refresh_cache():
    frames = []
    for c, sid in SERIES.items():
        s = pd.read_csv(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")
        s.columns = ["month", "usd_per_tonne"]
        s["usd_per_tonne"] = pd.to_numeric(s["usd_per_tonne"], errors="coerce")
        frames.append(s.dropna().assign(commodity=c, series=f"IMF {sid} via FRED"))
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    pd.concat(frames).to_csv(CACHE, index=False)


class Known:
    """World x FX (NGN/t) and our real prices, as known on a given date."""

    def __init__(self, commodity):
        w = pd.read_csv(CACHE, parse_dates=["month"])
        w = w[w["commodity"] == commodity].set_index("month")["usd_per_tonne"].sort_index()
        self.world = w
        self.known_from = w.index + pd.offsets.MonthEnd(0) + pd.Timedelta(days=PUBLISH_LAG_DAYS)
        fx = pd.read_csv(PATHS["fx_rates"], parse_dates=["date"]).set_index("date")["fx_rate_usd_ngn"]
        self.fx = fx.dropna().sort_index()
        self.fx_month = self.fx.resample("MS").mean()
        m = pd.read_csv(PATHS["master"], parse_dates=["date"])
        self.local = (m[(m["commodity"] == commodity) & m["data_source"].isin(LOCAL_SOURCES)]
                      .set_index("date")["price_ngn_mt"].sort_index())

    def wfx_now(self, d):
        """Latest published world month x latest exchange rate, on date d."""
        months = self.world[self.known_from <= d]
        fx = self.fx[self.fx.index <= d]
        return None if months.empty or fx.empty else months.iloc[-1] * fx.iloc[-1]

    def published(self, month_start, d):
        """Was this month's world average out by date d?"""
        return month_start in self.world.index and self.known_from[self.world.index.get_loc(month_start)] <= d

    def wfx_month(self, month_start):
        if month_start not in self.world.index or month_start not in self.fx_month.index:
            return None
        return self.world[month_start] * self.fx_month[month_start]

    def local_at(self, d):
        s = self.local[self.local.index <= d]
        return (s.iloc[-1], s.index[-1]) if len(s) else (None, None)


def forecast(k, origin, start, h, kind, p1, p2):
    """One variant's forecast for origin + h weeks, from start price `start`."""
    now = k.wfx_now(origin)
    if now is None or not start:
        return None
    if kind == "catchup":
        L, b = p1, p2
        back = origin - pd.DateOffset(months=L)
        then = k.wfx_now(back)
        local_then, _ = k.local_at(back)
        if not then or not local_then:
            return None
        gap = np.log(now / then) - np.log(start / local_then)
        return start * np.exp(b * gap)
    # parity: our usual ratio to world x FX over the past p1 weeks (each real
    # price against that month's world x FX - all published by the origin)
    a = p2
    hist = k.local[(k.local.index <= origin) & (k.local.index > origin - pd.Timedelta(weeks=p1))]
    ratios = []
    for d, p in hist.items():
        month = d.replace(day=1)
        if k.published(month, origin) and k.wfx_month(month):
            ratios.append(p / k.wfx_month(month))
    if len(ratios) < 6:
        return None
    target = np.median(ratios) * now
    a_h = 1 - (1 - a) ** (h / 4)
    return start * np.exp(a_h * np.log(target / start))


def acc(pred, actual):
    return round(float(100 - ((pred - actual).abs() / actual * 100).mean()), 1)


def main():
    if "--refresh" in sys.argv or not os.path.exists(CACHE):
        refresh_cache()
    rows = pd.read_csv(AUDIT_ROWS, parse_dates=["target", "origin"])
    report = {"world_prices": "IMF monthly via FRED, cached in data/research/world_prices_imf_monthly.csv",
              "untestable": UNTESTABLE, "adopt_rule": f"beat the audit's method by >= {ADOPT_GAIN} pt at 1 and "
                                                      "3 months, in both halves of the period",
              "commodities": {}}
    for c in SERIES:
        k = Known(c)
        d = rows[rows["commodity"] == c].copy()
        names = []
        for kind, p1, p2 in VARIANTS:
            name = f"{kind} {p1},{p2}"
            names.append(name)
            d[name] = [forecast(k, r.origin, r.p_naive, r.h, kind, p1, p2) for r in d.itertuples()]
        d = d.dropna(subset=names)
        mid = d["target"].sort_values().iloc[len(d) // 2]
        res = {}
        for h in sorted(d["h"].unique()):
            x = d[d["h"] == h]
            halves = (x[x["target"] < mid], x[x["target"] >= mid])
            res[f"{h}w"] = {"n": int(len(x)), "price_holds": acc(x["p_naive"], x["actual"]),
                            "audit_method": acc(x["pred"], x["actual"]),
                            "variants": {n: acc(x[n], x["actual"]) for n in names},
                            "halves": [{"price_holds": acc(hx["p_naive"], hx["actual"]),
                                        "audit_method": acc(hx["pred"], hx["actual"]),
                                        **{n: acc(hx[n], hx["actual"]) for n in names}}
                                       for hx in halves if len(hx)]}
        adopt = [n for n in names if all(
            half[n] - half["audit_method"] >= ADOPT_GAIN
            for w in ("4w", "13w") if w in res for half in res[w]["halves"])]
        report["commodities"][c] = {"scored_from": str(d["target"].min().date()), "horizons": res,
                                    "passes_adopt_rule": adopt}
        print(f"\n{c}  (targets {d['target'].min().date()} to {d['target'].max().date()})")
        print(f"  {'':16}" + "".join(f"{h:>9}" for h in res))
        for lab, get in [("price holds", lambda v: v["price_holds"]), ("audit method", lambda v: v["audit_method"])] + \
                        [(n, lambda v, n=n: v["variants"][n]) for n in names]:
            print(f"  {lab:16}" + "".join(f"{get(v):>8.1f}%" for v in res.values()))
        print(f"  n per horizon   " + "".join(f"{v['n']:>9}" for v in res.values()))
        print(f"  passes the adopt rule: {adopt or 'none'}")
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
