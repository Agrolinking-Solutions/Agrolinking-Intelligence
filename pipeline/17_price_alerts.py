"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Price alerts: a GitHub issue whenever our price and an independent price disagree

Reads outputs/logs/price_crosscheck.json (written by 16_cross_check.py the
same run) and keeps one open GitHub issue, labelled price-alert, for every
commodity whose status is "disagree" (more than 30% apart). Everyone watching
the repository gets the issue by email.

  newly disagreeing   open an issue: both prices, dates, sources and links
  still disagreeing   update the issue text quietly every day; add a comment
                      (which emails watchers) only if the gap moved by
                      RENOTIFY_POINTS or more since the last email
  back in line        comment and close once the gap is under CLOSE_BELOW_PCT
                      - a little under the 30% that opens it, so a price
                      hovering around 30% does not open and close every day
  nothing to compare  (no web price today, or our price is old) leave the
                      issue as it is

What was last emailed is kept in a hidden marker in the issue text, so no
state file is needed. With no GITHUB_TOKEN (a local run) it prints what it
would do and changes nothing. Never blocks the pipeline.

Run: python pipeline/17_price_alerts.py [--dry-run]
"""

import os, re, sys, json
from datetime import datetime, timezone

import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

REPORT_PATH     = os.path.join(PATHS["logs_dir"], "price_crosscheck.json")
LABEL           = "price-alert"
LABEL_COLOUR    = "d93f0b"
CLOSE_BELOW_PCT = 25      # close once the sources are this close again
RENOTIFY_POINTS = 10      # email again only if the gap moved this much
CROSSCHECK_URL  = "https://pis-api.agrolinking.com/crosscheck"
API             = "https://api.github.com"
MARKER          = re.compile(r"<!-- price-alert (\{.*?\}) -->")


# ── What to do (pure, no network) ─────────────────────────────────────────────

def read_marker(body):
    m = MARKER.search(body or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def money(v):
    return f"N{v:,.0f}" if v is not None else "-"


def issue_title(commodity):
    return f"Price check: {commodity} - our price and other sources disagree"


def apart(gap):
    return f"{abs(gap):.0f}% {'above' if gap > 0 else 'below'}"


def issue_body(commodity, e, run_date, emailed_gap):
    gap = e["gap_pct"]
    basis = "wholesale, NGN per crate of 30" if commodity == "Eggs" else "wholesale, NGN per tonne"
    theirs = e.get("compared_with") or "other sources"
    # the price(s) the status was decided on: AFEX when there is one, else every web price
    ref = [p for p in e["web_prices"] if "mansamarkets" in p["site"]] if theirs.startswith("AFEX") else e["web_prices"]
    ref = ref or e["web_prices"]
    if len(ref) == 1:
        p = ref[0]
        their_row = f"| **{theirs}** | {money(p['price'])} | {p['source_date']} | [{p['site']}]({p['url']}) ({p['level']}) |"
    else:
        their_row = f"| **{theirs}** | {money(e['web_median'])} | | middle of {len(ref)} websites, listed below |"
    lines = [
        f"**{commodity}: {theirs} is {apart(gap)} our price** "
        f"(cross-check of {run_date}). A person should check which one is right before customers see it.",
        "",
        f"| | Price ({basis}) | Date | Source |",
        "|---|---|---|---|",
        f"| **Ours** | {money(e['our_price'])} | {e['our_date']} | {e['our_source']} ({e['our_level']}) |",
        their_row,
    ]
    if len(e["web_prices"]) > 1 or len(ref) != len(e["web_prices"]):
        lines += ["", "Prices compared:"]
        lines += [f"- {money(p['price'])} - [{p['site']}]({p['url']}), {p['source_date']}, {p['level']}"
                  for p in e["web_prices"]]
    if e.get("other_web_prices"):
        lines += ["", "Also seen, not compared (shop prices, other grades, or sites already compared):"]
        lines += [f"- {money(p['price'])} - [{p['site']}]({p['url']}), {p['source_date']}, {p['level']}"
                  for p in e["other_web_prices"]]
    if e.get("notes"):
        lines += ["", "Notes:"] + [f"- {n}" for n in e["notes"]]
    lines += [
        "",
        "**What to do**",
        "- If **our** price is wrong: correct it with the partner (Agricome, WFP, AFEX or FEWS NET data).",
        "- If the **other** price is wrong (a shop price, another grade or unit, an old article): add the "
        "website to `data/raw/web_sources_blocked.csv` with the reason, and it is ignored from the next run.",
        "- If both are right (prices really moved): no action - the next partner price will catch up.",
        "",
        f"This issue updates itself every day and closes when the gap is under {CLOSE_BELOW_PCT}%. "
        f"Full cross-check for all commodities: {CROSSCHECK_URL}",
        "",
        f"<!-- price-alert {json.dumps({'commodity': commodity, 'emailed_gap': emailed_gap})} -->",
    ]
    return "\n".join(lines)


def plan(report, open_issues):
    """Actions for today's report. open_issues: [{number, title, body}] with the price-alert label."""
    run_date = report["run_date"]
    by_commodity = {}
    for iss in open_issues:
        c = read_marker(iss.get("body")).get("commodity")
        if c:
            by_commodity[c] = iss
    actions = []
    for c, e in report["commodities"].items():
        iss = by_commodity.get(c)
        status, gap = e["status"], e.get("gap_pct")
        if status == "disagree":
            if iss is None:
                actions.append({"do": "open", "commodity": c, "title": issue_title(c),
                                "body": issue_body(c, e, run_date, gap)})
                continue
            emailed = read_marker(iss["body"]).get("emailed_gap")
            moved = emailed is None or abs(gap - emailed) >= RENOTIFY_POINTS or (gap > 0) != (emailed > 0)
            if moved:
                actions.append({"do": "comment", "commodity": c, "number": iss["number"],
                                "comment": (f"Gap now {gap:+.0f}% (was {emailed:+.0f}% at the last email): "
                                            f"ours {money(e['our_price'])} ({e['our_date']}), "
                                            f"{e.get('compared_with')} {money(e['web_median'])}."
                                            if emailed is not None else f"Gap now {gap:+.0f}%."),
                                "body": issue_body(c, e, run_date, gap)})
            else:
                actions.append({"do": "update", "commodity": c, "number": iss["number"],
                                "body": issue_body(c, e, run_date, emailed)})
        elif iss is not None and status in ("agree", "check") and gap is not None and abs(gap) < CLOSE_BELOW_PCT:
            actions.append({"do": "close", "commodity": c, "number": iss["number"],
                            "comment": (f"Back in line on {run_date}: {e.get('compared_with')} is {apart(gap)} "
                                        f"our price ({money(e['web_median'])} vs {money(e['our_price'])}). "
                                        f"Closing.")})
    return actions


