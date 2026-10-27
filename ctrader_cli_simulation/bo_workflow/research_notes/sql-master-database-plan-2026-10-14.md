# Kho dữ liệu SQL master cho kết quả backtest — kế hoạch tổng hợp

Ngày lập: 2026-10-14. Trạng thái: **bước 2–6 ĐÃ TRIỂN KHAI cùng ngày** (người dùng: "hãy triển khai nhé, sau đó backtest thử nghiệm") — xem
**mục 10** cuối file: những gì đã làm, các điểm lệch so với kế hoạch gốc, kết quả kiểm. Phần 1–9 giữ nguyên làm tài liệu thiết kế.
Việc còn lại (bước 7: đồng bộ Redis, dashboard đọc kho, bảng phân tích) vẫn chỉ làm khi người dùng nói "chốt" cho đúng việc đó (quy tắc ở CLAUDE.md).
Tên bảng và tên cột dùng **tiếng Anh**, viết đủ từ (người dùng chốt 2026-10-14). Phần giải thích bằng tiếng Việt.

Ghi chú nguồn: mục nào ghi **(đã đo)** là đã kiểm trên máy này trong các phiên 2026-10-12→14; mục ghi **(chưa kiểm)** là suy luận hoặc nhớ từ ghi chú cũ.
Các điểm móc nối trong `core_engine` được tra bằng graphify (graph `graphify-out/`, 1.613 nút, cập nhật 2026-10-14) rồi đọc lại mã thật.

---

## 0. Tóm tắt

- **Mục tiêu:** một kho SQL (SQL Server Express trên VM-BO20) lưu **đầy đủ** mỗi lần backtest, sao cho dù Redis signal đổi, `.algo`/mã bot đổi, hay engine đổi, mọi kết quả vẫn được giữ lại, biết rõ sinh ra từ phiên bản đầu vào nào, và so sánh được theo ô (chiến lược × symbol × khung).
- **Thiết kế:** 6 bảng: `backtest_run`, `trade`, `daily_equity`, `bot_build`, `signal_snapshot`, `walkforward_decision`. Mỗi lượt chạy trỏ tới đúng phiên bản bot và đúng bản chụp tín hiệu nó đã dùng. File gốc (report, events, log) vẫn giữ ở ngoài, SQL là bản đã bóc tách + hash + đường dẫn.
- **Nguyên tắc vận hành:** chỉ thêm, không sửa; file trên đĩa là nguồn sự thật, SQL là bản sao có thể dựng lại bất cứ lúc nào; một đường trích xuất duy nhất (file → payload → SQL) dùng chung cho nạp tay và nạp tự động.
- **Hiệu chỉnh `core_engine`:** 1 sửa quy ước ngày kết thúc (đang tự mâu thuẫn), 2 chụp mã nguồn `.cs` cùng bản build, 3 chép `events.json` của cTrader, 4 thêm hai module mới (`output_util/run_export.py`, `master_db.py`) và vài hàm thuần, 5 hai điểm móc trong `facilitator.record_result` / `walkforward`. Dashboard (Codex) không sửa.

---

## 1. Bối cảnh

- Kết quả backtest hiện nằm ở `bo_workflow/runs/research/{grid,walkforward}/<experiment>/`: mỗi experiment là một thư mục + một `index.sqlite` riêng (bảng `runs`, 30 cột) + từng lượt chạy có `report.json.gz`, `execution.json`, `spec.json`, `params.cbotset`, (tuỳ) `bot.log`.
- Vấn đề: "ô nào ở kỳ nào" chỉ nhận ra qua tên thư mục; danh sách lệnh nằm trong JSON nén nên không truy vấn được; cùng một ô-kỳ-tham số bị chạy lại ở nhiều experiment; phiên bản bot/tín hiệu/CLI nằm rải rác trong từng file; muốn thêm một chỉ số (cắt nửa năm, luật lỗ ngày FTMO) phải đọc lại hàng trăm file.
- **Ba nguồn đầu vào thay đổi theo thời gian, ta không tự nắm được:**
  1. **Bot (cBot):** `.cs` → build ra `.algo`. Hash `.algo` đổi mỗi lần build; `.algo` **không chứa mã nguồn** (`SourceIncluded: false`, đã đo); không có git (cố ý). Hôm 2026-10-13 cả 3 bot đã được sửa và build lại.
  2. **Tín hiệu Redis (db0 gốc, db1 trend):** OG8 có thể dựng lại hoặc ghi đè quá khứ; Redis không có hồ sơ phiên bản, không có khoá metadata, không có TTL (đã đo). Đã xảy ra thật: Combo đổi 44/44 dấu vân tay giữa 10/10 và 13/10; MACD Break đổi 88/88 trong ngày 13/10 do người dùng gắn lọc trùng hướng ở nguồn (memory `combolegs-bot-and-profile`).
  3. **Engine/broker:** bản CLI tự cập nhật (5.10.18 → 5.10.19 trong 13–14/10, đã đo); **swap rate của broker đổi theo ngày** làm kết quả lệch 1–5% (có khi 16%) giữa hai lần chạy cùng ô (memory `combolegs-bot-and-profile`).
- **Dữ liệu cũ:** toàn bộ `runs/research/grid/` (149 thư mục) đã bị người dùng xoá ngày 2026-10-13 (memory `grid-experiments-cleared-2026-10-13`). Còn: 4 experiment `walkforward/` (dashboard_walkforward_* ×3, `us30_h1_combo_full_wf` 306 lượt), `matrix/`, và 1 experiment mẫu mới `grid/flow_us30_combo_2026_9m` (giữ lại làm ca thử nạp).
- **Hạ tầng:** SQL Server 2025 Express (17.0.1000.7) đã cài trên VM-BO20, instance `localhost\SQLEXPRESS`, đăng nhập Windows (không mật khẩu). ODBC Driver 17/18 có sẵn; **`pyodbc` chưa cài**. Windows Server đang là bản Evaluation, hết hạn **2027-01-12** (đã đo, phải kích hoạt bản chính thức trước ngày đó).

---

## 2. Luồng Input → Process → Output → Storage (đã quan sát trên lượt mẫu)

Lượt mẫu: Combo, US30.cash, H1, 2026-01-01→2026-09-30, db0, KSL Fib1618 / KTP Fib2618, vốn 100.000, profile `ctrader-5.9.16-ticks-approxfx-v1`. Kết quả: 161 lệnh, lãi 16.932,12, PF 1,20, DD equity 18,06%; CLI 73,7 s, 1 lần đăng nhập. Dấu vân tay dự kiến == thực tế.

```
INPUT
  config.yaml + experiment config ──► RunSpec (planner.expand_grid)
  Combo.cs ──(build tay trong GUI)──► Combo.algo (+ manifest: ngày build, 14 tham số, enum)
  Redis db0/db1: LIST + HASH ───────► CSV tín hiệu (signal_trans.resolve_signal_path)
  cache tick .zticks (theo ngày) ───► CLI tự đọc (US30.cash: 273 file, 133,6 MB cho kỳ này)
PROCESS  (facilitator.run_experiment)
  1. chụp .algo vào inputs/<sha>.algo, chụp CSV vào inputs/<sha>.csv
  2. run_key = hash(spec, algo_sha, signal_window_sha, engine_profile)   [param_hash hiện tại]
  3. ghi cli_launches (hạn ngạch/ngày) → claim dòng index.sqlite
  4. cli_runner.run_backtest → spawn ctrader-cli.exe backtest (1 lần đăng nhập)
        └─► algohost.exe --title=<sha256 .algo>  (chạy bot, bot đọc CSV qua SignalFilePath)
  5. cTrader ghi: --report-json (vào thư mục .work_* tạm) + thư mục riêng
        Documents\cAlgo\Data\cBots\<sha256 .algo>\<guid>\Backtesting\{events.json, log.txt, report.html, parameters.cbotset}
OUTPUT / STORAGE (hôm nay)
  record_result → store.record: spec.json, execution.json, params.cbotset, report.json.gz,
                  bot.log (từ stdout, chỉ khi keep_logs hoặc lượt lỗi), cập nhật index.sqlite
                  xoá .work_* ;  events.json của cTrader KHÔNG được lưu
```

### Bản đồ dữ liệu: ai khai, mức ghi nhận, lưu ở đâu

