# SEN05 DP Program

This source tree is terminal-first. The dashboard and exe build flow are
intentionally out of scope until DP Program is stable as a terminal application.

## Runtime Model

`core_engine` is the application entrypoint. It supervises two independent data
engines:

- Historical OHLCV: `core_engine.historical_pulling`
- Live OHLCV: `core_engine.live_fetching`

In production, start the DP Program supervisor:

```powershell
cd C:\Users\ADMIN\Desktop\dp_program
python -m core_engine run
```

The supervisor can auto-start live fetching, schedule daily historical backfill,
monitor heartbeats, restart stale live processes, and shut down gracefully.

## First Setup

```powershell
cd C:\Users\ADMIN\Desktop\dp_program
python .\initial_setup\install_python_deps.py
copy .\config\dp_provider.env.example .\config\dp_provider.env
```

Edit `config/dp_provider.env` before real operation. Keep this file private.

## Operator Commands

Open the terminal launcher:

```powershell
cd C:\Users\ADMIN\Desktop\dp_program
.\run_dp.bat
```

The launcher keeps the menu open while long-running jobs are started in their
own PowerShell windows.

```powershell
# Readiness checks
python -m core_engine doctor
python -m core_engine doctor --deep-auth

# Current state
python -m core_engine status
python -m core_engine status --json
python -m core_engine data-health

# DP Program supervisor
python -m core_engine run
python -m core_engine stop

# Run live directly for testing
python -m core_engine live
python -m core_engine live --smoke-seconds 120

# Run historical directly
python -m core_engine historical --mode auto --dry-run
python -m core_engine historical --mode gap
python -m core_engine historical --mode full
python -m core_engine historical --mode reset --dry-run

# TradingView auth
python -m core_engine auth status
python -m core_engine auth diagnose
python -m core_engine auth login --timeout-sec 900

# Runtime cleanup
python -m core_engine clean-runtime --days 30
```

## Key Config

DP Program supervisor:

```env
WS_LIVE_AUTO_START=1
HISTORICAL_BACKFILL_ENABLED=1
HISTORICAL_BACKFILL_UTC=06:00
HISTORICAL_BACKFILL_MODE=gap
BACKEND_LIVE_RESTART_ON_EXIT=1
BACKEND_LIVE_RESTART_ON_STALE=1
BACKEND_LIVE_STALE_MINUTES=15
BACKEND_LOG_RETENTION_DAYS=30
```

`WS_LIVE_AUTO_START=1` means `python -m core_engine run` will start live fetching
automatically. For 24/7 operation on DP6, use the Windows Service scripts in
`initial_setup\windows_service` after the terminal smoke tests are clean.

## Logs And State

Runtime files are written under `runtime/`.

System logs:

- `runtime/logs/system/system.log` - supervisor, scheduler, restart, cleanup.
- `runtime/logs/system/activity.log` - operator timeline from terminal start to finish.
- `runtime/logs/system/auth.log` - TradingView token/cookie/browser auth flow.
- `runtime/logs/system/discord.log` - Discord delivery result and retry status.
- `runtime/logs/system/subprocess_debug.log` - debug-only child stdout/stderr capture.

Operation logs:

- `runtime/logs/operation/live_fetching.log`
- `runtime/logs/operation/historical_pulling.log`
- `runtime/logs/operation/data_warehouse.log`
- `runtime/logs/operation/live_fetching_summary.jsonl`
- `runtime/logs/operation/historical_pulling_summary.jsonl`

Runtime state:

- `runtime/run/backend_engine_state.json`
- `runtime/run/ws_live_state.json`

These files are operational output and should not be committed.

## Production Notes

- Live and historical run as separate processes when started by the supervisor.
- TradingView auth refresh is coordinated so processes do not renew credentials
  at the same time.
- Historical backfill uses database locks and yields around live batch windows.
- Stop requests are cooperative first; force termination happens only after the
  configured grace period.
- The terminal DP Program is the foundation. A dashboard should only observe or
  request actions after this layer is proven stable.
