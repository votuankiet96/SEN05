# Catalog: mọi vấn đề thực tế khi chạy `ctrader-cli` — tổng hợp 2026-08-27 → 2026-09-10

Gom toàn bộ sự cố đã gặp khi tự động hoá backtest/optimize qua `ctrader-cli`, từng case
cụ thể: **triệu chứng chính xác → cách nhận biết → nguyên nhân gốc → cách xử lý → trạng thái
trong pipeline**. Nguồn: các phiên 08-27 → 09-10, `bo_workflow/reports/*`, artifact `runs/`,
memory. Ký hiệu **[đo]** = kiểm chứng trực tiếp.

---

## BẢN ĐỒ NHANH

| Nhóm | Case | Mức độ | Trạng thái |
|---|---|---|---|
| A. Invocation | A1 tên symbol thiếu `.cash` | Cao (đã tốn nhiều lượt) | Đã hiểu, có memory |
| | A2 format ngày `MM/dd` vs `dd/MM` | Cao | Đã sửa (chốt `dd/MM/yyyy`) |
| | A3 đuôi file phải là `.cbotset` | Trung | Đã sửa |
| | A4 interactive shell từ chối mọi `--flag` | Trung | Đã hiểu, không dùng shell |
| | A5 `--balance` in ra "0" gây hiểu lầm | Thấp | Đã hiểu |
| | A6 `--data-dir` không có trong `--help` nhưng vẫn chạy | Trung | Đã sửa (functional probe) |
| B. Dữ liệu/cache | B1 tải tick lạnh cực chậm (server bóp băng thông) | Cao | Bản chất CLI, không sửa được |
| | B2 cache dùng chung GUI↔CLI (điểm mạnh, ít người biết) | — | Đã hiểu, tận dụng |
| | B3 `Progress` kẹt ở phase "Loading" | Cao | Watchdog + retry |
| | B4 JP225/HK50 "flaky theo cache" (kết luận cũ SAI) | — | Đã hiệu chỉnh |
| C. **Bug quy đổi tiền tệ** | C1 tick + symbol non-USD → sim đông cứng | **RẤT CAO** | **Bế tắc — dùng m1/GUI** |
| | C2 `Message expected` / `BacktestReportSavingStateStrategy` | Cao | Triệu chứng, nhiều nguyên nhân |
| | C3 cТrader tự abort "aborted by timeout" | Cao | Retry |
| D. Vòng đời process | D1 CLI 5.9.0 không tự thoát sau report | Cao | `--exit-on-stop` + watchdog |
| | D2 CLI treo ở "lưu report" dù đã tính xong (99.94%) | Cao | Watchdog kill sau report |
| | D3 process mồ côi khi Python crash / bị kill | Trung | `release_abandoned()` (F2) |
| | D4 `taskkill /IM` giết nhầm worker song song | Cao | `_kill_tree(pid)` |
| E. Bảo mật/log | E1 CLI 5.9.0 echo argv (chứa `--ctid`/`--account`) vào stderr | Cao | `-e` + redaction |
| | E2 console cp1252 crash vì tiếng Việt | Trung | `errors="replace"` |
| | E3 định danh nhúng trong report.json | Thấp | `_redact_report` |
| F. Version/feature | F1 `optimize` không tồn tại (< CLI 5.10) | Cao | Backend quarantine |
| | F2 chọn nhầm binary theo mtime | Trung | Chọn theo version thật |
| | F3 các bản `app_5.9.x` cũ trên đĩa HỎNG (thiếu hostfxr.dll) | Trung | Chọn theo version thật |
| | F4 `.algo` .NET 6 vs yêu cầu .NET 8 của CLI mới | Chưa gặp | Theo dõi |
| G. Song song/tài nguyên | G1 N worker = N login riêng | Trung | Chấp nhận (giống GUI) |
| | G2 1 backtest = 1 luồng | — | Đã hiểu |
| | G3 chạy song song với Desktop dùng chung account | Chưa rõ | Theo dõi |

---

