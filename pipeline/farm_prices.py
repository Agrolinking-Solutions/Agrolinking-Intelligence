"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Farm crop prices: naira per kg for the crops AgLens values on farms

AgLens (formerly AgTrail AI) puts a naira value on each farm's harvest
estimate. These crops are not PIS forecast commodities, so this gives each
one a price per kg, in the form the farmer sells, with its date, source,
level (wholesale/bulk bag/retail) and zone. Price only: no forecast, and not
on the client dashboard.

Sources (all real, dated observations; nothing is estimated or carried
forward beyond MAX_AGE_DAYS):
  - ColdHubs weekly zone prices: tomato, pepper   } data/raw/produce_prices.csv
  - Business A.M. weekly wholesale list:          }   (fetch_produce_prices.py)
    tomato, onion, pepper, sweet potato           }
  - WFP monthly shop prices, Borno and Yobe (North-East): yam, groundnut,
    tomato, onion (data/raw/wfp_food_prices_nga.csv, refreshed by step 00d)

None of these is a farm-gate price: no regular source publishes one for these
crops, so a value built on them is what the harvest would fetch in a market.
Cassava (fresh roots) has no current source at all and stays unpriced.

Which price a farm gets (choose()): a price from the farm's own zone first
(wholesale before retail), then a wholesale price with no zone or across
zones, then a price from another zone. Newest first within each step.

Output: outputs/intelligence/farm_prices_<date>.json, served at
GET /farm-prices, GET /farm-prices/{crop}?state=..., and (headline price, in
the shape AgLens already reads) GET /forecasts/{crop}.

