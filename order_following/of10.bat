@echo off
rem OF (Order Follower) - luon cd vao src\ truoc, vi config.yaml dung duong dan tuong doi.
rem Dung full path toi python.exe de khong phu thuoc PATH cua moi truong goi no (vd Task Scheduler).
rem   of.bat            chay that (vao vong lap chinh)
rem   of.bat check      kiem tra ket noi/cau hinh roi thoat, khong dat lenh gi
rem   of.bat close-all  KHAN CAP: dong het vi the + huy het lenh cho cua combo roi thoat
set PYTHON_EXE="C:\Program Files\Python312\python.exe"
cd /d "%~dp0src"
if "%~1"=="check" goto check
if "%~1"=="close-all" goto closeall
%PYTHON_EXE% main.py
goto :eof

:check
%PYTHON_EXE% main.py --check
goto :eof

:closeall
%PYTHON_EXE% main.py --close-all
goto :eof