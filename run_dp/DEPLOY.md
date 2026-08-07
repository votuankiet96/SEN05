# Triển khai dp_program sang máy mới

Thư mục `run_dp/` này là gói triển khai độc lập — copy nguyên cả thư mục
sang máy đích (không cần cài Python), rồi làm theo đúng thứ tự dưới đây.

## Bước 1 — Chạy installer

Mở PowerShell **với quyền Administrator**, vào đúng thư mục `run_dp/` đã
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
- Tạo `Config.yaml` từ mẫu `Config.example.yaml` nếu chưa có, rồi **dừng
  lại** để bạn điền thông tin thật.

Nếu `winget` không có sẵn trên máy, installer sẽ báo rõ link tải ODBC
Driver 18 / sqlcmd thủ công — tải về, cài xong rồi chạy lại `install.ps1`.

## Bước 2 — Điền `Config.yaml`

Mở `Config.yaml` (installer vừa tạo từ `Config.example.yaml`), điền:
- `sql_server`: server, database, và thông tin đăng nhập SQL Server thật
  của môi trường này.
- `tradingview`: token/cookie phiên đăng nhập TradingView thật.
- `discord`: bật + webhook nếu muốn nhận cảnh báo.
- `live.symbols` / `live.timeframes`: phải khớp đúng tên đang bật
  (`IsActive=1`) trong `DWH.Dim_Symbol` / `DWH.Dim_Timeframe` của SQL
  Server đang trỏ tới.

Xong thì chạy lại `install.ps1` — lần này nó sẽ thấy `Config.yaml` đã có
và đi tiếp.

## Bước 3 — Tạo schema SQL (chỉ khi SQL Server là MỚI hoàn toàn)

Nếu Config.yaml ở Bước 2 trỏ vào **cùng SQL Server đang chạy production
thật**, **bỏ qua bước này** — schema đã có sẵn rồi.

Nếu là SQL Server hoàn toàn mới, chưa từng chạy dp_program, chạy:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -SetupSqlSchema -SqlServer "TEN_SERVER"
```

Không cần chỉ định tên database — `sql/01_setup_database.sql` tự tạo và
dùng đúng database `SEN05_AutoTrading` theo tên cố định. Toàn bộ script
SQL trong `sql/00_run_all.sql` (bản sao y hệt `scripts/sql/` của repo
chính) **an toàn chạy lại nhiều lần** — không bao giờ xóa/ghi đè dữ liệu
đã có, chỉ tạo những gì còn thiếu.

## Bước 4 — Đăng ký chạy tự động

`install.ps1` tự đăng ký 1 Scheduled Task (`SEN05 DP Program Engine`) —
tự khởi động cùng máy, tự restart nếu crash. Mặc định **không tự chạy
ngay** — chỉ kích hoạt ở lần khởi động máy tiếp theo. Muốn chạy ngay:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -StartNow
```

Hoặc double-click thẳng `dp_program.exe` để chạy thủ công một lần (nó tự
đọc `live.enabled`/`backfill.enabled` trong `Config.yaml` để quyết định
chạy live, backfill, hay cả hai).

## Lưu ý quan trọng

- **Không copy cả `run_dp/` này sang một máy đang chạy dp_program khác**
  (ví dụ máy build/dev) — `dp_program.exe` tự tạo `runtime/` riêng dựa
  trên vị trí của chính nó, không biết tới tiến trình khác đang chạy ở
  vị trí khác, có thể tạo ra 2 engine chạy song song không kiểm soát.
- `dp_program.exe` không nhận tham số dòng lệnh và không có lệnh chẩn
  đoán (`status`/`doctor`/`check-sql`) — muốn kiểm tra tình trạng, xem
  Discord (nếu đã bật cảnh báo) hoặc xem file log trong `runtime/logs/`.
- `dp_program.exe` là bản build tĩnh tại 1 thời điểm — sửa code trong
  repo chính sau này **không tự động cập nhật** file `.exe` này. Phải
  build lại (`pyinstaller scripts/windows/dp_program_entry.py ...` từ
  repo chính) rồi copy `run_dp/` mới sang.
