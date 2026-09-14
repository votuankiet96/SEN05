# Cấu trúc dữ liệu Redis của DP — thiết kế, luồng chạy, và giới hạn

> Trạng thái: đã triển khai production lúc **2026-09-14 04:45 UTC**.
> Đối tượng đọc: người thiết kế/vận hành hệ thống auto trading.

---

## 1. Redis đứng ở đâu trong hệ thống

Ba chương trình, chỉ nói chuyện qua Redis:

```
TradingView ──► DP ──► SQL Server ──► Redis ──► OG ──► (OF, chưa xây)
                       (nguồn bền)   (bản chụp)  (tính signal)
```

Phân vai dứt khoát, và mọi quyết định thiết kế bên dưới đều suy ra từ đây:

| | SQL Server | Redis |
|---|---|---|
| Vai trò | Nguồn sự thật bền | Bản chụp cửa sổ gần nhất |
| Giữ gì | Toàn bộ lịch sử | 100 nến mới nhất mỗi pair |
| Mất dữ liệu thì sao | Mất thật | Dựng lại được từ SQL |
| Ai ghi | DP | Chỉ DP |
| Ai đọc | DP, công cụ phân tích | OG |

**Hệ quả quan trọng nhất:** Redis **không được phép** là một nhánh dữ liệu song
song với SQL. Nếu Redis có thể khác SQL mà không ai biết, thì OG đang tính signal
trên một thực tại thứ hai. Vì vậy mọi giá trị DP ghi lên Redis đều **đọc lại từ
SQL trước khi ghi** — kể cả trên đường live, nơi DP đã có sẵn giá trị trong RAM.

---

## 2. Contract dữ liệu

```
LIST  CANDLE:{SYMBOL}_{TIMEFRAME}
        ["2026-07-29_04-00-00", "2026-07-29_05-00-00", "2026-07-29_06-00-00"]

HASH  CANDLE:{SYMBOL}_{TIMEFRAME}:{YYYY-MM-DD_HH-MM-SS}
        timestamp   1785301200            epoch giây UTC của open time
        datetime    2026-07-29 06:00:00   cùng mốc, dạng người đọc
        open        100.00000000          DECIMAL scale 8
        high        102.00000000
        low          99.00000000
        close       101.00000000
        volume       12.5000              DECIMAL scale 4, hoặc "null"
        source      CAPITALCOM:BTCUSD     {BrokerChannel}:{Symbol}
        inserttime  2026-07-29 06:07:03   Fact_OHLCV.CreatedAt
```

Ba quy tắc, không có quy tắc thứ tư:

1. **Key Hash = key List + `":" + phần tử lấy từ List`.** Một phép nối.
2. **SYMBOL trước, TIMEFRAME sau**, nối bằng `_`.
3. **Mốc:** `YYYY-MM-DD_HH-MM-SS`, luôn UTC, không offset, rộng cố định.

Kiểm nhanh bằng mắt: key List có đúng **1** dấu `:`, key Hash có đúng **2**.

---

## 3. Vì sao thiết kế như vậy

### 3.1. Vì sao tách List và Hash thay vì một cấu trúc

Bài toán đọc của OG là: *"cho tôi N nến gần nhất của pair này"*. Nó cần hai thứ
mà không cấu trúc Redis đơn lẻ nào cho cả hai:

- **Thứ tự thời gian** → để lấy N cái cuối, chèn nến đến muộn, tỉa nến cũ.
- **Ghi đè được từng nến** → vì provider hiệu chỉnh nến đã đóng.

Hash cho cái thứ hai, không cho cái thứ nhất. List cho cái thứ nhất, không cho cái
thứ hai. Ghép lại: **List giữ thứ tự và phạm vi cửa sổ, Hash giữ nội dung.**

Đã cân nhắc và loại:

