# Prompt — audit chéo cách OG (core_python) đọc Redis

> Copy toàn bộ phần trong khung dưới đây, đưa cho người/agent sẽ audit repo OG.
> Mục tiêu: kiểm tra phía OG có đang đọc đúng cấu trúc dữ liệu Redis mà DP đang
> ghi hay không.

---

Bạn đang audit repo **OG (`core_python`, VM-OG8)** — chương trình đọc nến từ Redis
để tính signal. Nhiệm vụ: **đối chiếu code đọc Redis của OG với contract mà DP
(`dp_program_v3`) đang thực sự ghi lên Redis lúc này**, chỉ ra mọi chỗ lệch.

Không sửa code. Xuất một báo cáo: từng mục contract → OG tuân thủ / lệch / không
tìm thấy chỗ xử lý, kèm `file:line` dẫn chứng trong repo OG.

## Contract DP đang ghi (ground truth — schema 2)

Redis: `10.11.12.8:6379`, db 0. Đây là trạng thái code DP hiện tại, đã kiểm bằng
lệnh Redis trực tiếp trên production.

### Key layout

```
STRING  dp:candles:schema                       = "2"
LIST    dp:candles:{timeframe}:{symbol}:order    [epoch, epoch, ...]  epoch giây UTC, TĂNG DẦN
HASH    dp:candles:{timeframe}:{symbol}:{epoch}  field: bartime, open, high, low, close, volume
```

- Mỗi nến là **một Hash riêng**, key = `...:{symbol}:{epoch}`. Không có Hash gộp.
- List chứa **chỉ epoch**, là **chỉ mục thứ tự thời gian duy nhất**. Reader lấy N
  nến gần nhất bằng `LRANGE ...:order -N -1` rồi đọc từng Hash tương ứng.
- `bars_per_snapshot` hiện tại = **100** (không phải 500). Nến cũ hơn 100 bị evict
  khỏi Redis (vẫn còn trong SQL).
- Thứ tự trong List luôn tăng dần theo epoch, kể cả khi nến đến muộn (DP chèn đúng
  vị trí).

### Giá trị trong Hash

- Tất cả là **chuỗi text**, giữ nguyên scale DECIMAL của warehouse:
  giá scale 8 (`"52845.10000000"`), volume scale 4 (`"11314.0000"`).
- `volume` có thể là chuỗi literal `"null"` khi không có dữ liệu.
- **KHÔNG phải JSON** — không `json.loads` trên value của Hash. Đọc field nào thì
  `float()` hoặc `Decimal()` trực tiếp field đó.
- Field `bartime` chỉ để người đọc; format **không đồng nhất**: đường reconcile ghi
  `"2026-09-08 14:00:00"` (không offset), đường event incremental ghi
  `"2026-09-08 14:00:00+00:00"` (có offset).

### Kênh sự kiện Pub/Sub

Channel: `dp:events:candles`. Payload mỗi message (JSON, **per-pair**):

```json
{"symbol":"US30","timeframe":"H1","candles":[
  {"bartime":"2026-09-08 14:00:00+00:00","open":52880.10000000,"high":52906.10000000,
   "low":52720.10000000,"close":52845.10000000,"volume":11314.0000}
]}
```

- Trong payload event, `open/high/low/close/volume` là **số JSON** (không có ngoặc
  kép), `volume` có thể là `null`. Khác với Hash (nơi mọi giá trị là chuỗi).
- DP chỉ publish khi giá trị **thực sự đổi**. Ghi lại y hệt không sinh event.
- Mỗi message chỉ liên quan **một** `(symbol, timeframe)`. Không có broadcast toàn
  bộ. Số đo thật: mỗi chu kỳ DP fetch 165 pair nhưng thường chỉ 2–40 nến đổi.
- Pub/Sub là **fire-and-forget**: OG offline lúc publish là mất message vĩnh viễn,
  không replay được. Hash + List mới là bản đúng để đọc lại.

### Key đã BỎ (nếu OG còn đọc → nhận nil / sai)

