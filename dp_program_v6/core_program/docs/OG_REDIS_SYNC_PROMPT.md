# Prompt đồng bộ cho OG (core_python) — cấu trúc Redis mới

> Copy toàn bộ phần trong khung dưới đây gửi cho phía OG.
> Thay thế `OG_REDIS_AUDIT_PROMPT.md` (mô tả cấu trúc cũ, đã bỏ).

---

DP (`dp_program_v3`, VM-DP6) đã đổi sang cấu trúc Redis mới và **đã xoá sạch toàn
bộ dữ liệu cũ**. Mọi key `dp:candles:*` **không còn tồn tại** — code đọc Redis
hiện tại của OG sẽ nhận `nil`/rỗng ở mọi truy vấn. Cần sửa phía đọc theo đúng mô
tả dưới đây.

Đây là cấu trúc **đã chốt và duy nhất** sẽ dùng từ nay. Không còn đánh số phiên
bản schema, và key `dp:candles:schema` **đã bị bỏ** — nếu OG đang kiểm key này
lúc khởi động thì phải gỡ đoạn kiểm đó, nếu không OG sẽ tự dừng.

Redis: `10.11.12.8:6379`, db 0. Channel Pub/Sub: `dp:events:candles` (không đổi).

## 1. Việc OG phải làm trước tiên: xoá dữ liệu cũ phía OG

DP đã xoá sạch trên Redis. OG cũng phải xoá mọi bản sao/cache cũ của mình:

- Mọi cache nến trong bộ nhớ hoặc trên đĩa dựng từ cấu trúc `dp:candles:*` cũ.
- Mọi key Redis do OG tự ghi mà còn bám tên cũ (nếu có).
- Khởi động lại tiến trình OG để không còn giữ window cũ trong RAM.

Không được trộn dữ liệu cũ với mới: định dạng mốc thời gian đã đổi hoàn toàn.

## 2. Bố trí key mới

```
LIST  L_CANDLE_{SYMBOL}_{TIMEFRAME}
        ["2026-09-13_15:30:00", "2026-09-13_15:35:00", "2026-09-13_15:40:00"]

HASH  L_CANDLE_{SYMBOL}_{TIMEFRAME}:{stamp}
        open   52547.00000000
        high   52549.00000000
        low    52536.00000000
        close  52538.00000000
        volume 25.0000
```

Ví dụ thật đang chạy:

```
LRANGE L_CANDLE_US30_M5 -3 -1
  1) "2026-09-11_20:45:00"
  2) "2026-09-11_20:50:00"
  3) "2026-09-11_20:55:00"

HGETALL "L_CANDLE_US30_M5:2026-09-11_20:55:00"
  open   52547.00000000
  high   52549.00000000
  low    52536.00000000
  close  52538.00000000
  volume 25.0000
```

**Ba điều quan trọng nhất:**

1. **Key Hash = key List nối thêm `":" + phần tử lấy từ List`.** Đúng một phép
   nối, không quy ước nào khác. Cầm tên List và một phần tử là dựng ra key Hash.
2. **Thứ tự: SYMBOL trước, TIMEFRAME sau** — `L_CANDLE_US30_M5`, không phải
   `L_CANDLE_M5_US30`. Phân tách bằng `_`.
3. **Hash chỉ có 5 field OHLCV.** Không còn field `bartime` — mốc thời gian đã
   nằm trong chính tên key.

## 3. Định dạng mốc thời gian

```
YYYY-MM-DD_HH:MM:SS        vd: 2026-09-13_15:40:00
```

- **Luôn UTC**, **không có hậu tố offset** (`+00:00` đã bỏ hoàn toàn).
- Dấu phân tách giữa ngày và giờ là **gạch dưới `_`**, không phải `T`, không phải
  dấu cách.
- Rộng cố định, có đệm 0.

**Hệ quả OG nên tận dụng:** vì rộng cố định nên **so sánh chuỗi đã cho đúng thứ
tự thời gian** — không cần parse ra datetime chỉ để sắp xếp hay so sánh. Đã kiểm
cả các mốc vượt ngày/tháng/năm. Chỉ parse khi thực sự cần đối tượng datetime:

```python
datetime.strptime(stamp, "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc)
```

## 4. Giá trị trong Hash

- Tất cả là **chuỗi text**, giữ nguyên scale DECIMAL của warehouse:
  giá scale 8 (`"52547.00000000"`), volume scale 4 (`"25.0000"`).
- `volume` có thể là chuỗi literal `"null"` khi provider không trả volume —
  `float("null")` sẽ ném lỗi, phải xử lý.
- **KHÔNG phải JSON.** Không `json.loads` trên value của Hash; `float()` hoặc
  `Decimal()` trực tiếp.

