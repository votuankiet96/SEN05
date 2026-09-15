# Prompt đồng bộ cho OG (core_python) — cấu trúc Redis `L_CANDLE_`

> Copy toàn bộ phần dưới đây gửi cho phía OG.

---

DP đã cắt sang contract Redis mới lúc **2026-09-15 02:25 UTC**. Vẫn **db0**, vẫn
`10.11.12.8:6379`, vẫn channel `dp:events:candles`. Thay đổi so với bản `CANDLE:`
vừa rồi chỉ gồm **hai điểm**, nhưng cả hai đều nằm ở tên key nên bắt buộc phải sửa.

## 1. Hai thay đổi

| | Bản trước (`CANDLE:`) | **Bản mới** |
|---|---|---|
| Key List | `CANDLE:BTCUSD_H1` | `L_CANDLE_BTCUSD_H1` |
| Mốc | `2026-09-14_03-00-00` | `20260914_030000` |
| Key Hash | `CANDLE:BTCUSD_H1:2026-09-14_03-00-00` | `L_CANDLE_BTCUSD_H1:20260914_030000` |

Mốc trong key dùng dạng gọn `YYYYMMDD_HHMMSS` — **không chứa `:`**, nên key Hash
chỉ còn đúng một dấu `:` (dấu của phép nối) và Redis GUI hiện đúng hai tầng.
Dạng người đọc vẫn có, nằm ở field `datetime` trong Hash. Bộ 9 field **không đổi**.

## 2. Bố trí key

```
LIST  L_CANDLE_{SYMBOL}_{TIMEFRAME}
        ["20260914_010000", "20260914_020000", "20260914_030000"]

HASH  L_CANDLE_{SYMBOL}_{TIMEFRAME}:{YYYYMMDD_HHMMSS}
        timestamp   1789354800
        datetime    2026-09-14 03:00:00
        open        77545.35000000
        high        77807.90000000
        low         77450.75000000
        close       77537.05000000
        volume      12684.0000
        source      CAPITALCOM:BTCUSD
        inserttime  2026-09-14 03:05:12
```

**Ba quy tắc:**

1. **Key Hash = key List + `":" + phần tử lấy từ List`.** Một phép nối duy nhất.
2. **SYMBOL trước, TIMEFRAME sau**, nối bằng `_`.
3. **Mốc trong key, `timestamp` và `datetime` đều là open time** (`BarTime` của
   SQL) — ba cách viết của cùng một thời điểm, sinh từ đúng một hàm nên không
   thể lệch nhau. Chỉ `inserttime` là thứ khác: `Fact_OHLCV.CreatedAt`.

## 3. Điểm quan trọng nhất phải để ý khi sửa code

Cách phân biệt key List với key Hash đổi lần nữa:

```
L_CANDLE_BTCUSD_H1                     → 0 dấu ':'   ← LIST
L_CANDLE_BTCUSD_H1:20260914_030000     → 1 dấu ':'   ← HASH
```

- Nếu bản trước bạn dùng `key.count(":") == 1` để nhận ra **List** → **không còn
  đúng**, vì giờ đó lại là dấu hiệu của Hash. Đổi thành `":" not in key`.
- Nếu dùng `SCAN ... TYPE list` thì vẫn đúng, không phải sửa.
- Pattern SCAN đổi từ `CANDLE:*` sang `L_CANDLE_*`.

Key không chứa dấu cách nên `redis-cli` gõ thẳng, không cần ngoặc kép.

## 4. Định dạng mốc

```
YYYYMMDD_HHMMSS        vd: 20260914_030000
```

- **Luôn UTC**, không hậu tố offset.
- Rộng cố định, đệm 0 → **so sánh/sắp xếp trực tiếp trên chuỗi vẫn đúng thứ tự
  thời gian**, kể cả qua mốc vượt ngày/tháng/năm (đã kiểm). Không cần parse chỉ
  để sắp xếp.

```python
datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
```

