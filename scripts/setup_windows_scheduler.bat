@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM AGROLINKING INTELLIGENCE — Windows Task Scheduler Setup
REM Run this script ONCE as Administrator to set up daily automation.
REM ─────────────────────────────────────────────────────────────────────────

SET PROJECT_DIR=%~dp0..
SET PYTHON=%PROJECT_DIR%\venv\Scripts\python.exe
SET PIPELINE=%PROJECT_DIR%\pipeline\run_pipeline.py

echo Setting up Agrolinking Intelligence Scheduler...
echo Project: %PROJECT_DIR%
echo Python:  %PYTHON%
echo.

REM ── Daily forecast run (Mon–Sun at 08:00 AM, skip training) ──────────────
schtasks /create /tn "Agrolinking Daily Forecast" ^
  /tr "\"%PYTHON%\" \"%PIPELINE%\" --skip-train" ^
  /sc daily ^
  /st 08:00 ^
  /f
echo [OK] Daily forecast task created (08:00 AM every day)

REM ── Weekly full retrain (Every Monday at 07:00 AM) ────────────────────────
schtasks /create /tn "Agrolinking Weekly Retrain" ^
  /tr "\"%PYTHON%\" \"%PIPELINE%\"" ^
  /sc weekly ^
  /d MON ^
  /st 07:00 ^
  /f
echo [OK] Weekly retrain task created (07:00 AM every Monday)

echo.
echo ═══════════════════════════════════════════════
echo  Setup complete! Verify in Task Scheduler app.
echo  Search "Task Scheduler" in Windows Start Menu
echo  Look for "Agrolinking Daily Forecast" task
echo ═══════════════════════════════════════════════
pause
