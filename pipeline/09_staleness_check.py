"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Data Freshness / Staleness Alert

Runs after every pipeline run. Checks each commodity's actual
last-known REAL price date against a threshold, and raises a loud,
impossible-to-miss warning if any commodity has gone stale —
BEFORE that staleness reaches the forecast, the dashboard, or a
customer, the way the Maize (white)/(yellow) 2023 anchor did for
months before anyone noticed.

Run this as the LAST step of every pipeline run:
  python pipeline/09_staleness_check.py

Exit code is non-zero if anything is stale, so it can also be used
as a CI/cron failure signal (e.g. `python pipeline/09_staleness_check.py || echo FAILED`).
"""

import os, sys, json, smtplib
from datetime import datetime
from email.mime.text import MIMEText

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

REAL_SOURCES = {"Agricome", "WFP", "Agrolinking_primary"}

# Thresholds — tuned to how often each commodity's real source SHOULD
# update. Agricome-native commodities are scraped weekly, so >45 days
# stale is already a real problem worth flagging. Adjust if your
# team's actual scrape cadence differs.
WARN_DAYS  = 45   # yellow flag — worth a look
ALERT_DAYS = 90   # red flag — treat as broken, same class of bug as Maize

ALERT_OUT = os.path.join(PATHS["logs_dir"], "staleness_alert.json")

# Optional email alerting — leave EMAIL_TO empty to disable and just
# rely on the JSON file + non-zero exit code.
EMAIL_TO       = os.environ.get("STALENESS_ALERT_EMAIL", "")
EMAIL_FROM     = os.environ.get("ALERT_EMAIL_FROM", "")
SMTP_HOST      = os.environ.get("SMTP_HOST", "")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD  = os.environ.get("SMTP_PASSWORD", "")


def check_freshness():
    df = pd.read_csv(PATHS["master"], parse_dates=["date"])
    today = pd.Timestamp(datetime.now().date())

    results = []
    for commodity in COMMODITIES:
        sub = df[df["commodity"] == commodity]
        real = sub[sub["data_source"].isin(REAL_SOURCES)]

        if real.empty:
            results.append({
                "commodity": commodity,
                "last_real_date": None,
                "days_stale": None,
                "status": "NO_REAL_DATA",
            })
            continue

        last_real_date = real["date"].max()
        days_stale = (today - last_real_date).days

        if days_stale >= ALERT_DAYS:
            status = "ALERT"
        elif days_stale >= WARN_DAYS:
            status = "WARN"
        else:
            status = "OK"

        results.append({
            "commodity": commodity,
            "last_real_date": str(last_real_date.date()),
            "days_stale": int(days_stale),
            "status": status,
        })

    return results


def format_report(results, run_date):
    alerts = [r for r in results if r["status"] == "ALERT"]
    warns  = [r for r in results if r["status"] == "WARN"]
    no_data = [r for r in results if r["status"] == "NO_REAL_DATA"]

    lines = [
        "=" * 60,
        "  DATA FRESHNESS CHECK",
        f"  {run_date.strftime('%A, %d %B %Y')}",
        "=" * 60,
    ]

    if not alerts and not warns and not no_data:
        lines.append(f"  ✅ All {len(results)} commodities within {WARN_DAYS} days — no issues.")
    else:
        if alerts:
            lines.append(f"\n  🚨 ALERT — {len(alerts)} commodit(y/ies) stale >{ALERT_DAYS} days:")
            for r in alerts:
                lines.append(f"     {r['commodity']:<20} last real data: {r['last_real_date']} "
                             f"({r['days_stale']} days ago)")
        if warns:
            lines.append(f"\n  ⚠️  WARN — {len(warns)} commodit(y/ies) stale >{WARN_DAYS} days:")
            for r in warns:
                lines.append(f"     {r['commodity']:<20} last real data: {r['last_real_date']} "
                             f"({r['days_stale']} days ago)")
        if no_data:
            lines.append(f"\n  🚨 NO REAL DATA at all for {len(no_data)} commodit(y/ies):")
            for r in no_data:
                lines.append(f"     {r['commodity']}")

    lines.append("=" * 60)
    return "\n".join(lines)


def send_email_alert(subject, body):
    if not (EMAIL_TO and EMAIL_FROM and SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        logger.debug("  Email alerting not configured (missing env vars) — skipping.")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        logger.success(f"  Alert email sent to {EMAIL_TO}")
    except Exception as e:
        logger.warning(f"  Failed to send alert email: {e}")


def run_staleness_check():
    run_date = datetime.now()
    logger.info("=" * 60)
    logger.info("STEP 9 — DATA FRESHNESS CHECK")
    logger.info("=" * 60)

    results = check_freshness()
    report = format_report(results, run_date)
    for line in report.split("\n"):
        logger.info(line)

    os.makedirs(os.path.dirname(ALERT_OUT), exist_ok=True)
    with open(ALERT_OUT, "w") as f:
        json.dump({
            "run_date": str(run_date.date()),
            "results": results,
        }, f, indent=2)

    n_alert = sum(1 for r in results if r["status"] == "ALERT")
    n_no_data = sum(1 for r in results if r["status"] == "NO_REAL_DATA")

    if n_alert or n_no_data:
        send_email_alert(
            subject=f"🚨 Agrolinking: {n_alert + n_no_data} commodit(y/ies) with stale/missing data",
            body=report,
        )
        logger.error(f"STALENESS CHECK FAILED — {n_alert + n_no_data} commodit(y/ies) need attention")
        return False

    logger.success("STALENESS CHECK PASSED")
    return True


if __name__ == "__main__":
    ok = run_staleness_check()
    sys.exit(0 if ok else 1)