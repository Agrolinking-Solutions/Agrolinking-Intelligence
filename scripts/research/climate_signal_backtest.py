"""
Research: do climate signals improve 3- and 6-month price forecasts?

Question (John, 16 Oct 2026): with a very strong El Nino and a rainy season
34% below normal in the north, should the forecasts use rainfall / ENSO?
This is a backtest only - nothing here changes the live forecasts.

Signals, each known at the forecast date (no look-ahead):
  season  - the latest COMPLETED rainy season's rainfall (1 Apr - 31 Oct) in
            the commodity's growing zones, as % vs the mean of the 10 seasons
            before it (NASA POWER, zone points as in 11_external_factors.py).
  enso    - NOAA's Oceanic Nino Index for the latest 3-month season that had
            ended at least a month before the forecast date.

Models, scored on REAL prices (PARTNER_SOURCES) at 13 and 26 weeks ahead:
  base           price holds, or the seasonal pattern (season@1.0)
  base + signal  base x exp(beta x signal), beta re-estimated at every
                 forecast date from earlier dates only (expanding window,
                 pooled within the commodity's group, at least MIN_TRAIN points)

Forecast dates every 4 weeks from 2004; scoring from 2012 so beta has history.
Writes outputs/backtest/climate_signal_test.json.
Run: python scripts/research/climate_signal_backtest.py
"""

import os, sys, json, time
import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from config.settings import PATHS, COMMODITIES, PARTNER_SOURCES
from pipeline.drift_models import forecast_path, seasonal_index

ZONE_POINTS = {
    "North West": (12.00, 8.52), "North Central": (9.90, 8.90), "North East": (11.85, 13.16),
    "South West": (7.38, 3.95), "South East": (6.21, 7.07), "South South": (4.82, 7.05),
}
CACHE = os.path.join(ROOT, "data", "research", "nasa_power_precip_2001_2017.csv")
OUT = os.path.join(ROOT, "outputs", "backtest", "climate_signal_test.json")
HORIZONS = (13, 26)
STEP_WEEKS = 4
SCORE_FROM = "2012-01-01"
MIN_TRAIN = 30
GROUPS = {
    "local crop": ["Maize (white)", "Maize (yellow)", "Sorghum", "Beans (white)", "Beans (red)", "Rice (local)", "Soybeans"],
    "imported": ["Rice (foreign)", "Wheat"],
    "export crop": ["Cocoa", "Sesame", "Cashew Nuts", "Ginger", "Hibiscus"],
    "livestock": ["Meat (beef)", "Meat (goat)", "Fish (dried)", "Eggs"],
}
GROUP_OF = {c: g for g, cs in GROUPS.items() for c in cs}


def long_rainfall():
    """Daily precip per zone 2001 onwards: cached NASA POWER 2001-2017 + weather_daily.csv (2018+)."""
    if not os.path.exists(CACHE):
        frames = []
        for zone, (lat, lon) in ZONE_POINTS.items():
            r = requests.get("https://power.larc.nasa.gov/api/temporal/daily/point",
                             params={"start": "20010101", "end": "20171231", "latitude": lat, "longitude": lon,
                                     "community": "ag", "format": "json", "parameters": "PRECTOTCORR"}, timeout=180)
            r.raise_for_status()
            p = r.json()["properties"]["parameter"]["PRECTOTCORR"]
            d = pd.DataFrame({"date": pd.to_datetime(list(p), format="%Y%m%d"), "precip_mm": list(p.values())})
            d = d[d["precip_mm"] > -990]
            d["zone"] = zone
            frames.append(d)
            time.sleep(1)
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        pd.concat(frames).to_csv(CACHE, index=False)
    old = pd.read_csv(CACHE, parse_dates=["date"])
    w = pd.read_csv(os.path.join(ROOT, "data", "external", "weather_daily.csv"), parse_dates=["date"])
    new = w[w["source"] == "nasa-power"][["date", "precip_mm", "zone"]]
    return pd.concat([old, new[new["date"] >= "2018-01-01"]], ignore_index=True)


def season_anomalies(rain):
    """{(zone, year): % vs mean of the 10 previous seasons}."""
    r = rain[(rain["date"].dt.month >= 4) & (rain["date"].dt.month <= 10)]
    tot = r.groupby(["zone", r["date"].dt.year])["precip_mm"].sum()
    out = {}
    for zone in ZONE_POINTS:
        s = tot.loc[zone]
        for y in s.index:
            prior = s[(s.index < y) & (s.index >= y - 10)]
            if len(prior) >= 5:
                out[(zone, y)] = (s[y] / prior.mean() - 1) * 100
    return out


def growing_zones(commodity):
    cfg = json.load(open(os.path.join(ROOT, "data", "external", "zones_config.json")))
    zs = [z for z, v in cfg.items() if any(commodity in cs for cs in v.get("state_primary", {}).values())]
    return zs or list(ZONE_POINTS)


def oni_table():
    t = pd.read_csv(os.path.join(ROOT, "data", "external", "enso_oni.csv"))
    centre = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8,
              "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}
    t["centre"] = pd.to_datetime(dict(year=t["year"], month=t["season"].map(centre), day=1))
    t["known_from"] = t["centre"] + pd.offsets.MonthBegin(2)   # season ends a month after centre; published soon after
    return t.sort_values("known_from")[["known_from", "oni"]]


