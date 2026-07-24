# State: ops/run_tickdata (VM-DP Operations)

> Living document. Update after every session that modifies Task Scheduler or PowerShell ops.
> Last updated: 2026-06-13
> **These scripts run on VM-DP (Windows Server 2022) only.**

---

## Scheduled Tasks (VM-DP Task Scheduler)

All tasks are under path `\SEN05\`:

| Task Name | Script | Schedule | Function |
|-----------|--------|----------|----------|
| `SEN05_TickLive_Supervisor` | `tick_live_supervisor.ps1` | At system startup | Loop: start tick ingest, monitor, restart on crash |
| `SEN05_TickLive_Watchdog` | `tick_live_watchdog.ps1` | Every 60s | Check supervisor alive, stale feed detection/restart |
| `SEN05_Tick_Check` | `tick_check.ps1` | Every 5 min | Run `checker.py` → freshness report → Discord if stale |
| `SEN05_Tick_ShortRepair` | `tick_short_overlap_repair.ps1` | Every 5 min | Dedup ticks in recent overlap window |

---

## Key Scripts

| Script | Purpose |
|--------|---------|
| `tick_live_watchdog.ps1` | Watchdog logic: supervisor check + stale feed grace (900s) |
| `tick_status.ps1` | Quick status: process, PID file, heartbeat, DB lock, spool |
| `tick_dashboard.ps1` | Formatted live dashboard view |
| `tick_short_overlap_repair.ps1` | Dedup ticks in short overlap window |
| `tick_daily_overlap_backfill.ps1` | Full-day overlap dedup (run manually) |
| `install_tick_tasks.ps1` | **⚠ Modifies Task Scheduler** — installs/updates all tasks |
| `lib/TickDataOps.psm1` | PowerShell module: all helper functions |

---

## PowerShell Module (TickDataOps.psm1) — Exported Functions

| Function | Purpose |
|----------|---------|
| `Get-TickDataPaths` | Returns all path constants (log files, PID, heartbeat, etc.) |
| `Get-TickLiveProcesses` | Win32_Process scan for running tick ingest |
| `Get-TickDataCheckReport` | Runs `checker.py --json`, parses JSON report |
| `Request-TickLiveGracefulShutdown` | Writes `shutdown_requested=1` to DB lock Payload |
| `Repair-TickDataStaleRuntime` | Removes stale PID + clears DB lock (if no live process) |
| `Update-TickDataSupervisorHeartbeat` | Writes JSON heartbeat file |
| `Get-TickDataSupervisorHeartbeat` | Reads heartbeat JSON |
| `Get-TickDataDbLockStatus` | Inspects SEN.ActiveTask for `tick_live_runtime` |
| `Clear-TickDataDbLock` | Deletes lock row (used by stale repair) |

---

## Runtime Files (auto-created at `ops/run_tickdata/runtime/`)

| File | Content | Purpose |
|------|---------|---------|
| `tick_live_supervisor.heartbeat.json` | `{timestamp_utc, state, child_exit_code, process_id, host}` | Supervisor alive check |
| `tick_live_no_process_since.txt` | UTC datetime string | Grace window start for "no process" |
| `tick_live_stale_feed_since.txt` | UTC datetime string | Grace window start for stale feed |
| `tick_live_stale_check_after.txt` | UTC datetime string | Next stale check time |
| `logs/tick_live_watchdog.log` | `[ts] [LEVEL] message` | Watchdog log |
| `logs/tick_live_supervisor.log` | `[ts] [LEVEL] message` | Supervisor log |
| `logs/tick_live_console.log` | Python process stdout | Tick ingest console output |
| `logs/tick_dashboard.log` | Dashboard script log | Dashboard access log |

---

## Watchdog Logic (tick_live_watchdog.ps1)

```
Every 60s:
  1. Check supervisor task exists → if not found: exit 2
  2. If task.State != "Running" → Start-ScheduledTask → exit 0
  3. If live Python processes found:
       → clear no-process grace file
       → Test-AndRepairStaleFeed (every 300s):
           run checker.py --json
           count stale_heartbeat findings
           if stale > 0 for > 900s → Request-TickLiveGracefulShutdown
     → log "healthy" → exit 0
  4. If no live Python processes:
       → start/extend no-process grace (60s window)
       → if > 60s: Stop + Start supervisor task
```

---

## Supervisor Logic (tick_live_supervisor.ps1)

```
While true:
  Repair-TickDataStaleRuntime (clean PID + DB lock if stale)
  Start tick ingest: python -m data_provider.apps.ctrader_ftmo_tick live
  Monitor process until exit
  Update heartbeat on each loop
  On exit: log exit code, sleep 10s, restart
```

---

## Known Issues

1. **Log rotation not implemented** — log files grow indefinitely. Manual cleanup or external log rotation needed on VM-DP.

2. **Short overlap repair lock** — `tick_short_overlap_repair.lock` prevents concurrent runs. If repair script crashes mid-run, lock file may persist. Manual delete needed: `runtime/tick_short_overlap_repair.lock`.

3. **install_tick_tasks.ps1 is destructive** — rewrites task definitions. Do not run during market hours.

---

## Recent Changes

| Date | Change |
|------|--------|
| 2026-06-13 | Documented in `docs/ARCHITECTURE.md` §7 |
| 2026-06-13 | `tick_short_overlap_repair.ps1` added (new file, untracked) |
| Prior | Tick ops refactored, watchdog stale feed logic added (commit `95ba3745a`) |
