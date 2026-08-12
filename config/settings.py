"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Central Configuration File
All paths, commodities, model settings, and constants live here.
"""

import os

# ─── ROOT ────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─── PATHS ───────────────────────────────────────────────────────────────────
PATHS = {
    # Raw data (original, never overwritten)
    "raw_agricome":     os.path.join(ROOT, "data", "raw", "agricome_raw.csv"),
    "raw_wfp":          os.path.join(ROOT, "data", "raw", "wfp_food_prices_nga.csv"),

    # Processed / living master dataset (the recycling file)
    "master":           os.path.join(ROOT, "data", "processed", "agrolinking_master.csv"),

    # External feature data (FX, inflation, fuel, seasons)
    "fx_rates":         os.path.join(ROOT, "data", "external", "fx_rates.csv"),
    "inflation":        os.path.join(ROOT, "data", "external", "inflation.csv"),
    "fuel_prices":      os.path.join(ROOT, "data", "external", "fuel_prices.csv"),
    "season_calendar":  os.path.join(ROOT, "data", "external", "season_calendar.csv"),

    # Live cross-reference cache (refreshed each run)
    "live_cache":       os.path.join(ROOT, "data", "live", "cross_ref_cache.json"),

    # Outputs
    "forecasts_dir":    os.path.join(ROOT, "outputs", "forecasts"),
    "logs_dir":         os.path.join(ROOT, "outputs", "logs"),
    "reports_dir":      os.path.join(ROOT, "outputs", "reports"),
    "daily_alerts_dir": os.path.join(ROOT, "outputs", "daily_alerts"),

    # Saved model artifacts
    "models_dir":       os.path.join(ROOT, "models"),
}

# ─── COMMODITIES ─────────────────────────────────────────────────────────────
COMMODITIES = [
    "Hibiscus",
    "Sesame",
    "Ginger",
    "Cocoa",
    "Soybeans",
    "Cashew Nuts",
    "Sorghum",
    "Beans (white)",
    "Beans (red)",
    "Maize (white)",
    "Maize (yellow)",
    "Wheat",
    "Rice",
    "Meat (beef)",
    "Meat (goat)",
    "Fish (dried)",
    "Eggs",
]

# WFP name mapping → our standard names
WFP_NAME_MAP = {
    # Grains and legumes
    "Beans (white)":    "Beans (white)",
    "Beans (red)":      "Beans (red)",
    "Beans (niebe)":    "Beans (white)",   # treat niebe as white beans
    "Maize":            "Maize (white)",
    "Maize (white)":    "Maize (white)",
    "Maize (yellow)":   "Maize (yellow)",
    "Sorghum":          "Sorghum",
    "Sorghum (white)":  "Sorghum",
    "Soybeans":         "Soybeans",
    "Wheat":            "Wheat",
    "Rice":             "Rice",
    # Livestock and protein
    "Meat (beef)":      "Meat (beef)",
    "Beef":             "Meat (beef)",
    "Meat (goat)":      "Meat (goat)",
    "Goat":             "Meat (goat)",
    "Fish":             "Fish (dried)",
    "Fish (dried)":     "Fish (dried)",
    "Eggs":             "Eggs",
}

# ─── FORECAST HORIZONS ───────────────────────────────────────────────────────
FORECAST_HORIZONS = {
    "daily":    1,
    "weekly":   7,
    "2_weeks":  14,
    "monthly":  30,
    "3_months": 90,
    "6_months": 180,
}

# ─── MODEL SETTINGS ──────────────────────────────────────────────────────────
MODEL_CONFIG = {
    "arima": {
        "order":         (1, 1, 1),
        "seasonal_order":(1, 1, 1, 52),  # 52 weeks in a year
        "enforce_stationarity": False,
        "enforce_invertibility": False,
    },
    "prophet": {
        "changepoint_prior_scale":  0.05,
        "seasonality_prior_scale":  10.0,
        "seasonality_mode":         "multiplicative",
        "yearly_seasonality":       True,
        "weekly_seasonality":       True,
        "daily_seasonality":        False,
    },
    "xgboost": {
        "n_estimators":   500,
        "max_depth":      6,
        "learning_rate":  0.05,
        "subsample":      0.8,
        "colsample_bytree": 0.8,
        "random_state":   42,
        "n_jobs":         -1,
    },
    "lstm": {
        "lookback":       52,   # weeks of history to look back
        "units":          64,
        "dropout":        0.2,
        "epochs":         100,
        "batch_size":     16,
        "patience":       15,   # early stopping
    },
    "ensemble": {
        # Weights assigned to each model in ensemble
        # Adjusted dynamically based on cross-reference accuracy
        "default_weights": {
            "arima":   0.20,
            "prophet": 0.30,
            "xgboost": 0.35,
            "lstm":    0.15,
        },
        "error_threshold_pct": 5.0,  # max allowed % error vs. live sources
    }
}

# ─── EXTERNAL FEATURES ───────────────────────────────────────────────────────
EXTERNAL_FEATURES = [
    "fx_rate_usd_ngn",     # NGN/USD exchange rate
    "fuel_price_diesel",   # Diesel price (NGN/litre)
    "fuel_price_petrol",   # Petrol price (NGN/litre)
    "inflation_cpi",       # Consumer Price Index (Nigeria)
    "food_inflation",      # Food sub-index CPI
    "season_planting",     # Binary: 1 if planting season
    "season_harvesting",   # Binary: 1 if harvesting season
    "season_dry",          # Binary: 1 if dry season
    "season_rainy",        # Binary: 1 if rainy season
    "rainfall_index",      # Rainfall index (0–1, from WFP/FAO)
]

# ─── NIGERIAN SEASON CALENDAR ─────────────────────────────────────────────────
# Month numbers (1=Jan, 12=Dec)
SEASON_CALENDAR = {
    "dry_season":       [11, 12, 1, 2, 3],
    "rainy_season":     [4, 5, 6, 7, 8, 9, 10],
    "planting_season":  [4, 5, 6],      # Main planting: April–June
    "harvesting_season":[9, 10, 11],    # Main harvest: Sept–Nov
}

# ─── LIVE CROSS-REFERENCE SOURCES ────────────────────────────────────────────
# These are queried live every forecast run
LIVE_SOURCES = [
    {
        "name":  "FAO GIEWS",
        "url":   "https://www.fao.org/giews/food-prices/price-warnings/en/",
        "type":  "web",
    },
    {
        "name":  "World Bank Commodity Prices",
        "url":   "https://www.worldbank.org/en/research/commodity-markets",
        "type":  "web",
    },
    {
        "name":  "CBN Inflation Data",
        "url":   "https://www.cbn.gov.ng/rates/inflationrates.asp",
        "type":  "web",
    },
]

# ─── PRICE UNITS ─────────────────────────────────────────────────────────────
STANDARD_UNIT    = "NGN/MT"
STANDARD_CURRENCY = "NGN"

# ─── BRANDING ────────────────────────────────────────────────────────────────
BRAND = {
    "name":    "Agrolinking",
    "dark":    "#053307",
    "mid":     "#007f07",
    "yellow":  "#FFCE35",
    "white":   "#FFFFFF",
    "accent":  "#22C55E",
}
