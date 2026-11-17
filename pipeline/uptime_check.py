"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Uptime check: a GitHub issue when the site or API is down or serving old data

Nothing watched the live service: if the API or the website went down, or
the server stopped picking up new runs (it pulls the repository on every push
- a broken deploy key would freeze it on old data), nobody would know. This
runs every 3 hours in the web price workflow (no extra GitHub Actions job) and checks:

  API         https://pis-api.agrolinking.com/health answers "operational"
  website     https://pis.agrolinking.com answers
  data        the API's forecast date matches the newest daily run in the
              repository, once that run has been in the repository for
              DEPLOY_GRACE_MIN (the server takes ~6 minutes to pick it up)

and keeps one open GitHub issue, labelled uptime, while anything fails (it
emails everyone watching the repository), updated quietly, closed with a
comment when all is well again. A request is retried before it counts as
down, so one network blip doesn't open an issue. With no GITHUB_TOKEN (a
local run) it prints what it would do. Never fails the job.

For checks every few minutes, add the same three URLs to a free outside
monitor (docs/SECURITY.md); this is the backstop that needs no account.

Run: python pipeline/uptime_check.py [--dry-run]
"""

import os, sys, glob, json, time, subprocess, importlib.util
from datetime import datetime, timezone

import requests
from loguru import logger

_spec = importlib.util.spec_from_file_location(
    "price_alerts", os.path.join(os.path.dirname(os.path.abspath(__file__)), "17_price_alerts.py"))
_alerts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_alerts)

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API      = "https://pis-api.agrolinking.com"
SITE     = "https://pis.agrolinking.com"
LABEL, LABEL_COLOUR = "uptime", "b60205"
TITLE    = "PIS is down or serving old data"
TRIES, PAUSE_S, TIMEOUT_S = 3, 20, 30
DEPLOY_GRACE_MIN = 45


def fetch(url, tries=TRIES, pause=PAUSE_S):
    """(status code or None, body text, error) - retried before giving up."""
    err = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=TIMEOUT_S, headers={"User-Agent": "Agrolinking-uptime-check/1.0"})
            if r.status_code < 500:
                return r.status_code, r.text, None
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__
        if i < tries - 1:
            time.sleep(pause)
    return None, "", err


def newest_run_in_repo():
    """(date 'YYYY-MM-DD', unix time it was committed or None) of the newest validated forecast."""
    files = sorted(glob.glob(os.path.join(ROOT, "outputs", "forecasts", "validated", "forecast_validated_*.json")))
    if not files:
        return None, None
    day = os.path.basename(files[-1])[len("forecast_validated_"):-len(".json")]
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", files[-1]], cwd=ROOT,
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return day, int(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return day, None


def run_checks(now=None, get=fetch, repo_run=newest_run_in_repo):
    """[(check, ok, detail)]"""
    now = now or datetime.now(timezone.utc)
    results = []
    code, body, err = get(f"{API}/health")
    ok = code == 200 and '"operational"' in body
    results.append(("API", ok, "answering" if ok else f"/health: {err or f'HTTP {code}'}"))
    code, _, err = get(SITE)
    ok = code is not None and code < 400
    results.append(("Website", ok, "answering" if ok else f"{err or f'HTTP {code}'}"))
    code, body, err = get(f"{API}/commodities")
    try:
        served = json.loads(body).get("as_of") if code == 200 else None
    except ValueError:
        served = None
    day, committed = repo_run()
    if served is None:
        results.append(("Data", False, f"/commodities gave no date ({err or f'HTTP {code}'})"))
    elif day and served < day:
        age_min = (now.timestamp() - committed) / 60 if committed else None
        if age_min is not None and age_min < DEPLOY_GRACE_MIN:
            results.append(("Data", True, f"serving {served}; the {day} run is {age_min:.0f} min old, "
                                          "still within the time the server takes to pick it up"))
        else:
            results.append(("Data", False, f"serving the {served} forecasts, but the {day} run has been in "
                                           "the repository for "
                                           + (f"{age_min / 60:.1f} hours" if age_min else "a while")
                                           + " - the server isn't picking up new runs (check its deploy)"))
    else:
        results.append(("Data", True, f"serving the {served} forecasts, the newest run"))
    return results


def issue_body(results, now):
    rows = "\n".join(f"| {name} | {'OK' if ok else '**FAILING**'} | {detail} |" for name, ok, detail in results)
    return (f"Checked {now:%Y-%m-%d %H:%M} UTC from GitHub Actions (every 3 hours).\n\n"
            f"| Check | Status | Detail |\n|---|---|---|\n{rows}\n\n"
            f"API: {API} - website: {SITE}\n\n"
            "This issue closes by itself once every check passes.")


def plan(results, open_issue, now):
    """open / update / close / nothing. Pure - no network."""
    failing = [r for r in results if not r[1]]
    if failing:
        body = issue_body(results, now)
        if open_issue is None:
            return {"do": "open", "body": body}
        # the time in the text changes every run; only rewrite when the checks changed
        if [l for l in (open_issue.get("body") or "").splitlines() if l.startswith("| ")] != \
           [l for l in body.splitlines() if l.startswith("| ")]:
            return {"do": "update", "number": open_issue["number"], "body": body}
        return None
    if open_issue is not None:
        return {"do": "close", "number": open_issue["number"],
                "comment": f"All checks pass again ({now:%Y-%m-%d %H:%M} UTC) - closing."}
    return None


def run(dry_run=False):
    logger.info("=" * 60)
    logger.info("UPTIME CHECK - API, website, data freshness")
    logger.info("=" * 60)
    try:
        now = datetime.now(timezone.utc)
        results = run_checks(now)
        for name, ok, detail in results:
            logger.info(f"  {name:<8} {'OK     ' if ok else 'FAILING'} {detail}")
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        gh = _alerts.GitHub(token, repo) if token and repo and not dry_run else None
        open_issue = None
        if gh:
            items = gh.call("GET", "/issues", params={"labels": LABEL, "state": "open", "per_page": 20})
            open_issue = next((i for i in items if "pull_request" not in i and i["title"] == TITLE), None)
        action = plan(results, open_issue, now)
        if action is None:
            logger.info("    nothing to open, update or close")
        elif not gh:
            logger.info(f"    would {action['do']} the uptime issue (dry run)")
        elif action["do"] == "open":
            gh.call("POST", "/labels", json={"name": LABEL, "color": LABEL_COLOUR,
                                             "description": "The site or API is down or serving old data"})
            gh.call("POST", "/issues", json={"title": TITLE, "body": action["body"], "labels": [LABEL]})
            logger.info("    opened the uptime issue")
        elif action["do"] == "update":
            gh.call("PATCH", f"/issues/{action['number']}", json={"body": action["body"]})
            logger.info(f"    updated #{action['number']}")
        else:
            gh.call("POST", f"/issues/{action['number']}/comments", json={"body": action["comment"]})
            gh.call("PATCH", f"/issues/{action['number']}", json={"state": "closed", "state_reason": "completed"})
            logger.info(f"    closed #{action['number']}")
    except Exception as e:
        logger.warning(f"  uptime check failed this run (job continues): {e}")
    return True


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
    sys.exit(0)
