# shared/ — Công cụ dùng chung

## Tổng quan

Thư mục `shared/` chứa các công cụ kỹ thuật thuần túy — không biết gì về chiến lược Combo.  
Toàn bộ thư mục `strategies/combo/` đều dựa vào đây, nhưng `shared/` hoàn toàn độc lập với Combo.

```
shared/
├── data.py          Lấy dữ liệu từ DB và kiểm tra chất lượng
├── execution.py     Engine mô phỏng giao dịch bar-by-bar (QUAN TRỌNG NHẤT)
├── portfolio.py     Gộp kết quả nhiều symbol thành danh mục
├── monte_carlo.py   Kiểm tra độ bền chiến lược bằng mô phỏng ngẫu nhiên
├── metrics.py       Tính toán các chỉ số hiệu quả (Sharpe, Drawdown, ...)
├── broker.py        Làm tròn lot size theo quy tắc broker
├── contracts.py     Định nghĩa kiểu dữ liệu kết quả trả về
└── theme.py         Style màu sắc cho tất cả chart
```

---

## data.py — Lấy và kiểm tra dữ liệu

### Vai trò
File này là **cổng duy nhất** để `core_python` lấy dữ liệu giá từ database.  
Không có file nào khác trong `core_python` được phép kết nối trực tiếp với DB hoặc import `modules.data_loader`.

### Các hàm quan trọng

---

#### `load_backtest_ohlcv()` — Hàm dùng nhiều nhất

```python
load_backtest_ohlcv(
    symbol_id,          # ID symbol trong DB (ví dụ: 10 = US30)
    date_to=None,       # Lấy đến ngày này (None = đến hôm nay)
    tf="H4",            # Timeframe
    max_bars=60000,     # Số bar tối đa
    warmup=200,         # Số bar warmup (cho indicator ổn định)
    validate=True,      # Có kiểm tra chất lượng không?
    min_bars=100,       # Yêu cầu tối thiểu để backtest có ý nghĩa
)
```

**Nó làm gì từng bước:**
1. Kết nối database, chạy câu SQL lấy OHLCV
2. Đổi tên cột về chữ thường: `Open → open`, `High → high`, ...
3. Sắp xếp theo thời gian tăng dần
4. Nếu `validate=True`: gọi `validate_backtest_data()` để kiểm tra chất lượng
5. Trả về DataFrame với DatetimeIndex

**Ví dụ kết quả:**
```
                     open     high      low    close  volume
BarTime
2023-01-02 08:00  33000.0  33150.0  32900.0  33100.0    1234
2023-01-02 12:00  33100.0  33200.0  33050.0  33180.0    1456
...
```

---

#### `validate_backtest_data()` — Kiểm tra chất lượng

Tự động gọi bởi `load_backtest_ohlcv()`. Kiểm tra và sửa 4 vấn đề:

| Vấn đề | Cách xử lý |
|--------|-----------|
| Dòng có giá trị NaN (thiếu dữ liệu) | Xóa dòng đó |
| Timestamp trùng lặp | Giữ lần đầu, xóa lần sau |
| High < Low (bar vô lý) | Xóa dòng đó |
| Dữ liệu chưa sắp xếp theo thời gian | Tự sắp xếp |

Nếu sau khi dọn, số bar còn lại < `min_bars` → ném lỗi `DataQualityError`.

---

#### `load_scan_ohlcv()` — Dành cho scanner (khác schema)

```python
load_scan_ohlcv(symbol_id, n_bars, tf_code="H4", warmup=200)
```

Trả về dữ liệu có cột `BarTime` là cột thường (không phải index) — dùng riêng cho scanner.  
**Không dùng hàm này cho backtest.**

---

#### `DataQualityError` — Lỗi chất lượng dữ liệu

```python
from core_python.shared.data import DataQualityError

try:
    df = load_backtest_ohlcv(symbol_id=10)
except DataQualityError as e:
    print(f"Dữ liệu không đủ: {e}")
    # → Kiểm tra data pipeline
```

---

