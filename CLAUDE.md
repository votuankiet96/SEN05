# SEN05 Auto Trading System

## Tổng quan dự án

**Chiến lược**: Combo v2 — Mean-reversion quanh MA trên khung H4, xác nhận bởi MACD, tự đảo chiều khi xuất hiện tín hiệu ngược.

**Luồng dữ liệu**: TradingView → SQL Server (staging → fact) → Python backtest/signals → Streamlit dashboard.

**Tài sản**: 37 symbols — indices (US30, J225, HK50…), forex (EURUSD, GBPUSD…), kim loại (GOLD), crypto (BTCUSD). Tất cả qua Capital.com CFDs trên TradingView.

> **LƯU Ý:** Chủ dự án KHÔNG biết code. Mọi thay đổi phải giải thích bằng tiếng Việt đơn giản, an toàn, không phá hỏng hệ thống hiện tại.

---

## Trạng thái hiện tại

| Thành phần | Trạng thái |
|---|---|
| Data pipeline (TradingView → DB) | ✅ Hoạt động |
| Backtesting engine | ✅ Chạy được — nhưng có bugs nghiêm trọng |
| Signal dashboard (Streamlit) | ✅ Hoạt động |
| Walk-forward optimization | ✅ Có framework — cần fix bugs trước |
| Unit tests | ❌ Chưa có (Phase 3) |
| **Live trading** | 🚫 **CHƯA SẴN SÀNG** — phải fix 3 bugs CRITICAL trước |

---

## Workflow bắt buộc

1. **Trước khi thay đổi strategy** → đọc `STRATEGY_PRINCIPLES.md` hoặc gõ `/review-change`
2. **Làm việc trên branch riêng** — KHÔNG commit trực tiếp vào `main`
3. **Sau khi viết code xong** → gõ `/check-code` để lint + format + tạo PR
4. **Review PR** → merge vào main

---

## Cấu trúc thư mục

```
config.py                          # Bảng điều khiển trung tâm: DB credentials, 37 symbols,
                                   #   timeframes, bar counts, TV auth
modules/                           # Thư viện dùng chung (imported bởi tất cả pipelines)
  db_connector.py                  # Kết nối SQL Server, retry logic, ETL staging→fact
  data_loader.py                   # Load OHLCV từ DB thành pandas DataFrame
  indicators.py                    # SMA, EMA, RSI, MACD, ATR, Bollinger, VWAP
  chart_builder.py                 # Tạo chart Plotly

data_provider/                     # Pipeline dữ liệu
  01_data_pipeline.py              # Tải lịch sử (full) + backfill hàng ngày (gap)
  02_ws_live.py                    # WebSocket cập nhật realtime 24/7 (mỗi 5 phút)
  03_chart.py                      # Flask chart dashboard (http://localhost:8050)
  04_checker.py                    # Kiểm tra & tự sửa dữ liệu so với TV (mỗi 3 ngày)
  archive/                         # Script đã retire: 02_gap_fill.py, 05_reconcile.py
  00_sql/                          # Schema SQL Server + stored procedures

core_python/strategies/combo/
  core/
    execution.py                   # ⚠️ Bar-by-bar simulation: mở/đóng lệnh (có 2 bugs CRITICAL)
    backtest_engine.py             # Wrapper: load data → calc signals → execute
    metrics.py                     # ⚠️ KPI: Sharpe, Sortino, PF, Drawdown (có bug inflation)
    strategy_config.py             # Single source of truth cho tất cả tham số chiến lược
    walk_forward.py                # Walk-forward optimization (IS/OOS rolling windows)
    monte_carlo.py                 # ⚠️ Monte Carlo simulation (có bug bootstrap)
    scan_pipeline.py               # Quét tín hiệu nhiều symbol song song
    reversal_scanner.py            # Phát hiện reversal signals
  deploy/
    signal_dashboard.py            # Streamlit dashboard xem signal live
  research/
    01_backtest.ipynb              # Notebook backtest portfolio
    02_wf_optimizer.ipynb          # Notebook tối ưu tham số

.claude/commands/                  # Custom skills cho Claude Code
STRATEGY_PRINCIPLES.md             # "Hiến pháp" chiến lược — BẮT BUỘC đọc trước khi thay đổi
IMPROVEMENT_TASKS.md               # Roadmap 4 Phase, 13 Tasks — chi tiết từng bug và cách fix
```

