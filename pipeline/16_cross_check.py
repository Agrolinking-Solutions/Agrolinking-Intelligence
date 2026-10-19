"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Daily price cross-check: do independent web prices agree with our prices?

Every commodity on the platform is priced at WHOLESALE, in Naira per tonne
(Eggs: per crate of 30). Our price comes from Agricome, WFP or Agrolinking
field data. This step compares it with the wholesale and exchange prices the
web search found recently (one per website, its latest), and flags the
commodities where they disagree so a person checks them before customers do.

  agree         web prices within CHECK_ABOVE of ours
  check         CHECK_ABOVE to DISAGREE_ABOVE apart
  disagree      more than DISAGREE_ABOVE apart
  no web price  no usable wholesale/exchange web price found recently
  our price is old  our latest real price is older than MAX_SOURCE_AGE_DAYS,
                so there is nothing current to compare (web prices still listed)

Web prices only count if the model gave medium or high confidence, named a
URL, and the source date parses and is at most MAX_SOURCE_AGE_DAYS old.
Retail and unlabelled web prices are listed for context but not compared.
AFEX exchange prices (data/raw/afex_prices.csv, read daily from Mansa Markets
by fetch_afex_prices.py) are always included as one more website.

Writes outputs/logs/price_crosscheck.json (served by the API at /crosscheck).
Never blocks the pipeline; skips cleanly with no database.

Run: python pipeline/16_cross_check.py
"""

import os, sys, json, importlib.util
from datetime import datetime, timezone, timedelta
from statistics import median

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, WFP_NAME_MAP, READ_DIRECTLY
from pipeline.pg_commodities import ensure_web_estimates_schema

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

# Reuse the promotion step's date parsing and website naming, so "a usable
# source date" means the same thing in both places.
_spec = importlib.util.spec_from_file_location(
    "promote_web_prices", os.path.join(os.path.dirname(os.path.abspath(__file__)), "14_promote_web_prices.py"))
_promote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_promote)
parse_source_date, source_site = _promote.parse_source_date, _promote.source_site
load_blocked_sites = _promote.load_blocked_sites

# Our price = the latest REAL price the platform holds (what it validates
# forecasts against), including web prices already promoted by step 14.
PARTNER_SOURCES     = {"Agricome": "Agricome", "WFP": "WFP", "Agrolinking_primary": "Agrolinking field data",
                       "AFEX": "AFEX exchange", "FEWS_NET": "FEWS NET market monitor",
                       "Web_search": "a web price accepted earlier"}
COMPARE_LEVELS      = {"wholesale", "exchange"}
ACCEPT_CONFIDENCE   = {"high", "medium"}
LOOKBACK_DAYS       = 14     # web searches from the last two weeks
MAX_SOURCE_AGE_DAYS = 45     # a web source older than this is not current
CHECK_ABOVE         = 0.15
DISAGREE_ABOVE      = 0.30
OUT_PATH            = os.path.join(PATHS["logs_dir"], "price_crosscheck.json")
BASIS               = "Wholesale, NGN per tonne (Eggs: NGN per crate of 30)"


def wfp_coverage():
    """{our commodity: 'Borno, Yobe, Adamawa'} - states in WFP's latest month per commodity."""
    try:
        w = pd.read_csv(PATHS["raw_wfp"], low_memory=False)
        w = w[w["date"].astype(str).str[:1].str.isdigit()].copy()
        w["date"] = pd.to_datetime(w["date"])
        w["ours"] = w["commodity"].map(WFP_NAME_MAP)
        recent = w[w["date"] >= w["date"].max() - pd.Timedelta(days=120)]
        return {c: ", ".join(sorted(g["admin1"].dropna().unique())) for c, g in recent.groupby("ours")}
    except Exception as e:
        logger.warning(f"  could not read WFP market coverage: {e}")
        return {}


def our_prices(master, coverage):
    """Latest real price per commodity, with what kind of price it is."""
    out = {}
    real = master[master["data_source"].isin(PARTNER_SOURCES)].sort_values("date")
    for c in COMMODITIES:
        sub = real[real["commodity"] == c]
        if sub.empty:
            out[c] = None
            continue
        r = sub.iloc[-1]
        notes = str(r.get("notes") or "")
        if "wholesale-equivalent" in notes:
            level = "wholesale-equivalent"
        elif str(r.get("market_type") or "").lower() == "retail":
            level = "retail"
        else:
            level = "wholesale"
        where = PARTNER_SOURCES[r["data_source"]]
        if r["data_source"] == "WFP" and coverage.get(c):
            where = f"WFP markets in {coverage[c]}"
        out[c] = {"price": float(r["price_ngn_mt"]), "date": r["date"].date(),
                  "source": r["data_source"], "level": level, "coverage": where}
    return out


def usable(cand, today):
    """(ok, source_date) - medium+ confidence, a URL, and a recent parseable date."""
    if not cand.get("price") or cand["price"] <= 0:
        return False, None
    if str(cand.get("confidence") or "").strip().lower() not in ACCEPT_CONFIDENCE:
        return False, None
    if not (cand.get("source_url") or "").strip():
        return False, None
    sd = parse_source_date(cand.get("source_date"))
    if sd is None or sd > today or (today - sd).days > MAX_SOURCE_AGE_DAYS:
        return False, sd
    return True, sd


