# Research: `ctrader-cli` backtest / optimize — tài liệu + forum + mẹo chạy

Ngày 2026-09-09. Tổng hợp: tài liệu chính thức Spotware, `CLI-references` repo, changelog
`ctrader-console-docker`, forum cTrader, ClickAlgo, + probe trực tiếp trên VM-BO20.
Nguồn liệt kê ở §10. Probe của tôi đánh dấu **[đo]**; còn lại là **[tài liệu]** / **[forum]**.

---

## 1. Kết luận nhanh (cho người vội)

1. **1 backtest = 1 luồng.** Spotware xác nhận: *"Backtesting is a sequential single threaded
   process so it doesn't really benefit from multicore processors."* → pool N-process của
   pipeline **chính là mô hình GUI optimizer** (N pass đơn luồng song song), không phải hack.
   Đa lõi chỉ giúp `optimize`.
2. **Nút cổ chai = tải tick từ server.** Với indices (DOW/DAX/JP225…), >1 tuần tick = hàng trăm
   nghìn tick; mạng chậm → cТrader **tự kill tiến trình vì "not responding"**. Đây là cơ chế
   thật của "JP225 tick-hang" — flaky theo mạng/cache, đúng như Codex hiệu chỉnh.
3. **`--data-dir` KHÔNG nhanh hơn hiện trạng** — chỉ cache lần đầu chậm; cache ấm ≈ cache
   Spotware chung. Giá trị: tách biệt + khiêng đi được. Giữ opt-in.
4. **3 mẹo lớn chưa dùng:** `--environment-variables` (xoá hẳn rò rỉ credential), `metadata`
   (validate param/enum, không login), `tick-csv`+`--data-file` (dữ liệu tự cấp → hết phụ
   thuộc server, hết hang, deterministic).
5. **`optimize` chỉ có ở CLI 5.10 local edition.** Máy đang 5.9.12 bundled (+ 5.9.10) + 5.9.0
   standalone. Không có 5.10.
6. **`.algo` phải .NET 8 cho CLI mới.** Combo/MA Cross đang `net6.0` — chạy `backtest` trên
   5.9.12 OK, nhưng `optimize` / CLI 5.10 nhiều khả năng đòi rebuild .NET 8. **`ctrader-cli
   build` có .NET SDK kèm** → rebuild được không cần GUI (chưa test, cần cho phép).

---

## 2. Bức tranh phiên bản

| Kênh | Bản | optimize? | Ghi chú |
|---|---|---|---|
| Standalone (winget `Spotware.cTrader.CLI`) | **5.9.0.38** | ❌ | thiếu mọi fix 5.9.10/5.9.11 — **tránh dùng** |
| Bundled trong Desktop | **5.9.12.53118** (+ 5.9.10 cũ) [đo] | ❌ | có mọi fix tick/freeze/echo-stderr |
| CLI 5.10 "local edition" | — | ✅ `optimize`, `--cores`, grid/genetic, `.optres` | **chưa phát hành trên kênh máy này** |
| Docker `ghcr.io/spotware/ctrader-console:latest` | Linux, .NET SDK kèm | tuỳ tag | amd64+arm64 |

Changelog liên quan (`ctrader-console-docker` releases):
- **5.9.10**: *"Faster backtesting and optimization on tick data via removed per-quote scans
  over open positions"*; *"Fixed false stop-out incorrectly terminating backtests after final
  position closure"*; *"Diagnostic logging moved off main thread preventing freezes"*; *"native
  algo host exit errors reported instead of hanging"*.
- **5.9.11**: *"Fixed freeze after backtest completion with large reports"*; *"Optimization no
  longer retains finished pass charts in memory"*; **"CLI no longer writes startup echo to
  stderr"** (= fix rò rỉ credential mà Codex vá thủ công).

→ **Bắt buộc dùng bundled ≥5.9.11.** `settings._select_ctrader_cli()` chọn theo mtime của
`Spotware/cTrader/*/ctrader-cli.exe` — hiện trúng shim (tự resolve 5.9.12) [đo], nhưng logic
mtime có thể trúng nhầm `app_5.9.10/` nếu nó mới hơn; nên chốt bằng regex version.

---

## 3. `backtest` — tham chiếu flag đầy đủ

Nguồn: `spotware/CLI-references` (canonical) + `--help` [đo].