## A. INVOCATION — sai cú pháp / tham số

### A1 — Tên symbol thiếu hậu tố `.cash`

**Triệu chứng [đo]:** login OK → in `Progress | Backtesting | 0.00 %` → **crash ngay**:
```
System.InvalidOperationException: Message expected
   at cTrader.Console.Infrastructure.StateMachine.Strategies
      .BacktestReportSavingStateStrategy.DoEnter()
```
KHÔNG có dòng `Progress | Loading <symbol>, <period> | X%` nào.

**Nhận biết:** log dừng ở `Backtesting | 0.00 %`, KHÔNG có phase "Loading". (≠ case C: case C
CÓ phase Loading chạy xong 100%.)

**Nguyên nhân:** `--symbol=US30` thay vì `--symbol=US30.cash`. Symbol không tồn tại → không
tải được data → state machine nhảy thẳng sang "lưu report" với stream rỗng → exception.

**Chi phí thực tế:** đã tốn rất nhiều lượt điều tra sai hướng (nghi version-mismatch, nghi
`--data-mode`, nghi `AccessRights`, dựng hẳn cBot rỗng để loại trừ) trước khi phát hiện
nguyên nhân tầm thường này.

**Xử lý:** LUÔN chạy `ctrader-cli symbols` lấy tên đầy đủ trước. Pipeline: `strategy.py` map
symbol cTrader đầy đủ (`_SYMBOL_GROUP`), `metadata` preflight. Memory: `ctrader-cli-symbol-name-bug`.

### A2 — Format ngày `MM/dd/yyyy` vs `dd/MM/yyyy`

**Triệu chứng [đo]:** backtest "kết thúc sạch" nhưng dừng SỚM hơn `--end` yêu cầu. Ví dụ UK100
H4 `--end=04/01/2025` → thực chạy tới `03/01/2025 21:00 / 71.88%` rồi dừng, KHÔNG báo lỗi.

**Nguyên nhân:** wrapper cũ dùng `MM/dd/yyyy`; CLI 5.9 batch mode cần `dd/MM/yyyy [hh:mm]` UTC.
`04/01` bị đọc là 4 tháng 1 thay vì 1 tháng 4.

**Chi phí:** kết luận sai "UK100 dừng sớm / bug dữ liệu"; retry ±1 ngày trước đó chỉ CHE lỗi.

**Xử lý:** `runner._cli_date()` chốt cứng `f"{d:%d/%m/%Y} 00:00"`, bỏ toàn bộ retry ±1 ngày.
`--start`/`--end` cho `backtest`+`optimize` là `dd/MM/yyyy`; còn `--from`/`--to` (candles,
deals) nhận CẢ `yyyy-MM-dd` LẪN `dd/MM/yyyy` — dễ nhầm.

### A3 — File tham số phải có đuôi `.cbotset`

**Triệu chứng [đo]:** `Error: Unable to determine destination for argument value` khi truyền
`cbotset.xml`.

**Xử lý:** `runner` ghi `run_dir / "params.cbotset"`. Không đặt tên khác.

### A4 — Interactive shell từ chối mọi `--flag=value`

**Triệu chứng [đo]:** trong shell (`ctrader-cli` không kèm command), gõ `backtest <algo>
<symbol> <period> <from> <to>` rồi thêm `--data-mode=...`:
```
Unknown form for 'backtest'. Expected:
  backtest <algo-file> <symbol> <period> <from> <to>
  backtest <algo-file> <symbol> <period> <from> <to> <params>
```

**Hệ quả:** không thể set `--data-mode`/`--balance`/`--report-json` trong shell → **shell 1-login
nhiều-command VÔ DỤNG cho pipeline**. Batch mode (non-interactive) mới nhận đủ flag, nhưng batch
= mỗi lệnh 1 login.

**Ghi chú:** trong shell dùng `--robot-params=<key=value,...>` cho tham số bot, nhưng vẫn không
set được testing context.

### A5 — `--balance` in ra "0 | default value"

