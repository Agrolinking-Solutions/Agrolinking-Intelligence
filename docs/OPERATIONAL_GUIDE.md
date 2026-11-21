# Operational Guide

How to run PIS day to day. Rewritten 20 November 2026; the earlier version
described a setup that no longer exists (hand-typed reference prices, Render,
a Streamlit dashboard). For how the system works, and for running it on your
own computer, see the README.

## What runs on its own

| When (Lagos time) | What | Where |
|---|---|---|
| Daily, 05:17 (backups 07:43, 09:23, 11:37 if the first didn't commit) | Full pipeline: fetch sources, forecast, check, commit | GitHub Actions, "Daily Forecast Pipeline" |
| Mondays, inside the daily run | Fair accuracy test (step 06c, about 9 minutes) | same |
| 1st of each month | Accuracy report for the month before, as a GitHub issue | same |
| 03:11 and 15:11 | Web price search | "Web price stream" workflow |
| Every 3 hours | Uptime check: API, website, newest run served | "Web price stream" workflow |
| A few minutes after each commit to `main` | API redeploys | Agrolinking's server (Victor) |

Nobody needs to push the daily outputs. The workflow commits them itself.

## Daily check (2 minutes)

1. Open the latest "Daily Forecast Pipeline" run on GitHub. The summary at
   the top says which steps failed, which commodities lack a recent real
   price, what the web search found, and any field quotes on trial.
2. Look at open GitHub issues. Each label means:

| Label | Opened when | What to do |
|---|---|---|
| `price-alert` | Two sources disagree about a price | Check which source is right; withdraw a bad web price (README, "Routine operations") |
| `data-feed` | A feed stopped: Agricome over 10 days, AFEX over 7 | Paste Agricome's posts, or check the AFEX reader |
| `uptime` | The API or website is down, or serving an old run | Tell Victor |
| `accuracy-report` | Monthly accuracy report | Read it; no action unless a commodity falls |

The API's own health check is https://pis-api.agrolinking.com/health.

## Things a person does

| How often | Task | How |
|---|---|---|
| Every 2-3 days | Add Agricome's prices | Copy their Threads posts; append rows to `data/raw/agricome_raw.csv` (README, "Routine operations") |
| Weekly, if collected | Add trader quotes | `data/raw/field_prices.csv` (README, "Routine operations"). Only `FIELD_QUOTES_LIVE` commodities (wheat, yellow maize) feed the forecasts; the rest are on trial. A quote held back in the run summary is usually a wrong unit: fix it, or write `confirmed` in its notes |
| When an issue opens | Review the alert | See the labels above |
| When a feed's delay changes | Update `PUBLICATION_LAG_DAYS` | `config/settings.py` |

Any change to how forecasts are made (a new source used as a real price, a
commodity moved out of trial, a method rule) is tested first with
`python scripts/research/forecast_audit.py` and switched on only with John's OK.

## Running it by hand

- **Re-run today:** GitHub, Actions, "Daily Forecast Pipeline", "Run workflow".
  A manual run always runs, even if today's run already committed.
- **On your own computer:** see the README, "Running it locally". Don't commit
  the outputs a local run writes: they clash with the automated commit.
  `git checkout -- outputs/ data/processed/` discards them.

## When something goes wrong

| Problem | First check |
|---|---|
| No commit today | The run's page: the quality gate or the commit step failed. Fix the cause, then "Run workflow" |
| A step failed but the run committed | Steps continue on error so one bad source can't stop publishing. The run summary names the step; its log on the run page gives the error |
| A price looks wrong | `/crosscheck` and `/checks` on the API; the master row's `data_source` and `notes` say where it came from |
| The API serves yesterday's data | Wait a few minutes after the commit (redeploy), then check the `uptime` issue; if it persists, tell Victor |
| Database out of step with the files | `python scripts/migration/migrate_to_postgres.py` (safe to repeat, no duplicates). Needs the database login |
| Local repo in a mess | `git status`, then `git checkout -- outputs/ data/processed/` and `git pull`. Never force-push `main` |

## Who

| Area | Person |
|---|---|
| Pipeline, data sources, forecasts | John |
| Server, database, website | Victor |
| GitHub issues | [Agrolinking-Solutions/Agrolinking-Intelligence](https://github.com/Agrolinking-Solutions/Agrolinking-Intelligence/issues) |
