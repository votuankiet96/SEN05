@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem OG Program launcher.
rem Each selected action opens a separate terminal window, so long-running
rem dashboard/live/log/export processes do not overlap with this menu.

set "APP_TITLE=OG Program Launcher"
set "LINUX_DIR=/home/administrator/Desktop/og_program"
set "SSH_USER=administrator"
set "SSH_HOST=VM-OG8"
set "DASHBOARD_URL=http://127.0.0.1:8516"
set "WIN_DIR=%~dp0"
set "MODE="

call :choose_mode

:main_menu
cls
echo ============================================================
echo %APP_TITLE%
echo Backend: %MODE%
echo ============================================================
echo.
echo 1. OG Engine  - config, services, strategies, tests
echo 2. OG Past    - dashboard and CSV export
echo 3. OG Live    - live Redis engine and healthcheck
echo 4. Quick status for all services
echo 5. Change backend
echo 0. Exit
echo.
set /p "MAIN_CHOICE=Choose: "
if "%MAIN_CHOICE%"=="1" goto engine_menu
if "%MAIN_CHOICE%"=="2" goto past_menu
if "%MAIN_CHOICE%"=="3" goto live_menu
if "%MAIN_CHOICE%"=="4" call :quick_status & goto main_menu
if "%MAIN_CHOICE%"=="5" call :choose_mode & goto main_menu
if "%MAIN_CHOICE%"=="0" goto done
goto main_menu

:engine_menu
cls
echo ==================== OG Engine ====================
echo.
echo 1. Show OG core config
echo 2. Show strategies and parameters
echo 3. Check systemd service status
echo 4. Start all production services
echo 5. Stop all production services
echo 6. Restart all production services
echo 7. Run lint/tests/static audit
echo 8. Open project shell
echo 0. Back
echo.
set /p "ENGINE_CHOICE=Choose: "
if "%ENGINE_CHOICE%"=="1" call :run_task "OG Engine - Config" "%PY_CMD% -m og_core.ops config" & goto engine_menu
if "%ENGINE_CHOICE%"=="2" call :run_task "OG Engine - Strategies" "%PY_CMD% -m og_core.ops strategies" & goto engine_menu
if "%ENGINE_CHOICE%"=="3" call :run_linux_task "OG Engine - Service Status" "%SYSCTL% status og-live.service og-dashboard.service; %SYSCTL% list-timers og-live-healthcheck.timer" & goto engine_menu
if "%ENGINE_CHOICE%"=="4" call :run_linux_task "OG Engine - Start Services" "%SYSCTL% start og-live.service og-dashboard.service og-live-healthcheck.timer; %SYSCTL% status og-live.service og-dashboard.service --no-pager" & goto engine_menu
if "%ENGINE_CHOICE%"=="5" call :run_linux_task "OG Engine - Stop Services" "%SYSCTL% stop og-live.service og-dashboard.service og-live-healthcheck.timer; %SYSCTL% status og-live.service og-dashboard.service --no-pager" & goto engine_menu
if "%ENGINE_CHOICE%"=="6" call :run_linux_task "OG Engine - Restart Services" "%SYSCTL% restart og-live.service og-dashboard.service; %SYSCTL% restart og-live-healthcheck.timer; %SYSCTL% status og-live.service og-dashboard.service --no-pager" & goto engine_menu
if "%ENGINE_CHOICE%"=="7" call :run_task "OG Engine - Validate" "%PY_CMD% -m ruff check src/ tests/; %PY_CMD% -m pytest -q; %PY_CMD% -m vulture src tests --min-confidence 80" & goto engine_menu
if "%ENGINE_CHOICE%"=="8" call :open_shell & goto engine_menu
if "%ENGINE_CHOICE%"=="0" goto main_menu
goto engine_menu

:past_menu
cls
echo ==================== OG Past ====================
echo.
echo 1. Dashboard status
echo 2. Start dashboard service
echo 3. Stop dashboard service
echo 4. Restart dashboard service
echo 5. Follow dashboard logs
echo 6. Open dashboard in browser
echo 7. Open SSH tunnel for dashboard then browse 127.0.0.1:8516
echo 8. Run dashboard foreground in a separate terminal
echo 9. Export single-symbol signal CSV
echo 10. Export bulk signal CSV
echo 0. Back
echo.
set /p "PAST_CHOICE=Choose: "
if "%PAST_CHOICE%"=="1" call :run_linux_task "OG Past - Status" "%SYSCTL% status og-dashboard.service --no-pager; curl -fsS --max-time 5 %DASHBOARD_URL%/health" & goto past_menu
if "%PAST_CHOICE%"=="2" call :run_linux_task "OG Past - Start Dashboard" "%SYSCTL% start og-dashboard.service; %SYSCTL% status og-dashboard.service --no-pager" & goto past_menu
if "%PAST_CHOICE%"=="3" call :run_linux_task "OG Past - Stop Dashboard" "%SYSCTL% stop og-dashboard.service; %SYSCTL% status og-dashboard.service --no-pager" & goto past_menu
if "%PAST_CHOICE%"=="4" call :run_linux_task "OG Past - Restart Dashboard" "%SYSCTL% restart og-dashboard.service; %SYSCTL% status og-dashboard.service --no-pager" & goto past_menu
if "%PAST_CHOICE%"=="5" call :run_linux_task "OG Past - Logs" "journalctl --user -u og-dashboard.service -f" & goto past_menu
if "%PAST_CHOICE%"=="6" start "" "%DASHBOARD_URL%" & goto past_menu
if "%PAST_CHOICE%"=="7" call :open_dashboard_tunnel & goto past_menu
if "%PAST_CHOICE%"=="8" call :run_task "OG Past - Dashboard Foreground" "%PY_CMD% -m og_past.main --host 127.0.0.1 --port 8516" & goto past_menu
if "%PAST_CHOICE%"=="9" call :export_single & goto past_menu
if "%PAST_CHOICE%"=="10" call :export_bulk & goto past_menu
if "%PAST_CHOICE%"=="0" goto main_menu
goto past_menu

