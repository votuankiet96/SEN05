# Báo cáo debug & giám sát: TradingView → DP → SQL → Redis

> Tổng hợp phiên debug 2026-09-15 → 2026-09-16. Mục tiêu: trả lời checklist debug
> mentor giao (theo dõi DP push nến lên Redis, xác nhận dữ liệu đúng lúc đóng nến,
> lúc nhiều timeframe cùng đóng, timeframe đặc biệt M45/M90) bằng dữ liệu thật —
> không suy đoán. Người đọc: team DP (Kiệt Võ, Văn Hiển).

---

## 1. Checklist debug theo yêu cầu mentor — trạng thái cuối phiên

| # | Yêu cầu | Trạng thái | Bằng chứng |
|---|---|---|---|
| 1 | DP chạy liên tục, độc lập, push nến lên Redis, nhiều symbol/timeframe | ✅ Xong | §5 |
| 2 | Có công cụ theo dõi/debug DP → Redis | ✅ Xong | §6 |
| 3 | Tại thời điểm nến đóng, lấy có đúng không, có lỗi không | ✅ Xong | §7, §4 |
| 4 | Tại thời điểm nhiều timeframe cùng đóng thì sao | ✅ Xong | §8 |
| 5 | Cần quan sát data thật trên TradingView | ⚠️ **User tự làm** — đã chốt, xem §9 | §9 |
| 6 | Timeframe đặc biệt M45, M90 có lấy chuẩn không | ✅ Xong | §4 |
| — | "Thầy hướng dẫn team tới đâu" | Ngoài phạm vi, mentor cũng chưa rõ | — |

---

## 2. Kiến trúc hệ thống

```
TradingView (WebSocket) ──► DP (dp_program_v3)
                               │
                               ├──► SQL Server (DWH.Fact_OHLCV) ── nguồn dữ liệu bền
                               │
                               └──► [chỉ khi SQL ghi thành công] Redis ── bản chụp cửa sổ gần nhất
                                                                            │
                                                                            ▼
                                                              OG (chưa có trong repo này)
                                                              đọc Redis → tính signal → đẩy lại Redis
                                                                            │
                                                                            ▼
                                                              OF (chưa xây) → đặt lệnh broker
```

DP có 2 workflow độc lập, không phụ thuộc nhau: **live** (lấy nến mới mỗi ~2 phút,
chỉ nến đã đóng) và **backfill** (kiểm/lấp cửa sổ lịch sử theo lịch). Redis chỉ
được `live.py` gọi tới, sau khi `fetch_and_store()` đã ghi SQL thành công.
**`backfill.py` hoàn toàn không đụng Redis** (đã grep xác nhận, xem §5).

Universe hiện tại: **165 pair = 11 symbol live × 15 timeframe**
(M5, M10, M15, M20, M30, M45, H1, M90, H2, H3, H4, H6, H8, D1, W).

---

## 3. Redis contract hiện tại

```
LIST  L_CANDLE_{SYMBOL}_{TIMEFRAME}                    [stamp tăng dần]
HASH  L_CANDLE_{SYMBOL}_{TIMEFRAME}:{stamp}             6 field:
      timestamp, open, high, low, close, time_update
```

Stamp = `YYYY-MM-DD HH:MM:SS`, luôn UTC, rộng cố định, so sánh chuỗi = so sánh
thời gian (Lua dựa hẳn vào tính chất này). Key Hash = key List nối thêm `":" +
stamp`. Đây là **thế hệ thứ 5** của contract (deploy 2026-09-15 05:37 UTC), sau 4
lần đổi trước đó vì lý do vận hành thật (vỡ encoding Redis, bug trùng field, GUI
tách nested folder do dấu `:` trong giờ-phút-giây...). Chi tiết đầy đủ 5 thế hệ:
`docs/REDIS_PUBLISH_AUDIT_BRIEF.md` §4.

