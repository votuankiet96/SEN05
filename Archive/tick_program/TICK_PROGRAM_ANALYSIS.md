# Báo cáo phân tích: Tick Program (SEN05 cTrader FTMO Tick Provider)

## 1. Executive Summary

Tick Program **không phải** là một trình client WebSocket nhận tick realtime liên tục như tên gọi có thể gợi ý. Đây là một **dịch vụ backfill lịch sử định kỳ, chạy 24/7** ("backfill-only"), tự mô tả rõ trong docstring của `tick_engine/backend_engine.py`:

> "The service keeps no realtime WebSocket connection. It owns the singleton process, token refresh, health checks, spool drain, and scheduled overlap historical backfills."

Chương trình định kỳ mở kết nối TCP tới cTrader Open API (protobuf), xác thực application + tài khoản FTMO, gọi API lịch sử `ProtoOAGetTickDataReq` để tải tick BID/ASK theo cửa sổ thời gian có chồng lấn (overlap), ghép BID/ASK thành quote (Bid/Ask/Mid/Spread), rồi ghi vào 11 bảng SQL Server theo symbol. Dữ liệu này phục vụ downstream qua các view SQL (`tick.v_LatestQuote`, `tick.v_<SYM>_Quote`, `tick.v_IngestHealth`) cho hệ thống auto trading SEN05 và một dashboard nội bộ đọc dữ liệu tick (`tick_datacheck`).

Chương trình chạy **độc lập** như một tiến trình Python riêng (`python -m tick_engine service`), giao tiếp với phần còn lại của hệ thống hoàn toàn qua SQL Server (không gọi trực tiếp module trading nào khác trong repo này).

## 2. Program Purpose

- **Mục đích**: tải dữ liệu tick lịch sử (historical BID/ASK) từ cTrader Open API cho 11 symbol (FR40, DE40, HK50, J225, SP35, UK100, US500, US100, US30, GOLD, BTCUSD) và lưu vào SQL Server để phục vụ hệ thống trading tự động SEN05.
- **Nguồn dữ liệu**: cTrader Open API (protobuf/TCP, cổng 5035), qua endpoint `demo.ctraderapi.com` hoặc `live.ctraderapi.com` tuỳ `CTRADER_ENV` (`runtime.py:31-33`). Tài khoản là tài khoản FTMO (được chọn qua `CTRADER_ACCOUNT_ID`/OAuth, không có bằng chứng code cứng buộc phải là "Swing" — xem mục 16).
- **Dữ liệu dùng cho**: bảng SQL `tick.<SYMBOL>` và các view `tick.v_<SYMBOL>_Quote`, `tick.v_LatestQuote` trong database `SEN05_AutoTrading`, dùng làm input giá cho hệ thống auto trading (theo tên database) và cho dashboard xem tick nội bộ (`tick_datacheck`).
- **Độc lập hay tích hợp**: chạy độc lập như một CLI/service Python riêng biệt (`tick_engine`), có thư mục `config/`, `runtime/`, `sql/` riêng. Không import hay gọi trực tiếp bất kỳ module trading nào khác trong workspace hiện tại — điểm nối duy nhất được xác nhận là bảng/view SQL Server dùng chung.

## 3. Architecture Overview

| Thành phần | File | Trách nhiệm |
|---|---|---|
| CLI dispatcher | `tick_engine/__main__.py` | Định tuyến subcommand (service, oauth-login, backfill, check, …) |
| Supervisor 24/7 | `tick_engine/backend_engine.py` (`BackendSupervisor`) | Vòng lặp chính, singleton PID, heartbeat, tiền kiểm, shutdown |
| Scheduler + job runner | `tick_engine/scheduler.py` | Định nghĩa job theo lịch, spawn subprocess, ưu tiên backfill, reap |
| Kết nối/backfill cTrader | `tick_engine/historical_pulling.py` | Toàn bộ logic protobuf: connect, auth, request tick lịch sử, merge BID/ASK |
| Runtime settings + cTrader SDK helper | `tick_engine/utils_support/runtime.py` | `TickRuntimeSettings`, load SDK, tạo request, auth chain |
| OAuth thô | `tick_engine/utils_support/auth.py` | Đổi code lấy token, refresh token, local OAuth callback server |
| Token cache | `tick_engine/utils_support/token_store.py` | Đọc/ghi `runtime/cache/ctrader_ftmo_oauth.json` |
| Khoá tiến trình/DB | `tick_engine/utils_support/lock_coord.py` | File lock (`ctrader-history.lock`), DB advisory lock `SEN.ActiveTask`, cancel sentinel |
| Tiện ích PID | `tick_engine/utils_support/proc_utils.py` | Kiểm tra PID sống, terminate |
| Health check | `tick_engine/utils_support/health.py` | `run_tick_check`, activity profile theo khung giờ |
| Service state | `tick_engine/utils_support/service_state.py` | Heartbeat file, progress file cho batch |
| Lưu trữ SQL | `tick_engine/data_storage/store_sql.py` (`TickSqlStore`) | Insert dedup vào bảng tick, IngestRun/IngestState |
| Spool SQLite | `tick_engine/data_storage/spool.py` | Buffer khi SQL Server không kết nối được |
| Symbol matching | `tick_engine/data_storage/symbols.py` | Ghép symbol nội bộ ↔ symbol cTrader |
| Tick model | `tick_engine/data_storage/ticks.py` | `TickRecord`, giải mã delta tick, quy đổi giá |
| Notification | `tick_engine/reporting/notifications.py`, `notification_policy.py` | Gửi Discord webhook có throttle |
| System log | `tick_engine/reporting/system_log.py` | Ghi `runtime/logs/system.log` |
| Dashboard đọc dữ liệu | `tick_engine/tick_datacheck/server.py` | HTTP server read-only tại :8060 |
| Launcher vận hành | `initial_setup/launcher/tick_launcher.ps1` | Menu PowerShell cho operator |
| Deploy schema | `initial_setup/deploy_schema.py`, `sql/tickdata_setup.sql` | Tạo bảng/view SQL |