:live_menu
cls
echo ==================== OG Live ====================
echo.
echo 1. Live service status
echo 2. Start live service
echo 3. Stop live service
echo 4. Restart live service
echo 5. Follow live logs
echo 6. Run live healthcheck
echo 7. Run live once smoke test
echo 8. Run live foreground in a separate terminal
echo 9. Inspect Redis streams and latest signal
echo 0. Back
echo.
set /p "LIVE_CHOICE=Choose: "
if "%LIVE_CHOICE%"=="1" call :run_linux_task "OG Live - Status" "%SYSCTL% status og-live.service --no-pager; %PY_CMD% -m og_live.healthcheck" & goto live_menu
if "%LIVE_CHOICE%"=="2" call :run_linux_task "OG Live - Start" "%SYSCTL% start og-live.service; %SYSCTL% status og-live.service --no-pager" & goto live_menu
if "%LIVE_CHOICE%"=="3" call :run_linux_task "OG Live - Stop" "%SYSCTL% stop og-live.service; %SYSCTL% status og-live.service --no-pager" & goto live_menu
if "%LIVE_CHOICE%"=="4" call :run_linux_task "OG Live - Restart" "%SYSCTL% restart og-live.service; %SYSCTL% status og-live.service --no-pager" & goto live_menu
if "%LIVE_CHOICE%"=="5" call :run_linux_task "OG Live - Logs" "journalctl --user -u og-live.service -f" & goto live_menu
if "%LIVE_CHOICE%"=="6" call :run_task "OG Live - Healthcheck" "%PY_CMD% -m og_live.healthcheck" & goto live_menu
if "%LIVE_CHOICE%"=="7" call :run_task "OG Live - Once" "%PY_CMD% -m og_live.main --once" & goto live_menu
if "%LIVE_CHOICE%"=="8" call :run_task "OG Live - Foreground" "%PY_CMD% -m og_live.main" & goto live_menu
if "%LIVE_CHOICE%"=="9" call :run_task "OG Live - Redis Inspect" "%PY_CMD% -m og_live.healthcheck --json --compact-json" & goto live_menu
if "%LIVE_CHOICE%"=="0" goto main_menu
goto live_menu

:choose_mode
cls
echo ============================================================
echo Choose where commands should run
echo ============================================================
echo.
echo 1. SSH to %SSH_USER%@%SSH_HOST%  [recommended for VM-OG8]
echo 2. WSL/Linux local path %LINUX_DIR%
echo 3. Windows local checkout beside this .bat file
echo.
echo Notes:
echo - Passwords are NOT stored in this file.
echo - SSH mode will ask for the VM password in each new terminal if needed.
echo - Service start/stop/status requires SSH or WSL/Linux mode.
echo.
set /p "MODE_CHOICE=Choose backend [1]: "
if "%MODE_CHOICE%"=="" set "MODE_CHOICE=1"
if "%MODE_CHOICE%"=="1" set "MODE=SSH"
if "%MODE_CHOICE%"=="2" set "MODE=WSL"
if "%MODE_CHOICE%"=="3" set "MODE=WIN"
if "%MODE%"=="" goto choose_mode
if /I "%MODE%"=="WIN" (
  set "PY_CMD=.\.venv\Scripts\python.exe"
) else (
  set "PY_CMD=./.venv/bin/python"
)
set "SYSCTL=export XDG_RUNTIME_DIR=/run/user/1000; systemctl --user"
exit /b

:quick_status
call :run_linux_task "OG - Quick Status" "%SYSCTL% status og-live.service og-dashboard.service --no-pager; %SYSCTL% list-timers og-live-healthcheck.timer; %PY_CMD% -m og_live.healthcheck; curl -fsS --max-time 5 %DASHBOARD_URL%/health"
exit /b

:run_linux_task
if /I "%MODE%"=="WIN" (
  echo This command requires SSH or WSL/Linux mode.
  pause
  exit /b
)
call :run_task "%~1" "%~2"
exit /b

