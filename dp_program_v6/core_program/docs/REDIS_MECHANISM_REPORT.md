# Báo cáo cơ chế Redis của DP

## 1. Phạm vi, nguồn chứng cứ và kết luận ngắn

Báo cáo này mô tả **chặng DP → Redis** trong `dp_program_v3`: đường live đồng bộ
nến vừa ghi SQL sang Redis, còn reconcile đồng bộ lại cửa sổ gần nhất từ SQL. Sau
đó DP báo cho OG biết pair nào vừa thay đổi.

Nguồn kiểm chứng của báo cáo là code và test hiện tại, chủ yếu tại:

- `core_program/src/dp_program/util/redis_publisher.py`
- `core_program/src/dp_program/engine/live.py`
- `core_program/src/dp_program/engine/runtime.py`
- `core_program/src/dp_program/engine/sql_connector.py`
- `core_program/test/test_redis_publisher_regression.py`

**Kết luận:** SQL Server là nguồn dữ liệu bền; Redis là bản chụp trạng thái gần
nhất cho OG đọc nhanh. DP dùng một Hash cho mỗi nến và một List làm chỉ mục thời
gian. Lua bảo đảm một lần cập nhật Redis không để reader nhìn thấy trạng thái dở
dang. Pub/Sub chỉ là tín hiệu có thay đổi, không phải kênh bảo đảm giao nhận.
Khi Redis hoặc process DP gặp sự cố, reconcile từ SQL có nhiệm vụ đưa lại cửa sổ
Redis về trạng thái SQL hiện có.

Phần code DP không chứa chiến lược signal của OG. Vì vậy báo cáo chỉ nêu **contract
đọc dữ liệu mà OG cần tuân theo**, không khẳng định OG hiện đang tính signal theo
một chiến lược cụ thể nào.

> Lưu ý về số liệu vận hành: các số như dung lượng Redis, số pair, thời gian
> reconcile hoặc trạng thái thị trường thay đổi theo thời điểm. Chỉ đưa chúng vào
> báo cáo vận hành khi kèm thời gian UTC, lệnh đã chạy và output/log tương ứng.
> Bản này không coi các số liệu lịch sử là bất biến thiết kế.

---

## 2. Bối cảnh và ranh giới trách nhiệm

DP lấy nến từ provider và ghi vào SQL trước. Redis nằm sau SQL, phục vụ nhu cầu
đọc nhanh của OG. Luồng thực tế là:

```text
Provider
   │
   ▼
DP fetch_and_store()
   │
   ├── ghi / cập nhật SQL Server ───────────────► nguồn dữ liệu bền
   │
   └── chỉ khi SQL thành công: enqueue Redis
                                      │
                                      ▼
                           worker Redis nền + Lua
                              │                 │
                              ▼                 ▼
                       Hash + List        Pub/Sub event
                              │                 │
                              └──────► OG đọc / tính lại pair liên quan

Khi khởi động và theo chu kỳ cấu hình:
SQL Server ─────────────► reconcile Redis cho các live pair
```

Ranh giới này dẫn đến ba quyết định quan trọng:

1. SQL mới là nơi lưu lịch sử và phục hồi sau sự cố; Redis chỉ giữ cửa sổ gần nhất.
2. Caller của đường live không chờ I/O Redis. Sau khi SQL trả về thành công,
   `publish_candle_update()` chỉ đưa công việc vào bộ nhớ; worker riêng mới kết nối
   Redis và chạy Lua.
3. Redis có thể chậm hơn SQL trong một khoảng thời gian. Theo đường live hiện tại,
   DP không chủ động publish một nến trước khi `fetch_and_store()` trả về thành công.

Điều thứ ba là bảo đảm của đường code này, không phải bảo đảm cho một writer bên
ngoài DP. Nếu có process khác ghi vào prefix `dp:candles:*`, contract không còn được
DP kiểm soát.

---

## 3. Dữ liệu được lưu như thế nào

Mỗi cặp `(symbol, timeframe)` có một cửa sổ tối đa `bars_per_snapshot` nến. Giá trị
này là cấu hình vận hành, không phải hằng số của kiến trúc; mọi mô tả về kích thước
cửa sổ trong báo cáo này đều phụ thuộc giá trị đó.

