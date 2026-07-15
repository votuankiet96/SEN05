# Kiến Trúc Kết Nối Và Truyền Dữ Liệu Giữa DP Program - Redis - OG Program

Ngày: 2026-07-15  
Phạm vi: Luồng dữ liệu từ `dp_program` sang Redis, từ Redis sang `og_program`, và từ `og_program` publish tín hiệu trở lại Redis  
Mục đích: Tài liệu kiến trúc để mentor/reviewer kiểm tra cách triển khai, rủi ro và tính đúng đắn của cơ chế handoff dữ liệu

## 1. Tóm Tắt Tổng Quan

`dp_program` là chương trình cung cấp dữ liệu OHLCV. Nhiệm vụ chính của nó là lấy nến đã đóng từ TradingView, ghi an toàn vào SQL Server, sau đó đưa dữ liệu nến mới nhất sang Redis để hệ thống phía sau sử dụng.

Redis đóng vai trò là lớp trung gian tốc độ cao giữa `dp_program` và `og_program`.

`og_program` đọc dữ liệu nến từ Redis, chạy logic chiến lược, tính ra tín hiệu giao dịch, rồi publish tín hiệu đó ra Redis cho các thành phần phía sau như Order Follower hoặc downstream consumer.

Thiết kế hiện tại tách rõ các vai trò:

```text
SQL Server       = nguồn dữ liệu gốc, đầy đủ và lâu dài
Redis State Key  = bản snapshot mới nhất để OG đọc nhanh
Redis Stream     = lịch sử event bền vững, có thể đọc lại
Redis Pub/Sub    = thông báo realtime để OG phản ứng nhanh
OG Signal Stream = kết quả tín hiệu do OG publish ra Redis
```

Nguyên tắc vận hành quan trọng:

```text
Redis hỗ trợ truyền dữ liệu sang OG, nhưng Redis không được làm nghẽn hoặc làm hỏng luồng lấy dữ liệu chính của DP.
```

## 2. Vai Trò Của Từng Hệ Thống

### 2.1 DP Program

DP Program chịu trách nhiệm:

- Lấy nến OHLCV đã đóng từ TradingView.
- Ghi dữ liệu nến vào SQL Server.
- Tạo snapshot mới nhất theo từng symbol/timeframe.
- Đưa snapshot và metadata cập nhật sang Redis.

DP Program không chịu trách nhiệm:

- Tính tín hiệu buy/sell.
- Đặt lệnh.
- Điều khiển Order Follower.
- Thay thế SQL Server làm kho dữ liệu dài hạn.

### 2.2 Redis

Redis là lớp trung gian dùng để truyền dữ liệu nhanh và hỗ trợ realtime.

Redis đang được dùng theo nhiều kiểu khác nhau:

- Key/Value để lưu latest candle snapshot.
- Stream để lưu event cập nhật có thể đọc lại.
- Pub/Sub để thông báo realtime.
- Stream đầu ra của OG để lưu tín hiệu giao dịch.

Redis không tự query DP. DP chủ động gửi lệnh sang Redis.

### 2.3 OG Program

Theo phản hồi từ phía OG, hiện `og_program` đang dùng cả hai cơ chế input từ DP:

```text
Stream input : dp:candle_snapshot:events
Pub/Sub input: dp:pubsub:candle_snapshot:events
```

Cả hai service hiện đang active và health check đang `OK`.

OG Program đang:

- Lấy `state_key` từ event/message.
- `GET state_key` từ Redis.
- Parse JSON snapshot.
- Đọc mảng `bars`.
- Chạy strategy trực tiếp trên dữ liệu nến đó.
- Strategy hiện tại: `combo`.
- Watchlist hiện tại: nhóm `Indice`, timeframe `H1,H2,H3,H4`, tổng 36 symbol/timeframe pairs.

## 3. Luồng Dữ Liệu Tổng Quát

