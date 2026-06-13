@echo off
setlocal

set "OG8_USER=administrator"
set "OG8_HOST=10.11.12.8"
set "SEN05_DIR=/srv/sen05"
set "SEN05_VENV=~/.venvs/sen05-og/bin/activate"

title OG8 SEN05 Shell
echo Connecting to %OG8_USER%@%OG8_HOST% and opening SEN05 runtime shell ...
ssh -t %OG8_USER%@%OG8_HOST% "cd %SEN05_DIR% && source %SEN05_VENV% && exec bash"

echo.
echo SSH session closed.
pause
