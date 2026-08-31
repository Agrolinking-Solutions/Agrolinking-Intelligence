"""
DIAGNOSTIC — find why Maize (white), Maize (yellow), Rice show a
stale last_known_date (~2023) instead of a recent 2026 date.

Run from your project root:
  python pipeline/diagnose_stale_anchor.py

Prints, for each affected commodity:
  - total row count and full date range
  - row count and date range split by data_source
  - the last 10 rows sorted by date (so you can see exactly what
    05_forecast.py's `real_rows["price_ngn_mt"].iloc[-1]` would pick)
"""

import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

REAL_SOURCES = {"Agricome", "WFP", "Agrolinking_primary"}
CHECK = ["Maize (white)", "Maize (yellow)", "Rice", "Meat (beef)", "Meat (goat)",
         "Fish (dried)", "Eggs", "Hibiscus", "Sesame", "Ginger", "Cocoa",
         "Soybeans", "Cashew Nuts", "Sorghum"]


def main():
    master_path = PATHS["master"]
    if not os.path.exists(master_path):
        print(f"Master file not found at: {master_path}")
        return

    df = pd.read_csv(master_path, parse_dates=["date"])
    print(f"Master loaded: {len(df):,} rows, {df['commodity'].nunique()} commodities\n")

    for commodity in CHECK:
        print("=" * 70)
        print(f"  {commodity}")
        print("=" * 70)

        sub = df[df["commodity"] == commodity].copy()
        if sub.empty:
            print("  No rows found for this commodity at all.\n")
            continue

        print(f"  Total rows        : {len(sub)}")
        print(f"  Full date range   : {sub['date'].min().date()} -> {sub['date'].max().date()}")

        if "data_source" in sub.columns:
            print("\n  By data_source:")
            for src, grp in sub.groupby("data_source"):
                print(f"    {src:<20} {len(grp):>5} rows  "
                      f"{grp['date'].min().date()} -> {grp['date'].max().date()}")

            real = sub[sub["data_source"].isin(REAL_SOURCES)].sort_values("date")
            print(f"\n  Rows tagged as REAL_SOURCES {REAL_SOURCES}: {len(real)}")
            if not real.empty:
                print(f"  Real-source date range: {real['date'].min().date()} -> {real['date'].max().date()}")
                print(f"\n  Last 10 REAL rows sorted by date (this is what "
                      f"05_forecast.py's .iloc[-1] actually picks):")
                cols = [c for c in ["date", "price_ngn_mt", "data_source",
                                     "record_type", "source"] if c in real.columns]
                print(real[cols].tail(10).to_string(index=False))

                last_real_date = real["date"].max()
                non_real_after = sub[
                    (sub["date"] > last_real_date) &
                    (~sub["data_source"].isin(REAL_SOURCES))
                ]
                if not non_real_after.empty:
                    print(f"\n  NOTE: {len(non_real_after)} row(s) exist AFTER the last "
                          f"real-source date ({last_real_date.date()}) but are tagged "
                          f"with a non-real data_source — these are being correctly "
                          f"ignored as anchor candidates, but check whether they "
                          f"should actually be real data mislabeled.")
            else:
                print("  ⚠️  ZERO rows tagged as a real source for this commodity — "
                      "this alone would explain a stale/wrong anchor.")
        else:
            print("  ⚠️  No 'data_source' column found in master at all.")

        print()

    print("=" * 70)
    print("Compare the 'Last 10 REAL rows' dates above against today's date.")
    print("If they stop in 2023, the raw source file (agricome.csv or the WFP")
    print("CSV) genuinely has no fresher REAL-tagged row for that commodity —")
    print("the bug is upstream in what's being scraped/labelled as real, not")
    print("in 05_forecast.py's selection logic itself.")
    print("=" * 70)


if __name__ == "__main__":
    main()