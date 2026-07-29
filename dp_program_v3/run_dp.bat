@echo off
setlocal
cd /d "%~dp0"

set "DP_ROOT=%CD%"
set "DP_PYTHON=%DP_ROOT%\.venv\Scripts\python.exe"
set "DP_TASK_PATH=\SEN05\"
set "DP_TASK_NAME=SEN05 DP Program 24x7"
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
echo DP Program
echo   1. System check
echo   2. Start supervised 24x7 task
echo   3. Gracefully stop 24x7 task
echo.
choice /c 123 /n /m "Select [1-3]: "
if errorlevel 3 goto stop
if errorlevel 2 goto start
goto check

:check
set "DP_RESULT=0"
echo.
echo === Effective settings (secret-free) ===
"%DP_PYTHON%" -m dp_program settings
if errorlevel 1 set "DP_RESULT=1"
echo.
echo === Runtime status ===
"%DP_PYTHON%" -m dp_program status
if errorlevel 1 set "DP_RESULT=1"
echo.
echo === Scheduled Task ===
powershell.exe -NoProfile -Command "Get-ScheduledTask -TaskPath '%DP_TASK_PATH%' -TaskName '%DP_TASK_NAME%' -ErrorAction Stop | Select-Object TaskPath,TaskName,State"
if errorlevel 1 set "DP_RESULT=1"
echo.
echo === System doctor ===
"%DP_PYTHON%" -m dp_program doctor
if errorlevel 1 set "DP_RESULT=1"
exit /b %DP_RESULT%

:start
echo Starting the supervised DP Program 24x7 task...
"%DP_PYTHON%" -c "from pathlib import Path; from dp_program.configuration import load_config; (Path(load_config()['app']['runtime_dir']) / 'run' / 'stop.request').unlink(missing_ok=True)"
if errorlevel 1 (
    echo ERROR: The previous stop request could not be cleared safely.
    exit /b 1
)
powershell.exe -NoProfile -Command "try { Start-ScheduledTask -TaskPath '%DP_TASK_PATH%' -TaskName '%DP_TASK_NAME%' -ErrorAction Stop } catch { Write-Error $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo ERROR: The production Scheduled Task could not be started.
    exit /b 1
)
for /l %%I in (1,1,360) do (
    "%DP_PYTHON%" -m dp_program status >nul 2>&1
    if not errorlevel 1 goto started
    powershell.exe -NoProfile -Command "Start-Sleep -Seconds 1" >nul
)
echo ERROR: The task started but the service did not become healthy within 360 seconds.
exit /b 1

:started
"%DP_PYTHON%" -m dp_program status
exit /b %ERRORLEVEL%

:stop
echo Requesting a graceful stop from the supervised task...
"%DP_PYTHON%" -m dp_program stop
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -Command "for ($i=0; $i -lt 20; $i++) { $task=Get-ScheduledTask -TaskPath '%DP_TASK_PATH%' -TaskName '%DP_TASK_NAME%' -ErrorAction Stop; if ($task.State -ne 'Running') { exit 0 }; Start-Sleep -Seconds 1 }; Write-Error 'Scheduled Task remained running after graceful stop'; exit 1"
exit /b %ERRORLEVEL%

:usage
echo Usage: %~nx0 [check^|start^|stop]
exit /b 2
