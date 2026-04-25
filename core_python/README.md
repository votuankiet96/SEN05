# core_python — Tổng quan hệ thống

## Hệ thống này làm gì?

`core_python` là lớp phần mềm chứa toàn bộ logic nghiên cứu chiến lược giao dịch.  
Nó nhận dữ liệu giá từ database, phát tín hiệu, mô phỏng giao dịch, và tính toán kết quả.

---

## Sơ đồ tổng thể

```
Dữ liệu giá (SQL Server)
        ↓
core_python/shared/data.py          ← Lấy và kiểm tra dữ liệu
        ↓
strategies/combo/logic.py           ← Tính chỉ báo, phát tín hiệu BUY/SELL
        ↓
core_python/shared/execution.py     ← Mô phỏng bar-by-bar: vào lệnh, quản lý, đóng lệnh
        ↓
  ┌─────────────────────────┐
  │  symbol/backtest.py     │  ← Chạy 1 symbol
  │  symbol/optimize.py     │  ← Tìm tham số tốt nhất cho 1 symbol
  │  symbol/walkforward.py  │  ← Kiểm định độ bền theo thời gian
  └─────────────────────────┘
        ↓
  ┌─────────────────────────┐
  │  portfolio/backtest.py  │  ← Gộp nhiều symbol thành 1 danh mục
  │  portfolio/walkforward  │  ← Kiểm định danh mục theo thời gian
  └─────────────────────────┘
        ↓
Kết quả: danh sách lệnh, đường vốn, Sharpe, Drawdown, ...
```

---

## Cấu trúc thư mục

```
core_python/
├── shared/                          Công cụ dùng chung — không biết gì về Combo
│   ├── data.py                      Lấy dữ liệu từ DB + kiểm tra chất lượng
│   ├── execution.py                 Engine mô phỏng giao dịch (2 chế độ)
│   ├── portfolio.py                 Gộp equity nhiều symbol, tính metrics danh mục
│   ├── monte_carlo.py               Kiểm tra độ bền bằng mô phỏng ngẫu nhiên
│   ├── metrics.py                   Tính Sharpe, Drawdown, PF, ...
│   ├── broker.py                    Làm tròn lot, resolve specs broker
│   ├── contracts.py                 Kiểu dữ liệu trả về (SymbolBacktestResult, ...)
│   └── theme.py                     Style màu sắc cho chart
│
└── strategies/combo/                Chiến lược Combo — logic và điều phối
    ├── config.py                    Bảng tham số trung tâm (KHÔNG sửa file khác)
    ├── logic.py                     Luật tín hiệu BUY/SELL (KHÔNG sửa file khác)
    ├── scanner.py                   Công cụ nhìn tín hiệu trực quan (KHÔNG phải backtest)
    │
    ├── symbol/                      Làm việc với từng symbol riêng lẻ
    │   ├── backtest.py              Chạy backtest 1 symbol
    │   ├── optimize.py              Tìm tham số tốt nhất
    │   └── walkforward.py           Kiểm định IS/OOS
    │
    └── portfolio/                   Làm việc với toàn bộ danh mục
        ├── backtest.py              Chạy backtest nhiều symbol cùng lúc
        ├── optimize.py              Tối ưu tham số cấp danh mục
        └── walkforward.py           Kiểm định danh mục theo thời gian
```

---

## Nguyên tắc thiết kế quan trọng

### 1. Tách biệt giữa "shared" và "combo"
- `shared/` chứa các công cụ **không biết gì** về chiến lược Combo. Chúng có thể dùng cho bất kỳ chiến lược nào.
- `strategies/combo/` chứa mọi thứ **đặc thù** của Combo: tham số, luật tín hiệu, cách điều phối.

### 2. Một nguồn sự thật duy nhất
- Tất cả tham số chiến lược chỉ ở một chỗ: `strategies/combo/config.py`
- Tất cả luật tín hiệu chỉ ở một chỗ: `strategies/combo/logic.py`
- **Không bao giờ** copy-paste tham số sang file khác.

### 3. Hai chế độ backtest
- **`backtest_symbol()`** — Backtest đầy đủ: trả về từng lệnh chi tiết, đường vốn. Dùng khi cần kết quả cuối cùng.
- **`backtest_fast()`** — Backtest nhanh: chỉ trả con số tổng hợp (Sharpe, PF, ...). Dùng khi tìm tham số (chạy hàng trăm lần).

### 4. Scanner là công cụ nhìn — không phải backtest
- `scanner.py` chỉ hiển thị tín hiệu trên chart.
- Nó không mô phỏng fill lệnh, không tính phí, không có trailing. **Không dùng để đánh giá hiệu quả chiến lược.**

---

## Luồng sử dụng thực tế

### Tôi muốn xem chiến lược chạy thế nào trên US30
```python
from core_python.strategies.combo import run_symbol_backtest

result = run_symbol_backtest("US30", date_from="2023-01-01", date_to="2024-01-01")
print(result.metrics)   # Sharpe, PF, Drawdown, ...
print(result.trades)    # Danh sách từng lệnh
```
→ Xem chi tiết: [symbol/backtest.py](strategies/combo/symbol/backtest.py)

### Tôi muốn tìm tham số tốt nhất cho US30
```python
from core_python.strategies.combo import run_symbol_grid_search

results_df = run_symbol_grid_search("US30", date_from="2022-01-01", date_to="2024-01-01")
print(results_df.head(5))   # Top 5 tham số tốt nhất theo Sharpe
```
→ Xem chi tiết: [symbol/optimize.py](strategies/combo/symbol/optimize.py)

### Tôi muốn kiểm định độ bền (walk-forward)
```python
from core_python.strategies.combo import walk_forward_backtest

oos_df, summary = walk_forward_backtest("US30", df_ind, cfg)
print(summary)   # efficiency, profitable_windows, ...
```
→ Xem chi tiết: [symbol/walkforward.py](strategies/combo/symbol/walkforward.py)

### Tôi muốn chạy toàn bộ danh mục
```python
from core_python.strategies.combo import run_portfolio_backtest

result = run_portfolio_backtest(
    ["US30", "GOLD", "BTCUSD"],
    initial_balance=100_000,
    account_mode="ftmo"
)
print(result.metrics)
```
→ Xem chi tiết: [portfolio/backtest.py](strategies/combo/portfolio/backtest.py)

---

## Cảnh báo quan trọng

| Việc KHÔNG được làm | Lý do |
|---|---|
| Sửa tham số trong `execution.py` hay `logic.py` trực tiếp | Phải sửa qua `config.py` để tất cả chỗ đều thay đổi đồng bộ |
| Tin kết quả `backtest_fast()` là kết quả cuối cùng | Nó bỏ qua một số chi phí, dùng để lọc nhanh thôi |
| Dùng scanner để đánh giá hiệu quả | Scanner là công cụ nhìn, không phải execution engine |
| Chạy optimizer trước khi verify broker cost | Cost sai → optimizer chọn tham số ảo |

---

## Đọc tiếp

- Tầng công cụ dùng chung: [shared/README.md](shared/README.md)
- Chiến lược Combo (tham số + tín hiệu): [strategies/combo/README.md](strategies/combo/README.md)
- Chạy 1 symbol: [strategies/combo/symbol/README.md](strategies/combo/symbol/README.md)
- Chạy danh mục: [strategies/combo/portfolio/README.md](strategies/combo/portfolio/README.md)
