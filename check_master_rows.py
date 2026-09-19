import pandas as pd

df = pd.read_csv("data/processed/agrolinking_master.csv", parse_dates=["date"])

for name in ["Fish (dried)", "Meat (goat)", "Sorghum", "Wheat"]:
    sub = df[(df["commodity"] == name) & (df["date"] >= "2026-08-01")].sort_values("date")
    print(f"--- {name} ---")
    print(sub[["date", "price_ngn_mt", "data_source", "record_type"]].to_string(index=False))
    print()