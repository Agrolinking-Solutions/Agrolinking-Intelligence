"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Automated Daily Scheduler

Runs the full pipeline automatically every day at a configured time.
Also handles weekly model retraining on Mondays.

Usage:
  python pipeline/scheduler.py          # Start the scheduler (runs indefinitely)
  python pipeline/scheduler.py --now    # Run pipeline once immediately then schedule

Windows Task Scheduler alternative:
  Instead of running this script 24/7, you can use Windows Task Scheduler
  to run: python pipeline/run_pipeline.py --skip-train
  at 8:00 AM every day, and:
  python pipeline/run_pipeline.py
  at 8:00 AM every Monday.
"""

import os, sys, time, argparse
from datetime import datetime, timedelta
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")
logger.add(
    os.path.join(PATHS["logs_dir"], "scheduler_{time:YYYY-MM}.log"),
    rotation="1 month", retention="3 months", level="DEBUG")

# ── Configuration ──────────────────────────────────────────────────────────
DAILY_RUN_HOUR   = 8     # 8:00 AM daily forecast run
DAILY_RUN_MINUTE = 0
WEEKLY_TRAIN_DAY = 0     # 0=Monday — full retrain with new data


def run_now(full_train: bool = False):
    """Trigger the pipeline immediately."""
    import importlib.util
    pipeline_dir = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "run_pipeline",
        os.path.join(pipeline_dir, "run_pipeline.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run_full_pipeline(skip_train=not full_train)


def seconds_until(hour: int, minute: int) -> float:
    """Seconds until next occurrence of HH:MM."""
    now   = datetime.now()
    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if today <= now:
        today += timedelta(days=1)
    return (today - now).total_seconds()


def start_scheduler(run_immediately: bool = False):
    """Main scheduler loop."""
    logger.info("╔" + "═"*55 + "╗")
    logger.info("║  AGROLINKING INTELLIGENCE SCHEDULER STARTED         ║")
    logger.info(f"║  Daily run: {DAILY_RUN_HOUR:02d}:{DAILY_RUN_MINUTE:02d}  "
                f"│  Weekly retrain: Monday {DAILY_RUN_HOUR:02d}:{DAILY_RUN_MINUTE:02d}  ║")
    logger.info("║  Press Ctrl+C to stop                                ║")
    logger.info("╚" + "═"*55 + "╝")

    if run_immediately:
        logger.info("Running pipeline immediately (--now flag)")
        is_monday = datetime.now().weekday() == WEEKLY_TRAIN_DAY
        run_now(full_train=is_monday)

    while True:
        now     = datetime.now()
        secs    = seconds_until(DAILY_RUN_HOUR, DAILY_RUN_MINUTE)
        next_dt = now + timedelta(seconds=secs)

        logger.info(
            f"Next run: {next_dt.strftime('%A %d %b %Y at %H:%M')} "
            f"({secs/3600:.1f}h from now)"
        )

        # Sleep in 60-second intervals so we can log countdown
        remaining = secs
        while remaining > 60:
            time.sleep(60)
            remaining -= 60
            if remaining % 3600 < 60:  # Log every hour
                logger.info(f"⏰ Next run in {remaining/3600:.1f} hours")

        time.sleep(max(0, remaining))

        # Time to run
        logger.info("🚀 Scheduled pipeline run starting...")
        is_monday   = datetime.now().weekday() == WEEKLY_TRAIN_DAY
        full_train  = is_monday
        if full_train:
            logger.info("  Monday detected — running full retrain")
        else:
            logger.info("  Daily run — skipping model retrain")

        try:
            run_now(full_train=full_train)
        except Exception as e:
            logger.error(f"Scheduled run failed: {e}")

        # Small buffer before calculating next run
        time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agrolinking Daily Scheduler")
    parser.add_argument("--now", action="store_true",
                        help="Run pipeline immediately then start scheduler")
    args = parser.parse_args()

    try:
        start_scheduler(run_immediately=args.now)
    except KeyboardInterrupt:
        logger.info("\nScheduler stopped by user.")
