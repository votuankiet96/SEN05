# dp_program (SEN05 Data Provider) — Kiến trúc `src/core_engine/`

Tài liệu này mô tả cây thư mục và chức năng từng nhóm file/file trong package Python duy nhất của dp_program (`src/core_engine/`, ~24.000 dòng, 65 file). Biên soạn bằng cách đọc trực tiếp code hiện tại (không suy đoán từ tên file).

## Nguyên tắc tổ chức

5 nhóm, hướng phụ thuộc một chiều **`core/` → `shared/` → `util/`**, cộng 2 nhóm nền tảng đứng riêng (`settings/`, `other/`):

- **`core/`** — lõi nghiệp vụ: 2 con đường duy nhất đưa giá OHLCV vào hệ thống (`live/`, `historical/`).
- **`shared/`** — hạ tầng dùng chung mà CẢ 2 core engine đều cần (nguồn dữ liệu TradingView, đích ghi SQL, công thức nghiệp vụ dùng chung).
- **`util/`** — hạ tầng vận hành/giám sát, không tự lấy/ghi dữ liệu OHLCV.
- **`other/`** — tiện ích không mang trạng thái, dùng bởi cả core lẫn util.
- **`settings/`** — nền cấu hình cho toàn bộ hệ thống, đứng ngoài 4 nhóm trên.

---

## Cây thư mục

