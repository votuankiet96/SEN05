# Chiến Lược Combo

Thư mục `strategies/combo/` chứa toàn bộ phần đặc thù của chiến lược Combo: cấu hình, logic tín hiệu, engine pending breakout, backtest một symbol, backtest danh mục, optimizer, walk-forward và research notebooks.

Tên gọi chuẩn trong tài liệu là **Combo**.

## Chiến Lược Làm Gì

Combo là chiến lược pending breakout. Tại close của mỗi bar, hệ thống kiểm tra tín hiệu. Nếu tín hiệu hợp lệ, engine tạo pending stop order cho bar sau:

- BUY: entry = `bar_high + x`, SL = `bar_low - x`.
- SELL: entry = `bar_low - x`, SL = `bar_high + x`.
- TP = `entry +/- ktp * ATR`.
- Pending order có TTL mặc định 3 bars.

Sau khi vào lệnh:

- lot size dựa trên risk theo equity và khoảng cách SL;
- có tính spread, dynamic slippage, commission và swap;
- có partial TP;
- sau partial TP, SL dời về breakeven và trailing có thể chạy theo MA;
- tín hiệu ngược chiều có thể đóng vị thế hiện tại ở open bar kế tiếp và tạo pending mới.

## Điều Kiện Tín Hiệu

Tín hiệu được định nghĩa trong `signals.py`.

BUY cần các điều kiện chính:

- `prev_close <= prev_ma` và `close > ma`;
- nến tăng: `close > open`;
- MACD histogram dương;
- session filter pass;
- R:R pass nếu `MIN_RR` không phải `None`.

SELL cần các điều kiện chính:

- `prev_close >= prev_ma` và hiện tại không ở trên MA;
- nến giảm: `close < open`;
- MACD histogram âm;
- session filter pass;
- R:R pass nếu `MIN_RR` không phải `None`.

Ghi chú audit: `cross_down` hiện dùng `~(close > ma)`, nghĩa là bao gồm trường hợp `close == ma`. Đây là điểm bất đối xứng với BUY và nên được sửa trong một bước riêng nếu muốn strict symmetry.

## Cấu Trúc Thư Mục

```text
combo/
├── config.py                         nguồn tham số Combo
├── universe.py                       danh sách symbol Combo
├── signals.py                        indicator, session mask, signal generation
├── orders.py                         tạo order intent cho pending breakout
├── execution.py                      facade export các engine chính
├── engines/
│   ├── common.py                     helper pending order, sizing, position
│   ├── symbol.py                     full single-symbol engine
│   ├── fast.py                       fast optimizer approximation
│   └── portfolio.py                  unified-account portfolio engine
├── symbol/
│   ├── backtest.py                   run_symbol_backtest()
│   ├── optimize.py                   run_symbol_grid_search()
│   ├── walkforward.py                true IS/OOS symbol walk-forward
│   └── selection.py                  lọc và xếp hạng optimizer results
├── portfolio/
│   ├── backtest.py                   run_portfolio_backtest()
│   └── walkforward.py                portfolio OOS với fixed params
├── research/                         notebooks
└── research_utils/                   dashboard, export, replay, chart helpers
```

## File Nào Là Source Of Truth

- Tham số chiến lược: `config.py`.
- Symbol universe riêng của Combo: `universe.py` và `COMBO_SYMBOL_PARAMS` trong `config.py`.
- Logic tín hiệu: `signals.py`.
- Công thức pending breakout: `engines/common.py` và `orders.py`.
- Full execution một symbol: `engines/symbol.py`.
- Execution danh mục: `engines/portfolio.py`.

Các file wrapper/facade như `execution.py` tồn tại để giữ import path gọn và tương thích với notebooks.

## Luồng Single-Symbol

```text
load_backtest_full()
  -> add_combo_indicators()
  -> detect_combo_signals()
  -> backtest_symbol()
  -> calc_metrics()
  -> SymbolBacktestResult
```

Entry point thường dùng:

```python
from core_python.strategies.combo import run_symbol_backtest

result = run_symbol_backtest(
    "US30",
    date_from="2023-01-01",
    date_to="2024-01-01",
    account_mode="standard",
)

print(result.metrics)
print(result.trades)
```

## Luồng Optimize

`symbol/optimize.py::run_symbol_grid_search()` chạy grid nhỏ trên `ktp`, `x`, `min_rr` bằng `backtest_fast()`.

Quan trọng:

- Optimizer yêu cầu window thời gian rõ ràng.
- Nếu `date_from=None` và `date_to=None`, hàm raise `ValueError`, trừ khi truyền `allow_full_history=True`.
- `backtest_fast()` là approximation để ranking, không phải kết quả cuối.
- Candidate tốt nên được validate lại bằng `backtest_symbol()`.

Ví dụ:

```python
from core_python.strategies.combo.symbol.optimize import run_symbol_grid_search

grid = run_symbol_grid_search(
    "US30",
    date_from="2022-01-01",
    date_to="2024-01-01",
)
```

## Walk-Forward

Combo có true symbol-level walk-forward trong `symbol/walkforward.py`:

- mỗi window lấy IS slice;
- optimize params trên IS bằng fast engine;
- kiểm tra plateau stability;
- áp dụng best params lên OOS;
- tổng hợp OOS metrics.

Portfolio walk-forward trong `portfolio/walkforward.py` thì khác: nó nhận `symbol_params` từ bên ngoài và chạy OOS portfolio evaluation. Nó chưa tự chứng minh params đến từ IS-only optimization. Vì vậy cần provenance check trước khi dùng cho kết luận nghiêm túc.

## Equity Và Risk

Full symbol engine và portfolio engine đã ghi equity theo mark-to-market. Open position được mark theo close của bar với adverse exit pricing. Sizing vẫn dựa trên realized/account equity để tránh dùng lãi/lỗ chưa đóng làm phóng đại lot.

Các giới hạn chính:

- daily loss limit;
- max drawdown limit;
- partial TP;
- trailing SL;
- lot min/max/step theo broker config;
- swap, commission, spread, dynamic slippage.

## Hạn Chế Hiện Tại

- `MA_PERIOD` vẫn cố định 20 trong grid search.
- `backtest_fast()` vẫn có signal logic inline và là approximation.
- Portfolio walk-forward chưa có provenance metadata cho `symbol_params`.
- Nhiều broker specs/swap rates chưa verify đầy đủ ở `shared/market.py`.
- `cross_down` còn edge case `close == ma`.

## Khi Nào Dùng File Nào

| Việc cần làm | File/entry point |
|---|---|
| Chạy backtest một symbol | `symbol/backtest.py::run_symbol_backtest()` |
| Tối ưu một symbol | `symbol/optimize.py::run_symbol_grid_search()` |
| Kiểm định IS/OOS một symbol | `symbol/walkforward.py::walk_forward_backtest()` |
| Chạy danh mục | `portfolio/backtest.py::run_portfolio_backtest()` |
| Kiểm định danh mục OOS | `portfolio/walkforward.py::walk_forward_portfolio()` |
| Xem/replay/debug signal | `chart.py`, `research_utils/`, notebooks |