- `dp:candles:{tf}:{symbol}:data` — schema field-phẳng cũ, đã xoá.
- Dạng cũ hơn nữa: 1 Hash field = 1 chuỗi JSON OHLCV — đã xoá.

## Checklist phải kiểm trong code OG

### A. Đọc dữ liệu

1. OG đọc từ key `dp:candles:{tf}:{symbol}:{epoch}` (một Hash / nến) — hay còn tham
   chiếu key `...:data` đã bỏ?
2. OG lấy "N nến gần nhất" bằng `LRANGE ...:order` rồi pipeline `HGET`/`HMGET` —
   hay đang quét `KEYS`/`SCAN`, hay tự đoán epoch?
3. OG có `json.loads` trên value Hash không? (phải KHÔNG — value là chuỗi số thô)
4. OG xử lý `volume == "null"` thế nào? Có crash khi `float("null")` không?
5. OG có dùng `Decimal` khi cần chính xác, hay ép hết qua `float` (mất scale)?
6. OG giả định cửa sổ bao nhiêu nến? Có hardcode 500 ở đâu không? (phải là 100,
   tốt nhất là đọc từ độ dài List, không hardcode)
7. OG có tự sort nến theo bartime/epoch sau khi đọc, hay tin tuyệt đối thứ tự List?

### B. Mốc thời gian

8. OG lấy thời gian của nến từ **epoch** (trong List / trong tên key) hay từ **parse
   chuỗi `bartime`**? Nếu parse `bartime` bằng `pd.to_datetime()` suy luận format
   hỗn hợp → sẽ lỗi vì format không đồng nhất (đây từng là nguyên nhân báo "NaN").
9. OG có giả định `bartime` luôn có (hoặc luôn không có) offset timezone không?

### C. Nhận cập nhật thời gian thực

10. OG subscribe đúng channel `dp:events:candles`?
11. OG parse payload đúng schema `{symbol, timeframe, candles:[...]}`? Có xử lý
    trường hợp `candles` nhiều phần tử (catch-up bù nhiều nến) không?
12. OG dùng payload event như **tín hiệu** (rồi đọc lại cửa sổ của đúng pair đó từ
    Hash/List) — hay dựng chuỗi nến **chỉ** từ payload event? Nếu chỉ từ payload:
    khi lỡ một event, cửa sổ của OG lệch vĩnh viễn mà không tự biết.
13. OG có xử lý mất kết nối Pub/Sub không? (reconnect, và resync định kỳ đọc lại
    toàn bộ pair — tương đương cơ chế reconcile DP làm với SQL)
14. Khi nhận event của pair X, OG chỉ xử lý pair X — hay quét lại tất cả pair?
15. OG có nhầm lẫn giữa số JSON trong payload event và chuỗi text trong Hash không?
    (cùng một nến, hai nơi hai kiểu dữ liệu)

### D. An toàn phiên bản

16. OG có `GET dp:candles:schema` lúc khởi động và dừng/cảnh báo nếu khác `"2"`
    không? (đây là chốt chặn để OG chạy code cũ không đọc nhầm dữ liệu trong im lặng)
17. OG có ghi vào bất kỳ key nào thuộc prefix `dp:*` không? (phải KHÔNG — `dp:*`
    chỉ DP ghi)

## Định dạng báo cáo cần xuất

Với mỗi mục checklist ở trên:

| Mục | Trạng thái | Dẫn chứng (file:line trong repo OG) | Ghi chú |
|---|---|---|---|
| A1 | tuân thủ / lệch / không có xử lý | `og/.../reader.py:88` | ... |

Cuối báo cáo:
- **Danh sách chỗ lệch nghiêm trọng** (sẽ đọc sai dữ liệu hoặc crash): ưu tiên sửa.
- **Danh sách chỗ mong manh** (chạy được nhưng dễ hỏng khi lỡ event / khi DP đổi
  cấu hình cửa sổ): nên gia cố.
- **Kết luận một câu**: OG hiện có đồng bộ với schema 2 của DP hay không.