| Dữ liệu | Nguồn / ai khai | Mức | Hôm nay lưu ở | Đích SQL |
|---|---|---|---|---|
| Mã nguồn `.cs` (37 KB) | ta tự đọc/băm | gián tiếp | **không lưu** | `bot_build.source_code` |
| `.algo` (14 KB), manifest (ngày build, 14 tham số, enum) | bot tự khai hash ở 4 nơi: dòng đầu `bot.log`, `report.main.cBotName`, `algohost --title`, tên thư mục `Data\cBots` | trực tiếp | `inputs/*.algo` | `bot_build` |
| Tín hiệu Redis (`bartime, atr, entry, signal`) | ta chụp lúc chạy; Redis không khai phiên bản | gián tiếp | `inputs/*.csv` | `signal_snapshot` |
| Kênh db0/db1 | chỉ trong `experiment.json` (`redis_profile`) | gián tiếp | `experiment.json` | `signal_snapshot.channel` |
| Tham số, engine profile | spec | trực tiếp | `spec.json`, `params.cbotset` | `backtest_run` |
| Bản CLI | `ctrader-cli --version` | trực tiếp | `execution.json` | `backtest_run.cli_version` |
| Thông số symbol (lot, step, swap, commission, leverage) | cTrader khai trong `report.usedSymbols` | trực tiếp | `execution.json` | `backtest_run.symbol_spec_json` + hash |
| Tick giá | cache `.zticks` theo ngày | gián tiếp (chỉ liệt kê) | `%APPDATA%\Spotware\Cache` | không lưu nội dung |
| Tóm tắt, từng lệnh, equity (theo giờ) | cTrader report | trực tiếp | `report.json.gz` | `backtest_run`, `trade`, `daily_equity` |
| Vòng đời lệnh chờ (768 sự kiện/lượt mẫu) | cTrader `events.json` | trực tiếp | **chỉ ở `Data\cBots`** | file `events.json.gz` + `trade.close_reason` |
| Bộ đếm bot (loaded, placed, filled, expired…) | dòng OnStop trong log | trực tiếp | `execution.json` | `backtest_run.bot_counters_json` |
| Lúc Redis đổi, vì sao | — | **không ghi được** | — | (giai đoạn sau: job đồng bộ + manifest OG8) |

### Điều đã đo và ảnh hưởng thiết kế
1. **`--end` chạy trọn ngày end** (điểm equity cuối 30/09 23:59:51; lệnh cuối 30/09 12:30 do tín hiệu 30/09 10:00). Nhưng code đang gọi `end` là mốc "loại trừ" và hash tín hiệu chỉ băm các dòng `< end` → tín hiệu của ngày cuối **không nằm trong định danh** lượt chạy. Xem mục 6.A.
2. **cTrader tự ghi một bản kết quả riêng cho mỗi lần chạy CLI** và không tự dọn (53 thư mục cho riêng một hash `.algo`). Không có cờ CLI nào chỉ định nơi ghi `events.json` (đã đo help của `backtest`: chỉ có `--report`, `--report-json`, `--data-dir`...). Với lệnh `optimize`, thư mục pass có đường dẫn rõ ràng nên lấy trực tiếp được.
3. `bot.log` của ta dựng từ stdout CLI nên khác `log.txt` gốc đúng ở 326 ký tự `→` (ta thay bằng khoảng trắng). Vô hại, không sửa.
4. Report: 99% dung lượng là `equity.points` (4.434 điểm theo giờ H1 = 512 KB / 570 KB); `history` 51 KB; trường `comment` của lệnh luôn rỗng (lý do đóng chỉ có ở `events.json`/log).
5. Redis (đã đo 2026-10-14): db0 có 169.548 tín hiệu, db1 có 84.237, mỗi db 176 list; ~40 byte/dòng ⇒ một lần chụp cả hai kênh ≈ 10 MB chưa nén. Có `notify-keyspace-events = g$hzK` (sự kiện HASH bật, LIST không) — chỉ ghi nhận làm khả năng cho giai đoạn sau, **(chưa kiểm)**.

---

## 3. Các quyết định đã chốt

1. SQL Server Express trên VM-BO20, DB `bo_research`, đăng nhập Windows. Tên **tiếng Anh**, đủ từ.
2. **6 bảng** (không phải 25): đã rà từng bảng với câu hỏi "trả lời được câu nào mà cách khác không trả lời được" — bỏ ref/ana/period/experiment/run_param/run_counter/run_file/run_attempt/order_event/run_event, gộp bot_source+bot_build và 4 bảng tín hiệu thành 1.
3. **Tín hiệu:** lưu nguyên bản chụp (nén), dùng chung khi cùng hash; so sánh hai bản chụp bằng hàm Python, không bằng truy vấn SQL.
4. **`events.json`:** chép vào kho cùng report (`events.json.gz`), **chưa** có bảng `order_event`.
5. **Thông số symbol (swap):** ghi sau khi chạy (cột + hash) và gắn cờ khi khác lượt đã chạy; **không** nằm trong khoá trước-khi-chạy vì chỉ biết sau khi chạy (cần một lệnh CLI riêng để biết trước — `ctrader-cli symbols` có tồn tại nhưng **chưa kiểm** nó có trả swap không).
6. **Sửa quy ước ngày kết thúc** của hash tín hiệu (mục 6.A).
7. **Walk-forward:** mọi lượt trong walk-forward là dòng bình thường của `backtest_run` (kèm `window_name`, `zone`); thêm bảng nhỏ `walkforward_decision`.
8. `daily_equity` lưu cho **mọi** lượt (DSR/PBO cần chuỗi lãi theo ngày của cả 100 lượt mỗi cửa sổ).
9. Tài liệu đặt trong `research_notes` (file này). Dashboard thuộc Codex, không sửa. Hiện không có phiên Claude nào khác đang sửa `core_engine` (người dùng xác nhận 2026-10-14).

---

## 4. Thiết kế dữ liệu: 6 bảng

### 4.1 Sơ đồ liên kết

```
 bot_build ◄──────────┐
 signal_snapshot ◄────┼──  backtest_run  ◄──┬── trade           (1 dòng / lệnh đã đóng)
                      │          ▲          └── daily_equity    (1 dòng / ngày giao dịch Prague)
                      │          │
 walkforward_decision ┴──────────┘   (train_run_id, test_run_id → backtest_run)
```
- `backtest_run` là trung tâm: trỏ tới `bot_build` (code nào) và `signal_snapshot` (tín hiệu nào); thông số broker và cờ engine nằm ngay trong dòng.
- So sánh hai lượt "cùng ô" (cùng chiến lược, symbol, khung, kỳ, tham số) = xem con trỏ/hash nào khác. Khác đúng một thứ thì biết nguyên nhân.

### 4.2 `bot_build` — một bản build bot
| Cột | Kiểu | Ý nghĩa |
|---|---|---|
| `bot_build_id` | INT IDENTITY, PK | |
| `strategy` | NVARCHAR(30) | `combo` / `macross` / `macdbreak` |
| `algo_sha256` | CHAR(64), UNIQUE | hash file `.algo` (= tên thư mục `Data\cBots`) |
| `algo_file` | VARBINARY(MAX) | **chính file `.algo`** (để chạy lại bản cũ) |
| `manifest_json` | NVARCHAR(MAX) | manifest giải nén: ngày build, 14 tham số, enum |
| `build_time_utc` | DATETIME2 | `Timestamp` trong manifest |
| `source_sha256` | CHAR(64) NULL | hash `.cs` (NULL nếu dữ liệu cũ không có) |
| `source_code` | NVARCHAR(MAX) NULL | **toàn văn `.cs`** |
| `source_captured_at_utc` | DATETIME2 NULL | lúc chụp `.cs` |
| `first_seen_utc` | DATETIME2 | |
| `note` | NVARCHAR(500) NULL | |

