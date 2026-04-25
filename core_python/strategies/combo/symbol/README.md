# symbol/ — Làm việc với 1 symbol

## Tổng quan

Thư mục này chứa 3 file cho phép làm việc với **một symbol duy nhất**:

```
symbol/
├── backtest.py      Chạy backtest đầy đủ → xem chiến lược hoạt động thế nào
├── optimize.py      Tìm tham số tốt nhất → grid search nhẹ
└── walkforward.py   Kiểm định độ bền → tránh overfit
```

**Thứ tự sử dụng đúng:**
```
backtest.py → optimize.py → walkforward.py → (portfolio)
     ↑               ↑              ↑
  Xem kết quả   Tìm params    Kiểm tra params có giữ được không
```

---

## backtest.py — Chạy backtest 1 symbol

### Hàm chính: `run_symbol_backtest()`

```python
from core_python.strategies.combo import run_symbol_backtest

result = run_symbol_backtest(
    symbol_key="US30",
    init_eq=100_000,            # Vốn ban đầu
    account_mode="standard",    # Hoặc "ftmo"
    date_from="2023-01-01",     # Ngày bắt đầu (None = từ đầu lịch sử)
    date_to="2024-01-01",       # Ngày kết thúc (None = đến hôm nay)
)
```

**Kết quả trả về (SymbolBacktestResult):**
```python
result.trades       # Danh sách dict, mỗi dict là 1 lệnh
result.equity       # Series: thời gian → giá trị vốn
result.metrics      # Dict: Sharpe, PF, max_DD, n_trades, ...
result.signal_data  # DataFrame đã có cột signal (để debug)
result.symbol_config  # Cấu hình symbol đã dùng
```

**Xem metrics:**
```python
print(result.metrics)
# → {
#     'n_trades': 45,
#     'win_rate': 0.533,
#     'profit_factor': 1.42,
#     'max_drawdown': 0.087,
#     'sharpe': 1.15,
#     'total_return_pct': 12.3,
# }
```

---

### Luồng bên trong `run_symbol_backtest()`

Khi bạn gọi hàm này, nó thực hiện 5 bước:

```
Bước 1: load_backtest_data()
        → Kết nối DB, lấy OHLCV theo symbol_id và date range
        → Kiểm tra chất lượng (NaN, duplicate, high<low)

Bước 2: build_symbol_signal_frame()
        → Tính chỉ báo: MA, MACD histogram, ATR
        → Phát tín hiệu BUY/SELL theo 4 điều kiện
        → Thêm cột 'signal' (1/-1/0) vào DataFrame

Bước 3: get_account_settings()
        → Resolve risk_per_trade, daily_limit, max_dd theo account_mode

Bước 4: backtest_symbol()  [← shared/execution.py]
        → Mô phỏng bar-by-bar: fill lệnh, quản lý vị thế, đóng lệnh
        → Trả về (trades_list, equity_series)

Bước 5: calc_metrics()
        → Tính Sharpe, PF, Drawdown từ trades và equity
        → Đóng gói vào SymbolBacktestResult
```

---

### Hàm `run_symbol_backtest_on_frame()` — Khi data đã có sẵn

Dùng khi bạn **đã có DataFrame raw OHLCV trong bộ nhớ** (ví dụ trong walk-forward loop):

```python
# Thay vì load lại từ DB mỗi lần:
result = run_symbol_backtest_on_frame(
    symbol_key="US30",
    df_raw=df_already_loaded,    # ← Dùng data đã có sẵn
    date_from="2023-01-01",
    date_to="2023-06-30",
)
```

Hàm này bỏ qua bước load từ DB → nhanh hơn nhiều khi gọi lặp lại.

---

### Hàm `build_symbol_signal_frame()` — Chỉ tính indicator + signal

```python
df_sig, cfg, params = build_symbol_signal_frame(
    symbol_key="US30",
    df_raw=df_ohlcv,
)
# df_sig: DataFrame có thêm cột ma, macd_h, atr, signal, rr
# cfg: dict cấu hình symbol
# params: dict tham số indicator
```

Dùng khi bạn muốn debug xem signal trông như thế nào, hoặc chuẩn bị data cho bước khác.

---

## optimize.py — Tìm tham số tốt nhất

### Mục đích
Thay vì dùng tham số mặc định từ `config.py`, bạn có thể tìm tham số tốt hơn cho từng symbol.  
Optimizer chạy hàng chục đến hàng trăm tổ hợp tham số, sắp xếp theo Sharpe.

### Hàm chính: `run_symbol_grid_search()`

