# DPS — Data Provider Simulator

> Phát lại nến lịch sử từ SQL Server lên Redis **với đúng cấu trúc và đúng nhịp của hệ thống live**, trên một đồng hồ ảo
> chạy nhanh hơn thời gian thật, để chạy thử cả pipeline giao dịch trên nhiều tháng dữ liệu trong vài giờ.

| | |
|---|---|
| Phiên bản | v1 — cập nhật 2026-10-05 |
| Vị trí | `dps/` (cạnh `core_program/` và `run_dp/`) |
| Chạy bằng | `dps.bat <mode>` hoặc `python -B src <lệnh>` (từ thư mục `dps/`) |
| Trạng thái | Phần DPS đã triển khai và kiểm chứng trên SQL và Redis thật (mục 10). Chưa nối OG / chiến lược vào (mục 8). |
| Tài liệu liên quan | [research_notes.md](research_notes.md) (nghiên cứu nền tảng, nguồn tham khảo) · [config.example.yaml](config.example.yaml) · `dps.bat help` |

---

## 0. Về tài liệu này

**Mục đích.** Giúp người đọc hiểu trọn vẹn DPS mà không cần đọc mã: DPS là gì, sinh ra để giải quyết việc gì, nhận **đầu vào** gì,
**xử lý** ra sao, tạo ra **đầu ra** gì, chạy như thế nào, đọc kết quả ra sao, và hiện còn giới hạn gì.

**Dành cho ai.**
- Người vận hành: cần biết chạy, dừng, dọn và xử lý lỗi (mục 7).
- Kỹ sư bảo trì: cần biết thuật toán, cấu trúc mã và các quy tắc an toàn (mục 5, 9).
- Đội OG / chiến lược: cần biết DPS ghi gì lên Redis và cần làm gì để đọc nó (mục 6, 8).

**Cách đọc.**

| Bạn muốn… | Đọc mục |
|---|---|
| Hiểu DPS để làm gì, bối cảnh | 1, 2 |
| Nắm toàn cảnh trong năm phút | 3 |
| Biết DPS cần gì để chạy | 4 |
| Hiểu cách DPS làm việc | 5 |
| Biết kết quả nằm ở đâu và trông thế nào | 6 |
| Chạy thử, xử lý lỗi | 7 |
| Nối OG / chiến lược vào | 8 |
| Sửa hoặc mở rộng mã | 9 |
| Biết đã kiểm chứng gì, còn thiếu gì | 10, 11 |

**Quy ước độ tin cậy.** *Đã kiểm chứng* = có test hoặc phép đo thật · *Ước tính* = suy luận chưa đo · *Chưa làm* = ngoài phạm vi v1.

---

## 1. Bối cảnh

### 1.1 Hệ thống hiện có

DPS là mảnh ghép mới trong một hệ thống đã chạy: chương trình `dp_program` V3 lấy nến từ TradingView và phục vụ hai nơi.

```
 TradingView
    │
    ├─► dp_program · backfill (6 lần/ngày, UTC 03:03 07:07 11:11 15:15 19:19 23:23)
    │        └─► SQL Server  SEN05_AutoTrading · DWH.Fact_OHLCV      ← lịch sử, nguồn sự thật
    │
    └─► dp_program · live (mỗi 2 phút, chỉ nến đã đóng)
             └─► Redis (OG8) db0 · L_CANDLE_{SYMBOL}_{TF}  (List + Hash)   ← nến real-time
                       │  keyspace notification (sự kiện hset)
                       ▼
                 OG (order gateway) / chiến lược  ──►  tín hiệu, lệnh
```

- **SQL Server** giữ lịch sử 15 khung (M5 … W) cho 37 symbol, trong đó 11 symbol chạy live: FR40, DE40, HK50, J225, SP35, UK100,
  US500, US100, US30, GOLD, BTCUSD. Mỗi nến là một dòng trong `DWH.Fact_OHLCV`, khóa theo `(SymbolID, TimeframeID, BarTime)`;
  `BarTime` là **giờ mở nến** theo UTC.
- **Redis (live)** giữ, cho mỗi cặp symbol/khung, tối đa 1200 nến gần nhất dưới dạng **List** (các mốc giờ, nến mới nhất ở đầu) và
  **Hash** (một Hash mỗi nến, 6 field). Đây là "hợp đồng" mà OG đọc. OG được báo có nến mới nhờ sự kiện keyspace `hset` do
  chính các lệnh ghi Redis sinh ra, không qua kênh PUBLISH riêng.

### 1.2 Vấn đề

Chiến lược giao dịch đang chạy live nhận nến theo **giờ thật**: một nến M5 mới sau mỗi 5 phút, một nến H4 sau mỗi 4 giờ. Muốn biết nó
hoạt động ra sao trên **một năm** dữ liệu thì phải chờ một năm. Trong khi đó dữ liệu quá khứ đã có sẵn trong SQL.

Chạy backtest tách rời không giải quyết trọn vẹn: nó không đi qua **đường ống thật** — cách OG đọc List/Hash, cách phản ứng với sự
kiện, các điều kiện về thời gian, thứ tự nến đến… Điều cần là chạy lại quá khứ **qua đúng đường ống thật**, nhưng nhanh hơn thời gian thật
và lặp lại được.

### 1.3 Giải pháp

DPS đóng vai "dp_program live" nhưng lấy dữ liệu từ SQL thay vì TradingView, và làm **chủ đồng hồ** của mô phỏng. "Y như thực" gồm ba điều:

1. **Cấu trúc y hệt**: cùng tên key, cùng List/Hash, cùng định dạng giá, cùng cửa sổ 1200 nến, cùng script Lua ghi nến.
2. **Nhịp y hệt**: một nến chỉ xuất hiện khi đã **đóng**; các nến cùng giờ đóng (ví dụ M5 và M15 cùng đóng lúc 13:15) xuất hiện **trong cùng một lượt**.
3. **Cách báo y hệt**: mỗi lần ghi nến sinh sự kiện `hset` như live; không thêm kênh nào.

Khác với live ở đúng một điều có chủ ý: thời gian chạy nhanh hơn thật (mục 5.5), và DPS công bố **giờ ảo** ở key `dps:clock`.

### 1.4 Các tiền đề đã chốt với operator

| Tiền đề | Ý nghĩa với DPS |
|---|---|
| SQL được coi là **đúng 100%** | Nến nào có là sự thật; thiếu nến nghĩa là thị trường đóng cửa. DPS không bù, không sửa, không nghi ngờ dữ liệu. |
| Bỏ qua schema `tick` trong SQL | DPS chỉ dùng nến trong `DWH.Fact_OHLCV`. |
| Redis thử nghiệm là **db15** của cùng máy chủ Redis với production | DPS chỉ được ghi vào db15, **tuyệt đối không db0** (live). |
| Tập trung vào **11 symbol live × 8 khung**: M10, M20, M30, M45, H1, H2, H3, H4 (88 cặp) | Cấu hình mặc định hiện tại; đổi được bằng `config.yaml` hoặc tham số dòng lệnh. |

---

## 2. Mục tiêu và phạm vi

### 2.1 Mục tiêu