### 4.3 `signal_snapshot` — một bản chụp list tín hiệu Redis
| Cột | Kiểu | Ý nghĩa |
|---|---|---|
| `signal_snapshot_id` | INT IDENTITY, PK | |
| `channel` | NVARCHAR(10) | `original` (db0) hoặc `trend` (db1) |
| `strategy`, `symbol`, `timeframe` | NVARCHAR | symbol theo tên cTrader (US30.cash) |
| `redis_key` | NVARCHAR(100) | vd `L_PastSignal_US30_H1_COMBO` |
| `content_sha256` | CHAR(64) | hash toàn bộ CSV (= `execution.inputs.signal_sha256`) |
| `first_bar_time`, `last_bar_time` | DATETIME2 | |
| `row_count` | INT | |
| `rows_compressed` | VARBINARY(MAX) | CSV nén gzip (`bartime,atr,entry,signal`) |
| `captured_at_utc` | DATETIME2 | |
| UNIQUE | (`channel`,`strategy`,`symbol`,`timeframe`,`content_sha256`) | cùng nội dung → dùng lại, không thêm |

### 4.4 `backtest_run` — một lần backtest
| Nhóm | Cột |
|---|---|
| Định danh | `backtest_run_id` BIGINT IDENTITY PK; `run_key` CHAR(64) UNIQUE (= `param_hash` sau khi sửa 6.A); `batch_name`; `window_name` NULL (vd `wf01_is`); `zone` NULL (`train`/`oos`/`single`) |
| Ô | `strategy`, `symbol`, `timeframe`, `start_date`, `end_date_requested` (giá trị đã truyền cho `--end`, **gồm cả ngày này**), `last_data_time` (điểm equity cuối thật), `initial_balance` |
| Tham số | `ksl_atr_multiple`, `ktp_atr_multiple` (hệ số thật ×ATR, không phải số thứ tự enum), `risk_percent`, `leg_count`, `parameters_json` (đủ 13 tham số) |
| Phiên bản | `bot_build_id` FK, `signal_snapshot_id` FK, `signal_window_sha256`, `engine_profile`, `engine_json`, `cli_version`, `symbol_spec_json`, `symbol_spec_sha256` |
| Trạng thái | `status`, `failure_code`, `failure_reason`, `period_ok`, `signal_ok`, `approx_fx`, `margin_rejections`, `is_invalid` (cờ vô hiệu), `invalid_note` |
| Kết quả cTrader | `net_profit`, `profit_factor`, `total_trades`, `win_rate`, `max_equity_drawdown_percent`, `max_balance_drawdown_percent`, `ending_balance` |
| Bot | `bot_counters_json` (loaded, placed, filled, pending_expired, reversed…) |
| Thời gian | `started_utc`, `ended_utc`, `wall_seconds`, `host` |
| File gốc | `files_directory`, `report_sha256`, `events_sha256`, `bot_log_sha256`, `native_directory` (thư mục cTrader, nếu nối được) |
| Kiểm toán | `saved_at_utc` |

### 4.5 `trade` — một lệnh đã đóng
PK (`backtest_run_id`, `trade_number`). Cột: `direction` (`buy`/`sell`), `leg_label` (vd `ComboCsvPending_L1`), `entry_time_utc`, `close_time_utc`, `entry_price`, `close_price`, `volume`, `gross_profit`, `commission`, `swap`, `net_profit`, `balance_after`, `pips`, `close_reason` (`stop_loss` / `take_profit` / `closed_by_bot`, lấy từ `events.json`; NULL nếu không có).

### 4.6 `daily_equity` — một ngày của một lần chạy
PK (`backtest_run_id`, `trading_day`). Cột: `trading_day` (DATE, ngày theo **giờ Prague**), `balance_at_midnight`, `balance_at_close`, `lowest_equity`, `highest_equity`, `point_count`. Dùng cho luật lỗ ngày FTMO (5% vốn gốc 2-Step / 3% 1-Step so với balance 00:00 Prague), DD tích lũy, DSR/PBO, Monte Carlo.

### 4.7 `walkforward_decision` — một quyết định chọn tham số của một cửa sổ và một luật
PK (`walkforward_name`, `symbol`, `timeframe`, `window_number`, `selection_rule` — đặt tên này vì `rule` là từ khoá dành riêng của SQL Server). Cột: `strategy`, `train_start_date`, `train_end_date`, `test_start_date`, `test_end_date`, `objective`, `candidate_count`, `eligible_count`, `plateau_score` NULL, `selected_ksl_atr_multiple`, `selected_ktp_atr_multiple`, `selected_parameters_json`, `train_run_id` FK, `test_run_id` FK NULL.
Nguồn: `selection.json` (mỗi quyết định có `rule`, `candidates`, `eligible`, `plateau_score`, `selected_params`, `train_run_id`, `train_window`/`test_window`, các ngày) + thông tin cửa sổ trong `index.sqlite` (`window`, `zone`). Experiment mẫu `us30_h1_combo_full_wf`: 306 lượt = 3 cửa sổ × (100 lượt huấn luyện + 2 lượt kiểm, mỗi luật `plateau`/`best` một lượt). `test_run_id` được tìm bằng khớp cửa sổ + tham số được chọn **(cần kiểm khi nạp thử)**; nếu hai luật chọn cùng tham số thì cùng trỏ về một lượt.

### 4.8 View (đặt tên `v_...`, viết khi nạp dữ liệu xong)
`v_run_overview`; `v_cell_half_year_profit` (lãi theo nửa năm/quý/năm theo ngày đóng lệnh); `v_compare_same_cell` (ghép cặp lượt cùng ô khác phiên bản, chỉ rõ khác bot / signal / thông số symbol / CLI / engine); `v_durable_cells` (bảng bền: lãi cả 3 năm, ≥5/6 nửa năm, không margin, PF ≥ 1,10, DD ≤ 20%); `v_ftmo_daily_loss`; `v_walkforward_oos_chain` (nối lệnh các lượt kiểm theo thời gian).

### 4.9 Quy tắc chung
- **Chỉ thêm, không sửa kết quả đã xong.** Lượt sai → `is_invalid = 1` + `invalid_note`.
- Mọi thời gian lưu UTC; riêng `trading_day` theo giờ Prague.
- `run_key` = hash(spec chuẩn hoá, hash `.algo`, hash tín hiệu **gồm cả ngày end**, engine profile) — giữ công thức hiện tại, chỉ sửa ranh giới ngày. Bản CLI **không** nằm trong khoá (5.10.16 và 5.10.18 đã đo giống nhau từng bit); thông số symbol **không** nằm trong khoá trước-khi-chạy (xem 3.5).
- Hash `.algo` đổi mỗi lần build kể cả khi mã không đổi → khoá bảo thủ (build lại = coi là lượt mới). `source_sha256` cho biết "build lại, mã y hệt" với "mã đổi".

---

## 5. Quy trình lưu sau mỗi lượt chạy

Một giao dịch SQL duy nhất; lỗi giữa chừng → hoàn tác toàn bộ; file gốc vẫn còn nên chạy lại an toàn (idempotent theo `run_key`).

1. Kiểm `run_key`: đã có → dừng, không ghi đè.
2. Thêm `bot_build` và `signal_snapshot` nếu hash chưa có (dùng lại nếu có).
3. Thêm 1 dòng `backtest_run` trỏ tới hai bản trên.
4. Thêm `trade` (lượt mẫu: 161 dòng) và `daily_equity` (234 dòng). ≈ 400 dòng bảng con mỗi lượt.
5. Với walk-forward: sau khi cả 306 lượt vào, thêm các dòng `walkforward_decision` (3 cửa sổ × 2 luật = 6 dòng).
6. File gốc nén vẫn giữ ngoài: `report.json.gz`, `events.json.gz` (mới), `bot.log` (nếu có). SQL giữ phần bóc tách + hash + đường dẫn.

**Một đường trích xuất duy nhất:** `output_util/run_export.py` đọc file của một lượt (`execution.json`, `spec.json`, `report.json.gz`, `events.json.gz`, `inputs/*`) → trả về một `payload` thuần (dict); `master_db.py` nhận `payload` → SQL. Nạp dữ liệu cũ, nạp tay, và nạp tự động sau mỗi lượt đều đi qua cùng hai hàm này, nên không thể lệch nhau.

**SQL hỏng không được làm hỏng lượt chạy:** lỗi khi ghi SQL chỉ ghi cảnh báo; script nạp bù chạy lại sau.

---

## 6. Hiệu chỉnh `bo_workflow/core_engine` (tra bằng graphify, đọc lại mã thật)