**Lưu ý cần biết:** thế hệ 5 dùng lại dấu `:` trong phần giờ-phút-giây của stamp
(`HH:MM:SS`), tái tạo đúng vấn đề GUI-nesting mà thế hệ 3→4 từng sửa (key Hash có
3 dấu `:`, RedisInsight/Redis Desktop Manager sẽ tách thành 3 tầng thư mục rỗng
mỗi nến). Không tài liệu nào của thế hệ 5 ghi đây là đánh đổi có chủ ý — cần xác
nhận lại với người quyết định layout nếu muốn sửa.

---

## 4. Đối chiếu TradingView ↔ SQL ↔ Redis (công cụ: `research/redis_sql_tv.ipynb`)

Notebook lấy 100 nến đóng gần nhất của toàn bộ 165 pair từ cả 3 nguồn, TradingView
làm baseline. Kết quả lần chạy gần nhất:

- **Coverage: 165/165 pair đầy đủ 100 nến ở cả SQL và Redis. 0 bar bị thiếu, 0 bar
  thừa** (`missing_from_target` = 0, `extra_in_target_window` = 0).
- **242 ô (bar × field) lệch giá trị** so với TradingView đang kéo trực tiếp —
  nhưng biên độ rất nhỏ: max ≈ 0.115%, median ≈ 0.0066%, p95 ≈ 0.032%.
- **SQL và Redis lệch giống hệt nhau ở toàn bộ 242 ô này** (đối chiếu từng dòng,
  `redis_matches_sql = True` 100%) — tức Redis không tự sai độc lập, nó phản chiếu
  đúng SQL.
- **Không có bar nào lệch cả nến**: 86% chỉ lệch đúng 1 field (đa số là `low`/`high`
  — phần wick), 14% lệch 2 field liền kề (`high+open`, `low+open`...), **0% lệch 3
  hoặc 4 field**.
- **M45/M90 không có bug riêng**: xuất hiện trong danh sách lệch với đúng pattern
  như mọi timeframe khác (lệch nhỏ, 1 field), không có lỗi cấu trúc.

**Nguyên nhân khả dĩ nhất (đã research cộng đồng cTrader + tài liệu chính thức
TradingView, không phải suy đoán):** TradingView tự công bố trong tài liệu tích
hợp broker (`broker-api-docs/data/history`) rằng `/history` và `/streaming` được
phép lệch tới **5% số bar** mà vẫn coi là hợp lệ, và có bước lọc "incorrect prices
(adhesions)" chạy sau khi dữ liệu đã vào hệ thống. Pattern quan sát được (chỉ
lệch 1-2 field, luôn dính wick/open, không bao giờ cả nến) khớp với cơ chế lọc
tick outlier chạy sau — không phải lỗi ghi/parse của DP. Chi tiết nguồn:
xem lịch sử hội thoại phiên debug, mục nghiên cứu TradingView/cTrader.

---

## 5. Xác nhận DP chạy liên tục, độc lập, đang push Redis

| Tiêu chí | Kết quả đo (2026-09-16) |
|---|---|
| Process live | `dp_program.exe` PID 5260, uptime > 1 ngày, chưa restart lần nào |
| Heartbeat | Luôn < 60s tuổi (ngưỡng báo động 600s) |
| Scheduled Task | `SEN05 DP Program Engine` = **Running**, có `SEN05 DP Program Watchdog` riêng tự canh restart — độc lập hoàn toàn với terminal/session |
| Backfill | PID riêng (9912), chạy song song, generation gần nhất `555/555 processed, 0 failed` |
| Backfill có phụ thuộc Redis không | **Không** — grep `backfill.py` không có dòng nào import/gọi `redis_publisher` |
| Cycle live gần nhất | 165/165 pair OK, 0 failed |

**Cách xác nhận (lặp lại được, không cần script riêng):**
`python -m dp_program status --mode live` (CLI có sẵn của DP) +
`python research/redis_probe/state_probe.py --once` (đọc thẳng Redis, đối chiếu SQL).

**Cơ chế chịu lỗi khi Redis down (đọc từ code, xem thêm giới hạn ở cuối mục):**
`publish_candle_update()` chỉ enqueue vào RAM, không I/O — cycle live không bao
giờ chờ Redis. Một thread nền riêng mới thật sự nối Redis; lỗi thì mở circuit
breaker 30s và **requeue lại trong RAM** (không mất, không "bắn vào hư không"),
thử lại tới khi Redis sống. Nếu chính DP restart giữa lúc hàng đợi RAM đó chưa
publish được thì mất thật — nhưng reconcile lúc khởi động (đọc lại từ SQL) và
reconcile định kỳ mỗi 30 phút tự phục hồi độc lập với hàng đợi đó.

