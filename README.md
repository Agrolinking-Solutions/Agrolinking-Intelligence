# Agrolinking Commodity Intelligence Platform

> Nigeria's most accurate agricultural commodity price intelligence system. Built by and for Agrolinking Solutions.

---

## Overview

The Agrolinking Commodity Intelligence Platform is a production-grade forecasting and price intelligence system that tracks 12 Nigerian agricultural commodities across 6 geopolitical zones and 12 states. It combines ARIMA, Prophet, and XGBoost ensemble models with daily cross-reference validation against verified market sources to deliver actionable price intelligence to farmers, processors, and investors.

The system runs as a fully automated 7-step pipeline that ingests new data, trains models, generates forecasts, validates accuracy against live market prices, and produces both a Streamlit dashboard and WhatsApp-ready broadcast alerts.

---

## What It Does

- Forecasts prices for 12 commodities at 6 horizons: daily, weekly, 2 weeks, 1 month, 3 months, and 6 months
- Validates every forecast against Agricome Africa, WFP Nigeria, and live market sources, targeting under 5% error
- Applies structural price differentials across 12 states to generate state-level sourcing intelligence
- Identifies the cheapest sourcing location nationally for each commodity with spread analysis
- Produces daily broadcast alerts formatted for WhatsApp and email distribution
- Serves a live Streamlit dashboard with light and dark mode, zonal charts, and forecast trajectory graphs

---

## Commodities Tracked

| Commodity | Primary Source | Data Points |
|---|---|---|
| Hibiscus | Agricome Africa | 45+ weekly posts |
| Sesame | Agricome Africa | 828+ weekly posts |
| Ginger | Agricome Africa | 44+ weekly posts |
| Cocoa | Agricome Africa | 48+ weekly posts |
| Soybeans | Agricome Africa | 1,372+ weekly posts |
| Cashew Nuts | Agricome Africa | 48+ weekly posts |
| Sorghum | WFP Nigeria | 1,266+ market readings |
| Beans (white) | WFP Nigeria | 1,262+ market readings |
| Beans (red) | WFP Nigeria | 531+ market readings |
| Maize (white) | WFP Nigeria + Agrolinking | 1,265+ market readings |
| Maize (yellow) | WFP Nigeria + Agrolinking | 621+ market readings |
| Wheat | Agrolinking primary | 850+ weekly posts |

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
Step 4: Train        ARIMA + Prophet + XGBoost ensemble with automatic weight assignment
Step 5: Forecast     Generate 6-horizon price trajectories for all 12 commodities
Step 6: Validate     Cross-reference against verified market prices, apply corrections
Step 7: Zonal        Apply state-level price factors and generate subnational intelligence
```

### Model Performance (May 2026 training run)

| Commodity | Best Model | MAPE |
|---|---|---|
| Maize (white) | ARIMA | 1.0% |
| Maize (yellow) | ARIMA | 1.0% |
| Wheat | ARIMA | 2.4% |
| Cashew Nuts | XGBoost | 3.7% |
| Ginger | XGBoost | 2.3% |
| Beans (white) | XGBoost | 4.6% |
| Sesame | ARIMA | 13.1% |
| Soybeans | ARIMA | 17.4% |

### Validation Results (May 2026)

- 8 out of 12 commodities within 5% of live market prices post-correction
- Average error before validation: 11.3%
- Average error after validation: 4.5%

---

## Project Structure

```
agrolinking-intel/
    .streamlit/
        config.toml              Streamlit theme configuration
    config/
        settings.py              Commodity list, file paths, model parameters
    dashboard/
        app.py                   Streamlit dashboard (5 pages, light/dark mode)
    data/
        external/
            state_price_differentials.csv    144 rows: zone, state, commodity, factor
            zones_config.json                6 zones, 12 states, descriptions
            verified_prices_2026.json        Cross-referenced market reference prices
            fx_rates.csv                     USD/NGN exchange rates
            fuel_prices.csv                  Petrol prices (transport cost proxy)
            inflation.csv                    CPI series
            season_calendar.csv              Harvest and lean season calendar
    outputs/
        forecasts/
            validated/           forecast_validated_YYYY-MM-DD.json
            zonal/               zonal_forecast_YYYY-MM-DD.json
        daily_alerts/            alert_validated and alert_zonal .txt files
        logs/                    Per-step logs, model results, validation reports
    pipeline/
        01_ingest.py             Data ingestion and source validation
        02_clean.py              Master dataset cleaning and gap-filling
        03_features.py           Feature engineering
        04_train.py              ARIMA + Prophet + XGBoost ensemble training
        05_forecast.py           Multi-horizon forecast generation
        06_validate.py           Cross-reference validation and correction
        07_zonal_forecast.py     State-level price interpolation and drift
        run_pipeline.py          Full and skip-train pipeline runner
    requirements.txt
    fix_validate_prices.py       Utility to update MANUAL_PRICES reference values
