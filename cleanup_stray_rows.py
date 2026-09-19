import pandas as pd

path = "data/processed/agrolinking_master.csv"
df = pd.read_csv(path, parse_dates=["date"])

mask = (df["record_type"] == "validated_actual") & (df["date"] == "2026-09-19")
print(f"Removing {mask.sum()} stray unsnapped rows dated 2026-09-19:")
print(df.loc[mask, ["commodity", "date", "price_ngn_mt", "record_type"]].to_string(index=False))

df = df[~mask]
df.to_csv(path, index=False)
print(f"\nDone. Master now has {len(df):,} rows.")