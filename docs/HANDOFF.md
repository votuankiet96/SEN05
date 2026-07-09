# HANDOFF — OG (Order Generation) / redis_engine

**Ngày viết:** 2026-07-09
**Người viết:** Claude (Claude Code), theo yêu cầu chủ dự án, để bàn giao cho một AI coding assistant khác (Codex) tiếp tục làm việc.
**Mục đích tài liệu:** Mô tả khách quan, đầy đủ, chính xác hiện trạng thật của chương trình `og_program` tại thời điểm viết — bao gồm những gì đã xây, đã kiểm chứng thật (không phải chỉ viết code xong là coi như xong), những gì CHƯA làm, và lý do các quyết định thiết kế được chọn (để không lặp lại các phương án đã bị từ chối). Tài liệu này ưu tiên **sự thật có thể kiểm chứng lại được** (đường dẫn file, tên hàm, lệnh đã chạy, kết quả đo được) hơn là mô tả chung chung.

---

## 0. Bối cảnh hệ thống lớn hơn (ngoài phạm vi repo này)

`og_program` không chạy độc lập — nó là một khâu trong một hệ thống 3 phần, chạy trên các máy khác nhau:

```
┌──────────────────────┐        ┌──────────────────────┐        ┌──────────────────────┐
│   DP6 (10.11.12.6)   │        │   vm-og / vm-og8      │        │   "OF" (chưa tồn tại) │
│   Windows Server      │        │   (10.11.12.8)        │        │                       │
│                        │        │   Linux (Ubuntu)      │        │                       │
│  - SQL Server          │──SQL──▶│                       │        │                       │
│    (SEN05_AutoTrading) │        │  og_program (repo này)│        │  Hệ downstream sẽ đọc │
│  - dp_program/         │──Redis─▶│   ├─ og_core          │──Redis▶│  tín hiệu OG publish  │
│    live_fetching.py    │  Stream │   └─ redis_engine     │ Stream │  ra (chưa xây)        │
│    (ingest 24/7 giá    │        │                       │        │                       │
│     từ Capital.com)    │        │  + Redis server chạy   │        │                       │
│                        │        │    ngay trên máy này   │        │                       │
└──────────────────────┘        └──────────────────────┘        └──────────────────────┘
```

- **DP6**: máy Windows riêng, KHÔNG thuộc repo này. Chạy `dp_program` (đội DP6 tự phát triển, tự deploy). Có SQL Server lưu dữ liệu OHLCV thật (bảng `DWH.Fact_OHLCV`), và có script `live_fetching.py` chạy 24/7 nhận giá từ Capital.com, ghi vào SQL Server, đồng thời (từ 2026-07-09) đẩy dữ liệu lên Redis qua stream `candle_snapshot` (chi tiết ở mục 5). **Claude/Codex không có quyền sửa code DP6** — chỉ có quyền đọc qua CIFS mount `/mnt/sen05/dp_program` (nếu còn mount) để tham khảo, và viết tài liệu đề xuất cho đội DP6 tự triển khai.
- **vm-og / vm-og8 (10.11.12.8)**: máy Linux này — nơi repo `og_program` (tài liệu này) đang chạy. Cũng là nơi Redis server (`redis-server`, gói hệ thống, không phải code Python) đang chạy.
- **"OF"**: hệ thống downstream sẽ đọc tín hiệu OG publish ra để hành động tiếp (đặt lệnh giao dịch?). **Chưa được xây dựng** — nằm ngoài phạm vi hiện tại, chỉ là 1 stream Redis đang có sẵn dữ liệu (`signal_stream:combo`) chờ có consumer đọc.
- Máy host cá nhân của chủ dự án (10.11.12.5) nằm cùng LAN, dùng để SSH/RDP vào `vm-og8` và xem dữ liệu Redis bằng GUI (Redis Desktop Manager) — chỉ là công cụ quan sát, không phải một phần của hệ thống.

**Vì sao cần biết điều này:** `og_program` có 2 con đường lấy dữ liệu độc lập nhau (SQL Server trực tiếp, và Redis Stream do DP6 đẩy lên) — không phải 1 thay thế cho cái kia, mà là 2 lớp chạy song song có chủ đích (xem mục 4.3).

---

## 1. Repo này gồm 2 package độc lập, khác mục đích

```
og_program/
├── pyproject.toml          # package "og-core" (tên package, không đổi được lịch sử)
├── .env / .env.example     # SQL_*, REDIS_* — dùng chung bởi cả 2 package
├── CONTRACTS.md            # hợp đồng ngoài của og_core (SQL Server) — ĐỌC TRƯỚC khi sửa og_core/data
├── README.md               # tổng quan og_core
├── deploy/                 # systemd unit files + hướng dẫn cài (xem mục 8)
├── docs/                   # ← chính là file này
├── tests/                  # pytest cho og_core (indicators, strategies) + redis_engine (signal_id)
└── src/
    ├── og_core/            # PACKAGE 1 — dashboard + tính chiến lược từ SQL Server (ỔN ĐỊNH, không đổi hôm nay)
    └── redis_engine/       # PACKAGE 2 — lớp tín hiệu realtime qua Redis (ĐANG PHÁT TRIỂN, đổi nhiều hôm nay)
```

**Quan hệ giữa 2 package**: `redis_engine` PHỤ THUỘC MỘT CHIỀU vào `og_core` (import `og_core.engine`, `og_core.strategies.registry`, `og_core.data.loader`) để tái sử dụng logic tính chỉ báo/tín hiệu — **không viết lại logic chiến lược trong `redis_engine`**. `og_core` không biết gì về `redis_engine` (không import ngược lại). `redis_engine` là dependency **optional** (`pip install -e ".[watcher]"`, xem `pyproject.toml` — extra `watcher = ["redis"]`), không bắt buộc để `og_core` (dashboard) chạy được.

---

## 2. Package 1 — `og_core` (dashboard, ổn định)

