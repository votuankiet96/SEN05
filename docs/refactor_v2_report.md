# dp_program — Refactor v2: Báo cáo tổng hợp & Kế hoạch triển khai

**Ngày:** 2026-07-22
**Nguồn:** Tổng hợp từ phiên Q&A file/folder giữa Kiệt và Sonnet (log đầy đủ tại [refactor_v2_discussion_notes.md](refactor_v2_discussion_notes.md)), cộng với kết quả 4 vòng audit trước đó (Sonnet + Codex).
**Mục đích:** Tài liệu này là **spec để triển khai refactor v2** — dùng để hướng dẫn Codex thực thi, audit lại hướng xử lý, và làm cơ sở duyệt từng bước. Mọi thứ trong tài liệu này là **quyết định đã chốt với Kiệt**, trừ khi ghi rõ "CẦN THÊM DỮ LIỆU" hoặc "CHƯA RE-CONFIRM".
**Trạng thái tổng quát: CHƯA THỰC THI gì ngoại trừ 1 thay đổi config đã nêu ở mục 6.** Toàn bộ phần còn lại là quyết định/kế hoạch, chờ triển khai.

---

## 1. Phạm vi đã audit trong phiên này

Đã đi qua: `core_engine/` (tổng quan), `coordination/`, `historical/` (sâu), `live/` (sâu, tập trung `engine.py`), `warehouse/`, `tradingview/` (sâu, audit dead-code), `logkit/` + `reporting/` (sâu, audit dead-code), `supervisor/` + `health.py` (sâu, audit dead-code).

**Chưa đi qua / không cần đi qua** (theo yêu cầu Kiệt — các folder này đã có vị trí xác định sẵn từ quyết định cấu trúc, không cần audit nội dung sâu thêm): `redis_io/`, `dashboard/`, `settings/`, `other/` (`tls.py`, `exit_codes.py`).

---

## 2. Cấu trúc thư mục đích cho `src/core_engine/`

```
core_engine/
├── core/
│   ├── historical/
│   │   ├── engine.py, pipeline.py, runtime_support.py   (không đổi nội dung, trừ mục 4.1 bên dưới)
│   │   └── reporter.py            ← chuyển từ reporting/historical_reporter.py
│   └── live/
│       ├── engine.py (SẼ TÁCH — xem mục 5, streamline #13), state.py, runtime_support.py
│       ├── spool.py               ← chuyển từ warehouse/spool.py
│       └── reporter.py            ← chuyển từ reporting/live_reporter.py
├── shared/
│   ├── tradingview/                (protocol.py, history_client.py, auth/core.py, auth/jwt_utils.py, auth/captcha.py — không đổi vị trí)
│   ├── warehouse/                  (connection.py, writer.py, reader.py, validation.py, operation_log.py, maintenance.py, reconcile.py — spool.py đã chuyển đi, xem core/live/)
│   └── freshness.py (MỚI)          ← hợp nhất công thức "ngưỡng dữ liệu cũ" hiện viết trùng ở historical/live (streamline #14)
├── util/
│   ├── logkit/                     (không đổi: factory.py, handlers.py, formatters.py, tables.py, jsonl.py, activity.py)
│   ├── notify/ (MỚI)
│   │   ├── discord.py              ← chuyển từ reporting/discord.py
│   │   └── critical_outbox.py      ← chuyển từ logkit/critical_outbox.py
│   ├── supervisor/                 (engine.py, process_control.py — không đổi vị trí)
│   ├── coordination/                (locks.py — không đổi vị trí, không đổi logic)
│   ├── health.py                   (không đổi vị trí)
│   ├── cli.py                      (không đổi vị trí)
│   ├── redis_io/                   (không đổi vị trí — ngoài phạm vi audit sâu đợt này)
│   └── dashboard/                  (không đổi vị trí — ngoài phạm vi audit sâu đợt này)
├── other/
│   ├── tls.py                      ← chuyển từ vị trí hiện tại (root core_engine/)
│   └── exit_codes.py               ← chuyển từ vị trí hiện tại (root core_engine/)
└── settings/                       (đứng riêng, nhóm thứ 5 — instruments.py, system.py, operational.py — ngoài phạm vi audit sâu đợt này)
```

