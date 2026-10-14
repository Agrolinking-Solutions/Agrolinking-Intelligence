"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Daily real-data status, in plain language.

Pulls together what the day's run learned about our REAL prices:
  - commodities still without a recent real price (09_staleness_check)
  - web prices accepted today, and why the gap commodities' finds were not
    (14_promote_web_prices)
  - where web prices disagree with ours (16_cross_check)
  - commodities still on shop (retail) prices

Writes outputs/logs/data_status.md and, on GitHub Actions, shows the same
text at the top of the run page (GITHUB_STEP_SUMMARY). Never fails.

Run: python pipeline/data_status.py
"""

import os, sys, json
from collections import Counter
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

LOGS = PATHS["logs_dir"]
OUT_PATH = os.path.join(LOGS, "data_status.md")
GAP_COMMODITIES_WATCH = ["Maize (yellow)", "Wheat"]


def load(name):
    try:
        with open(os.path.join(LOGS, name)) as f:
            return json.load(f)
    except Exception:
        return None


def short_reason(r):
    r = str(r or "")
    for key, text in (("fresh", "already has a recent partner price"),
                      ("price level", "not a wholesale price (or level not recorded)"),
                      ("confidence", "low confidence (forum, undated list or shop)"),
                      ("source date unusable", "no exact date on the source"),
                      ("days old", "source too old"),
                      ("single source", "big move, only one website"),
                      ("needs human review", "too far from our yardstick"),
                      ("withdrawn", "withdrawn earlier after review"),
                      ("not newer", "not newer than our latest price"),
                      ("already promoted", "already added")):
        if key in r:
            return text
    return r[:60]


def build():
    today = datetime.now().strftime("%d %B %Y")
    stale = load("staleness_alert.json") or {}
    promo = load("web_price_promotion.json") or {}
    cross = load("price_crosscheck.json") or {}
    lines = [f"## Real-data status - {today}", ""]

    # 1. Commodities without a recent real price
    res = {r["commodity"]: r for r in stale.get("results", [])}
    gaps = [r for r in res.values() if r.get("status") in ("ALERT", "WARN", "NO_REAL_DATA")]
    ok = sum(1 for r in res.values() if r.get("status") == "OK")
    lines.append(f"**{ok} of {len(res)} commodities have a real price from the last 45 days.**" if res
                 else "**Freshness check did not run.**")
    if gaps:
        lines.append("")
        lines.append("| Still waiting for real data | Last real price | Days ago |")
        lines.append("|---|---|---|")
        for r in sorted(gaps, key=lambda r: -(r.get("days_stale") or 99999)):
            lines.append(f"| {r['commodity']} | {r.get('last_real_date') or 'never'} | {r.get('days_stale') or '-'} |")

    # 2. Web prices today
    decisions = promo.get("decisions", [])
    accepted = [d for d in decisions if d.get("accepted")]
    lines += ["", f"**Web prices added today: {len(accepted)}**"]
    for d in accepted:
        lines.append(f"- {d['commodity']}: N{d['price']:,.0f}/t ({d.get('price_level') or '?'}, "
                     f"{d.get('market_location') or 'location not stated'}) - {d.get('source_url')}")
    if promo.get("skipped"):
        lines.append(f"- Web price step skipped: {promo.get('reason') or promo.get('skipped')}")
    watch = sorted({g["commodity"] for g in gaps} | set(GAP_COMMODITIES_WATCH))
    for c in watch:
        mine = [d for d in decisions if d["commodity"] == c and not d.get("accepted")]
        if not mine:
            lines.append(f"- {c}: no web price found in the last 10 days")
            continue
        why = Counter(short_reason(d.get("reason")) for d in mine)
        lines.append(f"- {c}: {len(mine)} found, none used - " +
                     ", ".join(f"{n}x {w}" for w, n in why.most_common()))

    # 3. Cross-check
    comms = cross.get("commodities", {})
    flagged = {c: e for c, e in comms.items() if e.get("status") in ("disagree", "check")}
    lines += ["", f"**Our prices vs independent web prices:** "
              + (", ".join(f"{n} {s}" for s, n in sorted(Counter(e['status'] for e in comms.values()).items()))
                 if comms else "cross-check did not run")]
    for c, e in sorted(flagged.items(), key=lambda kv: -abs(kv[1].get("gap_pct") or 0)):
        lines.append(f"- {e['status'].upper()}: {c} - ours N{e['our_price']:,} ({e['our_source']}), "
                     f"web N{e['web_median']:,} from {len(e['web_prices'])} site(s), {e['gap_pct']:+.0f}%")

    # 4. Shop prices still in use
    try:
        m = pd.read_csv(PATHS["master"], parse_dates=["date"])
        real = m[m["data_source"].isin({"Agricome", "WFP", "Agrolinking_primary", "Web_search"})].sort_values("date")
        recent = real[real["date"] >= pd.Timestamp.now() - pd.Timedelta(days=90)]
        retail = [c for c in COMMODITIES
                  if not recent[recent.commodity == c].empty
                  and str(recent[recent.commodity == c].iloc[-1].get("market_type")).lower() == "retail"]
        if retail:
            lines += ["", f"**Still shop (retail) prices, waiting for a wholesale source:** {', '.join(retail)}"]
    except Exception:
        pass
    return "\n".join(lines) + "\n"


def main():
    try:
        text = build()
    except Exception as e:
        text = f"## Real-data status\n\nCould not build the summary: {e}\n"
    os.makedirs(LOGS, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
    sys.exit(0)
