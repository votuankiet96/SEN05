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
echo 3. OG Live    - Stream and Pub/Sub live mechanisms
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
echo 1. Show OG operation config
echo 2. Show selected strategy signal rules
echo 3. Show production service status
echo 4. Start all production services
echo 5. Stop all production services
echo 6. Restart all production services
echo 7. Run lint/tests/static audit
echo 8. Open project shell
echo 0. Back
echo.
set /p "ENGINE_CHOICE=Choose: "
if "%ENGINE_CHOICE%"=="1" call :run_task "OG Engine - Config" "%PY_CMD% -m og_core.ops config" & goto engine_menu
if "%ENGINE_CHOICE%"=="2" call :show_strategy_rules & goto engine_menu
if "%ENGINE_CHOICE%"=="3" call :run_linux_task "OG Engine - Service Status" "%PY_CMD% -m og_core.ops services" & goto engine_menu
if "%ENGINE_CHOICE%"=="4" call :run_linux_task "OG Engine - Start Services" "%SYSCTL% start og-live-stream.service og-live-pubsub.service og-dashboard.service og-live-stream-healthcheck.timer og-live-pubsub-healthcheck.timer; %PY_CMD% -m og_core.ops services" & goto engine_menu
if "%ENGINE_CHOICE%"=="5" call :run_linux_task "OG Engine - Stop Services" "%SYSCTL% stop og-live-stream.service og-live-pubsub.service og-dashboard.service og-live-stream-healthcheck.timer og-live-pubsub-healthcheck.timer; %PY_CMD% -m og_core.ops services" & goto engine_menu
if "%ENGINE_CHOICE%"=="6" call :run_linux_task "OG Engine - Restart Services" "%SYSCTL% restart og-live-stream.service og-live-pubsub.service og-dashboard.service; %SYSCTL% restart og-live-stream-healthcheck.timer og-live-pubsub-healthcheck.timer; %PY_CMD% -m og_core.ops services" & goto engine_menu
if "%ENGINE_CHOICE%"=="7" call :run_task "OG Engine - Validate" "%PY_CMD% -m og_core.ops validate" & goto engine_menu
if "%ENGINE_CHOICE%"=="8" call :open_shell & goto engine_menu
if "%ENGINE_CHOICE%"=="0" goto main_menu
goto engine_menu

:show_strategy_rules
echo.
echo Strategy signal rules
echo Available: combo, ma_cross, ai_trend, knn_combo
set "STRATEGY=combo"
set /p "STRATEGY=Strategy key [combo]: "
if "%STRATEGY%"=="" set "STRATEGY=combo"
call :run_task "OG Engine - Strategy %STRATEGY%" "%PY_CMD% -m og_core.ops strategies --strategy %STRATEGY%"
exit /b

:past_menu
cls
echo ==================== OG Past ====================
echo.
echo 1. Open dashboard
echo 2. Dashboard health and service
echo 3. Export single-symbol signal CSV
echo 4. Export bulk signal CSV
echo 5. Diagnostics and logs
echo 0. Back
echo.
set /p "PAST_CHOICE=Choose: "
if "%PAST_CHOICE%"=="1" call :open_dashboard & goto past_menu
if "%PAST_CHOICE%"=="2" goto past_service_menu
if "%PAST_CHOICE%"=="3" call :export_single & goto past_menu
if "%PAST_CHOICE%"=="4" call :export_bulk & goto past_menu
if "%PAST_CHOICE%"=="5" goto past_diagnostics_menu
if "%PAST_CHOICE%"=="0" goto main_menu
goto past_menu

:past_service_menu
cls
echo ============== OG Past - Dashboard Service ==============
echo.
echo 1. Show dashboard status and health
echo 2. Start dashboard service
echo 3. Restart dashboard service
echo 4. Stop dashboard service
echo 0. Back
echo.
set /p "PAST_SERVICE_CHOICE=Choose: "
if "%PAST_SERVICE_CHOICE%"=="1" call :run_linux_task "OG Past - Dashboard Status" "%SYSCTL% status og-dashboard.service --no-pager; %PY_CMD% -m og_past.ops health --base-url %DASHBOARD_URL%" & goto past_service_menu
if "%PAST_SERVICE_CHOICE%"=="2" call :run_linux_task "OG Past - Start Dashboard" "%SYSCTL% start og-dashboard.service; %SYSCTL% status og-dashboard.service --no-pager; %PY_CMD% -m og_past.ops health --base-url %DASHBOARD_URL%" & goto past_service_menu
if "%PAST_SERVICE_CHOICE%"=="3" call :run_linux_task "OG Past - Restart Dashboard" "%SYSCTL% restart og-dashboard.service; %SYSCTL% status og-dashboard.service --no-pager; %PY_CMD% -m og_past.ops health --base-url %DASHBOARD_URL%" & goto past_service_menu
if "%PAST_SERVICE_CHOICE%"=="4" call :run_linux_task "OG Past - Stop Dashboard" "%SYSCTL% stop og-dashboard.service; %SYSCTL% status og-dashboard.service --no-pager" & goto past_service_menu
if "%PAST_SERVICE_CHOICE%"=="0" goto past_menu
goto past_service_menu

