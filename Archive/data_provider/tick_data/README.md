# cTrader FTMO Tick Data Provider

Tài liệu này mô tả trạng thái hiện tại của hệ thống `tick_data` trong SEN05.
Hệ thống này lấy tick BID/ASK từ cTrader/FTMO, lưu vào SQL schema riêng `tick`,
và vận hành độc lập với `ws_live`, checker OHLCV, data pipeline nến, TradingView
token và các luồng strategy hiện có.

## Mục Tiêu

- Lấy live tick 24/7 qua cTrader Open API cho bộ symbol SEN05.
- Lấy historical tick để backfill lần đầu, repair gần realtime và daily overlap.
- Giữ dữ liệu tick liền mạch bằng mô hình nhiều lớp: live feed, short overlap
  repair, daily overlap backfill, checker, watchdog.
- Ghi raw tick BID/ASK vào các bảng `tick.<SYMBOL>` trên SQL Server.
- Cho phép chạy lại backfill/overlap an toàn nhờ idempotency hash và unique
  index `EventHash`.
- Có log, dashboard local và thông báo Discord cho sự kiện vận hành quan trọng.

## Symbol Universe

Hệ thống hiện chạy 11 symbol:

```text
FR40, DE40, HK50, J225, SP35, UK100, US500, US100, US30, GOLD, BTCUSD
```

Tên broker/cTrader thật không được hard-code trong code live. `symbol-sync` lấy
danh sách symbol từ account cTrader, match theo alias/score, rồi ghi mapping vào
`tick.SymbolMap`. Symbol chỉ được ingest khi mapping ở trạng thái hợp lệ.

## Kiến Trúc Hiện Tại

### 1. Live ingest 24/7

Command chính:

```powershell
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick live
```

Live ingest mở kết nối cTrader, authenticate bằng OAuth token, subscribe spot
events cho các symbol đã match, tách BID/ASK thành tick rows và flush vào SQL.

Live ingest là feed chính, nhưng không phải lớp duy nhất đảm bảo dữ liệu. Nếu
cTrader disconnect, process restart, SQL tạm lỗi, token refresh lỗi tạm thời hoặc
máy bị restart, các lớp repair phía dưới sẽ vá lại cửa sổ gần đây.

### 2. Short overlap repair

Script:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_short_overlap_repair.ps1
```

Mặc định script chạy cửa sổ gần đây:

- `LookbackMinutes = 15`
- `SafetyLagMinutes = 2`
- `BucketMinutes = 1`
- `RequestTimeoutSeconds = 120`
- `RetryCount = 1`

Nghĩa là mỗi lần chạy, nó backfill lại khoảng từ `now - 15m` đến `now - 2m`.
Nếu live đã ghi tick rồi, duplicate bị chặn bởi `EventHash`. Nếu live bị hụt một
đoạn ngắn, historical backfill sẽ bổ sung tick mới vào bảng.

Scheduled Task mặc định:

```text
\SEN05\SEN05_TickData_ShortRepair
```

Task này được `install_tick_tasks.ps1` cài chạy mỗi 5 phút. Nó gọi daily overlap
script ở chế độ `-SingleSession -SkipSymbolSync -SkipActivityProfile -NoNotify`,
để dùng một session cTrader cho cả vòng repair và chỉ gửi báo cáo tổng hợp.

### 3. Daily overlap backfill

Script:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_daily_overlap_backfill.ps1
```

Mặc định:

- `LookbackHours = 48`
- `ChunkHours = 6`
- `SafetyLagMinutes = 5`
- `RequestTimeoutSeconds = 180`
- `RetryCount = 3`
- Full 11 symbol nếu không truyền `-Symbols`

Cơ chế này nên chạy một lần mỗi ngày, ví dụ `09:06 UTC` như cấu hình vận hành
DP6 hiện tại. Nó kéo lại 48 giờ gần nhất để vá các khoảng live ingest bị ngắt
lâu hơn short repair, đồng thời tạo báo cáo trước/sau:

- `historical_added`: số tick historical mới được bổ sung.
- `bid_added` / `ask_added`: số tick mới theo side BID/ASK.
- `new_Nm_buckets`: số bucket thời gian trước đó trống, sau overlap có tick.

