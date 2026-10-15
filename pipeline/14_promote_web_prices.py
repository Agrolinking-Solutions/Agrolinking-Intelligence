"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Promote web-sourced prices to fill stale commodities

10_web_price_search.py collects dated, named-source prices into the isolated
web_price_estimates table. This step decides which of them are good enough to
be used as REAL data, and only for commodities whose Agricome/WFP price has
gone stale. Accepted prices are appended to data/raw/web_prices.csv, which
01_ingest.py reads as a third source ("Web_search") that always loses to
Agricome/WFP for the same week.

A web price is promoted only if ALL of these hold:
  - the commodity has no fresh real price: Agricome/primary older than
    FRESH_DAYS and WFP older than FRESH_DAYS_WFP
  - the model's own confidence is "high" or "medium"
  - it is a wholesale or exchange price (the platform's price basis);
    retail, farm-gate and rows saved before the level was recorded are not
  - it names a source URL
  - its source date parses, is not in the future, and is at most
    MAX_SOURCE_AGE_DAYS old
  - it is newer than the latest real price we already hold
  - it is within a sanity band of that latest real price (wider the staler
    that price is), so a wrong unit or a hallucinated number can't move us.
    Where a commodity has no recent Agricome/WFP price of its own, the band
    is centred on its latest accepted web price, else on a sibling
    commodity's real price (SIBLING_YARDSTICK), not on a World Bank estimate

Everything else is rejected with a reason. Every decision (accepted or
rejected) is written to outputs/logs/web_price_promotion.json for review.

Skips cleanly (exit 0) when no database is configured.

Run: python pipeline/14_promote_web_prices.py
"""

import os, sys, csv, json
from datetime import datetime, timezone, timedelta

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES
from pipeline.pg_commodities import ensure_web_estimates_schema

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

HIGH_QUALITY_SOURCES = {"Agricome", "WFP", "Agrolinking_primary"}
ANY_REAL_SOURCES     = HIGH_QUALITY_SOURCES | {"Web_search"}
# Baseline for the sanity band / "is it newer" checks: the latest Agricome/
# WFP/primary price or World Bank estimate. Earlier web prices are NOT a
# baseline: when two withdrawn web rows were still in master from the
# previous run, the same prices came back the next day measured against
# themselves (0% move) and were accepted again.
BASELINE_SOURCES     = HIGH_QUALITY_SOURCES | {"WB_RTP_estimate"}
# Prices a person has withdrawn after review. A candidate from the same
# website for the same commodity within 1% of a withdrawn price is rejected,
# so daily searches that keep finding the same page can't re-add it.
WITHDRAWN_PATH       = os.path.join(os.path.dirname(PATHS["raw_web_prices"]), "web_prices_withdrawn.csv")
# Whole websites found unreliable after review (undated lists etc.): nothing
# from them is promoted or used in the cross-check, and the search is told
# to skip them.
BLOCKED_SITES_PATH   = os.path.join(os.path.dirname(PATHS["raw_web_prices"]), "web_sources_blocked.csv")

# Freshness windows live in pipeline/freshness.py so this step and the
# 3-hourly web search agree on which commodities are gaps.
from pipeline.freshness import is_fresh
LOOKBACK_DAYS        = 10     # how far back in web_price_estimates to look
MAX_SOURCE_AGE_DAYS  = 21     # a source price older than this (at search time) is rejected
BASE_BAND            = 0.25   # allowed deviation from last real price...
BAND_PER_DAY         = 0.003  # ...plus this per day since it, capped at MAX_BAND
MAX_BAND             = 0.40
# A move bigger than this from the last real price is only accepted when a
# second source (different website) agrees within CORROBORATION_TOLERANCE.
# Found live on the first real run: single sources put Rice 44% and Maize
# (yellow) 38% below their last Agricome price while Maize (white) rose 9% -
# plausible-looking but very likely a different product grade or unit.
CORROBORATE_ABOVE    = 0.15
CORROBORATION_TOLERANCE = 0.10
ACCEPT_CONFIDENCE    = {"high", "medium"}
# Yardstick for commodities with no recent real price of their own: a sibling
# commodity's latest real price x their measured price ratio. Yellow maize
# trades ~2% above white maize (WFP wholesale, same market and month,
# 2014-2023: median 1.023, IQR 1.00-1.07, 1,320 market-months). Without this
# its yardstick was a World Bank estimate (691K, Jul 2026) - and World Bank
# overstates maize by ~40% (white: 641K vs real 450K) - so a plausible 425.6K
# wholesale price was rejected as 38% off.
SIBLING_YARDSTICK    = {"Maize (yellow)": ("Maize (white)", 1.023)}
OWN_REAL_MAX_DAYS    = 365    # an own Agricome/WFP/primary price newer than this keeps priority
RECENT_REAL_DAYS     = 45     # an accepted web or sibling price must be this recent to be the yardstick
ACCEPT_LEVELS        = {"wholesale", "exchange"}

WEB_CSV_COLUMNS = ["commodity", "week_start_date", "price", "currency", "unit", "source",
                   "market_type", "region", "fx_rate", "rainfall_index", "extraction_date",
                   "data_quality_score", "is_validated", "notes", "record_type"]
LOG_PATH = os.path.join(PATHS["logs_dir"], "web_price_promotion.json")


def parse_source_date(text):
    """Best-effort parse of the free-text date the model reported. None if unusable."""
    if not text:
        return None
    try:
        from dateutil import parser
        d = parser.parse(str(text), fuzzy=True, default=datetime(1900, 1, 1))
        if d.year == 1900:          # no year given -> don't guess
            return None
        return d.date()
    except Exception:
        return None


def allowed_band(days_since_last_real):
    return min(MAX_BAND, BASE_BAND + BAND_PER_DAY * max(days_since_last_real, 0))


def source_site(url):
    from urllib.parse import urlparse
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_corroborated(cand, peers):
    """True if a peer from a different website, at medium+ confidence, agrees on price."""
    site = source_site(cand.get("source_url"))
    for p in peers:
        if not p.get("price") or source_site(p.get("source_url")) in ("", site):
            continue
        if str(p.get("confidence") or "").strip().lower() not in ACCEPT_CONFIDENCE:
            continue
        if str(p.get("price_level") or "").strip().lower() not in ACCEPT_LEVELS:
            continue
        if abs(p["price"] - cand["price"]) / cand["price"] <= CORROBORATION_TOLERANCE:
            return True
    return False


def evaluate_candidate(cand, last_real_price, last_real_date, query_date, peers=()):
    """
    cand: dict with price, source_url, source_date, confidence.
    peers: other candidates for the same commodity (for corroboration).
    Returns (accepted: bool, reason: str, source_date: date|None).
    """
    price = cand.get("price")
    if price is None or price <= 0:
        return False, "no price", None
    if str(cand.get("confidence") or "").strip().lower() not in ACCEPT_CONFIDENCE:
        return False, f"confidence '{cand.get('confidence')}' below medium", None
    level = str(cand.get("price_level") or "").strip().lower()
    if level not in ACCEPT_LEVELS:
        return False, f"price level '{level or 'not recorded'}' - we only use wholesale or exchange prices", None
    if not (cand.get("source_url") or "").strip():
        return False, "no source URL", None
    sd = parse_source_date(cand.get("source_date"))
    if sd is None:
        return False, f"source date unusable ({cand.get('source_date')!r})", None
    if sd > query_date:
        return False, f"source date {sd} is in the future", sd
    if (query_date - sd).days > MAX_SOURCE_AGE_DAYS:
        return False, f"source is {(query_date - sd).days} days old (max {MAX_SOURCE_AGE_DAYS})", sd
    if last_real_date is not None and sd <= last_real_date:
        return False, f"not newer than latest known price ({last_real_date})", sd
    if last_real_price:
        days = (sd - last_real_date).days if last_real_date else 0
        band = allowed_band(days)
        dev = abs(price - last_real_price) / last_real_price
        if dev > band:
            return False, (f"{dev:.0%} from last known price {last_real_price:,.0f} "
                           f"(allowed {band:.0%}) - needs human review"), sd
        if dev > CORROBORATE_ABOVE and not is_corroborated(cand, peers):
            return False, (f"{dev:.0%} from last known price {last_real_price:,.0f} on a single "
                           f"source - needs a second website agreeing within "
                           f"{CORROBORATION_TOLERANCE:.0%}, or human review"), sd
    return True, "accepted", sd


def load_master_state(today=None):
    """Per commodity: latest high-quality real date, and latest real price/date of any real source."""
    today = today or datetime.now(timezone.utc).date()
    df = pd.read_csv(PATHS["master"], parse_dates=["date"])
    out = {}
    for c in COMMODITIES:
        sub = df[(df["commodity"] == c) & df["data_source"].isin(BASELINE_SOURCES)].sort_values("date")
        hq = sub[sub["data_source"].isin(HIGH_QUALITY_SOURCES)]
        out[c] = {
            "hq_date":    hq["date"].max().date() if len(hq) else None,
            "wfp_date":   (hq.loc[hq["data_source"] == "WFP", "date"].max().date()
                           if (hq["data_source"] == "WFP").any() else None),
            "direct_date": (hq.loc[hq["data_source"] != "WFP", "date"].max().date()
                            if (hq["data_source"] != "WFP").any() else None),
            "real_date":  sub["date"].iloc[-1].date() if len(sub) else None,
            "real_price": float(sub["price_ngn_mt"].iloc[-1]) if len(sub) else None,
        }
    # No recent Agricome/WFP/primary price of its own: centre the band on a
    # real price rather than a World Bank estimate (which overstates maize ~40%).
    for c, st in out.items():
        if st["hq_date"] and (today - st["hq_date"]).days <= OWN_REAL_MAX_DAYS:
            continue
        own_web = df[(df["commodity"] == c) & (df["data_source"] == "Web_search")].sort_values("date")
        if len(own_web) and (today - own_web["date"].iloc[-1].date()).days <= RECENT_REAL_DAYS:
            p, d = float(own_web["price_ngn_mt"].iloc[-1]), own_web["date"].iloc[-1].date()
            st.update(real_price=p, real_date=d, yardstick=f"our latest accepted web price {p:,.0f} ({d})")
            continue
        if c not in SIBLING_YARDSTICK:
            continue
        sibling, ratio = SIBLING_YARDSTICK[c]
        sib = df[(df["commodity"] == sibling) & df["data_source"].isin(ANY_REAL_SOURCES)].sort_values("date")
        if sib.empty or (today - sib["date"].iloc[-1].date()).days > RECENT_REAL_DAYS:
            continue
        sib_price, sib_date = float(sib["price_ngn_mt"].iloc[-1]), sib["date"].iloc[-1].date()
        st["real_price"] = sib_price * ratio
        st["real_date"] = st["hq_date"]      # "newer than" still means newer than OUR last real price
        st["yardstick"] = (f"{sibling} real price {sib_price:,.0f} ({sib_date}) x {ratio} "
                           f"= {sib_price * ratio:,.0f}")
    return out


def fetch_candidates(conn, since):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.name, w.query_date, w.price_ngn_mt, w.source_url, w.source_title,
                   w.source_date, w.source_snippet, w.model_confidence,
                   w.price_level, w.market_location
            FROM web_price_estimates w
            JOIN commodities c ON c.commodity_id = w.commodity_id
            WHERE w.query_date >= %s AND w.price_ngn_mt IS NOT NULL
            ORDER BY c.name, w.query_date DESC
        """, (since,))
        rows = cur.fetchall()
    return [{"commodity": r[0], "query_date": r[1], "price": float(r[2]) if r[2] is not None else None,
             "source_url": r[3], "source_title": r[4], "source_date": r[5],
             "notes": r[6], "confidence": r[7], "price_level": r[8],
             "market_location": r[9]} for r in rows]


