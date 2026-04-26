# strategies/combo/ — Chiến lược Combo v2

## Tổng quan

Thư mục này chứa toàn bộ mọi thứ đặc thù của chiến lược Combo v2:
- Tham số và cấu hình (`config.py`)
- Luật tín hiệu BUY/SELL (`logic.py`)
- Công cụ nhìn tín hiệu trực quan (`scanner.py`)
- Điều phối chạy 1 symbol (`symbol/`)
- Điều phối chạy cả danh mục (`portfolio/`)

```
combo/
├── config.py          Bảng tham số trung tâm — NGUỒN SỰ THẬT DUY NHẤT
├── logic.py           Luật tín hiệu — KHÔNG SỬA NẾU KHÔNG HIỂU RÕ
├── scanner.py         Nhìn tín hiệu trên chart (KHÔNG phải backtest)
├── notebook_utils.py  Helper dashboard/bảng/chart dùng chung cho notebook
├── symbol/            Làm việc với 1 symbol
└── portfolio/         Làm việc với cả danh mục
```

---

## Notebook workflow — Cách đọc các notebook mới

Các notebook `01` → `06` đã được tổ chức lại theo cùng một nhịp:

1. **Bootstrap path an toàn**  
   Tự tìm repo root bằng `pyproject.toml` + `core_python/shared`, tránh nhận nhầm
   `strategies/combo/config.py` là root.

2. **Import + cấu hình hiển thị**  
   Gọi `configure_notebook()` từ `notebook_utils.py` để thống nhất style bảng,
   chart và pandas display.

3. **`RUN_CONFIG`**  
   Mọi tham số quan trọng của lần chạy nằm trong một dict duy nhất. Khi muốn đổi
   symbol, account mode, date range, max bars, search space hoặc export report,
   ưu tiên sửa tại `RUN_CONFIG`.

4. **Run pipeline**  
   Chạy backtest/optimize/portfolio/walk-forward/scanner.

5. **Dashboard phân tích**  
   Notebook không chỉ `display()` bảng thô nữa, mà dùng helper để hiển thị:
   - KPI dashboard
   - equity + drawdown
   - trade explorer
   - price chart kèm entry/exit
   - optimizer heatmap
   - portfolio contribution
   - walk-forward stability

6. **Export tùy chọn**  
   Một số notebook có cell export CSV. Mặc định export tắt để tránh tạo file ngoài
   ý muốn. Bật bằng cách đổi `RUN_CONFIG["export_report"] = True` hoặc
   `EXPORT_REPORT = True`.

### File `notebook_utils.py` dùng để làm gì?

File này chỉ phục vụ giao diện notebook và phân tích hậu kỳ. Nó **không thay đổi**
luật tín hiệu, execution engine, lot sizing, phí, swap hay logic quản trị rủi ro.

Các helper chính:

| Helper | Dùng để làm gì |
|---|---|
| `show_run_config()` | Hiển thị cấu hình lần chạy trước khi execute |
| `show_kpi_dashboard()` | Hiển thị metrics theo thứ tự dễ đọc, có tô màu |
| `plot_equity_dashboard()` | Vẽ equity, drawdown và cumulative trade PnL |
| `plot_price_with_trades()` | Vẽ giá/MA kèm entry và exit của trade |
| `show_trade_explorer()` | Soi trade log theo exit reason, direction, top win/loss |
| `plot_optimization_dashboard()` | Vẽ top candidates, scatter và heatmap optimizer |
| `plot_portfolio_dashboard()` | Vẽ combined equity, drawdown, per-symbol equity, PnL contribution |
| `plot_walkforward_dashboard()` | Vẽ stability dashboard cho OOS windows |

---

## config.py — Bảng tham số trung tâm

### Vai trò
**Đây là nơi DUY NHẤT bạn được phép điều chỉnh tham số chiến lược.**  
Tất cả code khác đều đọc tham số từ đây — không file nào hardcode số riêng.

### Cấu trúc bên trong

---

#### STRATEGY — Tham số chiến lược tổng thể

