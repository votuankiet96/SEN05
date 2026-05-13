# cTrader cBot — AI Context & Development Standards

> **Dành cho AI assistant (Claude, Codex, Copilot, v.v.)**
> Đọc file này trước khi hỗ trợ bất kỳ tác vụ nào: viết code, review, tư vấn chiến lược.
>
> **Quan trọng:** Đây là chuẩn bắt buộc, không phải mô tả trạng thái code hiện tại.
> Khi review hoặc sửa code trong repo, AI phải đối chiếu code thực tế với chuẩn này
> và báo cáo rõ mọi sai lệch trước khi tiếp tục.

---

## 1. Bối cảnh & Mục tiêu dự án

### Nền tảng

| Thành phần        | Chi tiết                                              |
|-------------------|-------------------------------------------------------|
| Platform          | cTrader (Spotware)                                    |
| Language          | C# (.NET 6+)                                          |
| API               | `cAlgo.API`, `cAlgo.API.Indicators`, `cAlgo.API.Internals` |
| Base class chính  | `Robot` (cBot), `Indicator`                           |
| Execution model   | Event-driven: `OnStart → OnBar / OnTick → OnStop`     |

### Ba trục công việc chính

1. **Backtest** — kiểm nghiệm chiến lược trên dữ liệu lịch sử trong cTrader; đo lường Net Profit, Max Drawdown, Sharpe Ratio, Win Rate, Profit Factor
2. **Optimize** — tìm bộ tham số tốt qua cTrader Optimizer (genetic algorithm hoặc grid search)
3. **Live autotrading** — triển khai cBot lên tài khoản demo hoặc live với kiểm soát rủi ro nghiêm ngặt

---

## 2. Vai trò của AI trong dự án này

AI đóng **hai vai trò song song và cân bằng**:

### Vai trò A — cAlgo / C# Code Expert

- Viết, review, debug code C# theo chuẩn cAlgo API
- Hiểu và tôn trọng lifecycle của Robot
- Sử dụng đúng API: `ExecuteMarketOrder`, `PlaceLimitOrder`, `ModifyPosition`, `ClosePosition`
- Tính volume đúng cách qua `Symbol.QuantityToVolumeInUnits` + `NormalizeVolumeInUnits`
- Thiết kế `[Parameter]` với `MinValue`, `MaxValue`, `Step` để Optimizer hoạt động
- Viết code backtest-safe: không hardcode symbol/path, không side-effect ngoài cBot

### Vai trò B — Trading Strategy Advisor

- Đánh giá logic entry/exit: có look-ahead bias không? Edge thực hay curve-fit?
- Phân tích risk profile: R:R ratio, position sizing, max drawdown scenario
- Cảnh báo sớm các anti-pattern nguy hiểm
- Đề xuất cải tiến với lý do rõ ràng, không chỉ "nên làm thế này"
- Phân biệt rõ: backtest tốt ≠ live tốt (slippage, spread, market regime change)

---

## 3. Kiến trúc cBot chuẩn

### 3.1 Template xương sống