- **Stream**: là nhật ký append-only, hợp khi consumer cần ACK/replay từng event.
  Nến là *trạng thái*, không phải *mệnh lệnh* — SQL đã giữ lịch sử, OG chỉ cần cửa
  sổ hiện tại và đọc lại được bất cứ lúc nào. Dùng Stream buộc OG tự dựng trạng
  thái từ log và phải quản retention + consumer group. Thêm cơ chế, không thêm giá
  trị.
- **ZSet**: giữ được thứ tự, nhưng member phải là giá trị. Member là JSON OHLCV thì
  sửa giá sinh member mới; member là mốc thì vẫn cần nơi khác chứa OHLCV. Không
  loại được nhu cầu tách identity khỏi giá trị.

### 3.2. Vì sao mỗi nến một Hash riêng

Đo thực tế trên chính Redis này:

| Bố trí | Encoding | Bộ nhớ/nến |
|---|---|---|
| Một Hash lớn cho cả pair (500 nến × 5 field = 2500 field) | `hashtable` | ~378 B |
| Một Hash riêng mỗi nến (9 field) | `listpack` | ~200 B |

Redis giữ `listpack` khi Hash ≤ 128 field. Hash 9 field luôn nằm dưới ngưỡng. Ngoài
bộ nhớ, Hash riêng còn cho phép ghi đè đúng một nến mà không đụng nến khác, và cho
phép `HMGET` đúng field cần (ATR chỉ lấy `high/low/close`) thay vì `HGETALL` cả pair
rồi lọc phía client.

### 3.3. Vì sao mốc thời gian có hình dạng đó

Ba tính chất, mỗi cái giải một vấn đề cụ thể:

**Rộng cố định + đệm 0 + luôn UTC + không offset** → so sánh chuỗi trùng khít so
sánh thời gian. Đây không phải thẩm mỹ: Lua trong Redis dựa hẳn vào nó để chèn nến
đến muộn đúng vị trí và tỉa cửa sổ, chỉ bằng `<` và `>` trên chuỗi. Không dùng
`tonumber` vì `tonumber("2026-07-29_06-00-00")` trả `nil`. Chỉ cần một bản ghi lẫn
offset là vừa hỏng thứ tự vừa sinh key thứ hai cho cùng một nến.

**Giờ-phút-giây dùng `-` chứ không `:`** → Redis không có thư mục, nhưng mọi GUI đều
tách key theo `:` để dựng cây. Mốc dạng `HH:MM:SS` đẻ ra ba tầng thư mục rỗng cho
**mỗi** nến:

```
L_CANDLE_BTCUSD_D1 / 2026-06-05_21 / 00 / 00
```

Với 16.500 Hash thì cây key không đọc được bằng mắt. Đây là bài học phải trả giá:
thế hệ trước chỉ kiểm tính đúng đắn mà không kiểm **nó hiển thị ra sao trong công
cụ mà người vận hành thực sự dùng**. Đổi sang `-` giữ nguyên mọi tính chất logic,
chỉ dời dấu `:` về đúng hai chỗ mang nghĩa phân cấp.

**Dạng người đọc chứ không phải epoch** → mở GUI lên là hiểu ngay đang nhìn nến
nào, không cần công cụ giải mã. Epoch vẫn có, nằm trong field `timestamp`.

### 3.4. Vì sao Hash có 9 field

5 field OHLCV là dữ liệu. 4 field còn lại giải quyết vấn đề vận hành cụ thể:

- `timestamp` + `datetime`: hai cách biểu diễn cùng một open time. Consumer chọn
  cái tiện — khỏi phải parse chuỗi hay tự đổi epoch. Cả hai sinh ra từ đúng một
  nguồn nên không thể lệch khỏi tên key.
- `source` (`CAPITALCOM:BTCUSD`): biết nến đến từ kênh broker nào. Khi có nhiều
  nguồn giá, đây là thứ phân biệt.