## 5. Cách đọc N nến gần nhất

Cố định 2 round-trip bất kể N, không parse gì:

```python
def latest_closes(r, symbol: str, timeframe: str, n: int) -> list[float]:
    """Vd MA20: latest_closes(r, "US30", "M5", 20)"""
    list_key = f"L_CANDLE_{symbol}_{timeframe}"
    stamps = r.lrange(list_key, -n, -1)              # round-trip 1
    pipe = r.pipeline(transaction=False)
    for stamp in stamps:
        pipe.hget(f"{list_key}:{stamp}", "close")     # key = list_key + ":" + stamp
    return [float(v) for v in pipe.execute()]         # round-trip 2
```

Chỉ báo cần nhiều field (vd ATR cần high/low/close) thì đổi `hget` thành
`hmget(key, "high", "low", "close")` — vẫn một pipeline, chỉ kéo đúng field cần.

Lấy trọn một nến: `HGETALL "L_CANDLE_{sym}_{tf}:{stamp}"` → dict 5 field sẵn dùng.
Lấy nến mới nhất: `LINDEX L_CANDLE_{sym}_{tf} -1` → stamp → `HGETALL`.

Cửa sổ hiện tại là **100 nến/pair**. Nên đọc độ dài từ `LLEN` hoặc dùng `LRANGE`
với chỉ số âm (tự trả ít hơn nếu List ngắn hơn) thay vì hardcode.

## 6. Kênh Pub/Sub — không đổi cơ chế, chỉ đổi định dạng mốc

Channel `dp:events:candles`, payload mỗi message (per-pair):

```json
{"symbol":"US30","timeframe":"M5","candles":[
  {"bartime":"2026-09-13_15:40:00","open":52547.00000000,"high":52549.00000000,
   "low":52536.00000000,"close":52538.00000000,"volume":25.0000}]}
```

- `bartime` trong event dùng **đúng định dạng mốc của key Hash**, nên nối thẳng
  ra key được: `f"L_CANDLE_{symbol}_{timeframe}:{candle['bartime']}"`.
- Trong payload event, OHLCV là **số JSON** (không ngoặc kép); trong Hash là
  **chuỗi**. Hai đường đọc, hai kiểu dữ liệu — giữ tách bạch như OG đang làm.
- DP chỉ publish khi giá trị **thực sự đổi**; ghi lại y hệt không sinh event.
- Mỗi message chỉ liên quan **một** `(symbol, timeframe)`.
- Pub/Sub là fire-and-forget: mất message không mất dữ liệu — Hash/List luôn là
  bản đúng để đọc lại, và DP tự đối chiếu với SQL định kỳ.

## 7. Checklist sửa code phía OG

1. Xoá/không dùng mọi tham chiếu tới `dp:candles:*`.
2. Đổi cách dựng key: `L_CANDLE_{SYMBOL}_{TIMEFRAME}` cho List,
   `list_key + ":" + stamp` cho Hash. Chú ý **SYMBOL trước TIMEFRAME**.
3. Bỏ đọc field `bartime` trong Hash (không còn). Mốc lấy từ phần tử List.
4. Đổi định dạng mốc sang `YYYY-MM-DD_HH:MM:SS` (gạch dưới, UTC, không offset).
   Nếu đang parse `+00:00` thì bỏ.
5. **Gỡ đoạn kiểm `GET dp:candles:schema`** — key này không còn, giữ lại sẽ làm
   OG tự dừng.
6. Xử lý `volume == "null"`.
7. Không hardcode 100; đọc từ `LLEN` hoặc dùng `LRANGE` chỉ số âm.
8. Xoá cache/bản sao dữ liệu cũ và khởi động lại OG (mục 1).

## 8. Đảm bảo từ phía DP

- **Không có nến mồ côi**: mọi ghi đi qua đúng một Lua script atomic; List là chỉ
  mục duy nhất; eviction xoá key Hash trong cùng script đã LPOP mốc đó.
- **List luôn tăng dần**, kể cả khi nến đến muộn (DP chèn đúng vị trí).
- **Nến hiệu chỉnh ghi đè tại chỗ**, không sinh bản trùng.
- **Tự phục hồi**: DP đối chiếu toàn bộ với SQL lúc khởi động và định kỳ sau đó,
  nên Hash/List không thể lệch SQL vĩnh viễn. Ngay sau một lần restart có thể
  thiếu nến mới nhất trong vài phút cho tới lần đối chiếu kế tiếp — đọc lại là có.
- Đã kiểm sau khi triển khai: **163/165 pair khớp chính xác với SQL** ngay lập
  tức; 2 pair còn lại lệch đúng các nến đóng trong cửa sổ restart và đã tự khớp
  sau lần đối chiếu kế tiếp.