```python
STRATEGY = {
    "ktp":                  2.3,    # TP cách entry = ktp × ATR (bội số ATR)
    "min_rr":               1.25,   # Tỷ lệ TP/SL tối thiểu để entry
    "risk_per_trade":       0.005,  # 0.5% vốn rủi ro mỗi lệnh
    "trailing_activation":  1.0,    # Kích hoạt trailing khi lãi >= 1.0 × ATR
    "pending_ttl_bars":     3,      # Lệnh chờ tồn tại tối đa 3 bar
    "partial_tp_fraction":  0.5,    # Chốt 50% tại TP đầu tiên
}
```

**Giải thích từng tham số:**
- `ktp = 2.3`: Nếu ATR = 150 điểm, TP đặt cách entry = 2.3 × 150 = 345 điểm
- `min_rr = 1.25`: Nếu khoảng TP/SL < 1.25, bỏ qua tín hiệu đó
- `risk_per_trade = 0.005`: Vốn 100.000 USD → mỗi lệnh rủi ro tối đa 500 USD
- `trailing_activation = 1.0`: Trailing bắt đầu khi lệnh đang lãi >= 1 × ATR
- `pending_ttl_bars = 3`: Lệnh chờ không fill sau 3 bar H4 (12 giờ) → huỷ
- `partial_tp_fraction = 0.5`: Chốt 50% ở TP, để 50% tiếp tục trailing

---

#### ACCOUNT_MODES — Chế độ tài khoản

```python
ACCOUNT_MODES = {
    "standard": {
        "daily_loss_limit": 1.0,      # 100% = không có giới hạn thực tế
        "max_drawdown_limit": 1.0,    # 100% = không có giới hạn thực tế
    },
    "ftmo": {
        "daily_loss_limit": 0.05,     # Dừng ngày khi lỗ 5%
        "max_drawdown_limit": 0.10,   # Dừng hẳn khi drawdown 10%
        "max_drawdown_mode": "fixed_initial",  # Tính từ vốn ban đầu (không trailing)
    }
}
```

Dùng `account_mode="ftmo"` để mô phỏng theo quy tắc FTMO. Dùng `"standard"` để nghiên cứu không bị giới hạn.

---

#### SYMBOLS — Cấu hình từng tài sản (11 symbols)

```python
SYMBOLS = {
    "US30": {
        "symbol_id":    10,         # ID trong database
        "label":        "US30",     # Tên hiển thị
        "x":            10,         # Buffer breakout (điểm)
        "ktp":          2.3,        # kTP riêng (ghi đè STRATEGY.ktp)
        "ma_period":    20,         # MA period riêng
        "contract_value": 1.0,      # 1 lot = $1 mỗi điểm
        "point_size":   1.0,        # 1 point = 1 điểm giá
        "spread_pts":   3,          # Spread broker (điểm)
        "commission_per_lot": 3.5,  # Phí 1 chiều (USD/lot)
        "slippage_pts": 2,          # Slippage ước tính (điểm)
        "min_lot_size": 0.01,       # Lot tối thiểu
        "max_lot_size": 50.0,       # Lot tối đa
        "lot_step":     0.01,       # Bước làm tròn lot
        "swap_long_per_lot_per_day":  -3.5,   # Phí qua đêm mua (USD/lot/ngày)
        "swap_short_per_lot_per_day": 1.2,    # Phí qua đêm bán
        "session_hours_utc": [],    # Rỗng = giao dịch toàn thời gian
        "group": "indices_us",
        "spec_verified": False,     # ⚠️ Chưa verify với broker thật
    },
    "GOLD": { ... },
    "BTCUSD": { ... },
    # ... 8 symbols khác
}
```

**Cột `x` (breakout buffer) quan trọng như thế nào:**
- US30 `x=10`: Tín hiệu BUY → lệnh chờ ở `bar_high + 10`, SL ở `bar_low - 10`
- GOLD `x=0.5`: Tín hiệu BUY → lệnh chờ ở `bar_high + 0.5`, SL ở `bar_low - 0.5`
- BTCUSD `x=50`: Tín hiệu BUY → lệnh chờ ở `bar_high + 50`, SL ở `bar_low - 50`

**⚠️ Cảnh báo `spec_verified`:**  
Nhiều symbol có `spec_verified: False` — nghĩa là các giá trị swap, spread, commission chưa được đối chiếu với broker thật. PnL tính ra chưa hoàn toàn chính xác.

---

#### BROKER_PROFILES — Cấu hình theo broker

