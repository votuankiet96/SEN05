# SEN05 Autotrading — Kiến Trúc Hệ Thống

> Tài liệu này mô tả kiến trúc khái niệm và luồng vận hành thực tế của hệ thống.
> Mọi AI làm việc với repo này **phải đọc file này trước**.
> Cập nhật lần cuối: 2026-06-13

---

## 1. Nguyên Tắc Nền Tảng

**SEN05 không tự đặt lệnh.**
Python chỉ thu thập dữ liệu, tính tín hiệu, và gửi cảnh báo.
Execution thực sự nằm trong cBot cTrader (C#) — nằm ngoài scope Python runtime.

```
Python side          C# side
─────────────        ─────────────
Thu thập data   →    [signal] →   cBot cTrader tự đặt lệnh
Tính signal
Gửi cảnh báo
```

**Bar timestamp = UTC naive** — không convert, không localize, không DST.
**Signal ở bar `i` → entry ở bar `i+1`** — không lookahead, không exception.

---

## 2. Hạ Tầng

| VM | IP | OS | Code chạy |
|----|----|----|-----------|
| SERVER-HOST | 10.11.12.5 | Windows 11 Pro WS | Dev / repo gốc (`Z:\SEN05_Autotrading`) |
| **VM-DP** | 10.11.12.6 | Windows Server 2022 | `data_provider/` + SQL Server |
| **VM-OG** | 10.11.12.8 | Ubuntu Server 24.04 | `core_python/` (signal watcher) |
| VM-OF1–4 | 10.11.12.10–13 | Windows Server 2022 | cBot cTrader (ngoài scope) |

**Quan trọng:**
- VM-DP chạy Windows → dùng PowerShell, Task Scheduler
- VM-OG chạy Ubuntu → dùng systemd, bash
- Ops `*.ps1` chỉ áp dụng cho VM-DP, không chạy trên VM-OG

---

## 3. Luồng Tổng Quan

```
┌─────────────────────────────────────────────────────────────────┐
│                  NGUỒN DỮ LIỆU BÊN NGOÀI                        │
│  TradingView WebSocket (37 symbols × 15 TF)                     │
│  cTrader Open API / FTMO (tick data)                            │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  VM-DP  ·  data_provider  ·  10.11.12.6                        │
│                                                                 │
│  ┌──────────────┐  ┌───────────┐  ┌──────────────────────┐    │
│  │  ws_live.py  │  │pipeline.py│  │  tick_data/ (cTrader)│    │
│  │  (24/7 live) │  │(daily gap)│  │  (24/7 tick stream)  │    │
│  └──────┬───────┘  └─────┬─────┘  └──────────────────────┘    │
│         │                │                    │                 │
│         ▼                ▼                    ▼                 │
│  ┌────────────────────────┐         ┌─────────────────┐        │
│  │  DWH.Fact_OHLCV        │         │  tick.TickData  │        │
│  │  (37 sym × 15 TF)     │         │  (raw tick DB)  │        │
│  │  via SEN.TF_* staging  │         └─────────────────┘        │
│  └────────────┬───────────┘                                     │
│               │                                                 │
│               │  Redis PUBLISH bar_ready                        │
│               │  (sau mỗi batch WS_Live)                        │
└───────────────┼─────────────────────────────────────────────────┘
                │
                ▼  (qua LAN 10.11.12.0/24)
┌─────────────────────────────────────────────────────────────────┐
│  VM-OG  ·  core_python  ·  10.11.12.8  (Ubuntu)               │
│                                                                 │
│  ┌──────────────────────────────────────────────┐              │
│  │  signal_watcher.py  (systemd, 24/7)          │              │
│  │  Trigger: Redis bar_ready │ Fallback 300s    │              │
│  │                                               │              │
│  │  engine.py → strategy → detect → dedup       │              │
│  └──────────┬──────────────┬────────────────────┘              │
│             │              │                                     │
│             ▼              ▼                                     │
│       Discord alert    CSV file                                  │
│       (primary)    (/recorded_signal/)                          │
│                                                                 │
│  ┌──────────────────────────────────────────────┐              │
│  │  Dashboard Flask  (manual / on-demand)        │              │
│  │  port 8516  —  dùng cùng engine.py            │              │
│  └──────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. data_provider — Chi Tiết

### 4A. Ba Sub-system Độc Lập

| Sub-system | File chính | Nguồn | Đích | Chạy trên |
|------------|-----------|-------|------|-----------|
| **OHLCV Pipeline** | `pipeline.py` | TradingView WS | `DWH.Fact_OHLCV` | VM-DP |
| **WS Live** | `ws_live.py` | TradingView WS | `DWH.Fact_OHLCV` + Redis | VM-DP |
| **Tick Data** | `tick_data/` | cTrader Open API | `tick.TickData` | VM-DP |

---

### 4B. Luồng OHLCV Pipeline (Lịch Sử + Gap Fill)

```
TradingView WebSocket (prodata endpoint)
    │  get_hist() / ws_history.py / ws_replay.py
    │  37 symbols × 15 TF trực tiếp
    │  N_BARS: M5=20000 bars / D1=5000 bars / W=1000 bars
    ▼
Normalize → UTC naive, sorted ASC, validate
    ▼
SEN.TF_{M5,M10,...,W}  ← 15 staging tables (MERGE, chống trùng)
    ▼
EXEC DWH.usp_LoadDirect @SymbolID, @tf_code, @staging_table
    ▼
DWH.Fact_OHLCV  (bảng chính, 10+ năm lịch sử)
    ▼
[Optional] Recompute derived TFs từ Fact:
    M5 → M10, M20  |  M30 → M90  |  H3 → H6  |  H4 → H8

TRIGGER:
  - Daily 22:22 UTC: mode=gap (3-8 phút, bù thiếu hôm nay)
  - Manual: mode=full (2-4 giờ, rebuild toàn bộ)
  - Manual: mode=dry-run (chỉ in plan, không ghi DB)
```

---

### 4C. Luồng WS Live (Realtime 24/7)

```
┌─────────────────────────────────────────────────────┐
│  VÒNG LẶP 5 PHÚT (BATCH_INTERVAL_MIN=5)            │
│                                                     │
│  1. Mở WebSocket → TradingView                     │
│     Max 10 symbols/connection                       │
│     Chỉ: Indices + Metal + Crypto (bỏ FOREX)       │
│     Request N_BARS_LIVE=5 bars/symbol/TF            │
│                                                     │
│  2. Nhận bars → Queue thread-safe                  │
│     Overflow → SQLite spool (.spool_dir)            │
│                                                     │
│  3. Kiểm tra checker_repair lock                   │
│     ├─ LOCKED → giữ trong Staging, hoãn ETL        │
│     └─ UNLOCKED → tiếp tục                         │
│                                                     │
│  4. INSERT Staging (MERGE)                          │
│     ETL → Fact_OHLCV                               │
│                                                     │
│  5. Redis PUBLISH bar_ready                         │
│     → signal_watcher trên VM-OG nhận event         │
│                                                     │
│  6. Đóng WebSocket → sleep → lặp lại               │
└─────────────────────────────────────────────────────┘

SUPERVISOR (ws_live_supervisor.ps1):
  Loop { Start ws_live.py → Monitor → Restart nếu crash }

WATCHDOG (ws_live_watchdog.ps1, mỗi 60s):
  Check supervisor heartbeat → Force restart nếu chết
```

---

### 4D. Luồng Tick Data (cTrader FTMO, Độc Lập)

```
cTrader Open API (OAuth2, FTMO account)
    │  Protobuf streaming, subscribe spot prices
    │  Symbols: Indices + Metal + Crypto
    ▼
TickBatcher (batch_size=500, flush_seconds=1.0)
    │  RAM queue maxsize=50000
    │  Overflow → SQLite durable spool
    ▼
tick.TickData  (schema riêng, không liên quan DWH)
    │  Symbol mapping: tick.SymbolMap (cTrader ID → SEN05 ID)
    ▼
Monitoring:
  - Heartbeat mỗi 300s
  - Stale alert nếu không có tick > 600s (BTC: 120s)
  - Discord report mỗi 3600s

SCHEDULER:
  - tick_check.ps1 mỗi 5 phút: kiểm tra freshness
  - tick_short_overlap_repair.ps1 mỗi 5 phút: dedup ticks
```

---

### 4E. Lock Coordination (SEN.ActiveTask)

```
Bảng SQL: SEN.ActiveTask (TaskName PK, ExpiresAt, Payload)
Dead-man switch: lock tự hết hạn nếu process crash

Lock Name            Holder          Duration  Mục đích
──────────────────   ──────────────  ────────  ────────────────────────
tv_historical_job    pipeline/checker  30 min  Chỉ 1 TV pull nặng cùng lúc
tv_live_batch        ws_live          3 min   Sync timing batch
ws_live_runtime      ws_live          5 min   Heartbeat process
checker_repair       checker         90 min   Hoãn ETL khi đang repair
tick_live_runtime    tick_live        5 min   Heartbeat tick ingest

Ví dụ checker vs ws_live:
  checker ACQUIRE checker_repair
    → ws_live phát hiện → giữ bars ở Staging, không ghi Fact
  checker sửa xong → RELEASE
    → ws_live tự resume ghi Fact
  Nếu checker crash → lock hết hạn sau 90 phút → ws_live tự resume
```

---

## 5. core_python — Chi Tiết

### 5A. Hai Mode Vận Hành

| Mode | Entry Point | Trigger | State |
|------|------------|---------|-------|
| **Signal Watcher** | `signal_watcher.py` | Redis event + fallback timer | Persistent (`state.json`) |
| **Dashboard** | `core_python/main.py` (port 8516) | HTTP request từ user | Transient, per-request |

Cả hai đều dùng chung **`engine.py`** làm orchestrator.

---

### 5B. Engine — Shared Orchestrator

```
run_strategy_request(strategy, symbol, tf, bars, params)
    │
    ├─ ai_trend   → load H3 trend TF + M45 entry TF → merge → detect
    ├─ knn_combo  → load H3 trend TF + H1 entry TF → KNN gate → combo detect
    ├─ combo (HTF_TREND_ENABLED=True)
    │              → load entry TF + HTF trend TF → filter → detect
    └─ default    → single TF → load → indicators → detect → levels
                    (combo, ma_cross khi không có HTF)

Mỗi path:
  load_request_frame(symbol, tf, n_bars)
    → data/loader.py → SELECT TOP N FROM DWH.Fact_OHLCV
    → DataFrame [bartime, open, high, low, close, volume]
       UTC naive, sorted ASC
  add_indicators(df, params)
  detect_signals(df, symbol, params)    ← signal: 1=BUY, -1=SELL, 0=none
  add_levels(df, params, symbol)        ← entry, SL, TP
  → StrategyRunResult(enriched DataFrame)
```

---

### 5C. Các Strategy (Production)

| Strategy | TF chính | TF phụ | Symbols | Status |
|----------|----------|--------|---------|--------|
| **combo** | H1, H2, H3, H4 | H4/D1/W (HTF trend, optional) | 9 Indice | Production |
| **ai_trend** | M45 (entry) | H3 (trend filter) | 11 symbols | Production |
| **knn_combo** | H1 (entry) | H3 (KNN trend gate) | US30 | Dev |
| **ma_cross** | H1, H2, H3, H4 | — | 9 Indice | Dev |

**SCAN_GROUPS** (danh sách production watcher quét):
```
ai_trend  H3  → 11 symbols (h3_trend_change event)
ai_trend  M45 → 11 symbols (m45_entry_signal event)
combo     H1  → 9 Indice
combo     H2  → 9 Indice
combo     H3  → 9 Indice
combo     H4  → 9 Indice
```

---

### 5D. Signal Watcher — Luồng Realtime

```
START (systemd trên VM-OG)
    │  Single-instance lock (signal_watcher.lock)
    │  Load state.json (dedup store, TTL 60 ngày)
    │  Warm-up mode: đánh dấu tất cả signal hiện tại là "đã gửi"
    ▼

5 THREAD CHẠY SONG SONG:
┌─────────────────────────────────────────────────────────────┐
│ Thread 1: BarReadySubscriber                                │
│   Subscribe Redis channel bar_ready                         │
│   Nhận event → enqueue GroupTriggerEvent(source='redis')   │
│                                                             │
│ Thread 2: FallbackScanner (mỗi 300s)                       │
│   Nếu Redis không có event → quét toàn bộ SCAN_GROUPS      │
│   enqueue GroupTriggerEvent(source='fallback')              │
│                                                             │
│ Thread 3: SignalWorker (MAIN PROCESSING)                    │
│   Pop event từ queue                                        │
│   → check_once(group)                                       │
│       → engine.run_strategy_request()                       │
│       → detect signals trên bars mới nhất                  │
│       → state.has(key)? → skip nếu đã gửi                 │
│       → state.add(key) → send alert → export CSV           │
│                                                             │
│ Thread 4: DeliveryRelay                                     │
│   Retry Redis outbox nếu gửi lần đầu thất bại             │
│                                                             │
│ Thread 5: Main idle                                         │
│   Sleep 1s / log metrics mỗi 60s                          │
│   (queue depth, outbox pending, last scan time)            │
└─────────────────────────────────────────────────────────────┘

OUTPUT khi signal được phát:
  ├─ Discord Webhook (primary, SIGNAL_DISCORD_WEBHOOK_URL)
  ├─ CSV file: core_python/notify/recorded_signal/{strategy}/
  │            {SYMBOL}_{strategy}_{YYYYMMDD_HHMMSS}.csv
  │            Columns: bartime, atr, signal
  └─ Redis publish (nếu redis_on=True — đang build)
```

**Dedup key format:**
```
"{strategy}|{symbol}|{tf}|{bartime YYYY-MM-DD HH:MM:SS}|{signal}"
```
Lưu trong `core_python/runtime/state.json`. Restart không gửi lại signal cũ.

---

### 5E. Dashboard — Luồng On-demand

```
User mở browser → http://127.0.0.1:8516

/api/config → trả danh sách strategies, symbols, TFs, defaults

/api/scan?strategy=combo&symbol=US30&tf=H1&bars=500
    │  _DB_REQUEST_LOCK (serialize DB access)
    │  run_strategy_request() → engine → StrategyRunResult
    │  build_combo_payload(result) → JSON
    ▼
  { candles[], overlays[], panels[], markers[], levels[], signals[] }
  → Frontend render Lightweight Charts

/api/export?strategy=combo&symbol=US30&tf=H1
    │  export/service.build_single_export()
    ▼
  CSV binary download (flexible columns, cached 120s)

KHÔNG share state với signal_watcher:
  - Dashboard: transient, load fresh từ DB mỗi request
  - Watcher: persistent state.json
```

---

## 6. Kết Nối Giữa Hai Hệ Thống

```
VM-DP (data_provider)              VM-OG (core_python)
─────────────────────              ──────────────────
ws_live.py                         signal_watcher.py
  batch hoàn thành                   BarReadySubscriber thread
       │                                    │
       └── Redis PUBLISH bar_ready ─────────┘
           (LAN 10.11.12.x, <1ms latency)

Nếu Redis không hoạt động:
  FallbackScanner thread quét mỗi 300s độc lập
  → hệ thống không mất signal, chỉ delay tối đa 5 phút

Dữ liệu đi qua:
  VM-DP ghi Fact_OHLCV
  VM-OG đọc Fact_OHLCV (qua SQL Server 10.11.12.6)
  → Không có data transfer trực tiếp giữa 2 VM ngoài Redis event
```

---

## 7. Ops & Scheduling Tổng Hợp

### VM-DP (Windows Task Scheduler)

| Task | Script | Lịch | Mục đích |
|------|--------|------|---------|
| WsLive_Supervisor | `ws_live_supervisor.ps1` | At startup | Restart ws_live.py nếu crash |
| WsLive_Watchdog | `ws_live_watchdog.ps1` | Mỗi 60s | Restart supervisor nếu chết |
| OHLCV Pipeline | `pipeline.py --mode gap` | 22:22 UTC daily | Gap fill hàng ngày |
| Data Checker | `checker.py` | Mỗi 3 ngày 03:00 UTC | Scan + auto-repair chất lượng |
| TickLive_Supervisor | `tick_live_supervisor.ps1` | At startup | Restart tick ingest nếu crash |
| TickLive_Watchdog | `tick_live_watchdog.ps1` | Mỗi 60s | Monitor tick supervisor |
| Tick_Checker | `tick_check.ps1` | Mỗi 5 phút | Kiểm tra freshness tick |
| Tick_Repair | `tick_short_overlap_repair.ps1` | Mỗi 5 phút | Dedup tick |

### VM-OG (Ubuntu systemd)

| Service | Entry Point | Lịch | Mục đích |
|---------|------------|------|---------|
| signal_watcher | `python -m core_python.notify.signal_watcher` | Always restart | 24/7 realtime scan |
| Dashboard | `python -m core_python.main` | Manual | On-demand chart/export |

---

## 8. Contracts Không Được Thay Đổi

Những quyết định sau đã được kiểm chứng trong production. **Không tự ý thay đổi** mà không có review và test kỹ:

| Contract | Giá trị hiện tại | Lý do không đổi |
|----------|-----------------|-----------------|
| Bar timestamp format | UTC naive (tz-unaware) | TV trả UTC, tránh DST edge case |
| Signal timing | Bar `i` signal → entry bar `i+1` | Ngăn lookahead bias |
| Staging → Fact ETL | `EXEC DWH.usp_LoadDirect` | Stored proc handles upsert safety |
| Lock table | `SEN.ActiveTask` (SQL, not file) | Atomic, survives process crash |
| Dedup key format | `strategy\|symbol\|tf\|bartime\|signal` | State file migration compatibility |
| Redis signal payload | Xem redis_integration_plan.md | Locked — cBot sẽ consume |
| WS_Live FOREX skip | Indices + Metal + Crypto only | FOREX TF không đủ history qua WS |
| Dashboard port | 8516 | Hardcoded trong config.py:52 |
| Single watcher instance | file lock (`signal_watcher.lock`) | Prevent duplicate alerts |

---

## 9. Hiện Trạng (2026-06-13)

| Phần | Trạng thái | Ghi chú |
|------|-----------|---------|
| OHLCV Pipeline | ✅ Production | Chạy ổn định VM-DP |
| WS Live | ✅ Production | 5-phút batch, Redis publish OK |
| Tick Data (cTrader) | ✅ Production | Đang thu thập tick liên tục |
| Checker | ✅ Production | Auto-repair mỗi 3 ngày |
| Signal Watcher | ✅ Production | VM-OG, systemd, combo + ai_trend |
| Dashboard | ✅ Usable | Port 8516, manual start |
| Redis signal publish (OG→OF) | ⏳ Build | 0% — chưa bắt đầu |
| backtest_optimize | ⏳ Partial | Có code cơ bản, chưa production |
| cBot / OF system | ⏳ Planned | Ngoài scope Python hiện tại |

---

*Cập nhật file này mỗi khi có thay đổi kiến trúc lớn.*
*Chi tiết từng module: xem `docs/ai_state/{module}.md`*
