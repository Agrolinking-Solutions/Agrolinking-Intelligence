"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Master Pipeline Runner — runs all steps in sequence.

Usage:
  python pipeline/run_pipeline.py              # Run full pipeline
  python pipeline/run_pipeline.py --skip-train # Skip model training (faster daily run)
  python pipeline/run_pipeline.py --train-only # Only retrain models
"""

import os, sys, argparse, time, json, traceback
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")
logger.add(
    os.path.join(PATHS["logs_dir"], "pipeline_{time:YYYY-MM-DD}.log"),
    rotation="1 day", retention="30 days", level="DEBUG")


def run_step(name: str, func, *args, **kwargs):
    """Run a pipeline step with timing and error handling."""
    logger.info(f"\n{'═'*60}")
    logger.info(f"  RUNNING: {name}")
    logger.info(f"{'═'*60}")
    start = time.time()
    try:
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        logger.success(f"  ✅ {name} completed in {elapsed:.1f}s")
        return result, True
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"  ❌ {name} FAILED after {elapsed:.1f}s: {e}")
        logger.debug(traceback.format_exc())
        return None, False


def run_full_pipeline(skip_train: bool = False, train_only: bool = False):
    """Run the complete Agrolinking intelligence pipeline."""
    run_start = datetime.now()
    results   = {}

    logger.info("╔" + "═"*58 + "╗")
    logger.info("║  AGROLINKING COMMODITY INTELLIGENCE PIPELINE          ║")
    logger.info(f"║  {run_start.strftime('%A, %d %B %Y  %H:%M')}                       ║")
    logger.info("╚" + "═"*58 + "╝")

    # ── Import steps ──────────────────────────────────────────────────────────
    import importlib.util

    def load_module(path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    pipeline_dir = os.path.dirname(os.path.abspath(__file__))

    # ── Step 1: Ingest ────────────────────────────────────────────────────────
    if not train_only:
        ingest = load_module(os.path.join(pipeline_dir, "01_ingest.py"), "ingest")
        _, ok = run_step("Step 1 — Data Ingestion", ingest.run_ingestion)
        results["ingest"] = ok
        if not ok:
            logger.error("Ingest failed — aborting pipeline")
            return results

        # ── Step 2: Clean ─────────────────────────────────────────────────────
        clean = load_module(os.path.join(pipeline_dir, "02_clean.py"), "clean")
        _, ok = run_step("Step 2 — Data Cleaning", clean.run_cleaning)
        results["clean"] = ok
        if not ok:
            logger.error("Cleaning failed — aborting pipeline")
            return results

        # ── Step 3: Features ──────────────────────────────────────────────────
        feat = load_module(os.path.join(pipeline_dir, "03_features.py"), "features")
        _, ok = run_step("Step 3 — Feature Engineering", feat.run_feature_engineering)
        results["features"] = ok
        if not ok:
            logger.error("Feature engineering failed — aborting pipeline")
            return results

    # ── Step 4: Train (weekly, or on demand) ──────────────────────────────────
    if not skip_train:
        # Only retrain on Mondays or if forced
        today = datetime.now().weekday()
        should_train = (today == 0) or train_only  # Monday = 0

        if should_train or train_only:
            import warnings, logging as pylog
            warnings.filterwarnings("ignore")
            pylog.getLogger("prophet").setLevel(pylog.WARNING)
            pylog.getLogger("cmdstanpy").setLevel(pylog.WARNING)

            train = load_module(os.path.join(pipeline_dir, "04_train.py"), "train")
            _, ok = run_step("Step 4 — Model Training", train.run_training)
            results["train"] = ok
        else:
            logger.info(f"\n  Step 4 — Model Training: SKIPPED (next Monday)")
            results["train"] = "skipped"

    if train_only:
        logger.success("Train-only mode complete.")
        return results

    # ── Step 5: Forecast ──────────────────────────────────────────────────────
    # Remove old forecast rows before generating new ones
    import pandas as pd
    master_path = PATHS["master"]
    if os.path.exists(master_path):
        master = pd.read_csv(master_path, parse_dates=["date"])
        master = master[master["record_type"] != "forecast"]
        master.to_csv(master_path, index=False)
        logger.debug("  Cleared old forecast rows from master")

    forecast = load_module(os.path.join(pipeline_dir, "05_forecast.py"), "forecast")
    _, ok = run_step("Step 5 — Forecasting", forecast.run_forecasting)
    results["forecast"] = ok
    if not ok:
        logger.error("Forecasting failed — skipping validation")
        return results

    # ── Step 6: Validate ──────────────────────────────────────────────────────
    validate = load_module(os.path.join(pipeline_dir, "06_validate.py"), "validate")
    _, ok = run_step("Step 6 — Cross-Reference Validation", validate.run_validation)
    results["validate"] = ok

    # ── Summary ───────────────────────────────────────────────────────────────
    total_time = time.time() - run_start.timestamp()
    total_elapsed = (datetime.now() - run_start).total_seconds()

    logger.info("\n" + "═"*60)
    logger.info("  PIPELINE SUMMARY")
    logger.info("═"*60)
    step_icons = {True:"✅", False:"❌", "skipped":"⏭️"}
    for step, status in results.items():
        logger.info(f"  {step_icons.get(status,'?')} {step.title():<20} {status}")
    logger.info(f"\n  Total time: {total_elapsed/60:.1f} minutes")
    logger.info("═"*60)

    # Save run summary
    summary = {
        "run_at":       run_start.isoformat(),
        "elapsed_secs": round(total_elapsed, 1),
        "results":      {k: str(v) for k, v in results.items()},
        "success":      all(v is True or v == "skipped" for v in results.values()),
    }
    summary_path = os.path.join(PATHS["logs_dir"], "last_run.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    if summary["success"]:
        logger.success("PIPELINE COMPLETE — all steps succeeded")
    else:
        logger.warning("PIPELINE COMPLETE — some steps failed, check logs")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agrolinking Intelligence Pipeline")
    parser.add_argument("--skip-train", action="store_true",
                        help="Skip model training (use for daily runs after initial setup)")
    parser.add_argument("--train-only", action="store_true",
                        help="Only retrain models, skip forecast/validate")
    args = parser.parse_args()
    run_full_pipeline(skip_train=args.skip_train, train_only=args.train_only)
