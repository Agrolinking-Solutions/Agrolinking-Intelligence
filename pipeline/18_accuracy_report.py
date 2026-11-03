"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Monthly accuracy report - how did last month's forecasts do?

On the first pipeline run of each month this writes a report on the month
before, from what is already measured:

  live       the forecasts actually published (outputs/backtest/forecast_log.csv,
             15_track_accuracy.py) whose target date fell in the month and
             for which a real price arrived: accuracy, the same forecasts'
             "price holds" accuracy, and how often the real price fell
             inside the range shown
  fair test  the latest weekly audit (scripts/research/forecast_audit.py)
  site       the accuracy the site shows (13_select_forecaster backtest)
  real data  partner prices that arrived in the month, by source

and saves outputs/reports/accuracy_YYYY-MM.md (+ .json), then opens a GitHub
issue labelled accuracy-report so everyone watching the repository gets it
by email. A month whose report exists is never redone, so a missed day or a
rerun changes nothing. The first automatic report is 1 Dec 2026 (November):
the current forecasting method went live on 13 Oct, so November is its first
month with 1-month forecasts that have come due.

Never blocks the pipeline. Run:
  python pipeline/18_accuracy_report.py                     # last month, if not done
  python pipeline/18_accuracy_report.py --month 2026-10     # a given month (rewrites it)
  python pipeline/18_accuracy_report.py --month 2026-10 --no-issue