1. **Tái hiện đúng** nhịp và cấu trúc dữ liệu live (mục 1.3).
2. **Tăng tốc** thời gian: một năm dữ liệu trong vài giờ (mục 5.5).
3. **Tất định, lặp lại được**: cùng dữ liệu và cùng tham số cho cùng lịch phát, chứng minh bằng mã băm.
4. **An toàn**: không bao giờ chạm dữ liệu live, không ghi SQL (mục 5.7).
5. **Vận hành đơn giản**: dừng sạch, chạy tiếp từ chỗ dừng, dọn dẹp bằng một lệnh.

### 2.2 Không thuộc phạm vi

- DPS **không** tính tín hiệu, **không** đặt lệnh, **không** mô phỏng khớp lệnh hay trượt giá — đó là việc của OG / chiến lược.
- Không lấy dữ liệu từ TradingView, không ghi SQL, không đọc schema `tick`.
- Không gộp nến: mỗi khung được lấy nguyên từ SQL, không tự dựng M15 từ M5.
- Không mô hình độ trễ tới của nến và không có dữ liệu dưới mức nến (tick / intrabar).

### 2.3 Tiêu chí thành công (đã đạt trong v1)

- Trạng thái Redis sau khi chạy **khớp từng nến** với 1200 nến đóng gần nhất trong SQL.
- Hợp đồng ghi Redis **khớp mã nguồn live**, kiểm bằng test đối chiếu trực tiếp với `live.py`.
- Chạy ngắt quãng rồi chạy tiếp cho **đúng cùng kết quả** như chạy liền một mạch.

---

## 3. Tổng quan và thuật ngữ

### 3.1 Một trang

```
   INPUT                          XỬ LÝ (DPS)                                   OUTPUT
 ┌──────────────────┐   ┌───────────────────────────────────────────┐   ┌─────────────────────────────┐
 │ SQL: nến lịch sử │   │ 1. Seed: nạp 1200 nến đã đóng trước T0    │   │ Redis db15                  │
 │ (DWH.Fact_OHLCV) │──►│ 2. Lập lịch: giờ phát = giờ mở + độ dài   │──►│  · List + Hash nến (như     │
 ├──────────────────┤   │    khung; nến cùng giờ phát = một mốc     │   │    live)                    │
 │ config.yaml +    │──►│ 3. Phát từng mốc theo tốc độ đã chọn,     │   │  · dps:clock (giờ ảo)       │
 │ tham số dòng lệnh│   │    mỗi mốc ghi nguyên tử + checkpoint     │   │  · dps:state (checkpoint)   │
 └──────────────────┘   │ 4. Dừng sạch / chạy tiếp / kết thúc       │   │ Đĩa: log, state, manifest   │
                        └───────────────────────────────────────────┘   │ Màn hình: tóm tắt + mã băm  │
                                                                         └─────────────────────────────┘
                                                                                    │ sự kiện hset
                                                                                    ▼
                                                                         OG / chiến lược (mục 8)
```

### 3.2 Thuật ngữ

| Thuật ngữ | Nghĩa |
|---|---|
| **Nến** | Một dòng OHLC của một cặp symbol/khung. Chỉ dùng open, high, low, close (Redis không có volume). |
| **Giờ mở** (`BarTime`) | Thời điểm nến bắt đầu, UTC. |
| **Độ dài khung** (`Minutes`) | 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 1440, 10080 phút (theo `DWH.Dim_Timeframe`). |
| **Giờ phát** (release) | `giờ mở + độ dài khung` = lúc nến đóng và được phép xuất hiện. Cùng quy tắc "đã đóng" của hệ thật. |
| **Mốc phát** (tick) | Nhóm mọi nến có cùng giờ phát; được ghi lên Redis trong **một giao dịch**. Đánh số `seq` từ 1. |
| **Cặp** (pair) | Một tổ hợp symbol × khung (hiện có 88). |
| **T0** / **mốc cuối** | `start_utc` / `end_utc`: khoảng thời gian mô phỏng. Nến đã đóng ≤ T0 là *seed*; các mốc phát nằm trong (T0, mốc cuối]. |
| **Seed** | Nạp sẵn tối đa 1200 nến đã đóng trước T0 cho mỗi cặp, để Redis lúc bắt đầu giống Redis live tại thời điểm T0. |
| **Giờ ảo** | Thời gian trong mô phỏng, tiến theo từng mốc; công bố ở `dps:clock`. |
| **Pacing** | Cách quy đổi giờ ảo ra giờ thật: `delay` (mỗi mốc một khoảng cố định) hoặc `speed` (giữ tỷ lệ thời gian). |
| **Checkpoint** | `dps:state` trong Redis: đã phát tới mốc nào, mã băm tại đó; ghi cùng giao dịch với nến. |
| **Mã băm lịch** (digest) | SHA-256 tích lũy của toàn bộ lịch phát; cùng đầu vào thì cùng mã. |
| **Cửa sổ 1200** | Mỗi List giữ tối đa 1200 nến gần nhất; nến cũ hơn bị đẩy ra (kèm Hash của nó). |

---

## 4. Input

### 4.1 Dữ liệu nến từ SQL Server

DPS **chỉ đọc**, qua đúng 4 câu `SELECT` trong `sql_source.py`.

| Bảng | Cột dùng | Mục đích |
|---|---|---|
| `DWH.Fact_OHLCV` | `SymbolID`, `TimeframeID`, `BarTime`, `Open`, `High`, `Low`, `Close` | Nến cần phát và nến seed. Cột `Volume`, `TickCount`, `CreatedAt` không dùng. |
| `DWH.Dim_Symbol` | `SymbolID`, `Symbol`, `IsActive` | Đổi tên symbol sang ID; chỉ symbol đang bật. |
| `DWH.Dim_Timeframe` | `TimeframeID`, `Code`, `Minutes` | Đổi mã khung sang ID và độ dài khung. |

Đặc điểm của dữ liệu cần biết (đã đo trên SQL thật ngày 2026-10-04):

- **Nến theo giờ mở, mỗi khung là một chuỗi riêng** lấy trực tiếp từ nhà cung cấp, không gộp từ khung nhỏ. M15 và H1 lưu sẵn khớp tuyệt đối với phép gộp từ M5
  (kiểm trên GOLD, DE40, BTCUSD, US100, 40 ngày, 0 lệch).
- **Giờ mở của khung lớn neo theo phiên và dịch theo giờ mùa hè/đông**: D1 mở 21:00, 22:00 hoặc 23:00 UTC tùy symbol và mùa. Ngay cả M45, H2, H4 cũng không đóng
  cùng giờ giữa các symbol. Vì vậy DPS luôn dùng `BarTime` thật, không tính theo lưới UTC.
- **Độ sâu** (11 symbol live): M20, M30, M45, H1, H2, H3, H4 đủ 365 ngày gần nhất; M10 có từ khoảng 11/2025 (BTCUSD từ 01/2026). Lịch sử cũ hơn có lỗ hổng nhiều
  năm ở một số khung/symbol (đã thấy ở M15; chưa đo từng khung còn lại).
- **Fact giữ phiên bản mới nhất của mỗi nến**: thủ tục ghi (`usp_LoadDirect`) cập nhật nến khi giá đổi, nên SQL là dữ liệu đã hiệu chỉnh, có thể khác chút so với giá
  mà live thấy lúc đó. Theo tiền đề (mục 1.4) DPS coi SQL là đúng.

### 4.2 Cấu hình `config.yaml`

