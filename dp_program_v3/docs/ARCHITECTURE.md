# DP Program V3 Architecture

## Purpose

DP Program V3 fetches closed OHLCV candles from TradingView and persists them
to SQL Server for SEN05 AutoTrading.

The current runtime has two independent modes:

- `run-live`: continuous realtime candle ingestion.
- `run-backfill`: scheduled historical repair and bootstrap.

Each mode has its own process, state file, stop marker, and mode lock. Shared
auth/profile/cache, spool replay, and SQL delivery are protected by bounded
interprocess locks.

## Control Flow

```text
run_live.bat
  -> python -m dp_program run-live
  -> runtime.py
  -> live.py
  -> websocket.py
  -> pipeline.py
  -> spool.py
  -> sql_connector.py
  -> SEN.TF_*
  -> DWH.usp_LoadDirect v4
  -> DWH.Fact_OHLCV

run_backfill.bat
  -> python -m dp_program run-backfill
  -> runtime.py
  -> backfill.py
  -> websocket.py
  -> pipeline.py
  -> spool.py
  -> sql_connector.py
  -> SEN.TF_*
  -> DWH.usp_LoadDirect v4
  -> DWH.Fact_OHLCV
```

## Universe

Runtime symbol and timeframe definitions come from SQL:

- `DWH.Dim_Symbol`
- `DWH.Dim_Timeframe`

`Config.yaml` is only the operator parameter surface. It chooses live subsets
and runtime cadence, but it does not define the canonical SQL universe.

Backfill always uses all active SQL symbols and all SQL timeframes. Live uses
the operator-selected subset and rejects FOREX because FOREX is historical-only.

## Live

Live runs every `live.interval_minutes`, currently 5 minutes. The default base
request is `live.bars_per_request = 3`; the planner may extend the same socket
request for restart/pending recovery when Fact watermark requires it.

For each cycle:

1. Drain durable spool.
2. Ensure TradingView auth is valid.
3. Plan pairs from Fact watermark and pending state.
4. Fetch all timeframes for the same symbol over one physical WebSocket.
5. Validate closed candles only.
6. Compare provider-observed rows with Fact.
7. Write only provider deltas through spool and SQL transaction.
8. Keep failed or incomplete pairs pending for the next cycle.

## Backfill

Backfill runs on launch when `backfill.run_on_start` is true, then follows
`backfill.schedule_utc`.

First policy completion scans 60 days to prevent missing candles. Rolling
repair after completion starts from Fact watermark with overlap instead of
refetching and deleting a full history window.

Backfill yields when live is active, live heartbeat is uncertain, or the next
live cycle is due inside the guard window. If the live PID is dead, stale state
does not block backfill forever.

## Auth

Authentication is fail-closed. The engine never runs guest.

Auth resolution order:

1. runtime cache;
2. private `Config.yaml` token/cookie;
3. HTTP session cookie refresh;
4. persistent Chromium profile;
5. password login;
6. fresh headless login when enabled.

Refresh mutates cache/profile under an interprocess lock. A still-valid token
can use the fast path while another process is refreshing.

## Delivery

The pipeline writes validated candles to durable spool before SQL delivery.
Spool ack happens only after staging, loader, Fact verification, and commit
succeed.

Malformed WebSocket frames, malformed JSON, invalid candle shape, incomplete
series completion, and incomplete coverage fail the whole request. The pair
remains pending; Fact watermark is not advanced by failed provider responses.

## Files

Engine is exactly 8 Python files:

- `engine/auth.py`: TradingView auth.
- `engine/websocket.py`: TradingView WebSocket fetch.
- `engine/live.py`: live planning and pending recovery.
- `engine/backfill.py`: historical bootstrap and repair.
- `engine/pipeline.py`: validation and durable delivery.
- `engine/spool.py`: durable outbox and interprocess locks.
- `engine/sql_connector.py`: all SQL access, warehouse value contract, and SQL-backed pair selection.
- `engine/runtime.py`: mode lifecycle, schedule, state, and locks.

Utility is exactly 2 Python files:

- `util/discord_report.py`
- `util/chart/server.py`
