# DP Program (SEN05 Data Provider)

Terminal-first Windows application that fetches OHLCV data for 37 instruments
(FOREX, Indices, Metal, Crypto) from TradingView and stores it in SQL Server
for the SEN05 AutoTrading strategies, with an optional Redis candle-snapshot
handoff to OG. The dashboard and exe build flow are intentionally out of
scope until DP Program is stable as a terminal application.

## Layout

```
dp_program/
  src/core_engine/        # the package: pip install -e . makes it importable
    core/                   # live and historical domain engines
    shared/                 # TradingView, warehouse and shared domain helpers
    util/                   # CLI, supervisor, health, logging, notify, Redis, dashboard
    other/                  # TLS and process exit-code primitives
    settings/               # operational (env-driven) vs. system (fixed) config
  test/                     # pytest suite (no DB/TradingView/Redis required)
  config/                   # dp_provider.env (operator-editable, gitignored) + .example
  scripts/                  # setup, launcher, Scheduled Task deploy, SQL schema
  docs/                     # OPERATOR_RUNBOOK.md
  runtime/                  # logs/cache/run/spool - gitignored, created on demand
```

## First Setup

```powershell
cd <dp_program_root>
python scripts\install_python_deps.py
pip install -e .
copy config\dp_provider.env.example config\dp_provider.env
```

Edit `config/dp_provider.env` before real operation. Keep this file private -
it holds TradingView and SQL Server credentials (`.gitignore` excludes it;
only `*.env.example` is committed).

`pip install -e .` registers the `core_engine` package so `python -m
core_engine ...` works from anywhere under the checkout. The app root
(where `config/` and `runtime/` are read/written) is auto-detected from the
installed package location; override it explicitly with `DP_APP_ROOT` if a
deployment needs a fixed path regardless of where the package resolves from.

## Runtime Model

`core_engine` is the application entrypoint. It supervises two independent
data engines, each spawned as its own subprocess:

- Historical OHLCV: `core_engine.core.historical.engine`
- Live OHLCV: `core_engine.core.live.engine`

In production, start the DP Program supervisor:

```powershell
cd <dp_program_root>
python -m core_engine run
```

The supervisor can auto-start live fetching, schedule daily historical
backfill, monitor heartbeats, restart stale live processes, and shut down
gracefully.

## Operator Commands

Open the terminal launcher:

```powershell
cd <dp_program_root>
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

# Read-only chart/data-health viewer
python -m core_engine chart-datacheck --open-browser
```

## Key Config

DP Program supervisor:

```env
WS_LIVE_AUTO_START=1
HISTORICAL_BACKFILL_ENABLED=1
HISTORICAL_BACKFILL_UTC=11:00,22:00
HISTORICAL_BACKFILL_MODE=gap
BACKEND_LIVE_RESTART_ON_EXIT=1
BACKEND_LIVE_RESTART_ON_STALE=1
BACKEND_LIVE_STALE_MINUTES=15
BACKEND_LOG_RETENTION_DAYS=30
```

`WS_LIVE_AUTO_START=1` means `python -m core_engine run` will start live
fetching automatically. VM-DP6 production runs through Scheduled Task
`\SEN05\SEN05 DP Program 24x7`. The current owner-approved action runs the
committed checkout at `C:\Share\dp_program` directly. Do not install NSSM for
the current production release.

Storage destination (`config/dp_provider.env`):

```env
# Optional. Unset infers the mode from CANDLE_SNAPSHOT_ENABLED
# (0 -> sql, 1 -> both). Redis-only operation is rejected because SQL Server
# is the durable system of record.
DP_STORAGE_MODE=sql
```

## Logging

Every component logs through `core_engine.util.logkit.get_logger(component,
log_file, ...)`. Level policy is consistent across the whole program:

| Level | Meaning | Example |
|---|---|---|
| DEBUG | Diagnostics, off by default | raw WS payloads |
| INFO | Normal operation | batch done, job start/finish |
| WARNING | Degraded but self-recovering | retry, cooldown, stale data |
| ERROR | A task/component failed | ETL failed after retries |
| CRITICAL | Program-level failure or data-loss risk | can't start, forced to drop data |

`LOG_LEVEL` sets the global level; `LOG_LEVEL_<COMPONENT>` overrides it per
component (e.g. `LOG_LEVEL_LIVE_FETCHING=DEBUG`). Every WARNING and above,
from any component, also lands in `runtime/logs/system/errors.log` so an
operator can check one file instead of every component log. A CRITICAL
record automatically triggers a Discord alert - no call site needs to
remember to notify separately.

## Logs And State

Runtime files are written under `runtime/` (gitignored).

System logs:

- `runtime/logs/system/system.log` - supervisor, scheduler, restart, cleanup.
- `runtime/logs/system/errors.log` - every component's WARNING+ in one place.
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

## Tests

```powershell
pip install -e .[dev]
pytest test/
```

The suite (~130 tests, a few seconds) covers the TradingView WS wire format,
OHLCV validation, the disk spool, settings resolution, the logging
infrastructure, and the pure/testable pieces of the live and historical
engines. It needs no SQL Server, TradingView, or Redis connection.

## Production Notes

- Live and historical run as separate processes when started by the supervisor.
- TradingView auth refresh is coordinated so processes do not renew credentials
  at the same time.
- Historical backfill uses database locks and yields around live batch windows.
- Stop requests are cooperative first; force termination happens only after the
  configured grace period.
- The terminal DP Program is the foundation. A dashboard should only observe or
  request actions after this layer is proven stable.