def load_withdrawn():
    if not os.path.exists(WITHDRAWN_PATH):
        return []
    df = pd.read_csv(WITHDRAWN_PATH)
    return [(r["commodity"], str(r["source_site"]).lower(), float(r["price"]), str(r.get("withdrawn_on", "")))
            for _, r in df.iterrows()]


def load_blocked_sites():
    """{site: reason} from web_sources_blocked.csv (empty if missing)."""
    if not os.path.exists(BLOCKED_SITES_PATH):
        return {}
    df = pd.read_csv(BLOCKED_SITES_PATH)
    return {str(r["site"]).strip().lower(): str(r.get("reason", "")) for _, r in df.iterrows()}


def withdrawn_match(cand, withdrawn):
    site = source_site(cand.get("source_url"))
    for com, w_site, w_price, when in withdrawn:
        if (com == cand["commodity"] and w_site == site and cand.get("price")
                and abs(cand["price"] - w_price) / w_price <= 0.01):
            return when or "earlier"
    return None


def existing_web_keys():
    path = PATHS["raw_web_prices"]
    if not os.path.exists(path):
        return set()
    df = pd.read_csv(path)
    return set(zip(df["commodity"], df["week_start_date"].astype(str)))


def append_rows(rows):
    path = PATHS["raw_web_prices"]
    new_file = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new_file:
            w.writerow(WEB_CSV_COLUMNS)
        w.writerows(rows)