# ── GitHub ────────────────────────────────────────────────────────────────────

class GitHub:
    def __init__(self, token, repo):
        self.repo = repo
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def call(self, method, path, **kw):
        r = self.s.request(method, f"{API}/repos/{self.repo}{path}", timeout=30, **kw)
        if r.status_code >= 400 and not (method == "POST" and path == "/labels" and r.status_code == 422):
            r.raise_for_status()
        return r.json() if r.content else {}

    def ensure_label(self):
        self.call("POST", "/labels", json={"name": LABEL, "color": LABEL_COLOUR,
                                           "description": "Our price and an independent price disagree"})

    def open_alerts(self):
        items = self.call("GET", "/issues", params={"labels": LABEL, "state": "open", "per_page": 100})
        return [i for i in items if "pull_request" not in i]

    def apply(self, a):
        if a["do"] == "open":
            return self.call("POST", "/issues", json={"title": a["title"], "body": a["body"], "labels": [LABEL]})
        n = a["number"]
        if a["do"] in ("comment", "close"):
            self.call("POST", f"/issues/{n}/comments", json={"body": a["comment"]})
        if a["do"] in ("comment", "update"):
            self.call("PATCH", f"/issues/{n}", json={"body": a["body"]})
        if a["do"] == "close":
            self.call("PATCH", f"/issues/{n}", json={"state": "closed", "state_reason": "completed"})
        return {"number": n}


def write_summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and lines:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n### Price alerts\n" + "\n".join(f"- {l}" for l in lines) + "\n")


def run_alerts(dry_run=False):
    logger.info("=" * 60)
    logger.info("STEP 17 - PRICE ALERTS (GitHub issue when sources disagree)")
    logger.info("=" * 60)
    if not os.path.exists(REPORT_PATH):
        logger.warning("  no cross-check report - nothing to alert on")
        return True
    with open(REPORT_PATH) as f:
        report = json.load(f)
    today = str(datetime.now(timezone.utc).date())
    if report.get("run_date") != today:
        logger.warning(f"  cross-check report is from {report.get('run_date')}, not today - skipped")
        return True

    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    gh = GitHub(token, repo) if token and repo and not dry_run else None
    if gh is None:
        logger.info("  no GITHUB_TOKEN/GITHUB_REPOSITORY (or --dry-run): showing what would happen")
    try:
        open_issues = gh.open_alerts() if gh else []
        actions = plan(report, open_issues)
        if gh and any(a["do"] == "open" for a in actions):
            gh.ensure_label()
        done = []
        for a in actions:
            res = gh.apply(a) if gh else {}
            n = res.get("number") or a.get("number")
            ref = f"#{n}" if n else "(new)"
            e = report["commodities"][a["commodity"]]
            logger.info(f"    {a['do']:<8} {a['commodity']:<16} gap {e.get('gap_pct')}%  {ref}")
            if a["do"] != "update":
                verb = {"open": "opened", "comment": "gap moved", "close": "closed, back in line"}[a["do"]]
                done.append(f"{a['commodity']}: {verb} {ref} (gap {e.get('gap_pct'):+.0f}%)")
        if not actions:
            logger.info("    no disagreements and nothing to close")
        write_summary(done)
    except Exception as e:
        logger.warning(f"  price alerts failed this run (pipeline continues): {e}")
    return True


if __name__ == "__main__":
    run_alerts(dry_run="--dry-run" in sys.argv)
    sys.exit(0)
