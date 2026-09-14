# Hồ sơ audit — cơ chế DP publish nến lên Redis

> Tài liệu này viết cho **một bên thứ ba audit độc lập**, không tham gia quá
> trình thiết kế và triển khai. Mục tiêu là cung cấp đủ bối cảnh để đánh giá
> chéo mà không phải tin vào lời của người đã làm.
>
> Người viết là agent đã thực hiện phần triển khai này. Vì vậy mục 13 (giới hạn
> và điểm đáng ngờ) được viết thẳng, gồm cả những chỗ tự thấy yếu — nhưng vẫn
> nên đọc với giả định người viết có điểm mù.
>
> Trạng thái repo tại thời điểm viết: nhánh `v5-redis`, commit `35ab215`,
> working tree sạch. Mọi số liệu đo lúc **2026-09-14 01:52 UTC**.

---

## 1. Phạm vi audit

**Trong phạm vi:** toàn bộ chặng DP → Redis. Cụ thể là
`core_program/src/dp_program/util/redis_publisher.py` (524 dòng), phần config
Redis trong `configuration.py`, hai điểm gọi trong `engine/live.py` và
`engine/runtime.py`, và hai hàm đọc SQL trong `engine/sql_connector.py`.

**Ngoài phạm vi:** chiến lược tính signal của OG, cách OG đọc Redis (đã audit
riêng), SQL Server schema, cơ chế fetch từ TradingView.

**Câu hỏi cần trả lời:** cấu trúc dữ liệu hiện tại trên Redis có đúng, đủ, gọn
và an toàn không? Có lỗi tiềm ẩn nào chưa lộ ra không?

---

## 2. Bối cảnh hệ thống

Ba chương trình độc lập, chỉ giao tiếp qua Redis:

| | Chương trình | Máy | Vai trò |
|---|---|---|---|
| **DP** | `dp_program_v3` | VM-DP6 (`10.11.12.6`, Windows) | Lấy nến từ TradingView → ghi SQL Server → đẩy sang Redis |
| **OG** | `og_program` | VM-OG8 (`10.11.12.8`, Ubuntu) | Đọc nến từ Redis → tính indicator → sinh signal |
| **OF** | chưa xây | — | Sẽ đọc signal → đặt lệnh |

Redis server chạy **trên chính VM-OG8** (`10.11.12.8:6379`), Redis 8.0.5.
DP kết nối qua mạng, OG kết nối qua `127.0.0.1`.

Luồng dữ liệu:

```
TradingView (WebSocket)
   │
   ▼
DP: fetch_and_store()
   │
   ├──► SQL Server (10.11.12.6) — DWH.Fact_OHLCV — NGUỒN DỮ LIỆU BỀN
   │
   └──► chỉ khi SQL thành công: enqueue vào worker Redis (in-RAM)
                │
                ▼
        worker nền + Lua script
                │
        ┌───────┴────────┐
        ▼                ▼
  Hash + List      PUBLISH dp:events:candles
        │                │
        └────────► OG đọc lại pair vừa đổi
```

Ngoài ra DP còn chạy **reconcile**: đọc lại N nến mới nhất từ SQL cho toàn bộ
live pair và ép Redis khớp SQL — lúc khởi động và định kỳ 30 phút một lần.

**Nguyên tắc phân vai:** SQL Server là nguồn sự thật bền và giữ toàn bộ lịch sử.
Redis chỉ là bản chụp cửa sổ gần nhất để OG đọc nhanh. Mất dữ liệu trên Redis
không phải mất dữ liệu — reconcile dựng lại được từ SQL.

**Quy mô hiện tại:** 11 symbol × 15 timeframe = **165 pair**, mỗi pair giữ
**100 nến** → 165 List + 16.500 Hash = 16.665 key.

---

## 3. Ràng buộc bất biến (không được vi phạm khi sửa)

Trích từ `core_program/AGENTS.md`, đây là các ràng buộc kiến trúc có từ trước:

1. **Redis không bao giờ được chặn đường ghi SQL.** SQL là nguồn bền; nếu Redis
   chậm hoặc chết, việc ghi SQL vẫn phải chạy bình thường.
2. **Chỉ publish sau khi SQL ghi thành công.** Không bao giờ để Redis "biết"
   một nến mà SQL chưa cam kết.
3. `util/` chỉ được có đúng 3 file Python; `redis_publisher.py` là một trong ba.
   Không được tách thêm file.
4. Mọi truy cập SQL chỉ nằm trong `engine/sql_connector.py`.
5. Mọi file tối đa 300 dòng code (không tính dòng trống và comment nguyên dòng).
6. Comment giải thích viết tiếng Việt; code/log/identifier viết tiếng Anh.
7. Giá trị số phải giữ đúng contract `DECIMAL` của warehouse, **không đi qua
   `float`** — tránh sinh thay đổi giả do làm tròn nhị phân.

---

## 4. Lịch sử: ba thế hệ cấu trúc

Quan trọng để hiểu vì sao cấu trúc hiện tại trông như vậy.

