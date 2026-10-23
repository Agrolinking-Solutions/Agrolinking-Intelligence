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
from config.settings import PATHS, COMMODITIES, WFP_NAME_MAP, STANDARD_UNIT

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


# AFEX commodity-exchange prices (data/raw/afex_prices.csv, read daily from
# Mansa Markets by fetch_afex_prices.py) used as REAL prices, per commodity,
# only where a person decided they fit. Yellow maize (John, 15 Oct 2026): no
# real price since 2023; AFEX lists one "maize" price without saying white or
# yellow, and the two trade within ~2% (WFP, 1,320 market-months).
AFEX_AS_REAL = {"maize": ["Maize (yellow)"]}


def load_afex_prices() -> pd.DataFrame:
    """AFEX exchange rows for AFEX_AS_REAL, one per commodity and week (the latest)."""
    cols = ["commodity", "date", "price_ngn_mt", "currency", "unit", "source", "market_type",
            "region", "fx_rate", "rainfall_index", "data_quality_score", "is_validated",
            "notes", "data_source"]
    path = os.path.join(os.path.dirname(PATHS["raw_web_prices"]), "afex_prices.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    a = pd.read_csv(path, parse_dates=["as_of_date"])
    a = a[(a["price_type"] == "exchange") & a["mansa_commodity"].isin(AFEX_AS_REAL)]
    rows = []
    for _, r in a.iterrows():
        for ours in AFEX_AS_REAL[r["mansa_commodity"]]:
            rows.append({"commodity": ours, "date": r["as_of_date"], "price_ngn_mt": float(r["price_ngn_mt"]),
                         "currency": "NGN", "unit": "NGN/MT", "source": "AFEX via Mansa Markets",
                         "market_type": "exchange", "region": "National", "fx_rate": np.nan,
                         "rainfall_index": np.nan, "data_quality_score": 0.9, "is_validated": True,
                         "notes": (f"AFEX exchange price, {r['price_as_shown']} as of "
                                   f"{r['as_of_date'].date()} ({r['mansa_commodity']}, type not stated) - {r['url']}"),
                         "data_source": "AFEX"})
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["week"] = df["date"] - pd.to_timedelta(df["date"].dt.dayofweek, unit="D")
    df = df.sort_values("date").groupby(["commodity", "week"], as_index=False).last()
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
    thin = monthly["n_markets"] < 0.3 * monthly["recent_median"]
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

    comm_agg = (wide[["date", "commodity_std", "price_ngn_mt", "notes", "market_type"]]
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
    comm_agg["data_quality_score"] = 0.9
    comm_agg["is_validated"]       = True
    comm_agg["data_source"]        = "WFP"

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
    cur = current[current["data_source"].isin(["WFP", "FEWS_NET"])]
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
        web_df["date"] = web_df["date"] - pd.to_timedelta(web_df["date"].dt.dayofweek, unit="D")
    agricome_df = place_legacy_wfp(agricome_df, pd.concat([wfp_df] + ([web_df] if web_df is not None else []),
                                                          ignore_index=True))
    frames = [agricome_df, wfp_df]
    if web_df is not None and len(web_df) > 0:
        frames.append(web_df)
    combined = pd.concat(frames, ignore_index=True)

    # Sort: Agricome first, WFP second, web-sourced last — so when we drop
    # duplicates, the higher-quality row is kept
    source_priority = {"Agricome": 0, "FEWS_NET": 0.8, "WFP": 1, "AFEX": 1.5, "Web_search": 2, "WB_RTP_estimate": 3}
    combined["_priority"] = combined["data_source"].map(source_priority).fillna(9)
    combined = combined.sort_values(["commodity", "date", "_priority"])

    # Drop duplicates: keep first (Agricome wins)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["commodity", "date"], keep="first")
    dupes_removed = before - len(combined)
    logger.info(f"  Deduplication: removed {dupes_removed:,} overlapping rows (Agricome priority)")

    combined = combined.drop(columns=["_priority"])

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
        existing_historical = existing[(existing["record_type"] == "historical")
                                       & ~existing["data_source"].isin(["Web_search", "AFEX", "FEWS_NET"])
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
            {"FEWS_NET": 0.8, "WFP": 1, "AFEX": 1.2, "Web_search": 1.5, "WB_RTP_estimate": 1.8}).fillna(0)

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
    web_df                = pd.concat([load_web_prices(), load_afex_prices(), load_fews_prices()],
                                      ignore_index=True)
    master_df             = merge_sources(agricome_df, wfp_commodity_df, web_df)
    save_outputs(master_df, fuel_df)

    logger.info("=" * 60)
    logger.success("INGESTION COMPLETE")
    logger.info("=" * 60)

    return master_df


if __name__ == "__main__":
    run_ingestion()