- `inserttime` (`Fact_OHLCV.CreatedAt`): biết row vào SQL lúc nào. Dùng để trả lời
  *"nến này DP lấy về trễ bao lâu so với lúc nó đóng"* — chỉ số vận hành thật, và
  là bằng chứng khi cần đối chiếu sự cố.

`inserttime` **phải lấy từ SQL**, không được lấy thời điểm publish Redis. Hai mốc
đó khác nhau ở mọi đường không phải live (spool replay, backfill, reconcile), và
nếu bịa thì nó thành một con số vô nghĩa trông như có nghĩa.

### 3.5. Vì sao dùng Lua

Một lần cập nhật phải **atomic với bên đọc**. Không được để OG nhìn thấy Hash đã
ghi mà List chưa trỏ tới, hoặc List trỏ tới nến mà Hash chưa tồn tại. Redis chạy
mỗi script Lua như một đơn vị không bị chen ngang, nên toàn bộ so sánh → ghi →
chèn → tỉa → publish của một batch nằm gọn trong một script.

Đây cũng là lý do bất biến **"không có nến mồ côi"** thành lập: eviction `LPOP` mốc
ra khỏi List và `DEL` key Hash tương ứng **trong cùng một script**. Không có khoảng
thời gian nào mà mốc đã rời List trong khi Hash còn sống.

### 3.6. Vì sao so sánh trước khi ghi

Script đọc 5 field OHLCV cũ bằng `HMGET`, chỉ `HSET` khi giá trị canonical **thực
sự khác**, và chỉ nến đổi mới vào payload event. Hệ quả: ghi lại y hệt không sinh
event, nên OG không bị đánh thức vô ích mỗi chu kỳ live cho hàng trăm nến không đổi.

Chỉ 5 field OHLCV tham gia so sánh. `timestamp`/`datetime`/`source` là hàm thuần
của key và symbol nên không thể lệch; `inserttime` đi theo đúng row SQL đã sinh ra
OHLCV đó.

---

## 4. Luồng chạy

### 4.1. Hai đường ghi

**Đường incremental (nhanh, có event).** Sau khi một pair live ghi SQL thành công:

```
live.py: fetch_and_store() → SQL commit
   → publish_candle_update(config, symbol_id, symbol, tf, source, delivered_candles)
   → enqueue: CHỈ giữ lại các MỐC thời gian, bỏ giá trị provider trong RAM
   → (thread nền) đọc lại đúng các row đó từ Fact_OHLCV
   → Lua: so sánh → HSET cái đổi → chèn mốc vào List → tỉa cửa sổ → PUBLISH
```

Caller không chờ I/O Redis. `publish_candle_update()` chỉ bỏ việc vào dict trong
RAM rồi `notify()`. Một thread nền riêng mới chạm SQL và Redis.

Chỉ lấy `window` mốc mới nhất (`bartimes[-window:]`) trước khi đọc SQL. Nến cũ hơn
cửa sổ sẽ bị eviction xoá ngay trong cùng script, nên ghi rồi phát event cho chúng
là vô nghĩa — và việc cắt này cũng chặn luôn rủi ro vượt trần 2100 tham số của SQL
Server khi catch-up dài.

**Đường reconcile (chậm, không event).** Lúc khởi động và mỗi 30 phút:

```
runtime.py → reconcile_all_live_pairs(config, live_pairs)
   → đọc 100 nến mới nhất của CẢ 165 pair trong MỘT query SQL
   → Lua (pipeline 20 pair/lượt): so sánh từng nến → HSET cái khác
                                → DEL Hash của mốc không còn trong cửa sổ SQL
                                → dựng lại List CHỈ KHI khác thứ tự/thành phần
```

Reconcile **không publish**. Mục tiêu là đưa state về khớp SQL, không phải báo cho
OG mọi thao tác bảo trì.

### 4.2. Đường đọc của OG

Cố định 2 round-trip bất kể N, không parse gì:

