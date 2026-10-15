"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Web-Sourced Price Search (Claude API + web search)

For each commodity, asks Claude (with the web_search tool) to find the
most recent real Nigeria wholesale price it can locate on the open web,
and writes the result into the web_price_estimates Postgres table.

This is deliberately isolated from prices/forecasts/agrolinking_master.csv.
A web-searched price carries real hallucination risk — the model can state
a confident wrong number when it can't find a genuinely current source —
so this never overwrites or blends into the real/validated data those
hold. It accumulates here for review. Wiring it into validation as a
candidate reference price is a separate, later decision, not automatic.

Confirmed before building this: social media (Threads, Instagram) is NOT
usably searchable this way — Agricome's own posts never surfaced in a
direct web search test. This only helps for commodities that get covered
by ordinary news/market-tracker sites (Nairametrics, legit.ng, millingmea,
Mansa Markets, etc.), which in practice is most of them to some degree,
just not Agricome's own feed.

Requires ANTHROPIC_API_KEY (same precedence/soft-dependency pattern as
TSDB_URL elsewhere in this pipeline — skips cleanly if not configured,
never blocks the rest of the pipeline).

Two modes:
  python pipeline/10_web_price_search.py            # daily sweep: all commodities, general search
  python pipeline/10_web_price_search.py --rotate   # 3-hourly job (web_price_stream.yml)

--rotate searches every commodity that has no fresh Agricome/WFP price (see
pipeline/freshness.py) plus ROTATING_EXTRA_PER_RUN others in turn, with a
search focus that changes each run (exchange -> official -> northern markets
-> southern markets -> news). Over a day each commodity gets looked up in
several different places, which is what the promotion step's second-source
rule needs. Each (commodity, day, focus) is its own row.
"""

import os, sys, json, re, argparse
from datetime import datetime, timezone

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import COMMODITIES
from pipeline.pg_commodities import ensure_commodities, ensure_web_estimates_schema

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

MODEL_NAME = "claude-sonnet-5"
MAX_SEARCHES_PER_COMMODITY = 3   # caps cost per commodity per run (daily sweep)
ROTATING_MAX_SEARCHES      = 2   # focused 3-hourly searches need fewer
ROTATING_EXTRA_PER_RUN     = 2   # non-gap commodities added to each 3-hourly run

# Extra instruction per search focus. "general" is the daily sweep.
FOCI = {
    "general":  "",
    "exchange": "Search specifically for commodity EXCHANGE prices: AFEX spot or trade prices, "
                "including AFEX prices republished by Mansa Markets or quoted in the news.",
    "official": "Search specifically for OFFICIAL or institutional price data: NBS Selected Food "
                "Price Watch, a state Ministry of Agriculture price tracker (for example Lagos "
                "State's), FEWS NET or WFP market monitors.",
    "north":    "Search specifically for prices from named NORTHERN Nigerian wholesale markets, "
                "e.g. Dawanau (Kano), Giwa or Saminaka (Kaduna), Mubi (Adamawa), Maiduguri "
                "Monday Market, Gombe.",
    "south":    "Search specifically for prices from named SOUTHERN Nigerian wholesale markets, "
                "e.g. Mile 12 or Oyingbo (Lagos), Bodija (Ibadan), Ogbete (Enugu), Onitsha main "
                "market, Oil Mill (Port Harcourt).",
    "news":     "Search specifically for recent NEWS reports quoting a current wholesale price "
                "(e.g. Nairametrics, BusinessDay, Punch, Vanguard, Daily Trust, Leadership, "
                "Premium Times).",
}
ROTATION = ["exchange", "official", "north", "south", "news"]
# Legacy "Rice" is being retired in favour of Rice (local) / Rice (foreign);
# don't spend 3-hourly searches on it (the daily sweep still covers it).
ROTATION_SKIP = {"Rice"}

# Eggs are priced per crate (30 eggs) everywhere else in this system
# (see api.py's price_per_kg()), not per metric tonne — found this the
# hard way: the first real run asked for NGN/MT uniformly and got back a
# nonsensically scaled number for Eggs, because the source it found was
# already a per-crate price. The price_ngn_mt column name stays the same
# for every commodity (matches write_estimate() and how the rest of the
# system already overloads that column for Eggs) — only the unit asked
# for in the prompt changes.
UNIT_LABEL = {"Eggs": "NGN per crate of 30 eggs (NGN/crate)"}

# What to search for when the commodity name alone is ambiguous. Rice is split
# by type because local and imported rice trade at very different prices
# (local retail ran ~72% of imported in WFP data) - a search for plain "rice"
# returned whichever one the source happened to quote.
COMMODITY_DESCRIPTION = {
    # Maize and wheat are usually quoted per bag; the 700K white maize price
    # that was later withdrawn came from an undated aggregator page.
    "Maize (white)":  "white maize grain (often quoted per 100kg bag - multiply by 10 for NGN/MT). "
                      "NOT yellow maize, NOT maize flour",
    "Maize (yellow)": "yellow maize grain (often quoted per 100kg bag - multiply by 10 for NGN/MT). "
                      "NOT white maize, NOT maize flour",
    "Wheat":          "wheat grain (often quoted per 50kg or 100kg bag). NOT wheat flour, NOT semolina",
    "Rice (local)":   "Nigerian local rice (domestically grown, milled; often sold in 50kg bags - "
                      "multiply a 50kg bag price by 20 for NGN/MT). NOT imported/foreign rice, NOT paddy",
    "Rice (foreign)": "imported (foreign) parboiled rice (often sold in 50kg bags - multiply a 50kg "
                      "bag price by 20 for NGN/MT). NOT Nigerian local rice",
}

PROMPT_TEMPLATE = """Find the most recent real WHOLESALE price in Nigeria for: {commodity}.
Report it in {unit}.

