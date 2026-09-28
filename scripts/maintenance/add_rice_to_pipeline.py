"""
ONE-TIME SETUP: Add Rice to the Agrolinking Intelligence Pipeline.

Run this script once from your project root:
    python add_rice_to_pipeline.py

What it does:
  1. Copies rice_historical.csv into data/raw/
  2. Updates config/settings.py to add Rice to COMMODITIES list
  3. Updates data/external/state_price_differentials.csv with Rice factors
  4. Updates data/external/verified_prices_2026.json with Rice reference price
  5. Updates data/external/zones_config.json with Rice as primary product where relevant

After running this, do a full pipeline retrain:
    python pipeline/02_clean.py
    python pipeline/03_features.py
    python pipeline/04_train.py
    python pipeline/05_forecast.py
    python pipeline/06_validate.py
    python pipeline/07_zonal_forecast.py
"""
import os, sys, json, shutil, re
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
print(f"Project root: {BASE}")

# ── STEP 1: Copy rice historical data ────────────────────────────────────────
src = os.path.join(BASE, "rice_historical.csv")
dst = os.path.join(BASE, "data", "raw", "rice_historical.csv")
if not os.path.exists(src):
    print(f"ERROR: rice_historical.csv not found at {src}")
    print("Place rice_historical.csv in the project root first.")
    sys.exit(1)
shutil.copy(src, dst)
print(f"Copied rice_historical.csv -> {dst}")

# ── STEP 2: Update config/settings.py ────────────────────────────────────────
settings_path = os.path.join(BASE, "config", "settings.py")
with open(settings_path, encoding="utf-8") as f:
    settings = f.read()

if '"Rice"' not in settings and "'Rice'" not in settings:
    # Add Rice to the COMMODITIES list - insert after Wheat (last item)
    settings = re.sub(
        r'([\"\']Wheat[\"\'],?\s*\])',
        lambda m: m.group(0).replace(
            m.group(0),
            m.group(0).rstrip(']').rstrip(',').rstrip() + ',\n    "Rice"\n]'
        ) if '"Rice"' not in m.group(0) else m.group(0),
        settings
    )
    with open(settings_path, "w", encoding="utf-8") as f:
        f.write(settings)
    print("Added 'Rice' to COMMODITIES in config/settings.py")
else:
    print("Rice already in COMMODITIES - skipping")

# ── STEP 3: Update state_price_differentials.csv ─────────────────────────────
diff_path = os.path.join(BASE, "data", "external", "state_price_differentials.csv")
diff_df   = pd.read_csv(diff_path)

# Rice price factors by state (vs national average N1,550,000/MT)
# Rice is imported-dependent in the North, locally produced in some South states
# Ebonyi/Cross River produce local rice but not in our 12 states
# Kebbi state is Nigeria's biggest rice producer but not in our zones either
# So all our 12 states are roughly at or above national average
RICE_FACTORS = {
    # North West - grains dominant, some local rice cultivation
    "Kano":    0.95,  # some local rice, good market access
    "Kaduna":  0.93,  # Wushishi area rice, slightly cheaper
    # North Central - Middle Belt has rice cultivation
    "Plateau": 0.90,  # Shendam/Lafia rice growing area nearby
    "Kogi":    0.96,  # fair access, not a producer
    # North East - semi-arid, import dependent
    "Adamawa": 1.02,  # some Benue valley rice
    "Borno":   1.10,  # conflict premium, import dependent
    # South West - high consumption
    "Oyo":     1.08,  # consumer state
    "Lagos":   1.18,  # highest cost, import terminal premium
    # South East - heavy rice consumption
    "Anambra": 1.12,
    "Imo":     1.10,
    # South South - oil belt, premium pricing
    "Rivers":  1.15,
    "Delta":   1.12,
}

# Primary rice states (where rice is considered a primary product)
RICE_PRIMARY = ["Plateau", "Kaduna"]  # closest to rice production zones

