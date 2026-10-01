# Agrolinking Commodity Intelligence API

**Live URL:** `https://pis-api.agrolinking.com`

**Interactive Docs:** `https://pis-api.agrolinking.com/docs`

**API Version:** 2.1.0 | **Last Updated:** September 2026

> Moved off Render at the end of September 2026 because of cost — now on
> a VPS, served via `pis-api.agrolinking.com` (and the web frontend at
> `pis.agrolinking.com`) with proper HTTPS. See
> [FIXES_AND_IMPROVEMENTS.md](FIXES_AND_IMPROVEMENTS.md) for the full
> record of the move. Railway, used even earlier than Render, has been
> fully decommissioned and no longer resolves at all.

All responses are JSON. All prices in NGN/MT unless otherwise stated. CORS is currently open to all origins — see [SECURITY.md](SECURITY.md) for why this needs restricting.

---

## Infrastructure

| Component | Service | URL | Status |
|---|---|---|---|
| API | VPS | pis-api.agrolinking.com | Live — HTTPS |
| Web frontend | VPS | pis.agrolinking.com | Live |
| Dashboard | Streamlit Cloud | agrolinking-intelligence-f8qq4uhupaax2qny8rpcpx.streamlit.app | Live |
| Daily pipeline | GitHub Actions | Runs 05:00 UTC daily | Active |

**Deploy note:** the VPS auto-deploys on every push to `main`, same workflow as Render before it.

---

## Endpoint Directory

### V1 — Core Data
| Method | Endpoint | Description |
|---|---|---|
| GET | / | Health check and full endpoint directory |
| GET | /health | Dedicated health check for monitoring |
| GET | /summary | Dashboard hero card — accuracy, error, run date |
| GET | /commodities | All 17 live prices with daily change and validation |
| GET | /forecasts/latest | Full forecast all commodities. Optional ?horizon= filter |
| GET | /forecasts/{commodity} | Single commodity 6-horizon forecast with weekly series |
| GET | /forecasts/{commodity}/{horizon} | Chart-ready weekly series with confidence bands |
| GET | /zonal/latest | All zonal and state prices |
| GET | /zonal/{commodity} | State-level prices with best sourcing intelligence |
| GET | /alerts/latest | WhatsApp/email ready daily broadcast text |

### V2 — Intelligence Layer
| Method | Endpoint | Description |
|---|---|---|
| GET | /prices/kg | All 17 prices in NGN/kg (Eggs in NGN/crate) |
| GET | /prices/kg/{commodity} | Single commodity NGN/kg |
| GET | /index/food | Food Price Index (base 2025=100) with MoM change |
| GET | /index/volatility | 30-day rolling volatility index per commodity |
| GET | /outlook/30d | Aggregate 30-day price outlook + per commodity |
| GET | /confidence | Model confidence scores per commodity |
| GET | /movers | Biggest riser and faller today |
| GET | /alerts/early-warning | WFP ALPS alert status (Severe/High/Watch/Normal) |
| GET | /shortage-surplus | Shortage/surplus score 0-100 per commodity |
| GET | /seasonality/{commodity} | Monthly seasonality profile |
| GET | /spreads | State price high/low spread per commodity |
| GET | /arbitrage | Net arbitrage per kg after freight all commodities |
| GET | /arbitrage/{commodity} | Single commodity arbitrage detail |
| GET | /intelligence/latest | Full intelligence bundle — all metrics in one call |
| GET | /supply | Supply availability per zone (Tight/Balanced/Surplus) |
| GET | /supply/{zone} | Supply availability for specific zone |
| GET | /routes | All state-to-state distances and freight costs |
| GET | /routes/{origin}/{destination} | Route detail with viable commodities |

### V3 — History, Alerts, Meta
| Method | Endpoint | Description |
|---|---|---|
| GET | /history/{commodity} | Historical price series from 2016. ?days=90 or ?from_date= |
| GET | /history/compare | Multi-commodity comparison with indexed values |
| GET | /history/fpi | Historical Food Price Index series |
| GET | /alerts/saved | List all saved price threshold alerts |
| POST | /alerts/saved | Create a price threshold alert |
| DELETE | /alerts/saved/{alert_id} | Delete a saved alert |
| GET | /alerts/check | Check all alerts against current prices |
| GET | /factors | Forecast factor drivers per commodity (Rainfall/FX/Fuel/Harvest/Policy) |
| GET | /meta | Platform metadata, source counts, field documentation |

**Valid horizons:** daily | weekly | 2_weeks | monthly | 3_months | 6_months

---

## Key Endpoints — Request & Response Examples

### GET /commodities
```
GET https://pis-api.agrolinking.com/commodities
```
```json
{
  "count": 17,
  "as_of": "2026-08-06",
  "commodities": [
    {
      "commodity": "Rice",
      "price_ngn_mt": 1337074,
      "price_per_unit": 1337.07,
      "unit": "NGN/kg",
      "day_change_pct": 0.09,
      "forecast_price": 1351709,
      "within_target": true,
      "currency": "NGN"
    }
  ]
}
```

