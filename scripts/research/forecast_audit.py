"""
Forecast accuracy audit - every commodity, every horizon, honestly.

What the daily method selection (13_select_forecaster.py) reports is the
accuracy of the method that scored best over the last two years. This audit
checks how much of that holds up when tested the way the forecasts are
actually used:

  1. No leak from filled-in prices. A past forecast starts from the latest
     REAL price known at the time. Interpolated rows after it were drawn
     towards the NEXT real price, so they are dropped (and model rows), and
     the result is compared with the leaky start the selection step uses.
  2. Method chosen fairly. At every forecast date the method is picked with
     the same rules as 13_select_forecaster (weighted error over 4/8/13/26
     weeks, MIN_GAIN, inflation tie-break, long-range method at 6 months)
     using only results known by then (targets on or before the forecast
     date, last 104 weeks), and - like the live step - on test forecasts made
     with real data at most GAP_WEEKS old when there are enough of them.
  3. Scored on real prices from 2016 at 4, 8, 13 and 26 weeks: error,
     error of "price holds", bias (too high or too low), whether the shown
     80% range (drift_models.range_halfwidth, widened for old data)
     contained the real price, and error by year.

Writes outputs/backtest/forecast_audit.json and prints a scorecard.
Run: python scripts/research/forecast_audit.py [--rebuild] [--no-variants]
"""

import os, sys, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from config.settings import PATHS, COMMODITIES, PARTNER_SOURCES
from pipeline.drift_models import CANDIDATES, point_forecasts, range_halfwidth

FEATURES_DIR = os.path.join(os.path.dirname(PATHS["master"]), "features")
OUT = os.path.join(ROOT, "outputs", "backtest", "forecast_audit.json")
ROWS_OUT = os.path.join(ROOT, "outputs", "backtest", "forecast_audit_rows.csv")
RECENT_WEEKS = 104    # also score the last two years, the window the daily selection reports on
HORIZONS = (4, 8, 13, 26)
HORIZON_W = {4: 0.4, 8: 0.3, 13: 0.2, 26: 0.1}
WINDOW_WEEKS, MIN_TARGETS, MIN_GAIN = 104, 6, 0.3
TIE_METHOD, TIE_MARGIN, LONG_MIN_GAIN = "infl@0.5", 0.3, 1.0
SCORE_FROM = "2016-01-01"
BAND_K = 1.6          # the old fixed rule (half-width = 1.6 x mean abs error), kept as a variant
GAP_WEEKS = 12        # same as 13_select_forecaster: choose methods on normal-condition pairs
FRESH_WEEKS = 4       # same as 13_select_forecaster: "fresh data" is a latest real price at most this old
NOT_KNOWN_YET = {"Interpolated", "Agrolinking_validated"}


def safe_name(c):
    return c.lower().replace(" ", "_").replace("(", "").replace(")", "")


