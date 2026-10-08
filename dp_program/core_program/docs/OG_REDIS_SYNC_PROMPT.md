# Prompt đồng bộ cho OG (core_python) — cấu trúc Redis `L_CANDLE_`

> Copy toàn bộ phần dưới đây gửi cho phía OG.
>
> **Cập nhật 2026-09-28 — CÓ THAY ĐỔI PHÁ VỠ, phía OG phải sửa code đọc:**
>
> 1. **Thứ tự List ĐẢO CHIỀU: giảm dần, index 0 = nến MỚI NHẤT**, đuôi List
>    (index -1) = nến cũ nhất. Trước đây tăng dần, mới nhất ở cuối. Khớp đúng
>    với Redis OG18. Code còn dùng `lrange(key, -n, -1)` để lấy n nến mới nhất
>    sẽ **lấy nhầm n nến CŨ NHẤT mà không báo lỗi gì**.
> 2. **Độ dài tối đa 500 → 1200 nến/List.** Pair có lịch sử TradingView ngắn
>    hơn (vd `HK50/W` 409, `BTCUSD/W` và `GOLD/W` 491) vẫn ít hơn 1200.
> 3. **Giá không còn đệm số 0 ở cuối**: `"7649.9"`, `"25653"` thay cho
>    `"7649.90"`, `"25653.00"`. Vẫn tối đa 2 chữ số thập phân, không bao giờ ở
>    dạng mũ. `float()`/`Decimal()` vẫn đọc đúng; regex hoặc assert cố định
>    "2 chữ số thập phân" sẽ hỏng. (Giá trị số KHÔNG đổi: các chỉ số chỉ có
>    tối đa 1 chữ số thập phân, BTCUSD/GOLD tối đa 2.)
> 4. **Mỗi Hash nến có TTL 7 ngày (604800 giây); List không có TTL.** DP làm mới
>    TTL luân phiên (mỗi 8 phút một List) nên khi DP chạy bình thường không có
>    Hash nào hết hạn dưới một List còn trỏ tới. Nếu DP ngừng > 7 ngày thì Hash
>    tự biến mất còn List vẫn còn: phía đọc coi Hash thiếu là "không có dữ
>    liệu", đừng để crash. Việc làm mới TTL sinh sự kiện keyspace **`expire`**
>    (KHÔNG phải `hset`, cũng không đổi giá trị); listener đang lọc `hset` thì
>    bỏ qua được. Khối lượng: khoảng 1200 sự kiện `expire` mỗi 8 phút.
> 5. **Không đổi:** tên key `L_CANDLE_{SYMBOL}_{TIMEFRAME}` và
>    `…:{stamp}`, Hash đúng 6 field (`timestamp`, `open`, `high`, `low`,
>    `close`, `time_update`), `timestamp` là chuỗi datetime UTC
>    `YYYY-MM-DD HH:MM:SS`, db0, keyspace notification `hset`, tên symbol
>    (GOLD, DE40, J225…) và tập 11 symbol × 15 khung giờ.
> 6. **Chuyển đổi:** DP xoá sạch `L_CANDLE_*` và `dp:live:ceiling:*` ở db0 rồi
>    nạp lại toàn bộ 1200 nến/pair từ TradingView. Trong vài phút đầu db0 trống
>    hoặc đang đầy dần. Lần nạp đầu tạo ra khoảng **200.000 sự kiện `hset`**
>    (165 pair × ~1200 nến) trong vài chục giây, kèm khoảng 82.000 sự kiện `del`
>    lúc xoá db0: listener nên gộp/bỏ qua đợt bùng phát này thay vì tính lại
>    tín hiệu theo từng sự kiện. Mỗi List lúc mới nạp có 1199 nến (nến đang chạy
>    chưa đóng nên không tính) và lên 1200 sau lần đóng nến kế tiếp.
>    **Khuyến nghị:** sửa reader để **không phụ thuộc chiều
>    List** (so sánh `lindex(key, 0)` với `lindex(key, -1)`, xem mục 5) — như vậy
>    thời điểm hai bên đổi không cần trùng nhau. Sửa file trên đĩa là chưa đủ,
>    phải restart `og_signal.live_worker`.
>
> **Cập nhật 2026-09-21**: bố trí key/Hash/mốc thời gian bên dưới KHÔNG đổi so
> với bản 2026-09-15. Điều đổi là *nguồn* ghi Redis: `live` không còn ghi SQL
> rồi mirror sang Redis qua reconcile nữa — giờ đẩy thẳng từ TradingView lên
> Redis mỗi cycle, tự phát hiện gap và tự phục hồi. Đã sửa lại đúng phần bị
> ảnh hưởng bởi thay đổi này (mục 4 — ý nghĩa `time_update`; mục 5 — cửa sổ N
> nến; mục 8-9 — không còn reconcile-từ-SQL). Các mục còn lại vẫn đúng nguyên
> văn, không cần OG sửa gì thêm ngoài phần đã đánh dấu.
>
> **Cập nhật 2026-09-23**: kênh `dp:events:candles` nhắc tới ngay dưới đây đã
> **NGỪNG PUBLISH hoàn toàn**, sau khi OG tự xác nhận đã chuyển hẳn sang lắng
> nghe Redis keyspace notification (`__keyspace@0__:L_CANDLE_*`, lọc `hset`)
> thay vì kênh này. Bố trí key/Hash/mốc thời gian vẫn giữ nguyên y hệt —
> chính vì HSET/RPUSH đó vẫn diễn ra đúng như mô tả bên dưới nên keyspace
> notification mới tự động đúng theo. Chỉ riêng câu "vẫn channel
> `dp:events:candles`" ở đoạn ngay dưới đây là lịch sử (đúng tại thời điểm
> 2026-09-15), không còn đúng hiện tại.
>
> **Cập nhật 2026-09-24**: 4 field giá trong Hash (`open`/`high`/`low`/
> `close`) giờ chỉ còn **2 chữ số thập phân** (ROUND_HALF_UP), không còn 8
> chữ số theo scale DECIMAL(18,8) của warehouse nữa -- theo yêu cầu
> operator, riêng cho luồng live→Redis (SQL/backfill không đổi gì). Ví dụ
> Hash ở mục 1 bên dưới đã cập nhật theo đúng dạng mới.

