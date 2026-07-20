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

Important service account rule:

- Prefer running the service as the same Windows user that owns the TradingView browser/cache profile.
- Avoid `LocalSystem` unless you intentionally set up a separate auth/cache profile for it.
- Configure this in `services.msc` -> `SEN05DataProvider` -> Properties -> Log On.

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
