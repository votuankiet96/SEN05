@echo off
rem ============================================================================
rem  DPS launcher - runs the Data Provider Simulator in simple modes.
rem  Usage: dps.bat <mode> [options]        (dps.bat help lists the modes)
rem  Works from any folder: every path is resolved relative to this file.
rem  Options are forwarded exactly as typed (commas, "=" and quotes are kept).
rem  Set DPS_PYTHON to use a specific python.exe instead of the one on PATH.
rem ============================================================================
setlocal EnableExtensions
set "DPS_DIR=%~dp0"
set "SRC=%DPS_DIR%src"
set "PYTHONDONTWRITEBYTECODE=1"
set "PY=python"
if defined DPS_PYTHON set "PY=%DPS_PYTHON%"

rem --- split the command line: first word = mode, the rest is forwarded as typed ---
rem /y or --yes only means "do not ask" (used by clean); it is removed before forwarding.
set "ALL=%*"
set "MODE=%~1"
set "REST="
set "ASSUME_YES="
if defined MODE call set "REST=%%ALL:*%MODE%=%%"
for %%x in (%*) do (
    if /i "%%~x"=="/y" set "ASSUME_YES=1"
    if /i "%%~x"=="--yes" set "ASSUME_YES=1"
)
if defined REST set "REST=%REST: /y=%"
if defined REST set "REST=%REST: --yes=%"

rem --- modes that need no python ---
if "%MODE%"=="" goto help
if /i "%MODE%"=="help" goto help
if /i "%MODE%"=="-h" goto help
if /i "%MODE%"=="--help" goto help
if /i "%MODE%"=="/?" goto help
if /i "%MODE%"=="clean-files" goto cleanfiles

rem --- modes that run the program ---
"%PY%" --version >nul 2>&1
if errorlevel 1 goto nopython
if /i "%MODE%"=="plan"   set "CMD=plan" & goto go
if /i "%MODE%"=="run"    set "CMD=run" & goto go
if /i "%MODE%"=="status" set "CMD=status" & goto go
if /i "%MODE%"=="stop"   set "CMD=stop" & goto go
if /i "%MODE%"=="clean"  goto clean
echo Unknown mode: %MODE%
set "HELP_EXIT=1"
goto help

:go
"%PY%" -B "%SRC%" %CMD% %REST%
exit /b %ERRORLEVEL%

:clean
rem Without /y: show how many keys would be flushed, then ask. The program itself refuses any db
rem that is not in allowed_dbs, so this can never touch the live db.
if defined ASSUME_YES goto flush
"%PY%" -B "%SRC%" clean %REST%
if errorlevel 1 exit /b %ERRORLEVEL%
echo.
set "ANSWER="
set /p "ANSWER=Flush the Redis db shown above? [y/N]: "
if /i "%ANSWER%"=="y" goto flush
if /i "%ANSWER%"=="yes" goto flush
echo Aborted: nothing was deleted.
exit /b 0
:flush
"%PY%" -B "%SRC%" clean --yes %REST%
exit /b %ERRORLEVEL%

:cleanfiles
rem Deletes local artifacts INSIDE this folder only: runtime\ (logs, state, lock), __pycache__, .pytest_cache.
rem Refuses while a run is active. Redis is not touched (use "dps.bat clean" for that).
"%PY%" --version >nul 2>&1
if errorlevel 1 goto purge
"%PY%" -B "%SRC%" status 2>nul | findstr /c:"'healthy': True" >nul
if not errorlevel 1 goto active
:purge
if exist "%DPS_DIR%runtime" rmdir /s /q "%DPS_DIR%runtime"
for /d /r "%DPS_DIR%" %%d in (__pycache__ .pytest_cache) do if exist "%%d" rmdir /s /q "%%d"
echo Removed local artifacts under %DPS_DIR%: runtime, __pycache__, .pytest_cache.
exit /b 0
:active
echo A DPS run is active. Run "dps.bat stop" first, then try again.
exit /b 1

:nopython
echo ERROR: python was not found on PATH. Install Python 3.12 or set DPS_PYTHON to the full path of python.exe.
exit /b 1

:help
echo.
echo DPS - Data Provider Simulator: replays SQL candles onto Redis as if they were live.
echo.
echo Usage: dps.bat ^<mode^> [options]
echo.
echo Modes
echo   plan         Dry run: print the release schedule and its digest. Writes nothing.
echo   run          Start a NEW run: clear the target Redis db, seed it, then replay tick by tick.
echo                If Redis goes down while the run is going, it waits and continues by itself.
echo   status       Show the process state and the Redis checkpoint.
echo   stop         Ask the running process to stop cleanly (the next run starts over).
echo   clean        Flush the target Redis db. Shows the key count first and asks; /y skips the question.
echo   clean-files  Delete local artifacts in this folder: runtime\, __pycache__, .pytest_cache.
echo   help         Show this text.
echo.
echo Options for plan and run (they override config.yaml)
echo   --start "2026-09-30 13:00:00"  --end "2026-09-30 15:00:00"   UTC window of the replay
echo   --symbols GOLD,DE40            --timeframes M10,M20          what to replay
echo   --delay 0.2                    real seconds between ticks (0 = maximum speed)
echo   --speed 300                    simulated seconds per real second (instead of --delay)
echo   plan only:  --show 12 (print the first 12 ticks)   --export schedule.csv
echo   all modes:  --config path\to\config.yaml
echo.
echo Examples
echo   dps.bat plan --show 12
echo   dps.bat run --delay 0.2
echo   dps.bat run --symbols GOLD --timeframes M10,M20 --start "2026-09-30 13:00:00" --end "2026-09-30 15:00:00"
echo   dps.bat status
echo   dps.bat stop
echo   dps.bat clean
echo   dps.bat clean-files
if not defined HELP_EXIT set "HELP_EXIT=0"
exit /b %HELP_EXIT%
