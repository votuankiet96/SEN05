# Chạy đa luồng/song song nhiều tiến trình `ctrader-cli.exe backtest` — bằng chứng và khuyến nghị

Phạm vi: RESEARCH thuần tuý (không code, không chạy backtest/optimize, không sửa
`facilitator.py`/`cli_runner.py`). Trả lời câu hỏi: chạy N tiến trình `ctrader-cli backtest`
đồng thời có an toàn/hợp lý không, ở mức nào, dựa trên bằng chứng nào.

**Cảnh báo quan trọng cần đọc trước khi dùng tài liệu này**: nghiên cứu này CHỈ nói về rủi ro
*concurrency* (số tiến trình chạy đồng thời tại một thời điểm — CPU/RAM/tranh chấp tài
nguyên/có bị chặn kỹ thuật không). Đây **KHÔNG PHẢI** nguyên nhân vụ khoá tài khoản FTMO
<FTMO-ACCOUNT-ID> ngày 15/09/2026 ("hyperactivity") — nguyên nhân đó đã điều tra riêng và là **tổng số
simulated trades tích luỹ** (~181,652 lệnh mô phỏng trong 2 ngày, ~90 lần ngưỡng 2000/ngày của
FTMO), không phải số login hay số tiến trình song song. Xem mục 3 để không nhầm 2 khái niệm.

---

## 1. Tóm tắt bằng chứng đã đo THẬT trên máy này (VM-BO20)

### 1.1. Kiến trúc: N song song = N tiến trình `ctrader-cli.exe` = N login riêng biệt

- `bo_workflow/cli_engine/cli_runner.py::run_backtest()` — mỗi `RunSpec` tạo đúng 1
  `subprocess.Popen` riêng (1 process OS = 1 backtest). `bo_workflow/core_engine/facilitator.py`
  chỉ dùng `ThreadPoolExecutor(max_workers=workers)` để **điều phối gọi** N subprocess song
  song từ phía Python — bản thân Python thread không tính toán gì, chỉ `Popen` + `poll()`.
- `bo_workflow/reports/pipeline-parallelism-and-cli-tick-hang-audit-2026-09-09.md` §1: xác
  nhận bằng thực nghiệm **N worker = N tiến trình = N lần login riêng biệt** vào cùng account
  — khác hẳn GUI Desktop (1 login, N thread nội bộ chia sẻ 1 kết nối + dataset trong RAM). Đã
  thử tìm cách "1 login nhiều lệnh" qua **interactive shell** của `ctrader-cli` — **thất bại**:
  shell từ chối mọi cờ `--data-mode`/`--balance`/`--report-json` cần thiết
  (`"--name=value / -x flags are launch-time arguments and are rejected inside the shell"`) →
  N-login là **ràng buộc của công cụ**, không phải lựa chọn thiết kế tuỳ ý.
- `bo_workflow/reports/ctrader-cli-practical-issues-catalog-2026-09-10.md` §G2: Spotware xác
  nhận qua forum — *"Backtesting is a sequential single threaded process so it doesn't really
  benefit from multicore processors"* → **mỗi backtest chỉ tính toán trên 1 thread**. Vì vậy
  pool N-process của hệ thống này **chính là mô hình GUI Optimizer** (N pass đơn luồng chạy
  song song), không phải một cách lách luật hay hack.

### 1.2. Benchmark thông lượng đã đo trực tiếp

| Mức song song | Kết quả | Nguồn |
|---:|---|---|
| 3–4 | 0 lỗi auth, login OK | `pipeline-parallelism-and-cli-tick-hang-audit-2026-09-09.md` |
| 8 | 72/72 pass OK, wall 300.0s (31.5s/pass, gồm ~8s preflight) | `ab-plugin-api-vs-cli-2026-09-11.md` §4.3 |
| 10 | 100/100 tổ hợp, 0 failed (pipeline PowerShell cũ, 2026-09-02) | `pipeline-parallelism-and-cli-tick-hang-audit-2026-09-09.md` |
| 16 | 16/16 OK, 0 lỗi, wall 97s (so ~560s tuần tự = **5.8x**) | `pipeline-parallelism-and-cli-tick-hang-audit-2026-09-09.md` |

Ghi chú đi kèm bảng 16-way: **~570 MB RAM/tiến trình**, **CPU bão hoà quanh mức 16** trên máy
32 logical CPU — tức throughput ngừng tăng thêm sau 16, không phải máy bị treo/lỗi.

