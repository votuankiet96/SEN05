# Phân tích 2 cBot backtest trên VM-BO20 (10.11.12.20) — Combo & MA Cross

Đọc trực tiếp qua WinRM (Invoke-Command, TrustedHosts đã thêm 10.11.12.20 vào client này).
Nguồn: `Documents\cAlgo\Sources\Robots\{Combo\Combo\Combo.cs, MA Cross\MA Cross\MA Cross.cs, CLAUDE.md}` trên VM-BO20.
Ngày đọc: 2026-09-17.

## Bối cảnh hạ tầng xác nhận thêm

- VM-BO20 = máy Windows có cài cTrader Desktop thật (bản 5.9.10.52700 lúc viết), dùng để backtest/optimize qua GUI.
- **VM-OG8 = 10.11.12.8** — CÙNG địa chỉ IP với Redis đã khảo sát (DB0/DB1)! Ubuntu, chạy `core_python`, đây là nơi sinh cả CSV (cho backtest trên VM-BO20, mount qua SSHFS ổ `Z:`) lẫn tín hiệu Redis (cho live/OF). Tức là **cùng 1 codebase chiến lược Python** (`og_program/core_python/strategies/combo.py`, v.v.) chỉ khác kênh xuất — CSV để backtest GUI, Redis DB1 để OF đọc live.
- Cả 2 project (`Combo/`, `MA Cross/`) đều là project cTrader Automate chuẩn (net6.0 + package `cTrader.Automate`), mỗi cái có `.sln`/`.csproj`/`.cs`.
- Folder `Robots/` có cả `.claude/`, `CLAUDE.md` (61KB, đã đọc hết), `AGENT.md` (245KB, CHƯA đọc — log chi tiết hơn, có thể đọc sau nếu cần) — tức 2 bot này từng được phát triển với sự hỗ trợ của Claude Code trước đó, có lịch sử quyết định dài.
- `bo_workflow/` = hệ thống Python riêng (`api_engine`, `cli_engine`, `core_engine`, `reports`, `tests`) tự động hoá backtest/optimize qua `ctrader-cli` — KHÔNG phải bản thân bot, hiện tạm dừng (người dùng chủ động quay lại chạy GUI thủ công 2026-08-27). Chưa đọc sâu phần này.

## Input signal (CSV) — khác cấu trúc Redis DB1

- **Combo.cs** đọc CSV cột: `bartime, atr, entry, signal` (signal ∈ {1,-1}).
- **MA Cross.cs** đọc CSV cột: `bartime, atr, signal` — KHÔNG có `entry` (vì dùng Market Order, không cần giá đặt trước).
- **Không có cột stopLoss/takeProfit trong CSV của cả 2 bot** — SL/TP hoàn toàn do bot tự tính (xem dưới), KHÔNG lấy từ nguồn tín hiệu.

## Logic Entry

- **Combo**: `PlaceStopOrder` (pending STOP) tại đúng `signal.EntryPrice`, `ProtectionType.Absolute` cho SL/TP tuyệt đối. Pending order tự huỷ sau **3 nến chart chưa khớp** (đếm nến qua `OnBarClosed`, KHÔNG dùng timestamp tuyệt đối — khác hẳn field `expirationTimestamp` (epoch giây) có sẵn trong Redis HASH).
- **MA Cross**: `ExecuteMarketOrder` (khớp ngay), SL/TP truyền vào dạng **pips tương đối** (`stopLossPips`, `takeProfitPips`), không phải giá tuyệt đối.
- Cả 2 chỉ vào lệnh tại **tick khả dụng đầu tiên sau `AvailableTime` (= bartime + độ dài nến danh nghĩa)** — không giới hạn thời gian chờ nếu thị trường đóng cửa lâu. Đây tương đương khái niệm `valid_from` trong Redis HASH.

## Logic Exit (SL/TP) — PHÁT HIỆN QUAN TRỌNG NHẤT

