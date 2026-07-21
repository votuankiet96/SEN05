# SEN05 Data Provider — VM-DP6 Operator Runbook

## 1. Production identity

DP Program production is hosted on **VM-DP6**. Its 24/7 Windows wrapper is the
Scheduled Task below; it is not an NSSM/SCM Windows Service.

```text
TaskPath : \SEN05\
TaskName : SEN05 DP Program 24x7
Account  : Administrator (existing TradingView browser profile owner)
Trigger  : VM boot + 45 seconds
Retry    : every 1 minute, up to 999 attempts
```

The approved live universe is fixed at **11 symbols** (`Indice,Metal,Crypto`),
15 timeframes each, or **165 symbol/timeframe sessions per batch**. All 26
FOREX symbols remain historical-only. Historical backfill runs at **11:00 and
22:00 UTC**; UTC has no daylight-saving transition.

Do not install or configure NSSM during this production release. Any future
wrapper migration is a separate change decision.

## 2. Release and state layout

Production code is never imported from the mutable checkout after promotion.

```text
C:\Share\dp_program                 deployment source checkout only
C:\dp_releases\<sha>_<utc>         immutable code + release-local virtualenv
C:\dp_program\current              junction to active immutable release
C:\Share\dp_program\config         persistent operator config and secrets
C:\Share\dp_program\runtime        persistent logs/cache/spool/outboxes/state
```

Each release's `config` and `runtime` paths are junctions to the two persistent
directories above. This preserves the TradingView browser cache, live spool,
Discord outbox and logs across an atomic code switch while keeping executable
Python files immutable. `RELEASE_MANIFEST.json` and `RELEASE_REQUIREMENTS.txt`
inside the release identify the exact commit and installed dependency set.

The Scheduled Task action after promotion must be:

```text
Execute          C:\dp_program\current\.venv\Scripts\python.exe
Arguments        -m core_engine run --live
WorkingDirectory C:\dp_program\current
```

## 3. Configuration contract

Operator configuration is `C:\Share\dp_program\config\dp_provider.env`.
Never paste its contents into chat, tickets, or reports. The production guard
values are:

```env
LIVE_ASSET_TYPES=Indice,Metal,Crypto
EXPECTED_LIVE_SYMBOLS=11
HISTORICAL_BACKFILL_ENABLED=1
HISTORICAL_BACKFILL_UTC=11:00,22:00
```

`EXPECTED_LIVE_SYMBOLS` defaults to 11 if omitted. Startup fails rather than
silently accepting a different live universe. Verify the resolved values:

```powershell
cd C:\dp_program\current
.\.venv\Scripts\python.exe -m core_engine settings --json
```

Acceptance fields are `expected_live_symbols=11`,
`resolved_live_symbols=11`, and `symbol_timeframe_sessions=165`.

## 4. SQL and Redis data contracts

SQL Server database `SEN05_AutoTrading` is the durable system of record.
`DWH.usp_LoadDirect` must advertise `DPContractVersion=3`, and
`SEN.ActiveTask` must contain both `OwnerId` and `Fence`.

Redis/OG in `both` mode is an **eventually consistent candle snapshot**:

- SQL remains authoritative for recovery, reconciliation and audit.
- Redis delivery is not guaranteed lossless for every event.
- On Redis recovery, DP Program reseeds a bounded snapshot from SQL.
- OG must not treat Redis Stream continuity as proof that no candle was lost;
  it must tolerate reseed/duplicate snapshot events.
- `DP_STORAGE_MODE=redis` is forbidden and fails startup. Use `sql` or `both`.

This is the approved production contract; a durable Redis outbox is not a GO
prerequisite under this contract.

## 5. Controlled promotion on VM-DP6

Run from an elevated PowerShell prompt **on VM-DP6**, never from the mapped
`Z:` drive on another Windows kernel:

```powershell
cd C:\Share\dp_program
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\windows_task\promote_vm_dp6.ps1
```

The script hard-gates this sequence:

1. Capture hostname, commit, Scheduled Task XML/state, doctor and data-health.
2. Create a full `COPY_ONLY, CHECKSUM` database backup and run
   `RESTORE VERIFYONLY ... WITH CHECKSUM`.
3. Request a graceful supervisor stop and abort if it remains alive after
   120 seconds; it does not force-kill before migration.
4. Deploy usp_LoadDirect v3, lock fencing, and the transactional US500/D1
   unsupported-calendar archive migration.
5. Verify contract version 3, two fencing columns, 2,231 archived rows and
   zero unsupported US500/D1 staging rows.
6. Run `reconcile-fact --apply --json` and require all three fields to be zero:
   `supported_missing_fact_rows`, `supported_mismatched_fact_rows`, and
   `unsupported_calendar_rows`.