**`reporting/` biến mất hoàn toàn** — 3 file phân về 3 nơi khác nhau theo domain/vai trò thật (xem bảng quyết định #8 ở mục 3).

---

## 3. Quyết định kiến trúc đã chốt (đầy đủ, không chỉ 6 câu hỏi nghiệp vụ)

| # | Quyết định | Lý do |
|---|---|---|
| 1 | Gộp "hỗ trợ vận hành" + "ngoại vi" thành 1 folder `util/` | Đơn giản hoá cấu trúc — cùng bản chất "giúp chạy an toàn/giám sát", không trực tiếp lấy/lưu dữ liệu |
| 2 | `settings/` đứng riêng, không vào `util/` | Cả nhóm `core/` lẫn `util/` đều import trực tiếp — gộp vào `util/` sẽ khiến `core/` phụ thuộc ngược vào `util/`, phá vỡ hướng phụ thuộc core→shared→util |
| 3 | `coordination/` (locks.py) **bắt buộc phải có**, không đơn giản hoá logic — chỉ đổi vị trí vào `util/` | 3 tiến trình độc lập (`live`, `historical`, `supervisor`) cùng ghi 1 DB qua bảng `SEN.ActiveTask`; có sự cố thật (VM-DP6 reboot); rủi ro nếu bỏ là ghi chồng vào `Fact_OHLCV` mà SEN05 AutoTrading dùng để giao dịch thật |
| 4 | `tls.py`, `exit_codes.py` → `other/`, không phải `util/` | Không mang trạng thái/logic nghiệp vụ, dùng bởi cả `core/` lẫn `util/` — không phải "mối quan tâm vận hành" đúng nghĩa |
| 5 | `tradingview/`, `warehouse/` đổi nhãn từ "lõi nghiệp vụ" → `shared/` | Không cần di chuyển code, chỉ đổi cách phân nhóm — cả `historical/` lẫn `live/` đều cần |
| 6 | `warehouse/spool.py` → chuyển sang `core/live/` | Grep xác nhận 100%: chỉ `live/` gọi (qua `live/state.py`), `historical/` không dùng — không phải "shared" đúng nghĩa |
| 7 | `warehouse/reader.py`, `warehouse/operation_log.py` **giữ nguyên** trong `shared/warehouse/` (KHÔNG chuyển vào `redis_io/`/`logkit/`) | Grep xác nhận: chỉ 1/6 hàm của `reader.py` phục vụ Redis thật (5 hàm còn lại phục vụ `historical/`); `operation_log.py` là khuôn mẫu "mỗi domain tự có lớp format log riêng" nhất quán toàn hệ thống (giống `_hlog()`/`_llog()`), không phải hạ tầng log chung |
| 8 | Tổ chức lại `logkit/`+`reporting/` → `logkit/` (giữ nguyên) + `util/notify/` (MỚI, gộp `discord.py`+`critical_outbox.py`) + `reporter.py` theo domain (`core/historical/`, `core/live/`) | `discord.py` cross-cutting thật (5 nơi dùng khắp core+util+shared) nên hợp lý đứng cùng nhóm hạ tầng cảnh báo với `critical_outbox.py` (2 module cùng nói chuyện với Discord webhook, hiện tách rời vô lý ở 2 folder). `historical_reporter.py`/`live_reporter.py` là formatter đơn-người-dùng — đi theo domain giống nguyên tắc đã áp dụng cho `spool.py` |
| 9 | `health.py` **bắt buộc phải giữ**, không xoá/gộp | Grep xác nhận 2 consumer thật: `cli.py` (`doctor`/`status`/`data-health`) và `supervisor/engine.py` (input quyết định restart, gọi `collect_health()` 3 lần trong vòng lặp chính) |

---

## 4. Toàn bộ 24 ứng viên streamline — phân loại theo hành động

### 4.1 CODE CHẾT — xoá, rủi ro ~0 (grep xác nhận 0 caller toàn repo, độc lập bằng cả Python script lẫn ripgrep)

| # | Gì | Vị trí |
|---|---|---|
| 15 | `get_staging_bar_window()`, `get_fact_bar_window_context()` | `warehouse/reader.py` |
| 16 | `_renew_auth_token()` (~59 dòng, bị thay thế bởi `_renew_auth_token_coordinated()` mà `renew()` public thực sự gọi) | `tradingview/auth/core.py:1155-1213` |
| 17 | `TradingViewWsHistoryError` (exception class không ai raise/catch) | `tradingview/history_client.py:93-94` |
| 18 | `_fmt_ts()` | `tradingview/history_client.py:163-169` |
| 20 | `_headline_status()` (static method, 0 caller kể cả nội bộ class) | `reporting/live_reporter.py:360` → sau khi move: `core/live/reporter.py` |
| 21 | `_description_from_text()` | `reporting/discord.py:429` → sau khi move: `util/notify/discord.py` |
| 22 | `notify_database_event()` (4 hàm anh em cùng mẫu đều có người gọi, riêng cái này 0) | `reporting/discord.py:1177` → sau khi move: `util/notify/discord.py` |

### 4.2 TRÙNG LẶP LOGIC — hợp nhất, rủi ro thấp (hành vi giữ nguyên)

| # | Gì | Vị trí | Ghi chú |
|---|---|---|---|
| 8 | Khối đếm `consecutive_fail` trùng nhau trong `run_full_load`/`run_backfill` | `historical/pipeline.py` | Gộp thành 1 hàm dùng chung |
| 10 | Cấu trúc giữ/nhả khoá (acquire→atexit→try/finally) trùng giữa nhánh `reset` và nhánh `full/gap` | `historical/engine.py main()` | Gộp |
| 11 | `get_latest_bars()` bị gọi 2 lần khi mode=auto→gap (`detect_mode()` rồi `run_backfill()` gọi lại) | `historical/` | Truyền kết quả xuống thay vì gọi lại — lợi ích nhỏ (hiệu năng, không phải bug) |
| 14 | Công thức "ngưỡng dữ liệu cũ" viết trùng độc lập 2 nơi, đã xác minh cùng kết quả toán học | `historical/runtime_support.gap_threshold_minutes()` vs `live/runtime_support.freshness_threshold_minutes()` | Hợp nhất vào `shared/freshness.py` (mới) |
| 19 | Vòng lặp "nhận gói tin WS + echo heartbeat + parse" copy-paste (kèm y hệt 1 khối comment 10 dòng) | `tradingview/history_client.py`: `drain_until_complete()` (trong `fetch_history()`) vs `drain()` (trong `fetch_replay_window()`) | Tách thành 1 helper dùng chung trong `protocol.py` |
| 23 | Parse timestamp ISO/"Z" viết trùng | `health.py::_parse_time()` (dòng 73-87) vs `supervisor/engine.py::_live_state_age_seconds()` (dòng 891-897) | `engine.py` có thêm 2 guard riêng (khớp PID, bỏ qua timestamp cũ hơn lúc khởi động) → không xoá thẳng được, chỉ dùng chung bước parse |
| 24 | Lấy "tên host cục bộ" để so khớp lock cùng máy, viết trùng — **bản trong `health.py` yếu hơn thật trên Windows** (`os.uname()` không tồn tại trên Windows nên luôn ra rỗng) | `supervisor/process_control.py::_local_host_names()`/`_same_local_host()` vs `health.py::_locks_check()` (inline) | `health.py` nên gọi thẳng hàm của `process_control.py` |

### 4.3 ĐẶT TÊN/CẤU TRÚC FILE — cần bàn thêm trước khi làm (chưa có tên cụ thể)

| # | Gì | Ghi chú |
|---|---|---|
| 9 | Tên `runtime_support.py` (cả `historical/` lẫn `live/`) quá chung chung, không phản ánh phần giá trị nhất (vd logic phân loại lỗ hổng ở historical) | Cân nhắc đổi tên/tách nhỏ — **CẦN Kiệt chọn tên cụ thể trước khi thực thi**, chưa có đề xuất tên chốt |

### 4.4 REFACTOR LỚN — rủi ro trung bình→cao, làm sau cùng

| # | Gì | Lộ trình rủi ro thấp→cao |
|---|---|---|
| 13 | Tách `live/engine.py` (4.450 dòng — file lớn nhất hệ thống) theo mẫu `historical/` đã áp dụng thành công | 1) `logging_support.py` (~213d, rủi ro ~0) → 2) `batch_metrics.py` (~206d, rủi ro thấp) → 3) `db_worker.py` (~746d, gộp spool-dispatch + `_db_worker`, rủi ro trung bình) → 4) `BatchFetcher` (~1.054d, khó nhất — cần bàn riêng có tách phần giao thức WS khỏi phần vòng đời worker/wedge-detect hay không) |