new_rows = []
# Get zone defaults from existing data
zone_defaults = {}
for _, row in diff_df.iterrows():
    zone_defaults[row['state']] = row.get('zone_default', 1.0)

for state, factor in RICE_FACTORS.items():
    zone = diff_df[diff_df['state'] == state]['zone'].values
    zone = zone[0] if len(zone) > 0 else "Unknown"
    zd   = zone_defaults.get(state, 1.0)
    new_rows.append({
        'zone':             zone,
        'state':            state,
        'commodity':        'Rice',
        'price_factor':     factor,
        'is_primary_product': state in RICE_PRIMARY,
        'zone_default':     zd,
    })

# Remove any existing Rice rows first
diff_df = diff_df[diff_df['commodity'] != 'Rice']
diff_df = pd.concat([diff_df, pd.DataFrame(new_rows)], ignore_index=True)
diff_df.to_csv(diff_path, index=False)
print(f"Added Rice to state_price_differentials.csv ({len(new_rows)} state rows)")

# ── STEP 4: Update verified_prices_2026.json ─────────────────────────────────
vp_path = os.path.join(BASE, "data", "external", "verified_prices_2026.json")
with open(vp_path) as f:
    vp = json.load(f)

if "Rice" not in vp.get("commodities", {}):
    vp["commodities"]["Rice"] = {
        "price_ngn_mt": 1_550_000,
        "range":         "1400000-1750000",
        "source":        "Market research May 2026 (50kg bag N70K-N87K)",
    }
    with open(vp_path, "w") as f:
        json.dump(vp, f, indent=2)
    print("Added Rice to verified_prices_2026.json at N1,550,000/MT")
else:
    print("Rice already in verified_prices_2026.json")

# ── STEP 5: Update zones_config.json ─────────────────────────────────────────
zc_path = os.path.join(BASE, "data", "external", "zones_config.json")
with open(zc_path) as f:
    zc = json.load(f)

for zone_name, zone_data in zc.items():
    sp = zone_data.get("state_primary", {})
    for state in zone_data.get("states", []):
        if state in RICE_PRIMARY and "Rice" not in sp.get(state, []):
            sp.setdefault(state, []).append("Rice")
    zone_data["state_primary"] = sp

with open(zc_path, "w") as f:
    json.dump(zc, f, indent=2)
print("Updated zones_config.json with Rice as primary for Plateau, Kaduna")

# ── STEP 6: Update 06_validate.py MANUAL_PRICES ──────────────────────────────
validate_path = os.path.join(BASE, "pipeline", "06_validate.py")
with open(validate_path, encoding="utf-8", errors="replace") as f:
    vc = f.read()

if '"Rice"' not in vc:
    # Add after Wheat entry
    vc = re.sub(
        r'([\"\']Wheat[\"\']:\s*\d[\d_,]+,.*?\n)',
        lambda m: m.group(0) + '    "Rice":          1_550_000,   '
                                '# Market research May 2026 (50kg bag N75K-N87K)\n',
        vc
    )
    with open(validate_path, "w", encoding="utf-8") as f:
        f.write(vc)
    print("Added Rice to MANUAL_PRICES in 06_validate.py")
else:
    print("Rice already in 06_validate.py MANUAL_PRICES")

print("\n" + "="*60)
print("SETUP COMPLETE")
print("="*60)
print("""
Next steps:
  1. Run the pipeline (02 through 07):
     python pipeline\\02_clean.py
     python pipeline\\03_features.py
     python pipeline\\04_train.py
     python pipeline\\05_forecast.py
     python pipeline\\06_validate.py
     python pipeline\\07_zonal_forecast.py

  2. Verify Rice appears in dashboard Zonal Prices and Forecasts pages

  3. Update MANUAL_PRICES for Rice in 06_validate.py whenever you get
     a fresh market price (50kg bag price / 50 * 1000 = per MT)
     e.g. 50kg bag at N75,000 = N1,500,000/MT
""")
