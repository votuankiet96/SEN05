# A/B: Plugin Backtesting API vs CLI — audit + test thực nghiệm

Ngày: 2026-09-11 · Người kiểm: Claude (Opus 5) · Máy: VM-BO20, cTrader/CLI 5.9.16.53348
Phạm vi: audit `bo_workflow/ctrader_api/` (spike của Codex) + A/B với pipeline CLI
`bo_workflow/pipeline/`. **Không sửa code plugin/pipeline/bot**; chỉ gửi job qua bridge và chạy
pipeline. Đọc kèm: `ctrader-backtest-api-plugin-optimize-record-2026-09-11.md` (Codex).

---

## 1. Kết luận

**Dùng CLI (pipeline Python) làm nền tảng cho toàn luồng.** Plugin API không nên là backend chính.

Lý do cốt lõi, đo được: **Plugin Backtesting API chính là cTrader Desktop gọi lại `ctrader-cli.exe
backtest`** với đúng bộ cờ pipeline đang dùng (+`--port`). Cùng engine ⇒ cùng kết quả (72/72 pass
và 442/442 lệnh trùng từng trường), cùng tốc độ (291 s vs 300 s cho 72 pass × 8 song song). Plugin
không thêm năng lực engine nào — kể cả `PreciseConversion` (bị lờ đi). Cái plugin thêm vào là một
lớp điều phối **yếu hơn** pipeline hiện có: không validate input, báo `ok` cho run hỏng, không
timeout, không bot log, cần Desktop GUI mở.

Plugin chỉ đáng quay lại nếu **Plugin API expose `PreciseConversion` trước khi CLI có cờ
`--precise-conversion`** — lúc đó nó là đường tự động duy nhất có tỷ giá lịch sử thật. Kiến trúc
"1 core + executor backend" (Codex đề xuất, pipeline đã theo) giữ sẵn chỗ cho việc đó.

> **Đính chính (cùng ngày, sau phản hồi của đội audit):** câu "plugin không thêm năng lực engine
> nào" chỉ đúng với `Backtesting.Start` trên dữ liệu server. Plugin API có **custom data source**
> (`Backtesting.DataSources.Add(...)`, `BacktestingDataSourceDataType.Tick | OpenPrices`) — nạp dữ
> liệu tick/bar tự sinh hoặc thêm nhiễu vào engine backtest. Đã xác minh có trong `cAlgo.API.xml`
> của bản 5.9.16 đang cài (105 lần nhắc `BacktestingDataSource`); CLI 5.9.16 không có tương đương
> (không có cả `tick-csv`). ⇒ Lý do thứ hai để quay lại plugin: **stress test mức engine** (nhiễu
> tick, spread/slippage giả lập) ở giai đoạn robustness. Chưa thử nghiệm.
> Nguồn: help.ctrader.com/ctrader-algo/guides/backtesting-custom-data-sources/

Phát hiện phụ quan trọng hơn cả A/B (ảnh hưởng **cả hai** backend, §5): khi PreciseConversion OFF,
engine ước tính margin bằng **giá cuối kỳ backtest** ⇒ volume của mọi lệnh bị chặn margin phụ thuộc
ngày kết thúc (look-ahead). Đây là nguyên nhân thật của 44/70 pass GUI≠plugin mà Codex ghi nhận.

---

## 2. Phát hiện kiến trúc: API = CLI

Trong lúc plugin chạy 8 job, 8 tiến trình `ctrader-cli.exe` là **con trực tiếp của `cTrader.exe`**
(PID cha 21936), cùng file `…\Spotware\cTrader\abb70432…\ctrader-cli.exe`. Tên cờ (không in giá trị):

```
backtest --start --end --balance --data-mode --commission --spread --report-json --ctid --account
--symbol --period --port --environment-variables --full-access --exit-on-stop --<tham số bot>...
```

