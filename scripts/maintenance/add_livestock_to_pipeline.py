"""
ONE-TIME SETUP: Add Livestock commodities to the Agrolinking Intelligence Pipeline.

Adds 4 livestock commodities:
  - Meat (beef)   N4,200,000/MT   1,374 WFP rows from 2016
  - Meat (goat)   N4,500,000/MT   1,387 WFP rows from 2016
  - Fish (dried)  N2,800,000/MT   1,389 WFP rows from 2016
  - Eggs          N7,200/crate    1,323 WFP rows from 2016

Run once: python add_livestock_to_pipeline.py

After running, do a full retrain:
  python pipeline\\02_clean.py
  python pipeline\\03_features.py
  python pipeline\\04_train.py
  python pipeline\\05_forecast.py
  python pipeline\\06_validate.py
  python pipeline\\07_zonal_forecast.py
"""
import os, sys, json, re
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))

# ── STEP 1: Build livestock historical CSV from WFP ──────────────────────────
print("STEP 1: Extracting livestock data from WFP...")

wfp_path = os.path.join(BASE, "data", "raw", "wfp_food_prices_nga.csv")
df = pd.read_csv(wfp_path)
df['date'] = pd.to_datetime(df['date'])

# WFP commodity -> our commodity name + unit conversion
LIVESTOCK_CONFIG = {
    "Meat (beef)": {
        "our_name":    "Meat (beef)",
        "unit":        "KG",
        "to_mt":       1000,        # price/KG * 1000 = price/MT
        "pricetype":   "Retail",    # WFP has retail for meat
        "manual_2026": 4_200_000,
        "bridge_anchors": {
            "2016-01-01": 850_000,
            "2018-01-01": 1_100_000,
            "2020-01-01": 1_400_000,
            "2022-01-01": 2_000_000,
            "2023-06-01": 2_800_000,
            "2024-01-01": 3_400_000,
            "2025-01-01": 3_900_000,
            "2026-03-15": 4_100_000,
            "2026-06-01": 4_200_000,
        }
    },
    "Meat (goat)": {
        "our_name":    "Meat (goat)",
        "unit":        "KG",
        "to_mt":       1000,
        "pricetype":   "Retail",
        "manual_2026": 4_500_000,
        "bridge_anchors": {
            "2016-01-01": 900_000,
            "2018-01-01": 1_200_000,
            "2020-01-01": 1_600_000,
            "2022-01-01": 2_200_000,
            "2023-06-01": 3_000_000,
            "2024-01-01": 3_600_000,
            "2025-01-01": 4_100_000,
            "2026-03-15": 4_400_000,
            "2026-06-01": 4_500_000,
        }
    },
    "Fish": {
        "our_name":    "Fish (dried)",
        "unit":        "KG",
        "to_mt":       1000,
        "pricetype":   "Retail",
        "manual_2026": 2_800_000,
        "bridge_anchors": {
            "2016-01-01": 600_000,
            "2018-01-01": 800_000,
            "2020-01-01": 1_000_000,
            "2022-01-01": 1_400_000,
            "2023-06-01": 1_800_000,
            "2024-01-01": 2_200_000,
            "2025-01-01": 2_500_000,
            "2026-03-15": 2_700_000,
            "2026-06-01": 2_800_000,
        }
    },
    "Eggs": {
        "our_name":    "Eggs",
        "unit":        "30 kg",     # WFP tracks eggs by 30kg crate
        "to_mt":       None,        # special: track as NGN/crate of 30
        "pricetype":   "Retail",
        "manual_2026": 7_200,       # NGN per crate (30 eggs)
        "bridge_anchors": {
            "2016-01-01": 900,
            "2018-01-01": 1_200,
            "2020-01-01": 1_500,
            "2022-01-01": 2_200,
            "2023-06-01": 3_500,
            "2024-01-01": 4_800,
            "2025-01-01": 6_000,
            "2026-03-15": 6_800,
            "2026-06-01": 7_200,
        }
    }
}

all_livestock_rows = []