```
ctrader-cli backtest <algo> [<cbotset>] \
  --ctid=<c> --pwd-file=<p> --account=<a> [--broker=<b>] \
  --symbol=<s> --period=<tf> --start=<dd/MM/yyyy [hh:mm]> --end=<...> --data-mode=<m> \
  [--full-access] [--exit-on-stop] [--report-json=<p>] [--report=<p.html>] ...
```

| Flag | Ý nghĩa | Pipeline |
|---|---|---|
| `--data-mode` | `ticks` \| `m1` \| `m1-csv` \| `tick-csv` \| `open` | dùng `Ticks` (hoa cũng nhận) |
| `--data-file` | CSV cho `m1-csv`/`tick-csv` | **chưa dùng — xem §5.4** |
| `--data-dir` | thư mục cache giá, **dùng lại giữa các run** | opt-in |
| `--balance` | vốn khởi điểm | ✅ |
| `--commission` + `--commission-type` | `UsdPerMillionUsdVolume`(mặc định)/`UsdPerOneLot`/`PercentageOfTradingVolume`/`QuoteCurrencyPerOneLot` | để 0 (FTMO demo) |
| `--commission-auto` | dùng commission thật của symbol | cân nhắc cho "realistic" |
| `--spread` | override spread (pips) | để 0 |
| `--precise-conversion` | **tải tỷ giá lịch sử thật** để quy đổi P/L & margin | **xem §6 — quan trọng cho JP225/HK50/DE40** |
| `--report-json` / `--report` | xuất JSON / HTML | JSON ✅ |
| `--CustomParameter=<v>` / `--<Param>=<v>` | set param bot thẳng trên CLI (batch) | thay thế/bổ sung cbotset |
| `--environment-variables` / `-e` | đọc giá trị flag từ **biến môi trường** | **xem §8.1** |
| `--full-access` | bỏ giới hạn AccessRights (bot đọc CSV) | ✅ bắt buộc |
| `--exit-on-stop` | thoát process khi bot dừng (global option, exit 0) | ✅ |

- **Ngày UTC**, `dd/MM/yyyy [hh:mm]`. `--end` cho `backtest`: quan sát của pipeline là exclusive
  cận ngày → runner +1 ngày (đã verify khớp GUI).
- **Output Data folder**: `…/data/{cBotName}/{BacktestingInstanceID}/Backtesting/` chứa
  `events.json` (chi tiết lệnh), `log.txt`, `report.html`, `parameters.cbotset` — file bị ghi
  đè mỗi lần. `--report-json` là bản rich riêng, độc lập.
  → **`events.json` mà `fidelity_lib.py` cần CÓ TỒN TẠI** ở Data folder; pipeline chỉ chưa đi
  lấy (`{BacktestingInstanceID}` thay đổi mỗi run — phải quét mtime mới nhất).

---

## 4. `optimize` — tham chiếu (CLI 5.10, chưa chạy được)

```
ctrader-cli optimize <algo> --params=<params.json> \
  --ctid=<c> --pwd-file=<p> --account=<a> \
  --symbol=<s> --timeframe=<tf[,tf2,...]> --start=<...> --end=<...> --data-mode=<m> \
  [--method=grid|genetic] [--cores=N] [--criteria="A:max,B:min"] [--fitness] \
  [--auto-select-best] [--optres=<r.optres>] [--passes-dir=<dir>] [--precise-conversion]
```

- **`--timeframe` (KHÔNG `--period`)** — 1 giá trị pin, nhiều giá trị (`h1,h2`) quét. Ghi đè
  timeframe trong optset file.
- **`--cores`** mặc định = **½ số CPU + 1** (32 logical → 17). Điều chỉnh được khi đang chạy
  (GUI: "Resources" slider).
- **`--method`**: `genetic` mặc định (nhanh, không vét cạn); `grid` vét cạn (space nhỏ, ví dụ
  8×9=72 của ta). ClickAlgo/Neptune: *"grid… will take years"* cho space lớn.
- **`--criteria`** chỉ số: `NetProfit, ProfitFactor, MaxEquityDrawdownPercentages,
  MaxBalanceDrawdownPercentages, MaxEquityDrawdown, MaxBalanceDrawdown, WinningTrades,
  LosingTrades, TotalTrades, AverageTrade` — mỗi cái `:max` hoặc `:min`.
- **`--fitness`**: dùng `GetFitness()` trong .cs (loại trừ `--criteria`). Combo/MA Cross chưa có.

