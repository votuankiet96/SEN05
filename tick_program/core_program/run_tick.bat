@echo off
setlocal
cd /d "%~dp0"

set "TICK_ROOT=%CD%"
set "TICK_PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%TICK_PYTHON%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python was not found in the expected install path or on PATH.
        exit /b 1
    )
    set "TICK_PYTHON=python"
)

if "%~1"=="" goto menu
if /i "%~1"=="check" goto check
if /i "%~1"=="start" goto start
if /i "%~1"=="stop" goto stop
goto usage

:menu
echo.
echo tick_program
echo   1. System check (settings, health, spool backlog)
echo   2. Run service in this foreground window
echo   3. Gracefully stop the running service
echo.
choice /c 123 /n /m "Select [1-3]: "
if errorlevel 3 goto stop
if errorlevel 2 goto start
goto check

:check
set "TICK_RESULT=0"
echo.
echo === Effective settings (secret-free) ===
"%TICK_PYTHON%" -B -m src show-config
if errorlevel 1 set "TICK_RESULT=1"
echo.
echo === Health check ===
"%TICK_PYTHON%" -B -m src check
if errorlevel 1 set "TICK_RESULT=1"
echo.
echo === Spool backlog ===
"%TICK_PYTHON%" -B -m src spool-status
if errorlevel 1 set "TICK_RESULT=1"
exit /b %TICK_RESULT%

:start
if not exist "%TICK_ROOT%\runtime\run" mkdir "%TICK_ROOT%\runtime\run"
del /f /q "%TICK_ROOT%\runtime\run\supervisor.stop" >nul 2>&1
echo Note: the production service normally runs via the "SEN05 Tick Program Engine"
echo Scheduled Task. If that instance is already running, starting another one here
echo will request it to gracefully hand off before this one takes over.
echo.
echo Running tick_program in the foreground. It runs on startup, then follows the
echo configured schedule. Keep this window open; use "%~nx0 stop" from another
echo window for a graceful stop.
"%TICK_PYTHON%" -B -m src service
exit /b %ERRORLEVEL%

:stop
if not exist "%TICK_ROOT%\runtime\run" mkdir "%TICK_ROOT%\runtime\run"
echo Requesting a graceful stop...
type nul > "%TICK_ROOT%\runtime\run\supervisor.stop"
if errorlevel 1 (
    echo ERROR: could not write the stop sentinel.
    exit /b 1
)
echo Stop requested -- the running service will exit at its next loop check
echo (within a few seconds). This does not kill the process directly.
exit /b 0

:usage
echo Usage: %~nx0 [check^|start^|stop]
echo   run_tick.bat check   -^> settings + check + spool-status
echo   run_tick.bat start   -^> run the service in this foreground window
echo   run_tick.bat stop    -^> request a graceful stop of the running service
exit /b 2