Report được ghi vào:

```text
ops\run_tickdata\runtime\reports\tick_overlap_<run>_report.json
ops\run_tickdata\runtime\logs\tick_daily_overlap_report.jsonl
```

### 4. Checker market-aware

Command:

```powershell
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick check
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick check --json
```

Checker đọc SQL và spool theo kiểu read-only. Nó kiểm tra mapping, heartbeat,
spool backlog, pid file và trạng thái live runtime.

Để tránh false positive khi thị trường nghỉ, checker dùng activity profile học từ
dữ liệu tick lịch sử:

```powershell
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick build-activity-profile `
  --lookback-days 12 `
  --bucket-minutes 15
```

Profile nằm tại:

```text
data_provider\runtime\cache\tick_activity_profile.json
```

Khi một symbol đang expected-quiet hoặc đang trong transition grace, checker sẽ
không báo stale heartbeat như lỗi live feed. Nếu profile thiếu hoặc stale,
checker vẫn chạy được nhưng sẽ quay về fallback thận trọng hơn.

### 5. Watchdog và graceful restart

Scheduled Task:

```text
\SEN05\SEN05_TickLive_Watchdog
```

Watchdog chạy mỗi 60 giây. Nó có hai nhiệm vụ:

- Nếu không thấy live process trong 60 giây, restart supervisor task.
- Mỗi 300 giây chạy tick checker. Nếu checker thấy expected-active symbols bị
  stale liên tục quá 900 giây, watchdog ghi graceful shutdown signal vào runtime
  lock. Live process flush dữ liệu, thoát sạch, rồi supervisor mở live process mới.

Điểm quan trọng: hệ thống không reconnect cưỡng bức mỗi phút. Nó giữ live stream
khi khỏe, reconnect khi cTrader disconnect, và chỉ restart mềm khi feed bị stale
thật sự trong lúc thị trường được kỳ vọng active.

### 6. Supervisor 24/7

Scheduled Task:

```text
\SEN05\SEN05_TickLive_Supervisor
```

Supervisor chạy `tick live` nền, restart sau khi child process thoát, và được
cài `MultipleInstances = IgnoreNew` để tránh mở chồng nhiều supervisor. Live app
cũng có runtime lock `tick_live_runtime` và pid file để instance mới yêu cầu
instance cũ shutdown gracefully trước khi takeover.

## SQL Contract

Migration chính:

```text
data_provider\sql\07_ctrader_ftmo_tick.sql
```

Các bảng/view quan trọng:

- `tick.AccountProfile`
- `tick.SymbolMap`
- `tick.IngestRun`
- `tick.IngestState`
- `tick.<SYMBOL>` cho 11 symbol
- `tick.v_SymbolMap`
- `tick.v_IngestHealth`

Các trạng thái `tick.IngestRun.Status`:

- `RUNNING`: run đang mở.
- `STOPPED`: live/smoke dừng có kiểm soát.
- `FAILED`: run lỗi.
- `DONE`: historical backfill hoàn tất.

Mỗi tick table có unique index trên `EventHash` với `IGNORE_DUP_KEY = ON`. Vì vậy
overlap backfill có thể chạy lặp lại cùng một cửa sổ mà không nhân đôi tick.

## Runtime Files

```text
data_provider\runtime\cache\ctrader_ftmo_oauth.json
data_provider\runtime\cache\tick_activity_profile.json
data_provider\runtime\spool\ctrader_ftmo_tick_spool.db
data_provider\runtime\logs\ctrader_ftmo_tick.log
data_provider\runtime\run\tick_live_runtime.pid
```

Ops runtime:

```text
ops\run_tickdata\runtime\logs\
ops\run_tickdata\runtime\reports\
ops\run_tickdata\runtime\tick_live_supervisor.heartbeat.json
ops\run_tickdata\runtime\tick_live_stale_feed_since.txt
ops\run_tickdata\runtime\tick_live_stale_check_after.txt
```

Token cache, spool, logs và reports là runtime data, không nên commit.

## CLI Commands

Các command operator thường dùng:

```powershell
Set-Location C:\Share\SEN05_Autotrading

.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick show-config
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick token-status
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick auth-url
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick oauth-login
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick account-list
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick symbol-sync
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick symbol-sync --apply
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick live --smoke-seconds 300
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick live
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick check
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick check --json
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick spool-status
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick history-depth --json --max-days 20000
```

Manual historical backfill:

```powershell
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick backfill `
  --from "2026-06-01T00:00:00Z" `
  --to "2026-06-02T00:00:00Z" `
  --symbols US30 GOLD BTCUSD `
  --request-timeout 180
```

Ops scripts có thể dùng `backfill --no-notify` để tắt Discord từng child job và
chỉ gửi báo cáo tổng hợp ở parent script.

## Scheduled Tasks Trên DP6

Các task tickdata hiện dùng trong `\SEN05\`:

```text
SEN05_TickLive_Supervisor   live ingest nền 24/7
SEN05_TickLive_Watchdog     kiểm tra process/feed, restart mềm khi cần
SEN05_TickData_Checker      health check định kỳ và Discord warning/error
SEN05_TickData_ShortRepair  short overlap repair mỗi 5 phút
SEN05_TickData_DailyOverlap daily 48h overlap, nếu đã đăng ký riêng
```

Cài/reinstall nhóm task chính:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\install_tick_tasks.ps1 -Force
```

Cài để chạy trước khi user login:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\install_tick_tasks.ps1 `
  -Force `
  -RunWhetherLoggedOnOrNot `
  -TaskUser "WIN-B8609EA108T\Administrator"
```

Kiểm tra trạng thái:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_status.ps1
```

## Discord Notifications

Live ingest gửi Discord cho các event quan trọng:

- Live ingest started.
- cTrader subscription active.
- Disconnect/reconnect scheduled.
- Ingest error.
- SQL write failed và tick đã được đưa vào SQLite spool.
- Live ingest stopped.
- Báo cáo dữ liệu định kỳ theo `CTRADER_FTMO_TICK_DISCORD_REPORT_SECONDS`,
  mặc định 3600 giây.

Checker:

- `check --notify` gửi Discord khi status không OK.
- Warning được throttle 3600 giây để tránh spam khi thị trường đang nghỉ hoặc lỗi
  lặp lại.

Short overlap repair:

- Gửi summary throttled, mặc định mỗi 60 phút.
- Gửi error alert throttled nếu repair job fail.
- Không gửi notification cho từng child backfill.

Daily overlap:

- Gửi thông báo start.
- Gửi thông báo fail nếu có lỗi.
- Gửi thông báo done kèm `historical_added`, số bucket mới và đường dẫn report.

## Log

App log chính:

```text
data_provider\runtime\logs\ctrader_ftmo_tick.log
```

Log này có:

- Live startup/auth/subscription.
- Live heartbeat compact mỗi `CTRADER_FTMO_TICK_HEARTBEAT_LOG_SECONDS`, mặc định
  300 giây.
- Historical request start/done/error.
- Backfill start/done/fail.
- Spool write/drain.
- Reconnect/disconnect.

Ops logs:

```text
ops\run_tickdata\runtime\logs\tick_live_supervisor.log
ops\run_tickdata\runtime\logs\tick_live_watchdog.log
ops\run_tickdata\runtime\logs\tick_check.log
ops\run_tickdata\runtime\logs\tick_short_overlap_repair.log
ops\run_tickdata\runtime\logs\tick_daily_overlap_backfill.log
ops\run_tickdata\runtime\logs\tick_dashboard.log
```

Mở tail log:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_log_viewer.ps1
```

## Dashboard

Local dashboard:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_dashboard.ps1 -Port 8061
```

Mặc định bind `127.0.0.1`. Nếu cần mở từ host/LAN phải bind `0.0.0.0`, mở firewall
port tương ứng trên DP6 và dùng IP DP6:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_dashboard.ps1 `
  -BindHost 0.0.0.0 `
  -OpenHost 10.11.12.6 `
  -Port 8061