```text
TradingView
    |
    v
DP live_fetching
    |
    v
SQL Server / DWH.Fact_OHLCV
    |
    v
DP candle_snapshot worker
    |
    +--> Redis SET latest snapshot key
    |
    +--> Redis XADD durable stream event
    |
    +--> Redis PUBLISH realtime Pub/Sub metadata
             |
             v
        OG Program
             |
             v
        Strategy combo
             |
             v
        Redis signal streams
             |
             v
        OF / downstream consumers
```

Điểm cốt lõi:

```text
DP luôn ghi SQL trước.
Redis chỉ được cập nhật sau khi dữ liệu nến đã được commit thành công.
```

## 4. Kết Nối Kỹ Thuật Giữa DP Và Redis

DP kết nối tới Redis bằng Python Redis client qua TCP.

Thông tin kết nối nằm trong:

```text
config/dp_provider.env
```

Các biến chính:

```env
OG_REDIS_HOST=10.11.12.8
OG_REDIS_PORT=6379
OG_REDIS_USERNAME=default
OG_REDIS_PASSWORD=...
OG_REDIS_DB=0
```

Về mặt mạng, kết nối là:

```text
Máy DP6 -> TCP -> Redis server 10.11.12.8:6379 -> Redis DB 0
```

DP gửi các lệnh Redis:

```text
SET
XADD
PUBLISH
```

Redis nhận lệnh, thực thi lệnh, lưu state/event và broadcast message cho consumer đang lắng nghe.

Nói chính xác:

```text
Redis không tự lấy dữ liệu từ DP.
DP chủ động mở kết nối và gửi lệnh sang Redis.
```

## 5. Cách DP Tạo Candle Snapshot

Khi `live_fetching` ghi thành công nến mới vào SQL Server, nó gọi:

```text
publish_candle_snapshot(symbol_id, tv_symbol, tf_code)
```

Hàm này không trực tiếp query SQL và không trực tiếp ghi Redis trong thread ghi DB chính.

Thay vào đó, DP dùng một worker riêng:

```text
DB writer thread
    -> đưa request nhẹ vào queue nội bộ
    -> quay lại luồng ghi dữ liệu chính

candle_snapshot worker
    -> query SQL lấy N nến mới nhất
    -> ghi Redis
```

Thiết kế này bảo vệ luồng chính:

```text
Redis chậm hoặc lỗi không được phép làm chậm ghi SQL.
```

## 6. Redis State Key

Redis State Key lưu snapshot mới nhất của một cặp symbol/timeframe.

Format key:

```text
dp:candle_snapshot:latest:{SYMBOL}:{TIMEFRAME}
```

Ví dụ:

```text
dp:candle_snapshot:latest:US30:H1
```

Value bên trong là một JSON snapshot, thường chứa 500 nến mới nhất.

Ví dụ cấu trúc:

```json
{
  "schema_version": 1,
  "program": "dp_program",
  "source": "live_fetching",
  "symbol_id": 10,
  "tv_symbol": "US30",
  "tf_code": "H1",
  "bars_count": 500,
  "latest_bar_time": "2026-07-14T11:00:00",
  "generated_at_utc": "2026-07-14T11:05:01Z",
  "snapshot_version": "US30:H1:2026-07-14T11:00:00",
  "bars": [
    {
      "bar_time": "2026-07-01T16:00:00",
      "open": 38400.1,
      "high": 38480.5,
      "low": 38320.2,
      "close": 38450.7,
      "volume": 1234
    }
  ]
}
```

Mảng `bars` được sắp xếp:

```text
nến cũ nhất -> nến mới nhất
```

Nến cuối cùng trong `bars` là nến mới nhất.

Cơ chế cập nhật:

```text
DP không lấy value cũ trong Redis rồi thêm một nến mới.
DP query lại SQL để lấy N nến mới nhất.
DP SET đè toàn bộ JSON snapshot mới vào Redis.
```

Lý do:

