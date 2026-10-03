-- ═══════════════════════════════════════════════════════════════════════
-- Agrolinking Commodity Intelligence — TimescaleDB Schema
-- ═══════════════════════════════════════════════════════════════════════
--
-- This is the schema that migrate_to_postgres.py and pipeline/06_validate.py
-- (write_to_postgres) both assume already exists. It was previously created
-- ad hoc in a DB console — not committed anywhere — so there was no
-- reproducible way to recreate it (a lost/corrupted DB, or a second
-- environment like staging, had nothing to rebuild from). This file is
-- that source of truth now.
--
-- Idempotent: every statement uses IF NOT EXISTS / ON CONFLICT DO NOTHING,
-- so this is safe to re-run against an existing database.
--
-- Usage:
--   psql "$TSDB_URL" -f scripts/migration/schema.sql
--
-- ═══════════════════════════════════════════════════════════════════════
-- STEP 0 — Enable TimescaleDB
-- ═══════════════════════════════════════════════════════════════════════
-- The "function create_hypertable(unknown, unknown) does not exist" error
-- means this extension was never enabled on this specific database. On
-- Timescale Cloud the extension is installed but each database needs this
-- run once. On a different managed Postgres (Render/Railway/plain RDS)
-- this may not be available at all — if this statement errors with
-- "extension \"timescaledb\" is not available", you're on a Postgres
-- provider without Timescale support and need either a Timescale Cloud
-- instance or to drop the create_hypertable() calls below and rely on a
-- plain btree index on (time) instead (slower on large time-range scans,
-- but functionally correct).

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 1 — Dimension tables (small, rarely change)
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS commodities (
    commodity_id    SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,       -- must match config.settings.COMMODITIES exactly
    category        TEXT NOT NULL,              -- 'agricultural' | 'livestock'
    unit            TEXT NOT NULL DEFAULT 'NGN/MT'
);

CREATE TABLE IF NOT EXISTS locations (
    location_id     SERIAL PRIMARY KEY,
    state           TEXT NOT NULL UNIQUE,       -- 'National' for country-level rows, else state name
    zone            TEXT,                       -- geopolitical zone, NULL for 'National'
    country         TEXT NOT NULL DEFAULT 'Nigeria'
);

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 2 — Hypertables (time-series data — this is the Timescale part)
-- ═══════════════════════════════════════════════════════════════════════

-- Replaces wfp_food_prices_nga.csv + agricome_raw.csv + agrolinking_master.csv
CREATE TABLE IF NOT EXISTS prices (
    time            TIMESTAMPTZ NOT NULL,
    commodity_id    INT NOT NULL REFERENCES commodities(commodity_id),
    location_id     INT NOT NULL REFERENCES locations(location_id),
    price           NUMERIC NOT NULL,
    source          TEXT NOT NULL,              -- 'Agricome' | 'WFP' | 'Agrolinking_validated' | ...
    PRIMARY KEY (time, commodity_id, location_id, source)
);
SELECT create_hypertable('prices', 'time', if_not_exists => TRUE);