Never fails the pipeline. Run: python pipeline/farm_prices.py
"""

import os, re, sys, json
from datetime import date, datetime, timezone
from statistics import median

import pandas as pd
from loguru import logger

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCE_PATH = os.path.join(BASE, "data", "raw", "produce_prices.csv")
WFP_PATH     = os.path.join(BASE, "data", "raw", "wfp_food_prices_nga.csv")
OUT_DIR      = os.path.join(BASE, "outputs", "intelligence")

# A price older than this is not used. WFP is monthly, dated mid-month and
# published a few weeks late, so its newest month is normally 4-8 weeks old.
MAX_AGE_DAYS = {"ColdHubs": 45, "Business A.M.": 45, "WFP": 60}
WFP_MIN_MARKETS = 8          # a WFP month counts once this many markets have reported
LEVEL_ORDER = {"wholesale": 0, "bulk bag": 0, "retail": 1}

SOURCES = {
    "ColdHubs":      {"name": "ColdHubs Market Weekly", "url": "coldhubs.com", "period": "week",
                      "about": "Weekly bag prices by zone from ColdHubs' hub managers. ColdHubs does not say "
                               "whether they are wholesale or retail, so they are labelled 'bulk bag'."},
    "Business A.M.": {"name": "Business A.M. fresh-produce prices", "url": "businessamlive.com", "period": "day",
                      "about": "Weekly wholesale prices for a fixed list of produce, with bag sizes. "
                               "The market is not named."},
    "WFP":           {"name": "WFP Nigeria Food Price Monitor", "url": "data.humdata.org/dataset/wfp-food-prices-for-nigeria",
                      "period": "month", "about": "Monthly shop (retail) prices from markets in Borno and Yobe."},
}
# What users are told about each price. None of the sources gives a farm-gate price.
PRICE_TYPE = {"wholesale": "market price, wholesale", "retail": "market price, retail",
              "bulk bag": "market price, bulk bag"}

ZONE_STATES = {
    "North Central": ["Benue", "FCT", "Kogi", "Kwara", "Nasarawa", "Niger", "Plateau"],
    "North East":    ["Adamawa", "Bauchi", "Borno", "Gombe", "Taraba", "Yobe"],
    "North West":    ["Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Sokoto", "Zamfara"],
    "South East":    ["Abia", "Anambra", "Ebonyi", "Enugu", "Imo"],
    "South South":   ["Akwa Ibom", "Bayelsa", "Cross River", "Delta", "Edo", "Rivers"],
    "South West":    ["Ekiti", "Lagos", "Ogun", "Ondo", "Osun", "Oyo"],
}
STATE_ZONE = {s: z for z, states in ZONE_STATES.items() for s in states}
STATE_ALIASES = {"abuja": "FCT", "federal capital territory": "FCT", "akwa-ibom": "Akwa Ibom",
                 "cross-river": "Cross River", "nassarawa": "Nasarawa"}

# In order of how many AgLens farms grow them. "items" are the source items
# that price the form the farmer sells; anything else a source lists for the
# crop (hybrid tomatoes, other peppers) is shown under other_items only.
CROPS = {
    "Cassava": {
        "form": "fresh roots", "aliases": ["cassava"], "items": [],
        "note": "No regular public price for fresh cassava roots. The latest figures found (Guardian, Oct 2026) "
                "were May 2026 prices in Ogun and Oyo during a glut: N5,000 per tonne at the farm and N25,000 at "
                "the factory. Too old and unusual to use.",
    },
    "Yam": {
        "form": "fresh tubers", "aliases": ["yam", "yams"], "items": ["Yam"],
        "note": "WFP shop prices per kg in the North-East. No wholesale price per kg is published "
                "(wholesale yam is sold per 100 tubers, without a weight).",
    },
    "Groundnut": {
        "form": "shelled nuts", "aliases": ["groundnut", "groundnuts", "peanut", "peanuts"], "items": ["Groundnuts"],
        "note": "WFP's 'Groundnuts' (sold by the mudu). In the same markets it ran about 1.4 times WFP's shelled "
                "groundnut price per kg (2016-22), so it is not an in-shell price; most likely shelled nuts. "
                "A yield in shell needs a shelling factor before this price applies.",
    },
    "Tomato": {
        "form": "fresh", "aliases": ["tomato", "tomatoes"], "items": ["Tomatoes", "ordinary tomatoes"],
        "note": "Ordinary (open-field) tomatoes. Hybrid tomatoes, sold in 25 kg crates, are listed separately "
                "and not used for the headline.",
    },
    "Sweet potato": {
        "form": "fresh roots", "aliases": ["sweet potato", "sweet potatoes", "ofsp", "orange-fleshed sweet potato"],
        "items": ["sweet potatoes"],
        "note": "Ordinary sweet potatoes; no separate price for orange-fleshed sweet potato.",
    },
    "Onion": {
        "form": "dry bulbs", "aliases": ["onion", "onions"], "items": ["Onions", "wet-season onions", "dry-season onions"],
        "note": "Business A.M. prices the rainy-season ('wet-season') and irrigated ('dry-season') crops separately; "
                "the headline uses the crop being harvested in that half of the year (wet-season Aug-Jan, "
                "dry-season Feb-Jul) - our rule, to be confirmed by an agronomist.",
    },
    "Pepper": {
        "form": "fresh", "aliases": ["pepper", "peppers", "chilli", "chili", "sweet pepper", "hot pepper"],
        "items": ["Pepper", "tatashe"],
        "note": "ColdHubs prices 'pepper' per 25 kg bag, the same bag as tatashe; Business A.M.'s tatashe is used "
                "for the headline and its atarugu (rodo) and shombo prices are listed separately.",
    },
}
WFP_ITEMS = {"Yam": "Yam", "Groundnuts": "Groundnut", "Tomatoes": "Tomato", "Onions": "Onion"}


def onion_item(on: date):
    """The onion crop being harvested in that half of the year (see CROPS['Onion'])."""
    return "wet-season onions" if on.month >= 8 or on.month == 1 else "dry-season onions"


def zone_for_state(state):
    """Zone for a state name (any case, common spellings), or None."""
    if not state:
        return None
    s = state.strip()
    s = STATE_ALIASES.get(s.lower(), s)
    return next((z for st, z in STATE_ZONE.items() if st.lower() == s.lower()), None)


# ── Observations ─────────────────────────────────────────────────────────

def produce_observations(df):
    out = []
    for r in df.to_dict("records"):
        kg = float(r["package_kg"])
        lo, hi = float(r["price_low_ngn"]) / kg, float(r["price_high_ngn"]) / kg
        zone = r["zone"] if isinstance(r["zone"], str) and r["zone"] else None
        out.append({"crop": r["crop"], "item": r["item"], "source": r["source"], "level": r["level"],
                    "zone": zone, "where": zone or "market not named",
                    "price_date": r["price_date"], "price_ngn_per_kg": round((lo + hi) / 2),
                    "low_ngn_per_kg": round(lo), "high_ngn_per_kg": round(hi),
                    "package": r["package"],
                    "package_price_ngn": f"{int(r['price_low_ngn']):,}" + ("" if r["price_low_ngn"] == r["price_high_ngn"]
                                                                          else f"-{int(r['price_high_ngn']):,}"),
                    "evidence": r["evidence"], "url": r["url"]})
    return out


def unit_kg(unit):
    """Kilograms in a WFP unit ("2.5 KG" -> 2.5, "KG" -> 1), None for anything else."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)?\s*KG", str(unit).strip(), re.I)
    return float(m.group(1) or 1) if m else None