```text
LIST  dp:candles:{timeframe}:{symbol}:order
      [epoch-1, epoch-2, ..., epoch-n]       # epoch UTC theo giây, tăng dần

HASH  dp:candles:{timeframe}:{symbol}:{epoch}
      bartime  <ISO text>
      open     <decimal text>
      high     <decimal text>
      low      <decimal text>
      close    <decimal text>
      volume   <decimal text | "null">

STRING dp:candles:schema = "2"
```

Ví dụ với `US30:H1`:

```text
dp:candles:H1:US30:order
dp:candles:H1:US30:1788876000
```

List không lưu giá. Nó là **chỉ mục thời gian** để reader biết cần đọc hash nào và
để DP biết nến cũ nhất cần tỉa. List được duy trì theo epoch tăng dần; đây không
phải chỉ là thứ tự một nến đến Redis. Nếu một nến đến muộn, Lua chèn nó vào đúng vị
trí theo thời gian.

Nến được định danh bởi key có epoch cố định, không phải bởi toàn bộ giá trị OHLCV.
Khi provider hoặc SQL hiệu chỉnh một nến, DP ghi đè field của đúng Hash đó. Vì vậy
Redis giữ **phiên bản canonical mới nhất theo SQL tại thời điểm đồng bộ** cho mỗi
epoch, thay vì giữ lịch sử các lần sửa nến.

Các giá trị số được chuẩn hoá theo contract `DECIMAL` của warehouse trước khi đưa
vào Redis: giá có scale 8 và volume có scale 4. Publisher không chuyển chúng qua
`float` trước khi so sánh hoặc ghi, để tránh sinh thay đổi giả do làm tròn nhị phân.

---

## 4. Vì sao chọn Hash + List

### 4.1. Bài toán là trạng thái, không phải nhật ký lệnh

Một candle cần trả lời câu hỏi: “OHLCV hiện hành của epoch này là gì?”. OG có thể
đọc lại câu trả lời đó bất cứ lúc nào. Đây là dữ liệu **trạng thái**.

Ngược lại, một lệnh giao dịch như “đặt BUY” là **mệnh lệnh**: cần lịch sử, xác nhận
xử lý và cơ chế chống thực thi lại. Hai loại dữ liệu không nên dùng cùng một cơ chế
chỉ vì đều đi qua Redis.

| Nhu cầu | Candle DP → OG | Lệnh OG → OF |
|---|---|---|
| Bản chất | Trạng thái mới nhất | Mệnh lệnh phải xử lý |
| Mất một thông báo | Có thể đọc lại trạng thái | Có thể bỏ lỡ hành động |
| Nhận lại | Ghi đè / đọc lại được | Phải chống thực thi trùng |
| Cơ chế phù hợp | Hash + List, Pub/Sub làm tín hiệu | Stream + consumer group + idempotency |

### 4.2. Vì sao không dùng Stream cho candle

Stream là nhật ký append-only. Nó phù hợp khi consumer cần biết từng event đã được
xử lý, ACK, replay và nhận lại sau khi restart. Candle ở chặng này không có yêu cầu
đó: SQL giữ lịch sử bền, còn OG cần một cửa sổ trạng thái mới nhất.

Dùng Stream cho candle vẫn có thể làm được, nhưng OG sẽ phải duyệt/replay stream để
tự dựng trạng thái hiện tại và phải quản lý retention, consumer group và replay.
Đó là thêm cơ chế mà Hash + List đã trả lời trực tiếp: nến nào có trong cửa sổ và
giá trị hiện tại của nó là gì.

### 4.3. Vì sao không dùng ZSet làm cấu trúc chính

ZSet có thể lưu thứ tự thời gian, nhưng không tự là mô hình dữ liệu đầy đủ cho
OHLCV có thể bị hiệu chỉnh. Nếu dùng JSON OHLCV làm member, sửa giá sẽ tạo member
mới vì member thay đổi; nếu dùng epoch làm member, vẫn cần nơi khác để lưu các
field OHLCV. Vì vậy ZSet không loại được nhu cầu tách identity nến khỏi giá trị.

