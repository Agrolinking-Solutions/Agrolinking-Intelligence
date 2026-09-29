"""
NCX price-freshness probe — NOT part of the pipeline.

Purpose: NCX (ncx.com.ng) shows commodity prices on its public agricultural
commodities page with no date or "last updated" stamp anywhere. Before
building real ingestion around it, we need empirical evidence it actually
updates daily rather than sitting static (the same trap that made FEWS NET,
WFP's HDX mirror, and the World Bank RTP study look live when they weren't —
see the research notes for the data-sourcing investigation this came out of).

This script does exactly one thing: fetch the public ticker, parse it, and
append one row per commodity to a log file, so that after ~1-2 weeks of
daily runs, `--summarize` can report whether each commodity's price has
actually changed at least once. No login, no credentials — this reads the
same page a browser gets without signing in.

This is deliberately NOT wired into pipeline/run_pipeline.py or the daily
GitHub Actions workflow. See .github/workflows/probe_ncx.yml for how it's
scheduled separately, writing to data/research/ (outside the pipeline's
quality-gated outputs/ tree) so a bad probe day can never affect real
forecasts.

Usage:
    python scripts/research/probe_ncx.py              # fetch + append today's row
    python scripts/research/probe_ncx.py --summarize   # report what's been observed so far
"""

import os
import re
import csv
import sys
import argparse
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL      = "https://ncx.com.ng/our-commodities/agricultural-commodities/"
LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "research", "ncx_probe_log.csv",
)
FIELDS = ["fetched_at_utc", "commodity", "code", "price_ngn", "pct_change_text", "color_class"]


def fetch_ticker_items():
    """
    Returns a list of dicts, one per <li class="ticker-inner"> entry on the
    public commodities page. Raises on network failure or if the ticker
    structure disappears entirely (site redesign) — callers should catch
    and log, not let this silently produce an empty/misleading probe row.
    """
    resp = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = soup.select("li.ticker-inner")
    if not items:
        raise RuntimeError(
            "No 'li.ticker-inner' elements found — NCX's page structure may "
            "have changed. Don't trust an empty probe row; investigate before "
            "re-running."
        )

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in items:
        h3 = item.find("h3")
        if not h3:
            continue
        b    = h3.find("b")
        span = h3.find("span")
        price_text = b.get_text(strip=True) if b else ""
        pct_text   = span.get_text(strip=True) if span else ""
        color      = " ".join(span.get("class", [])) if span else ""

        # h3 text is like "Cocoa (COCOND) ₦3,500.00 30%" — strip the parts
        # we already extracted to isolate "Cocoa (COCOND)".
        name_part = h3.get_text(" ", strip=True)
        if price_text:
            name_part = name_part.replace(price_text, "")
        if pct_text:
            name_part = name_part.replace(pct_text, "")
        name_part = name_part.strip()

        m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name_part)
        commodity, code = (m.group(1).strip(), m.group(2).strip()) if m else (name_part, "")

        price_num = None
        digits = re.sub(r"[^\d.]", "", price_text)
        if digits:
            try:
                price_num = float(digits)
            except ValueError:
                price_num = None

        rows.append({
            "fetched_at_utc":  now,
            "commodity":       commodity,
            "code":            code,
            "price_ngn":       price_num,
            "pct_change_text": pct_text,
            "color_class":     color,
        })
    return rows


def append_log(rows):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    is_new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def summarize():
    """
    Reports, per commodity, how many distinct price values have been
    observed across all logged days. A commodity stuck on exactly one
    distinct value after 1-2 weeks of daily runs is evidence the page is
    NOT actually updating daily for that commodity — the same "looks live,
    isn't" pattern already found in FEWS NET/WFP/World Bank RTP.
    """
    if not os.path.exists(LOG_PATH):
        print(f"No probe log yet at {LOG_PATH} — run without --summarize first.")
        return

    import pandas as pd
    df = pd.read_csv(LOG_PATH, parse_dates=["fetched_at_utc"])
    df["fetch_date"] = df["fetched_at_utc"].dt.date

    print(f"Probe log: {LOG_PATH}")
    print(f"Distinct fetch dates: {df['fetch_date'].nunique()}  "
          f"({df['fetch_date'].min()} to {df['fetch_date'].max()})")
    print()
    print(f"{'Commodity':<20} {'Code':<10} {'Days seen':>9} {'Distinct prices':>15}  Verdict")
    print("-" * 78)

    for (commodity, code), g in df.groupby(["commodity", "code"]):
        days = g["fetch_date"].nunique()
        distinct = g["price_ngn"].nunique(dropna=True)
        if days < 3:
            verdict = "not enough data yet"
        elif distinct <= 1:
            verdict = "STATIC — do not trust as a daily source"
        elif distinct < days:
            verdict = "partially static — investigate"
        else:
            verdict = "changed every day observed — looks genuinely live"
        print(f"{commodity:<20} {code:<10} {days:>9} {distinct:>15}  {verdict}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summarize", action="store_true",
                         help="Report on what's been observed so far instead of fetching")
    args = parser.parse_args()

    if args.summarize:
        summarize()
        return

    try:
        rows = fetch_ticker_items()
    except Exception as e:
        print(f"Probe fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    append_log(rows)
    print(f"Logged {len(rows)} commodity rows to {LOG_PATH}")
    for r in rows:
        price_str = f"N{r['price_ngn']:,}" if r["price_ngn"] is not None else "(no price parsed)"
        print(f"  {r['commodity']:<20} {r['code']:<10} {price_str}")


if __name__ == "__main__":
    main()
