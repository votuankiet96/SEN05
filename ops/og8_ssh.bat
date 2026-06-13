@echo off
setlocal

set "OG8_USER=administrator"
set "OG8_HOST=10.11.12.8"

title OG8 SSH
echo Connecting to %OG8_USER%@%OG8_HOST% ...
ssh %OG8_USER%@%OG8_HOST%

echo.
echo SSH session closed.
pause