"""

import os, sys, json, argparse, importlib.util
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, SCORED_SOURCES

# GitHub client shared with the price alerts
_spec = importlib.util.spec_from_file_location(
    "price_alerts", os.path.join(os.path.dirname(os.path.abspath(__file__)), "17_price_alerts.py"))
_alerts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_alerts)

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKTEST    = os.path.join(ROOT, "outputs", "backtest")
REPORTS_DIR = PATHS["reports_dir"]
LABEL       = "accuracy-report"
LABEL_COLOUR = "0e8a16"
FIRST_MONTH = "2026-11"      # automatic reports start with November 2026 (see above)
GOAL_PCT    = 95
RANGE_AIM   = 80
HORIZON_NAMES = {"weekly": "1 week", "monthly": "1 month"}


def pct(v):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.1f}%"


def live_stats(rows):
    """Accuracy, 'price holds' accuracy and in-range share for scored forecast rows."""
    if rows.empty:
        return None
    ape = (rows["predicted"] - rows["actual"]).abs() / rows["actual"] * 100
    naive = (rows["anchor_price"] - rows["actual"]).abs() / rows["actual"] * 100
    r = rows[rows["lower"].notna()]
    inside = (r["actual"] >= r["lower"]) & (r["actual"] <= r["upper"])
    return {"n": int(len(rows)), "commodities": int(rows["commodity"].nunique()),
            "real_prices": int(rows[["commodity", "actual_date"]].drop_duplicates().shape[0]),
            "accuracy_pct": round(float(100 - ape.mean()), 1),
            "price_holds_pct": round(float(100 - naive.dropna().mean()), 1) if naive.notna().any() else None,
            "in_range_pct": round(float(inside.mean() * 100), 1) if len(r) else None}


def build_report(month, log, audit, selection, master):
    """month 'YYYY-MM'. Returns (report dict, markdown)."""
    start = pd.Timestamp(f"{month}-01")
    end = start + pd.offsets.MonthEnd(0)
    log = log.copy()
    log["target_date"] = pd.to_datetime(log["target_date"])
    due = log[log["valid"].astype(bool) & log["actual"].notna()
              & (log["target_date"] >= start) & (log["target_date"] <= end)]
    # judge the method in use: the champion where it published, else whatever did
    if (due["method"] == "champion").any():
        due = due[due["method"] == "champion"]

    live = {name: live_stats(due[due["horizon"] == h]) for h, name in HORIZON_NAMES.items()}
    real = master[master["data_source"].isin(SCORED_SOURCES) & master["price_ngn_mt"].notna()
                  & (master["date"] >= start) & (master["date"] <= end)]
    fair = (audit or {}).get("commodities") or {}

    per = {}
    for c in COMMODITIES:
        d = due[due["commodity"] == c]
        f4 = ((fair.get(c) or {}).get("horizons") or {}).get("4w") or {}
        per[c] = {"live": {name: live_stats(d[d["horizon"] == h]) for h, name in HORIZON_NAMES.items()},
                  "fair_test_1_month": f4.get("accuracy_pct"),
                  "site_shows_1_month": (selection.get(c) or {}).get("accuracy_4w"),
                  "method": (selection.get(c) or {}).get("method"),
                  "real_prices": sorted(real.loc[real["commodity"] == c, "data_source"].unique().tolist())}

    report = {"month": month, "written": date.today().isoformat(), "live": live,
              "fair_test": (audit or {}).get("summary"), "fair_test_updated": (audit or {}).get("updated"),
              "real_prices_by_source": real.groupby("data_source").size().astype(int).to_dict(),
              "no_real_price": [c for c in COMMODITIES if not per[c]["real_prices"]],
              "commodities": per}
    return report, to_markdown(report, start)


def to_markdown(r, start):
    title = start.strftime("%B %Y")
    L = [f"## Forecast accuracy - {title}", ""]
    m, w = r["live"]["1 month"], r["live"]["1 week"]
    if m:
        L.append(f"**1 month ahead: {pct(m['accuracy_pct'])}** on {m['n']} published forecasts that came due "
                 f"in {title} ({m['commodities']} commodities, {m['real_prices']} real prices). "
                 f"\"Price stays the same\" scored {pct(m['price_holds_pct'])} on the same dates. "
                 f"The range shown held the real price {pct(m['in_range_pct'])} of the time (aim {RANGE_AIM}%).")
    else:
        L.append(f"**1 month ahead: nothing to score** - no published 1-month forecast came due in {title} "
                 "with a real price to check it against.")
    if w:
        L.append("")
        L.append(f"1 week ahead: {pct(w['accuracy_pct'])} on {w['n']} forecasts (\"price stays the same\" "
                 f"{pct(w['price_holds_pct'])}); range held the real price {pct(w['in_range_pct'])} of the time.")
    ft = r.get("fair_test")
    if ft:
        L += ["", f"Fair test (history replayed as forecasts are really made, {r.get('fair_test_updated')}): "
                  f"**{pct(ft['mean_1_month_accuracy_pct'])}** average 1 month ahead, "
                  f"{ft['commodities_at_95']} of {ft['commodities_tested']} commodities at {GOAL_PCT}% or better."]
    L += ["", "| Commodity | 1 month (live) | 1 week (live) | In range (1 week) | Fair test | Site shows | Partner prices this month |",
          "|---|---|---|---|---|---|---|"]
    def scored(s):
        return f"{pct(s['accuracy_pct'])} ({s['n']})" if s else "-"

    for c, v in r["commodities"].items():
        lm, lw = v["live"]["1 month"], v["live"]["1 week"]
        L.append(f"| {c} | {scored(lm)} | {scored(lw)} | {pct(lw['in_range_pct']) if lw else '-'} | "
                 f"{pct(v['fair_test_1_month'])} | {pct(v['site_shows_1_month'])} | "
                 f"{', '.join(v['real_prices']) or 'none'} |")
    L += ["", "Live = forecasts as published, scored against the real price that arrived for their date "
              "(number scored in brackets). Site shows = the backtest figure on the platform; the fair test "
              "is the honest one to quote. Partner prices = Agricome, WFP, AFEX, FEWS NET and Agrolinking's "
              "own - the prices forecasts are scored on (accepted web prices are used, not scored)."]
    if r["no_real_price"]:
        L += ["", f"**No partner price this month:** {', '.join(r['no_real_price'])} - these can't be scored "
                  "and their forecasts rest on older or web data."]
    src = r["real_prices_by_source"]
    if src:
        L += ["", "Partner prices received (weekly rows): " + ", ".join(f"{k} {v}" for k, v in sorted(src.items())) + "."]
    return "\n".join(L) + "\n"


def report_due(today, reports_dir=REPORTS_DIR):
    """The month to report on today ('YYYY-MM'), or None if done or before FIRST_MONTH is over."""
    last = (pd.Timestamp(today).replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")
    if last < FIRST_MONTH or os.path.exists(os.path.join(reports_dir, f"accuracy_{last}.md")):
        return None
    return last


def load_json(name):
    try:
        with open(os.path.join(BACKTEST, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run(month=None, issue=True):
    logger.info("=" * 60)
    logger.info("STEP 18 - MONTHLY ACCURACY REPORT")
    logger.info("=" * 60)
    month = month or report_due(date.today())
    if not month:
        logger.info("  nothing due (last month's report exists, or before the first report month)")
        return True
    try:
        log = pd.read_csv(os.path.join(BACKTEST, "forecast_log.csv"))
        master = pd.read_csv(PATHS["master"], parse_dates=["date"])
        report, md = build_report(month, log, load_json("forecast_audit.json"),
                                  load_json("model_selection.json"), master)
        os.makedirs(REPORTS_DIR, exist_ok=True)
        with open(os.path.join(REPORTS_DIR, f"accuracy_{month}.md"), "w", encoding="utf-8") as f:
            f.write(md)
        with open(os.path.join(REPORTS_DIR, f"accuracy_{month}.json"), "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"  wrote outputs/reports/accuracy_{month}.md")
        print(md)
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        if issue and token and repo:
            gh = _alerts.GitHub(token, repo)
            gh.call("POST", "/labels", json={"name": LABEL, "color": LABEL_COLOUR,
                                             "description": "Monthly forecast accuracy report"})
            res = gh.call("POST", "/issues", json={"title": f"Forecast accuracy - {pd.Timestamp(month + '-01'):%B %Y}",
                                                   "body": md, "labels": [LABEL]})
            logger.info(f"  opened issue #{res.get('number')}")
        else:
            logger.info("  no GITHUB_TOKEN/GITHUB_REPOSITORY (or --no-issue): report saved, no issue opened")
    except Exception as e:
        logger.warning(f"  accuracy report failed this run (pipeline continues): {e}")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="YYYY-MM (default: last month, if its report isn't written yet)")
    ap.add_argument("--no-issue", action="store_true")
    a = ap.parse_args()
    run(a.month, issue=not a.no_issue)
    sys.exit(0)
