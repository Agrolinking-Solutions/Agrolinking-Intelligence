"""
Updates MANUAL_PRICES in pipeline/06_validate.py with recalibrated
reference prices based on May 2026 market research.

Key changes from research:
  Ginger:  N9.70M -> N12,000,000  (NGX confirmed N13,000/kg Feb 2026,
                                    using N12M as conservative mid-market)
  Sesame:  N1.245M -> N1,650,000  (LCFE N1,650-N2,000/kg; using N1,650/kg)
  Sorghum: N335K   -> N420,000    (Market Naija TV N44,000/bag = N880K retail;
                                    wholesale mid-chain ~N420K is more accurate)
  Rice:    N1,550,000 (confirmed accurate per research)

Run once: python fix_manual_prices_v2.py
"""
import os, sys, re

BASE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(BASE, "pipeline", "06_validate.py")

if not os.path.exists(path):
    print(f"ERROR: {path} not found"); sys.exit(1)

with open(path, encoding="utf-8", errors="replace") as f:
    content = f.read()

# Find and replace MANUAL_PRICES block completely
old_start = content.find("MANUAL_PRICES = {")
if old_start == -1:
    print("ERROR: MANUAL_PRICES not found"); sys.exit(1)

# Find closing brace
depth = 0
i = old_start
while i < len(content):
    if content[i] == "{": depth += 1
    elif content[i] == "}":
        depth -= 1
        if depth == 0:
            old_end = i + 1
            break
    i += 1

new_block = """MANUAL_PRICES = {
    # ── Agricome Africa (@agricomeafrica) April 2026 confirmed posts ──────────
    "Hibiscus":      2_325_000,   # N2.1M-N2.65M   Agricome Apr 16 2026
    "Sesame":        1_650_000,   # RECALIBRATED: LCFE N1,650-N2,000/kg May 2026
                                  # (was N1,245,000 -- farmgate anchor was too low)
    "Ginger":       12_000_000,   # RECALIBRATED: NGX confirmed N13,000/kg Feb 2026
                                  # Using N12M as conservative mid-market estimate
                                  # (was N9,700,000 -- 25-35% below exchange level)
    "Cocoa":         5_650_000,   # N5.1M-N6.5M    Agricome Apr 16 2026
    "Soybeans":        745_000,   # N650K-N850K    Agricome Apr 16 2026
    "Cashew Nuts":   1_950_000,   # N1.7M-N2.2M    Agricome Apr 16 2026
    # ── WFP Nigeria + market research ────────────────────────────────────────
    "Sorghum":         420_000,   # RECALIBRATED: Market Naija TV N44K/bag = N880K
                                  # retail; wholesale mid-chain ~N420K
                                  # (was N335,000 -- northern farmgate dragging low)
    "Beans (white)":   813_000,   # WFP Mar 2026 confirmed
    "Beans (red)":     915_000,   # WFP Mar 2026 confirmed
    "Maize (white)":   370_000,   # Market 2026 N290K-N450K (well-calibrated)
    "Maize (yellow)":  400_000,   # Market 2026 N310K-N480K (well-calibrated)
    "Wheat":           706_833,   # Agrolinking primary Apr 13 2026
    "Rice":          1_550_000,   # Market research May 2026 (50kg N75K-N87K)
}"""

content = content[:old_start] + new_block + content[old_end:]

import ast
try:
    ast.parse(content)
    print("Syntax OK")
except SyntaxError as e:
    print(f"Syntax error: {e}"); sys.exit(1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"SUCCESS: MANUAL_PRICES updated in {path}")
print()
print("Recalibrated prices:")
print("  Ginger:  N9,700,000 -> N12,000,000  (+23.7% -- NGX exchange confirmed)")
print("  Sesame:  N1,245,000 -> N1,650,000   (+32.5% -- LCFE commercial level)")
print("  Sorghum:   N335,000 ->   N420,000   (+25.4% -- mid-chain wholesale)")
print()
print("Unchanged (already accurate):")
print("  Maize (white/yellow), Beans, Cocoa, Cashew Nuts, Hibiscus, Wheat, Rice")
print()
print("Next step: re-run pipeline steps 05-07:")
print("  python pipeline\\05_forecast.py")
print("  python pipeline\\06_validate.py")
print("  python pipeline\\07_zonal_forecast.py")
