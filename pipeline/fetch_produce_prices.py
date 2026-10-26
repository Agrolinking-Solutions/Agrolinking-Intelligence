"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Fresh-produce prices for the farm crops AgLens values (not forecast commodities)

AgLens puts a naira value on each farm's harvest. For tomato, pepper, onion
and sweet potato PIS had no price; two public sources publish one every week:

  - ColdHubs "Market Weekly" (coldhubs.com): a table of bag prices by
    geopolitical zone, from ColdHubs' hub managers - tomatoes per 50 kg,
    pepper per 25 kg. ColdHubs does not say wholesale or retail; they are
    bulk bag prices. North-East is usually blank.
  - Business A.M. (businessamlive.com): a weekly write-up of the same
    14-item wholesale list (ordinary/hybrid tomatoes, wet/dry-season onions,
    tatashe, atarugu, shombo, sweet potatoes ...), with bag sizes in kg. The
    market is not named. The prices are written as prose, so they are read
    with strict patterns: a clause must name exactly one of our items and
    nothing else from the list, and an item whose figures disagree within an
    article is skipped. Anything unclear is left out, never guessed. The
    clause each price came from is saved as evidence.

Both are read through the sites' public WordPress API (robots.txt allows it;
Business A.M. asks for 5 s between requests). New dated rows are appended to
data/raw/produce_prices.csv. pipeline/farm_prices.py turns them, with WFP's
monthly prices, into a price per kg per crop.

