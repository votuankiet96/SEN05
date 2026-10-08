# DPS (Data Provider Simulator) — research notes

Cập nhật: 2026-10-04 · Trạng thái: **v1 đã triển khai và kiểm chứng (mục 9)**; mục 0–8 là nghiên cứu nền tảng đã dẫn tới thiết kế
Mô tả đầy đủ module (bối cảnh, input, xử lý, output, cách chạy): xem [README.md](README.md).
Mức tin cậy: **[KC]** đã kiểm chứng trực tiếp (code / SQL / chạy thử) · **[SL]** suy luận · **[CHỜ]** chưa kiểm chứng hoặc đang nghiên cứu

---

## 0. Mục tiêu và chỉ đạo của operator

**Yêu cầu gốc (2026-10-04).** DPS lấy dữ liệu trong SQL Server và đẩy lên Redis "y như thực" để mô phỏng dữ liệu
real-time vận hành thế nào. Ví dụ: M5 + M15, delay 1 giây → giây 1 đẩy M5 thứ nhất, giây 2 đẩy M5 thứ hai, đến cây
thứ 3 đẩy cả M5 và M15 trong một lượt. Phần lõi là một thuật toán để hệ thống biết chính xác tại thời điểm nào thì lấy
nến nào lên Redis.

**Mục đích thật (operator làm rõ).** Chiến lược đã chạy live và không thể chờ thêm một năm; đã có dữ liệu quá khứ nên
muốn *mô phỏng lại toàn bộ tiến trình như thật*: replay lịch sử SQL qua đúng pipeline (Redis → OG / strategy). Vì vậy
DPS là **time master** của lần replay (giống kernel của một backtest engine), không chỉ là script đẩy dữ liệu.

**Chỉ đạo đã nhận**
1. Chưa viết code; research phương pháp/engine chuẩn trước. Thư mục làm việc `dp_program/dps/`; file này lưu nền tảng nghiên cứu.
2. Bốn điểm chốt trước đó (nghĩa của "pub", Redis đích, gate tuổi nến của consumer, xử lý khoảng trống) được operator xem là
   "phần đẩy qua Redis, dễ làm, chưa quan trọng" → hoãn, không hỏi lại. Mặc định đề xuất ở mục 5.
3. **Môi trường thử nghiệm (operator chỉ định 2026-10-04):** SQL và Redis lấy từ dp_program — operator cho phép đọc cấu hình để chép thông tin kết nối vào `dps/config.yaml`
   (chép bằng code, không in ra). **Redis thử nghiệm = db15** của cùng máy chủ Redis với production (OG8): DPS chỉ được ghi vào db15, tuyệt đối không db0.
   (Trước đó operator nói "có kênh Redis, sẽ cung cấp sau"; nay đã chỉ định db15.)
4. **Bỏ qua schema `tick`** trong SQL (tồn tại nhưng ngoài phạm vi: không khảo sát, không dùng).
5. **Giả định SQL đúng 100%**: nến nào có là sự thật; thiếu nến = thị trường đóng cửa / không phát sinh; DPS không bù,
   không sửa, không nghi ngờ dữ liệu.
6. **Phạm vi tập trung (2026-10-04):** 11 symbol của live (FR40, DE40, HK50, J225, SP35, UK100, US500, US100, US30, GOLD, BTCUSD) × **8 khung: M10, M20, M30, M45, H1, H2, H3, H4** = 88 pair
   (bỏ M5, M15, M90, H6, H8, D1, W). Đã cấu hình trong `dps/config.yaml` (đọc danh sách symbol từ live config).

---

## 1. Hợp đồng Redis của live — DPS phải khớp **[KC]**

Nguồn sự thật = code + test (AGENTS.md: code/test ưu tiên hơn docs). Đọc ngày 2026-10-04.

| Hạng mục | Hợp đồng hiện tại | Nguồn |
|---|---|---|
| Key List | `{key_prefix}_{SYMBOL}_{TF}`, prefix mặc định `L_CANDLE` (cấu hình được) | `live.py:141-144`, `configuration.py:186` |
| Key Hash | key List + `":"` + stamp (một phép nối) | `live.py:141-144` |
| Stamp | `YYYY-MM-DD HH:MM:SS`, UTC, = giờ **mở** nến; phần tử List = field `timestamp` = đuôi key Hash | `live.py:147-150` |
| Hash | 6 field: `timestamp, open, high, low, close, time_update` | `live.py:52` |
| Giá | tối đa 2 số lẻ ROUND_HALF_UP, bỏ số 0 thừa (`7649.9`, `25653`), không bao giờ dạng mũ | `live.py:159-172` |
| List | **giảm dần: index 0 = nến MỚI NHẤT**, đuôi = cũ nhất (đổi 2026-09-28) | `live.py:94-109`, `test_live_redis_regression.py:322` |
| Cửa sổ | `redis.bars_per_snapshot` = 1200; phần dư ở đuôi bị RPOP + DEL Hash | `live.py:104-109` |
| TTL | Hash 7 ngày (604800 s); List không TTL | `live.py:91`, `configuration.py:192` |
| `time_update` | giờ máy lúc live đẩy nến lên Redis | `live.py:181-190` |
| Thông báo | không PUBLISH; OG nghe keyspace notification `__keyspace@0__:L_CANDLE_*`, lọc `hset` | `live.py:46-50`, `OG_REDIS_SYNC_PROMPT.md` |
| Quy tắc "đã đóng" | `open_time + Minutes <= now`; `validate_candles(..., now=)` là hàm thuần đã nhận tham số `now` | `pipeline.py:72-101` |
| Cách ghi | 1 Lua script mỗi pair, ≤ 100 nến mỗi lần gọi | `live.py:61-111`, `225-239` |

**Hành vi của Lua cần nhớ**
- HSET (+EXPIRE) **chỉ khi giá khác** hash hiện có. Phát lại lên key đã có cùng giá **không sinh sự kiện `hset`** →
  consumer không được kích hoạt. Mỗi lần replay phải bắt đầu từ namespace sạch (hoặc nối tiếp từ đầu List).
- Nến cũ hơn đầu List bị **chèn vào giữa List** (`insert_sorted`) → tuyệt đối không replay vào Redis production
  (trùng tên key `L_CANDLE_*` với dữ liệu thật).
- Nạp đầy (165 pair × ~1200 nến) sinh khoảng **200.000 sự kiện `hset`** trong vài chục giây; OG khuyến nghị listener gộp/bỏ qua
  đợt bùng phát này. List mới nạp có 1199 nến (nến đang chạy chưa đóng), lên 1200 sau lần đóng kế tiếp
  (`OG_REDIS_SYNC_PROMPT.md`, phần cập nhật 2026-09-28).
- Pair có lịch sử thật ngắn hơn 1200 (vd khung W) thì List ngắn hơn — không phải lỗi.

**Docs cũ gây nhầm** — `REDIS_DESIGN_REPORT.md:257`, `REDIS_MECHANISM_REPORT.md:94,129`, `DEBUG_MONITORING_REPORT.md:54` vẫn ghi
List "tăng dần" (hợp đồng trước 2026-09-28; cỡ cửa sổ 500 cũng thuộc thời đó). Bản đúng: `AGENTS.md`, `ARCHITECTURE.md`,
`SYSTEM_OVERVIEW.md`, `OG_REDIS_SYNC_PROMPT.md`.

**Config live hiện hành (không bí mật)** — live.symbols = 11, live.timeframes = 15, live.interval_minutes = 2,
live.bars_per_request = 3, redis.bars_per_snapshot = 1200, redis.hash_ttl_seconds = 604800,
backfill.lookback_days = 60, backfill.schedule_utc = 11:11, 15:15, 19:19, 23:23, 03:03, 07:07.

**Quy ước vận hành của engine hiện tại (DPS nên theo cho nhất quán)** — `runtime.py`: khóa interprocess một-instance theo mode,
`state_<mode>.json` ghi atomic kèm heartbeat, dừng bằng file `stop_<mode>.request` hoặc SIGINT/SIGTERM, log có cấu trúc qua `log_event`.

---

## 2. SQL Server **[KC]** (đo 2026-10-04, chỉ đọc)

### 2.1 Cách truy cập
Dùng chính `load_config()` + `sql_connector.get_connection()` của dp_program (`PYTHONPATH=core_program/src`), nạp cấu hình
production `run_dp/config.yaml` bằng code (không đọc/in nội dung), session `READ UNCOMMITTED`, `LOCK_TIMEOUT 5000`, chỉ SELECT.
Database `SEN05_AutoTrading`, SQL Server 2022 (16.0.4265.3) Developer Edition.

### 2.2 Cấu trúc
- `DWH.Fact_OHLCV` — 17.658.122 dòng. Cột: FactID (PK identity), SymbolID, TimeframeID (tinyint), DateKey (= ngày UTC của BarTime,
  tra qua `DWH.Dim_Date` 2008-01-01 → 2035-12-31), **BarTime datetime2 = giờ MỞ nến UTC**, Open/High/Low/Close decimal(18,8),
  Volume decimal(20,4) NULL, TickCount, CreatedAt.
- Index: `UQ_Fact_OHLCV (SymbolID, TimeframeID, BarTime)`; **`IX_Fact_Sym_TF_Time (SymbolID, TimeframeID, BarTime DESC) INCLUDE (OHLC, Volume)`**
  — covering cho đọc theo cặp + khoảng thời gian; `IX_Fact_DateKey (DateKey, TimeframeID, SymbolID)`.
