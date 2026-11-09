"""
Research: do export prices improve the forecasts that say "price holds"?

Idea (Nov 2026): seven commodities forecast a flat price, and the export view
(pipeline/20_export_prices.py) now reads what buyers abroad pay. If Nigerian
prices follow those prices with a lag, they should help. Same test as
world_price_backtest.py; this is a backtest only - nothing here changes the
live forecasts.

Prices abroad (data/raw/export_prices.csv, fetch_export_prices.py):
  - Sesame, Cocoa, Cashew Nuts: Eurostat's monthly EU import price from
    Nigeria (EUR/t), months of EU_MIN_TONNES or more. Cashew is kernels
    against our raw nuts: a different product, but its price should move
    with ours.
  - Rice (foreign): Niger's imported-rice wholesale price (XOF/t), the
    median of its markets each month - a neighbour importing the same rice.
Ginger and soybeans reach the EU tonnage floor too rarely, and hibiscus has
no series, so they can't be tested.

Naira: the latest CBN euro rate on the forecast date (CFA through the fixed
655.957 per euro). No look-ahead: a month counts as known PUBLISH_LAG_DAYS
after it ends (Eurostat's August 2026 came out on 16 Oct; FAO's Niger
October on or before 6 Nov). Eurostat revises recent months, and the file
holds today's figures: a small advantage the live forecasts would not have.

Variants, fixed coefficients (Agricome history starts Jan 2025):
  catchup L,b,s  the gap between the abroad price's move and ours over the
                 last L months closes by b
  parity k,a,s   our price reverts towards its usual ratio to the abroad
                 price (median over the past k weeks), a of the gap a month
  lag L,b,s      the abroad price's move reaches us L months later: our move
                 over the next h weeks = b x its move over the same span L
                 months earlier (as far as published by the forecast date).
                 Added after the first run showed EU cocoa leading
                 Agricome's by ~3 months (correlation 0.72, 16 months) - so
                 it is held to the same adopt rule, with that caveat
  s = 1: the latest published month; s = 3: the median of the last 3
  (EU unit values are noisy month to month).

Scored on the fair audit's own rows (outputs/backtest/forecast_audit_rows.csv)
against "price holds" and the method the audit chose at the time. Adopt only
if a variant beats that method by at least 1 point at 1 AND 3 months, in both
halves of the period. With 11 variants per commodity one can pass by luck, so
a pass is a candidate to look at, not a change.

Also reports how monthly moves line up (correlation of monthly changes, the
price abroad leading by 0-3 months).

Writes outputs/backtest/export_price_test.json.
Run: python scripts/research/export_price_backtest.py
"""

import os, sys, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from config.settings import PATHS, PARTNER_SOURCES, EU_MIN_TONNES, CFA_PER_EUR

EXPORT = os.path.join(ROOT, "data", "raw", "export_prices.csv")
EUR = os.path.join(ROOT, "data", "external", "cbn_eur_daily.csv")
AUDIT_ROWS = os.path.join(ROOT, "outputs", "backtest", "forecast_audit_rows.csv")
OUT = os.path.join(ROOT, "outputs", "backtest", "export_price_test.json")
SIGNALS = {"Sesame": "EU", "Cocoa": "EU", "Cashew Nuts": "EU", "Rice (foreign)": "Niger"}
UNTESTABLE = {"Ginger": "EU imports reach the 10-tonne floor in only 13 of 32 months since 2024",
              "Soybeans": "EU imports reach the 10-tonne floor in only 7 of 29 months since 2024",
              "Hibiscus": "no EU or West African price series"}
PUBLISH_LAG_DAYS = {"EU": 50, "Niger": 35}
VARIANTS = [("catchup", 1, 0.5, 1), ("catchup", 3, 0.5, 1), ("catchup", 3, 1.0, 1),
            ("catchup", 3, 0.5, 3), ("catchup", 3, 1.0, 3),
            ("parity", 26, 0.25, 1), ("parity", 26, 0.5, 1), ("parity", 52, 0.25, 3), ("parity", 52, 0.5, 3),
            ("lag", 3, 0.5, 1), ("lag", 3, 1.0, 1)]
MIN_RATIOS = 4
ADOPT_GAIN = 1.0


def abroad_series(commodity, export):
    """Monthly price abroad in its own currency per tonne, and the naira per unit of that currency."""
    if SIGNALS[commodity] == "EU":
        e = export[(export["region"] == "EU") & (export["commodity"] == commodity)
                   & (export["quantity_t"] >= EU_MIN_TONNES)]
        s = e.groupby("period")["price_local_t"].median()
        per_eur = 1.0
    else:
        e = export[(export["country"] == "Niger") & (export["commodity"] == commodity)
                   & (export["price_type"] == "wholesale")]
        s = e.groupby("period")["price_local_t"].median()
        per_eur = 1 / CFA_PER_EUR
    s.index = pd.to_datetime(s.index)
    return s.sort_index(), per_eur