**Triệu chứng [đo]:** không truyền `--balance` → bảng tóm tắt CLI in `Balance | 0 | default
value`. Chữ "0" gây hiểu lầm là dùng balance = 0.

**Thực tế:** JSON output vẫn `startingCapital: 10000` đúng. "0" chỉ là placeholder hiển thị.

**Xử lý:** pipeline luôn truyền `--balance` tường minh.

### A6 — `--data-dir` không có trong `--help` nhưng CÓ hỗ trợ

**Triệu chứng [đo, 2026-09-10]:** `ctrader-cli backtest --help` liệt kê `--data-file` nhưng
KHÔNG có `--data-dir`. Gate capability cũ (chỉ đọc help) → chặn nhầm → `RuntimeError: CLI
5.9.16 không hỗ trợ option ['data-dir']`.

**Thực tế:** truyền `--data-dir=<thư mục tồn tại>` → chạy exit 0, ghi cache 27 file. Truyền
`--data-dir=<thư mục KHÔNG tồn tại>` → `Error: Can't find directory specified for --data-dir
parameter!` (thông điệp RIÊNG, khác `Unknown command line parameters found` của cờ bịa).

**Xử lý [F1]:** `cli.cli_option_supported(token)` — help nói CÓ thì tin; help im lặng thì
probe thật một lần bằng parse argv (không login/mạng). Phân loại 7/7 đúng.

---

## B. DỮ LIỆU / CACHE

### B1 — Tải tick lạnh cực chậm

**Triệu chứng [đo]:**
- US500 (cache **0 file**): tải + chạy full 20 tháng = **841s** (~14 phút). Sim thật chỉ
  ~2–4 phút → ~10 phút là tải.
- US100 (cache 35 file): loading **80% sau 37 phút** rồi tôi kill.
- BTCUSD (cache 1338 MB): từng stall ở "Loading BTCUSD, h1 | 70.95%" 912s (Codex).
- XAUUSD (cache 155 file): hoàn tất nhưng 1794s.