**Vai trò**: đọc OHLCV từ SQL Server trên DP6, tính chỉ báo kỹ thuật, phát hiện tín hiệu theo 4 chiến lược, hiển thị qua dashboard Flask + xuất CSV. Đây là phần **gốc, đã ổn định, KHÔNG bị thay đổi gì trong các phiên làm việc gần đây** (kể cả hôm nay) — an toàn để Codex đọc hiểu nhưng nên hỏi lại chủ dự án trước khi sửa, vì đây là phần đang chạy production thật (dashboard + gunicorn, xem mục 8).

```
src/og_core/
├── config.py            # SQL_* từ env; SYMBOLS (37 symbol, symbol_id khớp DWH.Dim_Symbol);
│                         # TF_MINUTES/TF_DISPLAY_ORDER (15 khung thời gian); get_symbol()
├── engine.py            # run_strategy_request() — orchestration DÙNG CHUNG cho dashboard + export
│                         # + redis_engine (qua compute.run_watched_item, xem mục 4).
│                         # Tự chọn nhánh: single-timeframe / combo MTF (HTF_TREND_ENABLED) /
│                         # ai_trend (2 khung thời gian) / knn_combo (2 khung thời gian).
├── logging_setup.py     # setup_logger(filename) — RotatingFileHandler dùng chung bởi CẢ og_core
│                         # VÀ redis_engine, ghi vào runtime/logs/{tên file}.log ở gốc repo.
├── main.py              # entrypoint CLI: python -m og_core.main [--port] [--debug]
├── data/
│   ├── db_connector.py  # get_connection() — pyodbc, đọc SQL_* từ config.py
│   └── loader.py        # load(symbol, tf, n_bars) / load_range(...) / load_bounds(...)
│                         # Hợp đồng đầu ra CỐ ĐỊNH: DataFrame [bartime, open, high, low, close,
│                         # volume], bartime UTC-naive, sắp tăng dần. ĐÂY LÀ HỢP ĐỒNG QUAN TRỌNG
│                         # NHẤT của repo — redis_engine/triggers/candle_store.py tạo ra DataFrame
│                         # CÙNG SCHEMA này từ nguồn khác (Redis) để logic chiến lược dùng chung.
├── indicators/          # SMA/EMA/MACD/ATR (core.py), AI Trend Navigator (ai_trend.py), Dow swing
│                         # HH/HL/LH/LL (dow_wave.py) — hàm tính thuần, không I/O.
├── strategies/
│   ├── registry.py      # STRATEGIES: dict[str, StrategySpec] — get_strategy(key). MỖI chiến lược
│   │                     # có đúng 4 hàm: normalize_params, add_indicators, detect_signals,
│   │                     # add_levels — GỌI THEO ĐÚNG THỨ TỰ NÀY, mỗi bước ăn output bước trước.
│   ├── combo/            # Combo (MA + MACD Histogram + ATR breakout) — CHIẾN LƯỢC DUY NHẤT đang
│   │                     # được redis_engine dùng (xem mục 4.3, ai_trend/knn_combo bị chặn có chủ đích)
│   │                     # HTF_TREND_ENABLED mặc định False (config.py dòng 38) — nếu bật, cần
│   │                     # dữ liệu Higher-Timeframe riêng, redis_engine.compute.run_from_bars() sẽ
│   │                     # raise ValueError nếu gặp trường hợp này (không tính sai âm thầm).
│   ├── ma_cross/         # MA Cross — single-timeframe, chưa được redis_engine dùng tới.
│   ├── ai_trend/         # AI Trend — CẦN 2 khung thời gian (trend + entry). Bị chặn ở
│   │                     # redis_engine.compute.MULTI_TIMEFRAME_STRATEGIES.
│   └── knn_combo/        # KNN Combo — CẦN 2 khung thời gian, cùng lý do bị chặn như ai_trend.
├── chart/                # Flask app (server.py: create_app(), routes: /health,
│                         # /lightweight-charts, /api/config, /api/scan, /api/export,
│                         # /api/export/bulk, /api/data-range) + frontend Lightweight Charts
│                         # (static/index.html, app.js, styles.css)
└── export/               # service.py + to_csv.py — xuất CSV cho /api/export*
```

**Hợp đồng ngoài của `og_core`** (xem `CONTRACTS.md` để biết đầy đủ): chỉ **đọc** (SELECT) từ `SEN05_AutoTrading.DWH.Fact_OHLCV` trên SQL Server DP6 (10.11.12.6). Trên Linux (`vm-og8`), **PHẢI dùng SQL Authentication** (`SQL_UID`/`SQL_PWD` thật trong `.env`, `SQL_DRIVER=freetds`) — Windows Integrated Auth (để trống UID/PWD) **không hoạt động** trên máy Linux không join domain (đã kiểm chứng thật 2026-07-07, xem `CONTRACTS.md` mục 2).

**Điều KHÔNG còn đúng nữa (đã bị xoá khỏi `og_core`, xem `CONTRACTS.md` mục 3)**: trước đây có 1 module `notify/` bắn tín hiệu qua Redis pub/sub cho hệ "OF" — đã bị gỡ hoàn toàn trong 1 lần refactor trước (không phải hôm nay). `redis_engine` (mục 4) là một hệ THIẾT KẾ LẠI HOÀN TOÀN từ đầu cho đúng vai trò đó, KHÔNG phải khôi phục code cũ — không có liên hệ code nào giữa 2 thứ.

---

## 3. Đang chạy thật trên máy này (tại thời điểm viết tài liệu)

Để Codex không giả định sai — đây là các tiến trình **thật đang chạy** trên `vm-og8`, ngoài phạm vi Python của repo:

- `redis-server` (gói hệ thống, không phải code trong repo) — lắng nghe `10.11.12.8:6379` VÀ `127.0.0.1`, yêu cầu xác thực (user `default` + password trong `.env`, khớp `REDIS_PASSWORD`).
- `sshd` — cổng 22, phục vụ SSH từ máy host.
- `xrdp` — cổng 3389, phục vụ Remote Desktop (Windows RDP client) — chỉ để truy cập desktop GUI khi cần, không liên quan tới chức năng của repo.
- `python -m redis_engine.main` — chạy bằng `nohup` THỦ CÔNG (không phải systemd — xem mục 8, đây là lỗ hổng chưa vá), khởi động lại lần cuối lúc 13:30 ngày 2026-07-09 SAU KHI đổi cấu trúc file hôm nay. **Nếu tiến trình này không còn chạy khi Codex đọc tài liệu này, đó là vì máy đã reboot hoặc tiến trình crash và KHÔNG có ai/gì tự khởi động lại nó** — đây chính xác là vấn đề mục 8 mô tả.
- `og_core` dashboard: có thể ĐANG hoặc KHÔNG đang chạy qua gunicorn tuỳ thời điểm — xem `deploy/README.md`.

Lệnh kiểm tra nhanh (không cần sudo):
```bash
ps aux | grep "[r]edis_engine.main"
tail -30 /home/administrator/Desktop/og_program/runtime/logs/redis_engine.log
```

---

## 4. Package 2 — `redis_engine` (lớp tín hiệu realtime, ĐANG PHÁT TRIỂN)

### 4.1. Vai trò và lý do tồn tại

Mục tiêu: khi DP6 có nến mới/sửa nến cũ, OG phải **phản ứng gần như ngay lập tức** (không phải chờ người dùng bấm dashboard), tự tính lại tín hiệu chiến lược, và publish tín hiệu mới lên Redis cho hệ "OF" đọc — đồng thời hệ thống phải chạy 24/7, chịu được restart/crash/mất kết nối Redis tạm thời mà KHÔNG được bỏ lọt tín hiệu.

### 4.2. Lịch sử thiết kế — ĐÃ THAY ĐỔI 3 LẦN, chỉ bản v3 (hiện tại) là đúng

**Quan trọng cho Codex: nếu thấy tài liệu/code nào nhắc "bar_ready", đó là bản THIẾT KẾ CŨ đã bị thay thế hoàn toàn — không dùng lại.**

| Bản | Ý tưởng | Vì sao bị thay/từ chối |
|---|---|---|
| v1 (`bar_ready`) | DP6 XADD 1 tin nhỏ báo "có bar mới cho symbol/tf X" (4 field) vào stream `bar_ready`. OG nhận tin rồi TỰ QUAY LẠI query SQL Server của DP6 để lấy dữ liệu giá thật. | Đã build, deploy, chạy thật, đo hiệu năng ổn (pickup ~0.2s). Nhưng chủ dự án quyết định kiến trúc cần thay đổi tận gốc: Redis nên là "xương sống" mang cả dữ liệu, không chỉ là chuông báo. |
| v2 (Hash + Sorted Set) | DP6 duy trì 1 cửa sổ 500 nến trên Redis bằng cấu trúc Hash+SortedSet, chạy SONG SONG với `bar_ready` cũ. | Bị chủ dự án bác ngay sau khi duyệt plan: "chức năng bar_ready đó không còn cần nữa... phải gỡ bỏ ra hoàn toàn chức năng cũ" — không muốn chạy song song 2 cơ chế, muốn đơn giản hơn. |
| **v3 (hiện tại) — `candle_snapshot`** | DP6 gộp "báo tin" + "mang dữ liệu" thành 1 hành động: mỗi khi có bar mới/sửa, DP6 tự query lại 500 bar gần nhất từ SQL Server CỦA CHÍNH DP6 (cùng máy, rẻ), đóng gói JSON, XADD 1 entry DUY NHẤT vào stream Redis `candle_snapshot`. `bar_ready` bị GỠ BỎ HOÀN TOÀN bên DP6 (đã xác nhận qua grep-audit code DP6, không còn ai gọi `publish_bar_ready()`). | **Đây là bản đang chạy thật, đã kiểm chứng (mục 4.6).** |

**Vì sao v3 không cần Redis tự "nhớ"/cập nhật nến cũ (khác v2)**: mỗi lần DP6 gửi là gửi lại TOÀN BỘ 500 nến đúng nhất tính đến thời điểm đó — nến nào bị sửa lại (DP6 có thể MERGE/update lại bar cũ) tự động đúng ngay trong lần gửi kế tiếp, không cần bất kỳ logic "cập nhật" nào ở phía Redis hay OG.

**Quyết định thiết kế đã cân nhắc và từ chối, để không đề xuất lại**: DP6 gửi tick/giá thô liên tục qua Redis để OG tránh query SQL — bị từ chối vì OG vẫn cần cả cửa sổ N-bar warmup (không chỉ 1 giá mới), làm vậy chỉ dời độ phức tạp từ "query SQL" sang "tự dựng lại cache OHLCV đồng bộ với SQL" — không có lợi rõ ràng.

### 4.3. Kiến trúc runtime — 2 luồng trigger chạy SONG SONG, không phải failover

```
                              ┌─────────────────────────────────────────┐
                              │        redis_engine.main (1 process)      │
                              │                                            │
   Redis Stream               │  Thread 1: triggers/                      │
   "candle_snapshot" ────────▶│    candle_snapshot_consumer.py            │
   (DP6 XADD, 500 nến/lần)     │    (XREADGROUP BLOCK, gần tức thời)       │──┐
                              │                                            │  │
   SQL Server (DP6, mỗi 300s) │  Thread 2: triggers/                      │  │  Cùng hội tụ vào
   ──────────────────────────▶│    safety_net_poller.py                   │──┤  1 đường dedup +
   (query lại toàn bộ WATCHED)│    (tự quét định kỳ, ĐỘC LẬP hoàn toàn     │  │  publish chung
                              │     với Redis còn sống hay không)          │  │  (delivery/)
                              │                                            │  │
                              │  compute.py (DÙNG CHUNG bởi cả 2 thread)   │◀─┘
                              │                                            │
                              │  delivery/ (DÙNG CHUNG bởi cả 2 thread)    │
                              │    state.py    — dedup theo signal_id      │
                              │    outbox.py   — retry khi publish lỗi     │
                              │    redis_client.py — XADD signal_stream    │
                              │    signal_id.py — hash xác định tín hiệu   │
                              └─────────────────────────────────────────┘
                                              │
                                              ▼
                              Redis Stream "signal_stream:{strategy}"
                              (OG → hệ "OF", CHƯA có consumer nào đọc)
```

