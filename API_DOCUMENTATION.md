# Agrolinking Commodity Intelligence API

**Live Base URL:** `https://agrolinking-intelligence-api.onrender.com`

**Local Base URL:** `http://localhost:8000`

**Interactive Docs (Swagger UI):** `https://agrolinking-intelligence-api.onrender.com/docs`

All responses are JSON. All prices are in NGN/MT (Nigerian Naira per metric tonne).
CORS is open so any frontend domain can call the API without configuration.

> Note: The API is hosted on Render's free tier. The first request after 15 minutes
> of inactivity takes approximately 30 seconds to wake up. Subsequent requests are instant.
> Contact the Agrolinking data team to upgrade to always-on when integrating into production.

---

## Endpoints

### GET /
Health check and API overview.

```json
{
  "status": "operational",
  "api": "Agrolinking Commodity Intelligence",
  "version": "1.0.0",
  "commodities": 13,
  "last_updated": "2026-06-03",
  "docs": "/docs"
}
```

---

### GET /commodities
All 13 commodities with current price, daily change, and validation status.

```
GET https://agrolinking-intelligence-api.onrender.com/commodities
```

```json
{
  "count": 13,
  "as_of": "2026-06-03",
  "commodities": [
    {
      "commodity": "Rice",
      "price_ngn_mt": 1584417,
      "last_known_date": "2026-06-03",
      "forecast_price": 1584417,
      "pct_change_daily": -2.5,
      "validation_error": 2.2,
      "validation_status": "verified",
      "within_target": true,
      "currency": "NGN",
      "unit": "NGN/MT"
    }
  ]
}
```

---

### GET /forecasts/latest
Full forecast for all 13 commodities. Optional horizon filter.

```
GET https://agrolinking-intelligence-api.onrender.com/forecasts/latest
GET https://agrolinking-intelligence-api.onrender.com/forecasts/latest?horizon=monthly
```

Query parameters:

| Parameter | Type | Values |
|---|---|---|
| horizon | string (optional) | daily, weekly, 2_weeks, monthly, 3_months, 6_months |

---

### GET /forecasts/{commodity}
Full 6-horizon forecast for one commodity including weekly price series and confidence bands.

```
GET https://agrolinking-intelligence-api.onrender.com/forecasts/Rice
GET https://agrolinking-intelligence-api.onrender.com/forecasts/Maize%20(white)
GET https://agrolinking-intelligence-api.onrender.com/forecasts/Ginger
```

Response includes the `horizons` block with `weekly_series` array suitable for charting:

```json
{
  "commodity": "Rice",
  "run_date": "2026-06-03",
  "last_known_price": 1584417,
  "last_known_date": "2026-06-03",
  "currency": "NGN",
  "unit": "NGN/MT",
  "models_used": ["arima", "prophet", "holt_winters", "xgboost"],
  "validation": {
    "reference_price": 1550000,
    "error_before_pct": 8.9,
    "error_after_pct": 2.2,
    "action": "soft_blend",
    "within_target": true
  },
  "horizons": {
    "monthly": {
      "forecast_date": "2026-06-29",
      "forecast_price": 1620000,
      "pct_change": 2.3,
      "direction": "up",
      "weekly_series": [
        {
          "date": "2026-06-08",
          "price": 1598000,
          "lower_ci": 1520000,
          "upper_ci": 1680000
        }
      ]
    }
  }
}
```

---

### GET /forecasts/{commodity}/{horizon}
Single commodity at a specific forecast horizon. Returns chart-ready weekly series.

```
GET https://agrolinking-intelligence-api.onrender.com/forecasts/Rice/monthly
GET https://agrolinking-intelligence-api.onrender.com/forecasts/Cocoa/3_months
GET https://agrolinking-intelligence-api.onrender.com/forecasts/Maize%20(white)/6_months
```

Valid horizons: `daily`, `weekly`, `2_weeks`, `monthly`, `3_months`, `6_months`

