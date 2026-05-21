# core_python - Dashboard va Telegram Signal Runtime

Tai lieu nay mo ta hanh vi hien tai cua `core_python` theo code trong thu muc nay. Day la lop tinh tin hieu, hien thi dashboard va day canh bao Telegram cua SEN05.

## 1. Muc Dich

`core_python` co hai luong chinh:

| Luong | Muc dich | File chinh |
|---|---|---|
| Dashboard | Xem chart, indicator va signal lich su theo tham so nguoi dung chon | `main.py`, `chart/`, `strategies/`, `indicators/` |
| Telegram watcher | Chay nen 24/7, quet nen da dong va gui signal moi len Telegram | `notify/signal_watcher.py`, `notify/scan_config.py`, `notify/state.py` |

`core_python` khong dat lenh that, khong quan ly von, khong quan ly vi the va khong tinh PnL live. Telegram alert hien tai la canh bao tin hieu, khong phai execution engine.

## 2. Luong Du Lieu Tong The

```text
SQL Server DWH.Fact_OHLCV
  -> core_python/data/loader.py
      -> DataFrame OHLCV: bartime, open, high, low, close, volume
  -> core_python/strategies/<strategy>/
      -> normalize_params()
      -> add_indicators()
      -> detect_signals()
      -> add_levels()
  -> hai noi dung khac nhau:
      1. chart/server.py tra JSON ve dashboard
      2. notify/signal_watcher.py gui Telegram neu signal moi
```

Dashboard va Telegram watcher dung chung data loader, indicator va strategy logic. Diem khac biet quan trong:

```text
Dashboard:
  - Doc theo request cua trinh duyet.
  - Co the hien thi ca bar dang mo.
  - Co the hien thi signal lich su theo so bars dang load.
  - Khong dung state.json va khong quyet dinh Telegram co gui hay khong.

Telegram watcher:
  - Chay nen 24/7.
  - Luon loc bo bar dang mo voi closed_only=True.
  - Chi gui signal moi chua co trong state.
  - Ghi key vao core_python/runtime/state.json sau khi gui thanh cong.
```

## 3. Cau Truc Thu Muc

```text
core_python/
  config.py
    Nap metadata symbol, timeframe va SQL config tu config.py goc.

  data/
    loader.py
      Tai OHLCV tu SQL Server, tra DataFrame tang dan theo bartime.

  indicators/
    core.py
      SMA, EMA, MACD histogram, ATR, safe_ratio.
    ai_trend.py
      AI Trend Navigator / KNN indicator.
    dow_wave.py
      Tinh swing/pivot Dow wave dung cho hien thi AI Trend.

  strategies/
    registry.py
      Dang ky StrategySpec cho combo, ma_cross, ai_trend, knn_combo.

    combo/
      config.py
        Tham so Combo, symbol-specific X/session, validation.
      signals.py
        Tinh MA, MACD, ATR va detect BUY/SELL.
      levels.py
        Tinh entry/SL/TP theo breakout cua bar signal.

    ma_cross/
      config.py
      signals.py
      levels.py
        Strategy dang duoc dang ky cho dashboard/generic pipeline,
        nhung khong nam trong production SCAN_GROUPS hien tai.

    ai_trend/
      config.py
        Tham so dashboard/strategy: Trend TF, Entry TF, KNN, EMA, Dow wave.
      signals.py
        Build H3/Entry frames, merge trend da dong vao entry, detect M45 entry.
      alerts.py
        Tao alert H3 trend-change va M45 entry, format Telegram AI Trend.
      payload.py
        Tao JSON payload rieng cho dashboard hai chart.
      levels.py
        Giu contract level columns; AI Trend phase hien tai khong tinh entry/SL/TP.
      ARCHITECTURE.md
        Tai lieu chi tiet rule AI Trend.

    knn_combo/
      config.py
        Tham so dashboard/strategy: Trend TF, Entry TF, KNN, Combo raw signal.
      signals.py
        Build HTF KNN trend da dong, merge vao entry TF, loc Combo-style signals.
      levels.py
        Giu level columns rong; strategy nay chi canh bao tin hieu visual.
      payload.py
        Tao JSON payload hai chart cho dashboard.
      ARCHITECTURE.md
        Tai lieu chi tiet rule KNN Combo.

  chart/
    server.py
      Flask API: /, /api/config, /api/scan, /api/export.
    payload.py
      Payload chart chung cho combo/ma_cross.
    static/
      HTML, CSS, JS dashboard.

  notify/
    scan_config.py
      Cau hinh production scan groups cho combo va AI Trend.
    signal_watcher.py
      Scheduler 24/7, canh bar close, goi strategy, gui Telegram.
    state.py
      Dedup key da gui, TTL 60 ngay, warm-up sentinel, migration legacy state.
    notifier.py
      Telegram/Discord/dry-run backend.
    formatter.py
      Format Telegram HTML cho signal chung nhu Combo.

  export/
    to_csv.py
      Xuat signal ra CSV khi watcher gui signal, neu khong bi tat bang --no-export.

  runtime/
    state.json
      File runtime local chong gui trung. Khong commit.
```