**Nguyên nhân:** `--data-mode=ticks` là chế độ CHẬM NHẤT (Spotware xác nhận) — CLI tải từng
tick từ server. Server bóp băng thông theo account (than phiền phổ biến trên forum: "slower
server connections"). Băng thông máy KHÔNG phải giới hạn.

**Đặc điểm phân biệt:** trong lúc chậm, **cache file TĂNG đều**, disk ghi, network tải.
(≠ case C: cache KHÔNG tăng.)

**Xử lý:**
- Phí MỘT LẦN — sau khi cache đầy thì nhanh (US30 warm = 239s).
- Warm trước bằng GUI (mở symbol, chạy tick backtest 2025→nay).
- Hoặc để CLI tự tải 1 lần, chấp nhận 15–40 phút/symbol.
- `--data-dir` KHÔNG giúp lần đầu (vẫn tải lạnh); chỉ nhanh từ lần 2.
- Screening: dùng `m1` (không tải tick).

### B2 — Cache DÙNG CHUNG giữa GUI và CLI

**Sự thật ít người biết:** CLI (không `--data-dir`) đọc VÀ ghi **cùng thư mục cache** với GUI:
```
%APPDATA%\Spotware\Cache\<broker-profile>\BacktestingCache\V1\<account>\<symbol>\t1\*.zticks
```
[đo] file `.zticks` được cả GUI lẫn CLI ghi (mtime lẫn lộn giữa các nguồn).

**Hệ quả tích cực:** US30 chạy nhanh (239s) CHÍNH VÌ dùng cache có sẵn (986 file). Không cần
làm gì thêm để "chia sẻ" — nó tự chia sẻ.

**Hệ quả cần nhớ:** warm cache bằng GUI chỉ cứu case B (tải chậm), KHÔNG cứu case C (bug engine)
— JP225 cache đầy đủ 620 file mà vẫn hỏng.

### B3 — `Progress` kẹt ở phase "Loading"

**Triệu chứng [đo]:** `Progress | Loading BTCUSD, h1 | 70.95 %` rồi đứng yên, watchdog pipeline
bắt sau `STARTUP_STALL_SECONDS` → `STALLED`.

**Nguyên nhân:** = B1, tải lạnh quá chậm, đôi khi đứng hẳn.

**Xử lý:** `runner._make_poll` cho **0% một ngưỡng dài hơn** (`STARTUP_STALL_SECONDS=900`) vì
0% có thể đang tải; chỉ dùng ngưỡng ngắn (`STALL_SECONDS=240`) SAU khi đã vượt 0%. Retry.

### B4 — Kết luận cũ "JP225/HK50 flaky theo cache" — ĐÃ HIỆU CHỈNH

**Kết luận sai ban đầu (Claude):** "≥3 tháng Ticks + JP225 → LUÔN treo, IO=0 nên loại trừ cache".

**Hiệu chỉnh (Codex + đo lại):** flaky, KHÔNG tất định — cùng lệnh lỗi rồi chạy được sau đó;
JP225 H4 OK; "IO=0" chỉ đo disk read, network vẫn chạy.

**Hiệu chỉnh CUỐI (2026-09-10):** thực ra là **case C** (bug quy đổi), không phải cache. JP225
cache đầy đủ. Xem C1.

---

## C. BUG QUY ĐỔI TIỀN TỆ — nghiêm trọng nhất

### C1 — Tick + symbol non-USD → simulation đông cứng

**Triệu chứng [đo, 2026-09-10]:** với symbol quote ≠ USD (JP225/JPY, HK50/HKD, UK100/GBP,
GER40·FRA40·SPN35/EUR):
```
Progress | Loading JP225.cash, h1 | 0 → 100 %      ← data chính: OK, nhanh
CBot instance started
Progress | Backtesting | 0.16 %
Progress | Loading USDJPY, h1 | 0 → 100 %          ← symbol QUY ĐỔI: OK, nhanh
Combo: loaded 4542 valid signal rows
Progress | Backtesting | 0.17 %                    ← ĐÔNG CỨNG Ở ĐÂY
[~1000s không nhúc nhích]
Error | CBot instance [Combo, JP225.cash, h1] aborted by timeout.   ← cTrader tự giết
```

**HOẶC** (chạy tay, range dài) biến thể ngược:
```
Progress | Backtesting | 0.17 % → 99.94 %          ← sim CHẠY XONG (tính ra 584 lệnh!)
Info | CBot instance [Combo, JP225.cash, h1] stopped.
{ "NetProfit": 14757.21, "TotalTrades": 584 }      ← KẾT QUẢ ĐẦY ĐỦ
Progress | Backtesting | 99.94 %                   ← KẸT Ở ĐÂY, không thoát nổi
[treo 37 phút]
```
→ `report.json` **ĐÃ GHI, hợp lệ** (2.2 MB, testingPeriod đúng).

**Nhận biết case C (khác A1, khác B):**
- CÓ phase `Loading <symbol chính>` chạy tới 100% (≠ A1).
- CÓ phase `Loading <symbol QUY ĐỔI>, h1` — USDJPY/USDHKD/GBPUSD/EURUSD (≠ mọi case khác).
- Cache **KHÔNG tăng file** trong lúc đông cứng, CPU ~0–7%, disk phẳng, network vẫn ~700 KB/s
  (≠ B1: B1 cache tăng đều).
- Đông cứng ở ~0.2% (bắt đầu sim) HOẶC ~99.94% (cuối, sau khi có kết quả).

**Bằng chứng loại trừ:**
| | quote | cache 2025 | kết quả |
|---|---|---|---|
| US30 | USD | đầy | ✅ ok 239s |
| XAUUSD | USD | **thiếu** | ✅ ok (chậm, tải) |
| JP225 | JPY | **đầy** | ❌ đông cứng |
| GER40 | EUR | thiếu | ❌ đông cứng |
→ Không phải cache. Không phải quote-currency đơn thuần (XAUUSD là USD-underlying non-USD... không,
XAUUSD quote USD). **Khác biệt duy nhất: có load symbol quy đổi hay không.**

**Bằng chứng KHÔNG phải Python:** chạy tay `ctrader-cli` (không pipeline) — JP225 20 tháng: sim
chạy xong, treo 37 phút; JP225 **3 tháng**: 40s SẠCH. → range dài mới lộ; pipeline không liên quan.

**Bằng chứng flaky:** probe JP225 12 tháng — lần 1 đông cứng 397s → **retry lần 2 xong 130s**.

**Nguyên nhân gốc (suy luận):** engine backtest của CLI có bug khi tick-mode phải nội suy giá
symbol quy đổi (USDJPY chỉ có bar h1, JP225 có hàng triệu tick → lookup mỗi tick). CLI mới ra
~1 tháng; changelog 5.9.10/5.9.11 toàn vá loại "hangs"/"freeze after backtest".

**Xử lý:**
- **Screening:** `data_mode='m1'` — [đo] JP225 m1 full year = **31.6s SẠCH**, GER40 m1 = 31.2s.
  m1 chỉ ~1440 event/ngày thay vì hàng triệu → lookup quy đổi chịu được.
- **Fidelity tick non-USD:** chạy trên **GUI** (engine ổn định), hoặc CLI + range ≤3 tháng + retry.
- **Retry [đã sửa]:** `transient_retries` nới 1→3, nhiều vòng, retry cả `PROGRESS_STALLED`/
  `HARD_TIMEOUT` (không chỉ `MESSAGE_EXPECTED`).
- **Watchdog [đã có]:** report.json hợp lệ → chờ grace 10s → kill → OK. Xử lý đúng biến thể
  "treo ở 99.94%".

### C2 — `Message expected` / `BacktestReportSavingStateStrategy.DoEnter()`

**Đây là triệu chứng CHUNG, nhiều nguyên nhân:**
```
System.InvalidOperationException: Message expected
   at ...BacktestReportSavingStateStrategy.DoEnter()
```
Nguyên nhân đã biết:
1. **Tên symbol sai** (A1) — không có phase Loading.
2. **cТrader tự abort giữa sim** (C1/C3) — stream vỡ → không lưu report được.
3. **Report tính xong nhưng process không finalize** (C1 biến thể cuối).

**Nhận biết nguyên nhân nào:** xem log CÓ phase `Loading` không, CÓ symbol quy đổi không, có
`aborted by timeout` trước không, `report.json` có được ghi không.

**Pipeline classify:** `_classify` bắt chuỗi "message expected" → `CLI_ERROR` +
`FailureCode.MESSAGE_EXPECTED` → retryable. Nhưng nếu `report.json` hợp lệ thì report THẮNG →
`OK` bất kể exception.

### C3 — cТrader tự abort: `aborted by timeout`

**Triệu chứng [đo]:** `Error | CBot instance [Combo, JP225.cash, h1] aborted by timeout.` xuất
hiện trong **bot.log/cli.log** ở ~1000–1150s, TRƯỚC khi pipeline timeout (3600s).

**Nguyên nhân:** watchdog "not responding" NỘI BỘ của cТrader — nếu luồng backtest bị block
(không pump message) quá lâu, cТrader tự giết. Forum: "automatically terminated by cTrader for
not responding". Ngưỡng động theo tiến độ: nếu Progress % còn nhích thì không giết (XAUUSD chạy
1794s không bị abort); đứng hẳn thì giết (~1000s).

