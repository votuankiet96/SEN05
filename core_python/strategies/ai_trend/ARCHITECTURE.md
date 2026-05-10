# AI Trend Strategy Architecture

Tai lieu nay mo ta dung hanh vi hien tai cua chien luoc `ai_trend` theo code trong `core_python/strategies/ai_trend/` va luong notify production trong `core_python/notify/`.

## 1. Muc Dich

AI Trend la chien luoc multi-timeframe:

```text
Trend TF:
  Xac dinh bias bullish / bearish bang AI Trend Navigator KNN.

Entry TF:
  Tim tin hieu BUY/SELL dau tien trong moi trend segment bang EMA 13/34.
```

Dashboard cho phep chon:

```text
Trend TF: H1, H2, H3, H4
Entry TF: M45, M30, M20, M15, M10, M5
```

Production Telegram hien tai dung contract co dinh:

```text
Trend alert: H3
Entry alert: M45
```

## 2. Production Symbols

AI Trend production scan 11 symbol:

```text
GOLD
BTCUSD
US30
UK100
J225
HK50
DE40
EURUSD
USDJPY
GBPUSD
AUDUSD
```

Danh sach nay nam trong `core_python/notify/scan_config.py`, khong nam trong folder strategy. Ly do: `notify/scan_config.py` la cau hinh runtime chung cho tat ca chien luoc.

## 3. File Ownership

```text
strategies/ai_trend/config.py
  Tham so strategy va dashboard: Trend TF, Entry TF, bars, KNN, EMA, Dow wave,
  MACD va level defaults.

strategies/ai_trend/signals.py
  Build trend frame va entry frame.
  Merge H3 da dong vao M45.
  Detect M45 signal dau tien trong moi trend segment voi EMA va MACD confirmation.

strategies/ai_trend/alerts.py
  Chuyen frame da tinh thanh alert domain:
    - h3_trend_change
    - m45_entry_signal
  Format Telegram HTML rieng cho AI Trend.

strategies/ai_trend/payload.py
  Tao JSON payload hai chart cho dashboard.

strategies/ai_trend/levels.py
  Tinh entry_price, sl_price, tp_price, risk_reward cho AI Trend.
  Entry = open cua cay Entry TF tiep theo.
  SL = swing Dow gan nhat da xac nhan.
  TP = fixed R:R mac dinh 1.5.
```

Nhung phan sau la shared runtime, khong thuoc rieng AI Trend:

```text
notify/scan_config.py
  Production symbols/groups.

notify/signal_watcher.py
  Scheduler, load DB, drop open bar, goi AI Trend alert extractor, gui Telegram.

notify/state.py
  Dedup state va warm-up.

notify/notifier.py
  Telegram/Discord backend.
```

## 4. Trend Frame

`prepare_trend_frame()` nhan OHLC cua Trend TF va them:

```text
ai_knn
ai_avg
ai_direction
h3_bias
h3_bias_segment
h3_close_time
h3_window_start
h3_window_end
knn_cross_over_avg
knn_cross_under_avg
knn_switch_up
knn_switch_down
knn_neutral
```

Bias:

```text
ai_direction = 1  -> h3_bias = 1  -> bullish
ai_direction = -1 -> h3_bias = -1 -> bearish
ai_direction = 0  -> h3_bias = 0  -> neutral
```

`h3_bias_segment` tang khi bias thay doi. M45 entry chi lay signal dau tien trong moi segment co bias khac 0.

## 5. Entry Frame

`prepare_entry_frame()` nhan OHLC cua Entry TF va them:

```text
ema_fast = EMA(close, 13)
ema_slow = EMA(close, 34)
ema_gap
ema_gap_pct
macd_h = MACD histogram (5, 25, 5 mac dinh)
m45_close_time
dow wave columns
```

Dow wave phuc vu dashboard va duoc dung de tim SL gan nhat sau khi pivot da xac nhan.

## 6. No-Lookahead Merge

`merge_trend_into_entry()` dung `pd.merge_asof()`:

```text
left_on  = entry.bartime
right_on = trend.h3_close_time
direction = backward
```

Dieu kien thuc te:

```text
h3_close_time <= entry bartime
```

Nghia la M45 chi duoc dung H3 khi H3 da dong truoc luc M45 bat dau. Cay M45 cuoi nam ben trong H3 vua dong khong duoc dung de tao signal cho H3 do.

Vi du:

```text
H3 close = 12:00 UTC

M45 11:15 -> 12:00:
  khong hop le cho trend H3 vua dong

M45 12:00 -> 12:45:
  la cay dau tien duoc xet
```

## 7. Signal Rule

Trong moi H3 bias segment:

```text
Neu h3_bias = 1:
  BUY tai cay Entry TF dau tien co EMA13 > EMA34 va macd_h > 0

Neu h3_bias = -1:
  SELL tai cay Entry TF dau tien co EMA13 < EMA34 va macd_h < 0

Neu khong co cay nao align truoc khi bias doi:
  Khong co M45 signal cho segment do
```

Moi segment chi co toi da mot signal entry.

## 8. Telegram Alerts

### H3 Trend Change

Duoc tao boi `extract_h3_trend_alerts()` khi:

```text
h3_bias in [1, -1]
AND h3_bias != previous h3_bias
```

Thong bao:

```text
AI Trend H3 Trend Change - SYMBOL
Direction: BULLISH/BEARISH
H3 close
H3 open
Close
KNN
Average
Segment
```

### M45 Entry Signal

Duoc tao boi `extract_m45_entry_alerts()` tu nhung dong `signal != 0` trong entry frame.

Thong bao:

```text
AI Trend M45 BUY/SELL - SYMBOL
M45 close
M45 open
H3 close
Close
EMA fast
EMA slow
H3 KNN
H3 Avg
Segment
```

## 9. State Key

AI Trend dedup key:

```text
ai_trend|kind|SYMBOL|TF|event_time|direction
```

Vi du:

```text
ai_trend|h3_trend_change|BTCUSD|H3|2026-05-06 12:00:00|1
ai_trend|m45_entry_signal|BTCUSD|M45|2026-05-06 14:15:00|-1
```

State duoc luu chung voi cac chien luoc khac:

```text
core_python/runtime/state.json
```

## 10. Dashboard vs Telegram

Dashboard:

```text
Co the hien thi signal lich su theo so bars dang load.
Khong dung state.json.
Khong quyet dinh Telegram gui hay khong.
```

Telegram watcher:

```text
Chi scan bar da dong.
Chi gui alert moi chua co trong state.
Warm-up danh dau signal lich su ma khong gui.
```

Vi vay thay marker cu tren dashboard la binh thuong va khong co nghia Telegram se gui lai.

## 11. Warm-Up Khi Doi Symbol/TF

Sau khi them symbol, TF, bars hoac group notify moi, can chay:

```powershell
.venv\Scripts\python.exe -m core_python.notify.signal_watcher --warm-up
```

Warm-up se chay cung logic scan hien tai va ghi key vao `core_python/runtime/state.json` ma khong gui Telegram.