```python
from core_python.strategies.combo import run_symbol_grid_search

results_df = run_symbol_grid_search(
    symbol_key="US30",
    date_from="2022-01-01",     # Giai đoạn IS (in-sample)
    date_to="2024-01-01",
    account_mode="standard",
)

print(results_df.head(5))
```

**Kết quả trả về (DataFrame):**
```
   ktp    x   ma_period  trailing_activation  sharpe   pf     ret   maxdd  score  trades
0  2.53  10       20             1.00          1.42   1.51   15.2   0.072   0.89     48
1  2.30  10       20             1.00          1.38   1.47   13.8   0.068   0.86     45
2  2.53   8       20             0.75          1.35   1.45   12.9   0.071   0.84     51
...
```

---

### Cách hoạt động bên trong

**Bước 1: Xây dựng lưới tham số**

```python
# Từ config.py OPTIMIZATION:
# ktp_multipliers:      [0.9, 1.0, 1.1]  → [2.07, 2.30, 2.53]
# x_offsets:            [-2, 0, 2]        → [8, 10, 12]
# trailing_multipliers: [0.75, 1.0, 1.25] → [0.75, 1.0, 1.25]
# ma_offsets:           [-5, 0, 5]        → [15, 20, 25]
# Tổng: 3 × 3 × 3 × 3 = 81 tổ hợp
```

**Bước 2: Nhóm theo ma_period để tái sử dụng indicator**

```python
# Với 81 tổ hợp, có 3 giá trị MA (15, 20, 25)
# → Chỉ cần tính indicator 3 lần, mỗi lần dùng cho 27 tổ hợp
# → Tiết kiệm ~66% thời gian tính toán
```

**Bước 3: Chạy `backtest_fast()` cho mỗi tổ hợp**

```python
# backtest_fast() nhanh hơn backtest_symbol() ~3-5 lần
# vì không lưu chi tiết từng lệnh
```

**Bước 4: Sắp xếp theo Sharpe**

---

### ⚠️ Cảnh báo khi dùng optimizer

`backtest_fast()` là **bộ lọc**, không phải kết quả cuối cùng.

**Quy trình đúng:**
```
1. run_symbol_grid_search()  → tìm top 5 tham số
2. run_symbol_backtest()     → validate từng tham số với backtest đầy đủ
3. Chọn tham số có kết quả nhất quán giữa fast và full
4. walk_forward_backtest()   → kiểm định thêm
```

---

### `build_parameter_grid()` — Xây dựng lưới tham số

```python
grid = build_parameter_grid("US30")
# → [
#   {'ktp': 2.07, 'x': 8, 'ma_period': 15, 'trailing_activation': 0.75},
#   {'ktp': 2.07, 'x': 8, 'ma_period': 15, 'trailing_activation': 1.0},
#   ...
# ]  (81 dicts)
```

Bạn có thể custom search space:
```python
custom_space = {
    'ktp':                 [2.0, 2.3, 2.6, 3.0],   # 4 giá trị
    'x':                   [10],                     # Cố định x=10
    'trailing_activation': [1.0],                    # Cố định trailing
    'ma_period':           [20],                     # Cố định MA=20
}
results_df = run_symbol_grid_search("US30", ..., search_space=custom_space)
```

---

## walkforward.py — Kiểm định độ bền (Walk-Forward)

### Mục đích
Trả lời câu hỏi quan trọng: **"Tham số tốt trong quá khứ có còn tốt trong tương lai không?"**

Walk-forward chia lịch sử thành nhiều cửa sổ IS/OOS và kiểm tra từng cửa sổ:
- **IS (In-Sample)**: Giai đoạn dùng để tìm tham số tốt nhất
- **OOS (Out-of-Sample)**: Giai đoạn chưa "nhìn thấy" → kiểm tra xem tham số IS có hoạt động không

```
Lịch sử dữ liệu:
|───────── IS ─────────|─── OOS ───|
                        |───────── IS ─────────|─── OOS ───|
                                                |───────── IS ─────────|─── OOS ───|
                                                ↑ Trượt từng bước
```

### Hàm chính: `walk_forward_backtest()`

```python
from core_python.strategies.combo import walk_forward_backtest

oos_df, summary = walk_forward_backtest(
    symbol="US30",
    df_ind=df_with_indicators,   # DataFrame đã có cột chỉ báo
    cfg=symbol_cfg,
    init_eq=100_000,
    is_bars=5000,       # Cửa sổ IS = 5000 bar (~7 tháng H4)
    oos_bars=1250,      # Cửa sổ OOS = 1250 bar (~45 ngày H4)
    step_bars=1250,     # Bước trượt mỗi lần
)
```

