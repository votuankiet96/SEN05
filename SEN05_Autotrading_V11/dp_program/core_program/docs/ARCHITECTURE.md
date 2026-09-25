# DP Program V3 Architecture

## Purpose

DP Program V3 has two fully independent workflows, both sourced from TradingView:

- `backfill`: the sole SQL writer. Bootstraps and repairs historical candles
  into SQL Server (`DWH.Fact_OHLCV`) for SEN05 AutoTrading.
- `live`: pushes the latest closed candles straight to Redis for OG to read in
  near-real-time. Never writes SQL.

The two workflows do not share a delivery path. `live` does not depend on
`backfill` having run first, beyond a one-time read of the SQL universe
(`DWH.Dim_Symbol` / `DWH.Dim_Timeframe`). `backfill` never touches Redis.

Each mode has its own process, state file, stop marker, and mode lock. Auth
cache and the TradingView WebSocket transport are shared, protected by bounded
interprocess locks; SQL delivery locking is backfill-only now.

## Control Flow

```text
python -m dp_program run-live   (prod: run_dp/dp_program.exe)
  -> runtime.py
  -> live.py
  -> websocket.py (fetch)
  -> pipeline.py validate_candles() (shared pure validation, no SQL)
  -> live.py (owns Redis client + Lua script)
  -> Redis List/Hash L_CANDLE_{SYMBOL}_{TIMEFRAME}

python -m dp_program run-backfill   (prod: run_dp/dp_program.exe)
  -> runtime.py
  -> backfill.py
  -> websocket.py
  -> pipeline.py (validate_candles() + fetch_and_store())
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

`config.yaml` is only the operator parameter surface. It chooses live subsets
and runtime cadence, but it does not define the canonical SQL universe. Live
reads this universe the same way backfill does (`select_pairs()`) — a read,
not a write; it is the only reason `live.py` still touches `sql_connector.py`.

Backfill always uses all active SQL symbols and all SQL timeframes. Live uses
the operator-selected subset and rejects FOREX because FOREX is historical-only.

## Live

Live runs every `live.interval_minutes`, currently 2 minutes. Each cycle:

1. Ensure TradingView auth is valid.
2. Fetch a small tail (`live.bars_per_request`) per pair from TradingView,
   grouped by symbol into one WebSocket batch per symbol.
3. Validate closed candles only (`pipeline.validate_candles()`).
4. For each pair, check the existing Redis List: if it already holds
   `redis.bars_per_snapshot` candles and the new tail is contiguous with the
   last stored candle, push just the tail (cheap). If the List is empty,
   short, or has a gap, fetch the full `bars_per_snapshot` window instead and
   push that (self-healing — no separate bootstrap step to keep in sync).
5. Push through one Lua script (`_INCREMENTAL_SCRIPT` in `live.py`): HSET only
   candles whose price actually changed, RPUSH only stamps not already
   present, evict the oldest stamps past `bars_per_snapshot` via LPOP + DEL,
   and PUBLISH a JSON event listing only the candles that changed.

There is no Fact watermark, no pending-pair state carried across cycles, and
no spool — a failed pair is simply retried fresh next cycle; the gap check
above makes this self-correcting without extra bookkeeping. Two independent
circuit breakers protect the cycle: consecutive/total TradingView fetch
failures (per symbol group) defer the rest of the cycle to protect the
account; a Redis failure opens a separate cooldown so a Redis outage does not
keep hammering TradingView uselessly.

## Backfill

Backfill runs on launch when `backfill.run_on_start` is true, then follows
`backfill.schedule_utc`. This schedule was deliberately left unchanged by the
live/Redis redesign — SQL is now purely a historical warehouse, so it no
longer needs near-real-time cadence.

First policy completion scans 60 days to prevent missing candles. Rolling
repair after completion starts from Fact watermark with overlap instead of
refetching and deleting a full history window.

Backfill yields when live is active, live heartbeat is uncertain, or the next
live cycle is due inside the guard window (`_live_yield_active()` in
`runtime.py`). If the live PID is dead, stale state does not block backfill
forever. This priority is unchanged by the redesign, and matters more now
that live is the only path feeding Redis in near-real-time.

## Auth

Authentication is fail-closed. The engine never runs guest. Shared by both
live and backfill.

Auth resolution order:

1. runtime cache;
2. private `config.yaml` token/cookie;
3. HTTP session cookie refresh;
4. persistent Chromium profile;
5. password login;
6. fresh headless login when enabled.

Refresh mutates cache/profile under an interprocess lock. A still-valid token
can use the fast path while another process is refreshing.

## Delivery

**Backfill (SQL)**: the pipeline writes validated candles to durable spool
before SQL delivery. Spool ack happens only after staging, loader, Fact
verification, and commit succeed. Malformed WebSocket frames, malformed JSON,
invalid candle shape, incomplete series completion, and incomplete coverage
fail the whole request. The pair remains pending for the next scheduled slot;
Fact watermark is not advanced by failed provider responses.

**Live (Redis)**: no spool, no durable outbox — the Redis List itself is the
durable state live checks against every cycle (see Live above). A failed or
interrupted push simply leaves the List slightly behind; the next cycle's gap
check detects and repairs it.

## Files

Engine is exactly 8 Python files:

- `engine/auth.py`: TradingView auth (shared).
- `engine/websocket.py`: TradingView WebSocket fetch (shared).
- `engine/live.py`: live fetch, Redis gap-detection/self-heal, and Redis push
  — owns the Redis client and Lua script.
- `engine/backfill.py`: historical bootstrap and repair.
- `engine/pipeline.py`: `validate_candles()` (shared pure validation) and
  `fetch_and_store()` (SQL delivery, backfill-only).
- `engine/spool.py`: durable outbox and interprocess locks (backfill-only).
- `engine/sql_connector.py`: all SQL access, warehouse value contract, and
  SQL-backed pair selection (live still reads the universe here, never writes).
- `engine/runtime.py`: mode lifecycle, schedule, state, and locks.

Utility is exactly 2 Python files:

- `util/discord_report.py`
- `util/chart/server.py`