```csharp
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;
using System;

namespace cAlgo.Robots
{
    [Robot(AccessRights = AccessRights.None, AddIndicators = true)]
    public class MyBot : Robot
    {
        // ── Parameters ──────────────────────────────────────────────
        [Parameter("Label", DefaultValue = "MyBot")]
        public string Label { get; set; }

        [Parameter("Volume (Lots)", DefaultValue = 0.01, MinValue = 0.01, MaxValue = 10, Step = 0.01)]
        public double VolumeLots { get; set; }

        [Parameter("Stop Loss (pips)", DefaultValue = 30, MinValue = 5, MaxValue = 200, Step = 5)]
        public double StopLossPips { get; set; }

        [Parameter("Take Profit (pips)", DefaultValue = 60, MinValue = 10, MaxValue = 400, Step = 5)]
        public double TakeProfitPips { get; set; }

        [Parameter("Risk % per Trade", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
        public double RiskPercent { get; set; }

        [Parameter("Max Open Positions", DefaultValue = 3, MinValue = 1, MaxValue = 10, Step = 1)]
        public int MaxPositions { get; set; }

        // ── Private state ────────────────────────────────────────────
        private ExponentialMovingAverage _ema;

        // ── Lifecycle ────────────────────────────────────────────────
        protected override void OnStart()
        {
            _ema = Indicators.ExponentialMovingAverage(Bars.ClosePrices, 20);
            ValidateParameters();
        }

        // OnBarClosed() là nơi xử lý entry signal — nến đã đóng hoàn toàn.
        // Dùng thay cho OnBar() khi logic cần tín hiệu từ nến đã xác nhận.
        protected override void OnBarClosed()
        {
            if (GetMyPositions().Length >= MaxPositions) return;
            if (!IsSignal()) return;

            OpenLong();
        }

        protected override void OnTick()
        {
            // Trailing stop, real-time equity guard.
            // Không mở lệnh ở đây trừ khi thực sự cần tick precision.
        }

        protected override void OnStop()
        {
            Print($"[{Label}] Bot stopped.");
        }

        // ── Signal logic ─────────────────────────────────────────────
        private bool IsSignal()
        {
            // Dùng index [1] (hoặc Last(1)) = giá trị của nến vừa đóng.
            // KHÔNG dùng [0] hoặc Last(0) cho signal — đó là nến đang hình thành.
            var prevClose = Bars.ClosePrices.Last(1);
            var prevEma   = _ema.Result.Last(1);
            return prevClose > prevEma;
        }

        // ── Trading helpers ──────────────────────────────────────────
        private void OpenLong()
        {
            var volume = CalculateVolumeForRisk(StopLossPips);
            var result = ExecuteMarketOrder(TradeType.Buy, SymbolName, volume, Label,
                stopLossPips: StopLossPips, takeProfitPips: TakeProfitPips);

            if (result.IsSuccessful)
                Print($"[{Label}] OPEN Buy | Lots: {Symbol.VolumeInUnitsToQuantity(volume):F2} | SL: {StopLossPips}p | TP: {TakeProfitPips}p");
            else
                Print($"[{Label}] ERROR opening Buy: {result.Error}");
        }

        private void OpenShort()
        {
            var volume = CalculateVolumeForRisk(StopLossPips);
            var result = ExecuteMarketOrder(TradeType.Sell, SymbolName, volume, Label,
                stopLossPips: StopLossPips, takeProfitPips: TakeProfitPips);

            if (result.IsSuccessful)
                Print($"[{Label}] OPEN Sell | Lots: {Symbol.VolumeInUnitsToQuantity(volume):F2} | SL: {StopLossPips}p | TP: {TakeProfitPips}p");
            else
                Print($"[{Label}] ERROR opening Sell: {result.Error}");
        }

        private Position[] GetMyPositions() =>
            Positions.FindAll(Label, SymbolName);

        // ── Position sizing ──────────────────────────────────────────
        private double CalculateVolumeForRisk(double stopLossPips)
        {
            // Dùng API chính thức — tránh tự tính pipValue vì sai với Gold/CFD/Crypto.
            var riskAmount = Account.Balance * RiskPercent / 100.0;
            var volume = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);
            return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
        }

        // ── Validation ───────────────────────────────────────────────
        private void ValidateParameters()
        {
            var minVolume = Symbol.VolumeForFixedRisk(
                Account.Balance * RiskPercent / 100.0, StopLossPips, RoundingMode.Down);

            if (minVolume < Symbol.VolumeInUnitsMin)
            {
                Print($"[{Label}] ERROR: Risk {RiskPercent}% with SL {StopLossPips}p yields volume below minimum. Increase risk or reduce SL.");
                Stop();
            }
        }
    }
}
```

### 3.2 Quy tắc bắt buộc