- SQL Server là source of truth.
- Redis chỉ là latest snapshot/cache.
- Nếu dữ liệu cũ trong SQL được sửa lại, snapshot mới sẽ phản ánh lại đúng.
- Không phụ thuộc vào việc value Redis cũ còn đúng hay không.

## 7. Redis Stream Input Cho OG

Redis Stream được dùng như một lịch sử event có thể đọc lại.

Tên stream:

```text
dp:candle_snapshot:events
```

Sau khi DP ghi state key thành công, DP thêm một event vào stream:

```text
XADD dp:candle_snapshot:events ...
```

Event này không chứa toàn bộ 500 nến. Nó chỉ chứa metadata để OG biết key nào vừa được cập nhật.

Ví dụ event:

```json
{
  "schema_version": "1",
  "event_type": "snapshot_updated",
  "program": "dp_program",
  "source": "live_fetching",
  "symbol_id": "10",
  "tv_symbol": "US30",
  "tf_code": "H1",
  "bar_time": "2026-07-14T11:00:00",
  "state_key": "dp:candle_snapshot:latest:US30:H1",
  "bars_count": "500",
  "published_at_utc": "2026-07-14T11:05:01Z",
  "snapshot_version": "US30:H1:2026-07-14T11:00:00"
}
```

Ý nghĩa của Stream:

```text
Nếu OG restart hoặc offline tạm thời, OG vẫn có thể đọc lại các event đã được ghi vào stream.
```

Theo phản hồi từ OG:

- Stream service đọc pending trước bằng consumer group.
- Sau đó đọc event mới.
- Nếu consumer group mới được tạo lần đầu, nó bắt đầu từ `$`, không backfill toàn bộ lịch sử cũ.
- Stream là lane bền vững/recovery tốt hơn Pub/Sub.

## 8. Redis Pub/Sub Input Cho OG

Redis Pub/Sub được dùng như một kênh thông báo realtime.

Tên channel:

```text
dp:pubsub:candle_snapshot:events
```

Sau khi DP đã:

```text
SET latest snapshot key
XADD stream event
```

thì DP publish thêm một message realtime:

```text
PUBLISH dp:pubsub:candle_snapshot:events {...metadata...}
```

Pub/Sub không chứa 500 nến. Nó chỉ báo cho OG rằng có snapshot mới.

Ví dụ message:

```json
{
  "schema_version": 1,
  "event_type": "snapshot_updated",
  "symbol_id": 10,
  "tv_symbol": "US30",
  "tf_code": "H1",
  "bar_time": "2026-07-14T11:00:00",
  "state_key": "dp:candle_snapshot:latest:US30:H1",
  "snapshot_version": "US30:H1:2026-07-14T11:00:00",
  "bars_count": 500,
  "published_at_utc": "2026-07-14T11:05:01Z"
}
```

OG khi nhận Pub/Sub sẽ:

```text
SUBSCRIBE dp:pubsub:candle_snapshot:events
nhận message
parse JSON
GET message.state_key
kiểm tra snapshot_version/latest_bar_time
chạy chiến lược
```

Điểm cần hiểu:

```text
Pub/Sub rất nhanh nhưng không lưu lịch sử.
Nếu OG không subscribe đúng lúc DP publish, message đó sẽ mất.
```

Theo phản hồi từ OG:

- Pub/Sub service subscribe lại sau restart và chờ message mới.
- Pub/Sub không replay message cũ.
- Nếu Pub/Sub nhận message nhưng `GET state_key` lỗi:
  - Redis error: log/audit `state_load_error`, reset Redis client, service loop restart.
  - Key missing: log/audit `state_missing`, skip message.
  - Không fallback nội bộ sang Stream trong cùng Pub/Sub service.
  - Stream service vẫn chạy độc lập nếu đang bật.

## 9. Vì Sao Dùng Cả Stream Và Pub/Sub

Việc dùng cả Stream và Pub/Sub là có chủ đích, không phải dư thừa.

### 9.1 Vì Sao Không Chỉ Dùng State Key?