---

## Bugs đã biết — CHƯA FIX

> Chi tiết đầy đủ và hướng dẫn fix từng bước trong `IMPROVEMENT_TASKS.md`

### 🔴 CRITICAL (phải fix trước khi live trading)

**Bug 1: Look-ahead bias** — `execution.py` dòng 114–118, 480–483
- Pending order fill ngay trên cùng bar khi giá chạm entry
- Thực tế: order chỉ fill được ở bar tiếp theo (T+1) cộng slippage
- **Hậu quả**: Backtest lạc quan hơn thực tế 5–20%

**Bug 2: MA trailing phá breakeven** — `execution.py` dòng 172, 188–193
- Sau partial TP, SL về breakeven. Nhưng MA trailing có thể kéo SL xuống dưới breakeven
- **Hậu quả**: Trades "đã bảo vệ" vẫn có thể lỗ — vi phạm risk management
- Fix: thêm guard `new_sl = max(new_sl, breakeven)` cho LONG, `min` cho SHORT

**Bug 3: Sharpe/Sortino inflate 38×** — `metrics.py` dòng 75–80
- Annualize bằng `sqrt(1512)` bất kể độ dài data
- Ví dụ: 3 tháng data → Sharpe thực 0.5 hiển thị thành 19.4
- **Hậu quả**: Metrics hoàn toàn không tin cậy để ra quyết định

### 🟠 HIGH

**Bug 4: Monte Carlo phá serial correlation** — `monte_carlo.py` dòng 54
- Dùng `np.random.permutation()` (random shuffle) phá vỡ tương quan chuỗi tự nhiên
- **Hậu quả**: Confidence interval quá hẹp — overconfident về future drawdown
- Fix: dùng stationary block bootstrap thay thế

**Bug 5: Walk-forward windows quá ngắn** — `walk_forward.py` dòng 160–163
- IS 5000 bars (~7 tháng), OOS 1250 bars (~35 ngày)
- **Hậu quả**: Dễ overfit, kết quả OOS không đáng tin

---

## Tham số chiến lược hiện tại

> Nguồn duy nhất: `core_python/strategies/combo/core/strategy_config.py`
> Phạm vi an toàn: xem `STRATEGY_PRINCIPLES.md`

| Tham số | Giá trị | Phạm vi an toàn |
|---|---|---|
| Timeframe | H4 | — |
| MA period | 20 (US30/J225: 25) | 10 – 50 |
| MACD | (5, 25, 5) | — |
| ATR period | 5 | — |
| kTP (take profit) | 2.8 (US30), 1.8 (J225) | 1.5 – 3.5 |
| Min R:R | 1.25 | 1.0 – 2.0 |
| Risk/trade | 0.5% | 0.2% – 1.0% |
| Partial TP | 50% fixed + 50% trailing | ≥ 40% |
| Trailing activation | 1.0× ATR (varies) | 0.5 – 2.0× ATR |
| Pending TTL | 3 bars | 1 – 5 |
| Daily loss limit | 5% | — (cứng, FTMO rule) |
| Max drawdown | 10% | — (cứng, FTMO rule) |

---

## Database schema

```
SQL Server: SEN05_AutoTrading (localhost, ODBC Driver 17)

Staging (nhận data thô từ TradingView):
  SEN.TF_W, TF_D1, TF_H4, TF_H3, TF_H2, TF_H1
  SEN.TF_M45, TF_M30, TF_M15, TF_M5

Fact (data sạch, đã validate):
  DWH.Fact_OHLCV          # Bảng chính: OHLCV theo SymbolID + TimeframeID + BarTime
  DWH.Dim_Symbol           # 37 symbols
  DWH.Dim_Timeframe        # Mapping timeframe → minutes

Stored procedures:
  usp_LoadDirect            # Load trực tiếp vào fact (W, D1)
  usp_AggregateFromStaging  # Aggregate staging → fact
```

Auth: ưu tiên Windows Authentication, fallback SQL Auth từ `.env`.

---

## Quy tắc code bắt buộc