```

---

## Setup

### Requirements

- Python 3.11.9 (Python 3.14 is not compatible with Prophet and some ARIMA dependencies)
- Windows 10/11 or Ubuntu 20.04+
- 4GB RAM minimum, 8GB recommended for full training run

### Installation

```powershell
# Clone the repository
git clone https://github.com/Agrolinking-Solutions/Agrolinking-Intelligence.git
cd Agrolinking-Intelligence

# Create and activate virtual environment
python -m venv venv
venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### Requirements File

The `requirements.txt` should contain:

```
streamlit>=1.28.0
pandas
numpy
plotly
prophet
pmdarima
xgboost
scikit-learn
loguru
requests
beautifulsoup4
```

---

## Running the Pipeline

### Daily Run (skip retraining, use existing models)

```powershell
python pipeline\run_pipeline.py --skip-train
```

### Full Weekly Run (retrain all models, takes 10 to 15 minutes)

```powershell
python pipeline\run_pipeline.py
```

### Run Individual Steps

```powershell
python pipeline\01_ingest.py
python pipeline\02_clean.py
python pipeline\03_features.py
python pipeline\04_train.py
python pipeline\05_forecast.py
python pipeline\06_validate.py
python pipeline\07_zonal_forecast.py
```

### Run for a Specific Date

```powershell
python pipeline\07_zonal_forecast.py --date 2026-05-21
```

---

## Dashboard

### Run Locally

```powershell
streamlit run dashboard\app.py
```

Opens at `http://localhost:8501`

Your colleagues on the same WiFi network can access it at your machine's network URL (shown in the terminal on startup).

### Dashboard Pages

| Page | Description |
|---|---|
| Dashboard | Live commodity price cards with daily change pills and validation status |
| Commodities | Deep dive with forecast trajectory chart and weekly breakdown table |
| Forecasts | Full 12-commodity summary table across any selected horizon |
| Zonal Prices | Zone overview, state detail with spider chart, best-buy market, production advantage |
| Alerts | National and zonal WhatsApp-ready broadcast text, ready to copy |

### Light and Dark Mode

Click the **Dark** or **Light** button in the navigation bar to toggle between themes. The toggle is the rightmost nav button.

---

## Operational Workflow

### Critical Rule

The Agricome Africa Instagram feed (`@agricomeafrica`) is the ground-truth data source for 6 commodities. Every time a new weekly post is published (typically Mondays and Thursdays), update `MANUAL_PRICES` in `pipeline/06_validate.py` immediately before running the pipeline.

```python
MANUAL_PRICES = {
    "Hibiscus":      2_325_000,   # Update from latest Agricome post
    "Sesame":        1_245_000,
    "Ginger":        9_700_000,
    "Cocoa":         5_650_000,
    "Soybeans":        745_000,
    "Cashew Nuts":   1_950_000,
    "Sorghum":         335_000,   # WFP Nigeria
    "Beans (white)":   813_000,
    "Beans (red)":     915_000,
    "Maize (white)":   370_000,   # Market research
    "Maize (yellow)":  400_000,
    "Wheat":           706_833,   # Agrolinking primary
}
```

Or run the patch utility:

```powershell
python fix_validate_prices.py
```