### 6.A Quy ước ngày kết thúc (làm trước, nhỏ, độc lập)
Hiện có **hai cách hiểu mâu thuẫn**: backtest đơn/grid gọi `end` là mốc loại trừ (`models.py:291` `"date_convention": "end_exclusive"`, comment ở `cli_runner.py:380`, `evidence.py:156`, `signal_trans.py:231`, test `test_end_date_is_exclusive_and_matches_gui` ở `test_core_engine.py:237`, thông báo "end is exclusive" ở `dashboard/actions.py:104`), trong khi walk-forward đã dùng `WINDOW_CONVENTION = "last_trading_day_inclusive_v2"` (`walkforward.py:70`) và lượt mẫu chứng minh cTrader chạy trọn ngày `--end`.

| Việc | Chỗ sửa | Ghi chú |
|---|---|---|
| Băm tới hết ngày `end`: `day < end` → `day <= end`; sửa docstring | `signal_trans.signal_window_sha256` (L229) | graph: được gọi từ `run_experiment` (`facilitator.py` ~L296–302) và `input_fingerprint` (~L123) |
| Đổi nhãn `"end_exclusive"` → `"end_inclusive"` | `models.RunSpec.canonical` (L291) | `RunSpec` có 63 liên kết (21 ở `test_core_engine.py`); **mọi `param_hash` đổi** (chủ đích) |
| Sửa comment sai | `cli_runner.py:380`, `evidence.py:156`, `signal_trans.py:231`; test `:200-201` | |
| Đổi tên test (`test_end_date_is_exclusive_and_matches_gui` → vd `test_end_date_goes_straight_to_cli_end`), **giữ nguyên hành vi** | `test_core_engine.py:237` | `--end` truyền thẳng + `period_ok` so với endDate của report vẫn đúng |
| Test mới: tín hiệu của ngày `end` nằm trong hash | `test_core_engine.py` | |
| `WINDOW_CONVENTION` giữ nguyên | `walkforward.py:70` | đã "inclusive" |

Hệ quả: dấu vân tay/cache của 3 file `runs/pipeline_locks/*.lock.json` và 4 experiment `walkforward/` cũ sẽ không khớp → chỉ còn đọc, không chạy tiếp được (cơ chế khoá hiện có, đúng như test `test_window_convention_is_written_into_the_locked_experiment`). `dashboard/actions.py` gọi `facilitator.input_fingerprint` ở 3 hàm (`prepare_single_backtest_session` L137, `prepare_grid_search_session` L321, `prepare_walkforward_session` L511) — chỉ giá trị hash đổi; thông báo "end is exclusive" (L104) là của Codex → **báo, không sửa**. Cũng **chưa kiểm** GUI cTrader có chạy trọn ngày "To" giống CLI không.

### 6.B Chụp mã nguồn `.cs` cùng bản build
- `config.yaml` + `config.example.yaml`: mỗi strategy profile thêm `source: <đường dẫn .cs tương đối ROBOTS_DIR>` (3 profile: `Combo/Combo/Combo.cs`, `MA Cross/MA Cross/MA Cross.cs`, `MACD Break/MACD Break/MACD Break.cs`).
- `configuration.StrategyProfile` (L98): thêm `source_path: Path | None`; `load_strategy_profile` (L237–264) đọc `raw.get("source")`. Thêm hàm đọc manifest `.algo` (ngày build) cạnh `validate_metadata`.
- `facilitator.run_experiment` cạnh L262: `store.snapshot(profile.source_path)` (hàm `snapshot` đã dùng đuôi file nên tạo `inputs/<sha>.cs`); cảnh báo nếu `.cs` mới hơn ngày build `.algo` (algo cũ hơn mã).
- `store.record` → `execution.json.inputs.source_sha256`.

### 6.C Chép `events.json` của cTrader
- `cli_runner`: hàm mới `find_native_output(...)` — tìm thư mục `Data\cBots\<algo_sha256>\<guid>` của đúng lượt chạy: lọc theo thời gian (thư mục tạo giữa `started` và `ended`), rồi **so nội dung `parameters.cbotset`** (đã đo: cùng khoá/giá trị với `params.cbotset` của ta, chỉ khác thụt dòng), rồi **kiểm bằng số lệnh/lãi ròng so với report**; không duy nhất hoặc không khớp → không chép, ghi cờ `events_missing` (không đoán).
- `cli_runner.run_backtest` (L508): sau khi CLI kết thúc, chép `events.json` sang `work_dir`; `_read_pass` (L843, nhánh `optimize`, đường dẫn pass rõ ràng) chép tương tự.
- `models.RunResult`: thêm `events_json`, `native_dir`. `store.record` (L380): nén ra `run_dir/events.json.gz`; `execution.json` thêm `native_dir`, `events_sha256`. `ExperimentStore.record` chỉ có **một** nơi gọi production (`facilitator.record_result` L383) + 2 test (`test_core_engine.py:1338, 1788`).
- Đường gốc `Data\cBots`: thêm vào `config.yaml` (mục `ctrader:` → `data_dir`), không để cứng trong code.

### 6.D Hai module mới và vài hàm thuần
- `core_engine/output_util/run_export.py` (**mới**, thuần đọc file, không SQL): `run_payload(experiment_dir, run_dir)` → dict gồm dòng `backtest_run`, danh sách `trade`, `daily_equity`, nội dung `bot_build`/`signal_snapshot` (đọc từ `inputs/`).
- `core_engine/master_db.py` (**mới**, chỉ SQL, dùng `pyodbc`, Windows auth): `connect`, `ensure_schema` (chạy `bo_workflow/sql/001_schema.sql`, idempotent), `find_run(run_key)`, `save_run(payload)` (một giao dịch), `save_walkforward_decisions(...)`, `import_experiment(experiment_dir)` (nạp bù/nạp dữ liệu cũ). **Không import `facilitator`/`cli_runner`** (quy ước ranh giới module, tránh import ký hiệu `_gạch_dưới` của file khác).
- `output_util/evidence.py` (đã có `read_report`, `metrics`, `bot_summary`, `validity_flags`, `environment`…): thêm hàm thuần `trades(report, events)` (gồm `close_reason`), `symbol_spec_and_hash(environment)`, `last_data_time(report)`.
- `models.py`: thêm hàm công khai `daily_equity_rows(points, timezone, initial)` bọc `_points_by_day` (đã có sẵn, dùng giờ Prague; hiện là hàm riêng tư nên **không** import từ file khác). `daily_balance_and_floor`/`daily_returns_by_day` đã dùng bởi `montecarlo`/`readout` — không đổi.
- `configuration.py`: `load_master_db_profile()`; `config.yaml`: mục `master_db:` (`enabled`, `server`, `database`).

### 6.E Hai điểm móc trong luồng chạy
- `facilitator.record_result` (L376; graph: chỉ được gọi từ `one()` L442 và `run_box()` L492 → móc một chỗ là phủ cả `backtest` lẫn `optimize`): sau `store.record(...)`, nếu `master_db` bật → `run_payload(...)` rồi `master_db.save_run(...)` trong `try/except` (không làm hỏng lượt).
- Giai đoạn sau: trong `one()` trước `reserve_launch`/`claim` (L405–409), nếu `master_db.find_run(run_key)` đã có và không `force` → bỏ qua, **không tốn lượt CLI** (đây là phần "đã chạy chưa").
- `optimize/walkforward.run_walkforward`: sau khi ghi `selection.json` (~L316–318) → `save_walkforward_decisions`.

### 6.F Không đụng
`dashboard/` (Codex; vẫn đọc `index.sqlite` cũ), logic chọn tham số walk-forward, `montecarlo`/`dsr`/`pbo`/`readout` (vẫn đọc file), cách `bot.log` dựng từ stdout, cơ chế `ProtocolStore` (hạn ngạch CLI/ngày, trần 2000), và `index.sqlite` hiện có (file store vẫn là nguồn sự thật).

### 6.G Kiểm thử
Bộ test hiện có 239 test (memory `combolegs-bot-and-profile`); `DailyCliBudgetTests` đang hỏng từ trước vì còn khẳng định trần 1500 trong khi trần đã là 2000 — sửa riêng, không thuộc kế hoạch này. Thêm: test ngày `end` trong hash (6.A); `find_native_output` với thư mục giả (khớp / không khớp / nhiều ứng viên); `run_payload` từ report mẫu (Σ`trade.net_profit` = `net_profit`; số `trade` = `total_trades`); `master_db` với DB thử (bỏ qua nếu không kết nối được SQL).

