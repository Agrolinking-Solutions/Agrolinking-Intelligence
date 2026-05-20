"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 4: Model Training
Trains ARIMA + Prophet + XGBoost per commodity.
Run: python pipeline/04_train.py
"""

import os, sys, json, warnings
import pandas as pd
import numpy as np
import joblib
from loguru import logger

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")
logger.add(os.path.join(PATHS["logs_dir"], "training_{time:YYYY-MM-DD}.log"),
    rotation="1 day", retention="30 days", level="DEBUG")

FEATURES_DIR = os.path.join(os.path.dirname(PATHS["master"]), "features")
MODELS_DIR   = PATHS["models_dir"]
RESULTS_FILE = os.path.join(PATHS["logs_dir"], "model_results.json")

XGB_EXCLUDE = [
    "date","commodity","currency","unit","source","market_type","region",
    "data_source","record_type","notes","is_validated","outlier_flag",
    "outlier_reason","price_raw_ngn_mt","price_ngn_mt","log_price",
    "price_usd_mt","price_real_ngn_mt","fx_rate","rainfall_index",
]

PROPHET_REGRESSORS = [
    "fx_rate_usd_ngn","fuel_cost_index",
    "food_inflation_yoy","shock_active","commodity_season_score",
]

def safe_name(c):
    return c.lower().replace(" ","_").replace("(","").replace(")","")

def get_model_path(mtype, commodity):
    d = os.path.join(MODELS_DIR, mtype)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{safe_name(commodity)}.pkl")

def safe_mape(a, p):
    a, p = np.array(a, dtype=float), np.array(p, dtype=float)
    mask = (a != 0) & ~np.isnan(a) & ~np.isnan(p)
    return float(np.mean(np.abs((a[mask]-p[mask])/a[mask]))*100) if mask.sum() > 0 else np.nan

def get_metrics(actual, predicted):
    a = np.array(actual, dtype=float)
    p = np.array(predicted, dtype=float)
    mask = ~(np.isnan(a)|np.isnan(p))
    a, p = a[mask], p[mask]
    if len(a) == 0:
        return {"mae":None,"rmse":None,"mape":None}
    return {
        "mae":  round(float(np.mean(np.abs(a-p))), 2),
        "rmse": round(float(np.sqrt(np.mean((a-p)**2))), 2),
        "mape": round(safe_mape(a, p), 2),
    }

def ts_split(df, test_pct=0.20):
    n   = len(df)
    cut = max(int(n*(1-test_pct)), n-26)
    cut = min(cut, n-4)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()

def load_features(commodity):
    path = os.path.join(FEATURES_DIR, f"features_{safe_name(commodity)}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Feature file not found: {path}")
    return pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)


# ── ARIMA ─────────────────────────────────────────────────────────────────────
def train_arima(df, commodity):
    try:
        import pmdarima as pm
    except ImportError:
        logger.warning(f"  pmdarima not installed")
        return {}

    # Cap at last 156 rows (3 years) — prevents memory overflow on large series
    MAX_ROWS = 156
    df_a = df.tail(MAX_ROWS).copy() if len(df) > MAX_ROWS else df.copy()
    train, test = ts_split(df_a)
    y_train = train["price_ngn_mt"].values.astype(float)
    y_test  = test["price_ngn_mt"].values.astype(float)
    n = len(y_train)

    try:
        seasonal = n >= 104
        model = pm.auto_arima(
            y_train,
            seasonal=seasonal, m=52 if seasonal else 1,
            stepwise=True, suppress_warnings=True, error_action="ignore",
            max_p=2, max_q=2, max_P=1, max_Q=1, max_d=2, max_D=1,
            information_criterion="aic", n_jobs=1,
        )
        preds = np.clip(model.predict(n_periods=len(y_test)), 0, None)
        scores = get_metrics(y_test, preds)
        joblib.dump({"model":model,"order":str(model.order),"n_train":n},
                    get_model_path("arima", commodity))
        logger.debug(f"  [{commodity}] ARIMA order={model.order} MAPE={scores['mape']:.1f}%")
        return {"model":model,"metrics":scores,"order":str(model.order)}
    except Exception as e:
        logger.warning(f"  [{commodity}] ARIMA failed: {e}")
        return {}


# ── PROPHET ───────────────────────────────────────────────────────────────────
def train_prophet(df, commodity):
    try:
        from prophet import Prophet
    except ImportError:
        logger.warning(f"  prophet not installed")
        return {}

    avail = [r for r in PROPHET_REGRESSORS
             if r in df.columns and df[r].notna().sum() > len(df)*0.5]

    def prep(d):
        out = d[["date","price_ngn_mt"]].rename(
            columns={"date":"ds","price_ngn_mt":"y"}).copy()
        out["ds"] = pd.to_datetime(out["ds"])
        for r in avail:
            out[r] = d[r].ffill().bfill().values
        return out

    train, test = ts_split(df)
    train_p, test_p = prep(train), prep(test)

    try:
        model = Prophet(
            changepoint_prior_scale=0.05,
            seasonality_prior_scale=10.0,
            seasonality_mode="multiplicative",
            yearly_seasonality=len(train)>=52,
            weekly_seasonality=False,
            daily_seasonality=False,
            interval_width=0.95,
        )
        for r in avail:
            model.add_regressor(r, standardize=True)
        if len(train) >= 52:
            model.add_seasonality(name="quarterly", period=91.25, fourier_order=5)
        model.fit(train_p)
        future = test_p[["ds"]+avail].copy()
        fc     = model.predict(future)
        preds  = np.clip(fc["yhat"].values, 0, None)
        scores = get_metrics(test_p["y"].values, preds)
        joblib.dump({"model":model,"regressors":avail},
                    get_model_path("prophet", commodity))
        logger.debug(f"  [{commodity}] Prophet MAPE={scores['mape']:.1f}%")
        return {"model":model,"regressors":avail,"metrics":scores}
    except Exception as e:
        logger.warning(f"  [{commodity}] Prophet failed: {e}")
        return {}


# ── XGBOOST ───────────────────────────────────────────────────────────────────
def train_xgboost(df, commodity):
    try:
        from xgboost import XGBRegressor
        from sklearn.preprocessing import RobustScaler
    except ImportError:
        logger.warning(f"  xgboost not installed")
        return {}

    numeric_types = ["float64","float32","int64","int32","bool","int8","uint8"]
    feature_cols = [
        c for c in df.columns
        if c not in XGB_EXCLUDE
        and str(df[c].dtype) in numeric_types
        and df[c].notna().sum() > len(df)*0.3
    ]
    if len(feature_cols) < 3:
        logger.warning(f"  [{commodity}] XGBoost: not enough features")
        return {}

    X = df[feature_cols].copy()
    for col in X.columns:
        X[col] = X[col].fillna(X[col].median())
    y = df["price_ngn_mt"].values.astype(float)

    cut = int(len(df)*0.80)
    X_train, X_test = X.iloc[:cut], X.iloc[cut:]
    y_train, y_test = y[:cut], y[cut:]

    try:
        scaler = RobustScaler()
        Xtr = scaler.fit_transform(X_train)
        Xte = scaler.transform(X_test)

        model = XGBRegressor(
            n_estimators=500, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
            reg_alpha=0.1, reg_lambda=1.0, random_state=42,
            n_jobs=-1, verbosity=0,
            early_stopping_rounds=30, eval_metric="mae",
        )
        model.fit(Xtr, y_train, eval_set=[(Xte, y_test)], verbose=False)
        preds  = np.clip(model.predict(Xte), 0, None)
        scores = get_metrics(y_test, preds)
        imps   = pd.Series(model.feature_importances_,
                           index=feature_cols).sort_values(ascending=False)
        joblib.dump({"model":model,"scaler":scaler,
                     "features":feature_cols,"importances":imps.head(10).to_dict()},
                    get_model_path("xgboost", commodity))
        logger.debug(
            f"  [{commodity}] XGBoost MAPE={scores['mape']:.1f}% | "
            f"top: {imps.index[0]}")
        return {"model":model,"scaler":scaler,
                "features":feature_cols,"metrics":scores,
                "importances":imps.head(10).to_dict()}
    except Exception as e:
        logger.warning(f"  [{commodity}] XGBoost failed: {e}")
        return {}


# ── ENSEMBLE WEIGHTS ──────────────────────────────────────────────────────────
def compute_weights(results):
    mapes = {
        name: res["metrics"]["mape"]
        for name, res in results.items()
        if res and "metrics" in res
        and res["metrics"].get("mape") is not None
        and res["metrics"]["mape"] < 60.0
    }
    if not mapes:
        valid = [k for k, v in results.items() if v]
        return {m: round(1/len(valid),4) for m in valid} if valid else {}
    inv   = {m: 1.0/(v+1e-6) for m,v in mapes.items()}
    total = sum(inv.values())
    return {m: round(v/total,4) for m,v in inv.items()}


# ── PER-COMMODITY ─────────────────────────────────────────────────────────────
# Commodities that perform better trained ONLY on real Agricome/WFP data
# (adding synthetic history hurts because the price regimes are too different)
REAL_DATA_ONLY = {"Cocoa", "Cashew Nuts", "Hibiscus", "Ginger"}

def train_commodity(commodity):
    df = load_features(commodity)

    # For commodities with structural breaks in price history,
    # train only on real data (last 2 years of Agricome/WFP).
    if commodity in REAL_DATA_ONLY:
        import pandas as pd
        real_sources = {"Agricome", "WFP", "Agrolinking_primary"}
        if "data_source" in df.columns:
            real_df = df[df["data_source"].isin(real_sources)]
            if len(real_df) >= 20:
                df = real_df.copy().reset_index(drop=True)
                logger.debug(f"  [{commodity}] Real-data-only mode: {len(df)} rows")

    n  = len(df)
    logger.info(f"\n{'─'*55}")
    logger.info(f"  {commodity}  ({n} rows | last: {df['date'].max().date()})")
    logger.info(f"{'─'*55}")

    results = {}
    if n >= 20:
        logger.info("  [1/3] ARIMA...")
        results["arima"]   = train_arima(df, commodity)
    if n >= 10:
        logger.info("  [2/3] Prophet...")
        results["prophet"] = train_prophet(df, commodity)
    if n >= 20:
        logger.info("  [3/3] XGBoost...")
        results["xgboost"] = train_xgboost(df, commodity)

    weights = compute_weights(results)
    joblib.dump(weights, get_model_path("ensemble", commodity))

    logger.info(f"  Ensemble weights: " +
                " | ".join(f"{k}: {v:.3f}" for k,v in weights.items()))
    logger.info(f"  {'Model':<10} {'MAPE':>8}  {'MAE':>16}  {'Weight':>8}")
    logger.info(f"  {'-'*50}")
    for name, res in results.items():
        if res and "metrics" in res:
            m = res["metrics"]
            mstr = f"{m['mape']:.1f}%" if m["mape"] is not None else "  n/a"
            maestr = f"₦{m['mae']:>12,.0f}" if m["mae"] is not None else "  n/a"
            w = weights.get(name, 0)
            logger.info(f"  {name:<10} {mstr:>8}  {maestr}  {w:>8.3f}")
        else:
            logger.info(f"  {name:<10}   FAILED")

    summary = {"commodity":commodity,"n_rows":n,
               "last_date":str(df["date"].max().date()),"weights":weights}
    for name, res in results.items():
        if res and "metrics" in res:
            summary[name] = res["metrics"]
    return summary


# ── MAIN ──────────────────────────────────────────────────────────────────────
def run_training():
    logger.info("="*60)
    logger.info("STEP 4 — MODEL TRAINING")
    logger.info(f"  Commodities : {len(COMMODITIES)}")
    logger.info(f"  Models      : ARIMA + Prophet + XGBoost")
    logger.info("="*60)

    all_results = {}
    for i, commodity in enumerate(COMMODITIES, 1):
        logger.info(f"\n[{i}/{len(COMMODITIES)}] Starting {commodity}...")
        try:
            all_results[commodity] = train_commodity(commodity)
        except Exception as e:
            logger.error(f"  [{commodity}] Unexpected error: {e}")
            all_results[commodity] = {"error": str(e)}

    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info("\n"+"="*70)
    logger.info("  FINAL TRAINING SUMMARY")
    logger.info("="*70)
    logger.info(f"  {'Commodity':<22} {'ARIMA':>8} {'Prophet':>9} {'XGBoost':>9} {'Best':>10}")
    logger.info(f"  {'-'*62}")
    for commodity in COMMODITIES:
        res = all_results.get(commodity, {})
        if "error" in res:
            logger.warning(f"  {commodity:<22} ERROR")
            continue
        def ms(k):
            v = res.get(k)
            return f"{v['mape']:.1f}%" if isinstance(v,dict) and v.get("mape") else "  skip"
        a,p,x = ms("arima"), ms("prophet"), ms("xgboost")
        cands = {k: res[k]["mape"] for k in ["arima","prophet","xgboost"]
                 if isinstance(res.get(k),dict) and res[k].get("mape")}
        best = min(cands, key=cands.get) if cands else "n/a"
        logger.info(f"  {commodity:<22} {a:>8} {p:>9} {x:>9} {best:>10}")

    logger.info("="*70)
    ok = sum(1 for r in all_results.values() if "error" not in r)
    logger.success(f"TRAINING COMPLETE — {ok}/{len(COMMODITIES)} commodities")
    logger.info("="*70)
    return all_results

if __name__ == "__main__":
    run_training()