## execution.py — Engine mô phỏng giao dịch

### Vai trò
Đây là file quan trọng nhất trong toàn bộ `core_python`.  
Nó mô phỏng từng bar một: kiểm tra lệnh chờ có được fill không, quản lý vị thế, đóng lệnh.

### Hai chế độ

| Hàm | Dùng khi nào | Trả về |
|-----|-------------|--------|
| `backtest_symbol()` | Cần kết quả chi tiết cuối cùng | Danh sách lệnh + đường vốn |
| `backtest_fast()` | Đang tìm tham số (chạy 100+ lần) | Chỉ Sharpe, PF, Drawdown |

---

### `backtest_symbol()` — Backtest đầy đủ

**Nhận vào:**
```python
backtest_symbol(
    symbol="US30",      # Tên symbol (chỉ để ghi vào log)
    df=df_sig,          # DataFrame đã có cột signal (1/-1/0) và chỉ báo
    cfg=cfg_dict,       # Cấu hình symbol: x, contract_value, point_size, swap, ...
    init_eq=100_000,    # Vốn khởi điểm
    strategy=None,      # Override tham số chiến lược (risk, trailing, ...)
    costs=None,         # Override chi phí (slippage, commission, ...)
)
```

**Vòng lặp từng bar — đây là trái tim của hệ thống:**

```
Bar mới đến
│
├─ [1] Sang ngày mới? → Reset daily_pnl, bỏ daily_stop
│
├─ [2] Max drawdown bị vi phạm? → Dừng hẳn
│
├─ [3] Có lệnh chờ (pending)?
│   ├─ TTL đã hết (đã chờ 3 bar) → Huỷ lệnh chờ
│   └─ Giá chạm entry?
│       └─ Có → Mở vị thế, tính lot size, trừ commission
│
├─ [4] Đang có vị thế?
│   ├─ SL bị đánh → Đóng toàn bộ, ghi lỗ
│   ├─ TP bị chạm (lần đầu) → Đóng 50%, SL về breakeven, bật trailing
│   ├─ Trailing đang active → Cập nhật SL theo MA (chỉ đi 1 chiều)
│   ├─ Daily loss quá ngưỡng → Đặt daily_stop
│   └─ Tín hiệu ngược chiều → Đóng lệnh hiện tại + đặt lệnh mới
│
└─ [5] Có tín hiệu mới? (signal != 0) + không daily_stop
    └─ Đặt lệnh chờ (pending order)
```

**Tính lot size (dòng 240-248):**
```python
risk_usd = equity * 0.005           # Rủi ro = 0.5% vốn
sl_dist  = |actual_entry - sl|      # Khoảng cách từ entry đến SL
lot_size = risk_usd / (sl_dist / point_size * contract_value)
lot_size = round_lot_size(lot_size, min_lot, max_lot, lot_step)
```

**Tính slippage động (dòng 193-199):**
```python
slippage = base_slip * (1 + 50 * clip(atr/close, 0.0005, 0.01))
# ATR càng lớn so với giá → thị trường càng biến động → slippage càng cao
```

**Trailing SL (dòng ~280-295):**
```python
# Chỉ kích hoạt khi trailing_active = True (sau partial TP, hoặc lãi >= 1×ATR)
new_sl = bar['ma']  # MA của bar hiện tại
# BUY: new_sl chỉ được tăng (không bao giờ giảm)
# SELL: new_sl chỉ được giảm (không bao giờ tăng)
```

**Trả về:**
```python
(trades, eq_ts)

# trades: list các dict, mỗi dict là 1 lệnh đầy đủ:
{
    'symbol': 'US30',
    'direction': 1,          # 1=BUY, -1=SELL
    'entry': 33150.0,
    'sl': 32900.0,
    'tp': 33600.0,
    'lot_size': 0.5,
    'close_price': 33600.0,
    'exit_reason': 'TP',     # 'TP', 'SL', 'REVERSED', 'DAILY_STOP', 'MAX_DD'
    'pnl_net': 450.0,        # Sau commission + swap
    'entry_time': Timestamp,
    'exit_time': Timestamp,
    ...
}

# eq_ts: Series thời gian → giá trị vốn
```