**Không có** thành phần "subscribe symbol" hoặc "nhận tick realtime" theo nghĩa WebSocket streaming — toàn bộ tick được lấy qua API lịch sử theo yêu cầu (request/response), không có subscription streaming nào được thiết lập.

## 4. Entry Points

- **File khởi động chính**: `tick_engine/__main__.py`, hàm `main(argv)` (dòng 551).
- **Chế độ dịch vụ 24/7**: `python -m tick_engine service` → `main()` rẽ nhánh sang `tick_engine.backend_engine.main()` (dòng 561-564) → khởi tạo `BackendSupervisor(load_service_config()).run()` (`backend_engine.py:380-395`).
- **Operator launcher**: `initial_setup/launcher/tick_launcher.ps1` — menu PowerShell gọi `python -m tick_engine <subcommand>` cho từng thao tác (health check, OAuth, start service ẩn nền qua `Start-Process -WindowStyle Hidden`, backfill thủ công, xem log…).
- **Thứ tự khởi động bên trong `BackendSupervisor.run()`** (`backend_engine.py:290-333`):
  1. `ensure_runtime_dirs()`
  2. Log kế hoạch job (`_log_plan`)
  3. Nếu `--dry-run`: in lịch rồi thoát
  4. `_check_prerequisites()` — load settings, kiểm tra field cTrader còn thiếu
  5. `_repair_stale_ingest_runs()` — đánh dấu `IngestRun` còn "RUNNING" từ lần chạy trước thành `INTERRUPTED`
  6. `_repair_stale_progress_files()` — đánh dấu file progress batch cũ còn "RUNNING" là "STALE"
  7. `_acquire_supervisor_pid()` — giành singleton PID file, có cơ chế handoff nếu có supervisor cũ đang chạy
  8. Cài signal handler (SIGINT/SIGTERM/SIGBREAK)
  9. `scheduler.init_timers()`, ghi heartbeat "RUNNING"
  10. Vòng lặp chính 1s/lần: poll stop sentinel → `scheduler.tick()` → ghi heartbeat → sleep
  11. Khi dừng: `_shutdown()` → `scheduler.shutdown()` → ghi heartbeat "STOPPED" → giải phóng PID file
- **Job con**: mỗi job lịch trình (refresh-token, check, spool-drain, backfill-batched, build-activity-profile…) được spawn như **subprocess** riêng `python -m tick_engine <subcommand>` qua `spawn_job()` (`scheduler.py:256-322`) — không chạy trong cùng tiến trình supervisor.

## 5. cTrader Connection and Authentication Flow

- **Cơ chế**: SDK chính thức `ctrader_open_api` (thư viện `ctrader-open-api` + `twisted`), sử dụng protobuf qua TCP, event-loop Twisted reactor. Nạp bằng `load_ctrader_sdk()` (`runtime.py:111-129`), là **optional dependency** — nếu chưa cài sẽ raise `MissingCTraderSdk`.
- **Application authentication**: `make_application_auth_req()` gửi `ProtoOAApplicationAuthReq` với `client_id`/`client_secret` (`runtime.py:209-213`), thực hiện trong `send_auth_chain()` (`runtime.py:295-343`).
- **Account authentication**: sau khi app auth OK, gửi tiếp `ProtoOAAccountAuthReq` với `ctidTraderAccountId` + `access_token` (`runtime.py:216-220`, gọi tại `send_auth_chain.on_app_auth` dòng 333-337). Đây là chuỗi tuần tự: connect → app auth → account auth → callback `on_authed`.
- **Account ID**: lấy từ `CTRADER_ACCOUNT_ID` trong `.env`, hoặc nếu trống, lấy từ token cache đã lưu (`ctidTraderAccountId` trong `runtime/cache/ctrader_ftmo_oauth.json`) — xem `load_settings()` (`runtime.py:170`).
- **Cơ chế chọn đúng tài khoản FTMO Swing**: **không có logic tự động xác định "Swing" trong code**. Việc chọn account là **thủ công qua operator**: chạy `account-list` để liệt kê mọi account được cấp quyền bởi access token, rồi `--save-account-id` hoặc `--save-matching-login` để lưu account đã chọn vào token cache (`__main__.py:186-211`, hàm `_maybe_save_selected_account`). Không có validation nào kiểm tra tên/loại tài khoản là "FTMO Swing" — nếu operator chọn nhầm `ctidTraderAccountId`, chương trình vẫn chạy bình thường với account đó. → **Có khả năng chọn nhầm account** nếu thao tác thủ công sai.
- **Access/refresh token**: lưu trong `runtime/cache/ctrader_ftmo_oauth.json` (file JSON, `chmod 0o600` trên POSIX) qua `save_token_cache()` (`token_store.py:114-140`). `TickRuntimeSettings` ưu tiên biến môi trường, fallback về cache.
- **Token hết hạn**: `ensure_fresh_access_token()` (`runtime.py:400-447`) được gọi trước mỗi thao tác cTrader — kiểm tra `should_refresh_access_token` (còn lại ≤ `token_refresh_safety_seconds`, mặc định 24h), nếu cần thì gọi `refresh_access_token()` và lưu lại cache. Ngoài ra job lịch trình `refresh-token` chạy mỗi `TICK_TOKEN_REFRESH_INTERVAL_SECONDS` (mặc định 1800s) và tại startup (`scheduler.py:580-585`).

## 6. FTMO Swing Account Selection

- Không tìm thấy trong code bất kỳ ràng buộc, filter, hay validate nào liên quan cụ thể tới "FTMO Swing" (không có chuỗi "Swing" nào trong toàn bộ mã nguồn đã đọc).
- Việc chọn account hoàn toàn phụ thuộc vào giá trị `CTRADER_ACCOUNT_ID` trong `config/tick_program.env` hoặc `ctidTraderAccountId` đã lưu trong token cache — do người vận hành cấu hình thủ công qua `oauth-login` + `account-list --save-account-id` (`__main__.py:644-678`).
- **Chưa đủ bằng chứng** để xác nhận tài khoản đang cấu hình trong `config/tick_program.env` hiện tại có đúng là FTMO Swing hay không — cần kiểm tra giá trị thực tế (không được đọc vì đây là file chứa credential, và theo yêu cầu không hiển thị secret).