**Đối chứng cùng engine, khác cơ chế spawn** (§2 và §4.3 của `ab-plugin-api-vs-cli-2026-09-11.md`):
Plugin Backtesting API của cTrader Desktop (`Backtesting.Start`) thực chất **chính là Desktop tự
spawn `ctrader-cli.exe backtest`** với cùng bộ cờ pipeline Python đang dùng (+`--port` thay vì
file mật khẩu). Chạy 8 job qua plugin: 291.1s. Chạy 8 job qua CLI pipeline Python: 300.0s. Kết
quả trùng khít (72/72 `history.items` trùng tuyệt đối, 442/442 lệnh FRA40 trùng từng trường) →
**ai đứng ra spawn tiến trình (Desktop hay Python) không ảnh hưởng gì đến hành vi/hiệu năng** —
cùng là N tiến trình `ctrader-cli.exe` cùng account.

### 1.3. CPU/RAM đo trực tiếp khi 8–9 tiến trình chạy song song

Từ `bo_workflow/reports/ctrader-backtest-api-plugin-optimize-record-2026-09-11.md` §5 (máy VM-BO20,
32 logical CPU, ~48 GB RAM), đo khi cTrader Desktop (plugin) đang chạy `MaxConcurrentJobs=8`
(thực tế 9 `ctrader-cli.exe` cùng lúc — có thêm 1 job CLI pipeline Python khác chạy chồng lên
ngoài dự kiến của phép đo):

| Time | running | CPU tổng | RAM dùng | cTrader working set |
|---|---:|---:|---:|---:|
| 01:17:07 | 8 | 29.2% | 29.6% | 2136 MB |
| 01:17:14 | 8 | 65.9% | 44.1% | 2646 MB |
| 01:17:21 | 8 | 81.3% | 60.1% | 5538 MB |
| 01:18:54 | 8 | 86.7% | 59.2% | 5875 MB |

Kết luận vận hành ghi trong báo cáo gốc (tác giả: Codex): *"CPU/RAM chưa vượt ngưỡng, nhưng có
spike CPU/RAM đáng kể. Nên production default ban đầu là 4 hoặc 6, sau đó benchmark 8 theo
symbol/range."* Đây chính là số liệu đứng sau dòng comment "8 song song vẫn dư CPU/RAM (audit
plugin 2026-09-11)" trong `facilitator.py`.

**Lưu ý về điều kiện đo — quan trọng để không suy diễn quá tay**: bảng CPU/RAM ở trên đo lúc
**cTrader Desktop app đang mở và tự chạy 8 backtest con qua plugin**, cộng thêm 1 tiến trình CLI
khác — tức luôn có chi phí nền của bản thân Desktop UI. Benchmark "16-way, 97s, 0 lỗi" (mục 1.2)
là chạy **CLI thuần từ Python**, không ghi lại CPU%/RAM% cụ thể tại từng thời điểm, chỉ ghi định
tính "CPU bão hoà ~16". Hai bộ số liệu không hoàn toàn cùng điều kiện (có Desktop hoạt động hay
không) — kết luận "12 an toàn" trong code hiện tại là **nội suy hợp lý giữa 2 bộ dữ liệu**, không
phải một điểm đo trực tiếp tại đúng mức 12.

### 1.4. Bottleneck thật của backtest tick-based: I/O/mạng, không phải CPU máy này

`bo_workflow/reports/cli-tick-bottleneck-diagnosis-2026-09-10.md` + `ctrader-cli-practical-issues-catalog-2026-09-10.md`
tách được **3 chế độ** biệt lập bằng đo trực tiếp:

- **Chế độ A** (USD-quote + cache ấm): chạy nhanh, tính toán xong 20 tháng tick trong ~225–239s.
  Không có gì để tối ưu — không phải nút thắt.
- **Chế độ B** (tải tick lạnh): **network/server-bound**, không phải CPU máy này. Đo: US500
  (cache 0 file) tải+chạy 20 tháng = 841s; US100 tải 80% sau 37 phút; trong lúc chậm **cache
  file tăng đều, disk ghi, network tải** — đúng dấu hiệu I/O-bound. Khớp với forum: *"backtesting
  more than a week on indices... hundreds of thousands of ticks... slow connection... causing
  the process to be automatically terminated by cTrader for not responding"* (trích trong
  `ctrader-cli-backtest-optimize-research-2026-09-09.md` §5.1, nguồn forum liệt kê ở mục 6).
- **Chế độ C** (bug engine khi tick + symbol cần quy đổi tiền tệ, vd JP225→USDJPY): CPU ~0–7%,
  disk phẳng, cache KHÔNG tăng file, nhưng process đơ hàng chục phút — đây là **bug nội bộ CLI**,
  không liên quan tài nguyên máy hay concurrency.

