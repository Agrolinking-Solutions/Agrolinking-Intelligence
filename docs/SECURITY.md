# Security & Operations Guide

Rewritten 3 Nov 2026 after a review against the code. Status is marked
**Done** (in the code and checked) or **Open** (with who acts next).

## Status

| Area | Status | Where / next step |
|---|---|---|
| Repository private | Done (2 Nov 2026) | GitHub settings |
| Secrets out of the code | Done | GitHub Actions secrets: `TSDB_URL`, `PGPASSWORD`, `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID`; the server's environment. Issues use the built-in `GITHUB_TOKEN` |
| HTTPS | Done | API and website |
| CORS | Done | `api.py` `CORS_ALLOWED_ORIGINS`: pis.agrolinking.com, agrolinking.com, www.agrolinking.com; local origins only with `ALLOW_LOCAL_DEV_CORS=1` |
| Rate limits | Done | slowapi: 60/min per IP, 20/min on alert writes, `/health` exempt |
| Saved alerts | Done | Google ID token verified in `auth.py`; each user sees only their own; Postgres only |
| Pinned packages | Done (3 Nov 2026) | `requirements.txt` exact pins; `constraints.txt` pins every package installed by the workflows; see "Updating packages" |
| Pinned GitHub actions | Done (3 Nov 2026) | `uses:` references are commit SHAs with the version in a comment |
| Uptime monitoring | Done (backstop, 3 Nov 2026) / Open (fast checks) | `pipeline/uptime_check.py` every 3 hours, issue label `uptime`. For 5-minute checks see "Uptime" - John to set up |
| Database connection and rights | Done (6-9 Nov 2026) | Victor ran `scripts/migration/least_privilege.sql` and moved the API to its own login. `pipeline/db_security_check.py` (daily, step 09g) confirms it: TLS 1.3 required, the pipeline login owns no tables and has no admin rights, no findings (20 Nov 2026) |
| API server packages | Open | `requirements_api.txt` still has `>=` versions. Pin to the server's `pip freeze` - Victor |
| Price data readable by anyone | Open - decision | No key on read endpoints; `/docs` public. Must be settled before FarmPaddy prices are served. See "API access" - John |
| Data rights | Open | Agricome agreement, Mansa Markets (AFEX prices) terms - John. FarmPaddy cleared in writing (Nov 2026), see "Data rights" |
| Dependabot alerts | Open | Repository Settings → Code security → enable Dependabot alerts (free; alerts only) - John |

## Updating packages

Workflows install with `-c constraints.txt`, so nothing changes until the
file does. To take updates (monthly, or when a security alert names a
package), resolve again taking only releases at least a week old, on the
same Python and platform as the runs:

```bash
pip install --dry-run --ignore-installed --report report.json \
  --uploaded-prior-to <date a week ago, e.g. 2026-11-24T00:00:00Z> \
  --python-version 3.10 --implementation cp --only-binary=:all: \
  --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
  --platform manylinux_2_28_x86_64 --platform linux_x86_64 \
  -r requirements.txt pytest pip
```

Write the resolved versions into `constraints.txt` and the direct ones into
`requirements.txt`, run `pytest tests/`, push, and check the next daily run.
`outputs/logs/installed_packages.txt` records what each run installed.
`tests/test_security.py` fails if a workflow installs without the
constraints or uses an action not pinned to a commit.

To update a GitHub action: look up the commit of the new release tag
(`https://api.github.com/repos/actions/checkout/git/ref/tags/<tag>`) and
replace the SHA and the version comment in all three workflows.

## Secrets

| Secret | Used by | Rotate when |
|---|---|---|
| `TSDB_URL`, `PGPASSWORD` | daily pipeline, web search, database check | after `least_privilege.sql`; if anyone with access leaves |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` | web price search | if exposed; set a monthly spend limit in the Anthropic console |
| Server environment (database login, Google client ID) | API | with the database rotation |
| Server's GitHub access (deploy key) | the server's pull on push | if the server is rebuilt |

Never paste a secret into chat, an issue or a commit. If one is exposed:
rotate it first, then check the Actions logs and git history.

## Database

1. Read the daily run's "Database security" summary or
   `outputs/logs/db_security.json`. It shows whether the connection is
   encrypted and whether the pipeline logs in as a superuser, without the
   address, user or password.
2. If not encrypted: turn on SSL on the server, then add `PGSSLMODE: require`
   to the workflows' `env` and the server's environment (libpq reads it), or
   `?sslmode=require` in `TSDB_URL`. Use `verify-full` once the server's CA
   certificate is available to the clients.
3. Run `scripts/migration/least_privilege.sql` as the admin (instructions in
   the file): a `pis_pipeline` login (prices and forecasts, no user data) and
   a `pis_api` login (reads prices; manages users, alerts, portfolios).
   Update the secrets and the server, then change the admin password.
4. Record where the database is hosted in this file.

## Uptime

`pipeline/uptime_check.py` runs every 3 hours in the web price workflow (no extra
job). It opens one issue labelled `uptime` while the API or website is down,
or while the server is serving forecasts older than the newest daily run 45
minutes after it was committed (a broken deploy). It closes the issue when
all is well.

For checks every 5 minutes, add three monitors to a free outside service
(e.g. UptimeRobot), with email alerts to the team:

- `https://pis-api.agrolinking.com/health`, keyword `operational`
- `https://pis.agrolinking.com`
- `https://pis-api.agrolinking.com/commodities` (any 200)

## API access

All read endpoints are public and `/docs` lists them. That suits the public
website. Before price data is sold or licensed, choose one:

- keep the website public and add **keys for partners** (AgLens, buyers),
  with higher limits for key holders, or
- **require keys everywhere**, with the website calling the API through its
  own server so the key never reaches browsers (needs Victor's frontend
  change), and turn off `/docs` in production.

FarmPaddy's terms exclude unrestricted redistribution. An open endpoint that
anyone can pull FarmPaddy's price history from would break them. So before
FarmPaddy prices are served, either take the second option, or keep
FarmPaddy's prices off every endpoint that needs no key.

## Data rights

| Source | Status |
|---|---|
| Agricome | Prices typed in from public posts; agreement and back history requested Nov 2026 |
| WFP (HDX) | Open data, attribution required |
| FEWS NET via FAO | Public |
| AFEX via Mansa Markets | Read from public pages (robots.txt allows); terms for reuse not cleared |
| FarmPaddy (planned) | Written permission from the CEO (email, Nov 2026): free standard API access. Allowed: display in PIS and AgLens, including to paying subscribers, with FarmPaddy credited and linked, within usage limits and the API terms. Not allowed: unrestricted redistribution, sublicensing, resale, or repackaging the dataset as a standalone data product. Not yet integrated |

## Personal data

`users` holds an email address and an optional WhatsApp number, created on
Google sign-in; `alerts` and `portfolio_holdings` link to it. Nothing else
in the platform is personal. The pipeline does not need these tables
(`least_privilege.sql` removes its access). Delete a user's rows on request.

## Incidents

- **API errors or old data**: check the latest daily run; `pipeline/quality_gate.py`
  locally; the `uptime` issue says which check failed. A stuck server needs
  its deploy checked (Victor).
- **Wrong prices**: the run page's "Real-data status", `/crosscheck`,
  `/checks`. Remove a bad web price by moving it to
  `data/raw/web_prices_withdrawn.csv`; block a bad site in
  `data/raw/web_sources_blocked.csv`.
- **Suspected breach**: rotate all secrets (table above), review Actions
  logs and recent commits, check the database's users and alerts tables.
