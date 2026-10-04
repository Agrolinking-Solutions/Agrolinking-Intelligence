"""
One-off historical backfill: flat files -> TimescaleDB Postgres.

Run once against schema.sql (in this folder) to load your pre-existing
history into Postgres. It is NOT the ongoing sync — pipeline/06_validate.py
already dual-writes each day's national prices/forecasts to Postgres on
every pipeline run. This script's job is everything that dual-write never
covered: years of historical prices, past validated forecasts, zonal
(state-level) forecast history, and intelligence metric history.

Populates:
  - prices                from data/processed/agrolinking_master.csv (national only —
                           there is no historical state-level price data, only
                           interpolated/forecast state prices, see below)
  - forecasts              from outputs/forecasts/validated/forecast_validated_*.json
                           (national) and outputs/forecasts/zonal/zonal_forecast_*.json
                           (state-level, one row per state per horizon)
  - intelligence_metrics   per-commodity volatility + arbitrage, from
                           outputs/intelligence/intelligence_*.json
  - market_index           basket-wide FPI/volatility/outlook/confidence,
                           one row per day, from the same intelligence files

Idempotent — every insert uses ON CONFLICT DO NOTHING, so re-running this
is always safe (it just skips rows that are already there).

Connecting — two ways:

  1. Discrete PG* variables (recommended — no URL-encoding pitfalls).
     A password containing '@', ':', '/' etc. breaks a combined connection
     URL unless every reserved character is percent-encoded by hand, which
     is exactly the kind of thing that fails silently. These are read
     automatically by libpq (both psql and psycopg2 use it), so there is
     nothing to encode — the password is passed as a plain, un-parsed string:

         $env:PGHOST     = "ji5ecism7r.m6f48luj98.tsdb.cloud.timescale.com"
         $env:PGPORT     = "38941"
         $env:PGUSER     = "tsdbadmin"
         $env:PGPASSWORD = "<password, no encoding needed>"
         $env:PGDATABASE = "tsdb"
         $env:PGSSLMODE  = "require"
         python scripts/migration/migrate_to_postgres.py

  2. TSDB_URL as a single connection string (backward compatible — this is
     what the GitHub Actions secret already uses). If your password has
     no reserved URL characters this is fine as-is; if it does, percent-
     encode them ('@' -> '%40', ':' -> '%3A', '/' -> '%2F', etc.):

         $env:TSDB_URL = "postgres://tsdbadmin:...@...tsdb.cloud.timescale.com:PORT/tsdb?sslmode=require"
         python scripts/migration/migrate_to_postgres.py

Either way, never hardcode the credential in this file — it's committed to git.

Usage:
    pip install psycopg2-binary pandas --break-system-packages
    psql "$TSDB_URL" -f scripts/migration/schema.sql   # once, before this
    python scripts/migration/migrate_to_postgres.py
"""
import os
import math
import json
import glob
import sys
from datetime import datetime, timedelta
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

DB_URL = os.environ.get("TSDB_URL")
# "is not None" rather than truthiness — an empty-string PGPASSWORD is a
# legitimate local trust-auth setup, not "unset". A plain truthiness check
# would treat that as not-configured and fall through to the DB_URL branch.
PG_DISCRETE_VARS_SET = os.environ.get("PGHOST") is not None and os.environ.get("PGPASSWORD") is not None
if not DB_URL and not PG_DISCRETE_VARS_SET:
    sys.exit(
        "No database connection configured. Set either TSDB_URL, or the "
        "discrete PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE/PGSSLMODE "
        "variables (recommended — see the top of this file for why)."
    )

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MASTER_CSV    = os.path.join(BASE_DIR, "data", "processed", "agrolinking_master.csv")
VALIDATED_DIR = os.path.join(BASE_DIR, "outputs", "forecasts", "validated")
ZONAL_DIR     = os.path.join(BASE_DIR, "outputs", "forecasts", "zonal")
INTEL_DIR     = os.path.join(BASE_DIR, "outputs", "intelligence")

