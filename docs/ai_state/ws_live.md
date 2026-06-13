# State: ws_live (data_provider)

> Living document. Update after every session that touches `data_provider/ws_live*.py`.
> Last updated: 2026-06-13

---

## Current Runtime Status

| Item | Status |
|------|--------|
| Process | Running 24/7 on VM-DP (Windows Server 2022) |
| Supervisor | Task Scheduler `\SEN05\WsLive_Supervisor` |
| Watchdog | Task Scheduler `\SEN05\WsLive_Watchdog` (every 60s) |
| Batch interval | 5 minutes (BATCH_INTERVAL_MIN=5) |
| Redis publish | After each batch completes → `bar_ready` channel |

---

## Architecture Summary

```
TradingView WebSocket (prodata endpoint)
    │  Max 10 symbols/connection, batched connections
    │  Symbols: Indices + Metal + Crypto (NO FOREX — excluded by design)
    │  N_BARS_LIVE=5 bars per symbol per TF
    ↓
ws_live_batch.py → Queue (thread-safe)
    │  Overflow → SQLite spool (.spool_dir)
    ↓
Staging insert (SEN.TF_* tables, MERGE)
    │  Check checker_repair lock → if locked: hold in staging
    ↓
ETL: DWH.usp_LoadDirect → DWH.Fact_OHLCV
    ↓
Redis PUBLISH bar_ready → VM-OG signal_watcher
```

---

## Key Files

| File | Role |
|------|------|
| `data_provider/ws_live.py` | Batch loop entry, connection management |
| `data_provider/ws_live_batch.py` | Batch processing, staging insert, Redis publish |
| `ops/run_wslive/ws_live_supervisor.ps1` | Supervisor loop |
| `ops/run_wslive/ws_live_watchdog.ps1` | Watchdog |
| `ops/run_wslive/install_ws_live_tasks.ps1` | ⚠ Task Scheduler installer |

---

## Checker-Repair Lock Interaction

```
When data checker runs repair (SEN.ActiveTask → checker_repair):
  ws_live detects lock → holds new bars in Staging (NOT written to Fact)
  When checker finishes → releases lock → ws_live resumes ETL
  If checker crashes → lock auto-expires after 90min → ws_live resumes

This prevents data corruption during repair operations.
```

---

## Redis bar_ready Message

```json
{"symbols": ["US30", "US500"], "timeframes": ["H1", "H2"], "batch_id": "uuid"}
```
(Exact format — verify in `ws_live_batch.py` before assuming)

Published to channel: `bar_ready`
VM-OG signal_watcher subscribes via `BarReadySubscriber` thread.

---

## FOREX Exclusion

WS Live **intentionally excludes FOREX symbols** (EUR/USD, GBP/USD, etc.).
Reason: TradingView FOREX data quality inconsistency during off-hours.
Indices + Metal + Crypto only.

This is a design decision, not a bug.

---

## Known Issues

1. **No bar-close guarantee from TradingView WS** — `N_BARS_LIVE=5` requests last 5 bars, which may include the currently-forming bar. Bar-close detection logic in `ws_live_batch.py` is critical — verify before any change.

2. **SQLite spool not monitored** — unlike tick_data spool, ws_live spool growth is not actively monitored. Check periodically.

3. **`ops/run_wslive/runtime/ws_live_supervisor.heartbeat.json`** — currently listed as modified in git status. May have stale content from last run.

---

## Recent Changes

| Date | Change |
|------|--------|
| 2026-06-13 | Documented in `docs/ARCHITECTURE.md` §4C |
| Prior | Commit `95ba3745a` — realtime signal delivery refactored |
