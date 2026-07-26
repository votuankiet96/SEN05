# DP Program — Tổng quan chức năng & kiến trúc

> Tài liệu cho mentor, dựa trên code thực tế (nhánh `refactor/v2-streamline`, kiểm chứng 2026-07-24).

## 1. Chương trình làm gì

DP Program là **data provider chạy 24/7 trên Windows** cho hệ thống SEN05 AutoTrading:

- Lấy dữ liệu nến OHLCV từ **TradingView** qua WebSocket — live nhận real-time, historical dùng history/replay request qua cùng giao thức (xem §4.2).
- Validate, khử trùng lặp, ghi vào **SQL Server**: staging → `DWH.Fact_OHLCV` qua stored procedure `usp_LoadDirect`.
- SQL Server là **nguồn sự thật duy nhất** cho toàn bộ dữ liệu OHLCV.
- Tự giám sát sức khỏe, tự phục hồi khi lỗi, cảnh báo qua Discord.

**Phạm vi:** 37 symbol, 15 timeframe. Live chạy cho 11 symbol (`Indice/Metal/Crypto`, 165 phiên symbol×timeframe), batch mỗi 5 phút, chỉ nhận nến đã đóng. FOREX chỉ chạy historical.

## 2. Kiến trúc tiến trình — và vì sao thiết kế vậy

3 tiến trình Windows lồng nhau, mỗi tiến trình một vai trò riêng:

```
[Windows Scheduled Task]           tự khởi động lại nếu bị dừng/VM reboot
        │  python -m core_engine run --live
        ▼
[Supervisor] 24/7 — "người quản lý", không tự lấy dữ liệu
        ├─▶ [Live process]        24/7 — kết nối WS mỗi 5 phút để nhận nến (chỉ lấy nến đã đóng, nếu thiếu nến trong batch sẽ ghi nhận lại và lấy trong đợt kế cận)
        └─▶ [Historical process]  Chạy theo định kỳ 2 lần/ngày hoặc ngay khi khởi động lại hệ thống
```

**Vì sao tách 3 tiến trình riêng thay vì gộp 1 chương trình?**

- **Cô lập sự cố**: historical là job nặng, dễ treo (backfill sâu hàng giờ) — nếu chung tiến trình, historical treo sẽ kéo cả live (24/7) ngừng theo.
- **Chính sách phục hồi khác nhau**: live cần restart gần như ngay khi hết tiến triển; historical chỉ cần giới hạn "chạy quá lâu thì huỷ" — khó gộp gọn vào một vòng lặp.
- **Supervisor tách khỏi phần dễ vỡ**: chỉ giám sát/restart, không tự lấy dữ liệu (phần phụ thuộc mạng/TradingView/SQL) → luôn ổn định để làm đúng vai trò kể cả khi 2 tiến trình con đang lỗi.
- **Task chỉ quản 1 tiến trình**: Task chỉ khởi động Supervisor; nếu Supervisor cũng crash/VM reboot, Task tự khởi động lại, Supervisor mới tự nhận biết bản cũ qua lock trên SQL Server để không chạy trùng.
- **Không share RAM → phối hợp qua SQL**: live/historical là 2 tiến trình OS tách biệt, không thể vô tình ghi chồng lên nhau; phối hợp (vd. "historical nhường chỗ lúc live đang ghi") đi qua trọng tài chung ngoài cả hai — bảng `SEN.ActiveTask` trên SQL Server, bền và không mất khi một bên bị kill & khởi động lại.

**Package chính** (`src/core_engine/`): `core/live` (engine 24/7), `core/historical` (engine theo lịch), `shared/tradingview` (giao thức WS, auth), `shared/warehouse` (SQL, validate, writer), `util/supervisor` (vòng đời tiến trình), `util/coordination` (SQL lock có fencing), `util/notify` (Discord + outbox CRITICAL), `settings` (contract & policy cố định).

## 3. Luồng dữ liệu