> **Giới hạn thành thật:** toàn bộ đoạn trên là đọc code (`_open_circuit`,
> `_requeue`, `_worker_loop`), **chưa có bằng chứng thực nghiệm**. Log live 14
> ngày gần nhất có `REDIS_PUBLISH_FAILED` = 0 lần, `REDIS_PUBLISH_RECOVERED` = 0
> lần — Redis chưa từng thật sự lỗi để kiểm chứng cơ chế này trong thực tế.
> Cũng chưa có unit test nào giả lập kịch bản Redis mất kết nối.

---

## 6. Công cụ giám sát (3 tầng ban đầu, còn lại 2)

| Probe | Tầng | Trạng thái | Kết quả tích luỹ |
|---|---|---|---|
| `state_probe.py` | Cao nhất — SQL là trọng tài | Đang **tắt** | 53.733 cảnh báo, 100% loại `stale`, **0 lỗi cấu trúc**. 3.115 lần cross-check SQL, **0 lệch giá trị**. |
| `pubsub_probe.py` | App-level — event vs Hash | Đang **tắt** | **0** `EVENT_HASH_MISMATCH`, **0** `EVENT_NOT_IN_HASH`, **0** event sai schema |
| ~~`keyspace_probe.py`~~ | Thấp nhất — lệnh Redis thật | **Đã xoá 2026-09-16** | Xem lý do bên dưới |

**Vì sao xoá `keyspace_probe`:** DP không dùng `notify-keyspace-events` — tín hiệu
thật của DP là `PUBLISH` tường minh trong Lua. Probe này chỉ có giá trị nếu server
Redis bật thêm lớp `h`/`l` trong `notify-keyspace-events` (hiện chỉ có `g$zK`,
xác nhận bằng log: **37.167/37.167 dòng quan sát được đều là `op=del`**, chưa
từng thấy HSET/RPUSH). Enable thêm lớp đó là sửa config server production (máy
VM-OG8, không thuộc quyền DP) chỉ để phục vụ 1 probe phụ — trong khi tín hiệu nó
muốn xác nhận (nến ghi đúng) đã được `state_probe` phủ mạnh hơn (so trực tiếp giá
trị với SQL), và phần DEL/eviction nó từng thấy được cũng đã bị `state_probe`
bắt gián tiếp qua check `window_overflow`. Kết luận: không đáng đánh đổi, đã bỏ.
Đã dọn: xoá file, sửa `redis_probe.bat` (bớt khỏi `PROGRAMS`), xoá sạch
`probe_logs/`.

**Trạng thái cuối phiên: `pubsub_probe` và `state_probe` đang TẮT**, chưa bật lại
— cần `redis_probe.bat start` nếu muốn tiếp tục giám sát liên tục.

---

## 7. Xác minh trực tiếp tại đúng thời điểm nến đóng (bằng chứng sống, không phải log cũ)

Canh đúng lúc nến M5 đóng (06:45:00 UTC ngày 2026-09-16), đợi DP chạy cycle kế
tiếp, đối chiếu 3 nguồn cho bar mở `06:40:00`:

| Pair | Nguồn | Open | High | Low | Close |
|---|---|---|---|---|---|
| BTCUSD/M5 | SQL | 75767.75 | 75852.40 | 75767.75 | 75842.40 |
| BTCUSD/M5 | Redis | 75767.75 | 75852.40 | 75767.75 | 75842.40 |
| BTCUSD/M5 | TradingView (pull mới) | 75767.75 | 75852.4 | 75767.75 | 75842.4 |
| GOLD/M5 | SQL | 4325.53 | 4328.12 | 4324.20 | 4327.93 |
| GOLD/M5 | Redis | 4325.53 | 4328.12 | 4324.20 | 4327.93 |
| GOLD/M5 | TradingView (pull mới) | 4325.53 | 4328.12 | 4324.2 | 4327.93 |