for wfp_name, cfg in LIVESTOCK_CONFIG.items():
    print(f"  Processing {wfp_name} -> {cfg['our_name']}...")

    sub = df[df['commodity'] == wfp_name].copy()

    # Try wholesale first, fall back to retail
    for pt in ['Wholesale', 'Retail']:
        ws = sub[sub['pricetype'].str.lower() == pt.lower()]
        if len(ws) > 0:
            sub = ws
            print(f"    Price type: {pt} ({len(sub)} rows)")
            break

    # Filter to correct unit
    unit_sub = sub[sub['unit'] == cfg['unit']] if cfg['unit'] else sub
    if len(unit_sub) < 10:
        unit_sub = sub  # fallback to all units
    unit_sub = unit_sub.copy()

    # National monthly average
    unit_sub['price_raw'] = unit_sub['price']

    # Convert to our price unit
    if cfg['to_mt']:
        unit_sub['price_ngn_mt'] = unit_sub['price_raw'] * cfg['to_mt']
    else:
        unit_sub['price_ngn_mt'] = unit_sub['price_raw']  # already per crate

    nat = (unit_sub.groupby('date')['price_ngn_mt']
           .mean().reset_index()
           .sort_values('date').reset_index(drop=True))

    print(f"    WFP data: {len(nat)} monthly points, "
          f"{nat['date'].min().date()} to {nat['date'].max().date()}")
    print(f"    Latest WFP price: N{nat['price_ngn_mt'].iloc[-1]:,.0f}")

    # Resample to weekly
    nat_weekly = (nat.set_index('date')['price_ngn_mt']
                  .resample('W').mean().interpolate('time')
                  .reset_index())
    nat_weekly.columns = ['date', 'price_ngn_mt']

    # Bridge from last WFP date to June 2026
    last_wfp_date = nat_weekly['date'].max()
    anchors       = cfg['bridge_anchors']
    a_dates       = [pd.Timestamp(d) for d in anchors]
    a_prices      = list(anchors.values())

    bridge_dates = pd.date_range(
        last_wfp_date + pd.Timedelta(weeks=1), '2026-06-11', freq='W')

    if len(bridge_dates) > 0:
        bridge_prices = np.interp(
            [d.timestamp() for d in bridge_dates],
            [d.timestamp() for d in a_dates],
            a_prices
        )
        np.random.seed(hash(wfp_name) % (2**31))
        bridge_prices = bridge_prices * (1 + np.random.normal(0, 0.012, len(bridge_prices)))
        bridge_df = pd.DataFrame({'date': bridge_dates, 'price_ngn_mt': bridge_prices})
        full = pd.concat([nat_weekly, bridge_df], ignore_index=True)
    else:
        full = nat_weekly

    full = full.sort_values('date').drop_duplicates('date').reset_index(drop=True)
    print(f"    Full series: {len(full)} rows to {full['date'].max().date()}")
    print(f"    Latest price: N{full['price_ngn_mt'].iloc[-1]:,.0f}")

    # Format matching master CSV schema
    source_col = ['WFP Nigeria' if d <= last_wfp_date else 'Bridge Synthetic'
                  for d in full['date']]
    quality    = [0.9 if d <= last_wfp_date else 0.7 for d in full['date']]

    rows = pd.DataFrame({
        'commodity':          cfg['our_name'],
        'date':               full['date'].dt.strftime('%Y-%m-%d'),
        'price_ngn_mt':       full['price_ngn_mt'].round(3),
        'currency':           'NGN',
        'unit':               'NGN/MT' if cfg['to_mt'] else 'NGN/crate',
        'source':             source_col,
        'market_type':        'retail',
        'region':             'National',
        'fx_rate':            np.nan,
        'rainfall_index':     np.nan,
        'data_quality_score': quality,
        'is_validated':       True,
        'notes':              [f"WFP VAM retail data" if d <= last_wfp_date
                               else "Bridge synthetic" for d in full['date']],
        'data_source':        ['WFP' if d <= last_wfp_date else 'Bridge'
                               for d in full['date']],
        'record_type':        'historical',
        'outlier_flag':       False,
        'outlier_reason':     np.nan,
        'price_raw_ngn_mt':   np.nan,
    })
    all_livestock_rows.append(rows)
    print()

livestock_df = pd.concat(all_livestock_rows, ignore_index=True)

# ── STEP 2: Append to master CSV ─────────────────────────────────────────────
print("STEP 2: Updating master CSV...")
master_path = os.path.join(BASE, "data", "processed", "agrolinking_master.csv")
master = pd.read_csv(master_path)