## 4. Production Watcher Hien Tai

Production watcher doc `SCAN_GROUPS` trong `core_python/notify/scan_config.py`.

### AI Trend

AI Trend dang scan 11 symbol:

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

AI Trend co 2 group production:

```text
H3  -> h3_trend_change, TREND_BARS=400
M45 -> m45_entry_signal, TREND_BARS=400, ENTRY_BARS=1000
```

### Combo

Combo dang scan 9 symbol indice:

```text
FR40
DE40
HK50
J225
SP35
UK100
US500
US100
US30
```

Combo co 4 group production:

```text
H1 -> 500 bars
H2 -> 500 bars
H3 -> 400 bars
H4 -> 300 bars
```

### KNN Combo

`knn_combo` da duoc dang ky cho dashboard/export va co duong chay watcher generic,
nhung khong nam trong `SCAN_GROUPS` production mac dinh. Strategy nay chi tao
tin hieu visual: HTF KNN trend gate + entry TF Combo-style raw signal, khong tinh
entry/SL/TP/position sizing.

## 5. Combo Signal Logic

Combo dung pipeline chung cua `StrategySpec`.

```text
load(symbol, tf, bars)
  -> _drop_open_bar()
  -> add_combo_indicators()
      ma, macd_h, atr, prev_close, prev_ma
  -> detect_combo_signals()
      BUY khi candle bullish, close > MA20, macd_h > 0 va state truoc khong phai BUY
      SELL khi candle bearish, close < MA20, macd_h < 0 va state truoc khong phai SELL
      Raw Combo V0 signal luan phien BUY -> SELL -> BUY.
      X, ATR, KTP va MIN_RR khong phai dieu kien tao raw signal.
  -> add_combo_levels()
      BUY entry = high + X, SL = low - X, TP = entry + KTP * ATR
      SELL entry = low - X, SL = high + X, TP = entry - KTP * ATR
```

Telegram key cua Combo:

```text
combo|SYMBOL|TF|bartime|direction
```

### KNN Combo Visual Variant

KNN Combo dung pipeline hai timeframe:

```text
load(symbol, TREND_TF, TREND_BARS)
  -> prepare_trend_frame()
      ai_knn, ai_avg, trend_bias, trend_close_time
load(symbol, ENTRY_TF, ENTRY_BARS)
  -> prepare_entry_frame()
      ma, macd_h, atr, prev_close, prev_ma
  -> merge_trend_into_entry()
      chi dung trend bar da dong: trend_close_time <= entry bartime
  -> detect_knn_combo_signals()
      raw BUY/SELL theo Combo-style MA/MACD/candle
      allow BUY khi KNN bullish, allow SELL khi KNN bearish
      neutral hoac missing closed trend bi block mac dinh
  -> add_knn_combo_levels()
      level columns rong vi chua co trade model
```

## 6. AI Trend Signal Logic

AI Trend la chien luoc multi-timeframe.

```text
Trend frame:
  Trend TF mac dinh dashboard: H3
  Production notify: H3
  Indicator: AI Trend Navigator / KNN
  Bias: 1 bullish, -1 bearish, 0 neutral

Entry frame:
  Entry TF mac dinh dashboard: M45
  Production notify: M45
  Indicator: EMA 13 va EMA 34
```

### H3 Trend Alert

```text
load(symbol, H3, 400)
  -> drop open bar
  -> prepare_trend_frame()
  -> extract_h3_trend_alerts()
      gui khi h3_bias doi sang bullish hoac bearish
```

### M45 Entry Alert

```text
load(symbol, H3, 400)
load(symbol, M45, 1000)
  -> drop open bar cho ca hai
  -> prepare_trend_frame()
  -> prepare_entry_frame()
  -> merge_trend_into_entry()
      chi dung H3 da dong:
      h3_close_time <= M45 bartime
  -> detect_ai_trend_signals()
      moi H3 bias segment chi lay entry dau tien:
        BUY neu H3 bullish va EMA13 > EMA34
        SELL neu H3 bearish va EMA13 < EMA34
```