**Nguyên tắc kiến trúc — PHẢI hiểu đúng, đây không phải "dò lỗi rồi chuyển đổi"**:
- Cả 2 thread chạy CÙNG LÚC, MỌI LÚC, không có bước nào tự dò "Redis có sống không" để quyết định dùng luồng nào. Không có cờ chuyển đổi.
- Thread 1 (nhanh) chỉ phản ứng khi CÓ entry mới trên `candle_snapshot`. Nếu Redis chết, thread này tự nhiên không nhận được gì — KHÔNG lỗi, KHÔNG crash, chỉ đơn giản là im lặng.
- Thread 2 (an toàn) chạy đều mỗi `SAFETY_NET_INTERVAL_SECONDS` (300s = 5 phút) BẤT KỂ Redis sống hay chết, vì nó tự query SQL Server trực tiếp — không phụ thuộc Redis.
- Vì cả 2 thread CỐ Ý tính trùng nhau (đây là lớp dự phòng, không phải chia việc), buộc phải có `delivery/state.py` để tránh publish 2 lần cùng 1 tín hiệu — xem mục 4.5.
- **Nếu sau này có ai đề xuất "tắt safety_net khi Redis đang sống" để tối ưu — ĐÓ LÀ SAI, phá vỡ đúng nguyên tắc dự phòng này.** Đã giải thích rõ cho chủ dự án và được xác nhận đây là thiết kế có chủ đích.

### 4.4. Cấu trúc file — MỚI SẮP XẾP LẠI HÔM NAY (2026-07-09), theo 2 nhóm chức năng

```
src/redis_engine/
├── __init__.py           # docstring package — vai trò + ranh giới phụ thuộc og_core
├── config.py             # TẤT CẢ hằng số cấu hình — xem mục 4.7 để biết giá trị hiện tại
├── main.py                # Entrypoint: `python -m redis_engine.main` (chạy 24/7) hoặc
│                          # `python -m redis_engine.main --once` (1 vòng safety-net rồi thoát,
│                          # dùng để test thủ công không cần chờ Redis). Khởi 2 thread daemon,
│                          # main() trả về exit code 1 nếu 1 trong 2 thread chết KHÔNG tự phục
│                          # hồi được — để systemd Restart=on-failure khởi động lại toàn tiến
│                          # trình (xem mục 8, service chưa cài).
├── compute.py             # CẦU NỐI DUY NHẤT sang og_core — không viết lại logic chiến lược.
│                          # run_watched_item(item) — dùng bởi safety_net_poller, TỰ QUERY SQL
│                          #   qua og_core.engine.run_strategy_request().
│                          # run_from_bars(strategy, symbol, tf, raw: pd.DataFrame) — dùng bởi
│                          #   candle_snapshot_consumer, NHẬN THẲNG DataFrame đã có sẵn (không
│                          #   query gì thêm), gọi trực tiếp 4 hàm của StrategySpec
│                          #   (normalize_params/add_indicators/detect_signals/add_levels).
│                          #   RAISE ValueError nếu strategy thuộc MULTI_TIMEFRAME_STRATEGIES
│                          #   ({"ai_trend","knn_combo"}) hoặc Combo có HTF_TREND_ENABLED=True —
│                          #   2 trường hợp này cần >1 khung thời gian, candle_snapshot chỉ
│                          #   mang 1 khung thời gian/lần gửi.
│                          # Cả 2 hàm dùng chung _build_payload()/_num() — payload tính từ 2
│                          #   nguồn PHẢI giống hệt nhau cho cùng 1 bar, để dedup ở state.py
│                          #   hoạt động đúng (đã kiểm chứng thật, xem mục 4.6).
│
├── triggers/              # NHÓM: khi nào tính tín hiệu + lấy dữ liệu vào
│   ├── __init__.py
│   ├── candle_snapshot_consumer.py   # Trigger NHANH. XREADGROUP BLOCK=5000ms trên stream
│   │                                  # "candle_snapshot", consumer group "og_watchers". Với
│   │                                  # mỗi entry: snapshot_symbol_tf() lấy (symbol, tf) →
│   │                                  # lọc theo config.WATCHED → parse_snapshot_entry() ra
│   │                                  # DataFrame → compute.run_from_bars() → publish nếu
│   │                                  # check_and_mark() cho phép. XACK NGAY sau khi xử lý
│   │                                  # (dù publish lỗi hay không) — lỗi publish đã có
│   │                                  # outbox.py xử lý riêng, không cần giữ lại entry để retry
│   │                                  # qua Streams. run() tự phục hồi vô hạn lần khi lỗi tạm
│   │                                  # thời (Redis down lúc khởi động, mất kết nối giữa
│   │                                  # chừng...) — không bao giờ để thread chết vì lỗi tạm.
│   │                                  # (ĐỔI TÊN hôm nay từ bar_ready_consumer.py — file CŨ đã
│   │                                  # xoá, không còn tồn tại.)
│   ├── candle_store.py    # HÀM THUẦN, không I/O (không gọi Redis/SQL) — parse 1 entry
│   │                       # candle_snapshot (dict field đã decode) thành DataFrame CÙNG
│   │                       # SCHEMA với og_core.data.loader.load(): [bartime, open, high, low,
│   │                       # close, volume], sắp tăng dần, bartime datetime64, OHLCV float64.
│   │                       #   parse_snapshot_entry(fields) -> pd.DataFrame
│   │                       #   snapshot_symbol_tf(fields) -> (tv_symbol, tf_code)
│   │                       # Raise MalformedSnapshotError (ValueError con) nếu thiếu field
│   │                       # "bars"/"tv_symbol"/"tf_code", JSON hỏng, rỗng, hoặc thiếu cột OHLC
│   │                       # bắt buộc. (File MỚI VIẾT hôm nay — Phase 2.)
│   └── safety_net_poller.py   # Trigger AN TOÀN. run_once(): quét toàn bộ config.WATCHED,
│                                # gọi compute.run_watched_item() (tự query SQL), publish tín
│                                # hiệu mới qua đúng cùng state.check_and_mark(), rồi thử
│                                # outbox.retry_all() (gửi lại tín hiệu publish lỗi lần trước).
│                                # run(): lặp vô hạn mỗi SAFETY_NET_INTERVAL_SECONDS.
│                                # KHÔNG SỬA GÌ về logic hôm nay — chỉ đổi đường import khi dời
│                                # file vào triggers/.
│
└── delivery/               # NHÓM: đẩy tín hiệu ra + đảm bảo không mất/không trùng
    ├── __init__.py
    ├── redis_client.py     # get_client() — lazy singleton, decode_responses=True,
    │                        # socket_timeout=15 (PHẢI lớn hơn rõ rệt BLOCK_MS=5000 của
    │                        # candle_snapshot_consumer — nếu không, client tự timeout đua với
    │                        # BLOCK phía server, gây warning giả). ensure_consumer_group() —
    │                        # tạo group "og_watchers" trên CANDLE_SNAPSHOT_STREAM, id="$"
    │                        # (chỉ thấy entry mới TỪ LÚC group được tạo, không đọc lại lịch sử
    │                        # cũ — nghĩa là restart tiến trình KHÔNG tự "bắt kịp" các entry đã
    │                        # trôi qua trong lúc tắt, safety_net_poller là lớp che việc này).
    │                        # publish_signal() — KHÔNG BAO GIỜ raise ra ngoài, lỗi Redis bị
    │                        # nuốt + log warning + trả None, caller tự quyết định ghi outbox.
    ├── signal_id.py         # build_signal_id(strategy, symbol, tf, bar_time, direction) ->
    │                        # str — hash SHA-256 rút gọn (24 hex), XÁC ĐỊNH (không phải
    │                        # random/timestamp) — cùng input luôn ra cùng signal_id, cho phép
    │                        # dedup đúng dù tính lại bao nhiêu lần từ nguồn nào.
    ├── state.py             # class SignalState — check_and_mark(signal_id) -> bool: kiểm tra
    │                        # + đánh dấu "đã thấy" NGUYÊN TỬ trong 1 lần khoá (Lock). Trả True
    │                        # = CHƯA từng thấy (nên publish), False = đã thấy (bỏ qua). Đây là
    │                        # thứ khiến việc chạy 2 thread trigger song song AN TOÀN, không bị
    │                        # publish trùng — KHÔNG PHẢI bản thân nó là 1 lớp "backup".
    │                        # Lưu JSON tại runtime/state.json, TTL 14 ngày (dọn entry cũ khi
    │                        # load lại). File JSON local — Redis KHÔNG lưu trạng thái này.
    └── outbox.py            # class DeliveryOutbox — add_pending()/retry_all(publish_fn).
                               # Bảo vệ đường publish tín hiệu OG → OF khỏi mất tín hiệu khi
                               # Redis lỗi ĐÚNG LÚC XADD (state.py đã đánh dấu "đã thấy" TRƯỚC
                               # khi publish — nếu publish lỗi mà không có outbox, tín hiệu đó
                               # sẽ vĩnh viễn không publish lại được vì dedup đã chặn). Lưu JSON
                               # tại runtime/delivery_outbox.json.
                               # retry_all() chỉ XOÁ ĐÚNG các item vừa gửi thành công (không gán
                               # đè toàn bộ list) — tránh mất item mới bị thêm vào SONG SONG lúc
                               # retry đang chạy (đã có bug thật kiểu này, đã sửa).

runtime/                   # state.json, delivery_outbox.json — dữ liệu vận hành, GITIGNORED,
                            # không phải code. runtime_dir() trong config.py tự tạo nếu chưa có.
```