→ Watchdog `DEFAULT_STARTUP_STALL_SECONDS = 900` trong `cli_runner.py` (0% có thể là tải cache
lạnh, im lặng nhiều phút) là giả định **khớp với chế độ B đã đo thật**, không phải suy đoán suông.

### 1.5. Cache tick dùng CHUNG giữa GUI và CLI — an toàn khi ĐỌC, chưa test race-condition khi GHI đồng thời

`ctrader-cli-practical-issues-catalog-2026-09-10.md` §B2: CLI (không `--data-dir`) đọc/ghi
**cùng thư mục** `%APPDATA%\Spotware\Cache\<broker>\BacktestingCache\V1\<account>\<symbol>\t1\*.zticks`
với GUI Desktop — đã quan sát mtime của cùng file bị cả 2 nguồn ghi, không thấy lỗi đọc khi
nhiều process cùng đọc cache đã có sẵn (vd US30 986 file, dùng chung bởi nhiều lượt chạy song
song không lỗi). **Chưa có test riêng biệt** cho tình huống 2+ process cùng lúc TẢI MỚI (ghi
lần đầu) đúng 1 ngày/1 symbol chưa có cache — đây là khoảng trống, liệt vào mục 5.

### 1.6. Chạy song song CLI trong khi Desktop GUI cũng đang mở/chạy job — CHƯA kết luận được

`ctrader-cli-practical-issues-catalog-2026-09-10.md` §G3, tự đánh giá "Chưa rõ": Desktop
(`ctrader.exe`) luôn chạy trên máy này, cùng login account <FTMO-ACCOUNT-ID>, cùng hit cache + cùng
account server session với CLI. Quan sát định tính: "các probe CLI vẫn chạy được khi Desktop
mở; nhưng batch dài hay lỗi hơn probe đơn lẻ" — nhưng **chưa tách được biến** Desktop mở/đóng
bằng một phép so sánh A/B rõ ràng. Riêng bảng CPU/RAM ở mục 1.3 xác nhận: có ít nhất 1 lần đã
chạy thành công 8 job Desktop-plugin + 1 job CLI-Python cùng lúc (9 tiến trình `ctrader-cli.exe`
tổng cộng) mà không ghi nhận lỗi đặc biệt nào — nhưng đây là quan sát đơn lẻ, không phải benchmark
có chủ đích cho câu hỏi này.

### 1.7. Không có bằng chứng nào ghi nhận login/tiến trình song song từng bị server từ chối

Ở mọi mức đã thử (3, 4, 8, 9, 10, 16) — **0 lỗi authentication, 0 lỗi "account cannot be
found", 0 dấu hiệu rate-limit/reject từ phía server** liên quan tới số lượng song song. Toàn bộ
lỗi đã catalog trong `ctrader-cli-practical-issues-catalog-2026-09-10.md` (invocation sai cú
pháp, bug quy đổi tiền tệ, tick tải chậm, vòng đời process...) đều có nguyên nhân KHÁC, không
phải do "quá nhiều login cùng lúc".

---

## 2. Bằng chứng/tài liệu bên ngoài (fetch trực tiếp 2026-09-20, trích nguồn)

### 2.1. Spotware xác nhận chính thức: CÓ hỗ trợ chạy nhiều backtest song song, không nêu giới hạn

