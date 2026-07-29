# DP Program V3

DP Program V3 is the standalone SEN05 TradingView OHLCV provider. It can be
cloned or copied to a new host and operated without V2, `core_engine`, Redis, or
another repository.

The production engine lives entirely under `src/dp_program`. It authenticates
to a real TradingView account, keeps JWT/cookies renewed, can recover the
session with headless Chromium, fetches closed OHLCV candles, persists them
before delivery, and upserts them to SQL Server through loader contract v4.

## Setup

Requirements: Python 3.11+, SQL Server ODBC Driver 18, network access to
TradingView and the configured SQL Server.

```powershell
python -m pip install -e .
python -m playwright install chromium
Copy-Item Config.example.yaml Config.yaml
```

`Config.yaml` is the only runtime configuration file and is ignored by Git
because it contains host credentials. Fill `tradingview.username` and
`tradingview.password` so expired token/cookie can be regenerated; set
`tradingview.two_factor_secret` when the account uses TOTP. SQL credentials,
connection details, workflow switches, the bootstrap lookback, schedules, log
level, runtime path, and optional Discord reporting are also configured in this
file. Set `discord.enabled: true` with a private `discord.webhook_url` to tie
the reporter to the production service lifecycle. Protocol URLs,
timeouts, retries, batching, safety guards, live-fetch contract, fixed
37-symbol/15-timeframe universe, SQL objects, and loader contract are technical
settings owned by `configuration.py` and cannot be overridden by YAML. V3 never
reads `.env` or operating-system environment variables.

For a new SQL Server warehouse, run the canonical installer from its own
directory. It is idempotent and never drops, truncates, or deletes data:

```powershell
Push-Location scripts\sql
sqlcmd -b -S <server> -E -i .\00_run_all.sql
Pop-Location
```

The six included SQL stages create only the V3 schemas, warehouse tables,
staging tables, durable bootstrap state, loader contract v4, reviewed metadata,
and verification gate.
Superseded migrations are retained in Git history instead of being shipped as
runnable files.

## Validate

```powershell
python -m pytest test/
python -m dp_program settings
python -m dp_program check-sql
python -m dp_program auth refresh
python -m dp_program doctor
python -m dp_program live --symbol CAPITALCOM:GOLD --timeframe M5
```

## Run

```powershell
# Production service: control the supervised Scheduled Task
.\run_dp.bat start

# Operational state and readiness
python -m dp_program status
python -m dp_program doctor
python -m dp_program auth status

# Graceful stop
.\run_dp.bat stop

# Foreground diagnostic only
python -m dp_program run

# Finite manual workflows; rejected while the service owns the writer lock
python -m dp_program backfill
python -m dp_program live
```

`run` is the only continuous command. `live` and `backfill` are finite manual
workflows and never own a long-running schedule.

When enabled, the bounded Discord worker starts and stops with `run`. It sends
startup, hourly health, changed HIGH/CRITICAL condition, recovery, and graceful
stop embeds under the display name `DP Program`. Webhook latency never blocks
live/backfill, duplicate incidents are suppressed for 15 minutes, and secrets
are excluded. Discord is notification only; runtime state and logs remain the
operational evidence.

The optional chart is a separate manual, read-only process:

```powershell
python -m dp_program.util.chart.server --open-browser
```

It binds to `127.0.0.1:8050` by default, serves the bundled
Lightweight Charts asset without an internet dependency, validates symbols and
timeframes against the engine contract, and reads committed Fact rows through
`engine/sql_connector.py`. It never starts jobs or writes SQL.

Production writes bounded one-line logs to `runtime/logs/dp_program.log`.
Every new event includes `component`, `event`, `risk`, and `pid`; normal INFO
keeps one live-cycle summary while successful live pairs are DEBUG. Secret
values and bounded exception text are redacted by `log.py`.

For the current backfill policy, a pair's bootstrap scans exactly the latest
`backfill.lookback_days` (60 by default). Non-Crypto starts with an adaptive
estimate while Crypto uses its full 24x7 bound; if needed, the engine extends
only the incomplete series with `request_more_data` on the same socket. It
requires a matching `series_completed` for every logical session/series,
proof that the response reaches the start of the window, filters to the exact
window, and atomically records completion in
`SEN.DP_BackfillState`. The policy epoch makes older completion rows
non-destructively pending for revalidation. Later backfills include three
committed overlap candles ending at the latest Fact candle; they do not pull another minimum
60-day window. A required tail beyond the 20,000-bar request cap fails closed
instead of silently skipping its oldest segment.

Live uses the latest Fact candle as its durable cursor. Healthy pairs use a
small fixed five-bar request. All timeframes of the same symbol normally share
one authenticated physical WebSocket; TradingView's one-series-per-chart-session
limit is handled with separate logical sessions inside that socket. Thus a
healthy cycle normally uses 11 handshakes instead of 165. The first cycle after
restart and any pending pair calculate a larger catch-up request from that SQL
watermark. A partial, malformed, or timed-out group is discarded before
spool/SQL and stays pending for a later cycle. Coverage must reach both the
overlap start and the existing Fact cursor, so an old-only batch cannot be
accepted as complete. A
live pair without a Fact watermark fails closed
until backfill establishes it. Missing candles mean only timestamps returned
by the provider but absent from Fact; no weekend or holiday calendar grid is
synthesized.

Install/update the Windows 24x7 wrapper after all gates pass:

```powershell
.\scripts\windows\install_task.ps1 -Start
```

The Scheduled Task is the only supervised 24/7 wrapper. Startup persists a
fresh generation state and tolerates SQL Server boot readiness for up to five
minutes. `run_dp.bat start`,
`run_dp.bat stop`, and its menu control that task; they do not launch an
unsupervised duplicate process. Direct `python -m dp_program run` remains a
foreground diagnostic command. A graceful stop marker remains durable across
automatic Task restarts; only an explicit operator `start` clears it.

Architecture and ownership are documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Installation, health checks,
auth recovery, deployment, and rollback are in
[`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md).

## Data integrity

`engine/live.py` and `engine/backfill.py` plan different windows, then use the
same `engine/pipeline.py::fetch_and_store()` path:

```text
authenticated TradingView WebSocket
  -> matching series_completed confirmation
  -> UTC validation and closed-candle filter
  -> exact provider-observed window and Fact comparison
  -> atomic runtime spool
  -> SEN.TF_* transaction
  -> DWH.usp_LoadDirect v4
  -> DWH.Fact_OHLCV commit
  -> spool acknowledgement
```

Keys remain `(SymbolID, BarTime)` in staging and
`(SymbolID, TimeframeID, BarTime)` in Fact. Retries after a process or SQL
failure are idempotent. No setup/migration script is run automatically.

When comparison finds no missing or changed candle, no candle is spooled or
staged. When a delta exists, loader contract v4 requires the complete filtered
provider window to be staged so persistent stale staging rows cannot overwrite
Fact. This is deliberate data protection, not a full-history reload; rolling
windows remain only the watermark tail plus overlap.
