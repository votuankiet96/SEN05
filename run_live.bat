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
if /i "%~1"=="chart" goto chart
goto usage

:menu
echo.
echo DP Program - Live
echo   1. System check
echo   2. Run live service in this foreground window
echo   3. Gracefully stop the live service
echo   4. Open read-only chart (manual, foreground)
echo.
choice /c 1234 /n /m "Select [1-4]: "
if errorlevel 4 goto chart
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
echo === Live runtime status ===
"%DP_PYTHON%" -B -m dp_program status --mode live
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
echo Checking SQL before starting the foreground live service...
"%DP_PYTHON%" -B -m dp_program check-sql
if errorlevel 1 (
    echo ERROR: SQL preflight failed; the live service was not started.
    exit /b 1
)
"%DP_PYTHON%" -B -c "from pathlib import Path; from dp_program.configuration import load_config; (Path(load_config()['app']['runtime_dir']) / 'run' / 'stop_live.request').unlink(missing_ok=True)"
if errorlevel 1 (
    echo ERROR: The previous stop request could not be cleared safely.
    exit /b 1
)
echo Running live in the foreground. Keep this window open.
echo Use "%~nx0 stop" from another window for a graceful stop.
"%DP_PYTHON%" -B -m dp_program run-live
exit /b %ERRORLEVEL%

:stop
echo Requesting a graceful stop from the live service...
"%DP_PYTHON%" -B -m dp_program stop --mode live
exit /b %ERRORLEVEL%

:chart
echo Starting the read-only offline chart at http://127.0.0.1:8050 ...
echo Press Ctrl+C to stop and return.
"%DP_PYTHON%" -B -m dp_program.util.chart.server --open-browser
exit /b %ERRORLEVEL%

:usage
echo Usage: %~nx0 [check^|start^|stop^|chart]
exit /b 2
