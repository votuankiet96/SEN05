# Hạ Tầng Dùng Chung

`core_python/shared/` chứa các module kỹ thuật dùng chung cho nhiều chiến lược. Thư mục này không nên chứa logic riêng của Combo hay MA Cross.

## Vai Trò

`shared/` chịu trách nhiệm cho:

- tải và validate dữ liệu OHLCV;
- chuẩn hóa symbol specs, broker profiles và cost settings;
- primitives cho execution: adverse price, slippage, commission, swap, lot sizing;
- market-order engine dùng chung;
- contracts/result objects;
- metrics, FTMO checks và Monte Carlo.

## Cấu Trúc Chính

```text
shared/
├── data.py                         tải và kiểm tra dữ liệu backtest
├── analytics.py                    calc_metrics, FTMO checks, Monte Carlo
├── market.py                       symbol specs, broker profiles, costs
├── contracts.py                    result containers và order contracts
├── sessions.py                     session filters
├── timeframes.py                   metadata timeframe
├── monte_carlo.py                  Monte Carlo helpers
└── execution/
    ├── primitives.py               pricing, slippage, swap, lot sizing
    └── engines/market.py           market-order backtest engine
```

## data.py

`data.py` là cổng chính để lấy OHLCV cho backtest. Nó chuẩn hóa index thời gian, tên cột và kiểm tra chất lượng dữ liệu.

Các trách nhiệm chính:

- load OHLCV theo `symbol_id`, timeframe, date range;
- kiểm tra dữ liệu thiếu, duplicate timestamps, OHLC không hợp lệ;
- sắp xếp index theo thời gian;
- hỗ trợ warmup bars cho indicator.

## market.py

`market.py` là catalog instrument và broker:

- `SYMBOLS`: symbol metadata, spread, slippage, lot bounds, swap;
- `BROKER_PROFILES`: broker-specific overrides;
- `DEFAULT_COSTS`: cost fallback;
- `get_symbol_config()`: merge symbol với broker profile;
- `get_cost_settings()`: lấy cost settings cho execution;
- `audit_symbol_specs()`: kiểm tra cấu hình broker/symbol.

Ghi chú quan trọng: nhiều `swap_long_per_lot_per_day` và `swap_short_per_lot_per_day` vẫn là `0.0` hoặc chưa verify. Kết quả backtest không nên được dùng cho quyết định live trước khi broker specs được đối chiếu.

## execution/primitives.py

Module này chứa các hàm nhỏ dùng bởi nhiều engine:

- `adverse_entry_price()`;
- `adverse_exit_price()`;
- `calc_dynamic_slippage()`;
- `round_turn_commission()`;
- `swap_cost()`;
- `price_pnl()`;
- `risk_sized_lots_from_distance()`;
- `max_drawdown_breached()`.

Những hàm này không biết strategy là Combo hay MA Cross. Strategy truyền vào `cfg`, direction, entry/exit và risk parameters.

## execution/engines/market.py

Đây là market-order engine dùng bởi MA Cross `market_single`.

Đặc điểm:

- signal ở bar T được execute tại open bar T+1;
- entry/exit dùng adverse pricing;
- có spread, dynamic slippage, commission, swap;
- SL/TP dựa theo ATR config;
- partial TP và trailing SL;
- reverse on opposite signal;
- equity curve được ghi mark-to-market.

## analytics.py

`analytics.py` tính metrics sau backtest:

- total trades, win rate, profit factor;
- total PnL, total return, annual return;
- max drawdown theo equity series được truyền vào;
- Sharpe/Sortino theo trade-level returns;
- Calmar, recovery factor;
- monthly PnL table;
- FTMO checks;
- Monte Carlo helpers.

Điểm cần nhớ: `calc_metrics()` không tự biết engine đúng hay sai; nó tính drawdown trên equity series được truyền vào. Vì vậy engine phải cung cấp equity curve đúng mô hình.

## contracts.py

`contracts.py` định nghĩa các object dùng chung:

- `SymbolBacktestResult`;
- `PortfolioBacktestResult`;
- `PortfolioWindowResult`;
- order intent và position contracts.

Mục tiêu là giảm việc mỗi strategy tự nghĩ ra shape dữ liệu riêng.

## Nguyên Tắc Khi Thêm Code Vào shared/

Chỉ thêm vào `shared/` nếu code:

- không phụ thuộc vào rule tín hiệu của một strategy cụ thể;
- có thể dùng lại cho nhiều strategy;
- không import ngược từ `strategies/combo` hoặc `strategies/ma_cross`;
- có test cho behavior cốt lõi nếu ảnh hưởng execution/metrics.

Không thêm vào `shared/`:

- rule BUY/SELL của Combo;
- rule BUY/SELL của MA Cross;
- config riêng của strategy;
- notebook-only helper.