def wfp_observations(wfp):
    """Newest month with at least WFP_MIN_MARKETS markets, per WFP item: the
    median shop price per kg. Cross-border markets "(CBM)" are left out: they
    sit on the Niger border and reported in CFA francs until 2016."""
    out = []
    w = wfp[(wfp["pricetype"] == "Retail") & wfp["commodity"].isin(WFP_ITEMS)
            & ~wfp["market"].astype(str).str.contains("(CBM)", regex=False)].copy()
    w["kg"] = w["unit"].map(unit_kg)
    w["per_kg"] = pd.to_numeric(w["price"], errors="coerce") / w["kg"]
    for item, g in w.dropna(subset=["per_kg"]).groupby("commodity"):
        months = g.groupby("date").agg(markets=("market", "nunique"), med=("per_kg", "median"),
                                       lo=("per_kg", lambda s: s.quantile(0.25)),
                                       hi=("per_kg", lambda s: s.quantile(0.75)))
        months = months[months["markets"] >= WFP_MIN_MARKETS]
        if months.empty:
            continue
        month = months.index.max()
        m = months.loc[month]
        states = sorted(g.loc[g["date"] == month, "admin1"].dropna().unique())
        zones = {STATE_ZONE.get(s) for s in states}
        unit = g.loc[g["date"] == month, "unit"].mode().iloc[0]
        out.append({"crop": WFP_ITEMS[item], "item": item, "source": "WFP", "level": "retail",
                    "zone": zones.pop() if len(zones) == 1 else None,
                    "where": f"{' and '.join(states)} (median of {int(m['markets'])} markets)",
                    "price_date": str(month)[:10], "price_ngn_per_kg": int(round(m["med"])),
                    "low_ngn_per_kg": int(round(m["lo"])), "high_ngn_per_kg": int(round(m["hi"])),
                    "package": f"per {unit.lower()} measure", "package_price_ngn": None,
                    "evidence": f"WFP {item}, retail, {str(month)[:7]}: median of {int(m['markets'])} markets "
                                f"(middle half N{m['lo']:,.0f}-{m['hi']:,.0f}/kg)",
                    "url": SOURCES["WFP"]["url"], "markets": int(m["markets"])})
    return out


def load_wfp(path=WFP_PATH):
    wfp = pd.read_csv(path, low_memory=False)
    wfp = wfp[~wfp["date"].astype(str).str.startswith("#")]          # HXL tag row
    wfp["date"] = pd.to_datetime(wfp["date"]).dt.strftime("%Y-%m-%d")
    return wfp


# ── Choosing a price ─────────────────────────────────────────────────────

def zone_median(obs):
    """One across-zones price from the newest ColdHubs report: the median of its
    zone prices, so a farm with no zone (or no price in its zone) is not given
    one zone's price."""
    zoned = [o for o in obs if o["source"] == "ColdHubs" and o["zone"]]
    if not zoned:
        return []
    newest = max(o["price_date"] for o in zoned)
    by_item = {}
    for o in zoned:
        if o["price_date"] == newest:
            by_item.setdefault(o["item"], []).append(o)
    return [{**rows[0], "zone": None, "where": f"median of {len(rows)} zones",
             "price_ngn_per_kg": round(median(o["price_ngn_per_kg"] for o in rows)),
             "low_ngn_per_kg": min(o["low_ngn_per_kg"] for o in rows),
             "high_ngn_per_kg": max(o["high_ngn_per_kg"] for o in rows),
             "package_price_ngn": None,
             "evidence": "; ".join(f"{o['zone']} N{o['low_ngn_per_kg']:,}-{o['high_ngn_per_kg']:,}/kg" for o in rows)}
            for rows in by_item.values()]


def choose(current, zone):
    """The price for a farm in `zone` (None = zone unknown) from current
    headline observations, with the basis it was chosen on."""
    def best(cands):
        return min(cands, key=lambda o: (LEVEL_ORDER.get(o["level"], 9), -date.fromisoformat(o["price_date"]).toordinal()))
    steps = []
    if zone:
        steps.append(("same zone", [o for o in current if o["zone"] == zone]))
    # "national": a wholesale price with no zone named, or the median across zones
    steps.append(("national", [o for o in current if o["zone"] is None and o["level"] != "retail"]))
    steps.append(("another zone" if zone else "one zone only", [o for o in current if o["zone"] != zone]))
    for basis, cands in steps:
        if cands:
            return {**best(cands), "basis": basis}
    return None


