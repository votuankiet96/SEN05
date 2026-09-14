# Prompt đồng bộ cho OG (core_python) — cấu trúc Redis `CANDLE:`

> Copy toàn bộ phần dưới đây gửi cho phía OG.
>
> **Lịch sử:** đây là lần đổi contract thứ hai. Bản trước (`L_CANDLE_{SYMBOL}_{TIMEFRAME}`,
> mốc `HH:MM:SS`, Hash 5 field) đã bị thay thế — nếu OG vừa migrate sang bản đó thì
> phần lớn công sức vẫn dùng lại được, chỉ đổi cách ghép key, định dạng mốc, và
> thêm 4 field metadata.

---

DP (`dp_program_v3`, VM-DP6) đổi cấu trúc key trên Redis. Vẫn **db0**, vẫn cùng
server `10.11.12.8:6379`, vẫn channel `dp:events:candles` — chỉ đổi **cách đặt tên
key, định dạng mốc thời gian, và bộ field trong Hash**.

## 1. Vì sao đổi

Bản trước đặt mốc trong key dạng `2026-06-05_21:00:00`. Redis không có thư mục —
nhưng Redis Desktop Manager (và mọi GUI khác) **tách key theo dấu `:`** để dựng cây.
Nên mỗi nến đẻ ra ba tầng thư mục rỗng:

```
L_CANDLE_BTCUSD_D1 / 2026-06-05_21 / 00 / 00      ← không đọc được
```

Đổi giờ-phút-giây sang `-` thì dấu `:` chỉ còn ở đúng hai chỗ có nghĩa, và cây
hiện đúng hai tầng:

```
CANDLE
  CANDLE:BTCUSD_H1                           ← List, nằm ngang với folder pair
  BTCUSD_H1
    CANDLE:BTCUSD_H1:2026-07-29_04-00-00     ← Hash từng nến
    CANDLE:BTCUSD_H1:2026-07-29_05-00-00
    CANDLE:BTCUSD_H1:2026-07-29_06-00-00
```

Mốc vẫn rộng cố định nên **so sánh chuỗi vẫn đúng thứ tự thời gian** — không mất
gì khi đổi `:` thành `-`.

## 2. Bố trí key

```
LIST  CANDLE:{SYMBOL}_{TIMEFRAME}
        ["2026-07-29_04-00-00", "2026-07-29_05-00-00", "2026-07-29_06-00-00"]

HASH  CANDLE:{SYMBOL}_{TIMEFRAME}:{YYYY-MM-DD_HH-MM-SS}
        timestamp   1785301200
        datetime    2026-07-29 06:00:00
        open        100.00000000
        high        102.00000000
        low         99.00000000
        close       101.00000000
        volume      12.5000
        source      CAPITALCOM:BTCUSD
        inserttime  2026-07-29 06:07:03
```

**Ba quy tắc, không có quy tắc thứ tư:**

1. **Key Hash = key List + `":" + phần tử lấy từ List`.** Đúng một phép nối.
2. **SYMBOL trước, TIMEFRAME sau**, nối bằng `_`: `CANDLE:BTCUSD_H1`, không phải
   `CANDLE:H1_BTCUSD`.
3. **Mốc dùng `-` cho giờ-phút-giây**, `_` giữa ngày và giờ: `2026-07-29_06-00-00`.
   Không `T`, không dấu cách, không `:`, không offset.

Đếm dấu `:` là cách kiểm nhanh: **key List có đúng 1, key Hash có đúng 2.**

## 3. Định dạng mốc (phần tử List và đuôi key Hash)

```
YYYY-MM-DD_HH-MM-SS        vd: 2026-07-29_06-00-00
```

- **Luôn UTC**, **không hậu tố offset**.
- Rộng cố định, có đệm 0 → so sánh/sắp xếp trực tiếp trên chuỗi là đúng thứ tự
  thời gian. Chỉ parse khi thật sự cần đối tượng `datetime`:

```python
datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=timezone.utc)
```

## 4. Chín field trong Hash

| Field | Kiểu | Ý nghĩa |
|---|---|---|
| `timestamp` | chuỗi số nguyên | Unix epoch **giây, UTC** của open time |
| `datetime` | chuỗi | open time dạng người đọc: `2026-07-29 06:00:00` (dấu `:` ở đây vô hại — nó là **giá trị**, không phải tên key) |
| `open` `high` `low` `close` | chuỗi thập phân | scale 8, đúng DECIMAL của warehouse |
| `volume` | chuỗi thập phân | scale 4; có thể là literal `"null"` |
| `source` | chuỗi | `{BrokerChannel}:{Symbol}`, vd `CAPITALCOM:BTCUSD` |
| `inserttime` | chuỗi | **`Fact_OHLCV.CreatedAt` thật** — thời điểm row được ghi vào SQL, không phải thời điểm publish Redis |

Lưu ý quan trọng:

- **Tất cả là chuỗi text, KHÔNG phải JSON.** Không `json.loads` trên value của
  Hash; dùng `float()` / `Decimal()` trực tiếp.