File riêng tư, chứa thông tin kết nối: **không commit, không in ra, không dán vào chat** (đã có trong `.gitignore`). Mẫu không bí mật: [config.example.yaml](config.example.yaml).
Khóa lạ hoặc thiếu đều bị từ chối.

| Mục | Khóa | Ý nghĩa | Hiện tại |
|---|---|---|---|
| `app` | `runtime_dir` | Thư mục state, khóa, log (tính từ thư mục chứa config) | `runtime` |
| `sql_server` | `server`, `port`, `database` | Máy chủ SQL | cùng máy chủ SQL của dp_program, DB `SEN05_AutoTrading` |
| | `username`, `password`, `trusted_connection` | Để trống tài khoản = Windows authentication | Windows authentication |
| | `encrypt`, `trust_server_certificate` | Tùy chọn kết nối | như dp_program |
| `redis` | `host`, `port`, `username`, `password` | Máy chủ Redis | Redis OG8 |
| | `db` | db sẽ ghi | **15** |
| | `allowed_dbs` | Danh sách db DPS được phép ghi; `db` phải nằm trong đây | `[15]` |
| | `key_prefix` | Tiền tố key nến | `L_CANDLE` (đúng live) |
| | `bars_per_snapshot` | Cửa sổ mỗi List | 1200 (đúng live) |
| | `hash_ttl_seconds` | TTL mỗi Hash nến, tính theo giờ máy chủ Redis | 604800 (7 ngày) |
| `replay` | `symbols`, `timeframes` | Phát cái gì | 11 symbol live × M10, M20, M30, M45, H1, H2, H3, H4 |
| | `start_utc`, `end_utc` | Khoảng mô phỏng (T0 → mốc cuối) | 2026-09-30 13:00 → 15:00 |
| `pacing` | `mode` | `delay` hoặc `speed` | `delay` |
| | `delay_seconds`, `speed` | Tham số của từng chế độ | 1.0 giây; 300 |

Các giá trị kỹ thuật cố định (driver ODBC 18, timeout, số lần thử lại, cửa sổ đọc SQL 7 ngày) do mã sở hữu, không chỉnh trong YAML.

### 4.3 Tham số dòng lệnh

Ghi đè `config.yaml` cho từng lần chạy (áp dụng cho `plan`, `run`, `resume`):

| Tham số | Ý nghĩa |
|---|---|
| `--start "2026-09-30 13:00:00"` `--end "2026-09-30 15:00:00"` | Khoảng mô phỏng, UTC |
| `--symbols GOLD,DE40` `--timeframes M10,M20` | Danh sách phát (phân cách bằng dấu phẩy) |
| `--delay 0.2` | Số giây thật giữa hai mốc (0 = tốc độ tối đa) |
| `--speed 300` | Số giây ảo mỗi giây thật (thay cho `--delay`; hai tham số loại trừ nhau) |
| `--config đường\dẫn\config.yaml` | Dùng file cấu hình khác |
| `plan --show N` · `plan --export lich.csv` | In N mốc đầu · xuất toàn bộ lịch ra CSV |

### 4.4 Điều kiện tiên quyết

- Python 3.12 với `pyodbc`, `redis` (redis-py), `PyYAML`; ODBC Driver 18 for SQL Server. (Chạy test cần thêm `pytest`.)
- Tài khoản Windows chạy DPS có quyền đọc SQL.
- Redis đích là **standalone** (không cluster — script Lua ghi key Hash sinh theo chương trình) và, nếu consumer cần sự kiện, phải bật `notify-keyspace-events`
  có `K` và `h` (Redis OG8 hiện là `g$hzK`).
- db đích **rỗng** khi bắt đầu một lần chạy mới.

---

## 5. Xử lý

### 5.1 Luồng chạy của `run`

1. **Khởi động.** Nạp và kiểm `config.yaml` + tham số; lấy *khóa một-instance* (chỉ một DPS chạy trên cùng `runtime_dir`); cài bộ bắt tín hiệu dừng (Ctrl+C, file `stop.request`).
2. **Xác định các cặp.** Đọc `Dim_Symbol` / `Dim_Timeframe` để ra 88 cặp (symbol × khung), xếp theo `SymbolID` rồi độ dài khung. Tên lạ hoặc symbol tắt → dừng với lỗi.
3. **Kiểm an toàn Redis** (mục 5.7): db được phép, rỗng (hoặc của DPS khi `resume`); đặt marker `dps:owner`.
4. **Seed** (bỏ qua khi `resume`, mục 5.3): nạp 1200 nến đã đóng trước T0 cho từng cặp; ghi `dps:clock` = T0 và checkpoint ban đầu.
5. **Vòng phát mốc.** Với mỗi mốc theo thứ tự thời gian: kiểm yêu cầu dừng → chờ theo pacing (mục 5.5) → ghi cả mốc trong một giao dịch (mục 5.4) → cập nhật tiến độ.
   Lịch được sinh dần theo từng cửa sổ 7 ngày giờ phát nên bộ nhớ không đổi dù chạy cả năm (mục 5.2).
6. **Kết thúc.** Hết mốc: đánh dấu `finished`; ghi `state.json` và `manifest.json`; in tóm tắt. Bị dừng giữa chừng: giữ trạng thái `running` trong Redis để chạy tiếp.

`plan` đi qua bước 1–2 và bước lập lịch, nhưng **không đụng Redis**: chỉ thống kê và in lịch.

### 5.2 Thuật toán lập lịch (lõi của DPS)

**Quy tắc.** Một nến chỉ được phát khi đã đóng:

```
giờ phát = BarTime + Minutes          (BarTime = giờ mở, Minutes = độ dài khung)
```

Mọi nến cùng giờ phát nhập thành một **mốc**; mốc sắp theo giờ phát tăng dần. Trong một mốc, nến sắp theo **(khung nhỏ trước, rồi SymbolID)**, nên thứ tự luôn như nhau.
Đồng hồ ảo **nhảy** từ mốc này sang mốc kế tiếp (không dò từng phút), nên khoảng nghỉ của thị trường không tốn thời gian ở chế độ `delay`.

**Vì sao không "cứ 3 nến M5 thì đẩy một M15".** Giờ đóng phụ thuộc giờ mở thật trong SQL: phiên nghỉ, cuối tuần, lỗ hổng dữ liệu và giờ neo phiên (khác nhau giữa symbol, dịch theo mùa) làm
lệch cách đếm. Dùng giờ phát tính từ `BarTime` thì luôn đúng.

**Ví dụ 1 — đúng ví dụ của operator** (GOLD, M5 + M15, delay 1 giây; `@hh:mm` là giờ mở nến). Kết quả thật từ SQL:

| Mốc | Giây thật | Giờ ảo (UTC) | Phát lên Redis |
|---:|---:|---|---|
| 1 | 1 | 13:05 | `M5@13:00` |
| 2 | 2 | 13:10 | `M5@13:05` |
| 3 | 3 | 13:15 | `M5@13:10` **+** `M15@13:00` (cùng một lượt) |
| 4 | 4 | 13:20 | `M5@13:15` |
| 5 | 5 | 13:25 | `M5@13:20` |
| 6 | 6 | 13:30 | `M5@13:25` **+** `M15@13:15` |