## 7. Tick Subscription Flow

**Lưu ý quan trọng**: chương trình **không có cơ chế "subscribe" tick theo nghĩa real-time streaming** (không gọi `ProtoOASubscribeSpotsReq` hoặc tương đương). Thay vào đó:

- **Symbol được cấu hình**: hard-code trong `tick_engine/settings.py` (`SYMBOLS` list, dòng 274-286) — 11 symbol cố định với `symbol_id` nội bộ (khớp `DWH.Dim_Symbol`).
- **Symbol ID cTrader**: được ánh xạ qua bước riêng `symbol-sync` — gọi `fetch_remote_symbols()` (dùng `ProtoOASymbolsListReq`) rồi `build_symbol_matches()` so khớp tên/alias (`symbols.py:80-153`), lưu kết quả vào bảng `tick.SymbolMap` (cột `CTraderSymbolId`). Backfill sau đó đọc từ `tick.SymbolMap` (`fetch_matched_symbols()`) thay vì gọi lại API mỗi lần.
- **Loại dữ liệu lấy**: `ProtoOAGetTickDataReq` (historical tick), gọi riêng hai lần cho mỗi cửa sổ thời gian: `quote_type="BID"` và `quote_type="ASK"` (`historical_pulling.py:1144-1227`), sau đó merge lại thành quote hai chiều bằng `merge_historical_quote_ticks()`.
- **Một lần "backfill" xử lý bao nhiêu symbol**: tất cả symbol đã MATCHED trong `tick.SymbolMap` (có thể lọc bằng `--symbols`), xếp thành hàng đợi tuần tự (`queue: deque[HistoryWindowRequest]`), xử lý từng symbol/window một, không song song trong cùng một kết nối.
- **Quản lý tập trung hay phân tán**: tập trung — một tiến trình `backfill` giữ một kết nối cTrader duy nhất, xử lý tuần tự toàn bộ hàng đợi window×symbol, rồi đóng kết nối khi xong.
- **Trạng thái symbol đã "subscribe"**: không có trạng thái subscription; trạng thái được lưu là **tiến độ ingest** (`tick.IngestState`: `LastHistoricalTickTimeUtc`, `LastSourceTimestampMs`…) dùng để biết đã lấy tới đâu.
- **Sau reconnect có resubscribe không**: không áp dụng — mỗi lần backfill là một kết nối mới, khi kết thúc sẽ ngắt hẳn (`stop_reactor`). Không giữ session dài hạn để phải "resubscribe".
- **Nguy cơ trùng lặp**: được ngăn ở tầng SQL bằng `EventHash` (SHA-256 của symbol+timestamp+bid/ask+quote_type) và `INSERT ... WHERE NOT EXISTS` với `UPDLOCK, HOLDLOCK` trong `insert_ticks()` (`store_sql.py:287-349`), cộng với unique index `EventHash` (`IGNORE_DUP_KEY=ON`) ở tầng bảng SQL. Do các job dùng cửa sổ thời gian **cố ý chồng lấn** (overlap 60s), việc trùng lặp raw request là thiết kế có chủ đích, nhưng được khử trùng an toàn ở tầng ghi.

## 8. End-to-End Tick Data Flow

```text
cTrader Open API (TCP/protobuf, historical GetTickData request)
→ historical_pulling.fetch_side() nhận callback on_ticks (client.send(...).addCallbacks)
→ ticks.decode_delta_ticks()   — giải mã delta timestamp/price thành DecodedHistoricalTick tuyệt đối
→ historical_pulling.merge_historical_quote_ticks()  — ghép luồng BID + ASK theo timestamp, forward-fill 2 chiều,
    lọc outlier giá (median×5), lọc crossed (Ask<Bid), lọc spread quá rộng (>1000 bps)
→ ticks.TickRecord.from_historical_quote()  — quy đổi raw_price/1e5 thành Decimal Bid/Ask, sinh EventHash
→ data_storage.spool.TickBatcher.add()  — gom batch (mặc định 500 tick / 1.0s)
→ khi đầy batch/hết thời gian: TickBatcher.flush()
    ├─ thành công → store_sql.TickSqlStore.insert_ticks()  — MERGE/INSERT dedup theo EventHash vào bảng tick.<SYMBOL>
    │                 → cập nhật tick.IngestState (update_ingest_state_after_insert)
    └─ lỗi SQL → data_storage.spool.TickSpool.append_many()  — ghi overflow vào SQLite (runtime/spool/tick_overflow.db)
                  → thông báo Discord (on_spooled) cảnh báo mất kết nối SQL, dữ liệu chưa mất
→ downstream: SQL views tick.v_<SYM>_Quote (forward-fill), tick.v_LatestQuote (1 dòng/symbol),
    tick.v_IngestHealth (theo dõi sức khoẻ) — dùng bởi hệ thống auto trading SEN05 và dashboard tick_datacheck (:8060)
```

Chi tiết từng bước:
- **Callback nhận dữ liệu**: `on_ticks(message)` trong `fetch_side()` (`historical_pulling.py:1182-1222`) — hỗ trợ phân trang (`hasMore`) bằng cách gọi đệ quy `fetch_side()` với `to_timestamp_ms` lùi lại.
- **Schema tick**: `DecodedHistoricalTick(timestamp_ms, raw_price, quote_type)` → sau merge là `TickRecord` (dataclass 20 field, bao gồm `event_hash`).
- **Timestamp**: cTrader trả delta (chênh lệch so với tick trước) theo mili-giây, được giải mã tuyệt đối trong `decode_delta_ticks()` (`ticks.py:343-378`); lưu SQL dưới dạng `DATETIME2(3)` UTC naive.
- **Giá**: `raw_price / 100000` (Decimal, `CTRADER_PRICE_SCALE=100000`), làm tròn theo `digits` của symbol (`price_from_raw()`, `ticks.py:19-26`).
- **Chuẩn hoá**: có — loại tick giá 0/âm (`is_valid_raw_price`), loại outlier theo median, loại tick "crossed" (Ask<Bid), loại spread bất thường >1000bps.
- **Queue/buffer**: có `TickBatcher` (in-memory buffer trước khi ghi SQL) + `TickSpool` (SQLite overflow khi SQL Server không sẵn sàng, có `journal_mode=WAL`).
- **Điểm cuối**: bảng SQL Server `tick.<SYMBOL>` (11 bảng), sau đó phơi ra qua view; không có bước "publish" tới message broker hay API khác trong repo này.
- **Downstream sử dụng**: chưa xác nhận trực tiếp module nào trong hệ thống auto trading đọc các view này (nằm ngoài phạm vi workspace hiện tại) — chỉ xác nhận được cấu trúc view tồn tại và được thiết kế để phục vụ mục đích đó (comment trong SQL: "used by dashboard chart", "used by /api/symbols").

