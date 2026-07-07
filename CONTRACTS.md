# OG External Contracts

OG (`og_core`) không còn phụ thuộc Python vào bất kỳ package nào khác trong
SEN05 (xem Giai đoạn 2 của refactor). Nhưng nó vẫn kết nối với phần còn lại
của hệ thống qua **hạ tầng** (SQL Server), không phải qua import. Tài liệu
này ghi lại đúng những hợp đồng đó.

## 1. Đọc dữ liệu — SQL Server trên DP6

`og_core/data/loader.py` + `og_core/data/db_connector.py` chỉ **đọc**
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

`SQL_DATABASE` cố định `"SEN05_AutoTrading"` (không qua env, khớp bản gốc).

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

## 3. Những gì KHÔNG còn là hợp đồng của OG

Trước refactor, `core_python/notify/` từng bắn signal qua Redis Streams cho
một hệ downstream gọi là "OF", và nghe Redis pub/sub "bar_ready" từ pipeline
ws_live trên DP6. Toàn bộ phần đó đã được gỡ khỏi repo này (xem lịch sử git,
commit "remove notify subsystem"). Nếu notify được xây lại sau này, hợp đồng
Redis đó cần được thiết kế lại từ đầu — tài liệu này cố tình không mô tả một
hợp đồng đã không còn tồn tại trong code.

## 4. Giới hạn kiểm thử đã biết

Môi trường phát triển hiện tại (VM `vm-og`, xem
`/home/administrator/Desktop/og_program`) không có driver ODBC hay SQL
Server thật để kết nối — `get_connection()` sẽ luôn fail ở đây. Đây là giới
hạn môi trường, không phải lỗi code; đã xác minh `_build_conn_str()` và
route `/api/scan` fail sạch sẽ (JSON 500 có thông báo rõ ràng) thay vì
crash.
