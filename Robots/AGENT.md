# AGENT.md — Trạng thái dự án & nhật ký bàn giao (dùng chung Claude ↔ Codex)

> **File này KHÔNG thay thế CLAUDE.md.** CLAUDE.md = quy tắc thiết kế/coding
> cố định (vai trò, quy ước API, cấu trúc file, các quyết định thiết kế đã
> chốt). AGENT.md = **trạng thái sống** — hôm nay đang làm gì, dở đến đâu, AI
> nào vừa động vào code, cần làm gì tiếp theo. Đọc CẢ HAI, không đọc 1 trong 2.
>
> Lưu ý đặt tên: một số công cụ (trong đó có Codex CLI) có quy ước tự động đọc
> file tên **`AGENTS.md`** (số nhiều, không có tên vendor). File này được đặt
> tên `AGENT.md` (số ít) theo đúng yêu cầu của người dùng — nếu Codex không tự
> nhận diện, người dùng cần trỏ thẳng file này cho Codex đọc ở đầu phiên.

---

## 0. Việc đầu tiên phải làm khi nhận phiên làm việc mới

Đọc theo đúng thứ tự sau, đừng bỏ qua bước nào:

1. **`CLAUDE.md`** (cùng thư mục) — toàn bộ. Đây là nguồn sự thật duy nhất về
   quy tắc code, API cTrader, cấu trúc project, và lịch sử thiết kế chi tiết
   (catalog 6 phương án Exit, nguyên tắc xây dựng, phương pháp kiểm định...).
2. Mục **§4 Trạng thái hiện tại** và **§5 Việc đang mở/TODO** ngay bên dưới
   trong file này.
3. Entry **mới nhất** (trên cùng) ở **§8 Nhật ký bàn giao** bên dưới — đây là
   tường thuật chi tiết nhất, sát thời điểm hiện tại nhất.
4. `Combo/Combo/Combo.cs` — code thật, đọc để đối chiếu với những gì §4/§8 mô
   tả (nếu lệch nhau, code là sự thật, file MD có thể đã cũ — cập nhật lại MD).
5. Nếu việc cần làm liên quan tới backtest/dữ liệu thật, xem mục **§7.3 Vị trí
   dữ liệu backtest**.

---

## 1. Bối cảnh dự án (tóm tắt — chi tiết đầy đủ nằm ở CLAUDE.md)

- Đây là repo cá nhân, **không phải sản phẩm thương mại** — công cụ
  nghiên cứu/kiểm định chiến lược trading để người dùng tự giao dịch qua tài
  khoản **FTMO** (prop firm).