Rules:
- We price everything at wholesale level (bulk trade: bags, tonnes, crates from farms or wholesalers, exchange prices). Say which level the price you found is in "price_level". If you can only find a retail (shop, market-stall per-unit, supermarket) price, you may still return it, but set price_level to "retail" - never present a retail price as wholesale.
- Say where the price is from in "market_location": the named market and city/state (e.g. "Dawanau, Kano"), or "national" for a national average or exchange price.
- Only use a price you can attribute to a specific, named, dated source (a news article, market report, exchange price, or similar). Never estimate or infer a price from general knowledge.
- Prefer the most recent date you can find. If nothing is available within roughly the last 60 days, return null rather than using an old or unclear figure.
- If the source gives a price in a different unit (per kg, per bag, per crate, per litre, a different currency), convert it to {unit} yourself and show that conversion in "notes".
- If you find conflicting prices from different sources, prefer the one that is most recent and most specific to a named Nigerian market or exchange.
- Source preference, best first: (1) commodity exchange spot prices (AFEX, including AFEX prices republished by Mansa Markets or reported in the news); (2) official or institutional price trackers (NBS Selected Food Price Watch, a state Ministry of Agriculture price tracker, FEWS NET, WFP); (3) dated wholesale market reports naming a Nigerian market (e.g. Dawanau in Kano, Bodija in Ibadan, Mile 12 in Lagos).
- Avoid undated "price list" pages, retail shops or e-commerce listings, and forum posts. Use one only if nothing better exists, and then set confidence to "low".

Respond with ONLY a single JSON object, no other text, in exactly this shape:
{{
  "price_ngn_mt": <number or null>,
  "source_url": "<url or null>",
  "source_title": "<page/article title or null>",
  "source_date": "<date the source price is from, as stated by the source, or null>",
  "price_level": "<wholesale, exchange, retail, farm-gate, or unknown>",
  "market_location": "<named market and city/state, or national, or null>",
  "confidence": "<high, medium, or low>",
  "notes": "<brief note: unit conversion shown if any, why confidence is what it is, or why null>"
}}"""


def blocked_sites():
    """Websites found unreliable after review (data/raw/web_sources_blocked.csv)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "raw", "web_sources_blocked.csv")
    try:
        import csv
        with open(path, newline="", encoding="utf-8") as f:
            return [r["site"].strip() for r in csv.DictReader(f) if r.get("site")]
    except FileNotFoundError:
        return []