**SL/TP không lấy từ tín hiệu — bot tự tính từ ATR:**
```
stopLossPrice   = entryPrice - direction × KSL_ratio × ATR
takeProfitPrice = entryPrice + direction × KTP_ratio × ATR
```
- `KSL_ratio`/`KTP_ratio` lấy từ 2 enum riêng biệt `SlFibLevel`/`TpFibLevel`, mỗi enum 10 mức, công thức DUY NHẤT (chốt 2026-09-16, lần sửa thứ 3): **φ^(n/2)**, φ=1.618034 (căn bậc hai của tỷ lệ vàng nhân đều giữa 2 mức liên tiếp).
  - `SlFibLevel` (mặc định `Fib1000`=1.0×ATR): 0.618, 0.786, 1.000, 1.272, 1.618, 2.058, 2.618, 3.330, 4.236, 5.388
  - `TpFibLevel` (mặc định `Fib2618`=2.618×ATR, KHÔNG còn mức nào <1×ATR — chủ đích tránh chốt lời non): 1.000, 1.272, 1.618, 2.058, 2.618, 3.330, 4.236, 5.388, 6.854, 8.719
- **Suy luận quan trọng cho OF**: field `stopLoss`/`takeProfit` trong Redis HASH (đã khảo sát, xác nhận là "placeholder") rất có thể sẽ được OG tính đúng theo公 CHÍNH công thức `entry ± ratio×ATR` này một khi "walk-forward optimizer thật" (nhắc ở tài liệu bối cảnh ban đầu) được xây xong — ratio (KSL/KTP) chính là tham số mà quy trình walk-forward trong CLAUDE.md đang tối ưu. Cho tới lúc đó, OF nên cân nhắc tự tính SL/TP từ `atr` (đã có sẵn trong Redis HASH) bằng công thức này thay vì tin trực tiếp field `stopLoss`/`takeProfit` hiện tại.

## Position sizing (Risk Management — tách biệt khỏi Position Management)

```
riskAmount      = Account.Balance × RiskPercent% (mặc định 1.0%)
requestedVolume = riskAmount / (stopLossPips × pipValue)
volume          = Symbol.NormalizeVolumeInUnits(min(requestedVolume, VolumeInUnitsMax), RoundingMode.Down)
→ bỏ qua tín hiệu nếu volume < VolumeInUnitsMin
```

- **`pipValue` tính LIVE tại thời điểm đặt lệnh, KHÔNG dùng `Symbol.PipValue`** — tài liệu cTrader xác nhận `Symbol.PipValue` là snapshot tỷ giá đóng băng lúc `OnStart`, không cập nhật → sai dần với symbol quote khác account currency (EUR/JPY/HKD...) trong run dài. Thay vào đó dùng `Symbol.QuoteAsset.Convert(Account.Asset, ...)` tính lại mỗi lần.
  - Dùng **probe 1,000,000 đơn vị rồi chia lại** thay vì convert trực tiếp 1 pip — vì `Asset.Convert` làm tròn theo số chữ số của tiền tài khoản (USD=2 chữ số) → quy đổi 1 pip JPY (~0.0064 USD) ra 0.00, khiến volume=0 (đã từng gây bug thật: JP225 placed=0/failed=33 lần build đầu).
  - **OF cần áp dụng đúng nguyên tắc này** khi tính pip value/giá trị volume qua cTrader Open API — không cache tỷ giá, luôn lấy giá live tại thời điểm đặt lệnh.
- **Margin cap đã bị GỠ (2026-09-11)**: từng có `CapVolumeByMargin`/`MaxMarginPercent` chặn volume theo margin ước tính, nhưng bị gỡ vì `Symbol.GetEstimatedMargin` trong backtest (khi API không bật được `PreciseConversion` — tỷ giá lịch sử thật) ước tính margin bằng **giá gần CUỐI KỲ backtest** → look-ahead bias (đo thật: cùng 1 lệnh US30, chỉ đổi ngày kết thúc test → volume đổi 14.85/15.06/15.77/14.07). **Lý do rủi ro margin/notional thật vẫn đúng** (1 lệnh SL hẹp có thể khoá ~110% Equity dù risk% trên giấy rất nhỏ) — chỉ là cách tính cũ bị lỗi do hạn chế của backtest. **OF chạy live có margin thật real-time (không bị look-ahead)** — đây là điểm OF có thể/nên làm tốt hơn bot backtest, không nên bỏ qua hoàn toàn.

## Exposure / Reversal (áp dụng khi consume tín hiệu Redis DB1 live)