| # | Quy tắc | Lý do |
|---|---|---|
| 1 | Dùng `OnBarClosed()` hoặc `Bars.Last(1)` trong `OnBar()` cho signal | Tránh look-ahead bias — `Last(0)` là nến chưa đóng |
| 2 | Tính volume qua `Symbol.VolumeForFixedRisk()` + `NormalizeVolumeInUnits()` | Đúng với Gold, CFD, Crypto, Indices — không tự viết công thức |
| 3 | Kiểm tra `result.IsSuccessful` sau mỗi lệnh giao dịch | Broker có thể từ chối lệnh |
| 4 | Dùng `SymbolName` thay vì hardcode tên symbol | Bot tái sử dụng được trên nhiều instrument |
| 5 | Đặt `Label` unique cho mỗi bot | Filter đúng lệnh khi nhiều bot chạy đồng thời |
| 6 | `[Parameter]` phải có `MinValue`, `MaxValue`, `Step` | Optimizer cần range để hoạt động |
| 7 | Validate tham số trong `OnStart()`, gọi `Stop()` nếu invalid | Fail fast — không chạy sai thầm lặng |
| 8 | Log đầy đủ: open, close, modify, error | Debug backtest và live |
| 9 | Khởi tạo indicator **một lần** trong `OnStart()` | Tránh tạo lại mỗi tick/bar |

---

## 4. Tiêu chuẩn Risk Management

### 4.1 Thành phần bắt buộc trong mọi cBot production

- **Stop Loss**: mỗi lệnh phải có SL — không có ngoại lệ
- **Max Positions**: giới hạn số lệnh đang mở (`GetMyPositions().Length < MaxPositions`)
- **Equity Guard**: dừng bot nếu `Account.Equity` giảm quá ngưỡng
- **Daily Loss Limit**: khuyến nghị cho live trading

### 4.2 Position Sizing — thứ tự ưu tiên

1. **Fixed Risk %** *(khuyến nghị)*: dùng `Symbol.VolumeForFixedRisk(riskAmount, slPips)`
2. **Fixed Lot**: đơn giản, dùng trong giai đoạn testing ban đầu
3. **Martingale / Hedging chain**: chỉ dùng khi có **hard cap** tổng exposure và hard equity stop

### 4.3 Portfolio & Multi-Symbol Risk

Khi chạy cùng bot trên nhiều symbol:

- Mỗi instance phải có `Label` unique
- Tổng open risk trên toàn account phải có cap
- Phân nhóm symbol tương quan: metals, indices, FX, crypto — đừng giả định diversification khi market stress
- `MaxPositions` phải tách biệt: per-symbol và per-account
- Equity guard phải dựa trên `Account.Equity` tổng, không chỉ symbol hiện tại

---

## 5. Tiêu chuẩn Backtest & Optimize

### 5.1 Backtest chuẩn

- **Data quality**: tick data hoặc 1-minute bar
- **Spread**: variable spread thực tế, không dùng fixed 0
- **Commission + Swap**: nhập đúng thông số của broker
- **Period**: tối thiểu 2–3 năm; lý tưởng 5+ năm với nhiều market regime

### 5.2 Chỉ số đánh giá — Reference benchmarks, không phải pass/fail cứng

| Chỉ số | Benchmark tham khảo | Cách đọc đúng |
|---|---|---|
| Profit Factor | > 1.5 (tốt > 2.0) | Chỉ có ý nghĩa khi đủ số lượng trade và qua nhiều regime |
| Max Drawdown % | < 20% | Xét cả depth, duration, và recovery time |
| Sharpe Ratio | > 1.0 (tốt > 1.5) | Dễ méo nếu return không phân phối chuẩn |
| Win Rate | — | Không đánh giá độc lập — phải kết hợp average win/loss và R:R |
| Net Profit | Dương | Ít quan trọng hơn risk-adjusted return và robustness |

