"""
Run this script once to patch 06_validate.py so it works with both the old
and new forecast JSON structure from 05_forecast.py.

Usage: python fix_validate_structure.py
"""
import os, sys, re

validate_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pipeline", "06_validate.py"
)
if not os.path.exists(validate_path):
    print(f"ERROR: {validate_path} not found")
    sys.exit(1)

with open(validate_path, encoding="utf-8", errors="replace") as f:
    content = f.read()

# ── PATCH 1: inject get_horizon_endpoint helper ──────────────────────────────
helper = '''
def get_horizon_endpoint(h_data):
    """
    Handle both old and new forecast JSON horizon structures.
    Old: h_data["forecast_end"] = {"date": "...", "price": 123}
    New: h_data["forecast_end"] = "2026-05-25" (string)
         h_data["forecast_end_detail"] = {"date": "...", "price": 123, ...}
    """
    fc_end = h_data.get("forecast_end", {})
    if isinstance(fc_end, str):
        detail = h_data.get("forecast_end_detail", {})
        vals   = h_data.get("ensemble", {}).get("values", [0])
        return {
            "date":                  fc_end,
            "price":                 detail.get("price", vals[-1] if vals else 0),
            "pct_change_from_today": detail.get("pct_change_from_today", 0),
        }
    elif isinstance(fc_end, dict) and fc_end:
        return fc_end
    else:
        vals  = h_data.get("ensemble", {}).get("values", [0])
        dates = h_data.get("dates", [""])
        return {
            "date":                  dates[-1] if dates else "",
            "price":                 vals[-1] if vals else 0,
            "pct_change_from_today": 0,
        }

'''

# Insert helper before the first def validate_commodity or def run_validation
insert_marker = None
for marker in ["def validate_commodity", "def run_validation", "def validate("]:
    if marker in content:
        insert_marker = marker
        break

if insert_marker and "def get_horizon_endpoint" not in content:
    content = content.replace(insert_marker, helper + insert_marker, 1)
    print("Injected get_horizon_endpoint helper")
else:
    print("Helper already present or no insertion point found")

# ── PATCH 2: replace all h_data["forecast_end"]["date"] / ["price"] ─────────
# Pattern: h_data["forecast_end"]["date"]
content = re.sub(
    r'h_data\["forecast_end"\]\["date"\]',
    'get_horizon_endpoint(h_data)["date"]',
    content
)
# Pattern: h_data["forecast_end"]["price"]
content = re.sub(
    r'h_data\["forecast_end"\]\["price"\]',
    'get_horizon_endpoint(h_data)["price"]',
    content
)
# Pattern: h_data["forecast_end"]["pct_change_from_today"]
content = re.sub(
    r'h_data\["forecast_end"\]\["pct_change_from_today"\]',
    'get_horizon_endpoint(h_data)["pct_change_from_today"]',
    content
)
# Handle fc_end = h_data["forecast_end"] then fc_end["date"] style
content = re.sub(
    r'fc_end\s*=\s*h_data\["forecast_end"\]\s*\n(\s*)fc_end\[',
    lambda m: f'fc_end = get_horizon_endpoint(h_data)\n{m.group(1)}fc_end[',
    content
)

print("Patched forecast_end references")

# ── PATCH 3: fix Maize last_known_date from 2023 ────────────────────────────
# The validated JSON has wrong last_known_date for Maize - patch it at load time
# Add a post-load fix in run_validation
old_load = 'logger.info(f"  Loaded forecast: {fc_file}")'
new_load = '''logger.info(f"  Loaded forecast: {fc_file}")

    # Fix stale last_known_date for commodities where master data was bridged
    CORRECT_LAST_KNOWN = {
        "Maize (white)":  "2026-04-06",
        "Maize (yellow)": "2026-04-06",
    }
    for comm, correct_date in CORRECT_LAST_KNOWN.items():
        if comm in all_forecasts:
            current = all_forecasts[comm].get("last_known_date", "")
            if current < "2026-01-01":
                all_forecasts[comm]["last_known_date"] = correct_date
                logger.info(f"  Fixed last_known_date for {comm}: {current} -> {correct_date}")'''

if old_load in content:
    content = content.replace(old_load, new_load)
    print("Injected last_known_date fix for Maize")
else:
    print("NOTE: Could not inject Maize date fix - add manually if needed")

import ast
try:
    ast.parse(content)
    print("Syntax OK")
except SyntaxError as e:
    print(f"Syntax error line {e.lineno}: {e}")
    lines = content.split("\n")
    for i, l in enumerate(lines[max(0,e.lineno-3):e.lineno+3], max(0,e.lineno-3)+1):
        print(f"  {i}: {l}")
    sys.exit(1)

with open(validate_path, "w", encoding="utf-8") as f:
    f.write(content)
print(f"\nSUCCESS: {validate_path} patched")
print("Now run: python pipeline/06_validate.py")