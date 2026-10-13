"""
Keeps the Postgres `commodities` table in step with config.settings.COMMODITIES.

Every Postgres write in the pipeline maps commodity names to ids with
`SELECT commodity_id, name FROM commodities` and silently skips names it
can't find. Adding a commodity to COMMODITIES (e.g. Rice (local) and
Rice (foreign)) would therefore never reach the database without a manual
migration. Calling ensure_commodities() first inserts any missing names.
Idempotent: existing rows are never changed.
"""

LIVESTOCK = {"Meat (beef)", "Meat (goat)", "Fish (dried)", "Eggs"}
UNITS = {"Eggs": "NGN/crate"}


# Columns added to `forecasts` after the production database was first
# created (scripts/migration/schema.sql lines with ALTER TABLE forecasts).
# Production never had those ALTERs applied, so every dual-write from 06
# failed with 'column "lower_ci" of relation "forecasts" does not exist'
# (seen 2026-10-13, the first runs that could log in at all). Additive only:
# ADD COLUMN IF NOT EXISTS never drops or changes existing data.
FORECAST_COLUMNS = [
    ("lower_ci", "NUMERIC"), ("upper_ci", "NUMERIC"), ("forecast_date", "DATE"),
    ("reference_price", "NUMERIC"), ("error_pct_before", "NUMERIC"),
    ("correction_applied", "TEXT"), ("last_known_price", "NUMERIC"),
    ("last_known_date", "DATE"), ("day_change_pct", "NUMERIC"), ("is_primary", "BOOLEAN"),
    ("state_price", "NUMERIC"), ("national_anchor_price", "NUMERIC"), ("yesterday_price", "NUMERIC"),
]


def ensure_forecast_columns(cur):
    """Add any of FORECAST_COLUMNS missing from the forecasts table. Returns the names added."""
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'forecasts'")
    existing = {r[0] for r in cur.fetchall()}
    missing = [(c, t) for c, t in FORECAST_COLUMNS if c not in existing]
    for c, t in missing:
        cur.execute(f"ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS {c} {t}")
    return [c for c, _ in missing]


def ensure_commodities(cur, commodities):
    """Insert any commodity missing from the table. Returns the names inserted."""
    cur.execute("SELECT name FROM commodities")
    existing = {r[0] for r in cur.fetchall()}
    missing = [c for c in commodities if c not in existing]
    for c in missing:
        cur.execute(
            "INSERT INTO commodities (name, category, unit) VALUES (%s, %s, %s) "
            "ON CONFLICT (name) DO NOTHING",
            (c, "livestock" if c in LIVESTOCK else "agricultural", UNITS.get(c, "NGN/MT")),
        )
    return missing