```python
BROKER_PROFILES = {
    "ftmo_2step_mt5": {
        "name": "ftmo_2step_mt5",
        "label": "FTMO 2-Step MT5",
        "platform": "mt5",
        "defaults": {
            "commission_per_lot": 3.5,
            "lot_step": 0.01,
        },
        "symbols": {
            "US30": {"spread_pts": 3, ...},   # Override riêng cho US30
        }
    },
    "windsor_prime_mt5": { ... },
}
```

Dùng khi broker của bạn có specs khác. Truyền `broker_profile="ftmo_2step_mt5"` vào các hàm backtest.

---

#### OPTIMIZATION — Tham số cho quá trình tối ưu

```python
OPTIMIZATION = {
    "symbol": {
        "ktp_multipliers":       [0.9, 1.0, 1.1],      # Thử kTP × 3 giá trị
        "x_offsets":             [-2, 0, 2],             # Thử x ± 2
        "trailing_multipliers":  [0.75, 1.0, 1.25],     # Thử trailing × 3 giá trị
        "ma_offsets":            [-5, 0, 5],             # Thử MA ± 5
        "max_bars":              80000,                  # Dùng tối đa 80.000 bar
        "score_column":          "sharpe",               # Sắp xếp kết quả theo Sharpe
    },
    "portfolio": {
        "top_k_per_symbol":      5,       # Lấy top 5 tham số mỗi symbol
        "score_column":          "score", # Dùng cột score tổng hợp
    }
}
```

3 × 3 × 3 × 3 = 81 tổ hợp tham số mỗi lần grid search.

---

### Các hàm quan trọng trong config.py

#### `get_indicator_params()` — Lấy tham số indicator

```python
p = get_indicator_params()
# → {'MA_PERIOD': 20, 'MACD_FAST': 5, 'MACD_SLOW': 25, 'MACD_SIGNAL': 5, 'ATR_PERIOD': 5, ...}
```

#### `get_symbol_params(sym_key)` — Lấy toàn bộ params 1 symbol

```python
cfg = get_symbol_params("US30")
# → Trả về dict đầy đủ gồm: x, ktp, ma_period, contract_value, swap, lot, ...
```

#### `get_account_settings(mode)` — Lấy cài đặt tài khoản

```python
s = get_account_settings("ftmo")
# → {'daily_loss_limit': 0.05, 'max_drawdown_limit': 0.10, ...}
```

#### `validate_config()` — Kiểm tra config có hợp lệ không

```python
report = validate_config()
# report = {
#   'warnings': ['US30: broker spec is not marked verified.', ...],
#   'errors':   [],
#   'ok':       True
# }
```

Chạy hàm này trước khi bắt đầu optimize để biết symbols nào còn thiếu specs.

---

## logic.py — Luật tín hiệu

### Vai trò
**Đây là nơi DUY NHẤT định nghĩa khi nào thì BUY, khi nào thì SELL.**  
Scanner, backtest, optimizer, walk-forward đều gọi từ đây. Sửa ở đây → toàn bộ pipeline thay đổi.

### `add_combo_indicators()` — Tính chỉ báo

```python
df_with_indicators = add_combo_indicators(df_ohlcv, params)
```

Tạo thêm các cột:
| Cột | Cách tính | Ý nghĩa |
|-----|-----------|---------|
| `ma` | `close.rolling(20).mean()` | Đường trung bình 20 bar |
| `macd_h` | `EMA(5) - EMA(25)` rồi trừ `EMA(5)` của hiệu | MACD histogram |
| `atr` | Wilder ATR 5 bar | Độ biến động trung bình |
| `prev_close` | `close.shift(1)` | Giá đóng cửa bar trước |
| `prev_ma` | `ma.shift(1)` | MA bar trước |

`prev_close` và `prev_ma` cần thiết để phát hiện giao cắt (crossover):  
→ "Bar trước ở dưới MA, bar này ở trên MA" = vừa cắt lên.

---

### `detect_combo_signals()` — Phát tín hiệu (vectorized)

Đây là hàm kiểm tra tất cả 4 điều kiện cho **mọi bar cùng lúc** (không phải từng bar một).

**Tín hiệu BUY (signal = +1)** — tất cả 4 điều kiện phải đúng:

```python
cross_up   = (prev_close <= prev_ma) & (close > ma)    # Vừa cắt lên MA
bull_candle = close > open                              # Nến tăng (xanh)
macd_pos   = macd_h > 0                                # MACD histogram dương
rr_ok      = (ktp * atr) / (high - low + 2*x) >= 1.25 # Tỷ lệ TP/SL đủ lớn

buy_signal = cross_up & bull_candle & macd_pos & rr_ok
```

**Tín hiệu SELL (signal = -1)** — tất cả 4 điều kiện phải đúng:

```python
cross_down  = (prev_close >= prev_ma) & ~(close > ma)  # Vừa cắt xuống MA
bear_candle = close < open                              # Nến giảm (đỏ)
macd_neg    = macd_h < 0                                # MACD histogram âm
rr_ok       = ...                                       # Cùng bộ lọc RR

sell_signal = cross_down & bear_candle & macd_neg & rr_ok
```

**Lưu ý bộ lọc RR:**
```
rr = (ktp × ATR) / (high - low + 2×x)
```
- Tử số: khoảng cách đến TP
- Mẫu số: khoảng SL (dựa trên độ rộng nến + buffer 2 chiều)
- Nếu nến quá rộng so với ATR → RR thấp → bỏ qua tín hiệu

---

### `session_mask()` — Lọc theo giờ giao dịch

```python
mask = session_mask(df, hours_utc=[8, 9, 10, 11, 12, 13, 14, 15])
# → True cho bar trong giờ 8h-15h UTC, False cho bar ngoài giờ
```

Nếu `hours_utc=[]` (rỗng) → tất cả bar đều True (không lọc).

---

### `build_raw_signal_masks()` — Tín hiệu thô cho scanner

Khác với `detect_combo_signals()`, hàm này **không áp bộ lọc RR**.  
Dùng cho scanner để hiển thị tất cả tín hiệu tiềm năng trên chart, kể cả tín hiệu không đủ RR.

---

### `resolve_trade_hit()` — Kiểm tra SL/TP bị chạm

```python
result = resolve_trade_hit(bar, direction=1, trade_sl=32900, trade_tp=33500)
# → 'TP' nếu bar_high >= 33500
# → 'SL' nếu bar_low <= 32900
# → 'SL' nếu cùng bar chạm cả hai (ưu tiên bảo thủ)
# → None nếu không chạm gì
```

---

### `scan_signals_reversal()` — Quét tín hiệu đảo chiều

Hàm này duyệt bar-by-bar để mô phỏng trạng thái vị thế và phát hiện khi nào nên đảo chiều.  
Khác với `detect_combo_signals()` (vectorized), hàm này cần duyệt tuần tự vì phải theo dõi trạng thái.

Dùng trong scanner để visualize — **không dùng trong backtest**.

---

## scanner.py — Công cụ nhìn tín hiệu

### ⚠️ Quan trọng cần hiểu rõ

Scanner **KHÔNG phải backtest**. Nó:
- ✅ Hiển thị tín hiệu trên chart
- ✅ Cho thấy entry/SL/TP/RR của từng tín hiệu
- ❌ Không mô phỏng pending fill (không biết lệnh có fill không)
- ❌ Không tính slippage/commission/swap
- ❌ Không có partial TP, trailing SL
- ❌ Không có lot sizing

**Khi nào dùng scanner:**  
→ Muốn xem tín hiệu trông như thế nào trên chart  
→ Muốn kiểm tra tín hiệu có hợp lý không về mặt trực quan

**Khi nào KHÔNG dùng scanner:**  
→ Đánh giá hiệu quả chiến lược (dùng backtest)  
→ So sánh tham số (dùng optimizer)

---

## Tóm tắt — Quy tắc sử dụng

| Việc muốn làm | File cần dùng |
|---|---|
| Điều chỉnh tham số chiến lược | `config.py` |
| Hiểu luật tín hiệu | `logic.py` |
| Xem tín hiệu trên chart | `scanner.py` |
| Chạy backtest 1 symbol | `symbol/` → xem README riêng |
| Tối ưu tham số | `symbol/` → xem README riêng |
| Chạy toàn danh mục | `portfolio/` → xem README riêng |

---

## Đọc tiếp

- [symbol/README.md](symbol/README.md) — Chạy backtest, optimize, walk-forward cho 1 symbol
- [portfolio/README.md](portfolio/README.md) — Chạy danh mục, so sánh FTMO vs standard