### Thế hệ 1 (bỏ)

Một Hash duy nhất cho mỗi pair, field đặt tên ghép:

```
HASH dp:candles:{tf}:{symbol}:data
     1788876000:o = 52880.1
     1788876000:h = 52906.1
     ...
```

**Vấn đề:** Hash lớn (500 nến × 5 field = 2500 field) vượt ngưỡng 128 field nên
Redis chuyển encoding từ `listpack` sang `hashtable` — đo được **378 B/nến**.
Đọc N nến gần nhất phải `HGETALL` toàn bộ rồi lọc phía client. Không có thứ tự
thời gian tường minh.

### Thế hệ 2 (bỏ)

Tách mỗi nến thành một Hash riêng, thêm List làm chỉ mục:

```
STRING dp:candles:schema = "2"
LIST   dp:candles:{tf}:{symbol}:order   [epoch, epoch, ...]
HASH   dp:candles:{tf}:{symbol}:{epoch}
       bartime <ISO text>
       open/high/low/close/volume
```

Giải quyết được vấn đề encoding (Hash 6 field → `listpack`). Nhưng còn:
- Key dùng **epoch số**, người đọc bằng mắt không hiểu là thời điểm nào.
- Tên List (`...:order`) khác gốc tên key Hash (`...:{epoch}`) → phải nhớ hai
  quy ước ghép key.
- **Timeframe đứng trước symbol** — ngược với cách người vận hành đọc.
- Có field `bartime` **trùng lặp** với thông tin đã nằm trong tên key, và từng
  gây sự cố thật: hai đường ghi (incremental / reconcile) serialize `bartime`
  khác định dạng (`...+00:00` vs không có offset), làm `pandas.to_datetime` phía
  OG suy ra sai format và biến các dòng thiểu số thành `NaT`.
- Có key đánh số phiên bản schema, và OG kiểm nó lúc khởi động.

### Thế hệ 3 (hiện tại)

Do người dùng chỉ định trực tiếp — xem mục 5.

---

## 5. Yêu cầu người dùng giao (nguyên văn)

Yêu cầu gốc, nguyên văn tiếng Việt:

> Cấu trúc dữ liệu trên Redis:
> - List sẽ chứa Hash, giá trị của List chính là timestamp (open time) đổi về
>   định dạng date & time rút gọn (không có chữ T ở giữa)
> - List sẽ có định dạng chung: `L_CANDLE_SYMBOL_TIMEFRAME`
> - Trong hash, chứa dữ liệu OHLCV sẽ là: `L_CANDLE_SYMBOL_TIMEFRAME:timestamp`

Sau khi tôi hỏi lại ba điểm chưa rõ, người dùng chốt:

1. **Dấu phân tách giữa ngày và giờ: gạch dưới `_`** ("Gạch dưới nhé") — thay
   vì dấu cách hoặc chữ `T`.
