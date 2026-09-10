@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem Quan ly 3 probe quan sat Redis (nen, chay lien tuc) o day:
rem keyspace_probe, pubsub_probe, state_probe. Ca 3 chi DOC Redis/SQL,
rem khong ghi gi -- day la ly do bo dp_simulator: no ghi that vao Redis
rem production qua redis_publisher, va khi no chay code cu con trong bo
rem nho thi da ghi hong du lieu schema moi (su co 2026-09-08).
rem
rem Dung force kill (taskkill /F) cho stop, KHONG dung co che
rem stop_*.request nhu run_live.bat/run_backfill.bat -- vi 3 chuong
rem trinh nay chi doc, khong co giao dich SQL/spool nao can dong sach
rem giua chung, nen ngat dot ngot la an toan.

set "PROBE_DIR=%CD%"
set "DP_PYTHON=%PROBE_DIR%\..\..\.venv\Scripts\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%DP_PYTHON%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python was not found in .venv or PATH.
        exit /b 1
    )
    set "DP_PYTHON=python"
)

set "PROGRAMS=keyspace_probe pubsub_probe state_probe"
set "RUN_DIR=%PROBE_DIR%\run"

if /i "%~1"=="start" goto start
if /i "%~1"=="stop" goto stop
if /i "%~1"=="status" goto status
goto usage

:start
if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"
for %%P in (%PROGRAMS%) do call :start_one %%P
echo.
echo Da yeu cau khoi dong ca 4. Log: probe_logs\<ten>.log -- PID: run\<ten>.pid
exit /b 0

:start_one
set "NAME=%~1"
if exist "%RUN_DIR%\%NAME%.pid" (
    set "EXISTING_PID="
    set /p EXISTING_PID=<"%RUN_DIR%\%NAME%.pid"
    if defined EXISTING_PID (
        tasklist /FI "PID eq !EXISTING_PID!" 2>nul | find "!EXISTING_PID!" >nul
        if not errorlevel 1 (
            echo %NAME%: da chay ^(PID !EXISTING_PID!^), bo qua.
            exit /b 0
        )
    )
)
start "dp_probe_%NAME%" /B "%DP_PYTHON%" -B "%PROBE_DIR%\%NAME%.py" >nul 2>&1
echo %NAME%: da khoi dong.
exit /b 0

:stop
for %%P in (%PROGRAMS%) do call :stop_one %%P
exit /b 0

:stop_one
set "NAME=%~1"
if not exist "%RUN_DIR%\%NAME%.pid" (
    echo %NAME%: khong thay pid file, co the chua chay.
    exit /b 0
)
set "STOP_PID="
set /p STOP_PID=<"%RUN_DIR%\%NAME%.pid"
if defined STOP_PID taskkill /F /PID !STOP_PID! >nul 2>&1
del "%RUN_DIR%\%NAME%.pid" >nul 2>&1
echo %NAME%: da dung ^(PID !STOP_PID!^).
exit /b 0

:status
for %%P in (%PROGRAMS%) do call :status_one %%P
exit /b 0

:status_one
set "NAME=%~1"
if not exist "%RUN_DIR%\%NAME%.pid" (
    echo %NAME%: KHONG chay
    exit /b 0
)
set "CHECK_PID="
set /p CHECK_PID=<"%RUN_DIR%\%NAME%.pid"
tasklist /FI "PID eq !CHECK_PID!" 2>nul | find "!CHECK_PID!" >nul
if errorlevel 1 (
    echo %NAME%: pid file con nhung tien trinh !CHECK_PID! da thoat ^(crash?^)
) else (
    echo %NAME%: dang chay ^(PID !CHECK_PID!^)
)
exit /b 0

:usage
echo Usage: %~nx0 [start^|stop^|status]
echo   start  - khoi dong ca 4 probe nen
echo   stop   - dung ca 4 ^(force kill -- an toan vi khong co giao dich can dong sach^)
echo   status - them ngoai start/stop, gan nhu mien phi: kiem PID con song khong
exit /b 2