Thiết kế hiện tại dùng key theo epoch cho identity, Hash cho OHLCV và List cho thứ
tự. Vai trò của từng cấu trúc đơn giản, trực tiếp và đúng với thao tác đọc thực tế.
Điều này không có nghĩa ZSet không thể được thiết kế đúng; chỉ là nó không đem lại
lợi ích cần thiết cho contract hiện tại.

### 4.4. Vì sao Hash và List đi cùng nhau

- Hash cho phép ghi đè field của đúng nến có epoch xác định.
- Hash không cung cấp thứ tự thời gian để lấy N nến gần nhất.
- List lưu đúng thứ tự epoch cần cho cửa sổ, late arrival và eviction.

Tóm lại: **Hash giữ nội dung; List giữ thứ tự và phạm vi cửa sổ.**

---

## 5. DP ghi Redis như thế nào

### 5.1. Incremental update: đường nhanh

Sau khi SQL ghi thành công, `live.py` đưa `delivered_candles` vào publisher. Publisher
coalesce theo `(symbol, timeframe, epoch)`: nếu cùng epoch được enqueue nhiều lần
trước khi worker xử lý, bản mới nhất thay thế bản trước trong hàng đợi RAM.

Worker gọi `_INCREMENTAL_SCRIPT` bằng Lua. Với mỗi nến, script:

1. Đọc năm field OHLCV đang có từ Hash.
2. Chỉ `HSET` khi giá trị canonical thật sự khác.
3. Nếu epoch chưa có trong List, thêm ở cuối hoặc chèn đúng thứ tự thời gian.
4. Nếu List vượt giới hạn, lấy epoch cũ nhất ra khỏi List và xoá Hash tương ứng.
5. Chỉ publish các nến có OHLCV thay đổi.

Mỗi lần gọi Lua là atomic trong Redis. Bên đọc không thấy trạng thái trung gian do
chính lần gọi script đó tạo ra, ví dụ Hash đã cập nhật nhưng List chưa được cập nhật.
Publisher chia một payload lớn thành các lần gọi tối đa 100 candle, nên không có
atomicity toàn cục nếu một lần publish vượt giới hạn đó. Atomicity cũng không bảo vệ
trước writer ngoài Lua hoặc dữ liệu đã hỏng từ trước.

Event có dạng khái quát:

```json
{"symbol":"US30","timeframe":"H1","candles":[{ "bartime":"…", "open":0, "high":0, "low":0, "close":0, "volume":0 }]}
```

Payload là JSON và có thể chứa nhiều candle trong một batch. Vì vậy cần phân biệt:
**Hash/List không cần JSON parse; payload Pub/Sub vẫn cần JSON parse.**

### 5.2. Đối chiếu (reconcile): đường phục hồi từ SQL

`runtime.py` enqueue reconcile cho live pair lúc khởi động và theo
`reconcile_interval_seconds`. Đây là thời điểm đưa việc vào worker; không phải cam
kết job sẽ hoàn tất chính xác ở mốc đó. Worker đọc N nến gần nhất của từng pair trực
tiếp từ SQL, rồi `_RECONCILE_SCRIPT`:

1. Ghi/ghi đè Hash khi năm field OHLCV khác dữ liệu SQL.
2. Xoá Hash của epoch đang có trong List nhưng không còn trong cửa sổ SQL mong muốn.
3. Dựng lại List chỉ khi List khác thứ tự hoặc khác thành phần của cửa sổ SQL.

Reconcile không phát Pub/Sub event. Mục tiêu là đưa state Redis khớp SQL, không báo
cho OG mọi thao tác bảo trì.

Điểm giới hạn quan trọng: script chỉ duyệt các epoch trong List hiện có. Nó **không
SCAN toàn bộ candle hash**, nên không thể tự phát hiện hoặc xoá một Hash mồ côi không
còn được List tham chiếu. Ngoài ra, script chỉ so sánh OHLCV: một Hash có OHLCV đúng
nhưng field `bartime` bị thiếu hoặc sai sẽ không nhất thiết được script tự ghi lại.
Do đó “không có nến mồ côi” chỉ là bất biến đạt được khi tất cả writer tuân thủ Lua
contract; không phải bảo đảm tự sửa mọi dữ liệu rác.