- `DWH.Dim_Symbol` 37 dòng: 11 live (9 Indice: FR40 DE40 HK50 J225 SP35 UK100 US500 US100 US30; Metal: GOLD; Crypto: BTCUSD) và 26 FOREX
  (chỉ lịch sử). BrokerChannel đều là CAPITALCOM. `DWH.Dim_Timeframe` 15 dòng, Minutes = 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 1440, 10080.
- Staging `SEN.TF_*` (15 bảng: RawID, SymbolID, BarTime, OHLC, Volume, ReceivedAt, IsProcessed). Đối tượng khác: `DWH.usp_LoadDirect`
  (contract v4, extended property `DPContractVersion = 4`), `DWH.usp_AggregateFromStaging`, `MART.usp_GetLatestCandles`, `MART.v_OHLCV`.

### 2.3 Cách dữ liệu được ghi
`DWH.usp_LoadDirect` chạy trong một transaction (XACT_ABORT): (1) UPDATE nến đã có nếu OHLC/Volume khác → **Fact giữ phiên bản mới nhất
của mỗi nến**, không có lịch sử hiệu chỉnh; (2) INSERT nến mới (NOT EXISTS theo SymbolID + TimeframeID + BarTime, DateKey tra qua Dim_Date).
Chỉ nhận dòng staging `IsProcessed = 1`. Nguồn ghi duy nhất là backfill. Nguồn gốc nến: TradingView (CAPITALCOM), mỗi khung lấy trực tiếp, không gộp từ khung nhỏ.

`MART.usp_GetLatestCandles(@Symbol, @Timeframe, @Rows)` trả N nến mới nhất và **không có tham số as-of** → thành phần nào đọc SQL trực tiếp
sẽ thấy dữ liệu thật mới nhất, tức "tương lai" của lần replay (xem mục 6).

### 2.4 Quy mô cho replay 1 năm (365 ngày: 2025-10-04 → 2026-10-04, 11 symbol × 15 khung)

| Khung | Nến trong 365 ngày | Liền mạch từ |
|---|---:|---|
| M5 | 506.302 | ~2026-02-15 (FR40 01-26, HK50 02-08, BTCUSD 03-15; SP35 từ 2025-11-03) |
| M10 | 351.980 | ~2025-11-09 (HK50/GOLD 11-02; BTCUSD 2026-01-04; FR40/SP35 đủ năm) |
| M15 | 261.399 | đủ 365 ngày |
| M20 | 196.267 | đủ |
| M30 | 130.867 | đủ |
| M45 | 91.324 | đủ |
| H1 | 65.454 | đủ |
| M90 | 45.719 | đủ |
| H2 | 33.854 | đủ |
| H3 | 22.377 | đủ |
| H4 | 16.565 | đủ |
| H6 | 11.209 | đủ |
| H8 | 8.545 | đủ |
| D1 | 2.935 | đủ |
| W | 561 | đủ |
| **Tổng** | **1.745.358** | |

- **Đọc**: 165 truy vấn theo cặp trên index covering mất 26,5 s (~66.000 dòng/s) → dựng cả lịch phát một năm trong chưa đầy một phút.
- **Mốc phát** (`release = BarTime + Minutes`): **92.507 mốc/năm, mọi mốc đều nằm trên ranh giới 5 phút** (0 mốc lệch lưới).
  Số nến mỗi mốc: trung bình 18,9 · p50 = 11 · p90 = 44 · p99 = 102 · **max = 120** (vd 09:00 UTC các ngày thứ 3–5 tháng 3–4/2026).
  Mỗi pair tối đa 1 nến mỗi mốc ⇒ một tick ≤ 120 lần ghi.
- **Lưới 5 phút**: 105.120 ô/năm; 92.507 ô có nến (88,0%), 12.613 ô trống (chủ yếu cuối tuần, với tập 11 symbol đã chọn).
- **Thời gian replay 1 năm**:

| delay mỗi tick | toàn bộ lưới (chờ cả ô trống) | bỏ qua ô trống |
|---:|---:|---:|
| 1,0 s | 29,2 giờ | 25,7 giờ |
| 0,5 s | 14,6 giờ | 12,8 giờ |
| 0,1 s | 2,9 giờ | 2,6 giờ |
| 0,05 s | 1,5 giờ | 1,3 giờ |

### 2.5 Độ sâu lịch sử
- M5…H8 của 11 symbol bắt đầu 2017-05 (HK50: 2020-06-29); D1 và W bắt đầu 2008–2017; kết thúc 2026-10-02 (BTCUSD tới 2026-10-04).
- **M5** chỉ có các mảng rời rạc 2017–2019 rồi liền mạch từ 2026 (≈ 7,5 tháng). **M15** còn lỗ nhiều năm ở một số symbol
  (J225 2024; UK100 2023–2024; US500 2022–2024; US100/US30 2021–2024; GOLD 2021–2024; BTCUSD 2018–2024). **H1/H4** liền mạch 2017 → 2026.
- Hệ quả: replay một năm đủ cả 15 khung chỉ thực hiện được cho M15 trở lên; engine **không được giả định mọi khung phủ cùng một cửa sổ** —
  mỗi pair có cửa sổ riêng, trước nến đầu tiên thì List ngắn hơn 1200 (đúng như live với pair lịch sử ngắn).
- FOREX: M5 chỉ từ 2026-02 (ngoài phạm vi live).

### 2.6 Neo phiên và DST (giờ mở nến, UTC, 365 ngày gần nhất)

| Khung | GOLD | DE40 / US100 / J225 / HK50 | BTCUSD | SP35 |
|---|---|---|---|---|
| H4 | 02,06,10,14,18,22 (mùa hè) · 03,07,11… (mùa đông) | 01,05,09,13,17,21 · 02,06… | 01,05,09,13,17,21 · 02,06… | 06,10,14 · 07,11,15 |
| H8 | 06,14,22 · 07,15,23 | 05,13,21 · 06,14,22 (+15,23) | 05,13,21 · 06,14,22 | 06,14 · 07,15 |
| D1 | 22:00 (×169) · 23:00 (×88) | 21:00 · 22:00 · 23:00 | 21:00 · 22:00 | 06:00 · 07:00 |
| W | CN 22:00 · CN 23:00 | CN 22:00 · CN 23:00 | CN 21:00 · CN 22:00 | T2 06:00 · T2 07:00 |

⇒ Giờ mở nến của khung lớn **neo theo phiên và dịch ±1 giờ theo DST**; không thể suy ra bằng số học trên lưới UTC. Luôn dùng `BarTime` thật + `Minutes`.

### 2.7 Khoảng nghỉ lớn nhất giữa hai nến liên tiếp (M5, 365 ngày, giờ)
FR40 106,1 · SP35 84,1 · HK50 74,1 · GOLD 73,1 · DE40/UK100/US500/US100/US30 56,8 · J225 50,1 · BTCUSD 16,2 **[SL]** (cuối tuần + ngày lễ + bảo trì).
Theo chỉ đạo: đây là thị trường đóng cửa, không phải lỗi dữ liệu.

### 2.8 Nhất quán chéo khung
M15 và H1 lưu sẵn **khớp tuyệt đối** phép gộp từ M5 lưu sẵn (GOLD, DE40, BTCUSD, US100; 40 ngày gần nhất; 0 lệch O/H/L/C trên 2.650–3.813 nến M15
và 661–923 nến H1 so sánh được mỗi symbol) ⇒ phát nhiều khung cùng một tick là nhất quán với nhau.

---

## 3. Thuật toán đề xuất **[SL]**, kiểm chứng trên dữ liệu thật **[KC]**

### 3.1 Định nghĩa
- Nến c = (symbol, tf, BarTime, O, H, L, C). **Giờ phát** `release(c) = BarTime + Minutes(tf)` — đúng quy tắc "đã đóng" của hệ thật (`pipeline.py:101`).
- **Tick** = một giá trị `r` thuộc tập mọi giờ phát. `tick(r) = { c : release(c) = r }`, sắp theo (Minutes tăng dần, thứ tự symbol) để thứ tự tất định.
- Đồng hồ ảo `T` nhảy tới tick kế tiếp (next-event). Dữ liệu hiện có luôn nằm trên lưới 5 phút nên `T` luôn là bội của 5 phút.

### 3.2 Pipeline
1. **Planner** (hàm thuần, tất định): đọc nến theo cặp từ SQL (keyset hoặc cả cửa sổ), tính `release`, gộp theo tick bằng k-way merge/heap
   (mỗi pair đã sắp theo BarTime nên cũng đã sắp theo release). Có thể xuất lịch phát ra file và băm (hash) để chứng minh tái lập; `--dry-run` chỉ in lịch, không đụng Redis.
2. **Seeder**: tại `T0`, mỗi pair nạp tối đa 1200 nến đã đóng (`release ≤ T0`) bằng một lượt ghi theo đúng hợp đồng. Truy vấn sargable:
   `BarTime <= DATEADD(minute, -Minutes, @T0)` (không dùng biểu thức trên cột). Trạng thái Redis tại `T0` giống production tại thời điểm đó.
3. **Player**: với mỗi tick theo thứ tự → ghi mọi nến của tick → chờ theo chế độ pacing → tick kế.
4. **Writer**: dùng đúng Lua script/khóa/định dạng giá của live; mỗi tick ghi gộp trong **một MULTI/EXEC** (≤ 120 lệnh), không dùng pipeline thường vì không atomic (mục 4.8); `SCRIPT LOAD` trước để `EVALSHA` không lỗi `NOSCRIPT`.

