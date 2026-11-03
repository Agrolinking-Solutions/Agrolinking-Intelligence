"""
AFEX's price for a day: the median of its last few trading days.

One day's AFEX figure swings - soybean 598K -> 645K -> 549K over 14-16 Oct
2026, maize 257K -> 471K in 8 trading days to 2 Nov 2026 - so the price
history (01_ingest.py) and the daily comparison (16_cross_check.py) both use
this median, never a single print. No side effects on import.
"""

import pandas as pd

AFEX_DAYS = 5           # median over AFEX's last 5 trading days...
AFEX_WINDOW_DAYS = 10   # ...that fall within 10 days of the date


def afex_median(prints: pd.DataFrame, as_of) -> tuple[float, int]:
    """
    Median price_ngn_mt of the last AFEX_DAYS prints on or before as_of and
    within AFEX_WINDOW_DAYS of it, and how many prints that was.
    prints: one commodity's rows with as_of_date and price_ngn_mt.
    """
    as_of = pd.Timestamp(as_of)
    dates = pd.to_datetime(prints["as_of_date"])
    recent = prints[(dates <= as_of) & (dates >= as_of - pd.Timedelta(days=AFEX_WINDOW_DAYS))]
    recent = recent.assign(_d=pd.to_datetime(recent["as_of_date"])).sort_values("_d").tail(AFEX_DAYS)
    return float(recent["price_ngn_mt"].median()), len(recent)