:run_task
set "TASK_TITLE=%~1"
set "TASK_CMD=%~2"
if /I "%MODE%"=="SSH" (
  start "%TASK_TITLE%" cmd /k ssh -t %SSH_USER%@%SSH_HOST% "cd %LINUX_DIR% && %TASK_CMD%; echo; read -r -p 'Press Enter to close...'"
  exit /b
)
if /I "%MODE%"=="WSL" (
  start "%TASK_TITLE%" cmd /k wsl.exe bash -lc "cd '%LINUX_DIR%' && %TASK_CMD%; echo; read -r -p 'Press Enter to close...'"
  exit /b
)
start "%TASK_TITLE%" cmd /k "cd /d "%WIN_DIR%" && %TASK_CMD% & echo. & pause"
exit /b

:open_shell
if /I "%MODE%"=="SSH" (
  start "OG Shell - SSH" cmd /k ssh -t %SSH_USER%@%SSH_HOST% "cd %LINUX_DIR% && bash -l"
  exit /b
)
if /I "%MODE%"=="WSL" (
  start "OG Shell - WSL" cmd /k wsl.exe bash -lc "cd '%LINUX_DIR%' && exec bash -l"
  exit /b
)
start "OG Shell - Windows" cmd /k "cd /d "%WIN_DIR%""
exit /b

:open_dashboard_tunnel
if /I not "%MODE%"=="SSH" (
  echo SSH tunnel is only needed in SSH mode.
  echo Open browser directly: %DASHBOARD_URL%
  pause
  exit /b
)
start "OG Dashboard SSH Tunnel" cmd /k ssh -N -L 8516:127.0.0.1:8516 %SSH_USER%@%SSH_HOST%
start "" "%DASHBOARD_URL%"
exit /b

:export_single
echo.
echo Single-symbol CSV export
set "STRATEGY=combo"
set "SYMBOL=US30"
set "TF=H1"
set "BARS=500"
set "COLS=bartime,side,signal,entry_price,sl_price,tp_price,risk_reward,signal_reason"
set "START_DATE="
set "END_DATE="
call :prompt_default STRATEGY "Strategy"
call :prompt_default SYMBOL "Symbol"
call :prompt_default TF "Timeframe"
call :prompt_default BARS "Bars"
call :prompt_default COLS "Columns"
set /p "START_DATE=Start date optional (YYYY-MM-DD or dd/mm/yyyy): "
set /p "END_DATE=End date optional   (YYYY-MM-DD or dd/mm/yyyy): "
call :collect_params
set "DATE_ARGS="
if not "%START_DATE%"=="" set "DATE_ARGS=%DATE_ARGS% --start-date %START_DATE%"
if not "%END_DATE%"=="" set "DATE_ARGS=%DATE_ARGS% --end-date %END_DATE%"
set "CMD=%PY_CMD% -m og_past.export_cli single --strategy %STRATEGY% --symbol %SYMBOL% --tf %TF% --bars %BARS% --cols %COLS% --output-dir runtime/exports%DATE_ARGS%%EXTRA_PARAMS%"
call :run_task "OG Past - Export Single CSV" "%CMD%"
exit /b

:export_bulk
echo.
echo Bulk CSV export
set "STRATEGY=combo"
set "SYMBOLS=BTCUSD,DE40,FR40,GOLD,HK50,J225,SP35,UK100,US100,US30,US500"
set "TF=H1"
set "BARS=500"
set "COLS=bartime,symbol,signal,entry_price,sl_price,tp_price,risk_reward,signal_reason"
set "START_DATE="
set "END_DATE="
call :prompt_default STRATEGY "Strategy"
call :prompt_default SYMBOLS "Symbols csv"
call :prompt_default TF "Timeframe"
call :prompt_default BARS "Bars"
call :prompt_default COLS "Columns"
set /p "START_DATE=Start date optional (YYYY-MM-DD or dd/mm/yyyy): "
set /p "END_DATE=End date optional   (YYYY-MM-DD or dd/mm/yyyy): "
call :collect_params
set "DATE_ARGS="
if not "%START_DATE%"=="" set "DATE_ARGS=%DATE_ARGS% --start-date %START_DATE%"
if not "%END_DATE%"=="" set "DATE_ARGS=%DATE_ARGS% --end-date %END_DATE%"
set "CMD=%PY_CMD% -m og_past.export_cli bulk --strategy %STRATEGY% --symbols %SYMBOLS% --tf %TF% --bars %BARS% --cols %COLS% --output-dir runtime/exports%DATE_ARGS%%EXTRA_PARAMS%"
call :run_task "OG Past - Export Bulk CSV" "%CMD%"
exit /b

:prompt_default
set "VAR_NAME=%~1"
set "LABEL=%~2"
set "CURRENT=!%VAR_NAME%!"
set /p "NEW_VALUE=%LABEL% [!CURRENT!]: "
if not "!NEW_VALUE!"=="" set "%VAR_NAME%=!NEW_VALUE!"
exit /b

:collect_params
set "EXTRA_PARAMS="
:param_loop
set "ONE_PARAM="
set /p "ONE_PARAM=Extra strategy param NAME=VALUE, blank when done: "
if "%ONE_PARAM%"=="" exit /b
set "EXTRA_PARAMS=%EXTRA_PARAMS% --param %ONE_PARAM%"
goto param_loop

:done
endlocal
exit /b 0