### 4.5 Ứng viên từ audit gốc (trước phiên Q&A này) — CHƯA re-confirm trong phiên này, vẫn còn hiệu lực

| # | Gì | Độ khó | Trạng thái |
|---|---|---|---|
| 1 | Xoá nhánh `DP_STORAGE_MODE=redis` (`settings/operational.py`, chưa từng wire) | Dễ | Carried-over, chưa re-verify trong phiên này — **nên grep xác nhận lại trước khi xoá** |
| 4 | Bỏ toggle `reconcile-fact --count-unsupported-as-missing` | Dễ | Carried-over, chưa re-confirm |
| 5 | Hợp nhất logic "extract token từ page Playwright" trùng giữa `_headless_refresh`/`_headless_login_fresh` (`tradingview/auth/core.py`) | Dễ | Carried-over, chưa re-verify chi tiết trong phiên audit `tradingview/` lần này (phiên này tập trung dead-code, không xét lại điểm này) |
| 6 | Đơn giản hoá text-inference engine trong `discord.py` (~500/1.178 dòng đoán feature/result/impact/action bằng regex heuristic) | Trung bình-khó | Carried-over — **cần bàn phạm vi cụ thể trước khi làm**, không đưa vào đợt thực thi cơ học |

### 4.6 CẦN THÊM DỮ LIỆU VẬN HÀNH THẬT — chưa quyết, đừng thực thi

