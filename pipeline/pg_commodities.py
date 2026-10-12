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
