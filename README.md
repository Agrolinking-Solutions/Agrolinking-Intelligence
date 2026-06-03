# Agrolinking Commodity Intelligence Platform

> Nigeria's most accurate agricultural commodity price intelligence system. Built by and for Agrolinking Solutions.

---

## Overview

The Agrolinking Commodity Intelligence Platform is a production-grade forecasting and price intelligence system that tracks 13 Nigerian agricultural commodities across 6 geopolitical zones and 12 states. It combines ARIMA, Prophet, Holt-Winters, XGBoost, and LightGBM ensemble models with daily cross-reference validation against verified market sources to deliver actionable price intelligence to farmers, processors, and investors.

The system runs as a fully automated 7-step pipeline that ingests new data, trains models, generates forecasts, validates accuracy against live market prices, and produces a Streamlit dashboard, a REST API, and WhatsApp-ready broadcast alerts.

**Live Dashboard:** https://agrolinking-intelligence-f8qq4uhupaax2qny8rpcpx.streamlit.app

**Live API:** https://agrolinking-intelligence-api.onrender.com

**API Docs:** https://agrolinking-intelligence-api.onrender.com/docs

---

## What It Does

- Forecasts prices for 13 commodities at 6 horizons: daily, weekly, 2 weeks, 1 month, 3 months, and 6 months
- Validates every forecast against Agricome Africa, WFP Nigeria, NGX, and live market sources, targeting under 3% error
- Currently achieving 13/13 commodities within 3% target at 1.5% average error post-correction
- Applies structural price differentials across 12 states to generate state-level sourcing intelligence
- Identifies the cheapest sourcing location nationally for each commodity with spread analysis
- Produces daily broadcast alerts formatted for WhatsApp and email distribution
- Serves a live Streamlit dashboard with light and dark mode, zonal charts, and forecast trajectory graphs
- Exposes a FastAPI REST API for frontend integration into the Agrolinking website

---

## Commodities Tracked

| Commodity | Primary Source | Data Points |
|---|---|---|
| Hibiscus | Agricome Africa | 853+ weekly posts |
| Sesame | Agricome Africa | 831+ weekly posts |
| Ginger | Agricome Africa | 1,375+ weekly posts |
| Cocoa | Agricome Africa | 1,375+ weekly posts |
| Soybeans | Agricome Africa | 1,375+ weekly posts |
| Cashew Nuts | Agricome Africa | 853+ weekly posts |
| Sorghum | WFP Nigeria | 1,269+ market readings |
| Beans (white) | WFP Nigeria | 1,265+ market readings |
| Beans (red) | WFP Nigeria | 535+ market readings |
| Maize (white) | WFP Nigeria + Agrolinking | 1,268+ market readings |
| Maize (yellow) | WFP Nigeria + Agrolinking | 625+ market readings |
| Wheat | Agrolinking primary | 853+ weekly posts |
| Rice | WFP Nigeria + Bridge | 1,271+ market readings |

---

## Zones and States

| Zone | States | Character |
|---|---|---|
| North West | Kano, Kaduna | Nigeria's main grain belt. Maize, Sorghum, Sesame, Wheat |
| North Central | Plateau, Kogi | Middle Belt. Ginger heartland and Cashew/Cocoa corridor |
| North East | Adamawa, Borno | Semi-arid. Sorghum and Maize. Conflict premium in Borno |
| South West | Oyo, Lagos | Commercial capital. Lagos sets consumer market prices |
| South East | Anambra, Imo | High consumption, import-dependent |
| South South | Rivers, Delta | Oil belt. High purchasing power. Cocoa producer (Delta) |

---

## Pipeline Architecture

The system runs as a sequential 7-step pipeline:

```
Step 1: Ingest       Scrape and validate new data from all sources
Step 2: Clean        Standardise, deduplicate, and fill gaps in master CSV
Step 3: Features     Engineer lag features, rolling stats, and seasonal signals
Step 4: Train        ARIMA + Prophet + Holt-Winters + XGBoost + LightGBM ensemble
Step 5: Forecast     Generate 6-horizon price trajectories for all 13 commodities
Step 6: Validate     Cross-reference against verified market prices, apply corrections
Step 7: Zonal        Apply state-level price factors and generate subnational intelligence
```

### Model Ensemble

Each commodity is trained on 5 models. Weights are assigned inversely proportional to each model's holdout MAPE so the best-performing model dominates the ensemble but all 5 contribute.

