# Triển khai tick_program sang máy mới

Thư mục `run_tick/` này là gói triển khai độc lập — copy nguyên cả thư mục
sang máy đích (không cần cài Python), rồi làm theo đúng thứ tự dưới đây.

## Bước 1 — Chạy installer

Mở PowerShell **với quyền Administrator**, vào đúng thư mục `run_tick/` đã
copy sang, rồi chạy:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Installer sẽ tự động:
- Kiểm tra/cài **ODBC Driver 18 for SQL Server** (qua `winget` nếu máy có;
  nếu không có `winget`, installer sẽ dừng lại và in link tải thủ công).
- Tạo `Config.yaml` từ mẫu `Config.example.yaml` nếu chưa có, rồi **dừng
  lại** để bạn điền thông tin thật.

## Bước 2 — Điền `Config.yaml`

Mở `Config.yaml` (installer vừa tạo từ `Config.example.yaml`), điền:
- `sql_server`: server, database, và thông tin đăng nhập SQL Server thật.
- `ctrader`: `client_id`/`client_secret` (OAuth app thật).
- `discord.webhook_url`: nếu muốn nhận cảnh báo (để trống nếu không cần).
- `symbols`: phải khớp đúng tên đang bật (`IsActive=1`) trong
  `DWH.Dim_Symbol` của SQL Server đang trỏ tới.

Sau khi điền `Config.yaml`, chạy các lệnh chẩn đoán để xác nhận trước khi
đăng ký chạy tự động:

```powershell
.\tick_program.exe show-config
.\tick_program.exe auth-check
```

Nếu cần đăng nhập OAuth lần đầu (`access_token`/`refresh_token` còn trống),
double-click `tick_program.exe` để mở menu, hoặc chạy `.\tick_program.exe
oauth-login --save-account-id <id>` từ dòng lệnh.

Xong thì chạy lại `install.ps1` — lần này nó sẽ thấy `Config.yaml` đã có
và đi tiếp đăng ký Scheduled Task.

## Bước 3 — Tạo schema SQL (chỉ khi SQL Server là MỚI hoàn toàn)

Nếu `Config.yaml` ở Bước 2 trỏ vào **cùng SQL Server đang chạy production
thật**, **bỏ qua bước này** — schema đã có sẵn rồi.

Nếu là SQL Server hoàn toàn mới, chưa từng chạy tick_program: file
`sql/tickdata_setup.sql` trong thư mục này là bản sao y hệt
`scripts/sql/tickdata_setup.sql` của repo chính — an toàn chạy lại nhiều
lần (không bao giờ xóa/ghi đè dữ liệu đã có). `tick_program.exe` không tự
chạy file `.sql` này (không đóng gói `sqlcmd`) — chạy nó bằng công cụ SQL
Server bất kỳ (SSMS, Azure Data Studio, hoặc `sqlcmd` nếu máy có), hoặc từ
máy có Python + repo chính qua `python scripts/deploy_schema.py`.

## Bước 4 — Đăng ký chạy tự động

`install.ps1` tự đăng ký **2 Scheduled Task** dưới `\SEN05\`:
- **SEN05 Tick Program Engine** — chạy `tick_program.exe` không tham số,
  `AtStartup`, tự restart tối đa 999 lần nếu crash.
- **SEN05 Tick Program Watchdog** — chạy `tick_program.exe --watchdog`
  mỗi 5 phút, kiểm tra heartbeat còn sống không, báo Discord nếu Engine
  chết mà không tự restart được (trường hợp thoát sạch với exit code khác
  0 — Task Scheduler không tự restart trường hợp này).

Mặc định **không tự chạy ngay** — chỉ kích hoạt ở lần khởi động máy tiếp
theo. Muốn chạy ngay:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -StartNow
```

Hoặc double-click thẳng `tick_program.exe` để mở menu vận hành (xem trạng
thái, chạy foreground để test, xem log, dừng an toàn, cài/gỡ Scheduled
Task) — không cần Administrator cho các thao tác xem/chạy thử, chỉ cần
cho phần cài đặt Scheduled Task.

## Lưu ý quan trọng

- **`tick_program.exe` đi kèm thư mục `_internal\` ngay cạnh nó — phải
  copy cả 2 cùng nhau** (không phải 1 file .exe độc lập như trước). Build
  dùng chế độ `--onedir` (không phải `--onefile`) — đã đổi sau khi phát
  hiện `--onefile` bị treo hoàn toàn (không log gì, "Responding" nhưng
  không chạy) khi khởi động qua Task Scheduler với logon S4U, dù chạy tay
  từ terminal vẫn luôn ổn định. `--onedir` không cần bước tự giải nén ra
  thư mục tạm mỗi lần chạy nên tránh được lớp lỗi này.
- **Không copy cả `run_tick/` này sang một máy đang chạy tick_program
  khác** (ví dụ máy build/dev) — `tick_program.exe` tự tạo `runtime/`
  riêng dựa trên vị trí của chính nó, không biết tới tiến trình khác đang
  chạy ở vị trí khác, có thể tạo ra 2 engine chạy song song không kiểm
  soát (`MultipleInstances IgnoreNew` trên Scheduled Task chỉ ngăn 2 lần
  chạy của CÙNG MỘT task, không ngăn 2 bản cài đặt khác thư mục).
- `tick_program.exe` nhận đầy đủ các lệnh CLI cũ (`check`, `backfill`,
  `symbol-sync`, `spool-status`, ...) — ví dụ `.\tick_program.exe check
  --json`. Không tham số + chạy nền (Task Scheduler) = chạy service;
  không tham số + terminal thật = mở menu.
- `tick_program.exe` là bản build tĩnh tại 1 thời điểm — sửa code trong
  repo chính sau này **không tự động cập nhật** file `.exe` này. Phải
  build lại (`scripts\windows\build_exe.ps1` từ repo chính) rồi copy
  `run_tick/` mới sang.
