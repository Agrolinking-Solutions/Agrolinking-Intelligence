"""
State price factors: how much dearer or cheaper each state is than the
national price (Lagos maize at 1.16 = 16% above national).

Two kinds, and every state price says which one it used:
  measured  - from FEWS NET's weekly WHOLESALE prices for named markets
              (via FAO), Jan 2023 onwards: Kano, Ibadan (Oyo), Maiduguri
              (Borno), plus Lagos and Giwa (Kaduna) until Jan 2025. Each
              week, a market's price over the mean of the national markets
              (the same ones the platform's national price uses); the factor
              is the median of those weekly ratios.
              Where FEWS NET has no market, WFP's market-by-market wholesale
              prices, Jan 2019 to Jan 2023 (the last period WFP reported
              outside the North East): a state's monthly median over the
              median across WFP states, median of the monthly ratios.
  estimated - the fixed table data/external/state_price_differentials.csv
              (set up May 2026, not derived from market data), or the zone
              default where the table has no row.
Measured wins where it exists.

Rebuild the measured file (only needed if the WFP history changes):
  python pipeline/state_factors.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

BASE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_PATH    = os.path.join(BASE, "data", "external", "state_price_differentials.csv")
MEASURED_PATH = os.path.join(BASE, "data", "external", "state_price_factors_measured.csv")
WFP_PATH      = PATHS.get("raw_wfp", os.path.join(BASE, "data", "raw", "wfp_food_prices_nga.csv"))

MEASURED = "measured_wfp_2019_2023"
MEASURED_FEWS = "measured_fews_2023_2026"
ESTIMATED = "estimated"
FAO_PATH = os.path.join(BASE, "data", "raw", "fao_fpma_prices.csv")

# FEWS NET market -> our state; national reference = mean of NATIONAL_MARKETS
FEWS_MARKET_STATE = {"Kano": "Kano", "Ibadan": "Oyo", "Maiduguri": "Borno", "Lagos": "Lagos", "Giwa": "Kaduna"}
NATIONAL_MARKETS = ["Kano", "Ibadan", "Kaura Namoda", "Maiduguri"]
FEWS_COMMODITIES = ["Maize (white)", "Sorghum", "Rice (foreign)"]
FEWS_SINCE = "2023-01-01"
MIN_WEEKS = 26

WFP_TO_OURS = {
    "Maize (white)":   "Maize (white)",
    "Maize (yellow)":  "Maize (yellow)",
    "Sorghum (white)": "Sorghum",
    "Rice (local)":    "Rice (local)",
    "Rice (imported)": "Rice (foreign)",
    "Cowpeas (white)": "Beans (white)",
    "Cowpeas (brown)": "Beans (red)",
}
PERIOD     = ("2019-01-01", "2023-01-31")
MIN_MONTHS = 24
# Outside this band the "factor" is a product or unit mismatch, not a price
# gap (WFP's Lagos "Rice (local)" sits at 0.55 of other states every month).
SANE_RANGE = (0.7, 1.5)


def build_measured(wfp_path=WFP_PATH, out_path=MEASURED_PATH):
    w = pd.read_csv(wfp_path, low_memory=False)
    w = w[w["date"].astype(str).str[:1].str.isdigit()].copy()
    w["date"] = pd.to_datetime(w["date"])
    w["price"] = pd.to_numeric(w["price"], errors="coerce")
    w = w[(w["date"] >= PERIOD[0]) & (w["date"] <= PERIOD[1])
          & w["commodity"].isin(WFP_TO_OURS) & (w["pricetype"] == "Wholesale")]
    kg = w["unit"].str.extract(r"([\d.]+)\s*KG")[0].astype(float).fillna(1.0)
    w = w.assign(price_kg=w["price"] / kg, commodity=w["commodity"].map(WFP_TO_OURS))

    st = w.groupby(["commodity", "date", "admin1"])["price_kg"].median().reset_index()
    nat = st.groupby(["commodity", "date"])["price_kg"].median().rename("national")
    st = st.join(nat, on=["commodity", "date"])
    st["ratio"] = st["price_kg"] / st["national"]
    f = (st.groupby(["admin1", "commodity"])["ratio"]
           .agg(price_factor="median", months="size").reset_index()
           .rename(columns={"admin1": "state"}))
    f = f[(f["months"] >= MIN_MONTHS) & f["price_factor"].between(*SANE_RANGE)]
    f["source"] = MEASURED
    fews = build_fews()
    if len(fews):
        f = f.merge(fews[["state", "commodity"]], how="left", indicator=True)
        f = pd.concat([f[f["_merge"] == "left_only"].drop(columns="_merge"), fews], ignore_index=True)
    f["price_factor"] = f["price_factor"].round(3)
    f = f.sort_values(["state", "commodity"])
    f.to_csv(out_path, index=False)
    return f


def build_fews(path=FAO_PATH):
    """Measured factors from FEWS NET weekly wholesale prices (2023 onwards)."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=["state", "commodity", "price_factor", "months", "source"])
    w = pd.read_csv(path, parse_dates=["date"])
    w = w[(w["price_type"] == "wholesale") & ~w["unit_suspect"].astype(bool) & (w["periodicity"] == "weekly")
          & (w["date"] >= FEWS_SINCE) & w["our_commodity"].isin(FEWS_COMMODITIES) & w["price_ngn_mt"].notna()]
    w["week"] = w["date"] - pd.to_timedelta(w["date"].dt.dayofweek, unit="D")
    rows = []
    for c, g in w.groupby("our_commodity"):
        piv = g.pivot_table(index="week", columns="market", values="price_ngn_mt", aggfunc="mean")
        nat_cols = [m for m in NATIONAL_MARKETS if m in piv.columns]
        nat = piv[nat_cols].mean(axis=1).where(piv[nat_cols].notna().sum(axis=1) >= 3)
        for market, state in FEWS_MARKET_STATE.items():
            if market not in piv.columns:
                continue
            ratio = (piv[market] / nat).dropna()
            if len(ratio) >= MIN_WEEKS:
                rows.append({"state": state, "commodity": c, "price_factor": float(ratio.median()),
                             "months": int(len(ratio)), "source": MEASURED_FEWS})
    f = pd.DataFrame(rows)
    return f[f["price_factor"].between(*SANE_RANGE)] if len(f) else f