**Kết quả summary:**
```python
{
    'n_windows': 8,
    'profitable_windows': 5,
    'pct_profitable': 0.625,        # 62.5% cửa sổ OOS có lời
    'oos_is_efficiency': 0.68,      # OOS đạt 68% hiệu quả so với IS
    'efficiency_status': 'PASS',    # PASS nếu >= 50%
    'is_profit_total': 12500,
    'oos_profit_total': 8500,
    'plateau_stable_windows': 6,    # Số cửa sổ params nằm trên plateau ổn định
}
```

**Kết quả oos_df (DataFrame):**
```
   window  oos_start    oos_end   n_trades  sharpe  profit_factor  max_drawdown
0       1  2022-06-01  2022-09-15      8     1.12         1.35          0.054
1       2  2022-09-15  2022-12-31     11     0.89         1.21          0.071
...
```

---

### Cách hoạt động từng cửa sổ

```
Mỗi cửa sổ IS/OOS:

1. IS phase:
   → Chạy grid search trên IS bars (dùng backtest_fast)
   → Tìm bộ tham số có Sharpe cao nhất
   → Kiểm tra plateau stability

2. OOS phase:
   → Lấy thêm INDICATOR_WARMUP bars từ cuối IS (carry-over)
   → Rebuild indicator trên OOS + warmup → đảm bảo MACD, ATR ổn định
   → Cắt bỏ phần warmup, chỉ giữ OOS bars thật
   → Chạy backtest_symbol với tham số tốt nhất từ IS
   → Tính KPI của OOS window này
```

**Tại sao cần warmup carry-over?**  
MACD dùng EMA(25) — cần 25+ bars để ổn định. Nếu rebuild indicator từ đầu OOS window, 25 bars đầu sẽ không có MACD → không có tín hiệu → OOS kết quả bị đánh giá thấp giả tạo.

---

### `check_plateau_stability()` — Kiểm tra tham số có ổn định không

```python
plateau_result = check_plateau_stability(
    param_grid_results=results_df,  # Kết quả grid search
    best_params={'ktp': 2.53, 'x': 10, ...},
    best_sharpe=1.42,
    radius=1,           # Kiểm tra neighbors trong bán kính 1 bước
    threshold_ratio=0.6, # Neighbor được tính là stable nếu Sharpe >= 60% best
)

# plateau_result:
{
    'is_plateau': True,         # True = tham số nằm trên vùng bằng phẳng
    'stable_ratio': 0.75,       # 75% neighbors cũng có Sharpe tốt
    'neighbors_checked': 8,
    'neighbors_stable': 6,
    'warning': None             # Có warning nếu stable_ratio thấp
}
```

**Ý nghĩa:**
- `is_plateau = True`: Tham số tốt ổn định — thay đổi nhỏ không làm kết quả tệ đột ngột → **đáng tin hơn**
- `is_plateau = False`: Tham số là spike — điểm cực trị đơn lẻ → dễ overfit → **cẩn thận**

---

## Tóm tắt — Khi nào dùng hàm nào

| Câu hỏi | Hàm cần dùng |
|---|---|
| "Chiến lược chạy thế nào trên US30 năm 2023?" | `run_symbol_backtest()` |
| "Tôi đã load data rồi, chỉ cần chạy backtest" | `run_symbol_backtest_on_frame()` |
| "Tham số mặc định tốt chưa? Có tốt hơn không?" | `run_symbol_grid_search()` |
| "Tham số tìm được có thực sự robust không?" | `walk_forward_backtest()` |
| "Tham số best_sharpe có dễ bị overfit không?" | `check_plateau_stability()` |

---

## Ví dụ luồng nghiên cứu đầy đủ cho 1 symbol

```python
# Bước 1: Xem kết quả baseline
result = run_symbol_backtest("US30", date_from="2020-01-01", date_to="2023-12-31")
print(result.metrics)

# Bước 2: Tìm tham số tốt hơn (IS = 2020-2022)
results_df = run_symbol_grid_search("US30", date_from="2020-01-01", date_to="2022-12-31")
top5 = results_df.head(5)

# Bước 3: Validate từng tham số top5 với full backtest
for _, row in top5.iterrows():
    r = run_symbol_backtest(
        "US30",
        date_from="2020-01-01", date_to="2022-12-31",
        indicator_overrides={"MA_PERIOD": int(row['ma_period'])},
        symbol_overrides={"ktp": row['ktp'], "x": row['x']}
    )
    print(r.metrics)

# Bước 4: Walk-forward kiểm định độ bền
# (Cần chuẩn bị df_ind trước)
oos_df, summary = walk_forward_backtest("US30", df_ind, cfg)
print(summary)

# Bước 5: Nếu efficiency_status = 'PASS' → dùng params này cho portfolio
```
