"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Feed alerts: a GitHub issue when a price feed we depend on stops

Seven commodities rest on Agricome's weekly prices and yellow maize on AFEX's
daily exchange price. When either stops, the forecasts quietly carry the last
price forward and accuracy freezes - Agricome's last prices were for the week
of 19 Oct 2026 and nothing said so. This keeps one open GitHub issue,
labelled data-feed, per feed whose newest price is older than its limit:

  stopped         open an issue: days since the last price, the commodities
                  resting on it, what to check (everyone watching gets it by email)
  still stopped   update the issue text quietly every day
  back            comment with the new price date and close

WFP and FEWS NET are not here: they always arrive weeks late (see
pipeline/freshness.py), so an alert would never close. With no GITHUB_TOKEN
(a local run) it prints what it would do and changes nothing. Never blocks
the pipeline.

Run: python pipeline/19_feed_alerts.py [--dry-run]
"""

import os, sys, importlib.util
from datetime import date

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, REAL_SOURCES

# GitHub client shared with the price alerts
_spec = importlib.util.spec_from_file_location(
    "price_alerts", os.path.join(os.path.dirname(os.path.abspath(__file__)), "17_price_alerts.py"))
_alerts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_alerts)

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

LABEL, LABEL_COLOUR = "data-feed", "fbca04"
# data_source in the master -> how old its newest price may be (days; master
# dates are the Monday of the week), what it is, and what to check
FEEDS = {
    "Agricome": {"max_days": 10,
                 "what": "Agricome's weekly prices, typed in from their posts (data/raw/agricome_raw.csv)",
                 "check": "Add Agricome's newest posted prices to data/raw/agricome_raw.csv, or find out "
                          "whether Agricome has stopped posting (the data request sent in Nov 2026 asks "
                          "for a direct weekly feed)."},
    "AFEX":     {"max_days": 7,
                 "what": "AFEX exchange prices, read daily from Mansa Markets (pipeline/fetch_afex_prices.py)",
                 "check": "Open step 00e of the latest daily run: the Mansa Markets page may have changed "
                          "or stopped showing AFEX's price."},
}


def title(feed):
    return f"Price feed stopped: {feed}"


def feed_status(master, today):
    """{feed: {latest, days, resting}} - resting = commodities whose newest real price is from it."""
    real = master[master["data_source"].isin(REAL_SOURCES) & master["price_ngn_mt"].notna()]
    newest = real.sort_values("date").groupby("commodity").tail(1).set_index("commodity")["data_source"]
    out = {}
    for feed in FEEDS:
        rows = real[real["data_source"] == feed]
        latest = rows["date"].max().date() if len(rows) else None
        out[feed] = {"latest": latest, "days": (today - latest).days if latest else None,
                     "resting": [c for c in COMMODITIES if newest.get(c) == feed]
                                or sorted(rows["commodity"].unique().tolist())}
    return out


def issue_body(feed, s, today):
    cfg = FEEDS[feed]
    return (f"No new price from **{feed}** for **{s['days']} days**: the newest is for the week of "
            f"{s['latest']} (limit {cfg['max_days']} days). Checked {today}.\n\n"
            f"{cfg['what']}.\n\n"
            f"Resting on it: {', '.join(s['resting']) or 'none'}. Their forecasts carry the last price "
            f"forward until a new one arrives, and their accuracy can't be measured meanwhile.\n\n"
            f"**What to check:** {cfg['check']}\n\n"
            f"This issue closes by itself when a new {feed} price arrives.")


def plan(status, open_issues, today):
    """Actions: open / update / close. Pure - no network."""
    by_title = {i["title"]: i for i in open_issues}
    actions = []
    for feed, s in status.items():
        issue = by_title.get(title(feed))
        stopped = s["days"] is None or s["days"] > FEEDS[feed]["max_days"]
        if stopped and s["days"] is not None:
            body = issue_body(feed, s, today)
            if issue is None:
                actions.append({"do": "open", "feed": feed, "title": title(feed), "body": body})
            elif issue.get("body") != body:
                actions.append({"do": "update", "feed": feed, "number": issue["number"], "body": body})
        elif not stopped and issue is not None:
            actions.append({"do": "close", "feed": feed, "number": issue["number"],
                            "comment": f"New {feed} price for the week of {s['latest']} - closing."})
    return actions


def run(dry_run=False):
    logger.info("=" * 60)
    logger.info("STEP 19 - FEED ALERTS (GitHub issue when a price feed stops)")
    logger.info("=" * 60)
    try:
        today = date.today()
        status = feed_status(pd.read_csv(PATHS["master"], parse_dates=["date"]), today)
        for feed, s in status.items():
            logger.info(f"  {feed:<9} newest {s['latest']} ({s['days']} days, limit {FEEDS[feed]['max_days']}) "
                        f"- resting on it: {', '.join(s['resting'])}")
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        gh = _alerts.GitHub(token, repo) if token and repo and not dry_run else None
        open_issues = []
        if gh:
            items = gh.call("GET", "/issues", params={"labels": LABEL, "state": "open", "per_page": 100})
            open_issues = [i for i in items if "pull_request" not in i]
        actions = plan(status, open_issues, today)
        if gh and any(a["do"] == "open" for a in actions):
            gh.call("POST", "/labels", json={"name": LABEL, "color": LABEL_COLOUR,
                                             "description": "A price feed we depend on has stopped"})
        for a in actions:
            logger.info(f"    {a['do']:<7} {a['feed']}" + ("" if gh else "  (dry run)"))
            if not gh:
                continue
            if a["do"] == "open":
                gh.call("POST", "/issues", json={"title": a["title"], "body": a["body"], "labels": [LABEL]})
            elif a["do"] == "update":
                gh.call("PATCH", f"/issues/{a['number']}", json={"body": a["body"]})
            else:
                gh.call("POST", f"/issues/{a['number']}/comments", json={"body": a["comment"]})
                gh.call("PATCH", f"/issues/{a['number']}", json={"state": "closed", "state_reason": "completed"})
        if not actions:
            logger.info("    nothing to open, update or close")
    except Exception as e:
        logger.warning(f"  feed alerts failed this run (pipeline continues): {e}")
    return True


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
    sys.exit(0)