## 9. Concurrency and Task Lifecycle

- **Trong tiến trình supervisor** (`BackendSupervisor`): vòng lặp đồng bộ đơn luồng, 1 giây/lần (`_LOOP_SECONDS = 1.0`), không dùng async/await. Có **1 thread phụ** cho mỗi job đang chạy để "tee" output từ subprocess sang logger (`threading.Thread(target=_tee, daemon=True)` trong `spawn_job()`).
- **Job con** (backfill, check, refresh-token…): mỗi job là **một subprocess Python độc lập** (`subprocess.Popen`), không chạy trong tiến trình supervisor — cô lập lỗi/crash. `TickScheduler._reap()` poll trạng thái subprocess, không block.
- **Trong tiến trình backfill** (`run_history_backfill`, `fetch_account_list`, …): dùng **Twisted reactor** (event loop callback-based, không phải asyncio) — `sdk.reactor.run()` chạy tới khi `stop_reactor()` được gọi. Toàn bộ luồng lấy tick, timeout (`reactor.callLater`), gửi/nhận message đều là callback trên reactor, đơn luồng.
- **Lock/shared state**:
  - File lock `ctrader-history.lock` (`exclusive_job_lock`) đảm bảo chỉ một job chiếm "quyền" gọi cTrader history API tại một thời điểm (tránh 2 backfill chạy song song).
  - DB advisory lock `SEN.ActiveTask` (bảng SQL) dùng cho mục đích khác trong hệ thống lớn hơn (module `lock_coord.acquire/release/renew`), không thấy được gọi trực tiếp bởi tick_engine trong các file đã đọc (có thể dùng bởi phần trading khác chia sẻ cùng schema).
  - `SUPERVISOR_PID` file — singleton, có cơ chế handoff giữa 2 tiến trình supervisor.
  - Cancel sentinel file (`runtime/run/cancel/*.cancel`) — cơ chế cooperative-cancel, các job tự kiểm tra `raise_if_cancelled()` định kỳ.
- **Vòng đời job nền** (định nghĩa trong `build_jobs()`, `scheduler.py:569-701`):

| Job | Cadence | Ghi chú |
|---|---|---|
| `refresh-token` | mỗi 1800s, chạy ngay khi start | |
| `check` | mỗi 300s | health check + notify |
| `spool-drain` | mỗi 600s | đẩy dữ liệu từ SQLite spool vào SQL |
| `build-activity-profile` | hằng ngày lúc 03:00 UTC | học pattern hoạt động thị trường |
| `startup-catchup-backfill` | chỉ lúc khởi động (startup-only) | backfill 180 phút gần nhất |
| `frequent-backfill` | mỗi 600s | backfill 60 phút gần nhất |
| `hourly-repair` | mỗi 3600s | backfill sửa lỗi 360 phút gần nhất |
| `daily-deep-repair` | hằng ngày lúc 22:30 UTC (cooldown 4h) | backfill sửa lỗi 1440 phút |
| `first-run-seed` | chỉ khi bật cờ `CTRADER_FTMO_TICK_FIRST_RUN_AUTO_START` | seed lịch sử ban đầu (30 ngày mặc định) |

- Các job **backfill** (`_HISTORY_JOBS`) được **gate ưu tiên** — nếu nhiều job backfill đến hạn cùng lúc, chỉ job có `_BACKFILL_JOB_PRIORITY` cao nhất chạy, các job khác bị "suppress" (đánh dấu đã chạy để không dồn) (`scheduler.py:744-765`). Nếu có backfill thủ công đang chạy (`_manual_backfill_active`), toàn bộ backfill lịch trình bị hoãn.

## 10. Reconnect, Timeout and Recovery

- **Vì không giữ kết nối realtime dài hạn**, "reconnect" ở đây có nghĩa là: mỗi lần chạy một job backfill là một kết nối mới từ đầu (connect → app auth → account auth → request → disconnect).
- **Phát hiện mất kết nối**: `on_disconnected()` callback (`historical_pulling.py:1310-1319`) — nếu chưa `finished` thì coi là lỗi và gọi `on_error()`.
- **"Reconnect" ở tầng batch**: `run_batched_backfill()` chia một khoảng thời gian lớn thành nhiều batch ngắn (mặc định 60 phút/batch, overlap 60s), **mỗi batch chạy như một subprocess `backfill` riêng** (`historical_pulling.py:398-433`) — nghĩa là kết nối cTrader được mở lại hoàn toàn (kể cả auth) cho mỗi batch, không phải reconnect trong cùng session.
- **Retry/backoff**: mỗi batch thử tối đa `max_attempts` lần (mặc định 3, job lịch trình dùng `TICK_SCHEDULED_BACKFILL_MAX_ATTEMPTS=5`), backoff kiểu exponential-capped: `min(retry_sleep_max, retry_sleep_seconds × 3^(attempt-1))` (`historical_pulling.py:630-641`). Mặc định `retry_sleep_seconds=15s`, `retry_sleep_max_seconds=180s` (từ `.env`).
- **Lỗi không nên retry**: nếu lỗi auth thuộc nhóm `RET_ACCOUNT_DISABLED`, `RET_ACCOUNT_NOT_FOUND`, `RET_ACCOUNT_NOT_AUTHORIZED`, `RET_INVALID_ACCOUNT` → dừng retry ngay (`_non_retryable_auth_reason`), ghi log ERROR (`historical_pulling.py:92-105`).
- **Auth có thực hiện lại mỗi batch không**: có — mỗi subprocess batch chạy lại toàn bộ chuỗi connect+auth từ đầu (`ensure_fresh_access_token` → connect → app auth → account auth).
- **Symbol có "subscribe" lại không**: không áp dụng (không có subscription); mỗi batch tự đọc lại `tick.SymbolMap` qua `fetch_matched_symbols()`.
- **Trạng thái cũ**: `tick.IngestState` được cập nhật tăng dần (`update_ingest_state_after_insert`), không bị reset giữa các batch — chỉ bị reset toàn bộ khi operator chạy lệnh nguy hiểm `reset-tick-data`.
- **Dữ liệu trong lúc mất kết nối**: nếu ghi SQL thất bại (không phải lỗi cTrader mà là lỗi SQL Server), tick được đẩy vào SQLite spool (`TickSpool`) để không mất, và job định kỳ `spool-drain` (mỗi 600s) sẽ đẩy lại vào SQL khi kết nối phục hồi.
- **Backfill dữ liệu bị thiếu**: đây chính là **cơ chế thiết kế cốt lõi** — không phải cơ chế phụ. Có 3 tầng "repair" chồng lấn theo thời gian: `frequent-backfill` (1h gần nhất, mỗi 10 phút), `hourly-repair` (6h gần nhất, mỗi giờ), `daily-deep-repair` (24h gần nhất, mỗi ngày lúc 22:30 UTC). Ngoài ra `startup-catchup-backfill` bù đắp 180 phút gần nhất ngay khi service khởi động lại (đề phòng downtime).

