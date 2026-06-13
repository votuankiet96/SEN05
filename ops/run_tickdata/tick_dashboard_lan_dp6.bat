@echo off
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%SCRIPT_DIR%tick_dashboard.ps1" -BindHost 0.0.0.0 -OpenHost 10.11.12.6 -Port 8061
