# portfolio/ — Làm việc với cả danh mục

## Tổng quan

Thư mục này chứa 3 file để vận hành **nhiều symbol cùng lúc** như một danh mục:

```
portfolio/
├── backtest.py      Chạy backtest toàn danh mục + kiểm tra FTMO
├── optimize.py      Tối ưu tham số cấp danh mục
└── walkforward.py   Kiểm định danh mục theo thời gian
```

**Nguyên tắc hoạt động:**  
Mỗi symbol chạy **độc lập** với phần vốn riêng.  
Sau đó các đường vốn được gộp lại thành view danh mục tổng.

```
initial_balance = 100.000 USD, 5 symbols
→ per_symbol = 20.000 USD mỗi symbol

US30  [20.000] → backtest → equity_US30
GOLD  [20.000] → backtest → equity_GOLD
BTCUSD[20.000] → backtest → equity_BTCUSD
J225  [20.000] → backtest → equity_J225
DE40  [20.000] → backtest → equity_DE40
                                   ↓
                              gộp lại
                                   ↓
                         combined_equity (tổng 5 symbols)
```

---

## backtest.py — Backtest toàn danh mục

### Hàm chính: `run_portfolio_backtest()`

```python
from core_python.strategies.combo import run_portfolio_backtest

result = run_portfolio_backtest(
    symbol_keys=["US30", "GOLD", "BTCUSD", "J225", "DE40"],
    initial_balance=100_000,
    account_mode="ftmo",         # Hoặc "standard"
    date_from="2023-01-01",
    date_to="2024-01-01",
)
```

Nếu `symbol_keys=None` → dùng tất cả 11 symbols trong `config.py`.

**Kết quả trả về (PortfolioBacktestResult):**
```python
result.combined_equity        # Series: tổng vốn toàn danh mục theo thời gian
result.equity_frame           # DataFrame: mỗi cột là 1 symbol
result.trades                 # List tất cả lệnh của mọi symbol (sorted by time)
result.metrics                # Dict: metrics tổng hợp
result.symbol_results         # Dict[symbol → SymbolBacktestResult]
result.account_mode           # "ftmo" hoặc "standard"
```

**Metrics danh mục:**
```python
result.metrics
# → {
#     'n_trades': 312,
#     'win_rate': 0.541,
#     'profit_factor': 1.38,
#     'max_drawdown': 0.082,
#     'sharpe': 1.21,
#     'total_return_pct': 18.4,
#     'ftmo_account_check': {           # Chỉ có khi account_mode='ftmo'
#         'ftmo_pass': True,
#         'breach_reason': None,
#         'max_daily_loss_pct': 0.038,
#         'max_dd_pct': 0.082,
#     }
# }
```

---

### Kiểm tra FTMO cấp tài khoản

Khi `account_mode="ftmo"`, sau khi tất cả symbol chạy xong, hệ thống kiểm tra thêm:

```python
# Code trong portfolio/backtest.py dòng 85-93:
if account_mode == "ftmo" and not combined_equity.empty:
    metrics["ftmo_account_check"] = check_portfolio_ftmo(
        combined_equity,
        initial_balance,
        daily_loss_limit=0.05,   # 5%
        max_dd_limit=0.10,       # 10%
    )
```

**⚠️ Giới hạn hiện tại:**  
Kiểm tra này chạy **sau khi** tất cả symbol kết thúc simulation. Nó báo cáo xem tài khoản có vi phạm FTMO không, nhưng **không dừng các symbol khác tại thời điểm vi phạm xảy ra**. Đây là điểm cần cải thiện trong tương lai.

---

### Hàm `compare_account_modes()` — So sánh standard vs FTMO

```python
from core_python.strategies.combo import compare_account_modes

modes = compare_account_modes(
    symbol_keys=["US30", "GOLD"],
    initial_balance=100_000,
    date_from="2023-01-01",
    date_to="2024-01-01",
)

standard_result = modes["standard"]
ftmo_result     = modes["ftmo"]

print("Standard PF:", standard_result.metrics['profit_factor'])
print("FTMO PF:    ", ftmo_result.metrics['profit_factor'])
```

Dùng để hiểu: nếu áp quy tắc FTMO, hiệu quả thay đổi bao nhiêu?

---

## optimize.py — Tối ưu tham số danh mục

### Hàm chính: `grid_search_portfolio()`

Tối ưu tham số **chung** cho cả danh mục (thay vì tối ưu từng symbol riêng).

```python
from core_python.strategies.combo import grid_search_portfolio

results_df = grid_search_portfolio(
    symbol_keys=["US30", "GOLD", "BTCUSD"],
    date_from="2022-01-01",
    date_to="2024-01-01",
    initial_balance=100_000,
)
```

**Lưu ý:** Tối ưu danh mục tốn nhiều thời gian hơn tối ưu từng symbol.  
Thứ tự thực hành tốt:
1. Optimize từng symbol riêng → lấy top params
2. Dùng params đó để chạy `run_portfolio_backtest()` để xem kết quả tổng

### `calc_portfolio_combo_metrics()` — Tính metrics danh mục

```python
from core_python.strategies.combo import calc_portfolio_combo_metrics

metrics = calc_portfolio_combo_metrics(trades_by_symbol, equity_by_symbol)
```

---

## walkforward.py — Kiểm định danh mục theo thời gian