Hoặc bỏ qua việc parse: field `timestamp` trong Hash đã là epoch giây UTC sẵn.

## 5. Đọc N nến gần nhất

```python
def latest_closes(r, symbol: str, timeframe: str, n: int) -> list[float]:
    list_key = f"L_CANDLE_{symbol}_{timeframe}"
    stamps = r.lrange(list_key, -n, -1)               # round-trip 1
    pipe = r.pipeline(transaction=False)
    for stamp in stamps:
        pipe.hget(f"{list_key}:{stamp}", "close")      # key = list_key + ":" + stamp
    return [float(v) for v in pipe.execute()]          # round-trip 2
```

Cửa sổ **100 nến/pair**. Đọc độ dài từ `LLEN` hoặc dùng `LRANGE` chỉ số âm.

Giá trị trong Hash là **chuỗi text, không phải JSON**. `volume` có thể là literal
`"null"` → `float()` sẽ ném lỗi, phải xử lý. Đừng `float()` lên `datetime` /
`source` / `inserttime`.

## 6. Pub/Sub

Channel `dp:events:candles`, `bartime` dùng đúng mốc mới:

```json
{"symbol":"BTCUSD","timeframe":"H1","candles":[
  {"bartime":"20260914_030000","open":77545.35000000,"high":77807.90000000,
   "low":77450.75000000,"close":77537.05000000,"volume":12684.0000}]}
```

Nối thẳng ra key: `f"L_CANDLE_{symbol}_{timeframe}:{candle['bartime']}"`.
Event chỉ là tín hiệu — nhận rồi đọc lại Hash/List, đừng coi payload là nguồn dữ liệu.

## 7. Checklist

1. Key List: `f"L_CANDLE_{symbol}_{timeframe}"`.
2. Mốc: `"%Y%m%d_%H%M%S"` (gọn, không dấu cách, không dấu hai chấm).
3. Phân biệt List/Hash: `":" not in key` (hoặc giữ `SCAN ... TYPE list`).
4. Pattern SCAN: `L_CANDLE_*`.
5. `key_prefix` → `L_CANDLE`.
6. Nếu test của bạn assert mốc **có** chứa `:` → phải bỏ assert đó.
7. **Restart `og_signal.live_worker`.** Sửa file trên đĩa là chưa đủ.

## 8. Trạng thái Redis lúc này

- **db0 đã nạp lại xong**: 165 List + 16.500 Hash = 16.665 key, đúng contract mới.
- Đã kiểm: 0 Hash mồ côi, 0 phần tử List trỏ hụt, 0 Hash sai bộ 9 field,
  **0 Hash có `datetime` không khớp mốc trong key**, 0 nến lệch giá trị so với SQL.
- Trong ~30 phút đầu sau khi DP restart, vài pair có thể thiếu 1-2 nến mới nhất —
  bình thường, tự lành ở lần đối chiếu kế tiếp. Lệch kéo dài qua **hai** chu kỳ
  reconcile mới là bất thường.

**Cần bạn kiểm giúp:** `db1` (nơi OG ghi signal) hiện **đang rỗng hoàn toàn**,
trước đó có ~601 key. DP không đụng tới db1. Nhiều khả năng có lệnh `FLUSHALL`
đã chạy thay vì `FLUSHDB 0`. Xác nhận giúp signal cũ có cần khôi phục không, và
OG có đang ghi signal mới bình thường sau khi restart không.

## 9. Đảm bảo từ phía DP

- Không có nến mồ côi: eviction xoá phần tử List và key Hash trong cùng một Lua
  script atomic.
- List luôn tăng dần, kể cả khi nến đến muộn.
- Nến hiệu chỉnh ghi đè tại chỗ, không sinh bản trùng.
- Mọi giá trị đọc lại từ SQL trước khi ghi Redis — Redis là bản chụp của
  `DWH.Fact_OHLCV`, không phải nhánh dữ liệu song song.
- Đối chiếu toàn bộ với SQL lúc khởi động và mỗi 30 phút.
