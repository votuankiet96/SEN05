# SEN05 — Hệ thống Auto Trading Research

**Mục tiêu:** Thu thập dữ liệu thị trường tự động, lưu trữ vào kho dữ liệu SQL Server, và cung cấp nền tảng nghiên cứu/backtest/tín hiệu cho các chiến lược giao dịch định lượng — hiện tại trong `core_python` gồm **Combo**, **MA Cross** và **AI Trend**.

> Đây là hệ thống nghiên cứu (research-grade), chưa ở giai đoạn live trading.

> Cập nhật vận hành `core_python`: hệ thống hiện có dashboard nội bộ và watcher Telegram 24/7 cho tín hiệu. Watcher chỉ gửi cảnh báo tín hiệu, không đặt lệnh thật, không quản lý vốn và không quản lý vị thế. Tài liệu chi tiết, sát code nhất nằm tại `core_python/README.md` và `core_python/strategies/ai_trend/ARCHITECTURE.md`.

---

## Mục lục

1. [Tổng quan kiến trúc](#1-tổng-quan-kiến-trúc)
2. [Cài đặt môi trường](#2-cài-đặt-môi-trường)
3. [Luồng dữ liệu end-to-end](#3-luồng-dữ-liệu-end-to-end)
4. [Data Provider — Thu thập và quản lý dữ liệu](#4-data-provider--thu-thập-và-quản-lý-dữ-liệu)
5. [Modules — Thư viện dùng chung (legacy)](#5-modules--thư-viện-dùng-chung-legacy)
6. [Core Python — Nền tảng chiến lược](#6-core-python--nền-tảng-chiến-lược)
7. [Chiến lược Combo](#7-chiến-lược-combo)
8. [Chiến lược MA Cross](#8-chiến-lược-ma-cross)
9. [Quy trình nghiên cứu — Notebooks](#9-quy-trình-nghiên-cứu--notebooks)
10. [Vận hành 24/7](#10-vận-hành-247)
11. [Test Suite](#11-test-suite)
12. [Cấu trúc thư mục đầy đủ](#12-cấu-trúc-thư-mục-đầy-đủ)

---

## 1. Tổng quan kiến trúc

```
┌─────────────────────────────────────────────────────────────────┐
│                        NGUỒN DỮ LIỆU                           │
│              TradingView (Capital.com — CAPITALCOM)             │
│         37 symbols · 10 TF trực tiếp · 5 TF phái sinh          │
└────────────────────────────┬────────────────────────────────────┘
                             │  tvdatafeed (WebSocket / REST)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     DATA PROVIDER LAYER                         │
│  01_data_pipeline.py   — Historical load + daily gap fill       │
│  02_ws_live.py         — Near-realtime batch WebSocket (5 min)  │
│  04_checker.py         — Data quality scan & auto-repair        │
│  03_chart.py           — Flask chart dashboard (nội bộ)         │
└────────────────────────────┬────────────────────────────────────┘
                             │  pyodbc → SQL Server
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    KHO DỮ LIỆU (SQL Server)                     │
│  Database: SEN05_AutoTrading                                     │
│  Schema SEN: TF_M5 / TF_M15 / ... / TF_H4 / TF_D1 / TF_W      │
│  DWH.Fact_OHLCV — bảng chính chứa toàn bộ nến OHLCV           │
│  SEN.ActiveTask  — lock table ngăn race condition giữa scripts  │
└────────────────────────────┬────────────────────────────────────┘
                             │  core_python/shared/data.py
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   CORE PYTHON STRATEGY LAYER                    │
│                                                                 │
│  shared/           — contracts, analytics, market specs         │
│  strategies/combo/ — Chiến lược Combo (pending breakout order)  │
│  strategies/ma_cross/ — Chiến lược MA Cross (basket reversal)  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    RESEARCH OUTPUT                              │
│  Jupyter Notebooks (01→06 mỗi strategy)                         │
│  output/ — CSV, JSON, cTrader validation files                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Cài đặt môi trường

### Yêu cầu
- Python ≥ 3.10
- SQL Server (local) với ODBC Driver 17
- Windows (ops scripts dùng PowerShell)

### Cài đặt nhanh

```bash
# Tạo venv và cài dependencies
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Cài project ở chế độ editable (để import hoạt động đúng)
pip install -e .
```

### Cấu hình credentials

Tạo file `.env` ở thư mục gốc (không commit vào git):

```env
# SQL Server (bỏ trống nếu dùng Windows Authentication)
SQL_UID=
SQL_PWD=

# TradingView — ưu tiên auth_token (lấy từ cookie trình duyệt)
TV_AUTH_TOKEN=your_token_here
TV_USERNAME=
TV_PASSWORD=

# Discord webhook để nhận thông báo pipeline (tuỳ chọn)
DISCORD_WEBHOOK_URL=
```

### Khởi tạo database

```sql
-- Chạy theo thứ tự trong data_provider/00_sql/
-- 00_run_all.sql  — chạy toàn bộ một lần
```

---

## 3. Luồng dữ liệu end-to-end

```
TradingView
    │
    │  1. Historical load (chạy một lần, mất 2–4 giờ)
    │     python data_provider/01_data_pipeline.py --mode full
    │
    │  2. Daily gap fill (chạy hàng ngày lúc 22:22 UTC)
    │     python data_provider/01_data_pipeline.py --mode gap
    │
    │  3. Near-realtime (chạy 24/7, cứ 5 phút một lần)
    │     python data_provider/02_ws_live.py
    │
    ▼
SQL Server (DWH.Fact_OHLCV)
    │
    │  10 TF trực tiếp: M5, M15, M30, M45, H1, H2, H3, H4, D1, W
    │  5 TF phái sinh (tính từ staging):
    │    M10 ← M5×2    M20 ← M5×4    M90 ← M30×3
    │    H6  ← H3×2    H8  ← H4×2
    │
    │  4. Data quality check (chạy mỗi 3 ngày)
    │     python data_provider/04_checker.py
    │
    ▼
core_python/shared/data.py  (data access layer)
    │
    ├── load_backtest_ohlcv()    → DataFrame cho backtest engine
    ├── load_scan_ohlcv()        → DataFrame cho signal scanner
    └── load_chart_candles()     → DataFrame cho chart dashboard
```

### Cơ chế chống xung đột (race condition)

Ba scripts (pipeline, ws_live, checker) chạy song song. Để tránh ghi đè nhau:
- `SEN.ActiveTask` — lock table trong SQL Server
- `data_provider/_task_lock.py` — acquire/release lock từ Python
- Khi checker đang repair: ws_live dừng ETL, pipeline nhường quyền

---

## 4. Data Provider — Thu thập và quản lý dữ liệu

### `config.py` — Bảng điều khiển trung tâm

File duy nhất cần chỉnh khi vận hành. Không sửa các script khác.

| Mục | Nội dung |
|-----|---------|
| SQL connection | `SQL_SERVER`, `SQL_DATABASE`, credentials từ `.env` |
| TradingView auth | `TV_AUTH_TOKEN` (ưu tiên) hoặc username/password |
| Bar counts | `N_BARS_H4 = 10000` ≈ 6.8 năm lịch sử H4 |
| Symbol list | 37 symbols: 9 Indices + 25 Forex + 2 Metal/Crypto |
| TF mapping | 10 direct TF + 5 derived TF + display order |

### `01_data_pipeline.py` — Historical Load & Daily Backfill

```bash
python data_provider/01_data_pipeline.py              # auto-detect mode
python data_provider/01_data_pipeline.py --mode full  # full load từ đầu
python data_provider/01_data_pipeline.py --mode gap   # chỉ bù phần thiếu
python data_provider/01_data_pipeline.py --dry-run    # xem kế hoạch, không ghi
python data_provider/01_data_pipeline.py --symbols US30,GOLD --mode gap
python data_provider/01_data_pipeline.py --asset-type Indice
```

Luồng xử lý: TradingView → Staging table (`SEN.TF_*`) → `usp_LoadDirect` → `Fact_OHLCV` → `usp_AggregateFromStaging` (tính TF phái sinh).

### `02_ws_live.py` — Near-Realtime Updater (V5 Batch Mode)

Chạy 24/7. Cứ mỗi 5 phút: mở WebSocket → nhận 3–5 nến mới nhất → ghi staging → ETL → đóng.

**Phạm vi**: Indices, Metal, Crypto. FOREX không theo dõi qua WS (dùng pipeline backfill).

**Tại sao Batch Mode?** Giữ WebSocket liên tục 24/7 dễ bị TradingView ban IP.

Tính năng chính:
- Overflow buffer: queue đầy → RAM buffer → SQLite spool (durable)
- Watermark: không bao giờ lưu duplicate bar
- Auth fallback: Token → Cookie → Guest (cảnh báo nếu rơi xuống Guest)
- Backlog tracking: nếu miss nhiều batch → yêu cầu thêm bar để bù khoảng trống

### `04_checker.py` — Data Quality & Auto-Repair

Chạy mỗi 3 ngày. So sánh dữ liệu DB với TradingView (nguồn gốc).

Phát hiện:
- Nến bị thiếu (missing bars)
- Nến thừa (duplicate/extra bars)
- OHLC sai số (chênh > 0.1% so với TV)
- Continuity gap ở TF trực tiếp và phái sinh

Quy trình 3 giai đoạn: **Scan** → **Confirm** (Discord, one-way) → **Repair** (xóa sai → kéo lại → verify).

### `03_chart.py` — Chart Dashboard nội bộ

Flask REST API + HTML frontend. Dùng để kiểm tra nhanh chất lượng dữ liệu và xem indicator.

```bash
python data_provider/03_chart.py
# Mở: http://127.0.0.1:8050
```

API: `/api/symbols`, `/api/timeframes`, `/api/candles?symbol=X&tf=Y&bars=N&ind=MA20,BB,MACD`

---

## 5. Modules — Thư viện dùng chung (legacy)

Nằm ở `modules/`. Đây là layer cũ hơn, được dùng bởi data provider và chart. `core_python/shared/data.py` là adapter mới ngăn strategy layer gọi thẳng vào đây.

| File | Chức năng |
|------|-----------|
| `db_connector.py` | Kết nối SQL Server (pyodbc), insert/merge staging, ETL vào Fact_OHLCV |
| `data_loader.py` | Query OHLCV từ DWH, trả DataFrame với schema chuẩn |
| `indicators.py` | Công thức indicator dùng chung: SMA, EMA, Bollinger, VWAP, MACD, ATR |
| `chart_builder.py` | Helper tạo chart cho dashboard |

> **Lưu ý**: `modules/indicators.py` được dùng bởi cả chart lẫn `core_python` (qua `add_combo_indicators()`). Sửa công thức ở đây ảnh hưởng đồng thời đến signal và backtest.

---

## 6. Core Python — Nền tảng chiến lược

Nằm ở `core_python/`. Đây là phần research/backtest chính, độc lập với data pipeline.

### `core_python/shared/` — Shared Infrastructure

| File | Chức năng |
|------|-----------|
| `contracts.py` | Typed result containers: `SymbolBacktestResult`, `PortfolioBacktestResult`, `PortfolioWindowResult`, `Position`, `OrderIntent`, `BacktestReplayEvent` |
| `analytics.py` | `calc_metrics()`, `run_monte_carlo()` (block bootstrap), `check_portfolio_ftmo()`, `build_combined_equity()` |
| `market.py` | Specs 11 instruments: contract size, spread, slippage, swap rates, broker profiles |
| `data.py` | Data access layer — `load_backtest_ohlcv()`, `validate_backtest_data()` |
| `execution/primitives.py` | `calc_dynamic_slippage()`, `swap_cost()`, `risk_sized_lots_from_distance()`, adverse entry/exit price |
| `execution/engines/market.py` | Engine market order tổng quát (`backtest_market_symbol`) — dùng bởi MA Cross |
| `reporting.py` | Màu sắc, format số, style bảng dùng chung cho notebooks |
| `ctrader_export.py` | Xuất kết quả ra CSV format cTrader để validate cross-platform |
| `sessions.py` | Session filter, timezone helpers (UTC normalization) |
| `timeframes.py` | TF utilities: expected timedelta, frequency mapping |

### Mô hình equity (quan trọng)

Tất cả engine bar-by-bar đều dùng **Mark-to-Market (MTM)**:
- Mỗi bar tính `mark_equity = realized_equity + floating_pnl_if_position_open`
- Daily stop và max drawdown đều trigger dựa trên MTM equity
- `backtest_fast()` (optimizer approximation) là ngoại lệ — không có equity series đầy đủ

---

## 7. Chiến lược Combo

### Ý tưởng giao dịch

Chờ giá break out khỏi vùng MA crossover, vào lệnh pending order tại đỉnh/đáy của nến tín hiệu. Dùng ATR để định vị SL/TP. Có partial TP và trailing stop theo MA.

### Signal Generation (`signals.py`)

```
Điều kiện BUY:  MA(20) cross up (prev_close ≤ MA, close > MA)
                + MACD histogram dương
                + Nến hiện tại tăng
                + Trong session giao dịch (nếu bật filter)

Điều kiện SELL: MA(20) cross down
                + MACD histogram âm
                + Nến hiện tại giảm
```

Không có look-ahead bias: signal xác nhận tại close bar T → pending order đặt tại đỉnh/đáy bar T → khớp từ bar T+1 trở đi.

### Parameter chính (`config.py`)

| Tham số | Giá trị mặc định | Ý nghĩa |
|---------|-----------------|---------|
| `ktp` | 2.272 (Fibonacci) | Hệ số TP = ktp × ATR từ entry |
| `x` | 0.0 | Offset entry: `bar_high + x` cho BUY |
| `pending_ttl_bars` | 3 | Pending hết hạn sau N bar |
| `partial_tp_fraction` | 0.5 | Đóng 50% tại TP, còn lại trailing |
| `trailing_activation` | 1.0 | Bật trailing khi lời ≥ 1× ATR |
| `risk_per_trade` | 0.5% equity | Sizing theo rủi ro cố định |

### Engines

```
backtest_symbol()      — Full simulation, trade log đầy đủ, replay events
                         Pending order logic: fill khi high/low chạm entry
                         Partial TP → Breakeven SL → Trailing MA → Force-close

backtest_fast()        — Approximation cho optimizer (không có full trade log)
                         Tính score = PF × √max(ret,0) / max(maxdd,1%)

backtest_portfolio()   — Unified account: nhiều symbol, chia sẻ vốn chung
                         Daily stop & max DD tính ở cấp account (đúng cho FTMO)
                         Position size từ account_equity tổng × alloc × risk_pct
```

### Quy trình tối ưu hóa

```
1. Symbol Grid Search
   run_symbol_grid_search()
   → 15 combos (5 ktp × 3 x × 1 min_rr)
   → Engine: backtest_fast() (nhanh)
   → Score: PF × √return / maxDD

2. Symbol Walk-Forward (TRUE walk-forward)
   walk_forward_backtest()
   → Mỗi window: IS re-optimize → chọn best params → test OOS
   → Plateau stability check: stable_ratio ≥ 0.6
   → OOS/IS efficiency: cảnh báo nếu < 0.5
   → Warmup bars từ IS-tail để indicator ổn định đầu OOS

3. Portfolio Backtest
   run_portfolio_backtest()
   → Chạy backtest_portfolio() với params đã chọn
   → Kết quả: trade log, equity curve, FTMO metrics

4. Portfolio Walk-Forward
   walk_forward_portfolio()
   → Nhận symbol_params cố định từ ngoài
   → Không re-optimize per window — đánh giá OOS ở cấp portfolio
```

### Broker Profiles & Account Modes

| Mode | daily_loss_limit | max_drawdown_limit | Dùng khi |
|------|-----------------|-------------------|----------|
| `standard` | disabled (1.0) | disabled (1.0) | Research, backtest không giới hạn |
| `ftmo` | 5% | 10% | Mô phỏng điều kiện FTMO challenge |

---

## 8. Chiến lược MA Cross

### Ý tưởng giao dịch

MA crossover đơn giản (fast/slow SMA), nhưng thực thi theo mô hình **basket**: mở nhiều lệnh nhỏ theo hướng xu hướng, đóng toàn bộ basket khi đạt mục tiêu lợi nhuận hoặc thua lỗ tổng ($100 mặc định).

### Signal Generation (`signals.py`)

```
BUY signal:  fast_ma cross above slow_ma (fast_ma > slow_ma, prev_fast ≤ prev_slow)
SELL signal: fast_ma cross below slow_ma

Không look-ahead: signal tại close bar T → pending_signal → entry tại open bar T+1
```

### Parameter chính (`config.py`)

| Tham số | Giá trị mặc định | Ý nghĩa |
|---------|-----------------|---------|
| `fast_ma` | 10 | Period SMA nhanh |
| `slow_ma` | 20 | Period SMA chậm |
| `atr_stop_mult` | 2.0 | SL = entry ± 2×ATR |
| `atr_tp_mult` | 2.0 | TP = entry ± 2×ATR (0 = không dùng TP cố định) |
| `basket_take_profit` | $100 USD | Đóng toàn basket khi floating PnL ≥ +$100 |
| `basket_stop_loss` | $100 USD | Đóng toàn basket khi floating PnL ≤ -$100 |
| `max_orders` | 5 | Tối đa 5 lệnh trong basket |
| `max_total_lot` | 0.10 | Tổng lot tối đa trong basket |
| `reversal_only` | True | Không averaging cùng chiều (anti-martingale) |

### Lot sizing trong basket

Tuyến tính (không phải martingale):
```
order_1: initial_lot = 0.01
order_2: 0.01 + 1 × lot_step = 0.02
order_3: 0.01 + 2 × lot_step = 0.03  (= max_single_lot, cap ở đây)
```

### Engines

```
backtest_market_symbol()          — Market order, MTM equity
                                   Dùng bởi single-symbol backtest (non-basket)

backtest_basket_reversal_symbol() — Basket model, MTM equity
                                   Basket TP/SL check tại close mỗi bar
                                   reversal_only=True: không mở thêm cùng chiều
```

### Quy trình Walk-Forward

> **Lưu ý quan trọng**: `simple_walkforward()` KHÔNG phải true walk-forward.
> Nó chỉ chạy backtest với params cố định trên các window IS/OOS định sẵn.
> Không có bước re-optimize trên IS — đây là điểm thiếu so với Combo.

---

## 9. Quy trình nghiên cứu — Notebooks

Mỗi chiến lược có cùng một bộ 6 notebook theo thứ tự logic:

```
core_python/strategies/{combo,ma_cross}/research/
├── 01_symbol_backtest.ipynb      — Backtest đơn symbol, xem trade log, equity curve
├── 02_symbol_optimize.ipynb      — Grid search tham số cho từng symbol
├── 03_portfolio_backtest.ipynb   — Backtest portfolio (nhiều symbol, vốn chung)
├── 04_portfolio_walkforward.ipynb — Walk-forward OOS test ở cấp portfolio
├── 05_chart_and_signal_scanner.ipynb — Xem nến + indicator + signal trực quan
└── 06_ftmo_vs_standard.ipynb     — So sánh kết quả ở 2 chế độ account
```

### Thứ tự làm việc chuẩn

```
01 → Chọn symbol, chạy backtest đầu tiên, kiểm tra tín hiệu có hợp lý không
02 → Grid search để tìm tham số tốt nhất trên IS window
03 → Ghép các symbol tốt thành portfolio, kiểm tra drawdown cấp account
04 → Walk-forward để xác nhận params không overfit trên IS
05 → Inspect thủ công các tín hiệu cụ thể khi nghi ngờ
06 → So sánh hiệu quả Standard vs FTMO để quyết định chế độ vận hành
```

---

## 10. Vận hành 24/7

### PowerShell scripts (`ops/`)

| Script | Mục đích | Lịch chạy |
|--------|---------|-----------|
| `run_ws_live_forever.ps1` | Khởi động `02_ws_live.py`, tự restart nếu crash | Boot/logon, chạy mãi |
| `run_pipeline_daily.ps1` | Chạy `01_data_pipeline.py --mode gap` | Hàng ngày, 22:22 UTC |
| `run_checker_cycle.ps1` | Chạy `04_checker.py` | Mỗi 3 ngày |

### Thiết lập Windows Task Scheduler

```powershell
# ws_live: trigger = At startup
powershell -ExecutionPolicy Bypass -File ops\run_ws_live_forever.ps1

# pipeline: trigger = Daily 22:22
powershell -ExecutionPolicy Bypass -File ops\run_pipeline_daily.ps1

# checker: trigger = Every 3 days
powershell -ExecutionPolicy Bypass -File ops\run_checker_cycle.ps1
```

### Kiểm tra nhanh hệ thống đang hoạt động tốt

```
✓ data_provider/logs/ws_live.log    — cập nhật liên tục?
✓ data_provider/logs/pipeline.log  — entry mới nhất < 24h?
✓ data_provider/logs/checker.log   — không có REPAIR FAILED?
✓ SEN.ActiveTask (SQL)              — không có lock expired?
```

---

## 11. Test Suite

Nằm ở `tests/` — 200 test cases, hầu hết pass. Full suite có thể fail một số test do permission cache trên Windows (pytest temp dirs).

```bash
pytest                              # chạy toàn bộ
pytest tests/test_combo_replay.py  # chạy test cụ thể
pytest -k "portfolio"              # chạy test có từ khóa
```

| Test file | Phạm vi |
|-----------|---------|
| `test_combo_replay.py` | Replay events của Combo engine |
| `test_portfolio_backtest_integrity.py` | Tính toàn vẹn của portfolio engine |
| `test_ma_cross_basket_reversal.py` | Basket logic: lot sizing, exposure caps |
| `test_ma_cross_replay.py` | Replay events của MA Cross |
| `test_execution_safety.py` | Edge cases: zero lot, near-zero SL, empty data |
| `test_quality_and_robustness.py` | Monte Carlo, FTMO check, analytics |
| `test_sprint1/2/3_regression.py` | Regression tests theo từng sprint |
| `test_data_provider_resilience.py` | Data pipeline lỗi mạng, auth fail |

---

## 12. Cấu trúc thư mục đầy đủ

```
SEN05/
│
├── config.py                          ← Bảng điều khiển trung tâm (CHỈNH TẠI ĐÂY)
├── pyproject.toml                     ← Package config, Ruff, Pytest
├── requirements.txt                   ← Dependencies
├── .env                               ← Credentials (KHÔNG commit — thêm vào .gitignore)
│
├── data_provider/
│   ├── 00_sql/                        ← SQL scripts khởi tạo database
│   │   ├── 00_run_all.sql             ← Chạy toàn bộ 1 lần
│   │   ├── 01_setup_database.sql      ← Tạo database
│   │   ├── 02_core_tables.sql         ← Fact_OHLCV, Symbol, Timeframe
│   │   ├── 03_staging_tables.sql      ← SEN.TF_* staging
│   │   ├── 04_business_objects.sql    ← Stored procedures (usp_LoadDirect, usp_AggregateFromStaging)
│   │   └── 05_verify.sql             ← Kiểm tra sau setup
│   ├── 01_data_pipeline.py            ← Historical load + daily gap fill
│   ├── 02_ws_live.py                  ← Near-realtime updater (chạy 24/7)
│   ├── 03_chart.py                    ← Chart dashboard (Flask)
│   ├── 04_checker.py                  ← Data quality scan & repair
│   ├── _discord.py                    ← Discord notification helper
│   ├── _helpers.py                    ← Staging transition cleaner, gap utils
│   ├── _task_lock.py                  ← Distributed lock (SEN.ActiveTask)
│   ├── _tv_auth.py                    ← TradingView auth helper
│   └── _tv_coord.py                   ← TV connection coordination
│
├── modules/                           ← Shared library (legacy, dùng bởi data_provider)
│   ├── db_connector.py                ← SQL connection, staging insert, ETL
│   ├── data_loader.py                 ← Query OHLCV → DataFrame
│   ├── indicators.py                  ← SMA, EMA, Bollinger, MACD, ATR, VWAP
│   └── chart_builder.py              ← Chart helper
│
├── core_python/
│   ├── shared/
│   │   ├── contracts.py               ← Typed result containers
│   │   ├── analytics.py               ← calc_metrics, Monte Carlo, FTMO check
│   │   ├── market.py                  ← 11 symbols specs + broker profiles
│   │   ├── data.py                    ← Data access layer (wrapper trên modules/)
│   │   ├── reporting.py               ← Visual theme, format helpers
│   │   ├── ctrader_export.py          ← Export trade log → cTrader CSV
│   │   ├── sessions.py                ← Session filter, UTC helpers
│   │   ├── timeframes.py              ← TF utilities
│   │   └── execution/
│   │       ├── primitives.py          ← Slippage, swap, lot sizing
│   │       └── engines/
│   │           └── market.py          ← backtest_market_symbol (MA market orders)
│   │
│   └── strategies/
│       ├── combo/
│       │   ├── config.py              ← STRATEGY, ACCOUNT_MODES, OPTIMIZATION params
│       │   ├── signals.py             ← detect_combo_signals(), add_combo_indicators()
│       │   ├── execution.py           ← Thin wrappers: backtest_fast, backtest_portfolio
│       │   ├── engines/
│       │   │   ├── common.py          ← build_pending_order, _risk_lot_size
│       │   │   ├── symbol.py          ← backtest_symbol (full log, MTM equity)
│       │   │   ├── fast.py            ← backtest_fast (optimizer approximation)
│       │   │   └── portfolio.py       ← backtest_portfolio (unified account, MTM)
│       │   ├── symbol/
│       │   │   ├── backtest.py        ← run_symbol_backtest, build_symbol_signal_frame
│       │   │   ├── optimize.py        ← run_symbol_grid_search (15-combo grid)
│       │   │   ├── walkforward.py     ← walk_forward_backtest (TRUE IS/OOS re-optimize)
│       │   │   └── selection.py       ← Symbol selection helpers
│       │   ├── portfolio/
│       │   │   ├── backtest.py        ← run_portfolio_backtest
│       │   │   └── walkforward.py     ← walk_forward_portfolio (fixed params)
│       │   └── research/
│       │       ├── 01_symbol_backtest.ipynb
│       │       ├── 02_symbol_optimize.ipynb
│       │       ├── 03_portfolio_backtest.ipynb
│       │       ├── 04_portfolio_walkforward.ipynb
│       │       ├── 05_chart_and_signal_scanner.ipynb
│       │       └── 06_ftmo_vs_standard.ipynb
│       │
│       └── ma_cross/
│           ├── config.py              ← STRATEGY, BASKET, OPTIMIZATION params
│           ├── signals.py             ← detect_ma_cross_signals()
│           ├── basket.py              ← next_linear_lot, can_open_new_order, basket_snapshot
│           ├── execution_basket.py    ← backtest_basket_reversal_symbol (MTM)
│           ├── symbol/
│           │   ├── backtest.py        ← run_symbol_backtest
│           │   ├── optimize.py        ← run_symbol_grid_search
│           │   └── walkforward.py     ← simple_walkforward (KHÔNG phải true WF)
│           ├── portfolio/             ← Portfolio tools cho MA Cross
│           └── research/
│               ├── 01_symbol_backtest.ipynb
│               ├── 02_symbol_optimize.ipynb
│               ├── 03_portfolio_backtest.ipynb
│               ├── 04_portfolio_walkforward.ipynb
│               ├── 05_chart_and_signal_scanner.ipynb
│               └── 06_ftmo_vs_standard.ipynb
│
├── ops/
│   ├── README_24x7.md                 ← Hướng dẫn vận hành 24/7
│   ├── run_ws_live_forever.ps1        ← Wrapper chạy ws_live + auto-restart
│   ├── run_pipeline_daily.ps1         ← Wrapper chạy pipeline daily
│   ├── run_checker_cycle.ps1          ← Wrapper chạy checker
│   └── run_combo_chart.ps1            ← Khởi động chart dashboard
│
├── tests/                             ← 200 test cases
│   ├── test_combo_replay.py
│   ├── test_portfolio_backtest_integrity.py
│   ├── test_ma_cross_basket_reversal.py
│   ├── test_execution_safety.py
│   ├── test_quality_and_robustness.py
│   └── ... (11 files khác)
│
└── output/                            ← Kết quả backtest, CSV xuất ra
```

---

## Tóm tắt nhanh (Quick Reference)

| Muốn làm gì | Chạy / Mở |
|------------|-----------|
| Load lịch sử lần đầu | `python data_provider/01_data_pipeline.py --mode full` |
| Gap fill hàng ngày | `python data_provider/01_data_pipeline.py --mode gap` |
| Bật live updater | `python data_provider/02_ws_live.py` |
| Kiểm tra data | `python data_provider/04_checker.py --dry-run` |
| Xem chart nến | `python data_provider/03_chart.py` → http://127.0.0.1:8050 |
| Backtest Combo | Mở `combo/research/01_symbol_backtest.ipynb` |
| Tối ưu tham số Combo | Mở `combo/research/02_symbol_optimize.ipynb` |
| Walk-forward Combo | Mở `combo/research/04_portfolio_walkforward.ipynb` |
| Chạy test suite | `pytest` |
| Chỉnh symbols/TF | Sửa `config.py` |