---

## 7. Các bước triển khai (mỗi bước chờ "chốt" riêng)

| # | Việc | Chạm vào | Kiểm bằng gì |
|---|---|---|---|
| 1 | Tài liệu này (đã viết) | `research_notes/` | người dùng duyệt |
| 2 | **6.A** sửa quy ước ngày kết thúc | `signal_trans`, `models`, comment, test | test mới + chạy lại bộ test |
| 3 | **6.B + 6.C + 6.D (phần thuần)**: chụp `.cs`, chép `events.json`, `run_export`, hàm thuần; **chạy lại lượt mẫu 1 lần** để có experiment đúng quy ước mới | `configuration`, `cli_runner`, `store`, `facilitator`, `evidence`, `models`, `config.yaml` | lượt mẫu: 768 sự kiện, 161 lệnh khớp; `inputs/*.cs` có mặt |
| 4 | Cài `pyodbc`, tạo DB `bo_research`, `sql/001_schema.sql`, `master_db.py`, importer; nạp lượt mẫu và `us30_h1_combo_full_wf` (306 lượt, dữ liệu cũ nên thiếu bot source/phiên bản đầy đủ) | máy BO20, `master_db.py` | từng số khớp file gốc; equity theo ngày Prague khớp `daily_balance_and_floor`; Σ`net` = `net_profit` |
| 5 | Viết các view (4.8) | SQL | tái tạo đúng các kết quả đã công bố (vd danh sách "bền" của kiểm mù 2024 khi có lại dữ liệu) |
| 6 | **6.E** hai điểm móc tự ghi + kiểm "đã chạy chưa"; chạy thử một đợt nhỏ (vd US30 Combo H1–H4 × 2 kênh) | `facilitator`, `walkforward` | đợt thử: SQL khớp file; so được db0 với db1 bằng `v_compare_same_cell` |
| 7 | Sau cùng, mỗi việc chốt riêng: job đồng bộ Redis chỉ-đọc (chụp tín hiệu độc lập với backtest, phân loại "nối thêm / sửa quá khứ / dựng lại") + nhờ OG8 publish manifest (`git rev-parse HEAD`, cờ chưa commit, tham số, khoảng dữ liệu, checksum); dashboard đọc kho mới; nhóm bảng phân tích (`candidate`, `check_result`, `decision`) | | |

---

## 8. Rủi ro và điểm chưa kiểm

- **Nối thư mục `Data\cBots` với lượt chạy** (6.C) là phần duy nhất dựa trên heuristic (thời gian + nội dung `parameters.cbotset` + đối chiếu số liệu). Chưa thử với chạy song song nhiều lượt cùng lúc, chưa kiểm `events.json` có đủ sự kiện cho lệnh đảo chiều phức tạp, chưa kiểm `events.json` của nhánh `optimize`. Phương án an toàn: không nối được thì bỏ qua và ghi cờ.
- cTrader có thể dọn `Data\cBots` → chỉ an toàn khi đã chép `events.json.gz` về kho lúc chạy.
- **Giới hạn dung lượng SQL Server Express** (10 GB mỗi database ở các bản trước; nhớ là bản 2025 cao hơn nhưng **chưa kiểm**). Ước lượng: ~400 dòng/lượt + vài chục KB cho `signal_snapshot` dùng chung; kết quả tối ưu hàng nghìn tổ hợp vẫn nhỏ (mỗi 100 lượt ≈ 40.000 dòng).
- **Windows Server Evaluation hết hạn 2027-01-12**: máy sẽ tự tắt định kỳ nếu không kích hoạt; nên sao lưu DB (`.bak`) định kỳ sang ổ khác.
- Đổi hash (6.A) làm 4 experiment `walkforward/` cũ và 3 file khoá `pipeline_locks/` chỉ còn đọc được.
- Ca đồng thời: có phiên Claude khác làm việc trên cùng repo (OF10, legs, 3 bot) — trước khi sửa `core_engine` xác nhận lại không ai đang sửa cùng file.
- Không ghi nhận được: lúc Redis đổi và lý do (đến giai đoạn 7); nội dung tick (chỉ liệt kê file cache); mã nguồn bot từ `.algo` (phải chụp `.cs` lúc chạy — lượt nào không có `.cs` thì `source_code` rỗng).
- `symbol_spec` biết trước khi chạy: cần kiểm `ctrader-cli symbols` có trả swap không.

---

## 9. Phụ lục

**Lượt mẫu** (`runs/research/grid/flow_us30_combo_2026_9m`, giữ lại làm ca thử nạp):
bot `Combo.algo` sha256 `c242e0d4…` (13.919 B, build 2026-10-13 11:08Z), `Combo.cs` sha256 `a97a33f0…` (37.005 B); tín hiệu `L_PastSignal_US30_H1_COMBO` 1.615 dòng (2022-01-03 → 2026-10-13, trong kỳ chạy 223 dòng), hash file `7ad83bfb…`, hash cửa sổ (quy ước cũ) `0e767e72…`; 13 tham số; 4.434 điểm equity (234 ngày UTC có dữ liệu); 161 lệnh (66 thắng); 768 sự kiện (Create Stop Order 223, Stop Order Filled 161, Order cancelled 62, Stop Loss Hit 63, Take Profit Hit 57, Position closed 41); bộ đếm bot: loaded 1.615, processed 223, placed 223, filled 161, pending_expired 53, reversed 50; CLI 5.10.19.54298.

**File liên quan:** memory `backtest-io-flow-observed-2026-10-14`, `grid-experiments-cleared-2026-10-13`, `combolegs-bot-and-profile`, `bo-workflow-backtest-pipeline`, `ctrader-cli-backtest-optimize-facts`, `redis-trend-channel-vs-original`, `ftmo-account-rules-and-leverage`, `report-per-cell-not-totals`.
Quy tắc phân tích: luôn theo đủ ba yếu tố chiến lược + symbol + khung (đơn vị là ô), không dùng số tổng gộp làm kết luận.

---

## 10. Đã triển khai (cập nhật 2026-10-14)

Người dùng cho phép triển khai và chạy backtest thử ("hãy triển khai nhé, sau đó tiến hành backtest thử nghiệm với us30 combo 9 tháng đầu 2026 với default tham số"). Bộ test: **252 test**, chỉ còn đúng 1 lỗi có từ trước (`DailyCliBudgetTests` còn khẳng định trần 1500 trong khi trần là 2000 — chưa sửa, ngoài phạm vi).

### File mới
| File | Vai trò |
|---|---|
| `core_engine/output_util/run_export.py` | `run_payload` (file một lượt → payload) và `walkforward_decisions` (selection.json → quyết định kèm run_key). Thuần đọc file, không SQL |
| `core_engine/master_db.py` | Toàn bộ SQL: `connect`, `ensure_database`, `ensure_schema`, `find_run`, `save_run` (một giao dịch, không ghi đè), `save_walkforward_decisions`, `import_experiment` (nạp/nạp bù, idempotent), `drop_database` (chỉ cho test) |
| `sql/001_schema.sql`, `sql/002_views.sql` | 6 bảng và 6 view (`v_run_overview`, `v_cell_half_year_profit`, `v_compare_same_cell`, `v_durable_cells`, `v_ftmo_daily_loss`, `v_walkforward_oos_chain`), chạy lặp lại được |
| `tests/test_master_db.py` | Test phần trích xuất, `find_native_output`, `store.record` chép events, `run_payload`, và các test chạm SQL Server thật (database thử `bo_research_test`, tự tạo/xoá, tự bỏ qua nếu không có SQL) |