**Ví dụ 2 — nhịp khác nhau giữa symbol** (cấu hình hiện tại, 2026-09-30). Mốc 13:30 gồm `M10@13:20` của cả 11 symbol, `M30@13:00` của cả 11 symbol và `M45@12:45` của
**10 symbol** — riêng GOLD đóng M45 lúc **13:45** (mốc 13:45 chỉ có `GOLD.M45@13:00`) vì phiên của GOLD neo khác. DPS không cần biết quy tắc phiên: nó chỉ đọc giờ mở thật.

**Đọc dữ liệu.** SQL được đọc theo cửa sổ giờ phát 7 ngày. Điều kiện "giờ phát trong (a, b]" được viết lại thành `BarTime` trong `(a − Minutes, b − Minutes]` để dùng được index covering của bảng;
mỗi cặp một truy vấn mỗi cửa sổ. Cả năm cho 165 cặp đọc trong 27 giây.

**Tất định và mã băm.** Mỗi mốc được băm (symbol, khung, giờ mở, bốn giá) rồi nối vào một chuỗi SHA-256 chạy suốt lịch. Mã băm cuối là "dấu vân tay" của lần chạy; mã băm tại từng mốc được lưu làm
checkpoint.

**Quy mô** (cửa sổ 365 ngày 2025-10-04 → 2026-10-04, đo 2026-10-05):

| Cấu hình | Số cặp | Số mốc | Số nến | Tối đa nến/mốc | Thời gian dựng lịch |
|---|---:|---:|---:|---:|---:|
| Hiện tại: 11 symbol × 8 khung | 88 | 61.895 | 908.701 | 81 | 25 giây |
| Toàn bộ live: 11 symbol × 15 khung | 165 | 92.508 | 1.745.381 | 120 | 43 giây |

### 5.3 Seed

Trước mốc đầu tiên, Redis phải giống Redis live tại thời điểm T0: mỗi List đã đủ 1200 nến gần nhất. DPS đọc `TOP 1200` nến có `BarTime ≤ T0 − Minutes` của từng cặp, đảo về thứ tự tăng dần và ghi bằng cùng script Lua
như live (100 nến mỗi lần gọi, 50 lần gọi mỗi vòng mạng). Cặp có lịch sử ngắn hơn 1200 thì List ngắn hơn, giống live. Với nến seed, `time_update` = T0.

Seed sinh khoảng một sự kiện `hset` cho mỗi nến (≈ 105.600 sự kiện với 88 cặp), nên **bật consumer sau khi seed xong** (mục 8).

### 5.4 Ghi một mốc lên Redis

Mỗi mốc được ghi trong **một giao dịch `MULTI/EXEC`** gồm:

1. với mỗi nến của mốc: gọi script Lua giống hệt live — chỉ `HSET` Hash của nến (+ `EXPIRE`) nếu nến mới hoặc đổi giá, `LPUSH` stamp vào đầu List, rồi đẩy phần dư ra khỏi cửa sổ 1200 bằng
   `RPOP` + `DEL` Hash tương ứng;
2. `SET dps:clock` = giờ ảo của mốc;
3. `HSET dps:state` = checkpoint (`run_id`, `tick_seq`, `sim_time`, `digest`, `status`).

Hệ quả: consumer **không bao giờ thấy một mốc ghi dở** (nếu M5 đã tới mà M15 cùng giờ đóng chưa tới); checkpoint **luôn khớp** dữ liệu đã ghi; nếu ghi lỗi giữa chừng thì hoặc cả mốc được áp dụng hoặc không mốc nào.
`time_update` của nến mới = giờ ảo của mốc (khác live, vốn là giờ máy lúc ghi).

Script Lua **idempotent**: ghi lại nến cùng giá thì không sinh `hset` nào. Đây là lý do mọi lần chạy mới bắt đầu từ db rỗng — chạy lại lên dữ liệu cũ sẽ **im lặng**, consumer không nhận được sự kiện nào.

### 5.5 Pacing — quy đổi giờ ảo ra giờ thật

Cả hai chế độ dùng **deadline tuyệt đối** trên đồng hồ đơn điệu (không cộng dồn sleep nên không trôi): mốc đến muộn thì không chờ nữa và độ trễ được ghi nhận (`max_lag`).

| Chế độ | Quy tắc | Khoảng nghỉ của thị trường | Dùng khi |
|---|---|---|---|
| `delay` | mốc thứ k phát lúc `t0 + k × delay`; `delay = 0` là tốc độ tối đa | **không tốn thời gian** (nhảy qua) | muốn chạy nhanh nhất |
| `speed` | mốc phát lúc `t0 + (giờ ảo − gốc) / speed` | **giữ nguyên tỷ lệ** (cuối tuần vẫn "im lặng" tương ứng) | muốn nhịp giống thật, chỉ nhanh hơn |

Mốc đầu tiên phát sau một khoảng `delay` (đúng ví dụ "giây số 1 đẩy mốc đầu"). Mọi lần chờ được cắt thành lát 0,25 giây để phản ứng ngay với lệnh dừng.

Thời gian chạy một năm của cấu hình hiện tại (61.895 mốc; chưa tính ≈ 7 giây seed):

| Cách chạy | Thời gian |
|---|---:|
| `--delay 1` | ≈ 17,2 giờ |
| `--delay 0.5` | ≈ 8,6 giờ |
| `--delay 0.1` | ≈ 1,7 giờ |
| `--speed 300` (một năm ≈ 31,5 triệu giây ảo) | ≈ 29,2 giờ |
| `--speed 3000` | ≈ 2,9 giờ |

*Ước tính theo công thức; mới đo thực tế ở `delay 1` (độ trễ tối đa 16 ms). Ở tốc độ cao hơn, `max_lag` trong tóm tắt cho biết Redis có theo kịp không.*

### 5.6 Checkpoint, dừng và chạy tiếp

- **Dừng sạch**: `dps.bat stop` (hoặc Ctrl+C, hoặc tạo file `runtime\run\stop.request`). DPS hoàn tất mốc đang ghi rồi dừng, giữ checkpoint với trạng thái `running`.
- **Chạy tiếp**: `dps.bat resume`. DPS dựng lại lịch từ T0, chỉ cập nhật mã băm cho các mốc đã chạy, **đối chiếu mã băm tại checkpoint**, rồi ghi tiếp từ mốc kế. Nếu mã băm lệch — dữ liệu hoặc tham số đã đổi —
  DPS từ chối thay vì ghi tiếp sai.
- **Phải dùng đúng các tùy chọn của lần `run` gốc** (khoảng thời gian, symbol, khung). Nếu bạn chỉnh bằng `config.yaml` thay vì tham số dòng lệnh thì không gặp vấn đề này.
- Lần chạy đã `finished` không chạy tiếp được; muốn chạy lại phải `clean` trước.

### 5.6b Khi Redis mất kết nối (tắt máy, đứt mạng, đang nạp lại)

DPS **không tự thoát** vì Redis: chỉ không ghi được thì chờ, Redis trả lời lại thì chạy tiếp.