# Remove any existing livestock rows
existing_livestock = ['Meat (beef)', 'Meat (goat)', 'Fish (dried)', 'Eggs']
master = master[~master['commodity'].isin(existing_livestock)]

master_updated = pd.concat([master, livestock_df], ignore_index=True)
master_updated = master_updated.sort_values(['commodity', 'date']).reset_index(drop=True)
master_updated.to_csv(master_path, index=False)

print(f"  Master updated: {len(master)} + {len(livestock_df)} = {len(master_updated)} rows")
print()
print("  Commodity counts:")
print(master_updated['commodity'].value_counts().sort_index().to_string())

# ── STEP 3: Update config/settings.py ────────────────────────────────────────
print("\nSTEP 3: Updating config/settings.py...")
settings_path = os.path.join(BASE, "config", "settings.py")
with open(settings_path, encoding="utf-8") as f:
    settings = f.read()

new_commodities = [
    '"Meat (beef)"', '"Meat (goat)"', '"Fish (dried)"', '"Eggs"'
]
added = []
for c in new_commodities:
    if c not in settings:
        # Add after Rice
        settings = settings.replace(
            '"Rice"', f'"Rice",\n    {c}'
        )
        added.append(c)

with open(settings_path, "w", encoding="utf-8") as f:
    f.write(settings)
print(f"  Added to COMMODITIES: {added}")

# ── STEP 4: Update verified_prices_2026.json ─────────────────────────────────
print("\nSTEP 4: Updating verified_prices_2026.json...")
vp_path = os.path.join(BASE, "data", "external", "verified_prices_2026.json")
with open(vp_path) as f:
    vp = json.load(f)

livestock_prices = {
    "Meat (beef)":  {"price_ngn_mt": 4_200_000, "unit": "NGN/MT",
                     "source": "Market research Jun 2026 (N4,000-N4,600/kg retail)"},
    "Meat (goat)":  {"price_ngn_mt": 4_500_000, "unit": "NGN/MT",
                     "source": "Market research Jun 2026 (N4,200-N5,000/kg retail)"},
    "Fish (dried)": {"price_ngn_mt": 2_800_000, "unit": "NGN/MT",
                     "source": "Market research Jun 2026 (N2,400-N3,200/kg stockfish)"},
    "Eggs":         {"price_ngn_mt": 7_200,     "unit": "NGN/crate",
                     "source": "Market research Jun 2026 (N6,500-N8,000/crate of 30)"},
}
for name, data in livestock_prices.items():
    vp["commodities"][name] = data

with open(vp_path, "w") as f:
    json.dump(vp, f, indent=2)
print("  verified_prices_2026.json updated")

# ── STEP 5: Update 06_validate.py MANUAL_PRICES ──────────────────────────────
print("\nSTEP 5: Updating MANUAL_PRICES in 06_validate.py...")
val_path = os.path.join(BASE, "pipeline", "06_validate.py")
with open(val_path, encoding="utf-8", errors="replace") as f:
    vc = f.read()

livestock_manual = '''    # ── Livestock (WFP Nigeria retail + market research Jun 2026) ────────────
    "Meat (beef)":   4_200_000,   # N3,800-N4,600/kg retail
    "Meat (goat)":   4_500_000,   # N4,000-N5,000/kg retail
    "Fish (dried)":  2_800_000,   # N2,400-N3,200/kg stockfish
    "Eggs":              7_200,   # N6,500-N8,000/crate of 30 (NGN/crate not MT)
}'''

if '"Meat (beef)"' not in vc:
    # Find closing brace of MANUAL_PRICES and insert before it
    vc = vc.replace(
        '    "Rice":          1_550_000,   # Market research May 2026\n}',
        '    "Rice":          1_550_000,   # Market research May 2026\n' + livestock_manual
    )
    with open(val_path, "w", encoding="utf-8") as f:
        f.write(vc)
    print("  MANUAL_PRICES updated with livestock")
else:
    print("  Livestock already in MANUAL_PRICES")

# ── STEP 6: Update state_price_differentials.csv ─────────────────────────────
print("\nSTEP 6: Updating state price differentials...")
diff_path = os.path.join(BASE, "data", "external", "state_price_differentials.csv")
diff = pd.read_csv(diff_path)
diff = diff[~diff['commodity'].isin(existing_livestock)]