| # | Gì | Cần gì để quyết |
|---|---|---|
| 12 | Ngưỡng `consecutive_fail=8` trong `historical/` không phân biệt lỗi do auth hay do khoá `warehouse_maintenance` bị giữ lâu — có thể lặp vô ích | Cần đọc `historical_pulling.log` thật để xác nhận tình huống này có từng xảy ra chưa |

---

## 5. 6 quyết định nghiệp vụ (business decisions) — đã chốt với Kiệt

| # | Câu hỏi | Quyết định của Kiệt | Hành động |
|---|---|---|---|
| 1 | Release-directory deployment (~1.000 dòng script, chưa dùng lần nào, xác nhận KHÔNG active production) | **Bỏ** | Xoá script — đưa vào giai đoạn triển khai |
| 2 | NSSM Windows Service script (runbook cấm dùng) | **Giữ, chưa cần xoá** | KHÔNG hành động |
| 3 | `CANDLE_SNAPSHOT_ENABLED` (Redis/OG handoff) | **Tắt flag, giữ code `redis_io/`** — dùng lại làm nền tảng khi xây OG sau | **ĐÃ THỰC THI** (xem mục 6) |
| 4 | Headless auth fallback (lớp 3/4 trong `auth/core.py`) | **Tạm giữ nguyên** | KHÔNG hành động |
| 5 | 15 TF × 37 symbol — TF nào SEN05 AutoTrading thực sự đọc | **Bỏ qua — không liên quan** | Loại khỏi phạm vi refactor v2 |
| 6 | `warehouse/spool.py` — giữ hay tách | **Tách sang `core/live/`** | Đưa vào giai đoạn di chuyển cấu trúc |

---

## 6. Việc đã thực thi

- **2026-07-22**: `config/dp_provider.env` dòng 90: `CANDLE_SNAPSHOT_ENABLED=1` → `CANDLE_SNAPSHOT_ENABLED=0`. Xác nhận tại thời điểm đổi không có tiến trình `live` nào đang chạy giữ PID cũ (`runtime/run/ws_live_runtime.pid`) → thay đổi sạch, có hiệu lực từ lần `live` khởi động kế tiếp, không cần restart thủ công. Code `redis_io/` giữ nguyên hoàn toàn.

Ngoài mục này, **chưa có dòng code/cấu trúc file nào khác bị thay đổi.**