```
src/core_engine/
├── __init__.py                          bootstrap: gán process role + bắt crash sớm nhất có thể
├── __main__.py                          entry point `python -m core_engine`
│
├── core/                                 LÕI NGHIỆP VỤ — 2 con đường duy nhất đưa giá vào hệ thống
│   ├── live/                              tiến trình 24/7, nhận giá gần-thời-gian-thực
│   │   ├── engine.py            (2046d)   composition root: startup/shutdown, batch loop, auth preflight
│   │   ├── scheduler.py           (46d)   căn thời điểm chạy batch theo boundary đồng hồ (mỗi 5')
│   │   ├── fetcher.py           (1262d)   1 worker bền vững/nhóm kết nối TradingView WS
│   │   ├── delivery.py           (741d)   staging writer + Fact loader (2 worker tách biệt)
│   │   ├── outbox.py             (493d)   durable write-ahead outbox (SQLite), state machine pending→leased→staged→ack
│   │   ├── runtime.py            (564d)   state + metrics + missing-pair state machine dùng chung của live
│   │   └── telemetry.py          (796d)   log/report/health-classification riêng của live
│   │
│   └── historical/                        job chạy theo lịch, vá lỗ hổng dữ liệu quá khứ
│       ├── engine.py             (625d)   CLI entry, khoá, dispatch theo --mode
│       ├── pipeline.py           (807d)   3 mode runner: full / backfill(gap) / reset
│       ├── runtime_support.py    (618d)   phân loại lỗ hổng thật vs đóng cửa thị trường
│       └── reporter.py           (435d)   log/report riêng của historical
│
├── shared/                                HẠ TẦNG DÙNG CHUNG — cả live và historical đều cần
│   ├── freshness.py               (16d)   1 công thức "ngưỡng dữ liệu cũ" dùng chung (trước đây viết trùng 2 nơi)
│   ├── time.py                    (47d)   helper thời gian thuần (utc_iso, parse, cutoff) không mang domain knowledge
│   │
│   ├── tradingview/                        cổng vào/thẻ ra vào TradingView, dùng chung
│   │   ├── protocol.py           (242d)   đóng/mở gói WS thô, phân loại lỗi WS
│   │   ├── history_client.py     (630d)   gọi WS lấy nến lịch sử (fetch/replay)
│   │   ├── diagnostics.py         (79d)   probe kết nối HTTPS + trạng thái Playwright Chromium (dùng chung)
│   │   └── auth/
│   │       ├── core.py         (2266d)   máy trạng thái auth — 8 lớp fallback (cache→…→guest)
│   │       ├── jwt_utils.py      (118d)   giải mã/kiểm tra JWT thuần, không state
│   │       └── captcha.py        (121d)   giải hCaptcha + sinh mã 2FA cho nhánh đăng nhập lại
│   │
│   └── warehouse/                          đích ghi/đọc SQL Server — "phòng kho" chung
│       ├── connection.py         (178d)   pyodbc connection + kiểm tra contract DB
│       ├── writer.py             (194d)   insert_staging_batch + run_etl_direct (gọi usp_LoadDirect)
│       ├── reader.py             (232d)   đọc bar mới nhất/gap/snapshot Redis
│       ├── validation.py         (136d)   chuẩn hoá/validate OHLCV trước khi ghi
│       ├── maintenance.py        (392d)   purge staging (chỉ sau khi Fact khớp giá trị), reset scope
│       ├── reconcile.py          (280d)   tìm/vá staging chưa tới Fact, tách "bug thật" khỏi "ngoài lịch Dim_Date"
│       └── operation_log.py       (81d)   định dạng log "WAREHOUSE | ..." riêng cho domain này
│
├── util/                                  HẠ TẦNG VẬN HÀNH/GIÁM SÁT — không tự lấy/ghi OHLCV
│   ├── cli.py                    (693d)   toàn bộ subcommand: run/status/doctor/stop/live/historical/auth/...
│   ├── health.py                (1427d)   collect_health() — ~16 check trạng thái toàn hệ thống (đọc-only)
│   ├── runtime_state.py          (122d)   atomic JSON state-file writer dùng chung (tmp+replace+retry)
│   │
│   ├── coordination/
│   │   └── locks.py             (1156d)   advisory lock qua SQL `SEN.ActiveTask` — bắt buộc phải có, 3 tiến trình dùng chung
│   │
│   ├── supervisor/                         "người quản lý ca trực 24/7"
│   │   ├── engine.py             (1660d)   BackendSupervisor — spawn/backoff/restart live+historical
│   │   └── process_control.py     (499d)   stop-file, hàng đợi historical job, nhận diện tiến trình cùng máy
│   │
│   ├── logkit/                             centralized logging, no domain knowledge
│   │   ├── __init__.py                     public API only
│   │   ├── bootstrap.py                    early crash capture and recovery
│   │   ├── core.py                         logger routing, levels and correlation context
│   │   ├── formatter.py                    operator columns, JSON schema and redaction
│   │   ├── query.py                        status/watch/find/trace/risk review
│   │   └── sink.py                         queue, cross-process append, rotation and retention
│   │
│   ├── notify/                             kênh cảnh báo ra ngoài (Discord)
│   │   ├── discord.py            (1468d)  gửi Discord bất đồng bộ (mọi alert thường), dịch ngôn ngữ kỹ thuật→vận hành viên
│   │   ├── critical_outbox.py     (513d)   gửi CRITICAL đồng bộ, có SQLite outbox bền vững, retry
│   │   └── transport.py            (57d)   POST webhook đồng bộ dùng chung cho cả 2 module trên (mới)
│   │
│   ├── redis_io/
│   │   └── candle_snapshot.py     (391d)   handoff candle mới nhất qua Redis cho OG — best-effort, không chặn ghi SQL
│   │
│   └── dashboard/                          viewer nội bộ, chỉ đọc
│       ├── server.py              (236d)   HTTP server cục bộ cho Chart & Data Health
│       ├── chart_queries.py       (130d)   query SQL đọc-only cho biểu đồ giá
│       └── health_queries.py      (277d)   query SQL đọc-only cho data-health viewer
│
├── other/                                  tiện ích 1 dòng, không trạng thái, dùng bởi cả core lẫn util
│   ├── tls.py                      (33d)   kích hoạt trust-store hệ điều hành cho HTTPS
│   └── exit_codes.py               (14d)   mã thoát process dùng chung (OK/ERROR/LOCK_CONFLICT/CANCELLED)
│
└── settings/                               NỀN CẤU HÌNH TOÀN HỆ THỐNG — đứng ngoài 4 nhóm trên
    ├── operational.py             (452d)   đọc config/dp_provider.env, định kiểu — LIVE/HISTORICAL/DB/BACKEND/...
    ├── instruments.py             (122d)   danh sách 37 symbol cố định (nguồn sự thật duy nhất)
    └── system.py                   (93d)   bảng cố định: timeframe/interval map, staging table name, N_BARS mặc định
```

---

## Mô tả theo nhóm

### `core/live/` — tiến trình 24/7

Ẩn dụ: "đội đứng canh nhận tin liên tục", mỗi 5 phút nhận giá mới từ TradingView và ghi ngay vào kho, không được rời vị trí.

