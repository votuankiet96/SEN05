# Triển khai datafeed sang máy mới

Thư mục `run_df/` này là gói triển khai độc lập — copy nguyên cả thư mục
sang máy đích (không cần cài Python), rồi làm theo đúng thứ tự dưới đây.

## Bước 1 — Chạy installer

Mở PowerShell **với quyền Administrator**, vào đúng thư mục `run_df/` đã
copy sang, rồi chạy:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Installer sẽ tự động:
- Kiểm tra/cài **ODBC Driver 18 for SQL Server** (qua `winget` nếu máy có;
  nếu không có `winget`, installer sẽ dừng lại và in link tải thủ công).
- Kiểm tra/cài **sqlcmd** (tương tự, cần để chạy schema SQL ở Bước 3).
- Copy sẵn **Chromium** (đã đóng gói cùng thư mục `vendor/ms-playwright/`)
  vào đúng chỗ hệ thống cần — bước này luôn tự động, không cần mạng.
- Tạo `config.yaml` từ mẫu `config.example.yaml` nếu chưa có, rồi **dừng
  lại** để bạn điền thông tin thật.

Nếu `winget` không có sẵn trên máy, installer sẽ báo rõ link tải ODBC
Driver 18 / sqlcmd thủ công — tải về, cài xong rồi chạy lại `install.ps1`.

## Bước 2 — Điền `config.yaml`

Mở `config.yaml` (installer vừa tạo từ `config.example.yaml`), điền:
- `sql_server`: server, database, và thông tin đăng nhập SQL Server thật
  của môi trường này.
- `tradingview`: token/cookie phiên đăng nhập TradingView thật.
- `discord`: bật + webhook nếu muốn nhận cảnh báo.
- `live.symbols` / `live.timeframes`: phải khớp đúng tên đang bật
  (`IsActive=1`) trong `DWH.Dim_Symbol` / `DWH.Dim_Timeframe` của SQL
  Server đang trỏ tới.

Xong thì chạy lại `install.ps1` — lần này nó sẽ thấy `config.yaml` đã có
và đi tiếp.

## Bước 3 — Tạo schema SQL (chỉ khi SQL Server là MỚI hoàn toàn)

Nếu config.yaml ở Bước 2 trỏ vào **cùng SQL Server đang chạy production
thật**, **bỏ qua bước này** — schema đã có sẵn rồi.

Nếu là SQL Server hoàn toàn mới, chưa từng chạy datafeed, chạy:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -SetupSqlSchema -SqlServer "TEN_SERVER"
```

Không cần chỉ định tên database — `sql/01_setup_database.sql` tự tạo và
dùng đúng database `SEN05_AutoTrading` theo tên cố định. Toàn bộ script
SQL trong `sql/00_run_all.sql` (bản sao y hệt `scripts/sql/` của repo
chính) **an toàn chạy lại nhiều lần** — không bao giờ xóa/ghi đè dữ liệu
đã có, chỉ tạo những gì còn thiếu.

## Bước 4 — Đăng ký chạy tự động

`install.ps1` tự đăng ký 1 Scheduled Task (`SEN05 Datafeed Engine`) —
tự khởi động cùng máy, tự restart nếu crash. Mặc định **không tự chạy
ngay** — chỉ kích hoạt ở lần khởi động máy tiếp theo. Muốn chạy ngay:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -StartNow
```

Hoặc double-click thẳng `datafeed.exe` để chạy thủ công một lần (nó tự
đọc `live.enabled`/`backfill.enabled` trong `config.yaml` để quyết định
chạy live, backfill, hay cả hai).

## Lưu ý quan trọng

- **Không copy cả `run_df/` này sang một máy đang chạy datafeed khác**
  (ví dụ máy build/dev) — `datafeed.exe` tự tạo `runtime/` riêng dựa
  trên vị trí của chính nó, không biết tới tiến trình khác đang chạy ở
  vị trí khác, có thể tạo ra 2 engine chạy song song không kiểm soát.
- `datafeed.exe` có nhận mode qua tham số dòng lệnh (`--doctor-ops`,
  `--restart`, `--watchdog`, ...) — xem bảng đầy đủ trong
  `OPERATOR_RUNBOOK.md` của repo chính. Không double-click chạy lệnh
  chẩn đoán được (double-click luôn vào `--run`/menu); phải gọi từ
  PowerShell/terminal, ví dụ `.\datafeed.exe --doctor-ops`.
- `datafeed.exe` là bản build tĩnh tại 1 thời điểm — sửa code trong
  repo chính sau này **không tự động cập nhật** file `.exe` này. Phải
  build lại (`pyinstaller scripts/windows/datafeed_entry.py ...` từ
  repo chính) rồi copy `run_df/` mới sang.