7. Build a release-local virtualenv, install non-editably, compile, run the
   full pytest suite, validate 11/165, switch `current`, update the existing
   Scheduled Task action without changing its account, and start it.
8. Require supervisor state `running`, a live child PID, a passing doctor,
   and the unchanged 11/165 settings.

Evidence is written under:

```text
C:\Share\dp_program\runtime\deploy\go_<UTC>_<SHA>\
C:\Share\dp_program\runtime\deploy\release_<SHA>_<UTC>\
```

If any post-migration gate fails, the promotion script attempts reverse-order
rollback: stop the new runtime, restore the old Task action/junction, restore
the verified full SQL backup, and start the prior Scheduled Task. Inspect
`promotion_failure.txt`, `rollback_result.txt`, and, if present,
`rollback_failure.txt` before any second attempt.

## 6. Daily Scheduled Task operations

Check wrapper state and last result:

```powershell
Get-ScheduledTask -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7' |
  Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7'
```

Start:

```powershell
Start-ScheduledTask -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7'
```

Graceful stop:

```powershell
cd C:\dp_program\current
.\.venv\Scripts\python.exe -m core_engine stop --reason operator
```

Wait for the Task to leave `Running`. `Stop-ScheduledTask` is reserved for an
approved maintenance/fault window after graceful stop has failed or for an
explicit recovery test.

Readiness and reconciliation:

```powershell
cd C:\dp_program\current
.\.venv\Scripts\python.exe -m core_engine status --json
.\.venv\Scripts\python.exe -m core_engine doctor --json
.\.venv\Scripts\python.exe -m core_engine data-health --json
.\.venv\Scripts\python.exe -m core_engine reconcile-fact --json
```

## 7. Logs and 30-minute production follow-up

Primary files are under `C:\Share\dp_program\runtime`:

```text
logs\system\system.log
logs\system\errors.log
logs\system\activity.log
logs\system\discord.log
logs\operation\live_fetching.log
logs\operation\historical_pulling.log
logs\operation\live_fetching_summary.jsonl
run\backend_engine_state.json
run\ws_live_state.json
spool\live_spool.sqlite3
```

For the approved shortened follow-up, sample for 30 minutes and then inspect
the complete interval. Record Fact watermark/freshness, spool pending/staged
and oldest age, live/historical restart counts, Discord sent/queued counts,
`ws_orphaned_threads`, `ws_wedged_group_recycles`, RSS, thread count and handle
count. Thirty minutes is accepted by the owner in place of the earlier
24–72-hour soak; it does not prove passage through both historical schedule
slots unless one actually occurs in the observed interval.

Gap health is based only on unresolved gaps during expected market-open time.
Weekend/holiday timeline gaps are classified as market closure, not repairs.

## 8. Discord webhook rotation

Rotation requires a Discord channel administrator:

1. Create a new webhook in the approved channel without posting its URL.
2. Replace only `DISCORD_WEBHOOK_URL` in the production env file.
3. Start/restart through the Scheduled Task and send the approved test alert.
4. Confirm `discord.sent` with HTTP 200/204 and an empty critical outbox.
5. Revoke the old webhook in Discord, then record timestamps only—never URLs.

Both successful delivery and old-webhook revocation are GO gates.

## 9. Approved socket-stall recovery test

Only during a confirmed maintenance window, inject one real callback stall in
group 0 by creating this exact one-shot marker:

```powershell
Set-Content `
  C:\Share\dp_program\runtime\run\fault_inject_ws_callback_stall_g0.request `
  'STALL_ONCE' -NoNewline
```

The first live callback atomically renames it to an `.active.<pid>.<UTC>`
evidence file and deliberately leaves one daemon WebSocket callback stuck.
Expected behavior is: fetch timeout → forced raw-socket close →
`ws_orphaned_threads` increments → no second socket is opened for that group →
three consecutive classifications → `ws_wedged_group_recycles` increments →
live child exits non-zero → supervisor restarts a fresh child. The request is
one-shot, so the replacement cannot enter a fault loop.

Do not create this marker outside the confirmed window.

## 10. Healthy production criteria

- Scheduled Task is `Running` and its action resolves through `current`.
- `RELEASE_MANIFEST.json` identifies the promoted commit.
- Supervisor and live state heartbeats advance; live PID is alive.
- Startup log reports 11 symbols and 165 sessions.
- Fact watermark advances when at least one approved market is open.
- Reconcile's three acceptance buckets are zero.
- Live spool has no old pending/staged lease backlog.
- Discord alert outbox drains after successful HTTP 200/204 delivery.
- Resource counts do not increase monotonically during the observation window.
- Historical gaps remaining after repair are explainable by market closure.
