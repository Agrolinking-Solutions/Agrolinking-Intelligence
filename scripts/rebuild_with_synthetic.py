"""
scripts/rebuild_with_synthetic.py
One-time (and safe-to-repeat) master rebuild script.
Merges ALL data sources in correct priority order:
  Agricome screenshots > agricome_raw > WFP > synthetic historical > Maize bridge
Run: python scripts/rebuild_with_synthetic.py
"""
import os, sys, pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES
from loguru import logger

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW  = os.path.join(BASE, "data", "raw")
EXT  = os.path.join(BASE, "data", "external")

WFP_MAP   = {
    "Beans (white)":"Beans (white)", "Beans (red)":"Beans (red)",
    "Beans (niebe)":"Beans (white)", "Maize":"Maize (white)",
    "Maize (white)":"Maize (white)", "Maize (yellow)":"Maize (yellow)",
    "Sorghum":"Sorghum", "Sorghum (white)":"Sorghum",
}
WFP_UNITS = {"KG":1000,"100 KG":10,"50 KG":20,"2.5 KG":400,"2.7 KG":370.37}

COLS = [
    "commodity","date","price_ngn_mt","currency","unit","source",
    "market_type","region","fx_rate","rainfall_index",
    "data_quality_score","is_validated","notes","data_source",
    "record_type","outlier_flag","outlier_reason","price_raw_ngn_mt",
]

def align(df):
    for c in COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[COLS].copy()