### `--params` JSON schema (tài liệu)

```json
{
  "parameters": [
    { "Name": "KslLevel", "Optimize": true, "Values": ["0","1","2","3","4","5","6","7"] },
    { "Name": "SlowPeriods", "Optimize": true, "Min": "5", "Max": "50", "Step": "5" },
    { "Name": "RiskPercent", "Value": "1.0", "Optimize": false }
  ]
}
```

- `Values` (rời rạc) HOẶC `Min/Max/Step` (dải). `ParameterType` optional (auto từ metadata).
- **"All unlisted parameters reset to defaults"** → phải liệt kê MỌI param, cả cái cố định
  (`"Optimize": false`). `optimizer.build_params_file` của pipeline đã làm đúng (dùng `fixed` =
  full param set).

### Output

- **`.optres`**: JSON, mọi pass, *"continue refining… across multiple local machines without
  losing progress"* → resume cấp job.
- **`--passes-dir/<pass>/`**: `.html` + `.txt` + `.json` + `.cbotset` mỗi pass. `.json` giả định
  = schema report.json → `metrics.parse_report` dùng lại (CHƯA đối chiếu artifact thật).

---

## 5. Dữ liệu & cache — cơ chế thật

### 5.1. Vì sao chậm / hang

- **[forum]** *"backtesting more than a week on indices such as the DOW, DAX, or NAS → hundreds
  of thousands of ticks… slow connection → takes a long time to download, causing the process
  to be **automatically terminated by cTrader for not responding**."*
- **[forum]** cТrader tick backtest 2016→nay từng mất **2 ngày**; nền C# tự viết: 12 phút.
- **[forum]** *"Backtesting Data is no longer cached"* sau update 4.4.x; *"cached data
  corruption"* → Spotware nhận *"known issue, resolved in upcoming update"*. Workaround chính
  thức từ Panagiotis: **tạo account backtest riêng** / đổi broker.
- **[forum]** JP225 cụ thể: *"set backtesting to minute data first, then back to tick data to
  get all the available history"* — **mẹo m1→ticks** cho instrument thiếu history tick.

→ "JP225 tick-hang" = **slow/stalled tick download + cТrader self-terminate**, phụ thuộc
mạng + trạng thái cache. Không tất định. Watchdog `STARTUP_STALL_SECONDS=900` của runner hiện
tại là hợp lý (cho tải lạnh 15 phút trước khi coi là stalled).

### 5.2. Cache mặc định (không `--data-dir`)

- `%APPDATA%\Spotware\Cache\<broker-profile>\BacktestingCache\V1\<account>\<symbol>\t1\*.zticks`
  (1 file/ngày). Vĩnh viễn trên đĩa. Desktop giữ ấm liên tục → CLI không `--data-dir` **dùng
  chung cache này** và thường đã ấm.
- **[đo]** US30 h1 Ticks 2 tháng: không `--data-dir` = **40.85s** (cache chung ấm).

### 5.3. `--data-dir`

- **[tài liệu]** *"Set --data-dir to a folder that persists between runs, so cTrader CLI reuses
  the downloaded price data instead of downloading it again."* Docker: map volume để giữ.
- **[đo]** cùng backtest: `--data-dir` **trống 357s** → ấm **40s** → ấm 41s. net không đổi.
- **[tài liệu troubleshooting]** *"When you optimise a symbol parameter, the engine downloads
  data for each new symbol on the first run. The run continues once the data is cached."*
- Kết luận: `--data-dir` = **cách ly + portable**, KHÔNG phải tăng tốc so với hiện trạng. Bật
  khi: chạy Docker, hoặc muốn cache riêng không lẫn Desktop (audit C3 nghi churn khi Desktop
  chạy song song). Giữ opt-in.

### 5.4. `tick-csv` / `m1-csv` + `--data-file` — deterministic, chưa dùng

- `--data-mode=tick-csv --data-file=<path.csv>` → dùng CSV **tự cấp**, **KHÔNG tải server**.
- Lợi: hết phụ thuộc mạng, hết hang, **bit-for-bit reproducible**, chạy offline/CI được.
- Hại: phải có sẵn CSV (export 1 lần từ GUI "Download Historical Data", hoặc Dukascopy +
  converter); khác dữ liệu server-ticks của GUI → chỉ đối chiếu được với chính nó.