### Đã sửa
- **6.A** `signal_trans.signal_window_sha256`: băm tới HẾT ngày end (`<=`); `models.RunSpec.canonical`: `end_exclusive` → `end_inclusive`; comment sai ở `cli_runner`/`evidence`; test cũ `..._is_exclusive_...` đổi tên `test_end_date_goes_straight_to_cli_end`, test `SignalWindowHashTests` đổi sang quy ước mới. Kiểm chéo: hash cũ với end=01/10 (loại trừ) == hash mới với end=30/09 (gồm) trên cùng CSV.
- **6.B** `configuration`: `StrategyProfile.source_path` (khai `source:` trong config.yaml cho 3 bot), `read_algo_manifest`, `ctrader_data_dir`, `load_master_db_profile`, `DEFAULT_REDIS_PROFILE` chuyển từ facilitator về đây; `facilitator.run_experiment` chụp `.cs` vào `inputs/<sha>.cs` và ghi `source_sha256`/`source_newer_than_algo` vào `execution.json`.
- **6.C** `cli_runner.find_native_output` (nối thư mục `Data\cBots\<sha>\<guid>\Backtesting` theo thời gian + nội dung `parameters.cbotset`; không đúng 1 ứng viên → None), `run_backtest` và nhánh `optimize` (`_read_pass`) chép `events.json`; `models.RunResult` thêm `events_json`, `native_dir`; `store.record` nén `events.json.gz`, ghi khối `native` (thư mục, sha256, `events_verified`) vào `execution.json`.
- **6.D** `evidence`: `events_match_report`, `trades`, `symbol_spec_and_hash`, `last_data_time`; `models.daily_equity_rows` (cùng `_points_by_day` với `daily_balance_and_floor`).
- **6.E** `facilitator.record_result` → `_save_to_master_db` (chỉ khi `master_db.enabled`; import muộn; lỗi SQL chỉ cảnh báo; kết quả có khoá `master_db` {saved/already/error}); `walkforward.run_walkforward` → `_save_decisions_to_master_db` sau khi các lượt kiểm xong.
- **Cấu hình:** `config.yaml` + `config.example.yaml` thêm `source:` mỗi bot, `ctrader.data_dir`, `master_db` (`enabled`, `server`, `database`, `driver`). `config.yaml` bật `enabled: true`; bản mẫu để `false`.

### Hạ tầng đã tạo trên máy BO20
- `pip install pyodbc` (5.3.0). Database thật **`bo_research`** trên `localhost\SQLEXPRESS` (RECOVERY SIMPLE), đủ 6 bảng + 6 view. Dữ liệu cũ KHÔNG nạp vào DB thật (xem dưới).

### Điểm lệch so với kế hoạch gốc (nói rõ)
1. Cột `rule` của `walkforward_decision` đổi thành **`selection_rule`** (`rule` là từ khoá dành riêng của SQL Server).
2. **Hai lượt kiểm của hai luật là HAI lượt riêng** dù cùng tham số: pipeline gắn `tags.rule` vào spec nên `param_hash` khác (đã đo trên `us30_h1_combo_full_wf`: cửa sổ 1 cả hai luật chọn KSL 9 / KTP 5, vẫn 2 lượt cho cùng lãi −495,48). Kế hoạch gốc ghi "SQL chỉ giữ một" — không đúng; `v_compare_same_cell` thấy cặp này với `versions_differing = 0`.
3. **Chưa làm phần "bỏ qua lượt đã có trong SQL trước khi tốn lượt CLI"** (6.E dòng 2): nếu bỏ qua lượt đã có ở experiment khác thì experiment hiện tại không có dòng cục bộ → walk-forward/dashboard/readout (đọc `index.sqlite`) sẽ thấy lưới thiếu. Làm đúng cần chép file của lượt cũ vào experiment mới như một lượt đã ghi — là quyết định thiết kế riêng, chờ người dùng. `master_db.find_run` đã sẵn để dùng.
4. Không gắn `MS_Description` cho từng cột (người dùng muốn đơn giản); chú thích nằm ngay trong `001_schema.sql`.
5. Chưa nạp dữ liệu walk-forward cũ (`us30_h1_combo_full_wf`, 306 lượt) vào DB thật: đã nạp thử vào database thử và kiểm chéo từng lượt với `index.sqlite` cũ (306/306 khớp trạng thái, lãi ròng, số lệnh; Σ`trade` = Σ`total_trades` = 72.103) rồi xoá database thử. Muốn nạp thật chỉ cần `master_db.import_experiment(conn, <thư mục experiment>)`.

### Kiểm chứng đã làm
- Test tự động (252) gồm test chạm SQL Server thật; nạp thử + kiểm chéo walk-forward 306 lượt (mục trên); các view kiểm trên dữ liệu thật (Σ lãi theo nửa năm == lãi ròng; lũy kế chuỗi OOS == tổng 3 lượt kiểm).
- `find_native_output` thử trên lượt mẫu thật: tìm đúng thư mục, trả None khi sai mốc giờ; 161/161 lệnh khớp lý do đóng từ `events.json`.

### Bài học khi triển khai (đã sửa)
- **Test phải KHÔNG phụ thuộc cờ `master_db.enabled` của máy.** Sau khi bật cờ trong `config.yaml`, các test chạy `run_experiment` bắt đầu ghi lượt GIẢ vào database THẬT (22 lượt batch `exp` + 2 bản chụp tín hiệu giả; đã xoá đúng các dòng đó trong 1 giao dịch). Đã chặn: `test_core_engine.py` và `test_optimize_backend.py` patch `load_master_db_profile` thành TẮT ở mức module (`setUpModule`); chỉ test chủ đích bật kho mới tự patch lại bên trong. Quy tắc: mọi test mới chạm `facilitator`/`walkforward` phải chạy với kho tắt.
- **`sqlite3.connect()` dùng `with` chỉ commit chứ KHÔNG đóng** — trên Windows giữ khoá `index.sqlite` tới khi GC (làm hỏng việc xoá thư mục). `run_export` dùng `contextlib.closing`.
- **`run_payload` đọc `.cs` bằng bytes** (không để Python đổi `\r\n`): bản lưu trong SQL trùng từng byte với file và `source_sha256` đúng (đã kiểm trên `Combo.cs` thật; có test CRLF).
- **Test chập chờn do dữ liệu mẫu:** `gzip.compress` mặc định ghi giờ hiện tại vào header nên hash `.algo` giả đổi theo giây; đã cố định `mtime=0`.

### Backtest thử nghiệm đầu tiên qua pipeline mới (2026-10-14)
US30.cash, Combo, H1, 2026-01-01 → 2026-09-30, vốn 100.000, **tham số mặc định của profile** (KSL Fib1000 = 1,0×ATR, KTP Fib2618, rủi ro 1%, 1 chân), kênh db0, experiment `us30_combo_2026_9m_default`. 73 giây, CLI 5.10.19. Kết quả: +27.540,89 (+27,54%), PF 1,24, 161 lệnh, thắng 33,54%, DD equity tối đa 15,18%, không bị margin từ chối; lý do đóng: cắt lỗ 91, chốt lời 48, bot đóng 22. Đã vào SQL: 1 `backtest_run`, 161 `trade`, 199 `daily_equity`, 1 `bot_build` (có mã nguồn `.cs` trùng từng byte), 1 `signal_snapshot` (1.615 dòng); đối chiếu SQL với file: Σ lãi các lệnh = lãi ròng. FTMO (view `v_ftmo_daily_loss`, chỉ để tham khảo): không vi phạm 2-Step (ngày 5%, tổng 90%); vi phạm 1-Step ở 6 ngày (sàn ngày 3%) và 23 ngày (sàn trailing); ngày sụt lớn nhất 3,88% vốn gốc. Số liệu chỉ để kiểm luồng, không để kết luận chiến lược.

### Quyết định của người dùng sau khi triển khai (2026-10-14)
1. **Kho CHỈ nhận lượt chạy HOÀN TẤT** (`status == "ok"`). Lượt lỗi, bị huỷ, treo/timeout, hoặc đang chạy dở rồi dừng thì KHÔNG nạp (không để lại dòng nào, kể cả `bot_build`/`signal_snapshot`). Đã code: `master_db.COMPLETED_STATUS`, `save_run` trả `(None, False)` với lượt chưa hoàn tất, hook của `facilitator` trả `skipped` (kết quả có khoá `master_db` dạng `{saved, already, skipped, error}`), `import_experiment` có thêm `not_completed`; có test ở cả hai tầng (254 test). Lượt lỗi vẫn nằm ở kho file như trước.
2. **KHÔNG nạp dữ liệu cũ** (walk-forward `us30_h1_combo_full_wf` 306 lượt) vào DB thật.
3. **KHÔNG làm** "bỏ qua lượt đã có trong SQL trước khi tốn lượt CLI" (điểm lệch số 3 ở trên coi như đã đóng, không làm).
4. Experiment mẫu cũ `flow_us30_combo_2026_9m` **đã xoá**. Còn lại trong `grid/`: `us30_combo_2026_9m_default` (lượt thử mặc định, đã nạp vào SQL).

