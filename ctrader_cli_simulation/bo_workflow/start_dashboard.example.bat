@echo off
setlocal EnableExtensions
REM Double-click launcher for the BO Workflow Dashboard.
REM - The server runs in THIS window: closing it (or Ctrl+C) stops the dashboard
REM   completely, including background workers and any running ctrader-cli.
REM - Running this file always gives the latest code: a dashboard still running
REM   from before the last code change is stopped and started again. It asks
REM   first when a dashboard session (backtest/analysis) is still running.
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

set "BO_WORKFLOW_DIR=%~dp0"
set "DASHBOARD_DIR=%~dp0dashboard"
set "DASHBOARD_URL=http://127.0.0.1:8050"
set "DASHBOARD_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
set "PY_ARGS="
if not exist "%DASHBOARD_PYTHON%" (
    set "DASHBOARD_PYTHON=py.exe"
    set "PY_ARGS=-3"
)

if not exist "%DASHBOARD_DIR%\app.py" (
    echo Dashboard app.py was not found:
    echo   %DASHBOARD_DIR%\app.py
    pause
    exit /b 1
)

call :dashboard_port_in_use
if errorlevel 1 goto start_server

REM A server already owns port 8050. Keep it only if it started after the
REM newest code change and still answers; otherwise replace it.
call :server_is_stale
if not errorlevel 1 goto restart_needed
call :dashboard_healthy
if not errorlevel 1 goto open_existing
echo The dashboard on port 8050 is not responding.

:restart_needed
echo The running dashboard is older than the code on disk or not responding: restarting it.
call :running_sessions
if errorlevel 4 goto ask_stop
if errorlevel 3 goto stop_and_start
goto ask_stop

:ask_stop
echo.
echo A dashboard session is still running - listed above - or its status could not be read.
echo Restarting now stops it; it can be run again from the dashboard afterwards.
choice /C YN /N /M "Stop it and restart with the latest code? [Y/N] "
if errorlevel 2 goto keep_existing
goto stop_and_start

:keep_existing
echo Keeping the current dashboard. Run this file again after the session finishes.
call :dashboard_healthy
if not errorlevel 1 start "" "%DASHBOARD_URL%"
pause
exit /b 0

:stop_and_start
call :stop_server
if errorlevel 1 goto stop_failed
goto start_server

:stop_failed
echo Port 8050 could not be freed. Close the old dashboard window, then run this file again.
pause
exit /b 1

:open_existing
echo The dashboard is already running the latest code.
start "" "%DASHBOARD_URL%"
exit /b 0

:start_server
title BO Workflow Dashboard - close this window to stop it
echo Starting BO Workflow Dashboard. Close this window or press Ctrl+C to stop it completely.
REM Open the browser only once Dash actually answers, not on a blind timer.
start "" /B powershell -NoProfile -Command "for ($i = 0; $i -lt 90; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing '%DASHBOARD_URL%' -TimeoutSec 2).StatusCode -eq 200) { Start-Process '%DASHBOARD_URL%'; exit 0 } } catch { }; Start-Sleep -Seconds 1 }"
pushd "%DASHBOARD_DIR%"
"%DASHBOARD_PYTHON%" %PY_ARGS% app.py
popd
echo.
echo Dashboard server stopped.
pause
exit /b 0

:dashboard_healthy
powershell -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing '%DASHBOARD_URL%' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch { } ; exit 1"
exit /b %errorlevel%

:dashboard_port_in_use
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue) { exit 0 }; exit 1"
exit /b %errorlevel%

:server_is_stale
REM Exit 0 when the server on port 8050 started before the newest .py file in
REM dashboard/ or core_engine/ (or config.yaml) was last written.
powershell -NoProfile -Command "$ids = @(Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); $started = $ids | ForEach-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_)).CreationDate } | Where-Object { $_ } | Sort-Object | Select-Object -First 1; if (-not $started) { exit 0 }; $root = '%BO_WORKFLOW_DIR%'; $files = @(Get-ChildItem -Path (Join-Path $root 'dashboard'), (Join-Path $root 'core_engine') -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -eq '.py' }) + @(Get-Item (Join-Path $root 'config.yaml') -ErrorAction SilentlyContinue); $newest = ($files | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime; if ($newest -and $newest -gt $started) { exit 0 }; exit 1"
exit /b %errorlevel%

:running_sessions
REM Exit 0 and list them when sessions are running; 3 when none; other codes
REM mean the status could not be read, which the caller treats as "ask".
pushd "%DASHBOARD_DIR%"
"%DASHBOARD_PYTHON%" %PY_ARGS% -c "import os, sqlite3, sys; p = '.session_state/sessions.sqlite'; rows = sqlite3.connect('file:' + p + '?mode=ro', uri=True).execute('SELECT kind, title FROM sessions WHERE state = ?', ('running',)).fetchall() if os.path.isfile(p) else []; [print('   -', kind, '-', title) for kind, title in rows]; sys.exit(0 if rows else 3)"
set "RUNNING_RC=%errorlevel%"
popd
exit /b %RUNNING_RC%

:stop_server
REM Stop every process listening on 8050 with its whole process tree
REM (background workers, ctrader-cli), and the old launcher window that hosted
REM it. Only cmd.exe windows that were running app.py or this launcher are
REM closed, never an unrelated terminal. Exit 0 once the port is free.
powershell -NoProfile -Command "$ids = @(Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); foreach ($id in $ids) { $proc = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id); $parent = $null; if ($proc) { $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $proc.ParentProcessId) }; taskkill.exe /PID $id /T /F | Out-Null; if ($parent -and $parent.Name -eq 'cmd.exe' -and ($parent.CommandLine -match 'app\.py' -or $parent.CommandLine -match 'start_dashboard')) { taskkill.exe /PID $parent.ProcessId /T /F | Out-Null } }; for ($i = 0; $i -lt 15; $i++) { if (-not (Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue)) { exit 0 }; Start-Sleep -Seconds 1 }; exit 1"
exit /b %errorlevel%
