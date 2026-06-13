@echo off
setlocal

set "OG8_USER=administrator"
set "OG8_HOST=10.11.12.8"
set "SERVICE=sen05-signal-watcher-combo-h4.service"

title OG8 Restart Signal Watcher
echo Restarting %SERVICE% on %OG8_HOST% ...
ssh -t %OG8_USER%@%OG8_HOST% "sudo systemctl restart %SERVICE% && sudo systemctl status %SERVICE% --no-pager --full; exec bash"

echo.
echo SSH session closed.
pause
