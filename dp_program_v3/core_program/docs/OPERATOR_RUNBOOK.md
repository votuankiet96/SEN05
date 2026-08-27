# DP Program V3 Operator Runbook

The two foreground batch files below remain the way to operate live/backfill
by hand (manual runs, debugging, first-time setup). For unattended 24/7
operation, also install the Scheduled Task supervision described in
[Scheduled Task Supervision](#scheduled-task-supervision) — it restarts a
crashed service and pages Discord if either service goes down, which a
foreground window left open cannot do on its own once the session that
started it ends.

## Before Operating

Do not print, paste, or commit `Config.yaml`, tokens, cookies, passwords,
connection strings, runtime spool, or auth cache.

Run read-only checks:

```powershell
python -m dp_program settings
python -m dp_program check-sql
python -m dp_program doctor
```

Expected settings highlights:

- `live_pairs = 165`
- `backfill_pairs = 555`
- `live_interval_minutes = 5`
- `live_bars_per_request = 3`
- `closed_candles_only = true`

## Start Live

```powershell
.\run_live.bat check
.\run_live.bat start
```

Keep the window open. Live writes:

- `runtime/run/state_live.json`
- `runtime/logs/dp_program_live.log`

Stop live gracefully from another window:

```powershell
.\run_live.bat stop
```

## Start Backfill

```powershell
.\run_backfill.bat check
.\run_backfill.bat start
```

Keep the window open. Backfill writes:

- `runtime/run/state_backfill.json`
- `runtime/logs/dp_program_backfill.log`

Stop backfill gracefully from another window:

```powershell
.\run_backfill.bat stop
```

## Status

```powershell
python -m dp_program status --mode live
python -m dp_program status --mode backfill
```

Healthy status requires:

- `status = running`
- `process_alive = true`
- heartbeat age under 600 seconds

## Auth

Inspect auth readiness:

```powershell
python -m dp_program auth status
```

Force refresh only when operating intentionally:

```powershell
python -m dp_program auth refresh
```

Auth is fail-closed. If all refresh paths fail, the engine stops instead of
running as guest.

## Backfill Schedule

Backfill schedule is configured in `backfill.schedule_utc`, currently:

```yaml
["11:11", "15:15", "19:19", "23:23", "03:03", "07:07"]
```

The scheduler evaluates these slots across UTC midnight. Backfill yields to
live when live is active, uncertain, or near its next cycle. A dead live PID
does not block backfill forever.

## Logs

- live service: `runtime/logs/dp_program_live.log`
- backfill service: `runtime/logs/dp_program_backfill.log`

Look for structured fields:

- `event=SERVICE_STARTED`
- `event=LIVE_CYCLE_COMPLETED`
- `event=BACKFILL_SCHEDULED`
- `event=PAIR_FAILED`
- `risk=HIGH` or `risk=CRITICAL`

## Scheduled Task Supervision

`scripts/windows/install_task.ps1` registers 3 Windows Scheduled Tasks under
`\SEN05\`:

- **SEN05 DP Program Live** / **SEN05 DP Program Backfill** — trigger
  `AtStartup`, run `run_live.bat start` / `run_backfill.bat start`, restart up
  to 999 times at 1-minute intervals if the process exits with a non-zero
  code. A graceful `dp_program stop` exits 0, so it is never fought; only a
  real crash triggers a restart.
- **SEN05 DP Program Watchdog** — runs `scripts/windows/watchdog.py` every 5
  minutes (`-WatchdogIntervalMinutes` to change). Read-only: it checks
  `service_status()` for both roles and, only on the transition into
  unhealthy (not on every poll), sends one Discord alert via
  `discord_report.send_watchdog_alert()`. It never restarts or signals the
  engine processes itself — recovery is Task Scheduler's job, alerting is
  the watchdog's.

Install (run once per machine, or after changing the batch files):

```powershell
powershell -File scripts\windows\install_task.ps1
```

The installer runs `dp_program doctor` first and aborts without touching any
task if it fails. It does not start the Live/Backfill tasks by default — if
those services are already running under a manually-started process, pass
`-StartServices` only on a fresh machine where nothing is running yet
(otherwise the task would just hit the engine's single-instance lock and
fail cleanly, which is harmless but pointless). Pass `-StartWatchdog` to
start watchdog polling immediately instead of waiting for its first
scheduled tick.

Verify registration:

```powershell
Get-ScheduledTask -TaskPath \SEN05\
```

## Validation Before Code Deployment

```powershell
python -m pytest test/
Get-ChildItem src/dp_program -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
python -m dp_program check-sql
python -m dp_program doctor
```

If a validation gate fails, do not start the new runtime. Keep the evidence and
roll back to the last known commit.

## Rollback

Use Git to return to the approved rollback commit, then run validation again.
Do not delete SQL data, truncate staging tables, reboot the host, or rotate
credentials as part of normal rollback.
