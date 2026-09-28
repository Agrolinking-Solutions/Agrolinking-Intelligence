"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Quality Gate — run after the pipeline, before anything is committed/pushed.

Fails (exit code 1) if the data or the latest outputs are unfit to serve:
  - git conflict markers in the master dataset or feature files
  - NaN / Infinity anywhere in the latest validated, zonal or intelligence
    JSON (the API can't encode these — endpoints return 500)
  - a commodity missing from the latest validated forecast, or with a
    missing / non-positive daily price
  - "nan" rendered into the latest subscriber alert

Usage:
  python pipeline/quality_gate.py
"""

import os, sys, re, glob, json, math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS, COMMODITIES, ROOT

CONFLICT_MARKER = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.MULTILINE)
NAN_WORD        = re.compile(r"(?<![a-z])nan(?![a-z])")


def _reject_non_finite(token):
    raise ValueError(f"contains {token}")


def latest(pattern):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def check_conflict_markers(errors):
    paths = [PATHS["master"]]
    paths += glob.glob(os.path.join(ROOT, "data", "processed", "features", "*.csv"))
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            if CONFLICT_MARKER.search(f.read()):
                errors.append(f"git conflict markers in {os.path.relpath(path, ROOT)}")


def load_strict(path, errors):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f, parse_constant=_reject_non_finite)
    except ValueError as e:
        errors.append(f"{os.path.relpath(path, ROOT)}: {e}")
        return None


def check_outputs(errors):
    fc_dir = PATHS["forecasts_dir"]
    targets = {
        "validated":    os.path.join(fc_dir, "validated", "forecast_validated_*.json"),
        "zonal":        os.path.join(fc_dir, "zonal", "zonal_forecast_*.json"),
        "intelligence": os.path.join(ROOT, "outputs", "intelligence", "intelligence_*.json"),
    }
    loaded = {}
    for name, pattern in targets.items():
        path = latest(pattern)
        if path is None:
            errors.append(f"no {name} output file found")
            continue
        loaded[name] = load_strict(path, errors)

    validated = loaded.get("validated")
    if validated is not None:
        validated = validated.get("forecasts", validated)
        for commodity in COMMODITIES:
            fc = validated.get(commodity)
            if fc is None:
                errors.append(f"validated forecast missing commodity: {commodity}")
                continue
            vals = fc.get("horizons", {}).get("daily", {}).get("ensemble", {}).get("values", [])
            price = vals[0] if vals else None
            if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                errors.append(f"{commodity}: invalid daily price {price!r}")

    alert = latest(os.path.join(PATHS["daily_alerts_dir"], "alert_validated_*.txt"))
    if alert:
        with open(alert, encoding="utf-8") as f:
            if NAN_WORD.search(f.read()):
                errors.append(f"{os.path.relpath(alert, ROOT)} contains 'nan' prices")


def run_gate() -> bool:
    errors = []
    check_conflict_markers(errors)
    check_outputs(errors)
    if errors:
        print("QUALITY GATE FAILED - do not commit or push these outputs:")
        for e in errors[:50]:
            print(f"  - {e}")
        if len(errors) > 50:
            print(f"  ... and {len(errors) - 50} more")
        return False
    print(f"QUALITY GATE PASSED - {len(COMMODITIES)} commodities, outputs valid")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_gate() else 1)
