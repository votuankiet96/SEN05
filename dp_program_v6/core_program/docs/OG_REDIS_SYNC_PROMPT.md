# Prompt đồng bộ cho OG (core_python) — cấu trúc Redis `L_CANDLE_`

> Copy toàn bộ phần dưới đây gửi cho phía OG.

---

DP đã cắt sang contract Redis mới lúc **2026-09-15 05:37 UTC**. Vẫn **db0**, vẫn
`10.11.12.8:6379`, vẫn channel `dp:events:candles`. Lần này đổi **cả tên key lẫn
bộ field trong Hash**, nên phải sửa cả hai phía đọc.

## 1. Bố trí key

```
LIST  L_CANDLE_{SYMBOL}_{TIMEFRAME}
        ["2026-09-14 01:00:00", "2026-09-14 02:00:00", "2026-09-14 03:00:00"]

HASH  L_CANDLE_{SYMBOL}_{TIMEFRAME}:{YYYY-MM-DD HH:mm:ss}
        timestamp     2026-09-14 03:00:00
        open          77545.35000000
        high          77807.90000000
        low           77450.75000000
        close         77537.05000000
        time_update   2026-09-14 03:05:12
```

**Ba quy tắc:**

1. **Key Hash = key List + `":" + phần tử lấy từ List`.** Một phép nối duy nhất.
2. **SYMBOL trước, TIMEFRAME sau**, nối bằng `_`.
3. **Phần tử List == field `timestamp` == đuôi key Hash.** Ba chỗ đó luôn là cùng
   một chuỗi, sinh từ đúng một hàm nên không thể lệch nhau.

## 2. Thay đổi so với bản trước

| | Bản trước | **Bản mới** |
|---|---|---|
| Key List | `CANDLE:BTCUSD_H1` | `L_CANDLE_BTCUSD_H1` |
| Mốc | `2026-09-14_03-00-00` | `2026-09-14 03:00:00` |
| Key Hash | `CANDLE:BTCUSD_H1:2026-09-14_03-00-00` | `L_CANDLE_BTCUSD_H1:2026-09-14 03:00:00` |
| Số field | 9 | **6** |
| `timestamp` | epoch `1789354800` | **chuỗi datetime** `2026-09-14 03:00:00` |
| `datetime` | có | **bỏ** (trùng `timestamp`) |
| `volume` | có | **bỏ** |
| `source` | có | **bỏ** |
| `inserttime` | có | đổi tên thành **`time_update`** |

**Hai điểm phải để ý nhất:**

- **`timestamp` giờ là chuỗi datetime, không phải epoch.** Nếu code đang làm
  `int(h["timestamp"])` hay `datetime.fromtimestamp(...)` thì sẽ vỡ.
- **`volume` không còn trên Redis.** Chỉ báo nào dựa trên khối lượng sẽ không lấy
  được dữ liệu từ Redis nữa. Volume vẫn có trong SQL nếu cần đường khác.

## 3. Phân biệt key List với key Hash

```
L_CANDLE_BTCUSD_H1                        → 0 dấu ':'   ← LIST
L_CANDLE_BTCUSD_H1:2026-09-14 03:00:00    → 3 dấu ':'   ← HASH
```

- Nếu bản trước dùng `key.count(":") == 1` để nhận ra List → **không còn đúng**.
  Đổi thành `":" not in key`.
- Nếu dùng `SCAN ... TYPE list` thì vẫn đúng, không phải sửa.
- Pattern SCAN đổi từ `CANDLE:*` sang `L_CANDLE_*`.

Key **chứa dấu cách**. Redis xử lý bình thường, nhưng gõ tay trong `redis-cli`
phải đặt trong ngoặc kép:

```bash
redis-cli -n 0 HGETALL "L_CANDLE_BTCUSD_H1:2026-09-14 03:00:00"
```

## 4. Định dạng mốc

```
YYYY-MM-DD HH:mm:ss        vd: 2026-09-14 03:00:00
```

Luôn UTC, không hậu tố offset. Rộng cố định, đệm 0 → **so sánh/sắp xếp trực tiếp
trên chuỗi vẫn đúng thứ tự thời gian**, kể cả qua mốc vượt ngày/tháng/năm (đã
kiểm). Không cần parse chỉ để sắp xếp.

```python
datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
```

Mốc này là **open time** của nến (`Fact_OHLCV.BarTime` phía SQL).
`time_update` là thứ khác: `Fact_OHLCV.CreatedAt`, tức lúc row vào SQL.

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

Cửa sổ **100 nến/pair**. Giá trị trong Hash là **chuỗi text, không phải JSON**.
Chỉ 4 field giá parse được ra số; `timestamp` và `time_update` là chuỗi thời gian.

## 6. Pub/Sub

Event cũng bỏ `volume`, chỉ còn `bartime` + 4 giá:

```json
{"symbol":"BTCUSD","timeframe":"H1","candles":[
  {"bartime":"2026-09-14 03:00:00","open":77545.35000000,"high":77807.90000000,
   "low":77450.75000000,"close":77537.05000000}]}
```

Nối thẳng ra key: `f"L_CANDLE_{symbol}_{timeframe}:{candle['bartime']}"`.
Event chỉ là tín hiệu — nhận rồi đọc lại Hash/List.

## 7. Checklist

1. Key List: `f"L_CANDLE_{symbol}_{timeframe}"`; pattern SCAN `L_CANDLE_*`.
2. Mốc: `"%Y-%m-%d %H:%M:%S"`.
3. Phân biệt List/Hash: `":" not in key` (hoặc giữ `SCAN ... TYPE list`).
4. **Bỏ mọi chỗ đọc `datetime`, `volume`, `source`.**
5. **`inserttime` → `time_update`.**
6. **`timestamp` là chuỗi, không phải epoch** — gỡ mọi `int()` / `fromtimestamp()`.
7. Nếu code assert Hash có 9 field → đổi thành 6.
8. `key_prefix` → `L_CANDLE`.
9. **Restart `og_signal.live_worker`.** Sửa file trên đĩa là chưa đủ.

## 8. Trạng thái Redis lúc này

db0 đã nạp lại xong: **165 List + 16.500 Hash = 16.665 key**. Đã kiểm: 0 Hash mồ
côi, 0 phần tử List trỏ hụt, 0 Hash sai bộ 6 field, **0 Hash có `timestamp` khác
phần tử List**, 0 nến lệch giá trị so với SQL.

Trong ~30 phút đầu sau khi DP restart, vài pair có thể thiếu 1-2 nến mới nhất —
bình thường, tự lành ở lần đối chiếu kế tiếp. Lệch kéo dài qua **hai** chu kỳ
reconcile mới là bất thường.

## 9. Đảm bảo từ phía DP

- Không có nến mồ côi: eviction xoá phần tử List và key Hash trong cùng một Lua
  script atomic.
- List luôn tăng dần, kể cả khi nến đến muộn.
- Nến hiệu chỉnh ghi đè tại chỗ, không sinh bản trùng.
- Mọi giá trị đọc lại từ SQL trước khi ghi Redis — Redis là bản chụp của
  `DWH.Fact_OHLCV`, không phải nhánh dữ liệu song song.
- Đối chiếu toàn bộ với SQL lúc khởi động và mỗi 30 phút.
