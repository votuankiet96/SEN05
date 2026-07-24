# Checklist toàn vẹn dữ liệu OHLCV

Đọc code và test hiện tại trước; các tên state dưới đây mô tả invariant, không
thay thế implementation.

## Live outbox và delivery

- Persist dữ liệu trước khi phụ thuộc RAM queue.
- Lease phải có owner/expiry hoặc recovery rõ ràng.
- Staging thành công chưa đủ để ack.
- Chỉ ack sau khi ETL/Fact commit đã được xác nhận.
- Startup phải thu hồi row còn leased/staged sau crash theo thiết kế hiện hành.
- Retry đồng thời phải idempotent theo business key.
- Full spool không được âm thầm drop candle.

Fault points cần test:

1. crash trước staging;
2. crash sau staging trước ETL;
3. stored procedure fail/timeout;
4. Fact commit xong trước ack;
5. restart khi còn pending/leased/staged;
6. duplicate retry hoặc hai worker xử lý cùng key.

## Warehouse và SQL

- Verify DB contract trước write.
- Staging và Fact key phải nhất quán với stored procedure.
- Purge chỉ khi Fact đã chứa đúng row/value.
- Reconcile chỉ tính row join được `DWH.Dim_Date` là Fact-eligible.
- Row ngoài calendar phải báo riêng; không tự mở rộng calendar.
- Migration cần transaction, validation và recovery plan.

## Locks

- `SEN.ActiveTask` phải giữ OwnerId/Fence semantics.
- Process chết không được giữ lock logic vô hạn.
- Owner cũ không được release/commit thay owner mới.
- Lock timeout phải hữu hạn và lỗi phải hiển thị.

## Historical và gaps

- Phân biệt market closure với missing candle trong giờ mở cửa.
- Weekend, holiday và session boundary không tự động là lỗi.
- Gap repair phải verify lại Fact sau pull/ETL.
- Targeted repair không được vô tình tải lịch sử sâu ngoài window.
- Restart nhiều lần phải hội tụ và không nhân đôi dữ liệu.

## Network/auth

- Heartbeat TradingView phải được echo/parse đúng.
- Reconnect không được tạo worker/thread tăng vô hạn.
- Token/cookie hết hạn phải đi qua refresh/fallback có cooldown.
- Timeout phải force-close đúng socket và để supervisor recycle khi wedged.

## Kết luận

Chỉ tuyên bố data path khỏe khi có bằng chứng liên tục:

```text
TradingView response
  -> validated closed candle
  -> durable spool/outbox
  -> staging
  -> DWH.usp_LoadDirect
  -> Fact row/watermark
  -> spool ack
```
