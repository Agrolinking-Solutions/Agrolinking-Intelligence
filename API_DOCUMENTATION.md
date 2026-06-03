# Agrolinking Commodity Intelligence API

Base URL (production): `https://agrolinking-intelligence-api.railway.app`
Base URL (local dev):  `http://localhost:8000`
Auto-generated docs:   `{base_url}/docs`

All responses are JSON. All prices are in NGN/MT (Nigerian Naira per metric tonne).

---

## Endpoints

### GET /
Health check. Returns API status and endpoint list.

```json
{
  "status": "operational",
  "commodities": 13,
  "last_updated": "2026-06-01",
  "docs": "/docs"
}
```

---

### GET /commodities
All 13 commodities with current price, daily change, and validation status.

```json
{
  "count": 13,
  "as_of": "2026-06-01",
  "commodities": [
    {
      "commodity": "Rice",
      "price_ngn_mt": 1584417,
      "last_known_date": "2026-06-01",
      "forecast_price": 1584417,
      "pct_change_daily": -2.5,
      "validation_error": 2.2,
      "within_target": true
    }
  ]
}
```

---

### GET /forecasts/latest
Full forecast for all commodities. Optional `?horizon=monthly` filter.

Query params:
- `horizon` — one of: `daily`, `weekly`, `2_weeks`, `monthly`, `3_months`, `6_months`

---

### GET /forecasts/{commodity}
Full 6-horizon forecast for one commodity including weekly price series.

```
GET /forecasts/Rice
GET /forecasts/Maize%20(white)
GET /forecasts/Ginger
```

Response includes:
- `last_known_price` and `last_known_date`
- `validation` block with error before/after and correction action
- `horizons` block with weekly series, confidence bands, and direction

---

### GET /forecasts/{commodity}/{horizon}
Single commodity at a specific forecast horizon.

```
GET /forecasts/Rice/monthly
GET /forecasts/Cocoa/3_months
GET /forecasts/Maize%20(white)/6_months
```

Returns `weekly_series` array suitable for charting:
```json
{
  "weekly_series": [
    { "date": "2026-06-08", "price": 1584000, "lower_ci": 1510000, "upper_ci": 1660000 },
    { "date": "2026-06-15", "price": 1598000, "lower_ci": 1520000, "upper_ci": 1680000 }
  ]
}
```

---

### GET /zonal/latest
All zonal and state prices. Optional filters:
- `?zone=North%20West`
- `?commodity=Rice`

---

### GET /zonal/{commodity}
State-level prices for one commodity with best sourcing intelligence.

```
GET /zonal/Rice
GET /zonal/Ginger
```

```json
{
  "commodity": "Rice",
  "national_price": 1584417,
  "best_sourcing": {
    "state": "Plateau",
    "price_ngn_mt": 1425976,
    "spread_pct": 31,
    "vs_state": "Lagos"
  },
  "state_prices": {
    "Lagos":   { "zone": "South West", "price_ngn_mt": 1870000, "day_change_pct": 0.4 },
    "Kano":    { "zone": "North West", "price_ngn_mt": 1510000, "day_change_pct": 0.4 },
    "Plateau": { "zone": "North Central", "price_ngn_mt": 1425976, "day_change_pct": 0.4 }
  }
}
```

---

### GET /alerts/latest
Latest WhatsApp-ready price alert as text and JSON.

---

### GET /summary
Dashboard hero data — use this for the website header widget.

```json
{
  "commodities_tracked": 13,
  "verified_accuracy": "13/13",
  "verified_accuracy_pct": 100.0,
  "avg_model_error_pct": 1.5,
  "last_pipeline_run": "2026-06-01",
  "zones": 6,
  "states": 12
}
```

---

## Setup Instructions for Dev Team

### Installation
```bash
pip install fastapi uvicorn
```

### Run locally
```bash
# From project root
python api.py
# or
uvicorn api:app --reload --port 8000
```

### Interactive docs
Open `http://localhost:8000/docs` in browser.
FastAPI auto-generates a full Swagger UI where the team can test every endpoint.

---

## Deployment on Railway (separate from Streamlit)

Add a `Procfile` to project root:
```
web: uvicorn api:app --host 0.0.0.0 --port $PORT
```

Or deploy with Docker:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install fastapi uvicorn
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Example Frontend Usage

### Fetch today's commodity prices
```javascript
const res  = await fetch('https://your-api-url/commodities');
const data = await res.json();
// data.commodities[i].commodity, .price_ngn_mt, .pct_change_daily
```

### Fetch Rice monthly forecast for a chart
```javascript
const res  = await fetch('https://your-api-url/forecasts/Rice/monthly');
const data = await res.json();
const series = data.weekly_series;
// series[i].date, .price, .lower_ci, .upper_ci
```

### Fetch zonal prices for a map widget
```javascript
const res  = await fetch('https://your-api-url/zonal/Maize%20(white)');
const data = await res.json();
const states = data.state_prices;
// states["Lagos"].price_ngn_mt, states["Kano"].price_ngn_mt
```

### Summary widget for homepage hero
```javascript
const res  = await fetch('https://your-api-url/summary');
const { commodities_tracked, verified_accuracy, avg_model_error_pct } = await res.json();
```

---

## Update Frequency

The API reads from static JSON files written by the pipeline.
Files update daily when `python pipeline/run_pipeline.py --skip-train` runs.
Full retrain happens weekly (Mondays).
No caching needed — file reads are near-instant.
