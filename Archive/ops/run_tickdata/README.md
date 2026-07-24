# Tick Data Ops

Dedicated Windows ops layer for `data_provider/tick_data`.

This folder is isolated from `ops/run_wslive`, the OHLCV checker, and the data pipeline.

## Files

- `tick_live_supervisor.ps1` runs `python -m data_provider.apps.ctrader_ftmo_tick live` hidden and restarts it after exit.
- `tick_live_watchdog.ps1` runs every minute and restarts the supervisor task if no live process is visible for 60 seconds.
- `install_tick_tasks.ps1` registers the hidden Scheduled Tasks.
- `tick_check.ps1` runs read-only health checks and sends Discord alerts when status is not OK.
- `tick_status.ps1` prints task, process, PID file, DB lock, heartbeat, and tick health.
- `tick_log_viewer.ps1` tails `data_provider/runtime/logs/ctrader_ftmo_tick.log`.
- `tick_log_viewer.bat` is the double-click log viewer.
- `tick_dashboard.ps1` starts the isolated tick dashboard on `http://127.0.0.1:8061/`.
- `tick_dashboard.bat` is the double-click dashboard launcher.
- `tick_initial_backfill_max.ps1` probes cTrader history depth, then optionally backfills from the deepest available point.
- `tick_daily_overlap_backfill.ps1` re-fetches a recent overlap window, reports net-new tick rows, and sends Discord start/error/done notifications.
- `tick_short_overlap_repair.ps1` re-fetches a short recent overlap window in one cTrader session for near-real-time gap repair.
- `lib/TickDataOps.psm1` contains shared helper functions.

## Install On DP6

Run from elevated PowerShell:

```powershell
Set-Location C:\Share\SEN05_Autotrading
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\install_tick_tasks.ps1 -Force
```

For a task that can run before interactive login:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\install_tick_tasks.ps1 -Force -RunWhetherLoggedOnOrNot -TaskUser "WIN-B8609EA108T\Administrator"
```

## First Max Backfill

Plan only:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_initial_backfill_max.ps1
```

Execute the plan:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_initial_backfill_max.ps1 -Apply
```

The script probes each matched symbol up to `-MaxDays 20000` by default and refines the deepest available point before launching per-symbol backfill jobs.

## Daily Overlap Backfill

Run a conservative 48-hour repair window:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_daily_overlap_backfill.ps1
```

Useful options:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_daily_overlap_backfill.ps1 `
  -LookbackHours 48 `
  -ChunkHours 6 `
  -SafetyLagMinutes 5 `
  -RequestTimeoutSeconds 180
```

The script sends Discord notifications when the run starts, fails, and completes.
The completion report includes:

- `historical_added`: net-new historical tick rows that appeared in `tick.*` after the overlap run.
- `bid_added` / `ask_added`: side-specific new tick rows.
- `new_Nm_buckets`: number of N-minute time buckets that had no tick before the run and at least one tick after the run.

Reports are written to:

```text
ops\run_tickdata\runtime\reports\tick_overlap_<run>_report.json
ops\run_tickdata\runtime\logs\tick_daily_overlap_report.jsonl
```

## Short Overlap Repair

Run a fast recent repair window manually:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_short_overlap_repair.ps1 `
  -LookbackMinutes 15 `
  -SafetyLagMinutes 2
```

The scheduled task installed by `install_tick_tasks.ps1` is:

```text
\SEN05\SEN05_TickData_ShortRepair
```

It runs every 5 minutes by default, uses one cTrader session per run, and suppresses
per-symbol backfill Discord messages. It sends a throttled Discord summary, default
once per hour, and sends throttled error alerts if the repair job fails.

## Live 24/7 Reports

The live service sends Discord notifications for important events:

- live ingest started
- cTrader subscription active
- disconnect and reconnect scheduled
- ingest error
- SQL write failed and ticks were spooled
- live ingest stopped

It also writes a compact heartbeat line to `data_provider/runtime/logs/ctrader_ftmo_tick.log`
every `CTRADER_FTMO_TICK_HEARTBEAT_LOG_SECONDS` seconds, default `300`.

The live service sends a periodic Discord data report every
`CTRADER_FTMO_TICK_DISCORD_REPORT_SECONDS` seconds, default `3600`.
The report includes received tick records, SQL inserted rows, spooled rows,
spool backlog, active symbols, last tick UTC, and top symbols for the window.
If no tick records are received during the window, the report is sent as a
warning.

The watchdog also checks the tick health report periodically. If the live process
is still running but expected-active symbols remain stale for 15 minutes, it writes
a graceful shutdown signal to the tick live runtime lock. The live process flushes
and exits, then the supervisor starts a fresh live process.

## View Logs

Double-click:

```text
ops\run_tickdata\tick_log_viewer.bat
```

Closing the viewer does not stop tick live.

## Dashboard

Double-click:

```text
ops\run_tickdata\tick_dashboard.bat
```

Or run:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_dashboard.ps1 -Port 8061
```
