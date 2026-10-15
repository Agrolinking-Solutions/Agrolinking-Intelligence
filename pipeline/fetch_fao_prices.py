"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Nigerian market prices from FAO's Food Price Monitoring tool (FPMA / GIEWS)

FAO republishes two real Nigerian price sources through a public API:
  - FEWS NET weekly WHOLESALE prices for named markets: Kano (Dawanau),
    Ibadan, Kaura Namoda (Zamfara) and Maiduguri still report (to Aug 2026,
    ~7 weeks behind); Lagos and Giwa stopped in 2024-25. White maize,
    sorghum, imported rice and milled (local) rice.
  - NBS monthly RETAIL national and zone averages (beans, eggs, fish, rice...).

This saves the full weekly/monthly history of the series below to
data/raw/fao_fpma_prices.csv (rewritten each run - FAO revises past weeks).
It does not feed the price history yet; that is a decision per commodity.

Known issue: from 2025 FEWS NET's milled-rice series for Ibadan, Kaura
Namoda and Maiduguri are labelled "50 kg" but their prices (~N102-110K)
match the older 100 kg series and run twice imported rice per kg. They are
kept, flagged unit_suspect=True, and should not be used until confirmed.

Never fails the pipeline. Run: python pipeline/fetch_fao_prices.py
"""

import os, sys, csv, time
from datetime import datetime, timezone

import requests
from loguru import logger

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE, "data", "raw", "fao_fpma_prices.csv")
API      = "https://fpma.fao.org/giews/v4/global/price_module/api/v1"
HEADERS  = {"User-Agent": "Agrolinking-Intelligence/1.0 (daily price reader; contact: john@agrolinking.com)"}
SINCE    = "2015-01-01"

# (FAO commodity name, price type) -> our commodity it describes
WANTED = {
    ("Maize (white)", "WHOLESALE"):      "Maize (white)",
    ("Sorghum (white)", "WHOLESALE"):    "Sorghum",
    ("Rice (imported)", "WHOLESALE"):    "Rice (foreign)",
    ("Rice (milled)", "WHOLESALE"):      "Rice (local)",
    ("Beans (black-eyed)", "RETAIL"):    "Beans (white)",
    ("Eggs (medium size)", "RETAIL"):    "Eggs",
    ("Fish (tilapia)", "RETAIL"):        "Fish (dried)",
    ("Beef (bone-in)", "RETAIL"):        "Meat (beef)",
    ("Rice (imported)", "RETAIL"):       "Rice (foreign)",
    ("Rice (milled)", "RETAIL"):         "Rice (local)",
    ("Maize (white)", "RETAIL"):         "Maize (white)",
}
RETAIL_MARKETS = {"National Average"}      # retail: national averages only
UNIT_KG = {"kg": 1.0, "100 kg": 100.0, "50 kg": 50.0, "2 kg": 2.0}
SUSPECT_UNIT = {("Rice (milled)", "50 kg")}
COLUMNS = ["date", "our_commodity", "fao_commodity", "price_type", "market", "admin_unit", "unit",
           "price_value", "price_ngn_mt", "periodicity", "source_name", "unit_suspect",
           "series_uuid", "retrieved_on"]


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=90)
    r.raise_for_status()
    return r.json()


def run():
    logger.info("=" * 60)
    logger.info("FAO FPMA - Nigerian market prices (FEWS NET wholesale, NBS retail)")
    logger.info("=" * 60)
    series = get(f"{API}/FpmaSerieDomestic/?iso3_country_code=NGA")["results"]
    keep = {}
    for s in series:
        ours = WANTED.get((s["commodity_name"], s["price_type"]))
        if not ours:
            continue
        if s["price_type"] == "RETAIL" and s["market_name"] not in RETAIL_MARKETS:
            continue
        keep[s["uuid"]] = (ours, s)
    logger.info(f"  {len(keep)} series selected of {len(series)} Nigerian series")

    rows, today = [], datetime.now(timezone.utc).date().isoformat()
    uuids = list(keep)
    for i in range(0, len(uuids), 15):
        batch = uuids[i:i + 15]
        data = get(f"{API}/FpmaSeriePrice/?uuid__in={','.join(batch)}")
        for item in (data["results"] if isinstance(data, dict) else data):
            ours, s = keep[item["uuid"]]
            unit = s["measure_unit_label"]
            per_kg = UNIT_KG.get(unit.lower()) if unit.lower() != "1 unit" else None
            pts = item["datapoints"]
            weekly = [p for p in pts if p["periodicity"] == "weekly"]
            for p in (weekly or pts):
                if p["date"] < SINCE or p.get("price_value") is None:
                    continue
                mt = None
                if ours == "Eggs" and unit.lower() == "1 unit":
                    mt = p["price_value"] * 30           # per egg -> per crate of 30, like the platform
                elif per_kg:
                    mt = p["price_value"] / per_kg * 1000
                rows.append([p["date"], ours, s["commodity_name"], s["price_type"].lower(),
                             s["market_name"], s["admin_unit"], unit, p["price_value"],
                             None if mt is None else round(mt, 2), p["periodicity"], s["source_name"],
                             (s["commodity_name"], unit) in SUSPECT_UNIT, item["uuid"], today])
        time.sleep(1)
    rows.sort(key=lambda r: (r[1], r[4], r[3], r[0]))
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows)
    latest = {}
    for r in rows:
        if r[3] == "wholesale" and not r[11]:
            latest[(r[1], r[4])] = r
    for (c, m), r in sorted(latest.items()):
        logger.info(f"  {c:<15} {m:<13} {r[0]}  N{r[8]:>11,.0f}/t  ({r[7]:,.0f} per {r[6]})")
    logger.success(f"FAO FPMA DONE - {len(rows):,} price points saved to {os.path.relpath(OUT_PATH, BASE)}")
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.warning(f"FAO price reader failed this run: {e}")
    sys.exit(0)