HORIZON_DAYS = {
    # "daily" maps to 0 — see the matching note in pipeline/06_validate.py's
    # HORIZON_DAYS_MAP. 0 avoids colliding with the "tomorrow" point.
    "daily": 0,
    "weekly": 7, "2_weeks": 14,
    "monthly": 30, "3_months": 90, "6_months": 180,
}

# Same daily-resolution extension as the two daily dual-write functions
# (06_validate.py, 07_zonal_forecast.py) — kept in sync manually since
# each file's horizon-bucket shape differs slightly. See 06_validate.py
# for the full rationale.
EXTRA_DAILY_POINTS = [(d, "weekly") for d in (1, 2, 3, 4, 5, 6)] + [(21, "monthly")]


def _parse_date(d):
    if isinstance(d, datetime):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d")


def _day_offset_nested(fc_data, gen_date, day_offset, bucket):
    """For national forecast JSON, where values live under h_data['ensemble']."""
    h_data = fc_data.get("horizons", {}).get(bucket)
    if not h_data:
        return None
    target = (gen_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
    dates = h_data.get("dates", [])
    if target not in dates:
        return None
    idx = dates.index(target)
    ens = h_data.get("ensemble", {})
    vals = ens.get("values", [])
    if idx >= len(vals):
        return None
    lo = ens.get("lower_ci", vals)
    hi = ens.get("upper_ci", vals)
    return (vals[idx], lo[idx] if idx < len(lo) else None, hi[idx] if idx < len(hi) else None, target)


def _checkpoint_nested(fc_data, h_name):
    h_data = fc_data.get("horizons", {}).get(h_name)
    if not h_data:
        return None
    vals = h_data.get("ensemble", {}).get("values", [])
    detail = h_data.get("forecast_end_detail", {})
    price = detail.get("price")
    if price is None:
        price = vals[-1] if vals else h_data.get("forecast_price")
    if price is None:
        return None
    date_str = detail.get("date") or (h_data.get("dates", [None])[-1])
    lo = h_data.get("ensemble", {}).get("lower_ci", vals)
    hi = h_data.get("ensemble", {}).get("upper_ci", vals)
    return (price, lo[-1] if lo else None, hi[-1] if hi else None, date_str)


def _day_offset_flat(cd, gen_date, day_offset, bucket):
    """For zonal forecast JSON, where values live directly on h_data (no 'ensemble' nesting)."""
    h_data = cd.get("horizons", {}).get(bucket)
    if not h_data:
        return None
    target = (gen_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
    dates = h_data.get("dates", [])
    if target not in dates:
        return None
    idx = dates.index(target)
    vals = h_data.get("values", [])
    if idx >= len(vals):
        return None
    lo = h_data.get("lower_ci", vals)
    hi = h_data.get("upper_ci", vals)
    return (vals[idx], lo[idx] if idx < len(lo) else None, hi[idx] if idx < len(hi) else None, target)


def _checkpoint_flat(cd, h_name):
    h_data = cd.get("horizons", {}).get(h_name)
    if not h_data:
        return None
    vals = h_data.get("values", [])
    price = h_data.get("end_price")
    if price is None:
        price = vals[-1] if vals else None
    if price is None:
        return None
    dates = h_data.get("dates", [])
    date_str = dates[-1] if dates else None
    lo = h_data.get("lower_ci", vals)
    hi = h_data.get("upper_ci", vals)
    return (price, lo[-1] if lo else None, hi[-1] if hi else None, date_str)

# Real price data only — skip synthetic/interpolated filler rows and
# raw "forecast" placeholder rows. This mirrors the same REAL_SOURCES
# logic the pipeline itself uses for anchor selection, extended to
# include validated_actual since those are confirmed-accurate prices.
REAL_RECORD_TYPES = {"historical", "validated_actual"}


def _reject_non_finite(token):
    raise ValueError(f"non-finite value {token} in JSON")


def load_json_safe(path):
    """
    Parse JSON, rejecting NaN/Infinity. A full historical backfill globs
    every file under outputs/, including days where the pipeline produced
    corrupted NaN output (e.g. 2026-09-26, before the ba28bf9 conflict-marker
    bug was fixed — see docs/FIXES_AND_IMPROVEMENTS.md). Returns None and
    prints a warning instead of inserting garbage into Postgres.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f, parse_constant=_reject_non_finite)
    except ValueError as e:
        print(f"  SKIPPING {os.path.basename(path)}: {e}")
        return None


def get_lookup_maps(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT commodity_id, name FROM commodities")
        commodity_map = {name: cid for cid, name in cur.fetchall()}
        cur.execute("SELECT location_id, state FROM locations")
        location_map = {state: lid for lid, state in cur.fetchall()}
    if "National" not in location_map:
        sys.exit("No 'National' row in locations — run scripts/migration/schema.sql first.")
    return commodity_map, location_map


def migrate_prices(conn, commodity_map, national_id):
    print("Loading master.csv...")
    df = pd.read_csv(MASTER_CSV, parse_dates=["date"])
    total = len(df)
    df = df[df["record_type"].isin(REAL_RECORD_TYPES)]
    df = df.dropna(subset=["date", "price_ngn_mt"])
    print(f"  {len(df):,} real/validated rows to migrate (out of {total:,} total)")

    rows = []
    skipped_unknown_commodity = set()
    for _, r in df.iterrows():
        cid = commodity_map.get(r["commodity"])
        if cid is None:
            skipped_unknown_commodity.add(r["commodity"])
            continue
        rows.append((
            r["date"],
            cid,
            national_id,  # master.csv is national-level — no NULL, see get_lookup_maps
            float(r["price_ngn_mt"]),
            str(r["data_source"]),
        ))

    if skipped_unknown_commodity:
        print(f"  WARNING — skipped rows for commodities not in the commodities table: "
              f"{skipped_unknown_commodity}")

    print(f"  Inserting {len(rows):,} price rows...")
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO prices (time, commodity_id, location_id, price, source)
            VALUES %s
            ON CONFLICT (time, commodity_id, location_id, source) DO NOTHING
            """,
            rows,
            page_size=1000,
        )
    conn.commit()
    print("  Done.")


def migrate_forecasts(conn, commodity_map, national_id):
    files = sorted(glob.glob(os.path.join(VALIDATED_DIR, "forecast_validated_*.json")))
    print(f"Found {len(files)} validated forecast files.")

    rows = []
    skipped_unknown_commodity = set()
    for path in files:
        data = load_json_safe(path)
        if data is None:
            continue
        forecasts = data.get("forecasts", data)

        for commodity_name, fc in forecasts.items():
            cid = commodity_map.get(commodity_name)
            if cid is None:
                skipped_unknown_commodity.add(commodity_name)
                continue

            gen_date_raw = fc.get("last_known_date") or fc.get("run_date")
            if not gen_date_raw:
                continue
            gen_date = _parse_date(gen_date_raw)
            validation = fc.get("validation", {})
            validated = validation.get("status") == "validated"
            error_pct = validation.get("error_pct_after")
            if error_pct is not None and not math.isfinite(error_pct):
                error_pct = None
            confidence = fc.get("model_confidence")

            reference_price = validation.get("reference_price")
            if reference_price is not None and not math.isfinite(reference_price):
                reference_price = None
            error_pct_before = validation.get("error_pct_before")
            if error_pct_before is not None and not math.isfinite(error_pct_before):
                error_pct_before = None
            correction_applied = validation.get("correction_applied")
            last_known_price = fc.get("last_known_price")
            if last_known_price is not None and not math.isfinite(last_known_price):
                last_known_price = None
            last_known_date = fc.get("last_known_date")

            def add_row(h_days, result):
                if not result:
                    return
                price, lo, hi, fdate = result
                if price is None or not math.isfinite(price) or price <= 0:
                    return
                lo = lo if (lo is not None and math.isfinite(lo)) else None
                hi = hi if (hi is not None and math.isfinite(hi)) else None
                rows.append((
                    gen_date_raw, cid, national_id, h_days,
                    float(price), lo, hi, fdate, confidence, validated, error_pct,
                    reference_price, error_pct_before, correction_applied,
                    last_known_price, last_known_date,
                ))

            for h_name, h_days in HORIZON_DAYS.items():
                add_row(h_days, _checkpoint_nested(fc, h_name))
            for day_offset, bucket in EXTRA_DAILY_POINTS:
                add_row(day_offset, _day_offset_nested(fc, gen_date, day_offset, bucket))

    if skipped_unknown_commodity:
        print(f"  WARNING — skipped forecast rows for commodities not in the commodities table: "
              f"{skipped_unknown_commodity}")

    print(f"  Inserting {len(rows):,} forecast rows...")
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO forecasts
                (time, commodity_id, location_id, horizon_days,
                 predicted_price, lower_ci, upper_ci, forecast_date,
                 model_confidence, validated, error_pct,
                 reference_price, error_pct_before, correction_applied,
                 last_known_price, last_known_date)
            VALUES %s
            ON CONFLICT (time, commodity_id, location_id, horizon_days) DO NOTHING
            """,
            rows,
            page_size=1000,
        )
    conn.commit()
    print("  Done.")


def migrate_zonal_forecasts(conn, commodity_map, location_map):
    """
    State-level forecast history from outputs/forecasts/zonal/*.json.
    Same forecasts table as national data, keyed by each state's
    location_id instead of National. This is what was previously missing —
    the location_id column existed for this from the start but nothing
    populated it, so every zonal forecast stayed trapped in JSON files.

    There's no equivalent zonal *prices* migration: the master CSV only
    has national-level real price observations. State prices in the zonal
    JSON are the pipeline's own interpolated estimates (state_price),
    i.e. already a forecast, not a raw observation — they belong in
    forecasts, not prices.
    """
    files = sorted(glob.glob(os.path.join(ZONAL_DIR, "zonal_forecast_*.json")))
    print(f"Found {len(files)} zonal forecast files.")

    rows = []
    skipped_unknown_commodity = set()
    skipped_unknown_state = set()
    for path in files:
        data = load_json_safe(path)
        if data is None:
            continue
        run_date_raw = data.get("run_date", "")
        if not run_date_raw:
            continue
        run_date = _parse_date(run_date_raw)

        for zone_data in data.get("zones", {}).values():
            for state_name, state_data in zone_data.get("states", {}).items():
                loc_id = location_map.get(state_name)
                if loc_id is None:
                    skipped_unknown_state.add(state_name)
                    continue

                for commodity_name, cd in state_data.items():
                    cid = commodity_map.get(commodity_name)
                    if cid is None:
                        skipped_unknown_commodity.add(commodity_name)
                        continue

                    day_change_pct = cd.get("day_change_pct")
                    if day_change_pct is not None and not math.isfinite(day_change_pct):
                        day_change_pct = None
                    is_primary = bool(cd.get("is_primary", False))
                    state_price = cd.get("state_price")
                    if state_price is not None and not math.isfinite(state_price):
                        state_price = None

                    def add_row(h_days, result):
                        if not result:
                            return
                        price, lo, hi, fdate = result
                        if price is None or not math.isfinite(price) or price <= 0:
                            return
                        lo = lo if (lo is not None and math.isfinite(lo)) else None
                        hi = hi if (hi is not None and math.isfinite(hi)) else None
                        rows.append((
                            run_date_raw, cid, loc_id, h_days,
                            float(price), lo, hi, fdate, None, False, None,
                            None, None, None, None, None, day_change_pct, is_primary, state_price,
                        ))

                    for h_name, h_days in HORIZON_DAYS.items():
                        add_row(h_days, _checkpoint_flat(cd, h_name))
                    for day_offset, bucket in EXTRA_DAILY_POINTS:
                        add_row(day_offset, _day_offset_flat(cd, run_date, day_offset, bucket))

    if skipped_unknown_state:
        print(f"  WARNING — skipped rows for states not in the locations table: "
              f"{skipped_unknown_state}")
    if skipped_unknown_commodity:
        print(f"  WARNING — skipped rows for commodities not in the commodities table: "
              f"{skipped_unknown_commodity}")

    print(f"  Inserting {len(rows):,} zonal forecast rows...")
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO forecasts
                (time, commodity_id, location_id, horizon_days,
                 predicted_price, lower_ci, upper_ci, forecast_date,
                 model_confidence, validated, error_pct,
                 reference_price, error_pct_before, correction_applied,
                 last_known_price, last_known_date,
                 day_change_pct, is_primary, state_price)
            VALUES %s
            ON CONFLICT (time, commodity_id, location_id, horizon_days) DO NOTHING
            """,
            rows,
            page_size=1000,
        )
    conn.commit()
    print("  Done.")


def migrate_intelligence(conn, commodity_map):
    """
    outputs/intelligence/intelligence_*.json splits into two shapes:
      - per-commodity metrics (volatility, arbitrage)  -> intelligence_metrics
      - basket-wide indices (FPI, outlook, confidence)  -> market_index, one row/day

    Neither was migrated before — FPI history, volatility trends, and
    arbitrage history all lived only in these JSON files.
    """
    files = sorted(glob.glob(os.path.join(INTEL_DIR, "intelligence_*.json")))
    print(f"Found {len(files)} intelligence files.")

    metric_rows = []
    index_rows = []
    skipped_unknown_commodity = set()
    for path in files:
        data = load_json_safe(path)
        if data is None:
            continue
        run_date = data.get("run_date", "")

        fpi     = data.get("food_price_index", {})
        vol     = data.get("volatility_index", {})
        outlook = data.get("outlook_30d", {})
        conf    = data.get("model_confidence", {})
        index_rows.append((
            run_date,
            fpi.get("value"), fpi.get("mom_change"),
            vol.get("value"),
            outlook.get("avg_pct_change"),
            conf.get("avg_pct"),
            None,  # commentary — only produced by the separate,
                   # not-yet-wired-in pipeline/generate_commentary.py
        ))

        vol_per_commodity = vol.get("per_commodity", {})
        arbitrage = data.get("arbitrage", {})
        for commodity_name in set(vol_per_commodity) | set(arbitrage):
            cid = commodity_map.get(commodity_name)
            if cid is None:
                skipped_unknown_commodity.add(commodity_name)
                continue
            vol_pct = vol_per_commodity.get(commodity_name)
            net_arb = arbitrage.get(commodity_name, {}).get("net_arbitrage_ngn_kg")
            viable  = (net_arb > 0) if net_arb is not None else None
            metric_rows.append((run_date, cid, vol_pct, net_arb, viable))

    if skipped_unknown_commodity:
        print(f"  WARNING — skipped rows for commodities not in the commodities table: "
              f"{skipped_unknown_commodity}")

    print(f"  Inserting {len(metric_rows):,} intelligence_metrics rows...")
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO intelligence_metrics
                (time, commodity_id, volatility_pct, net_arbitrage_ngn_kg, arbitrage_viable)
            VALUES %s
            ON CONFLICT (time, commodity_id) DO NOTHING
            """,
            metric_rows,
            page_size=1000,
        )
        print(f"  Inserting {len(index_rows):,} market_index rows...")
        execute_values(
            cur,
            """
            INSERT INTO market_index
                (time, fpi, fpi_mom_change, volatility_index, outlook_30d_pct,
                 model_confidence_pct, commentary)
            VALUES %s
            ON CONFLICT (time) DO NOTHING
            """,
            index_rows,
            page_size=1000,
        )
    conn.commit()
    print("  Done.")


def main():
    # Discrete PGHOST/PGPASSWORD/etc. take priority when set — psycopg2
    # picks them up automatically via libpq when called with no DSN, with
    # no URL-encoding involved. Falls back to the TSDB_URL connection string.
    conn = psycopg2.connect() if PG_DISCRETE_VARS_SET else psycopg2.connect(DB_URL)
    try:
        commodity_map, location_map = get_lookup_maps(conn)
        national_id = location_map["National"]
        print(f"Loaded {len(commodity_map)} commodities, {len(location_map)} locations from DB.\n")

        migrate_prices(conn, commodity_map, national_id)
        print()
        migrate_forecasts(conn, commodity_map, national_id)
        print()
        migrate_zonal_forecasts(conn, commodity_map, location_map)
        print()
        migrate_intelligence(conn, commodity_map)

        with conn.cursor() as cur:
            for table in ("prices", "forecasts", "intelligence_metrics", "market_index"):
                cur.execute(f"SELECT count(*) FROM {table}")
                print(f"\n{table} table now has {cur.fetchone()[0]:,} rows.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()