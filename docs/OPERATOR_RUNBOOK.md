# SEN05 Data Provider - Operator Runbook

This runbook is for terminal/service operation of DP Program.
It assumes the current app root is:

```powershell
<dp_program_root>
```

For DP6 production, prefer a local path such as:

```powershell
C:\Share\dp_program
```

Do not run the Windows Service from a mapped drive such as `Z:\`.

## 1. Install Python Runtime Dependencies

Run PowerShell as Administrator:

```powershell
cd <dp_program_root>
python .\scripts\install_python_deps.py
pip install -e .
```

This installs Python packages from `requirements.txt`, installs Playwright
Chromium into `runtime\cache\playwright-browsers`, and registers the
`core_engine` package (editable install) so `python -m core_engine ...`
resolves from anywhere under the checkout.

## 2. Prepare SQL Data Warehouse

The SQL setup files are in:

```text
scripts\sql
```

They create:

- `SEN05_AutoTrading`
- `SEN` and `DWH` schemas
- 15 staging tables `SEN.TF_*`
- `DWH.Dim_Symbol`
- `DWH.Dim_Timeframe`
- `DWH.Dim_Date`
- `DWH.Fact_OHLCV`
- `DWH.usp_LoadDirect`
- `SEN.ActiveTask`

Run with `sqlcmd`:

```powershell
cd <dp_program_root>\scripts\sql
sqlcmd -S localhost -E -i .\00_run_all.sql
```

If SQL Server uses SQL login instead of Windows auth:

```powershell
sqlcmd -S localhost -U <sql_user> -P <sql_password> -i .\00_run_all.sql
```

You can also run `00_run_all.sql` in SSMS with SQLCMD Mode enabled.

## 3. Configure Runtime

Edit:

```text
config\dp_provider.env
```

Minimum required groups:

- SQL Server connection
- TradingView credentials/session material
- Discord webhook
- DP Program schedule/live settings

Never paste secrets into reports or chat.

## 3b. Live vs Historical Coverage (IMPORTANT - read before relying on this for real-time FOREX)

DP Program tracks 37 instruments total, but only a **subset is fetched live** over
WebSocket every 5 minutes. The rest are refreshed **only by the scheduled historical
job** (`HISTORICAL_BACKFILL_UTC`, default `11:00,22:00` UTC).

- **Live (real-time, ~5 min lag)**: instruments whose `asset_type` is in
  `LIVE_ASSET_TYPES` (default `Indice,Metal,Crypto` - 11 of 37 symbols as configured
  at last count; run `python -m core_engine settings` to see the current live count).
- **Historical-only (scheduled, up to ~11h lag between runs, longer if a run fails or
  the schedule is disabled)**: everything else - by default this is all 26 FOREX
  pairs.

This is a **deliberate scope limit from the initial build, not a bug**, and this
refactor round does not change it or decide whether to change it - see round-2 audit
item P0-8. If SEN05 AutoTrading strategies need real-time FOREX prices, the live
universe would need to be extended (`LIVE_ASSET_TYPES=Indice,Metal,Crypto,FOREX`) and
load-tested first: that would raise live's WebSocket session count from ~165
(11 symbols x 15 timeframes) to ~555 (37 x 15), which is a materially different load
profile (more connection groups, more concurrent staging/ETL writes) that has not been
validated.

**Operator action needed:** confirm with the strategy owner whether the current
11-live/26-historical-only split is acceptable for production, or whether FOREX needs
to move to live coverage first.

Two related settings (`config/dp_provider.env`):

```env
# Optional. CSV of instruments.py asset_type values to fetch live. Default
# matches the historical hardcoded scope (Indice,Metal,Crypto).
LIVE_ASSET_TYPES=Indice,Metal,Crypto

