# AI Trend Strategy Architecture

Tai lieu nay mo ta dung hanh vi hien tai cua chien luoc `ai_trend` theo code trong `core_python/strategies/ai_trend/`.

> Ghi chu: ban truoc cua tai lieu nay co mo ta them luong notify production
> (Telegram alert format, state dedup key, warm-up). Phan notify da duoc go
> tam khoi core_python (xem lich su git) va se duoc thiet ke lai rieng khi
> phat trien tiep. Tai lieu nay chi con giu phan logic tin hieu con song.

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

## 2. File Ownership

```text
strategies/ai_trend/config.py
  Tham so strategy va dashboard: Trend TF, Entry TF, bars, KNN, EMA, Dow wave,
  MACD va level defaults.

strategies/ai_trend/signals.py
  Build trend frame va entry frame.
  Merge H3 da dong vao M45.
  Detect M45 signal dau tien trong moi trend segment voi EMA va MACD confirmation.

strategies/ai_trend/payload.py
  Tao JSON payload hai chart cho dashboard.

strategies/ai_trend/levels.py
  Tinh entry_price, sl_price, tp_price, risk_reward cho AI Trend.
  Entry = market order tren close cua cay Entry TF co signal.
  SL = swing Dow gan nhat da xac nhan.
  TP = fixed R:R mac dinh 1.5.
```

## 3. Trend Frame

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

## 4. Entry Frame

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

## 5. No-Lookahead Merge

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

## 6. Signal Rule

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