---

### `backtest_fast()` — Backtest nhanh cho optimizer

Giống `backtest_symbol()` về logic cốt lõi, nhưng:
- Không lưu từng lệnh chi tiết → nhanh hơn ~3-5 lần
- Chỉ trả tổng hợp: `{trades, pf, ret, maxdd, score, sharpe}`
- Dùng trong grid search khi cần chạy hàng trăm bộ tham số

**Lưu ý quan trọng:** `backtest_fast()` là công cụ lọc, không phải kết quả cuối cùng.  
Sau khi tìm được tham số tốt, phải validate lại bằng `backtest_symbol()`.

---

### Các hàm phụ trợ

#### `build_pending_order()` — Tạo cấu trúc lệnh chờ

```python
pending = build_pending_order(
    bar=current_bar,    # Bar dữ liệu hiện tại
    direction=1,        # 1=BUY, -1=SELL
    x=10,               # Buffer breakout (ví dụ US30=10 điểm)
    ktp=2.3,            # Hệ số TP theo ATR
    atr=150.0,          # ATR hiện tại
    ttl=3               # Sống tối đa 3 bar
)
# → {'direction': 1, 'entry': 33160, 'sl': 32890, 'tp': 33505, 'atr': 150, 'ttl': 3}
```

Công thức tính:
- BUY: `entry = high + x`, `sl = low - x`, `tp = entry + ktp * atr`
- SELL: `entry = low - x`, `sl = high + x`, `tp = entry - ktp * atr`

#### `calc_dynamic_slippage()` — Tính slippage

```python
slippage = calc_dynamic_slippage(base_slippage=2, atr=150, close=33000)
# → 2 * (1 + 50 * clip(150/33000, 0.0005, 0.01))
# = 2 * (1 + 50 * 0.00454) = 2 * 1.227 = 2.45 points
```

---

### Giới hạn rủi ro được kiểm tra khi nào

#### `_max_drawdown_breached()` — Kiểm tra max DD
```python
# Chế độ FTMO (fixed_initial): equity không được xuống dưới init_eq * 90%
equity <= init_eq * (1 - 0.10)  # → dừng hẳn simulation

# Chế độ trailing_peak: equity không được xuống dưới đỉnh vốn * 90%
equity <= peak_eq * (1 - 0.10)
```

#### Daily stop
```python
# Mỗi khi đóng lệnh, cộng PnL vào daily_pnl
# Nếu daily_pnl <= -(init_eq * 0.05) → daily_stop = True
# → Không mở thêm lệnh mới trong ngày hôm đó
```

---

## portfolio.py — Gộp kết quả nhiều symbol

### Vai trò
Sau khi chạy backtest cho từng symbol riêng, file này gộp các kết quả lại thành view danh mục.

### Các hàm quan trọng

#### `build_combined_equity()` — Gộp đường vốn

```python
equity_by_symbol = {
    "US30": pd.Series([100000, 100500, 101000, ...], index=timestamps),
    "GOLD": pd.Series([50000, 50200, 49800, ...], index=timestamps),
}
eq_frame, combined = build_combined_equity(equity_by_symbol)

# eq_frame: DataFrame với mỗi cột là 1 symbol
# combined: Series tổng vốn toàn danh mục
```

**Lưu ý:** Các symbol có timestamps khác nhau (BTCUSD 24/7 vs US30 theo giờ).  
Hàm này tự align bằng `fillna(method='ffill')` — giữ giá trị cuối cùng biết được.

#### `check_portfolio_ftmo()` — Kiểm tra FTMO cấp tài khoản