- **Đề xuất**: với JP225/HK50 (hay hang), pipeline nên hỗ trợ nhánh `tick-csv`: 1 lần export
  full-history CSV, sau đó mọi backtest/optimize đọc `--data-file` → ổn định tuyệt đối.

---

## 6. Quy đổi tiền tệ & fidelity (JP225/HK50/DE40/FR40/SP35)

- **[tài liệu]** Backtest cТrader **mặc định dùng tỷ giá quy đổi LỊCH SỬ** (không phải
  hiện tại), tự dựng conversion chain ngắn nhất — cho cả GUI lẫn CLI.
- **`--precise-conversion`**: *"Download real historical exchange rates for accurate
  profit/margin conversion."* → khi conversion chain cần 1 symbol (vd USDJPY) chưa có history
  cache, cờ này **ép tải tỷ giá thật**; không có cờ có thể xấp xỉ.
- GUI có checkbox tương đương: **"Download historical data for additional symbols"**.
- **[forum 42336]** có báo cáo drawdown £6,700 xuất hiện/biến mất tùy độ dài khoảng test với
  cùng param — nghi liên quan dữ liệu quy đổi thiếu.
- **Hệ quả cho pipeline**: US30/GOLD/BTC quote USD → không cần. **JP225 (JPY) / HK50 (HKD) /
  DE40·FR40·SP35 (EUR)**: để CLI khớp GUI, phải biết GUI có bật "additional symbols" không, và
  **truyền `--precise-conversion` tương ứng**. Đây là biến fidelity chưa được kiểm soát —
  chính là lý do `combo_us30_h1_2026_gui_check` khớp hoàn hảo (USD, quy đổi ×1) còn JP225
  chưa từng đối chiếu được.

---

## 7. Phương án triển khai — xếp hạng

| # | Phương án | Khi nào | Đánh giá |
|---|---|---|---|
| **A** | **Pool N-process `backtest`** (hiện tại, fallback) | CLI 5.9.x, mọi lúc | ✅ **= mô hình GUI optimizer** (N pass đơn luồng //). Chi phí thừa: N login + N check cache. Đã verify khớp GUI. **Giữ làm mặc định tới khi có 5.10.** |
| **B** | **Native `optimize --cores`** | CLI 5.10 local + .algo .NET 8 | Lý tưởng: 1 login, cТrader tự chia pass, `.optres` resume. Chưa chạy được. Code khung đã có (`core/optimizer.py`, GATE chặn). |
| **C** | **Docker `ctrader-console`** | CI, cách ly, Linux | Image có .NET SDK kèm (build được). `--data-dir` = volume mount. Nhiều container = nhiều login (như A). Tốt cho reproducibility/CI, không giải quyết bài toán tốc độ. |
| **D** | **Plugin Backtesting API** (`BacktestingProcess`, `BacktestingSettings`) | cần chạy trong Desktop | cТrader Automate có API cho **plugin** tự phóng backtest in-process. 1 login, có thể //. NHƯNG chạy trong Desktop GUI, không headless — mâu thuẫn mục tiêu "thay GUI". Chỉ xét nếu muốn 1 nút "chạy cả matrix" trong Desktop. |
| **E** | **`tick-csv` + `--data-file`** | JP225/HK50, cần deterministic/offline | Ghép với A hoặc B. Xoá bỏ hang + phụ thuộc mạng. Cần export CSV 1 lần. **Nên thêm cho các symbol hay hang.** |

**Khuyến nghị**: A (mặc định) → thêm E cho symbol khó → B khi 5.10 về. C nếu chuyển sang CI/Linux.

---

## 8. Mẹo chạy (tricks)

### 8.1. `--environment-variables` — xoá hẳn rò rỉ credential ✅[đo]

Đặt `CTID`, `PWD-FILE` (hoặc `PWD_FILE`), `ACCOUNT`, `BROKER`, `START`, `END`, `DATA-MODE`,
`BALANCE`… vào **môi trường process**, thêm cờ `-e` → CLI đọc từ env, **argv không còn chứa
định danh** → CLI 5.9.0 dù echo argv cũng không lộ gì. Bảng param CLI in "Source: environment
variable". **Verify [đo]**: login thành công, Start/End/Balance/DataMode đọc từ env.
→ Thay `_redact_output`/`redact_text` bằng cơ chế này ở runner: an toàn theo thiết kế, không
phải scrub hậu kỳ.

### 8.2. `ctrader-cli metadata <algo>` — validate, KHÔNG login ✅[đo]

Xuất JSON đầy đủ: `PropertyName`, `Type`, `DefaultValue`, `EnumValues {name: index}`,
`MinValue`/`MaxValue`, `BuildTime`. **[đo]** Combo.algo khớp 100% `strategy.py` +
`params.py` (Ksl 8 mức, Ktp 9 mức, index đúng).
→ `strategy.py` nên **verify `param_defs` + FIB tuples với `metadata` lúc `run()`** (fail-fast
nếu .cs enum đổi mà pipeline chưa cập nhật). `BuildTime` vào provenance.

### 8.3. `ctrader-cli build <project>` — rebuild .algo không cần GUI (chưa test)

Changelog 5.9.10: *"Build and create commands now functional on Linux/Docker with included
.NET SDK"*. → `ctrader-cli build "Combo/Combo/Combo.csproj"` **có thể** build được .algo (kể cả
đổi `net6.0`→`net8.0`) mà không cần IDE. **Chưa test — ghi đè Combo.algo, cần người dùng cho
phép.** Nếu chạy được: mở khoá migration .NET 8 + mọi sửa .cs.

