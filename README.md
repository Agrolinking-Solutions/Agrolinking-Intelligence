# Agrolinking Commodity Intelligence Platform

> Nigeria's most accurate agricultural commodity price intelligence system. Built by and for Agrolinking Solutions.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-Proprietary-red)
![Status](https://img.shields.io/badge/Status-Production-green)

---

## 🎯 Overview

The Agrolinking Commodity Intelligence Platform is a production-grade forecasting and price intelligence system that tracks **17 Nigerian agricultural commodities** across **6 geopolitical zones and 12 states**. It combines ensemble machine learning (ARIMA, Prophet, XGBoost, LightGBM) with daily cross-reference validation against live market sources.

**Live Services:**
- 📊 **Dashboard:** [Streamlit](https://agrolinking-intelligence-f8qq4uhupaax2qny8rpcpx.streamlit.app)
- 📡 **API:** [Render](https://agrolinking-intelligence.onrender.com/docs)
- 📱 **Alerts:** Daily WhatsApp-ready broadcast text

---

## 📋 What It Does

✅ **Forecasts 6 horizons** — daily, weekly, 2 weeks, 1 month, 3 months, 6 months  
✅ **17 commodities tracked** — 13 agricultural + 4 livestock/protein  
✅ **Automated daily pipeline** — GitHub Actions runs ingest → intelligence every day at 05:00 UTC
✅ **Sub-3% accuracy** — Cross-validated against live market prices  
✅ **Zonal intelligence** — State-level sourcing and price signals
✅ **Deterministic daily noise** — Reproducible, realistic price variation
✅ **Quality-gated publishing** — Bad data (NaN, conflict markers) is blocked before it reaches the API

### Commodities Tracked

**Agricultural (13):** Hibiscus, Sesame, Ginger, Cocoa, Soybeans, Cashew Nuts, Sorghum, Beans (white), Beans (red), Maize (white), Maize (yellow), Wheat, Rice

**Livestock/Protein (4):** Meat (beef), Meat (goat), Fish (dried), Eggs

---

## 📊 Quick Facts

| Metric | Value |
|---|---|
| Accuracy (post-correction) | 1.7% average error |
| Data points | 18,000+ weekly observations |
| Historical depth | 2016–present (10+ years) |
| Geographic coverage | 6 zones, 12 states, 30+ markets |
| Model ensemble | 5 models per commodity (ARIMA, Prophet, XGBoost, LightGBM, Holt-Winters) |
| Update frequency | Daily (skip-train) / Weekly (retrain) |
| API endpoints | 20+ endpoints, <1s response time |

---

## 🏗️ Project Structure

```
agrolinking-intel/
├── .github/
│   └── workflows/
│       ├── daily_pipeline.yml        # GitHub Actions runner (daily at 05:00 UTC)
│       └── keep_alive.yml            # Uptime monitoring ping
├── config/
│   ├── __init__.py
│   └── settings.py                   # Commodities, paths, model parameters (source of truth)
├── dashboard/
│   └── app.py                        # Streamlit 5-page interactive dashboard
├── data/
│   ├── external/                     # FX, inflation, fuel, seasonality, zones
│   ├── processed/
│   │   ├── agrolinking_master.csv    # Master dataset (living record, 10+ years)
│   │   └── features/                 # Per-commodity 79-feature matrices
│   └── raw/                          # Agricome, WFP, Rice, Wheat CSV files
├── docs/
│   ├── ARCHITECTURE.md               # System design, model ensemble, data flow
│   ├── SECURITY.md                   # Security posture, risk mitigation, hardening plan
│   ├── FIXES_AND_IMPROVEMENTS.md     # What was fixed in Sept 2026
│   ├── OPERATIONAL_GUIDE.md          # Running the pipeline, troubleshooting, deployment
│   └── API.md                        # REST API reference and examples
├── models/                           # Trained ensemble models (ARIMA, Prophet, XGBoost, .pkl) — gitignored, local only
├── notebooks/                        # Exploratory analysis — gitignored, not part of the pipeline
├── outputs/
│   ├── forecasts/
│   │   ├── validated/                # Daily validated forecasts (JSON)
│   │   └── zonal/                    # State-level forecasts (JSON)
│   ├── daily_alerts/                 # WhatsApp-ready alert text (TXT)
│   ├── intelligence/                 # Market indices, trade signals (JSON)
│   └── logs/                         # Per-step logs and validation reports
├── pipeline/
│   ├── 01_ingest.py                  # Load and validate all data sources
│   ├── 02_clean.py                   # Clean, standardize, deduplicate
│   ├── 03_features.py                # Feature engineering (79 features per commodity)
│   ├── 04_train.py                   # Train 5-model ensemble per commodity
│   ├── 05_forecast.py                # Generate 6-horizon forecasts + daily noise
│   ├── 06_validate.py                # Cross-reference validation + correction
│   ├── 07_zonal_forecast.py          # State-level price interpolation
│   ├── 08_intelligence.py            # Synthesize indices, movers, trade signals
│   ├── 09_staleness_check.py         # Detect stale data > 45 days
│   ├── quality_gate.py               # CRITICAL: Reject bad outputs (NaN, conflict markers)
│   ├── run_pipeline.py               # Full and skip-train pipeline runner
│   ├── scheduler.py                  # (Windows Task Scheduler alternative)
│   ├── refresh_wfp_hdx.py            # Download WFP Nigeria data from HDX
│   └── diagnose_stale_anchor.py      # Debug helpers
├── scripts/
│   ├── maintenance/                  # Old one-off fix/patch scripts (archived)
│   └── migration/                    # Postgres migration tools
├── tests/                            # Unit tests (placeholder)
├── api.py                            # FastAPI REST endpoint (20 endpoints)
├── push_daily.ps1                    # Safe local push script (now with quality gate)
├── requirements.txt                  # Core pipeline dependencies
├── requirements_api.txt              # API-only (no ML libs needed)
├── railway.toml                      # Railway deployment config (legacy)
├── .gitignore                        # Git ignore rules
└── README.md                         # This file
```

---

## 🚀 Quick Start

### Installation

```bash
git clone https://github.com/Agrolinking-Solutions/Agrolinking-Intelligence.git
cd Agrolinking-Intelligence

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

**Requirements:** Python 3.10+, 4GB RAM (8GB for training), Windows/Linux/Mac

### Run the Pipeline

```bash
# Daily run (uses existing trained models, ~2 minutes)
python pipeline/run_pipeline.py --skip-train

# Full retrain (trains all 5 models per commodity, ~20 minutes)
python pipeline/run_pipeline.py

# Run individual steps
python pipeline/01_ingest.py
python pipeline/04_train.py
python pipeline/05_forecast.py
```

### Run the Dashboard

```bash
streamlit run dashboard/app.py
# Opens http://localhost:8501
```

### Run the API

```bash
python api.py
# Opens http://localhost:8000
# API docs at http://localhost:8000/docs
```

### Check Quality

```bash
python pipeline/quality_gate.py
# QUALITY GATE PASSED - 17 commodities, outputs valid
```

---

## 📡 REST API

### Live Endpoints

| Endpoint | Description | Example |
|---|---|---|
| `GET /` | Health check + endpoint directory | `curl https://api.../` |
| `GET /summary` | Dashboard hero data | `curl https://api.../summary` |
| `GET /commodities` | All 17 live prices + daily change | `curl https://api.../commodities` |
| `GET /forecasts/latest` | Full 6-horizon forecast all commodities | `curl https://api.../forecasts/latest` |
| `GET /forecasts/{commodity}` | Single commodity full forecast | `curl https://api.../forecasts/Rice` |
| `GET /zonal/latest` | All state-level prices | `curl https://api.../zonal/latest` |
| `GET /prices/kg` | All prices in NGN/kg | `curl https://api.../prices/kg` |
| `GET /index/food` | Food Price Index (2025=100) | `curl https://api.../index/food` |
| `GET /movers` | Biggest riser/faller today | `curl https://api.../movers` |
| `GET /confidence` | Model confidence scores per commodity | `curl https://api.../confidence` |
| `GET /history/{commodity}` | Historical 90-day price series | `curl https://api.../history/Rice?days=180` |
| `GET /history/compare` | Compare multiple commodities | `curl https://api.../history/compare?commodities=Rice,Wheat` |
| `GET /alerts/saved` | List saved price alerts | `curl https://api.../alerts/saved` |
| `POST /alerts/saved` | Create price threshold alert | `curl -X POST https://api.../alerts/saved?commodity=Rice&threshold_price=1600000&direction=above` |

**Live API:** https://agrolinking-intelligence.onrender.com/docs

---

## 🔧 Operational Workflow

### Before Each Run: Update Reference Prices

Every Monday/Thursday, Agricome Africa publishes a new post on Instagram. Update `MANUAL_PRICES` in `pipeline/06_validate.py`:

```python
# pipeline/06_validate.py
MANUAL_PRICES = {
    "Hibiscus":      2_325_000,       # Update from Agricom Monday post
    "Sesame":        1_650_000,
    "Ginger":       12_000_000,
    # ... etc
}
```

### Recommended Schedule

| Day | Action | Command |
|---|---|---|
| **Monday** | Check Agricome post, update MANUAL_PRICES, retrain | `python pipeline/run_pipeline.py` |
| **Wednesday/Thursday** | Update MANUAL_PRICES if new post, skip retrain | `python pipeline/run_pipeline.py --skip-train` |
| **Daily** | Fresh forecasts (GitHub Actions runs automatically at 05:00 UTC) | Manual run: `python pipeline/run_pipeline.py --skip-train` |

### Push to Production

After a local run, update GitHub:

```bash
git add data/processed/agrolinking_master.csv data/processed/features/ outputs/
git commit -m "Daily update $(date +%Y-%m-%d)"
git push origin main
```

**Automation:** GitHub Actions runs daily at 05:00 UTC. Use `push_daily.ps1` only if Actions is down.

**Quality Check:** Quality gate prevents bad data from being committed. If it fails:

```bash
python pipeline/quality_gate.py
# QUALITY GATE FAILED - do not commit or push these outputs:
#   - outputs/forecasts/validated/forecast_validated_2026-09-28.json: contains NaN
```

Don't commit until the gate passes.

---

## 📚 Documentation

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, pipeline stages, ensemble logic, validation |
| [SECURITY.md](docs/SECURITY.md) | Security risks, mitigation strategies, hardening plan, incident response |
| [FIXES_AND_IMPROVEMENTS.md](docs/FIXES_AND_IMPROVEMENTS.md) | What was fixed in September 2026 (NaN crash, route shadowing, etc.) |
| [OPERATIONAL_GUIDE.md](docs/OPERATIONAL_GUIDE.md) | Running locally, troubleshooting, deployment, monitoring |
| [API.md](docs/API.md) | REST API reference, request/response schemas, integration examples |

---

## 🔒 Security & Production Readiness

### ✅ What's Working

- **No secrets in git** — TSDB_URL passed as GitHub secret
- **Quality gate blocks bad data** — NaN, conflict markers, missing commodities rejected before publish
- **Safe local push** — `push_daily.ps1` now checks quality gate + origin status
- **Data validation** — Input bounds (days, threshold_price), malformed row cleanup
- **Data integrity** — WFP downloads validated, Postgres writes checked for NaN

### ⚠️ Known Gaps (See [SECURITY.md](docs/SECURITY.md))

- Alert endpoints lack authentication (anyone can list/delete/create)
- CORS allows all origins (should restrict to Agrolinking domains)
- No rate limiting (vulnerable to DoS on `/history` endpoints)
- Postgres role needs least-privilege (currently admin)
- Requirements files use flexible versions (should pin exact)

**See [SECURITY.md](docs/SECURITY.md) for detailed remediation plan.**

---

## 🚢 Deployment

### Streamlit Dashboard

Auto-deploys on `git push` to Streamlit Cloud (30 seconds).

```
https://agrolinking-intelligence-f8qq4uhupaax2qny8rpcpx.streamlit.app
```

### FastAPI on Render

Auto-deploys on `git push` to Render (2 minutes).

```
https://agrolinking-intelligence.onrender.com
https://agrolinking-intelligence.onrender.com/docs
```

### GitHub Actions Pipeline

Runs daily at 05:00 UTC (see `.github/workflows/daily_pipeline.yml`):

1. Checkout repo
2. Install Python 3.10 + dependencies
3. Run steps 01–08 (ingest → intelligence)
4. Run quality gate (step 09)
5. Commit and push outputs to main
6. Notify on failure (if configured)

---

## 🛠️ Troubleshooting

### "API returns 500 errors"

1. Check the latest run log: `cat outputs/logs/forecast_*.log`
2. Run the quality gate: `python pipeline/quality_gate.py`
3. If it fails, don't push. Fix the data first.

### "Forecast prices look wrong"

1. Are `MANUAL_PRICES` stale? Check the last Agricome post
2. Did the validation correction fail? Check `outputs/logs/validation_report_*.json`
3. Is a commodity missing? Run `python pipeline/quality_gate.py`

### "Zonal prices don't match national"

This is expected. State prices interpolate the national curve using structural factors from `data/external/state_price_differentials.csv`. They won't match exactly.

### "An old run's data is being served"

API fallback to the last good file if the latest contains NaN. Check that `pipeline/quality_gate.py` passes on your data.

---

## 📊 Data Sources

| Source | Commodities | Frequency | Coverage |
|---|---|---|---|
| Agricome Africa (@agricomeafrica) | Hibiscus, Sesame, Ginger, Cocoa, Soybeans, Cashew Nuts, Wheat | Weekly | 7 crops |
| WFP Nigeria Food Price Monitor | Sorghum, Beans, Maize, Rice, Fish, Meat | Monthly | 13 markets |
| Agrolinking Primary | Wheat, Maize, Beans | Weekly | Internal collection |
| NGX/LCFE Exchange | Ginger, Sesame (validation) | Weekly | 2 commodities |
| Market Research | Eggs, Meat (beef), Meat (goat), Fish | Weekly | 4 livestock |

---

## 🔄 How Daily Variation Works

Each run produces unique values through deterministic date-seeded noise:

**Step 5 (Forecast):** ±0.5–2% noise on ensemble output, seeded by `commodity + date`  
**Step 6 (Validation):** ±0.8% noise on reference price anchor, seeded by `commodity + date`

Result: Same day always produces the same output (reproducible), but each day differs from the previous (realistic microstructure movement).

---

## 📦 Technology Stack

- **Language:** Python 3.10+
- **API:** FastAPI + Uvicorn
- **Dashboard:** Streamlit
- **Forecasting:** Prophet, pmdarima (ARIMA), XGBoost, LightGBM, statsmodels
- **Data:** pandas, NumPy, scikit-learn
- **Logging:** loguru
- **Data Fetching:** requests, BeautifulSoup4
- **Database:** PostgreSQL/TimescaleDB (optional, dual-write mode)

---

## 📄 License & Attribution

**Proprietary.** Built by Agrolinking Solutions Nigeria.

Data sources:
- Agricome Africa (licensed, attributed in outputs)
- WFP Nigeria (CC-BY, public)
- Internal collection (Agrolinking)

---

## 👥 Team

**Agrolinking Solutions Nigeria**  
🌐 Website: https://agrolinking.com  
📧 Contact: info@agrolinking.com  

*Redefining the Future of Agricultural Connection in Africa*

---

## 📞 Support

| Issue | Action |
|---|---|
| API down | Check GitHub Actions logs, run quality gate locally |
| Dashboard slow | Refresh the page (Streamlit auto-redeploy) |
| Bad forecasts | Update MANUAL_PRICES in 06_validate.py |
| Data corruption | Run quality gate, check git history for conflict markers |
| Want a new commodity | Add to COMMODITIES in config/settings.py, retrain models |

---

## 🎯 Roadmap

- [ ] Authentication on alert endpoints (OAuth2 or API key)
- [ ] Rate limiting (slowapi)
- [ ] Postgres for alerts storage (per-user isolation)
- [ ] Monitoring & Slack alerts
- [ ] Real-time data ingestion (not just daily batch)
- [ ] Mobile app for alerts
- [ ] WhatsApp integration for direct delivery

---

**Last Updated:** September 28, 2026
**Status:** Production | **Accuracy:** 1.7% average error post-correction (see [FIXES_AND_IMPROVEMENTS.md](docs/FIXES_AND_IMPROVEMENTS.md) for how this is measured)
