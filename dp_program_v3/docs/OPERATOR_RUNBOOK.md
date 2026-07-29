# DP Program V3 operator runbook

Run all commands from the repository root with the same Python interpreter
configured in Scheduled Task.

## Install on a new host

```powershell
python -m pip install -e .
python -m playwright install chromium
Copy-Item Config.example.yaml Config.yaml
```

Populate the private `Config.yaml` locally. Never send, print, or commit it.
The mandatory TradingView values are token/cookie and/or username/password
under `tradingview`. Configure `tradingview.two_factor_secret` when the account
uses TOTP. SQL connection values are under `sql_server`; no `.env` or
operating-system environment variables are read.

Discord reporting is optional:

```yaml
discord:
  enabled: false
  webhook_url: ""
```

When enabled, the reporter exists only inside `python -m dp_program run`.
It posts startup, hourly health, changed incident, recovery, and graceful-stop
embeds as `DP Program`. It does not have a Scheduled Task or process of its own.
Do not paste the webhook into logs, commands, screenshots, or tracked files.

The YAML is intentionally operator-only: credentials/connectivity, runtime
path, log level, workflow switches, bootstrap lookback, and UTC backfill schedule.
WebSocket protocol, timeouts/retries, batching, safety guards, live cadence,
closed-candle policy, and cache internals are fixed in `configuration.py`.
Technical keys in YAML are rejected so a handoff cannot silently change the
production contract.

For a new warehouse, run the canonical SQLCMD installer from `scripts/sql`.
Use the approved SQL authentication mode for the target host:

```powershell
Push-Location scripts\sql
sqlcmd -b -S <server> -E -i .\00_run_all.sql
Pop-Location
```

The installer is idempotent and non-destructive. It does not create legacy
coordination/archive objects and does not remove unrelated objects already
present in an upgraded database.

The installer also creates `SEN.DP_BackfillState`. On a fresh warehouse its
rows start empty. On an upgraded pilot, rows older than the fixed backfill
policy epoch remain intact but are treated as pending. Every enabled
symbol/timeframe is therefore revalidated over the exact latest 60-day window
without deleting Fact or state rows. Completion is recorded or timestamped
again only when coverage, loader v4, exact Fact verification, and the outer
transaction all succeed.

Run all gates before installing the task:

```powershell
python -m pytest test/
python -m dp_program settings
python -m dp_program check-sql
python -m dp_program auth refresh
python -m dp_program doctor
python -m dp_program live --symbol CAPITALCOM:GOLD --timeframe M5
```

Install or update the production task:

```powershell
.\scripts\windows\install_task.ps1 -Start
```

After installation, the portable operator menu controls this same task:

```powershell
.\run_dp.bat check
.\run_dp.bat start
.\run_dp.bat stop
```

`start` waits for a healthy service heartbeat; it never launches a detached,
unsupervised Python process. `stop` leaves a durable stop marker so an
automatic Task restart cannot race the operator request; only an explicit
`run_dp.bat start` or installer `-Start` clears that marker.

The script registers the current Windows identity with `S4U`, so no Windows
password is stored or requested. S4U cannot access network shares with the
user's delegated credentials; `doctor` must pass under the Task identity before
production use. Use `-TaskUser DOMAIN\User` only when another approved local
identity is required.

## Canonical deployment

- Task: `\SEN05\SEN05 DP Program 24x7`
- Action: `<production-python> -m dp_program run`
- Start in: physical V3 repository root
- Trigger: host startup
- Restart: every minute, up to 999 attempts
- Startup dependency grace: SQL Server readiness, up to five minutes
- Multiple instances: ignore new
- Execution time limit: none
- Logon type: S4U, highest privileges, no stored password

The task account must be able to read `Config.yaml`, write `runtime/`, launch
Playwright Chromium, and connect to SQL Server. With trusted SQL
authentication, it must be the approved Windows database identity.

## Routine checks

```powershell
python -m dp_program status
python -m dp_program doctor
Get-ScheduledTask -TaskPath "\SEN05\" -TaskName "SEN05 DP Program 24x7"
Get-ScheduledTaskInfo -TaskPath "\SEN05\" -TaskName "SEN05 DP Program 24x7"
Get-Content runtime\logs\dp_program.log -Tail 100
```

Healthy evidence includes:

- task and recorded PID are running;
- heartbeat is less than ten minutes old;
- latest live summary is 165/165 with zero failures;
- healthy live normally records 11 same-symbol physical socket batches; each
  batch may contain up to 15 logical chart sessions/timeframes;
- `pending_live_pairs` normally returns to empty; a failed pair must recover
  from its unchanged Fact watermark in a later cycle;
- `deferred` normally remains zero; a non-zero value means stop, cycle budget,
  or circuit protection intentionally postponed pairs without advancing them;
- auth state is `authenticated`, never guest;
- Chromium check and SQL contract v4 pass;
- spool has zero corrupt files and normally returns to zero pending;
- Fact count/watermark progresses when closed candles are available;
- repeated process samples do not show unbounded handle/thread/RSS growth.

When Discord is enabled, `event=DISCORD_REPORT_SENT` confirms delivery. A
`DISCORD_REPORT_FAILED`, queue warning, or shutdown timeout never stops the
engine; inspect connectivity and the webhook configuration. Notifications are
best-effort and do not replace the state/log evidence above.