**Khớp tuyệt đối cả 3 nguồn, 2 symbol khác nhau.** DP ghi SQL/Redis lúc 06:46:42
và 06:46:49 — khoảng 1 phút 42-49 giây sau khi nến thực sự đóng, đúng chu kỳ live
~2 phút.

---

## 8. Tại thời điểm nhiều timeframe cùng đóng

Không cần dựng kịch bản giả lập — DP đã chạy qua các mốc này thật, lấy trực tiếp
từ log làm bằng chứng lùi lại (chi tiết đầy đủ ở §10):

| Mốc | Timeframe cùng đóng | `affected` (số nến ghi) | So với cycle thường | Kết quả |
|---|---|---|---|---|
| 00:00:00 UTC (nửa đêm) | D1 + toàn bộ 14 timeframe khác (1440 chia hết cho mọi timeframe đang dùng) | 69-98 (ngày thường) | Cao gấp 5-13 lần | 165/165 ok, 0 failed |
| 06:45:00 UTC | M5+M15+M45 (11 symbol) | 39 | Cao gấp 3-10 lần | 165/165 ok, 0 failed |

Cycle nửa đêm chạy lâu hơn (~11-15s so với ~8s bình thường) nhưng không rớt pair
nào, không lỗi nào.

---

## 9. Quan sát data thật trên TradingView — việc con người, đã chốt

Đây là lớp kiểm cuối cùng không script nào thay được: cột "TradingView" ở §7 vẫn
là DP tự fetch bằng chính code của nó (`websocket.py`) — không phải nguồn độc
lập tuyệt đối nếu code đó có lỗi hệ thống trong cách hiểu response.

**Đã thống nhất với user:** user tự mở TradingView đối chiếu, đặc biệt là case
**SP35** (đang stale 8-11/15 timeframe tính tới cuối phiên — xem §11) để phân
biệt "thị trường đóng cửa thật" hay "bug thật". Chưa có kết quả tại thời điểm
viết báo cáo này.

---

## 10. Thống kê tải hệ thống — 14 ngày log (`dp_program_live.log` + `.log.1`)

9.233 cycle live trong khoảng 2026-09-02 → 2026-09-16.

**A. Trung bình số nến ghi theo phút-trong-giờ** (trung bình chung: 15.37/cycle):

| Phút | avg affected | max |
|---|---|---|
| :01 | 67.08 | 165 |
| :00 | 53.43 | 144 |
| :31 | 41.13 | 83 |
| :30 | 36.34 | 84 |
| (phút thường, vd :08, :09, :14...) | 7-8 | 25-34 |

→ Xác nhận rõ: mốc đầu giờ (`:00`/`:01`) và nửa giờ (`:30`/`:31`) luôn cao gấp
3-4 lần bình thường.

**B. Theo giờ-trong-ngày (chỉ cycle rơi vào :00/:01):** không có giờ nào nổi bật
kiểu "phiên London/NY mở cửa" — dao động khá đều 50-88 (ngoại trừ 22h UTC thấp
hẳn còn ~27, có thể liên quan rollover ngày của broker CFD, chưa kết luận). Vì
11 symbol đều là CFD chỉ số/hàng hoá/crypto, DP lấy theo lịch cố định chứ không
theo phiên giao dịch chính.

**C. Hiệu ứng thật sự mạnh: NGÀY TRONG TUẦN.** Riêng mốc 00:00 UTC theo từng
ngày — khớp 100% với lịch cuối tuần:

| Ngày | Thứ | affected |
|---|---|---|
| 2026-09-05 | Thứ 7 | 15 |
| 2026-09-06 | Chủ nhật | 11 |
| 2026-09-07 → 11 | Thứ 2-6 | 79, 94, 91, 84, 98 |
| 2026-09-12 | Thứ 7 | 14 |
| 2026-09-13 | Chủ nhật | 9 |
| 2026-09-14 → 16 | Thứ 2-4 | 69, 85, 84 |

Cuối tuần luôn thấp gấp 6-10 lần ngày thường — không phải bug, thị trường chỉ
số/hàng hoá CFD đóng cửa cuối tuần nên không có nến thật để đóng.

