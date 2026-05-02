# Kiến Trúc core_python

`core_python` là phần lõi phục vụ nghiên cứu chiến lược, backtest, tối ưu tham số và kiểm định độ bền. Lớp này lấy dữ liệu OHLCV, tính indicator, phát tín hiệu, mô phỏng khớp lệnh, tính metrics và cung cấp workflow notebook cho quá trình nghiên cứu.

Hiện có hai chiến lược chính:

- **Combo**: chiến lược pending breakout dựa trên MA, nến xác nhận, MACD histogram, TP theo ATR, partial TP và trailing SL.
- **MA Cross**: chiến lược giao cắt fast/slow MA, xác nhận ở close và vào lệnh market ở open bar kế tiếp; có thêm mô hình basket reversal.

## Cấu Trúc Chính

```text
core_python/
├── shared/
│   ├── data.py                         tải và kiểm tra dữ liệu OHLCV
│   ├── analytics.py                    metrics, FTMO check, Monte Carlo
│   ├── market.py                       symbol specs, broker profiles, costs
│   ├── contracts.py                    result containers, order contracts
│   └── execution/
│       ├── primitives.py               pricing, slippage, swap, lot sizing
│       └── engines/market.py           engine market-order dùng chung
└── strategies/
    ├── combo/
    │   ├── config.py                   tham số và universe của Combo
    │   ├── signals.py                  indicator và signal Combo
    │   ├── engines/                    execution engines của Combo
    │   ├── symbol/                     backtest/optimize/WF một symbol
    │   ├── portfolio/                  backtest/WF cấp danh mục
    │   └── research/                   notebooks nghiên cứu
    └── ma_cross/
        ├── config.py                   tham số MA Cross
        ├── signals.py                  indicator và signal MA Cross
        ├── basket.py                   trạng thái basket và giới hạn exposure
        ├── execution_basket.py         engine basket reversal
        ├── symbol/                     backtest/optimize/window evaluation
        └── research/                   notebooks nghiên cứu
```

## Luồng Hệ Thống

```text
Dữ liệu đầu vào
  -> shared/data.py

Chuẩn bị dữ liệu và indicator
  -> Combo:    strategies/combo/signals.py
  -> MA Cross: strategies/ma_cross/signals.py

Phát tín hiệu
  -> Combo:    detect_combo_signals()
  -> MA Cross: detect_ma_cross_signals()

Mô phỏng khớp lệnh
  -> Combo symbol:    combo/engines/symbol.py::backtest_symbol()
  -> Combo fast:      combo/engines/fast.py::backtest_fast()
  -> Combo portfolio: combo/engines/portfolio.py::backtest_portfolio()
  -> MA market:       shared/execution/engines/market.py::backtest_market_symbol()
  -> MA basket:       ma_cross/execution_basket.py::backtest_basket_reversal_symbol()

Metrics và báo cáo
  -> shared/analytics.py::calc_metrics()

Tối ưu tham số
  -> Combo:    combo/symbol/optimize.py::run_symbol_grid_search()
  -> MA Cross: ma_cross/symbol/optimize.py::run_symbol_grid_search()

Walk-forward / OOS
  -> Combo symbol:    combo/symbol/walkforward.py::walk_forward_backtest()
  -> Combo portfolio: combo/portfolio/walkforward.py::walk_forward_portfolio()
  -> MA Cross:        ma_cross/symbol/walkforward.py::simple_walkforward()
```

## Vai Trò Của shared/

`shared/` là lớp hạ tầng dùng chung. Nó không được chứa logic riêng của Combo hay MA Cross. Những phần nằm ở đây gồm:

- tải và kiểm tra dữ liệu;
- cấu hình symbol, broker profile, cost model;
- primitives cho giá khớp, spread, slippage, swap, lot sizing;
- engine market-order dùng chung;
- metrics, FTMO check, Monte Carlo;
- contracts/result containers.

Chiến lược chỉ nên import `shared/` để dùng hạ tầng, không đặt rule tín hiệu vào `shared/`.

## Mô Hình Equity Hiện Tại

Các engine báo cáo chính đang dùng equity mark-to-market:

- Combo symbol engine ghi floating PnL vào equity curve.
- Combo portfolio engine ghi floating PnL cho open positions và gắn `equity_model = "mark_to_market"`.
- MA Cross market engine ghi floating PnL vào equity curve.
- MA Cross basket engine ghi floating PnL của basket tại bar close.

`combo/engines/fast.py` là engine rút gọn cho optimizer. Nó trả KPI dict, không phải full trade log và không thay thế được full backtest.

## An Toàn Khi Optimize

Các entry point grid search yêu cầu phải có ít nhất một giới hạn thời gian rõ ràng. Nếu cả `date_from` và `date_to` đều là `None`, hàm sẽ raise `ValueError`, trừ khi caller chủ động truyền `allow_full_history=True`.

Ví dụ nên dùng:

```python
run_symbol_grid_search(
    "US30",
    date_from="2022-01-01",
    date_to="2024-01-01",
)
```

Mục tiêu là tránh vô tình optimize trên toàn bộ lịch sử rồi tưởng đó là OOS.

## Giới Hạn Đã Biết

Các điểm dưới đây là trạng thái thật của hệ thống hiện tại:

- Nhiều broker specs và swap rates chưa được verify đầy đủ.
- Combo portfolio walk-forward nhận fixed `symbol_params`; hiện chưa kiểm chứng provenance của params.
- Combo optimizer đang giữ `MA_PERIOD = 20` cố định.
- MA Cross `simple_walkforward()` là fixed-parameter window evaluation, chưa phải true IS optimization walk-forward.
- MA Cross basket TP/SL đang dùng ngưỡng USD tuyệt đối và check ở bar close.
- Sharpe hiện tính theo trade-level returns, không phải bar-level Sharpe chuẩn ngành.

## Tài Liệu Liên Quan

- [Hạ tầng dùng chung](shared/README_SHARED.md)
- [Chiến lược Combo](strategies/combo/README_COMBO.md)
- [Chiến lược MA Cross](strategies/ma_cross/README_MA_CROSS.md)
- [Notebook MA Cross](strategies/ma_cross/research/README_MA_CROSS_RESEARCH.md)