### 8.4. Mẹo m1 → ticks cho indices thiếu history ✅[forum]

Trước khi chạy tick thật cho JP225/HK50: chạy 1 backtest **`--data-mode=m1`** cùng range
trước → kéo full history → rồi chạy `--data-mode=ticks`. `backtest.py` warm-phase có thể làm
warm bằng m1 rồi mới tick.

### 8.5. Warm bằng range NGẮN rồi mở rộng

Tải lạnh 2 tháng = 357s [đo] nhưng dễ bị self-terminate ở range dài. Chia: warm 1 tuần →
1 tháng → full. Cache tích luỹ, không bị "not responding".

### 8.6. `DOTNET_gcServer=1` (chưa đo)

CLI là .NET (bundled .NET 8). Server GC tối ưu throughput đa lõi. Đặt env
`DOTNET_gcServer=1` cho process con — tương đương mẹo `<gcServer enabled="true"/>` trong
`ctrader.exe.config` mà ClickAlgo khuyên cho Desktop optimizer. Đáng đo cho native `--cores`.
(Desktop optimizer mặc định giới hạn <50% CPU "để máy còn dùng được".)

### 8.7. `--CustomParameter=` thay cbotset cho param đơn giản

Batch: `--RiskPercent=1.0 --KslLevel=2`. Giữ cbotset cho `SignalFilePath` + làm artifact,
nhưng có thể override nhanh không ghi file.

### 8.8. `--exit-on-stop` + kill grace ngắn

Sau report.json hợp lệ, CLI làm "several tasks at the end of the backtest" (forum: 100s+ cho
run dài) rồi mới thoát. `--exit-on-stop` giúp thoát sạch exit 0. Pipeline coi report hợp lệ =
OK bất kể kill → kill sau grace 10s là **cố ý bỏ hậu xử lý** (HTML report, events.json). Nếu
sau này cần `events.json` → nâng grace hoặc chờ process tự thoát.

### 8.9. Account backtest riêng ✅[forum, Panagiotis]

Workaround chính thức cho cache corruption: **tài khoản riêng chỉ để backtest**. Cân nhắc xin
FTMO 1 demo account thứ 2 dành riêng cho pipeline → tách cache, tách khỏi rủi ro Desktop dùng
chung account <FTMO-ACCOUNT-ID>.

### 8.10. `.optres` resume xuyên máy

Khi có 5.10: `.optres` cho phép dừng/tiếp optimize, gộp kết quả từ nhiều máy. Pipeline
`_ingest_passes` nên đọc cả `.optres` (tổng) lẫn `passes-dir` (chi tiết).

---

## 9. Đề xuất thay đổi pipeline (ưu tiên)