```
TradingView (WebSocket) → validate OHLCV (UTC, loại bar tương lai/trùng)
  → [Live] SQLite outbox (durable) → staging SQL → usp_LoadDirect → Fact_OHLCV
  → [Historical] fetch → validate → staging SQL → usp_LoadDirect → Fact_OHLCV → purge staging
  → health / log / Discord alert
```

---

## 4. Phương án kết nối & xử lý rủi ro mất kết nối (trọng tâm)

Hệ thống có **nhiều lớp phòng thủ độc lập**, mỗi lớp xử lý một loại lỗi khác nhau, đảm bảo *không mất, không lặp, không sai dữ liệu* khi mất kết nối ở bất kỳ điểm nào.

### 4.1 WebSocket TradingView (live)

Mỗi nhóm symbol có 1 worker thread cố định, mở lại kết nối mỗi batch (5 phút). Lỗi được phân loại (`classify_ws_error`) và xử lý khác nhau:

| Loại lỗi | Ứng xử |
|---|---|
| `rate_limit` (429) | Cooldown theo header `Retry-After` (mặc định 300s) |
| `auth` (401/403) | Đổi về `GUEST_TOKEN`, renew token bất đồng bộ; 403 thêm cooldown 900s |
| `server` (5xx) / `network` | Retry chuẩn với backoff |

- **Backoff cấp số nhân**: 30s → nhân đôi → trần 300s, tối đa 3 lần/chu kỳ.
- **Timeout cứng**: hết giờ chờ session → ép đóng socket gốc để tránh treo thread (đóng cờ `keep_running` không đủ để ngắt thread đang block trong `recv()`).
- **Thread không thu hồi được**: nếu vẫn sống sau khi ép đóng → đánh dấu cần **recycle toàn bộ live process** qua Supervisor.
- **Miss nhiều batch liên tiếp**: chuyển sang `requires_backfill`, báo lỗi, không retry vô hạn — cần chạy historical gap-repair.
- **Preflight**: kiểm tra khả năng vươn tới TradingView trước khi mở batch mới, cooldown nếu fail.

### 4.2 Historical lấy dữ liệu quá khứ bằng cách nào

Vẫn qua WebSocket (không phải HTTP), 2 kiểu request:
- **History request**: giống live, xin N nến gần nhất — dùng vá gap gần (`--mode gap`), không lùi xa được.
- **Replay session**: dùng cơ chế **"Bar Replay"** của TradingView để tua lùi xa hơn — dùng backfill sâu (`--mode full`), ghép nhiều cửa sổ thời gian lại.

### 4.3 Xác thực TradingView (token/cookie)

Chuỗi fallback khi phiên hết hạn/mạng gián đoạn:

```
token cache → cookie đã lưu → browser profile Playwright → headless login (user/pass, +2FA)
  → interactive login → GUEST_TOKEN (nếu guest_policy=abort thì dừng khởi động)
```

Có lock chống nhiều thread/tiến trình cùng refresh token trùng lặp.

### 4.4 Không mất dữ liệu khi crash: Durable Outbox (SQLite)

Cơ chế quan trọng nhất: `pending → leased → staged → ack (xoá)`

- Mỗi nến ghi vào **SQLite trên đĩa TRƯỚC** khi vào hàng đợi RAM.
- Chỉ `ack` (xoá) sau khi **Fact_OHLCV commit thành công** — không ack ngay sau khi ghi staging.
- Restart process → mọi row dở dang **tự reset về `pending`** và được thử lại (an toàn vì ghi là idempotent).
- Outbox đầy (100,000 rows) → tạm dừng nhận batch mới + cảnh báo CRITICAL, không âm thầm giữ trong RAM.

### 4.5 Kết nối SQL Server

- Kết nối retry ngắn hạn (3 lần / 5s), có `command timeout` + `LOCK_TIMEOUT` để câu lệnh treo không chặn cả worker.
- Ghi staging lỗi → retry 3 lần → nếu vẫn lỗi, row trả về `pending` (không mất).
- Chạy ETL (`usp_LoadDirect`) lỗi → retry ngay 3 lần → vẫn lỗi thì vào **deferred queue**, retry định kỳ **vô thời hạn** đến khi SQL hồi phục.
- Kiểm tra contract version + cột fencing trước khi cho ghi, tránh ghi sai khi DB đang chạy bản cũ.