def promote(candidates, state, today):
    """Pure decision logic over already-fetched candidates. Returns (rows_to_append, decisions)."""
    seen = existing_web_keys()
    withdrawn = load_withdrawn()
    blocked = load_blocked_sites()
    decisions, rows = [], []
    for c in sorted(candidates, key=lambda x: (x["commodity"], x["query_date"])):
        com = c["commodity"]
        st = state.get(com, {})
        d = {"commodity": com, "query_date": str(c["query_date"]), "price": c["price"],
             "source_url": c["source_url"], "source_date": c["source_date"],
             "confidence": c["confidence"], "price_level": c.get("price_level"),
             "market_location": c.get("market_location")}
        if st.get("yardstick"):
            d["yardstick"] = st["yardstick"]
        hq = st.get("hq_date")
        direct, wfp = st.get("direct_date"), st.get("wfp_date")
        fresh = is_fresh(direct, wfp, today)
        if fresh:
            d.update(accepted=False, reason=f"Agricome/WFP price is fresh ({hq}) - no gap to fill")
            decisions.append(d); continue
        if source_site(c.get("source_url")) in blocked:
            d.update(accepted=False, reason="website blocked after review - see web_sources_blocked.csv")
            decisions.append(d); continue
        when = withdrawn_match(c, withdrawn)
        if when:
            d.update(accepted=False, reason=f"withdrawn after review ({when}) - see web_prices_withdrawn.csv")
            decisions.append(d); continue
        peers = [p for p in candidates if p is not c and p["commodity"] == com]
        ok, reason, sd = evaluate_candidate(c, st.get("real_price"), st.get("real_date"),
                                            c["query_date"], peers)
        if ok and (com, str(sd)) in seen:
            ok, reason = False, "already promoted"
        d.update(accepted=ok, reason=reason)
        decisions.append(d)
        if ok:
            seen.add((com, str(sd)))
            # later candidates for the same commodity must beat this one
            st["real_date"], st["real_price"] = sd, c["price"]
            note = (f"Web search (Claude API), {c['confidence']} confidence, {c.get('price_level')} "
                    f"price ({c.get('market_location') or 'location not stated'}) - source: "
                    f"{c['source_title'] or 'untitled'} <{c['source_url']}> dated {sd}")
            rows.append([com, str(sd), f"{c['price']}", "NGN", "NGN/MT", "Web search", "wholesale",
                         "National", "", "", str(today), "0.7", "False", note, "historical"])
    return rows, decisions