```json
{
  "commodity": "Rice",
  "horizon": "monthly",
  "run_date": "2026-06-03",
  "last_known_price": 1584417,
  "forecast_date": "2026-06-29",
  "forecast_price_ngn": 1620000,
  "pct_change": 2.3,
  "direction": "up",
  "currency": "NGN",
  "unit": "NGN/MT",
  "validation_error_pct": 2.2,
  "within_target": true,
  "weekly_series": [
    {
      "date": "2026-06-08",
      "price": 1598000,
      "lower_ci": 1520000,
      "upper_ci": 1680000
    },
    {
      "date": "2026-06-15",
      "price": 1607000,
      "lower_ci": 1528000,
      "upper_ci": 1690000
    }
  ]
}
```

---

### GET /zonal/latest
All zonal and state prices for all 13 commodities across 6 zones and 12 states.

```
GET https://agrolinking-intelligence-api.onrender.com/zonal/latest
GET https://agrolinking-intelligence-api.onrender.com/zonal/latest?zone=North%20West
GET https://agrolinking-intelligence-api.onrender.com/zonal/latest?commodity=Rice
```

Query parameters:

| Parameter | Type | Example |
|---|---|---|
| zone | string (optional) | North West, North Central, North East, South West, South East, South South |
| commodity | string (optional) | Rice, Ginger, Maize (white) |

---

### GET /zonal/{commodity}
State-level prices for one commodity with best sourcing intelligence.

```
GET https://agrolinking-intelligence-api.onrender.com/zonal/Rice
GET https://agrolinking-intelligence-api.onrender.com/zonal/Ginger
GET https://agrolinking-intelligence-api.onrender.com/zonal/Maize%20(white)
```

```json
{
  "commodity": "Rice",
  "run_date": "2026-06-03",
  "national_price": 1584417,
  "day_change_pct": 0.4,
  "currency": "NGN",
  "unit": "NGN/MT",
  "best_sourcing": {
    "state": "Plateau",
    "price_ngn_mt": 1425976,
    "spread_pct": 31,
    "vs_state": "Lagos"
  },
  "state_prices": {
    "Kano":    { "zone": "North West",    "price_ngn_mt": 1506000, "day_change_pct": 0.4, "is_primary": false },
    "Kaduna":  { "zone": "North West",    "price_ngn_mt": 1474000, "day_change_pct": 0.4, "is_primary": true  },
    "Plateau": { "zone": "North Central", "price_ngn_mt": 1426000, "day_change_pct": 0.4, "is_primary": true  },
    "Lagos":   { "zone": "South West",    "price_ngn_mt": 1870000, "day_change_pct": 0.4, "is_primary": false }
  }
}
```

---

### GET /alerts/latest
Latest validated daily price alert, formatted for WhatsApp or email.

```
GET https://agrolinking-intelligence-api.onrender.com/alerts/latest
```

```json
{
  "date": "2026-06-03",
  "source": "alert_validated_2026-06-03.txt",
  "text": "AGROLINKING COMMODITY INTELLIGENCE ALERT\nMonday, 03 June 2026\n...",
  "format": "WhatsApp / Email ready"
}
```

---

### GET /summary
Dashboard hero data for the website header widget.

```
GET https://agrolinking-intelligence-api.onrender.com/summary
```

```json
{
  "commodities_tracked": 13,
  "verified_accuracy": "13/13",
  "verified_accuracy_pct": 100.0,
  "avg_model_error_pct": 1.5,
  "last_pipeline_run": "2026-06-03",
  "accuracy_target": "within 3% of live market prices",
  "data_sources": ["Agricome Africa", "WFP Nigeria", "NGX", "Market Naija TV", "LCFE"],
  "zones": 6,
  "states": 12,
  "forecast_horizons": ["daily", "weekly", "2_weeks", "monthly", "3_months", "6_months"]
}
```

---

## Frontend Integration Examples

### Summary widget for homepage hero section

```javascript
const res  = await fetch('https://agrolinking-intelligence-api.onrender.com/summary');
const data = await res.json();

// data.commodities_tracked    -> 13
// data.verified_accuracy      -> "13/13"
// data.avg_model_error_pct    -> 1.5
// data.last_pipeline_run      -> "2026-06-03"
```

### Live commodity price cards