### 4.6 Khoá điều phối chống owner "ma"

`SEN.ActiveTask` + **OwnerId/Fence** (giống fencing token trong distributed systems): mỗi lock có chủ sở hữu + số thế hệ riêng, tiến trình cũ quay lại không thể xoá nhầm lock của tiến trình mới. Tự dọn lock "ma" (PID chết, hoặc PID tái sử dụng sau reboot). Heartbeat renew mỗi ~30-60s.

### 4.7 Supervisor: giám sát & tự phục hồi

- Phát hiện crash qua exit code → tự restart, có ngân sách restart/giờ; hết ngân sách thì **backoff cấp số nhân** (30s → trần 1800s) thay vì bỏ cuộc.
- **Live freshness watchdog**: heartbeat/tiến triển batch cũ quá ngưỡng → tự restart live (xác nhận 2-3 lần để tránh restart nhầm).
- **Historical**: giới hạn thời gian chạy tối đa, huỷ job treo; backoff riêng khi fail lặp lại.
- Supervisor tự giữ 1 lock heartbeat; xử lý riêng trường hợp VM reboot khiến lock cũ chưa hết hạn.

### 4.8 Cảnh báo khi có sự cố (Discord)

- **Cảnh báo thường**: hàng đợi bất đồng bộ, circuit breaker, dedupe — không chặn luồng dữ liệu chính.
- **Cảnh báo CRITICAL**: ghi vào **SQLite outbox bền vững ngay lập tức** trước khi trả về — mất kết nối Discord cũng không mất alert; Supervisor tự rút & gửi lại định kỳ khi mạng phục hồi.

---

## 5. Tổng hợp: rủi ro → cơ chế chống đỡ

| Rủi ro | Cơ chế xử lý |
|---|---|
| Mất WebSocket giữa batch | Retry backoff theo loại lỗi, cooldown rate-limit/auth, timeout ép đóng socket |
| Token/cookie hết hạn | Chuỗi fallback cache→cookie→browser profile→headless→guest |
| Thread WS treo không thu hồi | Tự recycle toàn bộ live process qua Supervisor |
| Live process crash bất kỳ lúc nào | Outbox SQLite ghi trước-khi-xử lý, tự reset khi khởi động lại |
| Mất kết nối SQL tạm thời | Retry kết nối, staging retry, ETL deferred-retry vô hạn |
| Outbox đầy | Tạm dừng nhận batch mới + cảnh báo CRITICAL |
| Lock "ma" (process chết/VM reboot) | Fencing OwnerId/Fence + phát hiện PID chết/reuse |
| Crash loop | Ngân sách restart/giờ + backoff cấp số nhân, không bỏ cuộc |
| Live "sống" nhưng treo logic | Watchdog heartbeat + tiến triển batch, tự restart |
| Discord/webhook mất kết nối | Circuit breaker (thường); outbox bền vững + tự rút (CRITICAL) |
| Symbol/timeframe miss kéo dài | Chuyển trạng thái cần backfill + cảnh báo, không retry vô hạn |

## 6. Nguyên tắc thiết kế xuyên suốt

1. **Ghi-trước-khi-làm**: mọi nến phải durable trên đĩa/SQL trước khi được coi là "đã nhận".
2. **Không ack sớm**: chỉ xác nhận sau bước cuối cùng (Fact commit) — crash giữa chừng luôn để lại dấu vết để retry.
3. **Tách rời các lớp lỗi**: WS, auth, SQL, lock đều có đường xử lý & cooldown riêng.
4. **Không bao giờ "chết hẳn"**: mọi tầng đều backoff-và-tự-thử-lại thay vì dừng vĩnh viễn.
5. **Luôn có bằng chứng**: mọi trạng thái bất thường có counter/log/cảnh báo để `doctor`/`status`/`data-health` phản ánh đúng thực tế.