### 5.3. Không chặn SQL, nhưng không phải hàng đợi bền

Khi một job worker lỗi, kể cả lỗi Redis lúc publish hoặc lỗi SQL lúc đọc reconcile,
worker mở circuit breaker trong thời gian cooldown cấu hình, bỏ client cũ và requeue
công việc trong RAM để thử lại. Đường SQL của live không chờ Redis và vẫn có thể tiếp
tục ghi.

Đổi lại, hàng đợi Redis không bền:

- DP crash hoặc bị buộc dừng có thể làm mất update đang chờ.
- Khi shutdown chờ quá thời hạn, code có thể bỏ các pair pending; startup reconcile
  được thiết kế để bù lại sau đó.
- Không có giới hạn dung lượng hàng đợi tường minh trong publisher; coalescing chỉ
  giảm các update trùng epoch, không biến nó thành durable queue.
- Reconcile và incremental dùng chung một worker. Incremental được chọn trước khi
  reconcile trong hàng đợi, nên một luồng update liên tục có thể làm reconcile trễ.

---

## 6. Đảm bảo, fallback và giới hạn

| Tình huống | DP xử lý hiện tại | Phần còn lại / rủi ro |
|---|---|---|
| Redis timeout hoặc không kết nối được | Worker requeue RAM, mở circuit breaker, SQL không chờ Redis | DP crash trước retry thì update RAM mất |
| DP restart sau khi mất update Redis | Reconcile lúc khởi động đọc lại cửa sổ SQL | Chỉ phục hồi khi SQL, Redis và worker hoạt động lại |
| Một Pub/Sub subscriber offline | Không replay event | OG phải đọc lại Hash/List khi khởi động, reconnect hoặc resync định kỳ |
| Nến đến muộn | Lua chèn epoch theo thứ tự | Chi phí dựng lại List tăng theo kích thước cửa sổ, hiện được giới hạn bởi cấu hình |
| Nến revise | HSET đúng Hash epoch; List không tạo epoch trùng | Event chỉ có khi OHLCV canonical đổi |
| Writer ngoài contract ghi `dp:candles:*` | Không có cơ chế chặn ở Redis trong code DP | Có thể tạo key rác hoặc phá bất biến; cần quyền ghi/prefix ownership và giám sát vận hành |
| Redis đầy hoặc lỗi script | Worker coi là lỗi publish và retry theo circuit breaker | SQL vẫn là nguồn dữ liệu, nhưng Redis/OG có thể chậm cho đến khi lỗi được xử lý |

`state_probe.py` kiểm tra cấu trúc các pair có List: thứ tự epoch, duplicate, hash
thiếu OHLCV, số không hợp lệ, overflow và độ cũ. Nó có SQL cross-check lấy mẫu. Cờ
`stale` chỉ nói nến mới nhất cũ hơn ngưỡng; muốn kết luận do thị trường đóng hay do
pipeline lỗi phải đối chiếu thêm lịch giao dịch, provider và SQL. Probe hiện không
phải trình dò key hash mồ côi ngoài List, không tự thấy một pair mất hẳn `:order`, và
không coi riêng field `bartime` bị thiếu là lỗi cấu trúc.

---

## 7. Contract đọc dữ liệu dành cho OG

Trước khi đọc, OG nên kiểm tra `GET dp:candles:schema` bằng `"2"`. Nếu khác phiên
bản, OG nên dừng hoặc báo lỗi rõ ràng thay vì đọc sai schema trong im lặng.

Để lấy N close mới nhất cho một pair:

```python
from decimal import Decimal

def latest_closes(redis_client, symbol: str, timeframe: str, n: int) -> list[Decimal]:
    base = f"dp:candles:{timeframe}:{symbol}"
    epochs = redis_client.lrange(f"{base}:order", -n, -1)
    pipe = redis_client.pipeline(transaction=False)
    for epoch in epochs:
        pipe.hget(f"{base}:{epoch}", "close")
    return [Decimal(value) for value in pipe.execute() if value is not None]
```

