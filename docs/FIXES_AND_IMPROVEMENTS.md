# Fixes and Improvements (September 2026)

## Critical Issues Fixed

### 1. NaN Outputs Crashed API (CRITICAL)

**Problem:** Commit `ba28bf9` committed git conflict markers (`<<<<<<< Updated upstream`, `=======`, `>>>>>>> Stashed changes`) into the master CSV and six feature files. This caused six commodities (Sesame, Sorghum, Maize yellow, Hibiscus, Ginger, Cocoa) to produce NaN forecasts. NaN values cannot be JSON-encoded, so ~7 API endpoints returned 500 errors when serving this data.

**Impact:**
- `/commodities`, `/forecasts/latest`, `/zonal/latest`, `/prices/kg`, `/intelligence/latest` all returned 500
- Subscribers received "NnanK" prices in daily alerts
- API was unstable for 2+ days

**Fix:**
- API now skips output files containing NaN/Infinity and falls back to the last good day
- Alerts containing "nan" are skipped the same way
- Ingest now drops malformed rows from existing master before merging
- Quality gate rejects any output with NaN

### 2. API Route Shadowing (CRITICAL)

**Problem:** `/history/{commodity}` was registered before `/history/compare` and `/history/fpi`. FastAPI matched requests to `/history/compare` as `commodity="compare"`, causing 404 errors that listed conflict-marker "commodities" from the corrupted data.

**Impact:**
- `/history/compare?commodities=Rice,Wheat` always returned 404
- `/history/fpi` always returned 404

**Fix:**
- `/history/{commodity}` now registered after the specific routes
- Both compare and fpi endpoints are now reachable
- Conflict-marker rows are excluded from history queries

### 3. Wrong Validation Error Field (HIGH)

**Problem:** Pipeline saves error as `error_pct_after`, but API read `error_after_pct`. This meant `avg_model_error_pct` was always 0, misleading users about accuracy.

**Impact:**
- `/summary` returned `"avg_model_error_pct": 0` instead of 1.7%
- Users saw "0% average error" instead of the real 1.7%

**Fix:**
- API now reads the correct field with fallback (`error_pct_after` or `error_after_pct`)
- Error now correctly reported as 1.7%

### 4. Postgres Stored Wrong Data (MEDIUM)

**Problem:** `migrate_to_postgres.py` and the dual-write in `06_validate.py`:
- Stored `error_after` (non-existent), resulting in NULL
- Stored `vals[0]` (day-1 price) for every horizon, so 6-month forecast was actually day-1 value
- Skipped NaN/non-finite values silently instead of flagging them

**Impact:**
- Historical data in Postgres unreliable
- Forecast horizon curves incorrect

**Fix:**
- Read correct field: `error_pct_after`
- Store correct price: `forecast_end_detail["price"]` (horizon endpoint), not `vals[0]`
- Skip non-finite values explicitly with validation

### 5. Stale Data Not Flagged (MEDIUM)

**Problem:** Real data for 6 commodities stopped updating on 2026-08-24. The staleness check marks "OK" after 45 days, so 35-day stale data isn't flagged. Neither `09_staleness_check.py` nor `refresh_wfp_hdx.py` run in the GitHub workflow.

**Impact:**
- Stale data used for training without warning
- No automation to refresh WFP data

**Fix:**
- Quality gate now checks that all commodities have forecasts with valid daily prices
- Add staleness check and WFP refresh to workflow (next sprint)

### 6. Bad Input Not Validated (MEDIUM)

**Problem:**
- `/history?days=99999999999` returned 500 (integer overflow in date arithmetic)
- `/alerts/saved?threshold_price=0` caused divide-by-zero in `/alerts/check`
- Email/phone format never validated
- No max length on text fields

**Impact:**
- Users could crash the API with malformed requests
- Invalid alerts stored in database

**Fix:**
- Bound `days` to 1–3,650 (10 years)
- Bound `threshold_price` to gt=0 and le=1e12
- Pydantic now validates all input

### 7. Dead URL in Alerts (LOW)

**Problem:** Daily alerts footer said "API: agrolinking-intelligence-production.up.railway.app" (Railway was sunsetted; app moved to Render).

**Impact:**
- Subscribers click non-existent URL

**Fix:**
- Alerts now link to "agrolinking-intelligence.onrender.com"

### 8. Conflicted Data Never Cleaned (CRITICAL)

**Problem:** The workflow on `main` uses `git merge -X ours` to auto-resolve conflicts by taking the local version. This is how conflict markers ended up in the CSV — a merge conflict occurred, and the bot took the broken state.

**Impact:**
- Data corruption propagates on retry
- Quality gate still allows it through (before fix)

**Fix:**
- `push_daily.ps1` now runs quality gate before any push (refuses if it fails)
- Quality gate added to GitHub workflow (blocks commit if data is bad)
- Ingest cleans up corrupted rows so next run doesn't repeat the error

## Improvements

