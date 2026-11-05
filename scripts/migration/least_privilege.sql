-- Two database logins with only the rights each side uses (Nov 2026 security
-- review). Run ONCE, as the database owner/admin, after checking the daily
-- run's "Database security" summary (pipeline/db_security_check.py):
--
--   psql "$ADMIN_URL" -v pipeline_password='...' -v api_password='...' \
--        -f scripts/migration/least_privilege.sql
--
-- Choose two new long random passwords; they are passed in, never stored here.
-- Then:
--   1. GitHub repository secrets: PGPASSWORD = the pipeline password, and
--      TSDB_URL with user pis_pipeline (and ?sslmode=require once SSL is on).
--   2. The API server's environment: the same, with user pis_api.
--   3. Watch the next daily run: "Database security" should show no superuser
--      and no user tables for the pipeline; "Dual-write OK" in step 06.
--   4. Change the admin password, which has been in the old secrets.
--
-- Rights below come from every query in the code (Nov 2026):
--   pipeline  writes prices, forecasts (06, 07), intelligence_metrics,
--             market_index (08), web_price_estimates (10), new commodities;
--             reads locations. Never touches users, alerts or portfolios.
--   API       reads the price tables; manages users, alerts and portfolios.
-- Schema changes (ALTER/CREATE) stay with the admin: the pipeline's
-- ensure_* helpers (pipeline/pg_commodities.py) only alter a table when a
-- column is missing, which a complete schema.sql never leaves.

\set ON_ERROR_STOP on

CREATE ROLE pis_pipeline LOGIN PASSWORD :'pipeline_password';
CREATE ROLE pis_api LOGIN PASSWORD :'api_password';

DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO pis_pipeline, pis_api', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO pis_pipeline, pis_api;

-- pipeline (GitHub Actions)
GRANT SELECT, INSERT, UPDATE, DELETE ON prices, forecasts TO pis_pipeline;
GRANT SELECT, INSERT, UPDATE ON intelligence_metrics, market_index, web_price_estimates TO pis_pipeline;
GRANT SELECT, INSERT ON commodities TO pis_pipeline;
GRANT SELECT ON locations TO pis_pipeline;
GRANT USAGE, SELECT ON SEQUENCE commodities_commodity_id_seq,
                                web_price_estimates_estimate_id_seq TO pis_pipeline;

-- API server
GRANT SELECT ON prices, forecasts, commodities, locations, intelligence_metrics, market_index,
                web_price_estimates TO pis_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON users, alerts, portfolio_holdings TO pis_api;
GRANT USAGE, SELECT ON SEQUENCE users_user_id_seq, alerts_alert_id_seq,
                                portfolio_holdings_holding_id_seq TO pis_api;

-- Nobody else gets anything by default on new tables
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
