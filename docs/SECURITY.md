# Security & Operations Guide

## Critical Issues Fixed (Sept 2026)

See [FIXES_AND_IMPROVEMENTS.md](FIXES_AND_IMPROVEMENTS.md) for the full list of recent corrections.

## Current Security Posture

### ✅ Strengths

- **No secrets in git history:** TSDB_URL passed as GitHub secret, never committed
- **Read-only external data:** WFP HDX API calls public datasets only
- **Model verification:** Forecasts validated against live market prices before publication
- **Quality gate:** Data with NaN, conflict markers, or missing commodities is rejected
- **Reproducible builds:** Pin requirements files to exact versions

### ⚠️ Areas Requiring Attention

## Authentication & Authorization

### Alert Endpoints (FIXED — October 2026)

~~The `/alerts/saved` endpoints expose personal data and allow write operations without authentication~~ — fixed. All four endpoints (`GET/POST /alerts/saved`, `DELETE /alerts/saved/{id}`, `GET /alerts/check`) now require a verified Google login token (`Authorization: Bearer <google_id_token>`), checked in `auth.py` against Google's own public keys — no shared secret needed, the frontend's existing Google Sign-In is the trust anchor. Alerts are scoped to the caller's own `user_id`; trying to read or delete another user's alert returns the same 404 as a nonexistent ID (never reveals that it exists but belongs to someone else).

Storage moved from the world-readable `outputs/alerts/saved_alerts.json` file to Postgres (`users`/`alerts` tables in `scripts/migration/schema.sql`). Unlike the other Postgres-backed endpoints, there's deliberately no JSON-file fallback here — a fallback would have meant falling back to the old unscoped, no-login file, silently undoing the fix. If Postgres is unreachable, these endpoints return 503 instead.

Verified: user-isolation tested directly (a second simulated user cannot see, list, or delete a first user's alert — confirmed via FastAPI dependency override in lieu of a real Google token, which requires a browser flow), forged-JWT rejection tested (a token with a fake signature and a spoofed `email` claim is correctly rejected — confirms real cryptographic verification is happening, not just structural checks), and the full trigger-on-threshold-crossed logic tested against real price data.

See `docs/FIXES_AND_IMPROVEMENTS.md` for the full record. Email/phone are no longer stored per-alert at all — they live once in `users`, not duplicated and exposed on every alert row.

## CORS Configuration — FIXED

Origins restricted to the real Agrolinking domains instead of `*`:

```python
CORS_ALLOWED_ORIGINS = [
    "https://pis.agrolinking.com",   # the live web frontend (VPS)
    "https://agrolinking.com",
    "https://www.agrolinking.com",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=False,   # alerts auth uses Authorization: Bearer, not cookies —
                               # credentialed cross-origin requests were never needed
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
```

Local dev origins (`localhost:3000`/`5173`) are only added when `ALLOW_LOCAL_DEV_CORS=1` is set in the environment — never set on the deployed VPS. If another real frontend origin appears, add it to `CORS_ALLOWED_ORIGINS` in `api.py` rather than reopening this to `*`.

## Rate Limiting

### Current (None)

Every API call re-reads CSV files from disk:

```python
@app.get("/history/{commodity}")
def commodity_history(commodity: str, days: int = 90):
    df = pd.read_csv(MASTER_PATH)  # 3MB read every call
    ...
```

A simple loop can exhaust free-tier Render memory:

```bash
while true; do curl https://api.../history/Rice; done
```

**Fix:** Add `slowapi` rate limiting

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.get("/history/{commodity}")
@limiter.limit("100/hour")  # 100 requests per hour per IP
def commodity_history(request: Request, commodity: str, days: int = 90):
    ...
```

## Input Validation

### Current Issues

- `/history?days=99999999999` accepted before fix (now bounded 1–3650)
- `/alerts/saved?threshold_price=0` causes divide-by-zero (now rejected)
- Email/phone format never validated
- No max length on commodity/alert label fields

**Fix:** Stricter Pydantic models

```python
from pydantic import BaseModel, Field, EmailStr, conint

class CreateAlertRequest(BaseModel):
    commodity: str = Field(..., min_length=1, max_length=50)
    threshold_price: float = Field(..., gt=0, le=1e12)
    direction: Literal["above", "below"]
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, regex=r"^\+?1?\d{9,15}$")
    label: Optional[str] = Field(None, max_length=100)

@app.post("/alerts/saved")
async def create_alert(request: CreateAlertRequest):
    # Request automatically validated
    ...
```

## Data Integrity

### Source Validation

The WFP HDX download is unvalidated:

```python
csv_resp = requests.get(download_url, timeout=120)
csv_resp.raise_for_status()
with open(out_path, "wb") as f:
    f.write(csv_resp.content)  # No validation
```

**Fix:** Validate before writing

```python
def validate_wfp_csv(path: str) -> bool:
    """Check host, content type, and required columns."""
    try:
        df = pd.read_csv(path, nrows=100)
        required = {"date", "commodity", "price", "pricetype"}
        if not required.issubset(df.columns):
            return False
        if not df["date"].dtype in ("datetime64[ns]", "object"):
            return False
        return True
    except:
        return False

csv_resp = requests.get(download_url, timeout=120)
if csv_resp.headers.get("content-type") != "text/csv":
    raise ValueError("Expected CSV, got " + csv_resp.headers["content-type"])
# Write to temp file
with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as f:
    f.write(csv_resp.content)
    temp_path = f.name
if not validate_wfp_csv(temp_path):
    os.remove(temp_path)
    raise ValueError("WFP CSV validation failed")