class Known:
    """The price abroad in naira and our real prices, as known on a given date."""

    def __init__(self, commodity, export, eur, master):
        self.abroad, self.per_eur = abroad_series(commodity, export)
        lag = pd.Timedelta(days=PUBLISH_LAG_DAYS[SIGNALS[commodity]])
        self.known_from = self.abroad.index + pd.offsets.MonthEnd(0) + lag
        self.eur = eur
        self.eur_month = eur.resample("MS").mean()
        self.local = (master[(master["commodity"] == commodity) & master["data_source"].isin(PARTNER_SOURCES)]
                      .set_index("date")["price_ngn_mt"].sort_index())

    def fx(self, d):
        r = self.eur[self.eur.index <= d]
        return None if r.empty else r.iloc[-1] * self.per_eur

    def level(self, d, s):
        """The price abroad known on date d (median of the last s published months), in its own currency."""
        months = self.abroad[self.known_from <= d]
        return None if len(months) < s else float(months.iloc[-s:].median())

    def naira_now(self, d, s):
        lv, fx = self.level(d, s), self.fx(d)
        return None if lv is None or fx is None else lv * fx

    def naira_month(self, month):
        if month not in self.abroad.index or month not in self.eur_month.index:
            return None
        return self.abroad[month] * self.eur_month[month] * self.per_eur

    def published(self, month, d):
        return month in self.abroad.index and self.known_from[self.abroad.index.get_loc(month)] <= d

    def local_at(self, d):
        s = self.local[self.local.index <= d]
        return (s.iloc[-1], s.index[-1]) if len(s) else (None, None)


def forecast(k, origin, start, h, kind, p1, p2, smooth):
    """One variant's forecast for origin + h weeks, from start price `start`."""
    now = k.naira_now(origin, smooth)
    if now is None or not start:
        return None
    if kind == "catchup":
        back = origin - pd.DateOffset(months=p1)
        then = k.naira_now(back, smooth)
        local_then, _ = k.local_at(back)
        if not then or not local_then:
            return None
        gap = np.log(now / then) - np.log(start / local_then)
        return start * np.exp(p2 * gap)
    if kind == "lag":
        a0 = origin - pd.DateOffset(months=p1)
        a1 = min(origin + pd.Timedelta(weeks=h) - pd.DateOffset(months=p1), origin)
        then, later = k.naira_now(a0, smooth), k.naira_now(a1, smooth)
        if not then or not later:
            return None
        return start * np.exp(p2 * np.log(later / then))
    hist =k.local[(k.local.index <= origin) & (k.local.index > origin - pd.Timedelta(weeks=p1))]
    ratios = []
    for d, p in hist.items():
        month = d.replace(day=1)
        if k.published(month, origin) and k.naira_month(month):
            ratios.append(p / k.naira_month(month))
    if len(ratios) < MIN_RATIOS:
        return None
    target = np.median(ratios) * now
    a_h = 1 - (1 - p2) ** (h / 4)
    return start * np.exp(a_h * np.log(target / start))


def lead_lag(k):
    """Correlation of monthly log changes: the price abroad (naira) leading ours by 0-3 months."""
    local = k.local.resample("MS").median().dropna()
    abroad = pd.Series({m: k.naira_month(m) for m in k.abroad.index}).dropna()
    both = pd.concat({"local": np.log(local).diff(), "abroad": np.log(abroad.astype(float)).diff()}, axis=1,
                     sort=True)
    out = {}
    for lag in range(4):
        x = pd.concat([both["local"], both["abroad"].shift(lag)], axis=1).dropna()
        out[f"abroad leads {lag} month(s)"] = {"corr": round(float(x.corr().iloc[0, 1]), 2) if len(x) > 2 else None,
                                               "months": int(len(x))}
    return out


def acc(pred, actual):
    return round(float(100 - ((pred - actual).abs() / actual * 100).mean()), 1)


def main():
    export = pd.read_csv(EXPORT)
    eur = pd.read_csv(EUR, parse_dates=["date"]).set_index("date")["ngn_per_eur"].dropna().sort_index()
    master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    rows = pd.read_csv(AUDIT_ROWS, parse_dates=["target", "origin"])
    report = {"prices_abroad": {"EU": "Eurostat Comext DS-045409, EU imports from Nigeria (EUR/t, months of "
                                      f"{EU_MIN_TONNES} t or more)",
                                "Niger": "FAO FPMA, Niger imported-rice wholesale, median of markets (XOF/t)"},
              "publish_lag_days": PUBLISH_LAG_DAYS, "untestable": UNTESTABLE,
              "adopt_rule": f"beat the audit's method by >= {ADOPT_GAIN} pt at 1 and 3 months, in both halves "
                            "of the period", "commodities": {}}
    for c in SIGNALS:
        k = Known(c, export, eur, master)
        d = rows[rows["commodity"] == c].copy()
        names = []
        for kind, p1, p2, s in VARIANTS:
            name = f"{kind} {p1},{p2},s{s}"
            names.append(name)
            d[name] = [forecast(k, r.origin, r.p_naive, r.h, kind, p1, p2, s) for r in d.itertuples()]
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
        ll = lead_lag(k)
        report["commodities"][c] = {"price_abroad": SIGNALS[c], "scored_from": str(d["target"].min().date()),
                                    "scored_to": str(d["target"].max().date()), "split_at": str(mid.date()),
                                    "horizons": res, "passes_adopt_rule": adopt, "monthly_moves": ll}
        print(f"\n{c}  vs {SIGNALS[c]}  (targets {d['target'].min().date()} to {d['target'].max().date()}, "
              f"halves split {mid.date()})")
        print(f"  {'':20}" + "".join(f"{h:>9}" for h in res))
        for lab, get in [("price holds", lambda v: v["price_holds"]), ("audit method", lambda v: v["audit_method"])] + \
                        [(n, lambda v, n=n: v["variants"][n]) for n in names]:
            print(f"  {lab:20}" + "".join(f"{get(v):>8.1f}%" for v in res.values()))
        print(f"  n per horizon       " + "".join(f"{v['n']:>9}" for v in res.values()))
        print(f"  monthly moves:      " + ", ".join(f"lead {i}: {v['corr']} ({v['months']} mo)"
                                                    for i, v in enumerate(ll.values())))
        print(f"  passes the adopt rule: {adopt or 'none'}")
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