:past_diagnostics_menu
cls
echo ================ OG Past - Diagnostics ================
echo.
echo 1. Follow dashboard logs
echo 2. Run dashboard API smoke test
echo 3. Show latest CSV exports
echo 4. Run dashboard foreground for debugging
echo 0. Back
echo.
set /p "PAST_DIAG_CHOICE=Choose: "
if "%PAST_DIAG_CHOICE%"=="1" call :run_linux_task "OG Past - Dashboard Logs" "journalctl --user -u og-dashboard.service -f" & goto past_diagnostics_menu
if "%PAST_DIAG_CHOICE%"=="2" call :run_task "OG Past - API Smoke Test" "%PY_CMD% -m og_past.ops smoke --base-url %DASHBOARD_URL%" & goto past_diagnostics_menu
if "%PAST_DIAG_CHOICE%"=="3" call :run_task "OG Past - Latest CSV Exports" "%PY_CMD% -m og_past.ops exports --dir runtime/exports --limit 20" & goto past_diagnostics_menu
if "%PAST_DIAG_CHOICE%"=="4" call :run_task "OG Past - Dashboard Foreground Debug" "%PY_CMD% -m og_past.main --host 127.0.0.1 --port 8516" & goto past_diagnostics_menu
if "%PAST_DIAG_CHOICE%"=="0" goto past_menu
goto past_diagnostics_menu

:live_menu
cls
echo ==================== OG Live ====================
echo.
echo 1. Stream mechanism
echo 2. Pub/Sub mechanism
echo 3. Health for both mechanisms
echo 4. Follow both logs
echo 5. Audit logs and compare
echo 0. Back
echo.
set /p "LIVE_CHOICE=Choose: "
if "%LIVE_CHOICE%"=="1" goto live_stream_menu
if "%LIVE_CHOICE%"=="2" goto live_pubsub_menu
if "%LIVE_CHOICE%"=="3" call :run_linux_task "OG Live - Both Health" "%PY_CMD% -m og_live.stream_mechanism.ops health; %PY_CMD% -m og_live.pubsub_mechanism.ops health" & goto live_menu
if "%LIVE_CHOICE%"=="4" call :run_linux_task "OG Live - Both Logs" "journalctl --user -u og-live-stream.service -u og-live-pubsub.service -f" & goto live_menu
if "%LIVE_CHOICE%"=="5" goto live_audit_menu
if "%LIVE_CHOICE%"=="0" goto main_menu
goto live_menu

:live_audit_menu
cls
echo ================= OG Live - Audit Logs =================
echo.
echo 1. Show latest audit events for both mechanisms
echo 2. Compare Stream vs Pub/Sub by snapshot
echo 3. Compare one strategy / symbol / timeframe
echo 4. Show signal-publish events only
echo 0. Back
echo.
set /p "LIVE_AUDIT_CHOICE=Choose: "
if "%LIVE_AUDIT_CHOICE%"=="1" call :run_task "OG Live - Audit Events" "%PY_CMD% -m og_live.ops audit --mechanism both --limit 60" & goto live_audit_menu
if "%LIVE_AUDIT_CHOICE%"=="2" call :run_task "OG Live - Audit Compare" "%PY_CMD% -m og_live.ops compare --limit 40" & goto live_audit_menu
if "%LIVE_AUDIT_CHOICE%"=="3" call :live_audit_pair_compare & goto live_audit_menu
if "%LIVE_AUDIT_CHOICE%"=="4" call :run_task "OG Live - Signal Publish Audit" "%PY_CMD% -m og_live.ops audit --mechanism both --limit 80 --stage signal_published --stage signal_queued --stage signal_skipped" & goto live_audit_menu
if "%LIVE_AUDIT_CHOICE%"=="0" goto live_menu
goto live_audit_menu

