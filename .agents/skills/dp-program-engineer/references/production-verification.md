# Kiểm định deployment và runtime

## Nguyên tắc

Thu evidence read-only trước. Không coi `Running`, Discord delivery hoặc pytest
riêng lẻ là bằng chứng hệ thống đang giao dữ liệu thành công.

## Evidence matrix

| Lớp | Cần xác minh |
|---|---|
| Host | hostname, UTC time, boot time, disk |
| Code | physical root, junction, branch, HEAD, working tree |
| Wrapper | Scheduled Task state, action, account, trigger, restart policy |
| Process | supervisor/live/historical PID, start time, command line |
| Config | app root, 37/11/165 contract, lịch UTC, secret masked |
| SQL | reachable, DB contract, Fact count/watermark |
| Live | batch completion, accepted/fact_inserted, stale/missing pairs |
| Historical | last run, schedule queue, success/failure, Fact delivery |
| Durability | spool pending/leased/staged/oldest age, lock state |
| Auth/network | token state, connectivity, reconnect/forced-close metrics |
| Observability | four logs, emergency sink, alert outbox, Discord HTTP success |
| Resources | RSS, thread/handle count và xu hướng nếu có nhiều mẫu |

## Lệnh ứng dụng read-only

```powershell
python -m core_engine settings --json
python -m core_engine doctor --json
python -m core_engine status --json
python -m core_engine data-health --json
python -m core_engine logs status
python -m core_engine logs risks --since 24h
```

Dùng `collect-dp6-evidence.ps1` để lấy cùng một snapshot từ VM phát triển.
Không lưu output có secret; settings/doctor phải mask credential.

## Cách đọc kết quả

- Task `Running` nhưng không có supervisor PID là lỗi wrapper/startup.
- Supervisor sống nhưng batch completion không tiến là silent stall.
- `accepted_bars > 0` nhưng `fact_inserted = 0` cần kiểm tra staging, ETL và
  deferred spool; không kết luận ngay là mất dữ liệu.
- Fact watermark phải xét giờ thị trường và loại timeframe.
- Raw gap bao gồm weekend/holiday; chỉ unresolved market-open gap mới là lỗi.
- Token hết hạn nhưng refresh material còn không tự động là outage; phải xem
  refresh/reconnect outcome.
- Discord gửi được chỉ chứng minh alert transport, không chứng minh data path.
- Một snapshot resource không chứng minh không leak; cần ít nhất hai mẫu theo
  thời gian và cùng loại workload.

## Khi có thay đổi production

Trước deploy ghi baseline. Dừng graceful, chạy test bằng Python production,
start đúng wrapper, rồi so sánh:

- PID/process generation;
- first healthy batch;
- Fact watermark/count;
- spool/lock;
- ERROR/CRITICAL mới;
- alert delivery;
- resource count.

Nếu thay đổi chỉ là documentation hoặc repo-scoped skill, không restart engine.