[Backtest in plugins — cTrader Help Centre](https://help.ctrader.com/ctrader-algo/documentation/plugins/backtesting/):

> "When launching backtesting programmatically, you can launch several backtesting processes in
> parallel, which potentially could save you plenty of time."

Không có bất kỳ cảnh báo/giới hạn số lượng cụ thể nào đi kèm câu này trên trang tài liệu.

### 2.2. Không tìm thấy giới hạn kỹ thuật công bố nào riêng cho `ctrader-cli backtest`/login đồng thời

Đã kiểm tra: `help.ctrader.com/ctrader-cli/` (setup, cbots, optimisation, faq, troubleshooting),
`github.com/spotware/CLI-references`, `help.ctrader.com/ctrader-algo/documentation/rate-limits/`
— **không trang nào công bố trần số backtest hay số login đồng thời cho `ctrader-cli`.**

Trang [Rate limits — cTrader Algo](https://help.ctrader.com/ctrader-algo/documentation/rate-limits/)
chỉ áp dụng cho **hoạt động trading** qua kết nối đã xác thực (đặt lệnh 500/phút, huỷ lệnh
100/phút, sửa lệnh 100/phút, đóng vị thế 2.000/phút, sửa protection 1.000/phút (level 1) +
5.000/15 phút (level 2)) — **không đề cập backtest/optimize**, không đề cập số kết nối đồng thời.

### 2.3. Open/Connect API (khác cơ chế của `ctrader-cli`, nhưng liên quan tới câu hỏi "trần cứng có tồn tại không")

- [Forum: What is the maximum concurrent request for each app connected to demo.ctraderapi.com?](https://community.ctrader.com/forum/connect-api-support/21640/)
  và [Forum: Limits on concurrent request api](https://community.ctrader.com/forum/connect-api-support/37482/):
  trích dẫn tài liệu Spotware — **"50 requests per second per application. 25 concurrent
  connections per application"** cho request không phải dữ liệu lịch sử; **5 requests/second**
  cho historical data.
- Nhân viên Spotware (Panagiotis Charalampous) trả lời về việc nới trần: *"Unfortunately there
  is no such option at the moment"* nhưng *"We offer higher limits to our clients"* (theo trường
  hợp cụ thể); nhân viên khác (amusleh): *"No, we don't have any plan to increase the current
  limits for now."*
- **CẢNH BÁO SUY DIỄN**: đây là giới hạn của **Open/Connect API** — cơ chế xác thực bằng OAuth
  app-credentials, dùng để xây ứng dụng bên thứ 3 kết nối `demo.ctraderapi.com`/`live.ctraderapi.com`.
  `ctrader-cli` xác thực bằng `--ctid` + `--pwd-file` (giống đăng nhập desktop client bằng tài
  khoản cá nhân), **là cơ chế khác**. **CHƯA có bằng chứng chính thức nào nối 2 khái niệm** —
  không rõ `ctrader-cli` có bị tính vào cùng hạn ngạch "concurrent connections per application"
  của Open API hay không, hay dùng một cơ chế đếm hoàn toàn riêng (giống Desktop). Không nên
  khẳng định trần 25 áp dụng cho `ctrader-cli`; chỉ nêu ra như bối cảnh nền tảng có tồn tại khái
  niệm "concurrent connection limit" ở một hệ con khác của cùng platform.

### 2.4. Cộng đồng: chạy nhiều instance cùng 1 account là khả thi kỹ thuật, không bị nền tảng tự chặn

[Forum: Multiple cTrader's Running Same cBots?](https://community.ctrader.com/forum/ctrader-support/24757/):
user xác nhận chạy cùng lúc trên VPS + máy desktop, cùng account — chạy được về mặt kỹ thuật,
nhưng cBot đặt lệnh trùng vì *"trade operation results cannot be shared between separate
instances"*. Trả lời chính thức từ Spotware: *"The same cBot can run concurrently on different
machines... you need to handle the concurrency issues yourself."* — **không có cảnh báo về giới
hạn số session hay bị tự động đăng xuất.**

Liên hệ tới hệ thống này: rủi ro "đặt lệnh trùng" trong thread trên là rủi ro **live trading**
(nhiều instance cùng quản lý 1 vị thế thật) — **không áp dụng trực tiếp cho backtest** (mỗi
tiến trình backtest độc lập, không tranh chấp state vị thế thật với nhau). Điều rút ra được chỉ
là: nền tảng không có cơ chế tự chặn multi-login cùng account.

### 2.5. PHÁT HIỆN MỚI, ngoài phạm vi mọi tài liệu nội bộ hiện có: CLI 5.10.1 vừa phát hành 18/09/2026

[GitHub spotware/ctrader-console-docker releases](https://github.com/spotware/ctrader-console-docker/releases)
(fetch 20/09/2026) — bản mới nhất là **5.10.1, ngày 18/09/2026** (2 ngày trước ngày viết báo cáo
này). Changelog xem tại [tag 5.10.1](https://github.com/spotware/ctrader-console-docker/releases/tag/5.10.1):

- **"New `optimize` command — genetic and grid methods, optimization criteria, `--cores`,
  `--fitness`, `--auto-select-best` and `.optres` export"** — lệnh `optimize` mà mọi báo cáo nội
  bộ (đến tận 2026-09-17) đều ghi nhận "chưa tồn tại, cần CLI 5.10" **giờ đã tồn tại**.
- Thêm: Monte Carlo simulation (`--mc-method`, `--mc-iterations`...), `tick-csv` data mode,
  `--commission-type`/`--commission-auto`, tải "historical data for additional symbols",
  `--redownload-data`, "fewer per-tick allocations and noticeably lower memory usage".
- [help.ctrader.com/ctrader-cli/optimisation/](https://help.ctrader.com/ctrader-cli/optimisation/)
  (đã cập nhật theo bản mới): `--cores` **"Defaults to half the processor count plus one"** —
  *"number of parallel workers that run the backtest leg of each pass in parallel. Each worker
  uses one CPU thread, so the run uses roughly the same number of threads as the value you
  pass."* Khuyến nghị: *"Lower it on a small VPS to leave room for other workloads, or raise it
  on a dedicated machine to spread a genetic sweep across more threads."*

**Ý nghĩa cho câu hỏi concurrency đang nghiên cứu**: mô hình `optimize --cores` là **1 tiến
trình, 1 login, N thread nội bộ** — khác hẳn kiến trúc N-tiến-trình/N-login hiện tại của
`bo_workflow`. Điều này **không làm sai bất kỳ số liệu nào đã đo** (mọi benchmark ở mục 1 vẫn
đúng cho cách dùng `backtest` pool N-process hiện tại), nhưng có nghĩa là toàn bộ câu hỏi "N
login đồng thời có an toàn/hợp lý không" có thể sớm trở thành câu hỏi ít liên quan hơn nếu
chuyển sang `optimize --cores` — **đây là quyết định kiến trúc lớn, ngoài phạm vi báo cáo
concurrency thuần tuý này**, chỉ ghi nhận để không bỏ sót, không đề xuất hành động cụ thể ở đây.
**Chưa xác minh**: CLI cài trên VM-BO20 hiện tại đã là 5.10.1 hay vẫn bản cũ (report gần nhất đo
được là 5.9.16, ngày 2026-09-11/12); `.algo` hiện tại build `net6.0` có tương thích với runtime
mới hay không (changelog ghi "migrated to .NET 10").

**[Cập nhật 2026-09-20, sau khi kiểm tra trực tiếp]** — VM-BO20 vẫn ở bản cũ: bundled CLI (theo
Desktop, `%LOCALAPPDATA%\Spotware\cTrader\...\app_5.9.16.53348\`) báo `Version: 5.9.16.53348`;
standalone CLI (`%LOCALAPPDATA%\Programs\cTrader CLI\`, cài qua winget manifest) báo
`Version: 5.9.0.38`. `select_cli()` hiện đang chọn bản 5.9.16.53348 (cao hơn). **KHÔNG có 5.10.1
ở đâu trên máy này.**

Đối chiếu `help.ctrader.com/ctrader-cli/setup/` (fetch 20/09/2026) — cTrader CLI công bố đúng
**3 kênh phân phối chính thức cho Windows**: (1) `winget install Spotware.cTrader.CLI` (gói
standalone, tracker bên thứ ba `wingetgui.com` vẫn chỉ thấy 5.9.0, chưa có dấu hiệu 5.10.x),
(2) bundled sẵn trong cTrader Desktop (chưa tìm thấy bằng chứng Desktop đã có bản build nào bundle
CLI 5.10.1 — trang tải chính thức `ctrader.com/download` không hiện số phiên bản), (3)
`docker pull ghcr.io/spotware/ctrader-console:latest` — **đây chính là kênh duy nhất đã xác nhận
thật sự có 5.10.1** (repo `spotware/ctrader-console-docker`, tag `5.10.1`, phát hành 18/09/2026).

**Kết luận**: 5.10.1 với `optimize --cores` là có thật, nhưng **hiện chỉ xác nhận tồn tại dưới
dạng Docker image**, không phải bản cài đặt Windows native (winget/Desktop-bundled) như 2 kênh
`bo_workflow` đang dùng. Muốn dùng 5.10.1 hôm nay sẽ phải cài Docker lên VM-BO20 (Windows Server
2022 — cần Docker Desktop/Engine, có thể cần bật tính năng ảo hoá, và **thiết kế lại hẳn cách
`cli_engine/cli_runner.py` gọi CLI** — từ `subprocess.Popen([ctrader-cli.exe, ...])` sang
`docker run ...`, kèm câu hỏi mở: container có dùng chung được tick cache/session Desktop hiện có
không, hay cần cơ chế auth/cache hoàn toàn riêng). Đây đúng là "quyết định kiến trúc lớn" đã nói ở
trên — KHÔNG phải một lượt cài đặt đơn giản, và chưa được thực hiện (đợi quyết định riêng).

### 2.6. Xác nhận thêm: bottleneck backtest tick là network/download, không phải tính toán

Phù hợp với đo nội bộ (mục 1.4): forum [BackTesting delay/freeze after running through data](https://community.ctrader.com/forum/ctrader-algo/36935/)
và các thread liệt kê ở mục 6 mô tả hiện tượng tương tự "treo sau khi tải xong dữ liệu"/"treo khi
tải dữ liệu lớn". Changelog 5.9.10 (trích qua research nội bộ, cùng nguồn GitHub releases ở trên)
ghi *"Faster backtesting and optimization on tick data via removed per-quote scans over open
positions"* — đây là vá hiệu năng **tính toán** (thuật toán quét vị thế mở), khác với bottleneck
**tải mạng** — tức Spotware có cả 2 loại vấn đề hiệu năng riêng biệt trong lịch sử phát triển
CLI, khớp với việc hệ thống này quan sát thấy 2 chế độ hỏng khác nhau (B = mạng, C = bug engine).

### 2.7. Không tìm thấy tài liệu nào về cơ chế khoá file cho cache tick khi nhiều process cùng ghi

Không có mục nào trong `help.ctrader.com` hay `CLI-references` mô tả file-locking cho
`BacktestingCache`. Forum có ghi nhận lỗi ["cTrader Cached Data not Available or Corrupted"](https://community.ctrader.com/forum/ctrader-algo/42686/)
là "known issue" mà Spotware từng nói "resolved in upcoming update" — nhưng **không có bằng
chứng nào liên kết trực tiếp lỗi này với việc chạy nhiều process song song**; có thể do nguyên
nhân khác (cập nhật phần mềm giữa chừng, hết dung lượng đĩa, cache không tương thích phiên bản).

---

## 3. Phân biệt rõ 2 loại rủi ro — KHÔNG gộp chung

### (A) Rủi ro CONCURRENCY — số tiến trình chạy đồng thời tại một thời điểm

- Bản chất: rủi ro **hiệu năng/tài nguyên máy** (CPU, RAM, tranh chấp đọc/ghi cache) và câu hỏi
  kỹ thuật "server/nền tảng có chặn N-login đồng thời không".
- Bằng chứng: mục 1 — đã đo tới 16 song song, 0 lỗi, CPU/RAM tăng có kiểm soát, throughput
  plateau ở 16 trên máy 32-logical-CPU.
- **KHÔNG có bằng chứng nào** (nội bộ lẫn bên ngoài) cho thấy số lượng tiến trình/login đồng
  thời từng là nguyên nhân trực tiếp gây khoá tài khoản <FTMO-ACCOUNT-ID> ngày 15/09/2026.

### (B) Rủi ro TỔNG SỐ REQUEST/SIMULATED TRADES TÍCH LUỸ — đã có kết luận riêng, KHÔNG phải trọng tâm báo cáo này

- Nguyên nhân khoá tài khoản 15/09/2026 (theo điều tra riêng, sampling ~250 report.html) là
  **tổng số simulated trades** sinh ra từ chạy backtest hàng loạt (~181.652 lệnh mô phỏng trong
  2 ngày, ~90 lần ngưỡng 2.000/ngày của FTMO) — **không phải** số lần login hay số tiến trình
  song song. Giả thuyết "re-authentication mỗi lần chạy gây khoá" đã bị bác bỏ vì thiếu bằng
  chứng.

### Vì sao 2 việc này ĐỘC LẬP về mặt toán học — và vì sao dễ nhầm

- Chạy 1 tiến trình tuần tự trong 10 giờ có thể sinh ra **cùng tổng số simulated trades** như
  chạy 16 tiến trình song song hoàn thành trong 40 phút. Cái quyết định rủi ro (B) là **TỔNG SỐ
  backtest × số lệnh/backtest thực hiện trong 1 cửa sổ 24h** (theo giờ reset 00:00 Prague của
  FTMO), bất kể chạy nối tiếp hay song song.
- **Hệ luỵ thực tế**: tăng mức song song (concurrency) làm hoàn thành CÙNG một khối lượng công
  việc **nhanh hơn về mặt đồng hồ treo tường**, nhưng **không** làm tổng số lệnh mô phỏng sinh ra
  giảm đi. Nếu muốn giảm rủi ro (B), đòn bẩy đúng là giảm **tổng số backtest/ngày** hoặc tách
  sang tài khoản demo backtest riêng (xem mẹo cộng đồng đã ghi trong
  `ctrader-cli-backtest-optimize-research-2026-09-09.md` §8.9 — Panagiotis đề xuất "tài khoản
  backtest riêng" như workaround chính thức cho vấn đề khác, nhưng cũng hợp lý để tách rủi ro
  hyperactivity khỏi account live) — **không phải** giảm `max_parallel`.
- **Suy diễn cần tránh**: "giảm song song để né FTMO hyperactivity" — đây SẼ LÀ lặp lại đúng kiểu
  sai lầm "giả thuyết thiếu bằng chứng" đã bị bác bỏ trước đó. `max_parallel` ảnh hưởng tới
  **tốc độ hoàn thành**, không ảnh hưởng tới **tổng khối lượng** — 2 đại lượng khác nhau.

---

## 4. Khuyến nghị mức độ song song an toàn + căn cứ

1. **Giữ `MAX_PARALLEL_CAP = min(16, cpu_count)`** — có căn cứ đo trực tiếp (16-way: 16/16 OK,
   0 lỗi, throughput ngừng tăng thêm sau 16 trên máy 32-logical-CPU — mục 1.2).
2. **`DEFAULT_MAX_PARALLEL = 12` là lựa chọn thận trọng hợp lý, nhưng chưa có điểm đo trực tiếp
   TẠI đúng mức 12** — bằng chứng có ở 3, 4, 8, 9, 10, 16, không có ở 12. Đây là nội suy an toàn
   giữa 8 (đã đo CPU spike tới 86.7%, RAM 60.1%, nhưng dưới điều kiện Desktop cũng đang tự chạy
   8 job) và 16 (đã đo OK nhưng đó là CLI thuần từ Python, không rõ CPU%/RAM% cụ thể). Coi 12 là
   **"chưa có phản chứng"**, không phải "đã chứng minh là điểm tối ưu".
3. **Không cần giảm `max_parallel` vì lo ngại lặp lại vụ khoá FTMO 15/09** — đó là rủi ro loại
   (B), độc lập với concurrency (mục 3). Nếu muốn phòng (B), xử lý ở tầng khác (giới hạn tổng số
   backtest/ngày, tài khoản demo riêng cho backtest) — không phải ở `max_parallel`.
4. **Nếu cTrader Desktop GUI đang mở VÀ tự chạy job (backtest/optimize thủ công) cùng lúc với
   batch Python** — nên cân nhắc giảm `max_parallel` tạm thời trong lúc đó, vì Desktop cũng tiêu
   thụ CPU/RAM giống một "worker" ẩn, không nằm trong con số `max_parallel` mà Python kiểm soát
   (mục 1.6, G3 catalog nội bộ vẫn ghi "chưa kết luận" — đây là khuyến nghị phòng ngừa, không
   phải dựa trên benchmark định lượng riêng cho tình huống này).
5. Nếu muốn có bằng chứng chắc hơn cho đúng con số 12 (thay vì nội suy), nên đo lại CPU%/RAM% cụ
   thể ở mức 12 và 16 khi chạy **CLI thuần từ Python, Desktop đứng yên không chạy job khác** —
   để tách bạch điều kiện đo giữa 2 bộ dữ liệu hiện có (mục 1.3).
6. **Theo dõi CLI 5.10.1** (mục 2.5) — nếu xác nhận `optimize --cores` hoạt động ổn định và khớp
   GUI trên máy này, đây là kiến trúc thay thế đáng cân nhắc về lâu dài (1 login, N thread nội
   bộ trong 1 process, né hẳn câu hỏi "N login đồng thời"). Đây là **quyết định kiến trúc lớn,
   cần một phiên đánh giá riêng** (xác minh version đang cài, tương thích `.algo` net6.0, parity
   với kết quả GUI/CLI hiện tại) — ngoài phạm vi báo cáo này, chỉ nêu để không bỏ sót.

---

## 5. Chưa có bằng chứng / còn chưa rõ (liệt kê thẳng, không lấp bằng suy đoán)

1. **Không có test đo trực tiếp trần thật của server/broker** cho số login/tiến trình đồng thời
   — mọi mức đã thử (tới 16) đều pass; không biết trần thật (nếu có) nằm ở đâu cao hơn 16, hay
   liệu có tồn tại trần cứng phía server nào không.
2. **Không rõ `ctrader-cli` (đăng nhập bằng `--ctid`/`--pwd-file`) có bị tính vào cùng hạn ngạch
   "25 concurrent connections per application" của Open/Connect API hay không** — đây là 2 cơ
   chế xác thực khác nhau (mục 2.3), chưa có xác nhận chính thức nối 2 khái niệm.
3. **Chưa có test race-condition khi ghi cache tick**: 2+ tiến trình cùng tải MỚI (chưa có cache)
   đúng 1 symbol/1 ngày tại CÙNG một thời điểm — chỉ có bằng chứng gián tiếp rằng ĐỌC cache đã có
   sẵn từ nhiều nguồn (GUI + nhiều lượt CLI) là an toàn.
4. **"Chạy song song CLI khi Desktop đang mở/đang tự chạy job"** — G3 trong catalog nội bộ vẫn
   ghi "chưa kết luận"; quan sát "batch dài hay lỗi hơn probe đơn lẻ" chỉ là định tính, chưa có
   một phép so sánh A/B định lượng riêng cho biến "Desktop mở hay đóng".
5. **Chưa xác minh CLI đang cài trên VM-BO20 hiện tại đã là 5.10.1 (phát hành 18/09/2026) hay
   vẫn bản cũ** (report gần nhất đo trực tiếp trên máy này ghi nhận 5.9.16, ngày 2026-09-11/12).
6. **Không có khuyến nghị định lượng chính thức nào từ Spotware** về "an toàn tới bao nhiêu tiến
   trình song song" cho `ctrader-cli backtest` — mọi con số (8, 12, 16) trong hệ thống này đều tự
   đo trên máy VM-BO20, không phải giá trị nhà cung cấp công bố.
7. **Chưa rõ nguyên nhân gốc của "cache corruption"** mà cộng đồng report trên forum có liên
   quan gì tới việc chạy nhiều tiến trình song song hay không — hoàn toàn có thể không liên quan
   (update phần mềm giữa chừng, hết dung lượng đĩa, phiên bản cache không tương thích).
8. **Chưa đo ảnh hưởng của mức song song lên tần suất xảy ra "case C"** (bug tick + symbol quy
   đổi bị treo, mục 1.4) — có thể có giả thuyết rằng nhiều process .NET tranh chấp thread pool
   nội bộ làm case C dễ xảy ra hơn ở mức song song cao, nhưng đây **thuần là suy đoán chưa kiểm
   chứng**, không có dữ liệu nào trong toàn bộ report nội bộ đã đọc ủng hộ hay bác bỏ giả thuyết
   này.

---

## 6. Nguồn tham khảo

### Nội bộ — bằng chứng đo thật trên VM-BO20 (ưu tiên hàng đầu)

- `bo_workflow/core_engine/facilitator.py` — `DEFAULT_MAX_PARALLEL`, `MAX_PARALLEL_CAP`, comment gốc
- `bo_workflow/cli_engine/cli_runner.py` — `run_process`, `_make_poll`, watchdog stall/startup-stall
- `bo_workflow/reports/pipeline-parallelism-and-cli-tick-hang-audit-2026-09-09.md`
- `bo_workflow/reports/ctrader-cli-practical-issues-catalog-2026-09-10.md`
- `bo_workflow/reports/cli-tick-bottleneck-diagnosis-2026-09-10.md`
- `bo_workflow/reports/ctrader-cli-backtest-optimize-research-2026-09-09.md`
- `bo_workflow/reports/ab-plugin-api-vs-cli-2026-09-11.md`
- `bo_workflow/reports/ctrader-backtest-api-plugin-optimize-record-2026-09-11.md` (bảng CPU/RAM §5)
- `bo_workflow/reports/phase-2-research-and-optimization-design.md`
- Memory: `ctrader-cli-backtest-optimize-facts.md`

### Bên ngoài — fetch trực tiếp 2026-09-20

- [Backtest in plugins — cTrader Help Centre](https://help.ctrader.com/ctrader-algo/documentation/plugins/backtesting/)
- [Rate limits — cTrader Algo](https://help.ctrader.com/ctrader-algo/documentation/rate-limits/)
- [Optimisation — cTrader CLI](https://help.ctrader.com/ctrader-cli/optimisation/)
- [spotware/ctrader-console-docker — Releases](https://github.com/spotware/ctrader-console-docker/releases)
- [Release 5.10.1](https://github.com/spotware/ctrader-console-docker/releases/tag/5.10.1)
- [spotware/CLI-references](https://github.com/spotware/CLI-references)
- [Forum: max concurrent request per app (demo.ctraderapi.com)](https://community.ctrader.com/forum/connect-api-support/21640/)
- [Forum: Limits on concurrent request api](https://community.ctrader.com/forum/connect-api-support/37482/)
- [Forum: Multiple cTrader's Running Same cBots?](https://community.ctrader.com/forum/ctrader-support/24757/)
- [Forum: How to speed up back test? (Spotware xác nhận single-threaded)](https://community.ctrader.com/forum/ctrader-algo/23895/)
- [Forum: BackTesting delay/freeze after running through data](https://community.ctrader.com/forum/ctrader-algo/36935/)
- [Forum: cTrader Cached Data not Available or Corrupted](https://community.ctrader.com/forum/ctrader-algo/42686/)