| Ưu tiên | Việc | Lý do |
|---|---|---|
| Cao | Chuyển auth sang `--environment-variables` (§8.1) | An toàn theo thiết kế, bỏ scrub hậu kỳ |
| Cao | `strategy.py` verify với `ctrader-cli metadata` lúc chạy (§8.2) | Fail-fast khi .cs enum lệch pipeline |
| Cao | Chốt chọn CLI bằng regex version ≥5.9.11, không mtime (§2) | Tránh trúng nhầm bản cũ |
| Trung | Thêm `--precise-conversion` (config, mặc định off) + warm "additional symbols" cho JP225/HK50/DE40/FR40/SP35 (§6) | Điều kiện cần để CLI khớp GUI trên symbol non-USD |
| Trung | Nhánh `tick-csv` + `--data-file` cho symbol hay hang (§5.4) | Deterministic, hết hang, offline |
| Trung | Warm-phase dùng m1 trước ticks cho indices (§8.4) | Kéo full history, tránh self-terminate |
| Thấp | Lấy `events.json` từ Data folder (quét `{BacktestingInstanceID}` mtime mới nhất) | Mở khoá `fidelity_lib.py` |
| Thấp | Thử `ctrader-cli build` để migrate .NET 8 (cần cho phép) (§8.3) | Chuẩn bị cho CLI 5.10 optimize |
| Thấp | Đo `DOTNET_gcServer=1` khi có native `--cores` (§8.6) | Throughput optimize |

---

## 10. Nguồn

**Tài liệu chính thức:**
- [cTrader CLI docs](https://help.ctrader.com/ctrader-cli/) · [Setup](https://help.ctrader.com/ctrader-cli/setup/) · [cBot ops / backtest](https://help.ctrader.com/ctrader-cli/cbots/) · [Optimisation](https://help.ctrader.com/ctrader-cli/optimisation/) · [FAQ](https://help.ctrader.com/ctrader-cli/faq/) · [Troubleshooting](https://help.ctrader.com/ctrader-cli/troubleshooting/)
- [`spotware/CLI-references`](https://github.com/spotware/CLI-references) — canonical flag reference
- [`spotware/ctrader-console-docker` releases](https://github.com/spotware/ctrader-console-docker/releases) — changelog 5.9.x
- [Currency conversion guide](https://help.ctrader.com/ctrader-algo/guides/currency-conversion/) · [Optimise a cBot (GUI)](https://help.ctrader.com/ctrader-algo/how-tos/cbots/optimise-a-cbot/) · [Plugin Backtesting API](https://help.ctrader.com/ctrader-algo/references/Plugin/Backtesting/)
- [Spotware news: cTrader CLI](https://www.spotware.com/news/ctrader-cli/)

**Forum:**
- [How to speed up back test? (Spotware: single-threaded)](https://community.ctrader.com/forum/ctrader-algo/23895/)
- [ctrader optimization under-utilizing resources](https://community.ctrader.com/forum/ctrader-support/24796/)
- [Backtesting Data is no longer cached](https://community.ctrader.com/forum/ctrader-algo/39366/)
- [cached data preventing optimisation](https://community.ctrader.com/forum/ctrader-algo/43597/)
- [BackTesting delay/freeze after running through data](https://community.ctrader.com/forum/ctrader-algo/36935/)
- [Backtesting hanging on start](https://community.ctrader.com/forum/ctrader-algo/41451/)
- [Problem with Historical Data for accurate currency conversion](https://community.ctrader.com/forum/ctrader-algo/42336/)
- [run a BackTest via CLI](https://community.ctrader.com/forum/suggestions/41437/) · [Walk forward optimization](https://community.ctrader.com/forum/suggestions/21049/)

**Cộng đồng khác:**
- [ClickAlgo: Utilise 100% CPU Optimising](https://clickalgo.com/cpu-optimisation) · [Backtest Data Options](https://clickalgo.com/backtest-data-options) · [Neptune Optimisation Handbook](https://clickalgo.com/neptune-optimisation-handbook) · [Historical Market Data](https://clickalgo.com/backtest-data)

**Probe trực tiếp [đo]** — VM-BO20, bundled CLI 5.9.12.53118, 2026-09-09:
- `--data-dir` cold 357.47s / warm 40.04s / no-flag 40.85s (US30 h1 Ticks 2 tháng, net 292.61 mọi lần)
- `--environment-variables`: login OK, Start/End/Balance/DataMode "Source: environment variable"
- `metadata Combo.algo`: khớp 100% `strategy.py`/`params.py`, BuildTime 2026-09-09T07:31Z
- `--commands` (5.9.12): không có `optimize` · csproj Combo/MA Cross = `net6.0`
- bundled trên đĩa: `app_5.9.10.52700` + `app_5.9.12.53118`; standalone 5.9.0.38; **không có 5.10**
