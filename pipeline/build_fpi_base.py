"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Food Price Index base: each commodity's 2025 average price, from real data

The index (08_intelligence.py) is today's basket over the 2025 basket, x 100.
The 2025 prices used to be typed in by hand (beef 3.2M when WFP's real 2025
average was 5.86M). This builds them from the master dataset instead and
writes data/external/fpi_base_2025.csv, which 08 reads. The base is fixed on
purpose - rebuild only when the 2025 history itself changes:

  python pipeline/build_fpi_base.py

Rules, in order:
  1. real_average   - at least MIN_MONTHS months of 2025 with a real price:
                      the mean of those monthly means.
  2. real_and_gaps  - fewer real months, but real prices in 2025: the mean of
                      the 2025 weekly series of real prices and the straight
                      lines between them (FEWS NET paused most of 2025).
  3. sibling        - no real 2025 price: a sibling's base x measured ratio
                      (yellow maize = white maize x 1.023, WFP 2014-23).
  Otherwise the commodity is left out of the index.
"""

import os, sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, REAL_SOURCES

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE, "data", "external", "fpi_base_2025.csv")
YEAR     = ("2025-01-01", "2025-12-31")
MIN_MONTHS = 6
SIBLINGS = {"Maize (yellow)": ("Maize (white)", 1.023)}


def build(commodities, master=None):
    if master is None:
        master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    y = master[(master["date"] >= YEAR[0]) & (master["date"] <= YEAR[1])
               & (master["record_type"] != "forecast") & master["price_ngn_mt"].notna()]
    rows = {}
    for c in commodities:
        s = y[y["commodity"] == c]
        real = s[s["data_source"].isin(REAL_SOURCES)]
        months = real["date"].dt.month.nunique()
        if months >= MIN_MONTHS:
            base = real.groupby(real["date"].dt.month)["price_ngn_mt"].mean().mean()
            rows[c] = (base, "real_average", months, f"mean of {months} monthly averages of real 2025 prices")
        elif months > 0:
            filled = s[s["data_source"].isin(REAL_SOURCES | {"Interpolated"})]
            rows[c] = (filled["price_ngn_mt"].mean(), "real_and_gaps", months,
                       f"real prices in {months} month(s) of 2025 plus the straight lines between them")
    for c, (sib, ratio) in SIBLINGS.items():
        if c in commodities and c not in rows and sib in rows:
            rows[c] = (rows[sib][0] * ratio, "sibling", 0, f"{sib} 2025 base x {ratio} (no real 2025 price)")
    out = pd.DataFrame([{"commodity": c, "base_price_ngn_mt": round(v[0], 2), "method": v[1],
                         "real_months_2025": v[2], "note": v[3]} for c, v in rows.items()])
    return out


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import importlib.util
    spec = importlib.util.spec_from_file_location("intel", os.path.join(os.path.dirname(os.path.abspath(__file__)), "08_intelligence.py"))
    intel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(intel)
    out = build(list(intel.FPI_WEIGHTS))
    out.to_csv(OUT_PATH, index=False)
    print(out.to_string(index=False))
    print(f"-> {OUT_PATH}")