### 3.3 Ví dụ trên dữ liệu GOLD thật (M5 + M15, delay 1 s/tick) **[KC]**

Phiên thường, 2026-09-30:

| Tick | Giây thật | Giờ ảo T (UTC) | Phát lên Redis |
|---:|---:|---|---|
| 1 | 1 | 13:05 | M5@13:00 |
| 2 | 2 | 13:10 | M5@13:05 |
| 3 | 3 | 13:15 | M5@13:10 **+ M15@13:00** (cùng một lượt) |
| 4 | 4 | 13:20 | M5@13:15 |
| 5 | 5 | 13:25 | M5@13:20 |
| 6 | 6 | 13:30 | M5@13:25 **+ M15@13:15** |
| 7 | 7 | 13:35 | M5@13:30 |
| 8 | 8 | 13:40 | M5@13:35 |
| 9 | 9 | 13:45 | M5@13:40 **+ M15@13:30** |
| 10 | 10 | 13:50 | M5@13:45 |

(`@hh:mm` = giờ mở nến.) Đúng ví dụ của operator: tick 3 phát cả M5 và M15.

Cuối tuần 2026-10-02: tick 6 (T = 21:00) phát M5@20:55 + M15@20:45 — nến cuối của GOLD tuần đó. Sau đó không có nến cho tới khi mở cửa lại
Chủ nhật 22:00/23:00 UTC: ≈ 49 giờ = 588 ô 5 phút trống (592 tính tới 22:20 CN).
Pacing theo tỷ lệ thời gian: im lặng ≈ 9,8 phút ở tốc độ 300×; pacing theo tick: không im lặng.

### 3.4 Pacing (đề xuất các chế độ)
- **P1 — delay mỗi tick** (mô hình trong ví dụ operator): sau mỗi tick chờ `d` giây; khoảng nghỉ không tốn thời gian. Dữ liệu liên tục 5 phút: tốc độ = 300/d lần.
- **P2 — tỷ lệ thời gian**: `deadline_k = t_wall0 + (r_k − r_0)/speed`, đồng hồ đơn điệu, deadline tuyệt đối (không cộng dồn sleep nên không trôi). Giữ nguyên tỷ lệ khoảng nghỉ.
- **P3 — lock-step**: không chờ theo giờ; chờ ack của consumer rồi mới sang tick sau → tái lập được, tốc độ tối đa. Cần consumer ghi ack.
- **P4 — real-time (speed = 1)**: để thử với consumer dùng giờ thật, chỉ khả thi với cửa sổ ngắn.
- **P5 — bước**: mỗi lệnh một tick (gỡ lỗi), kèm pause/seek — theo `tcpreplay -o` và kdb+ (mục 4.10).

### 3.5 Phát đồng hồ ảo cho consumer **[SL]**
Replay nhanh hơn thật thì mọi logic dùng giờ máy của consumer (hết hạn tín hiệu, lọc phiên, cooldown) sai. Cách chuẩn là clock injection: DPS ghi
`dps:clock` (giờ ảo, số thứ tự tick, chế độ, run_id) **trong cùng MULTI** với nến của tick đó để consumer đọc nhất quán, và (nếu P3) đọc `dps:ack`.

### 3.6 Tính tất định và khôi phục **[SL]**
Cùng đầu vào → cùng lịch phát (băm lịch + manifest cấu hình/phiên bản). Checkpoint (`run_id`, số thứ tự tick, giờ ảo) ghi trong cùng MULTI với nến ⇒ crash giữa chừng
không để lại tick nửa vời; chạy lại tiếp từ checkpoint, ghi idempotent nhờ Lua.

---

## 4. Nghiên cứu web — nền tảng lý thuyết

Nguồn: agent A (engine backtest, mô phỏng sự kiện rời rạc, tái lập; 149 lượt tra cứu) và các trang tài liệu chính thức do tôi tự tra cứu lại ngày 2026-10-04
(Redis, NautilusTrader, QuantConnect). Trích dẫn qua công cụ WebFetch là bản tóm tắt của công cụ; các trang Redis hiển thị nguyên văn.
Nhãn: **[CS]** nguồn chính (docs chính thức / source code) · **[TC]** thứ cấp · **[SL]** suy luận của ta · **[?]** chưa kiểm chứng. Hướng B (agent B: giờ ảo cho consumer, Redis bổ sung,
sản phẩm replay, first-seen vs đã hiệu chỉnh, quy ước nhãn thời gian) ở mục 4.10.

### 4.1 Kết luận rút ra cho thiết kế DPS

| # | Quyết định | Căn cứ |
|---|---|---|
| 1 | Lịch phát theo **next-event** (nhảy giữa các mốc `open + tf`); pacing theo giờ thật chỉ là lớp điều tốc | Law; LEAN time provider backtest; Nautilus `advance_time`; Larson & Odoni |
| 2 | Nến chỉ hiện ở **giờ đóng**; đổi nhãn "mở" → "đóng" đúng một lần | LEAN `EndTime`; Nautilus `ts_init = ts_event + interval`; Freqtrade cảnh báo look-ahead khi ghép theo giờ mở |
| 3 | Mọi nến cùng mốc phát là **một nhóm nguyên tử** với thứ tự toàn phần có tài liệu (giờ đóng, khung nhỏ → lớn, symbol) | LEAN TimeSlice; Nautilus stable sort; NinjaTrader (series chính trước) |
| 4 | Gộp luồng bằng **heap k-way merge** khóa (giờ đóng, bậc, chỉ số luồng) | Nautilus `data_iterator.rs`; `heapq.merge`; SimPy |
| 5 | **Không fill-forward**: thiếu nến là khoảng trống (live không fill-forward) | LEAN coi fill-forward là tính năng riêng; Nautilus không forward-fill bar ngoài [?] |
| 6 | **Tất định + manifest** (phiên bản code, cấu hình, khoảng dữ liệu, băm lịch); khóa Redis tất định để chạy lại ghi đè | FoundationDB; TigerBeetle; Nautilus (seed + binary + config) |
| 7 | Kiểm thử: parity với live và **truncation test** (quyết định tại T không đổi khi cắt dữ liệu sau T) | Freqtrade lookahead-analysis; LMAX; QuantConnect reconciliation |
| 8 | Ghi mỗi tick bằng **MULTI/EXEC hoặc Lua** (atomic), không dùng pipeline thường; Pub/Sub và keyspace là at-most-once ⇒ lock-step + ack, không dựa vào việc consumer nhận đủ sự kiện | Redis docs (4.8) |
| 9 | **Phát đồng hồ ảo cho consumer** (clock seam): "now" = giờ phát của từng sự kiện, không dùng đồng hồ co giãn tự chạy | ROS 2 `/clock`; Java `Clock`; Nautilus; MT5 tester; Aeron Cluster (4.10) |
| 10 | **Lock-step có ack nhân quả** cho chuỗi consumer (strategy → OG); ack qua key hoặc Streams, không qua Pub/Sub | HLA time advance grant; PDES conservative (4.10) |
| 11 | **Điều khiển** pause / bước / seek (seek = dựng lại trạng thái rồi tiếp tục) và log ánh xạ giờ ảo ↔ giờ thật | tcpreplay; kdb+ tick; Databento (4.10) |
| 12 | **Seed không được làm tràn buffer Pub/Sub** của consumer (cứng 32 MB, mềm 8 MB/60 s) | Redis clients docs (4.10) |

### 4.2 NautilusTrader **[CS]**
- Thứ tự dữ liệu: "Data is ordered by `ts_init` using a stable sort." (nautilustrader.io/docs/latest/concepts/data/). Gộp nhiều luồng bằng min-heap theo khóa replay `ts_init`
  (`crates/backtest/src/data_iterator.rs`, github.com/nautechsystems/nautilus_trader).
- Đồng hồ: "Time advances only when the backtest engine tells it to via `advance_time(to_time_ns)`" (nautilustrader.io/blog/clocks-and-timers/).
  Trong một mốc: "all callbacks at timestamp T execute first, then venues are settled for T before advancing to T+1" (…/concepts/backtesting/execution-flow/).
- Bar: "each bar's initialization timestamp (`ts_init`) must represent the close of the interval"; bar gắn nhãn mở thì "set `ts_init = ts_event + interval_ns`" (…/backtesting/bar-execution/).
  `time_bars_timestamp_on_close` mặc định True: "ts_event is the bar close time" (concepts/data — tôi đã xác nhận lại).
- Khoảng trống: bar nội bộ phát bar rỗng khi `build_with_no_updates`; forward-fill bar ngoài không thấy tài liệu **[?]**. Nhiều khung cùng một mốc: không có quy tắc rõ **[?]**
  (stable sort + chỉ số luồng ngụ ý thứ tự chèn); thứ tự data so với timer cùng timestamp là vấn đề mở (github.com/nautechsystems/nautilus_trader/issues/4681) **[TC]**.
- Tất định: "Two runs with the same seed, binary, configuration, and platform produce identical observable behavior" (docs/concepts/dst.md) — chỉ cho harness Rust.