## 11. Timeout và Heartbeat

- **Connection/response timeout**: `responseTimeoutInSeconds=settings.response_timeout_seconds` cho mỗi lệnh `client.send()` (mặc định `CTRADER_FTMO_TICK_RESPONSE_TIMEOUT_SECONDS=60`, job lịch trình dùng `CTRADER_FTMO_TICK_SCHEDULED_REQUEST_TIMEOUT_SECONDS=120`).
- **Timeout tổng cho cả phiên backfill**: `reactor.callLater(total_timeout_seconds, ...)` — tính dựa trên số request dự kiến × response_timeout + 60s, hoặc override bằng `--timeout` (`historical_pulling.py:1064-1069`).
- **Authentication timeout**: có timeout riêng cho `fetch_account_list`, `verify_account_auth`, `fetch_remote_symbols` (mặc định 45s) qua `reactor.callLater`.
- **Heartbeat/keep-alive kiểu ping-pong cTrader**: **không thấy** — không có `ProtoOAApplicationHeartbeat` hay tương tự trong code (phù hợp vì không giữ kết nối dài hạn).
- **Heartbeat nội bộ của service**: có — `write_service_heartbeat()` ghi file `runtime/run/service_heartbeat.json` mỗi tối thiểu `TICK_SERVICE_HEARTBEAT_INTERVAL_SECONDS` (30s), coi là "stale" nếu tuổi > `TICK_SERVICE_HEARTBEAT_STALE_SECONDS` (180s) — dùng để `check` phát hiện service bị "hung" (còn PID nhưng không heartbeat).
- **Phát hiện job con "đứng"**: `TickScheduler._reap()` theo dõi `idle_seconds()` (thời gian không có output mới); nếu vượt `TICK_CHILD_IDLE_TIMEOUT_SECONDS` (mặc định 1800s) thì gửi cancel sentinel rồi terminate (`scheduler.py:766-781`).
- **Phát hiện "stale connection"/dữ liệu cũ**: không phải ở tầng kết nối mà ở tầng dữ liệu — `run_tick_check()` so sánh tuổi của tick mới nhất với `stale_seconds` (mặc định 1800s), có học "khung giờ hoạt động" (`activity_profile`) để tránh báo động giả khi thị trường đóng cửa.

## 12. Error Handling

| Nhóm lỗi | Xử lý |
|---|---|
| Application/account auth rejected | `write_system_event(..., level="ERROR")`, raise Exception, dừng phiên (`on_error` → `stop_reactor`) — không retry trong cùng phiên, nhưng có thể retry ở tầng batch ngoài |
| Network/socket disconnect | `on_disconnected` → coi là lỗi nếu chưa `finished`, retry ở tầng batch với backoff |
| Timeout (request/toàn phiên) | `reactor.callLater(...).on_error(TimeoutError(...))`, log + retry ở tầng batch |
| Malformed message | Bắt trong `try/except` của `on_ticks`, gọi `on_error(exc)` |
| Symbol lookup error | `symbol-sync` báo `UNMATCHED`/`AMBIGUOUS` (không raise), ghi vào `SymbolMap.MappingStatus`; `run_tick_check` phát ERROR nếu symbol enable nhưng chưa MATCHED |
| Subscription (thực chất: yêu cầu lịch sử) lỗi | Retry có backoff, log `Backfill Failure`/`Backfill Attempt` tuỳ cấu hình `TICK_ENGINE_BACKFILL_FAILURE_LEVEL` |
| SQL insert lỗi | Không propagate crash — spool vào SQLite (`on_spooled`), gửi cảnh báo Discord, log `logger.exception` (swallow + fallback) |
| DB connect lỗi | Retry `DB_RETRY_COUNT` (mặc định 3) lần với delay `DB_RETRY_DELAY_SEC` (5s) trong `get_connection()`, sau đó raise |
| Shutdown lỗi | `scheduler.shutdown()` gửi cancel sentinel, chờ `_JOB_STOP_GRACE=20s`, sau đó force terminate; các `IngestRun` còn RUNNING được tự sửa thành `INTERRUPTED` ở lần khởi động kế tiếp |
| Cancel yêu cầu (operator) | `CancelRequested` exception riêng, không coi là lỗi — trạng thái ghi là `CANCELLED`/`INTERRUPTED`, không retry |

Nhìn chung: lỗi cTrader/network → log + retry có giới hạn + backoff; lỗi SQL ghi tick → không mất dữ liệu (spool) nhưng service tiếp tục chạy; lỗi auth vĩnh viễn (account bị disable…) → dừng retry ngay để tránh vòng lặp vô ích; mọi lỗi quan trọng đều ghi `system.log` và có thể đẩy Discord (có throttle + policy lọc để tránh spam khi thị trường đóng cửa).