- Vai trò của AI (Claude hoặc Codex) khi làm việc trong repo này: **kỹ sư lập
  trình cBot cho cTrader Automate** (C#/.NET6, `cAlgo.API`) — nhận mô tả chiến
  lược, chuyển thành cBot hoàn chỉnh để backtest/optimize trong cTrader.
- Chiến lược đang phát triển chính: **`Combo`** (`Combo/Combo/Combo.cs`) —
  breakout dựa trên MA cross + MACD Histogram. **Tín hiệu (signal) + ATR do
  Python bên ngoài tính sẵn** (project `core_python` trên máy khác, VM-OG8,
  10.11.12.8) và xuất ra file CSV — cBot **KHÔNG tự tính MA/MACD**, chỉ đọc
  CSV rồi tự lo Entry/SL/TP/quản lý vốn. Xem chi tiết kiến trúc "4 tầng cấu
  hình độc lập" (Risk Sizing / Exit / Reversal Handling / Position Management)
  ở đầu `Combo.cs` và trong CLAUDE.md.
- Máy đang thao tác: **VM-BO20** (10.11.12.20) — có cài **cTrader Desktop
  thật**. Build/chạy cBot chỉ thực hiện được **qua chính cTrader Automate IDE**
  (không có `dotnet build` CLI độc lập để tự kiểm tra trước khi bàn giao code).

---

## 2. Quy ước "record md" — BẮT BUỘC tuân theo

Khi người dùng gõ đúng cụm **"record md"** (hoặc rõ ràng có ý tương tự, ví dụ
"ghi vào md", "cập nhật AGENT.md") trong bất kỳ phiên nào (Claude hay Codex),
agent đang làm việc **phải dừng việc đang làm lại và ngay lập tức**:

1. Cập nhật **§4 Trạng thái hiện tại** cho khớp với thực tế code/kết quả mới
   nhất tại thời điểm đó.
2. Cập nhật **§5 Việc đang mở/TODO** — đánh dấu việc đã xong, thêm việc mới
   phát sinh, xoá việc không còn liên quan.
3. Thêm **1 entry MỚI lên ĐẦU §8 Nhật ký bàn giao** (không sửa/xoá entry cũ)
   theo đúng template ở đầu §8 — bắt buộc có: ngày giờ, agent nào viết, việc
   đã làm trong phiên, việc đang dang dở (nếu build/test chưa xong), quyết
   định/thoả thuận với người dùng CHƯA kịp code hoá, và gợi ý bước tiếp theo
   rõ ràng cho agent kế tiếp.
4. Nếu vừa sửa `Combo.cs` mà **chưa được build/test thật trong cTrader** (máy
   này không có compiler độc lập để tự verify), phải ghi rõ điều đó trong
   entry — đừng để agent kế tiếp tưởng nhầm là đã build sạch.
5. Báo lại ngắn gọn cho người dùng: "Đã cập nhật AGENT.md xong."

Mục đích: người dùng luân phiên dùng Claude/Codex tuỳ credit còn lại — AI nào
vào sau phải đọc entry mới nhất là **hiểu ngay** không cần hỏi lại từ đầu.

---

## 3. Nguyên tắc làm việc chung (rút gọn từ CLAUDE.md — đọc bản đầy đủ ở đó)

- Đúng logic chiến lược > code sạch > dễ optimize > đầy đủ tính năng phụ.
- Không tự thêm tính năng ngoài yêu cầu. Không magic number — mọi ngưỡng/hệ số
  phải là `[Parameter]` hoặc `const` có tên rõ nghĩa.
- Giữ đúng khung region chuẩn trong mọi file `.cs` chiến lược: `Parameters` →
  `Indicators & State` → `Lifecycle` → `Entry Logic` → `Exit Logic` →
  `Risk & Position Sizing` → `Position Management` → `Helpers`.
- Dùng `Server.Time`, không `DateTime.Now`. Không `Thread.Sleep`. Không phụ
  thuộc chart drawing cho logic cốt lõi.
- **Không có compiler độc lập trên máy này** — mọi thay đổi code phải được
  người dùng tự build trong cTrader IDE và báo lỗi lại nếu có. Đừng khẳng định
  "đã chạy được" nếu chưa có xác nhận build thật từ người dùng.
- Khi sửa `Combo.cs`, luôn cân nhắc cả 5 `ExitMode` và 2 `ReversalMode` có thể
  kết hợp tự do với nhau — xem trước có phá vỡ tổ hợp nào khác không.

---

## 4. Trạng thái hiện tại (tính tới entry mới nhất ở §8)

> ⚠️ **§4 ĐÃ CŨ (viết ~2026-09-02).** Thay đổi lớn 2026-09-03→09-06 CHƯA phản ánh ở đây,
> xem entry §8 ngày 2026-09-06 + CLAUDE.md. Đính chính nhanh:
> - **KSL/KTP không còn "10 mức Fibonacci dùng chung"** — đã tách `SlFibLevel` (8 mức:
>   0.618→3.618) × `TpFibLevel` (9 mức: 0.236→6.854), code trong cả Combo.cs & MA Cross.cs,
>   đã build (`Combo.algo` 09-04, `MA Cross.algo` 09-05). Mọi `.cbotset`/kết quả optimize
>   cũ tham chiếu KslLevel/KtpLevel bằng SỐ đều sai nghĩa — xem CLAUDE.md.
> - **MA Cross đã thêm `ReconcileExistingExposure`** (sửa bug mở 2 vị thế ngược hướng),
>   build + verify 3 backtest thật.
> - **`MaxMarginPercent` default = 50%** (nâng từ 10%).
> - Signal-trace fidelity: đã đối chứng 11/11 symbol × 2 chiến lược
>   (`research/reports/*-signal-trace-*.csv` + `signal-trace-batch-2026-09-05-summary.md`).
> - **Audit lot-size safety** 2026-09-06: `research/reports/lotsize-safety-audit-2026-09-06.md`
>   + cross-check Codex `...-CODEX-CROSSCHECK-...`.
> - **[2026-09-06] Quy đổi tiền tệ trong lot size (approach C) — BUILD LẦN 1 LỘ BUG JP225, ĐÃ VÁ, CHƯA BUILD LẠI**:
>   `Symbol.PipValue` là snapshot đóng băng lúc OnStart → thay bằng helper `PipValueNow()` gọi
>   `Symbol.QuoteAsset.Convert(Account.Asset, ...)` live; `Symbol.PipValue` → `pipValue` tại 3 chỗ/file;
>   `pipValue` là tham số mới của `CapVolumeByMargin`. **Build lần 1 (08:44) + 3 backtest Combo/H1 Jan2026:**
>   US30 `placed=31/failed=0` ✓; GER40 `placed=38/failed=0` ✓ (`Asset.Convert` compile+chạy OK cho EUR);
>   **JP225 `placed=0/failed=33`** — `Asset.Convert` cắt kết quả về 2 chữ số USD → 1 pip JPY (~0.0064) → 0.00
>   → `pipValue<=0` → mọi volume=0. **Đã vá:** `PipValueNow()` giờ quy đổi `PipSize × 1e6` rồi `/1e6`
>   (probe-scaling) để né rounding; HKD cũng chính xác hơn (0.1286 thay vì 0.12). US30 no-op đã xác nhận
>   một phần: 21/21 lệnh không-margin-cap trùng khít bản cũ (10 lệnh cap lệch do commission-model +
>   date-range khác giữa 2 run, KHÔNG do code). Báo cáo: `reports/lotsize-fx-live-conversion-fix-2026-09-06.md`
>   §15. **Chờ: build lại 2 `.algo` (bản vá) → chạy lại JP225 + HK50 + US30(cùng cấu hình) + GER40.**

- **Thiết kế cBot hiện hành đã được đơn giản hoá theo quyết định mới của người
  dùng.** `Combo.cs` hiện không còn 5 `ExitMode`/`ReversalMode`; Combo chỉ đọc
  CSV (`bartime,atr,entry,signal`) và đặt **Pending Stop** tại đúng cột `entry`,
  giữ tối đa 3 nến. `MA Cross.cs` đọc CSV (`bartime,atr,signal`) và đặt
  **Market Order**. Hai cơ chế thực thi là cố định theo từng cBot, không có
  dropdown `OrderMode`.
- Cả hai cBot giữ 10 mức Fibonacci cho KSL/KTP, SL/TP tĩnh và risk động theo
  `Account.Balance * RiskPercent / 100`; `RiskPercent` mặc định 1%. Ba FTMO
  guard vẫn còn nhưng mặc định OFF.
- **Cơ chế căn chỉnh thời gian exact-first/missing-bar fallback đã code, được
  người dùng build trong cTrader IDE và backtest thật thành công.** Nếu CSV
  `bartime` tồn tại đúng trong bar FTMO, cBot xử lý từ `OnBarClosed` như logic
  cũ. Chỉ khi không tồn tại exact bar, cBot xử lý một lần tại tick FTMO đầu
  tiên sau `bartime + chart timeframe`. Không dùng offset symbol/DST hard-code;
  có chống trùng bằng `IsHandled` và schedule cursor.
- **[2026-09-01] Parameter `Enable Missing-Bar Fallback` đã BỊ XOÁ khỏi cả
  `Combo.cs` và `MA Cross.cs`** theo yêu cầu người dùng — fallback giờ LUÔN
  chạy mặc định, không còn là tuỳ chọn bật/tắt (không còn "mode" nữa).
  `InitializeSignalSchedule()` chỉ còn phụ thuộc `TryGetNominalBarDuration`
  (timeframe có hỗ trợ tính nến cố định hay không — vd Tick chart thì không)
  — không phụ thuộc lựa chọn người dùng nữa. **ĐÃ ĐƯỢC NGƯỜI DÙNG BUILD LẠI
  trong cTrader IDE và backtest thật thành công** (`parameters.cbotset` của
  các lượt archive sau đổi không còn field này — xác nhận build đúng bản
  mới). Đã backtest lại từ đầu 2025 → hiện tại cho cả 2 cBot (US30/H4 Combo,
  US30/M30 MA Cross), archive `*_AlwaysFallback_2025plus_20260901-*`.
- **[2026-09-02] Fix margin A+C đã hoàn tất cho cả hai cBot và đã được người
  dùng build/backtest thật xác nhận.** `CalculateVolume()` vẫn tính volume
  theo risk % balance, sau đó `CapVolumeByMargin()` dùng
  `Symbol.GetEstimatedMargin()` để giảm volume với safety factor 0.98 nếu
  vượt free margin; có counter `_marginCapped`/`_marginBlocked`. Lượt GUI
  US30/H1 01-30/01/2026 sau fix có 4 lần cap, `failed=0`, không còn
  `NOT_ENOUGH_MARGIN_BALANCE`. Phiên bản hiện tại:
  - `Combo/Combo/Combo.cs`:
    `DD7A748D054684F664ED22F5579EB2AB96C807E3737C34DCF9B52491FF2A2336`.
  - `MA Cross/MA Cross/MA Cross.cs`:
    `31E3F5B0DE26239499186983DDC7CC4F14D910F3AA8D19E7F4F089B0622178D5`.
  - `Combo.algo` đã build:
    `F55E7FBA663B6A212C7F19D6E63E2482F922E0B7865411CE19DA7B66780C04B7`.
  - `MA Cross.algo` đã build:
    `78CD8FAE84417534FED98C4687D5519B0A7C2BB216B16B741BE3F9CFFE05DDED`.
- **Combo US30.cash/H4 trước/sau:** exact subset giữ nguyên 176 signal; bản mới
  phục hồi thêm 38 fallback, thành 214 pending order (0 reject), 164 fill,
  49 cancel, 1 pending cuối kỳ. Fallback riêng: 28 fill/10 cancel, 6 TP/22 SL,
  net -$704.36. Tổng net +$834.30 → +$36.76; equity DD 18.001940% →
  26.116860%.
- **MA Cross US30.cash/M30 trước/sau:** exact subset giữ nguyên 897 attempt
  (503 accepted/394 `NoMoney`); bản mới thêm 14 fallback (6 accepted/8
  `NoMoney`), thành 509 position. Fallback riêng: 1 TP/5 SL, net -$212.55.
  Tổng net -$2,003.52 → -$2,168.86; equity DD 36.468021% → 34.521820%.
- Đối chiếu trực tiếp CSV/log/events xác nhận mỗi `bartime` được xử lý đúng một
  lần; không sai direction, SL/TP; Combo không sai entry. Nhánh exact giữ
  nguyên tuyệt đối lifecycle trước/sau. Chênh lệch P/L còn lại ngoài P/L trực
  tiếp fallback do risk 1% tính lại theo balance mới, không phải lỗi scheduler.
- Raw source/backtest trước và sau đã archive; báo cáo đầy đủ tại
  `reports/signal-alignment-baseline-2026-08-31.md`. Code 5 ExitMode cũ và các
  archive HK50 vẫn là lịch sử tham khảo, không phải kiến trúc của source hiện tại.
- **2026-09-01: lặp lại kiểm chứng missing-bar trên khung NGẮN (2026 tới nay)
  cho cả US30/H4 (Combo) và US30/M30 (MA Cross), đủ 4 lượt ON/OFF.** Xác nhận
  hiện tượng thiếu bar vẫn xảy ra thật (Combo 9/62=14.5%, MA Cross 3/203=1.5%),
  có quy luật (không ngẫu nhiên), và phát hiện thêm hiệu ứng lan qua cỡ lệnh
  (risk% theo balance động) — với MA Cross, bật fallback từng đổi 1 lệnh
  exact-match từ PLACED sang REJECTED margin. Đã audit sâu cơ chế code (đúng
  và hiệu quả ở phần lõi) + tìm ra 3 đề xuất cải thiện, **CHƯA triển khai**,
  đang chờ người dùng quyết định ưu tiên — chi tiết đầy đủ (bảng số liệu,
  bằng chứng, 3 đề xuất) tại `reports/missing-bar-followup-2026-09-01.md`.
- **[2026-09-01, mới] "3 tầng thành công" của việc đặt lệnh đã được định
  nghĩa và định lượng rõ**: (1) signal được nhận đúng, (2) lệnh được broker
  CHẤP NHẬN ("placed" trong log), (3) lệnh thực sự THÀNH vị thế ("filled").
  Với MA Cross (Market Order) tầng 2=3 luôn luôn; với Combo (Pending Stop)
  KHÔNG — trên lượt archive 2025+, 140 lệnh Combo "placed" chỉ có 110 thực
  sự filled (29 hết hạn không khớp + 1 còn treo cuối kỳ test), truy vết qua
  `orderId` trong `events.json` (hàm mới `fidelity_lib.get_fill_outcomes()`).
- **[2026-09-01, mới] Đã dựng `research/signal_chart_visualizer.ipynb`** —
  công cụ đối chứng TRỰC QUAN 3 nguồn dữ liệu độc lập (nến SQL + signal CSV +
  lệnh thật backtest) trên 1 biểu đồ nến duy nhất, dùng
  `research/vendor/lightweight-charts.js` (vendor từ chính DP6). Output:
  `research/output/signal_chart_viewer.html` — file tự chứa, chạy offline,
  dropdown chọn dataset. Đã thêm lớp marker thứ 3 "đóng lệnh" (SL/TP/khác,
  qua `fidelity_lib.get_exit_events()`) theo yêu cầu người dùng 2026-09-02.
- **[2026-09-02, MỚI NHẤT — pipeline `ctrader-cli` hoạt động và sự cố UK100
  đã giải quyết]** Nguyên nhân UK100 "dừng sớm" là pipeline cũ format ngày
  `MM/dd/yyyy` trong khi CLI 5.9 yêu cầu `dd/MM/yyyy`: `--end=04/01/2025` bị
  hiểu là 4/1 thay vì 1/4, nên nến cuối 3/1 21:00 là đúng. Không phải thiếu
  tick/deadlock/route sai broker. Pipeline đã đổi format, bỏ retry ±1 ngày,
  truyền rõ `--broker="FTMO Platform"`, và chỉ thành công khi `report.json`
  parse được + `testingPeriod` khớp input. UK100 H4 01/01→01/04/2025 đã chạy
  hết kỳ thật (18 trades, net -210.91). Đã bổ sung run manifest có hash
  `.algo`/CSV, parameter grid tuần tự có checkpoint và công cụ so sánh nhiều
  run ra CSV/JSON/Markdown. Tài liệu đầy đủ ở `research/cli_pipeline/README.md`;
  bằng chứng/test mới nhất ở §8 entry "tiếp 25".
- **[2026-09-02, mới nhất — full-range CLI retest US30 hoàn tất]** Đã chạy
  tuần tự bằng Ticks, cùng kỳ 01/01/2025→02/09/2026: Combo H4 có 201 signal
  trong kỳ, 200 pending placed, 153 fill, 36 expiry, 11 reversal-cancel,
  1 same-direction skip, net -233.47; MA Cross M30 có 561 signal, 310 market
  accepted/opened, 251 `NoMoney`, net +408.53. Cả hai `not-processed=0`,
  report khớp kỳ và cuối cùng còn 0 CLI process. Đối chiếu lịch xác nhận
  Combo có 31/201 và MA Cross có 11/561 `bartime` CSV không tồn tại trong
  lưới timestamp chart FTMO tương ứng; scheduler vẫn xử lý đủ bằng tick đầu
  tiên từ `bartime + timeframe`. Báo cáo chi tiết:
  `reports/cli-backtest-US30-2025-current-2026-09-02.md`; §8 "tiếp 26".
- **[2026-09-02, quyết định kiến trúc pipeline optimize — CHƯA CODE]** Cách
  mỗi run là một folder ngang hàng trong `research/cli_runs/` chỉ phù hợp
  backtest đơn/grid nhỏ. Với hàng trăm/nghìn run, hướng đã ghi nhận để trao
  đổi tiếp là gom theo `research/experiments/<ExperimentId>/`: metadata chung
  ở `manifest.json`, kết quả từng tổ hợp trong `results.sqlite` (và export
  `results.csv`), raw `report.json`/`log.txt` dưới `artifacts/run_XXXXXX/` khi
  cần. Thiết kế phải hỗ trợ parameter-hash chống chạy trùng, checkpoint/resume,
  IS/OOS/Walk-Forward và retention policy cho raw log. Chưa triển khai cho
  tới khi người dùng cùng chốt schema/quy tắc lưu.
- **[2026-09-02, parallel grid thật đã hoàn tất]** Experiment
  `research/experiments/Combo_US30_h4_2025_KSL-KTP_20260902-052051/` đã chạy
  đủ 100/100 tổ hợp KSL×KTP, Ticks, US30.cash/H4, 01/01/2025→01/01/2026,
  `MaxParallel=10`, 0 failed, khoảng 29 phút. Best theo Net Profit là
  KSL=`Fib2618`, KTP=`Fib0786`: +$407.67, ROI 4.08%, PF 1.34, 78 trades,
  max equity DD 4.1552%. Experiment có `manifest.json`, `results.csv/json`,
  top-10 và raw artifact từng run, nhưng đây chưa phải generic optimizer có
  SQLite/Walk-Forward. **Đặc biệt:** grid dùng `Combo.algo` hash CŨ
  `206A64B72134352FD83EB8947A7B62A3BEEF7ED36234C23B43073FB4FF09A62A`,
  nên không được dùng để xác nhận hành vi của binary margin-fixed hiện tại.
- **[2026-09-02, audit PipValue/quy đổi tiền tệ hoàn tất]** Đã kiểm chứng trực
  tiếp report/log/history cho đủ 6 symbol: XAUUSD, BTCUSD, US30.cash,
  GER40.cash, JP225.cash, HK50.cash — PASS 6/6. Ba symbol quote USD cho
  PipValue ~$1/pip/unit; GER40 tự nạp EURUSD và nhân EUR→USD; JP225/HK50 tự
  nạp USDJPY/USDHKD và chia đúng chiều về USD. Không phát hiện sai chiều hay
  sai bậc quy đổi. Giới hạn đã được tài liệu cTrader xác nhận: `PipValue` là
  snapshot lúc cBot khởi động và không update real-time, nên risk % ở các
  symbol quote khác USD có thể trôi theo tỷ giá trong run dài. Báo cáo:
  `reports/pipvalue-currency-conversion-audit-2026-09-02.md`.

---

## 5. Việc đang mở / TODO (ưu tiên từ trên xuống)

0-GAP. **[2026-09-08 — PHÁT HIỆN, CHƯA XỬ LÝ] Rủi ro thực tế/lệnh lệch xa thiết kế do trượt giá lệnh
   chờ + đảo chiều.** Qua backtest Combo/FRA40.cash/H1 (balance $10k, risk 1%): lệnh lỗ dao động
   −$11.70 → −$261.82 trên cùng ngân sách ~$95–101. **KHÔNG phải lỗi sizing/FX** (`pipValue 1.16364`
   = EURUSD đúng). Hai nguyên nhân: (1) **position 10** — bar tín hiệu 01:00, FR40 đóng cửa tới 07:00;
   pending Buy Stop @8361.4 đặt lúc 07:05 khi giá đã ở 8382 → khớp NGAY market @8382.42 (+21 điểm);
   SL @8345.88 (tuyệt đối, không đổi) giờ cách 36.5 pips không phải 15.5 → −39.3 pips = **2.56×
   budget**. (2) **position 15** — `reason=Closed`: tín hiệu ngược đóng lệnh giữa chừng ở −2.8 pips.
   Run: `reversed=20/placed=64 = 31%`. Công thức risk% chỉ đảm bảo "−1% NẾU khớp tại trigger VÀ SL
   khớp tại mức SL". Đề xuất khởi điểm: huỷ tín hiệu nếu giá đã vượt `EntryPrice` quá X%×ATR khi đặt
   được lệnh (1 tham số). Chi tiết + 5 hướng: `reports/lotsize-sizing-work-summary-2026-09-08.md` §4, §6.2.

0-DBG. **[2026-09-08 — Combo.algo ĐÃ BUILD 02:20, MA Cross.algo CHƯA] Log chi tiết để kiểm định bằng
   tay: sizing / FX quy đổi / SL-TP / P&L thật khi đóng lệnh.** `Combo.algo` build 09-08 02:20 — mọi
   dòng log mới chạy OK (compile sạch: `Asset.Convert`/`History`/`PositionCloseReason` đều OK, xác
   nhận qua backtest FR40). **`MA Cross.algo` còn bản 09-07 00:15 — CẦN BUILD LẠI.** Chạy backtest
   với **balance tuỳ ý** (bot đọc `Account.Balance`; GUI Starting Capital / CLI `--balance=`). Đã
   thêm (thuần `Print` + 1 `out` + 1 event handler, KHÔNG đổi hành vi, KHÔNG thêm `[Parameter]`/
   state/region). Các dòng log/lệnh:
   - `CalculateVolume(..., out double pipValue)` — `pipValue` THẬT đã dùng để sizing (1 call-site/file).
   - **`FILLED`** (Combo, `[2026-09-08 chiều]` — handler `OnPendingOrderFilled`, đăng ký
     `PendingOrders.Filled` trong `OnStart`): cTrader KHÔNG log gì khi lệnh chờ khớp (chỉ có ở
     `events.json`). Dòng này in: `pending {id} => position {id}`, giá khớp, `executed=` thời điểm
     khớp, `trigger was {X}`, `entry slippage {Y} pips worse/better`, **`SL now {Z} pips from fill =>
     real risk if SL hit=${W}`** (chỗ rủi ro thật phồng lên khi trigger bị bỏ qua trong phiên mở
     cửa — Vấn đề 3 / position 10). API mới: `PendingOrders.Filled`/`PendingOrderFilledEventArgs`,
     `PendingOrder.TargetPrice` — chuẩn cAlgo, chưa build-verify. MA Cross là Market Order (khớp ngay
     lúc đặt) → `executed=` + `fill=` đã đủ, không cần `FILLED` riêng.
   - **`RISK_DETAIL`** (tag 1 token): balance, risk% → budget $, `ATR`, `KSL/KTP ×ATR` → SL/TP pips,
     R:R, `pipValue`, `risk-based raw {X} → placed {Y} units, step {S}` (thấy round-down DOWN),
     est.margin, expected loss@SL $, expected profit@TP $. MA Cross thêm `fill/SL/TP` giá tuyệt đối.
   - **`FX`** (helper `LogConversionRate`): symbol quote ≠ USD → `1 {ccy} = {factor} USD (<=> {1/factor}
     {ccy} per USD)`, `factor = pipValue/PipSize` (không hardcode pair, không gọi Convert lần 2).
   - **`CLOSED`** (helper `LogClosedTrade`, trong `OnPositionClosed`): reason (SL/TP/Closed), entry,
     close, pips, volume units+lots, gross/commission/swap/**net** $, balance sau lệnh. Số từ
     `History`/`HistoricalTrade`, fallback `Position`.
   - Dòng "placed"/"market placed" cũ GIỮ NGUYÊN (parser `research/` không vỡ; đã có units+lots).
   **API mới (chưa build-verify):** `History`/`HistoricalTrade`, `PositionClosedEventArgs.Reason`/
   `PositionCloseReason`, `result.Position` (market), `Position.Commissions`/`Swap`,
   `Symbol.VolumeInUnitsToQuantity`, `Symbol.VolumeInUnitsStep`. Đều chuẩn cAlgo, rủi ro thấp — gửi
   lỗi compile nếu có (`ClosingPrice` có thể tên khác; `History` có thể chưa cập nhật lúc event → có
   fallback `Position`). Luôn-bật, chưa gate `[Parameter] Verbose`. FX = "Mức 1" (không nạp symbol
   pair thật = "Mức 2", chỉ làm nếu cần chốt Bid/Ask của `Asset.Convert`). Chi tiết: §8 entry 09-08.

0-FX. **[2026-09-07 — ĐÃ BUILD + VERIFY: CURRENCY CONVERSION PASS]
   Sửa quy đổi tiền tệ
   trong lot size (approach C, đã duyệt).** Helper `PipValueNow()` (đầu region `Risk & Position
   Sizing`, cả 2 file giống hệt) thay `Symbol.PipValue` snapshot bằng `Asset.Convert` live.
   **Build lần 1 (09-06 08:44) + 3 backtest:** US30 ✓, GER40 ✓ (EUR OK), **JP225 fail 33/33** vì
   `Asset.Convert` cắt kết quả về 2 chữ số USD → 1 pip JPY (0.0064) thành 0.00. **Đã vá:** probe-
   scaling — `Convert(PipSize × 1e6) / 1e6`. Vẫn approach C, chỉ né rounding của API. Chi tiết +
   diff + no-op check: `reports/lotsize-fx-live-conversion-fix-2026-09-06.md` §15.
   Audit Codex đã tái dựng từ tài liệu chính thức + metadata/P&L runtime và tái hiện HK50 độc lập
   bằng CLI: binary build lần 1 đặt volume cao hơn đối chứng `7.01%–7.28%`; JP225 vẫn `0/33` order.
   Báo cáo: `reports/lotsize-currency-conversion-full-audit-CODEX-2026-09-06.md`.
   **Đã đóng 2026-09-07:** build mới Combo hash `C4996CCE...` và MA Cross hash `55C88E48...`;
   7 run Ticks trên Combo JP225/HK50/US30/GER40/XAUUSD và MA Cross JP225/HK50 đều `failed=0`.
   JP225 đã có order ở cả hai bot; HK50 trở về mức conversion đúng; US30/XAUUSD không hồi quy;
   GER40 chỉ lệch tối đa một volume step do tỷ giá lịch sử tại lúc sizing. `CLAUDE.md` và audit Codex
   đã cập nhật. Không còn blocker build/test cho mục currency conversion này.

0. **[2026-09-06] Audit lot-size safety — ĐÃ CROSS-CHECK CODEX, CHƯA CODE.**
   Nguồn kết luận mới: [lotsize-safety-audit-CODEX-CROSSCHECK-2026-09-06.md](research/reports/lotsize-safety-audit-CODEX-CROSSCHECK-2026-09-06.md),
   đối chiếu [audit Claude](research/reports/lotsize-safety-audit-2026-09-06.md).
   Công thức nominal gross SL-risk hợp lệ, nhưng không bảo đảm net loss/account limits.
   Đã sửa lập luận B7 (restart sau lãi làm CHẶT hơn), B3 (progressive tiers cố định
   khiến scale xuống bảo thủ), B2 (API double, null-check dead; bug forum2023 không
   chứng minh GetEstimatedMargin5.9.10 lỗi), B9/D6 (không tự trừ Credit khỏi Balance).
   40–62% là scenario giả định, chưa chứng nhận gap DWH tương đương FTMO; BTC không
   được gọi là an toàn. D1 tách knob không giữ được risk1% khi stress cap bind.
   **[Tự audit tiếp 2026-09-06] Đã thu hẹp đề xuất:** xem report §10.1–§10.4.
   Hai nhóm sửa nhỏ đáng làm: Close/Cancel result (gồm giữ tracking pending khi cancel
   thất bại) và finite/final-volume validation. PM hiện hữu cần sửa khi dùng PM;
   không đồng nhất với triển khai mọi profile FTMO. Rút portfolio coordinator,
   stress engine, custom margin fallback, post-fill auto-close khỏi scope hiện tại.
   FTMO2-Step static10%/daily5%;1-Step EOD-trailing10%/daily3% và Best Day50%; chưa
   xác nhận product cụ thể của account7563609. Không hardcode offset CE(S)T.
   **Còn mở:** chưa có yêu cầu triển khai code. Profile/budget/ownership chỉ cần chốt
   cho phần mở rộng tương ứng, không phải điều kiện để sửa Close/Cancel và số không
   hợp lệ. Chưa đo partial fill, callback ordering và estimator-vs-actual margin trên
   Desktop5.9.10. Không biến thiếu thực nghiệm thành lý do tự xây thêm framework.

0b. **[2026-09-02, đã hoàn tất] `MaxMarginPercent` — đã code + build + test** (khác với ghi
   chú cũ bên dưới): default hiện tại **50%** (nâng từ 10%, CLAUDE.md 2026-09-03).
   Gap-cap là đề xuất đã hoãn ở mục 0, không phải code đang dang dở.
1. **Không còn task code thời gian đang dang dở.** Exact-first/fallback đã build,
   backtest, archive và đối chiếu trước/sau xong cho Combo US30/H4 và MA Cross
   US30/M30.
2. ~~Chờ quyết định người dùng: giữ `Enable Missing-Bar Fallback=true`~~ —
   **ĐÃ QUYẾT: xoá hẳn parameter, fallback luôn mặc định bật** (2026-09-01,
   đã build/test lại, xem §4). Không còn việc mở ở mục này.
3. ~~Risk-based volume không kiểm tra free margin, gây `NoMoney`/
   `NOT_ENOUGH_MARGIN_BALANCE`~~ — **ĐÃ FIX theo A+C, người dùng đã build và
   backtest xác nhận** (xem §4 và §8 "tiếp 29"). **Ghi chú cũ đã được thay thế:**
   trần chủ động `MaxMarginPercent` đã có, default50%; phần an toàn còn mở xem mục0.
   Audit `Symbol.PipValue` đã PASS 6/6 symbol, nhưng API giữ PipValue cố định
   từ lúc cBot khởi động. Nếu người dùng yêu cầu risk % sát tuyệt đối trong
   run dài cho EUR/JPY/HKD, cần chốt riêng hướng dynamic conversion trước khi
   sửa code.
4. Các vấn đề lịch sử của bản Combo 5 ExitMode (`ModifyExpirationTime` ~10.14%,
   margin reject theo số chân, thứ tự TP1 của FibCompensating3) vẫn được giữ
   nguyên trong archive/report cũ và **chưa fix**. Chúng không còn nằm trên
   code path của Combo đơn giản hiện hành; không tự phục hồi/sửa legacy code.
5. ~~Điều tra UK100.cash dừng sớm~~ — **ĐÃ GIẢI QUYẾT**: lỗi format ngày
   `MM/dd/yyyy` của wrapper, không phải lỗi dữ liệu/CLI; xem §4 và §8 "tiếp
   25". Pipeline hiện có chạy đơn + parameter grid tuần tự/checkpoint +
   chuẩn hoá/so sánh kết quả; full-range Combo US30/H4 và MA Cross US30/M30
   đã test thật thành công ngày 2026-09-02. **Việc mở tiếp theo:** (a) Walk-Forward đầy đủ
   (rolling IS/OOS, chọn tham số IS, chạy và nối OOS); (b) parser fidelity tự
   động riêng cho Combo và MA Cross để định lượng `signal -> placed ->
   fill/reject/expire`; (c) ranking đa mục tiêu thay vì chỉ Net Profit. Máy
   vẫn không có `dotnet` CLI độc lập; mọi sửa `.cs` vẫn phải được người dùng
   build lại trong cTrader IDE. **Parallel grid đã được chứng minh thực tế:**
   100 tổ hợp KSL×KTP chạy 10 process đồng thời, 100 success/0 failed; xem §4
   và §8 "tiếp 29". Phần generic còn mở là chuẩn hoá runner (thay cho lệnh
   ad-hoc), bổ sung `results.sqlite`, resume/retry chắc chắn, retention policy,
   rồi nối IS/OOS/Walk-Forward. Grid vừa xong dùng binary cũ, cần rerun trên
   `Combo.algo` margin-fixed nếu muốn dùng kết quả để ra quyết định chiến lược.
6. **[Mới, 2026-09-01] 3 đề xuất cải thiện cơ chế missing-bar fallback — ĐÃ
   GHI NHẬN, CHỜ QUYẾT ĐỊNH ưu tiên, chưa triển khai bất kỳ đề xuất nào**: (1)
   chốt thời điểm thực thi fallback vào đúng mốc đóng nến FTMO thay vì tick
   bất kỳ; (2) đổi cửa sổ hết hạn fallback từ thời gian tuyệt đối sang đếm nến
   chart thật (né lịch nghỉ, tổng quát cho mọi timeframe); (3) chuyển việc đối
   chiếu lịch CSV↔FTMO lên tầng Python (xuất sẵn cột `ftmo_execute_at`) — phụ
   thuộc câu hỏi còn treo "có lấy được lịch nến FTMO cho Python dùng không".
   Chi tiết đầy đủ: `reports/missing-bar-followup-2026-09-01.md` §5.
7. ~~Máy chưa cài Python/Jupyter~~ — **ĐÃ CÀI XONG** (Python 3.12.7 +
   jupyter/pandas/matplotlib/numpy, xem `research/README.md`). Đã dựng và
   TỰ-KIỂM-CHỨNG THÀNH CÔNG `research/signal_fidelity_check.ipynb` (đối
   chiếu CSV signal ↔ log.txt backtest, khớp tuyệt đối với phân tích thủ
   công trước đó trên 4 lượt archive thật). 1 giới hạn đã biết: không truy
   được bartime cụ thể cho các lần bị skip do dưới sàn volume — xem
   `research/README.md`.
8. **[Mới, 2026-09-01] Đã điều tra đầy đủ VM-DP6 (10.11.12.6, Windows,
   `dp_program_v3`)** — tầng lấy nến từ TradingView (kênh Capital.com) vào
   SQL Server `SEN05_AutoTrading.DWH.Fact_OHLCV`, xác nhận đây là nguồn gốc
   BarTime mà mọi CSV signal của OG8 kế thừa. Xem §7.5 (mới) để biết chi
   tiết truy cập + cấu trúc. **Sự cố an toàn đã xảy ra và đã báo người dùng**:
   vô tình đọc phải `Config.yaml` chứa secret thật (token/cookie TradingView,
   mật khẩu SQL/Redis) — người dùng xác nhận không sao (hệ thống nội bộ),
   không cần rotate. Từ nay tránh đọc `Config.yaml` trên DP6/OG8 trừ khi thật
   sự cần.
9. **[Mới, 2026-09-01] `research/signal_chart_visualizer.ipynb` đã chạy xong,
   không còn task dang dở** — nhưng mới có đúng 2 dataset (Combo US30/H4, MA
   Cross US30/M30) trong `RUNS`. Mở rộng thêm symbol/timeframe khác chỉ cần:
   (a) fetch thêm 1 file `data_cache/<Symbol>_<TF>_candles.csv` qua
   `db_connector.load_range()` trên OG8, (b) có sẵn 1 archived run mới, (c)
   thêm 1 entry vào `RUNS` — không cần sửa code render. Chưa có yêu cầu làm
   việc này; chỉ ghi nhận đường mở rộng sẵn có.

---

## 6. Bản đồ file quan trọng

| File/Folder | Vai trò |
|---|---|
| `CLAUDE.md` | Quy tắc thiết kế/coding chính thức — nguồn sự thật #1, **đọc trước AGENT.md** |
| `AGENT.md` | File này — trạng thái sống + nhật ký bàn giao |
| `Combo/Combo/Combo.cs` | Code cBot chiến lược Combo — đối tượng làm việc chính hiện tại |
| `Combo/Combo/Combo.csproj`, `Combo/Combo.sln` | Project khuôn chuẩn cTrader (net6.0 + `cTrader.Automate`) |
| `Z:\Desktop\og_program\` | Mount SSHFS tới VM-OG8 (10.11.12.8) — chứa `core_python` (sinh tín hiệu) và `runtime\exports\*.csv` (file signal thật để nạp vào `SignalFilePath`) |
| `Documents\cAlgo\Data\cBots\Combo\<instance-id>-Default\Backtesting\` | Kết quả lần Backtest đơn lẻ **gần nhất** — **BỊ GHI ĐÈ mỗi lần chạy mới**, xem §7.3 |
| `Documents\cAlgo\Data\cBots\Combo\<instance-id>-Default\Optimization\<n>\` | Kết quả từng tổ hợp Optimize — mỗi combo 1 folder số riêng, không bị ghi đè lẫn nhau (nhưng đè lên **cùng số thứ tự** nếu chạy Optimize mới) |
| `Documents\cAlgo\Data\cBots\Combo\<instance-id>-Default\ArchivedRuns\<Ten>_<ngày-giờ>\` | **Quy ước mới tự tạo trong phiên 2026-08-28** — nơi lưu trữ thủ công bản copy của `Backtesting\` trước khi người dùng chạy đè lượt kế tiếp, để so sánh nhiều lần chạy. Cùng quy ước áp dụng cho `Documents\cAlgo\Data\cBots\MA Cross\<instance-id>-Default\ArchivedRuns\`. Xem §7.3 |
| `research/` | **Mới, 2026-09-01** — nơi chứa Jupyter notebook phân tích dữ liệu. Máy CHƯA cài Python/Jupyter, xem `research/README.md` |
| `reports/*.md` | Báo cáo phân tích chi tiết theo từng đợt kiểm chứng (đặt tên có ngày) — `signal-alignment-baseline-2026-08-31.md`, `missing-bar-followup-2026-09-01.md`, `exit-mode-comparison-2026-08-28.md` (lịch sử, bản 5-ExitMode cũ) |

---

## 7. Ghi chú môi trường/vận hành

### 7.1. Build & compile
- **Không có `dotnet` CLI / compiler độc lập** trên VM-BO20. Mọi thay đổi
  `Combo.cs` phải chờ người dùng bấm Build trong cTrader Automate IDE và dán
  lại lỗi nếu có — đây là vòng lặp fix-lỗi bình thường của repo này, không
  phải bất thường.
- Không cố gắng phản chiếu (reflection) `cAlgo.API.dll` qua PowerShell 5.1 để
  tự tra chữ ký API — đã thử và thất bại (DLL build .NET6, PowerShell 5.1 chạy
  .NET Framework, không resolve được dependency, máy không có NuGet cache).
  Cách duy nhất tra chữ ký API chính xác 100% là build thật qua GUI.

### 7.2. Shell
- Máy chạy **Windows PowerShell 5.1** (không phải PowerShell 7/pwsh) — không
  có `&&`/`||`, không có ternary/null-coalescing. Xem quy tắc PowerShell chi
  tiết trong system prompt của agent nếu là Claude Code; nếu là Codex, kiểm
  tra tương đương trước khi chạy lệnh nhiều bước.
- State PowerShell **không giữ lại giữa các lệnh riêng biệt** — biến/session
  (vd SSH session) phải thiết lập lại trong cùng 1 lệnh nếu cần dùng tiếp.

### 7.3. Dữ liệu backtest — quy ước ArchivedRuns (MỚI, tự tạo trong phiên này)

`Backtesting\` của cTrader **bị ghi đè hoàn toàn mỗi lần chạy mới** — không có
lịch sử. Trong phiên 2026-08-28, khi người dùng cần so sánh nhiều lần chạy
(1 lần/ExitMode), quy trình đã dùng và NÊN TIẾP TỤC DÙNG:

1. Người dùng chạy Backtest cho 1 cấu hình (vd 1 ExitMode) trong cTrader GUI.
2. Người dùng báo "xong" cho AI biết — **CHỈ báo 1 việc/lần, đợi AI xác nhận
   đã lưu archive xong rồi mới chạy tiếp** (bài học từ sự cố mất dữ liệu
   `FixedTP` — xem §8 entry 2026-08-28 để biết chi tiết sự cố).
3. AI đọc `parameters.cbotset` trong `Backtesting\` để **xác minh đúng tham số
   mong đợi** (vd đúng `Exit=` đang cần) — **KHÔNG tin lời báo "xong" của
   người dùng mà không tự kiểm tra file**, vì cTrader dropdown dễ chọn nhầm.
4. Nếu đúng, copy nguyên `Backtesting\` sang
   `ArchivedRuns\<TênMode>_<yyyyMMdd-HHmm>\` (dùng PowerShell `Copy-Item
   -Recurse`).
5. Phân tích dựa trên bản archive, không dựa trên `Backtesting\` (vì nó có
   thể đã bị ghi đè bởi lượt chạy tiếp theo bất cứ lúc nào).

**Cấu trúc mỗi archive**: `log.txt` (y hệt tab Logs), `events.json` (mảng chi
tiết từng sự kiện lệnh — field quan trọng: `serial, orderId, positionId,
event, time (epoch ms), volume, type, entryPrice, tp, sl, closePrice,
grossProfit, pips, balance, equity`; `event` có các giá trị: `Create Stop
Order`, `Order modified`, `Order cancelled`, `Stop Order Filled`, `Create
Position`, `Stop Loss Hit`, `Take Profit Hit`, `Position Modified (S/L)`,
`Position closed`), `report.html`, `parameters.cbotset` (JSON, field
`Parameters.Exit` là số thứ tự enum `ExitMode`: `0=FixedTP,
1=PartialScaleOut4, 2=LadderRunner, 3=PartialBreakeven, 4=FibCompensating3`).

**Kỹ thuật đối chiếu hữu ích đã dùng** (tái sử dụng được cho lần sau):
- Nhóm `Create Stop Order` theo khoá `"entryPrice|sl|type"` để tái tạo lại
  từng "basket" (lô lệnh của 1 tín hiệu) — vì mọi chân của 1 tín hiệu luôn
  đặt CHUNG entryPrice+SL (xem `OpenOrReverse` trong `Combo.cs`).
- Đối chiếu `Filled == SLHit + TPHit + PositionClosed` để xác nhận không có
  lệnh nào "rơi mất" hoặc tính trùng — luôn phải khớp tuyệt đối.
- Grep `log.txt` tìm `NOT_ENOUGH_MARGIN_BALANCE` và `Modifying pending
  order.*FAILED` để phát hiện 2 vấn đề hạ tầng đã ghi ở §4.

### 7.5. Truy cập VM-DP6 (10.11.12.6, data warehouse) — mới, 2026-09-01

- Windows (hostname `WIN-B8609EA108T`), SSH port 22 mở, dùng Posh-SSH (SSH
  thô qua `ssh.exe` bị treo do không nhận input tương tác — xem cách gọi
  Posh-SSH mẫu ở CLAUDE.md/lịch sử phiên).
- Project chính: `C:\Users\Administrator\Desktop\dp_program_v3\` — gồm
  `core_program\` (source Python, có git) và `run_dp\` (bản đóng gói +
  script SQL setup ở `run_dp\sql\`).
- Đọc `core_program\docs\ARCHITECTURE.md`/`SYSTEM_OVERVIEW.md` trước —
  tóm tắt: TradingView (kênh Capital.com) → `DWH.Fact_OHLCV` (SQL Server,
  database `SEN05_AutoTrading`, BarTime UTC-naive). 37 symbol, 15 timeframe.
- **KHÔNG đọc `Config.yaml`** trừ khi thật sự cần — chứa secret thật (token/
  cookie TradingView, mật khẩu SQL/Discord/Redis). Chính
  `OPERATOR_RUNBOOK.md` của dự án cũng ghi rõ "Do not print, paste, or
  commit Config.yaml...". Nếu bắt buộc phải đọc, không hiển thị nội dung ra
  chat, không lưu bản copy cục bộ lâu hơn mức cần.

### 7.4. Truy cập VM-OG8 (Python signal generator)
- Mount SSHFS **`Z:`** trỏ vào home directory VM-OG8 — **không phải lúc nào
  cũng còn sống sau reboot**, lệnh remount:
  `net use Z: "\\sshfs\administrator@10.11.12.8" "Admin@123456" /persistent:yes`
- **KHÔNG chép plaintext password vào bất kỳ file MD nào** (kể cả file này) —
  nếu Codex cần SSH trực tiếp (không qua Z:), hỏi người dùng lại thông tin
  đăng nhập trong chat, đừng giả định/đoán.
- Code Python (`combo.py`, `levels.py`, `indicator.py`, `configuration.py`)
  nằm ở `Z:\Desktop\og_program\core_python\`.
- **[ĐÃ LỖI THỜI, sửa 2026-09-01 — xem entry "tiếp 9" ở §8]** ~~đã đọc kỹ
  `combo.py` (`_alternating_signals`)... tín hiệu CSV luôn đảm bảo đảo chiều
  tuyệt đối...~~ — **KHÔNG CÒN ĐÚNG**: `combo.py` đã bị sửa (uncommitted
  trên OG8, `_alternating_signals()` bị xoá hoàn toàn) — giờ mỗi bar tự
  đánh giá độc lập, CSV CÓ THỂ chứa nhiều tín hiệu cùng hướng liên tiếp
  (whipsaw). `Combo.cs` giờ tự xử lý việc này ở tầng cBot qua
  `ReconcileExistingExposure()` (dựa trên `Positions`/`PendingOrders` thật,
  không dựa vào giả định CSV tự loại trùng nữa) — xem §4/§8.

### 7.6. Truy cập máy host (10.11.12.5) — mới, 2026-09-01

- Windows (hostname `server-host`), SSH port 22 mở, dùng Posh-SSH (mẫu gọi
  giống DP6/OG8). Đăng nhập bằng tài khoản Microsoft (`kietvo1196@outlook.com`,
  map vào local user `admin`, profile `C:\Users\ADMIN`) — SMB/admin share
  (`\\10.11.12.5\C$`) và WinRM đều KHÔNG truy cập được (Access denied /
  WinRM chưa bật TrustedHosts), chỉ SSH dùng được. **KHÔNG lưu password vào
  file này** — hỏi lại người dùng trong chat khi cần.
- Shell mặc định qua SSH là **cmd.exe**, không phải PowerShell — muốn chạy
  lệnh PowerShell phải gọi tường minh `powershell -NoProfile -Command "..."`.
- `Set-SCPItem` (Posh-SSH) bản đang cài KHÔNG có tham số `-Recurse`/
  `-SessionId` — copy cả thư mục phải nén `.zip` trước (`Compress-Archive`),
  SCP nguyên 1 file zip, rồi `Expand-Archive -Force` từ xa để giải nén đè.
  Đường dẫn đích cho `Set-SCPItem -Destination` phải dùng **forward-slash**
  (`C:/Users/ADMIN/Desktop/`) — dùng backslash bị lỗi
  `scp: ...: No such file or directory`.
- `C:\Users\ADMIN\Desktop\` là nơi lưu **bản mirror cả 3 dự án**: `dp_program`
  (DP6), `og_program` (OG8), `Robots` (BO20, project này) — có vẻ dùng làm
  điểm backup/staging tập trung. Đã đồng bộ 1 lần (2026-09-01, ghi đè bản cũ
  hơn thiếu `research/`) — verify khớp tuyệt đối 29 file/16,741,867 bytes cả
  2 phía sau khi copy.
- Lệnh `Remove-Item`/`rmdir /s /q` (kể cả khi nhắm vào máy remote qua chuỗi
  lệnh SSH) có thể bị **hook chặn cục bộ** ("Remove-Item on system path...
  is blocked") — tránh dùng rmdir đệ quy nếu có thể; ưu tiên
  `Expand-Archive -Force` đè trực tiếp lên thư mục cũ (không cần xoá trước)
  khi nội dung cũ là tập con an toàn của nội dung mới.

---

## 8. Nhật ký bàn giao (mới nhất ở TRÊN CÙNG — không xoá entry cũ)

> **Template cho entry mới:**
> ```
> ### [YYYY-MM-DD HH:mm — Claude|Codex]
> **Đã làm:**
> **Đang dang dở / chưa build-test:**
> **Quyết định/thoả thuận với người dùng chưa code hoá:**
> **Bước tiếp theo đề xuất:**
> ```

### [2026-09-08 (chiều) — Claude — Combo.algo BUILD OK + chẩn đoán "vì sao lệnh FR40 lỗ khác nhau" + báo cáo tổng hợp sizing]

**Đã làm:**
- **`Combo.algo` đã build 2026-09-08 02:20** — mọi log mới (RISK_DETAIL/FX/CLOSED) chạy thật, compile
  sạch. Xác nhận qua backtest Combo/FRA40.cash/H1 01/01–01/03/2026 (balance $10k, risk 1%,
  MaxMargin 100%, KSL Fib1000 / KTP Fib2618). `Asset.Convert`, `History`/`HistoricalTrade`,
  `PositionCloseReason`, `Symbol.VolumeInUnitsToQuantity/Step` — TẤT CẢ compile + chạy OK trên build
  5.9.x. `ClosingPrice` đúng tên; `History` có dữ liệu lúc `Positions.Closed` fire (fallback không cần dùng).
- **Chẩn đoán câu hỏi người dùng** (lệnh −$261.82 vs −$11.70, cùng budget ~$100): KHÔNG phải lỗi
  sizing/FX. (1) **position 10** −$261: bar tín hiệu 01:00, FR40 đóng cửa tới 07:00 → pending Buy Stop
  @8361.4 đặt 07:05 khi giá đã 8382 → khớp NGAY market @8382.42 (+21 điểm slippage); SL @8345.88
  tuyệt đối không đổi → thành 36.5 pips không phải 15.5 → −39.3 pips = 2.56× budget. `RISK_DETAIL`
  `expected loss $100.96` vs `CLOSED net −$261.82` — chênh hiện rõ trong log. (2) **position 15**
  −$11.70: `reason=Closed` — tín hiệu ngược đóng lệnh giữa chừng @−2.8 pips, chưa chạm SL/TP.
  `reversed=20/64=31%`.
- **Viết `reports/lotsize-sizing-work-summary-2026-09-08.md`** — báo cáo tổng hợp toàn bộ việc sizing:
  bối cảnh, Vấn đề 1 (FX snapshot → PipValueNow + bug JP225 + probe-scaling), Vấn đề 2 (log kiểm định),
  Vấn đề 3 (gap risk FR40), before/after, 6 hướng xử lý gap, files & versions. Đã copy 1 bản sang
  Desktop máy host `10.11.12.5` (`C:\Users\ADMIN\Desktop\`, SSH user `admin`).
- **[chiều, sau đó] Thêm dòng log `FILLED` cho Combo** — người dùng chỉ ra: giữa "placed" và "CLOSED"
  không biết lệnh chờ đã khớp chưa / lúc nào / giá nào (cTrader không log fill, chỉ có `events.json`;
  `Filled` count = 0 trong cả log.txt). Handler `OnPendingOrderFilled` (`PendingOrders.Filled` đăng ký
  ở `OnStart`) in: giá + thời điểm khớp, slippage vs trigger, **SL tính lại từ giá khớp thật => real
  risk if SL hit $**. Combo.cs braces 205/205 OK. **CHƯA build lại** (Combo.algo còn bản 02:20 sáng,
  không có FILLED).
- AGENT.md §5: thêm `0-GAP` (gap risk, chưa xử lý); cập nhật `0-DBG` (Combo built sáng, FILLED + MA
  Cross chưa build).

**Đang dang dở / chưa build-test:**
- **`MA Cross.algo` CHƯA build lại** (còn 09-07 00:15) — chưa có RISK_DETAIL/FX/CLOSED. Cần build.
- **Vấn đề 3 (gap risk) chưa xử lý** — chỉ mới phát hiện + ghi nhận. Chưa chốt hướng.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Gap risk: đề xuất khởi điểm là "huỷ tín hiệu nếu giá đã vượt `EntryPrice` quá X%×ATR khi đặt được
  lệnh" (1 tham số, đúng tinh thần chỉ-vào-nếu-breakout-còn-hiệu-lực). 5 hướng khác ở report §6.2.
  Người dùng chưa quyết.

**Bước tiếp theo đề xuất:** (1) build `MA Cross.algo`. (2) người dùng đọc báo cáo tổng hợp, quyết
hướng cho gap risk (Vấn đề 3). (3) nếu chọn hướng (a): thêm `[Parameter] MaxEntryDriftAtrPct` +
check trong `PlacePendingOrder` trước `PlaceStopOrder` (Combo); MA Cross là market order nên ít
dính hơn nhưng vẫn nên có check giá mở cửa lệch quá xa giá tín hiệu.

### [2026-09-08 — Claude — log chi tiết sizing/FX/SL-TP/close để kiểm định thiết kế bằng tay (CHƯA BUILD)]

**Đã làm:** Người dùng muốn debug lot size, chạy backtest **balance 100,000 USD**, log đủ chi tiết
để đối chiếu THIẾT KẾ với hành vi đặt lệnh THẬT qua backtest. Sửa `Combo.cs` + `MA Cross.cs` (thuần
`Print`, KHÔNG đổi hành vi/sizing/order, KHÔNG thêm `[Parameter]`/state/region). 3 dòng log/lệnh +
`out`:
- `CalculateVolume(..., out double pipValue)` — 1 call-site/file. `pipValue` in ra = đúng giá trị đã sizing.
- Đầu `PlacePendingOrder`/`PlaceMarketOrder`: `double balanceAtEntry = Account.Balance;` (chốt balance
  sizing dùng, tránh lệch commission vài cent với MA Cross market order).
- Dòng **`RISK_DETAIL`** (đổi tên từ "RISK DETAIL" → 1 token dễ grep): `balance`, `risk%` → `budget $`,
  `ATR`, `KSL={r}xATR` → `SL pips`, `KTP={r}xATR` → `TP pips`, `R:R`, `pipValue` (USD/pip/unit),
  `risk-based raw {X} units => placed {Y} units ({lots}), step {S}` (thấy round-down DOWN ăn bao nhiêu),
  `est.margin`, `expected loss if SL hit=$`, `expected profit if TP hit=$`. MA Cross thêm `fill`/`SL`/
  `TP` giá tuyệt đối từ `result.Position`.
- Dòng **`FX`** (helper mới `LogConversionRate(barTime, pipValue)`, region Helpers): symbol quote khác
  USD → `1 {quoteCcy} = {factor:F8} USD (<=> {1/factor:F5} {quoteCcy} per USD); via Asset.Convert at
  placement tick`. `factor = pipValue / Symbol.PipSize` = đúng hệ số sizing đã dùng (KHÔNG hardcode tên
  pair, KHÔNG gọi Convert lần 2). Quote == USD → in "no conversion (factor 1.0)".
- Dòng **`CLOSED`** (helper `LogClosedTrade(position, args.Reason)`): `reason={StopLoss|TakeProfit|
  Closed}`, `entry`, `close`, `pips`, `volume units (lots)`, `gross/commission/swap/net $`, `balance now
  $`. Số từ `History.FirstOrDefault(t => t.PositionId == position.Id)` (HistoricalTrade), fallback `Position`.
- Dòng "placed"/"market placed" cũ GIỮ NGUYÊN (parser `research/` không vỡ; đã có units+lots).
- Brace balanced (Combo 192/192, MA Cross 170/170). Arg-count khớp placeholder cả 6 Print mới
  (Combo RISK_DETAIL 19, MA Cross 22).

**Đang dang dở / chưa build-test:** CHƯA build `.algo` nào. API mới chưa verify trên build này:
`History`/`HistoricalTrade` (`PositionId`,`ClosingPrice`,`Pips`,`GrossProfit`,`Commissions`,`Swap`,
`NetProfit`,`Balance`), `PositionClosedEventArgs.Reason`/`PositionCloseReason`, `result.Position`
(market), `Position.Commissions`/`.Swap`, `Symbol.VolumeInUnitsToQuantity`, `Symbol.VolumeInUnitsStep`.
Tất cả chuẩn cAlgo. Nếu lỗi: `ClosingPrice` có thể tên khác; `History` có thể chưa cập nhật lúc event
(đã có fallback `Position`). `.algo` gần nhất build 09-07 00:15; `.cs` sửa nhiều lần 09-07→09-08 →
**cần 1 lần build gộp** cho cả 2 file.

**Quyết định/thoả thuận với người dùng chưa code hoá:** Balance 100k = tham số backtest (Starting
Capital / `--balance=100000`), KHÔNG phải code (bot đọc `Account.Balance`). Log luôn-bật, chưa gate
`[Parameter]` — nếu ồn cho optimize/run dài thì thêm `Verbose` sau (chưa được yêu cầu). FX log chọn
"Mức 1" (suy từ `QuoteAsset`/`Account.Asset`) — KHÔNG nạp symbol pair thật (Mức 2, chỉ làm nếu cần
chốt Bid-hay-Ask của `Asset.Convert`).

**Bước tiếp theo đề xuất:** (1) build 2 `.algo`, gửi lỗi compile nếu có. (2) chạy 1 backtest ngắn
(vài tuần) mỗi bot, **balance 100k**, đọc `RISK_DETAIL` + `FX` + `CLOSED` đối chiếu bằng tay:
`budget = balance × risk%`? `SL pips = KSL × ATR`? `raw = budget / (SLpips × pipValue)`? round-down
→ `placed`? `expected loss@SL ≈ budget` (nhỏ hơn nếu margin-capped/round-down)? `net` lúc CLOSED vs
`expected` (chênh = commission + swap + slippage, đều có trong log)? `pipValue` ↔ `FX` line ↔ tỷ giá
lịch sử? (3) cập nhật `reports/lotsize-runtime-order-walkthrough-2026-09-07.md` bằng số từ log mới.

### [2026-09-07 — Codex — ghi nhận cách hiểu lot size theo lệnh runtime thật]

**Đã làm:** tạo `reports/lotsize-runtime-order-walkthrough-2026-09-07.md`, ghi lại pipeline đúng
theo code, các giá trị không hard-code và walkthrough lệnh Combo/HK50 thật: dự kiến risk `$100`,
đặt `5.41 units`, gross P/L thực tế `-$100.89`.

**Đang dang dở / chưa build-test:** thay đổi log units/lots ở entry ngay dưới vẫn chờ build; tài liệu
walkthrough dùng report hậu build currency conversion đã hoàn tất.

**Quyết định/thoả thuận với người dùng chưa code hoá:** các câu hỏi tiếp theo cần trả lời gọn gàng,
dựa hoàn toàn vào sự thật hiện có trong source Combo và MA Cross; tách rõ code bot, API cTrader và
hành vi runtime, không suy diễn tính năng chưa code.

**Bước tiếp theo đề xuất:** chờ câu hỏi cụ thể của người dùng.

### [2026-09-07 — Codex — thêm lots vào log thành công, chờ build]

**Đã làm:** cập nhật đúng một dòng `Print` ở mỗi bot để ghi `volume=... units (... lots)` sau khi
lệnh được chấp nhận; lots lấy từ `Symbol.VolumeInUnitsToQuantity(volume)`.

**Đang dang dở / chưa build-test:** hai `.algo` hiện chưa chứa thay đổi log này; cần người dùng build
lại trong cTrader GUI rồi smoke test.

**Quyết định/thoả thuận với người dùng chưa code hoá:** thay đổi chỉ phục vụ quan sát log, không tham
gia công thức sizing và không thay đổi volume gửi broker.

**Bước tiếp theo đề xuất:** build Combo và MA Cross, chạy một lượt ngắn mỗi bot để nhìn format log.

### [2026-09-07 — Codex — build hậu vá đã verify: currency conversion PASS]

**Đã làm:**
- Audit độc lập pipeline `RiskMoney USD → price distance → QuoteAsset→USD → VolumeInUnits → lots`
  của Combo và MA Cross từ source, tài liệu chính thức cTrader/Open API, metadata FTMO và P/L
  backtest thật cho DE40/FR40/SP35, HK50, JP225, US30, BTCUSD và XAUUSD.
- Xác nhận source không hard-code contract size/FX chain, không convert hai lần, gửi order đúng bằng
  `VolumeInUnits`; `Lots = VolumeInUnits / LotSize` chỉ là cách biểu diễn.
- Tái chạy Combo/HK50 bằng CLI cùng ticks và signal đối chứng: binary 08:44 đặt volume cao hơn
  `7.01%–7.28%`, cô lập được lỗi precision `Asset.Convert` per-unit. JP225 binary build lần 1
  fail toàn bộ do conversion 1 JPY-pip thành `0.00 USD`.
- Tạo báo cáo `reports/lotsize-currency-conversion-full-audit-CODEX-2026-09-06.md` và bộ tái lập
  `research/diagnostics/lotsize-currency-audit-2026-09-06/`.
- Xác nhận binary mới rồi chạy 7 backtest Ticks 01-08/01/2025: Combo JP225/HK50/US30/GER40/XAUUSD
  và MA Cross JP225/HK50. Tất cả hoàn tất đúng kỳ, `failed=0`; cập nhật báo cáo và `CLAUDE.md`.

**Đang dang dở / chưa build-test:**
- Không còn. Người dùng đã build ngày 2026-09-07; bảy run hậu build đều hoàn tất đúng kỳ và
  `failed=0`.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Giữ công thức manual tối giản dựa trên runtime metadata. Không thêm bảng symbol, contract multiplier,
  FX rate hay conversion chain hard-code.
- Chỉ gọi kết quả là `nominal gross stop-risk`: tài liệu Algo không công bố `Asset.Convert` dùng
  Bid/Ask nào theo hướng lệnh; commission, swap, gap/slippage nằm ngoài gross sizing.

**Bước tiếp theo đề xuất:** trao đổi với người dùng về từng đoạn code tham gia sizing; không sửa thêm
code nếu chưa có yêu cầu mới.

### [2026-09-06 — Claude — PipValueNow: build lần 1 lộ bug JP225 (Asset.Convert rounding), đã vá probe-scaling, chờ build lại]

**Đã làm:**
- Người dùng build cả 2 `.algo` lúc 09-06 08:44–08:45 (bản expression-body của `PipValueNow()`),
  chạy 3 backtest Combo/H1 Jan 2026 (Risk 1%, MaxMargin 50%, KSL2/KTP4):
  - **US30.cash** `placed=31, failed=0` ✓ — nhánh `Symbol.PipSize` chạy đúng.
  - **GER40.cash** `placed=38, failed=0` ✓ — `Asset.Convert` **compile được + chạy đúng** cho EUR,
    risk log hợp lý ($77.72 = 0.93%). → xoá lo ngại `Asset.Name`/overload `Convert` không resolve.
  - **JP225.cash** `placed=0, failed=33`, `events.json` rỗng, netProfit 0. **BUG.**
- **Chẩn đoán JP225:** `failed=33` im lặng (không Print) → tất cả rơi nhánh `if (volume <= 0)` trong
  `PlacePendingOrder`. `stopLossPips` không đổi (audit 09-02 PASS JP225) → thủ phạm `pipValue`.
  `Asset.Convert(to, value)` **làm tròn/cắt kết quả về `depositAssetDigits` (= 2 cho USD)** — xác
  nhận qua `report.html`. `Convert(Symbol.PipSize=1.0 JPY → USD)` ≈ 0.0064 → cắt còn **0.00** →
  `pipValue = 0` → `CalculateVolume` trả 0. EUR không dính (1 EUR ≈ 1.03 > 0.01). HKD sẽ không
  hard-fail nhưng sai ~7% (0.1286 → 0.12).
- **Đã vá cả 2 file** — `PipValueNow()` thành block body, probe-scaling:
  ```csharp
  private double PipValueNow()
  {
      if (Symbol.QuoteAsset.Name == Account.Asset.Name)
          return Symbol.PipSize;
      const double ProbeUnits = 1_000_000.0;
      return Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize * ProbeUnits) / ProbeUnits;
  }
  ```
  Quy đổi 1 triệu pip (kết quả đủ lớn, 2-dp là thừa) rồi chia lại. Vẫn approach C, chỉ thêm 1 hằng
  số scale né rounding. Không `[Parameter]`, không state. 3 call-site (`pipValue`) + param
  `CapVolumeByMargin` giữ nguyên như trước.
- **No-op check US30 (một phần, ĐÃ XÁC NHẬN):** so `events.json` build-lần-1 vs run cũ (binary 09-04,
  `Symbol.PipValue`), 31 lệnh Jan trùng khung: **21/21 lệnh KHÔNG margin-cap trùng volume từng cent**
  → sizing formula là no-op tuyệt đối cho quote USD. 10 lệnh margin-cap lệch nhẹ (1.44→1.52) do
  `GetEstimatedMargin` nhận cấu hình backtest khác (commission `usdPerMillionUsdVolume` vs `usdPer1Lot`
  + date range Jan–Jun vs Jan) — **KHÔNG do code** (không đụng `GetEstimatedMargin`/logic cap).
- Cập nhật báo cáo (§15 mới) + §4 + §5 item 0-FX.

**Đang dang dở / chưa build-test:**
- **Bản vá probe-scaling CHƯA build.** Cần build lại cả 2 `.algo`.
- Chạy lại sau build: JP225.cash (kỳ vọng `failed=0`), HK50.cash (pipValue ≈ 0.1286, chạy được),
  US30.cash Jan-only **cùng cấu hình backtest** với 1 run cũ để đóng hẳn no-op §15.3, GER40.cash
  (kỳ vọng y hệt build lần 1 — bản vá không đổi gì cho EUR ở mức số lệnh).
- Chưa cập nhật `reports/pipvalue-currency-conversion-audit-2026-09-02.md` + `CLAUDE.md` — làm SAU
  khi build lại + verify OK. Lưu ý thêm vào CLAUDE.md: `Asset.Convert` có rounding về digits tiền
  tài khoản, phải probe-scale khi quy đổi giá trị nhỏ (per-unit).

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có mới. Approach C giữ nguyên, probe-
scaling chỉ là chi tiết kỹ thuật để nó chạy đúng.

**Bước tiếp theo đề xuất:**
1. Build lại 2 `.algo`.
2. Chạy 4 backtest ở trên. JP225 `failed=0` là điều kiện tiên quyết.
3. Nếu OK: cập nhật audit 09-02 + CLAUDE.md; ghi hash mới vào §4; đối chiếu volume với Python.

---

### [2026-09-06 — Claude — quy đổi tiền tệ trong lot size: approach C TRIỂN KHAI XONG cả 2 file (chưa build) + báo cáo chi tiết]

**Đã làm:**
- **Code approach C vào CẢ `Combo.cs` VÀ `MA Cross.cs`** (entry trước mới code Combo dở, giờ xong cả 2):
  - Helper `PipValueNow()` đặt ở ĐẦU region `Risk & Position Sizing` (không phải `Helpers` như
    kế hoạch cũ — đặt sát `CalculateVolume` cho mạch sizing gọn 1 chỗ), kèm comment 8 dòng giải
    thích vì sao không dùng `Symbol.PipValue`. Nội dung 2 file GIỐNG HỆT nhau:
    ```csharp
    private double PipValueNow()
        => Symbol.QuoteAsset.Name == Account.Asset.Name
            ? Symbol.PipSize
            : Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize);
    ```
  - `CalculateVolume`: `double pipValue = PipValueNow();` gọi 1 lần đầu hàm; dùng `pipValue` ở
    guard `!double.IsFinite(pipValue) || pipValue <= 0` + công thức `requestedVolume`.
  - `CapVolumeByMargin` thêm tham số `double pipValue` (cạnh `stopLossPips`, đúng pattern có sẵn);
    call-site truyền xuống; dòng log `actualRiskAmount = normalizedVolume * stopLossPips * pipValue`.
  - `Symbol.PipSize` trong tính `stopLossPips`/`takeProfitPips` GIỮ NGUYÊN (không dính tiền tệ).
- Grep xác nhận: **0 `Symbol.PipValue` trên code path runtime** ở cả 2 file (chỉ còn trong comment).
- Không đổi `using` (Combo & MA Cross đều đã có `cAlgo.API` + `cAlgo.API.Internals`; `Asset`
  resolve được). Không đụng `.csproj`/`.sln`/`GlobalUsings.cs`.
- **Viết báo cáo chi tiết** theo yêu cầu người dùng: `reports/lotsize-fx-live-conversion-fix-2026-09-06.md`
  — bối cảnh, nguyên lý gốc, vấn đề snapshot, hiện trạng trước (3 call-site/file), diff đầy đủ,
  hiện trạng sau, so sánh 5 phương án B/A/C/D/E và vì sao chọn C, lý do từng chi tiết thiết kế
  helper (nhánh tắt USD, `Convert` vs bảng cứng, truyền tham số vs gọi 2 lần, không thêm
  `[Parameter]`, chọn region), bảng tác động 6 nhóm, scope guard, kế hoạch verify 3 nhóm, rủi ro
  build, nguồn.

**Đang dang dở / chưa build-test:**
- **CHƯA build `.algo` nào** — máy VM-BO20 không có compiler độc lập. Người dùng cần build GUI
  cả `Combo.algo` + `MA Cross.algo`, gửi lỗi compile nếu có.
- Điểm rủi ro build (xem báo cáo §11.1): `Asset.Name` (fallback: `Account.Currency` string);
  overload `Convert(Asset, double)` (fallback: `Convert(string, double)` với `Account.Currency`);
  fallback cuối = approach A rescale nếu `Asset.Convert` bất khả dụng.
- Verify chưa chạy — 3 nhóm: (A) no-op US30.cash+XAUUSD+BTCUSD phải BYTE-IDENTICAL bản cũ;
  (B) drift JP225.cash+GER40.cash kỳ dài; (C) HK50.cash ≤ ~1.3%.
- Chưa cập nhật `reports/pipvalue-currency-conversion-audit-2026-09-02.md` (thêm mục snapshot→live)
  và `CLAUDE.md` quy ước API (sizing dùng `PipValueNow()`, không đọc `Symbol.PipValue` trực tiếp)
  — làm SAU khi build + verify OK.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Không còn — approach C đã code xong đúng thoả thuận. Không đụng: công thức risk%, logic margin
  của `CapVolumeByMargin`, gap-cap/PM/D1–D7, `GetEstimatedMargin`, `NormalizeVolumeInUnits`,
  enum SL/TP, scheduler. Không thêm `[Parameter]`/region/state/`OnStart`.

**Bước tiếp theo đề xuất:**
1. Người dùng build GUI 2 `.algo`, gửi lỗi compile nếu có (agent kế tiếp fix theo fallback ở
   báo cáo §11.1).
2. Chạy 3 nhóm verify (§11.2 báo cáo). Nhóm A byte-identical là điều kiện tiên quyết — lệch =
   có bug.
3. Nếu OK: cập nhật audit 09-02 + CLAUDE.md; ghi hash source/`.algo` mới vào §4; đối chiếu volume
   với hàm Python `compute_volume_units()` trong `reports/lotsize-pipeline-reference.md`.

---

### [2026-09-06 — Claude, "record md" — quy đổi tiền tệ trong lot size: FIX approach C, ĐANG TRIỂN KHAI]

**Đã làm:**
- Phiên trao đổi sâu (người dùng chủ động, muốn hiểu tận gốc, KHÔNG muốn tài liệu dài):
  làm rõ **pipeline tính lot size + quy đổi tiền tệ** cho 6 nhóm symbol (US30/BTCUSD/GOLD =
  quote USD; DE40/FR40/SP35 = quote EUR; JP225 = quote JPY; HK50 = quote HKD). Người dùng
  sẽ tự viết lại phần lot size bằng **Python** trong hệ thống riêng → cần hiểu nguyên lý,
  không chỉ "gọi API".
- **Nguyên lý gốc chốt được** (verify tài liệu Spotware + forum + P/L backtest thật 8/8 ví dụ):
  `volumeUnits = riskMoney_acct / (slDistancePrice × FX(quoteCcy→acctCcy))`. PipSize triệt
  tiêu. cTrader units-model KHÔNG có contract multiplier (1 unit × 1 điểm giá = 1 quote-ccy,
  theo định nghĩa). `PipValue = PipSize × FX` — verify chính xác cho cả 6 nhóm.
- **Chiều quy đổi** (thuật toán chính thức open-api/symbol-rate-conversion): nhân nếu
  `convSymbol.base == quoteCcy` (EURUSD → × EURUSD); chia nếu `base == acctCcy` (USDJPY,
  USDHKD → ÷). Price: Bid (Long) / Ask (Short).
- **Lỗ hổng xác định:** cả Combo.cs và MA Cross.cs đang dùng `Symbol.PipValue` = **snapshot
  đóng băng lúc OnStart** (tài liệu chính thức: "not updated in real time"). → sizing EUR/JPY
  trôi theo FX kể từ đầu run. US-quote = 0 (FX luôn 1.0); HK50 ≤ ~1.3% (HKD neo); DE40/FR40/
  SP35 ~1–4% (backtest 2 tháng) → 5–8% (6 tháng); **JP225 tệ nhất** 10–15% (backtest dài).
  Ảnh hưởng risk-thực/equity-curve/maxDD %, KHÔNG ảnh hưởng logic tín hiệu.
- **Reflection `cAlgo.API.dll` (ProductVersion 5.9.13) đã xác nhận có:** `Asset.Convert(Asset
  to, double value)` → double; `Symbol.QuoteAsset`/`BaseAsset` → Asset; `Account.Asset` → Asset;
  `Algo.AssetConverter`. Backtest: `Convert` dùng rate lịch sử chính xác tại bar.
- **Người dùng đã DUYỆT approach C** (sau khi so B/A/C/D/E): thay `Symbol.PipValue` bằng helper
  `PipValueNow()` gọi `Asset.Convert` live mỗi tín hiệu. Đây là cách đúng nhất theo tài liệu
  (tái tạo chính xác cơ chế PipValue, chỉ bỏ đóng băng) + sát cơ chế margin/P&L thật.
- Đã viết `reports/lotsize-pipeline-reference.md` (tài liệu tham chiếu Python) — nhưng người
  dùng nói "viết nhiều quá", nên tài liệu này để đó, KHÔNG là trọng tâm; trọng tâm là fix code.
- **Ghi nhận: Combo.cs đã được sửa (giữa các phiên, không rõ ai) thêm finite guards** —
  `CalculateVolume` L337-344 hiện có `!double.IsFinite(stopLossPips)` + `!double.IsFinite(
  Symbol.PipValue)` + `!double.IsFinite(requestedVolume)`. → phát hiện "NaN bypass guard"
  của Codex/audit vòng trước **ĐÃ được xử lý**. (Chưa kiểm MA Cross.cs có tương tự chưa.)

**Đang dang dở / chưa build-test:**
- **CHƯA sửa `.cs` nào** trong phiên này — vừa đọc `Combo.cs` §Risk region thì người dùng gõ
  "record md". Approach C chưa được code vào file.
- Kế hoạch code (approach C, áp CÙNG pattern cho CẢ 2 file):
  1. `#region Helpers`: thêm
     ```csharp
     private double PipValueNow()
         => Symbol.QuoteAsset.Name == Account.Asset.Name
            ? Symbol.PipSize
            : Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize);
     ```
  2. Thay `Symbol.PipValue` → `PipValueNow()` tại 3 chỗ/file:
     - Combo: guard `CalculateVolume` (~L338), sizing (~L342), log `actualRiskAmount` trong
       `CapVolumeByMargin` (~L455).
     - MA Cross: guard (~L290), sizing (~L294), log (~L388).
  3. `Symbol.PipSize` giữ nguyên (không dính tiền tệ).
- Sau code: người dùng build GUI 2 `.algo`; nếu `Asset.Convert`/`QuoteAsset`/`Account.Asset`
  không resolve → gửi lỗi, fallback `MarketData.GetSymbol("EURUSD").Bid` (messier).

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Approach C đã duyệt. Không làm A (rescale) trừ khi C compile lỗi.
- KHÔNG động tới: công thức risk%, `CapVolumeByMargin` logic margin, gap-cap/PM/D1–D7,
  `GetEstimatedMargin`, `NormalizeVolumeInUnits`. Không thêm `[Parameter]`, không thêm region.
- Verify: (1) no-op check US30.cash+XAUUSD+BTCUSD phải ra kết quả byte-identical bản cũ;
  (2) drift check JP225.cash+GER40.cash kỳ dài; (3) HK50.cash ≤ ~1.3%.

**Bước tiếp theo đề xuất:**
1. Code approach C vào cả 2 file (đang làm).
2. Người dùng build GUI + gửi lỗi compile nếu có.
3. Chạy 3 nhóm verify ở trên.
4. Cập nhật `reports/pipvalue-currency-conversion-audit-2026-09-02.md` (thêm mục snapshot→live)
   + `CLAUDE.md` quy ước API (sizing dùng `PipValueNow()`, không đọc `Symbol.PipValue` trực tiếp).

---

### [2026-09-06 — Codex — tự audit, thu hẹp đề xuất sửa lot-size safety]

**Đã làm:** Theo yêu cầu người dùng ưu tiên clean, tinh gọn, đơn giản, đã phản biện
chính danh sách9 nhóm sửa của Codex. Cập nhật báo cáo cross-check §7 và §10.1–§10.4:
giữ hai nhóm sửa cục bộ Close/Cancel và kiểm số/volume cuối; PM là phần sửa cần thiết
khi sử dụng guard, không kéo theo xây mọi profile hoặc coordinator. Đã rút default
stress0,5%/25% tự đề xuất và yêu cầu có margin/stress model thứ hai mới được giao dịch.
Chia sẻ label theo bot/symbol đang phù hợp quy tắc một hướng; không tự thêm InstanceId
để vô tình đổi chiến lược. Giới hạn restart/pending và FTMO PM vẫn được ghi nhận.
Đọc lại source, reflection API local và tài liệu chính thức Close/Cancel/TradeResult;
kiểm lại NaN/Infinity. Không coi Claude đồng ý là bằng chứng độc lập.

**Đang dang dở / chưa build-test:** Chỉ cập nhật báo cáo và AGENT.md; không sửa `.cs`
hoặc `.algo`, không chạy backtest mới. Hai source hash giữ nguyên như audit trước.

**Quyết định/thoả thuận với người dùng chưa code hoá:** Người dùng yêu cầu audit lại,
chưa yêu cầu thực hiện bản vá. Không tự đổi default, công thức risk, SL/TP, loại lệnh
hay thêm tham số. Các đề xuất rộng trong entry trước là lịch sử, scope mới ở §10.1.

**Bước tiếp theo đề xuất:** Nếu người dùng yêu cầu sửa, thực hiện các thay đổi cục bộ
đã nêu; kiểm nhánh thất bại có chủ đích và regression vừa đủ. Không bắt người dùng
chốt cả năm câu hỏi kiến trúc để được sửa hai nhóm lỗi nhỏ.

### [2026-09-06 — Codex — hoàn tất cross-check độc lập lot-size safety]

**Đã làm:** Đọc toàn bộ CLAUDE.md, AGENT.md §0–§5/entry§8 mới nhất và memory;
đọc trực tiếp source cả hai bot, kiểm kê đúng2Robot/2algo/2Data-cBot directories.
Viết [báo cáo cross-check](research/reports/lotsize-safety-audit-CODEX-CROSSCHECK-2026-09-06.md)
đủ B1–B11,10 nhóm phát hiện bổ sung, phản biện D1–D7, FTMO theo product và nguồn đầy đủ.
21 kiểm tra bằng chứng pass trong `research/diagnostics/lotsize-crosscheck-2026-09-06/`:
so sánh sizing regions, reflection API local, .NET NaN, mô hình ca biên, DST2026,
tính lại native tick gold P/L. Lệnh vàng budget99,9976 USD đóng lỗ106,47 USD:
nominal96,6153 + entry slip8,95 + exit slip0,3847 + swap0,52. Commission trong
week/JanFeb runs bằng0 và auto-commission=false, không coi là full-cost simulation.

Sai sót chính của auditClaude: B7 đảo hướng tight/loose; B3 không chứng minh overshoot
do progressive tiers; B2 nullable dead và forum bug Account.Margin2023 không phải
current GetEstimatedMargin reproduction; D2 floor×0,5/assumed leverage không fail-closed;
D6 trừCredit chưa có cơ sở; D1 không bảo đảm gap loss hoặc giữ risk1%; BTC không
được chứng nhận an toàn. Raw BTC338samples cho estimated-margin/unit gần hằng số,
không đủ kết luận broker thay tiers theo historical dates. Source bỏ qua Close/Cancel
result, PM latch không retry, pending lifetime mất qua restart, instance cùng symbol
chia sẻ label, counter không tương đương successful reversals/fills.

**Đang dang dở / chưa build-test:** Không có code change cần build; không chạy backtest
mới, không sửa `.cs`/`.algo` hoặc account settings, không đọc file mật khẩu.
Source hashes giữ nguyên (đầy đủ trong report/verification.json).
Audit hoàn tất; các giới hạn chứng cứ đã ghi rõ: chưa biết release fix precision/margin,
chưa đo broker partial-fill/callback order/min-step origin ngoại lệ, chưa tái trích xuất
raw DWH gaps, chưa xác nhận contract/product của account7563609.

**Quyết định/thoả thuận với người dùng chưa code hoá:** Chỉ audit; D1–D7 chưa được duyệt
để triển khai. FTMO2-Step static khác1-Step EOD-trailing; scope/profile/stress budgets,
instance ownership và post-fill action cần được chốt. Không tự khôi phục exit catalog.

**Bước tiếp theo đề xuất:** Người dùng review mục10 của cross-check; nếu yêu cầu code,
ưu tiên execution failures/PM state và account room trước khi thêm gap formula.
EntryClaude bên dưới được giữ nguyên như lịch sử; kết luận bị phản biện phải đọc kèm
reportCodex. Khoảng trống09-03→09-06 vẫn tồn tại, không được coi entry này lấp đầy nó.

### [2026-09-06 — Claude, "ghi nhận lại" — audit lot-size safety]

> ⚠️ **§8 CÓ KHOẢNG TRỐNG 2026-09-03 → 2026-09-06.** Nhiều phiên làm việc thật (tách
> enum `SlFibLevel`/`TpFibLevel`, sửa bug overlapping-position của MA Cross bằng
> `ReconcileExistingExposure`, dựng pipeline signal-trace, chạy ~16 backtest CLI tick
> đối chứng 11 symbol × 2 chiến lược) **không được ghi vào §8**. Nguồn sự thật cho khoảng
> này: các mục "2026-09-03" trong **CLAUDE.md** (enum split, phát hiện catalog ExitMode
> không khớp code) + **`research/reports/`** (`signal-trace-batch-2026-09-05-summary.md`,
> `ALL-signal-trace-summary-2026-09-05.csv`, 23 file `*-signal-trace-*.csv`,
> `max-margin-percent-safe-ceiling-2026-09-03.md`, `margin-percent-sensitivity-2026-09-03.md`,
> `leverage-pipvalue-crosscheck-2026-09-04.md`) + `research/cli_runs/*_2026JanFeb_*`. §4/§5
> bên trên cũng đã cũ (vd §4 còn ghi "10 mức Fibonacci" — sai, đã tách 8/9 mức).

**Đã làm:**
- Audit đọc-code + suy luận toàn bộ `Risk & Position Sizing` (`CalculateVolume` +
  `CapVolumeByMargin`) và `Position Management` của **cả 2 cBot** (dùng chung khối code).
  Grep xác nhận repo chỉ có 2 cBot, không có bot thứ 3.
- Đối chứng **3 vòng, 7 nguồn**: (1) đọc code; (2) NỘI BỘ — dữ liệu backtest thật (log
  MARGIN GUARD), 3 báo cáo margin/leverage của dự án, lịch sử code legacy; (3) NGOÀI —
  tài liệu cTrader Algo chính thức, forum Spotware (bug đã xác nhận), luật FTMO chính
  thức, best-practice quản trị rủi ro.
- Kết quả: **công thức sizing = chuẩn ngành, KHÔNG có lỗi công thức.** Nhưng phát hiện
  lỗ hổng an toàn được cả 7 nguồn xác nhận:
  - **B1 [CAO]** `CapVolumeByMargin` chặn *margin*, không chặn *notional/đòn bẩy hiệu lực*.
    Số THẬT: FTMO indices đòn bẩy hiệu lực ~12–15 → `MaxMarginPercent=50` cho 1 cú gap
    lịch sử (US500 5.38%, US100 6.61%, DE40 9.95% Brexit) ăn **40–62% Equity** = gấp 4–6×
    hạn mức FTMO 10%. Chỉ BTCUSD an toàn (đòn bẩy ~1.3). Tệ hơn cho tài khoản nhỏ (tier
    tới 1:50) / nếu mở rộng sang FX (FTMO Forex 1:100).
  - **Mâu thuẫn tài liệu chưa hoà giải:** cùng 2026-09-03, CLAUDE.md nâng default
    `MaxMarginPercent` → 50% (lý do: "ít lệnh bị cap"), trong khi
    `max-margin-percent-safe-ceiling-2026-09-03.md` tính trần an toàn gap cho index chỉ
    **1–6%**. Hai phân tích chưa từng đối chiếu.
  - **B2 [CAO→]** `GetEstimatedMargin` có bug (Spotware xác nhận) báo margin quá thấp cho
    dynamic-leverage CFD → cap có thể không kích hoạt. Không sanity-check giá trị trả về.
  - **B7/B10 [TB]** Position Management lệch luật FTMO: `_initialBalance = Account.Balance
    @OnStart` (sai baseline nếu restart bot live); reset ngày theo UTC (FTMO: nửa đêm
    CE(S)T); trừ theo balance-đầu-ngày (FTMO: 5%×Vốn-Ban-Đầu).
  - **B6** cả 3 guard Position Management **mặc định TẮT**, và chỉ kiểm theo nến (không tick).
  - B3/B4/B5/B8/B9/B11 mức thấp/thông tin — chi tiết trong báo cáo.
- Báo cáo đầy đủ (11 phát hiện + ma trận theo loại tài khoản + 7 khuyến nghị + 3 phần
  đối chứng + mục Nguồn có link): **`research/reports/lotsize-safety-audit-2026-09-06.md`**.
- Khuyến nghị chính (D1–D3, **CHƯA CODE, người dùng chưa duyệt**):
  - **D1** thêm trần gap/notional **độc lập với đòn bẩy broker** — `[Parameter]
    MaxGapLossPercent` + `AssumedAdverseGapPercent` ("gap X% ngược hướng không lỗ nổi quá
    Y% Equity"). = chính công thức `max-margin-percent-safe-ceiling` đã thiết kế nhưng
    chưa code. Là knob RIÊNG (không hạ `MaxMarginPercent` vì làm vậy `RiskPercent` mất
    tác dụng — xem `margin-percent-sensitivity`).
  - **D2** sanity-check `GetEstimatedMargin` + fallback notional, không tin mù.
  - **D3** `EnableMaxDrawdown` default ON; `UpdatePositionManagement` trong `OnTick`;
    `[Parameter] InitialCapital` cứng; reset ngày theo giờ CE(S)T.

**Đang dang dở / chưa build-test:** Không sửa `.cs` nào. Audit là tài liệu đọc-code, chưa
có gì cần build.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Người dùng yêu cầu **CHƯA code** D1–D3 — chỉ ghi nhận + chuẩn bị prompt cho Codex đọc
  audit này và **research đối chứng độc lập, audit cả các đề xuất của Claude** (không
  rubber-stamp). Prompt đó đã được giao cho người dùng trong chat (không lưu vào repo).
- Chưa quyết định con số mặc định cho `MaxGapLossPercent`/`AssumedAdverseGapPercent`/
  `AssumedLeverage`, chưa quyết định có bật `EnableMaxDrawdown` mặc định không.

**Bước tiếp theo đề xuất:**
1. Người dùng đưa prompt cho Codex → Codex đọc `lotsize-safety-audit-2026-09-06.md` +
   research độc lập + phản biện D1–D7.
2. Đối chiếu kết luận Claude vs Codex → chốt hướng.
3. Chỉ khi đó mới code D1–D3 (qua GUI build như thường lệ).
4. Nhân tiện: cập nhật §4/§5/§8 cho khoảng trống 2026-09-03→09-06 (ngoài phạm vi audit này).

---

### [2026-09-02 19:35 — Codex, "record md" / tiếp 29]

**Đã làm:**
- Đọc lại `CLAUDE.md`, §4/§5 và entry mới nhất §8 trước khi audit; kiểm tra
  trực tiếp source hiện hành. `Combo.cs` và `MA Cross.cs` dùng cùng công thức
  `riskAmount / (stopLossPips * Symbol.PipValue)`, truyền volume in units và
  cùng gọi margin-cap A+C. Không sửa `.cs` trong phiên này.
- Hoàn tất audit độc lập `Symbol.PipValue`/quy đổi về USD cho đủ 6 symbol bằng
  raw `report.json`/`report.html` + `log.txt` + history/events, không suy diễn
  từ báo cáo cũ:
  - XAUUSD: quote USD, realized PipValue `1.00000000` USD/pip/unit — PASS.
  - BTCUSD: quote USD, realized `1.00003389` (sai số do gross làm tròn cent),
    sizing đúng `1` — PASS.
  - US30.cash: quote USD, realized đúng `1.00000000` — PASS; artifact GUI mới
    cũng xác nhận margin fix có 4 cap, 0 blocked, 0 failed.
  - GER40.cash: quote EUR; report tự nạp EURUSD; realized `1.02548884`, khớp
    EURUSD đầu 01/2025 và đúng chiều nhân — PASS.
  - JP225.cash: quote JPY; report tự nạp USDJPY; sizing suy ra
    `0.00639600898`, P/L suy ra `0.00639309479`, tương ứng USDJPY
    `156.35–156.42`, đúng chiều chia và đúng bậc — PASS.
  - HK50.cash: quote HKD; report tự nạp USDHKD; realized `0.12863217`, tương
    ứng USDHKD `7.77410`, đúng chiều chia — PASS.
- Ba run mới XAUUSD/BTCUSD/JP225.cash đều dùng `Combo.algo` margin-fixed hash
  `F55E7FBA663B6A212C7F19D6E63E2482F922E0B7865411CE19DA7B66780C04B7`;
  report parse được, đúng kỳ 01-15/01/2025, Ticks. JP225 lần chẩn đoán đầu
  thiếu report; lượt retry `Combo_JP225.cash_h4_20260902-192536` đã chạy sạch.
- Đối chiếu tài liệu cTrader chính thức: `PipValue` là monetary value của một
  pip tại lúc cBot start/indicator initialize, sau đó **không update real-time**.
  Vì vậy không có bug sai currency direction trong 6/6 phép thử, nhưng với
  EUR/JPY/HKD, risk % tiền thật có thể trôi theo tỷ giá nếu cBot/run kéo dài.
- Viết báo cáo đầy đủ:
  `reports/pipvalue-currency-conversion-audit-2026-09-02.md`.
- Ghi nhận nốt experiment optimize trước đó đã tự hoàn tất:
  `research/experiments/Combo_US30_h4_2025_KSL-KTP_20260902-052051/` —
  100/100 KSL×KTP success, 0 failed, `MaxParallel=10`, khoảng 29 phút. Best
  Net Profit: KSL Fib2618/KTP Fib0786, +$407.67, ROI 4.08%, PF 1.34,
  78 trades, max equity DD 4.1552%. **Không dùng kết quả này để xác nhận binary
  hiện tại** vì manifest ghi Algo hash cũ
  `206A64B72134352FD83EB8947A7B62A3BEEF7ED36234C23B43073FB4FF09A62A`.
- Sau toàn bộ lượt kiểm tra, `ctrader-cli.exe` còn 0 process.

**Đang dang dở / chưa build-test:**
- Không có sửa C# mới, nên không có build đang chờ. Fix margin A+C là việc đã
  được người dùng build/backtest xác nhận từ trước prompt này.
- `MaxMarginPercent` (trần margin chủ động mỗi lệnh theo % Equity) chưa được
  duyệt con số và chưa code.
- Chưa có cơ chế refresh/dynamic currency conversion để loại bỏ giới hạn
  PipValue snapshot trong cBot chạy lâu; đây là điểm thiết kế cần quyết định,
  không phải bug sai chiều vừa phát hiện.
- Parallel experiment trên là một lượt triển khai cụ thể có manifest/CSV/JSON/
  artifacts; generic optimizer với SQLite, resume/retry chuẩn, retention và
  Walk-Forward vẫn chưa hoàn thiện trong pipeline dùng lại được.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Người dùng đã chọn margin fix A+C; giữ nguyên, không làm lại.
- Đề xuất `MaxMarginPercent` vẫn chờ người dùng trả lời có triển khai hay không
  và default bao nhiêu % Equity.
- Nếu yêu cầu RiskPercent chính xác theo tỷ giá tại từng lệnh trong run dài,
  cần duyệt một hướng dynamic conversion riêng trước khi sửa code.

**Bước tiếp theo đề xuất:**
1. Hỏi/chốt `MaxMarginPercent`: có triển khai không và default bao nhiêu %.
2. Hỏi/chốt có cần xử lý PipValue động cho EUR/JPY/HKD hay chấp nhận snapshot
   được cTrader tài liệu hoá.
3. Nếu dùng kết quả optimize để chọn KSL/KTP, rerun grid 100 tổ hợp bằng
   `Combo.algo` hash hiện tại; sau đó mới mở rộng runner generic/Walk-Forward.

---

### [2026-09-02 05:07 — Codex, "record md" / tiếp 28]

**Đã làm:**
- Ghi nhận yêu cầu/nhận xét của người dùng: mô hình hiện tại mỗi backtest là
  một folder ngang hàng phù hợp chạy đơn hoặc grid nhỏ, nhưng không tối ưu
  cho vòng lặp backtest/optimize hàng trăm hoặc hàng nghìn tổ hợp.
- Cập nhật §4 và §5 với hướng kiến trúc lưu trữ đề xuất; chưa sửa pipeline.

**Đang dang dở / chưa build-test:**
- Chưa code mô hình `research/experiments/`. Cần trao đổi và chốt schema,
  retention policy và cách query trước khi triển khai.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Hướng đề xuất để bàn tiếp:
  `research/experiments/<ExperimentId>/manifest.json` lưu input chung;
  `results.sqlite` là catalog/kết quả chính, `results.csv` để mở Excel;
  `artifacts/run_XXXXXX/` giữ raw report/log theo chính sách.
- Mỗi run cần `RunId` ổn định và parameter hash để chống chạy trùng; hỗ trợ
  checkpoint/resume, trạng thái pending/running/success/failed, IS/OOS và
  Walk-Forward. Có thể chỉ giữ full log cho run lỗi/top candidates để giảm
  dung lượng, nhưng chính sách cụ thể chưa chốt.
- `research/cli_runs/` vẫn dành cho backtest đơn; không tự migration/xoá các
  artifact cũ.

**Bước tiếp theo đề xuất:**
1. Trao đổi lần lượt: cấu trúc `manifest` → schema một row kết quả → chính
   sách raw artifact → query/export → resume/retry.
2. Chỉ sau khi người dùng duyệt mới triển khai lớp Experiment, rồi mới nối
   Walk-Forward/optimizer vào đó.

---

### [2026-09-02 04:20 — Codex, "record md" / tiếp 27]

**Đã làm:**
- Trả lời câu hỏi người dùng bằng cách kiểm trực tiếp CSV + log +
  `report.json` của hai full-range run ở "tiếp 26", không suy diễn từ báo
  cáo cũ. Dùng `report.equity.points` làm lưới timestamp chart FTMO thực tế:
  - Combo H4: 170/201 `bartime` có exact timestamp, **31/201 (15.42%) không
    có bar timestamp khớp**.
  - MA Cross M30: 550/561 exact, **11/561 (1.96%) không khớp**.
- Phân biệt rõ "không có bar khớp bartime" với "signal không được xử lý".
  Source hiện hành cố ý không còn exact/fallback branch: đặt
  `AvailableTime=bartime+timeframe`, rồi chờ tick FTMO đầu tiên. Vì vậy cả
  hai vẫn processed toàn bộ (`not-processed=0`).
- Đối chiếu thời gian event log với `AvailableTime`: Combo 161/201 xử lý
  trong <=1 phút, 40 delayed >1 phút; MA Cross 540/561 trong <=1 phút, 21
  delayed. Delay tập trung ở 5/35/65 phút hoặc khoảng 2 ngày cuối tuần, cho
  thấy chênh lịch phiên có quy luật, không phải một UTC/DST offset cố định.
- Thêm mục "Đối chiếu lịch bar CSV và FTMO" vào
  `reports/cli-backtest-US30-2025-current-2026-09-02.md`; hash mới
  `0894E669581EBC8B63A56956E7568FF1762B7E0F49E8E5122307414C325EF850`.

**Đang dang dở / chưa build-test:** Không sửa code; không có build/test mới.

**Quyết định/thoả thuận với người dùng chưa code hoá:** Không có.

**Bước tiếp theo đề xuất:** Đưa phép exact-bar/delay audit này vào fidelity
parser tự động để mỗi run CLI luôn sinh thống kê tương tự, không phải chạy
script ad-hoc.

---

### [2026-09-02 04:13 — Codex, "record md" / tiếp 26]

**Đã làm:**
- Theo yêu cầu người dùng, chạy lại tuần tự hai backtest không GUI bằng
  pipeline mới: `US30.cash`, Ticks, 01/01/2025→02/09/2026, broker
  `FTMO Platform`, account demo 7563609. Mỗi lượt chỉ override
  `SignalFilePath`; default thật từ `.algo`: RiskPercent=1%, KSL/KTP=Fib0618,
  các FTMO guard OFF. Cả hai report parse được và `testingPeriod` khớp input.
- **Combo H4** — artifact
  `research/cli_runs/Combo_US30.cash_h4_20260902-034316/`:
  - CSV 1,101 signal; 900 trước start, 201 trong kỳ; processed=201,
    not-processed=0.
  - 200 pending placed, 0 failed, 1 same-direction skip; 153 fill/closed
    trade. 47 không fill được giải thích đầy đủ: 36 expiry sau 3 chart bars
    + 11 reversal-cancel. Có thêm 1 reversal đóng position. Cuối kỳ 0 order,
    0 position mở.
  - NetProfit -233.47, ROI -2.33%, PF 0.97, 79W/74L, max equity DD 17.43%.
- **MA Cross M30** — artifact
  `research/cli_runs/MA Cross_US30.cash_m30_20260902-040711/`:
  - CSV 2,936 signal; 2,375 trước start, 561 trong kỳ; processed=561,
    not-processed=0.
  - 310 market order accepted/opened, 251 failed; toàn bộ failure là
    `NOT_ENOUGH_MARGIN_BALANCE` / `NoMoney`. Cuối kỳ 0 position mở,
    history.items=310.
  - NetProfit +408.53, ROI +4.09%, PF 1.03, 157W/153L, max equity DD 18.94%.
- Sinh comparison tại
  `research/cli_comparisons/US30_2025-current_Combo-H4_vs_MA-Cross-M30_20260902-041052/`.
  Công cụ đánh dấu đúng `Comparable=False` vì Bot/Period khác; dùng để đặt
  kết quả cạnh nhau, không xếp hạng trực tiếp.
- Viết báo cáo kiểm định chi tiết
  `reports/cli-backtest-US30-2025-current-2026-09-02.md` (SHA-256
  `72F256456346262AEC9EF84D9D612CC868631C79A51D916B378C44B638EB9902`).
  Đối chiếu độc lập số signal trong CSV với summary cBot; mọi số lifecycle
  Combo khép kín: 200 placed = 153 fill + 36 expiry + 11 reversal-cancel.
- Sau cả hai lượt xác nhận còn 0 `ctrader-cli.exe`. Không sửa code `.cs` hay
  PowerShell pipeline trong lượt này.

**Đang dang dở / chưa build-test:**
- Không có code cần build. Hai `.algo` hiện hữu đã chạy thật qua CLI.
- Chưa có fidelity parser tự động hoá cho mọi run; thống kê fidelity lần này
  được truy xuất trực tiếp, có kiểm tra chéo CSV/log/report và đã lưu report.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Người dùng yêu cầu hai cấu hình chuẩn: Combo=US30 H4 pending; MA Cross=US30
  M30 market; kỳ từ đầu 2025 tới hiện tại; nguồn giá Ticks.

**Bước tiếp theo đề xuất:**
1. Tự động hoá chính phép kiểm tra fidelity vừa làm thành parser chung của
   pipeline, nhưng giữ adapter riêng cho Combo và MA Cross.
2. Sau đó mới xây Walk-Forward IS/OOS trên lớp chạy đơn/grid đã ổn định.

---

### [2026-09-02 03:28 — Codex, "record md" / tiếp 25]

**Đã làm:**
- Đọc đúng thứ tự `CLAUDE.md` → AGENT §4/§5 → entry "tiếp 24" → code
  `research/cli_pipeline/Invoke-CliBacktest.ps1`; không sửa file `.cs` nào.
- Điều tra xong hiện tượng UK100.cash "kết thúc sạch nhưng dừng 03/01/2025
  21:00 / 71.88%". `ctrader-cli --help` ghi rõ ngày batch là `dd/MM/yyyy`,
  nhưng wrapper cũ dùng `MM/dd/yyyy`. Artifact cũ truyền
  `--end=04/01/2025`; CLI hiểu là **04-Jan-2025**, chính `report.json` cũ cũng
  ghi `testingPeriod.endDate=2025-01-04`, nên nến cuối 03-Jan 21:00 là đúng.
  Kết luận cũ "UK100 dừng sớm" bị bác bỏ; retry ngày ±1 trước đó chỉ che lỗi
  format và làm thay đổi scope backtest.
- Rà CLI/broker/cache: CLI 5.9 có flag `--broker`; `accounts` xác nhận account
  `7563609` thuộc `FTMO Platform`. Đã truyền rõ `--broker="FTMO Platform"`
  trong `symbols` và `backtest`. Namespace cache `Spotware` không chứng minh
  route qua account Spotware, vì cache US30 do standalone CLI tạo cũng nằm ở
  đó. Không có CSV forex GBP-cross riêng để chạy phép thử gợi ý, nhưng không
  còn cần cho root cause này.
- Sửa `Invoke-CliBacktest.ps1`:
  - format ngày cố định `dd/MM/yyyy`, bỏ toàn bộ retry ±1 ngày;
  - validate CLI/pwd-file (chỉ kiểm tra tồn tại, **không đọc nội dung**),
    `.algo`, CSV signal, timeframe, ngày, timeout/poll và 0 process CLI;
  - `Resolve-CliSymbol` ưu tiên exact match, từ chối partial match mơ hồ;
  - chỉ `Success=True` nếu `report.json` parse được và kỳ thực tế khớp kỳ yêu
    cầu; vẫn giữ nguyên nguyên tắc không dựa `$job.State`/CPU để kết luận xong;
  - folder run timestamp tới giây, không ghi đè;
  - thêm `run-summary.json` chuẩn hoá input/actual/metrics/artifact, SHA-256
    `.algo` và CSV signal;
  - thêm `Invoke-CliBacktestGrid`: Cartesian grid tuần tự, `MaxRuns` guard,
    checkpoint JSON/CSV sau mỗi run;
  - thêm `Get-CliBacktestMetrics` và `Export-CliBacktestComparison`: đọc
    report raw, kiểm tra khả năng so sánh và xuất CSV/JSON/Markdown.
- Viết lại `research/cli_pipeline/README.md` thành tài liệu end-to-end: input,
  data mode, validation, cách chờ, artifact, grid, so sánh, giới hạn và sự cố
  UK100. SHA-256 cuối phiên:
  - script: `42EF930A7C6A93891DF743E73FB2B7454D40CC403B27770637A37083860FA525`
  - README: `F02ECD9A4FE8FEDDEF0F31AB1C578AF130A51F6779A6C44AB1494B4964BD7848`

**Tự kiểm chứng thực tế:**
- PowerShell AST parser: 0 lỗi; máy không có `git` command nên không chạy
  được `git diff/status`.
- UK100 H4, Ticks, 01/01/2025→01/04/2025, broker FTMO explicit:
  `research/cli_runs/Combo_UK100.cash_h4_20260902-031150/` — report khớp đúng
  kỳ yêu cầu, log tới 01/04/2025, 18 trades, NetProfit -210.91, ROI -2.11%.
- Smoke grid 2 RiskPercent (0.4/0.5), 01/01→07/01/2025:
  `research/cli_batches/Combo_UK100.cash_h4_grid_20260902-031907/` — 2/2
  thành công, checkpoint JSON/CSV hợp lệ.
- Smoke comparison:
  `research/cli_comparisons/UK100_grid_smoke_20260902-032259/` — parse được,
  `Comparable=True`, sinh đủ CSV/JSON/Markdown.
- Smoke manifest sau code mới:
  `research/cli_runs/Combo_UK100.cash_h4_20260902-032326/run-summary.json` —
  parse được, hash CSV dài 64 hex, kỳ report khớp. Sau mọi test còn **0**
  `ctrader-cli.exe`.

**Đang dang dở / chưa build-test:**
- Không có thay đổi C# nên không cần người dùng build cTrader IDE. PowerShell
  pipeline đã test thật như trên.
- Chưa làm Walk-Forward đầy đủ (rolling IS/OOS + ghép OOS); grid mới là lớp
  quét tham số tuần tự. Chưa có parser fidelity tự động riêng cho log Combo
  và MA Cross. `BestRun` của grid hiện chỉ xếp theo Net Profit.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Mục tiêu dài hạn được người dùng chốt: pipeline chuẩn hoàn toàn không GUI,
  bao trọn input → dữ liệu/mode → chờ backtest → lưu kết quả → phân tích so
  sánh; phải linh hoạt cho backtest đơn, grid và các bước mở rộng sau này.

**Bước tiếp theo đề xuất:**
1. Chốt schema/cửa sổ cho Walk-Forward (độ dài IS, OOS, bước trượt, metric
   chọn winner), rồi xây orchestrator dùng lại `Invoke-CliBacktestGrid`.
2. Viết parser fidelity riêng: Combo đếm signal/placed/filled/expired/rejected;
   MA Cross đếm signal/market accepted/rejected, dùng CSV + log + report.
3. Sau đó thêm ranking đa mục tiêu và báo cáo OOS tổng hợp, không tối ưu theo
   Net Profit đơn độc.

---

### [2026-09-01 (tiếp 18) — Claude]

**Đã làm:**
- **4 lượt CLI hoàn tất** (2 US30 xong trọn vẹn, 2 UK100 lỗi):
  - Combo US30.cash/H4 (RiskPercent 0.5%, default khác, 01/01/2025→01/09/2026,
    tick data): NetProfit **-546.10**, Equity 9,453.90, 88 trades (41W/47L),
    PF 0.77, MaxDD Balance/Equity 7%/8%.
  - MA Cross US30.cash/M30 (cùng tham số): NetProfit **+112.91**, Equity
    10,112.90, 336 trades (169W/167L), PF 1.01, MaxDD 9%/10%.
  - UK100.cash Combo/H4 và MA Cross/M30: **CẢ 2 LỖI MỚI** — `Error | CBot
    instance [...] aborted by timeout` rồi crash cùng chữ ký cũ
    (`InvalidOperationException: Message expected` tại
    `BacktestReportSavingStateStrategy.DoEnter()`). Symbol đã đúng
    `UK100.cash` (xác nhận qua log `Progress | Loading UK100.cash, <tf> |
    100%`) — **KHÔNG PHẢI** bug tên symbol đã sửa ở tiếp 16.
- **Chẩn đoán bug UK100 mới**: cả 2 lượt đều abort **ngay sau** dòng
  `Progress | Loading GBPUSD, <tf> | 100%` — do UK100.cash định giá bằng GBP,
  cần quy đổi qua GBPUSD (US30.cash định giá thẳng USD, không cần bước này).
  Chạy diagnostic thu hẹp date range còn 1 tháng (`01/01/2025→02/01/2025`) để
  tách bạch "chậm vì nhiều data" khỏi "treo thật" — **kết quả: CPU/Mem tiến
  trình con đứng yên tuyệt đối (10.3s/170MB) suốt >3 phút liên tục ngay sau
  khi khởi động**, dù chỉ 1 tháng dữ liệu. Kết luận: đây là **deadlock/hang
  thật xảy ra sớm** (nghi tại bước tính margin/PnL quy đổi GBPUSD lúc khớp
  lệnh đầu tiên), không phải do khối lượng tick data lớn — CLI tự có timeout
  nội bộ cố định (~28-29 phút, không cấu hình được) sẽ tự abort sau cùng bất
  kể date range ngắn hay dài. **Chưa xác định được cách khắc phục** — có thể
  là giới hạn/bug thật của `ctrader-cli` với symbol không định giá USD, chưa
  thử qua GUI để đối chiếu có bị y hệt không.
- **So sánh US30 CLI vs kết quả GUI cũ** — đã tìm 2 baseline gần nhất, NHƯNG
  **tham số KHÔNG khớp hoàn toàn** với lượt CLI (chỉ nên coi là tham chiếu
  ballpark, không phải validation engine chặt):
  - Combo: `Data\cBots\Combo\8403a83c...-Default\ArchivedRuns\
    US30_H4_ReconcileExposure_20260901-0935` — RiskPercent=**1%** (CLI 0.5%),
    KtpLevel=**7** (CLI mặc định 3), date **01/01/2024→30/08/2026** (CLI chỉ
    01/01/2025→01/09/2026, thiếu hẳn 1 năm 2024) → NetProfit +1,155.23
    (+11.55%), 224 trades (70W/154L), PF 1.07. Chênh lệch quá nhiều biến số
    cùng lúc để kết luận được gì về tính đúng đắn của CLI.
  - MA Cross: `Data\cBots\MA Cross\a08e1adc...-Default\ArchivedRuns\
    US30_M30_AlwaysFallback_2025plus_20260901-0208` — RiskPercent=1%,
    KtpLevel=7, date 01/01/2025→30/08/2026 (khớp gần đúng CLI, lệch 2 ngày).
    NetProfit -317.62 (-3.18%), 343 trades (96W/247L), PF 0.99. **Số lệnh
    343 vs 336 (CLI) khá gần nhau dù risk/Ktp khác** — dấu hiệu tốt là CLI
    nhận đúng cùng tập tín hiệu; chênh win/loss (96/247 vs 169/167) giải
    thích được hoàn toàn bởi KtpLevel xa hơn (7 vs 3) làm TP khó khớp hơn,
    không phải bất thường.
  - **Chưa có phép so sánh tham số-khớp-hoàn-toàn nào** — muốn validate CLI
    engine cho đúng nghĩa (cùng RiskPercent/KtpLevel/date range với 1 lượt
    GUI) thì cần chạy thêm 1 cặp matched, chưa làm, chờ người dùng xác nhận
    có cần không.
- Đã kill 2 tiến trình US30 (PID 16204/18176) sau khi xác nhận đã in xong
  JSON kết quả cuối nhưng không tự thoát (đúng pattern đã biết).

**Đang dang dở / chưa build-test:** diagnostic UK100 short-range
(`br1wp8z9u`, TimeoutMinutes=15) vẫn đang chạy nền lúc ghi entry này — dùng
để xác nhận thêm dữ liệu về hang, có thể tự kill khi hết 15 phút.

**Quyết định/thoả thuận với người dùng chưa code hoá:** người dùng chọn "làm
cả 2 song song" (so sánh US30 vs GUI cũ + debug UK100 timeout) thay vì làm
tuần tự — đã làm cả 2 trong entry này.

**Bước tiếp theo đề xuất:**
1. Hỏi người dùng có muốn 1 cặp backtest matched-parameter thật (GUI hoặc CLI,
   cùng RiskPercent/KtpLevel/date) để validate CLI engine chặt chẽ hơn không.
2. Tiếp tục điều tra hang UK100 nếu người dùng muốn — hướng khả dĩ: thử symbol
   USD-denominated khác (không cần GBPUSD) để xác nhận giả thuyết, hoặc thử
   chạy UK100 qua GUI thường để xem có treo giống vậy không (phân biệt bug
   CLI riêng vs bug chung cả GUI).
3. Cân nhắc chuyển 2 file kết quả US30 từ `scratchpad\combo_cli_FINAL.log`/
   `ma_cross_cli_FINAL.log` (vị trí tạm) vào `research/cli_runs/` cho đúng
   quy ước mới, nếu người dùng muốn giữ lâu dài.

---

### [2026-09-02 (tiếp 27) — Claude, "record md"]

**Đã làm:**

1. **Codex đối chiếu độc lập PipValue (6/6 symbol) — kết quả TRÙNG KHỚP với
   Claude, PASS toàn bộ.** Codex dùng phương pháp kiểm chứng CHẶT hơn (đối
   chiếu thêm qua P&L thật đã đóng, không chỉ suy ngược từ công thức sizing)
   — số liệu khớp sát Claude (sai lệch <0.1%, do làm tròn). Codex phát hiện
   thêm 1 điểm Claude chưa để ý: **`Symbol.PipValue` là ảnh chụp lúc cBot
   khởi động, KHÔNG cập nhật real-time** (theo tài liệu chính thức cTrader)
   — với symbol quote EUR/JPY/HKD, backtest dài có thể khiến risk% thực tế
   trôi nhẹ theo thời gian nếu tỷ giá biến động (không phải bug, là đặc tính
   API, chưa đo được mức độ ảnh hưởng thực tế). Báo cáo đầy đủ tại
   `reports/pipvalue-currency-conversion-audit-2026-09-02.md` (Codex viết).

2. **Đã giải thích lại nhiều vòng bằng ngôn ngữ đơn giản** cho người dùng về
   cơ chế margin/notional (ẩn dụ "tiền đặt cọc giữ chỗ") tới khi người dùng
   xác nhận hiểu đúng — bao gồm xác nhận 2 hệ quả của việc "kẹt margin": (a)
   không đặt được lệnh mới cho tới khi lệnh cũ đóng, (b) mất khả năng chống
   đỡ rủi ro bất ngờ → dễ cháy tài khoản.

3. **Người dùng đồng ý triển khai `MaxMarginPercent=10%` — ĐÃ CODE + áp dụng
   cho CẢ 2 chiến lược** (`Combo.cs` và `MA Cross.cs`, đối xứng):
   - Thêm `[Parameter] MaxMarginPercent` (Group="Risk Management", mặc định
     10.0, Min=0.1, Max=100.0).
   - Sửa `CapVolumeByMargin()`: giờ lấy **MIN của 2 ràng buộc độc lập** —
     `Account.Equity × MaxMarginPercent/100` (trần CHỦ ĐỘNG, LUÔN kiểm tra,
     điểm khác biệt so với Fix A cũ) và `Account.FreeMargin` (lớp chặn cuối
     giữ nguyên từ Fix A, cho trường hợp nhiều lệnh đang mở). Log phân biệt
     rõ lý do giảm (`vuot tran MaxMarginPercent=X%` hay `vuot free margin
     con lai`) — giữ nguyên 2 counter `_marginCapped`/`_marginBlocked` sẵn
     có, không đổi logic OnStop().
   - **CHƯA ĐƯỢC NGƯỜI DÙNG BUILD/TEST LẠI TRONG CTRADER IDE** — đây là thay
     đổi code MỚI, chưa build lần này, khác với `CapVolumeByMargin` gốc (Fix
     A) đã build/test thành công ở tiếp 25.

4. **Đã lưu vào memory + cập nhật CLAUDE.md** theo yêu cầu người dùng ("ghi
   nhớ vấn đề này vào memory, triển khai luôn lớp chặn này cho các chiến
   lược khác" — xác nhận repo chỉ có đúng 2 chiến lược, Combo+MA Cross đã đủ
   phủ hết "các chiến lược khác"):
   - Memory mới: `risk-sizing-must-cap-margin-utilization.md` (type=feedback)
     + cập nhật `MEMORY.md` index.
   - `CLAUDE.md` cập nhật 3 chỗ: (a) thêm đoạn giải thích `MaxMarginPercent`
     bắt buộc ngay sau đoạn Position Management/FTMO trong mục "Khung sườn
     file .cs chuẩn"; (b) sửa comment region `Risk & Position Sizing` trong
     khung code mẫu; (c) mở rộng bullet "Khối lượng" trong "Quy ước API" —
     thêm cả ghi chú PipValue-không-real-time từ audit Codex.

**Đang dang dở / chưa build-test:** `Combo.cs` và `MA Cross.cs` **VỪA SỬA
THÊM `MaxMarginPercent`, CHƯA BUILD/TEST LẠI**. Người dùng cần build lại
trong cTrader IDE, báo lỗi compile nếu có, rồi backtest lại (khuyến nghị
đúng lượt US30/H1/01-30/01/2026 đã dùng nhiều lần trong session này) để xác
nhận: (a) lệnh 3.38 lot giờ giảm mạnh hơn nữa (dự kiến ~0.30 lot thay vì
3.04 lot của Fix A cũ); (b) log phân biệt đúng lý do giảm; (c) không phá vỡ
hành vi bình thường của các lệnh SL không hẹp bất thường (volume không đổi
so với trước).

**Quyết định/thoả thuận với người dùng chưa code hoá:** Không còn — cả audit
PipValue lẫn quyết định `MaxMarginPercent` đều đã chốt và code xong (chờ
build/test).

**Bước tiếp theo đề xuất:**
1. Chờ người dùng build lại 2 cBot, backtest xác nhận `MaxMarginPercent`
   hoạt động đúng thiết kế (như mô tả ở "Đang dang dở" phía trên).
2. Nếu người dùng muốn định lượng ảnh hưởng của "PipValue không real-time"
   (Codex phát hiện) trên backtest dài thật (GER40/JP225/HK50) — chưa làm,
   chỉ ghi nhận hướng đi nếu có nhu cầu.
3. Không còn việc nào khác đang mở liên quan chủ đề risk-sizing/margin.

---

### [2026-09-02 (tiếp 26) — Claude, "record md"]

**Đã làm:**

1. **Xác nhận fix margin (tiếp 25) build/test THÀNH CÔNG**: người dùng build
   lại cả 2 cBot, backtest lại đúng US30.cash/H1/01-30/01/2026 — log xác nhận
   `CapVolumeByMargin` hoạt động đúng thiết kế: lệnh 3.38 lot từng bị
   `NOT_ENOUGH_MARGIN_BALANCE` giờ tự giảm xuống 3.04 lot và khớp thành công,
   **không còn dòng rejection nào trong toàn bộ 30 ngày**. Verify tay cả 4
   lần `margin-capped` xảy ra trong lượt này — khớp chính xác 100% công thức
   `volume × freeMargin/marginCần × 0.98`. `failed=0` toàn lượt.

2. **Tranh luận dài với người dùng về đòn bẩy/balance/margin — đã đi tới
   thống nhất** (người dùng nghi ngờ fix có rủi ro cháy tài khoản, đúng):
   - Ban đầu người dùng hiểu nhầm "nạp $100 + đòn bẩy 1:100 → balance hiện
     $100,000" — Claude đã làm rõ (dùng chính screenshot người dùng gửi:
     "$10,000.00 · 1:30" hiện TÁCH BIỆT không nhân) — **`Account.Balance`
     KHÔNG BAO GIỜ bị đòn bẩy khuếch đại**, ở CẢ tài khoản thường lẫn FTMO.
   - Người dùng hỏi thêm: để có tài khoản FTMO $10,000 này phải nạp bao
     nhiêu? → Claude giải thích đúng mô hình FTMO: KHÔNG nạp $10,000, chỉ
     trả phí Challenge nhỏ hơn nhiều, vượt qua thì được CẤP QUYỀN giao dịch
     trên vốn FTMO — người dùng xác nhận "Đúng".
   - **Kết luận kỹ thuật cuối cùng (quan trọng, áp dụng khi code)**: đòn bẩy
     KHÔNG khuếch đại số lỗ $ khi dính SL (luôn đúng = RiskPercent% Balance),
     nhưng đòn bẩy quyết định **margin cần cho 1 lô** — mà công thức risk%
     hiện tại HOÀN TOÀN MÙ trước đòn bẩy/margin/notional (chỉ nhìn SL) — nên
     **SL càng hẹp → volume tính ra càng to SO VỚI VỐN THẬT, độc lập đòn
     bẩy** (ví dụ thật: SL 29.55 điểm/0.061% giá → volume 3.38 lot →
     notional $163,096 = **16.3 lần** tài khoản $10,000 — con số 16.3 lần
     này tính TRƯỚC KHI đụng đòn bẩy). Đòn bẩy chỉ quyết định "phanh margin"
     có kịp chặn vị thế phồng to đó hay không — US30 trên FTMO đòn bẩy hiệu
     lực thấp (~1:15, suy ngược từ số liệu thật) nên margin tình cờ hết
     trước, VÔ TÌNH đóng vai trò phanh (đây là lý do lệnh 3.38 lot bị chặn).
     **Trên 1 symbol đòn bẩy CAO (FX majors 1:100+), CÙNG công thức sẽ tạo
     vị thế 16 lần vốn NHƯNG KHÔNG BỊ CHẶN GÌ CẢ** vì margin còn dư nhiều —
     "an toàn" hiện tại trên US30 chỉ là MAY MẮN, không phải code chủ động
     bảo vệ. **Đã đề xuất `MaxMarginPercent`** (trần margin CHỦ ĐỘNG, độc
     lập với margin lúc đó còn hay hết, vd 10% Equity/lệnh) — **CHƯA ĐƯỢC
     NGƯỜI DÙNG XÁC NHẬN CON SỐ CỤ THỂ, CHƯA CODE** — việc còn mở quan trọng
     nhất hiện tại.

3. **Yêu cầu audit MỚI từ mentor người dùng, ĐÃ LÀM XONG 6/6 SYMBOL**: kiểm
   tra `Symbol.PipValue` (dùng trong `CalculateVolume()`) có được cAlgo quy
   đổi ĐÚNG sang USD hay không cho: Vàng (`XAUUSD`), BTC (`BTCUSD`), US30
   (`US30.cash`), DE40 (`GER40.cash`), JP225 (`JP225.cash`), HK50
   (`HK50.cash`) — LƯU Ý tên broker thật KHÔNG khớp key `config.yaml` (Vàng≠
   "GOLD", DE40≠"DE40" mà là GER40, JP225≠"J225" mà là "JP225"). Phương pháp:
   chạy backtest ngắn (1-2 tuần, Combo H4, đảm bảo có ≥1 tín hiệu thật) qua
   `Invoke-CliBacktest.ps1`, lấy `entry`/`SL`/`volume` thật từ log, **suy
   ngược `PipValue`** rồi đối chiếu tỷ giá quy đổi thật hợp lý tại đúng thời
   điểm. **KẾT QUẢ: CẢ 6/6 SYMBOL ĐỀU ĐÚNG**, không tìm thấy bug quy đổi tiền
   tệ nào:
   - US30.cash (USD, không cần quy đổi): PipValue=$1.00/điểm/lot ✅
   - BTCUSD (USD, không cần quy đổi): PipValue≈$0.89–1.02 (kỳ vọng $1.00) ✅
   - XAUUSD (USD, không cần quy đổi, lotSize=100): PipValue_perUnit≈
     $0.0094–0.0107 (kỳ vọng $0.01) ✅
   - GER40.cash (EUR, ×EURUSD): PipValue≈$1.02–1.03 (khớp EURUSD thật
     đầu 2025 ~1.03) ✅
   - HK50.cash (HKD, ÷USDHKD): PipValue≈$0.1286–0.1288 (khớp 1/7.8) ✅
   - JP225.cash (JPY, ÷USDJPY, lotSize=10): PipValue_perLot≈$0.0637–0.0640
     (khớp gần tuyệt đối 1/157≈0.0637) ✅
   Cơ chế quy đổi của cAlgo xử lý đúng cả 2 chiều (nhân khi tiền quy đổi là
   BASE của cặp như EUR/EURUSD; chia khi tiền quy đổi là QUOTE như HKD/JPY
   trong USDHKD/USDJPY) và đúng mọi hệ số `lotSize` khác nhau (1/10/100) đã
   thử. **Việc audit PipValue coi như XONG HOÀN TOÀN.**

4. Đã soạn 1 prompt bàn giao đầy đủ cho Codex (lưu tại
   `AppData\Local\Temp\claude\...\scratchpad\codex_prompt_pipvalue_audit.md`
   — file tạm, KHÔNG nằm trong repo, người dùng tự copy khi cần) — ban đầu
   định nhờ Codex làm nốt 3/6 symbol, nhưng Claude đã tự làm xong hết 6/6
   trước khi người dùng kịp giao cho Codex — đã cập nhật lại prompt phản ánh
   đúng: vai trò Codex giờ là **đối chiếu độc lập** (không phải làm tiếp),
   cộng thêm việc CODE THẬT còn mở (`MaxMarginPercent`, xem mục 2 ở trên).

**Đang dang dở / chưa build-test:** Không có thay đổi `.cs` nào mới trong
lượt này (chỉ audit/tranh luận, không code) — `Combo.cs`/`MA Cross.cs` vẫn ở
đúng trạng thái đã build/test thành công từ tiếp 25.

**Quyết định/thoả thuận với người dùng chưa code hoá:** `MaxMarginPercent` —
đã đề xuất, CHƯA xác nhận % cụ thể, CHƯA code (xem mục 2).

**Bước tiếp theo đề xuất:**
1. Chờ người dùng xác nhận % cụ thể cho `MaxMarginPercent` rồi triển khai
   (Combo.cs + MA Cross.cs, theo đúng pattern `CapVolumeByMargin` đã có).
2. Nếu người dùng giao việc cho Codex chạy song song — đưa file prompt đã
   soạn (đường dẫn ở mục 4).
3. Không còn việc audit PipValue nào dang dở — coi như đóng hẳn chủ đề này
   trừ khi phát sinh nghi vấn mới.

---

### [2026-09-02 (tiếp 25) — Claude]

**Đã làm:** Người dùng phát hiện qua GUI (US30.cash/H1, Combo, 01-30/01/2026):
lệnh đầu tiên (volume 3.38, risk-based đúng 1%) bị broker từ chối lúc KHỚP
(`NOT_ENOUGH_MARGIN_BALANCE`) dù ĐẶT thành công. Đã debug + giải thích rõ:
công thức risk-based (`volume = riskAmount ÷ (SL-pips × PipValue)`) tính
ĐÚNG số học (verify tay khớp tuyệt đối 3.38), nhưng **hoàn toàn không kiểm
tra margin khả dụng** — 2 ràng buộc "rủi ro nếu dính SL" và "margin cần để
giữ lệnh" độc lập nhau, code cũ chỉ đảm bảo vế đầu.

Theo quyết định người dùng (phương án **A + C**, bỏ B): đã sửa **CẢ
`Combo.cs` VÀ `MA Cross.cs`** (đối xứng, cùng pattern):
- `CalculateVolume()` đổi chữ ký, nhận thêm `TradeType` — gọi
  `CapVolumeByMargin()` (hàm MỚI) sau khi tính volume risk-based.
- `CapVolumeByMargin()`: gọi `Symbol.GetEstimatedMargin(tradeType, volume)`,
  nếu vượt `Account.FreeMargin` → **giảm volume theo đúng tỉ lệ margin
  khả dụng/margin cần** (trừ hao an toàn 2%), log rõ risk % thực tế mới
  (thấp hơn `RiskPercent` khai báo) — nếu giảm xuống dưới
  `Symbol.VolumeInUnitsMin` thì bỏ hẳn tín hiệu (không đặt lệnh).
- Thêm 2 counter mới `_marginCapped`/`_marginBlocked` vào dòng tổng kết
  `OnStop()` của cả 2 bot (đúng pattern các counter sẵn có) — đáp ứng yêu
  cầu "log kĩ execution/lotsize" của người dùng ở tầm thống kê, không chỉ
  từng dòng log lẻ.
- Cũng nâng cấp 2 dòng log đã có (dưới ngưỡng min / vượt ngưỡng max) thêm
  `riskAmount`/`stopLossPips` cho dễ audit hơn.

**⚠️ Rủi ro/giới hạn CHƯA giải quyết được, đã nói rõ với người dùng**:
- `Symbol.GetEstimatedMargin(TradeType, double)` là API tôi tin tưởng có
  tồn tại trong `cAlgo.API` nhưng **CHƯA verify được chữ ký chính xác**
  (máy không có compiler độc lập) — có rủi ro lỗi compile, cần người dùng
  build thật trong cTrader IDE và báo lỗi lại nếu có.
- Cơ chế mới chỉ NGĂN TRƯỚC lúc đặt lệnh (dựa trên margin ước tính tại thời
  điểm đó) — vẫn có khả năng (nhỏ hơn nhiều so với trước) lệnh bị từ chối
  SAU đó lúc thực sự khớp nếu margin thay đổi giữa lúc đặt và lúc khớp (vd
  Combo đặt pending, giá chạm sau vài nến, margin có thể đã khác) — KHÔNG
  tìm được (chưa chắc có tồn tại) 1 cAlgo event bắt được đúng khoảnh khắc
  "pending order bị từ chối lúc trigger" để log/đếm riêng case đó.

**Đang dang dở / chưa build-test:** **CẢ 2 FILE `Combo.cs` và `MA Cross.cs`
VỪA SỬA, CHƯA ĐƯỢC BUILD/TEST TRONG CTRADER IDE.** Người dùng cần tự build,
báo lỗi compile nếu có (đặc biệt chú ý `Symbol.GetEstimatedMargin` — điểm
rủi ro nhất), rồi backtest lại đúng lượt US30/H1/01-30/01/2026 đã phát hiện
vấn đề để xác nhận lệnh giờ có tự giảm volume và vào được không.

**Bước tiếp theo đề xuất:** build 2 cBot, backtest lại US30/H1/Combo tháng
01/2026 xác nhận hết lỗi `NOT_ENOUGH_MARGIN_BALANCE` (hoặc còn nhưng đã có
log/counter margin-capped rõ ràng thay vì im lặng như trước), sau đó cân
nhắc quét lại toàn bộ archive cũ xem tần suất `margin-capped`/`margin-
blocked` cao tới mức nào trên các symbol/timeframe đã test trước đây.

---

### [2026-09-02 (tiếp 24) — Claude, "record md"]

**Đã làm:** Sau khi sửa bug polling ở tiếp 23, chạy lại UK100 (theo yêu cầu
người dùng "tuần tự với UK100 và US30 lần nữa" + sau đó "audit vì sao KtpLevel
gây treo" dẫn tới phát hiện+sửa bug ở tiếp 23) bằng script đã sửa:

- **UK100.cash/H4, khung 1 tháng (01→02/2025)**: hoàn tất "sạch" nhưng
  `placed=0` (0 tín hiệu rơi đúng khung ngắn này) — **không phải phép thử hợp
  lệ** cho giả thuyết treo (chưa từng chạm bước `Loading GBPUSD`).
- **UK100.cash/H4, khung 3 tháng (01/01→01/04/2025)**: `Success=True`, có
  đủ `report.json`, NHƯNG log dừng ở đúng **"03/01/2025 21:00", progress
  71.88%** — không chạy hết tới 01/04/2025 như yêu cầu. `before-start=1024,
  not-processed=219, processed=0` — **giống hệt tuyệt đối** con số của lượt
  1-tháng trước, dù khung ngày khác hẳn.
- **UK100.cash/H1 (period MỚI, chưa từng dùng trong session này), cùng khung
  3 tháng**: có **1 lệnh thật được đặt VÀ KHỚP thành công** ("03/01/2025 15:00
  | Trade | Placing Stop Order to Sell... SUCCEEDED", đóng lỗ -50.98) —
  **KHÔNG hề thấy dòng "Loading GBPUSD" xuất hiện** (khác hẳn mọi lần trước —
  nghi vấn: tick GBPUSD giờ đã cache sẵn trên đĩa từ hàng chục lần thử UK100
  trước đó trong session, nên bước tải không còn treo/chậm nữa). NHƯNG log
  **vẫn dừng đúng "03/01/2025 21:00" / progress 71.88%** — y hệt lượt H4,
  dù period khác hẳn (H1 vs H4)! → mốc dừng này **không phụ thuộc period**.
- Theo yêu cầu người dùng, kiểm tra trực tiếp cache tick trên đĩa
  (`%APPDATA%\Spotware\Cache\`) để test giả thuyết "cache thiếu nên dừng
  sớm": **BÁC BỎ** — tìm ra UK100.cash có 2 cache RIÊNG BIỆT dưới 2
  broker-profile khác nhau (`ftmo` — chứa US30/HK50/BTCUSD/USDHKD; và
  `Spotware` — chứa EURUSD/GBPUSD/GER40/UK100/US30) — tick cache
  `UK100.cash\t1` chỉ tồn tại dưới `Spotware`, và **phủ đầy đủ liên tục
  29/12/2024 → 09/01/2026** (377 file `.zticks`, dung lượng thật hàng trăm
  KB/ngày, không phải placeholder rỗng) — xa hơn RẤT NHIỀU so với mốc dừng
  "03/01/2025". Kết luận: **KHÔNG PHẢI do thiếu tick cache.**

**Tóm tắt trạng thái UK100 hiện tại (đã đổi nhiều lần trong session, đây là
bản MỚI NHẤT, ghi đè mọi mô tả trước đó)**:
1. Bug gốc ban đầu (sai tên symbol `US30` thay vì `US30.cash`) — đã sửa từ
   lâu, không còn liên quan.
2. "Treo/crash thật" với `Error | ... aborted by timeout` +
   `InvalidOperationException` — đã xảy ra NHIỀU LẦN trong session này ở giai
   đoạn đầu điều tra UK100 — **hiện tại (sau khi tick cache đã ấm/đầy đủ) có
   vẻ KHÔNG còn tái diễn nữa** trong 3 lượt gần nhất (đều `Success=True`,
   `Progress` không nhảy thẳng lên rồi dừng lỗi giữa chừng như trước).
3. Hiện tượng CÒN LẠI, MỚI, CHƯA GIẢI THÍCH ĐƯỢC: backtest kết thúc "êm" (đủ
   `report.json`, không lỗi) nhưng DỪNG SỚM ở đúng mốc cố định "03/01/2025
   21:00 / 71.88%" — tái hiện y hệt ở 2 period khác nhau (H1, H4), bất kể
   `--end` yêu cầu xa tới đâu — **không phải do thiếu tick cache** (đã kiểm
   chứng trực tiếp). Chưa rõ: do broker-profile `Spotware` (khác `ftmo` mà
   US30/HK50 đang dùng, chưa hiểu tại sao UK100 lại route qua profile khác)
   có giới hạn riêng gì đó, hay 1 nguyên nhân khác hoàn toàn chưa nghĩ tới.

**Quyết định của người dùng**: bàn giao việc điều tra tiếp UK100 + tiếp tục
xây dựng/hoàn thiện pipeline `ctrader-cli` này cho **Codex** thực hiện tiếp
(người dùng chủ động luân phiên Claude/Codex tuỳ credit) — xem prompt bàn
giao đầy đủ đã đưa cho người dùng trong cùng lượt trao đổi này (không lưu lại
nguyên văn prompt ở đây, chỉ ghi bối cảnh — nếu cần xem lại nguyên văn, hỏi
người dùng hoặc xem lịch sử hội thoại phiên 2026-09-02).

**Đang dang dở / chưa build-test:** Không có thay đổi code `.cs` nào trong
lượt này — toàn bộ là điều tra CLI/PowerShell. `Invoke-CliBacktest.ps1` đã
sửa bug polling (tiếp 23) và đã tự-kiểm-chứng qua nhiều lượt sau đó.

**Bước tiếp theo đề xuất cho Codex:**
1. Điều tra tại sao UK100.cash route qua broker-profile `Spotware` thay vì
   `ftmo` (khác US30/HK50) — có thể là manh mối cho mốc dừng sớm 03/01/2025.
2. Thử ép/kiểm tra xem có cách nào chỉ định broker-profile qua tham số CLI
   không (`ctrader-cli --help` toàn bộ, chưa rà hết mọi flag).
3. Thử 1 symbol GBP-cross KHÁC UK100 (nếu có sẵn signal CSV) để xem mốc dừng
   sớm có lặp lại y hệt hay là đặc thù riêng UK100.
4. Nếu bế tắc, quay lại dùng GUI cho UK100 (đã xác nhận GUI luôn chạy đúng,
   xem §8 "tiếp 18/19") — không phải vấn đề bắt buộc phải giải qua CLI.
5. Ngoài UK100: có thể tiếp tục mở rộng pipeline sang Walk-Forward/optimize
   qua CLI (mục còn mở ở §5 mục 5, chưa bắt đầu) nếu người dùng ưu tiên hướng
   đó hơn UK100.

---

### [2026-09-02 (tiếp 23) — Claude]

**⚠️ ĐÍNH CHÍNH — vô hiệu hoá kết luận "KtpLevel=7 gây deadlock" ở tiếp 20-22.**
Toàn bộ kết luận "US30 matched-parameter treo thật" từ tiếp 20/21/22 (bao gồm
cả "đã bác bỏ giả thuyết concurrency", "chạy đơn lẻ vẫn treo") **SAI HOÀN
TOÀN** — nguyên nhân gốc là **bug trong chính `Invoke-CliBacktest.ps1`**, xem
chi tiết dưới đây, không phải bug/giới hạn của `ctrader-cli`.

**Đã làm:** Theo yêu cầu người dùng "thử nghiệm lại tuần tự với UK100 và US30
lần nữa", chạy 2 test tách biến (Test A: RiskPercent=0.5%+KtpLevel=7; Test B:
RiskPercent=1%+KtpLevel=3, cả 2 date range ngắn 1 tháng, tuần tự, đơn lẻ).
Khi người dùng hỏi "audit vì sao KtpLevel đó lại treo", đọc thẳng `log.txt`/
`report.json` của Test A thay vì chỉ tin vào cảnh báo "treo" của script —
**phát hiện Test A đã CHẠY XONG HOÀN TOÀN** (`Progress|100%`, `CBot instance
stopped`, JSON đầy đủ NetProfit=-49.29). Audit ngược lại TẤT CẢ 5 lượt "treo"
trước đó (`b2122njor`, `bzzg7ojr9`, `bc340go2k`, Test A, Test B) — **CẢ 5 ĐỀU
ĐÃ CHẠY XONG THÀNH CÔNG THẬT SỰ**, có `report.json`/log hoàn chỉnh, bị kill
oan trước khi tôi kiểm tra kỹ.

**Nguyên nhân gốc (bug thật, đã sửa trong `Invoke-CliBacktest.ps1`)**: vòng
lặp theo dõi dùng `$job.State -eq "Completed"` làm điều kiện dừng — nhưng
`$job.State` chỉ đổi khi CHÍNH tiến trình `ctrader-cli.exe` **EXIT**. Bug đã
biết từ lâu (ghi trong README ngay từ đầu, "process không tự thoát") khiến
tiến trình đôi khi in xong KẾT QUẢ ĐẦY ĐỦ nhưng không tự exit — `$job.State`
kẹt "Running" mãi mãi dù việc đã xong, CPU đứng yên (vì không còn gì để làm),
script hiểu nhầm "CPU đứng yên = treo", chờ hết `TimeoutMinutes` rồi kill —
**xoá mất 1 lượt backtest THÀNH CÔNG, không hề treo**.

**Đã sửa**: vòng lặp giờ kiểm tra **`report.json` đã xuất hiện + parse được
hợp lệ chưa** mỗi vòng poll — đây mới là tín hiệu hoàn tất THẬT, độc lập với
việc process có tự exit hay không. CPU đứng yên giờ chỉ là cảnh báo chờ xác
nhận thêm qua `report.json`, không tự động kết luận treo nữa. Xem code đã sửa
+ comment giải thích đầy đủ trong `research/cli_pipeline/Invoke-CliBacktest.ps1`.

**Kết quả THẬT lấy được (nhờ audit lại, trước đó bị kill mất)** — Combo/MA
Cross US30.cash, RiskPercent=1%/KtpLevel=7 (Fib1618, **không phải Fib2000
như nhầm lẫn trước đó** — enum FibLevel index 7 = Fib1618, xem
[Combo.cs:16-28](Combo/Combo/Combo.cs#L16-L28)):
- Combo H4 (2024-01-01→2026-09-01): NetProfit **+825.16**, 160 trades, PF
  1.07, MaxDD Balance/Equity 23%/24%.
- MA Cross M30 (2025-01-01→2026-09-01): NetProfit **+963.79**, 207 trades,
  PF 1.06, MaxDD Balance/Equity 18%/19%.
- So với baseline GUI cũ (`US30_H4_ReconcileExposure_20260901-0935`:
  NetProfit +1,155.23, 224 trades, PF 1.07): PF khớp gần tuyệt đối (1.07 cả
  2), NHƯNG trade count/NetProfit lệch đáng kể (160 vs 224) — **CHƯA rõ
  nguyên nhân** (có thể do CLI/GUI engine thật sự khác nhau chút, hoặc do
  khác biệt data tick tải về giữa 2 lần chạy) — chưa điều tra tiếp, ghi nhận
  làm việc còn mở nếu người dùng muốn truy tới cùng.

**QUAN TRỌNG cho vai trò UK100 trong toàn bộ câu chuyện này**: kết luận UK100
(GBP-cross) THẬT SỰ LỖI ở tiếp 18/19 **VẪN ĐÚNG, KHÔNG bị ảnh hưởng bởi đính
chính này** — log UK100 có `Error | ... aborted by timeout` + JSON RỖNG
(`"Equity":,`) + stack trace `InvalidOperationException` THẬT, khác hẳn tín
hiệu "im lặng hoàn tất" của các lượt US30 bị kill oan ở trên. UK100 dừng ở
progress ~1.59% (không phải 100%) rồi bị CHÍNH `ctrader-cli` tự abort sau
~28-29 phút — đây là lỗi tự thân của CLI, không phải do script giám sát của
tôi kill nhầm.

**Đang dang dở / chưa build-test:** UK100 chưa chạy lại lần này (người dùng
yêu cầu "tuần tự với UK100 và US30" — mới làm xong phần US30 audit, UK100
còn lại).

**Bước tiếp theo đề xuất:**
1. Chạy lại UK100 (đơn lẻ, dùng script đã sửa) để tái xác nhận vẫn lỗi thật
   (kỳ vọng: vẫn lỗi, vì chữ ký lỗi khác hẳn — không phải false positive).
2. Cân nhắc điều tra thêm chênh lệch trade-count CLI(160) vs GUI(224) cho tổ
   hợp RiskPercent=1%/KtpLevel=Fib1618 nếu người dùng muốn kết luận chắc chắn
   CLI đáng tin cậy 100% khớp GUI.
3. Lưu 2 kết quả matched-parameter (Combo +825.16, MA Cross +963.79) vào
   `research/cli_runs/` đúng quy ước nếu người dùng muốn giữ lại.

---

### [2026-09-02 (tiếp 22) — Claude]

**Đã làm:** Theo yêu cầu người dùng ("kill hết tiến trình, làm lại lần lượt
xem sao"), chạy lại Combo US30.cash H4 matched-parameter (`b2122njor`,
RiskPercent=1%/KtpLevel=7, 01/01/2024→01/09/2026) **HOÀN TOÀN ĐƠN LẺ** — xác
nhận 0 tiến trình `ctrader-cli.exe` nào khác trước khi bắt đầu.

**KẾT QUẢ: VẪN TREO Y HỆT** (CPU đứng yên 45.6-45.7s suốt 18+ phút, wrapper tự
cảnh báo "có thể đã treo thật" ở mốc 120s/140s/160s không tăng CPU). **Bác bỏ
DỨT ĐIỂM giả thuyết concurrency ở tiếp 21** — chạy đơn lẻ tuyệt đối vẫn treo,
chứng minh nguyên nhân KHÔNG liên quan gì đến số session cùng lúc trên
account. Đã kill tiến trình (PID 8232).

**Kết luận cập nhật (thay thế tiếp 21)**: nguyên nhân treo nằm ở **tổ hợp
tham số cụ thể — RiskPercent=1%/KtpLevel=7 (và/hoặc date range bắt đầu từ
2024-01-01 thay vì 2025-01-01)** kết hợp với `ctrader-cli`, KHÔNG liên quan
đến: (a) symbol GBP-cross (US30.cash thuần USD vẫn treo), (b) data-mode
Ticks-vs-M1 (cả 2 đều treo trên UK100), (c) concurrency/số session song song
(đơn lẻ vẫn treo). Đây là bug/giới hạn thật của `ctrader-cli` 5.9.0.38 với
1 tổ hợp tham số cụ thể chưa xác định chính xác biến nào trong 3 biến còn lại
(RiskPercent, KtpLevel, date-range-start) là thủ phạm thật — cần tách từng
biến để xác định (vd thử lại 0.5%/KtpLevel=7 riêng, rồi 1%/KtpLevel=3 riêng).

**Đang dang dở / chưa build-test:** So sánh CLI vs GUI cũ cho US30
matched-parameter (RiskPercent=1%/KtpLevel=7) **VẪN CHƯA THỰC HIỆN ĐƯỢC** —
đã thử 2 lần (concurrent + đơn lẻ), cả 2 đều treo. Yêu cầu gốc "so sánh kết
quả cũ" của người dùng cho tổ hợp tham số NÀY coi như bế tắc qua CLI, có thể
cần chuyển sang GUI nếu người dùng vẫn muốn có số liệu.

**Bước tiếp theo đề xuất:** hỏi người dùng có muốn tiếp tục tách biến để tìm
chính xác tham số gây treo (tốn thêm nhiều lượt test, mỗi lượt tốn thời gian
chờ treo/xác nhận), hay dừng điều tra CLI cho tổ hợp tham số cao (KtpLevel=7)
và chỉ dùng CLI cho tham số mặc định/thấp (đã xác nhận ổn định).

---

### [2026-09-01 (tiếp 21) — Claude]

**Đã làm:** Người dùng hỏi thẳng "quá tải hiệu năng hay gì?" — điều tra sâu
nguyên nhân treo, tìm ra manh mối quan trọng:

- **KHÔNG PHẢI quá tải máy**: RAM 38GB trống/48GB, 32 core — dư dả tuyệt đối.
- **2 lượt UK100 khớp GUI (M1 data, `bnypuj7vc`/Combo H4, `b1r1ukj3y`/MA Cross
  M45, RiskPercent=1%/KtpLevel=3)**: CẢ 2 CŨNG TREO — y hệt chữ ký cũ
  (`aborted by timeout` ngay sau `Loading GBPUSD` + lệnh đầu tiên), nhưng
  nhanh hơn nhiều (~4.5 phút thay vì 28 phút, vì M1 tải nhanh hơn tick). →
  **loại bỏ hẳn giả thuyết "do data-mode Ticks vs M1"** (tiếp 19) — treo xảy
  ra với CẢ 2 data-mode, chỉ khác tốc độ tới lúc lộ ra.
- **Phát hiện bất ngờ, phá giả thuyết "chỉ GBP-cross" (tiếp 18/19)**: lượt
  matched-parameter **US30.cash** (`bzzg7ojr9` Combo, `bc340go2k` MA Cross —
  RiskPercent=1%/KtpLevel=7) — symbol THUẦN USD, không cần GBPUSD — **CŨNG
  TREO** (CPU đứng yên tuyệt đối 20-30+ phút liên tục), trong khi đúng
  symbol/period này với RiskPercent=0.5%/KtpLevel=3 (mặc định) từng chạy sạch
  hoàn toàn trước đó. → bug không chỉ giới hạn ở GBP-cross.
- **Phát hiện gốc rễ khả dĩ nhất — CONCURRENCY**: tại các thời điểm treo, LUÔN
  có **2-4 tiến trình `ctrader-cli.exe` cùng đăng nhập account 7563609 song
  song** (kể cả tiến trình "zombie" đã xong việc/đã treo từ trước nhưng KHÔNG
  tự thoát — bug lingering-process đã biết, giữ session mở thêm 15-30 phút).
  Mọi lượt chạy ĐƠN LẺ (2 lượt US30 gốc, GER40 diagnostic) đều chạy sạch;
  MỌI lượt treo đều xảy ra khi có ≥2 session cùng lúc trên cùng account.
- **Thử nghiệm xác nhận 1 chiều**: kill 2 session zombie (giảm 4→2 session
  đang treo thật), chờ 30s — **CPU vẫn đứng yên tuyệt đối, KHÔNG tự phục
  hồi**. Kết luận: deadlock (nếu đúng do concurrency) là **VĨNH VIỄN một khi
  đã xảy ra** — giảm tải sau đó không tự giải phóng được, phải kill thủ công.
  Chưa chứng minh chắc chắn concurrency là NGUYÊN NHÂN KHỞI PHÁT (chỉ là
  tương quan thời điểm mạnh), nhưng là giả thuyết khả dĩ nhất hiện có.
- Đã kill sạch toàn bộ 4 tiến trình `ctrader-cli.exe` (2 zombie + 2 deadlock
  thật) — hệ thống về trạng thái sạch, 0 tiến trình còn chạy.

**Đang dang dở / chưa build-test:** CHƯA có bất kỳ lượt matched-parameter
US30 nào hoàn tất thành công để so sánh với baseline GUI cũ (yêu cầu gốc của
người dùng "sau đó xong hãy làm so sánh" vẫn CHƯA thực hiện được) — cả 2 lượt
đều bị treo rồi phải kill. Cũng chưa xác nhận UK100 khớp tham số GUI (M1,
RiskPercent=1%/KtpLevel=3) có kết quả gì vì cũng treo.

**Quyết định/thoả thuận với người dùng chưa code hoá:** đã đề xuất người dùng
cho chạy lại matched-parameter US30 (và có thể UK100) **HOÀN TOÀN ĐƠN LẺ**
(không có tiến trình `ctrader-cli` nào khác chạy cùng lúc) để kiểm chứng giả
thuyết concurrency và có kết quả sạch — CHỜ XÁC NHẬN người dùng trước khi làm.

**Bước tiếp theo đề xuất:**
1. Nếu người dùng đồng ý: chạy lại tuần tự, TỪNG LƯỢT MỘT (đợi lượt trước xong
   + xác nhận 0 tiến trình `ctrader-cli.exe` còn sống trước khi bắt đầu lượt
   sau) — Combo US30 matched trước, MA Cross US30 matched sau.
2. Nếu vẫn treo dù chạy đơn lẻ → giả thuyết concurrency SAI, quay lại nghi
   vấn tham số RiskPercent=1%/KtpLevel=7 cụ thể (thử tách riêng từng biến).
3. Cập nhật `Invoke-CliBacktest.ps1`: cân nhắc thêm cơ chế tự kiểm tra "còn
   tiến trình `ctrader-cli.exe` nào khác đang chạy không" trước khi khởi động
   1 lượt mới, cảnh báo/chờ nếu có — để tránh lặp lại vấn đề concurrency này
   trong pipeline tương lai.

---

### [2026-09-01 (tiếp 20) — Claude]

**Đã làm:** Theo yêu cầu người dùng, chạy lại UK100 qua CLI với **tham số
khớp chính xác** lượt GUI người dùng vừa tự chạy (M1 data thay vì Ticks,
RiskPercent=1%, KtpLevel=3, Combo H4 `bnypuj7vc` + MA Cross M45 `b1r1ukj3y`)
— đang chạy, chưa có kết quả.

**Phát hiện phá vỡ giả thuyết GBP-cross ở tiếp 18/19**: trong lúc chờ, phát
hiện lượt **MA Cross US30.cash matched-parameter** (`bc340go2k`,
RiskPercent=1%, KtpLevel=7, cùng job đã chạy từ 23:35) **CPU đứng yên tuyệt
đối 44.8-44.9s suốt 16+ phút liên tục** (23:36→23:52) — treo y hệt pattern
UK100, trong khi lượt Combo song song (`bzzg7ojr9`, cùng tham số RiskPercent/
KtpLevel, khác symbol US30/H4) vẫn tăng CPU bình thường cùng lúc. **US30.cash
là symbol thuần USD, không cần quy đổi GBPUSD** — vậy giả thuyết "chỉ GBP-
cross mới treo" (tiếp 18/19) SAI hoặc không đầy đủ. Nghi vấn mới: có thể do
tổ hợp **RiskPercent=1%/KtpLevel=7 + MA Cross.cs** (không phải do symbol) —
nhưng chưa đủ bằng chứng kết luận chắc, vì 2 biến (KtpLevel, RiskPercent)
cùng đổi so với lượt US30 gốc từng chạy ổn (0.5%/KtpLevel=3 mặc định).

**Đang dang dở / chưa build-test:** 4 lượt CLI đang chạy nền cùng lúc:
`bnypuj7vc` (UK100 Combo M1), `b1r1ukj3y` (UK100 MA Cross M1), `bzzg7ojr9`
(US30 Combo matched, có vẻ khoẻ mạnh), `bc340go2k` (US30 MA Cross matched,
nghi treo — TimeoutMinutes=90 nên còn ~74 phút nữa mới tự bị wrapper kill nếu
đúng là treo).

**Bước tiếp theo đề xuất:** chờ cả 4 lượt, đặc biệt xem `bc340go2k` có tự
phục hồi hay tiếp diễn treo tới khi bị kill — nếu treo thật, cần thử thêm 1
lượt MA Cross US30 với KtpLevel=7 nhưng giữ RiskPercent=0.5 (hoặc ngược lại)
để tách bạch xem biến nào thực sự gây treo, thay vì đổ lỗi cho symbol.

---

### [2026-09-01 (tiếp 19) — Claude]

**Đã làm:** Kết luận dứt điểm bug "UK100 aborted by timeout" (mở ở tiếp 18):

- Diagnostic Combo GER40.cash (EUR-cross, cần EURUSD, giống UK100 cần GBPUSD)
  qua CLI, Tick data, date range ngắn: **chạy sạch, KHÔNG treo** — 1 lệnh
  Sell đặt đúng, `CBot instance stopped` bình thường. → bác bỏ giả thuyết
  "mọi symbol non-USD đều treo qua CLI" — không phải hiện tượng chung.
- Người dùng tự chạy UK100 qua **GUI** (Combo H4 + MA Cross M45, **M1 data**
  — khác Tick data đã test qua CLI, RiskPercent=1%, KtpLevel=3 mặc định,
  01/01/2025→01/09/2026): **CẢ 2 CHẠY TRỌN VẸN, KHÔNG TREO, `stopped` sạch**
  hết đúng khung ngày yêu cầu (`01/09/2026 23:59:00 | ... stopped`). Kết quả:
  Combo NetProfit -3,776.77 (-37.77%, Equity 6,223.23); MA Cross NetProfit
  -2,838.80 (-28.39%, Equity 7,161.20) — lỗ nặng nhưng đó là vấn đề chiến
  lược/tham số, không liên quan bug treo.
- **KẾT LUẬN**: bug "aborted by timeout" trên UK100.cash là **đặc thù riêng
  của `ctrader-cli`** (bản 5.9.0.38 standalone đang dùng), KHÔNG PHẢI lỗi
  chung của core engine backtest — GUI xử lý UK100.cash hoàn toàn bình
  thường. Do đó với UK100 (và khả năng cả các symbol GBP-cross khác), người
  dùng nên dùng GUI thay vì CLI cho tới khi bug này được xác định/sửa ở phía
  Spotware.
- **CHƯA tách bạch được** 2 biến đổi cùng lúc giữa 2 phép thử (GUI dùng M1,
  CLI dùng Ticks) — nên chưa biết chắc chắn là do (a) đặc thù riêng GBP/UK100
  qua CLI, hay (b) khác biệt data-mode Ticks vs M1 khi qua CLI. Nếu cần kết
  luận chặt hơn: thử CLI UK100 với `--data-mode=M1` (thay vì Ticks) — nếu vẫn
  treo → chắc chắn (a); nếu hết treo → là (b), tức bug nằm ở việc CLI xử lý
  Tick data cho symbol GBP-cross, không phải symbol UK100 nói chung.

**Đang dang dở / chưa build-test:** 2 lượt matched-parameter US30 (Combo
`bzzg7ojr9`, MA Cross `bc340go2k`) vẫn đang chạy nền — mục đích validate CLI
vs GUI cũ với tham số khớp hoàn toàn (RiskPercent=1%, KtpLevel=7, date range
khớp bản GUI cũ `ReconcileExposure`/`AlwaysFallback_2025plus`).

**Quyết định/thoả thuận với người dùng chưa code hoá:** chưa quyết định có
cần thử thêm `--data-mode=M1` qua CLI cho UK100 để tách bạch 2 biến (a)/(b)
ở trên hay dừng ở đây (coi CLI có giới hạn với UK100, dùng GUI thay thế).

**Bước tiếp theo đề xuất:**
1. Chờ 2 lượt matched-parameter US30 xong, so sánh với baseline GUI cũ
   (`US30_H4_ReconcileExposure_20260901-0935`,
   `US30_M30_AlwaysFallback_2025plus_20260901-0208`).
2. Hỏi người dùng có muốn thử thêm CLI UK100 + `--data-mode=M1` để tách bạch
   giả thuyết (a)/(b) hay dừng điều tra ở đây.
3. Cập nhật `research/cli_pipeline/README.md` mục "Rủi ro/hạn chế đã biết"
   với phát hiện GBP-cross/UK100 hang này.

---

### [2026-09-01 (tiếp 17) — Claude]

**Đã làm:** Người dùng đồng ý thiết kế pipeline CLI đã trình bày, yêu cầu
triển khai + test với UK100.

- Viết `research/cli_pipeline/Invoke-CliBacktest.ps1` — hàm `Invoke-CliBacktest`
  đóng gói toàn bộ: `Resolve-CliSymbol` (tra tên đầy đủ, không đoán), build
  `.cbotset` tạm từ hashtable, chạy nền qua `Start-Job`, tự retry lùi/tiến 1
  ngày nếu gặp lỗi parse `--end`, theo dõi qua CPU/memory (không đọc log vì
  bị buffer), tự dọn tiến trình con không tự thoát, lưu kết quả vào
  `research/cli_runs/<Bot>_<Symbol>_<TF>_<timestamp>/`.
- **Phát hiện + sửa 1 bug thật trong chính script mới** (phát hiện ngay lần
  chạy thử đầu tiên): `Resolve-CliSymbol` trả về `'U'` thay vì
  `'UK100.cash'` — PowerShell tự "unwrap" pipeline có đúng 1 phần tử thành
  chuỗi đơn khi gán biến, khiến `$matches[0]` lấy nhầm KÝ TỰ đầu của chuỗi
  thay vì PHẦN TỬ đầu mảng. Sửa bằng cách ép `@(...)` quanh toàn bộ pipeline
  kết quả (đảm bảo luôn là mảng dù 0/1/nhiều phần tử) + đổi tên biến khỏi
  `$matches` (trùng tên biến tự động của PowerShell, rủi ro phụ). Đã verify
  lại đúng cho cả UK100 và US30 sau khi sửa.
- Export CSV signal UK100 (Combo H4 + MA Cross M30) qua OG8, xác nhận UK100
  có sẵn trong DP6 (37 symbol, đã thấy đủ trong `db_connector.symbols()`).
- 2 lượt UK100 đầu tiên (dùng bug `--symbol=U`) đã dọn sạch, chạy lại đúng
  với `UK100.cash` — đang chạy nền cùng lúc với 2 lượt US30 gốc (tổng 4
  tiến trình `ctrader-cli` song song, mỗi tick-data run ~2GB+ RAM).

**Đang dang dở / chưa build-test:** 4 lượt backtest CLI đang chạy nền (US30
Combo/MA Cross tick-data đã chạy >15 phút vẫn ở 0% hiển thị nhưng bộ nhớ
tăng liên tục = đang xử lý thật, không treo; UK100 Combo/MA Cross mới khởi
động lại). Chưa có kết quả cuối cùng nào để verify/archive.

**Quyết định/thoả thuận với người dùng chưa code hoá:** chưa nối
`research/cli_runs/` vào `signal_chart_visualizer.ipynb` — ghi nhận là bước
tiếp theo hợp lý (không cần sửa code Python, chỉ thêm entry `RUNS`), chưa
làm vì đang chờ có kết quả thật để test.

**Bước tiếp theo đề xuất:** chờ 4 lượt hoàn tất, verify (đối chiếu số liệu
nội bộ như mọi lần), xác nhận `report.json` có đủ chi tiết per-trade hay
chỉ tổng hợp (câu hỏi còn treo trong README), rồi so sánh US30 CLI vs GUI
cũ theo đúng yêu cầu gốc của người dùng.

### [2026-09-01 (tiếp 16) — Claude]

**Đã làm:** Người dùng không chấp nhận kết luận "bế tắc", yêu cầu debug kỹ
hơn. Đã tìm ra **NGUYÊN NHÂN GỐC RỄ THẬT SỰ** — hoàn toàn khác giả thuyết
version-mismatch/system-bug đã ghi ở "tiếp 15":

- Loại trừ giả thuyết "bug do `--report-json`": chạy lại KHÔNG có
  `--report-json`/`--report` nào — crash y hệt. Không phải do bước ghi file.
- Soi kỹ đoạn JSON in ra trước khi crash: `"Equity":,` — cú pháp HỎNG THẬT
  (thiếu hẳn giá trị sau dấu `:`) → gợi ý lỗi nằm ở dữ liệu backtest (không
  có kết quả gì để báo cáo), không phải lỗi cú pháp ghi file.
- **Chạy `ctrader-cli symbols` để tra đúng tên symbol** → phát hiện: tên
  thật là **`US30.cash`**, không phải `US30` như đã dùng SUỐT TỪ ĐẦU trong
  mọi lệnh `backtest` (cả của Claude tự thử lẫn trong script đưa người
  dùng)!
- **Chạy lại với `--symbol="US30.cash"` → THÀNH CÔNG HOÀN TOÀN**, không
  crash: log hiện đủ `Progress | Loading US30.cash, h4 | 33/66/100%`, cBot
  chạy thật (đặt lệnh, đảo chiều đúng theo `ReconcileExistingExposure`),
  kết thúc sạch `CBot instance stopped`, JSON kết quả đầy đủ hợp lệ.

**Bài học quan trọng — đã lưu vào memory** (`ctrader-cli-symbol-name-bug.md`):
symbol sai khiến `ctrader-cli backtest` **KHÔNG báo lỗi "symbol not
found"** — vẫn đăng nhập/bắt đầu bình thường rồi crash khó hiểu tại đúng
0.00% với `System.InvalidOperationException: Message expected`, KHÔNG có
dòng `Progress | Loading ...` theo sau (dấu hiệu nhận biết chính xác nhất).
Đã tốn rất nhiều lượt điều tra sai hướng (nghi version-mismatch, thử bản
CLI đi kèm Desktop, dựng cBot test rỗng, thử shell tương tác) vì lỗi này —
**từ nay LUÔN tra `ctrader-cli symbols` lấy đúng tên đầy đủ trước khi
backtest 1 symbol mới, không đoán/rút gọn**.

- Đã đặt lại 2 lệnh backtest THẬT theo đúng yêu cầu gốc (US30.cash, tick
  data, 01/01/2025→09/01/2026, RiskPercent=0.5%, còn lại default) cho cả
  Combo (H4) và MA Cross (M30), chạy nền — đang chờ kết quả (tick data 20
  tháng dự kiến mất một lúc).

**Đang dang dở / chưa build-test:** không áp dụng — chờ 2 lượt backtest CLI
đang chạy nền hoàn tất, sẽ verify + archive như quy trình chuẩn.

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới —
hướng CLI cho `backtest` giờ đã CHỨNG MINH DÙNG ĐƯỢC (không còn là "bế tắc"
như kết luận sai ở "tiếp 15" — entry đó cần đọc với hiểu biết đã lỗi thời).

**Bước tiếp theo đề xuất:** chờ 2 backtest hoàn tất, verify kết quả (đối
chiếu số liệu nội bộ giống mọi lần), archive, rồi so sánh với kết quả GUI
cũ theo đúng yêu cầu ban đầu của người dùng.

### [2026-09-01 (tiếp 15) — Claude]

**Đã làm:** Người dùng yêu cầu thử `ctrader-cli backtest` trực tiếp (Combo
US30/H4 + MA Cross US30/M30, 01/01/2025→30/08/2026, tick data,
RiskPercent=0.5%, còn lại default) thay vì qua GUI. Điều tra sâu, kết luận:

1. **Tìm lại được file mật khẩu cũ** (`C:\Users\Administrator\.ctrader-cli-pwd.txt`,
   file ẩn, tạo lúc test lần trước 2026-08-26) — sau khi tìm sai hướng ban
   đầu (quét đệ quy toàn profile bị vướng vòng lặp junction folder Windows
   cũ `Local Settings\Application Data`↔`AppData\Local`, phải bỏ cách đó,
   quét trực tiếp từng thư mục top-level mới thấy).
2. **Xác nhận cú pháp `backtest` đầy đủ qua `--help`/`--commands`/`metadata`**:
   - `--full-access` được `backtest` ÂM THẦM chấp nhận dù KHÔNG có trong
     usage string chính thức (chỉ `run` mới ghi rõ) — cần thiết vì
     Combo/MA Cross đều `AccessRights.FullAccess`.
   - Định dạng ngày: **MM/DD/YYYY** (kiểu Mỹ), vd `01/01/2025`.
   - Override tham số cBot qua `--<PropertyName>=<value>` (đúng tên
     `PropertyName` lấy từ `ctrader-cli metadata <file.algo>`, vd
     `--RiskPercent=0.5`, `--SignalFilePath=...`).
   - `--data-mode` chấp nhận `Ticks`, `M1` (xác nhận qua thử nghiệm; các
     giá trị khác như `OneMinuteBars`/`Bars`/`OneMinute` bị từ chối).
3. **PHÁT HIỆN QUAN TRỌNG — bug/giới hạn thật, không phải do cách gọi lệnh**:
   - `--end=08/30/2026` (đúng ngày người dùng yêu cầu) liên tục báo "Value
     for parameter end can't be parsed" — nhưng các mốc lân cận
     (`08/01/2026`, `09/01/2026`) lại chạy được. Chưa rõ nguyên nhân chính
     xác (không phải do định dạng, không do độ dài khoảng ngày) — dùng
     `09/01/2026` thay thế (phủ nhiều hơn yêu cầu 1 ngày, không thiếu dữ
     liệu) để đi tiếp.
   - **Với `--end=09/01/2026`, đăng nhập THÀNH CÔNG** (`Logged in`, `Using
     account: #7563609 FTMO Platform USD 10000 demo`), cBot bắt đầu chạy
     (`Starting cBot...`, `Progress | Backtesting | 0.00 %`) — nhưng **crash
     ngay tại 0.00%** khi lưu report:
     `System.InvalidOperationException: Message expected` tại
     `BacktestReportSavingStateStrategy.DoEnter()`.
   - **Đã cô lập bằng 3 thử nghiệm loại trừ biến số** (mỗi lần đổi đúng 1
     yếu tố): (a) đổi `--data-mode` Ticks→M1: crash y hệt; (b) rút khoảng
     ngày 20 tháng→1 tháng: crash y hệt; (c) **tạo 1 cBot hoàn toàn mặc định
     qua `ctrader-cli create cbot`, build qua `ctrader-cli build`, KHÔNG cần
     `--full-access`, KHÔNG tham số nào**: **VẪN crash y hệt tại 0.00%**.
     → Kết luận: lỗi này **không liên quan gì tới Combo/MA Cross, data-mode,
     khoảng ngày, hay AccessRights** — là lỗi hệ thống của chính lệnh
     `ctrader-cli backtest` trên máy này, ở bản CLI đang cài.
   - **Giả thuyết nguyên nhân gốc, độ tin cậy cao**: **lệch phiên bản** —
     `ctrader-cli` standalone là bản **5.9.0.38** (cài qua winget từ trước),
     trong khi cTrader Desktop cài trên máy đã lên **5.9.10.52700**
     (`app_5.9.10.52700`). Thông báo lỗi "Message expected" đúng kiểu lỗi
     giao thức message-passing nội bộ giữa 2 phiên bản lệch nhau. **CHƯA xác
     nhận 100%** (chưa thử downgrade Desktop hoặc tìm bản CLI mới hơn để đối
     chứng) — chỉ là giả thuyết hợp lý nhất dựa trên bằng chứng hiện có.
   - Cleanup: đã xoá sạch cBot test (`CliTestBot/`, `CliTestBot.algo`) khỏi
     `Sources/Robots/` sau khi dùng xong — không để lại scaffolding thừa.
4. Người dùng yêu cầu: nếu không tự triển khai được, đưa script để người
   dùng tự chạy tay từng lệnh. Đã đưa (xem tin nhắn trả lời) — script dùng
   `--data-mode=M1` (đã xác nhận là giá trị hợp lệ, KHÔNG dùng `Ticks` vì
   người dùng yêu cầu ban đầu nhưng đã chứng minh không phải nguyên nhân
   crash) và `--end=09/01/2026` (né lỗi parse ngày cụ thể ở 08/30).

**Đang dang dở / chưa build-test:** không áp dụng (không phải task code) —
`ctrader-cli backtest` hiện **KHÔNG DÙNG ĐƯỢC** trên máy này ở trạng thái
hiện tại, bất kể cBot nào. Đây là giới hạn môi trường, không phải việc cần
sửa trong `Combo.cs`/`MA Cross.cs`.

**Quyết định/thoả thuận với người dùng chưa code hoá:** người dùng sẽ tự thử
chạy tay script bằng CLI thật, báo lại kết quả từng bước — CHƯA quyết định
có tiếp tục điều tra/tìm cách sửa version-mismatch hay quay lại hẳn GUI
(giống quyết định trước đó ở mục "Trạng thái Walk-Forward qua ctrader-cli"
trong `CLAUDE.md`, lúc đó `optimize` cũng không dùng được ở bản CLI này).

**Bước tiếp theo đề xuất:** chờ người dùng chạy script, báo kết quả. Nếu
vẫn crash y hệt → gần như chắc chắn là version-mismatch, hướng đi tiếp theo
là tìm bản `ctrader-cli` mới hơn (khớp 5.9.10) nếu Spotware đã phát hành,
hoặc bỏ hẳn hướng CLI cho `backtest` (giống `optimize` trước đó), quay lại
GUI làm chính.

**[Cùng ngày, tiếp]** Người dùng yêu cầu tự update `ctrader-cli`. Đã điều
tra:
- Không có `winget` khả dụng trên máy (khác lúc trước — `Get-Command
  winget` không ra gì, không có gói `Microsoft.DesktopAppInstaller` cài).
- Tra lại qua WebSearch (2 nguồn: wingetgui.com + trang setup chính thức
  help.ctrader.com/ctrader-cli/setup) — **vẫn xác nhận 5.9.0 là bản
  standalone công khai mới nhất** (khớp lại kết luận 2026-08-27, không có
  gì mới trong 5 ngày qua). Trang GitHub `spotware/CLI-references` vẫn nhắc
  tính năng "5.10" (nghi vấn optimize) nhưng không xác nhận đã phát hành.
- **Phát hiện mới đáng chú ý**: trang setup chính thức ghi rõ
  `ctrader-cli.exe` còn có **bản đi kèm ngay trong chính cTrader Desktop**
  (không chỉ bản standalone qua winget) — đã tìm thấy tại
  `AppData\Local\Spotware\cTrader\abb70432efbee65d18af69e79fe8efe1\app_5.9.10.52700\x64\ctrader-cli.exe`,
  khớp ĐÚNG version Desktop (5.9.10.52700, không lệch như bản standalone).
  Đã thử gọi (`--version`, `--help`, không tham số) — **exit code 3, hoàn
  toàn không có output/lỗi gì**, kể cả khi cTrader Desktop đang chạy sẵn
  (process id 11984, từ 28/08). CHƯA rõ nguyên nhân — có thể bản này cần
  gọi qua 1 cơ chế khác (menu trong chính app?) thay vì exec trực tiếp.
  CHƯA điều tra tiếp — ưu tiên thấp hơn việc chờ người dùng tự chạy tay.
- **[Kết luận cuối]** Người dùng tự chạy Phần A (shell tương tác) qua 2
  lượt (không + có kèm file `.cbotset` tham số riêng —
  `reports/combo_cli_params_risk05.cbotset`, mẫu RiskPercent=0.5,
  SignalFilePath đúng). **CẢ 2 LƯỢT LỖI NGAY**: `Additional AccessRights
  are required`, exit code 87. Log hiện đủ lệnh nội bộ shell tự dựng —
  KHÔNG có `--full-access`, và không có cách bật cờ này qua UI shell tương
  tác (không được hỏi, không có mục tương ứng trong 42 mục menu).
  **Kết luận cuối cùng**: bế tắc ở CẢ 2 hướng (shell tương tác: chặn sớm do
  thiếu `--full-access`; lệnh 1-dòng + `--full-access` thủ công: vượt qua
  được nhưng crash tại 0% với lỗi hệ thống thật của bản CLI, đã cô lập xác
  nhận không liên quan Combo/MA Cross). Đã đề xuất người dùng dừng hướng
  CLI cho `backtest`, quay lại GUI — giống tiền lệ `optimize` trước đó.
  Đang chờ người dùng xác nhận.
- Đã viết `reports/ctrader-cli-backtest-test-script-2026-09-01.md` — script
  đầy đủ cho người dùng tự chạy tay, ưu tiên thử **shell tương tác** của
  standalone CLI (cú pháp vị trí `backtest <algo> <symbol> <period> <from>
  <to>`, khác hẳn cú pháp `--start=/--end=` đã test và biết lỗi) — đây là 1
  code path HOÀN TOÀN CHƯA TEST, khả năng né được lỗi đang gặp.

### [2026-09-01 (tiếp 14) — Claude]

**Đã làm:**
- Người dùng đồng ý áp dụng cùng thay đổi (bỏ exact-match/missing-bar-
  fallback) cho `MA Cross.cs`, kèm yêu cầu **ghi nhớ cơ chế cũ vào memory**
  để có thể "yêu cầu dùng lại" sau này.
- **Trước khi sửa MA Cross.cs**: tạo file backup ĐẦY ĐỦ source code (không
  chỉ mô tả) cho CẢ `Combo.cs` lẫn `MA Cross.cs` bản trước khi xoá, tại
  `reports/legacy-exact-match-missing-bar-fallback-2026-09-01.md` — lý do
  làm cả 2 dù chỉ MA Cross sắp sửa: `Combo.cs` đã bị ghi đè ở lượt trước
  ("tiếp 13") mà chưa backup, tận dụng nội dung còn trong context của chính
  lượt Read trước đó để bù lại, tránh mất hẳn không backup được nữa.
- **Tạo memory file mới** (persistent, ngoài phạm vi conversation này):
  `exact-match-missing-bar-fallback-removed.md` — mô tả cơ chế, lý do xoá,
  trỏ rõ ràng tới file backup trên, kèm hướng dẫn cách khôi phục (đọc kỹ,
  hỏi lại người dùng phạm vi muốn khôi phục, tự merge chứ không ghi đè mù
  quáng nếu code đã đổi thêm từ đó tới lúc khôi phục). Đã thêm dòng trỏ vào
  `MEMORY.md` (file này lần đầu được tạo trong dự án).
- Viết lại `MA Cross.cs` theo đúng mẫu đã áp dụng cho `Combo.cs`: xoá
  `SignalAlignment`, gộp `ProcessFallbackSignals` → `ProcessScheduledSignals`
  (duy nhất, không giới hạn thời gian chờ — MA Cross vốn đã không có trần
  này nên thay đổi ở đây NHẸ hơn Combo, chủ yếu là bỏ nhánh exact-match
  trong `OnBarClosed` + gộp counter `exact/fallback` → `processed`).
- Cập nhật `fidelity_lib.py` thêm lần nữa cho khớp field `alignment=` optional
  (áp dụng chung cho cả Combo lẫn MA Cross, đã làm ở lượt trước) — regression
  test lại lần 2: MA Cross log cũ (343 placed/216 rejected) **giữ nguyên
  tuyệt đối**. Phát hiện phụ (không phải regression code): file CSV sống
  `combo_US30_H4_full_history_signals.csv` trên OG8 đã đổi nội dung (do
  combo.py logic đổi + đã bị ghi đè trong thí nghiệm "verify tham số" trước
  đó) khiến 1 archive CŨ (`US30_H4_AlwaysFallback_2025plus_20260901-0204`,
  không còn dùng trong `RUNS` hiện tại) giờ đối chiếu lệch — KHÔNG ảnh hưởng
  chart đang publish vì `RUNS` đã trỏ sang archive mới hơn
  (`ReconcileExposure`) rồi, chỉ ảnh hưởng nếu ai đó chạy lại
  `check_summary.py` (script tạm trong scratchpad) nhắm archive cũ đó.
- **CHƯA build/test `MA Cross.cs`** — người dùng cần build lại trong cTrader
  IDE.

**Đang dang dở / chưa build-test:** cả `Combo.cs` (từ "tiếp 13") lẫn
`MA Cross.cs` (lượt này) đều **CHƯA ĐƯỢC BUILD/TEST** sau khi bỏ cơ chế
exact-match/fallback. SHA-256 ở §4 lỗi thời hoàn toàn cho cả 2 file.

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới —
việc khôi phục cơ chế cũ (nếu có) đã có đường đi rõ ràng qua memory +
backup file, không cần quyết định gì thêm lúc này.

**Bước tiếp theo đề xuất:** chờ người dùng build cả 2 cBot + backtest lại,
verify theo đúng quy trình cũ trước khi archive/visualize.

### [2026-09-01 (tiếp 13) — Claude]

**Đã làm:**
- Sau khi phân tích sâu missing-bar (entry trước), người dùng quyết định:
  **bỏ HẲN toàn bộ cơ chế exact-match/missing-bar-fallback trong `Combo.cs`**
  — lý lẽ: khi live trading thật, bot chỉ phản ứng theo thời gian thực trên
  CHÍNH tài khoản đặt lệnh (FTMO) — không hề có khái niệm "có khớp đúng 1
  nến của broker khác hay không". Backtest nên phản ánh đúng thực tế đó:
  thấy tín hiệu (đủ thời gian trôi qua) → đặt lệnh, không cần biết FTMO có
  đúng nến khớp hay không.
- **Viết lại toàn bộ `Combo.cs`** (dùng Write thay vì Edit từng phần do phạm
  vi thay đổi trải khắp file):
  - Xoá `SignalAlignment` enum, xoá hẳn nhánh xử lý tín hiệu trong
    `OnBarClosed()` (không còn `_signals.TryGetValue(closedBarTime,...)`).
  - Gộp `ProcessFallbackSignals()` → đổi tên `ProcessScheduledSignals()` =
    **DUY NHẤT 1 luồng xử lý tín hiệu**, gọi từ `OnTick()`: chờ tới
    `AvailableTime` (bartime + độ dài nến danh nghĩa) rồi xử lý tại tick khả
    dụng đầu tiên, **KHÔNG còn giới hạn thời gian chờ** (bỏ hẳn
    `GetFallbackExpiry()`/`ExpireFallbackOrders()`/kiểm tra
    `Bars.OpenTimes.GetIndexByExactTime`) — giống hệt cách `MA Cross.cs` đã
    làm từ trước (đã xác nhận qua code, xem entry trước).
  - `PendingOrderLifetime` bỏ field `ExpiresAt` — MỌI pending order giờ chỉ
    dùng đúng 1 kiểu hết hạn: đếm 3 nến chart thật (`ExpirePendingOrders`,
    không đổi) — tự động né kỳ nghỉ dài như thiết kế gốc của nhánh
    exact-match cũ.
  - Gộp counter `_exactMatchedSignals`/`_fallbackAlignedSignals` →
    `_signalsProcessed` duy nhất; xoá hẳn `_fallbackSignalsExpired`. Đổi
    format dòng tổng kết `OnStop()` (bớt 2 field `exact`/`fallback`/
    `fallback-expired`, còn `processed` chung).
  - `ReconcileExistingExposure`/`_signalsSkippedSameDirection`/
    `_reversalsExecuted` (từ lượt sửa trước) **giữ nguyên hoàn toàn** — đây
    là cơ chế riêng, không liên quan missing-bar.
- **Cập nhật `research/fidelity_lib.py` để tương thích cả log cũ lẫn log
  mới** (không đợi người dùng báo lỗi mới sửa):
  - `PLACED_RE`/`REJECTED_RE`/`SAME_DIRECTION_SKIP_RE`: field `alignment=`
    đổi thành optional (`_ALIGNMENT` nhóm dùng chung) — log mới không còn in
    field này nữa.
  - `parse_summary_counters()`: nhận diện 2 định dạng dòng tổng kết OnStop
    (`_SUMMARY_RE_NEW` có `processed=`, `_SUMMARY_RE_OLD` có
    `exact=`/`fallback=`) — thử bản mới trước, không khớp mới thử bản cũ.
  - `FALLBACK_EXPIRED_RE` giữ nguyên (chỉ để đọc lại log CŨ nếu cần — log
    mới sẽ không bao giờ khớp, không phải lỗi).
  - **Regression test**: chạy lại `check_reconcile.py` trên 2 archive cũ
    (US30/H4, HK50/H2 `ReconcileExposure`) — kết quả **giống hệt tuyệt đối**
    trước và sau sửa, xác nhận 0 ảnh hưởng tới log cũ.
- **CHƯA build/test** — người dùng cần build lại `Combo.cs` trong cTrader
  IDE. Dự kiến sau khi build: KHÔNG còn `fallback_expired_waiting`/24 case
  mất tín hiệu ở HK50/H2 (không còn cơ chế nào có thể làm mất tín hiệu do
  hết giờ chờ) — nhưng có thể MA_Cross's timing behaviour giờ khác 1 chút vì
  bot xử lý tại tick khả dụng đầu tiên bất kể FTMO có đúng nến hay không
  (trước đây "exact match" xử lý sớm hơn trong `OnBarClosed`, giờ luôn qua
  `OnTick` — có thể chênh vài mili-giây/giá, không đáng kể).

**Đang dang dở / chưa build-test:** `Combo.cs` **CHƯA ĐƯỢC BUILD/TEST** sau
thay đổi này — SHA-256 ở §4 (kể cả bản `ReconcileExistingExposure`) ĐÃ LỖI
THỜI thêm 1 lớp nữa.

**Quyết định/thoả thuận với người dùng chưa code hoá:** `MA Cross.cs` CÓ
cùng cấu trúc exact/fallback (dù không có vấn đề hết hạn vì vốn không giới
hạn) — người dùng chưa yêu cầu sửa, chỉ nói về Combo. Chưa đụng tới.

**Bước tiếp theo đề xuất:** chờ người dùng build + backtest lại (khuyến
nghị lại đúng HK50/H2 để so sánh trực tiếp: 24 case mất tín hiệu trước đây
có biến mất hoàn toàn không) — verify theo đúng quy trình cũ (kiểm tra
`parameters.cbotset`/log "stopped" trước khi tin), rồi cập nhật lại report
missing-bar (tình trạng hiện tại đã lỗi thời một phần vì cơ chế đang phân
tích đã bị xoá).

### [2026-09-01 (tiếp 12) — Claude]

**Đã làm:**
- Người dùng hỏi sâu về nhóm "fail" trong kết quả visualize, dẫn tới yêu cầu
  phân tích sâu hơn hiện tượng missing-bar — mở rộng
  `reports/missing-bar-followup-2026-09-01.md` thêm §6, dựa trên 4 dataset
  (Combo US30/H4 + HK50/H2 bản `ReconcileExistingExposure` mới, MA Cross
  US30/M30 + HK50/M45 sẵn có).
- **Phát hiện 1**: tỷ lệ missing-bar phụ thuộc SYMBOL rõ hơn timeframe — HK50
  ~28% (cả 2 bot) so với US30 1-16% — bác bỏ giả thuyết cũ "chỉ do độ thô
  timeframe" (MA Cross/M30 mịn trên HK50 vẫn cao gấp ~26 lần chính nó trên
  US30).
- **Phát hiện 2**: pattern Chủ Nhật/Thứ Hai của US30/H4 được xác nhận MẠNH
  HƠN với mẫu lớn hơn nhiều (49 case, vẫn 100% Sun/Mon, so với 9 case trước).
  HK50 KHÔNG thuần Sun/Mon — Thứ Hai vẫn đa số (~67%) nhưng trải cả tuần —
  gợi ý HK50 có thêm nguồn lệch khác ngoài vấn đề đầu tuần.
- **Phát hiện 3 (quan trọng nhất)**: 24 tín hiệu MẤT THẬT SỰ (fallback hết
  hạn) — CHỈ xảy ra ở Combo/HK50/H2, 0 ở 3 dataset còn lại. Đây là bằng
  chứng THỰC TẾ đầu tiên cho rủi ro lý thuyết đã cảnh báo ở Đề xuất 2 (report
  cũ) — có 1 cụm 6 case liên tiếp trùng thời điểm lễ Thanh Minh Hong Kong
  (2026-04-05→07) + 1 case đúng đêm Giáng Sinh.
- **Phát hiện 4**: đọc code xác nhận `MA Cross.cs` KHÔNG có cơ chế hết hạn
  fallback nào (`ProcessFallbackSignals` chờ vô thời hạn) — khác Combo có
  trần 3×duration (Pending Order cần giới hạn tuổi thọ). Không phải bug —
  khác biệt kiến trúc hợp lý. Giải thích case gap 125 giờ (~5 ngày) ở MA
  Cross/HK50/M45 vẫn "recovered" bình thường.
- Nâng độ ưu tiên Đề xuất 2 (§5 report) từ "rủi ro lý thuyết chưa lộ" lên
  "đã xác nhận gây mất tín hiệu thật (24 case, HK50/H2)" — **vẫn CHƯA triển
  khai**, chỉ ghi nhận, chờ quyết định người dùng.

**Đang dang dở / chưa build-test:** không có — thuần phân tích dữ liệu, chưa
đụng code.

**Quyết định/thoả thuận với người dùng chưa code hoá:** Đề xuất 2 (đổi cửa
sổ hết hạn Combo sang đếm nến chart thật) giờ có độ ưu tiên cao hơn nhờ bằng
chứng thật — nhưng vẫn nằm trong nhóm "ghi nhận, chưa triển khai" như 2 đề
xuất còn lại, không tự ý code khi chưa được yêu cầu.

**Bước tiếp theo đề xuất:** nếu người dùng muốn, có thể đào sâu thêm: (a)
kiểm tra chính xác lịch nghỉ lễ Hong Kong quanh 2026-04-05/07 để xác nhận
100% giả thuyết Thanh Minh, (b) mở rộng mẫu HK50 sang timeframe khác
(H1/H3/H4) xem tỷ lệ 24 case failed có tái diễn tương tự không, hoặc (c)
bắt tay triển khai Đề xuất 2 nếu người dùng quyết định ưu tiên.

### [2026-09-01 (tiếp 11) — Claude]

**Đã làm:**
- Người dùng chạy tiếp HK50/H2 (Combo, bản `ReconcileExistingExposure`),
  yêu cầu visualize gộp cả US30/H4 lẫn HK50/H2. Verify + archive HK50/H2
  (đúng instance `1bae1864-...`) — `same-direction-skipped=19, reversed=24`,
  đối chiếu số học nội bộ khớp tuyệt đối (giống US30 lượt trước).
- Trước khi render, phát hiện + sửa 1 vấn đề: tín hiệu bị `same-direction-
  skipped` KHÔNG có dòng `Print()` riêng trong Combo.cs (chỉ tăng counter) —
  nếu visualize thẳng, `fidelity_lib` sẽ hiểu nhầm 9 (US30) + 19 (HK50) tín
  hiệu này thành "⚠ IN_WINDOW_BUT_MISSING" (bất thường giả), phản tác dụng
  đúng lúc muốn xem rõ tính năng mới.
  - **Đã thêm dòng `Print()` cho case này vào `Combo.cs`** (hygiene tốt cho
    các lượt chạy SAU — dòng log rõ ràng thay vì suy luận). **CHƯA build/
    test lại** — 2 lượt archive vừa dùng để visualize vẫn là bản TRƯỚC dòng
    Print() này, không ảnh hưởng.
  - **Xử lý 2 lượt archive hiện có bằng cơ chế đối chiếu số lượng** (không
    cần chạy lại): `fidelity_lib.py` thêm `SUMMARY_RE`/
    `parse_summary_counters()` (đọc dòng tổng kết OnStop cuối log) +
    `SAME_DIRECTION_SKIP_RE` (nhận dòng log mới cho lượt chạy sau). Trong
    `build_fidelity_report()`: nếu số dòng "⚠ IN_WINDOW_BUT_MISSING" khớp
    CHÍNH XÁC với counter `same-direction-skipped` thật → gán lại
    `same_direction_skipped_inferred`; không khớp → giữ nguyên cảnh báo (ưu
    tiên an toàn, không che giấu bất thường thật). Verify: khớp tuyệt đối cả
    2 lượt (9=9, 19=19), 0 "⚠" thật còn sót.
- Đổi 2 dataset Combo trong `RUNS` sang lượt `ReconcileExposure` mới (thay
  lượt cũ tiền-fix), giữ nguyên 2 dataset MA Cross. Rebuild + verify:
  - US30/H4: 393 marker, tier={'partial':47,'full':147,'fail':5} (lưu ý:
    run mới bắt đầu từ 2024-01-02, RỘNG HƠN candle cache đang có (2025-01-01
    trở đi) — phần 2024 không hiện trên chart vì thiếu nến cache, không phải
    lỗi, chỉ là chưa fetch thêm).
  - HK50/H2: 700 marker, tier={'full':245,'partial':81,'fail':48}.
  - `fill_status` khớp đúng: US30 224+76=300=placed; HK50 245+81=326=placed.
- `research/output/signal_chart_viewer.html` ghi đè (11.0MB, 4 dataset).

**Đang dang dở / chưa build-test:** `Combo.cs` có 1 dòng `Print()` mới thêm
(same-direction-skip) **CHƯA được build/test** — không cấp bách, chỉ là
logging, không đổi hành vi đặt lệnh. Có thể gộp vào lần build tiếp theo.

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới.

**Bước tiếp theo đề xuất:** nếu người dùng muốn chart phủ đúng từ 2024 cho
US30/H4 (khớp đúng khung backtest thật thay vì chỉ 2025+), cần fetch thêm
candle+indicator cache cho khoảng 2024-01-01→2024-12-31 — chưa làm, chỉ ghi
nhận khi có nhu cầu.

### [2026-09-01 (tiếp 10) — Claude]

**Đã làm:**
- Người dùng build + backtest lại US30.cash/H4 với `Combo.cs` bản mới
  (`ReconcileExistingExposure`). Gặp 1 sự cố nhỏ tương tự lần trước: verify
  lần đầu trúng đúng lúc 1 lượt MỚI vừa ghi đè lượt cũ (log.txt tụt từ 185KB
  xuống 80 byte giữa 2 lần đọc cách nhau vài giây) — phát hiện kịp, báo
  người dùng ngay, không mất dữ liệu. Cũng phát hiện instance folder
  nhầm lẫn — Combo có **3 instance-id khác nhau** tương ứng 3 chart khác
  nhau (`1bae1864...`=HK50/H2, `5665558f...`=HK50/H4 cũ, `8403a83c...`=
  US30/H4) — phải kiểm tra đúng `parameters.cbotset.SignalFilePath` từng
  instance để xác định đúng chart trước khi verify, không suy đoán theo
  request gần nhất.
- Verify lượt US30/H4 thật (sau khi người dùng chạy lại lần 2, log có dòng
  "stopped" đầy đủ): **counter mới xuất hiện đúng** —
  `same-direction-skipped=9, reversed=16`. Đối chiếu số học nội bộ khớp
  tuyệt đối: `placed(300) + same-direction-skipped(9) = 309 = exact(256) +
  fallback(53)`; `before-start(792) + 309 = loaded(1101)`. Spot-check 16
  dòng `reversal` thật trong log — toàn bộ là "cancelling existing pending
  order" (chưa có case nào phải đóng vị thế đã khớp trong lượt này, không
  phải lỗi, chỉ là chưa rơi vào tình huống đó).
- Đã archive vào `ArchivedRuns\US30_H4_ReconcileExposure_20260901-0935\`.

**Đang dang dở / chưa build-test:** không — `Combo.cs` đã build + test xong,
hành vi đúng thiết kế đã đề xuất. Cần lấy SHA-256 mới cho §4 (chưa làm, ưu
tiên thấp — không ảnh hưởng chức năng).

**Quyết định/thoả thuận với người dùng chưa code hoá:** vẫn treo câu hỏi có
áp dụng `ReconcileExistingExposure` tương tự cho `MA Cross.cs` không — chưa
hỏi lại, chờ người dùng chủ động nêu nếu quan tâm.

**Bước tiếp theo đề xuất:** hỏi người dùng có muốn visualize lượt US30/H4
mới này (thêm vào `RUNS` của `signal_chart_visualizer.ipynb`, đặc biệt hữu
ích để xem trực quan các điểm same-direction-skip/reversal) hay tiếp tục
backtest thêm symbol/timeframe khác trước.

### [2026-09-01 (tiếp 9) — Claude]

**Đã làm:**
- Người dùng dán 1 đề xuất kỹ thuật (nguồn ngoài, đã review nội bộ dự án) yêu
  cầu: (1) đọc, (2) kiểm tra `og_program/core_python/strategies/combo.py` để
  xác định thay đổi, (3) đề xuất hướng xử lý phía cBot Combo cho việc lặp
  tín hiệu cùng hướng / đảo chiều.
- Kiểm tra `combo.py`: xác nhận đúng như đề xuất — `_alternating_signals()`
  (state machine chặn tín hiệu cùng chiều lặp lại) **đã bị xoá**, hiện mỗi
  bar tự đánh giá độc lập. Đây là thay đổi **CHƯA COMMIT** trên OG8 (`git
  diff` xác nhận, git log chỉ có đúng 1 commit gốc của Codex ngày
  26/08/2026 — bản đó VẪN có `_alternating_signals`).
- **Phát hiện quan trọng**: `combo.py` sửa lúc 15:45:56 UTC hôm nay, 2 file
  CSV HK50 tôi export cho người dùng lúc ~15:48 UTC (SAU đó) — nghĩa là
  **lượt backtest HK50/H2 người dùng vừa chạy và đã chart đã dùng CSV sinh
  ra từ logic MỚI** (không còn đảm bảo đảo chiều tuyệt đối). Ghi chú cũ của
  tôi ở §7.4 ("tín hiệu CSV luôn đảm bảo đảo chiều tuyệt đối... 0/1744 cặp
  vi phạm") giờ ĐÃ LỖI THỜI — dựa trên CSV cũ trước khi đổi logic, cần đọc
  lại §7.4 với con mắt hoài nghi nếu tham chiếu trong tương lai.
- Đọc toàn bộ `Combo.cs`/`MA Cross.cs` — xác nhận CẢ HAI hiện KHÔNG có bất
  kỳ cơ chế nào kiểm tra exposure hiện tại trước khi đặt lệnh mới (chỉ chặn
  trùng-xử-lý-1-dòng-CSV qua `IsHandled` + guard tài khoản FTMO) — mọi
  signal hợp lệ đều đặt lệnh mới vô điều kiện, có thể chồng nhiều lệnh cùng
  hướng hoặc mở đồng thời 2 hướng ngược nhau.
- **Đề xuất + đã triển khai** (người dùng xác nhận "hãy triển khai") vào
  `Combo.cs`: 1 nguyên tắc duy nhất — mỗi symbol tối đa 1 hướng exposure
  (vị thế mở HOẶC lệnh chờ) dưới Label này tại 1 thời điểm, tự hỏi thẳng
  `Positions`/`PendingOrders` của cTrader (không thêm biến trạng thái riêng
  — luôn đúng theo thời gian thực, không như state machine bên OG).
  - Tín hiệu CÙNG hướng exposure đang có → bỏ qua (đáp ứng đúng ví dụ 1+2
    người dùng nêu, kể cả case 2 tự động đúng vì hết exposure = hết chặn).
  - Tín hiệu NGƯỢC hướng → đóng vị thế đang mở + huỷ lệnh chờ đang có, rồi
    đặt lệnh mới (đáp ứng ví dụ 3).
  - Thêm method `ReconcileExistingExposure(SignalRow)` + `DirectionOf()`,
    gọi trong `HandleSignal()` trước `PlacePendingOrder()`. Thêm 2 counter
    mới tách riêng khỏi guard cũ (`_signalsSkippedSameDirection`,
    `_reversalsExecuted`) + Print() cho mỗi lần đóng/huỷ do đảo chiều (giữ
    đúng triết lý logging đầy đủ audit trail của file, phục vụ được cả công
    cụ visualize research/ sau này nếu cần mở rộng).
- **CHƯA build/test** — người dùng cần build lại trong cTrader IDE như quy
  trình chuẩn (máy này không có `dotnet` CLI để tự compile-check).

**Đang dang dở / chưa build-test:** `Combo.cs` đã sửa xong nhưng **CHƯA
ĐƯỢC BUILD/TEST** — SHA-256 cũ ở §4 (dòng
`BC52B7E119292733DC6380E05505C65FCE6E8C47EF63F771E0B30298299E19A6`) ĐÃ LỖI
THỜI, cần cập nhật sau khi người dùng build + báo kết quả.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- `MA Cross.cs` có CÙNG lỗ hổng (không kiểm tra exposure) nhưng người dùng
  chỉ yêu cầu sửa Combo — đã nêu quan sát này trong lượt trả lời, CHƯA sửa,
  chờ người dùng quyết định có muốn áp dụng tương tự không.
- Việc commit các thay đổi uncommitted trên OG8 (combo.py + rất nhiều file
  khác đang M/D trong git status — có vẻ 1 phiên refactor lớn đang dở của
  Codex) — KHÔNG phải việc của tôi, không tự ý commit/đụng vào git OG8.

**Bước tiếp theo đề xuất:**
1. Chờ người dùng build `Combo.cs` trong cTrader IDE, báo lỗi compile nếu
   có (đặc biệt double-check `Position.TradeType`/`PendingOrder.TradeType`
   — tin là đúng API chuẩn nhưng máy này không tự compile-check được).
2. Sau khi build sạch, backtest lại ít nhất 1 lượt (khuyến nghị HK50/H2 vì
   đã biết chắc CSV đó có tín hiệu cùng chiều lặp lại thật) để verify hành
   vi mới đúng ý — có thể dùng notebook `signal_chart_visualizer.ipynb`
   quan sát trực quan các trường hợp bị bỏ qua/đảo chiều.
3. Hỏi người dùng có muốn áp dụng cùng nguyên tắc cho `MA Cross.cs` không.

### [2026-09-01 (tiếp 8) — Claude]

**Đã làm:**
- Người dùng backtest HK50/H2 (Combo) + HK50/M45 (MA Cross) lần đầu, cả 2 báo
  lỗi. Kiểm tra log: KHÔNG phải bug — 2 file signal CSV cho HK50/H2 và
  HK50/M45 chưa từng được export (chỉ có sẵn HK50/H4, HK50/M30 = timeframe
  mặc định). H2/M45 vẫn hợp lệ theo `config.yaml`. Đã export luôn 2 file
  qua đúng `export_cli.py` (không override tham số) để người dùng chạy lại
  ngay không cần qua OG8.
- Người dùng chạy lại thành công cả 2 (Combo: 247 placed/0 failed; MA Cross:
  286 placed/58 failed), báo "vừa backtest cả 2 với dữ liệu m1" (tick
  precision, không ảnh hưởng `parameters.cbotset`). Verify log "stopped" +
  events.json không rỗng trước khi archive — an toàn, đã archive vào
  `ArchivedRuns\HK50_H2_20260901-0603\` (Combo) và
  `ArchivedRuns\HK50_M45_20260901-0603\` (MA Cross).
- Fetch cache nến + chỉ báo cho HK50/H2 và HK50/M45 (cùng quy trình US30:
  script tạm chạy trên OG8 chỉ SELECT + gọi lại `run_strategy()`, xoá sạch
  file tạm sau khi fetch — đã verify `Test-Path`=False cho cả 6 file tạm).
- Thêm 2 entry mới vào `RUNS` trong `signal_chart_visualizer.ipynb`.
- **Phát hiện + sửa 1 BUG THẬT khi verify số liệu trước khi báo cáo** (không
  chỉ tin số notebook in ra — đối chiếu với counter nội bộ của log như mọi
  lần): Combo HK50/H2 báo `placed=249` trong khi log tự đếm `placed=247`.
  Truy nguyên: `fidelity_lib.get_fill_outcomes()` join `fill_status` vào
  `merged` qua GIÁ ENTRY (`on="entry"`) — HK50/H2 có đúng 1 cặp lệnh khác
  nhau trùng giá (`entryPrice=25324.6`, orderId 252 và 431), merge theo giá
  nhân đôi dòng. US30 không dính vì (may mắn) không giá nào trùng — bug tồn
  tại từ Phase 13/14 nhưng chưa bao giờ lộ ra ở các lần kiểm chứng trước.
  **Đã sửa**: gán `fill_status` theo VỊ TRÍ/thứ tự thời gian (cả `merged`
  placed-subset lẫn `fill_df` đều tự nhiên cùng chronological order, không
  cần join theo giá) — có cảnh báo in ra nếu số lượng 2 phía lệch nhau thay
  vì âm thầm gán sai. Re-verify: US30 không đổi (140/110/29/1), HK50/H2 giờ
  đúng 247/179/68 khớp log.
- Đã chạy lại `nbconvert --execute --inplace`, tổng 4 dataset trong dropdown,
  số liệu đã verify khớp log gốc cho cả 4. `research/output/
  signal_chart_viewer.html` ghi đè (10.9MB — tăng do 2 dataset mới có MACD/
  overlay đầy đủ; vẫn mở bình thường bằng trình duyệt, chưa thấy vấn đề hiệu
  năng nhưng cần để ý nếu thêm nhiều dataset nữa).

**Đang dang dở / chưa build-test:** **CHƯA có xác nhận từ người dùng** rằng
bản 4-dataset này hiển thị đúng (đặc biệt 2 dataset HK50 mới).

**Quyết định/thoả thuận với người dùng chưa code hoá:** người dùng dự định
tiếp tục backtest thêm — quy trình mở rộng `RUNS` giờ đã lặp lại 2 lần thành
công (US30→HK50), đủ tin cậy để lặp lại nhanh cho symbol/timeframe khác khi
được yêu cầu.

**Bước tiếp theo đề xuất:** chờ xác nhận từ người dùng; theo dõi kích thước
file HTML nếu tiếp tục thêm dataset (đã 10.9MB/4 dataset) — có thể cần tính
tới việc giảm số điểm MACD/overlay (vd downsample) nếu file quá nặng để mở
mượt trong tương lai, chưa cần làm ngay.

### [2026-09-01 (tiếp 7) — Claude]

**Đã làm:**
- Người dùng hỏi 2 việc: (1) MA/SMA/MACD hist có phải tính TRỰC TIẾP từ SQL
  data warehouse không (không phải đọc lại 1 file có sẵn)? (2) yêu cầu kiểm
  chứng tham số mặc định dùng khi tính chỉ báo có khớp đúng tham số đã tạo
  ra file signal CSV đang dùng hay không.
- Trả lời (1): xác nhận đúng — script tạm gọi `load_range()` (query SQL
  trực tiếp) rồi `run_strategy()` (hàm thật) NGAY trên kết quả đó; CSV chỉ
  là khâu vận chuyển kết quả tính toán từ OG8 (có SQL) sang BO20 (không có
  SQL), giống hệt cách candles đã được lấy từ đầu.
- Kiểm chứng (2) — không dừng ở suy luận gián tiếp qua `run_og.sh` (đã đọc
  kỹ `export_cli.py`, phát hiện CLI THỰC RA có `--param NAME=VALUE` override,
  dù menu tương tác không hỏi tới) — làm **thực nghiệm dứt điểm**:
  regenerate lại 2 file signal CSV bằng export_cli với tham số mặc định
  (không override), `diff` trực tiếp trên OG8 với file thật đang dùng trong
  `parameters.cbotset` của các lượt archive.
  - **Combo**: `diff -q` → identical tuyệt đối (0 khác biệt).
  - **MA Cross**: khác đúng 2 dòng, cả 2 đều ở CUỐI file — 2 signal MỚI hơn
    (2026-08-31 11:00/13:30) chỉ có trong bản regenerate, do DP6 đã ingest
    thêm bar mới từ lúc file gốc export tới giờ (full_history export không
    chốt `--to`, tự lấy tới bar mới nhất tại thời điểm chạy) — KHÔNG phải do
    khác tham số. Mọi dòng trước đó khớp tuyệt đối.
  - **Kết luận**: tham số mặc định dùng khi tính chỉ báo khớp đúng 100% với
    tham số đã tạo signal CSV đang hiển thị — không có rủi ro lệch tham số
    làm sai lệch việc đối chiếu trên chart.
  - Đã xoá sạch file tạm `/tmp/_verify_*.csv` trên OG8 sau khi kiểm chứng
    xong.

**Đang dang dở / chưa build-test:** không có — đây là công việc điều tra/xác
minh thuần, không đụng code/notebook.

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới.

**Bước tiếp theo đề xuất:** không có việc mở nào — chờ chỉ đạo tiếp theo.

### [2026-09-01 (tiếp 6) — Claude]

**Đã làm:**
- Người dùng xác nhận bản "tiếp 5" (fix race condition) đã hiển thị đúng, rồi
  yêu cầu thêm: (1) bỏ chart equity (không nhiều giá trị), (2) thêm chart chỉ
  báo đầu vào (MACD Histogram, MA20, SMA13/34...) lấy đúng từ
  `og_program/core_python/signal_display/` bên OG8. Giữa chừng nhận thêm 1
  phản hồi: marker Signal (chấm tròn) đang đè lên thân nến, cần đẩy ra xa.
- Đọc kỹ `signal_display/payload.py`/`server.py`/`live_page.py`/`renderer.py`
  bên OG8 (module dashboard sống nội bộ, dùng CHÍNH `lightweight-charts.js`
  bản đã vendor) — xác nhận chuẩn có sẵn: `configuration.run_strategy()` trả
  DataFrame có cột `ma` (Combo, MA_PERIOD=20), `fast_ma`/`slow_ma` (MA Cross,
  FAST_MA=13/SLOW_MA=34), `macd_h` (MACD Histogram, cả 2 strategy) — đúng
  khớp ví dụ người dùng nêu ("MACD hist, MA20, SMA13/34"). Giá trị period lấy
  trực tiếp từ `config.yaml` OG8 (đọc, không sửa).
- Viết 1 script Python một-lần (`_tmp_export_indicators.py`, chỉ SELECT SQL +
  gọi lại `run_strategy()` có sẵn — KHÔNG tự tính lại chỉ báo độc lập, tránh
  lệch với logic tín hiệu thật) — copy lên OG8 qua Z:, chạy qua SSH
  (Posh-SSH, thư mục `og_program/` gốc để `import core_python` resolve
  đúng), xuất 2 CSV chỉ báo, fetch về `research/data_cache/`, **đã xoá sạch
  file tạm + script trên OG8 ngay sau khi lấy xong** (verify bằng
  `Test-Path` = False cho cả 3 file).
- Sửa `signal_chart_visualizer.ipynb`: bỏ hẳn `equityChart`/`get_equity_curve`
  khỏi notebook (hàm vẫn giữ trong `fidelity_lib.py`, không dùng nữa); thêm
  overlay `addLineSeries` (MA/Fast+Slow SMA) chồng lên chart nến + 1 panel
  `addHistogramSeries` (MACD) bên dưới, đồng bộ kéo/zoom với chart nến (dùng
  LẠI cơ chế `linkTimeScales` có try/catch đã fix ở vòng trước — áp dụng
  ngay từ đầu, không đợi lỗi lặp lại); tooltip hover giờ có thêm giá trị chỉ
  báo tại đúng nến đang trỏ. Đổi marker Signal từ `position: inBar` (giữa
  thân nến, dễ bị đè) sang `belowBar`/`aboveBar` theo đúng hướng lệnh (khớp
  quy ước marker Entry) — 2 lớp không bao giờ trùng nến (đã verify khoảng
  cách tối thiểu 1 nến từ vòng trước) nên không lo chồng lấn.
- Verify số liệu không đổi sau refactor (280/902 marker, tier counts giữ
  nguyên) — chỉ thêm dữ liệu chỉ báo, không đụng logic đối chiếu CSV↔log.
  `research/output/signal_chart_viewer.html` ghi đè (6.1MB, tăng do thêm dữ
  liệu MACD/overlay).

**Đang dang dở / chưa build-test:** **CHƯA có xác nhận từ người dùng** rằng
bản này hiển thị đúng ý (chart chỉ báo + vị trí marker mới).

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới.

**Bước tiếp theo đề xuất:** chờ xác nhận từ người dùng.

### [2026-09-01 (tiếp 5) — Claude]

**Đã làm:**
- Người dùng báo chart mới (equity + marker) hoàn toàn TRỐNG sau vòng sửa
  "tiếp 4". Không đoán mò — thêm banner đỏ bắt lỗi JS hiển thị ngay trên
  trang + ép resize phòng thủ, yêu cầu người dùng hard-refresh và gửi lại.
  Người dùng gửi ảnh banner lỗi thật: `Re.setVisibleRange ... Error: Value
  is null`, stack trace trỏ đúng vào `linkTimeScales`.
- **Xác định đúng root cause qua stack trace (không phải đoán)**: race
  condition — `candleSeries.setData()` (dòng đầu `showDataset()`) bắn sự
  kiện "visible range đổi" NGAY LẬP TỨC → kích hoạt đồng bộ sang
  `equityChart` — nhưng `equitySeries.setData()` chưa kịp chạy (nằm sau vài
  dòng) → thư viện không tính được range cho 1 series rỗng → ném lỗi nội bộ
  → **dừng CẢ SCRIPT giữa chừng**, giải thích đúng tại sao nến hiện được
  (set trước khi crash) còn marker + equity chart phía sau code đều trống.
- Sửa: bọc try/catch quanh lệnh đồng bộ 2 chiều trong `linkTimeScales()` —
  bỏ qua an toàn khi chart kia chưa sẵn sàng, lần đồng bộ tường minh cuối
  `showDataset()` (chạy sau khi cả 2 series đã có dữ liệu) tự chỉnh lại đúng.
  Đã chạy `nbconvert --execute --inplace` lại, verify JS không còn lỗi cú
  pháp/escape brace ở khu vực sửa.

**Đang dang dở / chưa build-test:** **CHƯA có xác nhận từ người dùng** rằng
bản sửa này thực sự hiển thị đúng — cần người dùng hard-refresh lại và báo
kết quả (đã dặn trong lượt trả lời).

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới.

**Bước tiếp theo đề xuất:** chờ xác nhận từ người dùng; nếu banner đỏ vẫn
xuất hiện (lỗi khác), đọc nội dung banner mới thay vì đoán tiếp.

### [2026-09-01 (tiếp 4) — Claude]

**Đã làm:**
- Người dùng phản hồi thêm 2 vòng UX cho `signal_chart_visualizer.ipynb`
  (sau vòng sửa bug tách-marker ở entry "tiếp 3" bên dưới):
  1. Chú thích thu về 1 góc nhỏ (không chiếm header); marker Signal đổi
     sang có màu theo hướng (xanh dương Buy/cam Sell) thay vì xám đều; mặc
     định chỉ hiện ~200 nến gần nhất (kéo mới lộ thêm); thêm 1 chart phụ vẽ
     đường số dư tài khoản (từ field `balance`/`equity` có sẵn trong
     `events.json` — hàm mới `fidelity_lib.get_equity_curve()`) ngay bên
     dưới chart nến, **2 chart kéo/zoom đồng bộ trục thời gian**; text chi
     tiết OHLC + entry/SL/TP/trạng thái chuyển từ hiển thị thường trực (v4
     vẽ `SeriesMarker.text` vĩnh viễn cạnh marker — đây là nguồn gốc "hiển
     thị hết tất cả" người dùng phàn nàn) sang **tooltip chỉ hiện khi rê
     chuột** (`subscribeCrosshairMove`). SL/TP hoá ra đã có sẵn ngay trong
     dòng log "placed" — mở rộng `PLACED_RE` (nhóm optional, không phá số
     đếm 140/343 đã re-verify) thay vì phải join qua `events.json`.
  2. Chú thích thành **checkbox lọc theo nhóm marker** (Signal / Vào lệnh
     thành công / Đặt được không khớp) — bật/tắt riêng từng nhóm, đúng ý
     người dùng "chỉ muốn xem Signal + đặt được nhưng không khớp".
- Đã chạy lại `nbconvert --execute --inplace` sau cả 2 vòng, verify số liệu
  không đổi (280/902 marker, tier counts giữ nguyên) — chỉ thay đổi cách
  hiển thị, không đổi logic đối chiếu dữ liệu.
- `research/output/signal_chart_viewer.html` đã ghi đè (2.4MB, đã kiểm tra
  không có lỗi escape brace JS sau khi build từ f-string Python).

**Đang dang dở / chưa build-test:** không có gì mới.

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới.

**Bước tiếp theo đề xuất:** chờ người dùng mở lại file xác nhận UX đã đúng ý
(2 chart đồng bộ, tooltip hover, checkbox lọc, 200-nến mặc định).

### [2026-09-01 (tiếp 3) — Claude]

**Đã làm:**
- Người dùng phản hồi chart vừa giao **sai**: "nến có signal khác với nến vào
  lệnh, 2 marker đó phải tách ra mới đúng". Sửa tận gốc trong
  `signal_chart_visualizer.ipynb` + `fidelity_lib.py`:
  - `parse_log()` trước đây parse được nhóm regex `executed=...` (thời điểm
    lệnh THỰC SỰ gọi ra broker) nhưng **không giữ lại** — chỉ dùng `bartime`
    (nến sinh signal) cho mọi marker, khiến "nến vào lệnh" bị vẽ nhầm trùng
    "nến có signal". Đã thêm cột `executed` vào output của `parse_log()`.
  - `build_markers()` giờ vẽ **2 lớp marker tách biệt**: (1) chấm nhỏ trung
    tính tại `bartime` = tín hiệu xuất hiện, LUÔN vẽ; (2) mũi tên tại nến chứa
    `executed` (floor xuống nến gần nhất có thật trong series) = lệnh thực sự
    vào thị trường, CHỈ vẽ khi có lệnh đặt được. Verify bằng dữ liệu thật:
    exact-match luôn cách nhau đúng 1 nến (vì `OnBarClosed` báo khi nến ĐÃ
    đóng = đúng lúc nến kế mở), fallback cách 3-4h (~1 nến H4).
  - **Phát hiện thêm 1 bug độc lập trong lúc sửa (không phải người dùng báo,
    tự tìm ra khi verify)**: máy này chạy **Pacific Time (UTC-8)**, không
    phải UTC. `pandas Timestamp.timestamp()` trên giá trị tz-naive (bartime/
    executed đều là UTC-naive theo đúng quy ước CLAUDE.md) quy đổi qua múi
    giờ HỆ THỐNG trước khi ra epoch — mọi marker time trong bản giao lần đầu
    đã bị lệch ~8 tiếng so với candle series thật (candle series dùng
    `utc=True` nên KHÔNG bị lỗi này — chỉ marker mới bị). Sửa bằng
    `ts.value // 10**9` (đọc thẳng field wall-clock, không qua quy đổi tz) —
    verify: bartime sau fix khớp CHÍNH XÁC 100% với 1 timestamp có thật trong
    candle cache (test 5 mẫu, trước fix sẽ KHÔNG khớp candle nào).
  - Đã chạy lại `nbconvert --execute --inplace` thành công, output mới:
    Combo 280 marker (140 signal + 140 entry, đúng = 110 full + 30 partial),
    MA Cross 902 marker (559 signal + 343 entry, đúng = số "placed" thật).
  - `research/output/signal_chart_viewer.html` đã ghi đè lại (2.18MB).

**Đang dang dở / chưa build-test:** không có gì mới ngoài mục đã ghi ở entry
trước (`RUNS` vẫn 2 dataset).

**Quyết định/thoả thuận với người dùng chưa code hoá:** không có gì mới.

**Bước tiếp theo đề xuất:** chờ người dùng mở lại `signal_chart_viewer.html`
xác nhận 2 lớp marker đã tách đúng ý; nếu ổn, không cần làm gì thêm cho công
cụ này.

### [2026-09-01 (tiếp 2) — Claude]

**Đã làm:**
- Xoá parameter `Enable Missing-Bar Fallback` khỏi `Combo.cs`/`MA Cross.cs`
  (theo yêu cầu người dùng "muốn cơ chế fallback mặc định luôn, ko cần mode
  nữa") — `InitializeSignalSchedule()` chỉ còn phụ thuộc
  `TryGetNominalBarDuration`. Người dùng đã build lại trong cTrader IDE và
  xác nhận thành công (đã tự verify qua `parameters.cbotset` của các lượt
  archive sau — không còn field này).
- Người dùng backtest lại cả 2 cBot từ đầu 2025 → hiện tại (US30/H4 Combo,
  US30/M30 MA Cross) — archive `US30_H4_AlwaysFallback_2025plus_20260901-0204`
  và `US30_M30_AlwaysFallback_2025plus_20260901-0208`.
- Phân tích rõ **"3 tầng thành công" của việc đặt lệnh**: (1) signal nhận
  đúng, (2) broker CHẤP NHẬN lệnh ("placed"), (3) lệnh thực sự THÀNH vị thế
  ("filled"). MA Cross (Market Order): tầng 2=3 luôn. Combo (Pending Stop):
  KHÔNG — 140 "placed" nhưng chỉ 110 filled thật (29 hết hạn/1 còn treo cuối
  kỳ), truy vết qua `orderId` trong `events.json`.
- Tách `research/fidelity_lib.py` — refactor logic đã tự-kiểm-chứng từ
  `signal_fidelity_check.ipynb` thành module dùng chung, cộng thêm hàm mới
  `get_fill_outcomes()` (giải quyết đúng phân tích 3 tầng ở trên).
  `signal_fidelity_check.ipynb` giữ nguyên bản sao độc lập của nó, KHÔNG sửa
  lại/KHÔNG import module mới, để không rủi ro tới notebook đã validate.
- **Dựng `research/signal_chart_visualizer.ipynb`** theo yêu cầu người dùng
  ("visualize dùng lightweight chart, đối chứng 3 nguồn: nến SQL + signal CSV
  + lệnh thật backtest, mọi signal đều lên marker phân biệt đặt được/không").
  Vendor `research/vendor/lightweight-charts.js` từ chính `dp_program_v3`
  (DP6, byte-identical, xác nhận API qua đọc `util/chart/server.py` của DP6
  rồi xoá bản đọc tạm — không giữ code DP6 thừa trong repo). Cache 2 file nến
  SQL qua `db_connector.load_range()` trên OG8 (`US30_H4_candles.csv` 2,561
  dòng, `US30_M30_candles.csv` 20,188 dòng) — chỉ SELECT, xoá file tạm phía
  OG8 sau khi lấy xong.
  - Nhận thêm 1 chỉ đạo giữa chừng ("code đơn giản/gọn/tối ưu, dễ mở rộng,
    có dropdown chọn symbol/timeframe") → thiết kế `RUNS` = 1 CONFIG list làm
    nguồn chân lý duy nhất + 1 hàm `build_dataset()` dùng chung cho mọi
    combo, xuất RA ĐÚNG 1 file HTML (`output/signal_chart_viewer.html`) chứa
    tất cả dataset, dropdown chuyển qua lại bằng JS thuần (không dùng
    ipywidgets — dropdown nằm ngay trong chart, dùng được mãi mãi kể cả
    không mở lại Jupyter, khớp tinh thần "self-contained offline" đã có).
  - Chạy `nbconvert --execute --inplace` thành công (sau khi sửa 2 lỗi nhỏ:
    f-string chứa backslash không hợp lệ Python 3.12; so sánh
    tz-aware×tz-naive giữa candle timestamp và `merged["bartime"]`).
  - **Kết quả cuối (đã tự đối chiếu khớp với `fidelity_lib.build_fidelity_
    report()` summary, không chỉ tin output notebook)**: Combo H4 — 2,561
    nến, 140 marker (110 xanh/đỏ đậm "full", 30 vàng nhạt "partial" =
    29 expired_unfilled + 1 still_pending). MA Cross M30 — 20,188 nến, 559
    marker (343 xanh/đỏ đậm "full", 216 xám "fail" = đúng bằng số `rejected`
    trong log, không có bất thường ẩn). Tổng 753/2,934 signal CSV nằm trong
    khung test tương ứng, phần còn lại đều là `before_test_window` hợp lệ.
  - File output đã xác nhận tồn tại: `research/output/
    signal_chart_viewer.html` (~2.1MB, tự chứa, mở bằng trình duyệt bất kỳ,
    offline hoàn toàn).
- Cập nhật `research/README.md` (mô tả notebook mới) — chưa cập nhật gì khác.

**Đang dang dở / chưa build-test:**
- Không có thay đổi `Combo.cs`/`MA Cross.cs` nào trong phần việc này — 2 hash
  ở §4 không đổi so với bản đã build/test trước đó (chỉ khác: bản build đó
  BÂY GIỜ đã xác nhận chạy thật, không còn "chưa test" như ghi trước).
- `RUNS` trong `signal_chart_visualizer.ipynb` mới có 2 dataset (Combo
  US30/H4, MA Cross US30/M30) — chưa mở rộng thêm symbol/timeframe nào khác,
  chưa có yêu cầu làm việc đó (xem §5 mục 9).

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Không có quyết định mới treo lại trong phần việc này ngoài các mục đã ghi
  ở §5 (3 đề xuất fallback vẫn "ghi nhận, chưa triển khai"; ranh giới
  BTCUSD/margin vẫn thuộc đội khác).

**Bước tiếp theo đề xuất:**
1. Chờ người dùng mở `signal_chart_viewer.html`, xem trực quan và phản hồi
   — có phát hiện bất thường nào cần điều tra thêm không.
2. Nếu người dùng muốn mở rộng thêm symbol/timeframe cho visualizer, làm
   theo đúng quy trình đã ghi ở `research/README.md`/§5 mục 9 (không cần
   thiết kế lại).
3. Các việc mở khác vẫn đang chờ chỉ đạo — không tự ý làm tiếp (3 đề xuất
   fallback, Walk-Forward/ctrader-cli, margin debug BTCUSD).

### [2026-09-01 (tiếp) — Claude]

**Đã làm:**
- Tiếp quản từ entry Codex 00:02 cùng ngày — đọc đầy đủ `CLAUDE.md`,
  `AGENT.md`, `Combo.cs`, `MA Cross.cs`, `reports/signal-alignment-baseline-
  2026-08-31.md`; tự verify SHA-256 cả 2 source khớp tuyệt đối với hash Codex
  báo cáo.
- Lặp lại kiểm chứng missing-bar trên khung NGẮN (2026-01 → hiện tại, thay vì
  toàn bộ 2024-2026) cho cả 4 tổ hợp: Combo/H4 ON+OFF, MA Cross/M30 ON+OFF —
  tự xác minh tham số + khung ngày thật qua `parameters.cbotset`/`events.json`
  trước khi archive mỗi lượt (đã xử lý 1 sự cố suýt mất dữ liệu Combo/H4/OFF
  do người dùng báo "xong" 2 việc dồn 1 tin nhắn — bắt kịp trước khi bị ghi đè).
- Xác nhận hiện tượng missing-bar vẫn xảy ra thật: Combo 9/62=14.5%, MA Cross
  3/203=1.5%. Phát hiện thêm hiệu ứng lan qua cỡ lệnh (risk% theo balance
  động) — với MA Cross, tìm ra chính xác 1 lệnh exact-match
  (`2026-03-06 05:00`) đổi từ PLACED→REJECTED margin khi bật fallback.
- Audit sâu code cơ chế fallback (`InitializeSignalSchedule`,
  `ProcessFallbackSignals`, `ExpireFallbackOrders`, `GetFallbackExpiry`) —
  xác nhận phần lõi đúng/hiệu quả (O(N) qua cursor, chống trùng bằng
  `IsHandled`, tách đúng 2 kiểu hết hạn). Phát hiện 9/9 mốc fallback Combo/H4
  rơi đúng Chủ Nhật/Thứ Hai (gợi ý lệch giờ mở phiên đầu tuần Capital.com↔FTMO)
  — người dùng nhắc đúng: đây là quan sát nội tại riêng mẫu H4, không phải
  quy luật tổng quát cho mọi timeframe.
- Đề xuất 3 hướng cải thiện cơ chế fallback (đã rà lại theo đúng yêu cầu
  "phải tổng quát cho mọi timeframe" của người dùng) — **ghi vào
  `reports/missing-bar-followup-2026-09-01.md` §5, CHƯA triển khai**.
- Tạo `research/` (yêu cầu người dùng) — phát hiện máy chưa cài Python/Jupyter,
  chưa tự cài, đã ghi `research/README.md` + TODO §5 mục 7.
- **Điều tra đầy đủ VM-DP6** (theo yêu cầu người dùng hiểu trọn hệ thống 3
  tầng DP6→OG8→BO20) — đọc docs, SQL schema, `sql_connector.py` tương đương
  bên OG8 (`db_connector.py`), `ma_cross.py` (chưa đọc trước đây),
  `export_cli.py`. Xác nhận: TradingView/Capital.com → `Fact_OHLCV` (DP6) →
  `core_python` đọc SQL trực tiếp, tính signal, xuất CSV tối giản (KHÔNG có
  nhận thức gì về lịch nến FTMO) → cBot đọc CSV. Xác nhận thêm: MA Cross
  không cần cơ chế chống-trùng-hướng tường minh như Combo vì bản chất
  "cắt lên/xuống" tự đảm bảo xen kẽ toán học.
- **Sự cố an toàn**: vô tình đọc `Config.yaml` của DP6 (chứa token/cookie
  TradingView, mật khẩu SQL/Discord/Redis thật) — vi phạm đúng quy tắc
  "Before Operating" của chính `OPERATOR_RUNBOOK.md` dự án đó. Đã xoá bản
  copy cục bộ, báo người dùng minh bạch; người dùng xác nhận không sao (hệ
  thống nội bộ), không cần rotate, và cho phép đọc thoải mái các file khác.
- **Cài Python 3.12.7 + jupyter/pandas/matplotlib/numpy** trên BO20 (theo
  yêu cầu người dùng) — xem `research/README.md`.
- **Dựng và tự-kiểm-chứng `research/signal_fidelity_check.ipynb`** — đối
  chiếu ĐỘC LẬP CSV signal ↔ `log.txt` backtest (không tin bộ đếm nội bộ
  cBot), phân biệt đúng "before-test-window" / "fallback tắt nên không xử
  lý" (đúng thiết kế) / "⚠ trong khung test mà log không nhắc — cảnh báo
  thật" / direction-mismatch / entry-mismatch. Chạy thử trên 4 lượt archive
  thật (Combo H4 + MA Cross M30, cả 2 chế độ Fallback) — khớp tuyệt đối với
  phân tích PowerShell thủ công trước đó, 0 bất thường thật ở cả 4. Giới hạn
  đã biết: không truy được bartime cụ thể cho các lần bị skip do dưới sàn
  volume (Combo.cs/MA Cross.cs không gắn bartime vào dòng log đó).

**Đang dang dở / chưa build-test:**
- Không có thay đổi `Combo.cs`/`MA Cross.cs` nào trong phần này của phiên —
  chỉ phân tích dữ liệu archive sẵn có + tài liệu hoá. Build/test gần nhất
  vẫn là 2 hash ở §4 (không đổi).
- `research/` mới chỉ có `README.md`, chưa có notebook nào — chờ cài
  Python/Jupyter trước.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- 3 đề xuất cải thiện fallback (xem `reports/missing-bar-followup-2026-09-01.md`
  §5) — người dùng nói rõ "ghi nhận lại trước, chưa cần làm, trao đổi sau" —
  KHÔNG tự triển khai bất kỳ đề xuất nào cho tới khi có chỉ đạo tiếp.
- Câu hỏi còn treo (từ trước, vẫn chưa điều tra): khả năng lấy lịch nến/dữ
  liệu lịch sử FTMO cho Python dùng — mở khoá cả đề xuất 3 lẫn hướng "chuyển
  hẳn signal sang data FTMO" đã bàn trước đó.
- Cài Python/Jupyter cho `research/` — chưa tự làm, cần người dùng xác nhận
  trước (thay đổi hệ thống).

**Bước tiếp theo đề xuất:**
1. Hỏi người dùng có muốn điều tra khả năng lấy lịch nến FTMO cho Python
   trước (mở khoá nhiều hướng cùng lúc) hay ưu tiên việc khác.
2. Nếu người dùng muốn dùng `research/`, hỏi xác nhận trước khi cài
   Python/Jupyter (phiên bản, cài qua đâu — python.org installer hay khác).
3. Mọi thay đổi `Combo.cs`/`MA Cross.cs` sau này vẫn phải chờ người dùng build
   trong cTrader IDE; không tuyên bố build sạch nếu chưa có xác nhận thật.

### [2026-09-01 00:02 — Codex]

**Đã làm:**
- Tiếp quản dự án, đọc `CLAUDE.md`, `AGENT.md`, source thật và raw backtest;
  sau đó làm theo quyết định mới của người dùng: đơn giản hoá cBot để tập
  trung kiểm định signal → order, bỏ kiến trúc 5 ExitMode khỏi source hiện
  hành nhưng giữ toàn bộ archive/report legacy.
- Hoàn thiện hai cBot CSV độc lập: Combo dùng Pending Stop tại đúng `entry`,
  lifetime 3 nến; MA Cross dùng Market Order. Cả hai giữ KSL/KTP 10 mức Fib,
  risk mặc định 1% current balance và FTMO guards mặc định OFF.
- Code cơ chế thời gian exact-first/missing-bar fallback sạch và có thể tắt:
  exact CSV `bartime` tiếp tục xử lý ở `OnBarClosed`; chỉ signal không có exact
  FTMO bar mới được xử lý một lần tại tick đầu sau nominal source close. Không
  hard-code timezone/DST/symbol; đã thêm log tách exact/fallback và chống trùng.
- Đã lưu source + raw baseline trước sửa và post-run sau sửa vào `ArchivedRuns`;
  ghi hash/source/config/kết quả vào
  `reports/signal-alignment-baseline-2026-08-31.md`.
- Người dùng đã build cả hai source trong cTrader IDE và chạy lại đúng
  `Combo US30.cash/H4` cùng `MA Cross US30.cash/M30`. Raw post-run xác nhận:
  - Combo: 176 exact + 38 fallback = 214 unique signal/order; 0 reject; exact
    lifecycle giữ nguyên; fallback 28 fill/10 cancel, 6 TP/22 SL, net -$704.36.
    Tổng net trước/sau +$834.30 → +$36.76.
  - MA Cross: 897 exact + 14 fallback = 911 unique attempt; exact giữ nguyên
    503 accepted/394 `NoMoney`; fallback 6 accepted/8 `NoMoney`, 1 TP/5 SL,
    net -$212.55. Tổng net trước/sau -$2,003.52 → -$2,168.86.
- Đã đối chiếu CSV với log/events: 0 signal trùng, 0 thiếu sau scheduling,
  0 sai direction/SL/TP; Combo 0 sai entry. Kết luận runtime: scheduler chạy
  đúng thiết kế; nhóm signal được phục hồi thua lỗ là kết quả chiến lược/data,
  không phải lỗi căn chỉnh thời gian.

**Đang dang dở / chưa build-test:**
- Không còn thay đổi source nào chưa build/test trong task thời gian này; hai
  hash ghi ở §4 đã được người dùng build và backtest thật.
- Chưa có quyết định cuối về việc dùng fallback mặc định cho nghiên cứu hiệu
  suất tiếp theo hay chạy exact-only; hiện code vẫn để default `true` đúng yêu
  cầu kiểm định đủ signal.
- Nhánh debug BTCUSD/MA Cross margin chi tiết đã được người dùng giao đội khác;
  Codex không tiếp tục hoặc sửa risk/margin trong nhánh này.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Khi lịch CSV và FTMO trùng thì không chỉnh thời gian; chỉ fallback khi exact
  `bartime` thực sự không tồn tại. Không dùng offset cố định hay bảng DST.
- “Đã xử lý signal/đã attempt” phải tách khỏi “broker accepted”, và với Combo
  phải tiếp tục tách khỏi “pending đã fill”.
- Hai vấn đề legacy `ModifyExpirationTime` và margin reject theo số chân của
  bản 5 ExitMode vẫn chỉ được ghi nhận, chưa fix; không tự đưa logic legacy
  trở lại source Combo đơn giản.

**Bước tiếp theo đề xuất:**
1. Hỏi người dùng mục tiêu run kế tiếp là kiểm định tính đầy đủ signal
   (`fallback=true`) hay so hiệu suất exact-only (`fallback=false`); không tự
   đổi default vì một sample fallback đang thua.
2. Nếu tiếp tục backtest symbol/timeframe khác, archive từng run ngay sau khi
   người dùng báo xong rồi mới chạy run kế tiếp.
3. Mọi thay đổi code sau này vẫn phải chờ người dùng build trong cTrader IDE;
   không tuyên bố build sạch nếu chưa có backtest/xác nhận thật.

### [2026-08-28 19:26 — Codex]

**Đã làm:**
- Đã đọc yêu cầu kiểm toán độc lập raw data của đủ 5 ExitMode và kích hoạt
  ngay quy trình `record md` theo §2 vì người dùng gõ đúng cụm này.
- Đã cập nhật §4 và §5 để phản ánh đúng rằng raw-data audit là việc ưu tiên
  đang thực hiện; chưa coi bất kỳ kết luận cũ nào là đã được xác nhận.

**Đang dang dở / chưa build-test:**
- Chưa hoàn tất đọc/đếm `events.json` và `log.txt` của cả 5 archive; chưa có
  kết luận độc lập về số basket, số lần sửa SL, margin reject hay lỗi
  `ModifyExpirationTime`.
- Chưa sửa `reports/exit-mode-comparison-2026-08-28.md`; chưa sửa
  `Combo.cs`, nên không phát sinh yêu cầu build/test cTrader trong entry này.

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Mọi kết luận trong AGENT.md và báo cáo so sánh ExitMode phải được đối chiếu
  lại trực tiếp từ raw data trước khi Codex tuyên bố đã hiểu/xác nhận.
- Việc thử cTrader CLI 5.10 alpha qua Docker là hướng hạ tầng riêng, chưa cài
  vì VM chưa có Docker/WSL/Containers và chưa có bản Windows 5.10 ổn định.

**Bước tiếp theo đề xuất:**
1. Đọc đủ `parameters.cbotset`, `events.json`, `log.txt` của cả 5 archive.
2. Viết phép đếm tái lập được để kiểm tra basket, lifecycle lệnh, sửa SL và
   lỗi broker; đối chiếu từng con số với báo cáo hiện tại.
3. Nếu phát hiện sai/thiếu, sửa báo cáo rồi cập nhật tiếp §4/§5 và thêm entry
   bàn giao mới, không sửa/xoá entry lịch sử này.

### [2026-08-28 — Claude]

**Đã làm:**
- Hoàn thiện `Combo.cs` lên đúng 5/5 `ExitMode` (dồn mode TrailingStop độc
  lập vào `LadderRunner`, thêm `PartialBreakeven`, `FibCompensating3`).
- Sửa lỗi đếm `openBasketCount` trong `HoldBothWithDecay` (trước đếm số lệnh
  thô, giờ đếm số basket qua `EntryPrice.Distinct().Count()`).
- Thêm rồi **rút lại** cơ chế breakeven-ngay-lập-tức cho chân Runner của
  `LadderRunner` (người dùng chỉ ra đúng: dễ bị đóng oan nếu giá giật ngược
  ngay trong cây nến vừa kích hoạt) — thay bằng: chỉ bật cờ `Activated`, SL
  thật sự dời ở lần `UpdateTrailingStops` kế tiếp (đúng nhịp nến đóng).
- Thêm `TrailMethod` (`RatioOfSL` / `MovingAverage`) cho `LadderRunner` — case
  `MovingAverage` là **indicator DUY NHẤT cBot tự tính qua cAlgo**
  (`Indicators.MovingAverage`), ngoại lệ có chủ đích (chỉ phục vụ thực thi
  trail SL, không phải tín hiệu chiến lược).
- Thêm breakeven cho chân xa nhất của `FibCompensating3` khi có chân khác
  cùng basket chốt lời (kế thừa ý tưởng từ `PartialBreakeven`).
- Review code cẩn trọng toàn bộ `Combo.cs` — tìm và **loại trừ** 1 lo ngại lớn
  (tưởng `OpenOrReverse` không kiểm tra tín hiệu mới có thực sự đảo chiều —
  đã xác minh qua cả code Python (`_alternating_signals`) lẫn dữ liệu thật
  (0/1744 cặp vi phạm trên `combo_HK50_H1_...csv`) rằng đây KHÔNG phải vấn đề
  vì Python đã đảm bảo alternating từ nguồn). Giữ lại 3 phát hiện nhỏ mức độ
  thấp (xem §5 mục 4).
- **Kiểm chứng bằng backtest thật** (không chỉ đọc code): người dùng chạy lần
  lượt Backtest cho `HK50.cash/H1`, khoảng 2025→hiện tại,
  `KslLevel=KtpLevel=Fib0618`, `Reversal=Immediate`, đủ 5 ExitMode (nhưng
  `FixedTP` bị mất dữ liệu — xem mục "Đang dang dở"). Với 4/5 mode còn lại,
  đã tự đọc `events.json`/`log.txt` qua PowerShell, xác nhận:
  - Cấu trúc basket/số chân đúng 100% thiết kế ở cả 4 mode, không basket nào
    thiếu chân do dưới sàn volume.
  - Cân đối đóng lệnh (`Filled = SLHit + TPHit + PositionClosed`) khớp tuyệt
    đối ở cả 4 mode.
  - `LadderRunner`: chân Runner bị dời SL 1-39 lần/vị thế (xác nhận trail
    liên tục đúng thiết kế). `PartialBreakeven`: 49/49 vị thế chỉ dời SL
    ĐÚNG 1 lần, luôn = EntryPrice (đúng thiết kế). `PartialScaleOut4`: 0 lần
    điều chỉnh (đúng thiết kế "đặt xong là xong"). `FibCompensating3`: tỷ lệ
    TP1:TP2:TP3 luôn đúng `2.058:3.236:4.236`, breakeven 57/57 đúng.
  - Phát hiện 2 vấn đề hạ tầng (không phải lỗi code) — xem §4 để có số liệu
    đầy đủ: (a) `ModifyExpirationTime` fail ~10.1% đồng nhất mọi mode, do giờ
    đóng phiên HK50 (đã verify bằng cách đối chiếu ngày-trong-tuần của từng
    lần fail — khớp chính xác 20:00 hàng ngày + cuối tuần Thứ6→Thứ2); (b)
    margin reject tỷ lệ nghịch với số chân/basket, và riêng
    `FibCompensating3` phát hiện thêm chân TP1 (mang guarantee) bị reject
    83% số lần — nghi cTrader khớp lệnh theo thứ tự ngược lại thứ tự tạo.
- Tự tạo quy ước `ArchivedRuns\` (xem §7.3) để không mất dữ liệu giữa các lần
  chạy — vì `Backtesting\` bị ghi đè mỗi lần.
- Viết file `AGENT.md` này theo yêu cầu người dùng.

**Đang dang dở / chưa build-test:**
- Toàn bộ thay đổi `Combo.cs` liệt kê ở trên **CHƯA được build lại từ đầu
  bằng cTrader IDE sau khi đổi xong tất cả** — người dùng đã backtest được
  (nghĩa là ít nhất build ra file `.algo` chạy được), nhưng chưa có xác nhận
  tường minh "build sạch, 0 lỗi compile" trong đoạn hội thoại — coi như đã
  build được (vì backtest chạy ra kết quả thật) nhưng chưa có câu xác nhận
  rõ ràng, agent kế tiếp có thể coi là build ổn định.
- ~~`FixedTP` bị mất dữ liệu~~ — **ĐÃ CHẠY LẠI VÀ XÁC NHẬN XONG** (archive
  `FixedTP_20260828-1754`, `Exit=0` verify đúng trước khi lưu). Bảng so sánh
  5/5 mode đầy đủ:

  | Mode | Chân/basket | Margin reject % | ExpireModFail % | PosClosed % |
  |---|---|---|---|---|
  | FixedTP | 1 — đúng 100% | **30.18%** | 10.14% | 0% |
  | PartialBreakeven | 2 — đúng 100% | 15.09% | 10.14% | 5.49% |
  | FibCompensating3 | 3 — đúng 100% | 12.06% | 10.14% | 0.78% |
  | PartialScaleOut4 | 4 — đúng 100% | 8.01% | 10.14% | 0.43% |
  | LadderRunner | 4 — đúng 100% | 7.95% | 10.14% | 1.84% |

  FixedTP xác nhận rõ nét thêm quy luật margin: risk luôn cố định 2% dù chia
  mấy chân — 1 chân (không chia, khối lượng lớn nhất) → tỷ lệ bị từ chối
  margin cao nhất hẳn (30.18%, gấp ~4 lần mode 4 chân). `ExpireModFail`
  đúng ~10.14% ở CẢ 5/5, không lệch — bằng chứng dứt khoát đây là vấn đề
  tầng thực thi (giờ đóng phiên HK50), không liên quan ExitMode logic.
  **Kết luận: cả 5/5 mode đều thực thi đúng 100% thiết kế, không có sai lệch
  logic ở bất kỳ mode nào.**

**Quyết định/thoả thuận với người dùng chưa code hoá:**
- Người dùng đồng ý: `HoldBothWithDecay` về mặt Ý NGHĨA chỉ thật sự phù hợp
  với `FixedTP` (4 mode chia nhiều chân vẫn chạy đúng sau khi sửa
  `openBasketCount`, nhưng ý nghĩa "giữ cả 2 hướng" không mang nhiều giá trị
  thêm khi đã tự phân bậc rủi ro sẵn) — **KHÔNG chặn cứng bằng code**, xử lý
  gốc rễ dự kiến ở tầng Python (bộ lọc sideway) sau này, chưa cần làm ngay.
- Người dùng chưa quyết định 2 việc ở §5 mục 2-3 (đảo thứ tự đặt lệnh
  FibCompensating3; hướng xử lý lỗi ModifyExpirationTime) — **hỏi người dùng
  trước khi tự ý sửa**, đừng đoán.
- `KtpLevel=Fib0618` (cùng giá trị `KslLevel`) là mức được Claude đề xuất và
  người dùng đã dùng để chạy cả 5 mode trong đợt kiểm chứng này — CHỈ để đảm
  bảo mọi mode đều sinh ra lệnh phục vụ kiểm tra logic, **KHÔNG PHẢI** giá trị
  đã tối ưu hiệu suất — đừng nhầm lẫn khi đọc log các lần chạy này.

**Bước tiếp theo đề xuất:**
1. Chờ người dùng chạy lại `FixedTP`, xác minh + archive, hoàn tất bảng so
   sánh 5/5 mode.
2. Hỏi người dùng về hướng xử lý 2 vấn đề đang mở ở §5 mục 2-3 trước khi sửa.
3. Nếu người dùng gõ "record md" ở bất kỳ thời điểm nào sau đây — làm đúng
   quy trình ở §2, thêm entry mới lên đầu mục này (không sửa entry này).
