# SEN05 DP Program Operator Runbook

This runbook is the owner of production operation details. Architecture and
engineering rationale live in the other canonical docs.

## Production Identity

VM-DP6 runs DP Program through Scheduled Task:

```text
TaskPath : \SEN05\
TaskName : SEN05 DP Program 24x7
Command  : python -m core_engine run --live
```

This deployment is not a Windows Service and is not managed by NSSM. The
`scripts/windows_service/` scripts are legacy/unsupported for the current
deployment model; do not run them unless Kiệt approves a separate wrapper
migration.

## Verify Paths

As verified on 2026-07-24, the physical repository root is:

```text
C:\Users\Administrator\Desktop\dp_program
```

The Scheduled Task working directory is:

```text
C:\Share\dp_program
```

At that timestamp `C:\Share\dp_program` was a junction to the physical root.
Re-check before treating this as evidence:

```powershell
Get-Item -LiteralPath 'C:\Users\Administrator\Desktop\dp_program' |
  Select-Object FullName,Attributes,LinkType,Target
Get-Item -LiteralPath 'C:\Share\dp_program' |
  Select-Object FullName,Attributes,LinkType,Target
```

Use the Scheduled Task working directory for operator commands:

```powershell
cd C:\Share\dp_program
```

## Verify Scheduled Task

```powershell
Get-ScheduledTask -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7' |
  Select-Object TaskPath,TaskName,State

Get-ScheduledTaskInfo -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7'

Get-ScheduledTask -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7' |
  Select-Object -ExpandProperty Actions

Get-ScheduledTask -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7' |
  Select-Object -ExpandProperty Principal

Get-ScheduledTask -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7' |
  Select-Object -ExpandProperty Settings
```

Production Python verified on 2026-07-24:

```text
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
```

Re-check:

```powershell
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
& $python -c "import sys, core_engine; print(sys.executable); print(core_engine.__file__)"
```

## Git Baseline

Before changing files:

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline -10
```

Do not edit `.git` manually. If Git is not available, stop before mutating or
deleting documentation.

## Configuration Checks

Never paste `config\dp_provider.env` into chat or tickets.

Use the non-secret settings view:

```powershell
cd C:\Share\dp_program
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
& $python -m core_engine settings --json
```

Expected stable contract fields:

- `symbols_total=37`
- `resolved_live_symbols=11`
- `symbol_timeframe_sessions=165`
- `storage_mode=sql`
- `operator_config.ok=true`

Redis/OG snapshot fields are expected to remain disabled unless a reviewed
release explicitly enables them.

## Readiness And Status

```powershell
cd C:\Share\dp_program
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'

& $python -m core_engine doctor --json
& $python -m core_engine status --json
& $python -m core_engine data-health --json
& $python -m core_engine logs status --json
& $python -m core_engine logs risks --since 24h --json
```

Interpretation rules:

- Scheduled Task `Running` only proves the wrapper state.
- Discord delivery only proves alert transport.
- `doctor` status must be read with its timestamp and detailed checks.
- `data-health` reports historical-only `STALE` candidates as scheduled
  latency while the latest successful historical run remains inside its
  configured schedule grace. These pairs are informational and do not enter
  `pairs_needing_repair`.
- `data-health` is `warn` when a pair is missing, has a market-open hole, is
  stale on the live universe, or historical schedule evidence is overdue.
- Runtime PIDs, row counts and freshness values are snapshots, not permanent
  documentation facts.

## Process Tree

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'core_engine' } |
  Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine
```

Expected shape under the Scheduled Task:

```text
supervisor: python -m core_engine run --live
live child: python -m core_engine.core.live.engine
historical child: present only while a scheduled/queued historical job runs
```

## Logs

Four active logs live under `runtime\logs`:

```text
live.log         live WebSocket, validation, staging, ETL, Redis snapshot work
historical.log   historical fetch, repair, reset and warehouse work
system.log       supervisor, scheduler, locks, lifecycle and crash recovery
alerts.log       WARNING/ERROR/CRITICAL mirror and notification delivery state
```

Use supported queries:

```powershell
python -m core_engine logs status
python -m core_engine logs watch
python -m core_engine logs find --since 2h --level WARNING
python -m core_engine logs trace --correlation-id <id>
python -m core_engine logs risks --since 24h
```

Launcher options 13-16 follow these same four physical files. Options 17-20
run the supported status, find, risks and trace queries.

Avoid `Get-Content -Wait`; the built-in watcher avoids interfering with
Windows-safe rotation.

## Start And Stop

Start the Scheduled Task:

```powershell
Start-ScheduledTask -TaskPath '\SEN05\' -TaskName 'SEN05 DP Program 24x7'
```

Graceful stop:

```powershell
cd C:\Share\dp_program
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
& $python -m core_engine stop --reason operator
```

Wait for the task/process tree to leave the active state. `Stop-ScheduledTask`
or direct process termination is reserved for an approved maintenance or
recovery window after graceful stop fails.

## Historical Repair

Check first:

```powershell
python -m core_engine data-health --json
python -m core_engine reconcile-fact --json
```

Dry-run historical mode when investigating:

```powershell
python -m core_engine historical --mode auto --dry-run
```

Run gap repair only after confirming it is the intended operator action:

```powershell
python -m core_engine historical --mode gap
```

Do not extend `DWH.Dim_Date`, change SQL schema or run destructive reset without
explicit approval.

## Runtime Evidence To Capture

For production conclusions, record UTC timestamp plus:

- Task state/action/account/settings.
- Branch, HEAD and working tree.
- Python executable and `core_engine.__file__`.
- Process tree.
- `settings --json`.
- `doctor --json`.
- `status --json`.
- `data-health --json`.
- `logs status --json`.
- `logs risks --since 24h --json`.
- Fact watermark/count from health output.
- Live batch completion and spool pending/staged metrics.
- Historical last-run summary.
- Discord and CRITICAL outbox status.

Do not call a short snapshot a soak test.

## Discord Webhook Rotation

Webhook rotation requires a Discord channel administrator and explicit approval:

1. Create a new webhook in the approved channel without sharing the URL.
2. Replace only `DISCORD_WEBHOOK_URL` in the private env file.
3. Start through the Scheduled Task.
4. Confirm HTTP 200/204 delivery in alert logs/status.
5. Revoke the old webhook in Discord.
6. Record timestamps only, never URLs.

## Healthy Short-Check Criteria

For a short operational check, collect evidence that:

- Scheduled Task and process tree match the expected shape.
- `doctor --json` status and detailed checks are acceptable for the intended
  operation.
- Live state heartbeat and latest batch evidence are advancing when an approved
  market is open.
- Fact watermark advances when live markets are expected to produce data.
- Live spool has no old pending/staged backlog.
- Active locks are expected and not stale.
- Four log streams exist and have no unresolved ERROR/CRITICAL sequence.
- Discord and CRITICAL outbox status are not stuck.

If any gate is unclear, report it as risk instead of promoting the conclusion.
