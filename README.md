# 🌱 Agrolinking Commodity Intelligence System

Nigeria's most accurate agricultural commodity price intelligence and forecasting platform.

---

## Overview

This system ingests historical price data from Agricome and WFP Nigeria, trains an ensemble of ARIMA, Prophet, XGBoost, and LSTM models, cross-references forecasts against live web sources, and publishes results to a Streamlit dashboard — updated daily.

---

## Commodities Covered

| # | Commodity       |
|---|-----------------|
| 1 | Hibiscus        |
| 2 | Sesame          |
| 3 | Ginger          |
| 4 | Cocoa           |
| 5 | Soybeans        |
| 6 | Cashew Nuts     |
| 7 | Sorghum         |
| 8 | Beans (white)   |
| 9 | Beans (red)     |
|10 | Maize (white)   |
|11 | Maize (yellow)  |

---

## Project Structure

```
agrolinking-intel/
│
├── config/
│   └── settings.py          # All paths, constants, model configs
│
├── data/
│   ├── raw/                 # Original source files — NEVER overwritten
│   │   ├── agricome_raw.csv
│   │   └── wfp_food_prices_nga.csv
│   ├── processed/
│   │   └── agrolinking_master.csv   # The living master dataset
│   ├── external/            # FX rates, inflation, fuel, season calendar
│   └── live/                # Live cross-reference cache (refreshed daily)
│
├── models/
│   ├── arima/               # Saved ARIMA model artifacts per commodity
│   ├── prophet/             # Saved Prophet model artifacts
│   ├── xgboost/             # Saved XGBoost model artifacts
│   ├── lstm/                # Saved LSTM model weights
│   └── ensemble/            # Ensemble weights and meta-model
│
├── pipeline/
│   ├── 01_ingest.py         # Load + merge raw sources → master
│   ├── 02_clean.py          # Standardize, fill gaps, remove outliers
│   ├── 03_features.py       # Engineer all external features
│   ├── 04_train.py          # Train all 4 models per commodity
│   ├── 05_forecast.py       # Generate all forecast horizons
│   ├── 06_validate.py       # Live cross-reference validation
│   ├── 07_ensemble.py       # Pick best model, build ensemble output
│   ├── 08_alerts.py         # Daily % change report generator
│   └── run_pipeline.py      # Master runner — runs all steps in order
│
├── dashboard/
│   └── app.py               # Streamlit dashboard
│
├── outputs/
│   ├── forecasts/           # JSON + CSV forecast files per run
│   ├── logs/                # Pipeline run logs
│   ├── reports/             # Monthly analytical reports
│   └── daily_alerts/        # Daily price change alert files
│
├── scripts/
│   └── setup_env.py         # First-time setup helper
│
├── assets/                  # Logos, images for dashboard
├── notebooks/               # Exploratory analysis notebooks
├── requirements.txt
└── README.md
```

---

## Setup (First Time)

### 1. Install Python 3.10+
Download from https://www.python.org/downloads/

### 2. Create a virtual environment
```bash
python -m venv venv

# Activate it:
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Copy your raw data files into `data/raw/`
- `agricome_raw.csv` (your Agricome scraped data)
- `wfp_food_prices_nga.csv` (WFP Nigeria dataset)

### 5. Run the pipeline
```bash
python pipeline/run_pipeline.py
```

### 6. Launch the dashboard
```bash
streamlit run dashboard/app.py
```

---

## How the Data Recycling Works

```
[Raw Data] ──► [Clean + Feature Engineering] ──► [Train 4 Models]
                                                        │
                                              [Generate Forecasts]
                                                        │
                                        [Live Cross-Reference Validation]
                                                        │
                                          [Pick Best / Ensemble Result]
                                                 │              │
                                    [Forecast Log JSON]   [Append to Master CSV]
                                                                │
                                              [Master CSV ◄── next run trains on this]
```

The first run creates `agrolinking_master.csv` as a copy of the raw data + first forecasts.
Every run after that: the master file is used for training, and new forecasts are appended to it.
**No second duplicate is ever created.**

---

## Forecast Output Format

Each forecast run produces:
- `outputs/forecasts/forecast_YYYY-MM-DD.json` — structured per commodity
- Rows appended to `data/processed/agrolinking_master.csv`

---

## Daily Alert Format

```
📊 AGROLINKING DAILY COMMODITY ALERT — [DATE]

Cashew Nuts:   ₦350,000/MT  ▲ +16.7% vs yesterday
Cocoa:         ₦6,100,000/MT  ▼ -1.6% vs yesterday
Hibiscus:      ₦2,700,000/MT  → 0.0% unchanged
...
```

---

## Target Accuracy

| Metric                  | Target     |
|-------------------------|------------|
| Forecast error vs live  | < 5%       |
| Model accuracy          | ≥ 98%      |
| Cross-reference sources | 3+ per run |

---

Built by the Agrolinking Research & Data Team.