Khác pipeline duy nhất ở `--port` (xác thực qua phiên Desktop, không cần file mật khẩu). Không có
`--precise-conversion`. Mỗi job — dù qua plugin hay CLI — còn tạo 1 thư mục instance mới trong
`Documents\cAlgo\Data\cBots\Combo\<guid>\Backtesting\` (log.txt, events.json, report.html, cbotset).

---

## 3. Bảng A/B (số đo thật)

| Tiêu chí | Plugin API | CLI pipeline | Bằng chứng |
|---|---|---|---|
| Engine | `ctrader-cli.exe` do Desktop spawn | `ctrader-cli.exe` do Python spawn | cây tiến trình §2 |
| Độ khớp kết quả | **72/72 pass trùng lịch sử lệnh với CLI** | — | lưới US30 §4.3 |
| Độ khớp từng lệnh FRA40 full | **442/442 lệnh trùng mọi trường** với CLI | = GUI (bỏ tick) | a8 vs CLI |
| Tất định | 6 bản song song trùng nhau; 72/72 trùng lưới Codex chạy trước 4 giờ | (cùng engine) | a1–a6 |
| 1 job dài (FRA40 20 tháng ticks) | 147–160 s | 154 s | a7/a8, chiến dịch |
| 72 pass × 8 song song | **291.1 s** (31.4 s/pass) | **300.0 s** (31.5 s/pass; gồm ~8 s preflight) | §4.3 |
| `PreciseConversion=true` | **lờ đi, trả `ok`**, kết quả = OFF | **chặn trước khi chạy**: "CLI 5.9.16 không hỗ trợ option" | a7, probe |
| Tham số sai tên | lờ đi, dùng default, `ok` | chặn: `tham số chưa đăng ký` | b1 |
| Enum ngoài phạm vi | cTrader âm thầm về default, `ok` | chặn: `index 8 ngoài [0, 7]` | b2 |
| File signal không tồn tại | **`ok`, 0 lệnh, net 0** | chặn: `Thiếu 1 signal CSV; chưa tạo experiment` | b3 |
| Bot log (loaded/margin-capped…) | không thu | `bot.log` + `cli.log` mỗi run | — |
| Timeout / kill / retry | không có | hard timeout, kill cây PID, retry transient | code |
| Resume / cache / provenance | không có | SQLite + `param_hash` (sha algo/signal/cli/account) | code |
| Kiểm tra testingPeriod | không | có (`PERIOD_MISMATCH`) | `runner._classify` |
| Xác thực | phiên Desktop (không cần file mật khẩu) | env `-e` + đường dẫn file mật khẩu, redact log | — |
| Phụ thuộc vận hành | Desktop GUI mở ở phiên tương tác (session 2) + plugin bật | headless, chạy được khi Desktop tắt | session 2 vs 0 |
| Lưu kết quả | JSON + HTML nhúng: **5.5 MB/run** FRA40; bridge đã 95 MB/259 run | `report.json` riêng/run | đo |
| Mã phải bảo trì | C# + deploy + mailbox + client Python, 2 bản source | Python | — |

---

## 4. Test đã chạy (qua bridge thật, plugin đang bật, `MaxConcurrentJobs=8`)

### 4.1 Nhóm A — tất định & PreciseConversion
| Job | Kết quả |
|---|---|
| a1–a6: 6 bản giống hệt US30 h1 ticks 2026-01, KSL Fib0618/KTP Fib4618, MM50 | cả 6: net 18,695.77, 22 lệnh, **hash `history.items` trùng nhau**, trùng lưới Codex 01:18 |
| a7: FRA40 2025-01-01→2026-09-10 ticks, `preciseConversion=true` | `ok`, **−8,166.05** = OFF (GUI bật tick ra −8,053.03) |
| a8: như a7, `false` | `ok`, −8,166.05; **442/442 lệnh trùng CLI** (`combo_h1_2025_to_now_ticks`, `fra40_gui_match`) |

### 4.2 Nhóm B — hợp đồng & đường lỗi (US30 m1 1 tuần)
| Job | Kết quả | Đánh giá |
|---|---|---|
| b0 baseline (KSL Fib1000) | ok, 5 lệnh, net 10,417.93 | — |
| b1 `KslLevell` (sai tên) | ok, **trùng b0** | ❌ lờ đi |
| b2 `KslLevel="8"` | ok, **trùng b0** | ❌ âm thầm về default |
| b3 SignalFilePath không tồn tại | **ok, 0 lệnh, net 0** | ❌ run hỏng báo thành công |
| b4 `startUtc` thiếu `Z` | trùng b0, testingPeriod 00:00 đúng | ✅ (nghi ngờ tĩnh ban đầu SAI — rút lại) |
| b5 `robotName="NoSuchBot"` + algoPath hợp lệ | `plugin_error` **sau 60 s** (`AutomateWaitTimeoutException … AlgoInstallResMessage`) | ❌ chặn toàn plugin 60 s |
| b6 schema sai | `failed/` ngay | ✅ |
| b7 `KslLevel="Fib0618"` vs b8 `"0"` | trùng nhau | ✅ |

Trong 60 s của b5: heartbeat đứng im (12:23:41→12:24:41 UTC), không dispatch job mới, và event
`Completed` của 5 job đang chạy cũng bị hoãn ⇒ `wallSeconds` của b0–b4 phồng lên ~61 s (thực ~20 s).
⇒ `Completed` chạy trên cùng thread với `OnTimer`.

### 4.3 A/B lưới 72 pass
US30.cash h1 ticks 2026-01-01→2026-02-01, KSL 8 mức × KTP 9 mức, Risk 1, MM50, balance 100k.
Plugin: 72/72 ok, 291.1 s. CLI (`max_parallel=8`, experiment `ab_cli_us30_grid72`): 72/72 ok, 300.0 s.
**So `history.items` từng pass: 72/72 trùng tuyệt đối.** Best cả hai: Fib0618/Fib4618, net 18,695.77,
22 lệnh, maxEqDD 7.0996%.

---

## 5. Phát hiện engine (ảnh hưởng cả plugin lẫn CLI): margin tính bằng giá CUỐI KỲ khi OFF

**Triệu chứng (record Codex §4.4):** GUI optimize MM50 vs plugin: 26/70 pass khớp, 44 lệch.
So từng lệnh: giờ vào/ra, giá **khớp hết**; chỉ `volume` lệch (~1%) ⇒ net/gross/balance/swaps lệch
theo. `usedSymbols` (swap, lot, step…) giống hệt; `commissions` từng lệnh đều 0 ⇒ **không phải**
commission (GUI ghi `main.commissions.value=30` nhưng không lệnh nào bị trừ).

**Bằng chứng từ bot log CLI** (pass Fib0786/Fib1000, lệnh đầu 02/01/2026 10:00, giá đặt 48,253.4):
`Signal wanted 26.61 units, which needs about $86545.26 margin` ⇒ $3,252.35/unit ⇒ ở 1:15 ứng với giá
≈ 48,785 — gần giá **cuối kỳ** (lệnh cuối đóng 48,820), không phải 48,253. GUI ra 15.23 unit =
đúng giá lúc đặt (49,000 / (48,253/15) = 15.23).

**Test quyết định** (plugin, cùng start 2026-01-01, cùng tham số, chỉ đổi `endUtc`) — volume của
**cùng lệnh đầu tiên** (vào 48,253.5 ngày 02/01):

| endUtc | volume lệnh 1 | giá tham chiếu suy ra (1:15) |
|---|---:|---:|
| 2026-01-15 | 14.85 | ~49,500 |
| 2026-02-01 | 15.06 | ~48,800 |
| 2026-04-01 | 15.77 | ~46,600 |
| 2026-07-01 | 14.07 | ~52,200 |

⇒ Với PreciseConversion OFF, `Symbol.GetEstimatedMargin` quy đổi notional base-asset ("US 30 Index")
sang USD bằng **tỷ giá xấp xỉ = giá gần cuối kỳ**, kể cả symbol quote USD. Hệ quả:
1. **Look-ahead** trong sizing của mọi lệnh bị `CapVolumeByMargin` chặn (lệnh tháng 1 dùng giá tương lai).
2. **Walk-forward/chia khúc**: cùng 1 lệnh ra volume khác nhau tuỳ cửa sổ kết thúc ở đâu.
3. **GUI≠CLI/plugin** khi cap kích hoạt, nếu GUI bật ô "Download historical data for additional
   symbols" (tab Backtesting VÀ tab Optimization có ô riêng). MM100 khớp tuyệt đối vì cap không kích hoạt.
4. Không backend nào sửa được trên 5.9.16 (CLI không có cờ; API không có property).

Hướng xử lý (chưa làm, cần bạn quyết):
- Đối chiếu GUI: bỏ tick ô trên ở **cả hai** tab khi so với CLI.
- Research: theo dõi `margin-capped` trong bot log (chỉ CLI có) — nếu tỷ lệ lệnh bị cap cao, kết
  quả chịu look-ahead đáng kể.
- Sửa bot (**cần cho phép**, đụng Combo.cs/MA Cross.cs): `CapVolumeByMargin` tự tính margin từ giá
  hiện tại + đòn bẩy thay vì `GetEstimatedMargin` — tách sizing khỏi tỷ giá xấp xỉ của engine. Cần
  kiểm chứng API đòn bẩy/margin rate trước khi code.
- Chờ CLI có `--precise-conversion` (+ rebuild .algo .NET 8).

Liên quan (chưa giải thích xong): FRA40/SPN35 CLI cùng spec, chạy cách 4.5 giờ lệch ~$7 (trades
y hệt), symbol USD không lệch — tỷ giá xấp xỉ của EUR có vẻ còn phụ thuộc thời điểm chạy.

---

## 6. Audit code plugin (spike của Codex)

Ưu tiên: **P1 = sai kết quả mà vẫn báo `ok`**, P2 = vận hành, P3 = vệ sinh.

| # | Mức | Vị trí | Vấn đề | Bằng chứng |
|---|---|---|---|---|
| 1 | P1 | `BacktestMapper.cs:16-19, 40-60` | `ApplyOptionalSetting` âm thầm bỏ qua property không tồn tại ⇒ `PreciseConversion`/`Commission*` có thể không áp dụng mà result không ghi lại | a7 |
| 2 | P1 | `BoBacktestRunner.cs:137-159` | `ok` = `BacktestingError==None` + có JSON; không kiểm bot có chạy đúng (signal thiếu ⇒ 0 lệnh vẫn ok), không kiểm testingPeriod | b3 |
| 3 | P1 | `BacktestMapper.cs:27-38` | key tham số không khớp tên bot bị lờ đi (typo ⇒ cả sweep chạy default) | b1 |
| 4 | P1 | `BacktestMapper.cs:79-91` | `Enum.ToObject` không `IsDefined` ⇒ giá trị ngoài phạm vi âm thầm thành default | b2 |
| 5 | P1 | `BacktestMapper.cs:117` | `"tick-csv"` map thành `Ticks` server ⇒ âm thầm thay nguồn dữ liệu | tĩnh |
| 6 | P2 | `BoBacktestRunner.cs:115-130` | `AlgoRegistry.Install` chặn thread plugin 60 s rồi timeout; cũng là side effect cài .algo từ đường dẫn bất kỳ trong job | b5 |
| 7 | P2 | `BoBacktestRunner.cs:97-113` | không timeout/watchdog; job treo giữ slot vĩnh viễn; client timeout không huỷ được process | tĩnh |
| 8 | P2 | `BoBacktestRunner.cs:29-45` | `OnStop` terminate nhưng không ghi result ⇒ job mồ côi trong `claimed/`, client chờ tới timeout; không cơ chế nhả claim khi khởi động lại | tĩnh |
| 9 | P2 | `bridge_client.py:37-49` | `wait_result` không đọc heartbeat ⇒ plugin tắt thì chờ hết timeout | tĩnh |
| 10 | P2 | `BoBacktestRunner.cs:148-159` | nhúng cả `JsonReport` + `HtmlReport` vào 1 file result: 5.5 MB/run FRA40; không dọn `claimed/results` | đo 95 MB |
| 11 | P2 | — | không thu bot log (file có ở `Data\cBots\Combo\<guid>\` nhưng không map được về job) | quan sát |
| 12 | P3 | `BoBacktestRunner.cs:115-118` | robot đã đăng ký thì `algoPath` bị bỏ qua — job khai 1 .algo, chạy .algo khác mà không báo | tĩnh |
| 13 | P3 | `deploy_plugin.py` | 2 bản source (`bo_workflow` ↔ `Sources\Plugins`), deploy ghi đè sửa đổi trong IDE | tĩnh |
| 14 | P3 | `contracts.py` | `BacktestApiResult` bỏ `startedUtc/endedUtc/lastProgress` | tĩnh |
| 15 | P3 | `BoBacktestRunner.cs:202-209` | ghi atomic = delete + move (có khoảng trống ngắn) | tĩnh |

Chạy tốt: claim bằng `File.Move` nguyên tử; schema sai vào `failed/`; song song 8 ổn định; kết quả
tất định; enum theo tên hoặc số thứ tự; 2 unit test Python pass.

---

## 7. Đính chính record của Codex

- "72 pass ~5 phút thay vì 36–40 phút" — so với chạy **tuần tự**. CLI pool 8 song song cũng 300 s
  ⇒ lợi ích đến từ song song, không phải từ plugin.
- "MM50 cần chuẩn hóa BacktestingSettings (commission 30 vs 0)" — không phải commission (mọi lệnh
  commission = 0 ở cả hai). Nguyên nhân: margin ước tính theo tỷ giá xấp xỉ (§5). Chuẩn hoá settings
  trong plugin không sửa được trên 5.9.16.
- "PreciseConversion chưa được chứng nhận" — đã đo: **bị lờ đi**, run vẫn `ok`.
- Đồng ý với Codex: 1 core engine + executor backend; bridge chỉ là transport; không xoá CLI.

---

## 8. Khuyến nghị

1. **CLI pipeline = backend mặc định/duy nhất** cho optimize → walk-forward → Monte Carlo. Giữ
   `core/cli.py`, `core/runner.py` (không xoá như cân nhắc trước đó).
2. **Plugin: đóng băng spike**, không tích hợp vào pipeline. Nếu sau này muốn làm backend phụ,
   bắt buộc sửa #1–#10 trước, và thêm parity gate tự động (so `history.items` với CLI).
3. **Trigger quay lại plugin:** Spotware expose `BacktestingSettings.PreciseConversion` trong Plugin
   API mà CLI chưa có cờ ⇒ plugin là đường duy nhất tới tỷ giá thật ⇒ đáng đầu tư.
4. **Vấn đề margin look-ahead (§5)** quan trọng hơn chọn backend — cần quyết hướng xử lý trước khi
   chạy walk-forward trên symbol/tham số có cap kích hoạt nhiều.
5. Dọn dẹp chung (cả hai đường đều gây ra): `Data\cBots\Combo` đã **923 instance / 2.8 GB**; bridge
   95 MB. Chỉ dọn khi bạn đồng ý.

---

## 9. Tái lập

Script (scratchpad phiên, không nằm trong repo): `plugin_audit.py` (A+B), `ab_plugin_grid.py`,
`ab_cli_grid.py`, `plugin_vs_cli_trades.py`, `gui_vs_plugin_mm50.py`, `enddate_margin_test.py`.
Artifact: job/result tiền tố `audit-052306-*` (15 result + 2 failed), `audit-054016-end-*` (4),
`ab-plugin-052834-*` (72) trong `ctrader_api/bridge/`; experiment CLI
`runs/backtest/ab_cli_us30_grid72/` (72 run).