Đây là hai lượt mạng: một lượt lấy epoch từ List, một lượt pipeline lấy field từ các
Hash. Số lượt mạng không tăng theo N, nhưng tổng số lệnh và số byte vẫn tăng theo số
nến đọc. Với indicator chỉ cần `close`, không nên lấy cả OHLCV. Với ATR, dùng
`HMGET high low close` cho từng Hash trong pipeline.

`epoch` trong List/key là mốc thời gian chuẩn để tính cửa sổ. `bartime` là field mô
tả thuận tiện cho con người; không nên dùng nó làm identity chính. Dùng `Decimal`
nếu chiến lược cần chính xác theo số thập phân warehouse; chỉ dùng `float` nếu sai số
nhị phân nhỏ chấp nhận được với chỉ báo đó.

### Dùng Pub/Sub đúng vai trò

OG subscribe `dp:events:candles` để biết pair nào cần xử lý. Event không phải nguồn
dữ liệu bền và không thay thế việc đọc Hash/List.

Phương án an toàn, đơn giản nhất là: khi nhận event của một pair, OG đọc lại cửa sổ
cần thiết của **riêng pair đó**, rồi tính lại signal. Cách này chịu được revision và
không yêu cầu OG tự xử lý gap phức tạp.

Nếu OG giữ cửa sổ trong RAM để giảm đọc Redis, nó phải xử lý cả hai trường hợp: epoch
mới tiếp theo và revision của epoch đã có. Khi phát hiện thiếu epoch, dữ liệu không
liên tục, reconnect hoặc restart, OG cần tải lại cửa sổ từ Redis. Một vòng resync
định kỳ là lớp bảo vệ bổ sung cho việc mất Pub/Sub event.

Repository DP hiện không có source code OG, nên không thể xác minh công thức indicator,
quy tắc vào lệnh hoặc quy tắc chuyển signal thành order. Các phần đó phải được mô tả
và kiểm thử trong repository OG, tách khỏi contract Redis của DP.

---

## 8. Những điều cơ chế này không đảm bảo

- Không bảo đảm OG nhận đủ mọi event Pub/Sub.
- Không lưu lịch sử đầy đủ trong **cửa sổ được List lập chỉ mục**; lịch sử nằm trong
  SQL Server. Một Hash rác ngoài List là lỗi vận hành riêng mà publisher không tự dọn.
- Không cung cấp durable queue hoặc exactly-once delivery cho candle event.
- Không tự dọn mọi Hash mồ côi do writer ngoài contract tạo ra.
- Không hỗ trợ Redis Cluster ở dạng script/key hiện tại: script tạo candle key động,
  đồng thời dùng order key và event channel. Redis Cluster yêu cầu các key script truy
  cập phải được khai báo và cùng slot; cần thiết kế lại đầy đủ, không chỉ thêm một
  hash tag cho candle key.
- Không chứng minh correctness của chiến lược OG hoặc quy trình đặt lệnh OF.

Đây là các lựa chọn phạm vi có chủ đích: DP tối ưu cho một bản trạng thái nến mới nhất
đọc nhanh, còn SQL chịu trách nhiệm về tính bền và lịch sử. Nếu chặng OG → OF cần
giao lệnh bền, cần thiết kế riêng bằng Stream, consumer group và idempotency; đó không
phải là tính năng mà publisher candle DP đang cung cấp.

## 9. Checklist vận hành tối thiểu

1. Xác nhận schema Redis là `2` trước khi OG chạy.
2. Giám sát log `REDIS_PUBLISH_FAILED`, `REDIS_PUBLISH_RECOVERED` và
   `REDIS_RECONCILE_COMPLETED` của DP.
3. Chạy `state_probe` để kiểm tra các pair có List; không diễn giải `stale` thành lỗi
   dữ liệu nếu chưa đối chiếu lịch thị trường và SQL.
4. Khi có nghi ngờ dữ liệu rác ngoài List, kiểm tra keyspace theo prefix bằng một công
   cụ read-only riêng; reconcile thông thường không thay thế bước này.
5. Khi ghi nhận số liệu production, lưu timestamp UTC, Redis DB, command, output đã
   che secret và log ID để người khác tái kiểm chứng được.
