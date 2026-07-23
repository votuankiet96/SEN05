# dp_program — Audit + Test + Debug độc lập (2026-07-22)

Audit độc lập trên VM-DP6 thật (SSH `Administrator@10.11.12.6`), không tin báo cáo/refactor trước đó — mọi kết luận tự xác minh bằng code thật, test thật, log thật, và trạng thái runtime thật.

---

## 1. Kết luận

# **GO — có điều kiện**

Hệ thống đủ điều kiện vận hành production 24/7 dựa trên bằng chứng thu thập được. Điều kiện treo duy nhất: cơ chế "wedge → hard-deadline recycle" (3 batch liên tiếp kẹt → process tự thoát → supervisor respawn) đã được xác minh đầy đủ ở mức unit test + đọc code, nhưng **chưa chạy hết một lượt thật trên production** trong phiên audit này (bị gián đoạn bởi Codex làm việc song song trên cùng VM — xem mục 9-10). Đây là hạng mục duy nhất cần làm lại trước khi coi là "kiểm chứng đầy đủ 100%".

Không phát hiện bug nào trong code hiện tại của dp_program qua toàn bộ audit. Một số ứng viên streamline từ báo cáo cũ (trước phiên refactor v2) đã lỗi thời và bị bác bỏ bằng bằng chứng (mục 4).

---

## 2. Thời gian

- Bắt đầu: ~09:50 UTC 2026-07-22 (kết nối SSH đầu tiên vào VM-DP6, baseline `doctor`/`status`)
- Kết thúc: ~14:25 UTC 2026-07-22 (cleanup + báo cáo)
- Gồm 1 khoảng gián đoạn ~13:00-14:20 UTC do Codex làm việc song song trên cùng VM (xem mục 9)

## 3. Branch, HEAD, working tree

| Thời điểm | Branch | HEAD | Working tree |
|---|---|---|---|
| Lúc bắt đầu audit | `refactor/v2-streamline` | `85f15581` | sạch |
| Lúc kết thúc (sau khi Codex xong logging/alerting) | `refactor/v2-streamline` | `7d25221d` | sạch |

3 commit mới do Codex tạo trong lúc audit đang chạy:
```
4325ca8a fix: harden logging and alert delivery for 24x7 runtime
7c3b516f test: make critical alert assertions deterministic
7d25221d fix: treat idle notification producers as unexercised
```
Diff `85f15581..7d25221d`: 26 file, +2697/-382 dòng. Tập trung vào `util/logkit/` (paths.py mới), `util/notify/` (transport.py mới, discord.py/critical_outbox.py viết lại đáng kể), `util/health.py` (+277, thêm check `log_sinks`), `util/supervisor/engine.py` (+229).

`git diff --check` (whitespace): sạch ở cả 2 mốc.

## 4. Bảng findings

