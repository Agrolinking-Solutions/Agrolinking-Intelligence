# Agrolinking daily push script.
#
# NOTE: GitHub Actions (daily_pipeline.yml) already runs the pipeline and
# pushes every day. Running this as well means two machines write the same
# generated files - that is how conflict markers ended up in the master CSV
# (commit ba28bf9). Prefer letting Actions publish, and only use this if
# Actions is down.
#
# Safety rules:
#   - the quality gate must pass (no NaN outputs, no conflict markers)
#   - never force-push: if origin/main moved, stop and pull first

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

& $python (Join-Path $PSScriptRoot "pipeline\quality_gate.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "Quality gate failed - nothing was pushed. Fix the data first." -ForegroundColor Red
    exit 1
}

git fetch origin
$behind = git rev-list --count HEAD..origin/main
if ([int]$behind -gt 0) {
    Write-Host "Local main is $behind commit(s) behind origin/main (probably the Actions bot)." -ForegroundColor Red
    Write-Host "Run 'git pull' and re-run the pipeline before pushing. Nothing was pushed." -ForegroundColor Red
    exit 1
}

git add outputs/
git commit -m "Daily update $(Get-Date -Format 'yyyy-MM-dd')"
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "Push rejected - do NOT force-push. Pull, re-run, and try again." -ForegroundColor Red
    exit 1
}
Write-Host "Push complete"
