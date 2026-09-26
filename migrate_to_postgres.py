"""
One-off migration: flat files -> Timescale Postgres.

Populates:
  - prices              from data/processed/agrolinking_master.csv
  - forecasts            from outputs/forecasts/validated/forecast_validated_*.json

Idempotent — every insert uses ON CONFLICT DO NOTHING, so re-running this
is always safe (it just skips rows that are already there).

Usage:
    pip install psycopg2-binary pandas --break-system-packages
    python migrate_to_postgres.py

Set your connection string as an environment variable rather than
hardcoding it here — this file gets committed to git, your password
shouldn't be in it.

    $env:TSDB_URL = "postgres://tsdbadmin:...@...tsdb.cloud.timescale.com:PORT/tsdb?sslmode=require"
    python migrate_to_postgres.py
"""
import os
import json
import glob
import sys
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

DB_URL = os.environ.get("TSDB_URL")
if not DB_URL:
    sys.exit("Set TSDB_URL as an environment variable before running this script.")

MASTER_CSV = "data/processed/agrolinking_master.csv"
VALIDATED_DIR = "outputs/forecasts/validated"

# Real price data only — skip synthetic/interpolated filler rows and
# raw "forecast" placeholder rows. This mirrors the same REAL_SOURCES
# logic the pipeline itself uses for anchor selection, extended to
# include validated_actual since those are confirmed-accurate prices.
REAL_RECORD_TYPES = {"historical", "validated_actual"}


def get_lookup_maps(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT commodity_id, name FROM commodities")
        commodity_map = {name: cid for cid, name in cur.fetchall()}
        cur.execute("SELECT location_id, state FROM locations")
        location_map = {state: lid for lid, state in cur.fetchall()}
    if "National" not in location_map:
        sys.exit("No 'National' row in locations — run 01_seed_dimensions.sql first.")
    return commodity_map, location_map


def migrate_prices(conn, commodity_map, national_id):
    print("Loading master.csv...")
    df = pd.read_csv(MASTER_CSV, parse_dates=["date"])
    df = df[df["record_type"].isin(REAL_RECORD_TYPES)]
    print(f"  {len(df):,} real/validated rows to migrate (out of {len(df):,} total after filter)")

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
        with open(path) as f:
            data = json.load(f)
        forecasts = data.get("forecasts", data)

        for commodity_name, fc in forecasts.items():
            cid = commodity_map.get(commodity_name)
            if cid is None:
                skipped_unknown_commodity.add(commodity_name)
                continue

            gen_date = fc.get("last_known_date") or fc.get("run_date")
            validation = fc.get("validation", {})
            validated = validation.get("status") == "validated"
            error_pct = validation.get("error_after") or validation.get("error_pct")
            confidence = fc.get("model_confidence")

            horizons = fc.get("horizons", {})
            HORIZON_DAYS = {
                "daily": 1, "weekly": 7, "2_weeks": 14,
                "monthly": 30, "3_months": 90, "6_months": 180,
            }
            for h_name, h_days in HORIZON_DAYS.items():
                h_data = horizons.get(h_name)
                if not h_data:
                    continue
                vals = h_data.get("ensemble", {}).get("values", [])
                price = vals[0] if vals else h_data.get("forecast_price")
                if price is None:
                    continue
                rows.append((
                    gen_date, cid, national_id, h_days,
                    float(price), confidence, validated, error_pct,
                ))

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
                 predicted_price, model_confidence, validated, error_pct)
            VALUES %s
            ON CONFLICT (time, commodity_id, location_id, horizon_days) DO NOTHING
            """,
            rows,
            page_size=1000,
        )
    conn.commit()
    print("  Done.")


def main():
    conn = psycopg2.connect(DB_URL)
    try:
        commodity_map, location_map = get_lookup_maps(conn)
        national_id = location_map["National"]
        print(f"Loaded {len(commodity_map)} commodities, {len(location_map)} locations from DB.\n")

        migrate_prices(conn, commodity_map, national_id)
        print()
        migrate_forecasts(conn, commodity_map, national_id)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM prices")
            print(f"\nprices table now has {cur.fetchone()[0]:,} rows.")
            cur.execute("SELECT count(*) FROM forecasts")
            print(f"forecasts table now has {cur.fetchone()[0]:,} rows.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()