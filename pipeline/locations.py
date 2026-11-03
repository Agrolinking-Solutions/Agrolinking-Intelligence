"""
Where a web price was quoted, and what it is as a national price.

The platform's prices are national. A price quoted for one state's markets
(Lagos: Mile 12, Ijora...) is that state's price, and states differ: Lagos
beans run 26% above the national median (WFP 2019-23). Such a price stands
for, or is compared with, a national price only through a MEASURED state gap
(state_price_factors_measured.csv, built by state_factors.py). Without one it
stays out - the estimated state table is not data. On 3 Nov 2026 four Lagos
prices (local rice x3, eggs) were standing as national prices, and a Lagos
50kg-bag price had opened price alert #7 against AFEX's national maize price.

Used by 14_promote_web_prices.py (what enters the price history) and
16_cross_check.py (what is compared). No side effects on import.
"""

import os
import re

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEASURED_PATH = os.path.join(BASE, "data", "external", "state_price_factors_measured.csv")

STATES = ["Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
          "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "Gombe", "Imo", "Jigawa",
          "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger",
          "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara"]
# Market towns and cities that turn up in price articles -> their state
PLACES = {"Abuja": "FCT", "FCT": "FCT", "Ibadan": "Oyo", "Bodija": "Oyo", "Maiduguri": "Borno",
          "Dawanau": "Kano", "Port Harcourt": "Rivers", "Onitsha": "Anambra", "Mile 12": "Lagos",
          "Zaria": "Kaduna", "Giwa": "Kaduna", "Jos": "Plateau", "Ilorin": "Kwara", "Yola": "Adamawa",
          "Mubi": "Adamawa", "Makurdi": "Benue", "Lafia": "Nasarawa", "Minna": "Niger",
          "Lokoja": "Kogi", "Owerri": "Imo", "Uyo": "Akwa Ibom", "Calabar": "Cross River",
          "Abeokuta": "Ogun", "Akure": "Ondo", "Osogbo": "Osun", "Asaba": "Delta",
          "Kaura Namoda": "Zamfara", "Gusau": "Zamfara", "Dutse": "Jigawa", "Damaturu": "Yobe",
          "Birnin Kebbi": "Kebbi", "Jalingo": "Taraba", "Umuahia": "Abia", "Abakaliki": "Ebonyi",
          "Yenagoa": "Bayelsa", "Gwagwalada": "FCT"}
_NAMES = sorted([(s, s) for s in STATES] + list(PLACES.items()), key=lambda x: -len(x[0]))
# "Niger Delta" is a region, not Niger and Delta states
_REGIONS = re.compile(r"\bniger[- ]delta\b", re.I)


def state_of(location):
    """
    The one state a location names ("Mile 12, Lagos" -> "Lagos"), or None
    when it names none ("national", unstated) or several (a multi-state
    survey is closer to a national price than to any one state's).
    """
    text = _REGIONS.sub(" ", str(location or ""))
    found = {state for name, state in _NAMES if re.search(rf"\b{re.escape(name)}\b", text, re.I)}
    return found.pop() if len(found) == 1 else None


_measured = None


def measured_gap(commodity, state):
    """(factor, source) - state price / national price, measured - or None."""
    global _measured
    if _measured is None:
        _measured = {}
        if os.path.exists(MEASURED_PATH):
            for r in pd.read_csv(MEASURED_PATH).itertuples():
                _measured[(r.state, r.commodity)] = (float(r.price_factor), r.source)
    return _measured.get((state, commodity))


def national_equivalent(commodity, price, location):
    """
    (national price or None, state or None, note or None) for a price quoted
    at `location`. Unlocated or national: unchanged. One state with a
    measured gap: price / gap. One state without: None - not usable as a
    national price.
    """
    state = state_of(location)
    if state is None:
        return price, None, None
    gap = measured_gap(commodity, state)
    if gap is None:
        return None, state, (f"{state} price - there is no measured {state}-to-national price "
                             f"gap for {commodity}, so it can't stand for the national price")
    factor, source = gap
    national = price / factor
    return national, state, (f"{state} price N{price:,.0f} / {factor} (measured {state} gap, "
                             f"{source}) = N{national:,.0f} national")