def load_factors():
    """{state: {commodity: (factor, source)}} - measured over the estimated table."""
    out = {}
    table = pd.read_csv(TABLE_PATH)
    for r in table.itertuples():
        out.setdefault(r.state, {})[r.commodity] = (float(r.price_factor), ESTIMATED)
    if os.path.exists(MEASURED_PATH):
        for r in pd.read_csv(MEASURED_PATH).itertuples():
            out.setdefault(r.state, {})[r.commodity] = (float(r.price_factor), r.source)
    return out


def zone_defaults():
    """{state: (zone, zone_default)} from the estimated table."""
    table = pd.read_csv(TABLE_PATH)
    return {r.state: (r.zone, float(r.zone_default)) for r in table.itertuples()}


def factor_for(state, commodity, factors=None, defaults=None):
    """(factor, source) for one state and commodity; None if the state is unknown."""
    factors = load_factors() if factors is None else factors
    defaults = zone_defaults() if defaults is None else defaults
    key = next((s for s in defaults if s.lower() == state.lower()), None)
    if key is None:
        return None
    return factors.get(key, {}).get(commodity, (defaults[key][1], ESTIMATED))


if __name__ == "__main__":
    f = build_measured()
    print(f"{len(f)} measured state factors -> {MEASURED_PATH}")
    print(f.pivot_table(index="state", columns="commodity", values="price_factor").round(2).to_string())
