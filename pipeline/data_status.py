"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Daily real-data status, in plain language.

Pulls together what the day's run learned about our REAL prices:
  - commodities still without a recent real price (09_staleness_check)
  - web prices accepted today, and why the gap commodities' finds were not
    (14_promote_web_prices)
  - where web prices disagree with ours (16_cross_check)
  - commodities still on shop (retail) prices
  - which steps of the run failed (GitHub Actions only: STEP_OUTCOMES is the
    workflow's steps context). Many steps continue on error so a bad source
    can't stop publishing; their failures used to show only on the run page.
    The quality gate and commit run after this step: if either fails there
    is no commit that day.

Writes outputs/logs/data_status.md and, on GitHub Actions, shows the same
text at the top of the run page (GITHUB_STEP_SUMMARY). Never fails.

Run: python pipeline/data_status.py
"""

import os, sys, json
from collections import Counter
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, REAL_SOURCES, PROVISIONAL_SOURCES

LOGS = PATHS["logs_dir"]
OUT_PATH = os.path.join(LOGS, "data_status.md")
GAP_COMMODITIES_WATCH = ["Wheat"]   # Yellow maize closed 16 Oct 2026 (AFEX)

# Step ids in .github/workflows/daily_pipeline.yml -> the step's name there.
STEP_NAMES = {
    "install": "Install dependencies",
    "external_factors": "00 - Live external factors", "weather_report": "00h - Weather report",
    "web_search": "00b - Web price search", "promote_web": "00c - Promote web prices",
    "refresh_wfp": "00d - Refresh WFP prices", "macro": "00g - Real macro data",
    "afex": "00e - AFEX exchange prices", "fao_fews": "00f - FAO / FEWS NET market prices",
    "produce": "00i - Fresh-produce prices", "farm_prices": "00j - Farm crop prices for AgLens",
    "export_prices_fetch": "00k - Export and neighbouring-market prices",
    "ingest": "01 - Ingest", "clean": "02 - Clean", "features": "03 - Features",
    "select_forecaster": "04b - Accuracy scoring and champion selection",
    "forecast": "05 - Forecast", "validate": "06 - Validate",
    "live_accuracy": "06b - Live accuracy tracking", "fair_test": "06c - Weekly fair accuracy audit",
    "zonal": "07 - Zonal forecast", "intelligence": "08 - Intelligence",
    "export_view": "08a - Export view", "drivers": "08b - Price drivers",
    "staleness": "09 - Staleness check", "cross_check": "09b - Price cross-check",
    "price_alerts": "09c - Price alerts", "feed_alerts": "09e - Feed alerts",
    "db_security": "09g - Database security check", "accuracy_report": "09f - Monthly accuracy report",
    "data_checks": "09d - Accuracy checks", "tests": "Tests",
}


def step_lines(raw):
    """Markdown lines on which run steps failed, from the steps context as JSON; [] off Actions."""
    if not raw:
        return []
    try:
        steps = json.loads(raw)
    except Exception:
        return ["", "**Run steps:** could not read the step results."]
    failed = [i for i, s in steps.items() if (s or {}).get("outcome") == "failure"]
    skipped = [i for i, s in steps.items() if (s or {}).get("outcome") == "skipped"]
    if not failed:
        return ["", f"**Run steps:** all {len(steps)} ran without an error."]
    out = ["", f"**Steps that failed today ({len(failed)} of {len(steps)}) - see this run's page for the error:**"]
    out += [f"- {STEP_NAMES.get(i, i)}" for i in failed]
    if skipped:
        out.append(f"- {len(skipped)} later step(s) did not run because of it: "
                   + ", ".join(STEP_NAMES.get(i, i) for i in skipped))
    return out


def load(name):
    try:
        with open(os.path.join(LOGS, name)) as f:
            return json.load(f)
    except Exception:
        return None


def short_reason(r):
    r = str(r or "")
    for key, text in (("wrong date", "page is older than the date the search gave"),
                      ("could not open the page", "page couldn't be opened to check its date"),
                      ("no publication date on the page", "page shows no date to check"),
                      ("stand for the national price", "one state's price, no measured gap to national"),
                      ("fresh", "already has a recent partner price"),
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
    lines = [f"## Real-data status - {today}"]
    lines += step_lines(os.environ.get("STEP_OUTCOMES"))
    lines.append("")

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
        real = m[m["data_source"].isin(REAL_SOURCES)].sort_values("date")
        recent = real[real["date"] >= pd.Timestamp.now() - pd.Timedelta(days=90)]
        retail = [c for c in COMMODITIES
                  if not recent[recent.commodity == c].empty
                  and str(recent[recent.commodity == c].iloc[-1].get("market_type")).lower() == "retail"]
        if retail:
            lines += ["", f"**Still shop (retail) prices, waiting for a wholesale source:** {', '.join(retail)}"]
        latest = real.groupby("commodity").tail(1)
        prov = latest[latest["data_source"].isin(PROVISIONAL_SOURCES)]
        if len(prov):
            lines += ["", "**Provisional (WFP's first markets this month, replaced when WFP completes it):** "
                      + ", ".join(f"{r.commodity} {r.date:%d %b}" for r in prov.itertuples())]
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