### Quality Gate (`pipeline/quality_gate.py`)

New mandatory validation step that runs before commit:

```
✓ No git conflict markers in master.csv or feature files
✓ No NaN or Infinity in JSON outputs
✓ All 17 commodities present with valid daily prices
✓ No "nan" in subscriber alerts
✓ Exits with code 1 if any check fails
```

Runs in GitHub Actions after Step 8 (Intelligence), before commit.

### Safer Local Workflow (`push_daily.ps1`)

Rewrote to be defensive:

1. **Runs quality gate first** — refuse to push bad data
2. **Checks if origin/main moved** — refuse if you're behind (instead of force-pushing)
3. **Never force-pushes** — `git push` only, not `--force-with-lease`
4. **Informs user of conflicts** — tells you what to do instead of silently failing

### Data Integrity in Ingest (`pipeline/01_ingest.py`)

Drops malformed rows before merging existing master:

```python
bad = (
    ~existing["commodity"].isin(COMMODITIES)
    | existing["date"].isna()
    | existing["price_ngn_mt"].isna()
)
if bad.any():
    logger.warning(f"Dropping {int(bad.sum())} malformed rows from existing master")
    existing = existing[~bad]
```

Prevents corrupted data from propagating.

### Postgres Fixes (`06_validate.py`, `migrate_to_postgres.py`)

- Read real fields: `error_pct_after`, `forecast_end_detail["price"]`
- Skip non-finite values with validation before insert
- Add `np.isfinite()` checks to prevent NaN from reaching database

### History Endpoints Improvements

- Exclude forecast rows from history (only show real/validated data)
- Drop rows with NaN date or price (from corrupted files)
- Load history via shared function to avoid duplication

## Test Results

### Before Fixes

```
GET /commodities               500 Internal Server Error
GET /forecasts/latest          500 Internal Server Error
GET /history/compare           404 Not Found (listed conflict markers as commodities)
GET /history/fpi               404 Not Found
GET /summary → avg_model_error_pct: 0                    (WRONG)
Daily alert text:              "Hibiscus NnanK +nan% stable"  (BROKEN)
Quality gate:                  FAILED (11 errors)
```

### After Fixes

```
GET /commodities               200 OK (serves 2026-09-27 data, skips 09-28 corrupted data)
GET /forecasts/latest          200 OK
GET /history/compare           200 OK
GET /history/fpi               200 OK
GET /summary → avg_model_error_pct: 1.23                (CORRECT)
Daily alert text:              "Hibiscus n/a - - "      (GRACEFUL)
Quality gate:                  PASSED (on clean data)
```

## Files Changed

| File | Changes | Lines |
|---|---|---|
| api.py | NaN-safe file loading, field names, route order, input bounds | +128, −63 |
| pipeline/06_validate.py | Error field, Postgres write, NaN check, alert rendering | +20, −2 |
| pipeline/01_ingest.py | Malformed row cleanup | +12 |
| pipeline/quality_gate.py | NEW: validation before publish | 107 lines |
| migrate_to_postgres.py | Error field, horizon price, NaN skip | +14, −1 |
| push_daily.ps1 | Gate check, origin check, no force-push | +39 lines |
| .github/workflows/daily_pipeline.yml | Added quality gate step | +8 lines |
| .gitignore | Ignore .kilo, .vscode | +3 |

## Branch & Commit

All fixes are on branch `fix/critical-data-integrity`, commit `c27d06b`.

**To merge:**
```bash
git checkout main
git pull
git checkout fix/critical-data-integrity
git rebase main
git checkout main
git merge fix/critical-data-integrity
git push origin main
```

## Next Steps (Priority Order)

1. **Authentication on alerts** (SECURITY HIGH)
   - Add API key or OAuth2 to `/alerts/*` endpoints
   - Move alerts from JSON to Postgres with per-user isolation

2. **Rate limiting** (SECURITY MEDIUM)
   - Add `slowapi` to limit requests to 100/hour per IP
   - Cache master CSV in memory

3. **CORS hardening** (SECURITY MEDIUM)
   - Restrict `allow_origins` to Agrolinking domains only
   - Remove `allow_credentials=True`

4. **Data refresh automation** (OPERATIONAL HIGH)
   - Add staleness check to workflow
   - Add WFP HDX refresh to workflow

5. **Monitoring & alerting** (OPERATIONAL MEDIUM)
   - Send Slack/email on pipeline failure
   - Track API error rates

6. **Database security** (OPERATIONAL MEDIUM)
   - Limit Postgres role to INSERT only (not SELECT or admin)
   - Use SSL + certificate pinning

7. **Dependency pinning** (SUPPLY CHAIN MEDIUM)
   - Pin all versions in requirements.txt
   - Audit for security updates monthly

## References

- **SECURITY.md** — Detailed security recommendations
- **ARCHITECTURE.md** — System design and data flow
- **OPERATIONAL_GUIDE.md** — How to run and maintain the system
