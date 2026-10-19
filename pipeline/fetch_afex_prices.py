"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Daily AFEX exchange prices (Nigeria), read from Mansa Markets

Mansa Markets publishes the AFEX commodity exchange's daily Nigerian price for
maize, sorghum, soybean, rice, cocoa, ginger, cashew and sesame, each with its
"as of" date, in a plain table on its public commodity pages (robots.txt
allows /commodities). This reads those tables directly - no AI, no guessing -
and appends new dated rows to data/raw/afex_prices.csv. Nigerian FAO FPMA
retail rows on the same pages are kept too, labelled as such.

How the prices are used (see MANSA_TO_OURS):
  - 16_cross_check compares our prices with the AFEX price every day.
  - They are NOT yet fed into the price history as real prices: AFEX's maize
    grade (white or yellow) is not stated, its rice is probably paddy, and its
    ginger and soybean grades differ from Agricome's. That is a decision for a
    person, per commodity.

The same pages show a monthly world benchmark price (US$/tonne); it is saved
to data/raw/world_prices.csv for the price-drivers panel. Mansa's own "+x%"
figure has no stated period, so it is stored but not used; changes are
measured from our own daily record.

Never fails the pipeline. Run: python pipeline/fetch_afex_prices.py
"""

import os, re, sys, csv, html, time
from datetime import datetime, timezone

import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE, "data", "raw", "afex_prices.csv")
URL      = "https://www.mansamarkets.com/commodities/{slug}"
HEADERS  = {"User-Agent": "Agrolinking-Intelligence/1.0 (daily price reader; contact: john@agrolinking.com)"}
COLUMNS  = ["as_of_date", "mansa_commodity", "country", "price_type", "price_ngn_mt",
            "price_as_shown", "source", "url", "retrieved_on"]
WORLD_PATH = os.path.join(BASE, "data", "raw", "world_prices.csv")
WORLD_COLUMNS = ["as_of_date", "mansa_commodity", "usd_per_tonne", "change_pct_shown", "source", "url", "retrieved_on"]

# Mansa page -> our commodities it can be compared with. Empty = stored only.
MANSA_TO_OURS = {
    "maize":   ["Maize (white)", "Maize (yellow)"],   # grade not stated on the page
    "sorghum": ["Sorghum"],
    "soybean": ["Soybeans"],
    "cocoa":   ["Cocoa"],
    "cashew":  ["Cashew Nuts"],
    "sesame":  ["Sesame"],
    "ginger":  ["Ginger"],
    "rice":    [],     # AFEX trades paddy rice; ours is milled - not comparable
    "wheat":   [],     # no Nigerian price on the page (global benchmark only)
}
UNIT_KG = {"kg": 1.0, "100 kg": 100.0, "50 kg": 50.0, "mt": 1000.0, "tonne": 1000.0, "metric ton": 1000.0}


def parse_rows(page):
    """[(country, type, price_as_shown, source, as_of)] from the page's price table."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) == 5 and cells[0] != "Country":
            out.append(tuple(cells))
    return out


def to_ngn_mt(shown):
    """'407.34 NGN / kg' -> 407340.0; None unless it is a Naira price per a known weight."""
    m = re.match(r"([\d,]+(?:\.\d+)?)\s*NGN\s*/\s*(.+)$", shown.strip(), re.I)
    if not m:
        return None
    per = UNIT_KG.get(m.group(2).strip().lower())
    return None if per is None else float(m.group(1).replace(",", "")) / per * 1000.0


def parse_benchmark(page):
    """(usd_per_tonne, change_pct_shown, as_of) from the page's 'Global benchmark' block, or None."""
    i = page.find("Global benchmark")
    if i < 0:
        return None
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page[i:i + 1500])).split())
    m = re.search(r"Global benchmark \$([\d,]+(?:\.\d+)?)\s*([+-−]\s*[\d.]+)?\s*%?\s*USD per metric ton.*?(\d{1,2} [A-Z][a-z]{2} \d{4})", text)
    if not m:
        return None
    chg = m.group(2).replace("−", "-").replace(" ", "") if m.group(2) else None
    return float(m.group(1).replace(",", "")), chg, datetime.strptime(m.group(3), "%d %b %Y").date().isoformat()