---

DP đã cắt sang contract Redis mới lúc **2026-09-15 05:37 UTC**. Vẫn **db0**, vẫn
`10.11.12.8:6379`. Lần này đổi **cả tên key lẫn bộ field trong Hash**, nên phải
sửa cả hai phía đọc.

## 1. Bố trí key

```
LIST  L_CANDLE_{SYMBOL}_{TIMEFRAME}         (giảm dần: index 0 = MỚI NHẤT, từ 2026-09-28)
        ["2026-09-14 03:00:00", "2026-09-14 02:00:00", "2026-09-14 01:00:00"]

HASH  L_CANDLE_{SYMBOL}_{TIMEFRAME}:{YYYY-MM-DD HH:mm:ss}     (TTL 7 ngày, từ 2026-09-28)
        timestamp     2026-09-14 03:00:00
        open          77545.35
        high          77807.9
        low           77450.75
        close         77537.05
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

Mốc này là **open time** của nến.
`time_update` **[sửa 2026-09-21]** giờ là lúc `live` fetch/đẩy nến đó lên
Redis (không còn là `Fact_OHLCV.CreatedAt` — `live` không còn ghi SQL nên
không còn "lúc row vào SQL" để tham chiếu).

## 5. Đọc N nến gần nhất

```python
def latest_closes(r, symbol: str, timeframe: str, n: int) -> list[float]:
    """n giá close MỚI NHẤT, trả về theo thứ tự thời gian tăng dần (cũ -> mới).

    [sửa 2026-09-28] List giảm dần (index 0 = mới nhất) nên lấy từ ĐẦU List.
    Đoạn `head >= tail` giúp hàm đúng với cả List kiểu cũ (tăng dần) lẫn kiểu
    mới (giảm dần), nên thời điểm DP và OG đổi không cần trùng nhau.
    """
    list_key = f"L_CANDLE_{symbol}_{timeframe}"
    head, tail = r.lindex(list_key, 0), r.lindex(list_key, -1)
    if head is None:
        return []
    if head >= tail:                                   # giảm dần (contract mới)
        stamps = r.lrange(list_key, 0, n - 1)[::-1]    # round-trip 1, đảo về cũ -> mới
    else:                                              # tăng dần (contract cũ)
        stamps = r.lrange(list_key, -n, -1)
    pipe = r.pipeline(transaction=False)
    for stamp in stamps:
        pipe.hget(f"{list_key}:{stamp}", "close")      # key = list_key + ":" + stamp
    values = pipe.execute()                            # round-trip 2
    return [float(v) for v in values if v is not None]  # Hash thiếu (hết TTL) -> bỏ qua