2. **Bỏ hậu tố timezone offset** ("Ok bỏ nhé") — không còn `+00:00`.
3. **Không có key đánh số phiên bản schema** ("Ko cần schema 3, đây là cấu trúc
   đã chốt và duy nhất sẽ dùng").

Và yêu cầu vận hành kèm theo:

> Khi chạy xong hoàn thiện, bạn hãy viết cho tôi prompt để phía OG tiến hành
> đồng bộ nhé. **Yêu cầu OG clear luôn cả data cũ trên redis.**

Yêu cầu nền, từ các lượt trước đó trong cùng dự án:

> làm sao để toàn diện và hoàn thiện, bao gồm cả **ko parse** / OG thì sẽ lấy
> n nến theo symbol/timeframe/indicator để tính chứ sao? ví dụ ma 20 thì cần 20
> nến gần nhất, atr 5 thì chỉ tính dựa trên 5 nến gần nhất

> list là dùng để truy vấn theo time series với thứ tự thời gian, còn hash là
> lưu được dữ liệu nến gồm OHLC, làm thế nào cách lưu thật sự gọn gàng mà phía
> og có thể truy vấn nhanh và chính xác vào đó, nhưng **hiển thị vẫn tươm tất**

---

## 6. Cách tôi hiểu yêu cầu

Diễn giải của tôi — đây là chỗ đầu tiên có thể sai, nên auditor cần kiểm lại:

| Yêu cầu | Tôi hiểu là | Hệ quả triển khai |
|---|---|---|
| "List chứa Hash" | List không chứa dữ liệu giá, nó chứa **khoá trỏ tới** Hash; List là chỉ mục thời gian | List chỉ giữ chuỗi mốc thời gian |
| "giá trị của List chính là timestamp (open time)" | Phần tử List = thời điểm **mở** nến, không phải thời điểm đóng | Dùng `BarTime` của warehouse, vốn là open time |
| "định dạng date & time rút gọn (không có chữ T)" | Người dùng muốn **đọc được bằng mắt** trên Redis GUI, không phải epoch số | `YYYY-MM-DD_HH:MM:SS` |
| "`L_CANDLE_SYMBOL_TIMEFRAME`" | SYMBOL **trước**, TIMEFRAME **sau** — đảo so với thế hệ 2 | `L_CANDLE_US30_H1` |
| "`L_CANDLE_SYMBOL_TIMEFRAME:timestamp`" | Key Hash = **đúng tên List** nối thêm `":" + mốc` | Một phép nối duy nhất |
| "ko parse" | Phía OG không phải `json.loads` hay tách chuỗi để lấy giá | Hash 5 field phẳng, giá trị là chuỗi số trần |
| "hiển thị vẫn tươm tất" | Mở Redis GUI lên phải hiểu ngay, không cần công cụ giải mã | Mốc dạng người đọc được; Hash 5 dòng rõ ràng |

**Suy luận thêm mà người dùng không nói ra, tôi tự quyết:**

Vì mốc thời gian rộng cố định, có đệm 0 và luôn UTC, nên **so sánh chuỗi trùng
khít với so sánh thời gian**. Tôi quyết định dựa hẳn vào tính chất này trong Lua
(dùng `<` / `>` trên chuỗi thay vì `tonumber`), vì `tonumber("2026-09-08_14:00:00")`
trả `nil`. Đây là quyết định kỹ thuật quan trọng nhất **không** nằm trong yêu cầu
gốc, và nó biến ba tính chất "UTC / không offset / rộng cố định" từ thẩm mỹ thành
**điều kiện đúng đắn bắt buộc**. Nếu một bản ghi lẫn offset, thứ tự sẽ sai.

---

## 7. Quyết định thiết kế và lý do

### 7.1. Vì sao Hash + List chứ không phải Stream / ZSet / JSON

- **Không dùng Stream:** Stream là nhật ký append-only, hợp khi consumer cần
  ACK / replay / consumer-group. Nến ở đây là **trạng thái**, không phải mệnh
  lệnh: SQL đã giữ lịch sử, OG chỉ cần cửa sổ mới nhất và có thể đọc lại bất cứ
  lúc nào. Dùng Stream sẽ buộc OG tự dựng trạng thái hiện tại từ log.
- **Không dùng ZSet làm cấu trúc chính:** ZSet giữ được thứ tự, nhưng member
  phải là giá trị. Nếu member là JSON OHLCV thì sửa giá sinh member mới; nếu
  member là mốc thời gian thì vẫn cần nơi khác chứa OHLCV. Không loại được nhu
  cầu tách identity khỏi giá trị.
- **Chọn Hash + List:** Hash giữ **nội dung** (ghi đè từng field khi nến được
  hiệu chỉnh); List giữ **thứ tự và phạm vi cửa sổ** (lấy N nến cuối, chèn nến
  đến muộn, tỉa nến cũ).

### 7.2. Vì sao mỗi nến một Hash riêng

Đo thực nghiệm trên chính Redis này:

| Bố trí | Encoding | Bộ nhớ/nến |
|---|---|---|
| Một Hash lớn cho cả pair (500 nến × 5 field) | `hashtable` | ~378 B |
| Một Hash riêng mỗi nến (5 field) | `listpack` | ~200 B |

Redis giữ `listpack` khi Hash ≤ 128 field (`hash-max-listpack-entries`). Hash 5
field luôn nằm dưới ngưỡng. Kiểm lại hôm nay: `OBJECT ENCODING` của cả Hash và
List đều trả `listpack`.

### 7.3. Vì sao bỏ field `bartime` trong Hash

Mốc thời gian đã nằm trong tên key. Giữ thêm một bản trong Hash tạo ra **hai
nguồn sự thật cho cùng một thông tin**, và trong thế hệ 2 chúng đã thực sự lệch
nhau (xem mục 4). Bỏ hẳn field này là cách duy nhất khiến lỗi đó không thể tái
diễn — không phải chỉ sửa cho hai đường ghi cùng format.

### 7.4. Vì sao bỏ key đánh số schema

Người dùng chốt đây là cấu trúc duy nhất, không có thế hệ sau. Giữ lại một key
phiên bản chỉ tạo thêm một thứ phải đồng bộ. *(Ghi chú: việc bỏ key này đã gây
một sự cố vận hành phía OG — xem mục 12.3.)*

### 7.5. Vì sao dùng Lua thay vì pipeline nhiều lệnh

Một lần cập nhật một pair phải là **atomic với bên đọc**: không được để OG nhìn
thấy Hash đã ghi nhưng List chưa cập nhật, hoặc List đã trỏ tới nến mà Hash chưa
tồn tại. Redis chạy mỗi script Lua như một đơn vị không bị chen ngang, nên toàn
bộ so sánh–ghi–chèn–tỉa–publish của một batch nằm gọn trong một script.

### 7.6. Vì sao so sánh trước khi ghi

Script đọc 5 field cũ bằng `HMGET`, chỉ `HSET` khi **giá trị canonical thực sự
khác**, và chỉ đưa nến đó vào payload event. Hệ quả: ghi lại y hệt không sinh
event, nên OG không bị đánh thức vô ích mỗi chu kỳ live cho những nến không đổi.

### 7.7. Vì sao không chặn đường SQL

`publish_candle_update()` chỉ bỏ việc vào dict trong RAM rồi `notify()` — không
có I/O mạng. Một thread nền riêng mới kết nối Redis và chạy Lua. Kèm circuit
breaker: lỗi thì mở mạch trong `circuit_cooldown_seconds`, bỏ client cũ, giữ
lại công việc để thử lại.

---

## 8. Triển khai — bản đồ mã nguồn

### 8.1. File và vai trò

| File | Vai trò trong cơ chế này |
|---|---|
| `src/dp_program/util/redis_publisher.py` | Toàn bộ logic publish. 524 dòng. |
| `src/dp_program/configuration.py` (dòng 175-209) | Đọc và validate khối `redis:` |
| `src/dp_program/engine/live.py` (dòng 256) | Điểm gọi incremental duy nhất |
| `src/dp_program/engine/runtime.py` (dòng 205, 226, 275, 283) | Reconcile lúc khởi động + định kỳ, và flush lúc dừng |
| `src/dp_program/engine/sql_connector.py` (dòng 31-57, 361-417) | Serialize DECIMAL + hai hàm đọc cửa sổ nến |
| `test/test_redis_publisher_regression.py` | Test với Redis emulator ngữ nghĩa |
| `research/redis_probe/*.py` | 3 probe quan sát độc lập, chạy ngoài engine |

### 8.2. API công khai của `redis_publisher.py`

```python
publish_candle_update(config, symbol_id, symbol, tf_code, candles)  # enqueue delta
reconcile_pair(config, symbol_id, symbol, tf_code)                  # enqueue 1 pair
reconcile_all_live_pairs(config, pairs)                             # enqueue tất cả
wait_for_redis_idle(timeout_seconds) -> bool                        # chờ có giới hạn
shutdown_redis_publisher(timeout_seconds=10.0) -> bool              # flush khi dừng
```

### 8.3. Sinh mốc thời gian — `_stamp()`

```python
def _stamp(bartime: Any) -> str:
    if isinstance(bartime, datetime):
        value = (bartime.astimezone(timezone.utc) if bartime.tzinfo else bartime).replace(
            microsecond=0, tzinfo=None
        )
    else:
        value = datetime.fromtimestamp(int(float(bartime)), timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d_%H:%M:%S")
```

Đây là **hàm duy nhất** sinh mốc, dùng chung cho cả đường incremental lẫn
reconcile lẫn payload event. Đó là điểm sửa gốc cho lỗi lệch format của thế hệ 2.

Lưu ý: datetime **naive** được coi là **đã là UTC** và không convert (vì SQL
`DATETIME2(0)` của DP lưu UTC naive). Datetime **có tzinfo** thì convert sang UTC.

### 8.4. Sinh giá trị — `_candle_fields()` / `_candle_json()`

Giá trị đi qua `warehouse_value_signature()` trong `sql_connector.py`:

```python
prices = tuple(_decimal_text(v, precision=18, scale=8) for v in (open_, high, low, close))
normalized_volume = None if volume is None else _decimal_text(volume, precision=20, scale=4)
```

`_decimal_text()` dùng `Decimal(...).quantize(..., ROUND_HALF_UP)`, ném lỗi nếu
giá trị không hữu hạn hoặc tràn precision. **Không có bước nào đi qua `float`.**

`volume` là `None` → ghi chuỗi literal `"null"` vào Hash, và `null` (JSON null)
trong payload event.

### 8.5. Sinh key — `_keys()`

```python
base = f"{settings['key_prefix']}_{symbol}_{tf_code}"
return base, f"{base}:"          # (list_key, candle_prefix)
```

Trả về prefix chứ không trả từng key nến, vì số key mỗi lần gọi là động; Lua tự
ghép `candle_prefix .. stamp`.

---

## 9. Hai script Lua — giải thích chi tiết

### 9.1. `_INCREMENTAL_SCRIPT` — đường nhanh

**Đầu vào.** `KEYS[1]` = list_key, `KEYS[2]` = channel.
`ARGV[1]` = max_size, `ARGV[2]` = candle_prefix, `ARGV[3]` = tiền tố JSON của
event. Từ `ARGV[4]` trở đi, mỗi nến chiếm **7 ô**: stamp, open, high, low,
close, volume, payload JSON của nến đó.

```lua
for i = 4, #ARGV, 7 do
    local stamp = ARGV[i]
    local key = candle_prefix .. stamp
    local old = redis.call('HMGET', key, 'open','high','low','close','volume')
    local differs = false
    for n = 1, 5 do
        if old[n] ~= ARGV[i + n] then differs = true end
    end
    if differs then
        redis.call('HSET', key, 'open',ARGV[i+1], 'high',ARGV[i+2],
                   'low',ARGV[i+3], 'close',ARGV[i+4], 'volume',ARGV[i+5])
        table.insert(events, ARGV[i+6]); changed = changed + 1
    end
    if not redis.call('LPOS', list_key, stamp) then
        local tail = redis.call('LINDEX', list_key, -1)
        if not tail or stamp > tail then
            redis.call('RPUSH', list_key, stamp)
        else
            insert_sorted(stamp)
        end
        added = added + 1
    end
end
```

Ba điểm cần soi kỹ:

1. **`HMGET` trên key chưa tồn tại** trả về mảng `false` trong Lua (không phải
   `nil`), nên `old[n] ~= ARGV[i+n]` cho `true` → nến mới luôn được ghi. Đúng.
2. **`LPOS` trả `0` khi phần tử nằm ở đầu List.** Trong Lua `0` là **truthy**
   (khác Python), nên `if not redis.call('LPOS', ...)` hoạt động đúng. Đây là
   chỗ dễ sai nếu ai đó port sang ngôn ngữ khác.
3. **Đường nhanh vs đường chậm:** nến mới hơn đuôi List thì `RPUSH` O(1). Chỉ
   nến đến muộn mới đi qua `insert_sorted`.

**`insert_sorted`** — dựng lại toàn bộ List:

```lua
local current, rebuilt, inserted = redis.call('LRANGE', list_key, 0, -1), {}, false
for _, existing in ipairs(current) do
    if not inserted and stamp < existing then
        table.insert(rebuilt, stamp); inserted = true
    end
    table.insert(rebuilt, existing)
end
if not inserted then table.insert(rebuilt, stamp) end
redis.call('DEL', list_key)
redis.call('RPUSH', list_key, unpack(rebuilt))
```

`stamp < existing` là **so sánh chuỗi** — đúng thứ tự thời gian nhờ mốc rộng cố
định. Xem mục 13.2 về rủi ro của `DEL` rồi `RPUSH`.

**Tỉa cửa sổ và phát event:**

```lua
local evicted_count = math.max(0, redis.call('LLEN', list_key) - max_size)
if evicted_count > 0 then
    for _, bt in ipairs(redis.call('LPOP', list_key, evicted_count)) do
        redis.call('DEL', candle_prefix .. bt)
    end
end
if #events > 0 then
    redis.call('PUBLISH', channel, event_prefix .. table.concat(events, ',') .. ']}')
end
return {changed, added, evicted_count}
```

Việc `LPOP` mốc và `DEL` Hash tương ứng nằm trong **cùng một script** là lý do
bất biến "không có nến mồ côi" thành lập: không có cửa sổ thời gian nào mà mốc
đã rời List trong khi Hash còn sống.

`LPOP key count` cần **Redis ≥ 6.2**. Server đang chạy 8.0.5.

### 9.2. `_RECONCILE_SCRIPT` — đường phục hồi từ SQL

`KEYS[1]` = list_key. `ARGV[1]` = candle_prefix, rồi mỗi nến **6 ô** (không có
payload vì reconcile **không publish**).

Ba việc, theo thứ tự:

1. So từng nến SQL với Hash hiện có, chỉ `HSET` khi khác.
2. Duyệt List hiện tại, `DEL` Hash của mốc **không còn** trong cửa sổ SQL.
3. So List hiện tại với thứ tự mong muốn; **chỉ dựng lại List khi thực sự khác**.

```lua
local current, removed, rebuild = redis.call('LRANGE', list_key, 0, -1), 0, false
for _, stamp in ipairs(current) do
    if not desired[stamp] then
        redis.call('DEL', candle_prefix .. stamp); removed = removed + 1
    end
end
if #current ~= #desired_order then rebuild = true end
if not rebuild then
    for i = 1, #desired_order do
        if current[i] ~= desired_order[i] then rebuild = true; break end
    end
end
if rebuild then
    redis.call('DEL', list_key)
    if #desired_order > 0 then redis.call('RPUSH', list_key, unpack(desired_order)) end
end
```

**Giới hạn có chủ ý:** script chỉ duyệt các mốc **có trong List**. Nó không
`SCAN` keyspace, nên không tự phát hiện được một Hash mồ côi mà không List nào
trỏ tới. Bất biến "không mồ côi" chỉ đúng khi **mọi** writer đều đi qua Lua.

**Reconcile không phát event** — chủ ý: mục tiêu là đưa state về khớp SQL, không
phải báo cho OG mọi thao tác bảo trì.

---

## 10. Luồng vận hành đầy đủ

### 10.1. Đường incremental

1. `live.py` gọi `fetch_and_store()`, SQL commit xong.
2. Nếu `result["delivered_candles"]` không rỗng → `publish_candle_update(...)`.
   **Đây là điểm gọi incremental duy nhất trong toàn bộ codebase.**
3. `enqueue()` gộp vào `self._updates[(symbol_id, symbol, tf_code)]`, khoá theo
   `_stamp` — cùng một mốc enqueue nhiều lần thì bản cuối thắng.
4. Worker nền lấy job, `_pipeline_candles_to_args()` khử trùng và **sắp tăng
   dần**, chia lô 100 nến (`stride = 100 × 7`), gọi Lua từng lô.

### 10.2. Đường reconcile

- **Lúc khởi động** (`runtime.py:226`): `reconcile_all_live_pairs()` cho toàn bộ
  165 pair, rồi `wait_for_redis_idle()` với timeout `max(30, 165 × 0.3)` giây.
  Không xong thì log `REDIS_STARTUP_PENDING` và **vẫn chạy tiếp**.
- **Định kỳ** (`runtime.py:275, 283`): mỗi `reconcile_interval_seconds` = 1800s.
  Kiểm ở hai chỗ — sau mỗi cycle live, và trong vòng chờ giữa các cycle.
- Đọc SQL **một lần cho tất cả pair** qua `read_latest_candles_for_pairs()`
  (bảng tạm `#RedisPairs` + `CROSS APPLY TOP (?)`), rồi chạy Lua theo pipeline
  20 pair một lượt.

### 10.3. Lúc dừng

`runtime.py:205` gọi `shutdown_redis_publisher()`, chờ tối đa 10s cho worker
flush. Quá hạn thì bỏ pending, log `REDIS_SHUTDOWN_TIMEOUT`, và dựa vào
reconcile lúc khởi động lần sau để bù.

---

## 11. Cấu hình và hạ tầng thực tế

`config.yaml` khối `redis:` (đã che secret):

```yaml
redis:
  enabled: true
  host: "10.11.12.8"
  port: 6379
  db: 0
  username: ***
  password: ***
  timeout_seconds: 0.3
  circuit_cooldown_seconds: 30
  bars_per_snapshot: 100
  key_prefix: "L_CANDLE"
  event_channel: "dp:events:candles"
  reconcile_interval_seconds: 1800
```

Lưu ý: `configuration.py` để mặc định `bars_per_snapshot` là **500**, nhưng
config thật đang dùng **100**. Cửa sổ thật là 100.

Tham số Redis server (đọc qua `CONFIG GET`, 2026-09-14 01:52 UTC):

```
redis_version           : 8.0.5
maxmemory               : 0          (không giới hạn)
maxmemory-policy        : noeviction
notify-keyspace-events  : g$zK
```

---

## 12. Đã kiểm chứng những gì

### 12.1. Kiểm cấu trúc toàn bộ keyspace (2026-09-14 01:52 UTC)

Quét **mọi** key trong db0, không chỉ những key mà List trỏ tới:

```
DBSIZE                      : 16665
  type=list                 : 165
  type=hash                 : 16500
key không thuộc prefix      : 0
List có ':' trong tên       : 0
Hash không có ':' trong tên : 0

Hash được List lập chỉ mục  : 16500
Hash thực sự tồn tại        : 16500
Hash MỒ CÔI (không List nào trỏ tới) : 0
Phần tử List TRỎ HỤT (Hash đã mất)   : 0

Hash sai bộ field           : 0
Hash có OHLC không parse được : 0
List trùng mốc / sai thứ tự / vượt cửa sổ / sai định dạng mốc : 0
```

### 12.2. Đối chiếu với SQL, toàn bộ 165 pair (2026-09-14 01:52 UTC)

So từng pair: tập mốc, thứ tự mốc, và **từng giá trị OHLCV** (qua chính
`warehouse_value_signature`).

```
pair không có List trên Redis         : 0
pair lệch tập mốc / thứ tự so với SQL : 0
nến lệch giá trị OHLCV so với SQL     : 0
KẾT LUẬN: MATCH
```

### 12.3. Sự cố vận hành đã xảy ra khi cắt sang cấu trúc này

Khi cắt (2026-09-13 15:44 UTC) đã **flush sạch db0**. Hệ quả phía OG:

- Tiến trình `og_signal.live_worker` của OG khởi động từ 2026-09-08 16:13 UTC,
  chạy code thế hệ 2 trong RAM, **không được restart** sau khi DP cắt.
- Code đó gọi `check_schema_version()` **đúng một lần lúc khởi động**. Vì lúc
  khởi động key `dp:candles:schema` còn tồn tại, nó qua cửa kiểm tra và không
  bao giờ kiểm lại → khi key biến mất, nó **không crash, chỉ câm lặng**.
- Kết quả: 615 dòng log `Empty candle snapshot`, 0 signal, trong ~8 giờ. OG
  restart lúc 2026-09-14 00:03 UTC thì hết ngay.
- **18 signal mất vĩnh viễn** do hết cửa sổ hiệu lực trước khi OG được restart.

Đây là sự cố **vận hành**, không phải lỗi cấu trúc dữ liệu. Nhưng nó cho thấy
việc bỏ key schema (mục 7.4) có cái giá mà lúc thiết kế tôi không lường: nó lấy
đi cơ chế duy nhất để consumer **tự phát hiện** mình đang đọc sai thế hệ.

### 12.4. Kiểm khác đã chạy

- 190 test pass (`pytest test/`), gồm test emulator ngữ nghĩa cho cả hai script.
- Thử end-to-end trên Redis thật bằng key tạm: nến đến muộn được chèn đúng vị
  trí; eviction xoá **cả** phần tử List **và** Hash; ghi lại y hệt **không** sinh
  event.
- `OBJECT ENCODING` cả Hash và List đều `listpack`; đo 200 B/nến.
- Kiểm so sánh chuỗi cho đúng thứ tự thời gian ở các mốc vượt ngày/tháng/năm.

---

## 13. Giới hạn đã biết và điểm đáng ngờ

**Đây là mục quan trọng nhất với auditor.** Liệt kê cả những chỗ tôi tự thấy yếu
nhưng chưa sửa, kèm đánh giá mức độ của tôi — mà auditor nên kiểm lại chứ đừng
tin.

### 13.1. Event có thể phát cho nến vừa bị tỉa mất

Trong `_INCREMENTAL_SCRIPT`, event được gom **trong vòng lặp**, còn eviction chạy
**sau vòng lặp**, và `PUBLISH` chạy **sau eviction**. Một nến đến muộn nằm ngoài
cửa sổ sẽ được `HSET`, được đưa vào payload, rồi bị `LPOP` + `DEL` ngay trong
cùng script — nhưng event vẫn được phát.

Hệ quả: consumer nhận event cho một nến không tồn tại trên Redis. *Mức độ theo
tôi: thấp* (OG đã xử lý Hash thiếu, và OG dùng event chỉ như tín hiệu rồi đọc
lại). Nhưng nó là mâu thuẫn thật giữa event và state.

### 13.2. `DEL` rồi `RPUSH` — script Lua không rollback

Cả `insert_sorted` lẫn nhánh `rebuild` của reconcile đều làm `DEL list_key` rồi
`RPUSH` lại. Script Lua của Redis atomic với **bên đọc**, nhưng **không có
rollback**: nếu script bị hủy giữa chừng (OOM, `maxmemory` chạm trần, lỗi lệnh),
List có thể mất sạch trong khi các Hash vẫn còn → toàn bộ pair thành mồ côi, và
reconcile **không tự phát hiện được** (vì nó chỉ duyệt theo List — mục 9.2).

Hiện `maxmemory = 0` nên rủi ro OOM thấp. *Mức độ theo tôi: thấp nhưng hậu quả
cao.* Đáng cân nhắc dựng List vào key tạm rồi `RENAME` (atomic, không có khoảng
trống), nhưng tôi **chưa làm**.

### 13.3. Điểm mù: không ai quét được Hash mồ côi ở mức pair

Reconcile chỉ chạy cho các pair **đang trong universe live** và chỉ duyệt theo
List. Hai hệ quả:

- Nếu một pair bị loại khỏi universe live, List và 100 Hash của nó **nằm lại
  vĩnh viễn**, không ai dọn. Không có TTL trên bất kỳ key nào.
- Nếu List của một pair mất (13.2) mà Hash còn, không cơ chế nào trong DP phát
  hiện ra.

Hôm nay đo được đúng 165 List = đúng 165 live pair nên chưa có rác. *Mức độ theo
tôi: trung bình* — sẽ thành vấn đề thật ngay lần đầu universe thay đổi.

### 13.4. `timeout_seconds: 0.3` có thể quá gắt

`socket_timeout` và `socket_connect_timeout` đều 0.3s. Một lô incremental tối đa
làm 100 `HMGET` + tới 100 `HSET` + `LPOS`/`LINDEX`/`RPUSH`, cộng khả năng
`insert_sorted` dựng lại List 100 phần tử. Reconcile pipeline 20 pair × (100
`HMGET` + `HSET`…) trong một lượt còn nặng hơn.

Quá hạn → mở circuit breaker 30s → job bị requeue. Không mất dữ liệu, nhưng có
thể tạo chu kỳ timeout–retry mà log chỉ hiện `REDIS_PUBLISH_FAILED`. *Mức độ
theo tôi: trung bình.* **Chưa đo** thời gian chạy thật của script dưới tải — đây
là chỗ tôi muốn auditor đo cụ thể.

### 13.5. Backfill và spool replay không publish

`publish_candle_update()` chỉ được gọi từ `live.py`. Nến do **backfill** ghi vào
SQL, và nến do **spool replay** (`drain()`) ghi vào SQL, đều **không** đẩy sang
Redis ngay — phải chờ tới lần reconcile kế tiếp, tức **tối đa 30 phút**.

Có chủ ý (Redis chỉ phục vụ cửa sổ live gần nhất), nhưng nên xác nhận lại đây là
điều mong muốn chứ không phải sót.

### 13.6. `unpack()` giới hạn theo kích thước cửa sổ

`redis.call('RPUSH', list_key, unpack(rebuilt))` — `unpack` của Lua có trần
stack (thường ~8000 phần tử). Với cửa sổ 100 thì an toàn tuyệt đối. Nếu ai đó
nâng `bars_per_snapshot` lên hàng nghìn, đây sẽ là chỗ gãy **âm thầm và bất ngờ**.
Không có chỗ nào trong code chặn giá trị này.

### 13.7. Keyspace notification không bật cho Hash và List

`notify-keyspace-events = g$zK` — có `g` (generic), `$` (string), `z` (zset),
`K` (keyspace). **Không có `h` (hash) và `l` (list).**

Nghĩa là `HSET` và `RPUSH` trên dữ liệu nến **không phát keyspace notification**.
Thiết kế hiện tại không phụ thuộc vào đó (dùng Pub/Sub tầng ứng dụng), nên không
gãy. Nhưng repo có `research/redis_probe/keyspace_probe.py` lắng `__keyspace@0__:L_CANDLE_*`
— probe đó gần như mù với cấu hình này. Cần xác nhận probe đó còn ý nghĩa không.

### 13.8. Hàng đợi in-RAM, không bền, không giới hạn

Không có trần dung lượng hàng đợi. DP crash thì update đang chờ mất (reconcile
lúc khởi động bù lại). `_next_job()` **ưu tiên update trước reconcile**, nên một
luồng update liên tục về lý thuyết có thể làm reconcile trễ.

### 13.9. Không hỗ trợ Redis Cluster

Script tạo key nến động từ prefix thay vì khai báo qua `KEYS[]`, và dùng chung
script cho list key + channel. Redis Cluster yêu cầu mọi key script chạm phải
được khai báo và cùng slot. Hiện là deployment một node nên không sao; muốn
chuyển Cluster phải thiết kế lại, không chỉ thêm hash tag.

### 13.10. Không có cơ chế cho consumer tự phát hiện thế hệ sai

Hệ quả trực tiếp của mục 7.4, đã gây sự cố thật ở 12.3. Hiện không có bất kỳ
tín hiệu nào trên Redis để một consumer cũ biết mình đang đọc sai cấu trúc — nó
chỉ đọc ra rỗng và im lặng.

---

## 14. Đề nghị auditor trả lời cụ thể

1. Cấu trúc `L_CANDLE_{SYMBOL}_{TIMEFRAME}` + `:{stamp}` có thật sự là cách gọn
   nhất cho nhu cầu "lấy N nến gần nhất, không parse" không? Có phương án nào
   tốt hơn mà vẫn giữ được yêu cầu "hiển thị tươm tất" của người dùng?
2. Việc dựa vào **so sánh chuỗi** thay cho so sánh thời gian trong Lua (mục 6)
   có chỗ nào gãy mà tôi chưa nghĩ tới không? Ví dụ mốc trước năm 1000, sau năm
   9999, hoặc bất kỳ đầu vào nào khiến `_stamp()` sinh chuỗi khác độ dài.
3. Mục 13.1 (event phát cho nến đã bị tỉa) và 13.2 (`DEL` rồi `RPUSH`) — đánh
   giá mức độ của tôi có đúng không? Có nên sửa ngay không, và sửa thế nào?
4. Đo giúp thời gian chạy thật của `_INCREMENTAL_SCRIPT` với lô 100 nến và của
   `_RECONCILE_SCRIPT` với pipeline 20 pair, đối chiếu với `timeout_seconds: 0.3`
   (mục 13.4). Giá trị đó có an toàn không?
5. Mục 13.3 — nên dọn key của pair đã rời universe bằng cách nào, mà không phá
   nguyên tắc "reconcile không SCAN keyspace"?
6. Mục 13.10 — sau sự cố 12.3, có nên đưa lại một dạng tín hiệu thế hệ nào đó
   không? Nếu có thì dạng nào không mâu thuẫn với yêu cầu "không cần schema
   version" của người dùng?
7. Có lỗi nào trong hai script Lua mà cả 190 test lẫn các đợt kiểm trực tiếp
   trên Redis thật đều không chạm tới không?

---

## 15. Cách tự kiểm chứng lại (đường dẫn cụ thể)

| Muốn xem | Ở đâu |
|---|---|
| Toàn bộ logic publish | `core_program/src/dp_program/util/redis_publisher.py` |
| Hai script Lua | cùng file, dòng 70-120 và 128-166 |
| Điểm gọi incremental | `core_program/src/dp_program/engine/live.py:256` |
| Reconcile khởi động / định kỳ | `core_program/src/dp_program/engine/runtime.py:226, 275, 283` |
| Serialize DECIMAL | `core_program/src/dp_program/engine/sql_connector.py:31-57` |
| Hai hàm đọc cửa sổ nến | cùng file, dòng 361-417 |
| Test hồi quy | `core_program/test/test_redis_publisher_regression.py` |
| Contract gửi OG | `core_program/docs/OG_REDIS_SYNC_PROMPT.md` |
| Báo cáo cơ chế đầy đủ | `core_program/docs/REDIS_MECHANISM_REPORT.md` |
| Lịch sử thay đổi | `git log v5-redis`, commit `35ab215` |