def run():
    logger.info("Rebuilding master with all data sources...")
    parts = []

    # ── 1. Real Agricome scraped (agricome_raw.csv) ──────────────────────────
    ag_path = os.path.join(RAW, "agricome_raw.csv")
    if os.path.exists(ag_path):
        ag = pd.read_csv(ag_path, parse_dates=["week_start_date"])
        real = ag[ag["source"].isin(["Agricom","WFP Nigeria Food Prices"])].copy()
        real.rename(columns={"week_start_date":"date","price":"price_ngn_mt"}, inplace=True)
        real["record_type"]        = "historical"
        real["data_source"]        = "Agricome"
        real["price_raw_ngn_mt"]   = real["price_ngn_mt"]
        real["outlier_flag"]       = False
        real["outlier_reason"]     = ""
        real["data_quality_score"] = 0.95
        real["is_validated"]       = True
        real["currency"]           = "NGN"
        real["unit"]               = "NGN/MT"
        real["market_type"]        = "wholesale"
        real["region"]             = "National"
        real["notes"]              = "Scraped from @agricomeafrica"
        real["fx_rate"]            = np.nan
        real["rainfall_index"]     = np.nan
        parts.append(align(real))
        logger.info(f"  Agricome raw:         {len(real):>5} rows")

    # ── 2. New Agricome screenshots (Feb-Apr 2026) ───────────────────────────
    sc_path = os.path.join(RAW, "new_agricome_screenshots.csv")
    if os.path.exists(sc_path):
        sc = pd.read_csv(sc_path, parse_dates=["date"])
        parts.append(align(sc))
        logger.info(f"  Agricome screenshots: {len(sc):>5} rows | {sc['date'].min().date()} → {sc['date'].max().date()}")
    else:
        logger.warning("  new_agricome_screenshots.csv not found — place in data/raw/")

    # ── 3. WFP Nigeria (grains) ──────────────────────────────────────────────
    wfp_path = os.path.join(RAW, "wfp_food_prices_nga.csv")
    if os.path.exists(wfp_path):
        wfp = pd.read_csv(wfp_path, parse_dates=["date"])
        wfp_s = wfp[wfp["commodity"].isin(WFP_MAP)].copy()
        wfp_s = wfp_s[wfp_s["pricetype"]=="Wholesale"]
        wfp_s["commodity"]    = wfp_s["commodity"].map(WFP_MAP)
        wfp_s["price_ngn_mt"] = wfp_s.apply(
            lambda r: r["price"] * WFP_UNITS.get(r["unit"], np.nan), axis=1)
        wfp_s = wfp_s.dropna(subset=["price_ngn_mt"])
        wfp_agg = wfp_s.groupby(["date","commodity"])["price_ngn_mt"].mean().reset_index()
        wfp_agg["source"]             = "WFP Nigeria Food Prices"
        wfp_agg["data_source"]        = "WFP"
        wfp_agg["record_type"]        = "historical"
        wfp_agg["data_quality_score"] = 0.9
        wfp_agg["is_validated"]       = True
        wfp_agg["currency"]           = "NGN"
        wfp_agg["unit"]               = "NGN/MT"
        wfp_agg["market_type"]        = "wholesale"
        wfp_agg["region"]             = "National"
        wfp_agg["notes"]              = "WFP VAM"
        wfp_agg["fx_rate"]            = np.nan
        wfp_agg["rainfall_index"]     = np.nan
        wfp_agg["price_raw_ngn_mt"]   = wfp_agg["price_ngn_mt"]
        wfp_agg["outlier_flag"]       = False
        wfp_agg["outlier_reason"]     = ""
        parts.append(align(wfp_agg))
        logger.info(f"  WFP:                  {len(wfp_agg):>5} rows")

    # ── 4. Synthetic historical (World Bank / FAO / NEPC) ────────────────────
    synth_path = os.path.join(EXT, "synthetic_historical.csv")
    if os.path.exists(synth_path):
        synth = pd.read_csv(synth_path, parse_dates=["date"])
        parts.append(align(synth))
        logger.info(f"  Synthetic historical: {len(synth):>5} rows")
    else:
        logger.warning("  synthetic_historical.csv not found — place in data/external/")

    # ── 5. Wheat primary data (Agrolinking) ─────────────────────────────────
    wheat_path = os.path.join(RAW, "wheat_agrolinking.csv")
    if os.path.exists(wheat_path):
        wh = pd.read_csv(wheat_path, parse_dates=["date"])
        parts.append(align(wh))
        logger.info(f"  Wheat primary:        {len(wh):>5} rows | {wh['date'].min().date()} → {wh['date'].max().date()}")
    else:
        logger.warning("  wheat_agrolinking.csv not found — place in data/raw/")

    # ── 6. Maize 2023-2026 bridge (inflation-adjusted) ──────────────────────
    # Build on-the-fly if not in master already
    logger.info("  Building Maize 2023-2026 bridge...")
    bridge_rows = []
    for commodity, start_p, end_p in [
        ("Maize (white)",  211_000, 370_000),
        ("Maize (yellow)", 227_000, 400_000),
    ]:
        dates  = pd.date_range("2023-01-23", "2026-04-06", freq="W-MON")
        n      = len(dates)
        prices = np.linspace(start_p, end_p, n)
        np.random.seed(42)
        prices = np.clip(prices * (1 + np.random.normal(0, 0.02, n)),
                         start_p * 0.95, end_p * 1.05)
        for d, p in zip(dates, prices):
            bridge_rows.append({
                "commodity": commodity, "date": d,
                "price_ngn_mt": round(p, 2),
                "currency": "NGN", "unit": "NGN/MT",
                "source": "Estimated (inflation-adjusted bridge)",
                "market_type": "wholesale", "region": "National",
                "fx_rate": np.nan, "rainfall_index": np.nan,
                "data_quality_score": 0.60, "is_validated": False,
                "notes": "Bridge: interpolated WFP 2023 → current market",
                "data_source": "Interpolated",
                "record_type": "historical",
                "outlier_flag": False, "outlier_reason": "",
                "price_raw_ngn_mt": round(p, 2),
            })
    bridge_df = pd.DataFrame(bridge_rows)
    parts.append(align(bridge_df))
    logger.info(f"  Maize bridge:         {len(bridge_df):>5} rows")

    # ── Merge with strict priority dedup ────────────────────────────────────
    combined = pd.concat(parts, ignore_index=True)
    combined = combined[combined["commodity"].isin(COMMODITIES)].copy()
    combined["date"] = pd.to_datetime(combined["date"])
    combined["date"] = combined["date"] - pd.to_timedelta(
        combined["date"].dt.dayofweek, unit="D")

    src_rank = {
        "Agricome": 0, "Agrolinking_primary": 1, "WFP": 2,
        "Synthetic (World Bank/FAO × FX)": 3, "Interpolated": 4, "Agrolinking_old": 5,
    }
    combined["_p"] = combined["data_source"].map(src_rank).fillna(9)
    combined = combined.sort_values(["commodity","date","_p"])
    combined = combined.drop_duplicates(subset=["commodity","date"], keep="first")
    combined = combined.drop(columns=["_p"])
    combined = combined.sort_values(["commodity","date"]).reset_index(drop=True)
    combined.to_csv(PATHS["master"], index=False)

    logger.success(f"Master rebuilt: {len(combined):,} rows")
    logger.info("")
    for c in COMMODITIES:
        sub  = combined[combined["commodity"]==c]
        hist = sub[sub["record_type"]=="historical"]
        real = hist[hist["data_source"].isin(["Agricome","Agrolinking_primary","WFP"])]
        synth_rows = hist[hist["data_source"]=="Synthetic (World Bank/FAO × FX)"]
        logger.info(
            f"  {c:<20} hist:{len(hist):>5} | "
            f"real:{len(real):>4} | synthetic:{len(synth_rows):>4} | "
            f"last real: {real['date'].max().date() if len(real)>0 else 'n/a'}"
        )


if __name__ == "__main__":
    run()