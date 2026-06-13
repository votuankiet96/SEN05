# State: core_python (strategies + engine + dashboard)

> Living document. Update after every session that touches `core_python/`.
> Last updated: 2026-06-13

---

## Module Overview

| Component | Status | Entry point |
|-----------|--------|-------------|
| Engine (orchestrator) | ~90% | `core_python/engine.py` |
| combo strategy | Production | `core_python/strategies/combo/realtime.py` |
| ai_trend strategy | Production (with open bugs) | `core_python/strategies/ai_trend/realtime.py` |
| knn_combo strategy | Dev — not in SCAN_GROUPS | `core_python/strategies/knn_combo/realtime.py` |
| ma_cross strategy | Dev — not in SCAN_GROUPS | `core_python/strategies/ma_cross/realtime.py` |
| Dashboard (Flask) | ~85% | `core_python/main.py` port 8516 |
| CSV export | ~55% — bugs | `core_python/export/service.py` + `to_csv.py` |
| Redis publisher | 0% — NOT BUILT | — |

---

## Engine Routes

```python
run_strategy_request(strategy, symbol, tf, bars, params)
  ai_trend   → load H3 trend + M45 entry → merge → detect
  knn_combo  → load H3 trend + H1 entry → KNN gate → combo detect
  combo (HTF_TREND_ENABLED=True) → load entry TF + HTF trend → filter → detect
  default    → single TF → load → indicators → detect → levels
              (combo without HTF, ma_cross)
```

All paths produce: `StrategyRunResult(enriched DataFrame with signal, levels, indicators)`

---

## Strategy Details

### combo
- **TF:** H1, H2, H3, H4 (each scanned independently)
- **Symbols:** 9 Indice (US30, US500, US100, DAX, etc.)
- **HTF trend:** H4/D1/W optional filter via `HTF_TREND_ENABLED`
- **Status:** Production, stable

### ai_trend
- **TF entry:** M45; **TF trend:** H3
- **Symbols:** 11 (Indice + Metal + Crypto)
- **Status:** Production, but known bugs:
  - `atr5`, `atr14`, `sl_dow` column changes partially applied, may not be committed/tested
  - ATR panel in dashboard depends on these fixes

### knn_combo
- **TF entry:** H1; **TF trend:** H3 (KNN gate)
- **Symbols:** US30 only currently
- **Status:** Dev code exists, not in SCAN_GROUPS, not production

### ma_cross
- **TF:** H1, H2, H3, H4
- **Symbols:** 9 Indice
- **Status:** Dev code exists, not in SCAN_GROUPS, not production

---

## Known Bugs & Open Work

### CSV Export (Priority: Medium)

1. **`to_csv.py` hardcodes 3 columns** — `["bartime", "atr", "signal"]`
   Missing per-strategy columns: combo needs no extras; ai_trend needs `atr5, atr14, sl_dow`

2. **Two separate export paths** — watcher uses `to_csv.py`, dashboard uses `export/service.py`
   Not synchronized; different column sets.

3. **Bulk ai_trend export missing `TREND_TF`** — URL param not passed through, uses default H3

4. **`COLUMN_LABELS` sl_dow label** — hardcodes pivot parameters into display label

### ai_trend Changes (Priority: High — affects production signal quality)
- `atr`, `sl_dow`, `dow_pivot` column swap changes in `ai_trend/realtime.py` may be uncommitted
- Verify current state of `core_python/strategies/ai_trend/realtime.py` before modifying

### Redis Publisher (Priority: Future)
- `redis_on` flag exists in signal_watcher config
- Zero implementation — need to add publish call in `SignalWorker.check_once()`
- Payload format is LOCKED (see `AI_BRIEFING.md` §3D)

---

## Dashboard API

```
GET /api/config           → strategies, symbols, TFs, defaults
GET /api/scan?strategy=combo&symbol=US30&tf=H1&bars=500
    → { candles[], overlays[], panels[], markers[], levels[], signals[] }
GET /api/export?strategy=combo&symbol=US30&tf=H1
    → CSV binary download (cached 120s)
```

Dashboard does NOT share state with signal_watcher:
- Dashboard: transient, fresh DB query per request
- Watcher: persistent state.json

---

## Data Flow

```
SQL Server DWH.Fact_OHLCV (at VM-DP 10.11.12.6)
  ↓ SELECT TOP N (UTC naive datetimes, ASC sorted)
core_python/data/loader.py
  ↓ DataFrame [bartime, open, high, low, close, volume]
engine.py → add_indicators → detect_signals → add_levels
  ↓ StrategyRunResult
signal_watcher → dedup → Discord alert + CSV
dashboard → JSON payload → chart render
```

---

## Recent Changes

| Date | Change |
|------|--------|
| 2026-06-13 | Documented in `docs/ARCHITECTURE.md` §5 |
| Prior | Reverse-signal policy, combo SL/TP methods, chart viewer, cBot annotation (commit `27c729b1d`) |
| Prior | Combo signals and provider changes (commit `4a0516ee4`) |