```javascript
const res  = await fetch('https://agrolinking-intelligence-api.onrender.com/commodities');
const data = await res.json();

data.commodities.forEach(c => {
  console.log(c.commodity);       // "Rice"
  console.log(c.price_ngn_mt);    // 1584417
  console.log(c.pct_change_daily); // -2.5
  console.log(c.within_target);   // true
});
```

### Price forecast chart (Chart.js / Recharts)

```javascript
const res  = await fetch(
  'https://agrolinking-intelligence-api.onrender.com/forecasts/Rice/monthly'
);
const data = await res.json();

// data.weekly_series is chart-ready
const labels = data.weekly_series.map(p => p.date);
const prices = data.weekly_series.map(p => p.price);
const lower  = data.weekly_series.map(p => p.lower_ci);
const upper  = data.weekly_series.map(p => p.upper_ci);

// Pass directly into Chart.js, Recharts, or D3
```

### Nigeria map widget (state-level prices)

```javascript
const res  = await fetch(
  'https://agrolinking-intelligence-api.onrender.com/zonal/Rice'
);
const data = await res.json();

const states = data.state_prices;
// states["Lagos"].price_ngn_mt   -> 1870000
// states["Kano"].price_ngn_mt    -> 1506000
// states["Plateau"].price_ngn_mt -> 1426000

// Bind to Leaflet.js, Datawrapper, or any Nigeria map library
```

### All commodities at one horizon (forecast table)

```javascript
const res  = await fetch(
  'https://agrolinking-intelligence-api.onrender.com/forecasts/latest?horizon=monthly'
);
const data = await res.json();

Object.entries(data.forecasts).forEach(([name, fc]) => {
  console.log(name, fc.forecast_price_ngn, fc.pct_change);
});
```

---

## Commodity Name Reference

Use these exact names in endpoint paths (URL-encode spaces as %20):

| Name in URL | Display Name |
|---|---|
| Hibiscus | Hibiscus |
| Sesame | Sesame |
| Ginger | Ginger |
| Cocoa | Cocoa |
| Soybeans | Soybeans |
| Cashew%20Nuts | Cashew Nuts |
| Sorghum | Sorghum |
| Beans%20(white) | Beans (white) |
| Beans%20(red) | Beans (red) |
| Maize%20(white) | Maize (white) |
| Maize%20(yellow) | Maize (yellow) |
| Wheat | Wheat |
| Rice | Rice |

---

## Deployment on Render

The API is deployed on Render as a Web Service from the same GitHub repository
as the Streamlit dashboard.

### How to redeploy or set up from scratch

1. Go to https://render.com and sign in with GitHub
2. Click New, then Web Service
3. Connect repository: `Agrolinking-Solutions/Agrolinking-Intelligence`
4. Set branch to `main`
5. Set build command: `pip install -r requirements.txt`
6. Set start command: `uvicorn api:app --host 0.0.0.0 --port $PORT`
7. Set plan to Free
8. Click Create Web Service

Render auto-redeploys on every push to `main`. New forecast data pushed to GitHub
appears in the API within 2 minutes.

### Environment Variables

None required. The API reads directly from the JSON files in the `outputs/` folder
that are committed to the GitHub repository.

### Upgrading from Free to Always-On

The free tier spins down after 15 minutes of inactivity, causing a 30-second cold
start on the next request. When the API is integrated into the live agrolinking.com
website, upgrade to the Starter plan ($7/month) on Render for always-on service.

---

## Update Frequency

The API serves static JSON files written by the pipeline. Files update when the
pipeline runs and new outputs are pushed to GitHub.

Daily cadence (run from your local machine or automate with Windows Task Scheduler):

```powershell
python pipeline\run_pipeline.py --skip-train

git add outputs\forecasts\validated\
git add outputs\forecasts\zonal\
git add outputs\daily_alerts\
git commit -m "Update forecasts $(Get-Date -Format 'yyyy-MM-dd')"
git push
```

Weekly cadence (full retrain, Monday):

```powershell
python pipeline\run_pipeline.py
git add outputs\
git commit -m "Weekly retrain $(Get-Date -Format 'yyyy-MM-dd')"
git push
```