### 4.3 QuantConnect LEAN **[CS]**
- "The `Slice` object this method receives represents all of the data at a moment of time, a time-slice." và "Once your algorithm reaches the `EndTime` of a data point, LEAN sends the data to your `OnData` method."
  Với bar intraday, EndTime là "the beginning of the next period" (quantconnect.com/docs/v2/writing-algorithms/key-concepts/time-modeling/timeslices — tôi đã xác nhận lại).
  "LEAN passes the bar to your algorithm at the end time so that you don't receive the bar before it was actually available" (…/time-modeling/periods).
- Source (`SubscriptionSynchronizer.Sync`, github.com/QuantConnect/Lean, Engine/DataFeeds; agent A đọc code): một frontier lấy từ time provider; mỗi subscription được rút cạn
  khi `EmitTimeUtc <= frontierUtc`; mọi packet vào một `TimeSlice`. Time provider của backtest lấy **min thời điểm phát kế tiếp** (next-event); live dùng `RealTimeProvider`.
- Cùng symbol ở hai độ phân giải: `TimeSliceFactory` giữ độ phân giải cao nhất. Ties: "then by unique id so that for scheduled events in the same time respect their creation order, so its deterministic" (`BacktestingRealTimeHandler.cs`).
- Fill-forward: "if there is no data point for the current slice, LEAN uses the previous data point" (…/securities/requesting-data) — tính năng riêng, DPS không bắt chước.
- Thứ tự giữa các subscription có cùng EndTime: không tìm thấy quy tắc **[?]**.

### 4.4 Zipline, backtrader, hftbacktest **[CS]**
- Zipline `MinuteSimulationClock` (`sim_engine.pyx`): phát (timestamp, SESSION_START / BAR / SESSION_END) theo lịch mở/đóng; bước cố định 1 phút, chỉ trong phiên; bar "labeled with the end of the bar" (github.com/quantopian/trading_calendars).
- backtrader: Replayer — "Only when the bar is complete will the 'length' of the data be changed effectively delivering a closed bar", tức strategy thấy bar đang hình thành; Resampler chỉ đẩy bar sau `_checkbarover`
  (bar hoàn chỉnh). DPS đi theo kiểu Resampler (chỉ bar đã đóng) — khớp live.
- hftbacktest: độ trễ feed, order-entry và response được mô hình riêng; "The exchange timestamp must be earlier than the local timestamp" (hftbacktest.readthedocs.io/en/latest/data.html)
  ⇒ độ trễ tới của nến nên là tùy chọn riêng, không trộn vào lịch phát.

### 4.5 Look-ahead và thứ tự cùng mốc **[CS]**
- Freqtrade `merge_informative_pair`: "Since dates are candle open dates, merging a 15m candle that starts at 15:00, and a 1h candle that starts at 15:00 will result in all candles to know the close at 16:00 which they should not know."
  (freqtrade.io/en/stable/lookahead-analysis/) — đúng lỗi nhãn mở/đóng; xác nhận quy tắc `BarTime + Minutes`. Test của họ so sánh dữ liệu đầy đủ với dữ liệu bị cắt (baseline vs sliced).
- Pine Script: lookahead không offset "will return data from the future on historical bars, which is dangerously misleading" (tradingview.com/pine-script-docs/concepts/repainting/).
- NinjaTrader: "primary bars series will always be processed first, followed by the secondary bars series (regardless of the period value used)"; thứ tự lịch sử "is NOT guaranteed to be the same sequence that these events occurred in real-time"
  (docs.ninjatrader.com/ninjascript/multi_time_frame_instruments). Không có chuẩn chung **[?]**.
- Chính sách DPS: tại mốc T mọi nến đã đóng (mọi khung) hiện cùng lúc, như live thật (M5 và M15 cùng đóng); kiểm bằng parity với live.

### 4.6 Mô phỏng sự kiện rời rạc **[CS]**
- Law (averill-law.com/types-of-simulation-modeling/): đồng hồ được "advanced from time 0 to the time of the first most-imminent event, then to the time of the second most-imminent event"; fixed-increment là "some fixed increment of time delta-t (e.g., one minute)".
- Larson & Odoni, MIT (web.mit.edu/urban_or_book/www/book/chapter7/7.3.html): mô phỏng event-paced "not waste any effort dealing with times at which 'nothing occurs.'"
- Tổng quan **[TC]**: fixed-increment coi mọi sự kiện trong một khoảng xảy ra ở cuối khoảng (gây sai số) và buộc phải quy định thứ tự sự kiện đồng thời; next-event là cách phổ biến nhất.
- Python `heapq` (docs.python.org/3/library/heapq.html): "The entry count serves as a tie-breaker so that two tasks with the same priority are returned in the order they were added"; `heapq.merge` "assumes that each of the input streams is already sorted". SimPy: `heappush(self._queue, (self._now + delay, priority, next(self._eid), event))`.
- Lưới 5 phút cố định chỉ là hệ quả của dữ liệu (mọi mốc đều là bội của 5 phút, mục 2.4), không phải cơ chế cần thiết.

### 4.7 Tái lập, tất định, parity **[CS]**
- FoundationDB: "Determinism is crucial in that it allows perfect repeatability of a simulated run" (apple.github.io/foundationdb/testing.html). TigerBeetle: "deterministic based on a seed number and the Git commit" (docs/internals/vopr.md).
- Nautilus nhận diện một lần chạy bằng (seed, binary hash, configuration hash). Băm chính lịch phát: không tìm thấy nguồn — là suy rộng của ta **[?]**.
- LMAX: "copies the sequence of events to their development environment and replays them there" (martinfowler.com/articles/lmax.html).
- QuantConnect: "If your algorithm is perfectly reconciled, it has an exact overlap between its live and OOS backtest equity curves" (quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation) — ý tưởng đối chiếu live với replay.
- Idempotence (redis.io/docs/latest/develop/data-types/streams/idempotency/): "handling the same message multiple times produces the same system state as handling it once".

### 4.8 Redis — tài liệu chính thức **[CS]** (tôi tự xác nhận 2026-10-04)
- Lua: "Redis guarantees the script's atomic execution. While executing the script, all server activities are blocked during its entire runtime. These semantics mean that all of the script's effects either have yet to happen or had already happened."
  (redis.io/docs/latest/develop/programmability/eval-intro/)
- MULTI/EXEC: "All the commands in a transaction are serialized and executed sequentially. A request sent by another client will never be served **in the middle** of the execution of a Redis Transaction."
  (redis.io/docs/latest/develop/using-commands/transactions/). Script cũng là transactional: "Everything you can do with a Redis Transaction, you can also do with a script".
- Pipeline thường **không** atomic: "The commands in a pipelined request run in the order they are sent, but other clients' commands may be interleaved for execution between these." (eval-intro).
  `SCRIPT LOAD` trước để `EVALSHA` không lỗi `NOSCRIPT` trong pipeline hoặc MULTI/EXEC ⇒ mỗi tick ghi trong một MULTI/EXEC.
- Keyspace notification: "Redis Pub/Sub is *fire and forget*; that is, if your Pub/Sub client disconnects, and reconnects later, all the events delivered during the time the client was disconnected are lost."
  Mặc định tắt, bật bằng `notify-keyspace-events`, cần `K` hoặc `E`; `h` = hash commands; "HSET, HSETNX and HMSET all generate a single `hset` event"; "LPUSH and LPUSHX generates a single `lpush` event, even in the variadic case";
  "all the commands generate events only if the target key is really modified"; trong cluster sự kiện là theo từng node (redis.io/docs/latest/develop/pubsub/keyspace-notifications/).
- Pub/Sub: "Redis' Pub/Sub exhibits _at-most-once_ message delivery semantics … If the subscriber is unable to handle the message (for example, due to an error or a network disconnect) the message is forever lost.";
  "Subscribers receive the messages in the order that the messages are published." (redis.io/docs/latest/develop/pubsub/).
- **Hệ quả cho Lua của live**: docs yêu cầu "all names of keys that a script accesses must be explicitly provided as input key arguments" và "Scripts **should never** access keys with programmatically-generated names".
  Script live sinh tên key Hash theo chương trình (`candle_prefix .. stamp`) ⇒ chỉ chạy đúng trên Redis **standalone**, không phải cluster. Redis đích của operator phải là standalone (hoặc DPS phải đổi cách ghi).

### 4.9 Chưa kiểm chứng hoặc chưa tới
Law & Kelton, Banks et al. (PDF không đọc được); trang Databento; quy tắc thứ tự cùng EndTime giữa các subscription của LEAN; clock Zipline cho daily; nguồn gọi tên "parity testing".

### 4.10 Hướng B — giờ ảo cho consumer, Redis bổ sung, sản phẩm replay, first-seen vs đã hiệu chỉnh, quy ước nhãn thời gian

Nguồn: agent B (152 lượt tra cứu); trích dẫn nguyên văn từ trang đã tải; nhãn như đầu mục 4.

**Giờ ảo cho tiến trình riêng biệt [CS]**
- Clock seam: Java `Clock` tồn tại để "allow alternate clocks to be plugged in as and when required" (docs.oracle.com/en/java/javase/21/docs/api/java.base/java/time/Clock.html);
  Nautilus: "Time advances only when the backtest engine tells it to via `advance_time(to_time_ns)`".
- Dịch vụ đồng hồ mô phỏng: ROS 2 — "The time abstraction can be published by one source on the /clock topic"; sai số của thời gian co giãn là "proportional to the increase in the rate at which simulated time
  advances compared to real time (the 'real time factor')" (design.ros2.org/articles/clock_and_time.html) ⇒ tăng tốc càng cao, đồng hồ co giãn tự chạy càng lệch; nên đặt "now" = giờ phát của từng sự kiện.