## 13. Configuration

Từ `config/tick_program.env.example` và `settings.py` (không hiển thị giá trị bí mật):

- **SQL Server**: `SQL_SERVER`, `SQL_DATABASE` (mặc định `SEN05_AutoTrading`), `SQL_DRIVER`, `SQL_PORT`, `SQL_UID`/`SQL_PWD`, `SQL_ENCRYPT`, `SQL_TRUST_SERVER_CERT`.
- **cTrader/FTMO**: `CTRADER_ENV` (demo/live), `CTRADER_CLIENT_ID/SECRET`, `CTRADER_ACCESS_TOKEN/REFRESH_TOKEN`, `CTRADER_ACCOUNT_ID`, `CTRADER_TRADER_LOGIN`, `CTRADER_REDIRECT_URI`, `CTRADER_OAUTH_SCOPE`.
- **Tick tuning**: batch size, flush seconds, queue maxsize (khai báo trong `.env.example` nhưng `TICK_QUEUE_MAXSIZE`/`TICK_RECONNECT_MIN/MAX_SECONDS` **không thấy được đọc trong `settings.py` hiện tại** — có vẻ là biến "còn sót" từ một phiên bản trước có realtime WS, hiện không dùng — xem mục 16).
- **Lịch trình backfill**: `CTRADER_FTMO_TICK_BACKFILL_DELAY_SECONDS`, `..._STARTUP_CATCHUP_*`, `..._FREQUENT_BACKFILL_*`, `..._HOURLY_REPAIR_*`, `..._DAILY_DEEP_REPAIR_*`.
- **First-run seed**: `..._FIRST_RUN_ENABLED`, `..._FIRST_RUN_AUTO_START`, `..._FIRST_RUN_BACKFILL_DAYS`.
- **Timeout/retry**: `..._SCHEDULED_REQUEST_TIMEOUT_SECONDS`, `..._SCHEDULED_BACKFILL_MAX_ATTEMPTS`, `..._SCHEDULED_BACKFILL_RETRY_SLEEP_SECONDS/MAX_SECONDS`.
- **Guardrail runtime**: `..._SERVICE_HEARTBEAT_INTERVAL/STALE_SECONDS`, `..._SCHEDULED_PROGRESS_STALE_SECONDS`, `..._CHILD_IDLE_TIMEOUT_SECONDS`.
- **Logging**: 3 file cố định — `runtime/logs/system.log`, `operation.log`, `manual.log` (RotatingFileHandler, 10MB × 5 backup).
- **Storage/spool**: `runtime/spool/tick_overflow.db` (SQLite), `runtime/cache/ctrader_ftmo_oauth.json` (token cache).
- **Notification**: `DISCORD_WEBHOOK_URL`.
- **Symbol universe**: hard-code trong `settings.py` (không qua `.env`).

## 14. External Dependencies

- **cTrader SDK**: gói `ctrader-open-api` (Python, optional import — cài qua `pip install ctrader-open-api twisted`), dùng cùng `twisted.internet.reactor` làm event loop. Không xác định được version cụ thể trong file đã đọc (không có `requirements.txt` pin version — xem `requirements.txt` nếu cần, chưa đọc chi tiết nội dung).
- **pyodbc**: driver kết nối SQL Server (`ODBC Driver 18 for SQL Server` mặc định).
- **python-dotenv**: nạp `config/tick_program.env`.
- **requests**: gọi HTTP OAuth token endpoint (`https://openapi.ctrader.com/apps/token`) và Discord webhook.
- **SQLite (built-in)**: dùng cho spool overflow, không phải service ngoài.
- **Discord Webhook**: kênh thông báo duy nhất, không có Slack/Telegram khác được thấy.
- **Database**: SQL Server (`SEN05_AutoTrading`), đây vừa là storage chính vừa đóng vai trò "message broker" gián tiếp (downstream đọc qua view SQL).
- **Message broker riêng biệt**: không có (không dùng Kafka/RabbitMQ/Redis…).
- **File storage**: chỉ dùng local filesystem (`runtime/` — logs, cache, run, spool).
- **Logging framework**: `logging` chuẩn của Python + `RotatingFileHandler`.
- **Monitoring service**: không có Prometheus/Grafana; "monitoring" thực chất là dashboard HTTP tự viết (`tick_datacheck`) + Discord alert.

## 15. File Map

