"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Real macro data: inflation and exchange rate (CBN), fuel (WFP)

Until Oct 2026, data/external/inflation.csv, fx_rates.csv and fuel_prices.csv
continued past ~April 2026 as smooth projections (food inflation 27.8% when
the CBN's September figure was 19.59%; N1,675/$ when the official rate was
N1,331). This rebuilds all three from real published data every day:

  inflation.csv    CBN API (GetAllInflationRates): headline and food
                   year-on-year, monthly since 2003. A month's figure applies
                   from the 15th of the next month (when it is published), so
                   no week uses a number that wasn't out yet.
  fx_rates.csv     CBN API (GetAllExchangeRates): official US$ central rate,
                   daily since 2001; weekly = mean of that week's rates.
  fuel_prices.csv  WFP market prices: petrol, monthly median across markets
                   (thin months dropped). Diesel is real until WFP stopped
                   reporting it (Jan 2023); after that it is petrol x the
                   petrol price, flagged as an estimate (the 2019-23 ratio of
                   ~1.57 is from the petrol-subsidy era and would put diesel at
                   ~N2,260/L; since deregulation the two trade close together).

After the latest real figure every series is carried forward flat - never
projected. Each file gets a *_source column saying which.

Also saves the raw monthly/daily series (cbn_inflation_monthly.csv,
cbn_usd_daily.csv, wfp_petrol_monthly.csv) for the price-drivers panel.
Any source that fails leaves its file unchanged. Never fails the pipeline.

Run: python pipeline/fetch_macro.py
"""

import os, sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

EXT     = os.path.dirname(PATHS["inflation"])
CBN     = "https://www.cbn.gov.ng/api"
HEADERS = {"User-Agent": "Agrolinking-Intelligence/1.0 (daily data reader; contact: john@agrolinking.com)"}
PUBLISH_DAY = 15            # CBN/NBS publish a month's CPI around the 15th of the next month
THIN_MARKETS = 10           # WFP months with fewer petrol markets than this are dropped


def weekly_grid(start, today):
    """Mondays from start to the Monday of today's week."""
    end = pd.Timestamp(today) - pd.Timedelta(days=pd.Timestamp(today).dayofweek)
    return pd.date_range(pd.Timestamp(start), end, freq="W-MON")


