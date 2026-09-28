"""
Patches pipeline/01_ingest.py to include Rice from rice_historical.csv.
Run once from project root: python patch_ingest_for_rice.py
"""
import os, sys, re

BASE          = os.path.dirname(os.path.abspath(__file__))
ingest_path   = os.path.join(BASE, "pipeline", "01_ingest.py")

with open(ingest_path, encoding="utf-8", errors="replace") as f:
    content = f.read()

if "rice_historical" in content.lower():
    print("Rice already in 01_ingest.py - nothing to do")
    sys.exit(0)

# Find where WFP data is ingested and add rice loader after it
# The rice ingest block to inject
rice_block = '''

# ── RICE: WFP + Bridge Data ────────────────────────────────────────────────────
def ingest_rice(base_dir):
    """Load rice price series from rice_historical.csv (WFP + synthetic bridge)."""
    path = os.path.join(base_dir, "data", "raw", "rice_historical.csv")
    if not os.path.exists(path):
        logger.warning("rice_historical.csv not found in data/raw/. Skipping Rice.")
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        df = df.rename(columns={"price_ngn_mt": "price_ngn_mt"})
        df["commodity"]   = "Rice"
        df["source"]      = df.get("source", "WFP+bridge")
        df["quality_score"] = 0.75  # lower than Agricome (0.9) since partly synthetic
        df = df[["date", "commodity", "price_ngn_mt", "source", "quality_score"]]
        df = df.dropna(subset=["date", "price_ngn_mt"])
        df = df[df["price_ngn_mt"] > 0]
        df = df.sort_values("date").drop_duplicates("date")
        logger.info(f"  Rice: {len(df)} rows | {df['date'].min().date()} to {df['date'].max().date()}")
        logger.info(f"  Rice: latest price N{df['price_ngn_mt'].iloc[-1]:,.0f}/MT")
        return df
    except Exception as e:
        logger.warning(f"  Rice ingest failed: {e}")
        return pd.DataFrame()

'''

# Insert the rice function before the main run_ function
# Find a good insertion point
for marker in ["def run_ingestion", "def run_ingest", "if __name__"]:
    if marker in content:
        content = content.replace(marker, rice_block + marker, 1)
        print(f"Injected rice ingest function before '{marker}'")
        break
else:
    # Append at end if no marker found
    content += rice_block
    print("Appended rice ingest function at end of file")

# Now find where all commodity data is combined/appended and add rice
# Look for where WFP or other sources are combined into master
combine_patterns = [
    "all_data.append(",
    "frames.append(",
    "dfs.append(",
    "concat_frames.append(",
]

rice_call = "    all_data.append(ingest_rice(BASE))\n"
injected = False

for pat in combine_patterns:
    if pat in content:
        # Find last occurrence and add rice after it
        last_idx = content.rfind(pat)
        # Find end of that line
        end_of_line = content.find("\n", last_idx) + 1
        content = content[:end_of_line] + rice_call + content[end_of_line:]
        print(f"Added rice call after '{pat}'")
        injected = True
        break

if not injected:
    print("WARNING: Could not auto-inject rice call into combine section.")
    print("You may need to manually add: all_data.append(ingest_rice(BASE))")
    print("in the section where all commodity data frames are combined.")

import ast
try:
    ast.parse(content)
    print("Syntax OK")
except SyntaxError as e:
    print(f"Syntax error line {e.lineno}: {e}")
    sys.exit(1)

with open(ingest_path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Patched: {ingest_path}")