def build_prompt(commodity: str, focus: str = "general") -> str:
    prompt = PROMPT_TEMPLATE.format(
        commodity=COMMODITY_DESCRIPTION.get(commodity, commodity),
        unit=UNIT_LABEL.get(commodity, "NGN per metric tonne (NGN/MT)"),
    )
    skip = blocked_sites()
    if skip:
        prompt += ("\n\nDo not use these websites - they were checked and found unreliable: "
                   + ", ".join(skip) + ".")
    if FOCI.get(focus):
        prompt += "\n\nFocus for this search: " + FOCI[focus]
    return prompt


def rotation_plan(now_utc, commodities, gaps):
    """
    (focus, commodities) for a 3-hourly run. The run number counts 3-hour
    slots since a fixed date, so consecutive runs step through ROTATION and
    through the non-gap commodities regardless of when the job starts.
    """
    run_no = now_utc.date().toordinal() * 8 + now_utc.hour // 3
    focus = ROTATION[run_no % len(ROTATION)]
    gaps = [c for c in gaps if c not in ROTATION_SKIP]
    others = [c for c in commodities if c not in gaps and c not in ROTATION_SKIP]
    extra = []
    if others:
        start = (run_no * ROTATING_EXTRA_PER_RUN) % len(others)
        extra = [others[(start + i) % len(others)]
                 for i in range(min(ROTATING_EXTRA_PER_RUN, len(others)))]
    return focus, list(gaps) + extra


def search_commodity_price(client, commodity: str, focus: str = "general",
                           max_uses: int = MAX_SEARCHES_PER_COMMODITY) -> dict:
    """Raises on any API-level failure — caller logs and continues with the next commodity."""
    response = client.messages.create(
        model=MODEL_NAME,
        # 1024 was too small — confirmed live: with web_search in the
        # loop, the model's own search queries and result snippets eat
        # into this same budget before it gets to write the final JSON
        # answer, so several real responses were cut off mid-sentence.
        max_tokens=4096,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_uses,
            # No user_location — the API's country-code allowlist doesn't
            # include Nigeria ("Country code NG is not supported", found
            # testing this live). The prompt itself already says "Nigeria"
            # explicitly, which is enough to steer the search correctly.
        }],
        messages=[{"role": "user", "content": build_prompt(commodity, focus)}],
    )

    # The final text block should be the JSON — but the model may still
    # wrap it in prose or a code fence despite instructions, so extract
    # the first {...} block rather than assuming response.content[-1] is
    # clean JSON on its own.
    full_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    match = re.search(r"\{.*\}", full_text, re.DOTALL)
    if not match:
        raise ValueError(
            f"No JSON object found (stop_reason={response.stop_reason}): {full_text[:500]!r}"
        )

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Malformed JSON (stop_reason={response.stop_reason}): {e} — {match.group(0)[:500]!r}"
        )
    parsed["_raw_response"] = full_text
    return parsed


def write_estimate(cur, commodity_id: int, query_date, parsed: dict, focus: str = "general"):
    price = parsed.get("price_ngn_mt")
    if price is not None:
        try:
            price = float(price)
            if price <= 0:
                price = None
        except (TypeError, ValueError):
            price = None

    cur.execute("""
        INSERT INTO web_price_estimates
            (commodity_id, query_date, query_focus, price_ngn_mt, source_url, source_title,
             source_date, source_snippet, model_confidence, model_used, raw_response,
             price_level, market_location)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (commodity_id, query_date, query_focus)
        DO UPDATE SET
            price_ngn_mt     = EXCLUDED.price_ngn_mt,
            source_url       = EXCLUDED.source_url,
            source_title     = EXCLUDED.source_title,
            source_date      = EXCLUDED.source_date,
            source_snippet   = EXCLUDED.source_snippet,
            model_confidence = EXCLUDED.model_confidence,
            model_used       = EXCLUDED.model_used,
            raw_response     = EXCLUDED.raw_response,
            price_level      = EXCLUDED.price_level,
            market_location  = EXCLUDED.market_location,
            created_at       = now()
    """, (
        commodity_id, query_date, focus, price,
        parsed.get("source_url"), parsed.get("source_title"), parsed.get("source_date"),
        parsed.get("notes"), parsed.get("confidence"), MODEL_NAME, parsed.get("_raw_response"),
        (str(parsed.get("price_level") or "unknown").strip().lower() or "unknown"),
        parsed.get("market_location"),
    ))