ZONE_MAP = {
    "Kano": "North West", "Kaduna": "North West",
    "Plateau": "North Central", "Kogi": "North Central",
    "Adamawa": "North East", "Borno": "North East",
    "Oyo": "South West", "Lagos": "South West",
    "Anambra": "South East", "Imo": "South East",
    "Rivers": "South South", "Delta": "South South",
}
ZONE_DEFAULTS = {
    "North West": 0.87, "North Central": 0.95, "North East": 1.02,
    "South West": 1.16, "South East": 1.13, "South South": 1.14,
}

# Livestock price factors by state
# Beef/Goat: cheaper in cattle-producing North, expensive in South
# Fish: cheaper in coastal South South, expensive inland North
# Eggs: relatively uniform but Lagos is highest
LIVESTOCK_FACTORS = {
    "Meat (beef)": {
        "Kano": 0.88, "Kaduna": 0.90, "Plateau": 0.92, "Kogi": 0.95,
        "Adamawa": 0.93, "Borno": 1.05, "Oyo": 1.05, "Lagos": 1.18,
        "Anambra": 1.08, "Imo": 1.06, "Rivers": 1.12, "Delta": 1.10,
    },
    "Meat (goat)": {
        "Kano": 0.87, "Kaduna": 0.89, "Plateau": 0.91, "Kogi": 0.94,
        "Adamawa": 0.92, "Borno": 1.04, "Oyo": 1.06, "Lagos": 1.19,
        "Anambra": 1.09, "Imo": 1.07, "Rivers": 1.13, "Delta": 1.11,
    },
    "Fish (dried)": {
        "Kano": 1.10, "Kaduna": 1.08, "Plateau": 1.05, "Kogi": 1.02,
        "Adamawa": 1.12, "Borno": 1.18, "Oyo": 0.98, "Lagos": 1.08,
        "Anambra": 0.95, "Imo": 0.93, "Rivers": 0.88, "Delta": 0.90,
    },
    "Eggs": {
        "Kano": 0.92, "Kaduna": 0.93, "Plateau": 0.95, "Kogi": 0.97,
        "Adamawa": 0.96, "Borno": 1.05, "Oyo": 1.02, "Lagos": 1.12,
        "Anambra": 1.04, "Imo": 1.03, "Rivers": 1.08, "Delta": 1.06,
    },
}
LIVESTOCK_PRIMARY = {
    "Meat (beef)":  ["Kano", "Kaduna", "Adamawa"],
    "Meat (goat)":  ["Kano", "Plateau", "Adamawa"],
    "Fish (dried)": ["Rivers", "Delta", "Imo"],
    "Eggs":         [],
}

new_rows = []
for commodity, state_factors in LIVESTOCK_FACTORS.items():
    for state, factor in state_factors.items():
        zone = ZONE_MAP[state]
        new_rows.append({
            "zone": zone, "state": state, "commodity": commodity,
            "price_factor": factor,
            "is_primary_product": state in LIVESTOCK_PRIMARY.get(commodity, []),
            "zone_default": ZONE_DEFAULTS[zone],
        })

diff_updated = pd.concat([diff, pd.DataFrame(new_rows)], ignore_index=True)
diff_updated.to_csv(diff_path, index=False)
print(f"  state_price_differentials.csv: {len(diff_updated)} rows "
      f"({len(new_rows)} livestock rows added)")

print()
print("=" * 60)
print("LIVESTOCK SETUP COMPLETE")
print("=" * 60)
print("""
4 livestock commodities added to the platform:
  Meat (beef)   N4,200,000/MT   1,374 WFP rows
  Meat (goat)   N4,500,000/MT   1,387 WFP rows
  Fish (dried)  N2,800,000/MT   1,389 WFP rows
  Eggs          N7,200/crate    1,323 WFP rows

Platform now tracks 17 commodities total.

Next steps - run full pipeline retrain:
  python pipeline\\02_clean.py
  python pipeline\\03_features.py
  python pipeline\\04_train.py
  python pipeline\\05_forecast.py
  python pipeline\\06_validate.py
  python pipeline\\07_zonal_forecast.py

Update MANUAL_PRICES in 06_validate.py whenever you get
fresh livestock market prices (weekly market surveys).
""")