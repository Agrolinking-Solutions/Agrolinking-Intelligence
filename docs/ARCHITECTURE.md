# System Architecture

Rewritten 3 Nov 2026 to match the code. The earlier version described the
ML ensemble, date-seeded "daily noise", correction towards typed-in prices
and Render/Streamlit hosting; none of that is how the platform works now.

## Overview

The Agrolinking Price Intelligence System (PIS) publishes national and state
wholesale prices and 1-day to 6-month forecasts for 18 commodities, in NGN
per tonne (eggs: NGN per crate of 30). All data work runs in GitHub Actions;
the server only runs the API.

```
Sources ──► GitHub Actions ──► repository files ──► API server (VPS) ──► website, AgLens
            (daily run,     └► Postgres/TimescaleDB ──┘
             3-hourly search)
            └► GitHub issues (alerts, emailed to repo watchers)
```

| Part | Where | Notes |
|---|---|---|
| Daily pipeline | `.github/workflows/daily_pipeline.yml` | 04:17 UTC (05:17 Lagos) plus backup slots; a gate skips a slot once the day's run has committed |
| Web price search | `.github/workflows/web_price_stream.yml` | Every 3 hours; Claude (Anthropic API) writes candidates to Postgres; also runs the uptime check |
| API | `api.py` on a VPS, `https://pis-api.agrolinking.com` | FastAPI; the server pulls the repository on every push to `main` (~6 minutes) |
| Website | `https://pis.agrolinking.com` | Same VPS; built by Victor |
| Database | Postgres + TimescaleDB, address in the `TSDB_URL` secret | Second store; see Storage |

## Data sources

Every row in the price history carries `data_source`. Forecasts start from,
and are scored on, observed prices only (`config/settings.py`):

- `PARTNER_SOURCES` - Agricome, WFP, FEWS_NET, AFEX, Agrolinking_primary,
  WFP_provisional. `SCORED_SOURCES` is the same minus WFP_provisional.
- `REAL_SOURCES` - partner sources plus `Web_search` (web prices that passed
  `pipeline/14_promote_web_prices.py`).
- Estimates: `Synthetic (World Bank/FAO × FX)`, `Interpolated`,
  `WB_RTP_estimate`, `Agrolinking_validated`.

| Source | Reader | Notes |
|---|---|---|
| Agricome weekly prices (7 crops) | typed into `data/raw/agricome_raw.csv` | from public posts; no agreement yet |
| WFP market monitor | `pipeline/refresh_wfp_hdx.py` (HDX) | monthly, ~4-6 weeks late; newest partial month kept as WFP_provisional; download checked before use |
| FEWS NET | `pipeline/fetch_fao_prices.py` (FAO FPMA API) | weekly wholesale white maize, imported rice |
| AFEX exchange | `pipeline/fetch_afex_prices.py` (Mansa Markets pages) | real for white maize only (grade confirmed by Mansa Markets, 3 Nov 2026; 5-day median, `pipeline/afex_median.py`) |
| Sibling estimate | `pipeline/02_clean.py` (`SIBLING_PRICES` in `config/settings.py`) | yellow maize = white maize's real price x 1.023 (measured, WFP 2014-23); labelled an estimate, never scored |
| Agrolinking field data | `data/raw/field_prices.csv` | trader quotes; weekly median |
| Web prices | `pipeline/10_web_price_search.py` → `14_promote_web_prices.py` | checks: level, date (page opened), age, band, corroboration, location (`pipeline/locations.py`), blocked/withdrawn lists |
| CBN exchange rate, inflation; WFP fuel | `pipeline/fetch_macro.py` | |
| Weather, El Niño | `pipeline/11_external_factors.py`, `pipeline/weather_report.py` | context only |

`docs/API.md` lists the endpoints. The internal "PIS System Map" page has
the full inventory with what is real and what is estimated.

## The daily run

1. **Fetch** (steps 00-00j): external factors, weather, web price
   promotion, WFP, CBN macro, AFEX, FAO/FEWS NET, produce and farm prices.
2. **Build the history** (01 ingest, 02 clean, 03 features): one row per
   commodity per week; partner prices beat web prices beat estimates; known
   bad data dropped (CFA-franc border markets before 2017, `WFP_MISDATED`,
   withdrawn web prices); filled-in weeks only between two real prices.
3. **Train** (04): the old ML models (ARIMA, Prophet, XGBoost, LSTM). Still
   run, not published.
4. **Choose the method** (04b, `13_select_forecaster.py`) and **forecast**
   (05, `05_forecast.py`, `drift_models.py`), described below.
5. **Validate** (06): compare with the newest real price from the last 45
   days, or mark the forecast "unchecked"; nothing is corrected towards a
   typed-in number. Dual-writes to Postgres.
6. **Measure** (06b live accuracy, 06c Monday fair audit
   `scripts/research/forecast_audit.py`).
7. **Publish** (07 state prices and day changes, labelled revision / new
   real price / forecast; 08 intelligence; 08b price drivers).
8. **Check and alert** (09 staleness, 09b cross-check, 09c price alerts,
   09e feed alerts, 09g database check, 09f monthly report, 09d data checks,
   tests, data status, quality gate).
9. **Commit** the results; the server picks them up.

## Forecasting

Eight explainable methods (`pipeline/drift_models.py`): `naive` (price
holds), `infl@0.5`, `infl@1.0` (food-inflation drift), `trend@0.5` (half the
last 26 weeks' movement, capped 1.5%/week), `season@0.5`, `season@1.0`
(monthly pattern from at least 3 years of real prices), `mix`,
`season+infl`.

`13_select_forecaster.py` replays each on the last 104 weeks of scored real
prices at 4, 8, 13 and 26 weeks (weights 0.4/0.3/0.2/0.1). A challenger must
beat `naive` by 0.3 points; at 26 weeks another method may take over
gradually if at least 1 point better. The forecast starts from the latest
real price carried to this week. Ranges come from measured errors
(`range_halfwidth`), aiming to hold the real price 8 times in 10 and widening
with data age.

Accuracy: weekly fair audit (`/accuracy` `fair_test`), the selection backtest
(`backtest`), and live scoring of published forecasts (`live`, with
`in_range_pct`).

## Storage

- **Repository files** (main store; the API reads these): `data/raw/`,
  `data/processed/agrolinking_master.csv`, `data/processed/features/`,
  `outputs/forecasts/{validated,zonal}/`, `outputs/intelligence/`,
  `outputs/backtest/`, `outputs/logs/`. Git keeps every version.
- **Postgres + TimescaleDB** (`scripts/migration/schema.sql`): prices
  (hypertable), forecasts, commodities, locations, intelligence_metrics,
  market_index, web_price_estimates, users, alerts, portfolio_holdings. The
  last 180 days of prices are kept an exact copy of the master. The API reads
  it for `/forecasts/latest` (file fallback) and saved alerts (no fallback).

## Failure handling

- A failing core step stops the run; the API keeps serving the last good
  files. GitHub emails the repository owner about failed runs.
- Report-only steps (`continue-on-error`) never block publishing.
- The quality gate (`pipeline/quality_gate.py`) blocks NaN/Infinity, merge
  markers or missing commodities from being committed.
- `pipeline/uptime_check.py` (every 3 hours) opens an issue if the API or
  website is down or the server stops picking up new runs.