### 4.5. Vì sao cấu trúc file đổi hôm nay (bối cảnh cho Codex, tránh hiểu nhầm là 2 phiên bản khác nhau)

Trước hôm nay, toàn bộ 10 file `.py` nằm PHẲNG trực tiếp trong `src/redis_engine/` (không có `triggers/`/`delivery/`). Chủ dự án yêu cầu sắp xếp lại theo nhóm chức năng để dễ hiểu — đã thực hiện bằng `mv` (không phải `git mv`, vì các file này CHƯA từng được commit vào git — kiểm tra bằng `git ls-files src/redis_engine/` chỉ thấy đúng 1 file `__init__.py` được track, còn lại đều untracked). Không có lịch sử git bị mất.

### 4.6. Đã kiểm chứng THẬT — không phải chỉ code xong

Tất cả các mục dưới đây đã **chạy thật, đo được, không phải suy diễn**, thực hiện ngày 2026-07-09:

1. **Parity test (tính từ Redis vs tính từ SQL phải khớp)**: với `US30/H1`, tín hiệu tính từ `compute.run_from_bars()` (nguồn: `candle_snapshot`, 500 nến) cho ra 45 tín hiệu; tính từ `compute.run_watched_item()` (nguồn: SQL, 300 nến) cho ra 27 tín hiệu — **toàn bộ 27 tín hiệu từ SQL đều khớp CHÍNH XÁC signal_id với 1 trong 45 tín hiệu từ Redis** (phần dư 18 tín hiệu bên Redis chỉ là do nhìn xa hơn về quá khứ nhờ có 500 nến thay vì 300, KHÔNG phải sai lệch). Tương tự với `DE40/H1`: 22/22 khớp trong tổng 44.
2. **Consumer group hoạt động thật, không lag**: `XINFO GROUPS candle_snapshot` cho `lag=0`, `pending=0`, `entries-read` tăng liên tục theo thời gian thực khi DP6 tiếp tục gửi dữ liệu — nghĩa là consumer đang thật sự theo kịp luồng dữ liệu, không bị nghẽn.
3. **Mô phỏng 1 lượt DP6 gửi tin thật vào tiến trình ĐANG SỐNG**: lấy 1 entry `US30/H1` thật, XADD lại như "tin mới", quan sát `last-delivered-id` của consumer group khớp đúng ID vừa tạo, `pending=0` sau đó (XACK thành công), không có exception trong log. Xác nhận `signal_id` liên quan đã có trong `state.json` (117 tín hiệu, khớp đúng `XLEN signal_stream:combo = 117`) — dedup đúng, không publish trùng.
4. **`ruff check src/ tests/`**: sạch, không lỗi. `vulture` (dò dead code): không phát hiện file/hàm thừa nào trong `redis_engine` (chỉ có false-positive ở route Flask của `og_core`, không liên quan).
5. **`tests/test_redis_engine.py`** (5 test, chỉ test `signal_id.build_signal_id`): PASS.