# Replace only after validation passes
os.replace(temp_path, out_path)
```

## Database Security

### Postgres Dual-Write

If TSDB_URL is configured, the pipeline writes to a TimescaleDB instance.

**Issues:**
1. Database user likely has admin privileges
2. Connection string contains plaintext password in environment variable
3. No column-level encryption for sensitive fields

**Fix:**

1. Create a limited-privilege role:

```sql
CREATE ROLE pipeline_writer WITH PASSWORD '...';
GRANT INSERT ON TABLE prices TO pipeline_writer;
GRANT INSERT ON TABLE forecasts TO pipeline_writer;
REVOKE ALL ON SCHEMA public FROM pipeline_writer;
```

2. Use `sslmode=verify-full` and pinned certificate:

```python
import psycopg2

conn = psycopg2.connect(
    os.environ["TSDB_URL"],
    sslmode="verify-full",
    sslcert="/path/to/cert.pem",
    connect_timeout=5
)
```

3. Store password in `.pgpass` (Unix) or use IAM (Cloud providers)

## Dependency Management

### Current Issues

Both `requirements.txt` and `requirements_api.txt` use flexible versions:

```
pandas>=2.0.0
prophet>=1.1.0
```

A future release could introduce a breaking change or security vulnerability silently.

**Fix:** Pin exact versions

```
# requirements.txt
pandas==2.1.4
prophet==1.1.5
pmdarima==2.0.4
scikit-learn==1.3.2
```

Update only when intentional:

```bash
pip list --outdated
pip install --upgrade pandas==2.2.0
pip freeze > requirements.txt
```

## Supply Chain Security

### GitHub Actions

Currently allows actions from any source. The workflow can execute arbitrary code.

**Fix:** Pin Actions to commit SHAs

```yaml
# Before (flexible):
- uses: actions/setup-python@v5

# After (pinned):
- uses: actions/setup-python@8877dd65e17d21883a2fb4d7103c01c2f9af88c5
```

### Secrets Management

**Current:** GitHub Secrets hold `TSDB_URL`

**Risk:** Exposed in logs or error messages

**Mitigation:**
- Never print secrets to logs
- Use `echo "::add-mask::$SECRET"` to hide from Actions logs
- Rotate secrets periodically (especially DB passwords)

## Deployment Security

### Streamlit Cloud

- **Read-only GitHub token:** Streamlit uses minimal permissions
- **Data in repo:** Forecasts and outputs are public (by design for the API)
- **No sensitive data:** Master CSV has no PII

### Render API

- **Free tier:** Limited to 0.5GB RAM, vulnerable to memory exhaustion
- **No authentication:** Anyone can call any endpoint
- **No HTTPS enforcement:** HTTPS available but not required

**Fix:**
1. Set environment variables on Render for rate limiting
2. Add basic auth or API key requirement
3. Enable HTTPS redirect

## Monitoring & Alerting

### Current (None)

Pipeline failures logged to GitHub Actions, but no external alert.

**Fix:** Add Slack/Email notifications

```python
# In pipeline after quality_gate.py fails
import smtplib
from email.mime.text import MIMEText

if gate_failed:
    msg = MIMEText("Pipeline quality gate failed - see GitHub Actions")
    msg["Subject"] = "Agrolinking Pipeline Alert"
    msg["From"] = os.environ["ALERT_EMAIL"]
    msg["To"] = os.environ["ALERT_RECIPIENTS"]
    
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["ALERT_EMAIL"], os.environ["ALERT_PASSWORD"])
        server.send_message(msg)
```

## Incident Response

### What to do if...

#### ...the API returns 500 errors

1. Check the latest run log in `outputs/logs/`
2. Run `python pipeline/quality_gate.py` locally
3. If it fails, check for NaN or conflict markers
4. Don't push until quality gate passes

#### ...forecasts are obviously wrong

1. Check `MANUAL_PRICES` in `pipeline/06_validate.py` — is it stale?
2. Did the latest Agricome post arrive? Update the prices
3. Run `python pipeline/run_pipeline.py`
4. Check the validation report in `outputs/logs/validation_report_*.json`

#### ...a data breach is suspected

1. Rotate all GitHub secrets (TSDB_URL, API keys)
2. Check GitHub access logs for unauthorized activity
3. Review committed git history for accidentally-committed secrets
4. If secrets were exposed, invalidate them immediately

## Compliance

### Data Retention

- Master CSV kept for 2+ years (historical training data)
- Outputs kept for 90 days in outputs/ folder
- Logs kept for 30 days, then archived

### Data Ownership

- Agricome data: Licensed from Agricome Africa, attributed in outputs
- WFP data: Public, CC-BY license
- Agrolinking primary: Internal collection

### Personal Data (Alerts)

- User emails/phones stored only if user opts in
- Never shared with third parties
- Deleted on user request

## Security Checklist

- [ ] `requirements*.txt` pinned to exact versions
- [ ] GitHub Actions all pinned to commit SHAs
- [x] Authentication on `/alerts/*` endpoints (Google ID token, Oct 2026 — not API keys as originally planned, see above)
- [x] CORS restricted to real Agrolinking domains (Oct 2026 — `pis.agrolinking.com`, `agrolinking.com`, `www.agrolinking.com`; `allow_credentials=False`, see above)
- [ ] Rate limiting enabled (slowapi)
- [ ] Input validation on all endpoints
- [ ] Database role limited to INSERT only
- [ ] TSDB connection uses SSL + verify-full
- [ ] WFP downloads validated before use
- [ ] Secrets never logged or printed
- [ ] Monitoring/alerting configured for production
- [ ] Incident response plan documented and tested
