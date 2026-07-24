# SEN05 Autotrading — AI Briefing

> **Entry point for all AI agents working on this repository.**
> Read this file before making any changes. Update the relevant `docs/ai_state/*.md` after completing a task.
> This file is AI-agnostic (Claude, Codex, or any other AI).
> Last updated: 2026-06-13

---

## 1. Quick Orientation

**What this system does:**
- Collects market OHLCV data and raw tick data (VM-DP)
- Computes trade signals and sends Discord alerts (VM-OG)
- Signals are consumed by cTrader cBots (C# — outside Python scope)

**What this system does NOT do:**
- Python code NEVER places orders, manages positions, or connects to broker APIs for execution
- All order execution is in `cbot_calgo/` (C# cTrader) — separate, out of scope for Python changes

**Build status (as of 2026-06-13):**

| Component | Status | Notes |
|-----------|--------|-------|
| data_provider tick_data | Production | 24/7 on VM-DP |
| data_provider ws_live | Production | 24/7 on VM-DP |
| data_provider pipeline | Production | Daily gap fill |
| core_python signal_watcher | Production | 24/7 on VM-OG, manual start |
| core_python dashboard | ~85% done | Flask port 8516 |
| core_python CSV export | ~55% done | Known bugs in ai_trend path |
| Redis publisher (OG→OF) | NOT BUILT | 0% — future work |
| OF order execution | NOT BUILT | 0% — future work |
| Backtest optimize | NOT BUILT | 0% — future work |

---

## 2. Infrastructure

| VM | IP | OS | Code |
|----|----|----|------|
| SERVER-HOST | 10.11.12.5 | Windows 11 Pro WS | Dev / repo root `Z:\SEN05_Autotrading` |
| **VM-DP** | 10.11.12.6 | **Windows Server 2022** | `data_provider/` + SQL Server |
| **VM-OG** | 10.11.12.8 | **Ubuntu Server 24.04** | `core_python/` (alias: OG8) |
| VM-OF1–4 | 10.11.12.10–13 | Windows Server 2022 | cBot cTrader (out of scope) |

**Critical VM distinctions:**
- VM-DP = Windows → Task Scheduler, PowerShell (`ops/run_tickdata/`, `ops/run_wslive/`)
- VM-OG = Ubuntu → systemd, bash (`ops/run_signal_watcher_og8/`)
- PowerShell scripts under `ops/` are **VM-DP only** — never run on VM-OG
- SQL Server is at VM-DP (10.11.12.6) — `core_python` connects across LAN

---

## 3. Locked Contracts

**These cannot change without updating all consumers simultaneously. Treat as immutable unless explicitly tasked.**

### 3A. Timestamp Convention
```
ALL bar timestamps = UTC naive (Python datetime, no tzinfo, no DST)
NO tz-aware datetimes, NO convert, NO localize
```
Applies everywhere: SQL Server, DataFrames, CSV exports, Redis payloads, Discord messages.

### 3B. Signal Timing Rule
```
Signal detected at bar i → entry executed at bar i+1 open
NO lookahead: detecting a signal REQUIRES bar i to be fully closed
```

### 3C. Signal Direction Enum
```
direction: 1 = BUY, -1 = SELL (integer, never string)
0 = no signal (never published)
```

### 3D. Redis Signal Payload (LOCKED FORMAT)
```json
{
  "strategy":    "combo",
  "symbol":      "US30",
  "timeframe":   "H1",
  "direction":   1,
  "bar_time":    "2026-06-07T10:00:00",
  "signal_time": "2026-06-07T11:00:05",
  "atr":         45.2
}
```
- `bar_time` = UTC naive ISO string (no Z suffix, no +00:00)
- `direction` = integer 1 or -1
- `atr` = float, ATR at signal bar (for position sizing by cBot)
- Channel format: `signal:{strategy}:{symbol}:{timeframe}`

### 3E. Signal Dedup Key Format
```
"{strategy}|{symbol}|{tf}|{bartime YYYY-MM-DD HH:MM:SS}|{signal}"
```
Stored in `core_python/runtime/state.json`, TTL 60 days. Never change format without migration.

### 3F. SEN.ActiveTask Lock Protocol
```sql
Table: SEN.ActiveTask (TaskName PK, ExpiresAt DATETIME, Payload NVARCHAR(500))
```
- Locks are dead-man switches (auto-expire if process crashes)
- `Payload` column carries control signals (e.g., `shutdown_requested=1`)
- Never DELETE from this table outside of `runtime_lock.py` or `TickDataOps.psm1`
- Lock names: `tv_historical_job`, `tv_live_batch`, `ws_live_runtime`, `checker_repair`, `tick_live_runtime`

### 3G. tick Schema Isolation
```
tick.TickData, tick.SymbolMap, tick.IngestHealth, tick.IngestRun
```
Completely separate from `DWH.*` schema. Do not JOIN across schemas in application code.

---

## 4. Danger Zones

Before modifying these, read the section and understand the full impact.

### 4A. `modules/db_connector.py`
Shared by BOTH VM-DP (Python processes) and VM-OG (signal_watcher). Changing connection behavior affects the entire system. Changes here require testing on both VMs.

### 4B. `core_python/notify/signal_watcher.py` warm-up mode
On startup, signal_watcher marks ALL current signals as already sent (warm-up). This prevents duplicate alerts after restart. Breaking this causes alert storms on every redeploy.

### 4C. `data_provider/tick_data/runtime_lock.py`
Controls the singleton lock for tick ingest. The handoff protocol (shutdown signal → wait → acquire) is critical for zero-downtime restart. Race conditions here cause double-process or permanent lock.

### 4D. WS Live bar-close detection
In `ws_live.py` / `ws_live_batch.py` — the logic that determines "this bar has closed" drives when signals are computed. Any change to bar boundary detection requires verifying that no signal is generated before bar close.

### 4E. `SEN.TF_*` staging tables
15 staging tables used by ws_live and pipeline. Schema MUST match `DWH.Fact_OHLCV`. ETL stored procedures (`DWH.usp_LoadDirect`) assume specific column layout.

### 4F. ops/run_tickdata PowerShell scripts
Running `install_tick_tasks.ps1` will MODIFY Windows Task Scheduler entries on VM-DP. This is reversible but disruptive. Always read the script before running.

---

## 5. Task Routing — What to Read First

| Task type | Read first | Also read |
|-----------|------------|-----------|
| Tick data bug / change | `docs/tick_data_system.html` | `docs/ai_state/tick_data.md`, `data_provider/tick_data/*.py` |
| Signal watcher bug / change | `docs/ai_state/signal_watcher.md` | `core_python/notify/signal_watcher.py` |
| Strategy logic | `docs/ai_state/core_python.md` | `core_python/strategies/{name}/`, `core_python/engine.py` |
| Dashboard / export | `docs/ai_state/core_python.md` | `core_python/main.py`, `core_python/export/` |
| OHLCV pipeline | `docs/ARCHITECTURE.md` §4B | `data_provider/pipeline.py`, `data_provider/ws_live.py` |
| Ops / scheduling VM-DP | `docs/ai_state/ops_run_tickdata.md` | `ops/run_tickdata/*.ps1`, `ops/run_tickdata/lib/TickDataOps.psm1` |
| DB schema / SQL | `docs/ARCHITECTURE.md` §4E | `modules/db_connector.py` |
| Full system overview | `docs/ARCHITECTURE.md` | `docs/tick_data_system.html` |

---

## 6. Module State Docs

Living documents — updated after each AI session.

| Module | State doc | What it tracks |
|--------|----------|----------------|
| Tick Data | `docs/ai_state/tick_data.md` | Ingest health, known issues, recent changes |
| Signal Watcher | `docs/ai_state/signal_watcher.md` | Runtime state, dedup store, known issues |
| core_python strategies | `docs/ai_state/core_python.md` | Strategy status, bugs, CSV export gaps |
| Ops / VM-DP tasks | `docs/ai_state/ops_run_tickdata.md` | Task Scheduler entries, watchdog state |
| WS Live | `docs/ai_state/ws_live.md` | Batch health, bar_ready publish, Redis state |

---

## 7. Key Constants & Config Locations

| Constant | Value | Location |
|----------|-------|----------|
| SQL Server | 10.11.12.6 | `config.py` `DB_*` |
| Redis host | 10.11.12.6 | `config.py` `REDIS_HOST` |
| Redis bar_ready channel | `bar_ready` | `data_provider/ws_live_batch.py` |
| tick flush size | 500 | `data_provider/tick_data/spool.py` |
| tick flush interval | 1.0s | `data_provider/tick_data/spool.py` |
| Stale feed grace | 900s | `ops/run_tickdata/tick_live_watchdog.ps1` |
| Signal dedup TTL | 60 days | `core_python/notify/signal_watcher.py` |
| Dashboard port | 8516 | `core_python/main.py` |
| WS Live batch interval | 5 min | `data_provider/ws_live.py` |
| Symbols tracked | 37 symbols × 15 TF (OHLCV); subset for tick | `config.py` |

---

## 8. Update Protocol

After completing any non-trivial task:

1. **Update the relevant `docs/ai_state/*.md`** — record what changed, current state, known remaining issues
2. **Update `docs/ARCHITECTURE.md`** — only if architecture or flow changed (rare)
3. **Do NOT rewrite AI_BRIEFING.md** unless a locked contract changed or a new module was added

What to write in state docs:
- What changed (file, function, behavior)
- Why (the reason or bug being fixed)
- Current known limitations or follow-up needed
- Date (UTC)

---

## 9. Useful Entry Points by Code Path

```
data_provider/
  tick_data/
    service_live.py       ← tick ingest main loop
    spool.py              ← TickBatcher + TickSpool (durability)
    store_sql.py          ← SQL writer, schema, security
    runtime_lock.py       ← singleton guard + handoff protocol
    checker.py            ← health check + activity profile
    notify.py             ← Discord throttled alerts
  ws_live.py              ← WS live batch loop
  ws_live_batch.py        ← batch processing + Redis publish
  pipeline.py             ← historical gap fill

core_python/
  notify/signal_watcher.py ← 5-thread event loop (main watcher)
  engine.py               ← strategy orchestration
  strategies/
    combo/realtime.py     ← combo signal detection
    ai_trend/realtime.py  ← ai_trend signal detection
    ma_cross/realtime.py  ← ma_cross (dev)
  main.py                 ← Flask dashboard entry

modules/
  db_connector.py         ← shared SQL connection
  redis_client.py         ← shared Redis connection (if exists)

ops/
  run_tickdata/           ← VM-DP tick ops (PowerShell)
  run_wslive/             ← VM-DP ws_live ops (PowerShell)
  run_signal_watcher_og8/ ← VM-OG signal watcher ops (bash)
```