Start the optional offline chart manually when needed:

```powershell
python -m dp_program.util.chart.server --open-browser
```

The default listener is local-only at `127.0.0.1:8050`. It uses the bundled
JavaScript asset and parameterized committed reads from `DWH.Fact_OHLCV`; it
does not run live/backfill or modify SQL.

`check-sql` reports `bootstrap_state_rows_total`,
`bootstrap_completed_pairs`, and `bootstrap_remaining_pairs`. The first value
includes older policy rows; the latter two refer only to the current policy.
The reviewed universe has 555 historical pairs. Once a pair completes exact
60-day validation, scheduled backfill uses only its Fact-watermark tail plus
three committed overlap candles.

`backfill_queue_remaining` is work not yet visited in the current runtime
generation. It is intentionally different from `bootstrap_remaining_pairs`,
which is durable SQL policy state. Failed backfill pairs are listed once in
`backfill_failed_pairs` and wait for the next scheduled generation rather than
retrying hot against the provider.

`backfill_generation_total`, `backfill_generation_processed`, and
`last_backfill_progress_at` distinguish slow but progressing work from a stuck
queue. A due schedule while a generation is active is coalesced and recorded,
not appended as another 555-pair queue.
If the historical circuit opens, `backfill_failed_pairs` contains only pairs
that were actually attempted and failed. Unattempted work is reported
separately in `backfill_deferred_pairs` and
`backfill_generation_deferred`; it is retried by the next scheduled generation
and must not be reported as missing market data.

## Logs and risk queries

Production logs are UTF-8, UTC, one event per line:

```text
2026-07-27T12:00:00Z INFO component=runtime event=LIVE_CYCLE_COMPLETED risk=NONE pid=1234 pairs=165 ok=165 failed=0 deferred=0 affected=12 spool_pending=0
2026-07-27T12:00:02Z ERROR component=pipeline event=PAIR_FAILED risk=HIGH pid=1234 workflow=live symbol=CAPITALCOM:GOLD timeframe=M5 stage=sql_delivery error_type=RuntimeError error="RuntimeError: SQL unavailable" action="transaction rolled back; candles retained in spool"
```

Use simple PowerShell queries; no log parser is required:

```powershell
Select-String -Path runtime\logs\dp_program.log* -Pattern " risk=(HIGH|CRITICAL) "
Select-String -Path runtime\logs\dp_program.log* -Pattern " event=PAIR_FAILED "
Select-String -Path runtime\logs\dp_program.log* -Pattern " component=auth "
```

Risk labels mean:

- `NONE`: normal lifecycle or successful summary;
- `LOW`: bounded automatic retry;
- `MEDIUM`: fallback path or timing degradation that is being handled;
- `HIGH`: one pair, SQL delivery, or durable spool needs recovery/attention;
- `CRITICAL`: the authenticated production service cannot continue.

A backfill queue with no progress for more than one hour is also `HIGH`;
`last_backfill_progress_at` is the evidence used for that classification.

INFO records lifecycle, authentication renewal, backfill progress, spool replay,
and one live-cycle summary. Successful live pairs are DEBUG only. Candle values,
JWTs, cookies, passwords, connection strings, and raw configuration are never
intentional log fields; exception text is redacted and capped. Rotation remains
bounded by the fixed defaults in `configuration.py`; older pre-deployment lines
keep their original format until normal rotation removes them.

## Authentication recovery

Check without revealing credentials:

```powershell
python -m dp_program auth status
python -m dp_program doctor
```

To force renewal, first stop the writer so its Chromium profile is not in use:

```powershell
.\run_dp.bat stop
python -m dp_program auth refresh
.\run_dp.bat start
```

V3 tries cookie refresh, the persistent headless profile, password login, and
fresh headless login. If all fail, the engine exits or pauses delivery instead
of using a guest session. CAPTCHA or a changed third-party sign-in flow can
still require an operator to renew token/cookie in `Config.yaml`.

## Controlled stop, deploy, and rollback

```powershell
.\run_dp.bat stop
Stop-ScheduledTask -TaskPath "\SEN05\" -TaskName "SEN05 DP Program 24x7"
python -m pytest test/
python -m dp_program doctor
.\run_dp.bat start
```

Rollback does not delete SQL rows, runtime spool, browser profile, or logs.
Disable/stop the V3 task, restore the approved V3 Git commit, reinstall the
editable package, rerun the gates, and start the task. Do not restart V2 as an
implicit rollback; that requires a separate operator decision.

Never reboot the host, stop SQL Server, delete/truncate data, rotate
credentials, clear the spool, or kill unrelated Python processes as part of a
normal V3 deployment.

## Data-fetch incident interpretation

- `coverage` or `IncompleteFetchError`: the response was discarded before
  spool/SQL; the Fact watermark did not move.
- `pending_live_pairs`: observational priority list only. Fact is the durable
  catch-up cursor, including after process restart.
- `stage=planning error_type=CatchupWindowError`: no latest-only subset is written; run
  a targeted historical recovery after review.
- `missing_before`: only candles observed in the completed provider response
  but absent from Fact. Weekend, holiday, and provider session closures do not
  create synthetic gaps.
- A delta causes the complete filtered provider window to be staged because
  loader v4 reads persistent staging from `FromTime`. Staging only the delta is
  unsafe under contract v4.
