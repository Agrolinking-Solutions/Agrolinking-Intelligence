"""
Which commodities have a fresh observed price from our direct sources?

Shared by 14_promote_web_prices.py (web prices only fill commodities without
one) and 10_web_price_search.py (the 3-hourly rotation always searches those
commodities). One definition, so the two can't disagree.
"""

import pandas as pd

from config.settings import PATHS

DIRECT_SOURCES = {"Agricome", "Agrolinking_primary"}
FRESH_DAYS     = 10   # Agricome/primary price newer than this => no gap
# WFP is monthly and published ~4 weeks late, so a 10-day window made every
# WFP commodity look like a gap and let web prices compete with real WFP
# data (a single-source beef price 15% above WFP's put a spike in the
# history, Oct 2026). Same window 06_validate.py uses for references.
FRESH_DAYS_WFP = 45


def real_dates(master=None):
    """{commodity: (latest Agricome/primary date, latest WFP date)} as python dates or None."""
    if master is None:
        master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    out = {}
    for c, sub in master.groupby("commodity"):
        direct = sub.loc[sub["data_source"].isin(DIRECT_SOURCES), "date"]
        wfp = sub.loc[sub["data_source"] == "WFP", "date"]
        out[c] = (direct.max().date() if len(direct) else None,
                  wfp.max().date() if len(wfp) else None)
    return out


def is_fresh(direct_date, wfp_date, today):
    return ((direct_date is not None and (today - direct_date).days <= FRESH_DAYS)
            or (wfp_date is not None and (today - wfp_date).days <= FRESH_DAYS_WFP))


def gap_commodities(commodities, today, master=None):
    """Commodities with no fresh observed price from Agricome/primary or WFP."""
    dates = real_dates(master)
    return [c for c in commodities if not is_fresh(*dates.get(c, (None, None)), today)]
