"""
Fix MANUAL_PRICES in pipeline/06_validate.py with verified 2026 market prices.
Usage: python fix_validate_prices.py
"""
import re, os, sys

validate_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline", "06_validate.py")
if not os.path.exists(validate_path):
    print(f"ERROR: {validate_path} not found")
    sys.exit(1)

# Read with UTF-8 encoding to handle special characters
with open(validate_path, encoding="utf-8", errors="replace") as f:
    content = f.read()

old_block_start = content.find("MANUAL_PRICES = {")
if old_block_start == -1:
    print("ERROR: MANUAL_PRICES dict not found in 06_validate.py")
    print("Check the variable name — it may be called WEB_PRICES or REFERENCE_PRICES")
    # Show what price-related variables exist
    for line in content.split("\n"):
        if "price" in line.lower() and "=" in line and "{" in line:
            print(f"  Found: {line.strip()[:80]}")
    sys.exit(1)

# Find the closing brace of MANUAL_PRICES
depth = 0
i = old_block_start
while i < len(content):
    if content[i] == "{": depth += 1
    elif content[i] == "}":
        depth -= 1
        if depth == 0:
            old_block_end = i + 1
            break
    i += 1

new_block = """MANUAL_PRICES = {
    # Agricome Africa (@agricomeafrica) April 2026 confirmed posts
    "Hibiscus":      2_325_000,   # N2.1M-N2.65M  Agricome Apr 16 2026
    "Sesame":        1_245_000,   # N1.1M-N1.35M  Agricome Apr 16 2026
    "Ginger":        9_700_000,   # N8.5M-N11M    Agricome Apr 16 2026
    "Cocoa":         5_650_000,   # N5.1M-N6.5M   Agricome Apr 16 2026
    "Soybeans":        745_000,   # N650K-N850K   Agricome Apr 16 2026
    "Cashew Nuts":   1_950_000,   # N1.7M-N2.2M   Agricome Apr 16 2026
    # WFP Nigeria Mar 2026 + market research
    "Sorghum":         335_000,   # WFP Mar 2026 + trend
    "Beans (white)":   813_000,   # WFP Mar 2026 confirmed
    "Beans (red)":     915_000,   # WFP Mar 2026 confirmed
    "Maize (white)":   370_000,   # Current market N290K-N450K
    "Maize (yellow)":  400_000,   # Current market N310K-N480K
    "Wheat":           706_833,   # Agrolinking primary Apr 13, 2026
}"""

old_block = content[old_block_start:old_block_end]
content = content[:old_block_start] + new_block + content[old_block_end:]

# Write back with UTF-8 encoding
with open(validate_path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"SUCCESS: MANUAL_PRICES updated in {validate_path}")
print()
print("New prices:")
print("  Hibiscus     N2,325,000")
print("  Sesame       N1,245,000")
print("  Ginger       N9,700,000")
print("  Cocoa        N5,650,000")
print("  Soybeans       N745,000")
print("  Cashew Nuts  N1,950,000")
print("  Sorghum        N335,000")
print("  Beans (white)  N813,000")
print("  Beans (red)    N915,000")
print("  Maize (white)  N370,000")
print("  Maize (yellow) N400,000")
print("  Wheat          N706,833")