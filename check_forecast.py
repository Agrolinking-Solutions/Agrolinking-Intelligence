import json

with open("outputs/forecasts/forecast_2026-09-19.json") as f:
    data = json.load(f)

forecasts = data.get("forecasts", data)

for name in ["Fish (dried)", "Meat (goat)", "Sorghum", "Wheat"]:
    fc = forecasts.get(name)
    if fc is None:
        print(f"--- {name}: not found in JSON ---")
        continue
    print(f"--- {name} ---")
    print("last_known_price:", fc.get("last_known_price"))
    print("last_known_date :", fc.get("last_known_date"))
    daily = fc["horizons"]["daily"]
    print("ensemble first value:", daily["ensemble"]["values"][0])
    for m, mf in daily.get("per_model", {}).items():
        vals = mf.get("values", [])
        print(f"  {m} first value:", vals[0] if vals else None)
    print()