### Ba đợt chạy thử và hai lỗi tìm ra (2026-10-14, đã sửa sau khi người dùng chốt)
Đợt chạy: JP225 Combo 3 chân + tỷ giá thật (`grid/jp225_combo_2026_9m_3legs_precisefx`), walk-forward US30 học 3 : kiểm 1 tháng lưới 3×3 (`walkforward/us30_wf_2026_9m_3is1oos_3x3`, 66 lượt), optimize XAUUSD MACD Break (`grid/xauusd_macdbreak_2026_9m_optimize_3x3`, 9 pass). Walk-forward: `end` là mốc LOẠI TRỪ (đặt 01/10 mới có cửa sổ kiểm tháng 9).
1. **Mất lượt khi ghi song song:** `_ensure_bot_build`/`_ensure_signal_snapshot` làm SELECT rồi INSERT không khoá; 8 luồng cùng ghi bản chụp mới → 1 bên vi phạm UNIQUE và mất cả lượt (1/66). Sửa: `WITH (UPDLOCK, HOLDLOCK)` ở 2 câu SELECT; test đua 8 luồng × 4 vòng (trượt 3/3 trên code cũ, qua 5/5 sau sửa). Lượt mất đã nhập bù bằng `import_experiment`.
2. **Mất `close_reason` ở 74% lệnh walk-forward:** `find_native_output` có khoảng đệm 5 s TRƯỚC lúc bắt đầu và `parameters.cbotset` không chứa ngày ⇒ thư mục của lượt liền trước cùng KSL/KTP bị tính là ứng viên thứ hai ⇒ None (49/65 lượt). Sửa: bỏ khoảng đệm trước; thêm `accept` = `evidence.events_match_report` (nay chặt hơn: ngoài số vị thế còn so tập (giờ đóng, giá đóng, khối lượng) với `report.history`). Ghép lại offline 66 lượt thật: 66/66 (trước 17/66); đúng 28/28 events hiện có trong kho file.
3. `v_durable_cells` chỉ lấy `zone = 'single'` (65 cửa sổ ngắn của walk-forward từng bị gắn "bền" vô nghĩa).
Chưa làm (chờ người dùng): ghép lại `close_reason` cho 49 lượt walk-forward đã có trong SQL (cần các thư mục cTrader còn trên đĩa — còn đủ 66/66).

### Lược đồ GỌN — dọn 34 cột thừa (2026-10-14, người dùng: "pilot, cứ mạnh tay refactor")
Nguyên tắc: **cột suy ra được từ cột/bảng khác thì không lưu** (mỗi cái đã kiểm bằng số trên 77 lượt / 4.134 lệnh / 6.144 ngày). 6 bảng giữ nguyên, 117 → 83 cột; DB `bo_research` được xoá và dựng lại, nạp lại bằng 3 đợt chạy `_v2`.
- `backtest_run` 52 → 35. Bỏ: `status` (kho chỉ có lượt hoàn tất; payload vẫn mang `status` để `save_run` lọc, không lưu), `failure_code`, `failure_reason`, `is_invalid`, `invalid_note`, `period_ok` (báo cáo chỉ lặp lại `--end` ta truyền), `signal_ok` (suy ra từ `bot_counters_json`), `approx_fx` (suy ra từ `engine_json` + `symbol_spec_json`), `ending_balance` (= vốn đầu + lãi, 77/77), `max_balance_drawdown_percent` (tính lại từ chuỗi lệnh, 77/77), `wall_seconds` (= `ended_utc` − `started_utc`; lượt optimize: ghi thời gian CẢ HỘP ở started/ended), `host`, `files_directory`, `native_directory`, `report_sha256`, `events_sha256`, `bot_log_sha256` (trỏ vào kho file có thể bị xoá). GIỮ `window_name` + `zone` (nhãn của lượt học walk-forward không được chọn).
- `trade` 16 → 14. Bỏ `balance_after` (cộng dồn lãi, 77/77), `pips` (= chênh giá vào–ra khi pip = 1; forex sau này cần `symbol_spec_json`).
- `daily_equity` 7 → 4 (`backtest_run_id`, `trading_day`, `balance_at_close`, `lowest_equity`). Bỏ `balance_at_midnight` (= `balance_at_close` ngày trước, 6.144/6.144; `v_ftmo_daily_loss` tính bằng LAG), `highest_equity`, `point_count`.
- `bot_build` 11 → 9. Bỏ `manifest_json` (nằm sẵn trong `algo_file`), `note`; đổi `source_captured_at_utc` → `source_modified_utc` (thực chất là giờ sửa file .cs).
- `signal_snapshot` 12 → 10. Bỏ `first_bar_time`, `row_count` (tính lại từ `rows_compressed`, `DECOMPRESS()`); GIỮ `redis_key` (JP225.cash → `J225`, XAUUSD → `GOLD`: không dựng lại được từ tên cTrader).
- `walkforward_decision` 19 → 11. Bỏ `strategy`, 4 cột ngày học/kiểm, `selected_ksl/ktp_atr_multiple`, `selected_parameters_json` — trùng 12/12 với lượt học/kiểm mà `train_run_id`/`test_run_id` trỏ tới. Lưu hết các lượt học không được chọn (≈1,2 MB cho 66 lượt); không ghi hằng số luật plateau (là hằng số trong code).
- View: bỏ mọi điều kiện `status`/`is_invalid`; `v_durable_cells` chỉ lấy `zone = 'single'`.

### Kiểm chứng sau khi dọn (chạy lại 3 nhóm với tên `_v2`, 2026-10-14)
JP225 (98 s), optimize XAUUSD (556 s), walk-forward US30 (330 s) đều ok, tự vào DB mới: 76 lượt (1 + 9 + 66), 3.973 lệnh, 5.945 ngày, 12 quyết định, ≈1,5 MB.
- Lỗi A hết: 66/66 lượt walk-forward có mặt (mỗi cửa sổ học đủ 9, kiểm đủ 2); `IDENT_CURRENT` bằng id lớn nhất ở cả 3 bảng (không còn lần chèn bị huỷ).
- Lỗi B hết: `close_reason` rỗng 0/3.973 lệnh (trước 2.307/3.114 ở walk-forward).
- Tất định: 75/76 `run_key` trùng lần chạy trước và kết quả y hệt (lãi, số lệnh). 1 lượt khác khoá: cửa sổ 1, luật `plateau` — hai ô (KSL idx 2 và 3, cùng KTP idx 4) có `plateau_score` bằng nhau (−1.248,66) nên thứ tự quyết định bởi thứ tự hàng vào hàm (chạy song song ⇒ không cố định). **Lỗi tất định có sẵn của luật `plateau` khi hoà điểm (walkforward.py `plateau`/`pick`), chưa sửa — chờ người dùng.**
- View: 6/6 chạy; `v_ftmo_daily_loss` khớp phép tính độc lập (JP225: sụt ngày lớn nhất 3,5420% ngày 2026-08-20; ngày đầu = vốn đầu ở 76/76 lượt); `v_walkforward_oos_chain` 220 dòng; `v_durable_cells` 10 dòng (không lẫn walk-forward).