```

Cửa sổ **1200 nến/pair** **[sửa 2026-09-28, trước là 500; và trước nữa là 100]**.
Giá trị trong Hash là **chuỗi text, không phải JSON**, giá **không đệm số 0**
(`"7649.9"`, `"25653"`). Chỉ 4 field giá parse được ra số; `timestamp` và
`time_update` là chuỗi thời gian.

## 6. Pub/Sub — ĐÃ GỠ (2026-09-24)

**Mục này không còn áp dụng.** Kênh `dp:events:candles` đã ngừng publish
hoàn toàn (xem ghi chú cập nhật 2026-09-23 ở đầu file) sau khi OG xác nhận
đã chuyển hẳn sang lắng nghe Redis keyspace notification
(`__keyspace@0__:L_CANDLE_*`, lọc `hset`) trên chính HSET/RPUSH ở mục 1-5.
Không còn message JSON nào được publish cho phần candle nữa.

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
10. **[2026-09-28] List giảm dần, index 0 = mới nhất.** Đổi mọi
    `lrange(key, -n, -1)` / `lindex(key, -1)` dùng để lấy nến mới nhất thành
    `lrange(key, 0, n-1)` / `lindex(key, 0)`, hoặc dùng hàm không phụ thuộc chiều
    ở mục 5.
11. **[2026-09-28] Cửa sổ 1200 nến**, không còn giả định 500.
12. **[2026-09-28] Giá không đệm số 0** (`"7649.9"`): gỡ mọi regex/assert cố định
    2 chữ số thập phân; số không bao giờ ở dạng mũ.
13. **[2026-09-28] Hash có TTL 7 ngày**; Hash thiếu dưới một List còn trỏ tới
    coi là "không có dữ liệu", không crash. Sự kiện `expire` (không phải `hset`)
    xuất hiện đều đặn từ việc làm mới TTL của DP: bỏ qua.

## 8. Trạng thái Redis lúc này **[sửa 2026-09-28]**

Đã verify trực tiếp trên production sau khi chuyển đổi (2026-09-28 09:20 UTC,
165/165 live pair nạp lại trong 1 cycle ~50 giây, 0 lỗi): 165 List và khoảng
191.000 Hash (mỗi Hash đúng 6 field, TTL ≈ 7 ngày, List không TTL), 0 List trỏ
tới Hash thiếu, 0 Hash mồ côi. Mỗi List tối đa 1200 nến: lúc mới nạp là 1199
(nến đang chạy chưa đóng nên không tính) và lên 1200 sau lần đóng nến kế tiếp.
Các timeframe dài có lịch sử thật ngắn hơn thì ít hơn 1200 — số liệu thật, không
phải lỗi thiếu: `W` từ 409 (`HK50`) đến 613 (`J225`/`DE40`), 491 với `BTCUSD` và
`GOLD`.

Đối chiếu với Redis OG18 trên 8 symbol tương ứng: tập nến giống hệt nhau (0 nến
chỉ có ở một bên), giá khớp ~99,4%, và chuỗi giá **giống hệt từng ký tự** ở mọi
chỗ hai giá trị bằng nhau. Phần lệch còn lại là khác biệt giá thật của nguồn,
không phải khác định dạng.

Số liệu cũ ("16.500 Hash" ở bản 2026-09-15, hay "500 Hash/List" ở bản 2026-09-21)
đã lỗi thời — tính lại theo cửa sổ 1200.

Cơ chế tự lành đã đổi: **không còn đợi chu kỳ reconcile 30 phút.** Mỗi cycle
live (hiện `interval_minutes=2`) tự kiểm List có thiếu/gap so với TradingView
không và tự vá ngay trong cùng cycle đó — lệch kéo dài quá 1-2 cycle (~2-4
phút) mới là bất thường, không phải hai chu kỳ 30 phút như trước.

## 9. Đảm bảo từ phía DP

- Không có nến mồ côi: eviction xoá phần tử List và key Hash trong cùng một Lua
  script atomic.
- List luôn giảm dần (index 0 = mới nhất), kể cả khi nến đến muộn (chèn đúng
  vị trí). **[sửa 2026-09-28, trước là tăng dần]**
- Hash của mọi nến còn trong List có TTL được DP làm mới luân phiên (mỗi 8
  phút một List, mỗi List được làm mới khoảng 22 giờ một lần, TTL là 7 ngày).
- Nến hiệu chỉnh ghi đè tại chỗ, không sinh bản trùng.
- **[sửa 2026-09-21]** Redis **không còn** là bản chụp của `DWH.Fact_OHLCV` —
  `live` đẩy thẳng từ TradingView lên Redis, độc lập hoàn toàn với SQL. SQL
  giờ chỉ do `backfill` ghi, phục vụ lưu trữ lịch sử, không còn là nguồn trung
  gian cho Redis.
- **[sửa 2026-09-21]** Không còn "đối chiếu toàn bộ với SQL mỗi 30 phút" —
  thay bằng tự kiểm/tự vá theo TradingView ngay trong mỗi cycle live (xem
  mục 8).