**Xử lý:** không tắt được. → retry, hoặc range ngắn hơn (ít cơ hội block), hoặc m1.

---

## D. VÒNG ĐỜI PROCESS

### D1 — CLI 5.9.0 không tự thoát sau khi ghi report.json

**Triệu chứng:** `report.json` đã ghi xong nhưng `ctrader-cli.exe` vẫn sống ≥30s (5.9.0),
buộc phải poll + kill từ ngoài.

**Xử lý:**
- **`--exit-on-stop`** (Codex tìm ra): cờ chính thức GLOBAL. [đo] CLI 5.9.0 + 5.9.12 + 5.9.16
  tự thoát exit 0 ~0.5s sau report.
- Watchdog vẫn giữ làm fallback: `run_process` poll `report.json`, hợp lệ → grace
  `REPORT_EXIT_GRACE_SECONDS=10` → `kill_tree`.

### D2 — Treo ở "lưu report" dù đã tính xong (Progress 99.94%)

**Triệu chứng [đo]:** sim chạy 100%, kết quả in ra stdout, `report.json` ghi hợp lệ, RỒI process
kẹt ở `Progress | Backtesting | 99.94 %` — chạy tay treo **37 phút** trước khi tôi kill.

**Nguyên nhân:** = C1 biến thể cuối — `BacktestReportSavingStateStrategy` không transition được.