- **Chờ.** Lỗi Redis *tạm thời* (kết nối bị từ chối/đứt, hết thời gian chờ, `LOADING`, `MISCONF`, `READONLY`) khiến DPS chuyển sang `status: waiting_redis`, thử lại sau 1, 2, 4, 8 rồi 15 giây một lần,
  không bao giờ bỏ cuộc vì hết giờ. Trong lúc chờ nó vẫn ghi heartbeat (`dps.bat status` báo `healthy: True`, kèm `waiting_since`, `redis_retries`, `redis_error`) và vẫn nghe `dps.bat stop`/Ctrl+C (dừng trong vài giây).
  Log: `REDIS_UNAVAILABLE` lúc bắt đầu chờ, nhắc `REDIS_STILL_UNAVAILABLE` mỗi phút, `REDIS_RECOVERED` khi Redis trả lời. Khởi động `run`/`resume` khi Redis đang tắt cũng chờ như vậy.
- **Chạy tiếp đúng chỗ.** Mỗi mốc là một giao dịch nguyên tử và script Lua ghi lặp không đổi kết quả, nên sau khi Redis về DPS đọc `dps:state`: nếu checkpoint còn là mốc đang ghi (hoặc ngay trước nó) thì ghi tiếp
  đúng mốc đó (nếu giao dịch lần trước đã được áp dụng dù mất phản hồi thì không ghi lần hai); nếu Redis quay lại bằng **snapshot cũ hơn** thì DPS dựng lại lịch rồi chạy tiếp từ checkpoint đó như `--resume`,
  ghi lại phần Redis đã mất (log `REDIS_CHECKPOINT_BEHIND`). Kết quả cuối cùng và mã băm giống hệt lần chạy liền một mạch (có test).
- **Không phát bù dồn dập.** Sau khi chờ, đồng hồ pacing được đặt lại: mốc kế tiếp cách đúng `delay` giây chứ không chạy hết tốc độ để "đuổi" khoảng đã mất. Cùng nguyên tắc cho mọi lần đứng hình quá 5 giây
  (Redis/SQL chậm): log `PACER_RESYNC`, `max_lag` vẫn ghi độ trễ thật.
- **Vẫn dừng (có hướng dẫn rõ)** khi: Redis quay lại **không còn dữ liệu của DPS** (restart không persist → `use clean and start a new run`); Redis mất kết nối **ngay trong lúc seed** (chưa có checkpoint → `clean` rồi `run`);
  lỗi không phải mất kết nối (sai mật khẩu, sai kiểu key, script lỗi...); lịch không khớp checkpoint.
- **Chưa bao phủ SQL:** nếu SQL Server mất kết nối giữa chừng, lần chạy vẫn dừng ở trạng thái `failed`; dùng `dps.bat resume`.

### 5.7 Cơ chế an toàn

| Cơ chế | Tác dụng |
|---|---|
| Hai lớp guard Redis | (1) `db` phải nằm trong `allowed_dbs`; (2) db phải rỗng hoặc mang marker `dps:owner`. Db0 của live không bao giờ qua được. |
| `clean` = `FLUSHDB ASYNC` sau khi qua guard | Chỉ xóa db của DPS; không bao giờ `FLUSHALL`; không dùng `KEYS`/`SCAN` (có test kiểm trên mã nguồn). |
| SQL chỉ đọc | Chỉ 4 câu `SELECT`; test kiểm không có câu ghi nào trong `sql_source.py`. |
| Khóa một-instance | Hai tiến trình DPS không chạy chồng trên cùng `runtime_dir`. |
| Từ chối chạy chồng lên dữ liệu cũ | Vì ghi lặp sẽ im lặng (mục 5.4). |
| Bí mật | `config.yaml` bị `.gitignore`; log che mọi trường chứa `password`/`secret`/`token`; thông báo lỗi không kèm thông tin kết nối. |
| Cách ly key | Key điều khiển `dps:*` nằm ngoài mẫu `L_CANDLE_*` mà consumer lắng nghe; sự kiện db15 đi trên kênh `__keyspace@15__`, không lẫn với db0. |

---

## 6. Output

### 6.1 Trên Redis (db15)

Dữ liệu nến — **cùng cấu trúc live**:

| Key | Kiểu | Nội dung |
|---|---|---|
| `L_CANDLE_{SYMBOL}_{TF}`, ví dụ `L_CANDLE_GOLD_M5` | **LIST** | Các mốc giờ, **nến mới nhất ở index 0**, tối đa 1200. Không có TTL. |
| `L_CANDLE_{SYMBOL}_{TF}:{giờ mở}`, ví dụ `L_CANDLE_GOLD_M5:2026-09-30 14:55:00` | **HASH** | Một nến, đúng 6 field, TTL 7 ngày. |

Ví dụ thật (lấy từ lần chạy thử GOLD M5 + M15; các khung M10 … H4 có cùng cấu trúc) — Hash `L_CANDLE_GOLD_M5:2026-09-30 14:55:00`:

```
timestamp    2026-09-30 14:55:00      giờ mở nến (UTC), trùng phần tử List và đuôi key Hash
open         4175.29                  giá làm tròn tối đa 2 số lẻ, bỏ số 0 thừa ("4176.6", "25653")
high         4176.6
low          4164.92
close        4165.23
time_update  2026-09-30 15:00:00      giờ ảo của mốc đã phát nến này (seed: T0). Khác live: ở live là giờ máy.
```

Key điều khiển của DPS (không thuộc hợp đồng live):

| Key | Kiểu | Nội dung |
|---|---|---|
| `dps:clock` | STRING | Giờ ảo hiện tại, ví dụ `2026-09-30 15:00:00`. Cập nhật cùng giao dịch với nến. |
| `dps:state` | HASH | Checkpoint: `run_id`, `tick_seq`, `sim_time`, `digest`, `status` (`running` / `finished`). |
| `dps:owner` | STRING | Marker "db này thuộc DPS". |

**Quy mô** (đo thật, 88 cặp): 105.600 Hash + 88 List + 3 key điều khiển = **105.691 key**, tăng **34,0 MB** bộ nhớ Redis (≈ 322 byte/key); `clean` đưa bộ nhớ về mức cũ. Nếu seed đủ 165 cặp: khoảng 198.000
key, ước tính ≈ 64 MB.

**Sự kiện**: mỗi nến mới hoặc đổi giá sinh sự kiện keyspace `hset` (cùng `expire`) trên kênh `__keyspace@15__:L_CANDLE_…`, đúng như live trên db0.

### 6.2 Trên đĩa (`dps/runtime/`, tự tạo, bị `.gitignore`)

| File | Nội dung |
|---|---|
| `logs/dps.log` | Log một dòng mỗi sự kiện (`RUN_START`, `SEED_DONE`, `PROGRESS` mỗi 10 giây, `RUN_FINISHED`/`RUN_STOPPED`/`RUN_FAILED`; khi Redis chập chờn: `REDIS_UNAVAILABLE`, `REDIS_STILL_UNAVAILABLE`, `REDIS_RECOVERED`, `REDIS_CHECKPOINT_BEHIND`; `PACER_RESYNC`); xoay vòng mỗi 5 MB, giữ 5 bản cũ. |
| `run/state.json` | Trạng thái tiến trình kèm heartbeat (ghi giãn nhịp ≥ 2 giây): `status` (`running`, `waiting_redis`, `stopped`, `finished`, `failed`), `pid`, `tick_seq`, `sim_time`, `ticks`, `candles`; khi chờ Redis có thêm `waiting_since`, `redis_retries`, `redis_error`. |
| `run/manifest.json` | Tóm tắt lần chạy vừa xong: `run_id`, trạng thái, cấu hình (không bí mật), số mốc/nến, mã băm lịch, mốc cuối. |
| `run/dps.lock` | Khóa một-instance. |
| `run/stop.request` | Tồn tại tạm thời khi có yêu cầu dừng. |
| *(tùy chọn)* `lich.csv` | Từ `plan --export`: `seq, release_utc, symbol, timeframe, bar_open_utc` cho mọi nến. |

