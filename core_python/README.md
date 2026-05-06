# core_python — Hệ thống Tín hiệu Giao dịch SEN05

## Mục đích

`core_python` là trái tim của hệ thống SEN05 — nơi toàn bộ logic tính toán tín hiệu giao dịch, hiển thị biểu đồ và gửi cảnh báo Telegram được tập trung.

Module này phục vụ hai mục đích song song:

| Mục đích | Thành phần |
|----------|-----------|
| **Xem biểu đồ tương tác** — tra cứu tín hiệu theo yêu cầu qua trình duyệt | `chart/`, `data/`, `strategies/`, `indicators/` |
| **Giám sát 24/7** — tự động gửi tín hiệu mới lên Telegram khi bar đóng | `notify/`, `data/`, `strategies/` |

---

## Phạm vi

**Module này làm:**
- Tải dữ liệu OHLCV từ SQL Server
- Tính toán các chỉ báo kỹ thuật (MA, MACD, ATR)
- Phát hiện tín hiệu BUY/SELL theo từng chiến lược
- Tính mức Entry, Stop Loss, Take Profit để hiển thị
- Render biểu đồ qua Flask + Lightweight Charts (trình duyệt)
- Giám sát nhiều symbol × khung thời gian và gửi cảnh báo Telegram

**Module này KHÔNG làm:**
- Đặt lệnh thật (không kết nối broker)
- Quản lý vốn hoặc rủi ro
- Backtest (đây là công cụ forward-scan, không phải backtester)
- Lưu trữ lịch sử tín hiệu dài hạn (state.json chỉ dùng để chống gửi trùng)

---

## Kiến trúc — Luồng dữ liệu

```
┌─────────────────────────────────────────────────────────────────┐
│                         SQL Server                              │
│              DWH.Fact_OHLCV + DWH.Dim_Timeframe                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │ TOP N bars, ORDER BY BarTime DESC
                            ▼
                   data/loader.py
             load(symbol, tf, n_bars)
             → DataFrame [bartime, open, high, low, close, volume]
                            │
                            ▼
              strategies/<tên_chiến_lược>/
          ┌─────────────────────────────────┐
          │ 1. add_indicators(df, params)   │  ← Thêm MA, MACD, ATR
          │ 2. detect_signals(df, ...)      │  ← Tính signal (+1/-1/0)
          │ 3. add_levels(df, params, sym)  │  ← Tính Entry, SL, TP
          └─────────────────────────────────┘
                            │
               ┌────────────┴──────────────┐
               │                           │
               ▼                           ▼
     [Dashboard - trình duyệt]   [Watcher - 24/7 Telegram]
               │                           │
     chart/payload.py             notify/formatter.py
     → JSON payload               → Tin nhắn HTML
               │                           │
     chart/server.py              notify/notifier.py
     Flask /api/scan              → Telegram / Discord
               │
     chart/static/app.js
     Lightweight Charts
```

---

## Cấu trúc thư mục

```
core_python/
├── README.md               ← File này
├── __init__.py
├── config.py               ← Metadata symbol, TF, kết nối SQL
├── main.py                 ← Entry point khởi động Flask server
│
├── data/
│   └── loader.py           ← Tải OHLCV từ SQL Server
│
├── indicators/
│   └── core.py             ← SMA, EMA, MACD, ATR, safe_ratio
│
├── export/
│   └── to_csv.py           ← Xuất tín hiệu ra CSV (cho backtest)
│
├── chart/
│   ├── server.py           ← Flask app: /api/scan, /api/config, /api/export
│   ├── payload.py          ← Xây JSON payload cho Lightweight Charts
│   └── static/
│       ├── index.html      ← Giao diện web
│       ├── app.js          ← Logic frontend (vanilla JS)
│       └── styles.css      ← Giao diện tối (dark mode)
│
├── strategies/
│   ├── registry.py         ← StrategySpec — đăng ký chiến lược
│   ├── combo/
│   │   ├── config.py       ← Tham số mặc định và validation
│   │   ├── signals.py      ← Chỉ báo và phát hiện tín hiệu
│   │   └── levels.py       ← Tính Entry/SL/TP dạng breakout
│   └── ma_cross/
│       ├── config.py       ← Tham số mặc định và validation
│       ├── signals.py      ← Chỉ báo và phát hiện tín hiệu MA cắt
│       └── levels.py       ← Tính Entry/SL/TP dựa trên bar tiếp theo
│
└── notify/
    ├── scan_config.py      ← Danh sách nhóm cần scan (symbol × TF)
    ├── signal_watcher.py   ← Vòng lặp 24/7, canh bar đóng, gửi alert
    ├── formatter.py        ← Định dạng tin nhắn HTML cho Telegram
    ├── notifier.py         ← Backend gửi (Telegram / Discord / dry-run)
    └── state.py            ← JSON store chống gửi tín hiệu trùng
```

---

## Mô tả từng submodule

### `config.py` — Cấu hình trung tâm
Nạp metadata từ file `config.py` gốc ở thư mục cha (SEN05 root). Cung cấp:
- `SYMBOLS`: dict mọi symbol được hỗ trợ với symbol_id DB, loại tài sản, buffer X
- `TF_MINUTES`: ánh xạ mã khung thời gian ("H1", "M5") sang số phút
- `get_symbol(symbol)`: tra cứu symbol_id và thông số để loader dùng

