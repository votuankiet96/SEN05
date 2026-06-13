# State: tick_data (data_provider)

> Living document. Update after every session that touches `data_provider/tick_data/`.
> Last updated: 2026-06-13

---

## Current Runtime Status

| Item | Status |
|------|--------|
| Process | Running 24/7 on VM-DP (Windows Server 2022) |
| Supervisor | Task Scheduler `\SEN05\SEN05_TickLive_Supervisor` |
| Watchdog | Task Scheduler `\SEN05\SEN05_TickLive_Watchdog` (every 60s) |
| Data freshness checker | Task Scheduler every 5 min |
| Short-overlap repair | Task Scheduler every 5 min |
| DB lock name | `tick_live_runtime` in `SEN.ActiveTask` |

---

## Architecture Summary

```
cTrader Open API (FTMO, Protobuf streaming)
    │  OAuth2 token, subscribe spot prices
    ↓
TickBatcher (RAM, maxsize=50000)
    │  flush every 500 ticks OR 1.0s
    ↓  SQL insert fails?
TickSpool (SQLite WAL, durable overflow)
    │  INSERT OR IGNORE (dedup by event_hash)
    ↓  drain_spool() on next flush_loop
tick.TickData  (SQL Server, isolated schema)
```

**6 risk defense layers (in order):**
1. Exponential backoff reconnect (5s → 300s max)
2. TickBatcher → TickSpool overflow (SQLite durable buffer)
3. Delete-only-after-success pattern in drain_spool
4. Runtime lock (DB + PID file) → only 1 process running
5. Heartbeat + dead-man expiry (60min) → auto-release on crash
6. Checker + watchdog stale-feed detection → graceful restart after 900s stale

---

## Key Files

| File | Role |
|------|------|
| `data_provider/tick_data/service_live.py` | Main ingest loop, state machine, all async callbacks |
| `data_provider/tick_data/spool.py` | TickBatcher + TickSpool classes |
| `data_provider/tick_data/store_sql.py` | SQL writer, per-symbol table routing, security |
| `data_provider/tick_data/runtime_lock.py` | Singleton guard, handoff protocol |
| `data_provider/tick_data/checker.py` | Health check, activity profile (30-day), stale detection |
| `data_provider/tick_data/notify.py` | Discord alerts, double throttle |
| `ops/run_tickdata/lib/TickDataOps.psm1` | All PowerShell helper functions |
| `ops/run_tickdata/tick_live_watchdog.ps1` | Watchdog: stale-feed restart logic |

---

## Configuration Parameters (Key)

| Param | Default | Where |
|-------|---------|-------|
| `TICK_BATCH_SIZE` | 500 | `spool.py` |
| `TICK_FLUSH_SECONDS` | 1.0 | `spool.py` |
| `TICK_STALE_SECONDS` | 600 | `checker.py` (BTC: 120) |
| `TICK_HEARTBEAT_SECONDS` | 300 | `service_live.py` |
| `LOCK_HEARTBEAT_SECONDS` | 900 | `runtime_lock.py` |
| `LOCK_DURATION_MIN` | 60 | `runtime_lock.py` |
| `StaleFeedRestartSec` | 900 | `tick_live_watchdog.ps1` |
| `StaleCheckEverySec` | 300 | `tick_live_watchdog.ps1` |

---

## SQL Schema (Isolated — `tick` schema)

| Table | Purpose |
|-------|---------|
| `tick.SymbolMap` | cTrader symbolId → SEN05 SymbolID + allowed_symbols set |
| `tick.TickData` | Raw ticks: SymbolID, EventTimeUtc, BidPrice, AskPrice, VolumeInLots |
| `tick.IngestHealth` | Per-symbol heartbeat: LastLiveTickTimeUtc, current bid/ask |
| `tick.IngestRun` | Run history: UUID, start/end, tick count per run |

Per-symbol tables: `tick.[{symbol_id}_{symbol_name}]` — bracket-quoted, validated via `IDENTIFIER_RE`.

---

## Known Issues / Limitations

1. **Activity profile warm-up:** First 30 days of production lack historical profile → TRANSITION_GRACE state more frequent than expected. Not a bug, resolves naturally.

2. **Checker parallel fetch:** `checker.py` fetches activity profile per-symbol sequentially. For many symbols, startup can be slow. No async — acceptable for now.

3. **Spool growth:** If SQL Server is down for extended periods (>hours), SQLite spool grows unbounded. No explicit size cap. Manual cleanup needed if it reaches GB scale.

4. **No tick backfill from REST API:** cTrader Open API only provides streaming ticks. Historical tick data must come from cTrader's separate historical export. Gap after restart = gap in data.

---

## Recent Changes

| Date | Change |
|------|--------|
| 2026-06-13 | Comprehensive documentation created: `docs/tick_data_system.html` |
| 2026-06-13 | Architecture documented in `docs/ARCHITECTURE.md` |
| Prior | cTrader FTMO tick provider added (commit `deef0b837`) |
| Prior | Realtime signal delivery refactored (commit `95ba3745a`) |

---

## Manual Operations (VM-DP)

```powershell
# Check status
.\ops\run_tickdata\tick_status.ps1

# View live dashboard
.\ops\run_tickdata\tick_dashboard.ps1

# Force restart (graceful: writes shutdown signal, waits for clean exit)
# 1. Watchdog detects stale after 900s automatically
# 2. Manual: write shutdown signal via PowerShell
Import-Module .\ops\run_tickdata\lib\TickDataOps.psm1
$paths = Get-TickDataPaths
Request-TickLiveGracefulShutdown -Paths $paths
```