1. **Credentials** — dùng `os.environ` hoặc `.env`. KHÔNG BAO GIỜ hardcode password/token
2. **Database** — mọi thay đổi DB phải có `transaction` + `rollback` trong `except`
3. **DB pattern** — theo chuẩn `modules/db_connector.py`: `get_connection()` → try/commit → except rollback → finally close
4. **Logging** — dùng `logging` module. KHÔNG dùng `print()` trong production code
5. **Error handling** — mọi DB function phải có `try/except/finally` với `conn.close()` trong `finally`
6. **Imports** — KHÔNG import `tvDatafeed` ở top-level. Dùng deferred import như trong `config.py`
7. **SQL** — KHÔNG format string vào SQL. Dùng parameterized queries (`?` với pyodbc)

---

## Anti-patterns cần tránh

| Anti-pattern | Lý do |
|---|---|
| Fill order trên cùng bar giá chạm entry | Look-ahead bias — phải dùng bar T+1 |
| `.shift(-1)` hoặc lọc ngược thời gian trong backtest | Future data leak |
| Annualize Sharpe không kiểm tra sample size | Inflation — kiểm tra `>= 1/4 năm` trước |
| Format string trực tiếp vào SQL | SQL injection |
| Ghi DB từ nhiều thread không có transaction isolation | Race condition |

---

## Coding conventions

- **Linter/Formatter**: Ruff (config trong `pyproject.toml`)
- **Line length**: 100
- **Python**: >= 3.10
- **Imports order**: isort, `known-first-party = ["modules", "data_provider", "core_python", "config"]`
- **Comments**: Tiếng Việt cho business logic, tiếng Anh cho technical comments

---

## Cách chạy

```bash
# Tải dữ liệu lịch sử lần đầu (chạy 1 lần, mất 2-4 tiếng)
python data_provider/01_data_pipeline.py --mode full

# Backfill hàng ngày (3-8 phút)
python data_provider/01_data_pipeline.py --mode gap

# Dry-run kiểm tra không ghi DB
python data_provider/01_data_pipeline.py --mode dry-run

# WebSocket cập nhật realtime 24/7
python data_provider/02_ws_live.py

# Xem chart tương tác
python data_provider/03_chart.py           # http://localhost:8050

# Kiểm tra & tự sửa dữ liệu (chạy mỗi 3 ngày hoặc thủ công)
python data_provider/04_checker.py                    # full autonomous repair
python data_provider/04_checker.py --dry-run          # scan only, không ghi DB

# Signal dashboard
streamlit run core_python/strategies/combo/deploy/signal_dashboard.py
```

---

## Roadmap cải tiến

> Chi tiết từng task trong `IMPROVEMENT_TASKS.md`

| Phase | Mục tiêu | Số tasks | Ưu tiên |
|---|---|---|---|
| Phase 1 | Fix 4 bugs critical/high | 4 tasks | 🔴 Trước khi live |
| Phase 2 | Bảo vệ khỏi overfitting (walk-forward) | 3 tasks | 🟠 Ngay sau Phase 1 |
| Phase 3 | Unit tests (pytest) | 4 tasks | 🟠 Song song Phase 2 |
| Phase 4 | DB optimization + infra | 3 tasks | 🟡 Khi cần scale |

---

## Skills có sẵn

| Lệnh | Dùng khi nào |
|---|---|
| `/review-change` | Trước khi thay đổi bất kỳ thứ gì liên quan strategy |
| `/check-code` | Sau khi viết/sửa code xong — lint + format + tạo PR |
| `/data-check` | Kiểm tra chất lượng data trước khi chạy backtest |
| `/backtest-review` | Đánh giá kết quả backtest, cảnh báo các bugs ảnh hưởng |
| `/diagnose` | Khi có lỗi xảy ra — chẩn đoán và hướng dẫn fix |

---

## Tài liệu quan trọng

- **`STRATEGY_PRINCIPLES.md`** — "Hiến pháp" chiến lược: quy tắc bất biến, phạm vi tham số an toàn. **BẮT BUỘC đọc trước khi thay đổi strategy.**
- **`IMPROVEMENT_TASKS.md`** — Chi tiết 13 tasks: mô tả bug, code cần sửa từng dòng, cách verify sau fix.