AI Trend khong cho M45 dung cay entry nam trong H3 chua xac nhan. Trend H3 phai dong truoc, M45 signal moi duoc xet sau do.

Telegram key cua AI Trend:

```text
ai_trend|kind|SYMBOL|TF|event_time|direction
```

Vi du:

```text
ai_trend|h3_trend_change|BTCUSD|H3|2026-05-06 12:00:00|1
ai_trend|m45_entry_signal|BTCUSD|M45|2026-05-06 14:15:00|-1
```

## 7. State, Warm-Up Va Dedup Telegram

State file production:

```text
core_python/runtime/state.json
```

Watcher chi ghi key vao state sau khi Telegram/Discord gui thanh cong va khong phai dry-run.

Warm-up:

```powershell
.venv\Scripts\python.exe -m core_python.notify.signal_watcher --warm-up
```

Warm-up chay cung logic scan hien tai, nhung khong gui Telegram. No danh dau cac signal lich su trong cua so scan hien tai la da thay. Muc dich la tranh flood Telegram khi khoi dong lan dau hoac khi them symbol/TF moi.

Luu y van hanh:

```text
Neu them symbol, TF, bars hoac strategy group moi:
  -> phai dung watcher
  -> chay warm-up
  -> khoi dong lai watcher
```

## 8. Realtime Semantics

Watcher la realtime theo bar close, khong phai tick-by-tick.

```text
bar close time
  -> cho buffer 5 giay
  -> load DB
  -> neu DB chua co bar moi, retry moi 5 giay trong 10 giay
  -> neu co signal moi, gui Telegram ngay trong vong scan
```

Do tre thuc te phu thuoc vao:

```text
1. Data provider ghi nen moi vao SQL Server nhanh hay cham.
2. SQL Server tra data thanh cong.
3. Telegram API gui thanh cong.
```

## 9. Hourly Telegram Summary

Watcher co summary moi `--health-interval-minutes`, mac dinh 60 phut.

Summary gui Telegram theo symbol, dua tren cac signal da gui that trong runtime hien tai:

```text
SEN05 Hourly Signal Summary

Window: <last 60m>
Signals: N

By symbol
GOLD: AI Trend M45 BUY ...
US30: Combo H1 SELL ...
BTCUSD: no new signal
```

Summary nay la in-memory. Neu watcher restart giua gio, summary chi tinh signal tu sau lan restart. Dedup signal that van nam trong `state.json`.

## 10. Dashboard

Chay dashboard:

```powershell
.venv\Scripts\python.exe -m core_python.main
```

Mac dinh:

```text
http://127.0.0.1:8516
```

Neu port khac duoc truyen qua CLI, vi du:

```powershell
.venv\Scripts\python.exe -m core_python.main --port 8517
```

Dashboard co the chon strategy, symbol, TF, bars va params. Voi AI Trend, dashboard dung path rieng de tai ca Trend TF va Entry TF cung luc.

## 11. Chay Watcher

Dry-run mot vong:

```powershell
.venv\Scripts\python.exe -m core_python.notify.signal_watcher --dry-run --once
```

Warm-up:

```powershell
.venv\Scripts\python.exe -m core_python.notify.signal_watcher --warm-up
```

Production:

```powershell
.venv\Scripts\python.exe -m core_python.notify.signal_watcher --log-file logs\watcher.log
```

Hoac dung repo-level:

```powershell
run_watcher.bat
```

Can `.env` co:

```text
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>
COMBO_DISCORD_WEBHOOK_URL=<discord_webhook_for_combo_raw_signals>
```

## 12. Gia Dinh Va Gioi Han Da Biet

| Hang muc | Hanh vi hien tai |
|---|---|
| BarTime | DB tra `bartime` UTC-naive. Code localize ve UTC khi can so sanh timezone-aware. |
| Bar dang mo | Dashboard co the hien thi. Watcher loc bo bang `_drop_open_bar()`. |
| State TTL | 60 ngay. TTL phai lon hon cua so bars dai nhat de tranh re-alert. |
| Data gap | `loader.py` loc NaN/duplicate nhung khong phat hien gap thoi gian. |
| DB retry | Loader mo ket noi theo tung request; retry/backoff DB khong nam trong `loader.py`. |
| Execution | Khong co order placement, position sizing, kill switch hay risk limit live. |
| Summary | Hourly summary la in-memory, khong hoi cuu tu log sau restart. |
| Single process | Watcher quet tuan tu. Symbol cham co the lam tre cac group sau. |