### 4.7. Cấu hình hiện tại (`config.py`) — giá trị thật, không phải ví dụ

```python
REDIS_HOST / REDIS_PORT / REDIS_USERNAME / REDIS_PASSWORD  # từ .env
CANDLE_SNAPSHOT_STREAM = "candle_snapshot"
CONSUMER_GROUP = "og_watchers"
CONSUMER_NAME = "og-primary"
SIGNAL_STREAM_PREFIX = "signal_stream"      # → signal_stream_key("combo") = "signal_stream:combo"
SIGNAL_STREAM_MAXLEN = 10_000               # trim gần đúng, tránh phình vô hạn
SAFETY_NET_INTERVAL_SECONDS = 300           # 5 phút

WATCHED = [
    {"strategy": "combo", "symbols": ["US30", "DE40"], "tf": "H1", "bars": 300},
]
```

**`WATCHED` hiện chỉ có 1 mục — Combo, 2 symbol, H1.** Mở rộng phạm vi (thêm symbol/tf/chiến lược) chỉ cần sửa list này, KHÔNG cần đổi gì phía DP6 (DP6 publish `candle_snapshot` cho toàn bộ 11 symbol × 15 khung thời gian họ đang xử lý, bất kể `WATCHED` cần gì — xem mục 5). Nhưng nếu thêm `ai_trend`/`knn_combo`, hoặc bật `HTF_TREND_ENABLED` cho combo, đường trigger nhanh (`run_from_bars`) sẽ RAISE lỗi có chủ đích (xem mục 4.4, `compute.py`) — cần thiết kế thêm nếu muốn hỗ trợ (xem mục 9).

---

## 5. Hợp đồng dữ liệu Redis — CHÍNH XÁC, đã xác nhận bằng dữ liệu thật

### 5.1. Stream `candle_snapshot` — DP6 ghi, OG đọc

Mỗi entry = 1 lần DP6 ghi/sửa xong 1 bar cho 1 (symbol, timeframe). Field (tất cả là string, do `decode_responses=True`):

```
symbol_id   : "7"                      (khớp DWH.Dim_Symbol.SymbolID)
tv_symbol   : "UK100"                  (mã TradingView, khớp og_core.config.SYMBOLS)
tf_code     : "M5"                     (khớp og_core.config.TF_MINUTES)
bars        : "[{\"bar_time\":\"2026-07-07T16:40:00\",\"open\":10665.4,\"high\":10667.9,\"low\":10661.0,\"close\":10663.2,\"volume\":126.0}, ... 500 phần tử, sắp cũ→mới ...]"
```

- `bars` là 1 CHUỖI JSON (không phải mảng Redis) chứa đúng 500 object `{bar_time, open, high, low, close, volume}`, sắp xếp tăng dần theo thời gian.
- MAXLEN trim gần đúng ~2000 entry (phía DP6 cấu hình, không phải OG).
- DP6 publish cho **toàn bộ 11 symbol họ xử lý × tất cả timeframe họ có** — không chỉ riêng những gì `WATCHED` cần. OG (`_matching_items()` trong `candle_snapshot_consumer.py`) tự lọc theo `WATCHED`, bỏ qua thầm lặng (không log) mọi entry không khớp.
- Đã xác nhận thật (2026-07-09) có dữ liệu cho ĐỦ các symbol: BTCUSD, DE40, FR40, GOLD, HK50, J225, SP35, UK100, US100, US30, US500 — với hầu hết các timeframe M5/M10/M15/M20/M30/M45/M90/H1 (một số symbol có thêm H2/H3/H4/H6).

### 5.2. Stream `signal_stream:{strategy}` — OG ghi, hệ "OF" (chưa tồn tại) sẽ đọc

Ví dụ `signal_stream:combo`. Mỗi entry field:

```
signal_id       : hash 24 hex, xác định — dùng để hệ đọc phía sau tự dedup nếu cần
strategy        : "combo"
symbol          : "US30"
timeframe       : "H1"
direction       : "1" (BUY) hoặc "-1" (SELL)
side            : "BUY" / "SELL"
bar_time        : ISO 8601, vd "2026-07-09T00:00:00"
event_close     : giá đóng bar phát tín hiệu (string số, hoặc "" nếu None)
entry_price / sl_price / tp_price / risk_reward / atr   : string số hoặc ""
signal_reason   : text mô tả lý do (từ strategy logic)
produced_at     : ISO 8601 UTC, thời điểm OG tính ra (KHÁC bar_time)
```

MAXLEN 10,000 (gần đúng). **Chưa có consumer group nào trên stream này** — hệ "OF" đọc bằng cách nào (XREAD thường, consumer group riêng...) là quyết định của việc xây "OF" sau này, KHÔNG phải quyết định của `redis_engine`.

### 5.3. Key/stream CŨ còn sót lại trong Redis (rác vô hại, có thể xoá)

- Key `bar_ready` — stream CŨ từ thiết kế v1, DP6 đã ngừng publish, không ai đọc nữa. Còn tồn tại trong Redis (không tự mất) nhưng không ảnh hưởng gì — an toàn để `DEL bar_ready` nếu muốn dọn, không bắt buộc.
- Key `test_key` — vết còn lại từ 1 lần test thủ công trong quá trình phát triển. Vô hại, có thể xoá.

