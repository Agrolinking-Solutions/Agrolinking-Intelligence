"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 3: Feature Engineering
─────────────────────────────────────────────────────────
Joins the clean master dataset with all external features:
  - FX rate (NGN/USD)
  - Fuel prices (diesel + petrol)
  - Food inflation & headline CPI
  - Nigerian agricultural season calendar
  - Lagged price features (1w, 2w, 4w, 8w, 12w, 26w, 52w)
  - Rolling statistics (mean, std, momentum)
  - Commodity-specific season flags
  - Trend & acceleration features
  - Global shock flag (war, pandemic, FX devaluation events)

Output: data/processed/features_{commodity}.csv  (one per commodity)

Run standalone:  python pipeline/03_features.py
"""

import os
import sys
import pandas as pd
import numpy as np
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, EXTERNAL_FEATURES

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
logger.add(
    os.path.join(PATHS["logs_dir"], "features_{time:YYYY-MM-DD}.log"),
    rotation="1 day", retention="30 days", level="DEBUG"
)

# ── Output directory for per-commodity feature files ─────────────────────────
FEATURES_DIR = os.path.join(os.path.dirname(PATHS["master"]), "features")
os.makedirs(FEATURES_DIR, exist_ok=True)

# ── Commodity → which season columns are most relevant ───────────────────────
COMMODITY_SEASON_MAP = {
    "Cashew Nuts":   ["cashew_harvest", "season_dry", "is_lean_season"],
    "Cocoa":         ["cocoa_main_crop", "cocoa_mid_crop", "season_rainy"],
    "Sesame":        ["sesame_harvest", "season_harvesting", "season_planting"],
    "Ginger":        ["ginger_harvest", "season_rainy", "season_planting"],
    "Hibiscus":      ["hibiscus_harvest", "season_harvesting", "season_rainy"],
    "Soybeans":      ["soybean_harvest", "season_planting", "season_harvesting"],
    "Sorghum":       ["sorghum_harvest", "season_planting", "season_dry"],
    "Maize (white)": ["maize_harvest", "season_planting", "is_lean_season"],
    "Maize (yellow)":["maize_harvest", "season_planting", "is_lean_season"],
    "Beans (white)": ["season_harvesting", "season_planting", "is_lean_season"],
    "Beans (red)":   ["season_harvesting", "season_planting", "is_lean_season"],
}

# ── Known shock event windows ─────────────────────────────────────────────────
# These are binary flags covering periods with verified external shocks
SHOCK_EVENTS = [
    # (name, start, end, severity 1-3)
    ("naira_devaluation_2016",   "2016-06-01", "2016-10-31", 3),
    ("covid_lockdown_2020",      "2020-03-01", "2020-09-30", 3),
    ("russia_ukraine_war_2022",  "2022-02-24", "2022-12-31", 2),
    ("subsidy_removal_2023",     "2023-05-29", "2023-12-31", 3),
    ("naira_collapse_2024",      "2024-01-01", "2024-06-30", 3),
    ("middle_east_war_2026",     "2026-03-13", "2026-12-31", 2),
]


# ─────────────────────────────────────────────────────────────────────────────
# LOADERS
# ─────────────────────────────────────────────────────────────────────────────

def load_external_data() -> dict:
    """Load all external feature datasets."""
    data = {}

    # FX rates
    data["fx"] = pd.read_csv(PATHS["fx_rates"], parse_dates=["date"])

    # Fuel prices
    data["fuel"] = pd.read_csv(PATHS["fuel_prices"], parse_dates=["date"])

    # Inflation
    data["inflation"] = pd.read_csv(PATHS["inflation"], parse_dates=["date"])

    # Season calendar
    data["seasons"] = pd.read_csv(PATHS["season_calendar"], parse_dates=["date"])

    logger.info(f"  External data loaded:")
    logger.info(f"    FX:        {len(data['fx']):,} rows ({data['fx']['date'].min().date()} → {data['fx']['date'].max().date()})")
    logger.info(f"    Fuel:      {len(data['fuel']):,} rows ({data['fuel']['date'].min().date()} → {data['fuel']['date'].max().date()})")
    logger.info(f"    Inflation: {len(data['inflation']):,} rows")
    logger.info(f"    Seasons:   {len(data['seasons']):,} rows")

    return data


# ─────────────────────────────────────────────────────────────────────────────
# SNAP ALL DATES TO MONDAY WEEK-START
# ─────────────────────────────────────────────────────────────────────────────

def snap_to_monday(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    df = df.copy()
    df[col] = pd.to_datetime(df[col])
    df[col] = df[col] - pd.to_timedelta(df[col].dt.dayofweek, unit="D")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# MERGE EXTERNAL FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def merge_external(df: pd.DataFrame, ext: dict) -> pd.DataFrame:
    """Left-join all external feature tables onto the commodity time series."""
    df = snap_to_monday(df)

    # FX
    fx = snap_to_monday(ext["fx"])
    df = df.merge(fx[["date", "fx_rate_usd_ngn"]], on="date", how="left")

    # Fuel
    fuel = snap_to_monday(ext["fuel"])
    df = df.merge(
        fuel[["date", "fuel_diesel_ngn_litre", "fuel_petrol_ngn_litre"]],
        on="date", how="left"
    )

    # Inflation
    inf = snap_to_monday(ext["inflation"])
    df = df.merge(
        inf[["date", "headline_inflation_yoy", "food_inflation_yoy"]],
        on="date", how="left"
    )

    # Seasons
    seasons = snap_to_monday(ext["seasons"])
    df = df.merge(seasons, on="date", how="left")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# FORWARD-FILL EXTERNAL GAPS
# ─────────────────────────────────────────────────────────────────────────────

def fill_external_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill and back-fill gaps in external features."""
    ext_cols = [
        "fx_rate_usd_ngn",
        "fuel_diesel_ngn_litre", "fuel_petrol_ngn_litre",
        "headline_inflation_yoy", "food_inflation_yoy",
    ]
    for col in ext_cols:
        if col in df.columns:
            df[col] = df[col].ffill().bfill()

    return df