---

## 7. Kế hoạch triển khai đề xuất (cho Codex, theo giai đoạn)

Nguyên tắc sắp xếp: rủi ro thấp → cao, việc cơ học (move file, xoá dead-code) trước việc đổi logic, refactor lớn nhất để cuối cùng khi mọi thứ khác đã ổn định và có thể chạy full test suite làm mốc so sánh.

**Giai đoạn A — Dọn code chết** (mục 4.1, 7 điểm, rủi ro ~0)
Xoá thẳng, chạy lại test suite sau mỗi file để cô lập lỗi nếu có.

**Giai đoạn B — Hợp nhất trùng lặp logic** (mục 4.2, 7 điểm, rủi ro thấp)
Bao gồm tạo mới `shared/freshness.py` (#14) và helper dùng chung trong `protocol.py` (#19). Giữ nguyên hành vi — nên có test trước/sau đối chiếu output.

**Giai đoạn C — Di chuyển cấu trúc thư mục** (mục 2, cơ học)
Theo đúng cấu trúc đích ở mục 2: tạo `core/`, `shared/`, `util/notify/`, `other/`; di chuyển `spool.py`, `historical_reporter.py`→`reporter.py`, `live_reporter.py`→`reporter.py`, `discord.py`+`critical_outbox.py`→`notify/`, `tls.py`+`exit_codes.py`→`other/`. Cập nhật toàn bộ import path liên quan. **Rủi ro chính là sót import** — chạy full test suite + `python -m core_engine show-config` sau bước này để xác nhận không có ImportError.

**Giai đoạn D — Xử lý theo quyết định nghiệp vụ** (mục 5)
Xoá release-directory deployment script (quyết định #1). Không làm gì với NSSM (#2), headless auth (#4), 15TF×37symbol (#5) — giữ nguyên theo quyết định.

**Giai đoạn E — Refactor lớn: tách `live/engine.py`** (mục 4.4, làm SAU CÙNG)
Theo đúng 4 bước rủi ro thấp→cao đã liệt kê. Chạy test + xác nhận `live --smoke-seconds 60` sau MỖI bước tách, không gộp nhiều bước rồi mới test.

**Trước khi thực thi bất kỳ giai đoạn nào ở mục 4.5** (carried-over #1, #4, #5, #6): nên quay lại xác nhận với Kiệt hoặc tự grep-verify lại vì các mục này **chưa được re-confirm trong phiên Q&A vừa qua**.

**Không đụng tới trong đợt refactor v2 này**: `redis_io/`, `dashboard/`, `settings/`, `other/` nội dung bên trong (vị trí thư mục đã xác định, không audit sâu thêm) — và mục 4.6 (#12, cần dữ liệu log thật trước).

---

## 8. Việc còn treo (không nằm trong kế hoạch thực thi ngay)

- **#12**: cần đọc `historical_pulling.log` thật trước khi sửa ngưỡng `consecutive_fail=8`.
- **#9**: cần Kiệt chốt tên cụ thể trước khi đổi tên/tách `runtime_support.py`.
- **#6** (discord.py text-inference): cần bàn phạm vi cụ thể trước khi làm, không phải việc cơ học.
- **Mục 4.5** (carried-over #1, #4, #5): nên re-verify/re-confirm trước khi thực thi vì không được nhắc lại trong phiên Q&A vừa qua.

---

## 9. Ghi chú cho Codex khi triển khai

- Toàn bộ đường dẫn file/dòng trong báo cáo này lấy từ trạng thái code tại thời điểm audit (2026-07-22) — **verify lại bằng grep trước khi sửa**, đặc biệt nếu có commit nào chen giữa lúc audit và lúc thực thi.
- Log Q&A gốc đầy đủ (bối cảnh, lý do, bằng chứng grep chi tiết cho từng quyết định) nằm tại [refactor_v2_discussion_notes.md](refactor_v2_discussion_notes.md) — tham khảo khi cần hiểu rõ "tại sao" đằng sau một quyết định trong bảng tóm tắt ở đây.
- Sau mỗi giai đoạn (A-E), chạy test suite đầy đủ + `python -m core_engine show-config`/`doctor` trước khi sang giai đoạn kế tiếp.