| # | Severity | file:line | Bằng chứng | Kịch bản | Trạng thái |
|---|---|---|---|---|---|
| F1 | Info (bác bỏ đề xuất cũ) | `core/live/engine.py:1269-1289` | Đọc code trực tiếp: `if STORAGE.mode == "redis": ... return 1` kèm comment "SQL is the durable system of record" | Streamline candidate #1 từ báo cáo refactor v2 gốc ("xoá nhánh DP_STORAGE_MODE=redis chưa từng wire") **đã lỗi thời** — code hiện tại KHÔNG phải dead code mà là safety gate chủ động từ chối khởi động ở mode redis-only | Đã xác minh, khuyến nghị **giữ nguyên, không xoá** |
| F2 | Low (vận hành, không phải bug) | VM-DP6 `C:\` | `doctor` báo `disk_free_percent: 13.24`, `disk_free_gb: 9.92/74.89` | Đĩa hệ thống còn trống thấp; `runtime/` chỉ chiếm ~1GB nên không phải nguyên nhân từ dp_program — cần theo dõi ở tầng hạ tầng VM | Ghi nhận, không phải action code |
| F3 | Info (lỗi công cụ của người audit, không phải code dp_program) | Script audit tự viết `audit_monitor.ps1` | `Get-Content -Raw` bị PowerShell tự đính kèm `PSPath`/`PSDrive` reflection object khi `ConvertTo-Json`, làm file phình lên 517MB | Không liên quan tới dp_program — đã xoá sạch script + file rác, xác nhận ổ đĩa VM không bị ảnh hưởng lâu dài | Đã khắc phục |
| — | — | — | **Không tìm thấy bug nào khác trong code dp_program** qua toàn bộ audit (outbox, scheduler, WS lifecycle, missing-pair, SQL, supervisor, health, logging/alerting mới) | — | — |

## 5. Đánh giá từng nhóm

### Live (`core/live/`)
**ĐẠT.** Đọc trực tiếp toàn bộ 8 file hiện tại (`engine.py`, `scheduler.py`, `fetcher.py`, `delivery.py`, `outbox.py`, `runtime.py`, `telemetry.py`, `__init__.py`, 5.936 dòng).
- Outbox (`outbox.py`): state machine `pending→leased→staged→ack` đúng thiết kế write-ahead. Xác nhận bằng code: persist trước enqueue (`_enqueue_or_buffer`), staging fail → `release_for_retry` (không mất row), Fact fail → row giữ `staged` (không ack), crash recovery qua `init()` reset `leased`/`staged`→`pending`, ack theo snapshot chính xác (`staged_snapshot_for_key`) tránh race 2 worker.
- Outbox đầy → pause fetch thật (`_spool_full_blocks_batch()` dự trù chỗ cho cả batch TRƯỚC khi fetch, không đợi ghi mới phát hiện đầy) — xác nhận bằng code + gọi từ `prepare_batch()`.
- 3 API compat cũ (`write()`, `ack_staged_for_key()`, `staged_ids_for_key()`) đã bị xoá thật khỏi `outbox.py` — khớp commit `c11c3602`.
- Missing-pair state machine (`runtime.py::update_missing_pairs()`) — đã hợp nhất thành 1 hàm atomic dưới 1 lock, đúng khớp commit `85f15581 fix(live): make missing-pair recovery state atomic` (sửa đúng bug "2 bộ đếm chồng chéo" phát hiện ở vòng audit review trước).
- WS worker lifecycle (`fetcher.py`): đúng mẫu "1 thread bền vững/group" (`start_worker()` gọi 1 lần); timeout → forced raw-socket close → nếu thread vẫn sống sau grace period → `ws_orphaned_threads`+1, `_requires_process_recycle=True`; 3 batch liên tiếp kẹt → `LiveProcessRecycleRequired` raise → thread excepthook → exit code khác 0 → supervisor phân biệt crash/clean-exit.
- Cơ chế fault-injection socket-stall G0 (`_claim_ws_callback_stall_fault`) có sẵn trong code, one-shot, đúng khớp `docs/OPERATOR_RUNBOOK.md` mục 8.
- Dependency ngược `util/health.py`→`core/live` đã sửa: giờ import `playwright_browser_status` từ `shared/tradingview/diagnostics.py` (module mới, gộp đúng `ConnectivityProbe`).
- Công thức "ngưỡng dữ liệu cũ" trùng lặp giữa historical/live đã hợp nhất vào `shared/freshness.py::stale_after_minutes()`, cả 2 bên gọi đúng 1 hàm.

### Historical (`core/historical/`)
**ĐẠT.** Đọc `pipeline.py` (3 mode runner: `run_full_load`/`run_backfill`/`run_reset_scope`), `runtime_support.py`. `_WarehouseWriteSlot` (nhường quyền ghi cho live qua `wait_for_historical_slot`) đúng thiết kế. `reset` có 2 lớp xác nhận (dry-run preview rồi gõ đúng "RESET"). Chạy thật trong lúc audit: 2 lần gap-repair (286/286 rồi 347/347 pairs, `fail=0` cả 2 lần).

### SQL/Outbox (`shared/warehouse/`)
**ĐẠT.** Đọc `connection.py`, `writer.py`, `reader.py`, `validation.py`, `maintenance.py`, `reconcile.py`, `operation_log.py`.
- `usp_LoadDirect` v4 (`scripts/sql/12_migration_usp_loaddirect_v4_bounded_plan.sql`): `NOT EXISTS` kèm `UPDLOCK, HOLDLOCK` chống race; bảng `Fact_OHLCV` có **UNIQUE CONSTRAINT thật** `(SymbolID, TimeframeID, BarTime)` (`scripts/sql/02_core_tables.sql:325`) — phòng thủ kép đúng chuẩn.
- `purge_staging()`: chỉ xoá khi `IsProcessed=1` **VÀ** khớp cả giá trị OHLC với Fact (không chỉ khớp key) — phòng trường hợp TradingView sửa giá sau khi staging.
- `reconcile.py` tách rõ "in range" (bug thật) khỏi "unsupported calendar" (ngoài phạm vi Dim_Date, không retry, không fail deploy).
- `EXPECTED_CONTRACT_VERSION="4"` khớp `doctor` xác nhận thật trên VM (`"version": "4"`).

### Supervisor (`util/supervisor/`)
**ĐẠT.** Từ chối khởi động khi DB contract sai hoặc lịch historical rỗng — với lý do dẫn evidence sự cố thật ("multi-day Fact_OHLCV staleness"). Backoff mũ tăng có trần, lock-conflict/cancelled có backoff riêng chậm hơn. Test `test_supervisor_restart.py` (29 test) phủ đủ các nhánh backoff/budget/reset.

### Health/Discord (`util/health.py`, `util/notify/`)
**ĐẠT**, gồm cả phần Codex vừa thêm. `health.py` bắt buộc phải có (2 consumer thật: `cli.py doctor/status`, `supervisor` dùng để quyết định restart). Phân biệt đúng `batch_completed_at` (business progress) với `heartbeat` (process liveness) — không nhầm process sống với data đang chảy. Phần mới của Codex: `util/logkit/paths.py` (mỗi process role có file log vật lý riêng, tránh xung đột rotate-khi-đang-mở của Windows), `util/notify/transport.py` (gộp logic gửi Discord đồng bộ dùng chung cho cả `critical_outbox` lẫn `discord.py`), check `log_sinks` mới trong `doctor` (xác nhận mọi sink đang hoạt động, trong ngân sách dung lượng). Test mới (`test_logging_go_gate.py`, 6 test) là fault-injection thật: crash-trong-lúc-import vẫn được ghi nhận, 2 process cùng role không bao giờ share 1 file rotating, sink-setup fail không để lại child orphan.

### Resource stability
**Một phần** — xác nhận qua code: `MAX_BATCH_METRIC_HISTORY=288` cap lịch sử batch metrics, `_DEFERRED_ETL_MAX=5000` cap hàng đợi Fact chờ, log rotation 10MB/5 backup, JSONL cap 10MB/5000 dòng, quarantine cleanup theo tuổi. Nhưng **theo dõi resource 30 phút thật (RSS/thread/handle time-series) không hoàn tất** trong phiên này — xem mục 9-10.

### Cấu trúc code
**ĐẠT.** Không tìm thêm orphan/dead-code nào ngoài những gì đã audit ở phiên trước (đã xoá). Import giữa `fetcher.py`/`delivery.py` dùng qua `runtime` module đúng hướng phụ thuộc core→shared→util.

## 6. Test

- **Baseline cũ (HEAD `85f15581`)**: `353 passed in 21.47s` (đúng khớp báo cáo trước — tự chạy lại, không tin số liệu cũ). Chạy riêng 128 test mục tiêu (outbox/delivery/worker-pool/runtime/scheduler/supervisor/locks/health/log-rotation/critical-outbox) — **128/128 PASSED**, tên test khớp chính xác kịch bản crash-recovery đọc được trong code (vd `test_kill_after_fact_commit_before_ack_recovers_via_restart_reset`, `test_running_with_stale_batch_completed_at_is_fail_even_with_fresh_heartbeat`).
- **Sau khi Codex thêm logging/alerting (HEAD `7d25221d`)**: `384 passed in 32.56s` (+31 test mới, khớp đúng các file test mới/mở rộng). Không có pass giả phát hiện — tên test đều gắn với hành vi cụ thể, đọc nội dung xác nhận assertion có ý nghĩa thật (không phải `assert True`).
- Chạy bằng đúng Python production `C:\Users\Administrator\...\Python312\python.exe`, đúng thư mục `C:\Share\dp_program`, trên chính VM-DP6 — không phải môi trường local khác.

## 7. Commit mới

**Không có commit nào do tôi (Claude) tạo.** Không tìm thấy bug nào cần sửa nên không có finding nào cần debug/fix/commit. 3 commit trong mục 3 đều do Codex tạo (ngoài phạm vi audit này, đã review diff ở mục 3/5).

## 8. Runtime evidence

- **Scheduled Task**: `\SEN05\SEN05 DP Program 24x7`, `State=Running`, `WorkingDirectory=C:\Share\dp_program` — khớp `OPERATOR_RUNBOOK.md`.
- **Process/PID**: xác nhận nhiều lần qua `Win32_Process` — supervisor luôn có `ParentProcessId=1840` (svchost/Task Scheduler), spawn đúng live + historical làm con trực tiếp.
- **Live smoke (700s, tự nhiên)**: 4 batch, `accepted_bars=fact_inserted=staging_rows=139` (khớp tuyệt đối). Batch 4 bị cắt ngang bởi shutdown giữa lúc 2 group chưa nhận đủ session — code tự nhận diện đây là "shutdown đang diễn ra" (log INFO/warning, không phải wedge cần recycle) — "Live feed stopped cleanly", không mất dữ liệu, process thoát sạch.
- **Kill-child có kiểm soát**: kill PID 5072 lúc `11:23:57.237 UTC` (trạng thái `waiting`, không mid-batch) → supervisor phát hiện + respawn PID mới `5088` lúc `11:24:58.099 UTC` → **~61 giây**. `live_restart_count_last_hour` tăng đúng 1. Historical (PID riêng) chạy song song hoàn toàn không bị ảnh hưởng. Fact recovery xác nhận: batch đầu của process mới `accepted=fact_inserted=11` (khớp).
- **Fault-injection socket-stall G0 + resource time-series 30 phút**: **KHÔNG HOÀN TẤT.** Marker tạo lúc `11:30:12` nhưng chưa từng được code claim/kích hoạt trước khi hệ thống bị dừng (~13:13-13:19, trùng lúc Codex làm việc). Đã xoá sạch marker còn sót + file log lỗi do chính script audit tạo ra (mục Findings F3). **Đây là hạng mục cần làm lại** — xem mục 12.
- **Trạng thái cuối (sau khi dọn dẹp, `doctor --json` lúc 14:20:43 UTC)**: `status: "ok"` toàn bộ 15 check. Fact_OHLCV 16.217.715 dòng (tăng ~3.609 dòng trong ~2.5h). `log_sinks`: 3 role đăng ký đúng (`supervisor.5700`, `live.4068`, `historical.3704`), 33 file log, 2.76MB (ngân sách 2GB/3GB warn/fail — dư nhiều). `discord`: supervisor + live có `worker_alive=true`, `consecutive_failures=0`; historical `unexercised` (chưa gửi gì trong phiên chạy này — đúng theo fix `7d25221d`, không phải lỗi). `critical_alerts`: `pending_count=0`. Lock table sạch, không stale.

## 9. Rủi ro còn mở

1. **Wedge → hard-deadline-recycle chưa được chứng minh end-to-end trên production thật trong phiên này** — chỉ có bằng chứng từ đọc code + unit test (`test_wedged_batch_raises_fatal_recycle_signal`, `test_fetch_marks_process_recycle_when_socket_thread_survives_force_close`, `test_controlled_socket_stall_marker_is_exact_and_one_shot`). Cần chạy lại fault-injection thật + quan sát PID đổi sau đúng 3 batch kẹt.
2. **Theo dõi resource (RSS/thread/handle) liên tục 30 phút chưa hoàn tất** — chỉ có snapshot rời rạc (không đủ để kết luận có/không leak dài hạn).
3. Đĩa hệ thống VM-DP6 còn trống thấp (~13%) — rủi ro hạ tầng, không phải rủi ro code, nhưng đáng theo dõi.
4. **Hai AI agent (Claude + Codex) có thể thao tác đồng thời trên cùng VM production mà không có cơ chế điều phối rõ ràng** — đã xảy ra 1 lần trong phiên này (không gây hại thật, nhưng là quy trình cần thống nhất trước lần sau).

## 10. Việc chưa được kiểm chứng trong phiên này

- Reboot VM-DP6 thật (theo đúng yêu cầu, không tự làm nếu chưa xác nhận riêng).
- Soak dài hạn 24-72 giờ (đã thống nhất từ đầu, 30 phút thay thế — nhưng ngay cả 30 phút cũng chưa hoàn tất, xem mục 9.2).
- Đi qua đủ 2 khung giờ lịch historical (11:00 và 22:00 UTC) trong 1 lần quan sát liên tục.
- Discord webhook rotation thật (không cần trong phiên này, không có yêu cầu xoay webhook).
- `historical` role của cơ chế Discord sender mới (Codex) chưa "exercised" thật (chưa từng gửi alert nào trong phiên chạy hiện tại) — cần một tình huống thật kích hoạt (vd lỗi historical) để xác nhận đường gửi này hoạt động, không chỉ ở trạng thái mặc định "chưa từng chạy".

## 11. Khuyến nghị merge/tag

**Khuyến nghị**: sau khi hoàn tất lại mục 9.1-9.2 (fault-injection + 30 phút resource time-series thật, không bị gián đoạn), branch `refactor/v2-streamline` (HEAD `7d25221d`) đủ điều kiện xem xét merge vào `refactor/production-structure` rồi lên `main`. **Không tự merge** — quyết định merge/tag thuộc về Kiệt.

## 12. Action còn lại (đủ cụ thể để làm không cần suy luận)

1. Chọn 1 cửa sổ bảo trì không trùng với bất kỳ AI agent nào khác đang thao tác trên VM-DP6.
2. Tạo lại marker: `Set-Content C:\Share\dp_program\runtime\run\fault_inject_ws_callback_stall_g0.request 'STALL_ONCE' -NoNewline`.
3. Theo dõi liên tục (không để bị gián đoạn) cho tới khi thấy: `ws_orphaned_threads` tăng → sau đúng 3 lần phân loại "stuck" liên tiếp (~15 phút với cấu hình mặc định `WS_LIVE_GROUP_WEDGE_HARD_DEADLINE_BATCHES=3`, batch 5 phút/lần) → `ws_wedged_group_recycles` tăng → PID live đổi → PID mới không giữ stale worker → group còn lại (G1) vẫn ghi Fact bình thường trong suốt thời gian G0 bị fault.
4. Trong cùng cửa sổ đó, lấy time-series RSS/thread-count/handle-count mỗi 4-5 phút trong 30 phút liên tục (dùng script đơn giản, **tránh bug `Get-Content -Raw` + `ConvertTo-Json` gặp phải lần này** — ép kiểu `[string]` tường minh hoặc dùng `[System.IO.File]::ReadAllText()` thay vì `Get-Content`).
5. Sau khi xong: xoá marker/evidence file, xác nhận `git status` sạch, `doctor --json` = `ok`.
6. Xác nhận `historical` Discord sender role thật sự gửi được alert (có thể chờ 1 sự kiện tự nhiên, hoặc tạo tình huống warning/error nhẹ có kiểm soát để "exercise" nó).
7. Theo dõi disk free trên VM-DP6 ở tầng hạ tầng (ngoài phạm vi code dp_program).
8. Thống nhất với Kiệt một quy ước điều phối khi có nhiều AI agent cùng thao tác trên VM-DP6 (vd: file lock/thông báo trước khi SSH can thiệp runtime) để tránh lặp lại tình huống mục 9.4.
