# WS Live Ops

This folder contains the dedicated Windows ops layer for `data_provider/apps/ws_live.py`.

## Files

- `ws_live_supervisor.ps1` runs `ws_live.py` hidden and restarts it after exit.
- `ws_live_watchdog.ps1` runs every minute and restarts the supervisor task if it disappears.
- `install_ws_live_tasks.ps1` registers the hidden Scheduled Tasks.
- `ws_live_status.ps1` prints task, process, PID file, DB lock, and heartbeat status.
- `ws_live_log_viewer.ps1` tails logs in realtime.
- `ws_live_log_viewer.bat` is the double-click entrypoint for realtime app logs.
- `lib/WsLiveOps.psm1` contains shared helper functions.

## Install On DP6

Run from elevated PowerShell:

```powershell
Set-Location C:\Share\SEN05_Autotrading
powershell -ExecutionPolicy Bypass -File ops\run_wslive\install_ws_live_tasks.ps1 -Force
```

For a task that can run before an interactive login, use:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_wslive\install_ws_live_tasks.ps1 -Force -RunWhetherLoggedOnOrNot -TaskUser "WIN-B8609EA108T\Administrator"
```

## View Logs

Double-click:

```text
ops\run_wslive\ws_live_log_viewer.bat
```

Or run:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_wslive\ws_live_log_viewer.ps1
```

Closing the viewer does not stop `ws_live`; it only tails the log.