:live_audit_pair_compare
echo.
echo Audit compare filter
set "STRATEGY=combo"
set "SYMBOL=HK50"
set "TF=H4"
call :prompt_default STRATEGY "Strategy"
call :prompt_default SYMBOL "Symbol"
call :prompt_default TF "Timeframe"
call :run_task "OG Live - Audit Compare %STRATEGY% %SYMBOL% %TF%" "%PY_CMD% -m og_live.ops compare --strategy %STRATEGY% --symbol %SYMBOL% --timeframe %TF% --limit 40"
exit /b

:live_stream_menu
cls
echo ================= OG Live Stream =================
echo.
echo 1. Health
echo 2. Redis stream/state and latest signal
echo 3. Service status
echo 4. Start service
echo 5. Restart service
echo 6. Stop service
echo 7. Follow logs
echo 8. Debug tools
echo 0. Back
echo.
set /p "LIVE_STREAM_CHOICE=Choose: "
if "%LIVE_STREAM_CHOICE%"=="1" call :run_task "OG Live Stream - Health" "%PY_CMD% -m og_live.stream_mechanism.ops health" & goto live_stream_menu
if "%LIVE_STREAM_CHOICE%"=="2" call :run_task "OG Live Stream - Inspect" "%PY_CMD% -m og_live.stream_mechanism.ops inspect" & goto live_stream_menu
if "%LIVE_STREAM_CHOICE%"=="3" call :run_linux_task "OG Live Stream - Service Status" "%SYSCTL% status og-live-stream.service --no-pager; %PY_CMD% -m og_live.stream_mechanism.ops health" & goto live_stream_menu
if "%LIVE_STREAM_CHOICE%"=="4" call :run_linux_task "OG Live Stream - Start" "%SYSCTL% start og-live-stream.service; %SYSCTL% status og-live-stream.service --no-pager; %PY_CMD% -m og_live.stream_mechanism.ops health" & goto live_stream_menu
if "%LIVE_STREAM_CHOICE%"=="5" call :run_linux_task "OG Live Stream - Restart" "%SYSCTL% restart og-live-stream.service; %SYSCTL% status og-live-stream.service --no-pager; %PY_CMD% -m og_live.stream_mechanism.ops health" & goto live_stream_menu
if "%LIVE_STREAM_CHOICE%"=="6" call :run_linux_task "OG Live Stream - Stop" "%SYSCTL% stop og-live-stream.service; %SYSCTL% status og-live-stream.service --no-pager || true" & goto live_stream_menu
if "%LIVE_STREAM_CHOICE%"=="7" call :run_linux_task "OG Live Stream - Logs" "journalctl --user -u og-live-stream.service -f" & goto live_stream_menu
if "%LIVE_STREAM_CHOICE%"=="8" goto live_stream_debug_menu
if "%LIVE_STREAM_CHOICE%"=="0" goto live_menu
goto live_stream_menu

:live_stream_debug_menu
cls
echo ================= OG Live Stream - Debug Tools =================
echo.
echo 1. Run stream once smoke test
echo 2. Run stream foreground
echo 3. Run strict healthcheck
echo 0. Back
echo.
echo Warning: once/foreground debug commands can consume real Redis event entries.
echo Use them only when you intentionally debug the Stream mechanism.
echo.
set /p "LIVE_STREAM_DEBUG_CHOICE=Choose: "
if "%LIVE_STREAM_DEBUG_CHOICE%"=="1" call :run_task "OG Live Stream - Once Debug" "%PY_CMD% -m og_live.stream_mechanism.main --once" & goto live_stream_debug_menu
if "%LIVE_STREAM_DEBUG_CHOICE%"=="2" call :run_task "OG Live Stream - Foreground Debug" "%PY_CMD% -m og_live.stream_mechanism.main" & goto live_stream_debug_menu
if "%LIVE_STREAM_DEBUG_CHOICE%"=="3" call :run_task "OG Live Stream - Strict Healthcheck" "%PY_CMD% -m og_live.stream_mechanism.ops health --fail-on-warn" & goto live_stream_debug_menu
if "%LIVE_STREAM_DEBUG_CHOICE%"=="0" goto live_stream_menu
goto live_stream_debug_menu

