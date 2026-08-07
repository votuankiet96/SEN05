@echo off
setlocal
cd /d "%~dp0"

set "DP_ROOT=%CD%"
set "DP_PYTHON=%DP_ROOT%\.venv\Scripts\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"
if defined PYTHONPATH (
    set "PYTHONPATH=%DP_ROOT%\src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%DP_ROOT%\src"
)
if not exist "%DP_PYTHON%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python was not found in .venv or PATH.
        exit /b 1
    )
    set "DP_PYTHON=python"
)

if "%~1"=="" goto menu
if /i "%~1"=="check" goto check
if /i "%~1"=="start" goto start
if /i "%~1"=="stop" goto stop
goto usage

:menu
echo.
echo DP Program - Backfill
echo   1. System check
echo   2. Run backfill scheduler in this foreground window
echo   3. Gracefully stop the backfill service
echo.
choice /c 123 /n /m "Select [1-3]: "
if errorlevel 3 goto stop
if errorlevel 2 goto start
goto check

:check
set "DP_RESULT=0"
echo.
echo === Effective settings (secret-free) ===
"%DP_PYTHON%" -B -m dp_program settings
if errorlevel 1 set "DP_RESULT=1"
echo.
echo === Backfill runtime status ===
"%DP_PYTHON%" -B -m dp_program status --mode backfill
if errorlevel 1 set "DP_RESULT=1"
echo.
echo === SQL contract ===
"%DP_PYTHON%" -B -m dp_program check-sql
if errorlevel 1 set "DP_RESULT=1"
echo.
echo === System doctor ===
"%DP_PYTHON%" -B -m dp_program doctor
if errorlevel 1 set "DP_RESULT=1"
exit /b %DP_RESULT%

:start
echo Checking SQL before starting the foreground backfill scheduler...
"%DP_PYTHON%" -B -m dp_program check-sql
if errorlevel 1 (
    echo ERROR: SQL preflight failed; the backfill service was not started.
    exit /b 1
)
"%DP_PYTHON%" -B -c "from pathlib import Path; from dp_program.configuration import load_config; (Path(load_config()['app']['runtime_dir']) / 'run' / 'stop_backfill.request').unlink(missing_ok=True)"
if errorlevel 1 (
    echo ERROR: The previous stop request could not be cleared safely.
    exit /b 1
)
echo Running backfill in the foreground. It runs on startup, then follows the configured schedule.
echo Keep this window open and use "%~nx0 stop" from another window for a graceful stop.
"%DP_PYTHON%" -B -m dp_program run-backfill
exit /b %ERRORLEVEL%

:stop
echo Requesting a graceful stop from the backfill service...
"%DP_PYTHON%" -B -m dp_program stop --mode backfill
exit /b %ERRORLEVEL%

:usage
echo Usage: %~nx0 [check^|start^|stop]
exit /b 2