| File | Class/Function chính | Vai trò | Được gọi bởi | Gọi tới |
|---|---|---|---|---|
| `tick_engine/__main__.py` | `main()`, `build_parser()` | CLI entry, điều phối subcommand | operator, launcher `.ps1` | mọi module dưới đây |
| `tick_engine/backend_engine.py` | `BackendSupervisor` | Vòng lặp service 24/7, singleton, heartbeat | `__main__.main()` khi `service` | `scheduler`, `settings`, `service_state` |
| `tick_engine/scheduler.py` | `TickScheduler`, `Job`, `spawn_job` | Lịch trình + spawn job subprocess | `backend_engine` | `proc_utils`, `lock_coord`, `env_safety` |
| `tick_engine/historical_pulling.py` | `run_history_backfill`, `run_batched_backfill`, `fetch_account_list`, `verify_account_auth`, `sync_symbols` | Toàn bộ logic kết nối/lấy tick cTrader | `__main__`, `scheduler` (qua subprocess) | `runtime.py`, `data_storage.*`, `reporting.*` |
| `tick_engine/utils_support/runtime.py` | `TickRuntimeSettings`, `load_settings`, `send_auth_chain`, `ensure_fresh_access_token` | Cấu hình runtime + wrapper SDK cTrader | `historical_pulling`, `__main__` | `token_store`, `auth.py`, `system_log` |
| `tick_engine/utils_support/auth.py` | `exchange_code_for_token`, `refresh_access_token`, `run_local_oauth_login` | OAuth thô qua HTTP | `runtime.py`, `__main__` | `requests` |
| `tick_engine/utils_support/token_store.py` | `save_token_cache`, `load_token_cache`, `token_status` | Cache token trên đĩa | `runtime.py`, `__main__` | filesystem |
| `tick_engine/utils_support/lock_coord.py` | `exclusive_job_lock`, `acquire/release` (DB), cancel file | Khoá tiến trình/DB | `__main__`, `scheduler`, `historical_pulling` | `db_connector`, `proc_utils` |
| `tick_engine/utils_support/proc_utils.py` | `is_pid_alive`, `terminate_pid` | Quản lý PID cross-platform | `lock_coord`, `backend_engine`, `health.py` | OS API |
| `tick_engine/utils_support/health.py` | `run_tick_check`, `build_tick_activity_profile` | Health check + activity profile | `__main__ check`, `scheduler` job | `store_sql`, `spool`, `service_state` |
| `tick_engine/utils_support/service_state.py` | `write_service_heartbeat`, `scan_backfill_progress` | Heartbeat + progress file | `backend_engine`, `health.py` | filesystem |
| `tick_engine/data_storage/store_sql.py` | `TickSqlStore` | Ghi/đọc SQL Server, dedup, IngestRun/State | `historical_pulling`, `health.py`, `__main__` | `db_connector`, `symbols.py`, `ticks.py` |
| `tick_engine/data_storage/spool.py` | `TickSpool`, `TickBatcher` | Buffer SQLite + batching insert | `historical_pulling`, `store_sql` | SQLite |
| `tick_engine/data_storage/symbols.py` | `TargetSymbol`, `RemoteSymbol`, `build_symbol_matches` | Ghép symbol nội bộ ↔ cTrader | `historical_pulling`, `store_sql` | — |
| `tick_engine/data_storage/ticks.py` | `TickRecord`, `decode_delta_ticks` | Model tick + giải mã giá/timestamp | `historical_pulling`, `spool` | — |
| `tick_engine/data_storage/db_connector.py` | `get_connection` | Kết nối SQL Server có retry | `store_sql`, `lock_coord`, `health.py` | `pyodbc` |
| `tick_engine/reporting/notifications.py` | `notify_tick_report`, `tg_alert` | Gửi Discord webhook | `historical_pulling`, `__main__ check` | `requests`, Discord |
| `tick_engine/reporting/notification_policy.py` | `build_tick_check_notification` | Lọc/giảm nhiễu cảnh báo | `__main__ check` | `system_log`, `health.py` |
| `tick_engine/reporting/system_log.py` | `write_system_event` | Ghi `system.log` | hầu hết module trên | filesystem |
| `tick_engine/tick_datacheck/server.py` | `TickDataCheckHandler`, `run_server` | Dashboard HTTP read-only :8060 | `__main__ datacheck`, launcher mode 4 | `tick_queries`, `health_queries` |
| `tick_engine/settings.py` | hằng số cấu hình, `build_conn_str` | Trung tâm cấu hình đường dẫn/env | mọi module | `.env` file, `env_utils` |
| `initial_setup/deploy_schema.py` | `main`, `check_schema_health` | Triển khai schema SQL | operator thủ công | `db_connector`, `sql/tickdata_setup.sql` |
| `initial_setup/launcher/tick_launcher.ps1` | menu PowerShell | Giao diện vận hành cho operator | operator | `python -m tick_engine ...` |

## 16. Text Sequence Diagram

```text
Operator/Scheduler
  → python -m tick_engine service
      → BackendSupervisor.run()
          → load_settings() [kiểm tra field cTrader thiếu]
          → repair stale IngestRun/progress (từ lần chạy trước)
          → acquire singleton PID (handoff nếu có supervisor cũ)
          → vòng lặp 1s: TickScheduler.tick()
              → job "due" theo lịch (ví dụ frequent-backfill mỗi 10 phút)
              → spawn_job(["backfill-batched", --from, --to, ...])  [subprocess con]
                  → backfill-batched chia thành các batch nhỏ
                      → mỗi batch: subprocess "backfill" riêng
                          → historical_pulling.run_history_backfill()
                              → load_ctrader_sdk() + new_client()
                              → client.startService()
                              → on_connected → send_auth_chain()
                                  → ProtoOAApplicationAuthReq  [application auth]
                                  → ProtoOAAccountAuthReq      [account auth — FTMO account]
                              → on_authed → send_next()
                                  → với mỗi symbol × window:
                                      → ProtoOAGetTickDataReq(BID)
                                      → ProtoOAGetTickDataReq(ASK)
                                      → merge_historical_quote_ticks()
                                      → TickBatcher.add()/flush()
                                          → [OK] TickSqlStore.insert_ticks()  → tick.<SYMBOL>
                                                 → update tick.IngestState
                                          → [SQL lỗi] TickSpool.append_many()  → SQLite overflow
                                                       → notify_tick_report() Discord (WARNING)
                              → hết queue → batcher.flush() cuối → stop_reactor()
                              → finish_ingest_run(status=DONE/FAILED/INTERRUPTED)
                          → nếu lỗi/timeout: retry với backoff (tối đa N lần) trong cùng batch
                      → nếu batch fail sau hết retry: dừng toàn bộ chuỗi batch, ghi FAILED
              → job "check" (mỗi 5 phút): run_tick_check() → so sánh độ tươi dữ liệu →
                    nếu WARNING/ERROR & không bị suppress → notify_tick_report() Discord
              → job "spool-drain" (mỗi 10 phút): đẩy SQLite overflow → SQL Server
          → nhận SIGINT/SIGTERM hoặc supervisor.stop sentinel
              → scheduler.shutdown(): gửi cancel sentinel tới job đang chạy, chờ 20s, force-terminate còn lại
              → ghi heartbeat STOPPED, giải phóng PID file
```

## 17. Confirmed Facts

Các kết luận dưới đây có bằng chứng trực tiếp từ code:

1. Đây là **dịch vụ backfill lịch sử định kỳ**, không có kết nối WebSocket/streaming realtime — tự nêu rõ trong docstring `backend_engine.py:1-7`: *"The service keeps no realtime WebSocket connection."*
2. Entry point service: `tick_engine/__main__.py:main()` dòng 551, rẽ nhánh sang `tick_engine.backend_engine.main()` khi `args.command == "service"` (dòng 561-564).
3. Kết nối cTrader dùng SDK `ctrader_open_api` + Twisted reactor, load động qua `load_ctrader_sdk()` (`runtime.py:111-129`), là optional dependency.
4. Xác thực app → account theo đúng chuỗi tuần tự trong `send_auth_chain()` (`runtime.py:295-343`): `ProtoOAApplicationAuthReq` trước, `ProtoOAAccountAuthReq` sau, chỉ gửi account auth sau khi app auth thành công (`on_app_auth`).
5. Không có logic nào trong code ràng buộc account phải là "FTMO Swing" — account được chọn hoàn toàn qua cấu hình thủ công `CTRADER_ACCOUNT_ID`/token cache, xác nhận bằng việc tìm kiếm không thấy chuỗi "Swing" trong toàn bộ source đã đọc và cơ chế lưu account trong `_maybe_save_selected_account()` (`__main__.py:186-211`) không kiểm tra gì ngoài khớp `ctidTraderAccountId`/`traderLogin`.
6. Tick được lấy qua API lịch sử `ProtoOAGetTickDataReq` (không phải subscribe realtime), gọi riêng cho BID và ASK rồi merge, xác nhận tại `historical_pulling.py:1144-1303`.
7. Dedup tick dựa trên `EventHash` (SHA-256) ở cả tầng ứng dụng (`insert_ticks` dùng `NOT EXISTS`) và tầng SQL (`UNIQUE INDEX ... IGNORE_DUP_KEY=ON`), xác nhận tại `store_sql.py:320-341` và `tickdata_setup.sql:249-252`.
8. Có 5 tầng backfill lịch trình với độ ưu tiên khác nhau (`_BACKFILL_JOB_PRIORITY`), chỉ 1 job backfill chạy tại một thời điểm, xác nhận tại `scheduler.py:54-60, 744-901`.
9. Có cơ chế spool SQLite khi ghi SQL Server thất bại, không làm mất dữ liệu, xác nhận tại `TickBatcher.flush()` (`spool.py:131-156`).
10. Token refresh tự động trước mỗi thao tác cTrader qua `ensure_fresh_access_token()` (`runtime.py:400-447`), cộng thêm job lịch trình riêng mỗi 1800s.
11. Dashboard `tick_datacheck` là read-only, không điều khiển service, tự khẳng định trong docstring (`server.py:1-9`).

## 18. Unknowns and Missing Evidence

- **Không thể xác nhận từ code**: liệu `CTRADER_ACCOUNT_ID` hiện đang cấu hình trong `config/tick_program.env` có thực sự trỏ tới FTMO Swing account hay không — giá trị này là dữ liệu vận hành thực tế, không nằm trong mã nguồn.
- **Phụ thuộc biến môi trường** (không đọc được nội dung thật vì là secret): `CTRADER_CLIENT_ID/SECRET`, `CTRADER_ACCESS_TOKEN/REFRESH_TOKEN`, `SQL_UID/PWD`, `DISCORD_WEBHOOK_URL` — hành vi thực tế (đã set hay chưa, có hợp lệ hay không) cần kiểm tra qua `token-status`/`show-config` lúc runtime, không thể suy ra tĩnh từ code.
- **Downstream tiêu thụ thực tế**: chương trình tạo view SQL rõ ràng để phục vụ hệ thống trading khác, nhưng **không có bằng chứng trong workspace hiện tại** (`Z:\tick_program`) về module trading nào thực sự query các view này — cần kiểm tra repo hệ thống auto trading riêng (nằm ngoài phạm vi được cung cấp).
- **Một số biến trong `.env.example` có vẻ không còn được đọc bởi code hiện tại**: `CTRADER_FTMO_TICK_QUEUE_MAXSIZE`, `CTRADER_FTMO_TICK_RECONNECT_MIN_SECONDS`, `CTRADER_FTMO_TICK_RECONNECT_MAX_SECONDS`, `CTRADER_FTMO_TICK_HEARTBEAT_LOG_SECONDS`, `CTRADER_FTMO_TICK_DISCORD_REPORT_SECONDS` — không tìm thấy các tên này trong `settings.py` (chỉ xác nhận bằng cách rà `settings.py`, chưa grep toàn bộ repo để loại trừ khả năng chúng được đọc trực tiếp bằng `os.environ.get()` ở nơi khác). Đây có thể là tàn dư cấu hình từ một kiến trúc realtime-WS cũ trước khi refactor sang backfill-only — **giả thuyết, chưa xác nhận đầy đủ**.
- **Version cụ thể của `ctrader-open-api`/`twisted`**: chưa đọc `requirements.txt` chi tiết để xác nhận version pin.
- **`SEN.ActiveTask` (DB advisory lock)**: có module `lock_coord.acquire/release/renew` đầy đủ nhưng **không thấy được gọi** ở bất kỳ đâu trong `historical_pulling.py`, `scheduler.py`, hay `__main__.py` đã đọc — có thể được dùng bởi phần khác của hệ thống SEN05 chia sẻ cùng bảng, hoặc là code chưa/không còn được dùng trong tick_engine. Cần grep toàn bộ để xác nhận nếu cần.
- **Cần log runtime để hiểu rõ hơn**: hành vi thực tế khi cTrader API rate-limit hoặc trả lỗi không nằm trong `_NON_RETRYABLE_AUTH_MARKERS`; tần suất thực tế của cảnh báo Discord bị suppress bởi `notification_policy.py` trong điều kiện thị trường thật.
- **Cần kiểm tra tài khoản FTMO/cTrader trực tiếp**: xác nhận `account-list` trả về đúng 1 account "Swing" như kỳ vọng, và `CTRADER_ENV` (demo/live) đang set đúng môi trường mong muốn.

## Preliminary Observations — Not Yet Audited

- Việc chọn tài khoản FTMO Swing hoàn toàn dựa vào thao tác thủ công của operator (`account-list` → `--save-account-id`), không có bất kỳ validation nào trong code để phát hiện nếu operator chọn nhầm account (ví dụ chọn nhầm sang một tài khoản demo hoặc một challenge khác có cùng access token). Đây chỉ là quan sát, không phải kết luận root cause hay đề xuất sửa.
