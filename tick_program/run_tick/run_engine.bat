@echo off
rem Launcher for the "SEN05 Tick Program Engine" Scheduled Task.
rem
rem Does NOT run tick_program.exe directly as the task's own Action.
rem Confirmed live (2026-09-02): a --console-subsystem exe launched by Task
rem Scheduler (S4U logon, no interactive session) with no stdio redirected
rem at all hangs indefinitely before writing a single log line -- the
rem process stays alive and "Responding" per Get-Process, but produces
rem zero output forever (reproduced repeatedly, including after fixing the
rem app's own _interactive() check, so the hang is below the Python layer
rem -- most likely stdio-handle setup in the C runtime itself finding no
rem real console to attach to). Redirecting stdin/stdout/stderr here, before
rem the exe ever runs, avoids that state entirely and is the actual fix.
rem tick_program.exe's real logging goes to runtime\logs\tick_engine.log
rem regardless (its own structured log writer, not stdout) -- discarding
rem stdout/stderr here loses nothing operationally.
cd /d "%~dp0"
tick_program.exe < NUL > NUL 2>&1
