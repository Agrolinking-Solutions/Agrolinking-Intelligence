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

    logger.success(f"Agricome loaded: {len(df):,} rows | {df['commodity'].nunique()} commodities")
    logger.debug(f"  Commodities: {sorted(df['commodity'].unique())}")
    logger.debug(f"  Date range:  {df['date'].min().date()} → {df['date'].max().date()}")
    return df


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
    "Fuel (diesel)":        "_fuel_diesel",      # external feature, not commodity forecast
    "Fuel (petrol-gasoline)": "_fuel_petrol",    # external feature
}


def convert_wfp_to_ngn_mt(price: float, unit: str) -> float:
    """Convert WFP price in its native unit to NGN/MT."""
    factor = WFP_UNIT_TO_MT.get(unit)
    if factor is None:
        return np.nan
    return price * factor


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

    # Use Wholesale price preferentially, fall back to Retail
    # Group by date + commodity, pick wholesale mean, else retail mean
    def agg_price(group):
        w = group[group["pricetype"] == "Wholesale"]["price_ngn_mt"]
        r = group[group["pricetype"] == "Retail"]["price_ngn_mt"]
        if len(w) > 0:
            return w.mean()
        return r.mean()

    comm_agg = (
        comm_df.groupby(["date", "commodity_std"])
        .apply(agg_price, include_groups=False)
        .reset_index()
        .rename(columns={"commodity_std": "commodity", 0: "price_ngn_mt"})
    )

    # Round to weekly Monday
    comm_agg["date"] = pd.to_datetime(comm_agg["date"])

    # Build standard schema
    comm_agg["currency"]           = "NGN"
    comm_agg["unit"]               = "NGN/MT"
    comm_agg["source"]             = "WFP Nigeria Food Prices"
    comm_agg["market_type"]        = "wholesale"
    comm_agg["region"]             = "National"
    comm_agg["fx_rate"]            = np.nan
    comm_agg["rainfall_index"]     = np.nan
    comm_agg["data_quality_score"] = 0.9
    comm_agg["is_validated"]       = True
    comm_agg["notes"]              = "WFP VAM data — converted to NGN/MT"
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

def merge_sources(agricome_df: pd.DataFrame, wfp_df: pd.DataFrame) -> pd.DataFrame:
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
    combined = pd.concat([agricome_df, wfp_df], ignore_index=True)

    # Sort: Agricome first, WFP second — so when we drop duplicates,
    # Agricome row is kept
    source_priority = {"Agricome": 0, "WFP": 1}
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
        existing_historical = existing[existing["record_type"] == "historical"]
        forecast_rows = existing[existing["record_type"] == "forecast"]

        logger.info(
            f"  Existing master found: {len(existing):,} rows "
            f"({len(existing_historical):,} historical + {len(forecast_rows):,} forecast)"
        )

        # Merge strategy: preserve all existing data, layer fresh ingest on top.
        # Priority: new Agricome/WFP data > existing historical > synthetic/carry-forward
        master_df["record_type"] = "historical"
        master_df["_priority"] = 0  # highest priority — just ingested

        # Tag existing rows by source priority
        existing_historical["_priority"] = existing_historical["data_source"].map({
            "Agricome": 0, "Agrolinking_primary": 0, "WFP": 1,
            "Synthetic (World Bank/FAO × FX)": 2,
            "Interpolated": 3, "Agrolinking_old": 4,
        }).fillna(5)

        # Combine: new ingest + ALL existing (including Wheat, synthetic, screenshots)
        combined = pd.concat([master_df, existing_historical, forecast_rows], ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"])
        combined["date"] = combined["date"] - pd.to_timedelta(combined["date"].dt.dayofweek, unit="D")
        combined = combined.sort_values(["commodity", "date", "_priority"])
        combined = combined.drop_duplicates(subset=["commodity", "date"], keep="first")
        combined = combined.drop(columns=["_priority"], errors="ignore")
        combined = combined.sort_values(["commodity", "date"]).reset_index(drop=True)
        combined.to_csv(master_path, index=False)
        logger.success(f"  Master updated: {len(combined):,} total rows → {master_path}")

    else:
        # First run — create the master file
        master_df["record_type"] = "historical"
        master_df.to_csv(master_path, index=False)
        logger.success(f"  Master created: {len(master_df):,} rows → {master_path}")

    # ── Save fuel data as external feature ───────────────────────────────────
    # Merge WFP-derived fuel with any manually patched rows already in file.
    # Manually patched rows (both diesel + petrol populated) win on conflict.
    fuel_path = PATHS["fuel_prices"]
    if os.path.exists(fuel_path):
        existing_fuel = pd.read_csv(fuel_path, parse_dates=["date"])
        combined_fuel = pd.concat([fuel_df, existing_fuel], ignore_index=True)
        combined_fuel = combined_fuel.sort_values("date")
        # Rows with both values populated are manually verified — keep those
        combined_fuel["_complete"] = (
            combined_fuel["fuel_diesel_ngn_litre"].notna() &
            combined_fuel["fuel_petrol_ngn_litre"].notna()
        )
        combined_fuel = combined_fuel.sort_values(["date", "_complete"], ascending=[True, False])
        combined_fuel = combined_fuel.drop_duplicates(subset=["date"], keep="first")
        combined_fuel = combined_fuel.drop(columns=["_complete"]).reset_index(drop=True)
        combined_fuel.to_csv(fuel_path, index=False)
        logger.success(f"  Fuel prices updated: {len(combined_fuel):,} rows → {fuel_path}")
    else:
        fuel_df.to_csv(fuel_path, index=False)
        logger.success(f"  Fuel prices saved: {len(fuel_df):,} rows → {fuel_path}")

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
    master_df             = merge_sources(agricome_df, wfp_commodity_df)
    save_outputs(master_df, fuel_df)

    logger.info("=" * 60)
    logger.success("INGESTION COMPLETE")
    logger.info("=" * 60)

    return master_df


if __name__ == "__main__":
    run_ingestion()