### Recommended Weekly Schedule

| Day | Action |
|---|---|
| Monday | Check Agricome post, update MANUAL_PRICES, run full pipeline with retrain |
| Wednesday | Check Agricome post, update MANUAL_PRICES if new post, run --skip-train |
| Thursday | Check Agricome post, update MANUAL_PRICES if new post, run --skip-train |
| Daily | Run --skip-train for fresh interpolated zonal prices |

### Push Updates to GitHub

After each pipeline run, push the new outputs so the cloud deployment stays current:

```powershell
git add outputs\forecasts\validated\
git add outputs\forecasts\zonal\
git add outputs\daily_alerts\
git commit -m "Update forecasts $(Get-Date -Format 'yyyy-MM-dd')"
git push
```

---

## How the Zonal Price Drift Works

The zonal forecast does not repeat the same prices every day. Each day it interpolates the national price from the model forecast curve, using `last_known_date` as day zero.

For example, if the last Agricome data was on April 13 and today is May 21 (38 days elapsed), the system interpolates between the monthly horizon (28 days) and the 3-month horizon (91 days) to produce a price that is unique to that day. Tomorrow it will interpolate at 39 days, producing a slightly different value.

This means prices drift naturally along the forecast trajectory between pipeline retrains. The drift direction and magnitude reflect the model's prediction, not a manual rule.

### State Price Factors

Each state has a structural price differential based on WFP subnational surveys, NAERLS crop reports, and AFEX market data. A factor below 1.0 means that state produces the commodity and prices are cheaper than the national benchmark.

Key sourcing advantages (May 2026):

| Commodity | Best State | Factor | Saving vs Lagos |
|---|---|---|---|
| Ginger | Kaduna | 0.72x | 67% cheaper |
| Maize (white) | Kano | 0.80x | 62% cheaper |
| Sorghum | Kano | 0.78x | 54% cheaper |
| Soybeans | Plateau | 0.83x | 47% cheaper |
| Cashew Nuts | Kogi | 0.80x | 44% cheaper |

---

## Deployment on Streamlit Cloud

1. Go to https://share.streamlit.io and sign in with the Agrolinking-Solutions GitHub account
2. Click New app
3. Set repository to `Agrolinking-Solutions/Agrolinking-Intelligence`
4. Set branch to `main`
5. Set main file path to `dashboard/app.py`
6. Click Deploy

The app will be available at a URL like `https://agrolinking-intelligence.streamlit.app` within a few minutes. It auto-redeploys within 30 seconds of every push to the `main` branch.

---

## Known Limitations

- Cocoa validation error is 6.3% (above 5% target) because the model was trained on synthetic historical data before real post-2025 market prices were available. This improves with each weekly retrain as more real data accumulates.
- Cashew Nuts has 7.1% validation error for the same reason.
- Prophet is consistently outperformed by ARIMA and XGBoost on this dataset. It receives low ensemble weight automatically but is retained for seasonal decomposition signals.
- The pipeline requires an active internet connection to pull exchange rates and fuel price updates.

---

## Data Sources

| Source | Commodities | Frequency | Quality Score |
|---|---|---|---|
| Agricome Africa (@agricomeafrica) | Hibiscus, Sesame, Ginger, Cocoa, Soybeans, Cashew Nuts, Wheat | Weekly | 1.0 (ground truth) |
| WFP Nigeria Food Price Monitor | Sorghum, Beans (white), Beans (red), Maize | Monthly | 0.9 |
| Agrolinking primary collection | Wheat, Maize, Beans | Weekly | 0.95 |
| World Bank commodity index | All (anchor validation) | Monthly | 0.8 |

---

## Built With

- Python 3.11.9
- Streamlit 1.28+
- Prophet (Facebook/Meta)
- pmdarima (auto-ARIMA)
- XGBoost
- Plotly
- pandas, numpy, scikit-learn, loguru

---

## Organisation

Agrolinking Solutions Nigeria

Contact: info@agrolinking.com

Website: https://agrolinking.com

---

*Redefining the Future of Agricultural Connection in Africa*