def save_world(rows):
    seen = set()
    if os.path.exists(WORLD_PATH):
        with open(WORLD_PATH, newline="", encoding="utf-8") as f:
            seen = {(r["mansa_commodity"], r["as_of_date"]) for r in csv.DictReader(f)}
    new = [r for r in rows if (r[1], r[0]) not in seen]
    if new:
        write_header = not os.path.exists(WORLD_PATH)
        with open(WORLD_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(WORLD_COLUMNS)
            w.writerows(new)
    return len(new)


def existing_keys():
    """({(commodity, price type, as-of date)}, {(commodity, price type): newest as-of date}) already saved.

    The source label is not part of the key: Mansa relabelled AFEX's row from
    "AFEX (daily)" to "Mansa Markets (daily)" on 15 Oct 2026, and on 19 Oct
    its soybean and ginger pages showed an OLDER as-of date (14 Oct) again,
    so the same day was saved twice with different prices."""
    if not os.path.exists(OUT_PATH):
        return set(), {}
    seen, newest = set(), {}
    with open(OUT_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["mansa_commodity"], r["price_type"])
            seen.add(k + (r["as_of_date"],))
            newest[k] = max(newest.get(k, ""), r["as_of_date"])
    return seen, newest


def run():
    logger.info("=" * 60)
    logger.info("AFEX EXCHANGE PRICES (Nigeria) via Mansa Markets")
    logger.info("=" * 60)
    (seen, newest), new_rows, world_rows = existing_keys(), [], []
    today = datetime.now(timezone.utc).date().isoformat()
    for slug in MANSA_TO_OURS:
        url = URL.format(slug=slug)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            logger.warning(f"  {slug:<8} could not read page: {e}")
            continue
        bench = parse_benchmark(r.text)
        if bench:
            world_rows.append([bench[2], slug, bench[0], bench[1], "Mansa Markets global benchmark", url,
                               datetime.now(timezone.utc).date().isoformat()])
        rows = [x for x in parse_rows(r.text) if x[0] == "Nigeria"]
        if not rows:
            logger.info(f"  {slug:<8} no Nigerian price on the page")
        for country, ptype, shown, source, as_of in rows:
            try:
                as_of_date = datetime.strptime(as_of, "%d %b %Y").date().isoformat()
            except ValueError:
                logger.warning(f"  {slug:<8} unreadable date {as_of!r} - skipped")
                continue
            mt = to_ngn_mt(shown)
            if mt is None:
                logger.warning(f"  {slug:<8} unreadable price {shown!r} - skipped")
                continue
            key = (slug, ptype, as_of_date)
            older = as_of_date < newest.get((slug, ptype), "")
            flag = ("already saved" if key in seen else
                    f"older than the {newest[(slug, ptype)]} price already saved - skipped" if older else "new")
            logger.info(f"  {slug:<8} {ptype:<9} {shown:<18} {source:<22} as of {as_of_date}  ({flag})")
            if key not in seen and not older:
                seen.add(key)
                new_rows.append([as_of_date, slug, country, ptype, round(mt, 2), shown, source, url, today])
        time.sleep(1)   # be polite: one page a second
    if new_rows:
        write_header = not os.path.exists(OUT_PATH)
        with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(COLUMNS)
            w.writerows(new_rows)
    n_world = save_world(world_rows)
    logger.info(f"  World benchmarks: {', '.join(f'{r[1]} ${r[2]:,.0f}/t' for r in world_rows)} ({n_world} new)")
    logger.success(f"AFEX PRICES DONE - {len(new_rows)} new row(s) saved to {os.path.relpath(OUT_PATH, BASE)}")
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.warning(f"AFEX price reader failed this run: {e}")
    sys.exit(0)