### 6.3 Trên màn hình và log

`plan`:

```
      3  2026-09-30 13:15  GOLD.M5@13:10 + GOLD.M15@13:00
pairs: 88 | ticks: 14 | candles: 319 | max candles per tick: 72
first tick: 2026-09-30 13:10:00 | last tick: 2026-09-30 15:00:00
schedule digest: b42a5c8497a8ba4ee35b56647909caa06774a459db084f69174ef8e11ae98c79
estimated run time at the current pacing (delay): 14.0 s = 0.00 h
```

`run` (rút gọn):

```
... INFO dps.player RUN_START run_id=64056be5210c resume=False pairs=88 start=2026-09-30 13:00:00 end=2026-09-30 15:00:00 pacing=delay
... INFO dps.player SEED_DONE candles=105600 hset=105600 evicted=0
... INFO dps.player RUN_FINISHED run_id=64056be5210c ticks=14 candles=319 hset=319 max_lag_s=0.016 digest=b42a5c8497a8ba4e
finished: run_id=64056be5210c ticks=14 candles=319 hset=319 evicted=319 max_lag=0.016s last_sim_time=2026-09-30 15:00:00
schedule digest: b42a5c8497a8ba4ee35b56647909caa06774a459db084f69174ef8e11ae98c79
```

Cách đọc: `ticks`/`candles` = số mốc/nến đã ghi trong lần chạy này · `hset` = số nến thật sự ghi mới hoặc đổi · `evicted` = số nến bị đẩy khỏi cửa sổ 1200 · `max_lag` = độ trễ lớn nhất so với lịch pacing ·
`digest` = dấu vân tay của lịch (giống `plan` thì lần chạy khớp lịch dự kiến).

### 6.4 Kiểm tra kết quả

- Nhanh: `dps.bat status` — số key, `dps:clock`, checkpoint (`status: finished`, `tick_seq` đúng số mốc), và **mã băm của `run` phải trùng mã băm của `plan`** cho cùng tham số.
- Đối chiếu sâu (đã làm trong lần chạy thử): so từng List và từng Hash trong Redis với 1200 nến đóng gần nhất trong SQL tại mốc cuối (6 field, định dạng giá, `time_update`). Hiện chưa có lệnh `verify` dựng sẵn; kịch bản này
  chạy thủ công bằng đoạn mã chỉ đọc.

---

## 7. Cách chạy và vận hành

### 7.1 Chuẩn bị

1. Có `dps/config.yaml` (sao chép từ `config.example.yaml` rồi điền; bản đang dùng đã có sẵn).
2. Python 3.12 trên PATH (hoặc đặt biến `DPS_PYTHON` trỏ tới `python.exe`).
3. Chắc chắn db đích (db15) rỗng — `dps.bat status` cho biết số key.

### 7.2 Quy trình chuẩn

```powershell
cd C:\Users\Administrator\Desktop\dp_program\dps
dps.bat plan --show 12        # 1) xem trước lịch phát, không ghi gì
dps.bat run --delay 0.2       # 2) phát lên db15 (cấu hình hiện tại: 14 mốc, vài giây sau seed)
dps.bat status                # 3) kiểm tiến độ và checkpoint
dps.bat clean                 # 4) dọn db15 trước lần chạy mới (báo số key, hỏi trước)
```

### 7.3 Các mode của `dps.bat`

Chạy được từ bất kỳ thư mục nào; tham số được chuyển nguyên văn (giữ dấu phẩy, `=` và dấu ngoặc kép).

