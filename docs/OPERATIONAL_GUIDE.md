# Operational Guide

## Daily Operations

### Before You Run the Pipeline

1. **Check Agricome Africa** (@agricomeafrica on Instagram)
   - Look for the latest weekly post (usually Monday or Thursday)
   - Note the prices for: Hibiscus, Sesame, Ginger, Cocoa, Soybeans, Cashew Nuts, Wheat

2. **Update Reference Prices**
   ```python
   # File: pipeline/06_validate.py
   MANUAL_PRICES = {
       "Hibiscus":      2_325_000,    # Update this value
       "Sesame":        1_650_000,    # from Agricom post
       # ... etc
   }
   ```

3. **Run Quality Gate** (sanity check)
   ```bash
   python pipeline/quality_gate.py
   # Should output: QUALITY GATE PASSED - 17 commodities, outputs valid
   ```

### Running the Pipeline

**Daily run** (use existing trained models, ~2 minutes):
```bash
python pipeline/run_pipeline.py --skip-train
```

**Weekly full retrain** (Mondays, trains all 5 models per commodity, ~20 minutes):
```bash
python pipeline/run_pipeline.py
```

**Just one step**:
```bash
python pipeline/01_ingest.py
python pipeline/04_train.py  # Takes the most time
python pipeline/06_validate.py
```

### After Running: Quality Check

```bash
python pipeline/quality_gate.py
```

Must pass before you push. If it fails:
- Check the errors listed
- Do NOT commit or push until it passes
- Review the raw output files for NaN, missing data, etc.

### Pushing to GitHub

```bash
# Commit the updated forecasts and master dataset
git add data/processed/agrolinking_master.csv data/processed/features/ outputs/
git commit -m "Daily update $(date +%Y-%m-%d)"

# Push (will be rejected if you're behind origin/main — run 'git pull' first)
git push origin main
```

**Automatic:** GitHub Actions pushes daily at 05:00 UTC. Only push manually if Actions is down.

---

## Troubleshooting

### Problem: "API returns 500 errors"

1. Check if today's run completed: `ls -la outputs/logs/ | tail`
2. Look at the latest log: `tail -50 outputs/logs/forecast_*.log`
3. Run the quality gate: `python pipeline/quality_gate.py`
4. If quality gate fails → don't commit/push
5. If quality gate passes but errors still happen → check Render logs

### Problem: "Forecast prices are unrealistic"

1. Are MANUAL_PRICES stale? 
   ```bash
   grep "MANUAL_PRICES = {" pipeline/06_validate.py -A 20
   ```
2. Check the Agricome post date — is there a newer price?
3. Run validation report:
   ```bash
   tail -100 outputs/logs/validation_report_*.json | python -m json.tool
   ```

### Problem: "Zonal prices don't match national"

This is normal. State prices interpolate the national forecast using structural factors from `data/external/state_price_differentials.csv`. They won't be identical to national.

### Problem: "An old run's data is serving"

The API falls back to the last good file if the latest contains NaN. This is by design. Check that your quality gate passes on the latest data.

### Problem: "Git push rejected"

```bash
git status

# If you're behind:
git pull
python pipeline/run_pipeline.py --skip-train
git add ...
git commit -m "..."
git push
```

### Problem: "Merge conflict in outputs"

Don't resolve by hand. Let the pipeline regenerate:

```bash
git checkout --ours outputs/
git add outputs/
python pipeline/run_pipeline.py --skip-train
git add outputs/
git commit -m "Resolve conflict: regenerate outputs"
git push
```

---

## Dashboard Maintenance

### Streamlit

**Local:** `streamlit run dashboard/app.py` (opens `http://localhost:8501`)

**Deployed:** Auto-updates 30 seconds after you push to GitHub

**If dashboard is slow or stale:**
- Refresh the page (Streamlit auto-reloads on git push)
- Check GitHub Actions logs for pipeline errors

---

## API Maintenance

### Local: `python api.py`

Opens `http://localhost:8000` with interactive docs at `/docs`

### Deployed: Auto-updates on git push to Render (2 minutes)

### Testing Endpoints