**Xử lý [đã có]:** watchdog thấy `report.json` hợp lệ (`main.testingPeriod`) → chờ grace 10s →
`kill_tree` → `_classify` trả **OK** vì report thắng. Chạy tay không có lớp này nên treo 37 phút.
**Đây là chỗ pipeline làm TỐT HƠN chạy tay.**

### D3 — Process mồ côi khi Python crash / bị kill cứng

**Triệu chứng:** Ctrl-C / `taskkill /F` tiến trình Python giữa chừng → `ctrader-cli.exe` con
tiếp tục sống; row SQLite kẹt `RUNNING`; lần sau claim báo `busy` mãi (kể cả `force=True`).

**Xử lý [F2, 2026-09-10]:**
- `store.release(run_id, token)` — CAS theo token, chỉ chính chủ nhả.
- Scheduler bọc `except BaseException` → Ctrl-C tự nhả claim rồi re-raise.
- Cột `claim_owner` = `host:pid`. `store.release_abandoned()` chỉ nhả row có **PID chủ trên
  máy này ĐÃ CHẾT** (kiểm qua `OpenProcess`), không đoán theo đồng hồ.
- [đo] `taskkill /F` batch → `release_abandoned()` dọn đúng row còn treo.

**Còn thiếu:** Windows Job Object (`KILL_ON_JOB_CLOSE`) để con tự chết khi cha chết — chưa làm.

### D4 — `taskkill /IM ctrader-cli.exe` giết nhầm worker song song

**Triệu chứng:** khi chạy N worker, kill "toàn bộ ctrader-cli" giết luôn các worker đang chạy.

**Kinh nghiệm xương máu (phiên này):** tôi 2 lần dùng `Stop-Process ctrader-cli` / `taskkill
/IM` để dọn tiến trình test → giết nhầm worker của batch (US100 `no_report 56s`, BTCUSD).

**Xử lý:** `_kill_tree(pid)` = `taskkill /F /T /PID <pid>` — chỉ cây PID của chính mình.
**Quy tắc:** không bao giờ `/IM` khi có nhiều CLI cùng chạy.

---

## E. BẢO MẬT / LOGGING

### E1 — CLI 5.9.0 echo nguyên argv (chứa `--ctid`/`--account`/`--pwd-file`) vào stderr

**Triệu chứng [Codex]:** mọi `cli.log` chứa dòng lặp lại nguyên lệnh gọi, gồm `--ctid=<email>
--account=<số> --pwd-file=<path>` (không có nội dung password nhưng lộ định danh + đường dẫn).

**Xử lý:**
- **`-e` / `--environment-variables`** [đo]: đọc `CTID/PWD-FILE/ACCOUNT/BROKER` từ ENV process
  → argv KHÔNG còn định danh. [đo] quét 4 pattern trên toàn artifact = 0 file.
- Fallback: `redact_argv()` + `redact_text()` scrub stdout/stderr trước khi ghi.
- CLI 5.9.11 changelog: "CLI no longer writes startup echo to stderr" — bản mới đã tự sửa.

### E2 — Console cp1252 crash vì dòng tiến trình tiếng Việt

**Triệu chứng [Codex]:** `UnicodeEncodeError` khi in progress tiếng Việt trên console cp1252
Windows → **giết cả scheduler** giữa mẻ.