**D. Sức khoẻ hệ thống dưới tải cao:** chỉ **3/9.233 cycle (0.03%)** có
failed/deferred trong 14 ngày, và **không cái nào trùng mốc tải cao** (nằm ở giờ
lẻ 01:23, 03:41, 20:26) — tải cao không làm hệ thống dễ lỗi hơn. Thời gian xử lý
tăng rất nhẹ theo tải (9.9s → 16.2s trung bình khi affected tăng từ <10 lên
100+, tức tải tăng 10x nhưng thời gian chỉ tăng 1.6x), p95 cao nhất 26.5s — vẫn
cách xa giới hạn chu kỳ 120s rất nhiều.

**3 cycle có vấn đề, chưa điều tra (nằm ngoài phạm vi báo cáo này):**

| Thời điểm | affected | failed | deferred | duration |
|---|---|---|---|---|
| 2026-09-04 01:23:14 | 25 | 15 | 45 | 181.56s |
| 2026-09-07 03:41:36 | 0 | 30 | 135 | 12.30s |
| 2026-09-10 20:26:33 | 28 | 15 | 0 | 62.09s |

---

## 11. Vấn đề đang mở (chưa đóng tại thời điểm viết báo cáo)

1. **SP35 stale** — 8-11/15 timeframe của SP35 liên tục rơi vào trạng thái
   "stale" (620-1100+ phút không có nến mới), trong khi DP tự báo fetch thành
   công mỗi cycle (`FETCH_BATCH_COMPLETED`, không lỗi). 10 symbol còn lại hoàn
   toàn sạch. **User nhận trách nhiệm tự kiểm TradingView** để phân biệt thị
   trường đóng cửa thật hay bug — chưa có kết quả.
2. **2 sự cố log thật** (2026-09-04 01:23, 2026-09-07 03:41) — chưa điều tra
   nguyên nhân, không liên quan tải cao.
3. **`state_probe` và `pubsub_probe` đang tắt** — không ai giám sát real-time
   cho tới khi bật lại bằng `redis_probe.bat start`.

---

## 12. Rủi ro/giới hạn kiến trúc đã biết (không phải bug mới, cần nhớ khi debug tiếp)

Trích từ `docs/REDIS_PUBLISH_AUDIT_BRIEF.md` §13, vẫn còn nguyên ở thế hệ 5:

- **Không có tín hiệu để consumer (OG) tự phát hiện đang đọc sai thế hệ contract**
  — chính là nguyên nhân sự cố 8 giờ/18 signal rồi 43 phút/3 signal đã ghi nhận
  trước đó khi đổi contract.
- Reconcile không SCAN keyspace → pair bị loại khỏi universe live thì List+100
  Hash của nó **nằm rác vĩnh viễn**, không TTL.
- `DEL` rồi `RPUSH` trong Lua không rollback — script bị ngắt giữa chừng (OOM)
  có thể để lại pair mồ côi mà reconcile không tự phát hiện.
- `timeout_seconds: 0.3` chưa từng được đo tải thật với batch 100 nến hay
  reconcile pipeline 20 pair.
- Backfill/spool-replay không publish Redis ngay — chỉ được vá qua reconcile,
  trễ tối đa 30 phút.
- Không hỗ trợ Redis Cluster (key nến dựng động trong Lua, không khai báo qua
  `KEYS[]`).

---

## 13. Kết luận

Hệ thống DP → SQL → Redis đang chạy đúng, liên tục, độc lập, và dữ liệu đúng ở
mọi lớp đã kiểm được bằng công cụ (coverage, giá trị, thời điểm đóng nến, tải
cao nhiều timeframe cùng lúc) — không phát hiện bug thật nào của DP trong phiên
debug này. Lệch OHLC nhỏ quan sát được nhiều khả năng đến từ TradingView tự
revise dữ liệu lịch sử phía họ, không phải lỗi ghi nhận của DP. Việc còn treo là
case SP35 (cần xác nhận thị trường đóng cửa hay bug) và 2 sự cố log chưa điều
tra — cả hai đều đã bàn giao rõ trách nhiệm cho bước tiếp theo.
