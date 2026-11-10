"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Central Configuration File
All paths, commodities, model settings, and constants live here.
"""

import os

# ─── ROOT ────────────────────────────────────────────────────────────────────
# Locally, ROOT is the project folder (unchanged behavior).
# On Railway, set the AGROLINKING_DATA_ROOT environment variable to your
# Volume's mount path (e.g. "/data") so data/, outputs/, and models/ persist
# across deploys and restarts instead of living in the ephemeral container
# filesystem. Code (pipeline/, config/) still ships via git as normal —
# only the DATA root moves onto the Volume.
_CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get("AGROLINKING_DATA_ROOT", _CODE_ROOT)

# ─── PATHS ───────────────────────────────────────────────────────────────────
PATHS = {
    # Raw data (original, never overwritten)
    "raw_agricome":     os.path.join(ROOT, "data", "raw", "agricome_raw.csv"),
    "raw_wfp":          os.path.join(ROOT, "data", "raw", "wfp_food_prices_nga.csv"),
    # Web-sourced prices that passed 14_promote_web_prices.py's checks
    "raw_web_prices":   os.path.join(ROOT, "data", "raw", "web_prices.csv"),
    # Prices Agrolinking collects itself (trader quotes, field visits), typed in by hand
    "raw_field_prices": os.path.join(ROOT, "data", "raw", "field_prices.csv"),

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
    # The legacy "Rice" series (World Bank estimate-based) was retired on
    # 23 Oct 2026, once the website showed the two types below.
    "Rice (local)",    # Nigerian local milled rice   - WFP "Rice (local)"
    "Rice (foreign)",  # imported/foreign parboiled   - WFP "Rice (imported)"
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
    "Rice (imported)":  "Rice (foreign)",
    "Rice (local)":     "Rice (local)",
    # Livestock and protein
    "Meat (beef)":      "Meat (beef)",
    "Beef":             "Meat (beef)",
    "Meat (goat)":      "Meat (goat)",
    "Goat":             "Meat (goat)",
    "Fish":             "Fish (dried)",
    "Fish (dried)":     "Fish (dried)",
    "Eggs":             "Eggs",
}

# ─── PRICE SOURCES ───────────────────────────────────────────────────────────
# data_source values in the master that are REAL observed prices. Every step
# imports these instead of keeping its own list (they had drifted apart).
#   PARTNER_SOURCES - partner, exchange and market-monitor data
#   SCORED_SOURCES  - the above minus provisional months; what the forecast
#                     backtests and accuracy scores are measured on
#   REAL_SOURCES    - partner data plus vetted web prices (still real, observed)
# Everything else (Interpolated, Synthetic, WB_RTP_estimate,
# Agrolinking_validated) is an estimate.
# WFP_provisional: WFP's newest month while only a few of its ~20 markets have
# reported (01_ingest.py) - real observations, but a provisional national
# figure, replaced when WFP completes the month. Never scored against.
PROVISIONAL_SOURCES = frozenset({"WFP_provisional"})
PARTNER_SOURCES = frozenset({"Agricome", "WFP", "Agrolinking_primary", "AFEX", "FEWS_NET"}) | PROVISIONAL_SOURCES
SCORED_SOURCES  = PARTNER_SOURCES - PROVISIONAL_SOURCES
REAL_SOURCES    = PARTNER_SOURCES | {"Web_search"}
# Days after its date in the master that a real price reaches the pipeline.
# The accuracy tests (13_select_forecaster, scripts/research/forecast_audit.py)
# start a past forecast only from prices that had ARRIVED by then; assuming
# instant data put the 1-month figure ~3 points too high (Nov 2026: 92.5% vs
# 89.8%). Measured Nov 2026 - update when a feed changes (an automatic
# Agricome feed would make its delay ~1 day, and the tests would show it):
#   Agricome   pasted by hand a median 7 days (mean 12) after posting (git
#              history, Jul-Nov 2026); the weekly row is dated the Monday
#              before the post it carries
#   WFP        a month, dated the Monday of its 15th, is complete ~4 weeks
#              later (Oct 2026: 130 rows on 12 Oct, 508 by 9 Nov)
#   FEWS_NET   reaches FAO FPMA 6-7 weeks late (newest 26 Aug on 15 Oct,
#              30 Sep on 9 Nov)
#   AFEX       daily; a week's row is dated its Monday
#   Agrolinking_primary  field quotes, typed in within days
# Web prices are found the day they're published (no delay).
PUBLICATION_LAG_DAYS = {"Agricome": 14, "WFP": 28, "WFP_provisional": 0, "FEWS_NET": 45,
                        "AFEX": 5, "Agrolinking_primary": 7}
# Websites a dedicated daily reader already covers, so the web search neither
# uses them nor has its copies promoted or cross-checked. A web copy skipped
# the reader's checks: AFEX's maize (grade not stated) became White maize's
# "real" price, and AFEX's fresh ginger was compared with Agricome's dried
# ginger (Oct 2026). {site: the reader that covers it}
READ_DIRECTLY = {"mansamarkets.com": "pipeline/fetch_afex_prices.py (AFEX exchange prices)"}
# A commodity with no recent real price of its own is estimated from a sibling
# commodity's real price x their measured price ratio: {commodity: (sibling,
# ratio)}. Yellow maize trades ~2% above white maize (WFP wholesale, same market
# and month, 2014-2023: median 1.023, IQR 1.00-1.07, 1,320 market-months).
# Yellow maize has no real price since WFP's Jan 2023: AFEX's maize, used for it
# from 15 Oct 2026, is white maize (Mansa Markets confirmed, 3 Nov 2026).
# 02_clean.py writes these weekly as SIBLING_SOURCE rows - an estimate, labelled.
SIBLING_PRICES = {"Maize (yellow)": ("Maize (white)", 1.023)}
SIBLING_SOURCE = "Sibling_estimate"
SIBLING_AFTER_DAYS = 45   # sibling rows start this long after the commodity's own last real price
# Units a price in data/raw/field_prices.csv (Agrolinking's own quotes) may be
# in -> how many fit in a tonne. Crate of 30: the Eggs series is NGN/crate.
FIELD_UNITS = {"100kg bag": 10.0, "50kg bag": 20.0, "25kg bag": 40.0, "tonne": 1.0, "kg": 1000.0,
               "crate": 1.0}

# ─── FREIGHT ─────────────────────────────────────────────────────────────────
# Road haulage, NGN per kg per 100 km - an ESTIMATE, set in July 2026 without a
# cited source ("diesel haulage rates June 2026"). Outside figures put it lower:
# ~5 (Kaduna-Ibadan grain ~N35,000/t in 2025, one trader's analysis) to ~9
# (Lagos-Kano truck N1.6m for 30 t in 2023-24, scaled to 2026 fuel). Replace
# with transporters' quotes. Used by /arbitrage and the export view.
FREIGHT_NGN_PER_KG_PER_100KM = 12.0

# ─── EXPORT AND NEIGHBOURING-MARKET PRICES ───────────────────────────────────
# What buyers outside Nigeria pay (pipeline/fetch_export_prices.py ->
# data/raw/export_prices.csv; pipeline/20_export_prices.py -> the export view).
#
# EU: Eurostat Comext DS-045409 - the declared value and weight of the EU's
# imports FROM NIGERIA, by product, monthly, ~10 weeks late. A landed price at
# the EU border (cost, insurance and freight paid), not a price at Lagos port.
# Free to reuse with attribution. {our commodity: (CN8 code, product)}
EU_PRODUCTS = {
    "Sesame":      ("12074090", "Sesame seeds"),
    "Cocoa":       ("18010000", "Cocoa beans, whole or broken, raw"),
    "Cashew Nuts": ("08013200", "Cashew kernels (shelled)"),
    "Ginger":      ("09101100", "Ginger, dried, whole or split (not crushed or ground)"),
    "Soybeans":    ("12019000", "Soya beans"),
}
EU_MIN_TONNES = 10   # a month's EU price counts only on at least this much trade
# EU products that are not the product our Nigerian price is for: shown, never compared
EU_NOT_COMPARABLE = {
    "Cashew Nuts": "The EU buys Nigeria's cashew as shelled kernels; our price is for raw nuts in shell "
                   "(4-5 kg of raw nuts make 1 kg of kernels), so the two prices are not compared.",
}

# West Africa: neighbouring countries' market prices from FAO's price API (as
# fetch_fao_prices.py), reported by national statistics offices, FEWS NET and
# Afrique Verte, monthly. All CFA-franc countries: 655.957 CFA = 1 euro
# (fixed), so they convert to naira through the CBN euro rate.
# Ghana (cedi; its series end in mid-2026) is not included yet.
WA_COUNTRIES = {"NER": ("Niger", "XOF"), "CMR": ("Cameroon", "XAF"), "BEN": ("Benin", "XOF"),
                "TGO": ("Togo", "XOF"), "TCD": ("Chad", "XAF")}
CFA_PER_EUR = 655.957
# {FAO commodity name: our commodity}
WA_PRODUCTS = {
    "Maize (white)": "Maize (white)", "Maize": "Maize (white)", "Maize (imported)": "Maize (white)",
    "Sorghum": "Sorghum", "Sorghum (white)": "Sorghum",
    "Cowpeas (white)": "Beans (white)", "Niebe": "Beans (white)", "Niebe (cowpea)": "Beans (white)",
    "Cowpeas (red)": "Beans (red)",
    "Rice (imported)": "Rice (foreign)", "Rice (25% broken, imported)": "Rice (foreign)",
}
# Markets close enough to the 12 zone states to compare with: name -> (country, lat, lon)
WA_MARKETS = {
    "Maradi": ("Niger", 13.50, 7.10), "Zinder": ("Niger", 13.81, 8.99), "Niamey": ("Niger", 13.51, 2.11),
    "Diffa": ("Niger", 13.32, 12.61),
    "Kousseri": ("Cameroon", 12.08, 15.03), "Maroua": ("Cameroon", 10.59, 14.32),
    "Garoua": ("Cameroon", 9.30, 13.40),
    "N'Djamena": ("Chad", 12.13, 15.06),
    "Cotonou": ("Benin", 6.37, 2.39), "Parakou": ("Benin", 9.34, 2.63),
    "Lomé": ("Togo", 6.13, 1.22), "DAGL (Lomé) Region": ("Togo", 6.13, 1.22),
}

# Nigeria's Export (Prohibition) Act 1989 (Cap E22, LFN 2004), Schedule:
# beans, cassava tuber, maize, rice, yam tuber, their products, and all
# imported food. Nigeria Customs' 2026 export prohibition list names maize.
# These show the price abroad for information, never an export cost from a state.
EXPORT_PROHIBITED = {
    "Maize (white)": "maize", "Maize (yellow)": "maize",
    "Beans (white)": "beans", "Beans (red)": "beans",
    "Rice (local)": "rice", "Rice (foreign)": "rice (and all imported food)",
    "Wheat": "all imported food",
}
EXPORT_LAW = "Export (Prohibition) Act 1989, Schedule (Cap E22, Laws of the Federation 2004)"

# Where each zone state's goods are bought, and the export ports: (place, lat,
# lon). Road distance = straight line x ROAD_FACTOR - an estimate; it gives
# Kano-Lagos ~1,090 km (road ~1,050), Maiduguri-Kousseri ~270 (road ~250).
STATE_MARKETS = {
    "Kano": ("Kano (Dawanau)", 12.07, 8.45), "Kaduna": ("Kaduna", 10.52, 7.44),
    "Plateau": ("Jos", 9.90, 8.86), "Kogi": ("Lokoja", 7.80, 6.74),
    "Adamawa": ("Yola", 9.21, 12.48), "Borno": ("Maiduguri", 11.83, 13.15),
    "Oyo": ("Ibadan (Bodija)", 7.42, 3.92), "Lagos": ("Lagos (Mile 12)", 6.61, 3.40),
    "Anambra": ("Onitsha", 6.15, 6.79), "Imo": ("Owerri", 5.48, 7.03),
    "Rivers": ("Port Harcourt", 4.82, 7.03), "Delta": ("Asaba", 6.20, 6.73),
}
EXPORT_PORTS = {"Lagos ports (Apapa)": (6.44, 3.37), "Onne port": (4.71, 7.15)}
ROAD_FACTOR = 1.3

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