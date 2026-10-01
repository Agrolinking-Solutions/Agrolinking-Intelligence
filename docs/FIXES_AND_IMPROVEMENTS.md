# Fixes and Improvements (September 2026)

## Infrastructure Change Log

### October 2026 — Moved API hosting from Render to a VPS

**Reason:** Render's cost. The billing card was removed from the Render account at the end of September 2026, which means the Render deployment (`agrolinking-intelligence.onrender.com`) will stop serving once Render suspends the service for non-payment — do not rely on that URL in any new work.

**What changed:**
- API is now served from a VPS at `pis-api.agrolinking.com` (confirmed live, HTTPS working, responding with current data — `last_updated` matched the actual latest pipeline run date at time of verification)
- Web frontend now served from the same VPS at `pis.agrolinking.com`
- Deployment mechanism is unchanged — still auto-deploys on every push to `main`, same as Render before it (confirmed with the team, not just assumed)
- There was a brief transition period where the API was reachable at a temporary `sslip.io` address pointing directly at the VPS's IP (`169.58.87.142`) before the permanent `pis-api.agrolinking.com` domain was pointed at it — that sslip.io address is no longer referenced anywhere in the docs and should be treated as dead

**What this fixes incidentally:** the temporary sslip.io address was plain HTTP with no TLS — a real gap for any traffic depending on it. The permanent `pis-api.agrolinking.com` domain serves proper HTTPS, so that gap is closed as a side effect of the permanent domain landing.

**Verified working on the permanent domain:** `/health`, `/`, and both Postgres-backed endpoints (`/history/Rice`, `/history/compare`) all returned correct data from `https://pis-api.agrolinking.com` directly.

**What still needs checking:**
- CORS is still wide open (`allow_origins=["*"]`) regardless of which host serves the API — see docs/SECURITY.md, unrelated to this move but worth remembering now that a real production domain is in place
- `docs/API.md`, `README.md` updated to reference the new permanent domains throughout; `docs/SECURITY.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONAL_GUIDE.md` still have older Render-era references in places that are more historical/narrative — not actively misleading, but worth a cleanup pass later

### October 2026 — GitHub Actions workflow: missing TSDB_URL on 2 steps, and a near-miss on training frequency

**Found while answering a routine question** about whether the daily pipeline was fully automated.

**Real bug fixed:** `.github/workflows/daily_pipeline.yml` only passed the `TSDB_URL` secret to the "06 - Validate" step. Each GitHub Actions step's `env:` is scoped to that step alone — it doesn't carry to later steps. This meant the zonal (step 07) and intelligence (step 08) Postgres dual-writes, added earlier, have been silently logging "TSDB_URL not set" and skipping on every single automated run since they were built — only the one-time manual backfill actually got that data into Postgres, not the ongoing daily feed. Fixed by adding the same `env:` block to steps 07 and 08.

**Near-miss, caught before pushing — did NOT change this:** `run_pipeline.py` (used for local/manual runs) has a weekly-retrain intent — `today == 0` (Monday) skips training the rest of the week. The GitHub Actions workflow calls each pipeline script directly, never through that wrapper, so it has always trained every single day, not just Mondays. That looked like an obvious second bug to fix alongside the TSDB_URL one — a draft fix (Monday-only training via a day-of-week check) was written, then traced through before pushing: GitHub Actions runners are ephemeral (a fresh VM per run) and `models/` is gitignored, never committed, with no `actions/upload-artifact`/`download-artifact` or any other caching in the workflow to carry Monday's trained model files to the rest of the week. `forecast_commodity()` in `05_forecast.py` skips a commodity entirely when all 3 of its models return `None` (checked via `os.path.exists()` before `joblib.load()`). On an ephemeral runner with no `models/` directory at all, that's every commodity, every day except Monday — the "fix" would have produced zero forecasts 6 days out of 7. The draft was reverted before it was ever committed.

**Real fix still needed, not done yet:** add actual model persistence across runs (most likely `actions/upload-artifact` after the Monday training step, `actions/download-artifact` before forecasting on other days), then the Monday-only skip becomes safe to add. Training daily is costing GitHub Actions minutes unnecessarily until this is built — it's correct output, just wasteful compute, so there's no urgency to fix it blindly.

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
