@echo off
setlocal

set "OG8_USER=administrator"
set "OG8_HOST=10.11.12.8"
set "SERVICE=sen05-signal-watcher-combo-h4.service"

title OG8 Signal Watcher Status
ssh -t %OG8_USER%@%OG8_HOST% "sudo systemctl status %SERVICE% --no-pager --full; echo; echo 'Recent logs:'; sudo journalctl -u %SERVICE% -n 80 --no-pager; exec bash"

echo.
echo SSH session closed.
pause