```bash
# Health check
curl https://agrolinking-intelligence.onrender.com/health

# Today's commodities
curl https://agrolinking-intelligence.onrender.com/commodities | python -m json.tool | head -50

# Single commodity history
curl https://agrolinking-intelligence.onrender.com/history/Rice?days=30

# Save alerts (requires auth after hardening)
curl -X POST https://agrolinking-intelligence.onrender.com/alerts/saved \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d "commodity=Rice&threshold_price=1550000&direction=above"
```

---

## Monitoring

### Automated Checks (GitHub Actions)

Runs daily at 05:00 UTC:
1. Ingest data from all sources
2. Clean and validate
3. Engineer features
4. Train models (if Monday) or skip (if not)
5. Generate forecasts
6. Validate against live prices
7. Generate zonal intelligence
8. **Run quality gate** ← Stops here if checks fail
9. Commit and push outputs

Check status: https://github.com/Agrolinking-Solutions/Agrolinking-Intelligence/actions

### Manual Monitoring

```bash
# Check latest run date across outputs
ls -la outputs/forecasts/validated/
ls -la outputs/intelligence/

# Verify all 17 commodities are present
curl https://agrolinking-intelligence.onrender.com/summary | python -m json.tool | grep "commodities_tracked"

# Check accuracy
curl https://agrolinking-intelligence.onrender.com/confidence | python -m json.tool
```

---

## Disaster Recovery

### If Data Gets Corrupted

**Option 1: Let the next automated run fix it**
- GitHub Actions regenerates all outputs daily
- Corrupted local files are overwritten on the next `git pull`

**Option 2: Manual recovery**
```bash
# Discard local changes and pull clean data
git checkout -- data/ outputs/
git pull origin main

# Verify quality gate passes
python pipeline/quality_gate.py
```

### If Git History Gets Messed Up

```bash
# Check what happened
git log --oneline --decorate -20

# Revert to last good commit (example: c27d06b)
git reset --hard c27d06b
git push --force-with-lease origin main
```

### If Postgres Gets Out of Sync

```python
# Re-migrate from CSV (idempotent — won't duplicate)
python scripts/migration/migrate_to_postgres.py
```

---

## Deployment Checklist

Before deploying to production:

- [ ] All tests pass locally: `python pipeline/quality_gate.py` ✓
- [ ] All 17 commodities have valid forecasts
- [ ] No NaN in outputs/forecasts/validated/*.json
- [ ] Model accuracy is within target (<3% error)
- [ ] Zonal prices make sense (check 2-3 commodities by hand)
- [ ] Daily alerts are readable (no "NnanK" prices)
- [ ] API endpoints respond (curl a few key ones)
- [ ] Dashboard renders without errors

---

## Schedule Summary

| Frequency | Task | Who | Command |
|---|---|---|---|
| Daily, 05:00 UTC | Full pipeline run | GitHub Actions | Automatic |
| Monday | Check Agricome post | You | Manual inspection |
| Monday | Update MANUAL_PRICES | You | Edit pipeline/06_validate.py |
| Monday | Full retrain | You or Actions | `python pipeline/run_pipeline.py` |
| Wednesday/Thursday | Check Agricome post (if new) | You | Manual inspection |
| Wednesday/Thursday | Daily forecast (skip train) | You or Actions | `python pipeline/run_pipeline.py --skip-train` |
| After any run | Quality gate check | You | `python pipeline/quality_gate.py` |
| After any run | Git push | You | `git push origin main` |

---

## Key Files to Know

| File | Purpose | Edit? |
|---|---|---|
| `pipeline/06_validate.py` | Reference price anchors (MANUAL_PRICES) | **YES** (weekly) |
| `config/settings.py` | Commodity list, paths, model params | Usually no |
| `data/external/state_price_differentials.csv` | State price factors | Rarely |
| `.github/workflows/daily_pipeline.yml` | GitHub Actions scheduler | Only if you need to change run time |
| `pipeline/quality_gate.py` | Data validation rules | Only if you need to loosen checks (not recommended) |
| `push_daily.ps1` | Local push helper (Windows) | Usually no |

---

## Contact & Support

**Team Email:** info@agrolinking.com  
**GitHub Issues:** [Agrolinking-Solutions/Agrolinking-Intelligence](https://github.com/Agrolinking-Solutions/Agrolinking-Intelligence/issues)  
**Production Status:** https://agrolinking-intelligence.onrender.com/health