# Drift guard: live refuses to start unless the resolved live symbol count
# matches exactly - defaults to 11 (the current LIVE_ASSET_TYPES count), so
# this is enforcing by default (round-3 audit NEW finding: it used to
# default to 0/disabled, so it never actually caught anything). Set to 0 to
# disable; update the number here if you deliberately change
# LIVE_ASSET_TYPES or instruments.py's asset mix.
EXPECTED_LIVE_SYMBOLS=11
```

## 4. Check Readiness

The simplest operator entrypoint is:

```powershell
cd <dp_program_root>
.\run_dp.bat
```

The launcher starts with pre-checks: Status, Doctor, Data Health, Core Settings,
and TradingView Auth. Production start is a single action:
`Start DP Program 24/7`, which starts live fetching and keeps scheduled
historical backfill enabled. Long-running jobs open in their own PowerShell
windows so the menu remains available.

Manual data operations in the launcher are maintenance actions. Use them only
when you intentionally want to run backfill, full pull, replay, reset, or direct
live outside the 24/7 supervisor. The launcher blocks these manual actions when
DP Program, live fetching, or historical pulling is already active; choose
Graceful Stop first, then confirm System Check reports no active runtime process.

```powershell
cd <dp_program_root>
python -m core_engine doctor
```

Useful focused checks:

```powershell
python -m core_engine status
python -m core_engine data-health
python -m core_engine auth status
python -m core_engine auth diagnose
```

If TradingView auth is not valid, run:

```powershell
python -m core_engine auth login --timeout-sec 900
```

Log in manually in the browser window, then wait for the command to finish.

## 5. Smoke Test Before Service Install

Run a short DP Program smoke test:

```powershell
python -m core_engine run --live --no-schedule --smoke-seconds 90
```

Expected:

- live worker starts
- at least one batch attempts to fetch data
- shutdown is graceful
- `python -m core_engine status` returns OK or a clear WARN

## 6. Install as Windows Service

Install NSSM first and make sure `nssm.exe` is in PATH, or pass `-NssmPath`.

Run PowerShell as Administrator:

```powershell
cd <dp_program_root>
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_service\install_windows_service.ps1 -Start
```

If NSSM is not in PATH:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_service\install_windows_service.ps1 -NssmPath "C:\Tools\nssm\nssm.exe" -Start
```

The installer validates `python -c "import core_engine"` before creating the service
and fails loudly if it does not import cleanly (usually a missing `pip install -e .`),
instead of installing a service that would just crash-loop.

Important service account rule:

- Prefer running the service as the same Windows user that owns the TradingView browser/cache profile.
- Avoid `LocalSystem` unless you intentionally set up a separate auth/cache profile for it.
- Set this directly at install time instead of as a separate manual step (a manual
  `services.msc` step is easy to skip, and skipping it fails silently until the next
  headless auth refresh is needed):

```powershell
$cred = Get-Credential  # enter the target Windows account and password
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_service\install_windows_service.ps1 `
    -ServiceUser $cred.UserName -ServicePassword $cred.Password -Start
```

  If `-ServiceUser`/`-ServicePassword` are omitted, the installer warns and leaves the
  service on `LocalSystem` - configure it afterward in `services.msc` ->
  `SEN05DataProvider` -> Properties -> Log On if you did not pass them.

## 7. Daily Operation Commands

Check service:

```powershell
Get-Service SEN05DataProvider
```

Start service:

```powershell
Start-Service SEN05DataProvider
```

Request graceful stop:

```powershell
cd <dp_program_root>
python -m core_engine stop --reason operator
```

Then stop the Windows service if needed:

```powershell
Stop-Service SEN05DataProvider
```

Remove service:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_service\remove_windows_service.ps1
```

## 8. Logs

System logs:

```text
runtime\logs\system\system.log
runtime\logs\system\errors.log
runtime\logs\system\activity.log
runtime\logs\system\auth.log
runtime\logs\system\discord.log
runtime\logs\system\subprocess_debug.log
runtime\logs\system\service_stdout.log
runtime\logs\system\service_stderr.log
```

`errors.log` aggregates every component's WARNING and above in one place -
check it first when something looks wrong, then follow up in the specific
component log for full context.

Operation logs:

```text
runtime\logs\operation\live_fetching.log
runtime\logs\operation\historical_pulling.log
runtime\logs\operation\data_warehouse.log
runtime\logs\operation\live_fetching_summary.jsonl
runtime\logs\operation\historical_pulling_summary.jsonl
```

Use:

```powershell
Get-Content .\runtime\logs\system\errors.log -Tail 100
Get-Content .\runtime\logs\system\activity.log -Tail 100
Get-Content .\runtime\logs\system\system.log -Tail 100
Get-Content .\runtime\logs\operation\live_fetching.log -Tail 100
Get-Content .\runtime\logs\operation\historical_pulling.log -Tail 100
```

## 9. Historical Jobs

Run scoped dry-run check:

```powershell
python -m core_engine historical --mode gap --dry-run --symbols BTCUSD --timeframes M5 --hole-lookback-days 1
```

Run daily gap/backfill:

```powershell
python -m core_engine historical --mode gap
```

Run full historical pull:

```powershell
python -m core_engine historical --mode full
```

Reset is emergency-only:

```powershell
python -m core_engine historical --mode reset --dry-run --symbols BTCUSD --timeframes M5
python -m core_engine historical --mode reset --reset --yes --symbols BTCUSD --timeframes M5
```

## 10. What Good Looks Like

Healthy operation means:

- `python -m core_engine status` is OK.
- `system.log` shows DP Program running.
- `activity.log` shows command/process start and completion.
- `live_fetching.log` shows regular live batches.
- `discord.log` shows `discord.sent` with HTTP `204`.
- `DWH.Fact_OHLCV` row count increases during live market periods.
- No stale `SEN.ActiveTask` locks block historical/live work.