- `volume == "null"` sẽ làm `float()` ném lỗi — phải xử lý riêng.
- `timestamp` và `datetime` là **hai cách biểu diễn cùng một open time**, và trùng
  với mốc trong tên key. Dùng cái nào tiện nhất; không cái nào được phép lệch.

## 5. Đọc N nến gần nhất

Cố định 2 round-trip bất kể N, không parse gì:

```python
def latest_closes(r, symbol: str, timeframe: str, n: int) -> list[float]:
    """Vd MA20: latest_closes(r, "BTCUSD", "H1", 20)"""
    list_key = f"CANDLE:{symbol}_{timeframe}"
    stamps = r.lrange(list_key, -n, -1)               # round-trip 1
    pipe = r.pipeline(transaction=False)
    for stamp in stamps:
        pipe.hget(f"{list_key}:{stamp}", "close")      # key = list_key + ":" + stamp
    return [float(v) for v in pipe.execute()]          # round-trip 2
```

Chỉ báo cần nhiều field (ATR cần high/low/close) thì đổi `hget` →
`hmget(key, "high", "low", "close")` — vẫn một pipeline.

Cửa sổ hiện tại **100 nến/pair**. Đọc độ dài từ `LLEN` hoặc dùng `LRANGE` chỉ số
âm, đừng hardcode.

## 6. Pub/Sub — cơ chế không đổi, chỉ đổi định dạng mốc

Channel `dp:events:candles`:

```json
{"symbol":"BTCUSD","timeframe":"H1","candles":[
  {"bartime":"2026-07-29_06-00-00","open":100.00000000,"high":102.00000000,
   "low":99.00000000,"close":101.00000000,"volume":12.5000}]}
```

- `bartime` dùng **đúng định dạng mốc mới**, nối thẳng ra key Hash được:
  `f"CANDLE:{symbol}_{timeframe}:{candle['bartime']}"`.
- Event chỉ mang 5 giá trị OHLCV (dạng **số JSON**), không mang 4 field metadata —
  muốn metadata thì đọc Hash.
- DP chỉ publish khi giá trị **thực sự đổi**; ghi lại y hệt không sinh event.
- Mỗi message chỉ liên quan **một** `(symbol, timeframe)`.
- Fire-and-forget: mất message không mất dữ liệu; Hash/List luôn là bản đúng.

## 7. Checklist sửa code phía OG

1. Đổi cách dựng key List: `f"CANDLE:{symbol}_{timeframe}"` (trước là
   `f"L_CANDLE_{symbol}_{timeframe}"`).
2. Key Hash giữ nguyên quy tắc `list_key + ":" + stamp` — không đổi.
3. Đổi định dạng mốc: `"%Y-%m-%d_%H:%M:%S"` → **`"%Y-%m-%d_%H-%M-%S"`**. Đây là
   thay đổi dễ bỏ sót nhất vì chỉ khác hai ký tự.
4. Nếu đang phân biệt key List với key Hash bằng `":" in key` thì **không còn đúng**
   (key List giờ cũng có `:`). Đổi sang `key.count(":") == 1`, hoặc dùng
   `SCAN ... TYPE list` (OG đã dùng cách này — vẫn đúng, không phải sửa).
5. Hash giờ có **9 field**. Nếu code đang assert đúng 5 field thì phải nới ra.
   Đừng `float()` lên `datetime`/`source`/`inserttime`.
6. Cân nhắc dùng `timestamp` (epoch) thay cho việc tự parse mốc — rẻ hơn.
7. `config.yaml` phía OG: `key_prefix` đổi `L_CANDLE` → **`CANDLE`**.
8. **Restart tiến trình `og_signal.live_worker` sau khi sửa.** Lần trước tiến
   trình cũ chạy code cũ trong RAM suốt 8 giờ, đọc ra rỗng mà không báo lỗi, mất
   18 signal. Sửa file trên đĩa là chưa đủ.

## 8. Thời điểm cắt và dữ liệu cũ

DP **chưa deploy** bản này. Hai bên chốt giờ cắt trước, rồi:

- DP dừng engine, deploy exe mới, xoá key `L_CANDLE_*` cũ, khởi động lại.
- OG deploy code mới **và restart worker**.

Trong lúc chưa cắt, Redis vẫn đang chạy contract `L_CANDLE_*` cũ — OG không cần
vội. Sau khi cắt, key `L_CANDLE_*` sẽ không còn tồn tại.

## 9. Đảm bảo từ phía DP

- **Không có nến mồ côi**: mọi ghi qua đúng một Lua script atomic; List là chỉ mục
  duy nhất; eviction xoá key Hash trong cùng script đã LPOP mốc đó.
- **List luôn tăng dần**, kể cả khi nến đến muộn.
- **Nến hiệu chỉnh ghi đè tại chỗ**, không sinh bản trùng.
- **Mọi giá trị đọc lại từ SQL trước khi ghi Redis** — kể cả trên đường live. Redis
  là bản chụp của `DWH.Fact_OHLCV`, không phải một nhánh dữ liệu song song.
- **Tự phục hồi**: DP đối chiếu toàn bộ với SQL lúc khởi động và mỗi 30 phút.