- Giả lập giờ ở mức tiến trình: libfaketime "supposed to work on Linux and macOS" (`+1y x2` làm "the clock run twice as fast"); time-machine: "Other processes are not affected."; freezegun: "time.monotonic()
  and time.perf_counter() will also be frozen" ⇒ không hợp với consumer riêng trên Windows → cần clock injection trong consumer.
- Lock-step: HLA — "the federate will receive a timeAdvanceGrant() callback", lookahead là "The lookahead period promised by this federate"
  (cs.cmu.edu/afs/cs/academic/class/15413-s99/www/hla/doc/rti_synopsis/06-Time_Management/Time_Management.html). PDES (ACM TOMACS 2022, doi.org/10.1145/3505248): "conservative methods that require lookahead
  but not rollback, and optimistic methods that require rollback but not lookahead". DPS biết trước toàn bộ lịch ⇒ lookahead chính xác (mốc kế tiếp), nên đồng bộ conservative (lock-step) là tự nhiên **[SL]**. Chi tiết null-message **[TC]**.
- Harness giao dịch: MT5 tester — "TimeLocal() is always equal to the server time TimeTradeServer()" (mql5.com/en/docs/runtime/testing), tức trong mô phỏng giờ cục bộ = giờ ảo;
  Aeron Cluster — "Use this value as the timestamp within your application state, so it will be consistent under replay." (github.com/aeron-io/aeron/wiki/Cluster-Tutorial).

**Redis bổ sung [CS trừ khi ghi khác]**
- Thời điểm bắn sự kiện so với MULTI/Lua: docs lõi không nói. Trang Redis Triggers (module Redis Stack, đã deprecated — không phải keyspace notification lõi) viết "in case of a multi/exec or Lua function,
  the notifications are fired at the end of the transaction". Thực tế consumer chỉ xử lý được lệnh của mình sau khi script/transaction kết thúc (Redis đơn luồng) **[SL]**.
- Replica: không có tuyên bố chính thức; `src/notify.c` không kiểm tra master/replica; wiki Lettuce: "Each Redis server will emit keyspace events." **[TC]**.
- Subscriber chậm: "Pub/Sub clients have a default hard limit of 32 megabytes and a soft limit of 8 megabytes per 60 seconds"; vượt thì "the client connection is closed" (redis.io/docs/latest/develop/reference/clients/).
- Streams (chỉ ghi chú): "Messages in streams are persisted, and support both at-most-once as well as at-least-once delivery semantics"; `XREADGROUP` giữ PEL "a list of message IDs delivered but not yet acknowledged" tới khi `XACK`.
- TTL theo giờ máy chủ: "the time is flowing even when the Redis instance is not active" (redis.io/docs/latest/commands/expire/). Bảng sự kiện: "RESTORE generates a restore event"; "COPY generates a copy_to event"
  ⇒ seed bằng RESTORE/COPY không sinh `hset` **[SL]**.

**Sản phẩm replay [CS]**
- tcpreplay: mặc định phát "at the speed at which they were recorded"; `-x`: "2.0 will replay traffic at twice the speed captured"; `-t`: "as fast as possible"; `-o`: "step through one or more packets at a time"
  (tcpreplay.appneta.com/reference/man/tcpreplay/) ⇒ tương ứng P4, P2, P3 và chế độ bước.
- kdb+ tick: `upd` đóng dấu `.z.P` lúc nhận và ghi log; `-11!(n;x)` "replays n chunks from top of logfile" ⇒ replay tốc độ tối đa, giữ dấu thời gian first-seen, seek chỉ theo số lượng.
- Databento: replay "in an event-driven manner, as if it were real-time" (databento.com/blog/real-time-tick-data); `start` là "The inclusive start of subscription replay". Pacing và giới hạn 24 giờ **[?]**.
- Điều khiển: pause = ngừng phát; seek = dựng lại trạng thái rồi tiếp tục (khử trùng biên inclusive); thêm chế độ bước; ghi lại ánh xạ giờ ảo ↔ giờ thật.

**First-seen và dữ liệu đã hiệu chỉnh [CS]** — liên quan độ trung thực dù SQL được coi là đúng
- Alpaca: "Updated bars are emitted after each half-minute mark if a 'late' trade arrived after the previous minute mark."; TradingView: "A broker/exchange may retroactively modify values reported on realtime bars"
  (tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/); Zipline: "when the data was known, or became available"; ALFRED: "each economic data release (vintage) that was available on a specific date in history".
- Glassnode **[TC]**: "Only immutable, point-in-time metrics ensure you're replaying history as it actually happened". Không thấy nguồn chính về hiệu chỉnh nến FX/CFD **[?]**.
- Ý nghĩa: Fact là bản mới nhất của mỗi nến (mục 2.3), có thể khác nến consumer thấy lúc đó. Theo chỉ đạo coi SQL đúng nên không xử lý; tùy chọn tương lai: ghi lại nến first-seen kèm giờ nhận từ Redis live (mẫu kdb+ tick) để có băng "point-in-time" thật.

**Quy ước nhãn thời gian [CS]**
- Nhãn giờ mở: MQL5 "Period start time"; Binance "uniquely identified by their open time"; Nautilus mặc định đóng ("When True, ts_event is the bar close time"); IB gắn bar ngày theo ngày đóng
  ("the date of the bar will correspond to the day on which the bar closes").
- Neo: TradingView (Charting Library) muốn D/W/M gắn "beginning of the trading day at 00:00:00 UTC"; bar phiên: "The first bar timestamp coincides with the session opening time", cuối phiên "non-inclusive";
  Pepperstone: "Server time is set to GMT +3 while US daylight savings is in place"; NYSE: "Each market will close early at 1:00 p.m." (ngày đóng sớm). Ngày giao dịch kiểu NY-close mở 21:00 hoặc 22:00 UTC tùy DST **[SL]**.
- Hệ quả: giờ phát không phải lúc nào cũng `open + tf` (D1/W, bar cuối phiên, ngày đóng sớm cần lịch phiên của nhà cung cấp). DPS giữ đúng quy tắc của production (`open + Minutes`), SQL coi là đúng; không bao giờ phát trước giờ đóng.

**Chưa tới / chưa kiểm chứng**: bài gốc PDES (Fujimoto 1990, Chandy–Misra 1979) và cơ chế null-message (PDF không đọc được); pacing và giới hạn 24 giờ của Databento; ngày giao dịch CME; ngày bắt đầu W1 của MT5;
căn W/M của Binance; sản phẩm exchange simulator; docs chính thức OANDA/Dukascopy; sự kiện trên replica ngoài source code và wiki Lettuce.

---

## 5. Kiến trúc đề xuất và mặc định cho các điểm đã hoãn **[SL]**

**Thành phần**: Source (đọc SQL; có thể thêm cache cục bộ dạng Parquet — numpy/pandas/pyarrow có sẵn — để chạy lại nhiều lần không phải đọc production SQL lặp lại) → Planner → Seeder → Player (clock + pacing) → Writer (hợp đồng live) → State (khóa một-instance, `state.json` + heartbeat, file dừng, log có cấu trúc, theo quy ước `runtime.py`).

**Mặc định cho các điểm đã hoãn (operator có thể đổi bất cứ lúc nào)**
| Điểm | Mặc định đề xuất |
|---|---|
| "pub" | Ghi List + Hash đúng hợp đồng live (OG nhận qua keyspace notification), không PUBLISH. Chờ operator xác nhận nếu "kênh" nghĩa là pub/sub. |
| Redis đích | db15 của Redis OG8 (operator chỉ định 2026-10-04); **không bao giờ db0**; hai lớp guard: `allowed_dbs` trong config và db phải rỗng hoặc mang marker `dps:owner`; mỗi lần chạy bắt đầu từ db sạch (clean = `FLUSHDB ASYNC` chỉ sau khi qua guard; không bao giờ `FLUSHALL`). |
| Gate tuổi nến / giờ của consumer | Phát `dps:clock`; consumer dùng giờ ảo (mục 3.5). Cần rà code OG/strategy. |
| Khoảng trống | Next-event cho lịch phát; pacing P1 (bỏ khoảng nghỉ) hoặc P2 (giữ tỷ lệ) tùy chọn. |
| Độ trễ tới của nến | Mặc định 0 (nến lên Redis đúng giờ đóng). Live thật có trễ (chu kỳ 2 phút + xử lý); mô hình trễ để sau, dạng tùy chọn. |
| `time_update` | Giờ ảo của tick. |
| Hết dữ liệu | Dừng sạch. |
| Thứ tự trong tick | (Minutes tăng dần, symbol) — tất định; ghi gộp trong một MULTI/EXEC để consumer không thấy trạng thái nửa vời. |
| Điều khiển | pause / bước / seek; log ánh xạ giờ ảo ↔ giờ thật (mục 4.10). |

### 5.1 Cấu trúc file và kế hoạch làm (chờ operator duyệt) **[SL]**

Cấu trúc phẳng (`dps/src/*.py`, `dps/test/`; không có gói con — cập nhật 2026-10-04 theo yêu cầu của operator), Python 3.12, chỉ cần `pyodbc`, `redis`, `PyYAML`. Quy ước theo `core_program/AGENTS.md`: định danh và chuỗi log tiếng Anh, comment tiếng Việt,
mỗi file ≤ ~250 dòng code, không `__init__.py` chứa logic, một cách biểu diễn nến duy nhất, mỗi tài nguyên ngoài (config, SQL, Redis) có đúng một owner.