State key lưu dữ liệu mới nhất, nhưng nó không tự báo cho OG biết khi nào key đổi.

Nếu chỉ có state key, OG phải polling liên tục.

### 9.2 Vì Sao Không Chỉ Dùng Stream?

Stream bền vững và có thể đọc lại, nhưng độ realtime phụ thuộc vào cách OG consume stream.

Stream phù hợp làm lớp đáng tin cậy và phục hồi.

### 9.3 Vì Sao Không Chỉ Dùng Pub/Sub?

Pub/Sub realtime nhanh, nhưng không lưu lịch sử.

Nếu OG restart hoặc mất kết nối, các message trong khoảng đó mất vĩnh viễn.

Với hệ thống chạy 24/7, chỉ dùng Pub/Sub là rủi ro.

### 9.4 Thiết Kế Hiện Tại

Thiết kế hiện tại:

```text
State Key = dữ liệu mới nhất
Stream    = lịch sử event bền vững
Pub/Sub   = thông báo realtime
```

Lợi ích:

- OG có thể phản ứng nhanh khi online.
- OG vẫn có đường phục hồi khi restart.
- Dữ liệu 500 nến chỉ lưu ở state key, không nhồi toàn bộ vào Pub/Sub.
- DP không bị phụ thuộc ngược vào Redis/OG.

### 9.5 Ghi Chú Từ OG

Hiện OG chưa cấu hình theo kiểu primary/fallback tự động.

Thực tế hiện tại:

```text
Stream và Pub/Sub đang chạy song song, tách biệt để đối chiếu.
Stream output ra Redis DB1.
Pub/Sub output ra Redis DB2.
```

Do hai lane output tách riêng, OG hiện chưa dedupe chéo giữa Stream và Pub/Sub.

Nếu sau này hợp nhất production thành một đường tín hiệu duy nhất, cần quyết định rõ:

- Lane nào là primary.
- Lane nào là fallback.
- Dedupe chéo theo `strategy|symbol|tf|snapshot_version` hay theo `signal_id`.
- Output cuối cùng cho OF sẽ lấy từ DB/key/stream nào.

## 10. Tần Suất Cập Nhật Redis

DP live fetching hiện chạy theo batch định kỳ.

Tần suất có nến mới phụ thuộc vào timeframe.

Ví dụ:

```text
M5  -> có thể cập nhật mỗi 5 phút
M10 -> có thể cập nhật mỗi 10 phút
H1  -> có thể cập nhật mỗi giờ
D1  -> có thể cập nhật mỗi ngày
W   -> có thể cập nhật mỗi tuần
```

Redis không nhất thiết nhận đủ tất cả symbol/timeframe ở mỗi batch.

DP publish snapshot khi có dữ liệu live mới được commit cho symbol/timeframe tương ứng.

Nếu timeframe dài chưa có nến mới, state key cũ vẫn còn giá trị mới nhất cho timeframe đó.

## 11. OG Validation, Dedupe Và Stale Check

Theo phản hồi từ OG, OG có validate snapshot trước khi tính signal.

Các trường được kiểm tra gồm:

- `event_type`
- `tv_symbol`
- `tf_code`
- `bar_time`
- `state_key`
- `snapshot_version`
- `latest_bar_time`
- `bars`
- Các field OHLCV bắt buộc trong từng bar

OG yêu cầu:

```text
snapshot_version, symbol, timeframe, latest_bar_time phải khớp với event/message.
```

### 11.1 Chống Xử Lý Trùng

OG hiện có dedupe theo từng mechanism riêng:

```text
Snapshot dedupe = strategy|symbol|tf|snapshot_version
Signal dedupe   = signal_id
Dedup key TTL   = 14 ngày
```

Hiện Stream và Pub/Sub không dùng chung dedupe vì đang là hai lane output riêng.

### 11.2 Chống Stale/Cũ

OG hiện có một phần stale protection:

- Event quá cũ quá 15 phút sẽ bị skip.
- Nếu `state_key` hiện tại không còn khớp `snapshot_version` trong event thì skip.
- Stream replay event cũ mà state đã bị DP ghi snapshot mới hơn thì cũng skip.

Điểm còn mở:

```text
OG chưa có stale rule riêng theo từng timeframe, ví dụ H4 phải dưới X giờ.
Health hiện chủ yếu kiểm tra coverage/bar count.
```

## 12. OG Signal Output

Theo phản hồi từ OG, output hiện là Redis Stream, không phải Pub/Sub.

### 12.1 Output Từ Stream Mechanism

```text
Redis DB : db1
Type     : Stream
Key      : og:stream:signals:{strategy}:{symbol}:{timeframe}
Ví dụ    : og:stream:signals:combo:UK100:H1
```

### 12.2 Output Từ Pub/Sub Mechanism

```text
Redis DB : db2
Type     : Stream
Key      : og:pubsub:signals:{strategy}:{symbol}:{timeframe}
Ví dụ    : og:pubsub:signals:combo:UK100:H1
```

Signal stream không có TTL.

Dedup key là Redis String với TTL 14 ngày.

Stream bị trim theo:

```text
MAXLEN ~ 10000
```

OG chưa định nghĩa consumer group cho OF. Phần đó thuộc OF/downstream.

## 13. Schema Signal Output Của OG

Redis Stream entry ở dạng field/value, tương đương object sau:

```json
{
  "schema_version": "1",
  "signal_id": "7d4019b06b969ef3dec4c3fa",
  "strategy": "combo",
  "symbol": "UK100",
  "timeframe": "H1",
  "direction": "-1",
  "side": "SELL",
  "bar_time": "2026-07-15T05:00:00",
  "event_close": "10489.6",
  "entry_price": "10474.1",
  "sl_price": "10501.9",
  "tp_price": "10440.22780601583",
  "risk_reward": "1.22",
  "atr": "14.908536084582295",
  "signal_reason": "first Combo SELL state: bearish candle, close below MA, MACD histogram < 0",
  "produced_at": "2026-07-15T06:00:38.555065+00:00",
  "source_program": "dp_program",
  "source_mechanism": "stream",
  "source_state_key": "dp:candle_snapshot:latest:UK100:H1",
  "source_snapshot_version": "UK100:H1:2026-07-15T05:00:00",
  "source_bar_time": "2026-07-15T05:00:00"
}
```

Các field nguồn như `source_state_key`, `source_snapshot_version`, `source_bar_time` rất quan trọng để audit tín hiệu quay lại dữ liệu đầu vào.

## 14. Health Check Và Recovery Phía OG

Theo phản hồi từ OG, health check hiện có:

- Redis ping input/output.
- Stream length/latest entry.
- Consumer group lag/pending.
- Pub/Sub subscriber count.
- Snapshot coverage theo watchlist.
- Bar count.
- Latest bar time.
- Signal stream latest signal.
- Local outbox/state.

Health check publish signal có:

- Kiểm tra signal stream output có đọc được không.
- Kiểm tra latest signal có đủ `signal_id`, `symbol`, `timeframe`, `bar_time`, `side`.
- Kiểm tra local delivery outbox pending = 0.

Restart recovery:

- Stream: đọc pending trước bằng consumer group, sau đó đọc event mới.
- Pub/Sub: subscribe lại và chờ message mới, không replay message cũ.
- Cả hai load lại local state và retry local outbox.
- Nếu Stream consumer group mới tạo lần đầu, nó bắt đầu từ `$`, không backfill toàn bộ lịch sử cũ.

Backpressure:

- Stream có consumer group nên lag có thể tăng.
- Chưa có cơ chế "always skip to latest" tuyệt đối.
- Event cũ quá 15 phút hoặc state đã mismatch sẽ bị skip.
- Pub/Sub không có durable backlog; nếu OG chậm/offline, message có thể mất.

## 15. Cơ Chế An Toàn Khi Redis Lỗi