def signal_season(anom, zones, t):
    y = t.year if t.month >= 11 else t.year - 1
    vals = [anom[(z, y)] for z in zones if (z, y) in anom]
    return float(np.mean(vals)) if vals else None


def signal_enso(oni, t):
    s = oni[oni["known_from"] <= t]
    return float(s["oni"].iloc[-1]) if len(s) else None


def build_rows():
    rain = long_rainfall()
    anom = season_anomalies(rain)
    oni = oni_table()
    master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    real = master[master["data_source"].isin(PARTNER_SOURCES) & master["price_ngn_mt"].notna()]
    rows = []
    for c in COMMODITIES:
        if c == "Rice" or c not in GROUP_OF:
            continue
        s = real[real["commodity"] == c].groupby("date")["price_ngn_mt"].mean().sort_index()
        if len(s) < 40:
            continue
        hist_all = real[real["commodity"] == c][["date", "price_ngn_mt", "data_source"]]
        zones = growing_zones(c) if GROUP_OF[c] in ("local crop", "export crop") else list(ZONE_POINTS)
        origins = s.index[(s.index >= "2004-01-01")][::max(1, STEP_WEEKS)]
        for t in origins:
            p0 = s.loc[t]
            hist = hist_all[hist_all["date"] <= t]
            sidx = seasonal_index(hist)
            for h in HORIZONS:
                target = t + pd.Timedelta(weeks=h)
                near = s[(s.index >= target - pd.Timedelta(days=14)) & (s.index <= target + pd.Timedelta(days=14))]
                if near.empty:
                    continue
                actual = float(near.iloc[(abs(near.index - target)).argmin()])
                seas = float(forecast_path("season@1.0", hist, p0, t, [target], season_idx=sidx)[0])
                rows.append({"commodity": c, "group": GROUP_OF[c], "t": t, "h": h, "p0": p0, "actual": actual,
                             "seasonal": seas, "sig_season": signal_season(anom, zones, t), "sig_enso": signal_enso(oni, t)})
    return pd.DataFrame(rows)


def evaluate(df, base, sig):
    """Out-of-sample: beta from earlier forecast dates of the same group and horizon."""
    d = df.dropna(subset=[sig]).sort_values("t").copy()
    d["base_pred"] = d["p0"] if base == "naive" else d[base]
    d["resid"] = np.log(d["actual"] / d["base_pred"])
    preds = []
    for (g, h), sub in d.groupby(["group", "h"]):
        for i, r in sub.iterrows():
            train = sub[sub["t"] < r["t"] - pd.Timedelta(weeks=h)]     # outcomes known by then
            if len(train) < MIN_TRAIN:
                preds.append((i, np.nan, np.nan))
                continue
            x, y = train[sig].values, train["resid"].values
            beta = float((x * y).sum() / (x * x).sum()) if (x * x).sum() > 0 else 0.0
            preds.append((i, beta, r["base_pred"] * np.exp(beta * r[sig])))
    p = pd.DataFrame(preds, columns=["i", "beta", "climate_pred"]).set_index("i")
    d = d.join(p).dropna(subset=["climate_pred"])
    d = d[d["t"] >= SCORE_FROM]
    d["ape_base"] = (d["base_pred"] - d["actual"]).abs() / d["actual"] * 100
    d["ape_clim"] = (d["climate_pred"] - d["actual"]).abs() / d["actual"] * 100
    return d


def summarise(d):
    out = {}
    for key, sub in [("all", d)] + list(d.groupby("group")):
        res = {}
        for h, s in sub.groupby("h"):
            per_c = s.groupby("commodity")[["ape_base", "ape_clim"]].mean()
            res[f"{h}w"] = {"n": int(len(s)), "base_error_pct": round(s["ape_base"].mean(), 2),
                            "with_signal_error_pct": round(s["ape_clim"].mean(), 2),
                            "commodities_improved": f"{int((per_c['ape_clim'] < per_c['ape_base']).sum())}/{len(per_c)}",
                            "last_beta": round(float(s.sort_values('t')['beta'].iloc[-1]), 5)}
        out[key] = res
    return out


if __name__ == "__main__":
    df = build_rows()
    print(f"{len(df)} forecast-target pairs, {df['commodity'].nunique()} commodities, "
          f"{df['t'].min().date()} to {df['t'].max().date()}")
    report = {"question": "Do rainy-season rainfall or ENSO improve 13/26-week forecasts on real prices?",
              "method": __doc__.strip().splitlines()[0], "results": {}}
    for base in ("naive", "seasonal"):
        for sig in ("sig_season", "sig_enso"):
            d = evaluate(df, base, sig)
            report["results"][f"{base}+{sig[4:]}"] = summarise(d)
            a = report["results"][f"{base}+{sig[4:]}"]["all"]
            print(f"{base:>8} + {sig[4:]:<6}: " + " | ".join(
                f"{h}: {v['base_error_pct']:.2f}% -> {v['with_signal_error_pct']:.2f}% ({v['commodities_improved']} better, n={v['n']})"
                for h, v in a.items()))
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print("->", OUT)