| File | Vai trò |
|---|---|
| `engine.py` | Composition root — khởi động, auth preflight, vòng đời tắt máy, gắn tất cả các mảnh lại với nhau |
| `scheduler.py` | Chạy 1 batch ngay khi khởi động, sau đó căn đúng boundary đồng hồ (đầu mỗi 5 phút) |
| `fetcher.py` | 1 thread bền vững cho mỗi nhóm kết nối WebSocket TradingView; tự phát hiện timeout → force-close socket → nếu vẫn kẹt 3 batch liên tiếp → tự thoát process để supervisor cấp phát instance mới |
| `delivery.py` | 2 worker tách biệt: ghi staging (nhanh) và nạp Fact (`usp_LoadDirect`, có thể trễ) — không worker nào chặn worker kia |
| `outbox.py` | Outbox SQLite ghi-trước-khi-gửi: mọi nến qua đây trước khi vào hàng đợi RAM, đảm bảo không mất dữ liệu khi crash bất kỳ lúc nào |
| `runtime.py` | Toàn bộ state/lock/counter dùng chung của live, gồm state machine "cặp symbol/timeframe bị thiếu" hợp nhất |
| `telemetry.py` | Định dạng report theo lô/nhóm kết nối, phân loại mức độ nghiêm trọng (CRITICAL/WARNING) riêng của live |

### `core/historical/` — job theo lịch

Ẩn dụ: "đội đi kiểm kê và vá hồ sơ cũ", chạy 2 lần/ngày (11:00, 22:00 UTC), không quan tâm "vừa xảy ra" mà quan tâm "chỗ nào đang thiếu".

| File | Vai trò |
|---|---|
| `engine.py` | Entry CLI, giữ khoá, xác định mode (`full`/`gap`/`reset`) |
| `pipeline.py` | Chạy thật: fetch→validate→stage→ETL, 3 mode runner |
| `runtime_support.py` | Phân biệt "lỗ hổng dữ liệu thật" với "thị trường đóng cửa bình thường" |
| `reporter.py` | Log/report riêng cho historical (bảng PAIR FLOW, SCAN SUMMARY...) |

### `shared/` — dùng chung cho cả 2 core engine

`tradingview/` = cổng vào lấy dữ liệu (giao thức WS + xác thực 8 lớp fallback). `warehouse/` = đích ghi/đọc SQL Server, kể cả cơ chế reconcile phân biệt bug thật với dữ liệu ngoài lịch `Dim_Date`. `freshness.py`/`time.py` = 2 công thức/helper nhỏ từng bị viết trùng ở cả live và historical, nay gộp lại một chỗ duy nhất.

### `util/` — vận hành, giám sát, không tự lấy/ghi OHLCV

- `coordination/locks.py`: 1 bảng SQL (`SEN.ActiveTask`) làm "bảng đăng ký giữ chỗ chung" cho 3 tiến trình độc lập (supervisor/live/historical) — bắt buộc phải có, không thể thay bằng OS mutex vì chạy trên nhiều tiến trình khác nhau.
- `supervisor/`: người quản lý ca trực — quyết định khi nào restart, phân biệt crash/lock-conflict/operator-stop, có backoff tăng dần có trần.
- `logkit/` + `notify/`: logging owns four canonical files, structured
  formatting, querying, rotation and retention; notify owns the durable
  CRITICAL outbox and asynchronous Discord delivery.
- `health.py`: người quan sát trung lập, chỉ đọc trạng thái toàn hệ thống (không tự sửa gì) — dùng bởi cả CLI (`doctor`/`status`) lẫn chính supervisor để quyết định restart.
- `redis_io/`, `dashboard/`: 2 nhánh ngoại vi tuỳ chọn — Redis handoff cho OG (best-effort, không chặn đường ghi SQL chính), dashboard chỉ đọc để xem biểu đồ/health, không được supervisor gọi.

### `other/` và `settings/`

`other/` chỉ 2 file tiện ích không trạng thái (TLS trust-store, exit code) — dùng bởi cả core lẫn util nên không thuộc riêng nhóm nào. `settings/` là nền cấu hình duy nhất cho toàn hệ thống: `instruments.py` định nghĩa cứng 37 symbol, `system.py` là bảng miền cố định (timeframe/staging table), `operational.py` đọc `config/dp_provider.env` và định kiểu mọi giá trị vận hành operator có thể chỉnh.
