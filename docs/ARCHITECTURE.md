# DP Program Architecture

This document describes the architecture reconstructed from the codebase and
runtime contract. It intentionally avoids line counts, file counts, PIDs, row
counts and freshness snapshots because those values change.

## System Boundary

DP Program is one Python package, `core_engine`, with a `src/` layout. The
operator entrypoint is:

```powershell
python -m core_engine
```

The CLI owns operator commands. The supervisor owns process lifecycle. The live
and historical engines own OHLCV acquisition and warehouse delivery.

## Runtime Processes

```text
Scheduled Task
  -> python -m core_engine run --live
     -> supervisor process
        -> live child process
        -> historical child process when scheduled or requested
```

The live and historical engines are separate Python subprocesses. They
coordinate through SQL advisory locks, runtime state files and durable storage;
they do not share memory.

## Package Ownership

```text
src/core_engine/
  __main__.py              package entrypoint for python -m core_engine
  core/
    live/                  24/7 near-real-time engine
    historical/            scheduled/backfill/reset engine
  shared/
    tradingview/           protocol, history client, diagnostics, auth
    warehouse/             SQL connection, validation, writer, reader, reconcile
    freshness.py           shared stale/freshness threshold helper
    time.py                small UTC parsing/formatting helpers
  util/
    cli.py                 operator CLI
    supervisor/            24/7 process owner
    coordination/          SQL advisory locks
    health.py              read-only status, doctor and data-health checks
    logkit/                structured logging, rotation, retention, queries
    notify/                Discord delivery and durable CRITICAL outbox
    redis_io/              optional Redis/OG candle snapshot
    dashboard/             read-only local chart/data-health viewer
  settings/
    system.py              reviewed product/data contract
    instruments.py         symbols, timeframes and market-closure metadata
    internal.py            fixed retry, timeout, queue and protocol policies
    operational.py         operator env parsing and validation
  other/
    tls.py                 Windows trust-store helper
    exit_codes.py          process exit codes
```

The intended dependency direction is domain-first:

```text
core -> shared -> util
settings and other are foundational inputs
```

`util.health` is an observer. It can inspect process, runtime, database, log
and alert state, but it does not repair data by itself.

## Data Flow

```text
TradingView
  -> shared.tradingview protocol/auth/history client
  -> live or historical processing
  -> shared.warehouse.validation
  -> live durable outbox or historical direct stage/write path
  -> SEN.TF_* staging tables
  -> DWH.usp_LoadDirect
  -> DWH.Fact_OHLCV
  -> health, logs and Discord alerts
```

SQL Server is the durable source of truth. Redis/OG candle snapshots, when
enabled by reviewed configuration, are a best-effort consumer handoff after SQL
Fact delivery, not the recovery authority.

## Live Engine

The live engine is the 24/7 near-real-time path.

Main responsibilities:

- Resolve the approved live universe from `settings.system` and
  `settings.instruments`.
- Verify the expected live-symbol count before running.
- Authenticate to TradingView and keep token/cookie material fresh.
- Open grouped TradingView WebSocket sessions for all live symbols/timeframes.
- Drop the still-open last bar and accept only closed OHLCV candles.
- Persist each accepted candle to the SQLite outbox before RAM dispatch.
- Validate OHLCV before staging.
- Keep staging write and Fact load on separate workers.
- Ack live outbox rows only after Fact commit succeeds for the covered key.
- Maintain live state, batch metrics, missing/backfill signals and health
  evidence for the supervisor.

The durable live outbox uses the state flow:

```text
pending -> leased -> staged -> ack/delete
```

Rows are reset to `pending` on restart if they were leased or staged but not
acked. This makes the delivery path idempotent across process crashes.

## Historical Engine

The historical engine is the scheduled/backfill/reset path.

Main responsibilities:

- Choose `full`, `gap` or `reset` mode.
- Hold the historical job lock and cooperate with live batch windows.
- Probe TradingView auth/network readiness.
- Fetch historical or replay windows.
- Validate OHLCV and write staging.
- Run `DWH.usp_LoadDirect` even when staging merge is a no-op, so prior
  crash-after-staging cases can still reconcile into Fact.
- Write `runtime/run/historical_last_run.json`.
- Purge processed staging rows only after Fact values match.

Gap classification separates actionable missing market-open data from normal
closure signatures and verified upstream-unavailable windows.

## TradingView Layer

`shared.tradingview.protocol` owns the raw TradingView WebSocket framing:

- `~m~<length>~m~<payload>` packet parsing.
- `~h~` heartbeat echoing.
- conversion of TradingView bars to data frames.
- WebSocket error classification.

`shared.tradingview.history_client` owns historical/replay requests.
`shared.tradingview.auth` owns token/cookie state, runtime credential cache,
browser-profile fallback, optional headless refresh/login and guest fallback.
Auth status and diagnostics must not expose secrets.

## Warehouse Layer

`shared.warehouse.connection` owns pyodbc connection creation and DB contract
verification. The code expects `DWH.usp_LoadDirect` contract version `4` and
OwnerId/Fence columns in `SEN.ActiveTask`.

`shared.warehouse.writer` owns:

- `insert_staging_batch()`: temp table plus MERGE into staging.
- `run_etl_direct()`: executes `DWH.usp_LoadDirect` and commits returned counts.

`shared.warehouse.validation` normalizes UTC timestamps, removes invalid OHLCV,
deduplicates bars and drops future bars.

`shared.warehouse.maintenance` can purge staging only after matching Fact rows
exist. `shared.warehouse.reconcile` separates Fact-eligible divergence from
rows outside the supported `DWH.Dim_Date` calendar.

## Coordination

SQL advisory locks live in `util.coordination.locks` and use `SEN.ActiveTask`.
The important task names are:

- `dp_program_supervisor`
- `ws_live_runtime`
- `tv_live_batch`
- `tv_historical_job`
- `warehouse_maintenance`

OwnerId/Fence semantics prevent stale or reconnected owners from deleting a
lock they no longer own. Same-host stale process cleanup is allowed only after
process identity checks.

## Observability

All logs are routed through `util.logkit`. The four active text logs are:

- `runtime/logs/live.log`
- `runtime/logs/historical.log`
- `runtime/logs/system.log`
- `runtime/logs/alerts.log`

`util.notify.discord` sends ordinary Discord alerts through a bounded
asynchronous queue. `util.notify.critical_outbox` durably persists CRITICAL
alert rows before the logging call returns.

`util.health` backs:

- `python -m core_engine status`
- `python -m core_engine doctor`
- `python -m core_engine data-health`

Health output is runtime evidence with a timestamp, not architecture.
