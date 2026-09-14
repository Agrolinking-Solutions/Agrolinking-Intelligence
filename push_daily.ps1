# Agrolinking daily push script - handles conflicts automatically
git fetch origin
git checkout --ours outputs/
git add outputs/
git commit -m "Daily update $(Get-Date -Format 'yyyy-MM-dd')" 2>$null
git fetch origin
git push --force-with-lease origin main
Write-Host "Push complete"
