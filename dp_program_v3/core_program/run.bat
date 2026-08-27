@echo off
setlocal
cd /d "%~dp0"

rem Top-level launcher: dispatches into run_backfill.bat / run_live.bat /
rem the chart server, either by argument (run.bat live|backfill|chart) or
rem via an interactive menu (run.bat with no argument). Any extra
rem arguments after the mode are forwarded as-is, e.g.
rem "run.bat backfill check" -> run_backfill.bat check.
rem
rem "Redis" in the Live Mode label is a placeholder for a planned future
rem component (pushing live candles to Redis on VM-OG for core_python to
rem consume) -- run-live itself still only writes to SQL Server today,
rem same as backfill.

if "%~1"=="" goto menu
if /i "%~1"=="live" goto live
if /i "%~1"=="backfill" goto backfill
if /i "%~1"=="chart" goto chart
goto usage

:menu
echo.
echo DP Program - Chon mode
echo   1. Backfill Mode (SQL)
echo   2. Live Mode (Redis - tuong lai)
echo   3. Chart Mode
echo.
choice /c 123 /n /m "Select [1-3]: "
if errorlevel 3 goto chart
if errorlevel 2 goto live
goto backfill

:backfill
call "%~dp0run_backfill.bat" %2 %3 %4
exit /b %ERRORLEVEL%

:live
call "%~dp0run_live.bat" %2 %3 %4
exit /b %ERRORLEVEL%

:chart
call "%~dp0run_live.bat" chart
exit /b %ERRORLEVEL%

:usage
echo Usage: %~nx0 [live^|backfill^|chart] [args...]
echo   run.bat backfill check   -^> run_backfill.bat check
echo   run.bat live start       -^> run_live.bat start
echo   run.bat chart            -^> opens the read-only chart
exit /b 2
