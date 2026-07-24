# DP Program (SEN05 Data Provider)

DP Program is the terminal-first Windows data provider for SEN05. It fetches
TradingView OHLCV data, validates it, writes SQL Server staging tables, and
loads `DWH.Fact_OHLCV` through the warehouse contract used by SEN05
AutoTrading.

SQL Server is the durable system of record. Redis/OG candle snapshots are an
optional, best-effort handoff when enabled by a reviewed release; they are not
the durable audit or recovery source.

## Canonical Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Operator Runbook](docs/OPERATOR_RUNBOOK.md)
- [Logging Architecture](docs/LOGGING_ARCHITECTURE.md)
- [Engineering Decisions](docs/ENGINEERING_DECISIONS.md)

Historical audit reports and discussion notes are kept in Git history, not as
active documentation.

## Layout

```text
dp_program/
  src/core_engine/        Python package, installed with pip install -e .
    core/                 live and historical OHLCV engines
    shared/               TradingView, warehouse and shared domain helpers
    util/                 CLI, supervisor, health, logging, notify, dashboard
    other/                TLS and process exit-code primitives
    settings/             operational, internal, system and instrument settings
  config/                 dp_provider.env.example; real env file is private
  docs/                   canonical operator and engineering documentation
  scripts/                launcher, dependency helper and SQL scripts
  test/                   pytest suite
  runtime/                logs/cache/run/spool, gitignored and created on demand
```

## Setup

```powershell
cd <dp_program_root>
python scripts\install_python_deps.py
python -m pip install -e .
copy config\dp_provider.env.example config\dp_provider.env
```

Edit `config\dp_provider.env` before real operation. Never commit or paste the
real env file; it contains SQL, TradingView and Discord secrets.

The package entrypoint is:

```powershell
python -m core_engine
```

## Runtime Model

The supervisor owns the 24/7 process lifecycle and can spawn two independent
Python child processes:

- `core_engine.core.live.engine` for near-real-time TradingView batches.
- `core_engine.core.historical.engine` for scheduled full/gap/reset work.

Live and historical share TradingView auth, warehouse validation/writer code,
SQL advisory locks and the four canonical logs. They do not share an in-memory
process.

## Operator Commands

```powershell
# Readiness and current state
python -m core_engine settings --json
python -m core_engine doctor --json
python -m core_engine status --json
python -m core_engine data-health --json

# Supervisor lifecycle
python -m core_engine run --live
python -m core_engine stop --reason operator

# Direct engine runs for controlled testing
python -m core_engine live --smoke-seconds 120
python -m core_engine historical --mode auto --dry-run
python -m core_engine historical --mode gap
python -m core_engine historical --mode full
python -m core_engine historical --mode reset --dry-run

# TradingView auth
python -m core_engine auth status
python -m core_engine auth diagnose
python -m core_engine auth login --timeout-sec 900

# Logs
python -m core_engine logs status
python -m core_engine logs find --since 2h --level WARNING
python -m core_engine logs trace --correlation-id <id>
python -m core_engine logs risks --since 24h

# Read-only local chart/data-health viewer
python -m core_engine chart-datacheck --open-browser
```

The menu launcher remains available:

```powershell
.\run_dp.bat
```

## Configuration

`config/dp_provider.env.example` is the committed template. The real
`config/dp_provider.env` is gitignored and deployment-specific.

The stable production contract is owned by code:

- `settings/system.py`: live scope and SQL durability contract.
- `settings/instruments.py`: 37 instruments and 15 direct timeframes.
- `settings/internal.py`: retry, timeout, queue and protocol policies.
- `settings/operational.py`: small operator-facing env surface and validation.

Useful verification fields from `settings --json` include:

- `symbols_total=37`
- `resolved_live_symbols=11`
- `symbol_timeframe_sessions=165`
- `storage_mode=sql`
- `candle_snapshot_enabled=false` unless a reviewed Redis/OG release enables it

## Production Notes

VM-DP6 production is operated by Scheduled Task, not by a Windows Service. As
verified on 2026-07-24, the task uses `C:\Share\dp_program` as its working
directory; that path is a junction to the physical repository root
`C:\Users\Administrator\Desktop\dp_program`. Re-verify with the runbook before
using any deployment fact as evidence.

Detailed Scheduled Task commands and recovery steps belong in the
[Operator Runbook](docs/OPERATOR_RUNBOOK.md).

## Logging

Every component logs through `core_engine.util.logkit`. There are four active
text logs:

- `runtime/logs/live.log`
- `runtime/logs/historical.log`
- `runtime/logs/system.log`
- `runtime/logs/alerts.log`

Rotated files are gzip-compressed under `runtime/logs/archive/YYYY-MM-DD/`.
Retention is controlled by `LOG_RETENTION_DAYS` and archive disk budget by
`LOG_DISK_BUDGET_MB`. Current logs, SQLite outboxes and live spool databases
are not deleted by log retention.

See [Logging Architecture](docs/LOGGING_ARCHITECTURE.md).

## Tests

```powershell
python -m pip install -e .[dev]
python -m pytest test/
```

The suite covers TradingView protocol parsing, auth helpers, OHLCV validation,
live outbox/delivery boundaries, warehouse contract logic, SQL lock fencing,
settings validation, health, logging and supervisor behavior. Unit tests do not
prove production delivery by themselves; use `doctor`, `status`, `data-health`
and runtime evidence for that.