AFEX_PATH = os.path.join(os.path.dirname(PATHS["master"]), "..", "raw", "afex_prices.csv")
AFEX_NOTES = {
    "maize":  "AFEX does not say whether its maize is white or yellow.",
    "ginger": "AFEX ginger is a different grade (fresh or unsplit) from Agricome's dried split ginger, "
              "so it is listed for reference but not compared.",
}
# AFEX prices for a different product than ours: listed, never compared. AFEX
# ginger was 990K/tonne against Agricome's 8.0M dried split (16 Oct 2026) - the
# usual fresh-to-dried ratio, not a price disagreement.
AFEX_DIFFERENT_GRADE = {"ginger"}


AFEX_DAYS = 5          # AFEX's median over its last 5 trading days (within AFEX_WINDOW_DAYS)
AFEX_WINDOW_DAYS = 10  # one day's figure swings: soybean 598K -> 645K -> 549K over 14-16 Oct 2026


def afex_candidates(path=AFEX_PATH):
    """AFEX exchange price per Mansa commodity - the median of its last AFEX_DAYS trading days -
    as candidates for each mapped commodity."""
    from pipeline.fetch_afex_prices import MANSA_TO_OURS
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path)
    df = df[df["price_type"] == "exchange"].drop_duplicates(["mansa_commodity", "as_of_date"], keep="last")
    df = df.sort_values("as_of_date")
    out = []
    for slug, g in df.groupby("mansa_commodity"):
        r = g.iloc[-1]
        last = pd.to_datetime(g["as_of_date"])
        recent = g[last >= last.max() - pd.Timedelta(days=AFEX_WINDOW_DAYS)].tail(AFEX_DAYS)
        price = float(recent["price_ngn_mt"].median())
        for ours in MANSA_TO_OURS.get(slug, []):
            out.append({"commodity": ours, "query_date": r["as_of_date"], "price": price,
                        "afex_days": len(recent),
                        "source_url": r["url"], "source_date": r["as_of_date"], "confidence": "high",
                        "price_level": "exchange, different grade" if slug in AFEX_DIFFERENT_GRADE else "exchange",
                        "market_location": "national (AFEX exchange)",
                        "note": AFEX_NOTES.get(slug), "is_afex": True})
    return out


def latest_per_site(cands):
    best = {}
    for c in cands:
        site = source_site(c["source_url"])
        if site not in best or (c["_sd"], str(c["query_date"])) > (best[site]["_sd"], str(best[site]["query_date"])):
            best[site] = c
    return list(best.values())


def status_for(gap):
    a = abs(gap)
    return "agree" if a <= CHECK_ABOVE else ("check" if a <= DISAGREE_ABOVE else "disagree")