---

## 6. Vận hành — cách chạy, cách kiểm tra

### 6.1. Cài đặt

```bash
cd /home/administrator/Desktop/og_program
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev,watcher]"   # dev: pytest/ruff/vulture; watcher: redis
cp .env.example .env   # rồi điền SQL_*/REDIS_* thật (xem .env hiện có trên máy này)
```

### 6.2. Chạy `redis_engine` (thủ công — CHƯA phải systemd, xem mục 8)

```bash
nohup ./.venv/bin/python -m redis_engine.main > /dev/null 2>&1 &
# LƯU Ý: redirect stdout/stderr về /dev/null, KHÔNG redirect vào file log — logging_setup.py
# đã tự ghi vào runtime/logs/redis_engine.log qua RotatingFileHandler; nếu nohup CŨNG ghi vào
# cùng file đó, log sẽ bị NHÂN ĐÔI mỗi dòng (bug đã gặp thật, đã sửa bằng cách redirect /dev/null).

# Test 1 vòng safety-net rồi thoát, không cần chờ Redis:
./.venv/bin/python -m redis_engine.main --once
```

### 6.3. Kiểm tra trạng thái

```bash
tail -f runtime/logs/redis_engine.log
ps aux | grep "[r]edis_engine.main"

# Redis (cần REDIS_PASSWORD trong .env):
redis-cli -h 10.11.12.8 -p 6379 -a "$REDIS_PASSWORD" --no-auth-warning XLEN candle_snapshot
redis-cli -h 10.11.12.8 -p 6379 -a "$REDIS_PASSWORD" --no-auth-warning XLEN signal_stream:combo
redis-cli -h 10.11.12.8 -p 6379 -a "$REDIS_PASSWORD" --no-auth-warning XINFO GROUPS candle_snapshot
```

### 6.4. Test + lint

```bash
./.venv/bin/ruff check src/ tests/
./.venv/bin/vulture src/ tests/ --min-confidence 70
./.venv/bin/pytest tests/test_redis_engine.py -q      # PASS (5/5)
./.venv/bin/pytest tests/ -q                           # XEM MỤC 7.3 — 2 file khác đang lỗi, KHÔNG
                                                        # liên quan redis_engine
```

---

## 7. CHƯA làm / giới hạn đã biết — đọc kỹ trước khi báo "xong"

### 7.1. `systemd` CHƯA được cài — đây là lỗ hổng thật với yêu cầu "24/7"

`deploy/redis-engine.service` và `deploy/og-dashboard.service` đã CHUẨN BỊ SẴN NỘI DUNG (đọc trực tiếp 2 file này) nhưng **CHƯA cài vào `/etc/systemd/system/`** — vì việc này cần quyền `sudo` mà tài khoản chạy AI coding assistant (Claude, và có thể cả Codex) không có. Hiện `redis_engine` chỉ chạy nhờ ai đó gõ `nohup` thủ công — **không tự khởi động lại khi crash, KHÔNG tự chạy lại khi máy `vm-og8` reboot**. Đây là việc BẮT BUỘC phải làm để thực sự đạt yêu cầu 24/7 của chủ dự án, nhưng cần CHỦ DỰ ÁN (có quyền sudo) tự chạy:

```bash
sudo cp deploy/redis-engine.service /etc/systemd/system/
sudo cp deploy/og-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now redis-engine.service
sudo systemctl enable --now og-dashboard.service
```

### 7.2. Chưa test resilience thật (tắt Redis, xem DP6/safety_net có ảnh hưởng không)

Đã XÁC NHẬN BẰNG LOGIC/THIẾT KẾ rằng `safety_net_poller` độc lập với Redis (mục 4.3), nhưng CHƯA có 1 lần test thật: tắt `redis-server` vài phút trong lúc DP6 vẫn ghi dữ liệu, xác nhận DP6 không bị ảnh hưởng VÀ `safety_net_poller` vẫn publish đúng qua SQL. Nên làm trước khi coi hệ thống là "đã kiểm chứng chịu lỗi".

### 7.3. `tests/test_indicators.py` và `tests/test_strategies.py` đang lỗi — KHÔNG liên quan `redis_engine`, có TRƯỚC hôm nay

```
ModuleNotFoundError: No module named 'tests'
```

khi chạy `pytest tests/` (không lỗi khi chạy riêng `pytest tests/test_redis_engine.py`). Nguyên nhân: 2 file này có `from tests.fixtures import make_ohlcv`, nhưng thiếu `conftest.py` ở gốc repo để pytest coi `tests/` là 1 package import được — dấu vết cho thấy từng có `conftest.py` (thấy qua file `.pyc` mồ côi trong `__pycache__`, đã bị dọn) nhưng bị xoá ở 1 lần refactor TRƯỚC (`git log` cho thấy commit "refactor: package as og-core with src layout, drop sys.path hacks"). Cách sửa khả thi: thêm lại `conftest.py` ở gốc repo với nội dung chèn `sys.path`/`rootdir`, hoặc đổi `tests/fixtures.py` thành import tương đối. **Chưa sửa — không thuộc phạm vi công việc `redis_engine` hôm nay, nêu ra để Codex không nhầm là do thay đổi hôm nay gây ra.**

### 7.4. Hệ "OF" (downstream consumer của `signal_stream:*`) CHƯA TỒN TẠI

`redis_engine` đang publish tín hiệu thật vào `signal_stream:combo` (117 tín hiệu tại thời điểm viết) nhưng **không có gì đọc nó**. Đây không phải lỗi — nằm ngoài phạm vi đã thống nhất cho giai đoạn này.

### 7.5. Chỉ chiến lược Combo được nối vào `redis_engine` (có chủ đích, xem mục 4.7)

