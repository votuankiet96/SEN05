# State: signal_watcher (core_python)

> Living document. Update after every session that touches `core_python/notify/signal_watcher.py`.
> Last updated: 2026-06-13

---

## Current Runtime Status

| Item | Status |
|------|--------|
| Process | Running 24/7 on VM-OG (Ubuntu 24.04, IP 10.11.12.8) |
| Start method | **Manual** — no systemd unit yet. Started by hand. |
| Primary trigger | Redis `bar_ready` channel (from VM-DP ws_live) |
| Fallback trigger | Timer every 300s (independent of Redis) |
| Dedup store | `core_python/runtime/state.json` |
| Alert channel | Discord webhook |

---

## Architecture Summary

```
5 concurrent threads:
  Thread 1: BarReadySubscriber — Redis subscribe bar_ready
  Thread 2: FallbackScanner — timer every 300s
  Thread 3: SignalWorker — pop queue, run engine, dedup, alert
  Thread 4: DeliveryRelay — retry Redis publish outbox
  Thread 5: Main — idle loop, log metrics every 60s
```

**Warm-up mode:** On startup, watcher runs a full scan but marks ALL current signals as already sent. This prevents alert storms after restart. Break this behavior = flood of duplicate alerts.

**Dedup key:** `"{strategy}|{symbol}|{tf}|{bartime YYYY-MM-DD HH:MM:SS}|{signal}"` — stored with 60-day TTL in state.json.

---

## Key Files

| File | Role |
|------|------|
| `core_python/notify/signal_watcher.py` | Main 5-thread event loop |
| `core_python/notify/alerts.py` | Discord send functions, throttle |
| `core_python/engine.py` | Strategy orchestration (shared with dashboard) |
| `core_python/runtime/state.json` | Dedup store (auto-created, persists across restarts) |
| `ops/run_signal_watcher_og8/` | VM-OG ops scripts (bash) |

---

## SCAN_GROUPS (Production — as of 2026-06-13)

```
ai_trend  H3  → 11 symbols  [h3_trend_change events]
ai_trend  M45 → 11 symbols  [m45_entry_signal events]
combo     H1  → 9 Indice
combo     H2  → 9 Indice
combo     H3  → 9 Indice
combo     H4  → 9 Indice
```

**knn_combo and ma_cross are NOT in SCAN_GROUPS** — code exists but not production.

---

## Signal Outputs

| Output | Status | Notes |
|--------|--------|-------|
| Discord alert | Working | Primary output |
| CSV export | Working (partial) | Bugs in ai_trend path — see core_python.md |
| Redis publish | NOT BUILT | `redis_on=True` flag but no publisher code yet |

---

## Known Issues

1. **No systemd unit** — process must be started manually on VM-OG. If VM reboots, watcher is down until manual restart.

2. **Redis publisher not built** — `redis_on=True` flag exists in code but publish logic is 0% complete. OF systems cannot receive signals yet.

3. **CSV export ai_trend bugs** — `to_csv.py` hardcodes 3 columns; missing per-strategy columns for ai_trend. See `docs/ai_state/core_python.md`.

4. **state.json can grow large** — 60-day TTL means it accumulates. No explicit size management. Rarely an issue but worth monitoring.

---

## Recent Changes

| Date | Change |
|------|--------|
| 2026-06-13 | Documented in `docs/ARCHITECTURE.md` §5D |
| Prior | Realtime signal delivery refactored (commit `95ba3745a`) |

---

## Startup (VM-OG, manual)

```bash
cd /path/to/SEN05_Autotrading
source .venv/bin/activate
python -m core_python.notify.signal_watcher
```

Or via ops script if available in `ops/run_signal_watcher_og8/`.