def run_web_price_search(rotate: bool = False):
    logger.info("=" * 60)
    logger.info("STEP 10 — WEB-SOURCED PRICE SEARCH (Claude API)"
                + (" — 3-hourly rotation" if rotate else ""))
    logger.info("=" * 60)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("  ANTHROPIC_API_KEY not set — skipping web price search")
        return True

    try:
        import anthropic
    except ImportError:
        logger.warning("  anthropic package not installed — skipping web price search")
        return True

    db_url = os.environ.get("TSDB_URL")
    if not db_url and not os.environ.get("PGHOST"):
        logger.warning("  [Postgres] TSDB_URL/PGHOST not set — skipping (web_price_estimates is Postgres-only)")
        return True

    try:
        import psycopg2
    except ImportError:
        logger.warning("  psycopg2 not installed — skipping web price search")
        return True

    try:
        conn = psycopg2.connect() if os.environ.get("PGHOST") else psycopg2.connect(db_url)
    except Exception as e:
        logger.warning(f"  [Postgres] Could not connect — skipping this run: {e}")
        return True

    # Some Anthropic Console API keys are scoped to a specific workspace
    # rather than the account's default one — those require every request
    # to carry an anthropic-workspace-id header, or the API rejects them
    # with a 400 before the request is even evaluated. Optional: only set
    # ANTHROPIC_WORKSPACE_ID if your key actually needs it.
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    extra_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else {}
    client = anthropic.Anthropic(api_key=api_key, default_headers=extra_headers)
    now = datetime.now(timezone.utc)
    query_date = now.date()
    n_ok, n_null, n_failed = 0, 0, 0
    if rotate:
        from pipeline.freshness import gap_commodities
        try:
            gaps = gap_commodities(COMMODITIES, query_date)
        except Exception as e:
            logger.warning(f"  could not read freshness ({e}) - treating no commodity as a gap")
            gaps = []
        focus, to_search = rotation_plan(now, COMMODITIES, gaps)
        max_uses = ROTATING_MAX_SEARCHES
        logger.info(f"  focus: {focus} | gaps: {gaps} | searching: {to_search}")
    else:
        focus, to_search, max_uses = "general", list(COMMODITIES), MAX_SEARCHES_PER_COMMODITY

    try:
        with conn.cursor() as cur:
            added = ensure_commodities(cur, COMMODITIES)
            if added:
                logger.info(f"  Added to commodities table: {added}")
            changed = ensure_web_estimates_schema(cur)
            if changed:
                logger.info(f"  web_price_estimates schema: {changed}")
            cur.execute("SELECT commodity_id, name FROM commodities")
            commodity_map = {name: cid for cid, name in cur.fetchall()}
        conn.commit()

        for commodity in to_search:
            cid = commodity_map.get(commodity)
            if cid is None:
                logger.warning(f"  {commodity}: not found in commodities table, skipping")
                continue

            try:
                parsed = search_commodity_price(client, commodity, focus, max_uses)
            except Exception as e:
                logger.warning(f"  {commodity:<15} search failed: {e}")
                n_failed += 1
                continue

            with conn.cursor() as cur:
                write_estimate(cur, cid, query_date, parsed, focus)
            conn.commit()

            price = parsed.get("price_ngn_mt")
            if price:
                n_ok += 1
                logger.info(f"  {commodity:<15} N{float(price):>12,.0f}  "
                            f"({parsed.get('confidence','?')}, {parsed.get('price_level','?')}, "
                            f"{parsed.get('market_location') or '?'})  {parsed.get('source_url','')}")
            else:
                n_null += 1
                logger.info(f"  {commodity:<15} no usable price found — {parsed.get('notes','')[:60]}")

        logger.info("=" * 60)
        logger.success(f"WEB PRICE SEARCH COMPLETE — {n_ok} found, {n_null} no result, {n_failed} failed")
        return True
    except Exception as e:
        logger.warning(f"  Web price search failed this run: {e}")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotate", action="store_true",
                    help="3-hourly mode: gap commodities + a rotating slice, rotating search focus")
    ok = run_web_price_search(rotate=ap.parse_args().rotate)
    sys.exit(0 if ok else 1)