`ma_cross` chưa được thêm vào `WATCHED` dù về mặt kỹ thuật có thể (single-timeframe, không bị chặn bởi `MULTI_TIMEFRAME_STRATEGIES`). `ai_trend`/`knn_combo` bị CHẶN CÓ CHỦ ĐÍCH vì cần 2 khung thời gian — muốn hỗ trợ cần thiết kế thêm (xem mục 9).

### 7.6. Chưa giới hạn firewall theo IP cho Redis (port 6379) và SSH (port 22)

Hiện tại (theo hiểu biết tốt nhất, KHÔNG kiểm chứng được trực tiếp vì `ufw status` cần sudo) không có giới hạn ufw riêng cho port 6379 — Redis mở cho toàn mạng LAN nội bộ có thể truy cập (đã xác nhận DP6 VÀ máy host cá nhân của chủ dự án đều kết nối được qua LAN). Việc giới hạn ufw chỉ cho đúng IP DP6 (10.11.12.6) là optional, cần sudo, chưa làm.

---

## 8. Sự cố/bài học thật đã xảy ra — để Codex không lặp lại

1. **`UnboundLocalError` crash production (bên DP6, không phải bên repo này)**: circuit breaker dùng biến module-level, bị reassign trong hàm mà thiếu khai báo `global` → cả `_db_worker` thread chết ngay lần gọi `publish_bar_ready()` đầu tiên khi bật flag thật. Bài học: circuit breaker nên dùng class/object thay vì biến module-level + `global`, để cấu trúc loại bỏ hẳn khả năng lỗi này (đã áp dụng khi DP6 viết lại `candle_snapshot.py`).
2. **TOCTOU race trong dedup** (đã sửa, xem `delivery/state.py`): trước đây `has()` và `add()` là 2 lời gọi tách rời — 2 thread có thể cùng đọc `has()==False` trước khi bên nào kịp `add()`, dẫn tới publish trùng. Sửa bằng `check_and_mark()` — 1 thao tác nguyên tử duy nhất trong 1 lock.
3. **Bug mất item trong `outbox.retry_all()`** (đã sửa): code cũ gán đè toàn bộ `_pending` bằng danh sách "còn lại" tính từ snapshot cũ — nếu có thread khác `add_pending()` một item MỚI đúng lúc `retry_all()` đang chạy, item mới đó bị mất theo phép gán đè. Sửa bằng cách chỉ XOÁ ĐÚNG các item vừa gửi thành công.
4. **Log bị nhân đôi mỗi dòng**: chạy `nohup ... >> log_file 2>&1 &` trong khi `logging_setup.py` CŨNG ghi vào đúng file đó qua `RotatingFileHandler` → mỗi dòng log xuất hiện 2 lần. Sửa bằng cách đổi redirect của `nohup` thành `/dev/null`.
5. **`socket_timeout` đua với `BLOCK_MS`** (đã sửa, xem `delivery/redis_client.py`): `socket_timeout=5` bằng đúng `BLOCK_MS=5000` của consumer, khiến client tự timeout đúng lúc server đang BLOCK bình thường → warning "Timeout reading from socket" giả (không phải mất kết nối thật). Sửa bằng cách nâng `socket_timeout=15`, có margin rõ rệt so với `BLOCK_MS`.

---

## 9. Đề xuất bước tiếp theo (thứ tự ưu tiên gợi ý, KHÔNG bắt buộc theo đúng thứ tự)

1. **Cài `systemd`** (mục 7.1) — cần chủ dự án tự chạy lệnh sudo, đây là việc còn thiếu quan trọng nhất để hệ thống thực sự đạt "24/7 tự phục hồi".
2. **Test resilience thật** (mục 7.2) — tắt Redis vài phút, xác nhận `safety_net_poller` che được hoàn toàn, DP6 không bị ảnh hưởng.
3. Xây hệ "OF" tối thiểu (mục 7.4) — dù chỉ là 1 script đọc `signal_stream:combo` và in ra console, để có bằng chứng đầu-cuối tín hiệu thực sự "tới nơi".
4. Sửa `conftest.py` (mục 7.3) — việc nhỏ, không cấp bách, nhưng chặn `pytest tests/` chạy trọn vẹn.
5. Cân nhắc mở rộng `WATCHED` sang `ma_cross` (kỹ thuật đã sẵn sàng, chỉ cần sửa `config.py`).
6. Nếu cần hỗ trợ `ai_trend`/`knn_combo`/Combo-MTF qua đường nhanh: cần DP6 gửi THÊM 1 entry `candle_snapshot` riêng cho khung thời gian phụ (trend timeframe), và `compute.run_from_bars()` cần thiết kế lại để nhận 2 DataFrame — đây là thay đổi kiến trúc, nên bàn với chủ dự án trước, không tự quyết định.
7. Dọn rác Redis vô hại (mục 5.3) — `DEL bar_ready`, `DEL test_key`. Không cấp bách.
8. Xem xét ufw giới hạn IP cho port 6379/22 (mục 7.6) — cần sudo.

---

## 10. Quy tắc làm việc đã được chủ dự án xác nhận nhiều lần trong quá trình phát triển (nên tiếp tục tuân theo)

- **Từng bước, xin xác nhận trước khi triển khai thay đổi lớn** — chủ dự án nhiều lần yêu cầu rõ "trao đổi từng bước 1, tôi đồng ý thì mới làm".
- **Không sửa code phía DP6 trực tiếp** — chỉ viết tài liệu đề xuất, đội DP6 tự triển khai.
- **Không tự chạy lệnh cần `sudo`** — luôn đưa lệnh chính xác để chủ dự án tự chạy.
- **Không gộp/xoá file khi chưa rõ "bản chất" đúng của nó** — đã có 1 lần chủ dự án yêu cầu rà soát toàn bộ file trước khi cho phép xoá, kết luận là KHÔNG có file thừa (mục 4.6, điểm 4).
- **Ưu tiên kiểm chứng bằng dữ liệu thật** (query Redis/SQL trực tiếp, không chỉ đọc code) trước khi báo cáo trạng thái "đã xong".