### `data/loader.py` — Tải dữ liệu OHLCV
Kết nối SQL Server, lấy N bar gần nhất cho một symbol và khung thời gian.  
**Hợp đồng UTC:** `BarTime` trong DB lưu dạng UTC-naive (Capital.com/MT5). Caller phải tự localize nếu cần timezone-aware.

### `indicators/core.py` — Chỉ báo kỹ thuật
Các hàm thuần tính toán trên `pd.Series`/`pd.DataFrame`. Không có side effect.
- `sma`, `ema`, `ma` — Trung bình động
- `macd_hist` — MACD Histogram (MACD line − Signal line)
- `atr` — Average True Range theo Wilder (ewm alpha=1/period)
- `safe_ratio` — Chia có xử lý NaN và inf

### `strategies/registry.py` — Đăng ký chiến lược
`StrategySpec` là dataclass đóng gói 4 callable của một chiến lược:
`normalize_params` → `add_indicators` → `detect_signals` → `add_levels`.  
Thêm chiến lược mới: tạo module trong `strategies/`, đăng ký vào `STRATEGIES`.

### `strategies/combo/` — Chiến lược Combo

**Điều kiện BUY:**
```
close vượt lên trên MA (cross up)
AND close > open (nến tăng)
AND MACD histogram > 0
AND R:R >= MIN_RR (nếu bật filter)
```

**Điều kiện SELL:** Ngược lại (cross down, nến giảm, MACD < 0).

**Entry:** Breakout qua đỉnh/đáy bar tín hiệu + buffer X.  
**SL:** Đáy/đỉnh bar tín hiệu − buffer X.  
**TP:** Entry ± KTP × ATR.

### `strategies/ma_cross/` — Chiến lược MA Cross

**Điều kiện BUY:** `fast_MA` cắt lên trên `slow_MA` (crossover).  
**Điều kiện SELL:** `fast_MA` cắt xuống dưới `slow_MA` (crossunder).

**Entry:** Giá mở cửa bar *tiếp theo* + spread + slippage.  
**SL:** Entry ± ATR_STOP_MULT × ATR.  
**TP:** Entry ± ATR_TP_MULT × ATR (nếu ATR_TP_MULT > 0).

### `chart/` — Dashboard trình duyệt
Flask server lắng nghe tại `127.0.0.1:8515`. Giao diện lấy dữ liệu qua REST API và render bằng thư viện [Lightweight Charts](https://tradingview.github.io/lightweight-charts/).  
**Lưu ý:** Chart *có bao gồm bar đang mở* (chưa đóng). Watcher thì không.

### `export/to_csv.py` — Xuất CSV
Lọc các dòng có signal != 0 và xuất file CSV gồm `[bartime, atr, signal]` vào thư mục `core_python/exports/`. Phục vụ import vào cTrader hoặc công cụ backtest khác.

### `notify/` — Hệ thống cảnh báo 24/7

| File | Vai trò |
|------|---------|
| `scan_config.py` | Định nghĩa nhóm scan (symbol × TF × chiến lược) |
| `signal_watcher.py` | Vòng lặp chính, canh bar đóng, gọi check_once() |
| `formatter.py` | Chuyển signal row → tin nhắn HTML có emoji |
| `notifier.py` | Gửi Telegram / Discord, retry khi rate-limit |
| `state.py` | Lưu key đã gửi vào JSON, TTL 60 ngày, ghi atomic |

---

## Giả định quan trọng

| Giả định | Chi tiết |
|----------|----------|
| **UTC-naive** | `BarTime` trong DB không có timezone. Tất cả so sánh thời gian phải localize về UTC trước. |
| **Bar đóng** | Watcher chỉ phát tín hiệu trên bar đã đóng hoàn toàn (`closed_only=True`). Dashboard hiển thị cả bar đang mở. |
| **Giá trị signal** | `+1` = BUY, `-1` = SELL, `0` = không có tín hiệu. |
| **Không lookahead** | Tín hiệu được tính từ dữ liệu của bar hiện tại (đã đóng). Combo dùng entry breakout → không cần shift. MA Cross dùng giá open bar tiếp theo → shift(-1). |
| **TTL state** | `state.json` TTL = 60 ngày phải > cửa sổ bars lớn nhất (H4: 300 bars × 4h = 50 ngày). |

---

## Cách chạy

```powershell
# Dashboard biểu đồ tương tác
.venv\Scripts\python.exe -m core_python.main
# Mở trình duyệt: http://127.0.0.1:8515

# Watcher Telegram — dry-run (in ra màn hình, không gửi)
.venv\Scripts\python.exe -m core_python.notify.signal_watcher --dry-run --once

# Watcher Telegram — seed state trước khi chạy lần đầu
.venv\Scripts\python.exe -m core_python.notify.signal_watcher --warm-up

# Watcher Telegram — chế độ production 24/7
.venv\Scripts\python.exe -m core_python.notify.signal_watcher
```

Yêu cầu file `.env` có:
```
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>
```

---

## Giới hạn đã biết

- **Không có bar gap detection:** `_validate()` trong loader chỉ lọc NaN/duplicate, không kiểm tra khoảng trống thời gian bất thường.
- **MA Cross: bar cuối thiếu entry:** `add_ma_cross_levels()` dùng `shift(-1)` nên bar tín hiệu cuối cùng trong DataFrame không có entry_price (NaN).
- **Không retry DB:** Nếu SQL Server tạm thời ngắt, watcher log lỗi và tiếp tục — không có cơ chế retry với backoff.
- **Single-process:** Không có multi-threading. Nếu một symbol mất nhiều thời gian truy vấn, các symbol trong cùng nhóm sẽ bị delay.
