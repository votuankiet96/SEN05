# Notebook Research MA Cross

Thư mục này chứa notebooks nghiên cứu cho MA Cross. Các notebook dùng production path của strategy, không nên định nghĩa lại rule tín hiệu hoặc execution logic bên trong notebook.

## Danh Sách Notebook

1. `01_symbol_backtest.ipynb`
   - Chạy baseline một symbol.
   - So sánh timeframe M20, M30, M45 nếu workflow cấu hình như vậy.
   - Dùng `run_symbol_backtest()` của MA Cross.

2. `02_symbol_optimize.ipynb`
   - Chạy grid search bằng full backtest path.
   - Search fast/slow MA, SMA/EMA, ATR stop/TP và timeframe.
   - Cần truyền date window rõ ràng; không nên optimize full-history nếu không có chủ đích.

3. `03_portfolio_backtest.ipynb`
   - Chạy nhiều symbol với cùng workflow.
   - Tổng hợp equity, metrics và contribution theo symbol.

4. `04_portfolio_walkforward.ipynb`
   - Kiểm tra fixed params trên các cửa sổ IS/OOS.
   - Lưu ý: workflow hiện tại không optimize lại trong từng IS window.

5. `05_chart_and_signal_scanner.ipynb`
   - Xem chart, MA, ATR, BUY/SELL markers và replay.
   - Đây là công cụ quan sát, không thay thế cost-aware backtest.

6. `06_ftmo_vs_standard.ipynb`
   - So sánh cùng symbol/timeframe dưới `standard` và `ftmo`.
   - Dùng để xem tác động của daily loss và max drawdown rules.

## Quy Tắc Research

- Signal logic phải đến từ `core_python.strategies.ma_cross.signals`.
- Strategy defaults và search space phải đến từ `core_python.strategies.ma_cross.config`.
- Execution phải đi qua `core_python.strategies.ma_cross.symbol.backtest`.
- Không chỉnh source code bằng regex trong notebook.
- Không hardcode broker cost trong notebook nếu có thể truyền qua config/overrides.
- Kết quả chỉ là research nếu chưa verify data quality, broker specs, OOS, walk-forward và Monte Carlo.

## Cảnh Báo Hiện Tại

- `simple_walkforward()` của MA Cross là fixed-parameter window evaluation, chưa phải true walk-forward optimization.
- Basket TP/SL đang dùng USD tuyệt đối và check ở bar close.
- `r_multiple` của basket trade hiện là `0.0`, nên không dùng `avg_r` để kết luận chất lượng basket.
- Swap/specs chưa verify đầy đủ có thể làm kết quả quá lạc quan hoặc quá bi quan.