### GET /forecasts/{commodity}/{horizon}
```
GET /forecasts/Rice/monthly
GET /forecasts/Maize%20(white)/3_months
GET /forecasts/Ginger/6_months
```
Returns `weekly_series` array — drop directly into Chart.js, Recharts, or D3:
```json
{
  "commodity": "Rice",
  "horizon": "monthly",
  "forecast_price_ngn": 1351709,
  "pct_change": 1.1,
  "direction": "up",
  "weekly_series": [
    {"date": "2026-08-09", "price": 1340000, "lower_ci": 1280000, "upper_ci": 1400000},
    {"date": "2026-08-16", "price": 1345000, "lower_ci": 1285000, "upper_ci": 1408000}
  ]
}
```

### GET /factors?commodity=Rice
```json
{
  "commodity": "Rice",
  "overall_pressure": "Neutral",
  "factors": {
    "rainfall_season":  {"rating": "Low",  "note": "Harvest season — good supply"},
    "fuel_transport":   {"rating": "Mid",  "note": "Fuel prices stable"},
    "fx_import_parity": {"rating": "High", "note": "Import-dependent — FX pressure"},
    "harvest_supply":   {"rating": "Low",  "note": "Surplus (score=69)"},
    "policy_tariffs":   {"rating": "High", "note": "50% import levy active"}
  }
}
```

### GET /history/{commodity}?days=90
```
GET /history/Rice?days=90
GET /history/Ginger?from_date=2026-01-01
GET /history/compare?commodities=Rice,Wheat&days=180
```

### POST /alerts/saved
```
POST /alerts/saved?commodity=Rice&threshold_price=1500000&direction=above&email=buyer@company.com
```
```json
{
  "message": "Alert created successfully.",
  "alert": {
    "id": "a3f2b1c4",
    "commodity": "Rice",
    "threshold_price": 1500000,
    "direction": "above",
    "active": true
  }
}
```

---

## Commodity Name Reference

| URL-encoded | Display Name |
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
| Meat%20(beef) | Meat (beef) |
| Meat%20(goat) | Meat (goat) |
| Fish%20(dried) | Fish (dried) |
| Eggs | Eggs |

---

## Zones and States

| Zone | States |
|---|---|
| North West | Kano, Kaduna |
| North Central | Plateau, Kogi |
| North East | Adamawa, Borno |
| South West | Oyo, Lagos |
| South East | Anambra, Imo |
| South South | Rivers, Delta |

---

## Frontend Integration Examples

### Homepage hero widget
```javascript
const res  = await fetch('https://pis-api.agrolinking.com/summary');
const data = await res.json();
// data.commodities_tracked, data.verified_accuracy, data.avg_model_error_pct
```

### Live price cards
```javascript
const res  = await fetch('https://pis-api.agrolinking.com/commodities');
const data = await res.json();
data.commodities.forEach(c => {
  console.log(c.commodity, c.price_ngn_mt, c.day_change_pct);
});
```

### Forecast chart
```javascript
const res  = await fetch('https://pis-api.agrolinking.com/forecasts/Rice/monthly');
const data = await res.json();
const labels = data.weekly_series.map(p => p.date);
const prices = data.weekly_series.map(p => p.price);
// Pass to Chart.js, Recharts, or D3
```

### Nigeria map widget
```javascript
const res  = await fetch('https://pis-api.agrolinking.com/zonal/Rice');
const data = await res.json();
const states = data.state_prices;
// states["Lagos"].price_ngn_mt, states["Kano"].price_ngn_mt
```

### Factor drivers panel
```javascript
const res  = await fetch('https://pis-api.agrolinking.com/factors?commodity=Rice');
const data = await res.json();
// data.overall_pressure, data.factors.rainfall_season.rating, etc.
```

### Price alert creation
```javascript
const res = await fetch(
  'https://pis-api.agrolinking.com/alerts/saved' +
  '?commodity=Rice&threshold_price=1500000&direction=above',
  { method: 'POST' }
);
const alert = await res.json();
// alert.alert.id — save this to check or delete later
```

---

## Data Update Schedule

| Update | Frequency | Method |
|---|---|---|
| Price forecasts | Daily 7am WAT | GitHub Actions auto-pipeline |
| Zonal prices | Daily 7am WAT | GitHub Actions auto-pipeline |
| Intelligence metrics | Daily 7am WAT | GitHub Actions auto-pipeline |
| Model retrain | Weekly (Mondays) | Manual — run pipeline/04_train.py |
| MANUAL_PRICES | When Agricome posts | Manual — update pipeline/06_validate.py |

---

## Deployment Notes

The API is deployed on a VPS, served at `pis-api.agrolinking.com`. It autodeploys on every push to the `main` branch of `Agrolinking-Solutions/Agrolinking-Intelligence` — same workflow as Render before it.

The `as_of` field on every endpoint shows the date of the last pipeline run. If it shows a date older than today, the pipeline may not have run — check GitHub Actions logs.

