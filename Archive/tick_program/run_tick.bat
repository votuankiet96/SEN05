@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0initial_setup\launcher\tick_launcher.ps1" %*
