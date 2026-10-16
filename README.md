# Agrolinking Commodity Intelligence Platform

Daily wholesale price intelligence and forecasts for Nigerian agricultural commodities, built by Agrolinking Solutions.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-Proprietary-red)
![Status](https://img.shields.io/badge/Status-Production-green)

- **Web app:** [pis.agrolinking.com](https://pis.agrolinking.com) (frontend lives in a separate repository)
- **API:** [pis-api.agrolinking.com](https://pis-api.agrolinking.com) · interactive docs at [/docs](https://pis-api.agrolinking.com/docs)
- **Pipeline:** GitHub Actions, every morning, plus a web price search every 3 hours

---

## What it does

- **Prices for 19 commodities.** Hibiscus, Sesame, Ginger, Cocoa, Soybeans, Cashew Nuts, Sorghum, Beans (white), Beans (red), Maize (white), Maize (yellow), Wheat, Rice (local), Rice (foreign), Meat (beef), Meat (goat), Fish (dried) and Eggs. The legacy "Rice" is still listed and is being retired now that the two rice types are live.
- **One price basis: wholesale.** Every price is wholesale, in naira per tonne. Eggs are per crate of 30. Meat, fish and eggs are shop (retail) prices until a wholesale source exists, and they are labelled that way.
- **Real data first.** Each price point records its source. Real observed prices (partner, exchange, market-monitor and vetted web prices) are kept apart from estimates (interpolated, synthetic, World Bank model, platform model), and the API labels every point.
- **Forecasts for 6 horizons:** next day, 1 week, 2 weeks, 1 month, 3 months and 6 months, each with a range.
- **A forecast method chosen daily by measured accuracy.** For each commodity, the method that is most accurate on real past prices is used. Accuracy is measured, published at `/accuracy`, and tracked against the prices that actually arrive.
- **State prices** for 12 states in 6 zones. Each state price is labelled as using a measured or an estimated state price gap.
- **Daily data checks.** Every morning the run checks freshness, compares our prices with independent web and exchange prices, applies a quality gate, and posts a plain-language status at the top of the GitHub run page.

---

## Data sources

| Source | What it gives | How often | Used as |
|---|---|---|---|
| **Agricome Africa** | Wholesale prices for hibiscus, sesame, ginger, cocoa, soybeans, cashew and sorghum | Weekly, pasted from their Threads posts into `data/raw/agricome_raw.csv` | Real, highest priority |
| **WFP Nigeria** (HDX) | Monthly shop prices from about 24 North-East markets (Borno, Yobe, Adamawa) for beans, rice, meat, fish, eggs and sorghum | Monthly, about 4 weeks late (step 00d) | Real. Beans, sorghum and rice are converted to wholesale-equivalent using measured wholesale/retail ratios |
| **FEWS NET** (via FAO's price API) | Weekly wholesale prices for Kano (Dawanau), Ibadan, Kaura Namoda and Maiduguri | Weekly, about 7 weeks late (step 00f) | Real for White maize and Rice (foreign). Comparison only for the others |
| **AFEX exchange** (via Mansa Markets) | Daily exchange prices: maize, sorghum, soybean, rice (paddy), cocoa, ginger, cashew, sesame | Daily (step 00e) | Real for Yellow maize (AFEX does not state the maize type). Comparison only for the others |
| **Web price search** (Claude API with web search) | Named, dated wholesale prices found online | Every 3 hours, plus a daily sweep | Real only after vetting (step 00c), and only for commodities with no fresh partner price |
| **Agrolinking field collection** | Wheat, across 10 states | Stopped 13 April 2026 | Real |

**Supporting data, all real:** inflation and the official US$ rate from the **Central Bank of Nigeria** (step 00g), petrol prices from WFP, NOAA's El Niño index, world benchmark prices for maize, wheat, rice, soybeans and cocoa (Mansa Markets, step 00e), and weather (NASA POWER history plus a 16-day Open-Meteo forecast for 6 zones, step 00). After the latest real figure each series is carried forward flat, never projected. Until October 2026 these files ran on as smooth projections.

**Estimates, always labelled:** World Bank Real-Time Prices model rows, prices interpolated between two real prices, synthetic rows rebuilt from international prices × FX, and the platform's own daily model price.

The list of sources that count as real lives in one place: `PARTNER_SOURCES` and `REAL_SOURCES` in `config/settings.py`.

---

## Daily pipeline

`.github/workflows/daily_pipeline.yml` runs at **04:17 UTC (05:17 Lagos)**. A backup slot at **06:43 UTC** runs only if the first one didn't happen. GitHub drops scheduled jobs at busy times, so a gate job checks for today's commit before doing anything. The run can also be started by hand from the Actions tab.

| Step | Script | What it does |
|---|---|---|
| 00 | `11_external_factors.py` | Live FX and weather |
| 00b | `10_web_price_search.py` | Daily web price sweep, saved to Postgres `web_price_estimates` |
| 00c | `14_promote_web_prices.py` | Vets web prices; accepted ones go to `data/raw/web_prices.csv` |
| 00d | `refresh_wfp_hdx.py` | Downloads and validates the latest WFP file |
| 00e | `fetch_afex_prices.py` | AFEX exchange prices to `data/raw/afex_prices.csv` |
| 00f | `fetch_fao_prices.py` | FEWS NET wholesale and NBS retail prices to `data/raw/fao_fpma_prices.csv` |
| 00g | `fetch_macro.py` | CBN inflation and exchange rate, WFP petrol: rebuilds `inflation.csv`, `fx_rates.csv` and `fuel_prices.csv` |
| 01 | `01_ingest.py` | Merges all sources into `data/processed/agrolinking_master.csv`, ranked by source priority |
| 02 | `02_clean.py` | Fills gaps between real prices (labelled), drops outdated estimates; never edits real prices |
| 03 | `03_features.py` | Per-commodity feature tables |
| 04 | `04_train.py` | Trains the ML ensemble (kept for comparison) |
| 04b | `13_select_forecaster.py` | Backtests every forecast method on real prices and picks each commodity's method |
| 05 | `05_forecast.py` | Forecasts 6 horizons with ranges |
| 06 | `06_validate.py` | Checks forecasts against the latest real price; writes CSV and Postgres |
| 06b | `15_track_accuracy.py` | Scores past published forecasts against the real prices that arrived |
| 07 | `07_zonal_forecast.py` | State and zone prices |
| 08 | `08_intelligence.py` | Food price index, movers, early warnings, arbitrage, confidence |
| 08b | `price_drivers.py` | Price-driver cards per commodity (weather, season, climate, fuel, exchange rate, inflation, world price), served at `/drivers` |
| 09 | `09_staleness_check.py` | Flags commodities without a recent real price |
| 09b | `16_cross_check.py` | Our prices vs AFEX and web wholesale prices (agree / check / disagree) |
| | `data_status.py` | Plain-language real-data status at the top of the run page |
| | `quality_gate.py` | Blocks publishing if outputs contain NaN, conflict markers or missing commodities |
| | (commit) | Commits data and outputs to `main` |

**Other workflows:**
- `web_price_stream.yml`, every 3 hours at :41: a focused web search for commodities without a fresh partner price, plus two others in turn, rotating through exchange, official, northern-market, southern-market and news sources.
- `probe_ncx.yml`: a research-only probe, not part of the pipeline.

---

## Forecasting

- **Champion method per commodity (`13_select_forecaster.py`, `drift_models.py`).** The candidates are: price holds (naive), rise with food inflation, recent trend, a trend/inflation mix, and seasonal patterns (with or without inflation). Each is tested by rolling backtest on real prices at 1, 2, 3 and 6 months ahead. A challenger must beat "price holds" by 0.3 points to win. When "price holds" wins by less than 0.3 points, rising with half of food inflation is used instead, so that equally accurate forecasts don't show a flat line.
- **Long-range blend.** Some methods win near term and others at 6 months. Where a real pattern (seasonal or trend) beats the champion at 6 months by at least 1 error point, the forecast follows the champion for 3 months and moves to that pattern by month 6. White maize, for example, holds through the harvest, then follows its lean-season rise. Its 6-month accuracy improved from 68.5% to 73.8%.
- **Ranges** come from each commodity's measured error at each horizon.
- **The ML ensemble** (ARIMA, Prophet, XGBoost) is still trained and stored for comparison. It is no longer published, because the backtest showed it lost to simpler methods. `FORECAST_CHAMPION_MODE` defaults to `live`.
- **Accuracy is measured, not assumed.** Accuracy = 100 minus mean absolute % error against real prices. In the October 2026 backtest, mean 1-month accuracy is about 93%, and 9 of 19 commodities reach 95% or better. `/accuracy` serves the current backtest plus the live record of published forecasts.

## Validation and data quality

- **Reference price:** the latest real price no more than 45 days old. The hand-typed `MANUAL_PRICES` in `06_validate.py` are only a last-resort fallback.
- **Web prices** are promoted only when all of these hold: medium or high confidence; a wholesale or exchange price; a named URL; a dated source no more than 21 days old; newer than our latest price; within a sanity band; and a second website agrees when the move is over 15%. When a commodity has no recent partner price, the band is centred on our last accepted web price or a sibling commodity (yellow maize = white maize × 1.023), never a World Bank estimate.
- **Human review lists:**
  - `data/raw/web_prices_withdrawn.csv` removes a specific bad web price.
  - `data/raw/web_sources_blocked.csv` blocks a whole website.
- **Cross-check:** the daily `/crosscheck` report flags commodities where independent prices disagree with ours by more than 30%.

## State prices

State price = national price × that state's price gap (`pipeline/state_factors.py`).
- **Measured gaps** come from FEWS NET weekly wholesale prices for 2023–2026 where FEWS NET has the market (Lagos, Kano, Oyo, Borno, Kaduna). Elsewhere they come from WFP wholesale prices for 2019–2023.
- **Estimated gaps** come from the fixed table `data/external/state_price_differentials.csv` for everything else.

`/zonal/{commodity}` and `/history/{commodity}?state=Lagos` report which kind each price is (`price_basis`).

---

## API

FastAPI, `api.py`. Interactive reference: [pis-api.agrolinking.com/docs](https://pis-api.agrolinking.com/docs).

| Area | Endpoints |
|---|---|
| Status | `GET /`, `/health`, `/meta` (sources, markets and accuracy, counted from the data) |
| Prices and forecasts | `/summary`, `/commodities`, `/forecasts/latest`, `/forecasts/{commodity}`, `/forecasts/{commodity}/{horizon}`, `/prices/kg`, `/prices/kg/{commodity}` |
| History | `/history/{commodity}` (`days`, `resolution`, `state`; each point labelled with its source), `/history/compare`, `/history/fpi` |
| States and trade | `/zonal/latest`, `/zonal/{commodity}`, `/spreads`, `/arbitrage`, `/arbitrage/{commodity}`, `/routes`, `/routes/{origin}/{destination}`, `/supply`, `/supply/{zone}` |
| Intelligence | `/intelligence/latest`, `/index/food`, `/index/volatility`, `/outlook/30d`, `/movers`, `/confidence`, `/alerts/early-warning`, `/shortage-surplus`, `/seasonality/{commodity}`, `/factors` |
| Price drivers | `/drivers` (all commodities), `/drivers/{commodity}` (full cards with sources and dates). The older `/factors` is superseded |
| Data quality | `/accuracy`, `/crosscheck` |
| Alerts | `/alerts/latest` (WhatsApp/email-ready text); `/alerts/saved` (GET, POST), `/alerts/saved/{alert_id}` (DELETE) and `/alerts/check`, which need a Google sign-in token |

**Protections:**
- Requests are rate-limited to 60 a minute per visitor by default.
- Browser access is limited to the Agrolinking domains (`CORS_ALLOWED_ORIGINS`).
- Saved alerts verify the user's Google ID token (`auth.py`).

The API reads the pipeline's output files and, where configured, Postgres/TimescaleDB.

---

## Running it locally

```bash
git clone https://github.com/Agrolinking-Solutions/Agrolinking-Intelligence.git
cd Agrolinking-Intelligence
python -m venv venv
venv\Scripts\Activate.ps1        # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

**Core forecasting steps.** These use the data already in the repo and need no keys:

```bash
python pipeline/run_pipeline.py --skip-train   # external factors, ingest, clean, features, method selection, forecast, validate
python pipeline/quality_gate.py
```

**Full run, same order as GitHub.** Steps that need a key or the database skip themselves cleanly when it isn't set:

```bash
python pipeline/11_external_factors.py
python pipeline/10_web_price_search.py          # needs ANTHROPIC_API_KEY + database
python pipeline/14_promote_web_prices.py        # needs database
python pipeline/refresh_wfp_hdx.py
python pipeline/fetch_afex_prices.py
python pipeline/fetch_fao_prices.py
python pipeline/01_ingest.py
python pipeline/02_clean.py
python pipeline/03_features.py
python pipeline/04_train.py
python pipeline/13_select_forecaster.py
python pipeline/05_forecast.py
python pipeline/06_validate.py
python pipeline/15_track_accuracy.py
python pipeline/07_zonal_forecast.py
python pipeline/08_intelligence.py
python pipeline/09_staleness_check.py
python pipeline/16_cross_check.py
python pipeline/data_status.py
python pipeline/quality_gate.py
```

**API:** `python api.py`, then open http://localhost:8000/docs.

**Tests:** `pytest tests/ -v`

> **Don't commit local outputs.** GitHub Actions commits the official outputs every morning. Local runs rewrite `outputs/` and the master CSV, which then clash with the automated commit. Discard or move them before `git pull`.

### Environment variables

| Variable | Used by | Needed for |
|---|---|---|
| `TSDB_URL`, `PGPASSWORD` | pipeline, API | Postgres/TimescaleDB writes and reads (GitHub secrets) |
| `ANTHROPIC_API_KEY` (`ANTHROPIC_WORKSPACE_ID` if the key is workspace-scoped) | `10_web_price_search.py` | Web price search |
| `GOOGLE_CLIENT_ID` | `auth.py` | Saved alerts (Google sign-in) |
| `FORECAST_CHAMPION_MODE` | `05_forecast.py` | `live` (default), `shadow` or `off` |
| `STALENESS_ALERT_EMAIL`, `ALERT_EMAIL_FROM`, `SMTP_*` | `09_staleness_check.py` | Optional email alerts |
| `ALLOW_LOCAL_DEV_CORS` | `api.py` | Set to `1` to allow localhost browsers during development |
| `GEMINI_API_KEY` | `generate_commentary.py` | Optional market commentary tool (not in the daily run) |

---

## Routine operations

| Task | How |
|---|---|
| **Add Agricome prices** | Append rows to `data/raw/agricome_raw.csv`: commodity, week_start_date, price (NGN/MT), source `Agricom`, notes. They take effect on the next run. |
| **Remove a bad web price** | Add commodity, site and price to `data/raw/web_prices_withdrawn.csv`, and delete the row from `data/raw/web_prices.csv`. |
| **Block an unreliable website** | Add the site and reason to `data/raw/web_sources_blocked.csv`. |
| **Use another exchange or market-monitor commodity as a real price** | Add it to `AFEX_AS_REAL` or `FEWS_AS_REAL` in `pipeline/01_ingest.py`, after checking how its level compares with existing real data. |
| **Add a commodity** | Add it to `COMMODITIES` in `config/settings.py` and to its source mappings. New Postgres rows are created automatically. |
| **Rebuild the Food Price Index base** | `python pipeline/build_fpi_base.py` rebuilds `data/external/fpi_base_2025.csv` from real 2025 prices. The base is fixed on purpose, so only rebuild it when the 2025 history itself changes. |
| **Check today's data health** | Open the latest Daily Forecast Pipeline run on GitHub (status summary at the top), or call `/crosscheck` and `/accuracy`. |

## Deployment

- **API:** runs on Agrolinking's VPS (moved off Render in September 2026). It redeploys automatically when `main` changes; a deploy takes a few minutes.
- **Database:** PostgreSQL/TimescaleDB. Tables include prices, forecasts, commodities, locations, web_price_estimates, intelligence_metrics, users and alerts. Schema in `scripts/migration/schema.sql`; missing columns are added by the pipeline (`pipeline/pg_commodities.py`).
- **Frontend:** [pis.agrolinking.com](https://pis.agrolinking.com), maintained separately.
- **Legacy:** `dashboard/app.py` is the older Streamlit dashboard. `railway.toml` and `push_daily.ps1` predate GitHub Actions.

## Project structure

```
.github/workflows/   daily_pipeline.yml, web_price_stream.yml, probe_ncx.yml
config/settings.py   commodities, paths, price sources, model settings
data/raw/            source files: Agricome, WFP, AFEX, FAO/FEWS NET, web prices and review lists
data/external/       FX, weather, fuel, season calendar, zones, state price gaps
data/processed/      agrolinking_master.csv (every price with its source) and per-commodity features
pipeline/            the steps above, plus helpers (drift_models, freshness, state_factors, pg_commodities)
outputs/             forecasts (raw, validated, zonal), intelligence, alerts, backtest, logs
api.py, auth.py, db.py   API, Google sign-in check, database access
docs/                ARCHITECTURE, API, OPERATIONAL_GUIDE, SECURITY, FIXES_AND_IMPROVEMENTS
scripts/             Postgres migration, maintenance and research scripts
tests/               smoke tests
```

## Known gaps (October 2026)

- **Wheat** has no real price since 13 April 2026. The web has nothing current, so a field or trader source is needed.
- **Meat, fish and eggs** are shop prices; there is no wholesale source yet.
- **Agricome** prices are pasted by hand. A regular feed has been requested.
- **State gaps** for most cash crops and livestock are still estimates.
- **Old "Rice"** is due to be removed once the frontend drops its tab.
- Some documents in `docs/` predate the October 2026 changes. This README describes the current system.

## License

Proprietary. Built by Agrolinking Solutions, Nigeria. Data from Agricome Africa (attributed), WFP (CC-BY), FEWS NET and NBS via FAO FPMA, AFEX via Mansa Markets, and Agrolinking's own collection.

Contact: [agrolinking.com](https://agrolinking.com) · info@agrolinking.com

*Last updated: 15 October 2026*
