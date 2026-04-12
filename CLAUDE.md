# SEN05 Auto Trading System

## Giới thiệu

Hệ thống auto trading: lấy dữ liệu từ TradingView, lưu SQL Server, backtest chiến lược, chạy signal dashboard.

**LƯU Ý QUAN TRỌNG:** Chủ dự án KHÔNG biết code. Mọi thay đổi phải:
- Giải thích bằng tiếng Việt đơn giản
- An toàn, không phá hỏng hệ thống hiện tại
- Tuân thủ conventions bên dưới

## Workflow bắt buộc

1. Trước khi thay đổi strategy → **đọc `STRATEGY_PRINCIPLES.md`** hoặc gõ `/review-change`
2. Làm việc trên **branch riêng** (không commit trực tiếp vào main)
3. Sau khi viết code xong → gõ `/check-code` ��ể lint + format + tạo PR
4. Review PR → merge vào main

## Cấu trúc thư mục

```
config.py                    # Bảng điều khiển trung tâm (credentials, symbols, timeframes)
modules/                     # Thư viện dùng chung
  db_connector.py            # Kết nối SQL Server, ETL, staging → fact
  data_loader.py             # Load DataFrame từ DB
  indicators.py              # Chỉ báo kỹ thuật: SMA, EMA, MACD, ATR, RSI
  chart_builder.py           # Tạo chart Plotly
data_provider/               # Pipeline dữ liệu
  01_data_pipeline.py        # Tải lịch sử + backfill hàng ngày
  02_gap_fill.py             # Phát hiện & lấp lỗ hổng data
  03_ws_live.py              # WebSocket cập nhật realtime
  04_chart.py                # Dash server xem chart
  00_sql/                    # Schema + stored procedures SQL Server
core_python/strategies/combo/
  core/                      # Engine chiến lược
    execution.py             # Logic mở/đóng lệnh (có bugs critical - xem bên dưới)
    backtest_engine.py       # Chạy backtest
    metrics.py               # Tính KPI: Sharpe, Sortino, PF, DD (có bug - xem bên dưới)
    strategy_config.py       # Tham số chiến lược (single source of truth)
    walk_forward.py          # Walk-forward optimization
    monte_carlo.py           # Monte Carlo simulation
    scan_pipeline.py         # Pipeline quét tín hiệu
    reversal_scanner.py      # Phát hiện reversal
  deploy/
    signal_dashboard.py      # Streamlit dashboard xem signal
  research/
    01_backtest.ipynb        # Notebook backtest portfolio
    02_wf_optimizer.ipynb    # Notebook tối ưu tham số
```

## Bugs đã biết (CHƯA FIX)

**Xem chi tiết trong `IMPROVEMENT_TASKS.md`**

### CRITICAL (phải fix trước khi live trading):

1. **Look-ahead bias** (`core_python/strategies/combo/core/execution.py` line 114-118, 480-483)
   - Pending order fill trên cùng bar thay vì bar tiếp theo (T+1)
   - Backtest lạc quan hơn thực tế 5-20%

2. **MA trailing phá breakeven** (`core_python/strategies/combo/core/execution.py` line 172, 188-193)
   - Sau partial TP, SL về breakeven, nhưng MA trailing có thể kéo SL qua breakeven
   - Trades "đã bảo vệ" vẫn có thể lỗ

3. **Sharpe/Sortino inflate 38x** (`core_python/strategies/combo/core/metrics.py` line 75-80)
   - Annualize bằng sqrt(1512) bất kể sample size
   - 3 tháng data: Sharpe thực 0.5 hiển thị thành 19.4

### HIGH:

4. **Monte Carlo phá serial correlation** (`core_python/strategies/combo/core/monte_carlo.py` line 54)
   - Dùng random shuffle thay vì block bootstrap
   - Confidence interval quá hẹp (overconfident)

5. **Walk-forward windows quá ngắn** (`core_python/strategies/combo/core/walk_forward.py` line 160-163)
   - IS 5000 bars (~7 tháng), OOS 1250 bars (~35 ngày) → dễ overfitting

## Quy tắc code bắt buộc

1. **Credentials**: Dùng `os.environ` hoặc `.env` — KHÔNG BAO GIỜ hardcode passwords/tokens
2. **Database**: Mọi thay đổi DB phải có transaction + rollback trong except block
3. **Logging**: Dùng `logging` module — KHÔNG dùng `print()` trong production code
4. **Error handling**: Mọi DB function phải có `try/except/finally` với `conn.close()` trong finally
5. **DB pattern**: Theo chuẩn trong `modules/db_connector.py` — `get_connection()` → try/commit/except rollback/finally close
6. **Imports**: Không import trực tiếp `tvDatafeed` ở top-level (dùng deferred import như trong `config.py`)

## Anti-patterns cần tránh

1. **Look-ahead bias**: KHÔNG dùng bar hiện tại để fill order — phải dùng bar tiếp theo
2. **Future data leak**: KHÔNG dùng `.shift(-1)` hoặc lọc ngược thời gian trong backtest
3. **Sharpe inflation**: Khi annualize, kiểm tra sample size >= 1/4 năm cho timeframe đó
4. **SQL injection**: KHÔNG format string trực tiếp vào SQL — dùng parameterized queries `?`
5. **Race condition**: Khi ghi DB từ nhiều thread, PHẢI dùng transaction isolation

## Coding conventions

- **Linter/Formatter**: Ruff (config trong `pyproject.toml`)
- **Line length**: 100
- **Python**: >= 3.10
- **Imports order**: isort với `known-first-party = ["modules", "data_provider", "core_python", "config"]`
- **Comments**: Tiếng Việt cho business logic, tiếng Anh cho technical comments

## Cách chạy

```bash
# Tải dữ liệu lịch sử (chạy 1 lần, mất 2-4 tiếng)
python data_provider/01_data_pipeline.py --mode full

# Backfill hàng ngày (3-8 phút)
python data_provider/01_data_pipeline.py --mode gap

# Dry-run (kiểm tra không ghi DB)
python data_provider/01_data_pipeline.py --mode dry-run

# Lấp lỗ hổng data
python data_provider/02_gap_fill.py

# WebSocket cập nhật realtime
python data_provider/03_ws_live.py

# Xem chart tương tác (http://localhost:8050)
python data_provider/04_chart.py

# Signal dashboard (Streamlit)
streamlit run core_python/strategies/combo/deploy/signal_dashboard.py
```

## Tài liệu quan trọng

- **`STRATEGY_PRINCIPLES.md`** — "Hiến pháp" chiến lược: nguyên tắc không được vi phạm, phạm vi tham số an toàn, quy trình thay đổi. **BẮT BUỘC đọc trước khi thay đổi strategy.**
- **`IMPROVEMENT_TASKS.md`** — Danh sách bugs và tasks cải tiến (4 Phase, 13 Tasks)

## Skills có sẵn

- `/review-change` — Kiểm tra yêu cầu thay đổi có phù hợp nguyên tắc strategy không
- `/check-code` — Chạy lint + format, hướng dẫn tạo PR
- `/data-check` — Kiểm tra dữ liệu trước backtest
- `/backtest-review` — Đánh giá kết quả backtest, cảnh báo bug
- `/diagnose` — Chẩn đoán khi có lỗi xảy ra
