@echo off
setlocal EnableExtensions

rem core_python launcher.
rem CSV export opens in a separate terminal so its interactive wizard does
rem not overlap with this menu.

set "APP_TITLE=core_python Launcher"
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
echo 1. Open Dashboard
echo 2. Export Signal CSV
echo 0. Exit
echo.
set /p "MAIN_CHOICE=Choose: "
if "%MAIN_CHOICE%"=="1" call :open_dashboard & goto main_menu
if "%MAIN_CHOICE%"=="2" call :run_task "Export Signal CSV" "%PY_CMD% -m core_python.export_cli wizard --output-dir runtime/exports" & goto main_menu
if "%MAIN_CHOICE%"=="0" goto done
goto main_menu

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
start "%TASK_TITLE%" cmd /k "cd /d ""%WIN_DIR%"" && %TASK_CMD% & echo. & pause"
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
start "core_python Dashboard SSH Tunnel" cmd /k ssh -N -L 8516:127.0.0.1:8516 %SSH_USER%@%SSH_HOST%
start "" "%DASHBOARD_URL%"
exit /b

:done
endlocal
exit /b 0
