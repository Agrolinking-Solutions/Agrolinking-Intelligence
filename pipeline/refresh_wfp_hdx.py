"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Automated WFP Nigeria Refresh (via HDX CKAN API)

HDX (data.humdata.org) runs on CKAN, which has a fully public API for
PUBLIC datasets - no login, no API key. This script:
  1. Asks HDX for the current resource list for the Nigeria Food
     Prices dataset (metadata only - tells us the latest file's URL
     and last-modified date, cheap and safe to call daily).
  2. Only downloads the actual CSV if it's newer than what we last
     pulled (tracked in a small state file) - avoids hammering HDX.
  3. Overwrites data/raw/wfp_food_prices_nga.csv with the fresh pull.

IMPORTANT CAVEAT: HDX's Nigeria mirror of this data lags real time
by many months (it was ~9 months stale as of Aug 2026). This is a
supplementary/backup refresh, not a substitute for keeping Agricome's
own scrape current. Pair this with the staleness check
(09_staleness_check.py) so genuine gaps still get caught even when
this pull comes back "successful" but still old.

Run manually or on a schedule:
  python pipeline/refresh_wfp_hdx.py
"""

import os, sys, json
from datetime import datetime

import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

HDX_DATASET_ID = "wfp-food-prices-for-nigeria"
HDX_API_URL    = f"https://data.humdata.org/api/3/action/package_show?id={HDX_DATASET_ID}"
STATE_FILE     = os.path.join(os.path.dirname(PATHS["raw_wfp"]), ".wfp_hdx_state.json")


def get_latest_resource_info():
    """Ask HDX (metadata only, cheap) which CSV resource is current."""
    resp = requests.get(HDX_API_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"HDX API returned success=false: {data}")

    resources = data["result"]["resources"]
    # Prefer a resource that looks like the main price CSV (not the
    # QuickCharts/metadata companion files HDX also attaches)
    csv_resources = [
        r for r in resources
        if r.get("format", "").upper() == "CSV" and "qc" not in r.get("name", "").lower()
    ]
    if not csv_resources:
        raise RuntimeError("No suitable CSV resource found in HDX dataset")

    # Most recently modified first
    csv_resources.sort(key=lambda r: r.get("last_modified", ""), reverse=True)
    return csv_resources[0]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def run_refresh(force: bool = False):
    logger.info("=" * 60)
    logger.info("WFP NIGERIA — HDX AUTO-REFRESH")
    logger.info("=" * 60)

    try:
        resource = get_latest_resource_info()
    except Exception as e:
        logger.error(f"  Could not reach HDX API: {e}")
        return False

    remote_modified = resource.get("last_modified", "")
    download_url = resource.get("url", "")
    logger.info(f"  Latest HDX resource: {resource.get('name')}")
    logger.info(f"  Last modified (HDX): {remote_modified}")

    state = load_state()
    if not force and state.get("last_modified") == remote_modified:
        logger.info("  No update since last pull — skipping download.")
        return True

    logger.info(f"  New version detected — downloading...")
    try:
        csv_resp = requests.get(download_url, timeout=120)
        csv_resp.raise_for_status()
    except Exception as e:
        logger.error(f"  Download failed: {e}")
        return False

    out_path = PATHS["raw_wfp"]
    backup_path = out_path + f".backup_{datetime.now().strftime('%Y%m%d')}"
    if os.path.exists(out_path):
        os.replace(out_path, backup_path)
        logger.debug(f"  Previous file backed up -> {backup_path}")

    with open(out_path, "wb") as f:
        f.write(csv_resp.content)

    save_state({
        "last_modified": remote_modified,
        "pulled_at": datetime.now().isoformat(),
        "download_url": download_url,
    })

    logger.success(f"  WFP data refreshed -> {out_path}")
    logger.info("  Run 01_ingest.py next to pull this into the master dataset.")
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if HDX shows no change")
    args = parser.parse_args()
    ok = run_refresh(force=args.force)
    sys.exit(0 if ok else 1)