Redis handoff phía DP được thiết kế theo hướng best-effort và không chặn luồng chính.

Nếu Redis down hoặc chậm:

```text
DP vẫn ghi SQL
live_fetching vẫn chạy
DP log warning nội bộ
circuit breaker tạm ngưng gửi Redis trong một khoảng ngắn
DP không crash chỉ vì Redis lỗi
```

Nếu Pub/Sub lỗi:

```text
state key vẫn đã được ghi
stream event vẫn đã được ghi
chỉ mất thông báo realtime
```

Nếu Stream lỗi:

```text
state key có thể vẫn đã được ghi
durable event bị ảnh hưởng
DP log warning và mở circuit breaker
```

Nếu State Key ghi lỗi:

```text
OG không thể đọc snapshot mới nhất từ Redis
DP log warning và mở circuit breaker
SQL vẫn là source of truth
```

Mục tiêu:

```text
Redis không được trở thành single point of failure làm hỏng DP live ingestion.
```

## 16. Checklist Debug End-To-End

### 16.1 Kiểm Tra Log DP

Tìm các dòng:

```text
CANDLE_SNAPSHOT
CANDLE_SNAPSHOT_PUBSUB
publish failed
Redis recovered
snapshot queue is full
worker failed safely
```

Log liên quan:

```text
runtime/logs/operation/live_fetching.log
runtime/logs/system/backend_engine.log
```

### 16.2 Kiểm Tra Redis State Key

Ví dụ:

```bash
redis-cli GET dp:candle_snapshot:latest:US30:H1
```

Cần kiểm tra:

- Có JSON hay không.
- `bars_count` đúng không.
- `latest_bar_time` có hợp lý với timeframe không.
- `snapshot_version` có khớp symbol/timeframe/latest candle time không.
- `bars` có đủ các field `bar_time`, `open`, `high`, `low`, `close`, `volume` không.

### 16.3 Kiểm Tra Redis Stream Input

Ví dụ:

```bash
redis-cli XRANGE dp:candle_snapshot:events - + COUNT 5
```

Cần kiểm tra:

- Có event mới sau khi live có nến mới không.
- Event có `state_key`, `tv_symbol`, `tf_code`, `bar_time`, `snapshot_version` không.

### 16.4 Kiểm Tra Pub/Sub Input

Ví dụ:

```bash
redis-cli SUBSCRIBE dp:pubsub:candle_snapshot:events
```

Cần hiểu:

- Pub/Sub chỉ hiện message mới từ thời điểm subscribe trở đi.
- Pub/Sub không cho xem lại lịch sử cũ.
- Nếu không thấy message ngay, chưa chắc lỗi; có thể chưa tới lúc có nến mới.

### 16.5 Kiểm Tra OG Signal Output

Stream lane:

```text
DB  : db1
Key : og:stream:signals:{strategy}:{symbol}:{timeframe}
```

Pub/Sub lane:

```text
DB  : db2
Key : og:pubsub:signals:{strategy}:{symbol}:{timeframe}
```

Cần kiểm tra:

- Có signal stream mới không.
- Signal có đủ `signal_id`, `symbol`, `timeframe`, `bar_time`, `side` không.
- `source_snapshot_version` có khớp snapshot input không.
- `source_mechanism` là `stream` hay `pubsub`.

## 17. Cấu Hình Hiện Tại Liên Quan

Nhóm cấu hình snapshot phía DP:

```env
CANDLE_SNAPSHOT_ENABLED=1
CANDLE_SNAPSHOT_STATE_PREFIX=dp:candle_snapshot:latest
CANDLE_SNAPSHOT_EVENT_STREAM=dp:candle_snapshot:events
CANDLE_SNAPSHOT_EVENT_MAXLEN=10000
CANDLE_SNAPSHOT_BARS=500
CANDLE_SNAPSHOT_QUEUE_MAXSIZE=1000
CANDLE_SNAPSHOT_TIMEOUT_SEC=0.3
CANDLE_SNAPSHOT_CIRCUIT_COOLDOWN_SEC=30

DP_CANDLE_PUBSUB_ENABLED=true
DP_CANDLE_PUBSUB_CHANNEL=dp:pubsub:candle_snapshot:events
DP_CANDLE_PUBSUB_SCHEMA_VERSION=1
```