**File cấu hình (không phải code Python)** — đã tạo 2026-10-04: `dps/config.yaml` (riêng tư, chứa thông tin kết nối; không commit, không in ra) · `dps/config.example.yaml` (mẫu không bí mật) ·
`dps/.gitignore` (loại `config.yaml`, `runtime/`, cache). Ranh giới config và code: **config** = lựa chọn của operator (SQL/Redis đích, `db` và `allowed_dbs`, symbols/timeframes, T0 và T cuối, pacing,
`key_prefix`/cửa sổ/TTL mặc định theo live); **code** = quy tắc cố định quyết định tính đúng (Lua, định dạng Hash và giá, giờ phát, thứ tự mốc). Các file lõi thuần (`contract`, `schedule`) là
logic nhận tham số từ config, không phải cấu hình. Schema `config.yaml`: `app` (runtime_dir) · `sql_server` (server, port, database, username, password, trusted_connection, encrypt, trust_server_certificate) ·
`redis` (host, port, username, password, db, allowed_dbs, key_prefix, bars_per_snapshot, hash_ttl_seconds) · `replay` (symbols, timeframes, start_utc, end_utc) · `pacing` (mode, delay_seconds, speed).
Giá trị kỹ thuật cố định (driver ODBC, timeout, retry) do `configuration.py` sở hữu, giống dp_program.