# ─────────────────────────────────────────────────────────────────────────────
# PRICE IN USD (cross-market comparison feature)
# ─────────────────────────────────────────────────────────────────────────────

def add_usd_price(df: pd.DataFrame) -> pd.DataFrame:
    """Add price in USD/MT for cross-reference validation."""
    df["price_usd_mt"] = (
        df["price_ngn_mt"] / df["fx_rate_usd_ngn"]
    ).round(2)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# LAG FEATURES
# ─────────────────────────────────────────────────────────────────────────────

LAG_WEEKS = [1, 2, 4, 8, 12, 26, 52]

def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add lagged price values.
    Models learn: 'given prices 1, 4, 8, 26 weeks ago, what is the price now?'
    """
    df = df.sort_values("date").reset_index(drop=True)
    for lag in LAG_WEEKS:
        col = f"price_lag_{lag}w"
        df[col] = df["price_ngn_mt"].shift(lag)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# ROLLING STATISTICS
# ─────────────────────────────────────────────────────────────────────────────

def add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rolling mean, std, and min/max over multiple windows.
    Captures trend direction and volatility regime.
    """
    df = df.sort_values("date").reset_index(drop=True)
    price = df["price_ngn_mt"]

    for window in [4, 8, 12, 26]:
        df[f"rolling_mean_{window}w"]  = price.rolling(window, min_periods=2).mean()
        df[f"rolling_std_{window}w"]   = price.rolling(window, min_periods=2).std()

    # 4-week and 12-week min/max (range compression proxy)
    df["rolling_max_4w"]  = price.rolling(4,  min_periods=2).max()
    df["rolling_min_4w"]  = price.rolling(4,  min_periods=2).min()
    df["rolling_max_12w"] = price.rolling(12, min_periods=2).max()
    df["rolling_min_12w"] = price.rolling(12, min_periods=2).min()

    return df