### Lỗi hoà điểm của luật plateau (2026-10-14) — ĐÃ THỬ SỬA RỒI HOÀN NGUYÊN theo yêu cầu người dùng
**Trạng thái hiện tại: code `walkforward.plateau` ĐÃ HOÀN NGUYÊN về hành vi ban đầu (hoà điểm thì giữ thứ tự đầu vào), test thêm đã xoá. Phần dưới chỉ là ghi chép phân tích, KHÔNG phải mô tả code đang chạy.** Lý do hoàn nguyên: quy tắc phá hoà là quyết định về phương pháp (nó quyết định walk-forward chọn tham số nào); mình đã tự đổi sang quy tắc khác đề xuất ban đầu mà chưa hỏi người dùng. Quyết định phá hoà (và cách lưu/gắn nhãn walk-forward trong SQL) chờ người dùng.
`walkforward.plateau` sắp xếp ổn định chỉ theo `plateau_score` nên khi hoà thì giữ thứ tự hàng đầu vào = thứ tự các lượt chạy song song xong ⇒ cửa sổ 1 của lần chạy `_v2` chọn ô khác lần đầu. Hoà điểm KHÔNG hiếm: lưới 3×3 có 2–4 ô hoà ở đầu bảng ở cả 6 cửa sổ (đám hàng xóm chồng nhau).
- **Quy tắc đã thử (đã hoàn nguyên, không còn trong code):** hoà `plateau_score` → toạ độ nhỏ hơn thắng (KSL rồi KTP thấp hơn trước). Thứ tự đầu vào không còn ảnh hưởng (đảo/xáo đầu vào: 0 thay đổi).
- **Vì sao không dùng "lãi riêng của ô cao hơn" như đề xuất lúc đầu:** thử offline trên 15 quyết định plateau đã lưu của 3 experiment: lãi riêng khớp 6/15, nhiều ô lân cận → lãi riêng khớp 9/15, toạ độ nhỏ nhất khớp 14/15 (1 quyết định còn lại chính là cái bị đảo). Tức hành vi cũ thực chất là "toạ độ nhỏ nhất"; dùng lãi riêng sẽ đổi âm thầm 9/15 kết quả cũ và kéo `plateau` về trùng `best` (trái mục đích "không chọn đỉnh cô lập"). Đổi sang quy tắc khác là quyết định phương pháp, để người dùng chọn riêng.
- Kiểm bằng chính `pick()` trên 30 quyết định (plateau + best) của 3 experiment: khớp 29/30; 1 lệch là đúng quyết định không tất định (`_v2` cửa sổ 1: lưu ô (3,4), nay ra (2,4) như lần đầu).
- Còn rủi ro cùng loại ở luật `best`: `selection.rank` cũng sắp xếp ổn định theo một khoá (`net_profit`) nên hai ô lãi BẰNG NHAU sẽ theo thứ tự đầu vào. Chưa thấy hoà ở 3 experiment đã đo (xáo đầu vào không đổi kết quả). `rank` còn được nơi khác dùng nên chưa sửa.

### Walk-forward = backtest thường + NHÃN (bỏ bảng `walkforward_decision`) — 2026-10-15, người dùng chốt
Người dùng: walk-forward và optimize đều dựa trên cơ chế backtest nên cách chọn + lưu trong SQL phải giống nhau; chỗ khác phải gắn cờ/nhãn riêng cho nhóm tiến trình. Lược đồ giờ **5 bảng** (`backtest_run`, `trade`, `daily_equity`, `bot_build`, `signal_snapshot`), `backtest_run` có thêm 4 nhãn (35 → 39 cột):
- `method` — loại thí nghiệm (`grid` / `walkforward`), mọi lượt có (lấy từ `experiment.json`).
- `selection_rule` (`plateau` / `best`), `selection_objective` (vd `net_profit`) — CHỈ lượt KIỂM walk-forward (lấy từ `tags.rule` của spec + `selection.json["objective"]`).
- `train_run_id` — CHỈ lượt kiểm: khoá ngoại tự tham chiếu về lượt HỌC đã được chọn (giải từ `selection.json` → `execution.json` của lượt học; lượt học luôn được ghi trước lượt kiểm).
`plateau_score`, `candidate_count`, `eligible_count` không còn lưu: `candidate_count` tính trong view, `plateau_score` tính lại từ các lượt học (bản gốc còn trong `selection.json` của kho file). Views: `v_walkforward_selected` (mỗi cửa sổ × luật một dòng: tham số được chọn, lãi lúc học cạnh lãi lúc kiểm, số ứng viên) và `v_walkforward_oos_chain` (chuỗi lệnh ngoài mẫu + lãi luỹ kế) đều tính từ nhãn.
Code: `run_export._selection_labels` + `run_payload`; `master_db.save_run` giải `train_run_key` → `train_run_id`; đã bỏ `run_export.walkforward_decisions`, `master_db.save_walkforward_decisions`, hook `_save_decisions_to_master_db` của walkforward.
Kiểm: 12/12 quyết định cũ được `v_walkforward_selected` tính lại GIỐNG từng trường (khoá 2 lượt, lãi học/kiểm, KSL/KTP, số ứng viên, tiêu chí).
**Chưa làm theo thoả thuận:** nhãn "cách chạy" (backtest hay optimize gốc) — kho file không ghi lượt nào là optimize; muốn có phải sửa phần ghi lượt chạy.

### Bài học vận hành: DB thật là DB DÙNG CHUNG với phiên khác (2026-10-15)
Khi nâng cấp lược đồ, phát hiện một phiên Claude khác đang chạy backtest `combomulti` ghi vào chính `bo_research` (8 lượt mới của nó đã nằm trong DB; thêm 12 lượt nữa ở kho file). Mình đã (1) sửa code ghi trước khi sửa DB ⇒ các lượt hoàn tất trong khoảng đó lưu lỗi (chỉ cảnh báo): `combomulti_fix_2026h1_6sym` + `combomulti_2025_6sym`, 12 lượt — đã nạp bù từ kho file bằng `import_experiment`; (2) định xoá DB dựng lại ⇒ đổi sang **nâng cấp tại chỗ** (`ALTER TABLE` + điền nhãn từ kho file), không mất dòng nào. DB hiện 96 lượt / 10.497 lệnh. **Quy tắc:** trước khi đổi lược đồ hay xoá DB thật phải kiểm tiến trình đang chạy (python/ctrader-cli) và dòng của phiên khác; đổi DB TRƯỚC hoặc cùng lúc với code (cột thêm mới để NULL được để code cũ vẫn chạy); không dựng lại DB khi chưa kiểm.

### Thành phần danh mục vào luồng + lược đồ `analysis` vào file schema — 2026-10-26, người dùng chốt
Hai bảng tính thêm (tạo ngày 25/10 bằng hai script riêng, tự `CREATE SCHEMA analysis`) nay được xếp lại theo MỘT quy tắc: **biết ngay lúc chạy thì luồng ghi vào `dbo`; phải tính sau từ dữ liệu thô thì là bước dẫn xuất riêng trong `analysis`, bảng nằm trong file schema.**
- `dbo.portfolio_channel` (`001_schema.sql`): mỗi (batch, kênh) một dòng — `risk_percent`, `source` (nhãn profile Redis: original/knn/breakout/zigzag/tstat), `ksl`, `tp1..tp3`, `legs`. Luồng ghi: `run_export._portfolio_channels` dựng từ `spec.channels` (lượt từ 2 kênh trở lên; kênh không đè nguồn lấy nguồn mặc định của experiment), `master_db._save_portfolio_channels` ghi SAU khi lượt đã commit, một lần cho mỗi batch (UPDLOCK + HOLDLOCK như `_ensure_*`), lỗi chỉ cảnh báo — không làm mất lượt; lưu lại lượt đã có (hoặc `import_experiment`) ghi bù phần thiếu. Không còn suy nguồn từ tín hiệu.
- Nạp 592 dòng / 53 danh mục từ bảng suy ra cũ (đối chiếu hai chiều giống hệt). Đối chiếu với thành phần dựng từ cấu hình: 43/53 giống hoàn toàn; 10 danh mục `dashboard_mc_*` (21–22/10) chỉ khác nhãn nguồn vì dùng profile db1 cũ đã bỏ (`trend`, nay `knn`) — giữ nhãn `knn` đã suy ra.
- `analysis.group_excursion`, `analysis.excursion_run` (`003_analysis.sql`, nạp trong `master_db.SCHEMA_FILES`): đo MFE bằng tick, do `output_util/trade_excursion.py` tính (`run` / `status` / `check`) — KHÔNG do luồng backtest ghi; `status` cho biết còn bao nhiêu lượt chưa tính.
- Đã gỡ: `portfolio_composition.py`, test của nó, bảng `analysis.portfolio_channel`, và 3 cột chẩn đoán của việc suy nguồn (`source_match`, `source_note`, `signals`) — Power BI không dùng.

