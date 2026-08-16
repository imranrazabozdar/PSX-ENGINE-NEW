# run_local.ps1 — Safe way to run the PSX engine on your PC without diverging
# from the cloud. It ALWAYS syncs with the cloud first, and uses the venv Python
# (the system Python can't reach PSX — see ssl_compat.py / truststore).
#
# Usage:
#   .\run_local.ps1          Run + preview locally only (does NOT commit/push).
#   .\run_local.ps1 -Push    Run, then safely commit + push the database so the
#                            online dashboard updates immediately.
#
# Tip: for a routine refresh you usually don't need this — the cloud runs every
# 15 minutes on its own. Use -Push only when you want an instant update.

param([switch]$Push)

Write-Host "1/3  Syncing with the cloud (git pull --rebase)..." -ForegroundColor Cyan
git pull --rebase --autostash origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "Sync failed (you may have a conflict). Fix that before running." -ForegroundColor Red
    exit 1
}

Write-Host "2/3  Running the engine with the venv Python..." -ForegroundColor Cyan
& .\venv\Scripts\python.exe main.py run
if ($LASTEXITCODE -ne 0) { Write-Host "Engine run failed." -ForegroundColor Red; exit 1 }

if (-not $Push) {
    Write-Host "3/3  Preview only - nothing committed. Open the dashboard to view." -ForegroundColor Green
    Write-Host "     Re-run with  .\run_local.ps1 -Push  to update the online dashboard." -ForegroundColor DarkGray
    exit 0
}

Write-Host "3/3  Committing + pushing the database..." -ForegroundColor Cyan
git add psx_engine.db
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mmZ")
git commit -m "Local run $stamp"
if ($LASTEXITCODE -ne 0) {
    Write-Host "No database changes to commit - nothing to push." -ForegroundColor Yellow
    exit 0
}
git pull --rebase --autostash origin main
git push origin main
if ($LASTEXITCODE -eq 0) {
    Write-Host "Done - the online dashboard will refresh shortly." -ForegroundColor Green
} else {
    Write-Host "Push failed - run the script again to re-sync." -ForegroundColor Red
}