| Model | Strength | Typical Weight |
|---|---|---|
| ARIMA | Stationary price series, short-run momentum | 0.20-0.35 |
| Prophet | Seasonal decomposition, trend changepoints | 0.18-0.43 |
| Holt-Winters | Food price cycles, harvest/lean seasonality | 0.17-0.84 |
| XGBoost | Non-linear lag relationships, market shocks | 0.10-0.84 |
| LightGBM | Fast gradient boosting on smaller datasets | 0.05-0.25 |

### Validation Results (June 2026)

- 13 out of 13 commodities within 3% of live market prices post-correction
- Average error before validation: 9.8%
- Average error after validation: 1.5%

### Key Sourcing Intelligence (June 2026)

| Commodity | Best State | Saving vs Lagos |
|---|---|---|
| Ginger | Kaduna | 67% cheaper |
| Maize (white) | Kano | 62% cheaper |
| Sorghum | Kano | 54% cheaper |
| Soybeans | Plateau | 47% cheaper |
| Beans (white) | Kano | 47% cheaper |
| Rice | Plateau | 31% cheaper |

---

## Project Structure

```
agrolinking-intel/
    .streamlit/
        config.toml                   Streamlit theme configuration
    config/
        settings.py                   Commodity list, file paths, model parameters
    dashboard/
        app.py                        Streamlit dashboard (5 pages, light/dark mode)
    data/
        external/
            state_price_differentials.csv    156 rows: zone, state, commodity, factor
            zones_config.json                6 zones, 12 states, descriptions
            verified_prices_2026.json        Cross-referenced market reference prices
            fx_rates.csv                     USD/NGN exchange rates
            fuel_prices.csv                  Petrol prices (transport cost proxy)
            inflation.csv                    CPI series
            season_calendar.csv              Harvest and lean season calendar
        processed/
            agrolinking_master.csv           13,748+ rows across 13 commodities
            features/                        Per-commodity feature matrices
        raw/
            agricome_raw.csv                 Agricome Africa Instagram data
            wfp_food_prices_nga.csv          WFP Nigeria price monitor
            rice_historical.csv              WFP + bridge data for Rice
            wheat_agrolinking.csv            Agrolinking primary wheat data
    outputs/
        forecasts/
            validated/                       forecast_validated_YYYY-MM-DD.json
            zonal/                           zonal_forecast_YYYY-MM-DD.json
        daily_alerts/                        alert_validated and alert_zonal .txt files
        logs/                                Per-step logs, model results, validation reports
    pipeline/
        01_ingest.py                         Data ingestion and source validation
        02_clean.py                          Master dataset cleaning and gap-filling
        03_features.py                       Feature engineering (79 features per commodity)
        04_train.py                          5-model ensemble training
        05_forecast.py                       Multi-horizon forecast generation
        06_validate.py                       Cross-reference validation and correction
        07_zonal_forecast.py                 State-level price interpolation and drift
        run_pipeline.py                      Full and skip-train pipeline runner
    api.py                                   FastAPI REST API (8 endpoints)
    API_DOCUMENTATION.md                     API reference for the dev team
    requirements.txt
```

---

## Setup

### Requirements

- Python 3.11.9 (Python 3.14 is not compatible with Prophet and some ARIMA dependencies)
- Windows 10/11 or Ubuntu 20.04+
- 4GB RAM minimum, 8GB recommended for full training run

### Installation

```powershell
git clone https://github.com/Agrolinking-Solutions/Agrolinking-Intelligence.git
cd Agrolinking-Intelligence

python -m venv venv
venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

---

## Running the Pipeline

### Daily Run (skip retraining, uses existing models, runs in under 2 minutes)

```powershell
python pipeline\run_pipeline.py --skip-train
```

### Full Weekly Run (retrains all 5 models per commodity, takes 15-30 minutes)

```powershell
python pipeline\run_pipeline.py
```

### Run Individual Steps

```powershell
python pipeline\03_features.py
python pipeline\04_train.py
python pipeline\05_forecast.py
python pipeline\06_validate.py
python pipeline\07_zonal_forecast.py
```

---

## Dashboard

### Run Locally

```powershell
streamlit run dashboard\app.py
```

Opens at `http://localhost:8501`

### Dashboard Pages

| Page | Description |
|---|---|
| Dashboard | Live commodity price cards with daily change pills and validation status |
| Commodities | Deep dive with forecast trajectory chart and weekly breakdown table |
| Forecasts | Full 13-commodity summary table across any selected horizon |
| Zonal Prices | Zone overview, state detail with spider chart, best-buy market, production advantage |
| Alerts | National and zonal WhatsApp-ready broadcast text, ready to copy |

### Light and Dark Mode

Click the **Dark** or **Light** button in the navigation bar to toggle between themes.

---

## REST API