**Xử lý:** `_make_console_nonfatal()` → `sys.stdout.reconfigure(errors="replace")` gọi đầu `run()`.

### E3 — Định danh nhúng trong report.json của Spotware

**Triệu chứng:** report JSON có key `authorNickname`/`brokerTitle`/`accountNumber`/`ctid`.

**Xử lý [Codex]:** `runner._redact_report()` — thay các key nhạy cảm bằng `<redacted>`, giữ
nguyên mọi metric.

---

## F. VERSION / FEATURE AVAILABILITY

### F1 — `optimize` không tồn tại (< CLI 5.10)

**Triệu chứng [đo]:** `ctrader-cli --commands` (5.9.0, 5.9.12, 5.9.16) KHÔNG liệt kê `optimize`.
Gõ `optimize --help` với đủ credentials → rơi về interactive shell menu.

**Nguồn:** winget-pkgs + Homebrew `spotware/tap` — cả hai xác nhận 5.9.x là mới nhất công khai
một thời gian dài. Tài liệu Spotware ghi `optimize` là CLI 5.10 "local edition".

**Trạng thái (2026-09-10):** Desktop tự update lên **5.9.16.53348**, vẫn KHÔNG có `optimize`.

**Xử lý:** `methods/optimize.py` — backend `native` bị **quarantine** (`certified()` return
False), `auto` → luôn `fallback` (pool N-process backtest). Adapter native viết theo tài liệu,
GATE chặn chạy tới khi có CLI 5.10 + smoke test.

### F2 — Chọn nhầm binary theo mtime

**Triệu chứng:** logic cũ `sorted(glob, key=mtime)` có thể chọn `app_5.9.10/` nếu nó mới hơn
shim `abb70432.../ctrader-cli.exe`.

**Xử lý:** `settings._cli_version_key()` — chạy `--version` thật, parse semver, chọn cao nhất.
Binary probe lỗi → `(-1,)`, ưu tiên bundled theo path ổn định.

### F3 — Các bản `app_5.9.x` cũ trên đĩa HỎNG

**Triệu chứng [đo]:** `AppData\Local\Spotware\cTrader\<hash>\app_5.9.10.52700\ctrader-cli.exe`
và `app_5.9.12.53118\...` → chạy `--version` báo:
```
A fatal error occurred. The required library hostfxr.dll could not be found.
```
Chỉ shim top-level `<hash>\ctrader-cli.exe` chạy được (tự resolve về bản mới nhất).

**Xử lý:** F2 — probe version, binary hỏng trả `(-1,)`, bị loại.

### F4 — `.algo` .NET 6 vs yêu cầu .NET 8

**Trạng thái:** FAQ Spotware ghi "`.algo` file must be built for .NET 8". Combo/MA Cross csproj
đang `net6.0`. Nhưng `backtest` trên 5.9.12/5.9.16 vẫn chạy `.algo` .NET 6 bình thường [đo].
Nghi ngờ: `optimize` (CLI 5.10) hoặc runtime mới hơn mới bắt buộc .NET 8.

**Chưa gặp lỗi.** Mẹo dự phòng: `ctrader-cli build <csproj>` (bundled có .NET SDK kèm từ 5.9.10)
có thể rebuild `.algo` sang .NET 8 không cần GUI — **chưa test, ghi đè `.algo` nên cần cho phép**.

---

## G. SONG SONG / TÀI NGUYÊN

### G1 — N worker = N login riêng biệt

**Sự thật:** không có session pooling. N `ThreadPoolExecutor` worker = N `ctrader-cli.exe` =
N login vào cùng account. GUI có song song NỘI BỘ vì là app tích hợp (1 login, N pass thread).

**Kiểm chứng:** 3-way, 4-way (0 auth error, 5 login OK), 16-way (16/16 OK, ~570 MB/proc, CPU
bão hoà ~16 trên máy 32 CPU). Không có giới hạn login đồng thời cùng account rõ ràng.

