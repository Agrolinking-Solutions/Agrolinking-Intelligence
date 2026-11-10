"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 1: Data Ingestion
─────────────────────────────────────────
Loads Agricome and WFP Nigeria raw data, standardizes units to NGN/MT,
merges both sources into one unified dataset, and saves to data/processed/.

Run standalone:  python pipeline/01_ingest.py
"""

import os
import sys
import pandas as pd
import numpy as np
from loguru import logger
from datetime import datetime

# ── Allow imports from project root ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, WFP_NAME_MAP, STANDARD_UNIT, SIBLING_SOURCE, FIELD_UNITS

# ── Logger setup ─────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
logger.add(
    os.path.join(PATHS["logs_dir"], "ingestion_{time:YYYY-MM-DD}.log"),
    rotation="1 day", retention="30 days", level="DEBUG"
)


# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — LOAD AGRICOME
# ─────────────────────────────────────────────────────────────────────────────

def load_agricome() -> pd.DataFrame:
    """Load and lightly validate the Agricome raw dataset."""
    logger.info("Loading Agricome raw data...")
    path = PATHS["raw_agricome"]

    if not os.path.exists(path):
        logger.error(f"Agricome file not found at: {path}")
        raise FileNotFoundError(f"Missing: {path}")

    df = pd.read_csv(path, parse_dates=["week_start_date"])

    # IMPORTANT: The raw file contains two types of non-historical rows:
    #   1. source="Agricom"  — real scraped prices. Keep ALL of these,
    #      regardless of how record_type is labelled. They are ground truth.
    #   2. source="Agrolinking Forecast" / "Agrolinking AI Pipeline"
    #      — old forecast runs from the previous pipeline. Discard these;
    #      we regenerate all forecasts fresh.
    real_sources = ["Agricom", "WFP Nigeria Food Prices"]
    df = df[df["source"].isin(real_sources)].copy()

    # Relabel everything we kept as historical (some may have been tagged
    # as "forecast" by the old pipeline even though the price is real)
    df["record_type"] = "historical"

    # Rename to standard schema
    df = df.rename(columns={
        "week_start_date": "date",
        "price":           "price_ngn_mt",
    })

    # Keep only needed columns
    df = df[[
        "commodity", "date", "price_ngn_mt", "currency", "unit",
        "source", "market_type", "region",
        "fx_rate", "rainfall_index",
        "data_quality_score", "is_validated", "notes"
    ]].copy()

    # Tag the source
    df["data_source"] = "Agricome"
    # Some rows in this file are labelled "Agricom" but are World Bank
    # Real-Time Prices model estimates (Rice/Maize 2023-26, livestock
    # Mar-Jul 2026), or derived from them (Maize (yellow) = white x ratio).
    # Their notes say so. They are estimates, not observed market prices, so
    # tag them separately: they still fill gaps, but don't count as real data
    # for freshness, validation references or accuracy scoring.
    is_estimate = df["notes"].astype(str).str.contains("World Bank RTP|DERIVED:", regex=True)
    df.loc[is_estimate, "data_source"] = "WB_RTP_estimate"
    if is_estimate.any():
        logger.info(f"  {int(is_estimate.sum())} rows tagged as World Bank estimates (not real prices)")

    logger.success(f"Agricome loaded: {len(df):,} rows | {df['commodity'].nunique()} commodities")
    logger.debug(f"  Commodities: {sorted(df['commodity'].unique())}")
    logger.debug(f"  Date range:  {df['date'].min().date()} → {df['date'].max().date()}")
    return df


def load_web_prices() -> pd.DataFrame:
    """
    Web-sourced prices that passed 14_promote_web_prices.py's checks.
    Lowest-priority real source: Agricome and WFP win any same-week tie.
    Returns an empty frame (same columns) when nothing has been promoted yet.
    """
    cols = ["commodity", "date", "price_ngn_mt", "currency", "unit", "source", "market_type",
            "region", "fx_rate", "rainfall_index", "data_quality_score", "is_validated",
            "notes", "data_source"]
    path = PATHS["raw_web_prices"]
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, parse_dates=["week_start_date"])
    df = df.rename(columns={"week_start_date": "date", "price": "price_ngn_mt"})
    df["data_source"] = "Web_search"
    df["record_type"] = "historical"
    df = df[df["price_ngn_mt"].notna() & (df["price_ngn_mt"] > 0)]
    logger.success(f"Web-sourced prices loaded: {len(df):,} rows | {df['commodity'].nunique()} commodities")
    return df[cols].copy()


# Prices Agrolinking collects itself - trader quotes by phone or WhatsApp,
# field visits - typed into data/raw/field_prices.csv, one row per quote:
#   date, commodity, market, state, price_ngn, unit, price_level, reported_by, notes
# Several quotes in one week become one national row (their median). The file
# is the source of truth: delete a row and it leaves the master on the next
# run. Started Nov 2026 for wheat, which has had no other real source since
# Agrolinking's April 2026 field collection (those rows live in the master).
FIELD_QUOTES_SOURCE = "Agrolinking field quotes"
# Units a quote may be in: FIELD_UNITS in config/settings.py

def load_field_prices() -> pd.DataFrame:
    cols = ["commodity", "date", "price_ngn_mt", "currency", "unit", "source", "market_type",
            "region", "fx_rate", "rainfall_index", "data_quality_score", "is_validated",
            "notes", "data_source"]
    path = PATHS["raw_field_prices"]
    df = pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path) else pd.DataFrame()
    if df.empty:
        return pd.DataFrame(columns=cols)
    unit = df["unit"].astype(str).str.strip().str.lower()
    df["factor"] = unit.map(FIELD_UNITS)
    eggs = df["commodity"] == "Eggs"
    ok = (df["factor"].notna() & (eggs == (unit == "crate"))
          & (df["price_level"].astype(str).str.strip().str.lower() == "wholesale")
          & df["commodity"].isin(COMMODITIES) & (pd.to_numeric(df["price_ngn"], errors="coerce") > 0))
    for r in df[~ok].itertuples():
        logger.warning(f"  Field quote skipped (needs a known commodity, a wholesale price and a unit from "
                       f"{sorted(FIELD_UNITS)}): {r.date:%Y-%m-%d} {r.commodity} {r.price_ngn} {r.unit} {r.price_level}")
    df = df[ok].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["price_ngn"] = df["price_ngn"].astype(float)
    df["price_ngn_mt"] = df["price_ngn"] * df["factor"]
    df["week"] = df["date"] - pd.to_timedelta(df["date"].dt.dayofweek, unit="D")
    rows = []
    for (c, _), g in df.groupby(["commodity", "week"]):
        quotes = "; ".join(f"{r.market}, {r.state}: N{r.price_ngn:,.0f} per {r.unit} ({r.reported_by}, {r.date:%d %b})"
                           for r in g.itertuples())
        rows.append({"commodity": c, "date": g["date"].max(), "price_ngn_mt": float(g["price_ngn_mt"].median()),
                     "currency": "NGN", "unit": "NGN/MT", "source": FIELD_QUOTES_SOURCE,
                     "market_type": "wholesale", "region": "National", "fx_rate": np.nan,
                     "rainfall_index": np.nan, "data_quality_score": 0.9, "is_validated": True,
                     "notes": f"Agrolinking field quotes, median of {len(g)} - converted to NGN/MT: {quotes}",
                     "data_source": "Agrolinking_primary"})
    out = pd.DataFrame(rows, columns=cols)
    logger.success(f"Field quotes loaded: {len(df)} quotes -> {len(out)} weekly rows | "
                   f"{out['commodity'].nunique()} commodities")
    return out


# AFEX commodity-exchange prices (data/raw/afex_prices.csv, read daily from
# Mansa Markets by fetch_afex_prices.py) used as REAL prices, per commodity,
# only where a person decided they fit. Maize: white maize (Mansa Markets,
# 3 Nov 2026: "white maize, not yellow or a mix"; no delivery location or
# warehouse). It was used for yellow maize from 15 Oct 2026 while the grade
# was unknown; yellow maize is now white x SIBLING_PRICES, labelled an estimate.
AFEX_AS_REAL = {"maize": ["Maize (white)"]}
AFEX_GRADE = {"maize": "white maize"}


def load_afex_prices() -> pd.DataFrame:
    """
    AFEX exchange rows for AFEX_AS_REAL, one per commodity and week: the
    median of AFEX's last 5 trading days up to that week's latest print
    (afex_median.py, the same figure 16_cross_check compares). It was the
    single latest print, so yellow maize jumped +39% on one day's 470,870
    when the 5-day median was 337,700 (2 Nov 2026).
    """
    from pipeline.afex_median import afex_median, unchanged_run, FROZEN_DAYS
    cols = ["commodity", "date", "price_ngn_mt", "currency", "unit", "source", "market_type",
            "region", "fx_rate", "rainfall_index", "data_quality_score", "is_validated",
            "notes", "data_source"]
    path = os.path.join(os.path.dirname(PATHS["raw_web_prices"]), "afex_prices.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    a = pd.read_csv(path, parse_dates=["as_of_date"])
    a = a[(a["price_type"] == "exchange") & a["mansa_commodity"].isin(AFEX_AS_REAL)]
    a = a.drop_duplicates(["mansa_commodity", "as_of_date"], keep="last")
    a["week"] = a["as_of_date"] - pd.to_timedelta(a["as_of_date"].dt.dayofweek, unit="D")
    rows = []
    for slug, g in a.groupby("mansa_commodity"):
        for _, r in g.sort_values("as_of_date").groupby("week").tail(1).iterrows():
            run, since = unchanged_run(g, r["as_of_date"])
            if run >= FROZEN_DAYS:      # a standing listing, not a traded price (afex_median.py)
                logger.warning(f"  AFEX {slug}: price unchanged for {run} trading days since {since} - "
                               f"week of {r['week'].date()} not used")
                continue
            price, n_days = afex_median(g, r["as_of_date"])
            for ours in AFEX_AS_REAL[slug]:
                rows.append({"commodity": ours, "date": r["as_of_date"], "price_ngn_mt": price,
                             "currency": "NGN", "unit": "NGN/MT", "source": "AFEX via Mansa Markets",
                             "market_type": "exchange", "region": "National", "fx_rate": np.nan,
                             "rainfall_index": np.nan, "data_quality_score": 0.9, "is_validated": True,
                             "notes": (f"AFEX exchange price, median of {n_days} trading days to "
                                       f"{r['as_of_date'].date()} (that day: {r['price_as_shown']}; "
                                       f"{AFEX_GRADE.get(slug, slug)}) - {r['url']}"),
                             "data_source": "AFEX"})
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    logger.success(f"AFEX exchange prices loaded: {len(df):,} rows | {sorted(df['commodity'].unique())}")
    return df[cols].copy()


# FEWS NET weekly WHOLESALE prices (via FAO's price API, saved by
# fetch_fao_prices.py) used as REAL prices (John, 15 Oct 2026). The national
# figure is the mean of a fixed set of markets that still report, so the
# series doesn't jump when a market stops (Lagos and Giwa stopped Jan 2025).
# Over 2015-2023 Agricome/WFP white maize ran 0.96-0.97x this mean and WFP
# imported rice 1.03x, so they sit together without zigzags. Left out:
# Sorghum (Agricome covers it; FEWS NET ~9% higher would zigzag) and Rice
# (local) (one market, noisy, 0.92x WFP) - both stay comparison-only.
FEWS_AS_REAL = {
    "Maize (white)":  (["Kano", "Ibadan", "Kaura Namoda", "Maiduguri"], 3),   # markets, minimum reporting
    "Rice (foreign)": (["Ibadan", "Kaura Namoda", "Maiduguri"], 2),
}


def load_fews_prices() -> pd.DataFrame:
    """One FEWS NET row per commodity and week: the mean of FEWS_AS_REAL markets reporting."""
    cols = ["commodity", "date", "price_ngn_mt", "currency", "unit", "source", "market_type",
            "region", "fx_rate", "rainfall_index", "data_quality_score", "is_validated",
            "notes", "data_source"]
    path = os.path.join(os.path.dirname(PATHS["raw_web_prices"]), "fao_fpma_prices.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    f = pd.read_csv(path, parse_dates=["date"])
    f = f[(f["price_type"] == "wholesale") & ~f["unit_suspect"].astype(bool)
          & (f["periodicity"] == "weekly") & f["price_ngn_mt"].notna()]
    out = []
    for commodity, (markets, min_markets) in FEWS_AS_REAL.items():
        s = f[(f["our_commodity"] == commodity) & f["market"].isin(markets)].copy()
        s["date"] = s["date"] - pd.to_timedelta(s["date"].dt.dayofweek, unit="D")
        piv = s.pivot_table(index="date", columns="market", values="price_ngn_mt", aggfunc="mean")
        n = piv.notna().sum(axis=1)
        piv = piv[n >= min_markets]
        for week, row in piv.iterrows():
            have = row.dropna()
            out.append({"commodity": commodity, "date": week, "price_ngn_mt": float(have.mean()),
                        "currency": "NGN", "unit": "NGN/MT", "source": "FEWS NET via FAO FPMA",
                        "market_type": "wholesale", "region": "National", "fx_rate": np.nan,
                        "rainfall_index": np.nan, "data_quality_score": 0.9, "is_validated": True,
                        "notes": f"FEWS NET weekly wholesale, mean of {len(have)} markets ({', '.join(have.index)})",
                        "data_source": "FEWS_NET"})
    if not out:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(out)
    logger.success(f"FEWS NET wholesale prices loaded: {len(df):,} weekly rows | {sorted(df['commodity'].unique())}")
    return df[cols].copy()


# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — LOAD WFP + CONVERT UNITS → NGN/MT
# ─────────────────────────────────────────────────────────────────────────────

# Conversion factors: multiply WFP price-per-X to get NGN/MT
# MT = metric tonne = 1,000 kg
WFP_UNIT_TO_MT = {
    "KG":      1_000,        # price/kg  × 1000 = price/MT
    "100 KG":  10,           # price/100kg × 10 = price/MT
    "50 KG":   20,           # price/50kg × 20  = price/MT
    "2.5 KG":  400,          # price/2.5kg × 400 = price/MT
    "2.7 KG":  370.37,       # price/2.7kg × 370.37 ≈ price/MT
    "2.8 KG":  1000 / 2.8,   # imported rice retail measure
    "30 pcs":  1,            # Eggs: price per crate of 30 — the Eggs series is NGN/crate, not NGN/MT
    "L":       1_000,        # fuel: price/L × 1000 (kept as NGN/1000L for comparisons)
    "100 L":   10,
}

# WFP commodities we want — mapped to our standard names
WFP_COMMODITIES_WANTED = {
    "Beans (white)":        "Beans (white)",
    "Beans (red)":          "Beans (red)",
    "Beans (niebe)":        "Beans (white)",
    "Maize":                "Maize (white)",
    "Maize (white)":        "Maize (white)",
    "Maize (yellow)":       "Maize (yellow)",
    "Sorghum":              "Sorghum",
    "Sorghum (white)":      "Sorghum",
    "Soybeans":             "Soybeans",
    # Rice by type. Wholesale stops in Jan 2023, retail runs to date, so
    # retail-only months are converted to a wholesale equivalent (see
    # WHOLESALE_EQUIVALENT below).
    "Rice (imported)":      "Rice (foreign)",
    "Rice (local)":         "Rice (local)",
    # Livestock/protein: WFP has real retail prices to date. These used to be
    # loaded once by scripts/maintenance/add_livestock_to_pipeline.py and then
    # topped up with World Bank estimates; reading them here keeps them current.
    "Meat (beef)":          "Meat (beef)",
    "Meat (goat)":          "Meat (goat)",
    "Fish":                 "Fish (dried)",
    "Eggs":                 "Eggs",
    "Fuel (diesel)":        "_fuel_diesel",      # external feature, not commodity forecast
    "Fuel (petrol-gasoline)": "_fuel_petrol",    # external feature
}


WHOLESALE_EQUIVALENT = {"Rice (local)", "Rice (foreign)", "Sorghum"}
CBM_NAIRA_FROM = pd.Timestamp("2017-01-01")   # cross-border markets in CFA francs before this (see load_wfp)
# WFP market-months whose prices belong to another year. Abba Gamaram
# (Maiduguri) "15 Jan 2016": 25 rows at 2020s prices - eggs N6,200 a crate,
# beef N6,500/kg - while 20 other markets that month show 2016 prices and
# Abba Gamaram's own next report (Jun 2016) is ~6x lower. It was the only
# eggs, beans, fish, beef and goat price that month, so it became the
# national price and put a 98%-error year into the eggs accuracy test (found
# 3 Nov 2026). No other month matches it, so it is dropped, not re-dated.
WFP_MISDATED = {("Abba Gamaram", "2016-01-15")}
# WFP state-months with a unit slip: every market in one state moved ~2x (or
# ~0.5x) at once and moved back later, while the neighbouring state did not.
# Dropped, not rescaled. Found by scanning every state-month (5 Nov 2026);
# 2021 and 2024 jumps that every state shared are real and kept.
#   - Yobe, Jul 2017 - May 2018: retail per small measure (2.5-2.8 kg) doubled,
#     then fell to exactly half (white beans 1,667 -> 833, Jun 2018); Borno
#     stayed at 673-962 and Yobe's 100 kg wholesale rice stayed flat. White
#     beans' national price read 472-587K instead of ~290K for 11 months.
#   - Borno, Aug 2017: the same measures ~1.9x (beans 1,851; 950 and 962 either side).
#   - Yobe, Jun 2017; Borno, Apr 2022: eggs, beef, goat and fish at half for one
#     month in every market (Borno eggs 1,900 -> 950 -> 1,902).
#   - Yobe, May 2021: eggs 600 (1,400 before, 1,500 after; Borno 1,334).
MEASURES = {"2.5 KG", "2.7 KG", "2.8 KG"}
PER_ITEM = {"Eggs", "Meat (beef)", "Meat (goat)", "Fish"}
WFP_UNIT_SLIPS = [
    {"admin1": "Yobe",  "from": "2017-07-01", "to": "2018-05-31", "units": MEASURES},
    {"admin1": "Borno", "from": "2017-08-01", "to": "2017-08-31", "units": MEASURES},
    {"admin1": "Yobe",  "from": "2017-06-01", "to": "2017-06-30", "commodities": PER_ITEM},
    {"admin1": "Borno", "from": "2022-04-01", "to": "2022-04-30", "commodities": PER_ITEM},
    {"admin1": "Yobe",  "from": "2021-05-01", "to": "2021-05-31", "commodities": {"Eggs"}},
]
# (our commodity, month) pairs that lost rows to WFP_UNIT_SLIPS, set by
# load_wfp: the older WFP extract (place_legacy_wfp) is national averages of
# the same survey, slip included, so it must not fill those months back in.
SLIPPED_MONTHS: set = set()
# The platform prices everything at wholesale. WFP has only ever published
# RETAIL prices for beans (Borno/Yobe/Adamawa), but it published both retail
# and wholesale for cowpeas - the same product family - in the same markets
# and months (13 states, 2014-2023). Their median wholesale/retail ratio
# (~0.90 brown, ~0.93 white) converts beans retail to a wholesale equivalent.
WHOLESALE_PROXY = {"Beans (red)": "Cowpeas (brown)", "Beans (white)": "Cowpeas (white)"}
# Meat, fish and eggs: WFP has retail only and no wholesale proxy, so these
# stay retail and are labelled market_type "retail" (a known gap).


def convert_wfp_to_ngn_mt(price: float, unit: str) -> float:
    """Convert WFP price in its native unit to NGN/MT."""
    factor = WFP_UNIT_TO_MT.get(unit)
    if factor is None:
        return np.nan
    return price * factor


def wholesale_proxy_ratios(wfp: pd.DataFrame) -> dict:
    """{commodity: (wholesale/retail ratio, market-months used, proxy)} for WHOLESALE_PROXY."""
    out = {}
    for commodity, proxy in WHOLESALE_PROXY.items():
        p = wfp[wfp["commodity"] == proxy].copy()
        p["mt"] = [convert_wfp_to_ngn_mt(pr, u) for pr, u in zip(p["price"], p["unit"])]
        both = p.pivot_table(index=["market", "date"], columns="pricetype", values="mt",
                             aggfunc="median").dropna(subset=["Wholesale", "Retail"])                if {"Wholesale", "Retail"} <= set(p["pricetype"]) else pd.DataFrame()
        if len(both) < 100:
            logger.warning(f"  WFP: only {len(both)} {proxy} market-months with both price types - "
                           f"{commodity} stays retail")
            continue
        out[commodity] = (float((both["Wholesale"] / both["Retail"]).median()), len(both), proxy)
    return out


# WFP publishes a month while only a few of its ~20 markets have reported
# (Oct 2026: 5 markets in Borno and Yobe) and completes it weeks later. The
# thin-coverage rule below dropped that partial month, so meat, eggs and beans
# had no real price past day 45 every month. Instead, a series' newest month
# is kept as PROVISIONAL when enough markets reported both it and the month
# before: their same-market change is applied to the previous month's full
# national average. (The partial set's own average is biased by which markets
# report first - Oct 2026 eggs: those 5 markets ran 5% above the September
# national figure.) Stored as data_source "WFP_provisional", replaced when WFP
# completes the month, never scored against (config SCORED_SOURCES).
PROVISIONAL_MIN_MARKETS  = 4      # markets that reported both months
PROVISIONAL_MAX_SPREAD   = 0.25   # skip if their moves differ more (Oct 2026 dried fish: -47% to 0%)
PROVISIONAL_MAX_GAP_DAYS = 45     # the comparison month must be the month before


def provisional_months(monthly: pd.DataFrame, markets: pd.DataFrame) -> dict:
    """
    monthly: national rows (date, commodity_std, pricetype, price_ngn_mt, recent_median, thin).
    markets: market rows (date, commodity_std, pricetype, market, price_ngn_mt).
    Returns {(date, commodity_std, pricetype): (provisional price_ngn_mt, note)} for each
    series whose newest month is thin but can be estimated from the month before.
    """
    out = {}
    for (c, t), g in monthly.groupby(["commodity_std", "pricetype"]):
        g = g.sort_values("date")
        if len(g) < 2 or not g["thin"].iloc[-1] or g["thin"].iloc[-2]:
            continue
        new, prev = g.iloc[-1], g.iloc[-2]
        if (new["date"] - prev["date"]).days > PROVISIONAL_MAX_GAP_DAYS:
            continue
        mk = markets[(markets["commodity_std"] == c) & (markets["pricetype"] == t)]
        cur = mk[mk["date"] == new["date"]].groupby("market")["price_ngn_mt"].mean()
        old = mk[mk["date"] == prev["date"]].groupby("market")["price_ngn_mt"].mean()
        both = cur.index.intersection(old.index)
        if len(both) < PROVISIONAL_MIN_MARKETS:
            continue
        moves = cur[both] / old[both] - 1
        if moves.max() - moves.min() > PROVISIONAL_MAX_SPREAD:
            continue
        change = cur[both].mean() / old[both].mean() - 1
        out[(new["date"], c, t)] = (
            float(prev["price_ngn_mt"] * (1 + change)),
            f"PROVISIONAL: {len(cur)} of ~{new['recent_median']:.0f} WFP markets have reported "
            f"{new['date']:%b %Y}; their {change:+.1%} change since {prev['date']:%b %Y} applied to that "
            f"month's national average. Replaced when WFP completes the month")
    return out


# A recent month that is past the thin rule but still filling in (Nov 2026:
# 14 of ~19 markets in the 9 Nov file; June 2026 had 15-16 of 19 on 21 Sep,
# three months on) moved by 1.3% on average and up to 5.6% once WFP completed
# it (saved file versions May-Nov 2026, 40 such months). Its own average is
# kept - closer to the final figure than the same-market estimate (2.0%) at
# this coverage - but as PROVISIONAL, so no forecast is scored against it.
COMPLETE_SHARE = 0.85     # a month is complete with this share of the series' usual markets
PARTIAL_MONTHS = 3        # ...or once it is older than the series' newest PARTIAL_MONTHS months


def partly_reported_months(monthly: pd.DataFrame) -> dict:
    """
    monthly: national rows (date, commodity_std, pricetype, price_ngn_mt, n_markets, recent_median, thin).
    Returns {(date, commodity_std, pricetype): (price_ngn_mt, note)} for recent months reported by
    fewer than COMPLETE_SHARE of the usual markets (thin months are provisional_months' job).
    """
    newest = monthly.groupby(["commodity_std", "pricetype"])["date"].transform("max")
    sel = (~monthly["thin"] & (monthly["n_markets"] < COMPLETE_SHARE * monthly["recent_median"])
           & (monthly["date"] > newest - pd.DateOffset(months=PARTIAL_MONTHS)))
    return {(r["date"], r["commodity_std"], r["pricetype"]): (
                float(r["price_ngn_mt"]),
                f"PROVISIONAL: {int(r['n_markets'])} of ~{r['recent_median']:.0f} WFP markets have reported "
                f"{r['date']:%b %Y} so far; their average can still move a few %. Replaced when WFP completes the month")
            for _, r in monthly[sel].iterrows()}


def load_wfp() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load WFP data, extract:
      - commodity prices (NGN/MT) for Beans, Maize, Sorghum
      - fuel prices as external features
    Returns: (commodity_df, fuel_df)
    """
    logger.info("Loading WFP Nigeria data...")
    path = PATHS["raw_wfp"]

    if not os.path.exists(path):
        logger.error(f"WFP file not found at: {path}")
        raise FileNotFoundError(f"Missing: {path}")

    wfp = pd.read_csv(path, parse_dates=["date"])
    # WFP's cross-border markets, "(CBM)" in the market name, reported in CFA
    # francs until 2016 although the file says NGN: their prices ran ~3x the
    # Nigerian markets', in line with the CFA/Naira rate (Jul 2005 maize: Mai
    # Adoua 246/kg x 0.25 = N61 vs Kano Dawanau N60), and settled at ~1x from
    # 2017. Averaged in, they doubled pre-2016 maize and sorghum and added 61%
    # to imported rice (found 16 Oct 2026). Dropped before CBM_NAIRA_FROM.
    cfa = (wfp["market"].astype(str).str.contains("(CBM)", regex=False)
           & (pd.to_datetime(wfp["date"], errors="coerce") < CBM_NAIRA_FROM))
    if cfa.any():
        logger.info(f"  WFP: dropped {int(cfa.sum())} cross-border market rows before "
                    f"{CBM_NAIRA_FROM:%Y} (prices in CFA francs)")
        wfp = wfp[~cfa].copy()
    misdated = pd.Series([(m, f"{d:%Y-%m-%d}") in WFP_MISDATED for m, d in zip(wfp["market"], wfp["date"])],
                         index=wfp.index)
    if misdated.any():
        logger.info(f"  WFP: dropped {int(misdated.sum())} rows from market-months dated to the wrong "
                    f"year (WFP_MISDATED)")
        wfp = wfp[~misdated].copy()
    slipped = pd.Series(False, index=wfp.index)
    for slip in WFP_UNIT_SLIPS:
        slipped |= ((wfp["admin1"] == slip["admin1"]) & (wfp["pricetype"] == "Retail")
                    & wfp["date"].between(pd.Timestamp(slip["from"]), pd.Timestamp(slip["to"]))
                    & (wfp["unit"].isin(slip["units"]) if "units" in slip
                       else wfp["commodity"].isin(slip["commodities"])))
    if slipped.any():
        logger.info(f"  WFP: dropped {int(slipped.sum())} rows from state-months with a unit slip (WFP_UNIT_SLIPS)")
        gone = wfp[slipped]
        SLIPPED_MONTHS.update(zip(gone["commodity"].map(WFP_COMMODITIES_WANTED), gone["date"].dt.to_period("M")))
        wfp = wfp[~slipped].copy()
    proxy_ratios = wholesale_proxy_ratios(wfp)

    # Filter to only the commodities we need
    wfp = wfp[wfp["commodity"].isin(WFP_COMMODITIES_WANTED.keys())].copy()

    # Map to our standard names
    wfp["commodity_std"] = wfp["commodity"].map(WFP_COMMODITIES_WANTED)

    # Convert price to NGN/MT
    wfp["price_ngn_mt"] = wfp.apply(
        lambda r: convert_wfp_to_ngn_mt(r["price"], r["unit"]), axis=1
    )

    # Drop rows where conversion failed (unknown units)
    before = len(wfp)
    wfp = wfp.dropna(subset=["price_ngn_mt"])
    dropped = before - len(wfp)
    if dropped:
        logger.warning(f"WFP: dropped {dropped} rows with unrecognised units")

    # ── Fuel prices (external feature) ───────────────────────────────────────
    fuel_df = wfp[wfp["commodity_std"].str.startswith("_fuel", na=False)].copy()
    fuel_df = fuel_df.rename(columns={"commodity_std": "fuel_type"})
    fuel_df["fuel_type"] = fuel_df["fuel_type"].str.replace("_fuel_", "", regex=False)

    # Weekly average per fuel type
    fuel_agg = (
        fuel_df.groupby(["date", "fuel_type"])["price"]
        .mean()
        .reset_index()
        .rename(columns={"price": "price_ngn_litre"})
    )
    # Resample to weekly Monday
    fuel_agg["date"] = pd.to_datetime(fuel_agg["date"])
    fuel_pivot = fuel_agg.pivot_table(
        index="date", columns="fuel_type", values="price_ngn_litre"
    ).reset_index()
    fuel_pivot.columns.name = None
    fuel_pivot = fuel_pivot.rename(columns={
        "diesel": "fuel_diesel_ngn_litre",
        "petrol": "fuel_petrol_ngn_litre",
    })
    logger.success(f"WFP fuel data: {len(fuel_pivot):,} weekly rows")

    # ── Commodity prices ──────────────────────────────────────────────────────
    comm_df = wfp[~wfp["commodity_std"].str.startswith("_fuel", na=False)].copy()

    # National average per month and price type, across markets
    monthly = (
        comm_df.groupby(["date", "commodity_std", "pricetype"])
        .agg(price_ngn_mt=("price_ngn_mt", "mean"), n_markets=("market", "nunique"))
        .reset_index()
    )

    # Drop months where market coverage collapsed. Found in the Jul 2026 file:
    # every commodity had a single market (Rann, Borno, a conflict zone) after
    # 13-18 markets the month before, so its "national average" was one
    # market's price (Fish 500K/MT vs 2.26M the month before). Compared with
    # the series' own recent coverage, so early years that always had few
    # markets are kept.
    monthly = monthly.sort_values("date")
    monthly["recent_median"] = (
        monthly.groupby(["commodity_std", "pricetype"])["n_markets"]
        .transform(lambda s: s.shift(1).rolling(6, min_periods=3).median())
    )
    # Also catches a partial current month (WFP publishes a month while only
    # 4-5 of ~23 markets have reported, e.g. Oct 2026 in the Oct 11 file).
    monthly["thin"] = monthly["n_markets"] < 0.3 * monthly["recent_median"]
    provisional = provisional_months(monthly, comm_df)
    for (d, c, t), (price, note) in provisional.items():
        sel = (monthly["date"] == d) & (monthly["commodity_std"] == c) & (monthly["pricetype"] == t)
        monthly.loc[sel, "price_ngn_mt"] = price
        monthly.loc[sel, "thin"] = False
        logger.info(f"  WFP: {c} {t} {d.date()} kept as provisional - {note}")
    partial = partly_reported_months(monthly)
    for (d, c, t), (_, note) in partial.items():
        logger.info(f"  WFP: {c} {t} {d.date()} provisional - {note}")
    provisional.update(partial)
    thin = monthly["thin"]
    if thin.any():
        for _, r in monthly[thin].iterrows():
            logger.warning(f"  WFP: dropping {r['commodity_std']} {r['pricetype']} {r['date'].date()} "
                           f"- {int(r['n_markets'])} market(s) vs ~{r['recent_median']:.0f} usually")
        monthly = monthly[~thin]

    # Use Wholesale preferentially, fall back to Retail. For commodities in
    # WHOLESALE_EQUIVALENT, retail-only months are scaled by the median
    # wholesale/retail ratio measured over months that have both, so the
    # series doesn't jump when WFP stops reporting wholesale.
    wide = monthly.pivot_table(index=["date", "commodity_std"], columns="pricetype",
                               values="price_ngn_mt").reset_index()
    for col in ("Wholesale", "Retail"):
        if col not in wide.columns:
            wide[col] = np.nan
    wide["price_ngn_mt"] = wide["Wholesale"].fillna(wide["Retail"])
    wide["notes"] = np.where(wide["Wholesale"].notna(), "WFP VAM data — converted to NGN/MT",
                             "WFP VAM data (retail) — converted to NGN/MT")
    for commodity in WHOLESALE_EQUIVALENT:
        sel = wide["commodity_std"] == commodity
        both = sel & wide["Wholesale"].notna() & wide["Retail"].notna()
        if both.sum() < 24:
            logger.warning(f"  WFP: {commodity} has only {int(both.sum())} months with both price "
                           f"types - not converting retail to wholesale-equivalent")
            continue
        ratio = float((wide.loc[both, "Wholesale"] / wide.loc[both, "Retail"]).median())
        retail_only = sel & wide["Wholesale"].isna() & wide["Retail"].notna()
        wide.loc[retail_only, "price_ngn_mt"] = wide.loc[retail_only, "Retail"] * ratio
        wide.loc[retail_only, "notes"] = (f"WFP VAM retail × {ratio:.3f} (median wholesale/retail "
                                          f"ratio, {int(both.sum())} months) — wholesale-equivalent NGN/MT")
        logger.info(f"  WFP: {commodity} retail-only months scaled by {ratio:.3f} "
                    f"to wholesale-equivalent ({int(retail_only.sum())} months)")

    for commodity, (ratio, n_pairs, proxy) in proxy_ratios.items():
        retail_only = (wide["commodity_std"] == commodity) & wide["Wholesale"].isna() & wide["Retail"].notna()
        wide.loc[retail_only, "price_ngn_mt"] = wide.loc[retail_only, "Retail"] * ratio
        wide.loc[retail_only, "notes"] = (f"WFP VAM retail × {ratio:.3f} (median wholesale/retail ratio "
                                          f"of {proxy}, {n_pairs} market-months) — wholesale-equivalent NGN/MT")
        logger.info(f"  WFP: {commodity} retail scaled by {ratio:.3f} ({proxy} wholesale/retail) "
                    f"to wholesale-equivalent ({int(retail_only.sum())} months)")
    wide["market_type"] = np.where(wide["notes"].str.contains(r"\(retail\)", regex=True),
                                   "retail", "wholesale")
    # Provisional months (of the price type this row actually uses)
    used = np.where(wide["Wholesale"].notna(), "Wholesale", "Retail")
    prov_note = pd.Series([provisional.get((d, c, t), (None, None))[1]
                           for d, c, t in zip(wide["date"], wide["commodity_std"], used)], index=wide.index)
    is_prov = prov_note.notna()
    wide.loc[is_prov, "notes"] = wide.loc[is_prov, "notes"] + " — " + prov_note[is_prov]
    wide["data_source"] = np.where(is_prov, "WFP_provisional", "WFP")

    comm_agg = (wide[["date", "commodity_std", "price_ngn_mt", "notes", "market_type", "data_source"]]
                .dropna(subset=["price_ngn_mt"])
                .rename(columns={"commodity_std": "commodity"}))

    # Round to weekly Monday
    comm_agg["date"] = pd.to_datetime(comm_agg["date"])

    # Build standard schema
    comm_agg["currency"]           = "NGN"
    comm_agg["unit"]               = "NGN/MT"
    comm_agg["source"]             = "WFP Nigeria Food Prices"
    comm_agg["region"]             = "National"
    comm_agg["fx_rate"]            = np.nan
    comm_agg["rainfall_index"]     = np.nan
    comm_agg["data_quality_score"] = np.where(comm_agg["data_source"] == "WFP_provisional", 0.7, 0.9)
    comm_agg["is_validated"]       = True

    logger.success(
        f"WFP commodities loaded: {len(comm_agg):,} rows | "
        f"{comm_agg['commodity'].nunique()} commodities"
    )
    logger.debug(f"  Commodities: {sorted(comm_agg['commodity'].unique())}")
    logger.debug(
        f"  Date range:  {comm_agg['date'].min().date()} → {comm_agg['date'].max().date()}"
    )
    return comm_agg, fuel_pivot


# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — MERGE & DEDUPLICATE
# ─────────────────────────────────────────────────────────────────────────────

LEGACY_WFP_SOURCE = "WFP Nigeria Food Prices"


LEGACY_BAND = (0.4, 2.5)     # a leftover legacy month must sit within this x the current data...
LEGACY_NEAR_DAYS = 183       # ...within this many days of it (when there is any)


def place_legacy_wfp(agricome_df: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """
    The Agricome file's rows with source "WFP Nigeria Food Prices" are an older
    extract of the WFP survey (national averages, retail; beans 2016-26, maize
    2003-23), not Agricome posts. 85-89% of their months are also in the
    current WFP/FEWS data, a week apart, so each series held the same survey
    twice at two price levels - a monthly zigzag (found 16 Oct 2026).

    Keep a legacy row only for a month the current data lacks, scaled by the
    median legacy/current ratio over the months both have, and label it WFP.
    If the ratio is unstable (IQR over 0.3) or rests on under 12 months, the
    leftover months are dropped instead.
    """
    legacy = agricome_df["source"].astype(str) == LEGACY_WFP_SOURCE
    if not legacy.any():
        return agricome_df
    cur = current[current["data_source"].isin(["WFP", "WFP_provisional", "FEWS_NET"])]
    keep = [agricome_df[~legacy]]
    for c, g in agricome_df[legacy].groupby("commodity"):
        cc = cur[cur["commodity"] == c]
        cur_m = cc.groupby(cc["date"].dt.to_period("M"))["price_ngn_mt"].median()
        months = g["date"].dt.to_period("M")
        shared = months.isin(cur_m.index).values
        ratios = g.loc[shared, "price_ngn_mt"].values / cur_m.loc[months[shared]].values
        rest = g[~shared].copy()
        if len(ratios) < 12 or np.subtract(*np.percentile(ratios, [75, 25])) > 0.3:
            logger.info(f"  {c}: older WFP extract - {int(shared.sum())} months already in the current data, "
                        f"{len(rest)} left over dropped (level vs current data not stable)")
            continue
        ratio = float(np.median(ratios))
        rest["price_ngn_mt"] = rest["price_ngn_mt"] / ratio
        slip = [(c, m) in SLIPPED_MONTHS for m in rest["date"].dt.to_period("M")]
        if any(slip):
            logger.info(f"  {c}: older WFP extract - dropped {sum(slip)} leftover month(s) with a unit slip "
                        f"(WFP_UNIT_SLIPS)")
            rest = rest[[not x for x in slip]]
        # A leftover month far from the current data around it is the same
        # error the current file had: the extract's Jan 2016 beans (N4,200 per
        # 2.5kg, ~7x June's) is Abba Gamaram's mis-dated month (WFP_MISDATED).
        near = [cc.loc[(cc["date"] - d).abs() <= pd.Timedelta(days=LEGACY_NEAR_DAYS), "price_ngn_mt"].median()
                for d in rest["date"]]
        off = [(n == n) and not (LEGACY_BAND[0] <= p / n <= LEGACY_BAND[1])
               for p, n in zip(rest["price_ngn_mt"], near)]
        if any(off):
            logger.info(f"  {c}: older WFP extract - dropped {sum(off)} leftover month(s) more than "
                        f"{LEGACY_BAND[1]}x off the current data within {LEGACY_NEAR_DAYS} days")
            rest = rest[[not o for o in off]]
        rest["data_source"], rest["market_type"] = "WFP", "wholesale"
        rest["notes"] = (f"Older WFP national extract, scaled by 1/{ratio:.3f} to the current WFP/FEWS level "
                         f"(median over {len(ratios)} shared months)")
        keep.append(rest)
        logger.info(f"  {c}: older WFP extract - {int(shared.sum())} months already in the current data dropped, "
                    f"{len(rest)} months kept (scaled x{1 / ratio:.3f})")
    return pd.concat(keep, ignore_index=True)


def merge_sources(agricome_df: pd.DataFrame, wfp_df: pd.DataFrame,
                  web_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Merge Agricome and WFP commodity data.

    Priority rule:
      - For rows where both sources have the same commodity + week:
        → Agricome wins (it's our primary, manually validated source)
      - WFP fills gaps where Agricome has no data for that date
    """
    logger.info("Merging Agricome and WFP data sources...")

    # Align date columns
    agricome_df["date"] = pd.to_datetime(agricome_df["date"])
    wfp_df["date"]      = pd.to_datetime(wfp_df["date"])

    # Keep each row's own date: when a source posts several times in one week
    # (Agricome posts daily), the LATEST post must win the week. Before this the
    # first post won, so Agricome's 15 Oct 2026 price changes would have been
    # dropped in favour of the unchanged 12 Oct post.
    agricome_df["_obs_date"] = agricome_df["date"]
    wfp_df["_obs_date"] = wfp_df["date"]

    # Snap both to week-start (Monday)
    agricome_df["date"] = agricome_df["date"] - pd.to_timedelta(
        agricome_df["date"].dt.dayofweek, unit="D"
    )
    wfp_df["date"] = wfp_df["date"] - pd.to_timedelta(
        wfp_df["date"].dt.dayofweek, unit="D"
    )

    # Stack them
    if web_df is not None and len(web_df) > 0:
        web_df["date"] = pd.to_datetime(web_df["date"])
        web_df["_obs_date"] = web_df["date"]
        web_df["date"] = web_df["date"] - pd.to_timedelta(web_df["date"].dt.dayofweek, unit="D")
    agricome_df = place_legacy_wfp(agricome_df, pd.concat([wfp_df] + ([web_df] if web_df is not None else []),
                                                          ignore_index=True))
    frames = [agricome_df, wfp_df]
    if web_df is not None and len(web_df) > 0:
        frames.append(web_df)
    combined = pd.concat(frames, ignore_index=True)

    # Sort: Agricome first, WFP second, web-sourced last — so when we drop
    # duplicates, the higher-quality row is kept
    source_priority = {"Agricome": 0, "Agrolinking_primary": 0, "FEWS_NET": 0.8, "WFP": 1,
                       "WFP_provisional": 1.1, "AFEX": 1.5, "Web_search": 2, "WB_RTP_estimate": 3}
    combined["_priority"] = combined["data_source"].map(source_priority).fillna(9)
    combined = combined.sort_values(["commodity", "date", "_priority", "_obs_date"],
                                    ascending=[True, True, True, False], kind="stable")

    # Drop duplicates: keep first (Agricome wins; within a source, its latest post that week)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["commodity", "date"], keep="first")
    dupes_removed = before - len(combined)
    logger.info(f"  Deduplication: removed {dupes_removed:,} overlapping rows (Agricome priority)")

    combined = combined.drop(columns=["_priority", "_obs_date"])

    # Only keep the 11 target commodities
    combined = combined[combined["commodity"].isin(COMMODITIES)]

    # Sort final output
    combined = combined.sort_values(["commodity", "date"]).reset_index(drop=True)

    logger.success(
        f"Merged dataset: {len(combined):,} rows | "
        f"{combined['commodity'].nunique()} commodities"
    )

    # Coverage report
    logger.info("  Coverage per commodity:")
    for comm in sorted(combined["commodity"].unique()):
        sub = combined[combined["commodity"] == comm]
        logger.info(
            f"    {comm:<20} {len(sub):>4} rows | "
            f"{sub['date'].min().date()} → {sub['date'].max().date()}"
        )

    return combined


# ─────────────────────────────────────────────────────────────────────────────
# PART 4 — SAVE OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

def save_outputs(master_df: pd.DataFrame, fuel_df: pd.DataFrame):
    """Save the merged master dataset and fuel external features."""

    # ── Check: does master already exist? ────────────────────────────────────
    master_path = PATHS["master"]

    if os.path.exists(master_path):
        existing = pd.read_csv(master_path, parse_dates=["date"])
        # Drop rows that can't be real data — unknown commodity, no date or
        # no price. These appear when the CSV gets corrupted (e.g. git
        # conflict markers committed in ba28bf9 became "commodities" with
        # NaN dates, which made Prophet/XGBoost fail for 6 commodities).
        bad = (
            ~existing["commodity"].isin(COMMODITIES)
            | existing["date"].isna()
            | existing["price_ngn_mt"].isna()
        )
        if bad.any():
            logger.warning(f"  Dropping {int(bad.sum())} rows from existing master: malformed, or a "
                           f"commodity no longer tracked (the legacy Rice series, retired 23 Oct 2026)")
            existing = existing[~bad].copy()
        # Web-sourced rows are rebuilt from data/raw/web_prices.csv on every
        # ingest (they're in master_df), so that file is the single source
        # of truth: deleting a bad row there removes it from master too.
        # Same for WFP rows of every commodity this ingest reads from the WFP
        # file (so a month dropped for thin market coverage disappears), and
        # for World Bank estimate rows that older ingests stored as "Agricome"
        # (they come back from the raw file tagged WB_RTP_estimate).
        wfp_rebuilt = set(master_df.loc[master_df["data_source"] == "WFP", "commodity"])
        stale_wfp = (existing["data_source"] == "WFP") & existing["commodity"].isin(wfp_rebuilt)
        mislabelled_estimate = ((existing["data_source"] == "Agricome")
                                & existing["notes"].astype(str).str.contains("World Bank RTP|DERIVED:", regex=True))
        # the older WFP extract older ingests stored as "Agricome" (place_legacy_wfp rebuilds it)
        legacy_wfp = ((existing["data_source"] == "Agricome")
                      & (existing["source"].astype(str) == LEGACY_WFP_SOURCE))
        # (provisional WFP months, too: rebuilt from the WFP file, gone once WFP completes the month;
        # field quotes, rebuilt from data/raw/field_prices.csv; and sibling estimates, rebuilt by
        # 02_clean.py - kept, an unranked one would outrank a new real price the same week)
        existing_historical = existing[(existing["record_type"] == "historical")
                                       & ~existing["data_source"].isin(["Web_search", "AFEX", "FEWS_NET",
                                                                        "WFP_provisional", SIBLING_SOURCE])
                                       & (existing["source"].astype(str) != FIELD_QUOTES_SOURCE)
                                       & ~stale_wfp & ~mislabelled_estimate & ~legacy_wfp]
        forecast_rows = existing[existing["record_type"] == "forecast"]
        # Validated_actual rows — today's forecast, confirmed accurate by
        # 06_validate.py, appended so last_known_date advances daily.
        # Without preserving these here too, every re-ingest silently
        # erases the validated-anchor fix in 05_forecast.py, since these
        # rows match neither "historical" nor "forecast".
        validated_rows = existing[existing["record_type"] == "validated_actual"]

        logger.info(
            f"  Existing master found: {len(existing):,} rows "
            f"({len(existing_historical):,} historical + {len(forecast_rows):,} forecast "
            f"+ {len(validated_rows):,} validated_actual)"
        )

        # Merge strategy: preserve all existing data, layer fresh ingest on top.
        # Priority: new Agricome/WFP data > existing historical > synthetic/carry-forward
        master_df["record_type"] = "historical"
        # highest priority — just ingested (web-sourced still ranks below any
        # real row, including Agrolinking_primary rows that live only in master)
        master_df["_priority"] = master_df["data_source"].map(
            {"FEWS_NET": 0.8, "WFP": 1, "WFP_provisional": 1.1, "AFEX": 1.2, "Web_search": 1.5,
             "WB_RTP_estimate": 1.8}).fillna(0)

        # Tag existing rows by source priority
        existing_historical["_priority"] = existing_historical["data_source"].map({
            "Agricome": 0, "Agrolinking_primary": 0, "FEWS_NET": 0.8, "WFP": 1, "AFEX": 1.2, "Web_search": 1.5,
            "WB_RTP_estimate": 1.8,
            "Synthetic (World Bank/FAO × FX)": 3,
            "Interpolated": 4, "Agrolinking_old": 5,
        }).fillna(6)

        # forecast_rows and validated_rows previously got NO explicit
        # _priority — meaning pandas assigned them NaN after concat, and
        # NaN always sorts LAST regardless of value. That made them lose
        # a same-date tie-break to ANY row with an explicit priority,
        # including low-priority placeholders like "Interpolated" —
        # exactly backwards, since a validated_actual row (a live price
        # confirmed <3% off a real reference) should outrank a synthetic
        # gap-fill row, not lose to it. Assign explicit priorities so the
        # ordering is real (0-1) > validated_actual (2) > synthetic/
        # interpolated/old (3-5) > raw uncorrected forecast (6, lowest —
        # never trust an unvalidated future projection over anything).
        if len(forecast_rows) > 0:
            forecast_rows = forecast_rows.copy()
            forecast_rows["_priority"] = 6
        if len(validated_rows) > 0:
            validated_rows = validated_rows.copy()
            validated_rows["_priority"] = 2

        # Combine: new ingest + ALL existing (including Wheat, synthetic,
        # screenshots, and validated_actual rows from 06_validate.py)
        combined = pd.concat(
            [master_df, existing_historical, forecast_rows, validated_rows],
            ignore_index=True,
        )
        combined["date"] = pd.to_datetime(combined["date"])
        combined["date"] = combined["date"] - pd.to_timedelta(combined["date"].dt.dayofweek, unit="D")
        combined = combined.sort_values(["commodity", "date", "_priority"])
        # DIAGNOSTIC — how many validated_actual rows survive the dedup,
        # and if any are lost, exactly which (commodity, week) they lost
        # the tie-break on. Temporary — remove once the count is stable.
        pre_dedup_validated = combined[combined["record_type"] == "validated_actual"].copy()
        combined = combined.drop_duplicates(subset=["commodity", "date"], keep="first")
        post_dedup_validated = combined[combined["record_type"] == "validated_actual"]
        lost = pre_dedup_validated[
            ~pre_dedup_validated.set_index(["commodity", "date"]).index.isin(
                post_dedup_validated.set_index(["commodity", "date"]).index
            )
        ]
        logger.info(
            f"  [DIAG] validated_actual: {len(pre_dedup_validated)} entered dedup, "
            f"{len(post_dedup_validated)} survived, {len(lost)} lost"
        )
        if len(lost) > 0:
            beaten_by = combined[
                combined.set_index(["commodity", "date"]).index.isin(
                    lost.set_index(["commodity", "date"]).index
                )
            ]
            for _, row in lost.head(10).iterrows():
                winner = beaten_by[
                    (beaten_by["commodity"] == row["commodity"]) &
                    (beaten_by["date"] == row["date"])
                ]
                w_source = winner["data_source"].iloc[0] if len(winner) else "NONE FOUND"
                w_priority = winner["_priority"].iloc[0] if len(winner) else "n/a"
                logger.info(
                    f"    [DIAG]   {row['commodity']:<20} week-of {row['date'].date()} "
                    f"lost to data_source={w_source} (_priority={w_priority})"
                )
        combined = combined.drop(columns=["_priority"], errors="ignore")
        combined = combined.sort_values(["commodity", "date"]).reset_index(drop=True)
        combined.to_csv(master_path, index=False)
        logger.success(f"  Master updated: {len(combined):,} total rows → {master_path}")

    else:
        # First run — create the master file
        master_df["record_type"] = "historical"
        master_df.to_csv(master_path, index=False)
        logger.success(f"  Master created: {len(master_df):,} rows → {master_path}")

    # ── Fuel prices ───────────────────────────────────────────────────────────
    # data/external/fuel_prices.csv is built by fetch_macro.py (step 00g) from
    # WFP's real petrol prices. This step used to merge into it and kept any
    # row with both diesel and petrol - which, once WFP stopped reporting
    # diesel (2023), meant smooth projected rows always beat the real ones.
    if not os.path.exists(PATHS["fuel_prices"]):
        fuel_df.to_csv(PATHS["fuel_prices"], index=False)
        logger.success(f"  Fuel prices saved: {len(fuel_df):,} rows → {PATHS['fuel_prices']}")

    # ── Save ingestion metadata log ───────────────────────────────────────────
    meta = {
        "run_timestamp":   datetime.now().isoformat(),
        "master_rows":     len(master_df),
        "commodities":     sorted(master_df["commodity"].unique().tolist()),
        "date_range_min":  str(master_df["date"].min().date()),
        "date_range_max":  str(master_df["date"].max().date()),
        "sources":         sorted(master_df["data_source"].unique().tolist()),
    }
    import json
    log_path = os.path.join(PATHS["logs_dir"], "ingestion_meta.json")
    with open(log_path, "w") as f:
        json.dump(meta, f, indent=2)
    logger.info(f"  Ingestion metadata saved → {log_path}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_ingestion() -> pd.DataFrame:
    """Run the full ingestion pipeline. Returns the master DataFrame."""
    logger.info("=" * 60)
    logger.info("STEP 1 — DATA INGESTION")
    logger.info("=" * 60)

    agricome_df           = load_agricome()
    wfp_commodity_df, fuel_df = load_wfp()
    web_df                = pd.concat([load_web_prices(), load_afex_prices(), load_fews_prices(),
                                       load_field_prices()], ignore_index=True)
    master_df             = merge_sources(agricome_df, wfp_commodity_df, web_df)
    save_outputs(master_df, fuel_df)

    logger.info("=" * 60)
    logger.success("INGESTION COMPLETE")
    logger.info("=" * 60)

    return master_df


if __name__ == "__main__":
    run_ingestion()