import json, glob, os

files = sorted(glob.glob('outputs/forecasts/validated/forecast_validated_*.json'))
if not files:
    print('No validated files found')
else:
    with open(files[-1]) as f:
        data = json.load(f)

    within = sum(1 for c in data.values() if c.get('validation',{}).get('within_target', False))
    errors = [c.get('validation',{}).get('error_after_pct', 0) for c in data.values() if c.get('validation',{}).get('error_after_pct') is not None]
    avg = sum(errors)/len(errors) if errors else 0

    print(f"File:              {os.path.basename(files[-1])}")
    print(f"Commodities:       {len(data)}")
    print(f"Within 3pct:       {within}/{len(data)}")
    print(f"Avg error:         {avg:.2f}%")
    print()
    print(f"  {'Status':<6} {'Commodity':<24} {'Error':>8}  {'Ref Price':>14}")
    print("  " + "-"*60)
    for name, d in data.items():
        vld = d.get('validation', {})
        err = vld.get('error_after_pct', 0) or 0
        wt  = vld.get('within_target', False)
        ref = vld.get('reference_price', 0) or 0
        status = "OK  " if wt else "FAIL"
        print(f"  {status}  {name:<24} {err:>7.2f}%  N{ref:>12,.0f}")