```

## Cơ Chế Đảm Bảo Liền Mạch

Tick data không dùng giả định "mỗi phút phải có N tick", vì tick là dữ liệu không
đều và phụ thuộc symbol/session/market hours. Cơ chế an toàn hơn là overlap
repair:

1. Live ingest ghi tick gần realtime khi kết nối khỏe.
2. Short overlap mỗi 5 phút kéo lại 15 phút gần nhất, trễ 2 phút để tránh vùng dữ
   liệu còn đang về.
3. Daily overlap mỗi ngày kéo lại 48 giờ gần nhất để vá các lỗ dài hơn.
4. Duplicate tự bị chặn bởi `EventHash`, nên kéo lại cùng cửa sổ không làm phình
   dữ liệu giả.
5. Checker dùng activity profile để phân biệt "feed stale khi market expected
   active" với "market expected quiet".
6. Watchdog chỉ restart mềm live feed khi stale kéo dài trong expected-active
   window.

Nếu live ngắt trong vài phút, short repair thường đủ vá. Nếu ngắt lâu hơn hoặc
máy/SQL/cTrader có sự cố dài, daily overlap 48h sẽ tiếp tục bổ sung tick bị thiếu.
Backfill dài chỉ dùng cho bootstrap lần đầu, audit dữ liệu sâu hoặc khi phát hiện
khoảng thiếu vượt quá daily window.

## Xử Lý Sự Cố Nhanh

Kiểm tra trạng thái tổng quát:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_status.ps1
```

Nếu Discord báo live error/disconnect:

- Xem `data_provider\runtime\logs\ctrader_ftmo_tick.log`.
- Xem `tick_live_watchdog.log` để biết watchdog có restart mềm không.
- Chạy `check --json` để xem symbol nào stale và có expected-active không.
- Short repair sẽ tiếp tục vá dữ liệu gần nhất nếu historical API vẫn trả được.

Nếu Discord báo spool:

- Kiểm tra SQL Server/ODBC/network.
- Không xóa spool file.
- Khi SQL phục hồi, live flush loop sẽ drain spool lại.

Nếu checker warning cuối tuần/ngoài giờ:

- Build lại activity profile nếu profile thiếu/stale.
- Nếu symbol expected-quiet thì warning stale heartbeat không nên xuất hiện.

Nếu cần repair thủ công nhanh:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_short_overlap_repair.ps1 `
  -LookbackMinutes 30 `
  -SafetyLagMinutes 2
```

Nếu cần repair sâu hơn:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_daily_overlap_backfill.ps1 `
  -LookbackHours 72 `
  -ChunkHours 6 `
  -SafetyLagMinutes 5
```

## Mốc Vận Hành Gần Nhất

Tính đến đợt audit/triển khai ngày 2026-06-12:

- Test suite `tests\test_ctrader_ftmo_tickdata.py` chạy pass `24/24`.
- Live feed đã phục hồi về `active_symbols=11/11`.
- Short overlap repair đã bổ sung được tick historical mới trong các lần chạy
  recovery, chứng minh cơ chế overlap có thể vá đoạn hụt live.
- Checker trả `status=OK` khi activity profile available và feed healthy.

Các số liệu này là snapshot vận hành, không phải cam kết trạng thái hiện tại. Luôn
kiểm tra lại bằng `tick_status.ps1` và Discord/log khi audit production.

## File Map

- `auth.py`: OAuth URL, local OAuth callback, exchange code, refresh token.
- `token_store.py`: cache token local và status redacted.
- `runtime.py`: runtime settings, endpoint demo/live, token TTL, cTrader SDK wrapper.
- `runtime_lock.py`: runtime lock, pid file, graceful shutdown signal.
- `symbols.py`: symbol models và match symbol SEN05 với symbol account cTrader.
- `ticks.py`: tick model, price scaling, idempotency hash, historical windows,
  delta decode.
- `spool.py`: SQLite spool, batch flush SQL, drain spool.
- `store_sql.py`: ghi SQL Server schema `tick` và cập nhật ingest state.
- `checker.py`: read-only health checks và learned activity profile.
- `dashboard_server.py`: isolated tick dashboard API/server.
- `notify.py`: Discord notification helper và throttling.
- `service_jobs.py`: account-list, symbol-sync, historical backfill, profile build.
- `service_live.py`: live subscription, reconnect, heartbeat, periodic report, flush loop.
- `cli.py`: CLI operator.