Never fails the pipeline. Run: python pipeline/fetch_produce_prices.py
"""

import os, re, sys, csv, html, time
from datetime import date, datetime, timedelta, timezone

import requests
from loguru import logger

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE, "data", "raw", "produce_prices.csv")
HEADERS  = {"User-Agent": "Agrolinking-Intelligence/1.0 (daily price reader; contact: john@agrolinking.com)"}
COLDHUBS_API = "https://coldhubs.com/wp-json/wp/v2/posts?search=market&per_page=20&_fields=date,link,title,content"
BAM_API      = "https://businessamlive.com/wp-json/wp/v2/posts?search=kg%20bag&per_page=20&_fields=date,link,title,content"
LOOKBACK_DAYS = 120          # older posts are not read again
COLUMNS = ["price_date", "crop", "item", "source", "level", "zone", "package", "package_kg",
           "price_low_ngn", "price_high_ngn", "evidence", "url", "published", "retrieved_on"]

COLDHUBS_ZONES = {"n/central": "North Central", "n/east": "North East", "n/west": "North West",
                  "s/east": "South East", "s/south": "South South", "s/west": "South West"}
COLDHUBS_ITEMS = {"tomato": "Tomato", "pepper": "Pepper"}      # produce name prefix -> crop

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
# Business A.M.'s list: (crop, item, how the articles name it). "Big ordinary
# tomatoes" is a separate grade, not ordinary tomatoes.
BAM_ITEMS = [
    ("Tomato",       "ordinary tomatoes", r"(?<!big )\bordinary tomato(?:es)?\b"),
    ("Tomato",       "hybrid tomatoes",   r"\bhybrid tomato(?:es)?\b"),
    ("Onion",        "wet-season onions", r"\bwet[- ]season onions?\b"),
    ("Onion",        "dry-season onions", r"\bdry[- ]season onions?\b"),
    ("Pepper",       "tatashe",           r"\btatashe\b"),
    ("Pepper",       "atarugu (rodo)",    r"\batarugu\b|\brodo\b"),
    ("Pepper",       "shombo (bawa)",     r"\bshombo\b|\bbawa\b"),
    ("Sweet potato", "sweet potatoes",    r"\bsweet potato(?:es)?\b"),
]
# Other items on the same list: a clause that also names one of these is skipped.
BAM_OTHERS = re.compile(r"\b(?:big ordinary tomato|irish potato|maize|okr[oa]|cabbage|carrot|cucumber)", re.I)
# Where a sentence moves on to another item.
CLAUSE_BREAK = re.compile(r",?\s+(?:while|whereas|but|compared with|compared to|against|versus)\s+|;\s+", re.I)
NAIRA   = r"[N₦]\s?(\d{1,3}(?:,\d{3})+|\d+)"
FROM_TO = re.compile(rf"\bfrom {NAIRA}\b.{{0,45}}?\bto {NAIRA}", re.I)
AT      = re.compile(rf"\b(?:at|to|cost|costs)\s+{NAIRA}", re.I)
PER_KG  = re.compile(r"\s+per\s+kilo", re.I)                 # "N1,600 per kilogram" is not a bag price
SIZE    = re.compile(r"\b(\d+)\s*-?\s*(?:kg|kilogrammes?|kilograms?)\b\s*(bag|basket|crate)?", re.I)


def text_of(fragment):
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())
    return re.sub(r"\(adsbygoogle.*?\);", "", text).strip()


def naira(s):
    return int(s.replace(",", ""))


# ── ColdHubs ─────────────────────────────────────────────────────────────

def parse_coldhubs(content, published, url):
    """Rows from the zone table of one ColdHubs report. The bag size is in the
    produce cell ("Tomatoes (50kg)", "Tomatoes 50kg") or, in later editions,
    only in the text ("Tomatoes (50kg)"); a crop without one is skipped."""
    text = text_of(content)
    rows, header = [], None          # header: (row length, produce column, {column: zone})
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S):
        cells = [text_of(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        keys = [c.lower().replace(" ", "") for c in cells]
        if "produce" in keys and sum(k in COLDHUBS_ZONES for k in keys) >= 5:
            header = (len(cells), keys.index("produce"),
                      {i: COLDHUBS_ZONES[k] for i, k in enumerate(keys) if k in COLDHUBS_ZONES})
            continue
        if header is None or len(cells) != header[0]:
            header = None            # a section row ends the zone table
            continue
        _, produce_col, zones = header
        name = cells[produce_col]
        crop = next((c for p, c in COLDHUBS_ITEMS.items() if name.lower().startswith(p)), None)
        if not crop:
            continue
        size = re.search(r"(\d+)\s*kg", name, re.I) or \
            re.search(rf"{re.escape(name.split()[0])}\s*\(\s*(\d+)\s*kg\s*\)", text, re.I)
        if not size:
            logger.warning(f"  ColdHubs {published}: no bag size for {name} - skipped")
            continue
        kg = float(size.group(1))
        for i, zone in zones.items():
            amounts = [naira(a) for a in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", cells[i])]
            if not amounts or len(amounts) > 2:
                continue                                       # "–" = no price this week
            rows.append({"price_date": published, "crop": crop, "item": re.sub(r"\s*\(?\d+\s*kg\)?", "", name).strip(),
                         "source": "ColdHubs", "level": "bulk bag", "zone": zone,
                         "package": f"{kg:g} kg bag", "package_kg": kg,
                         "price_low_ngn": min(amounts), "price_high_ngn": max(amounts),
                         "evidence": f"{name} | {zone}: {cells[i]}", "url": url, "published": published})
    return rows


# ── Business A.M. ────────────────────────────────────────────────────────

def bam_price_date(text, published):
    """The newest date the article gives for its prices ("between October 5 and
    October 9, 2026", "in the week to September 11"), no later than the post
    and no more than 14 days before it; otherwise the post date."""
    pub = date.fromisoformat(published)
    best = None
    for m in re.finditer(rf"\b({'|'.join(MONTHS)})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text[:1200], re.I):
        try:
            d = date(pub.year, MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)))
        except ValueError:
            continue
        if d > pub:
            d = d.replace(year=pub.year - 1)
        if pub - timedelta(days=14) <= d <= pub and (best is None or d > best):
            best = d
    return (best or pub).isoformat()


def bam_clause_price(clause):
    """(price, 'from_to'|'at') for the bag price in one clause, or None."""
    m = FROM_TO.search(clause)
    if m and not PER_KG.match(clause[m.end():]):
        return naira(m.group(2)), "from_to"
    found = {naira(m.group(1)) for m in AT.finditer(clause) if not PER_KG.match(clause[m.end():])}
    return (found.pop(), "at") if len(found) == 1 else None


def parse_bam(content, published, url):
    """Rows for our items from one Business A.M. article (see the module notes)."""
    text = text_of(content)
    price_date = bam_price_date(text, published)
    found, sizes, containers = {}, {}, {}
    for sentence in re.split(r"\.\s+(?=[A-Z])", text):
        for clause in CLAUSE_BREAK.split(sentence):
            items = [(c, i) for c, i, pattern in BAM_ITEMS if re.search(pattern, clause, re.I)]
            if len(items) != 1 or BAM_OTHERS.search(clause):
                continue
            item = items[0]
            for m in SIZE.finditer(clause):
                sizes.setdefault(item, set()).add(float(m.group(1)))
                if m.group(2):
                    containers.setdefault(item, m.group(2).lower())
            price = bam_clause_price(clause)
            if price:
                found.setdefault(item, []).append((price, clause))
    rows = []
    for (crop, item), hits in found.items():
        values = {p for (p, _), _ in hits}
        if len(values) > 1:
            values = {p for (p, kind), _ in hits if kind == "from_to"}
        known = sizes.get((crop, item), set())
        if len(values) != 1 or len(known) != 1:
            logger.warning(f"  Business A.M. {published}: {item} skipped "
                           f"(prices {sorted({p for (p, _), _ in hits})}, sizes {sorted(known)})")
            continue
        price, kg = values.pop(), known.pop()
        container = containers.get((crop, item), "bag")
        clause = next(c for (p, _), c in hits if p == price)
        rows.append({"price_date": price_date, "crop": crop, "item": item, "source": "Business A.M.",
                     "level": "wholesale", "zone": "", "package": f"{kg:g} kg {container}", "package_kg": kg,
                     "price_low_ngn": price, "price_high_ngn": price, "evidence": clause[:300],
                     "url": url, "published": published})
    return rows


# ── Saving ───────────────────────────────────────────────────────────────

def key(r):
    return (r["source"], r["crop"], r["item"], r["zone"], r["price_date"])


def existing_keys():
    if not os.path.exists(OUT_PATH):
        return set()
    with open(OUT_PATH, newline="", encoding="utf-8") as f:
        return {key(r) for r in csv.DictReader(f)}


def save(rows):
    seen, today = existing_keys(), datetime.now(timezone.utc).date().isoformat()
    new = []
    for r in rows:
        if key(r) not in seen:
            seen.add(key(r))
            new.append({**r, "retrieved_on": today})
    if new:
        write_header = not os.path.exists(OUT_PATH)
        with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            if write_header:
                w.writeheader()
            w.writerows(new)
    return new


def posts(api):
    r = requests.get(api, headers=HEADERS, timeout=60)
    r.raise_for_status()
    since = (datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    return [p for p in r.json() if p["date"][:10] >= since]


def run():
    logger.info("=" * 60)
    logger.info("FRESH-PRODUCE PRICES (ColdHubs weekly, Business A.M. weekly)")
    logger.info("=" * 60)
    rows = []
    for name, api, parse in (("ColdHubs", COLDHUBS_API, parse_coldhubs), ("Business A.M.", BAM_API, parse_bam)):
        try:
            got = [r for p in posts(api) for r in parse(p["content"]["rendered"], p["date"][:10], p["link"])]
            logger.info(f"  {name}: {len(got)} prices read")
            rows += got
        except Exception as e:
            logger.warning(f"  {name}: not read ({e})")
        time.sleep(5)
    new = save(rows)
    for r in new:
        logger.info(f"  + {r['price_date']} {r['source']:<13} {r['crop']:<12} {r['item']:<18} "
                    f"{r['zone'] or '-':<13} N{r['price_low_ngn']:,}-{r['price_high_ngn']:,} per {r['package']}")
    logger.info(f"  {len(new)} new rows -> {OUT_PATH}")
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error(f"Produce price reader failed (pipeline continues): {e}")
    sys.exit(0)