The platform exposes a FastAPI REST API that the Agrolinking development team uses to build the website commodity intelligence section.

### Run Locally

```powershell
python api.py
```

Opens at `http://localhost:8000`
Interactive docs at `http://localhost:8000/docs`

### Live API

```
https://agrolinking-intelligence-api.onrender.com
https://agrolinking-intelligence-api.onrender.com/docs
```

### Key Endpoints

| Endpoint | Description |
|---|---|
| GET /summary | Homepage hero widget data |
| GET /commodities | All 13 live prices with daily change and validation status |
| GET /forecasts/latest | Full forecast all commodities, optional horizon filter |
| GET /forecasts/{commodity} | Single commodity full 6-horizon forecast |
| GET /forecasts/{commodity}/{horizon} | Chart-ready weekly series with confidence bands |
| GET /zonal/latest | All zonal and state prices |
| GET /zonal/{commodity} | State-level prices with best sourcing intelligence |
| GET /alerts/latest | Latest WhatsApp-ready broadcast alert |

See `API_DOCUMENTATION.md` for full request/response schemas and frontend integration examples.

---

## Operational Workflow

### Critical Rule

The Agricome Africa Instagram feed (`@agricomeafrica`) is the ground-truth source for 7 commodities. Every time a new weekly post is published (typically Mondays and Thursdays), update `MANUAL_PRICES` in `pipeline/06_validate.py` before running the pipeline.

```python
MANUAL_PRICES = {
    "Hibiscus":      2_325_000,
    "Sesame":        1_650_000,
    "Ginger":       12_000_000,
    "Cocoa":         5_650_000,
    "Soybeans":        745_000,
    "Cashew Nuts":   1_950_000,
    "Sorghum":         420_000,
    "Beans (white)":   813_000,
    "Beans (red)":     915_000,
    "Maize (white)":   370_000,
    "Maize (yellow)":  400_000,
    "Wheat":           706_833,
    "Rice":          1_550_000,
}
```

### Recommended Weekly Schedule

| Day | Action |
|---|---|
| Monday | Check Agricome post, update MANUAL_PRICES, run full pipeline with retrain |
| Wednesday | Check Agricome post, update MANUAL_PRICES if new post, run --skip-train |
| Thursday | Check Agricome post, update MANUAL_PRICES if new post, run --skip-train |
| Daily | Run --skip-train for fresh interpolated zonal prices |

### Push Updates to GitHub

After each pipeline run, push outputs so the Streamlit dashboard and API both stay current:

```powershell
git add outputs\forecasts\validated\
git add outputs\forecasts\zonal\
git add outputs\daily_alerts\
git commit -m "Update forecasts $(Get-Date -Format 'yyyy-MM-dd')"
git push
```

Streamlit Cloud auto-redeploys in 30 seconds. Render auto-redeploys in 2 minutes.

---

## How the Zonal Price Drift Works

The zonal forecast interpolates the national price along the model forecast curve daily, using the last known date as day zero. Each day produces a unique price slightly different from the previous day. The drift direction and magnitude reflect the model's prediction between retrains.

---

## Deployment

### Streamlit Dashboard

1. Go to https://share.streamlit.io
2. Connect `Agrolinking-Solutions/Agrolinking-Intelligence`
3. Set main file: `dashboard/app.py`
4. Deploy

### REST API on Render

1. Go to https://render.com
2. New Web Service, connect same GitHub repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn api:app --host 0.0.0.0 --port $PORT`
5. Plan: Free
6. Deploy

---

## Data Sources

| Source | Commodities | Frequency | Quality Score |
|---|---|---|---|
| Agricome Africa (@agricomeafrica) | Hibiscus, Sesame, Ginger, Cocoa, Soybeans, Cashew Nuts, Wheat | Weekly | 1.0 |
| WFP Nigeria Food Price Monitor | Sorghum, Beans, Maize, Rice | Monthly | 0.9 |
| Agrolinking primary collection | Wheat, Maize, Beans | Weekly | 0.95 |
| NGX / LCFE exchange data | Ginger, Sesame (validation) | Weekly | 0.95 |
| World Bank commodity index | All (anchor validation) | Monthly | 0.8 |

---

## Built With

- Python 3.11.9
- FastAPI + Uvicorn (REST API)
- Streamlit (dashboard)
- Prophet, pmdarima, statsmodels, XGBoost, LightGBM
- Plotly, pandas, numpy, scikit-learn, loguru

---

## Organisation

Agrolinking Solutions Nigeria

Contact: info@agrolinking.com

Website: https://agrolinking.com

---

*Redefining the Future of Agricultural Connection in Africa*