Nhóm cấu hình Redis:

```env
OG_REDIS_HOST=10.11.12.8
OG_REDIS_PORT=6379
OG_REDIS_DB=0
```

Yêu cầu phía OG đối với DP:

- Redis input DB: `db0`.
- State key: `dp:candle_snapshot:latest:{SYMBOL}:{TIMEFRAME}`.
- Stream: `dp:candle_snapshot:events`.
- Pub/Sub channel: `dp:pubsub:candle_snapshot:events`.
- `bars_count` nên là `500` nếu đủ dữ liệu.
- Time format ISO-8601.
- Symbol/timeframe uppercase.
- Event bắt buộc có `event_type`, `tv_symbol`, `tf_code`, `bar_time`, `state_key`, `snapshot_version`.
- State snapshot bắt buộc có `tv_symbol`, `tf_code`, `latest_bar_time`, `snapshot_version`, `bars`.
- Mỗi bar cần `bar_time`, `open`, `high`, `low`, `close`, `volume`.

Thông tin credential được lưu trong `.env` và không nên in ra log hoặc tài liệu.

## 18. Những Điểm Còn Cần Quyết Định Trước Khi Chốt Production Cuối

Hiện Stream và Pub/Sub đang chạy song song để đối chiếu, output tách riêng:

```text
Stream lane output -> db1
Pub/Sub lane output -> db2
```

Trước khi chốt production cuối, cần quyết định:

1. Có tiếp tục giữ hai lane output riêng không?
2. Hay sẽ chọn một lane chính cho OF sử dụng?
3. Nếu hợp nhất, dedupe chéo giữa Stream và Pub/Sub sẽ dựa trên `signal_id` hay `strategy|symbol|tf|snapshot_version`?
4. OF sẽ consume signal từ DB/key/stream nào?
5. OF có dùng consumer group để đọc lại signal khi offline không?
6. Có cần stale rule riêng theo timeframe không, ví dụ H4, H3, H2 khác H1?
7. Có cần skip-to-latest khi Stream lag quá lớn không?

## 19. Kết Luận

Kiến trúc hiện tại sử dụng Redis theo ba cơ chế đầu vào cho OG:

```text
State Key = dữ liệu nến mới nhất
Stream    = lịch sử event bền vững
Pub/Sub   = thông báo realtime
```

Ba cơ chế này không trùng vai trò:

- State Key chứa dữ liệu.
- Stream giúp phục hồi và audit event.
- Pub/Sub giúp phản ứng realtime.

Sau khi OG nhận dữ liệu, OG publish signal ra Redis Stream:

```text
Stream mechanism output -> db1 / og:stream:signals:{strategy}:{symbol}:{timeframe}
Pub/Sub mechanism output -> db2 / og:pubsub:signals:{strategy}:{symbol}:{timeframe}
```

DP Program được bảo vệ bằng cách:

- Ghi SQL trước.
- Redis chạy sau commit.
- Redis handoff chạy trong worker riêng.
- Redis lỗi không được phép làm hỏng live fetching.

OG Program được bảo vệ bằng cách:

- Validate snapshot.
- Dedupe snapshot/signal.
- Skip event quá cũ hoặc state mismatch.
- Dùng Stream để phục hồi và Pub/Sub để realtime.

Với vận hành production 24/7, nguyên tắc quan trọng là:

```text
DP phải tiếp tục lấy và ghi dữ liệu dù Redis gặp vấn đề.
OG phải luôn validate snapshot mới nhất trước khi phát tín hiệu.
OF/downstream cần được cấu hình rõ sẽ đọc signal từ stream nào.
```