def headline_items(crop, today):
    items = CROPS[crop]["items"]
    if crop == "Onion":
        items = [i for i in items if i == "Onions" or i == onion_item(today)]
    return items


def price_label(o):
    """The line users see: price type, source and the price's real date, e.g.
    "Market price, wholesale · Business A.M. fresh-produce prices · 9 Oct 2026".
    A weekly report is dated by its week and a monthly survey by its month."""
    d, src = date.fromisoformat(o["price_date"]), SOURCES[o["source"]]
    when = {"day": f"{d.day} {d:%b %Y}", "week": f"week of {d.day} {d:%b %Y}", "month": f"{d:%b %Y}"}[src["period"]]
    return f"{PRICE_TYPE[o['level']].capitalize()} · {src['name']} · {when}"


def price_entry(o, today):
    if o is None:
        return None
    age = (today - date.fromisoformat(o["price_date"])).days
    return {"price_ngn_per_kg": o["price_ngn_per_kg"], "low_ngn_per_kg": o["low_ngn_per_kg"],
            "high_ngn_per_kg": o["high_ngn_per_kg"], "price_date": o["price_date"],
            "price_period": SOURCES[o["source"]]["period"], "age_days": age,
            "source": SOURCES[o["source"]]["name"], "source_key": o["source"], "level": o["level"],
            "price_type": PRICE_TYPE[o["level"]], "is_farm_gate": False, "label": price_label(o),
            "zone": o["zone"], "where": o["where"], "item": o["item"], "package": o["package"],
            "package_price_ngn": o["package_price_ngn"], "basis": o.get("basis"),
            "evidence": o["evidence"], "url": o["url"]}


def build(produce, wfp, today=None):
    today = today or datetime.now(timezone.utc).date()
    obs = produce_observations(produce) + wfp_observations(wfp)
    current = [o for o in obs
               if 0 <= (today - date.fromisoformat(o["price_date"])).days <= MAX_AGE_DAYS[o["source"]]]
    crops = {}
    for crop, spec in CROPS.items():
        mine = [o for o in current if o["crop"] == crop]
        items = headline_items(crop, today)
        head = [o for o in mine if o["item"] in items]
        head += zone_median(head)
        newest_other = {}
        for o in sorted(mine, key=lambda o: o["price_date"]):
            if o["item"] not in items:
                newest_other[(o["source"], o["item"], o["zone"])] = o
        crops[crop] = {
            "form": spec["form"], "aliases": spec["aliases"], "note": spec["note"],
            "headline": price_entry(choose(head, None), today),
            "by_zone": {z: price_entry(choose(head, z), today) for z in ZONE_STATES},
            "other_items": [price_entry(o, today) for o in newest_other.values()],
        }
    return {"as_of": today.isoformat(), "unit": "NGN/kg",
            "about": "Market prices for the crops AgLens values on farms, per kg of the form the farmer sells. "
                     "Not farm-gate prices: no regular source publishes those, so a harvest valued with these is "
                     "what it would fetch in a market. Only real, dated observations are used; a price older than "
                     "max_age_days is dropped.",
            "max_age_days": MAX_AGE_DAYS, "sources": SOURCES, "state_zones": STATE_ZONE,
            "state_aliases": STATE_ALIASES, "crops": crops}


def run():
    logger.info("=" * 60)
    logger.info("FARM CROP PRICES (NGN/kg for AgLens)")
    logger.info("=" * 60)
    produce = pd.read_csv(PRODUCE_PATH) if os.path.exists(PRODUCE_PATH) else pd.DataFrame(
        columns=["price_date", "crop", "item", "source", "level", "zone", "package", "package_kg",
                 "price_low_ngn", "price_high_ngn", "evidence", "url"])
    rep = build(produce, load_wfp())
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"farm_prices_{rep['as_of']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    for crop, e in rep["crops"].items():
        h = e["headline"]
        logger.info(f"  {crop:<13} " + (f"N{h['price_ngn_per_kg']:,}/kg  {h['price_date']}  {h['source']} "
                                        f"({h['level']}, {h['where']})" if h else "no current price"))
    logger.info(f"  -> {path}")
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error(f"Farm crop prices failed (pipeline continues): {e}")
    sys.exit(0)