```python
list_key = f"CANDLE:{symbol}_{timeframe}"
stamps = r.lrange(list_key, -n, -1)               # round-trip 1
pipe = r.pipeline(transaction=False)
for stamp in stamps:
    pipe.hget(f"{list_key}:{stamp}", "close")      # key = list_key + ":" + stamp
closes = [float(v) for v in pipe.execute()]        # round-trip 2
```

Số round-trip không tăng theo N. Tổng byte thì có — nên chỉ báo cần `close` thì
đừng kéo cả 9 field.

### 4.3. Đường tín hiệu

Channel `dp:events:candles`, mỗi message một pair:

```json
{"symbol":"BTCUSD","timeframe":"H1","candles":[
  {"bartime":"2026-07-29_06-00-00","open":100.0,"high":102.0,
   "low":99.0,"close":101.0,"volume":12.5}]}
```

`bartime` dùng đúng mốc của key Hash nên consumer nối thẳng ra key được.

**Pub/Sub là tín hiệu, không phải nguồn dữ liệu.** Fire-and-forget: subscriber mất
kết nối là mất message, không có cách lấy lại. Cách dùng đúng: nhận event của pair
nào thì **đọc lại cửa sổ của riêng pair đó** rồi tính lại. Cách này chịu được cả
revision lẫn mất message.

### 4.4. Chống lỗi

Worker lỗi — kể cả lỗi Redis lúc ghi hay lỗi SQL lúc đọc — thì mở circuit breaker
trong `circuit_cooldown_seconds`, bỏ client cũ, và **giữ lại công việc trong RAM để
thử lại**. Đường ghi SQL của live không chờ Redis và vẫn chạy bình thường.

Đổi lại, hàng đợi Redis **không bền**: DP crash thì update đang chờ mất. Reconcile
lúc khởi động có nhiệm vụ bù lại.

---

## 5. Bảo đảm gì, và không bảo đảm gì

**Bảo đảm:**

- Không có nến mồ côi, miễn là mọi writer đi qua Lua contract.
- List luôn tăng dần, kể cả khi nến đến muộn.
- Nến hiệu chỉnh ghi đè tại chỗ, không sinh bản trùng.
- Mọi giá trị trên Redis đều là bản sao của một row SQL cụ thể.
- Redis không thể lệch SQL vĩnh viễn — reconcile 30 phút/lần kéo về.

**Không bảo đảm:**

- **OG nhận đủ mọi event.** Mất message là bình thường; phải đọc lại Hash/List.
- **Redis khớp SQL tại mọi thời điểm.** Xem mục 6.
- **Dọn key của pair đã rời universe live.** Reconcile chỉ chạy cho pair đang live
  và chỉ duyệt theo List; pair bị loại khỏi universe sẽ để lại List + 100 Hash nằm
  vĩnh viễn. Không có TTL trên bất kỳ key nào.
- **Redis Cluster.** Script ghép key nến động từ prefix thay vì khai báo qua
  `KEYS[]`; Cluster yêu cầu mọi key script chạm phải được khai báo và cùng slot.
- **Hàng đợi bền hay exactly-once.**

---

## 6. Độ trễ giữa SQL và Redis — cái cần biết khi vận hành

Redis **không** luôn khớp SQL tức thì. Có ba nguồn trễ, tất cả đều tự lành, nhưng
phải biết để không chẩn đoán nhầm:

**(a) Backfill và spool replay không publish.** Chỉ `live.py` gọi
`publish_candle_update()`. Nến do backfill (rolling repair) hoặc spool replay ghi
vào SQL chỉ tới Redis ở lần reconcile kế tiếp — **trễ tối đa 30 phút**.

**(b) Reconcile có thể kéo Redis lùi lại một nhịp.** Reconcile đọc một ảnh chụp SQL
rồi ép List khớp ảnh đó. Nếu một nến được commit vào SQL *trong lúc* reconcile đang
đọc, thì bản incremental vừa ghi có thể bị reconcile ghi đè bằng ảnh cũ hơn.

Quan sát thật ngay sau deploy 04:45 UTC:

