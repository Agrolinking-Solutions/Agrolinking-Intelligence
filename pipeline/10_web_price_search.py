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

Run: python pipeline/10_web_price_search.py
"""

import os, sys, json, re
from datetime import datetime, timezone

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

MODEL_NAME = "claude-sonnet-5"
MAX_SEARCHES_PER_COMMODITY = 3   # caps cost per commodity per run

PROMPT_TEMPLATE = """Find the most recent real wholesale price for {commodity} in Nigeria, in NGN per metric tonne (NGN/MT).

Rules:
- Only use a price you can attribute to a specific, named, dated source (a news article, market report, exchange price, or similar). Never estimate or infer a price from general knowledge.
- Prefer the most recent date you can find. If nothing is available within roughly the last 60 days, return null rather than using an old or unclear figure.
- If the source gives a price in a different unit (per kg, per bag, per crate, per litre, a different currency), convert it to NGN/MT yourself and show that conversion in "notes".
- If you find conflicting prices from different sources, prefer the one that is most recent and most specific to a named Nigerian market or exchange.

Respond with ONLY a single JSON object, no other text, in exactly this shape:
{{
  "price_ngn_mt": <number or null>,
  "source_url": "<url or null>",
  "source_title": "<page/article title or null>",
  "source_date": "<date the source price is from, as stated by the source, or null>",
  "confidence": "<high, medium, or low>",
  "notes": "<brief note: unit conversion shown if any, why confidence is what it is, or why null>"
}}"""


def search_commodity_price(client, commodity: str) -> dict:
    """Raises on any API-level failure — caller logs and continues with the next commodity."""
    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=1024,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": MAX_SEARCHES_PER_COMMODITY,
            "user_location": {"type": "approximate", "country": "NG"},
        }],
        messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(commodity=commodity)}],
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
        raise ValueError(f"No JSON object found in model response: {full_text[:200]!r}")

    parsed = json.loads(match.group(0))
    parsed["_raw_response"] = full_text
    return parsed


def write_estimate(cur, commodity_id: int, query_date, parsed: dict):
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
            (commodity_id, query_date, price_ngn_mt, source_url, source_title,
             source_date, source_snippet, model_confidence, model_used, raw_response)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (commodity_id, query_date)
        DO UPDATE SET
            price_ngn_mt     = EXCLUDED.price_ngn_mt,
            source_url       = EXCLUDED.source_url,
            source_title     = EXCLUDED.source_title,
            source_date      = EXCLUDED.source_date,
            source_snippet   = EXCLUDED.source_snippet,
            model_confidence = EXCLUDED.model_confidence,
            model_used       = EXCLUDED.model_used,
            raw_response     = EXCLUDED.raw_response,
            created_at       = now()
    """, (
        commodity_id, query_date, price,
        parsed.get("source_url"), parsed.get("source_title"), parsed.get("source_date"),
        parsed.get("notes"), parsed.get("confidence"), MODEL_NAME, parsed.get("_raw_response"),
    ))


def run_web_price_search():
    logger.info("=" * 60)
    logger.info("STEP 10 — WEB-SOURCED PRICE SEARCH (Claude API)")
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

    client = anthropic.Anthropic(api_key=api_key)
    query_date = datetime.now(timezone.utc).date()
    n_ok, n_null, n_failed = 0, 0, 0

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT commodity_id, name FROM commodities")
            commodity_map = {name: cid for cid, name in cur.fetchall()}

        for commodity in COMMODITIES:
            cid = commodity_map.get(commodity)
            if cid is None:
                logger.warning(f"  {commodity}: not found in commodities table, skipping")
                continue

            try:
                parsed = search_commodity_price(client, commodity)
            except Exception as e:
                logger.warning(f"  {commodity:<15} search failed: {e}")
                n_failed += 1
                continue

            with conn.cursor() as cur:
                write_estimate(cur, cid, query_date, parsed)
            conn.commit()

            price = parsed.get("price_ngn_mt")
            if price:
                n_ok += 1
                logger.info(f"  {commodity:<15} N{float(price):>12,.0f}  "
                            f"({parsed.get('confidence','?')})  {parsed.get('source_url','')}")
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
    ok = run_web_price_search()
    sys.exit(0 if ok else 1)