# ─────────────────────────────────────────────────────────────────────────────
# MOMENTUM & TREND FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def add_momentum_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Week-on-week change, momentum, and acceleration.
    These are key signals for XGBoost and LSTM.
    """
    df = df.sort_values("date").reset_index(drop=True)
    price = df["price_ngn_mt"]

    # Week-on-week % change
    df["pct_change_1w"]  = price.pct_change(1)  * 100
    df["pct_change_4w"]  = price.pct_change(4)  * 100
    df["pct_change_12w"] = price.pct_change(12) * 100
    df["pct_change_52w"] = price.pct_change(52) * 100

    # Momentum: difference between short and long rolling means
    # Positive = upward trend, Negative = downward trend
    rm4  = price.rolling(4,  min_periods=2).mean()
    rm26 = price.rolling(26, min_periods=2).mean()
    df["momentum_4w_vs_26w"] = ((rm4 - rm26) / rm26 * 100).round(3)

    # Acceleration: is the rate of change speeding up or slowing down?
    df["acceleration_1w"] = df["pct_change_1w"].diff(1)

    # Price relative to its 52-week mean (z-score-like)
    rm52 = price.rolling(52, min_periods=12).mean()
    std52 = price.rolling(52, min_periods=12).std()
    df["price_zscore_52w"] = ((price - rm52) / std52).round(3)

    # Log price (stabilises variance for ARIMA/LSTM)
    df["log_price"] = np.log1p(price)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# FUEL COST PRESSURE FEATURE
# ─────────────────────────────────────────────────────────────────────────────

def add_fuel_pressure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Composite fuel cost pressure index.
    Diesel drives transport cost for agricultural commodities.
    Petrol drives generator and small-scale processing costs.
    """
    if "fuel_diesel_ngn_litre" in df.columns and "fuel_petrol_ngn_litre" in df.columns:
        # Normalise by first available value
        d0 = df["fuel_diesel_ngn_litre"].dropna().iloc[0] if df["fuel_diesel_ngn_litre"].notna().any() else 200.0
        p0 = df["fuel_petrol_ngn_litre"].dropna().iloc[0] if df["fuel_petrol_ngn_litre"].notna().any() else 165.0

        diesel_idx = df["fuel_diesel_ngn_litre"] / d0
        petrol_idx = df["fuel_petrol_ngn_litre"] / p0

        # Diesel weighted higher (70%) as primary logistics cost
        df["fuel_cost_index"] = (0.70 * diesel_idx + 0.30 * petrol_idx).round(3)
        df["fuel_cost_index"] = df["fuel_cost_index"].ffill().bfill()

    return df


# ─────────────────────────────────────────────────────────────────────────────
# SHOCK EVENT FLAGS
# ─────────────────────────────────────────────────────────────────────────────

def add_shock_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add binary flags for known macro shock events."""
    df["shock_active"]   = 0
    df["shock_severity"] = 0

    for name, start, end, severity in SHOCK_EVENTS:
        mask = df["date"].between(start, end)
        df.loc[mask, "shock_active"]   = 1
        df.loc[mask, "shock_severity"] = df.loc[mask, "shock_severity"].clip(lower=severity)
        # Use max severity when events overlap
        df.loc[mask, "shock_severity"] = df.loc[mask, "shock_severity"].apply(
            lambda x: max(x, severity)
        )

    return df


# ─────────────────────────────────────────────────────────────────────────────
# COMMODITY-SPECIFIC SEASON RELEVANCE SCORE
# ─────────────────────────────────────────────────────────────────────────────

def add_commodity_season_score(df: pd.DataFrame, commodity: str) -> pd.DataFrame:
    """
    Composite season relevance score for this specific commodity.
    Combines the 2-3 most relevant season flags into a single signal.
    """
    relevant_cols = COMMODITY_SEASON_MAP.get(commodity, ["season_harvesting", "season_planting"])
    available = [c for c in relevant_cols if c in df.columns]

    if available:
        df["commodity_season_score"] = df[available].mean(axis=1).round(3)
    else:
        df["commodity_season_score"] = 0.0

    return df


# ─────────────────────────────────────────────────────────────────────────────
# REAL PRICE (INFLATION-ADJUSTED)
# ─────────────────────────────────────────────────────────────────────────────

def add_real_price(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add inflation-adjusted real price for long-range forecasting.
    Deflated by headline CPI (base = first observation).
    """
    if "headline_inflation_yoy" in df.columns:
        # Simple deflator: divide nominal price by (1 + inflation rate/100)
        df["price_real_ngn_mt"] = (
            df["price_ngn_mt"] / (1 + df["headline_inflation_yoy"] / 100)
        ).round(2)
    else:
        df["price_real_ngn_mt"] = df["price_ngn_mt"]

    return df


# ─────────────────────────────────────────────────────────────────────────────
# TIME INDEX FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def add_time_index(df: pd.DataFrame) -> pd.DataFrame:
    """Add numeric time index features for trend modelling."""
    df = df.sort_values("date").reset_index(drop=True)

    # Ordinal week number from the start of the series
    t0 = df["date"].min()
    df["weeks_since_start"] = ((df["date"] - t0).dt.days / 7).round(0).astype(int)

    # Year feature
    df["year"] = df["date"].dt.year

    # Quarter (1-4)
    df["quarter"] = df["date"].dt.quarter

    return df


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION CHECK
# ─────────────────────────────────────────────────────────────────────────────