-- Replaces outputs/forecasts/forecast_*.json and validated/*.json.
-- location_id is now REQUIRED (not NULL) — every row is either the
-- 'National' location or a specific state. Forward curve for one
-- commodity+location = all horizon_days rows for the same (commodity_id,
-- location_id, time).
--
-- horizon_days now holds more than the original 6 checkpoints: 1-7 (daily,
-- for the downloadable report's "today, tomorrow, ..." first week), 14, 21
-- (added for the report's "...1wk, 2wk, 3wk, 1mo..." step), 30, 90, 180.
-- This did not require any pipeline/model change — 05_forecast.py already
-- computes a full daily trajectory internally for every horizon bucket
-- (e.g. the "6_months" bucket already contains 180 individual daily
-- values with confidence bands); the pipeline just wasn't writing more
-- than each bucket's single end-of-horizon value to Postgres before.
CREATE TABLE IF NOT EXISTS forecasts (
    time              TIMESTAMPTZ NOT NULL,     -- forecast generation date
    commodity_id      INT NOT NULL REFERENCES commodities(commodity_id),
    location_id       INT NOT NULL REFERENCES locations(location_id),
    horizon_days      INT NOT NULL,             -- 1-7, 14, 21, 30, 90, 180
    predicted_price   NUMERIC NOT NULL,
    lower_ci          NUMERIC,                  -- confidence band, added alongside the daily points
    upper_ci          NUMERIC,
    model_confidence  NUMERIC,
    validated         BOOLEAN NOT NULL DEFAULT FALSE,
    error_pct         NUMERIC,
    PRIMARY KEY (time, commodity_id, location_id, horizon_days)
);
SELECT create_hypertable('forecasts', 'time', if_not_exists => TRUE);

-- Idempotent — adds the columns if this schema.sql is being re-run against
-- a database that already has the table from before this change.
ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS lower_ci NUMERIC;
ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS upper_ci NUMERIC;

-- Replaces the per-commodity parts of outputs/intelligence/intelligence_*.json
-- (volatility_index.per_commodity, arbitrage). No location_id: the current
-- 08_intelligence.py computes these at the national basket level only —
-- add the column later if per-state intelligence metrics get computed.
--
-- Guarded DROP/CREATE instead of plain CREATE TABLE IF NOT EXISTS: an
-- earlier ad hoc session (before this file existed) already created an
-- intelligence_metrics table under the original schema sketch, with
-- different column names (fpi, volatility, arbitrage_flag, commentary).
-- IF NOT EXISTS alone would see that table and silently keep the old,
-- incompatible columns instead of these — which is exactly what happened
-- the first time this file ran. The check only replaces the table when
-- the new column is missing, so this stays a safe no-op on every run
-- after the first, and never touches a table that's already correct.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'intelligence_metrics' AND column_name = 'volatility_pct'
    ) THEN
        DROP TABLE IF EXISTS intelligence_metrics;
        CREATE TABLE intelligence_metrics (
            time                    TIMESTAMPTZ NOT NULL,
            commodity_id            INT NOT NULL REFERENCES commodities(commodity_id),
            volatility_pct          NUMERIC,         -- 30-day rolling coefficient of variation
            net_arbitrage_ngn_kg    NUMERIC,         -- from outputs/intelligence: arbitrage[commodity]
            arbitrage_viable        BOOLEAN,         -- net_arbitrage_ngn_kg > 0
            PRIMARY KEY (time, commodity_id)
        );
        PERFORM create_hypertable('intelligence_metrics', 'time', if_not_exists => TRUE);
    END IF;
END $$;

-- Replaces the basket-wide parts of intelligence_*.json (food_price_index,
-- volatility_index top-level value, outlook_30d, model_confidence). One
-- row per pipeline run, not per commodity — these are aggregate indices.
CREATE TABLE IF NOT EXISTS market_index (
    time                TIMESTAMPTZ NOT NULL PRIMARY KEY,
    fpi                 NUMERIC,                 -- food_price_index.value (base 2025=100)
    fpi_mom_change      NUMERIC,                 -- food_price_index.mom_change
    volatility_index     NUMERIC,                 -- volatility_index.value (aggregate)
    outlook_30d_pct      NUMERIC,                 -- outlook_30d.avg_pct_change
    model_confidence_pct NUMERIC,                 -- model_confidence.avg_pct
    commentary            TEXT                     -- from pipeline/generate_commentary.py, when run
);
SELECT create_hypertable('market_index', 'time', if_not_exists => TRUE);

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 3 — Forward-looking tables (Phase 2: alerts, portfolio tracking)
-- ═══════════════════════════════════════════════════════════════════════
-- users/alerts are now live — api.py's /alerts/* endpoints require a
-- verified Google login (see auth.py) and store here instead of the old
-- world-readable outputs/alerts/saved_alerts.json file. user_id comes
-- from looking up (or creating) a row by the verified email in the
-- Google ID token — see docs/SECURITY.md for why this had to happen
-- before alerts could be considered a real feature rather than a
-- liability (anyone could list/delete anyone's alerts before this).

CREATE TABLE IF NOT EXISTS users (
    user_id         SERIAL PRIMARY KEY,
    email           TEXT UNIQUE,
    whatsapp_number TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id         SERIAL PRIMARY KEY,
    user_id          INT NOT NULL REFERENCES users(user_id),
    commodity_id     INT NOT NULL REFERENCES commodities(commodity_id),
    condition        TEXT NOT NULL,              -- 'above' | 'below'
    threshold        NUMERIC NOT NULL,
    label            TEXT,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at  TIMESTAMPTZ,
    triggered_at     TIMESTAMPTZ,
    triggered_price  NUMERIC
);

-- Idempotent — in case schema.sql is re-run after alerts already existed
-- under the original, narrower column set.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS label TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS triggered_price NUMERIC;

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    holding_id      SERIAL PRIMARY KEY,
    user_id         INT NOT NULL REFERENCES users(user_id),
    commodity_id    INT NOT NULL REFERENCES commodities(commodity_id),
    quantity        NUMERIC NOT NULL,
    entry_price     NUMERIC NOT NULL,
    entry_date      DATE NOT NULL
);

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 4 — Seed dimension data
-- ═══════════════════════════════════════════════════════════════════════
-- Must match config/settings.py COMMODITIES and data/external/zones_config.json
-- exactly, or migrate_to_postgres.py silently skips rows for anything that
-- doesn't match (it logs skipped commodity names — watch for that warning).

INSERT INTO commodities (name, category, unit) VALUES
    ('Hibiscus',       'agricultural', 'NGN/MT'),
    ('Sesame',         'agricultural', 'NGN/MT'),
    ('Ginger',         'agricultural', 'NGN/MT'),
    ('Cocoa',          'agricultural', 'NGN/MT'),
    ('Soybeans',       'agricultural', 'NGN/MT'),
    ('Cashew Nuts',    'agricultural', 'NGN/MT'),
    ('Sorghum',        'agricultural', 'NGN/MT'),
    ('Beans (white)',  'agricultural', 'NGN/MT'),
    ('Beans (red)',    'agricultural', 'NGN/MT'),
    ('Maize (white)',  'agricultural', 'NGN/MT'),
    ('Maize (yellow)', 'agricultural', 'NGN/MT'),
    ('Wheat',          'agricultural', 'NGN/MT'),
    ('Rice',           'agricultural', 'NGN/MT'),
    ('Meat (beef)',    'livestock',    'NGN/MT'),
    ('Meat (goat)',    'livestock',    'NGN/MT'),
    ('Fish (dried)',   'livestock',    'NGN/MT'),
    ('Eggs',           'livestock',    'NGN/crate')
ON CONFLICT (name) DO NOTHING;

-- National = country-level rows (what the pipeline calls "national anchors").
-- Every other row is one of the 12 tracked states across 6 zones.
INSERT INTO locations (state, zone) VALUES
    ('National',  NULL),
    ('Kano',      'North West'),
    ('Kaduna',    'North West'),
    ('Plateau',   'North Central'),
    ('Kogi',      'North Central'),
    ('Adamawa',   'North East'),
    ('Borno',     'North East'),
    ('Oyo',       'South West'),
    ('Lagos',     'South West'),
    ('Anambra',   'South East'),
    ('Imo',       'South East'),
    ('Rivers',    'South South'),
    ('Delta',     'South South')
ON CONFLICT (state) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 5 — Verify
-- ═══════════════════════════════════════════════════════════════════════

SELECT 'commodities' AS table_name, count(*) AS rows FROM commodities
UNION ALL SELECT 'locations', count(*) FROM locations
UNION ALL SELECT 'prices', count(*) FROM prices
UNION ALL SELECT 'forecasts', count(*) FROM forecasts
UNION ALL SELECT 'intelligence_metrics', count(*) FROM intelligence_metrics
UNION ALL SELECT 'market_index', count(*) FROM market_index
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'alerts', count(*) FROM alerts;
