# DP Program V3 System Overview

DP Program V3 fetches closed OHLCV candles from TradingView and writes them to
SQL Server for SEN05 AutoTrading.

## One-Minute Map

```text
TradingView
  -> auth.py
  -> websocket.py
  -> live.py / backfill.py planners
  -> pipeline.py validation
  -> spool.py durable outbox
  -> sql_connector.py
  -> SEN.TF_* staging
  -> DWH.usp_LoadDirect v4
  -> DWH.Fact_OHLCV
```

Runtime entrypoints:

- `run_live.bat` starts `python -m dp_program run-live`.
- `run_backfill.bat` starts `python -m dp_program run-backfill`.

There is no `run_dp.bat` and no Scheduled Task installer in the current pilot.

## Inputs

- SQL dimensions define the canonical universe:
  - `DWH.Dim_Symbol`
  - `DWH.Dim_Timeframe`
- `Config.yaml` defines operator choices:
  - live cadence;
  - live base bars per request;
  - live symbol/timeframe subset;
  - backfill lookback and schedule;
  - credentials and local runtime paths.

## Processing

- Live runs continuously every configured N minutes.
- Backfill runs on start and scheduled UTC slots.
- Live and backfill are separate processes with separate mode locks.
- Shared auth, spool, and SQL delivery use bounded interprocess locks.
- Failed or incomplete live pairs remain pending for the next cycle.
- Backfill scans 60 days on first policy completion, then repairs from Fact
  watermark with overlap.

## Outputs

- Fact table: `DWH.Fact_OHLCV`
- Staging tables: `SEN.TF_*`
- Live state: `runtime/run/state_live.json`
- Backfill state: `runtime/run/state_backfill.json`
- Live log: `runtime/logs/dp_program_live.log`
- Backfill log: `runtime/logs/dp_program_backfill.log`

## File Responsibilities

Package root, 3 Python files:

- `__main__.py`: CLI.
- `configuration.py`: config loader and fixed technical defaults.
- `log.py`: structured logging and secret masking.

Engine, exactly 8 Python files:

- `engine/auth.py`: TradingView authentication.
- `engine/websocket.py`: TradingView protocol and fetch.
- `engine/live.py`: live planner and pending recovery.
- `engine/backfill.py`: bootstrap and historical repair.
- `engine/pipeline.py`: validation and durable delivery.
- `engine/spool.py`: durable outbox and shared locks.
- `engine/sql_connector.py`: all SQL access, warehouse value contract, universe and pair selection.
- `engine/runtime.py`: service lifecycle, schedule, state, locks.

Utility, exactly 2 Python files:

- `util/discord_report.py`: optional Discord reporting.
- `util/chart/server.py`: read-only offline chart.

## Safety Rules

- Never run guest TradingView auth.
- Never advance Fact watermark after incomplete provider data.
- Never ack spool before SQL commit and Fact verification.
- Never treat weekend or holiday closures as missing candles by themselves.
- Never modify SQL schema or credentials without explicit operator approval.
