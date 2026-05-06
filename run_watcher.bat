@echo off
cd /d C:\Users\Administrator\Documents\GitHub\SEN05
set PYTHONUNBUFFERED=1
if not exist logs mkdir logs
:loop
.venv\Scripts\python.exe -u -m core_python.notify.signal_watcher --log-file logs\watcher.log
echo [%date% %time%] watcher stopped; restarting in 10 seconds...
echo [%date% %time%] watcher stopped; restarting in 10 seconds... >> logs\watcher.log
timeout /t 10 /nobreak >nul
goto loop