def build_report(master, candidates, today, coverage=None):
    ours = our_prices(master, coverage or {})
    blocked = load_blocked_sites()
    report = {}
    for c in COMMODITIES:
        mine = ours.get(c)
        pool = []
        for cand in candidates:
            if cand["commodity"] != c:
                continue
            if cand.get("is_afex") and mine and mine["source"] == "AFEX":
                continue    # our price IS the AFEX price - nothing to compare
            if not cand.get("is_afex") and source_site(cand.get("source_url")) in READ_DIRECTLY:
                continue    # a web-search copy of a site the AFEX reader covers (afex_candidates)
            ok, sd = usable(cand, today)
            if ok and source_site(cand.get("source_url")) not in blocked:
                pool.append({**cand, "_sd": sd})
        level = lambda x: str(x.get("price_level") or "").strip().lower()
        compared = latest_per_site([p for p in pool if level(p) in COMPARE_LEVELS])
        compared_sites = {source_site(p["source_url"]) for p in compared}
        context = latest_per_site([p for p in pool if level(p) not in COMPARE_LEVELS
                                   and source_site(p["source_url"]) not in compared_sites])

        def show(p):
            return {"price": round(p["price"]), "site": source_site(p["source_url"]), "url": p["source_url"],
                    "source_date": str(p["_sd"]), "level": level(p) or "not recorded",
                    "location": p.get("market_location"), "confidence": p.get("confidence")}

        entry = {"our_price": round(mine["price"]) if mine else None,
                 "our_date": str(mine["date"]) if mine else None,
                 "our_source": mine["source"] if mine else None,
                 "our_level": mine["level"] if mine else None,
                 "our_coverage": mine["coverage"] if mine else None,
                 "web_prices": [show(p) for p in sorted(compared, key=lambda p: p["price"])],
                 "other_web_prices": [show(p) for p in sorted(context, key=lambda p: p["price"])]}
        stale = mine is not None and (today - mine["date"]).days > MAX_SOURCE_AGE_DAYS
        if not mine:
            entry.update(status="no partner price", web_median=None, gap_pct=None)
        elif stale:
            entry.update(status="our price is old", web_median=(round(median(p["price"] for p in compared))
                                                              if compared else None), gap_pct=None)
        elif not compared:
            entry.update(status="no web price", web_median=None, gap_pct=None)
        else:
            # The exchange price leads when we have one: averaging it with a
            # single web article let one Lagos quote (800K) turn an AFEX
            # agreement (-10%) into a "disagree" for white maize (16 Oct 2026).
            afex = [p for p in compared if p.get("is_afex")]
            ref = afex if afex else compared
            med = median(p["price"] for p in ref)
            gap = med / mine["price"] - 1
            entry.update(status=status_for(gap), web_median=round(med), gap_pct=round(gap * 100, 1),
                         compared_with="AFEX exchange" if afex else f"web prices from {len(ref)} site(s)")
        notes = []
        if mine and mine["level"] == "retail":
            notes.append("Our price is a shop (retail) price: WFP publishes no wholesale price for this. "
                         "Wholesale should normally be below it.")
        if stale:
            notes.append(f"Our latest real price is {(today - mine['date']).days} days old - "
                         f"nothing current to compare against.")
        if mine and mine["source"] == "Web_search":
            notes.append("Our latest price itself came from the web, so this compares web sources with each other.")
        notes += sorted({p["note"] for p in compared + context if p.get("note")})
        others = sum(1 for p in compared if not p.get("is_afex"))
        if mine and others and any(p.get("is_afex") for p in compared):
            notes.append(f"Status uses the AFEX price; {others} other web price(s) listed for reference.")
        afex_days = max((p.get("afex_days") or 0 for p in compared + context if p.get("is_afex")), default=0)
        if afex_days > 1:
            notes.append(f"The AFEX price is the middle of its last {afex_days} trading days, "
                         "so one day's swing doesn't decide the status.")
        if entry["web_prices"] and len(entry["web_prices"]) == 1:
            notes.append("Only one website - treat the comparison as a hint, not proof.")
        entry["notes"] = notes
        report[c] = entry
    counts = {}
    for e in report.values():
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    return {"run_date": str(today), "basis": BASIS,
            "thresholds": {"check_above_pct": CHECK_ABOVE * 100, "disagree_above_pct": DISAGREE_ABOVE * 100,
                           "web_lookback_days": LOOKBACK_DAYS, "max_source_age_days": MAX_SOURCE_AGE_DAYS},
            "summary": counts, "commodities": report}


def fetch_candidates(conn, since):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.name, w.query_date, w.price_ngn_mt, w.source_url, w.source_date,
                   w.model_confidence, w.price_level, w.market_location
            FROM web_price_estimates w
            JOIN commodities c ON c.commodity_id = w.commodity_id
            WHERE w.query_date >= %s AND w.price_ngn_mt IS NOT NULL
        """, (since,))
        rows = cur.fetchall()
    return [{"commodity": r[0], "query_date": r[1], "price": float(r[2]), "source_url": r[3],
             "source_date": r[4], "confidence": r[5], "price_level": r[6], "market_location": r[7]}
            for r in rows]


def log_report(rep):
    logger.info(f"  Basis: {rep['basis']}")
    order = {"disagree": 0, "check": 1, "agree": 2, "our price is old": 3, "no web price": 4, "no partner price": 5}
    for c, e in sorted(rep["commodities"].items(), key=lambda kv: (order.get(kv[1]["status"], 9), kv[0])):
        if e["gap_pct"] is None:
            logger.info(f"    {e['status']:<16} {c:<16} ours {e['our_price'] or 0:>12,} ({e['our_source']}, {e['our_level']})")
        else:
            logger.info(f"    {e['status']:<16} {c:<16} ours {e['our_price']:>12,} ({e['our_source']}, {e['our_level']}) "
                        f"| web {e['web_median']:>12,} from {len(e['web_prices'])} site(s) | {e['gap_pct']:+.0f}%")
    logger.info(f"  Summary: {rep['summary']}")


def run_cross_check():
    logger.info("=" * 60)
    logger.info("STEP 16 - PRICE CROSS-CHECK (our price vs independent web prices)")
    logger.info("=" * 60)
    today = datetime.now(timezone.utc).date()

    candidates = afex_candidates()
    logger.info(f"  AFEX exchange prices: {len(candidates)} comparison(s)")
    db_url = os.environ.get("TSDB_URL")
    if not db_url and not os.environ.get("PGHOST"):
        logger.warning("  TSDB_URL/PGHOST not set - web search prices skipped, AFEX only")
    else:
        try:
            import psycopg2
            conn = psycopg2.connect() if os.environ.get("PGHOST") else psycopg2.connect(db_url)
            try:
                with conn.cursor() as cur:
                    ensure_web_estimates_schema(cur)
                conn.commit()
                candidates += fetch_candidates(conn, today - timedelta(days=LOOKBACK_DAYS))
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"  Could not read web search prices - AFEX only this run: {e}")

    master = pd.read_csv(PATHS["master"], parse_dates=["date"])
    rep = build_report(master, candidates, today, wfp_coverage())
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(rep, f, indent=2, default=str)
    log_report(rep)
    return True


if __name__ == "__main__":
    run_cross_check()
    sys.exit(0)
