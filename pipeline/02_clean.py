"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 2: Data Cleaning — Rewritten to fix IQR contamination bug.

Key rules:
- Real sources (Agricome, WFP, Agrolinking_primary) are NEVER modified.
- IQR runs only on synthetic/interpolated rows, within their own price regime.
- Smoothing only on synthetic/interpolated rows.
- Gap-fill only within the real data window (not extending beyond it).
"""
import os, sys, warnings
import pandas as pd
import numpy as np
from loguru import logger
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

REAL_SOURCES = {"Agricome","WFP","Agrolinking_primary"}

HARD_BOUNDS = {
    "Sorghum":       {"floor":50_000,    "ceil":1_000_000},
    "Maize (white)": {"floor":20_000,    "ceil":600_000},
    "Maize (yellow)":{"floor":20_000,    "ceil":600_000},
    "Beans (white)": {"floor":100_000,   "ceil":2_000_000},
    "Beans (red)":   {"floor":100_000,   "ceil":2_500_000},
    "Ginger":        {"floor":1_000_000, "ceil":50_000_000},
    "Cocoa":         {"floor":500_000,   "ceil":30_000_000},
    "Hibiscus":      {"floor":200_000,   "ceil":10_000_000},
    "Sesame":        {"floor":150_000,   "ceil":5_000_000},
    "Soybeans":      {"floor":50_000,    "ceil":3_000_000},
    "Cashew Nuts":   {"floor":200_000,   "ceil":8_000_000},
    "Wheat":         {"floor":300_000,   "ceil":2_000_000},
}

def run_cleaning():
    logger.info("=" * 60)
    logger.info("STEP 2 — DATA CLEANING & STANDARDIZATION")
    logger.info("=" * 60)

    df = pd.read_csv(PATHS["master"], parse_dates=["date"])
    logger.success(f"Loaded: {len(df):,} rows | {df['commodity'].nunique()} commodities")

    df["date"] = pd.to_datetime(df["date"])
    df["date"] = df["date"] - pd.to_timedelta(df["date"].dt.dayofweek, unit="D")
    df["outlier_flag"]   = df.get("outlier_flag", False).fillna(False).astype(bool)
    df["outlier_reason"] = df.get("outlier_reason", "").fillna("")

    # Dedup
    before = len(df)
    df = df.drop_duplicates(subset=["commodity","date"], keep="first")
    removed = before - len(df)
    logger.info(f"  Duplicates removed: {removed}" if removed else "  No duplicates found")

    # ── PERMANENT CONTAMINATION FIX ──────────────────────────────────────────
    # Every time a commodity's real data (Agricome/WFP/Agrolinking_primary)
    # advances to a new, more recent date, any OLD "Interpolated" rows sitting
    # inside the real-data window are stale leftovers from a previous run's
    # gap-fill against an older, shorter real range. If left in place, the
    # gap-fill below only fills genuinely MISSING dates — it never refreshes
    # dates that already have a (now-outdated) interpolated row sitting there.
    # That produces a zigzag: real anchors on one path, stale interpolated
    # rows on a completely different, disconnected path, for the same weeks.
    # This is exactly what happened to Maize, then recurred for Sorghum and
    # Beans the moment their real anchor advanced via the WFP refresh.
    #
    # Fix: on every run, purge ALL "Interpolated" rows that fall inside the
    # CURRENT real-data window for each commodity, then let gap-fill below
    # regenerate them fresh from the current real anchors. Real rows are
    # never touched. This is cheap (gap-fill already runs every time) and
    # makes the pipeline self-healing — no manual per-commodity rescue
    # script needed ever again.
    # This also had a real gap: it only purged data_source == "Interpolated",
    # but "Bridge" (and potentially other synthetic filler tags) sitting
    # inside the real window cause the exact same contamination. Broadened
    # to purge ANY non-real-source row inside the real window, not just one
    # specific tag name.
    logger.info("  Purging stale non-real rows inside the current real-data window...")
    total_purged = 0
    for commodity in COMMODITIES:
        sub = df[df["commodity"] == commodity]
        real_sub = sub[sub["data_source"].isin(REAL_SOURCES)]
        if len(real_sub) < 2:
            continue
        real_start, real_end = real_sub["date"].min(), real_sub["date"].max()
        purge_mask = (
            (df["commodity"] == commodity) &
            (df["date"] >= real_start) &
            (df["date"] <= real_end) &
            (~df["data_source"].isin(REAL_SOURCES))
        )
        n = purge_mask.sum()
        if n:
            total_purged += n
            purged_sources = df.loc[purge_mask, "data_source"].value_counts().to_dict()
            logger.debug(f"    {commodity}: purged {n} stale row(s) {purged_sources} "
                         f"inside real window [{real_start.date()} -> {real_end.date()}]")
            df = df[~purge_mask]
    logger.info(f"  Total stale non-real rows purged: {total_purged}")

    # Hard bounds — synthetic only
    hard_flagged = 0
    for commodity, bounds in HARD_BOUNDS.items():
        mask = (
            (df["commodity"]==commodity) &
            (~df["data_source"].isin(REAL_SOURCES)) &
            (~df["outlier_flag"]) &
            ((df["price_ngn_mt"]<bounds["floor"]) | (df["price_ngn_mt"]>bounds["ceil"]))
        )
        if mask.sum():
            df.loc[mask,"outlier_flag"] = True
            df.loc[mask,"outlier_reason"] = "Hard bound violation (synthetic)"
            hard_flagged += mask.sum()
    logger.info(f"  Hard-bound outliers flagged: {hard_flagged} (synthetic rows only)")

    # IQR — synthetic only, within their own price regime
    logger.info("  Running IQR outlier detection (synthetic rows only, k=3.0)...")
    iqr_total = 0
    for commodity in COMMODITIES:
        synthetic_mask = (
            (df["commodity"]==commodity) &
            (~df["outlier_flag"]) &
            (~df["data_source"].isin(REAL_SOURCES))
        )
        prices = df.loc[synthetic_mask,"price_ngn_mt"].dropna()
        if len(prices)<10: continue
        q1,q3 = prices.quantile(0.25), prices.quantile(0.75)
        iqr = q3-q1
        if iqr<=0: continue
        k = 3.0
        flag = synthetic_mask & (
            (df["price_ngn_mt"]<q1-k*iqr) | (df["price_ngn_mt"]>q3+k*iqr)
        )
        if flag.sum():
            df.loc[flag,"outlier_flag"] = True
            df.loc[flag,"outlier_reason"] = "IQR outlier (synthetic)"
            iqr_total += flag.sum()
            logger.debug(f"    {commodity}: {flag.sum()} IQR outlier(s) in synthetic data")
    logger.info(f"  IQR outliers flagged: {iqr_total}")

    # Replace outlier prices — synthetic only
    logger.info("  Replacing outlier prices with interpolated values...")
    fixed = 0
    for commodity in COMMODITIES:
        mask = (df["commodity"]==commodity) & df["outlier_flag"]
        if mask.sum()==0: continue
        df.loc[mask,"price_ngn_mt"] = np.nan
        idx = df["commodity"]==commodity
        df.loc[idx,"price_ngn_mt"] = (
            df.loc[idx,"price_ngn_mt"]
            .interpolate(method="linear",limit_direction="both")
            .ffill().bfill()
        )
        fixed += mask.sum()
    logger.info(f"  Outlier prices fixed: {fixed}")

    # Gap fill — within real data window only
    logger.info("  Filling weekly date gaps...")
    total_gaps = 0
    new_rows = []
    for commodity in COMMODITIES:
        sub      = df[df["commodity"]==commodity].sort_values("date")
        real_sub = sub[sub["data_source"].isin(REAL_SOURCES)]
        if len(real_sub)<2: continue
        real_start, real_end = real_sub["date"].min(), real_sub["date"].max()
        expected = pd.date_range(real_start, real_end, freq="W-MON")
        existing = set(sub["date"].values)
        missing  = [d for d in expected if d not in existing]
        if missing:
            total_gaps += len(missing)
            logger.debug(f"    {commodity}: {len(missing)} gap(s) filled")
            for d in missing:
                new_rows.append({
                    "commodity":commodity,"date":d,"price_ngn_mt":np.nan,
                    "currency":"NGN","unit":"NGN/MT","source":"Gap-filled",
                    "market_type":"wholesale","region":"National",
                    "fx_rate":np.nan,"rainfall_index":np.nan,
                    "data_quality_score":0.60,"is_validated":False,
                    "notes":"Weekly gap-fill","data_source":"Interpolated",
                    "record_type":"historical","outlier_flag":False,
                    "outlier_reason":"","price_raw_ngn_mt":np.nan,
                })
    if new_rows:
        gap_df = pd.DataFrame(new_rows)
        df = pd.concat([df,gap_df],ignore_index=True)
        df = df.sort_values(["commodity","date"]).reset_index(drop=True)
        for commodity in COMMODITIES:
            mask = df["commodity"]==commodity
            df.loc[mask,"price_ngn_mt"] = (
                df.loc[mask,"price_ngn_mt"]
                .interpolate(method="linear",limit_direction="both")
                .ffill().bfill()
            )
    logger.info(f"  Total weekly gaps filled: {total_gaps}")

    # Selective smoothing — synthetic/interpolated only
    logger.info("  Applying selective smoothing (real data preserved)...")
    for commodity in COMMODITIES:
        mask = (
            (df["commodity"]==commodity) &
            (~df["data_source"].isin(REAL_SOURCES))
        )
        if mask.sum()<3: continue
        df.loc[mask,"price_ngn_mt"] = (
            df.loc[mask,"price_ngn_mt"]
            .rolling(window=3,min_periods=1,center=True).median()
        )

    df = df.sort_values(["commodity","date"]).reset_index(drop=True)
    df.to_csv(PATHS["master"],index=False)
    logger.success(f"Clean master saved: {len(df):,} rows → {PATHS['master']}")

    logger.info("\n  CLEAN DATA SUMMARY:")
    for commodity in COMMODITIES:
        sub = df[(df["commodity"]==commodity)&(df["record_type"]=="historical")]
        if len(sub)==0: continue
        real = sub[sub["data_source"].isin(REAL_SOURCES)]
        logger.info(
            f"    {commodity:<20} {len(sub):>5} hist rows | "
            f"₦{sub['price_ngn_mt'].min():>12,.0f} – "
            f"₦{sub['price_ngn_mt'].max():>12,.0f}/MT | "
            f"real: {len(real)}"
        )
    logger.success("CLEANING COMPLETE")
    return df

if __name__ == "__main__":
    run_cleaning()