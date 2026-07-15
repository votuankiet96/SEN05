# OG External Contracts

OG không còn phụ thuộc Python vào bất kỳ package nào khác trong SEN05. Nó
kết nối với phần còn lại của hệ thống qua **hạ tầng**, không qua import:
`og_past` đọc SQL Server để phục vụ dashboard/export lịch sử, còn `og_live`
đọc/publish Redis Streams để sinh tín hiệu realtime.

## 1. Đọc dữ liệu lịch sử — SQL Server trên DP6

`og_past/data/loader.py` + `og_past/data/db_connector.py` chỉ **đọc**
(SELECT) từ database `SEN05_AutoTrading` trên DP6. Schema kỳ vọng:

```text
DWH.Fact_OHLCV
  SymbolID     — khớp DWH.Dim_Symbol.SymbolID (xem og_core/config.py: SYMBOLS[...]["symbol_id"])
  TimeframeID  — khớp DWH.Dim_Timeframe.TimeframeID
  BarTime      — UTC-naive datetime, thời điểm MỞ của bar (không phải đóng)
  [Open], High, Low, [Close], Volume

DWH.Dim_Timeframe
  TimeframeID
  Code         — mã TF dạng chuỗi ("M5", "H1", "H3", ...), khớp
                 og_core/config.py: TF_MINUTES / TF_DISPLAY_ORDER

DWH.Dim_Symbol
  SymbolID     — khớp og_core/config.py: SYMBOLS[...]["symbol_id"]
```

OG không quan tâm dữ liệu này được nạp vào bằng cách nào (TradingView
WebSocket, staging, ETL...) — đó là việc của pipeline data-ingestion trên
DP6 (`data_provider/apps/*`, `modules/db_connector.py` bản đầy đủ), nằm
ngoài phạm vi repo này.

**Giả định OG dựa vào (nếu vi phạm sẽ tính sai chỉ báo/tín hiệu):**

- BarTime tăng dần, không trùng lặp cho cùng (SymbolID, TimeframeID).
- Bar cuối cùng trả về CÓ THỂ là bar đang mở (chưa đóng) — OG tự xử lý qua
  tham số bars/date-range, không có cột "is_closed" riêng.
- BarTime là UTC-naive nhất quán (không lẫn giờ địa phương).

## 2. Kết nối SQL Server

Đọc từ biến môi trường/`.env` (xem `.env.example`), mặc định khớp với cấu
hình production thật:

```text
SQL_SERVER, SQL_DRIVER, SQL_PORT, SQL_TDS_VERSION, SQL_UID, SQL_PWD,
SQL_ENCRYPT, SQL_TRUST_SERVER_CERT
```

`SQL_DATABASE` mặc định `"SEN05_AutoTrading"` và có thể override qua env nếu
cần kiểm thử môi trường khác.

## 3. Dữ liệu live — Redis state keys + event stream từ DP6

`og_live` không query SQL. DP6 ghi snapshot mới nhất vào Redis state key, rồi
publish một event nhỏ để báo OG key nào vừa được cập nhật sau khi
`live_fetching` commit nến đóng vào SQL.

State key:

```text
dp:candle_snapshot:latest:{tv_symbol}:{tf_code}
  schema_version, program, source, symbol_id, tv_symbol, tf_code,
  bars_count, latest_bar_time, generated_at_utc, snapshot_version,
  bars: [{bar_time, open, high, low, close, volume}, ...]
```

Event stream:

```text
dp:candle_snapshot:events
  event_type=snapshot_updated, tv_symbol, tf_code, bar_time, state_key,
  bars_count, published_at_utc, snapshot_version
```

OG Stream mechanism dùng event stream làm live trigger trong Redis db0, sau đó `GET state_key`
để lấy 500 nến mới nhất. State key tồn tại một mình chỉ dùng cho
warm-up/healthcheck; nó không được xem là trigger trading.

Stream mechanism publish signal lên Redis db1 theo route:

```text
og:stream:signals:{strategy}:{symbol}:{timeframe}
  signal_id, strategy, symbol, timeframe, direction, side, bar_time,
  event_close, entry_price, sl_price, tp_price, risk_reward, atr,
  signal_reason, produced_at, schema_version, asset_type, source_program,
  source_mechanism, source_stream, source_entry_id, source_state_key,
  source_snapshot_version, source_bar_time
```

Ví dụ:

```text
og:stream:signals:combo:HK50:H4
```

Pub/Sub mechanism subscribe channel `dp:pubsub:candle_snapshot:events`, dùng
`state_key` trong message để GET snapshot từ Redis db0, rồi publish signal
lên Redis db2 theo route:

```text
og:pubsub:signals:{strategy}:{symbol}:{timeframe}
```

Redis Pub/Sub channel không nằm trong db0/db1/db2; db2 chỉ là nơi lưu signal
do Pub/Sub mechanism tạo ra.

**Đã kiểm chứng thật 2026-07-07** (xem `deploy/README.md`): từ Linux
(`vm-og`), phải dùng **SQL Authentication** (`SQL_UID`/`SQL_PWD` thật) với
`SQL_DRIVER=freetds`. Để trống UID/PWD (Windows Integrated Auth) **không
hoạt động** trên máy Linux không join domain — FreeTDS rơi về GSSAPI/Kerberos
và lỗi thẳng (`gss_init_sec_context: GSS_S_FAILURE`), đã xác minh bằng
`TDSDUMP`. Suy đoán trước đó trong bản refactor ban đầu (dựa trên việc không
thấy `SQL_UID`/`SQL_PWD` trong biến môi trường của service thật đang chạy
trên VM này) là **sai** — service đó nhiều khả năng đọc UID/PWD qua một
`.env` riêng mà tài khoản này không có quyền xem, không phải Windows Auth
thật.

## 4. Giới hạn kiểm thử đã biết

Môi trường phát triển hiện tại (VM `vm-og`, xem
`/home/administrator/Desktop/og_program`) không có driver ODBC hay SQL
Server thật để kết nối — `get_connection()` sẽ luôn fail ở đây. Đây là giới
hạn môi trường, không phải lỗi code; đã xác minh `_build_conn_str()` và
route `/api/scan` fail sạch sẽ (JSON 500 có thông báo rõ ràng) thay vì
crash.
