# core_python External Contracts

core_python không phụ thuộc Python vào bất kỳ package nào khác trong SEN05.
Nó kết nối với phần còn lại của hệ thống qua **hạ tầng**, không qua import:
đọc SQL Server (DP6) để phục vụ dashboard/export lịch sử.

Phạm vi hiện tại cố ý hẹp:

```text
SQL Server OHLCV -> core_python strategy calculation -> dashboard / CSV
```

Repo này không còn chứa Redis Stream/PubSub live engine, Order Follower,
order execution, Discord alerting hoặc cơ chế publish realtime.

## Đọc dữ liệu lịch sử — SQL Server trên DP6

`core_python/data/loader.py` + `core_python/data/db_connector.py`
chỉ **đọc** (SELECT) từ database `SEN05_AutoTrading` trên DP6. Schema kỳ
vọng:

```text
DWH.Fact_OHLCV
  SymbolID     — khớp DWH.Dim_Symbol.SymbolID (xem core_python/util/config.py: SYMBOLS[...]["symbol_id"])
  TimeframeID  — khớp DWH.Dim_Timeframe.TimeframeID
  BarTime      — UTC-naive datetime, thời điểm MỞ của bar (không phải đóng)
  [Open], High, Low, [Close], Volume

DWH.Dim_Timeframe
  TimeframeID
  Code         — mã TF dạng chuỗi ("M5", "H1", "H3", ...), khớp
                 core_python/util/config.py: TF_MINUTES / TF_DISPLAY_ORDER

DWH.Dim_Symbol
  SymbolID     — khớp core_python/util/config.py: SYMBOLS[...]["symbol_id"]
```

core_python không quan tâm dữ liệu này được nạp vào bằng cách nào
(TradingView WebSocket, staging, ETL...) — đó là việc của pipeline
data-ingestion trên DP6, nằm ngoài phạm vi repo này.

**Giả định core_python dựa vào (nếu vi phạm sẽ tính sai chỉ báo/tín hiệu):**

- BarTime tăng dần, không trùng lặp cho cùng (SymbolID, TimeframeID).
- Bar cuối cùng trả về CÓ THỂ là bar đang mở (chưa đóng) — core_python tự xử
  lý qua tham số bars/date-range, không có cột "is_closed" riêng.
- BarTime là UTC-naive nhất quán (không lẫn giờ địa phương).

## Kết nối SQL Server

Đọc từ biến môi trường/`.env` (xem `.env.example`), mặc định khớp với cấu
hình production thật:

```text
SQL_SERVER, SQL_DRIVER, SQL_PORT, SQL_TDS_VERSION, SQL_UID, SQL_PWD,
SQL_ENCRYPT, SQL_TRUST_SERVER_CERT
```

`SQL_DATABASE` mặc định `"SEN05_AutoTrading"` và có thể override qua env nếu
cần kiểm thử môi trường khác.

**Đã kiểm chứng thật 2026-07-07** (xem `deploy/README.md`): từ Linux
(`vm-og`), phải dùng **SQL Authentication** (`SQL_UID`/`SQL_PWD` thật) với
`SQL_DRIVER=freetds`. Để trống UID/PWD (Windows Integrated Auth) **không
hoạt động** trên máy Linux không join domain — FreeTDS rơi về GSSAPI/Kerberos
và lỗi thẳng (`gss_init_sec_context: GSS_S_FAILURE`), đã xác minh bằng
`TDSDUMP`.

## Giới hạn kiểm thử đã biết

Trên VM `vm-og`, kết nối SQL Server thật đã được smoke test bằng dashboard API
và CLI. Những máy checkout khác vẫn cần `.env` hợp lệ, ODBC/FreeTDS đúng phiên
bản và quyền `SELECT` trên các bảng DWH phía DP6.

`/health` chỉ là liveness check của process Flask/gunicorn; endpoint này không
query SQL. Muốn kiểm tra đầy đủ đường DB + strategy, dùng:

```bash
./.venv/bin/python -m core_python.util.ops smoke --base-url http://127.0.0.1:8516
```

Các API dashboard trả `400` cho lỗi input có thể dự đoán được như strategy,
symbol hoặc timeframe không hợp lệ. Lỗi hạ tầng ngoài dự kiến vẫn trả JSON
`500` kèm message để debug nội bộ khi dashboard chỉ bind `127.0.0.1`.