```python
result = check_portfolio_ftmo(
    combined_equity,            # Series tổng vốn
    initial_balance=100_000,
    daily_loss_limit=0.05,      # 5% mỗi ngày
    max_dd_limit=0.10,          # 10% tổng
)

# result:
{
    'ftmo_pass': True/False,
    'breach_reason': None / "daily_loss" / "max_drawdown",
    'breach_date': None / Timestamp,
    'max_daily_loss_pct': 0.032,    # Ngày tệ nhất mất 3.2%
    'n_daily_breach_days': 0,       # Số ngày vi phạm daily loss
    'max_dd_pct': 0.076,            # Max drawdown 7.6%
    'max_dd_usd': 7600.0,
}
```

**Lưu ý hiện tại:** Hàm này **chỉ báo cáo** sau khi simulation chạy xong.  
Nó chưa dừng simulation tại thời điểm breach xảy ra. Đây là điểm cần cải thiện trong tương lai.

---

## monte_carlo.py — Kiểm tra độ bền

### Vai trò
Trả lời câu hỏi: *"Nếu thứ tự các lệnh xáo trộn ngẫu nhiên, chiến lược có vẫn hoạt động không?"*

### `run_monte_carlo()` — Chạy mô phỏng

```python
mc_result = run_monte_carlo(
    trade_pnls=[450, -200, 300, -150, ...],  # PnL từng lệnh theo thứ tự thời gian
    n_iter=1000,                              # Chạy 1000 kịch bản
    dd_threshold=0.10,                        # Ngưỡng max DD quan tâm (10%)
    initial_balance=100_000,
)
```

**Cách hoạt động — Block Bootstrap:**
1. Lấy chuỗi PnL gốc có n lệnh
2. Chia thành các khối nhỏ (block_size = sqrt(n)) để giữ tương quan chuỗi
3. Xáo trộn các khối ngẫu nhiên → tạo 1 kịch bản mới
4. Tính equity, Sharpe, max DD cho kịch bản đó
5. Lặp lại 1000 lần → có phân phối thống kê

**Kết quả trả về:**
```python
{
    'equity_p5':     [...],   # Đường vốn xấu (5% trường hợp tệ hơn)
    'equity_p50':    [...],   # Đường vốn trung vị
    'equity_p95':    [...],   # Đường vốn tốt (5% trường hợp tốt hơn)
    'prob_exceed_dd': 0.12,   # 12% kịch bản có max DD > 10%
    'sharpe_ci_low':  0.8,    # Sharpe thấp nhất trong khoảng 95% CI
    'sharpe_ci_high': 1.4,
}
```

### `plot_monte_carlo()` — Vẽ biểu đồ

```python
fig = plot_monte_carlo(mc_result)
fig.show()
```

Tạo 3 biểu đồ: đường vốn percentile, phân phối max DD, phân phối Sharpe.

---

## broker.py — Làm tròn lot theo broker

### `round_lot_size()` — Làm tròn lot

```python
from core_python.shared.broker import round_lot_size

lot = round_lot_size(lot_size=0.037, min_lot=0.01, max_lot=50.0, lot_step=0.01)
# → 0.03  (làm tròn xuống bội số của 0.01, trong khoảng [0.01, 50.0])
```

Quy tắc:
- Làm tròn **xuống** (không bao giờ làm tròn lên — tránh rủi ro quá lot)
- Clamp vào `[min_lot, max_lot]`
- Phải là bội số của `lot_step`

---

## Tóm tắt — Khi nào dùng file nào

| Việc cần làm | File cần dùng |
|---|---|
| Lấy dữ liệu từ DB cho backtest | `data.py` → `load_backtest_ohlcv()` |
| Chạy mô phỏng giao dịch 1 symbol | `execution.py` → `backtest_symbol()` |
| Tìm tham số tốt (chạy nhiều lần) | `execution.py` → `backtest_fast()` |
| Gộp kết quả nhiều symbol | `portfolio.py` → `build_combined_equity()` |
| Kiểm tra FTMO cấp danh mục | `portfolio.py` → `check_portfolio_ftmo()` |
| Kiểm tra độ bền chiến lược | `monte_carlo.py` → `run_monte_carlo()` |
| Tính Sharpe, PF, Drawdown | `metrics.py` → `calc_metrics()` |