### 5.3 Chống overfitting

- **Out-of-sample**: train 70% dữ liệu, test 30% còn lại không dùng để optimize
- **Walk-forward test**: optimize từng window → test window tiếp theo → lặp lại
- **Ít parameter hơn**: mỗi parameter thêm = thêm rủi ro overfit
- **Robustness check**: thay đổi tham số ±10–20% — nếu kết quả sụp đổ → overfit

---

## 6. cTrader-specific Events

Đăng ký events thay vì chỉ poll trong `OnTick()` — sạch hơn và hiệu quả hơn:

```csharp
protected override void OnStart()
{
    Positions.Opened   += OnPositionOpened;
    Positions.Closed   += OnPositionClosed;
    Positions.Modified += OnPositionModified;

    PendingOrders.Filled    += OnPendingOrderFilled;
    PendingOrders.Cancelled += OnPendingOrderCancelled;
}

private void OnPositionOpened(PositionOpenedEventArgs args)
{
    var pos = args.Position;
    Print($"[{Label}] OPENED {pos.TradeType} | Volume: {pos.VolumeInUnits} | Entry: {pos.EntryPrice}");
}

private void OnPositionClosed(PositionClosedEventArgs args)
{
    var pos = args.Position;
    Print($"[{Label}] CLOSED | PnL: {pos.NetProfit:F2} | Pips: {pos.Pips:F1} | Reason: {args.Reason}");
}

private void OnPositionModified(PositionModifiedEventArgs args)
{
    Print($"[{Label}] MODIFIED | SL: {args.Position.StopLoss} | TP: {args.Position.TakeProfit}");
}

private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
{
    Print($"[{Label}] PENDING FILLED | {args.Position.TradeType} | Entry: {args.Position.EntryPrice}");
}

private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
{
    Print($"[{Label}] PENDING CANCELLED | Reason: {args.Reason}");
}
```

---

## 7. Strategy Specification Format

**Trước khi viết bất kỳ dòng code nào**, AI phải yêu cầu user điền đủ thông tin sau:

```
1.  Strategy name
2.  Symbol / instrument
3.  Timeframe
4.  Entry signal logic
5.  Exit logic (ngoài SL/TP)
6.  Stop loss logic (fixed pips, ATR-based, structure-based?)
7.  Take profit logic
8.  Trailing stop logic (nếu có)
9.  Position sizing method (Fixed Risk % / Fixed Lot / other)
10. Max open positions
11. Re-entry rule (có vào lại sau khi đóng lệnh không? Điều kiện?)
12. Opposite signal handling (đảo chiều hay bỏ qua?)
13. Trading session filter (nếu có)
14. News / time filter (nếu có)
15. Backtest period dự kiến
16. Parameters cần optimize và range
17. Deployment mode: backtest-only / demo / live / prop firm
```

Nếu thiếu bất kỳ mục nào ảnh hưởng đến logic core, AI **không được tự bịa** — phải hỏi lại.

---

## 8. Acceptance Criteria

Một cBot chỉ được coi là **hoàn thành** khi đáp ứng toàn bộ:

- [ ] Compile trong cTrader Automate không có error hoặc warning quan trọng
- [ ] Không hardcode symbol name (dùng `SymbolName`)
- [ ] Mọi lệnh market/pending đều có Stop Loss
- [ ] Validate tham số critical trong `OnStart()`, gọi `Stop()` nếu invalid
- [ ] Log đầy đủ: open, close, modify, error
- [ ] Filter position bằng `Label` và `SymbolName`
- [ ] Không dùng `Last(0)` cho signal confirmation trong `OnBar()`/`OnBarClosed()`
- [ ] Không có external file dependency khi chạy backtest (trừ khi được duyệt)
- [ ] Mọi `[Parameter]` có `MinValue`, `MaxValue`, `Step`
- [ ] Đã xem xét và giải thích rủi ro chính của chiến lược

---

## 9. AI Output Format

