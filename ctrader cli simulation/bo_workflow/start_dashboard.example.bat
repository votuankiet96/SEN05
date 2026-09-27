@echo off
setlocal EnableExtensions
REM Double-click launcher for the BO Workflow Dashboard.
REM It never starts a second server when the dashboard is already healthy.
REM
REM THIS IS AN EXAMPLE. Copy to start_dashboard.bat and fill in your own
REM CTRADER_CTID / CTRADER_PWD_FILE / CTRADER_ACCOUNT below. The real
REM start_dashboard.bat is gitignored, not pushed.

set "CTRADER_CTID=your-ctrader-account-email@example.com"
set "CTRADER_PWD_FILE=C:\path\to\.ctrader-cli-pwd.txt"
set "CTRADER_ACCOUNT=your-account-number"
set "CTRADER_BROKER=FTMO Platform"
set "PYTHONUTF8=1"
set "BO_DASH_DEBUG=0"

set "DASHBOARD_DIR=%~dp0dashboard"
set "DASHBOARD_URL=http://127.0.0.1:8050"
set "DASHBOARD_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not exist "%DASHBOARD_DIR%\app.py" (
    echo Dashboard app.py was not found:
    echo   %DASHBOARD_DIR%\app.py
    pause
    exit /b 1
)

REM A healthy existing server wins. This prevents port-8050 conflicts when
REM the launcher is clicked again or a Dash hot-reload process is already up.
call :dashboard_healthy
if not errorlevel 1 goto open_browser

REM An occupied but unhealthy port means a stale Dash/reloader process exists.
REM Starting another instance here creates a second server that cannot recover
REM the first one, so fail visibly instead of multiplying background workers.
call :dashboard_port_in_use
if not errorlevel 1 goto occupied_port

if not exist "%DASHBOARD_PYTHON%" set "DASHBOARD_PYTHON=py.exe"

echo Starting BO Workflow Dashboard...
if /I "%DASHBOARD_PYTHON%"=="py.exe" (
    start "BO Workflow Dashboard - server" /D "%DASHBOARD_DIR%" cmd.exe /k py.exe -3 app.py
) else (
    start "BO Workflow Dashboard - server" /D "%DASHBOARD_DIR%" cmd.exe /k ""%DASHBOARD_PYTHON%" app.py"
)

REM Do not open the browser on a blind timer: wait until Dash actually answers.
for /L %%I in (1,1,60) do (
    call :dashboard_healthy
    if not errorlevel 1 goto open_browser
    powershell -NoProfile -Command "Start-Sleep -Seconds 1"
)

echo.
echo Dashboard did not become available at %DASHBOARD_URL% within 60 seconds.
echo Check the "BO Workflow Dashboard - server" window for the exact error.
pause
exit /b 1

:occupied_port
echo.
echo Port 8050 is already in use, but the existing dashboard is not responding.
echo Stop the stale dashboard server before starting a new one.
pause
exit /b 1

:open_browser
start "" "%DASHBOARD_URL%"
exit /b 0

:dashboard_healthy
powershell -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing '%DASHBOARD_URL%' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch { } ; exit 1"
exit /b %errorlevel%

:dashboard_port_in_use
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue) { exit 0 }; exit 1"
exit /b %errorlevel%