- Tối đa **1 exposure ròng mỗi symbol dưới 1 `Label`** tại một thời điểm (position ĐANG MỞ hoặc pending order ĐANG CHỜ, xác định qua `Positions`/`PendingOrders` thật của cTrader, không dùng state machine riêng).
- Tín hiệu **cùng hướng** với exposure đang có → bỏ qua (không vào thêm).
- Tín hiệu **ngược hướng** → đóng position / huỷ pending order cũ trước, rồi mở lệnh mới (reversal).
- Comment code xác nhận: **OG's `combo.py` không còn tự đảm bảo "không lặp hướng liên tiếp"** (đã bỏ state machine `_alternating_signals` phía OG) — trách nhiệm lọc tín hiệu trùng hướng/xử lý đảo chiều **hoàn toàn thuộc về bên tiêu thụ tín hiệu** (bot backtest ở đây, và sẽ là OF ở live). → **OF bắt buộc phải tự implement logic reconcile-exposure tương tự**, không thể giả định OG đã lọc sẵn.
- MA Cross từng có bug thật do THIẾU bước kiểm tra này (phát hiện 2026-09-04): mở đồng thời 2 vị thế ngược hướng, free margin tụt còn ~$5,525 thay vì ~$10,000 — bằng chứng margin bị khoá kép. Đã sửa bằng đúng cơ chế reconcile ở trên.

## Position Management (circuit breaker cấp tài khoản — tách biệt hoàn toàn khỏi Risk Management)

- Daily Loss Limit, Max Total Drawdown, Max Consecutive Losses — chuẩn tham chiếu FTMO (mặc định 5% ngày / 10% tổng), **đều đang tắt mặc định** (`Enable...=false`) nhưng có sẵn khung nếu OF muốn dùng làm tham chiếu thiết kế circuit-breaker riêng.
- Khi breach: `ForceCloseAll()` — huỷ hết pending order + đóng hết position dưới đúng `Label`/`SymbolName` của bot đó, khoá giao dịch tới hết ngày UTC (daily) hoặc vĩnh viễn cho run đó (max drawdown).

## ⚠️ Cảnh báo quan trọng: đừng nhầm tài liệu Ý ĐỊNH với CODE THẬT

`CLAUDE.md` có 1 mục lớn "Thiết kế Exit cho Combo" mô tả catalog **6 phương án exit phức tạp** (`ExitMode` enum: `FixedTP`, `PartialScaleOut4`, `LadderRunner`, `PartialBreakeven`, `FibCompensating3`, + cơ chế `ReversalMode`/`HoldBothWithDecay`, basket-linking...). **Đã tự xác nhận trong chính file (2026-09-03): code thật đang chạy KHÔNG HỀ có các enum/cơ chế này** — code thật (đúng như tôi vừa đọc trực tiếp) chỉ có ĐÚNG 1 kiểu exit (SL/TP cố định tính 1 lần từ ATR×ratio, tương đương mô tả `FixedTP`) và ĐÚNG 1 kiểu reversal (đóng ngay khi có tín hiệu ngược, tương đương `Immediate`). Nguyên nhân chưa xác định (có thể catalog chỉ là kế hoạch chưa từng merge). **Kết luận: toàn bộ mục "Thiết kế Exit"/"Catalog 6 phương án" trong CLAUDE.md là tài liệu THIẾT KẾ/Ý ĐỊNH, không phản ánh hành vi thật — OF nên dựa vào chính source code (.cs) đã đọc trực tiếp ở trên, không dựa vào catalog đó.**

## Chưa đọc / có thể cần đọc thêm nếu cần sâu hơn

- `AGENT.md` (245KB) — log chi tiết hơn CLAUDE.md, chưa đọc do ưu tiên tốc độ.
- `bo_workflow/` (api_engine, cli_engine, core_engine, reports, tests) — hạ tầng Python tự động hoá backtest/optimize, có nhiều báo cáo (`reports/lotsize-sizing-work-summary-2026-09-08.md`, `reports/phase-2-research-and-optimization-design.md`...) được code Combo.cs/MA Cross.cs trực tiếp tham chiếu — chưa đọc nội dung.
- Chưa xem `events.json`/`report.html` mẫu (kết quả backtest thật) để đối chiếu số liệu thực tế với log `RISK_DETAIL` in ra.
