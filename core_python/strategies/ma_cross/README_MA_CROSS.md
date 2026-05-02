# Chiến Lược MA Cross

`strategies/ma_cross/` chứa chiến lược giao cắt fast/slow moving average. Tín hiệu được xác nhận ở close của bar, còn lệnh market được mô phỏng tại open của bar kế tiếp.

Chiến lược này tách riêng khỏi Combo vì Combo dùng pending breakout order, còn MA Cross dùng market-order execution và có thêm mô hình basket reversal.

## Chiến Lược Làm Gì

Mặc định:

- fast MA = SMA 10;
- slow MA = SMA 20;
- ATR = 14;
- BUY khi fast MA cắt lên slow MA;
- SELL khi fast MA cắt xuống slow MA;
- tín hiệu tại bar T chỉ được execute ở open bar T+1.

Có thể test EMA bằng `indicator_overrides={"MA_TYPE": "ema"}`.

## Hai Mô Hình Execution

### 1. `market_single`

Đây là mô hình market-order một vị thế chính:

- entry ở next-bar open sau khi signal được xác nhận;
- entry/exit có spread và dynamic slippage;
- SL dựa trên ATR, mặc định `2.0 * ATR14`;
- TP dựa trên ATR, mặc định `2.0 * ATR14`;
- partial TP mặc định đóng 50%;
- sau partial TP, SL có thể dời về breakeven và trailing theo `slow_ma`;
- opposite signal đóng/reverse ở next-bar open;
- nếu cùng một OHLC bar chạm cả SL và TP, engine ưu tiên SL theo hướng bảo thủ;
- equity curve ghi mark-to-market floating PnL.

Engine chính: `shared/execution/engines/market.py::backtest_market_symbol()`.

### 2. `basket_reversal`

Đây là mô hình basket nhiều order:

- opposite signal thêm leg mới thay vì đóng ngay leg cũ;
- lot tăng tuyến tính: `initial_lot + order_index * lot_step`;
- có giới hạn `max_orders`, `max_single_lot`, `max_total_lot`, `max_net_lot`;
- mặc định `reversal_only=True`, không cho stack thêm cùng chiều;
- basket đóng khi floating PnL đạt TP/SL basket;
- equity curve ghi mark-to-market tại bar close.

Engine chính: `execution_basket.py::backtest_basket_reversal_symbol()`.

## Cấu Trúc Thư Mục

```text
ma_cross/
├── config.py                         tham số MA Cross, basket, filters
├── signals.py                        indicator và crossover signals
├── basket.py                         BasketOrder, snapshot, exposure caps
├── execution_basket.py               basket reversal engine
├── chart.py                          chart/replay visual
├── symbol/
│   ├── backtest.py                   run_symbol_backtest()
│   ├── optimize.py                   run_symbol_grid_search()
│   ├── selection.py                  xếp hạng kết quả grid
│   └── walkforward.py                fixed-parameter window evaluation
├── portfolio/
│   └── optimize.py                   chạy grid search độc lập theo symbol
├── research/                         notebooks
└── research_utils/                   dashboard, replay, export helpers
```

## File Nào Là Source Of Truth

- Strategy defaults: `config.py::STRATEGY`.
- Basket defaults: `config.py::BASKET`.
- Entry filters: `config.py::FILTERS`.
- Indicator/signal logic: `signals.py`.
- Basket state/exposure rules: `basket.py`.
- Basket execution: `execution_basket.py`.
- Market-single execution: `shared/execution/engines/market.py`.

## Luồng Single-Symbol

```text
load_backtest_ohlcv()
  -> add_ma_cross_indicators()
  -> detect_ma_cross_signals()
  -> add_entry_filter_columns()
  -> chọn engine theo execution_model
  -> calc_metrics()
  -> SymbolBacktestResult
```

Ví dụ:

```python
from core_python.strategies.ma_cross import run_symbol_backtest

result = run_symbol_backtest(
    "US30",
    tf="M30",
    account_mode="standard",
    date_from="2023-01-01",
    date_to="2024-01-01",
)

print(result.metrics)
```

## Optimize

`symbol/optimize.py::run_symbol_grid_search()` chạy grid search bằng full backtest path. Search space mặc định gồm:

- `FAST_MA`;
- `SLOW_MA`;
- `MA_TYPE`;
- `atr_stop_mult`;
- `atr_tp_mult`;
- timeframe.

Optimizer yêu cầu window thời gian rõ ràng. Nếu cả `date_from` và `date_to` đều `None`, hàm raise `ValueError`, trừ khi truyền `allow_full_history=True`.

## Walk-Forward Hiện Tại

`symbol/walkforward.py::simple_walkforward()` hiện là **fixed-parameter window evaluation**:

- nhận danh sách window `(IS_from, IS_to, OOS_from, OOS_to)`;
- chạy cùng bộ params trên IS và OOS;
- không optimize trên IS;
- không chọn best params theo từng window;
- không có plateau stability check.

Vì vậy không nên xem `simple_walkforward()` là true walk-forward optimization. Nếu cần đánh giá MA Cross nghiêm túc, cần thêm một workflow tương tự Combo: optimize trên IS rồi apply best params sang OOS.

## Hạn Chế Hiện Tại

- Basket TP/SL đang là ngưỡng USD tuyệt đối (`100.0`) nên phụ thuộc account size.
- Basket TP/SL hiện check tại bar close, chưa kiểm tra intrabar high/low.
- Basket trade log đang ghi `r_multiple = 0.0`, nên `avg_r` không có ý nghĩa cho basket.
- `ma_gap_atr` được tính để phục vụ chart/dashboard nhưng chưa là filter mặc định trong signal logic.
- Broker specs và swap rates từ `shared/market.py` cần được verify trước khi dùng kết quả cho quyết định thật.

## Chart Và Research

Chart:

```powershell
.\.venv\Scripts\python.exe core_python\strategies\ma_cross\chart.py --port 8514
```

Notebook nghiên cứu nằm ở `research/`. Tài liệu riêng: [README_MA_CROSS_RESEARCH.md](research/README_MA_CROSS_RESEARCH.md).

## Khi Nào Dùng File Nào

| Việc cần làm | File/entry point |
|---|---|
| Chạy backtest một symbol | `symbol/backtest.py::run_symbol_backtest()` |
| Tối ưu một symbol | `symbol/optimize.py::run_symbol_grid_search()` |
| Đánh giá fixed params trên nhiều window | `symbol/walkforward.py::simple_walkforward()` |
| Chạy basket reversal | `execution_model="basket_reversal"` qua `run_symbol_backtest()` |
| Kiểm tra exposure basket | `basket.py` |
| Xem chart/replay | `chart.py`, `research_utils/replay.py` |