def validate_features(df: pd.DataFrame, commodity: str) -> dict:
    """Run basic sanity checks on the feature matrix."""
    total = len(df)
    issues = {}

    critical_cols = ["price_ngn_mt", "fx_rate_usd_ngn", "fuel_cost_index"]
    for col in critical_cols:
        if col not in df.columns:
            issues[col] = "MISSING COLUMN"
            continue
        null_pct = df[col].isna().sum() / total * 100
        if null_pct > 10:
            issues[col] = f"{null_pct:.1f}% null"

    if issues:
        logger.warning(f"  [{commodity}] Feature validation issues: {issues}")
    else:
        logger.debug(f"  [{commodity}] All feature checks passed")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PER-COMMODITY BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_features_for_commodity(
    master: pd.DataFrame,
    commodity: str,
    ext: dict
) -> pd.DataFrame:
    """Build the complete feature matrix for a single commodity."""

    # Filter to this commodity, historical only
    # Include both historical and carry_forward rows.
    # carry_forward = old pipeline bridge estimates (March 17 → April 4).
    # They extend the training window to the most recent available data.
    df = master[
        (master["commodity"] == commodity) &
        (master["record_type"].isin(["historical", "carry_forward"]))
    ].copy()

    if len(df) < 5:
        logger.warning(f"  [{commodity}] Only {len(df)} rows — skipping")
        return pd.DataFrame()

    df = df.sort_values("date").reset_index(drop=True)

    # ── Join external features ────────────────────────────────────────────────
    df = merge_external(df, ext)
    df = fill_external_gaps(df)

    # ── Engineered features ───────────────────────────────────────────────────
    df = add_usd_price(df)
    df = add_lag_features(df)
    df = add_rolling_features(df)
    df = add_momentum_features(df)
    df = add_fuel_pressure(df)
    df = add_shock_flags(df)
    df = add_commodity_season_score(df, commodity)
    df = add_real_price(df)
    df = add_time_index(df)

    # ── Final sort ────────────────────────────────────────────────────────────
    df = df.sort_values("date").reset_index(drop=True)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# SAVE & SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def save_features(df: pd.DataFrame, commodity: str):
    """Save feature matrix to disk."""
    safe_name = commodity.lower().replace(" ", "_").replace("(", "").replace(")", "")
    path = os.path.join(FEATURES_DIR, f"features_{safe_name}.csv")
    df.to_csv(path, index=False)
    logger.debug(f"    Saved: {path}")
    return path


def print_feature_summary(df: pd.DataFrame, commodity: str):
    """Print a compact feature summary to the log."""
    hist = df[df["record_type"] == "historical"] if "record_type" in df.columns else df
    n = len(hist)
    n_features = len(df.columns) - 5  # subtract ID-type cols
    null_pct = hist.isnull().sum().sum() / (n * len(hist.columns)) * 100

    logger.info(
        f"  {commodity:<20} | "
        f"{n:>4} rows | "
        f"{n_features:>3} features | "
        f"null: {null_pct:.1f}% | "
        f"price: ₦{hist['price_ngn_mt'].iloc[-1]:>12,.0f}/MT | "
        f"last: {hist['date'].max().date()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_feature_engineering() -> dict:
    """
    Run feature engineering for all commodities.
    Returns dict of {commodity: DataFrame}.
    """
    logger.info("=" * 60)
    logger.info("STEP 3 — FEATURE ENGINEERING")
    logger.info("=" * 60)

    # Load
    logger.info("Loading datasets...")
    master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    ext = load_external_data()

    logger.info(f"\nBuilding feature matrices for {len(COMMODITIES)} commodities...")
    all_features = {}

    for commodity in COMMODITIES:
        logger.debug(f"  Processing: {commodity}")
        df = build_features_for_commodity(master, commodity, ext)

        if df.empty:
            continue

        issues = validate_features(df, commodity)
        save_features(df, commodity)
        print_feature_summary(df, commodity)
        all_features[commodity] = df

    # ── Combined feature overview ─────────────────────────────────────────────
    logger.info(f"\nFeature columns (example — Sorghum):")
    if "Sorghum" in all_features:
        cols = all_features["Sorghum"].columns.tolist()
        logger.info(f"  Total: {len(cols)} columns")
        feature_cols = [c for c in cols if c not in [
            "date","commodity","currency","unit","source","market_type",
            "region","data_source","record_type","notes","is_validated",
            "outlier_flag","outlier_reason"
        ]]
        logger.info(f"  Model features: {len(feature_cols)}")
        for i in range(0, len(feature_cols), 5):
            logger.debug(f"    {feature_cols[i:i+5]}")

    logger.info("=" * 60)
    logger.success(f"FEATURE ENGINEERING COMPLETE — {len(all_features)} commodities")
    logger.info("=" * 60)

    return all_features


if __name__ == "__main__":
    run_feature_engineering()
