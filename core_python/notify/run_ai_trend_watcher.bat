@echo off
cd /d "%~dp0..\.."
set PYTHONUNBUFFERED=1
if not exist logs mkdir logs

:loop
.venv\Scripts\python.exe -u -m core_python.notify.signal_watcher ^
  --strategy ai_trend ^
  --symbols GOLD,BTCUSD,US30,UK100,J225,HK50,DE40 ^
  --tf H3,M45 ^
  --bars 1000 ^
  --state-path core_python\notify\ai_trend_state.json ^
  --log-file logs\ai_trend_watcher.log ^
  --no-export

echo [%date% %time%] ai_trend watcher stopped; restarting in 10 seconds...
echo [%date% %time%] ai_trend watcher stopped; restarting in 10 seconds... >> logs\ai_trend_watcher.log
timeout /t 10 /nobreak >nul
goto loop