**Trạng thái:** chấp nhận. `MAX_PARALLEL=4` mặc định (an toàn khi Desktop mở),
`MAX_FALLBACK_PROCESSES=min(16, cpu)` là trần cứng.

### G2 — 1 backtest = 1 luồng

**Sự thật (Spotware forum xác nhận):** "Backtesting is a sequential single threaded process".
Đa lõi CHỈ giúp `optimize` (nhiều pass //). → pool N-process của pipeline = ĐÚNG mô hình GUI
optimizer (N backtest đơn luồng //), không phải hack.

### G3 — Chạy song song với cTrader Desktop dùng chung account

**Chưa kết luận.** Desktop (`ctrader.exe`) luôn chạy trên máy này, login cùng account <FTMO-ACCOUNT-ID>.
Cả Desktop và CLI hit cùng cache + cùng account server session.

**Quan sát:** các probe CLI vẫn chạy được khi Desktop mở; nhưng batch dài hay lỗi hơn probe
đơn lẻ. Chưa tách được biến "Desktop mở/đóng".

**Mẹo forum (Panagiotis):** tạo **account demo RIÊNG chỉ để backtest** — workaround chính thức
cho cache corruption, có thể cũng giảm contention.

---

## H. NHỮNG GÌ ĐÃ THỬ VÀ XÁC NHẬN KHÔNG DÙNG ĐƯỢC

| Ý tưởng | Vì sao không |
|---|---|
| Interactive shell 1-login nhiều-command | Từ chối mọi `--flag` testing (A4) |
| `candles <symbol> t1` để warm tick cache | `ArgumentOutOfRangeException (framePeriod, Tick)` |
| `--data-dir` để tăng tốc | Warm ≈ không cờ; lần đầu CHẬM HƠN (tải lạnh); chỉ để cách ly (A6, B1) |
| Chỉ đọc `--help` để biết capability | Help không đầy đủ (A6) |
| `taskkill /IM` để dọn | Giết nhầm worker (D4) |
| Warm cache non-USD để sửa treo | Cache đã đầy vẫn treo — là bug engine (C1) |
| Tăng timeout pipeline | cТrader tự abort ở ~1000s trước (C3) |
| `data_mode='m1'` như tương đương GUI ticks | Fill khác — chỉ dùng screening |

---

## I. CHECKLIST TRƯỚC KHI CHẠY CLI CHO 1 SYMBOL/RANGE MỚI

1. `ctrader-cli symbols` → lấy tên đầy đủ (`.cash`). **[A1]**
2. Ngày dạng `dd/MM/yyyy`. **[A2]**
3. Symbol quote ≠ USD? → dùng `m1`, hoặc chấp nhận C1 với tick. **[C1]**
4. Cache trống (`...\BacktestingCache\...\<symbol>\t1\` ít file)? → lần đầu sẽ chậm 15–40 phút. **[B1]**
5. `data_mode`: `m1` để screening, `ticks` chỉ cho shortlist USD. **[C1]**
6. `timeout_seconds` ≥ 1800 cho full-year tick. **[C3]**
7. `transient_retries` ≥ 1 (mặc định) — ĐỪNG tắt. **[C1]**
8. Auth qua ENV (`CTRADER_CTID` v.v.) + pipeline dùng `-e`. **[E1]**
9. Không `taskkill /IM` khi có worker song song. **[D4]**
10. Nếu Python crash: `ExperimentStore(...).release_abandoned()`. **[D3]**

---

## J. TÓM TẮT 1 DÒNG

CLI **tốt ở phần tính toán** (sim JP225 20 tháng = 225s), **yếu ở 2 khâu**: (1) tải tick lạnh
chậm do server bóp băng thông — phí một lần, warm bằng GUI được; (2) **bug engine khi tick +
symbol quy đổi** — bế tắc thật, chưa có cách sửa từ phía ta, phải dùng `m1` hoặc GUI cho nhóm
non-USD. **Pipeline Python KHÔNG phải nguyên nhân** — chạy tay gặp y hệt, pipeline còn xử lý
tốt hơn (watchdog kill-after-report).