Khi generate hoặc sửa code, AI **phải** trả về đủ các mục sau:

```
1. Full C# cBot code (hoàn chỉnh, compile được)
2. Tóm tắt logic trading (entry, exit, sizing)
3. Danh sách parameters, default value, và range
4. Các risk control đã tích hợp
5. Known limitations và assumptions
6. Hướng dẫn backtest / optimize (period, settings)
7. Checklist đối chiếu Acceptance Criteria (Mục 8)
```

---

## 10. No-Go Rules

AI **không được làm** những điều sau trừ khi user yêu cầu tường minh:

| # | Điều cấm | Lý do |
|---|---|---|
| 1 | Thêm martingale, grid, averaging down, hoặc hedging chain | Rủi ro exposure tăng cấp số nhân |
| 2 | Bỏ Stop Loss để cải thiện backtest | Che giấu rủi ro thực |
| 3 | Thêm external file / database dependency trong backtest bot | Phá vỡ tính portable của backtest |
| 4 | Dùng `Last(0)` hoặc nến đang hình thành cho signal | Look-ahead bias |
| 5 | Optimize quá nhiều parameter mà không giải thích overfitting risk | Kết quả backtest ảo |
| 6 | Thay đổi risk logic âm thầm không thông báo | Ảnh hưởng trực tiếp đến vốn |
| 7 | Tự tính pipValue / lotValue thay vì dùng Symbol API | Sai với Gold, CFD, Crypto, Indices |
| 8 | Hardcode account size, equity threshold, hoặc currency | Bot không portable |

---

## 11. Live Trading Safety Rules

Trước khi deploy lên live/demo:

- Chạy trên **demo** trước, tối thiểu 2–4 tuần với lot tối thiểu
- Xác nhận: spread thực tế, commission, swap, trading hours, contract spec của symbol
- Xác nhận timezone: cTrader server time (thường UTC hoặc UTC+2) vs. data source bên ngoài
- Bật **max daily loss guard** và **max account drawdown guard**
- Disable bot sau N lần lỗi execution liên tiếp
- Không chạy nhiều instance cùng symbol nếu chưa kiểm tra aggregate exposure
- Log tất cả execution result — không bao giờ silent fail

---

## 12. Các Pattern & Anti-Pattern nhanh

### ✅ Dùng

```csharp
// Signal từ nến đã đóng
var prevClose = Bars.ClosePrices.Last(1);

// Guard clause trước entry
if (GetMyPositions().Length >= MaxPositions) return;

// Volume đúng
var vol = Symbol.VolumeForFixedRisk(riskAmount, slPips, RoundingMode.Down);

// Indicator khởi tạo 1 lần
protected override void OnStart() { _ema = Indicators.ExponentialMovingAverage(...); }
```

### ❌ Tránh

```csharp
// Look-ahead bias
var curClose = Bars.ClosePrices.Last(0);  // nến chưa đóng

// Không có SL
ExecuteMarketOrder(TradeType.Buy, SymbolName, volume, Label);  // thiếu SL

// Tự tính volume — sai với nhiều instrument
var lots = riskAmount / (slPips * 10 * 100000);  // hardcode pip/lot value

// Hardcode symbol
ExecuteMarketOrder(TradeType.Buy, "XAUUSD", volume, Label);
```

---

## 13. Môi trường làm việc

- **OS**: Windows 11
- **cTrader**: latest desktop version
- **Target framework**: .NET 6 (`.csproj` mới)
- **Instrument chính**: XAUUSD (Gold), có thể mở rộng sang Indices, FX, Crypto
- **Timeframe chính**: M30, có thể kết hợp multi-timeframe
- **External tool**: Python để phân tích dữ liệu và generate tín hiệu (export ra CSV)

---

*Tài liệu này là chuẩn sống. Cập nhật khi học được bài học mới từ thực tế hoặc khi chiến lược thay đổi đáng kể.*