def write_skip_log(reason):
    """So a skipped run is visible in the repo, not only in the Actions log."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump({"run_date": str(datetime.now(timezone.utc).date()), "skipped": reason,
                   "accepted": 0, "decisions": []}, f, indent=2)


def run_promotion():
    logger.info("=" * 60)
    logger.info("STEP 14 - PROMOTE WEB PRICES TO FILL STALE COMMODITIES")
    logger.info("=" * 60)

    db_url = os.environ.get("TSDB_URL")
    if not db_url and not os.environ.get("PGHOST"):
        logger.warning("  TSDB_URL/PGHOST not set - skipping (web_price_estimates is Postgres-only)")
        write_skip_log("no database configured (TSDB_URL/PGHOST not set)")
        return True
    try:
        import psycopg2
        conn = psycopg2.connect() if os.environ.get("PGHOST") else psycopg2.connect(db_url)
    except Exception as e:
        logger.warning(f"  Could not connect to Postgres - skipping this run: {e}")
        reason = " | ".join(l.strip() for l in str(e).strip().splitlines() if l.strip())
        write_skip_log(f"could not connect to Postgres: {reason[:300]}")
        return True

    today = datetime.now(timezone.utc).date()
    try:
        with conn.cursor() as cur:
            ensure_web_estimates_schema(cur)   # price_level / market_location columns
        conn.commit()
        candidates = fetch_candidates(conn, today - timedelta(days=LOOKBACK_DAYS))
    finally:
        conn.close()

    state = load_master_state(today)
    for c, st in state.items():
        if st.get("yardstick"):
            logger.info(f"  {c}: no recent real price of its own - yardstick {st['yardstick']}")
    rows, decisions = promote(candidates, state, today)
    if rows:
        append_rows(rows)

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump({"run_date": str(today), "accepted": len(rows), "decisions": decisions}, f,
                  indent=2, default=str)

    logger.info(f"  {len(candidates)} web prices considered, {len(rows)} promoted")
    for d in decisions:
        flag = "OK  " if d["accepted"] else "skip"
        logger.info(f"    [{flag}] {d['commodity']:<16} {d['query_date']}  {d['reason']}")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_promotion() else 1)