### Hàm chính: `walk_forward_portfolio()`

```python
from core_python.strategies.combo import walk_forward_portfolio

# symbol_params: tham số đã được optimize cho từng symbol
symbol_params = {
    "US30":   {"ktp": 2.53, "x": 10, "ma_period": 20, "trailing_activation": 1.0},
    "GOLD":   {"ktp": 2.30, "x": 0.5, "ma_period": 20, "trailing_activation": 1.0},
    "BTCUSD": {"ktp": 2.07, "x": 50, "ma_period": 15, "trailing_activation": 0.75},
}

oos_df, summary = walk_forward_portfolio(
    symbol_params=symbol_params,
    account_mode="standard",
    initial_balance=100_000,
    is_bars=5000,
    oos_bars=1250,
    step_bars=1250,
)

print(summary)
# → {
#     'n_windows': 8,
#     'profitable_windows': 5,
#     'avg_total_return': 0.023,    # Trung bình mỗi OOS window lời 2.3%
#     'avg_max_drawdown': 0.065,
#     'avg_sharpe': 0.94,
# }
```

---

### Sự khác biệt quan trọng với symbol/walkforward.py

| | symbol/walkforward.py | portfolio/walkforward.py |
|---|---|---|
| **Re-optimize mỗi cửa sổ?** | **Có** — tìm tham số mới trong mỗi IS window | **Không** — dùng tham số cố định đã cho |
| **Mục đích** | Kiểm tra xem optimize có thực sự hoạt động không | Kiểm tra danh mục với params đã chọn |
| **Khi nào dùng** | Nghiên cứu 1 symbol | Validate danh mục cuối cùng |

**Thứ tự đúng:**
```
1. symbol/walkforward.py  → kiểm tra từng symbol
2. portfolio/backtest.py  → xem danh mục tổng với params tốt
3. portfolio/walkforward.py → kiểm định danh mục theo thời gian
```

---

## Ví dụ luồng đầy đủ — Từ symbol đến danh mục

```python
# ─────────────────────────────────────────────────
# BƯỚC 1: Nghiên cứu từng symbol
# ─────────────────────────────────────────────────
from core_python.strategies.combo import (
    run_symbol_backtest, run_symbol_grid_search,
    run_portfolio_backtest, compare_account_modes
)

# Baseline
for sym in ["US30", "GOLD", "BTCUSD"]:
    r = run_symbol_backtest(sym, date_from="2020-01-01", date_to="2023-12-31")
    print(f"{sym}: Sharpe={r.metrics['sharpe']:.2f}, PF={r.metrics['profit_factor']:.2f}")

# ─────────────────────────────────────────────────
# BƯỚC 2: Tối ưu từng symbol
# ─────────────────────────────────────────────────
best_params = {}
for sym in ["US30", "GOLD", "BTCUSD"]:
    results = run_symbol_grid_search(sym, date_from="2020-01-01", date_to="2022-12-31")
    best = results.iloc[0]
    best_params[sym] = {"ktp": best["ktp"], "x": best["x"]}
    print(f"{sym} best params: ktp={best['ktp']}, Sharpe={best['sharpe']:.2f}")

# ─────────────────────────────────────────────────
# BƯỚC 3: Chạy danh mục
# ─────────────────────────────────────────────────
portfolio = run_portfolio_backtest(
    symbol_keys=["US30", "GOLD", "BTCUSD"],
    initial_balance=100_000,
    account_mode="ftmo",
    date_from="2023-01-01",
    date_to="2024-01-01",
    symbol_overrides=best_params,
)

print("Portfolio Sharpe:", portfolio.metrics["sharpe"])
print("FTMO Pass:", portfolio.metrics.get("ftmo_account_check", {}).get("ftmo_pass"))

# ─────────────────────────────────────────────────
# BƯỚC 4: So sánh standard vs FTMO
# ─────────────────────────────────────────────────
modes = compare_account_modes(
    symbol_keys=["US30", "GOLD", "BTCUSD"],
    initial_balance=100_000,
    date_from="2023-01-01",
    date_to="2024-01-01",
)
print("Standard return:", modes["standard"].metrics["total_return_pct"])
print("FTMO return:    ", modes["ftmo"].metrics["total_return_pct"])
```

---

## Câu hỏi thường gặp

**Q: Tại sao các symbol chạy độc lập? Không phải chia sẻ chung 1 vốn?**

Hiện tại là equal-weight: mỗi symbol nhận `initial_balance / N` và quản lý vốn riêng.  
Đây là thiết kế có chủ ý để đơn giản hóa. Portfolio không có allocation động, correlation control, hay kiểm soát tổng exposure.

**Q: Kết quả `ftmo_account_check` khác gì với kiểm tra FTMO trong từng symbol?**

- Kiểm tra trong từng symbol (execution engine): dừng symbol đó khi sleeve riêng vi phạm
- `ftmo_account_check`: kiểm tra tổng tài khoản có vi phạm không → báo cáo sau khi tất cả symbol xong

Hiện tại **chưa có** cơ chế dừng toàn bộ danh mục ngay khi tổng tài khoản vi phạm.

**Q: Khi nào dùng `run_portfolio_backtest()` vs `compare_account_modes()`?**

- `run_portfolio_backtest()`: Khi biết muốn dùng chế độ nào
- `compare_account_modes()`: Khi muốn thấy cả hai để so sánh