```
FR40 M5 bar 04:35+04:40  → CreatedAt 04:45:50.86   (trong lúc reconcile doc)
DE40 M5 bar 04:35+04:40  → CreatedAt 04:45:51.93   (trong lúc reconcile doc)
US30 M5 bar 04:35+04:40  → CreatedAt 04:46:01.02   (sau khi reconcile xong)
reconcile khoi dong      → hoan tat 04:45:52
```

Kết quả: US30 có đủ nến trên Redis ngay; FR40/DE40 thiếu 2 nến cho tới lần reconcile
sau. Đây là đặc tính của thiết kế, không phải lỗi — nhưng nó có nghĩa là **ngay sau
mỗi lần restart, một vài pair có thể thiếu nến mới nhất trong tối đa 30 phút**.

**(c) Nến đã đóng vẫn bị hiệu chỉnh.** Provider sửa volume của nến cũ; backfill ghi
vào SQL; Redis chờ reconcile. Quan sát thật: `FR40 H6 2026-09-11_18-00-00` có
volume Redis 1666 / SQL 1665 tại 04:49 UTC.

**Cách chẩn đoán đúng:** Redis lệch SQL một vài nến ở vài pair, ngay sau restart
hoặc trong vòng 30 phút — là bình thường. Lệch kéo dài qua hai chu kỳ reconcile mới
là bất thường, lúc đó xem log `REDIS_PUBLISH_FAILED` và `REDIS_RECONCILE_COMPLETED`.

---

## 7. Số liệu thực đo sau deploy (2026-09-14 04:48 UTC)

```
DBSIZE                              16.665
  List (1 dấu ':')                     165   = đúng 165 live pair
  Hash (2 dấu ':')                  16.500   = 165 × 100 nến
  key ngoài prefix                       0
Hash mồ côi (không List nào trỏ tới)     0
phần tử List trỏ hụt (Hash đã mất)       0
Hash sai bộ 9 field                      0
Hash sai kiểu giá trị                    0
List trùng mốc / sai thứ tự / vượt cửa sổ / sai định dạng mốc   0
encoding Hash / List             listpack / listpack
reconcile khởi động: 165 pair, 16.500 hash, 165 list, 1.797 giây
```

Đối chiếu với SQL: 156/165 pair khớp tuyệt đối; 9 pair lệch đúng như mô tả ở mục
6(b), tự lành ở reconcile kế tiếp.

---

## 8. Tham số vận hành

| Tham số | Giá trị | Ý nghĩa |
|---|---|---|
| `bars_per_snapshot` | 100 | Cửa sổ mỗi pair. Đừng nâng lên hàng nghìn: Lua dùng `unpack()` khi dựng lại List, có trần stack ~8000 phần tử |
| `reconcile_interval_seconds` | 1800 | Quyết định trần độ trễ ở mục 6(a) và 6(c) |
| `timeout_seconds` | 0.3 | Socket timeout. Gắt — một lô có thể làm 100 `HMGET` + 100 `HSET`. Chưa đo thời gian chạy thật dưới tải |
| `circuit_cooldown_seconds` | 30 | Thời gian mở mạch sau lỗi |
| `key_prefix` | `CANDLE` | Đổi giá trị này là đổi contract; phải đồng bộ với OG |

---

## 9. Vị trí mã nguồn

| Thành phần | File |
|---|---|
| Toàn bộ logic publish + 2 script Lua | `src/dp_program/util/redis_publisher.py` |
| Điểm gọi incremental (duy nhất) | `src/dp_program/engine/live.py` |
| Reconcile khởi động + định kỳ | `src/dp_program/engine/runtime.py` |
| Serialize DECIMAL, 2 hàm đọc nến | `src/dp_program/engine/sql_connector.py` |
| Test khoá contract | `test/test_redis_publisher_regression.py` |
| Contract gửi OG | `docs/OG_REDIS_SYNC_PROMPT.md` |