def get_json(path):
    r = requests.get(f"{CBN}/{path}", headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.json()


def rebuild_inflation(today):
    d = pd.DataFrame(get_json("GetAllInflationRates"))
    m = pd.DataFrame({
        "year": d["tyear"].astype(int), "month": d["tmonth"].astype(int),
        "headline_yoy": pd.to_numeric(d["allItemsYearOn"], errors="coerce"),
        "food_yoy": pd.to_numeric(d["foodYearOn"], errors="coerce"),
        "core_yoy": pd.to_numeric(d.get("allItemsLessFrmProdAndEnergyYearOn"), errors="coerce"),
    }).dropna(subset=["headline_yoy", "food_yoy"]).sort_values(["year", "month"])
    m["period"] = pd.to_datetime(dict(year=m["year"], month=m["month"], day=1))
    m["published"] = (m["period"] + pd.offsets.MonthBegin(1)) + pd.Timedelta(days=PUBLISH_DAY - 1)
    m[["period", "published", "headline_yoy", "food_yoy", "core_yoy"]].to_csv(
        os.path.join(EXT, "cbn_inflation_monthly.csv"), index=False)

    grid = pd.DataFrame({"date": weekly_grid("2016-01-04", today)})
    w = pd.merge_asof(grid, m.sort_values("published")[["published", "period", "headline_yoy", "food_yoy"]],
                      left_on="date", right_on="published", direction="backward")
    latest = m["period"].max()
    w["inflation_source"] = np.where(w["period"] == latest,
                                     f"CBN {latest:%b %Y} (latest)", "CBN " + w["period"].dt.strftime("%b %Y"))
    out = w.rename(columns={"headline_yoy": "headline_inflation_yoy", "food_yoy": "food_inflation_yoy"})[
        ["date", "headline_inflation_yoy", "food_inflation_yoy", "inflation_source"]].dropna(subset=["food_inflation_yoy"])
    out.to_csv(PATHS["inflation"], index=False)
    last = m.iloc[-1]
    logger.success(f"  Inflation: CBN {last['period']:%B %Y} - headline {last['headline_yoy']}%, food {last['food_yoy']}% "
                   f"({len(out)} weekly rows)")


def rebuild_fx(today):
    d = pd.DataFrame(get_json("GetAllExchangeRates"))
    usd = d[d["currency"].str.strip().str.upper() == "US DOLLAR"].copy()
    usd["date"] = pd.to_datetime(usd["ratedate"], errors="coerce")
    usd["central"] = pd.to_numeric(usd["centralrate"], errors="coerce")
    usd = usd.dropna(subset=["date", "central"]).sort_values("date").drop_duplicates("date", keep="last")
    usd[["date", "central"]].rename(columns={"central": "ngn_per_usd"}).to_csv(
        os.path.join(EXT, "cbn_usd_daily.csv"), index=False)
    usd["week"] = usd["date"] - pd.to_timedelta(usd["date"].dt.dayofweek, unit="D")
    wk = usd.groupby("week")["central"].mean()
    grid = weekly_grid("2002-01-07", today)
    s = wk.reindex(grid)
    src = np.where(s.notna(), "CBN official rate", None)
    s = s.ffill()
    src = pd.Series(src, index=grid).fillna("CBN, carried forward").values
    out = pd.DataFrame({"date": grid, "fx_rate_usd_ngn": s.round(4).values, "fx_source": src}).dropna(subset=["fx_rate_usd_ngn"])
    out.to_csv(PATHS["fx_rates"], index=False)
    logger.success(f"  Exchange rate: CBN official N{usd['central'].iloc[-1]:,.2f}/$ on {usd['date'].iloc[-1].date()} "
                   f"({len(out)} weekly rows)")


def rebuild_fuel(today):
    w = pd.read_csv(PATHS["raw_wfp"], low_memory=False)
    w = w[w["commodity"].isin(["Fuel (petrol-gasoline)", "Fuel (diesel)"]) & (w["unit"] == "L")].copy()
    w["date"] = pd.to_datetime(w["date"], errors="coerce")
    w["price"] = pd.to_numeric(w["price"], errors="coerce")
    w = w.dropna(subset=["date", "price"])
    monthly = w.groupby(["commodity", "date"]).agg(price=("price", "median"), markets=("market", "nunique")).reset_index()
    monthly = monthly[monthly["markets"] >= THIN_MARKETS]
    petrol = monthly[monthly["commodity"] == "Fuel (petrol-gasoline)"].set_index("date")["price"]
    diesel = monthly[monthly["commodity"] == "Fuel (diesel)"].set_index("date")["price"]
    monthly[monthly["commodity"] == "Fuel (petrol-gasoline)"][["date", "price", "markets"]].rename(
        columns={"price": "petrol_ngn_litre"}).to_csv(os.path.join(EXT, "wfp_petrol_monthly.csv"), index=False)

    ratio = 1.0   # see module docstring

    grid = weekly_grid("2014-05-19", today)
    def to_weekly(s):
        return pd.merge_asof(pd.DataFrame({"date": grid}), s.rename("v").reset_index().rename(columns={"index": "date"}),
                             on="date", direction="backward")["v"].values
    p = to_weekly(petrol)
    dz = to_weekly(diesel)
    last_diesel = diesel.index.max()
    est = grid > last_diesel
    dz = np.where(est, p * ratio, dz)
    out = pd.DataFrame({"date": grid, "fuel_diesel_ngn_litre": np.round(dz, 2), "fuel_petrol_ngn_litre": np.round(p, 2),
                        "petrol_source": "WFP median",
                        "diesel_source": np.where(est, f"estimate: same as petrol (no WFP diesel since {last_diesel:%b %Y})",
                                                  "WFP median")})
    out = out.dropna(subset=["fuel_petrol_ngn_litre"])
    out.to_csv(PATHS["fuel_prices"], index=False)
    logger.success(f"  Fuel: WFP petrol N{petrol.iloc[-1]:,.0f}/L ({petrol.index.max():%b %Y}, "
                   f"{int(monthly[monthly['commodity'] == 'Fuel (petrol-gasoline)']['markets'].iloc[-1])} markets); "
                   f"diesel set to petrol after {last_diesel:%b %Y} (estimate)")


def run():
    logger.info("=" * 60)
    logger.info("REAL MACRO DATA - inflation, exchange rate (CBN), fuel (WFP)")
    logger.info("=" * 60)
    today = datetime.now(timezone.utc).date()
    for name, fn in (("inflation", rebuild_inflation), ("exchange rate", rebuild_fx), ("fuel", rebuild_fuel)):
        try:
            fn(today)
        except Exception as e:
            logger.warning(f"  {name}: could not refresh ({e}) - file left unchanged")
    return True


if __name__ == "__main__":
    run()
    sys.exit(0)