:live_pubsub_menu
cls
echo ================= OG Live Pub/Sub =================
echo.
echo 1. Health
echo 2. Pub/Sub channel/state and latest signal
echo 3. Service status
echo 4. Start service
echo 5. Restart service
echo 6. Stop service
echo 7. Follow logs
echo 8. Debug tools
echo 0. Back
echo.
set /p "LIVE_PUBSUB_CHOICE=Choose: "
if "%LIVE_PUBSUB_CHOICE%"=="1" call :run_task "OG Live Pub/Sub - Health" "%PY_CMD% -m og_live.pubsub_mechanism.ops health" & goto live_pubsub_menu
if "%LIVE_PUBSUB_CHOICE%"=="2" call :run_task "OG Live Pub/Sub - Inspect" "%PY_CMD% -m og_live.pubsub_mechanism.ops inspect" & goto live_pubsub_menu
if "%LIVE_PUBSUB_CHOICE%"=="3" call :run_linux_task "OG Live Pub/Sub - Service Status" "%SYSCTL% status og-live-pubsub.service --no-pager; %PY_CMD% -m og_live.pubsub_mechanism.ops health" & goto live_pubsub_menu
if "%LIVE_PUBSUB_CHOICE%"=="4" call :run_linux_task "OG Live Pub/Sub - Start" "%SYSCTL% start og-live-pubsub.service; %SYSCTL% status og-live-pubsub.service --no-pager; %PY_CMD% -m og_live.pubsub_mechanism.ops health" & goto live_pubsub_menu
if "%LIVE_PUBSUB_CHOICE%"=="5" call :run_linux_task "OG Live Pub/Sub - Restart" "%SYSCTL% restart og-live-pubsub.service; %SYSCTL% status og-live-pubsub.service --no-pager; %PY_CMD% -m og_live.pubsub_mechanism.ops health" & goto live_pubsub_menu
if "%LIVE_PUBSUB_CHOICE%"=="6" call :run_linux_task "OG Live Pub/Sub - Stop" "%SYSCTL% stop og-live-pubsub.service; %SYSCTL% status og-live-pubsub.service --no-pager || true" & goto live_pubsub_menu
if "%LIVE_PUBSUB_CHOICE%"=="7" call :run_linux_task "OG Live Pub/Sub - Logs" "journalctl --user -u og-live-pubsub.service -f" & goto live_pubsub_menu
if "%LIVE_PUBSUB_CHOICE%"=="8" goto live_pubsub_debug_menu
if "%LIVE_PUBSUB_CHOICE%"=="0" goto live_menu
goto live_pubsub_menu

:live_pubsub_debug_menu
cls
echo ================= OG Live Pub/Sub - Debug Tools =================
echo.
echo 1. Run Pub/Sub once smoke test
echo 2. Run Pub/Sub foreground
echo 3. Run strict healthcheck
echo 0. Back
echo.
echo Warning: once/foreground debug commands subscribe to live Pub/Sub messages.
echo Use them only when you intentionally debug the Pub/Sub mechanism.
echo.
set /p "LIVE_PUBSUB_DEBUG_CHOICE=Choose: "
if "%LIVE_PUBSUB_DEBUG_CHOICE%"=="1" call :run_task "OG Live Pub/Sub - Once Debug" "%PY_CMD% -m og_live.pubsub_mechanism.main --once --timeout-seconds 60" & goto live_pubsub_debug_menu
if "%LIVE_PUBSUB_DEBUG_CHOICE%"=="2" call :run_task "OG Live Pub/Sub - Foreground Debug" "%PY_CMD% -m og_live.pubsub_mechanism.main" & goto live_pubsub_debug_menu
if "%LIVE_PUBSUB_DEBUG_CHOICE%"=="3" call :run_task "OG Live Pub/Sub - Strict Healthcheck" "%PY_CMD% -m og_live.pubsub_mechanism.ops health --fail-on-warn" & goto live_pubsub_debug_menu
if "%LIVE_PUBSUB_DEBUG_CHOICE%"=="0" goto live_pubsub_menu
goto live_pubsub_debug_menu

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
call :run_linux_task "OG - Quick Status" "%PY_CMD% -m og_core.ops services; %PY_CMD% -m og_live.stream_mechanism.ops health; %PY_CMD% -m og_live.pubsub_mechanism.ops health; %PY_CMD% -m og_past.ops health --base-url %DASHBOARD_URL%"
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

:open_dashboard
if /I "%MODE%"=="SSH" (
  call :open_dashboard_tunnel
  exit /b
)
start "" "%DASHBOARD_URL%"
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
