# DP Program V3 System Overview

DP Program V3 has two independent workflows, both sourced from TradingView:
`backfill` writes historical candles to SQL Server; `live` pushes the latest
closed candles straight to Redis for OG. Neither depends on the other's
delivery path.

## One-Minute Map

```text
TradingView
  -> auth.py (shared)
  -> websocket.py (shared fetch)
  -> pipeline.py validate_candles() (shared pure validation)

  live.py path:              backfill.py path:
    -> live.py Redis push      -> pipeline.py fetch_and_store()
    -> Redis List/Hash           -> spool.py durable outbox
       L_CANDLE_*                -> sql_connector.py
                                  -> SEN.TF_* staging
                                  -> DWH.usp_LoadDirect v4
                                  -> DWH.Fact_OHLCV
```

Runtime entrypoints:

- Dev/test: `python -m dp_program run-live` / `run-backfill` from `core_program/`
  with `src/` on `PYTHONPATH`.
- Production: frozen `run_dp/dp_program.exe` (built from
  `scripts/windows/dp_program_entry.py`), installed by `run_dp/install.ps1` as the
  `SEN05 DP Program Engine` Scheduled Task.

There are no `.bat` wrappers.

## Inputs

- SQL dimensions define the canonical universe:
  - `DWH.Dim_Symbol`
  - `DWH.Dim_Timeframe`
  - Both live and backfill read this (via `select_pairs()`); only backfill
    ever writes SQL.
- `config.yaml` defines operator choices:
  - live cadence (`live.interval_minutes`) and base bars per request;
  - live symbol/timeframe subset;
  - Redis snapshot window (`redis.bars_per_snapshot`) and connection;
  - backfill lookback and schedule;
  - credentials and local runtime paths.

## Processing

- Live runs continuously every configured N minutes, pushing straight to
  Redis. No SQL write, no Fact watermark, no cross-cycle pending state — each
  cycle re-checks the Redis List and self-heals any gap it finds.
- Backfill runs on start and scheduled UTC slots, writing to SQL.
- Live and backfill are separate processes with separate mode locks.
- Shared auth uses a bounded interprocess lock. SQL delivery locking is
  backfill-only now (live never writes SQL).
- Backfill yields to live when live is active, uncertain, or its next cycle
  is imminent.
- Backfill scans 60 days on first policy completion, then repairs from Fact
  watermark with overlap.

## Outputs

- Fact table (backfill only): `DWH.Fact_OHLCV`
- Staging tables (backfill only): `SEN.TF_*`
- Redis (live only): List + Hash `L_CANDLE_{SYMBOL}_{TIMEFRAME}`, stamps
  ascending oldest-to-newest, capped at `redis.bars_per_snapshot` candles (or
  the pair's true TradingView history if shorter). No app-level PUBLISH for
  this data since 2026-09-23 -- OG detects changes via Redis's own keyspace
  notifications on the HSET/RPUSH writes above (server-side `K`+`h` flags),
  not a DP-authored message.
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

- `engine/auth.py`: TradingView authentication (shared).
- `engine/websocket.py`: TradingView protocol and fetch (shared).
- `engine/live.py`: live fetch, Redis gap-detection/self-heal, and Redis push
  (owns the Redis client and Lua script).
- `engine/backfill.py`: bootstrap and historical repair.
- `engine/pipeline.py`: `validate_candles()` (shared) and `fetch_and_store()`
  (SQL delivery, backfill-only).
- `engine/spool.py`: durable outbox and shared locks (backfill-only).
- `engine/sql_connector.py`: all SQL access, warehouse value contract,
  universe and pair selection.
- `engine/runtime.py`: service lifecycle, schedule, state, locks.

Utility, exactly 2 Python files:

- `util/discord_report.py`: optional Discord reporting.
- `util/chart/server.py`: read-only offline chart.

## Safety Rules

- Never run guest TradingView auth.
- Never advance Fact watermark after incomplete provider data (backfill).
- Never ack spool before SQL commit and Fact verification (backfill).
- Never treat weekend or holiday closures as missing candles by themselves.
- Never modify SQL schema or credentials without explicit operator approval.
- Live never writes SQL; `live.enabled=true` requires `redis.enabled=true`
  (enforced in `configuration.py`) since live has nothing else to do otherwise.