| File | Trách nhiệm duy nhất | I/O | Căn cứ |
|---|---|---|---|
| `__main__.py` | CLI `plan` / `run` / `status` / `stop` / `clean`; chỉ phân tích tham số rồi gọi | — | |
| `configuration.py` | Đọc và kiểm `config.yaml` (owner duy nhất): kiểm kiểu/khoảng, `redis.db` phải nằm trong `allowed_dbs`; giá trị kỹ thuật (driver, timeout) do code sở hữu | file | AGENTS.md |
| `contract.py` | Hợp đồng Redis của live: tên key, stamp, làm tròn giá, Lua script (y hệt live), đóng gói tham số | không | mục 1 |
| `schedule.py` | Giờ phát `BarTime + Minutes`; thứ tự toàn phần tất định; nhóm theo mốc; băm lịch | không | LEAN, Nautilus, Law (4.1 #1–4, 6) |
| `sql_source.py` | Mọi truy cập SQL (chỉ SELECT): universe, nến theo cửa sổ giờ phát, seed 1200 nến | SQL | mục 2 |
| `redis_writer.py` | Mọi truy cập Redis: hai lớp guard (`allowed_dbs`; db rỗng hoặc mang marker `dps:owner`), `SCRIPT LOAD`, ghi một tick trong một MULTI/EXEC (+ `dps:clock` + checkpoint), seed, clean (`FLUSHDB ASYNC` chỉ sau guard) | Redis | Redis docs (4.8) |
| `player.py` | Vòng chạy: seed → mỗi tick (pacing → ghi → checkpoint) → dừng sạch / resume | dùng adapter | |
| `runtime.py` | Nền tảng dùng chung, bốn phần có tiêu đề riêng: (1) kiểu dữ liệu `DpsError`, `Pair`, `Candle`, `Tick`; (2) log có cấu trúc, che bí mật; (3) điều tốc `delay`/`speed` với deadline tuyệt đối (lock-step ở v1.1); (4) vòng đời tiến trình: khóa một-instance, `state.json` + heartbeat, file dừng, manifest | file, đồng hồ (inject) | `runtime.py` và `log.py` của dp_program; tcpreplay, ROS 2 (4.10); FoundationDB, TigerBeetle (4.7) |

Hướng phụ thuộc: `runtime` không import module nào khác của DPS; `contract`, `schedule` thuần (không I/O) và chỉ được lấy KIỂU DỮ LIỆU từ `runtime`; `sql_source`, `redis_writer` phụ thuộc `runtime` và `configuration`; `player` nối tất cả; `__main__` chỉ gọi
`configuration`, `player`, `redis_writer`, `runtime`. Test kiến trúc (`test_architecture.py`) kiểm các quy tắc này, mỗi tài nguyên ngoài (`yaml`, `pyodbc`, `redis`) chỉ một owner, giới hạn 300 dòng code mỗi file,
mỗi file có docstring trách nhiệm, `sql_source` chỉ chứa SELECT và `redis_writer` không có FLUSHALL/KEYS/SCAN.

**Luồng.** `plan`: đọc nến theo cửa sổ giờ phát (một tuần mỗi lần) → sắp xếp tất định → in/ghi lịch + băm, không đụng Redis. `run`: khóa + manifest → guard Redis → seed 1200 nến/pair →
vòng tick (pacing → MULTI/EXEC gồm nến + `dps:clock` + checkpoint) → hết lịch thì dừng sạch; `--resume` tiếp từ checkpoint nếu băm lịch khớp.

**Tối ưu có chủ đích.** Đọc theo cửa sổ giờ phát bằng truy vấn sargable (`BarTime` trong `(a − Minutes, b − Minutes]`) trên index covering ⇒ bộ nhớ không đổi, sắp xếp từng cửa sổ thay cho heap (tương đương, đơn giản hơn), không cần thread;
một MULTI/EXEC mỗi tick (≤ 120 EVALSHA); deadline tuyệt đối nên không trôi; không pandas/numpy; không cache ở v1 (đọc cả năm ≈ 27 s).

**Không làm ở v1 (YAGNI).** Mô hình độ trễ tới, tick/intrabar, fill-forward, gộp khung từ M5, Streams, ghi SQL, lock-step (cần consumer ack), pause/seek.

**Thứ tự làm — mỗi bước có kiểm chứng riêng**
1. Lõi thuần: `contract` (parity với `live.py`), `schedule` (bất biến: mỗi nến đúng một lần, không phát trước giờ đóng, thứ tự tất định, băm ổn định), điều tốc trong `runtime` (đồng hồ giả). Không cần SQL/Redis.
2. `sql_source` + `plan`: chạy trên SQL thật, đối chiếu mục 2.4 (92.507 mốc, ≤ 120 nến/mốc) và ví dụ GOLD mục 3.3; truncation test.
3. `redis_writer` + `player` + `runtime` trên Redis thử nghiệm: seed + replay 1 ngày, đếm sự kiện `hset`, trạng thái cuối, crash/resume.
4. Chạy thử cả năm ở tốc độ tối đa trên Redis thử nghiệm; đối chiếu trạng thái cuối.
5. Giờ ảo cho consumer (`dps:clock`) và lock-step khi đã thống nhất cách consumer dùng giờ/ack.

**Đã có / còn cần từ operator.** SQL và Redis đã có trong `dps/config.yaml` (SQL dùng Windows authentication như dp_program, DPS chỉ SELECT; Redis thử nghiệm = db15, đã kiểm: standalone, db15 rỗng,
`notify-keyspace-events` có K và h — mục 8). Bước 5 còn cần: rà code OG/strategy về giờ máy và khả năng ack.

---

## 6. Rủi ro và việc cần kiểm chứng

1. **Giờ của consumer** **[CHỜ]** — OG/strategy có dùng giờ máy (`datetime.now`/`time.time`) cho tín hiệu hết hạn, lọc phiên, cooldown? Code OG không nằm trong repo này (nằm ở og_program). Cần rà trước khi chọn pacing.
2. **Consumer đọc SQL trực tiếp** **[KC] cấu trúc / [CHỜ] việc sử dụng** — `MART.usp_GetLatestCandles` và `MART.v_OHLCV` không có as-of ⇒ rò rỉ tương lai. Mọi đường đọc dữ liệu thị trường của consumer phải đi qua Redis do DPS điều khiển (hoặc bị cắt ≤ giờ ảo).
3. **Độ sâu M5/M10** **[KC]** — mục 2.5; chọn cửa sổ theo khung.
4. **Chưa có Redis thử nghiệm trên máy này** **[KC]** — không có `redis-server`/`redis-cli`/Memurai/Docker; chỉ có `wsl.exe` (chưa kiểm tra distro); không có fakeredis/lupa. Cần Redis thật để kiểm thử Lua và keyspace event (Redis đích của operator, hoặc cài riêng).
5. **TTL Hash 7 ngày** **[CS]** — TTL chạy theo giờ máy chủ Redis ("the time is flowing even when the Redis instance is not active"), không theo giờ ảo. Replay vài giờ thì TTL không bao giờ kích hoạt;
   replay dài hơn 7 ngày thật thì Hash hết hạn dưới List còn trỏ tới. Nếu cần hết hạn theo giờ ảo thì DPS tự xóa.
6. **Bùng phát sự kiện lúc seed** **[KC]** — ~200.000 `hset`. Subscriber Pub/Sub chậm bị ngắt khi vượt buffer mặc định (cứng 32 MB, mềm 8 MB trong 60 s — redis.io/docs/latest/develop/reference/clients/);
   lần nạp lại 2026-09-28 có kèm việc listener của OG kết nối lại (ghi chú phiên trước; **[SL]** có thể liên quan). Cách xử lý: bật consumer sau khi seed; hoặc seed theo lô có nghỉ;
   hoặc seed bằng RESTORE/COPY (sinh sự kiện `restore`/`copy_to` thay vì `hset`, **[SL]** suy từ bảng sự kiện).
7. **Giờ phát theo độ dài danh nghĩa** **[SL]** — `BarTime + Minutes` có thể lệch giờ đóng thật của phiên (D1 ngày đổi DST, bar cuối phiên ngắn). Giống production nên giữ; SQL được coi là đúng.
8. **`dps/` nằm ngoài repo git** **[KC]** — chỉ `core_program/` có `.git`; `dp_program/` (gốc) không có. Chưa được version control.
9. **Redis đích phải là standalone** **[KC]** — Lua của live truy cập key Hash sinh theo chương trình, trái khuyến nghị của Redis cho cluster (mục 4.8). **Đã kiểm: Redis OG8 là standalone (8.0.5, master).**
10. **Keyspace notification trên Redis đích** **[KC]** — mặc định tắt; cần `K` và `h`. **Đã kiểm: `notify-keyspace-events` = `g$hzK`** (có K, h, g; không có `l` nên không có sự kiện lệnh List — OG chỉ lọc `hset` nên không ảnh hưởng; không có `E`).
    Sự kiện là fire-and-forget: consumer ngắt kết nối giữa chừng sẽ mất sự kiện ⇒ lock-step + ack, hoặc consumer tự đọc lại List khi kết nối lại.
12. **Redis đích dùng chung máy chủ với production** **[KC]** — db15 nằm trên cùng instance OG8: `maxmemory = 0` (không giới hạn), policy `noeviction`, `used_memory` ≈ 83 MB (db0 ≈ 191.600 key, db1 836, db2 72.600, db3 32.500; đo 2026-10-04).
    Seed đầy đủ 165 pair × 1200 ≈ 198.000 key sẽ tăng bộ nhớ đáng kể (đo thực tế ở lần chạy 88 pair: ≈ 322 byte/key ⇒ ≈ 64 MB **[KC]**); `SCRIPT LOAD` là toàn cục (script trùng chữ với live ⇒ trùng SHA1, vô hại); sự kiện của db15 đi trên `__keyspace@15__` còn OG chỉ nghe `@0`
    (docs OG) nên không nhận. Thử nhỏ trước (vài pair, vài giờ) và hỏi operator trước khi seed đầy đủ.
11. **Chuỗi consumer và ack nhân quả** **[CHỜ]** — nếu strategy → OG là một chuỗi thì lock-step chỉ đúng khi ack đi theo quan hệ nhân quả (OG ack sau khi xử lý xong tín hiệu của strategy cho bước đó);
    ack qua key hoặc Streams, không qua Pub/Sub (mục 4.10). Cần biết consumer có thể ack hay không.

---

## 7. Kế hoạch kiểm thử đề xuất **[SL]**
- **Planner (thuần)**: mỗi nến phát đúng một lần; giờ phát không giảm qua các tick; không nến nào phát trước `BarTime + Minutes`; thứ tự tất định; băm lịch ổn định giữa các lần chạy. Có thể dùng `validate_candles(..., closed_only=True, now=T)` làm oracle cho quy tắc đóng nến.
- **Parity hợp đồng**: Lua script của DPS ≡ `_INCREMENTAL_SCRIPT` của live (so chuỗi trong test); `_round_price`, khóa, stamp giống hệt trên dữ liệu ngẫu nhiên.
- **Trạng thái cuối**: sau khi replay tới T, mỗi List = min(1200, n) nến gần nhất có `release ≤ T`, giảm dần; số Hash = độ dài List; đủ 6 field.
- **Redis thật**: đếm sự kiện `hset` theo tick; consumer thấy trạng thái nhất quán trong một tick (MULTI/EXEC).
- **Pacing**: kiểm tra trôi thời gian với đồng hồ giả (deadline tuyệt đối).
- **Crash/resume**: dừng giữa chừng rồi chạy tiếp cho ra trạng thái cuối giống hệt chạy liền một mạch.
- **Truncation test (look-ahead)**: kết quả/Redis tại tick T phải y hệt khi dữ liệu sau T bị cắt khỏi nguồn (kiểu `lookahead-analysis` của Freqtrade, mục 4.5).
- **Đối chiếu production (tùy chọn, chỉ đọc)**: replay một ngày gần đây và so với Hash trên Redis production; lệch nếu có là khác biệt nguồn, không phải lỗi định dạng.

---

## 8. Môi trường (máy VM-DP6) **[KC]**
Python 3.12.10 · pyodbc 5.3.0 · redis-py 8.0.1 · numpy 2.4.6 · pandas 3.0.3 · pyarrow 24.0.0 · pytest 9.1.1. Chạy `python -B` với `PYTHONPATH=core_program/src`.
Không có `redis-server`, `redis-cli`, Memurai, Docker; có `wsl.exe`; không có tiến trình lắng nghe 6379/6380/6688/16379 cục bộ — không cần, vì đã có db15 trên Redis OG8.

**Kết nối của DPS (kiểm 2026-10-04 ~16:13 UTC, chỉ đọc; Redis chạy sau allow-list lệnh chỉ đọc)**
- SQL: kết nối được bằng `dps/config.yaml` (Windows authentication); cửa sổ thử GOLD 13:00–15:00 UTC có 24 nến M5 và 8 nến M15 (đúng kỳ vọng).
- Redis OG8: 8.0.5, standalone, role master, Linux; `databases = 16`; `notify-keyspace-events = g$hzK`; `maxmemory = 0`, `maxmemory-policy = noeviction`; `used_memory` ≈ 83 MB;
  keyspace db0 191.642 key (191.315 có TTL), db1 836, db2 72.638, db3 32.495; **db15: 0 key (rỗng)**. DPS chưa ghi gì vào Redis.

---

## 9. Triển khai v1 — kết quả kiểm chứng (2026-10-04)

**Mã nguồn**: `dps/src/*.py` (8 file phẳng, 1.103 dòng code; file lớn nhất `runtime.py` 248 dòng) và `dps/test/` (13 file). Các module import nhau trực tiếp (`import schedule`, `from runtime import Candle`);
`python src ...` thực thi `src/__main__.py` và đặt `src` lên `sys.path`, nên không cần `PYTHONPATH`. Chạy từ thư mục `dps/`:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B src plan   [--show N] [--export lich.csv]      # chạy khô: lịch phát + mã băm, không đụng Redis
python -B src run    [--resume]                          # seed rồi replay lên db15
python -B src status | stop | clean --yes
# ghi đè config.yaml bằng: --start --end --symbols --timeframes và --delay (giây/mốc) hoặc --speed (giây ảo/giây thật)
python -B -m pytest test -q -p no:cacheprovider          # test đơn vị (conftest.py tự thêm src vào sys.path)
$env:DPS_INTEGRATION = "1"; python -B -m pytest test/test_redis_integration.py   # test trên Redis thật (db15 phải rỗng)
```

**`dps.bat` (2026-10-04)** — file chạy nhanh ở `dps/dps.bat`, chạy được từ bất kỳ thư mục nào, tham số được chuyển nguyên văn (giữ dấu phẩy, `=`, ngoặc kép); `DPS_PYTHON` chọn python.exe khác nếu cần. Các mode:

| Lệnh | Việc làm |
|---|---|
| `dps.bat plan [tùy chọn]` | chạy khô: in lịch phát + mã băm, không ghi gì |
| `dps.bat run [tùy chọn]` | seed rồi replay lên db15 (db phải rỗng) |
| `dps.bat resume [tùy chọn]` | chạy tiếp từ checkpoint — **phải dùng đúng các tùy chọn của lần `run` gốc**, nếu không mã băm lệch và bị từ chối |
| `dps.bat status` / `stop` | xem trạng thái + checkpoint / yêu cầu dừng sạch |
| `dps.bat clean` | báo số key rồi hỏi trước khi xóa db15 (`/y` bỏ qua câu hỏi); chương trình vẫn từ chối db không được phép hoặc không thuộc DPS |
| `dps.bat clean-files` | xóa `runtime\`, `__pycache__`, `.pytest_cache` trong thư mục `dps` (từ chối khi đang có run; không đụng Redis) |
| `dps.bat help` | liệt kê mode và tùy chọn |

Đã kiểm thử bản thân file .bat: help và mã thoát, mode sai (thoát 1), `plan` với danh sách dấu phẩy + ngày có ngoặc kép + `--show=4` chạy từ thư mục khác, `run` → `status` → `run` lần hai bị từ chối → `clean /y`,
`clean` trả lời "n" (không xóa gì), dừng giữa chừng bằng `stop` rồi `resume` với cùng tùy chọn (mã băm cuối trùng mã băm của `plan`), `clean-files`. Phát hiện và sửa trong lúc thử: dòng tóm tắt của lần chạy **bị dừng** in mã băm có tính cả mốc đã đọc
nhưng chưa ghi (nay in đúng mã băm checkpoint, có test), và thông báo lỗi `resume` giờ gợi ý dùng cùng `--start/--end/--symbols/--timeframes`. Gợi ý cải tiến sau: lưu tham số của lần chạy trong `dps:state` để `resume` tự dùng lại.

**Đổi cấu trúc 2026-10-04**: bỏ thư mục con `src/dps/`, đưa 11 file lên `src/`, đổi import tương đối thành tuyệt đối, bỏ `python -m dps` (thay bằng `python src`). Test kiến trúc cấm import tương đối và kiểm không còn gói con;
`test_cli.py` nạp `src/__main__.py` theo đường dẫn vì pytest đã chiếm tên `__main__`. Đánh đổi của cấu trúc phẳng: tên module cấp cao khá chung chung (`schedule`, `runtime`) — an toàn vì `python src` đặt `src` lên đầu
`sys.path` (đã kiểm: không có gói cài sẵn nào trùng tên), nhưng nếu sau này import DPS từ nơi khác thì phải cẩn thận trùng tên.

**Gộp file 2026-10-04 (theo yêu cầu của operator)**: `model.py`, `log.py` và `pacing.py` đã gộp vào `runtime.py` (bốn phần có tiêu đề riêng; `log.setup` đổi tên thành `setup_logging`); còn 8 file. `contract` và `schedule` giờ lấy kiểu dữ liệu
từ `runtime`; test kiến trúc chỉ cho phép hai file thuần này lấy `Candle`, `Pair`, `Tick`, `DpsError` từ đó, để lõi thuần không dính mã vòng đời/log/điều tốc. Thêm hai test cho phần log (che trường bí mật, gắn handler đúng một lần).

**Gộp file 2026-10-06 (theo yêu cầu của operator, sau audit cấu trúc)**: `contract.py` gộp vào `redis_writer.py` thành phần 1 "hợp đồng Redis của live" (giữ nguyên từng ký tự,
kiểm bằng so sánh AST với bản cũ); còn 7 file, 1.099 dòng code. Lý do: `contract` chỉ có một consumer thật (`redis_writer`; `player` chỉ lấy `parse_stamp`). Test kiến trúc: bỏ
`contract` khỏi danh sách module và lõi thuần, thêm test kiểm các hàm hợp đồng trong `redis_writer` không chạm client Redis. Kiểm chứng: 97 test đơn vị (102 → 99 test thu thập: mất 4 bản
tham số hóa theo tên module `contract`, thêm 1 test mới), 2 test tích hợp trên db15, `plan` và `run` GOLD M5 + M15 cho cùng mã băm `f16a6a6b…` như trước khi gộp.
Bảng module ở mục 5 phía trên giữ nguyên như thiết kế ban đầu; cấu trúc hiện hành nằm ở README mục 9.

**Đã kiểm chứng**
- **Test**: 98 test đơn vị qua, gồm đối chiếu với live thật (Lua giống hệt, làm tròn giá trên 3.000 giá trị ngẫu nhiên, stamp, key, tham số) và 2 test tích hợp trên db15 (hợp đồng live, tính nguyên tử với reader
  MULTI/EXEC qua 300 mốc không lệch lần nào, ghi lặp không sinh `hset`, guard, clean).
- **`plan` trên SQL thật**: ví dụ GOLD M5 + M15 đúng yêu cầu (tick 3 = M5 + M15). Cả năm 165 pair: 92.508 mốc, 1.745.381 nến, tối đa 120 nến/mốc, dựng lịch trong 42,7 giây — khớp phép đo độc lập ở mục 2.4.
- **`run` end-to-end SQL → db15** (GOLD M5 + M15, 13:00–15:00 ngày 2026-09-30): seed 2.400 nến, 24 mốc, 32 nến; List giảm dần (index 0 = mới nhất), Hash đủ 6 field, giá đúng định dạng live (`4176.6`), `time_update` = giờ ảo của mốc,
  TTL ≈ 7 ngày; mã băm lúc chạy trùng mã băm của `plan`.
- **Dừng và chạy tiếp**: dừng giữa chừng rồi `--resume` cho trạng thái cuối và mã băm giống hệt chạy liền một mạch. Chạy lần hai khi db chưa clean bị từ chối; resume một run đã xong bị từ chối; khóa một-instance chặn tiến trình thứ hai;
  `status` phân biệt đúng tiến trình đang chạy và đã thoát.
- **Chạy thử 88 pair (11 symbol × 8 khung) trên db15, 2026-10-04 23:02 UTC**: seed 105.600 nến trong ≈ 7 giây rồi 14 mốc (319 nến) cách nhau 1 giây, tổng 21,6 giây; độ trễ pacing tối đa 16 ms; mã băm lúc chạy trùng mã băm của `plan`.
  Đối chiếu toàn diện với SQL (chỉ đọc): DBSIZE 105.691 đúng kỳ vọng; 88/88 List giống hệt 1200 nến đóng gần nhất tại 15:00 (mới nhất ở đầu); 105.600/105.600 Hash giống hệt (6 field, định dạng giá live, `time_update` = T0 cho nến seed và giờ mốc cho nến mới);
  TTL ≈ 7 ngày; `dps:clock` và `dps:state` đúng (`finished`, mốc 14). Bộ nhớ Redis: 87,09 MB → 121,12 MB (**+34,0 MB, ≈ 322 byte/key**; ước tính trước đó 37 MB); sau `clean` về 87,13 MB; số key db0–db3 không đổi.
- **Dọn dẹp**: sau mỗi lần thử db15 được đưa về 0 key; DPS chưa ghi gì ngoài db15.

**Cấu hình hiện tại (2026-10-04)**: 11 symbol × 8 khung (M10, M20, M30, M45, H1, H2, H3, H4) = 88 pair; cửa sổ thử 2026-09-30 13:00 → 15:00 UTC, `pacing` delay 1 giây, Redis db15. `plan` trên SQL thật cho cửa sổ này:
88 pair, 14 mốc, 319 nến, tối đa 72 nến một mốc (mốc 14:00 gồm M10, M20, M30, H1 và một phần H2, H4). Nhận xét từ dữ liệu thật: nến M45 và H2/H4 **không đóng cùng giờ giữa các symbol** (vd mốc 13:30 có M45@12:45 của 10 symbol, riêng GOLD đóng M45 lúc 13:45
vì neo phiên khác) — đúng lý do phải dùng `BarTime` thật chứ không tính theo lưới UTC (mục 2.6). Một lần `run` seed 105.600 nến (88 pair × đủ 1200 nến) ⇒ 105.691 key trên Redis dùng chung (đã chạy thử, kết quả ở bullet "Chạy thử 88 pair" bên dưới).
`config.example.yaml` giữ cấu hình demo nhỏ (GOLD, M5 + M15) vì các test dùng nó.

**Giới hạn đã biết và việc tiếp theo**
- Chưa chạy cả năm 165 pair lên Redis: cần hỏi operator trước (≈ 200.000 key trên instance dùng chung với production, mục 6 #12). Ước lượng thời gian: 2,6 giờ ở 0,1 giây/mốc.
- Chưa có lock-step với ack và clock injection cho consumer (v1.1; mục 3.4–3.5, 4.10): cần thống nhất cách OG/strategy dùng giờ ảo. `dps:clock` và `dps:state` đã được ghi sẵn trong mỗi MULTI/EXEC.
- Việc đọc SQL theo tuần diễn ra giữa các mốc nên có thể tạo một vệt trễ nhỏ ở ranh giới cửa sổ (được đo qua `max_lag`, bù bằng deadline tuyệt đối); chưa prefetch song song.
- Ngoài v1: pause/seek/bước, mô hình độ trễ tới của nến, cache cục bộ.
- `dps/` nằm ngoài repo git của `core_program` (chưa version control).

---

## Phụ lục A — Tái lập các phép đo (chỉ đọc)

Kết nối: `load_config(r"…\run_dp\config.yaml")` → `get_connection(config)` → `autocommit = True` → `SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED` → chỉ SELECT. Không in cấu hình.

```sql
-- Độ phủ theo cặp (symbol, khung)
SELECT s.Symbol, tf.Code, COUNT_BIG(*) AS n, MIN(f.BarTime) AS first_bar, MAX(f.BarTime) AS last_bar
FROM DWH.Fact_OHLCV f
JOIN DWH.Dim_Symbol s ON s.SymbolID = f.SymbolID
JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
GROUP BY s.Symbol, tf.Code;

-- Khoảng nghỉ lớn nhất 365 ngày (LAG theo cặp)
WITH x AS (
  SELECT f.SymbolID, f.TimeframeID, f.BarTime,
         LAG(f.BarTime) OVER (PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime) AS prev
  FROM DWH.Fact_OHLCV f WHERE f.BarTime >= @since)
SELECT SymbolID, TimeframeID, COUNT_BIG(*) AS n, MIN(BarTime) AS first_bar,
       MAX(DATEDIFF(minute, prev, BarTime)) AS max_gap_min
FROM x GROUP BY SymbolID, TimeframeID;

-- Đọc một cặp (phép đo quy mô: 165 lần, 26,5 s)
SELECT BarTime, [Open], High, Low, [Close], Volume FROM DWH.Fact_OHLCV
WHERE SymbolID = ? AND TimeframeID = ? AND BarTime >= ? ORDER BY BarTime;

-- Seed (đề xuất): 1200 nến đã đóng trước T0, sargable
SELECT TOP (1200) BarTime, [Open], High, Low, [Close]
FROM DWH.Fact_OHLCV
WHERE SymbolID = ? AND TimeframeID = ? AND BarTime <= DATEADD(minute, -?, ?)   -- T0 trừ Minutes
ORDER BY BarTime DESC;
```

Phân bố giờ mở nến (mục 2.6): `GROUP BY DATEPART(hour, BarTime), DATEPART(minute, BarTime)` (kèm `DATENAME(weekday, …)` cho khung W) trên 365 ngày.
Số mốc phát và số nến/mốc (mục 2.4): đọc từng cặp, cộng `Minutes` vào `BarTime` rồi đếm theo giá trị (Counter) trong Python.
Nhất quán chéo khung (mục 2.8): gộp M5 theo bucket 15/60 phút trong Python, so O/H/L/C với M15/H1 lưu sẵn.