| Lệnh | Việc làm |
|---|---|
| `dps.bat plan [tùy chọn]` | Chạy khô: in lịch phát + mã băm, không ghi gì |
| `dps.bat run [tùy chọn]` | Seed rồi replay lên db đích (db phải rỗng) |
| `dps.bat resume [tùy chọn]` | Chạy tiếp từ checkpoint (dùng đúng tùy chọn của lần `run` gốc) |
| `dps.bat status` | Trạng thái tiến trình + số key, đồng hồ ảo, checkpoint trong Redis |
| `dps.bat stop` | Yêu cầu dừng sạch (chạy tiếp được bằng `resume`) |
| `dps.bat clean` | Báo số key rồi hỏi trước khi xóa db đích; thêm `/y` để bỏ câu hỏi |
| `dps.bat clean-files` | Xóa `runtime\`, `__pycache__`, `.pytest_cache` trong thư mục `dps` (từ chối khi đang có run; không đụng Redis) |
| `dps.bat help` | Liệt kê mode và tùy chọn |

Tương đương không dùng .bat: `python -B src plan | run [--resume] | status | stop | clean --yes`. Mã thoát: `0` thành công · `1` lỗi nghiệp vụ hoặc không mong đợi (thông báo ở stderr) · `2` sai cú pháp lệnh.

### 7.4 Các kịch bản thường gặp

| Tình huống | Cách làm |
|---|---|
| Chạy thử nhỏ | `dps.bat run --symbols GOLD --timeframes M10,M20 --start "2026-09-30 13:00:00" --end "2026-09-30 15:00:00" --delay 0.2` |
| Chạy nhanh nhất | thêm `--delay 0` |
| Giữ nhịp tỷ lệ thật | thay `--delay` bằng `--speed 300` |
| Dừng giữa chừng rồi tiếp | `dps.bat stop` … `dps.bat resume` (cùng tùy chọn như lúc `run`) |
| Chạy lại từ đầu | `dps.bat clean` rồi `dps.bat run` |
| Đổi khoảng thời gian | sửa `config.yaml` (hoặc dùng `--start/--end`), `clean`, `run` |
| Dọn file tạm cục bộ | `dps.bat clean-files` |

### 7.5 Xử lý sự cố

| Thông báo | Nguyên nhân | Cách xử lý |
|---|---|---|
| `db 15 still holds a previous DPS run; use clean … or run --resume` | db còn dữ liệu của lần chạy trước | `dps.bat clean`, hoặc `dps.bat resume` nếu muốn chạy tiếp |
| `db 15 holds N keys that DPS does not own; refusing to write` | db đích đang chứa dữ liệu không phải của DPS | **Không xóa.** Tìm xem ai dùng db đó; chọn db được phép khác hoặc dọn thủ công nếu chắc chắn |
| `db X is not in allowed_dbs […]` | `redis.db` không nằm trong danh sách cho phép | Sửa `config.yaml`; chỉ dùng db thử nghiệm |
| `another DPS process is already running` | Có một DPS khác đang chạy cùng `runtime_dir` | `dps.bat status`, rồi `dps.bat stop` nếu cần |
| `the schedule differs from the checkpoint … cannot resume` | Tùy chọn hoặc dữ liệu khác lần `run` gốc | Chạy `resume` với đúng `--start/--end/--symbols/--timeframes` ban đầu |
| `nothing to resume` · `previous run already finished` · `checkpoint … incomplete` | Không có / đã xong / hỏng checkpoint | `clean` rồi `run` lại |
| `unknown or inactive symbol/timeframe: X` | Tên không có trong `Dim_Symbol`/`Dim_Timeframe` hoặc symbol đã tắt | Sửa tên trong config hoặc tham số |
| `SQL Server connection failed (…)` | SQL không chạy / thiếu driver / thiếu quyền | Kiểm dịch vụ SQL, ODBC Driver 18, tài khoản Windows |
| `redis: unreachable (…)` (trong `status`) | Không nối được Redis | Kiểm mạng, `host`/`port`/mật khẩu. Đang chạy thì DPS tự chờ (`status: waiting_redis`), không cần làm gì |
| `Redis came back without the DPS checkpoint (its data was lost); use clean and start a new run` | Redis khởi động lại mà không giữ dữ liệu DPS | `dps.bat clean` rồi `dps.bat run` |
| `Redis became unavailable while the run was being seeded …` | Mất kết nối đúng lúc seed (chưa có checkpoint) | `dps.bat clean` rồi `dps.bat run` |
| `configuration file not found` hoặc `<mục>.<khóa>: …` | Thiếu `config.yaml` hoặc giá trị sai | Đối chiếu với `config.example.yaml` |
| `error: unexpected …; see runtime/logs/dps.log` | Lỗi không lường trước | Xem chi tiết trong log (log đã che bí mật) |
| `python was not found on PATH` | Máy không có Python trên PATH | Cài Python 3.12 hoặc đặt `DPS_PYTHON` |

---

## 8. Tích hợp với OG / chiến lược

Đây là **bước tiếp theo, chưa làm** (mục 11). DPS đã ghi dữ liệu đúng hợp đồng; việc còn lại nằm ở phía consumer:

1. **Trỏ vào db15.** Consumer thử nghiệm đọc Redis ở `db=15` (cùng máy chủ, cùng `key_prefix`) và nghe `__keyspace@15__:L_CANDLE_*`. Cách đọc List/Hash **không đổi** so với live.
2. **Bật consumer sau khi seed xong.** Seed sinh một loạt sự kiện `hset` (≈ 105.600 với 88 cặp); subscriber chậm có thể bị Redis ngắt khi vượt giới hạn bộ đệm mặc định (cứng 32 MB, mềm 8 MB trong 60 giây).
3. **Dùng giờ ảo khi chạy nhanh hơn thật.** Nếu consumer dùng giờ máy cho kiểm tra tuổi tín hiệu, lọc phiên, cooldown… thì sẽ sai khi mô phỏng nhanh. `dps:clock` được cập nhật cùng mỗi mốc để consumer dùng làm "bây giờ";
   chưa có consumer nào dùng. Cần rà code OG / chiến lược về việc dùng giờ máy.
4. **Không đọc SQL trực tiếp trong lúc mô phỏng.** `MART.usp_GetLatestCandles` không có tham số as-of, nên sẽ thấy dữ liệu **mới hơn** giờ ảo ("nhìn thấy tương lai").
5. **Tính nguyên tử theo mốc** giúp consumer luôn thấy trạng thái nhất quán, nhưng mỗi nến trong mốc vẫn sinh một sự kiện `hset` riêng: consumer sẽ nhận nhiều sự kiện cho cùng một mốc.
6. **Đồng bộ tốc độ.** Hiện DPS chạy theo `delay`/`speed` mà không chờ consumer xử lý xong. Cần kết quả lặp lại tuyệt đối thì phải có cơ chế *lock-step* (DPS chờ consumer báo xong mỗi mốc) — chưa làm.

---

## 9. Cấu trúc mã nguồn và kiểm thử

### 9.1 Bảy file trong `dps/src/` (khoảng 1.100 dòng)

Cấu trúc phẳng; các module import nhau trực tiếp; `python src` thực thi `src/__main__.py`. Mỗi tài nguyên bên ngoài chỉ có **một owner**.

| File | Trách nhiệm duy nhất |
|---|---|
| `__main__.py` | CLI `plan` / `run` / `status` / `stop` / `clean`; chỉ đọc tham số rồi gọi module khác |
| `configuration.py` | Nơi duy nhất đọc `config.yaml`; kiểm kiểu và khoảng; `redis.db` phải nằm trong `allowed_dbs`; không in bí mật |
| `schedule.py` | Giờ phát, thứ tự toàn phần tất định, nhóm thành mốc, đọc theo cửa sổ, mã băm lịch (thuần, không I/O) |
| `sql_source.py` | Mọi truy cập SQL, chỉ `SELECT`: danh sách cặp, nến theo cửa sổ giờ phát, nến seed |
| `redis_writer.py` | Mọi thứ về Redis, hai phần có tiêu đề riêng: (1) hợp đồng Redis của live — tên key, định dạng stamp và giá, bộ field Hash, script Lua, đóng gói tham số (hàm thuần, không I/O); (2) writer — guard, seed, ghi một mốc nguyên tử, checkpoint, clean |
| `player.py` | Vòng chạy: seed → từng mốc → pacing → ghi → checkpoint; `resume`; chạy khô (`plan`) |
| `runtime.py` | Nền tảng dùng chung, bốn phần có tiêu đề riêng: kiểu dữ liệu (`Pair`, `Candle`, `Tick`, `DpsError`) · log có cấu trúc · điều tốc (pacing) · vòng đời tiến trình (khóa, state, dừng, manifest) |

Ngoài ra: `dps.bat` (chạy nhanh) · `config.yaml` / `config.example.yaml` · `.gitignore` · `research_notes.md` · `test/`.

### 9.2 Quy tắc phụ thuộc (có test kiểm)

- `runtime` không import module nào khác của DPS; `schedule` là lõi thuần — không I/O và chỉ được lấy **kiểu dữ liệu** (`Candle`, `Pair`, `Tick`, `DpsError`) từ `runtime`.
- Các hàm hợp đồng ở phần 1 của `redis_writer` (`stamp`, `parse_stamp`, `round_price`, `list_key`, `hash_prefix`, `script_args`) là hàm thuần: test kiểm chúng không chạm client Redis.
- Chỉ `configuration` import `yaml`; chỉ `sql_source` import `pyodbc`; chỉ `redis_writer` import `redis`.
- `player` nối các phần; `__main__` chỉ gọi `configuration`, `player`, `redis_writer`, `runtime`.
- Mỗi file ≤ 300 dòng code và có docstring nêu trách nhiệm; import giữa các file là import tuyệt đối.
- Quy ước: định danh, thông báo lỗi và log bằng tiếng Anh; comment và docstring bằng tiếng Việt.

### 9.3 Kiểm thử

```powershell
python -B -m pytest test -q -p no:cacheprovider                                        # 97 test đơn vị
$env:DPS_INTEGRATION = "1"; python -B -m pytest test/test_redis_integration.py          # 2 test trên Redis thật (db15 phải rỗng)
```

Bao gồm: đối chiếu trực tiếp với `live.py` (script Lua giống hệt, làm tròn giá trên 3.000 giá trị ngẫu nhiên, stamp, key, tham số); bất biến của lịch (mỗi nến đúng một lần, không phát trước giờ đóng,
thứ tự tất định, kích thước cửa sổ không đổi kết quả); pacing với đồng hồ giả; guard Redis; vòng điều phối với đối tượng giả (dừng, chạy tiếp, từ chối khi lệch mã băm); cấu hình; kiến trúc.
Hai test tích hợp kiểm trên Redis thật: hợp đồng live, tính nguyên tử với reader `MULTI/EXEC` qua 300 mốc, ghi lặp không sinh sự kiện, guard và clean. Test không bao giờ chạy khi db đích chưa rỗng.

### 9.4 Cơ sở thiết kế

Các quyết định chính — lịch phát theo mốc kế tiếp (next-event), nến hiện ở giờ đóng, mỗi mốc là một nhóm nguyên tử, tất định + mã băm, đồng hồ ảo cho consumer — dựa trên nghiên cứu các engine backtest/replay
(NautilusTrader, QuantConnect LEAN, Zipline, backtrader), lý thuyết mô phỏng sự kiện rời rạc và tài liệu Redis. Nguồn và trích dẫn ở [research_notes.md](research_notes.md), mục 4.

---

## 10. Kết quả đã kiểm chứng

| Hạng mục | Kết quả |
|---|---|
| Test | 97 test đơn vị + 2 test tích hợp qua (chạy lại 06/10/2026 sau khi gộp `contract.py` vào `redis_writer.py`); đối chiếu với `live.py` khớp |
| Ví dụ của operator | GOLD M5 + M15 trên dữ liệu thật: mốc 3 phát cả M5 và M15 |
| Lịch phát cả năm | 165 cặp: 92.508 mốc, 1.745.381 nến, tối đa 120 nến/mốc; 88 cặp: 61.895 mốc, 908.701 nến, tối đa 81 nến/mốc |
| Chạy thử 88 cặp lên db15 (2026-10-04) | Seed 105.600 nến ≈ 7 giây; 14 mốc (319 nến), mỗi mốc 1 giây; tổng 21,6 giây; `max_lag` 16 ms; mã băm lúc chạy trùng mã băm `plan` |
| Đối chiếu với SQL | `DBSIZE` 105.691 đúng kỳ vọng; 88/88 List giống hệt; 105.600/105.600 Hash giống hệt (6 field, định dạng giá, `time_update`); TTL ≈ 7 ngày; `dps:clock`/`dps:state` đúng |
| Tác động Redis dùng chung | Bộ nhớ +34,0 MB (87,09 → 121,12 MB); sau `clean` về 87,13 MB; số key db0–db3 không đổi |
| Dừng và chạy tiếp | `stop` rồi `resume` với cùng tùy chọn cho mã băm cuối trùng hệt `plan`; lần chạy bị dừng in đúng mã băm checkpoint |
| An toàn | Chạy chồng lên dữ liệu cũ bị từ chối; resume run đã xong bị từ chối; khóa một-instance chặn tiến trình thứ hai; `clean` chỉ xóa db của DPS |
| `dps.bat` | Mọi mode đã thử, gồm tham số có dấu phẩy, ngoặc kép và `=` |

---

## 11. Giới hạn, rủi ro và lộ trình

**Giới hạn đã biết**

1. **Chưa nối consumer** và chưa có đồng hồ ảo / lock-step ở phía OG và chiến lược (mục 8).
2. **Chưa chạy cả năm** lên Redis. Cần đồng ý trước vì db15 dùng chung máy chủ với production (bộ nhớ và CPU dùng chung; db15 chỉ cách ly theo chỉ số db). Cân nhắc Redis riêng cho các lần chạy dài.
3. **Giờ phát theo độ dài danh nghĩa** (`giờ mở + Minutes`) giống hệ thật, nhưng có thể lệch giờ đóng thực của phiên với nến D1/W, nến cuối phiên hoặc ngày đóng sớm. Đã chấp nhận vì SQL được coi là đúng.
4. **Dữ liệu SQL là bản đã hiệu chỉnh**, không phải giá consumer thấy lúc đó (first-seen).
5. **Không có độ trễ tới của nến**: nến xuất hiện đúng giờ đóng, trong khi live có độ trễ (chu kỳ 2 phút + xử lý).
6. **Độ sâu dữ liệu**: M10 chỉ từ khoảng 11/2025 (BTCUSD 01/2026); lịch sử cũ có lỗ hổng ở một số khung.
7. **Tốc độ cao chưa đo**: mới đo ở `delay 1`; ở `delay` rất nhỏ cần xem `max_lag`.
8. Việc đọc SQL theo tuần diễn ra giữa các mốc nên có thể tạo vệt trễ nhỏ ở ranh giới cửa sổ (bù bằng deadline tuyệt đối; chưa đọc song song).
9. `resume` đòi đúng các tùy chọn của lần `run` gốc.
10. Chưa có pause / seek / chạy từng mốc, chưa có cache cục bộ, chưa có lệnh `verify` dựng sẵn.
11. `dps/` nằm ngoài repo git của `core_program/` nên chưa được version control.

**Lộ trình đề xuất**

| Bước | Nội dung |
|---|---|
| 1 | Chạy cả năm cấu hình hiện tại lên Redis (sau khi operator đồng ý), đo `max_lag` ở các tốc độ |
| 2 | Rà code OG / chiến lược về việc dùng giờ máy và đọc SQL; thống nhất cách dùng `dps:clock` |
| 3 | v1.1: lock-step có xác nhận của consumer; lưu tham số của lần chạy trong checkpoint để `resume` không cần nhập lại |
| 4 | Lệnh `verify` dựng sẵn; đọc SQL song song với phát mốc; tùy chọn mô hình độ trễ tới |

---

## 12. Phụ lục

### 12.1 Khoảng thời gian mô phỏng và cách DPS xử lý biên

- Mốc phát nằm trong **(T0, mốc cuối]**: nến có giờ phát đúng bằng T0 thuộc seed, không phát lại; nến có giờ phát đúng bằng mốc cuối được phát.
- Cặp không có nến trong một khoảng (thị trường đóng, chưa có dữ liệu) đơn giản không có mốc nào ở đó.
- Hết dữ liệu trước mốc cuối: DPS dừng ở mốc cuối cùng có nến.

### 12.2 Ví dụ phần `replay` và `pacing` của cấu hình hiện tại

```yaml
replay:
  symbols: [FR40, DE40, HK50, J225, SP35, UK100, US500, US100, US30, GOLD, BTCUSD]
  timeframes: [M10, M20, M30, M45, H1, H2, H3, H4]
  start_utc: "2026-09-30 13:00:00"        # T0: seed = nến đã đóng <= T0, nến phát bắt đầu sau T0
  end_utc: "2026-09-30 15:00:00"          # dừng khi hết mốc phát <= end_utc

pacing:
  mode: delay                             # delay: mỗi mốc chờ delay_seconds | speed: tốc độ ảo/thật
  delay_seconds: 1.0
  speed: 300.0                            # chỉ dùng khi mode = speed (300 = 5 phút ảo mỗi giây thật)
```

### 12.3 Sơ đồ trạng thái của một lần chạy

```
 (db rỗng) ──run──► seed ──► đang phát ──hết mốc──► finished ──clean──► (db rỗng)
                               │   ▲
                       stop    │   │  resume (cùng tùy chọn, mã băm khớp)
                               ▼   │
                             stopped (checkpoint giữ nguyên, status = running trong Redis)
```