def pairs_for(commodity):
    df = pd.read_csv(os.path.join(FEATURES_DIR, f"features_{safe_name(commodity)}.csv"), parse_dates=["date"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    df = df[[c for c in ("date", "price_ngn_mt", "data_source", "food_inflation_yoy") if c in df.columns]]
    real = df[df["data_source"].isin(PARTNER_SOURCES)]
    rows = []
    for _, t in real[real["date"] >= pd.Timestamp(SCORE_FROM) - pd.Timedelta(weeks=WINDOW_WEEKS + 26)].iterrows():
        for h in HORIZONS:
            o = t["date"] - pd.Timedelta(weeks=h)
            leaky = df[df["date"] <= o]
            if len(leaky) < 20:
                continue
            real_before = leaky[leaky["data_source"].isin(PARTNER_SOURCES)]
            if real_before.empty:
                continue
            last_real = real_before["date"].max()
            clean = leaky[~(leaky["data_source"].isin(NOT_KNOWN_YET) & (leaky["date"] > last_real))]
            p_clean = point_forecasts(clean, float(clean["price_ngn_mt"].iloc[-1]), clean["date"].iloc[-1], t["date"])
            leaky_start = float(leaky["price_ngn_mt"].iloc[-1])
            row = {"commodity": commodity, "target": t["date"], "h": h, "origin": o, "actual": float(t["price_ngn_mt"]),
                   "naive_leaky": leaky_start, "start_was_filled": bool(leaky["data_source"].iloc[-1] in NOT_KNOWN_YET),
                   "real_age_weeks": (o - last_real).days / 7}
            row.update({f"p_{m}": float(p_clean[m]) for m in CANDIDATES})
            rows.append(row)
    return pd.DataFrame(rows)


BASELINE = {"min_gain": MIN_GAIN, "tie": True, "long": True, "band": ("calibrated", None), "gap": True}


def choose(avail, cfg=BASELINE):
    """Same rules as 13_select_forecaster on the pairs known at the time. Returns (short, long, mape_by_h)."""
    if avail.empty or (avail["h"] == 4).sum() < MIN_TARGETS:
        return "naive", None, {}
    ape = {m: (avail[f"p_{m}"] - avail["actual"]).abs() / avail["actual"] * 100 for m in CANDIDATES}
    by_h = {m: ape[m].groupby(avail["h"]).mean() for m in CANDIDATES}
    if not all(h in by_h["naive"].index for h in HORIZONS):
        return "naive", None, {m: by_h[m].to_dict() for m in CANDIDATES}
    score = {m: sum(HORIZON_W[h] * by_h[m].get(h, np.inf) for h in HORIZONS) for m in CANDIDATES}
    best = min(score, key=score.get)
    if best != "naive" and score["naive"] - score[best] < cfg["min_gain"]:
        best = "naive"
    if cfg["tie"] and best == "naive" and score[TIE_METHOD] - score["naive"] <= TIE_MARGIN:
        best = TIE_METHOD
    long = None
    if not cfg["long"]:
        return best, None, {m: by_h[m].to_dict() for m in CANDIDATES}
    l26 = {m: by_h[m].get(26, np.inf) for m in CANDIDATES}
    same_as_naive = {m for m in CANDIDATES if m != "naive" and np.allclose(
        [by_h[m].get(h, np.nan) for h in HORIZONS], [by_h["naive"].get(h, np.nan) for h in HORIZONS], equal_nan=True)}
    others = {m: v for m, v in l26.items() if m not in {"naive", best} | same_as_naive}
    if others:
        cand = min(others, key=others.get)
        if l26[best] - others[cand] >= LONG_MIN_GAIN:
            long = cand
    return best, long, {m: by_h[m].to_dict() for m in CANDIDATES}


def nested(pairs, cfg=BASELINE):
    pairs = pairs.sort_values("target").reset_index(drop=True)
    out = []
    for i, r in pairs.iterrows():
        if r["target"] < pd.Timestamp(SCORE_FROM):
            continue
        avail = pairs[(pairs["target"] <= r["origin"]) & (pairs["target"] > r["origin"] - pd.Timedelta(weeks=WINDOW_WEEKS))]
        normal = avail[avail["real_age_weeks"] <= GAP_WEEKS]
        use_normal = (cfg.get("gap") and (normal["h"] == 4).sum() >= MIN_TARGETS
                      and all(h in set(normal["h"]) for h in HORIZONS))
        short, long, _ = choose(normal if use_normal else avail, cfg)
        _, _, mape = choose(avail, {**cfg, "long": False})     # ranges use the error on all pairs, as the live step reports
        fresh_av = avail[avail["real_age_weeks"] <= FRESH_WEEKS]
        method = long if (long and r["h"] == 26) else short
        pred = r[f"p_{method}"]
        kind, k = cfg["band"]
        m_err = mape.get(method, {}).get(r["h"]) if mape else None
        m_err = m_err if m_err is not None and m_err == m_err else 10.0
        f = fresh_av[fresh_av["h"] == r["h"]]
        f_err = (float(((f[f"p_{method}"] - f["actual"]).abs() / f["actual"]).mean() * 100)
                 if len(f) >= MIN_TARGETS else None)
        if kind == "calibrated":     # as 05_forecast: fresh-data error when today's data is fresh
            base = f_err if (r["real_age_weeks"] <= FRESH_WEEKS and f_err is not None) else m_err
            hw = float(range_halfwidth(base, r["h"], max(r["real_age_weeks"], 0.0)))
        elif kind == "mae":
            m_err = mape.get(method, {}).get(r["h"]) if mape else None
            hw = min(max(m_err if m_err == m_err and m_err is not None else 10.0, 2.0) / 100 * k, 0.6)
        else:   # quantile of past absolute errors of this method at this horizon
            past = avail[avail["h"] == r["h"]]
            errs = ((past[f"p_{method}"] - past["actual"]).abs() / past["actual"]).values
            hw = float(np.quantile(errs, k / 100)) if len(errs) >= MIN_TARGETS else 0.15
            hw = min(max(hw, 0.02), 0.8)
        out.append({**r.to_dict(), "method": method, "pred": pred, "lo": pred * (1 - hw), "hi": pred * (1 + hw),
                    "err_all": m_err, "err_fresh": f_err})
    return pd.DataFrame(out)


def score(d):
    e = (d["pred"] - d["actual"]) / d["actual"] * 100
    naive = (d["p_naive"] - d["actual"]).abs() / d["actual"] * 100
    leaky = (d["naive_leaky"] - d["actual"]).abs() / d["actual"] * 100
    years = d.assign(ae=e.abs()).groupby(d["target"].dt.year)["ae"].mean().round(1)
    return {"n": int(len(d)), "accuracy_pct": round(100 - e.abs().mean(), 1),
            "naive_accuracy_pct": round(100 - naive.mean(), 1),
            "naive_with_filled_start_accuracy_pct": round(100 - leaky.mean(), 1),
            "share_started_from_filled_price_pct": round(d["start_was_filled"].mean() * 100, 1),
            "bias_pct": round(e.mean(), 1),
            "range_coverage_pct": round(((d["actual"] >= d["lo"]) & (d["actual"] <= d["hi"])).mean() * 100, 1),
            "error_by_year_pct": {int(k): float(v) for k, v in years.items()},
            "methods_used": d["method"].value_counts().to_dict()}


def verdict(s4, s26, reported):
    issues = []
    if s4["accuracy_pct"] < s4["naive_accuracy_pct"] - 0.3:
        issues.append(f"1-month forecast worse than 'price holds' ({s4['accuracy_pct']}% vs {s4['naive_accuracy_pct']}%)")
    if s4["accuracy_pct"] < 90:
        issues.append(f"1-month accuracy below 90% ({s4['accuracy_pct']}%)")
    if abs(s4["bias_pct"]) > 3:
        issues.append(f"1-month forecasts {'too high' if s4['bias_pct'] > 0 else 'too low'} by {abs(s4['bias_pct'])}% on average")
    for s, lab in ((s4, "1-month"), (s26, "6-month")):
        if s and not (70 <= s["range_coverage_pct"] <= 90):
            issues.append(f"{lab} range holds the real price {s['range_coverage_pct']}% of the time (aim 80%)")
    yrs = s4["error_by_year_pct"]
    if len(yrs) >= 3:
        worst = max(yrs, key=yrs.get)
        if yrs[worst] > 2 * (100 - s4["accuracy_pct"]) and yrs[worst] > 10:
            issues.append(f"{worst} much worse than other years ({yrs[worst]}% error)")
    if reported is not None and reported - s4["accuracy_pct"] > 2:
        issues.append(f"reported {reported}% vs {s4['accuracy_pct']}% when tested fairly")
    return ("passes" if not issues else "needs attention"), issues


if __name__ == "__main__":
    sel = json.load(open(os.path.join(ROOT, "outputs", "backtest", "model_selection.json")))
    report = {"scored_from": SCORE_FROM, "commodities": {}}
    all_rows = []
    PAIRS_CACHE = os.path.join(ROOT, "outputs", "backtest", "audit_pairs.csv")
    if os.path.exists(PAIRS_CACHE) and "--rebuild" not in sys.argv:
        cached = pd.read_csv(PAIRS_CACHE, parse_dates=["target", "origin"])
    else:
        built = []
        for c in COMMODITIES:
            if c == "Rice":
                continue
            try:
                built.append(pairs_for(c))
            except FileNotFoundError:
                continue
        cached = pd.concat(built, ignore_index=True)
        cached.to_csv(PAIRS_CACHE, index=False)
    by_c = {c: g for c, g in cached.groupby("commodity")}
    for c in COMMODITIES:
        if c not in by_c:
            continue
        p = by_c[c]
        d = nested(p)
        all_rows.append(d)
        if d.empty or (d["h"] == 4).sum() < MIN_TARGETS:
            report["commodities"][c] = {"verdict": "not enough real prices to test", "n_4w": int((d["h"] == 4).sum()) if len(d) else 0}
            print(f"{c:15} not enough real prices to test")
            continue
        s = {f"{h}w": score(d[d["h"] == h]) for h in HORIZONS if (d["h"] == h).sum() >= MIN_TARGETS}
        rep = (sel.get(c) or {}).get("accuracy_4w")
        recent = d[d["target"] > d["target"].max() - pd.Timedelta(weeks=RECENT_WEEKS)]
        s_recent = {f"{h}w": score(recent[recent["h"] == h]) for h in HORIZONS if (recent["h"] == h).sum() >= MIN_TARGETS}
        v, issues = verdict(s["4w"], s.get("26w"), rep)
        report["commodities"][c] = {"verdict": v, "issues": issues, "reported_accuracy_4w": rep, "horizons": s,
                                    "last_two_years": s_recent}
        a4, a26 = s["4w"], s.get("26w", {})
        print(f"{c:15} {v:16} 1m {a4['accuracy_pct']:5.1f}% (naive {a4['naive_accuracy_pct']:5.1f}, filled-start {a4['naive_with_filled_start_accuracy_pct']:5.1f}, "
              f"bias {a4['bias_pct']:+.1f}, range {a4['range_coverage_pct']:.0f}%) | 6m {a26.get('accuracy_pct', float('nan')):5.1f}% "
              f"range {a26.get('range_coverage_pct', float('nan')):.0f}% | reported {rep} | n={a4['n']}")
    acc = [v["horizons"]["4w"]["accuracy_pct"] for v in report["commodities"].values() if "horizons" in v]
    report["summary"] = {"mean_1_month_accuracy_pct": round(float(np.mean(acc)), 1),
                         "commodities_at_95": int(sum(a >= 95 for a in acc)), "commodities_tested": len(acc),
                         "passes": sum(v.get("verdict") == "passes" for v in report["commodities"].values())}
    print("SUMMARY", report["summary"])
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2, default=str)
    pd.concat(all_rows).to_csv(ROWS_OUT, index=False)

    if "--no-variants" in sys.argv:
        sys.exit(0)

    # Variants: the same history with one rule changed at a time
    variants = {
        "baseline (current rules)": BASELINE,
        "old ranges (fixed 1.6 x error)": {**BASELINE, "band": ("mae", BAND_K)},
        "choose methods on all pairs (gaps included)": {**BASELINE, "gap": False},
        "ranges: 80th percentile of past errors": {**BASELINE, "band": ("q", 80)},
        "ranges: 85th percentile of past errors": {**BASELINE, "band": ("q", 85)},
        "leave 'price holds' only for a 1-point gain": {**BASELINE, "min_gain": 1.0},
        "no inflation tie-break": {**BASELINE, "tie": False},
        "no long-range blend": {**BASELINE, "long": False},
    }
    print("\nVARIANTS (mean over commodities; last two years in brackets)")
    report["variants"] = {}
    for name, cfg in variants.items():
        accs, biases, cov4, cov26, acc26, n95, r_accs = [], [], [], [], [], 0, []
        for c, p in by_c.items():
            d = nested(p, cfg)
            if d.empty or (d["h"] == 4).sum() < MIN_TARGETS:
                continue
            s4 = score(d[d["h"] == 4])
            accs.append(s4["accuracy_pct"]); biases.append(abs(s4["bias_pct"])); cov4.append(s4["range_coverage_pct"])
            n95 += s4["accuracy_pct"] >= 95
            rec = d[(d["h"] == 4) & (d["target"] > d["target"].max() - pd.Timedelta(weeks=RECENT_WEEKS))]
            if len(rec) >= MIN_TARGETS:
                r_accs.append(score(rec)["accuracy_pct"])
            if (d["h"] == 26).sum() >= MIN_TARGETS:
                s26 = score(d[d["h"] == 26]); acc26.append(s26["accuracy_pct"]); cov26.append(s26["range_coverage_pct"])
        row = {"accuracy_1m": round(np.mean(accs), 2), "accuracy_1m_last_2y": round(np.mean(r_accs), 2),
               "accuracy_6m": round(np.mean(acc26), 2), "abs_bias_1m": round(np.mean(biases), 2),
               "range_coverage_1m": round(np.mean(cov4), 1), "range_coverage_6m": round(np.mean(cov26), 1),
               "commodities_at_95": int(n95)}
        report["variants"][name] = row
        print(f"  {name:<46} 1m {row['accuracy_1m']:.2f}% ({row['accuracy_1m_last_2y']:.2f}%) | 6m {row['accuracy_6m']:.2f}% | "
              f"|bias| {row['abs_bias_1m']:.2f} | ranges hold {row['range_coverage_1m']:.0f}% / {row['range_coverage_6m']:.0f}% | 95%+: {row['commodities_at_95']}")
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print("->", OUT)
