# cBot Development Rules — cTrader Backtest, Optimize & Trading Logic

> **Dành cho AI assistant.**
> Đây là bộ quy tắc dựa trên cách cTrader hoạt động thực tế — không phải lý thuyết chung.
> Trước khi hỗ trợ viết hoặc review bất kỳ cBot nào, đọc và áp dụng toàn bộ tài liệu này.
> **Ưu tiên tuyệt đối: capital preservation trước, profit sau.**

---

## 1. cTrader Backtester — Cách hoạt động thực tế

### 1.1 Hai chế độ dữ liệu

| Chế độ | Độ chính xác | Tốc độ | Khi nào dùng |
|--------|-------------|--------|--------------|
| **Tick data** | Cao nhất — dùng historical spread thực, `OnTick()` fired đúng mỗi tick | Chậm | Validate cuối cùng trước deploy |
| **1-min bar data** | Thấp hơn — chỉ có open price, spread cố định do user nhập, `OnTick()` chỉ fire 1 lần/phút tại bar close | Nhanh | Coarse scan ban đầu khi optimize |

**Hệ quả quan trọng khi dùng 1-min bar:**
- `OnTick()` chỉ được gọi **một lần mỗi phút** (tại bar close) — code dựa vào tick frequency sẽ behave khác hoàn toàn so với live
- Pending orders fill tại giá chính xác — không có gap simulation realistic
- Spread là fixed value do người dùng nhập — nếu nhập thấp hơn thực tế, kết quả bị inflate

### 1.2 Các tham số cấu hình backtest

Backtester cho phép set: Starting Capital, Commission (per million), Spread (fixed hoặc range min/max), Date range.

**Lưu ý khi code:**
- Commission trong cTrader tính theo **per million** — không phải per lot. Kiểm tra đúng thông số broker trước khi backtest
- Nếu cBot có parameter `MaxSpreadPercent` hoặc spread filter: phải đảm bảo spread trong backtest settings ≤ ngưỡng đó, nếu không bot sẽ không trade gì cả

### 1.3 Report metrics có sẵn sau backtest

cTrader tạo HTML report với đầy đủ thông tin. Các metric chính:
- Net Profit, Profit Factor, Sharpe Ratio, Sortino Ratio
- Max Balance Drawdown (%), Max Equity Drawdown (%)
- Winning Trades, Losing Trades, Total Trades, Average Trade
- Swaps, Commissions (tổng phí)
- Equity curve chart

---

## 2. cTrader Backtester — Giới hạn cụ thể (phải biết khi code)

### 2.1 Những gì backtester KHÔNG simulate được

| Giới hạn | Hệ quả với code |
|----------|----------------|
| **Không có slippage simulation** | Pending order (Buy Stop/Sell Stop) fill chính xác tại target price — trên live sẽ bị gap fill tệ hơn nhiều khi news |
| **Không simulate partial fill** | Order lớn luôn fill 100% — thực tế không đúng với instrument liquidity thấp |
| **`OnTick()` với 1-min data = 1 lần/phút** | Code dùng tick để trailing SL hoặc equity guard sẽ react chậm hơn thực tế |
| **`LoadMoreHistory()` không hoạt động trong backtest** | Nếu code gọi `LoadMoreHistory()` trong backtest → có thể loop vô hạn; phải guard |
| **Multi-symbol optimization không được hỗ trợ** | Bot dùng nhiều symbol không thể optimize qua Optimizer built-in |
| **Load symbol phụ trong `OnStart()` khi optimize** | Gây crash (NullReferenceException) — phải handle null hoặc guard |

### 2.2 Multi-Symbol Backtesting — Gotchas đặc thù

- Chạy multi-symbol backtest trên cùng period có thể cho kết quả **khác nhau** tùy vào symbol nào được chọn làm base chart — đây là known bug/limitation của cTrader
- Optimization không chạy được cho multi-symbol bot → nếu cần optimize bot multi-symbol: phải chạy từng symbol riêng lẻ và so sánh thủ công

### 2.3 Visual Mode vs Normal Mode

- **Visual mode**: Chạy từng tick/bar theo real-time simulation có thể slow down/speed up — dùng để debug, kiểm tra logic xử lý từng lệnh
- **Normal mode**: Chạy hết toàn bộ period ngay lập tức — dùng để lấy kết quả thống kê

---

## 3. cTrader Optimizer — Cách hoạt động thực tế

### 3.1 Hai mode optimize

**Grid Search (Exhaustive):**
- Tạo grid tất cả combination của các parameter và chạy tất cả
- Thorough nhưng cực kỳ chậm với tick data và nhiều parameter
- Với 5 parameter × 10 values mỗi cái = 100,000 backtests — có thể mất hàng ngày đến hàng tuần

**Genetic Algorithm (mặc định, khuyến nghị):**
- Mô phỏng natural selection: mỗi pass = một "cá thể", parameter = "gene"
- Bắt đầu với population randomized, sau đó crossover + mutation qua nhiều iteration
- Dừng khi fitness score **stagnate** (không cải thiện nữa)
- **Heuristic** — không deterministic: chạy lại cho kết quả khác nhau mỗi lần
- Internal parameters (population size, mutation rate, elite%, tournament size%) **không thể thay đổi**

**Workflow đúng:**
1. Dùng 1-min bar data cho coarse scan ban đầu (nhanh)
2. Thu hẹp range → chạy lại với tick data để verify (chính xác)
3. Validate kết quả trên out-of-sample period

### 3.2 Fitness Criteria có sẵn trong Optimizer

cTrader cung cấp các built-in criteria và tùy chọn Custom:

| Criterion | Nên dùng khi nào |
|-----------|-----------------|
| Net Profit | Không nên dùng đơn lẻ — dễ bị inflate bởi vài trade may mắn |
| Profit Factor | Tốt — cân bằng giữa tổng win và tổng loss |
| Max Equity Drawdown % | Dùng để minimize risk — thường kết hợp với criterion khác |
| Sharpe Ratio | Tốt cho risk-adjusted return |
| Sortino Ratio | Tốt hơn Sharpe nếu distribution lệch (downside risk only) |
| **Custom** | Viết `GetFitness()` khi muốn kết hợp nhiều tiêu chí |

**Khuyến nghị:** Không optimize theo Net Profit đơn thuần — dễ overfit. Dùng Profit Factor hoặc Custom function kết hợp điều kiện tối thiểu (ví dụ: trade count > 50).

### 3.3 Custom Fitness Function — Khi nào và cách dùng

Khi built-in criteria không đủ, override `GetFitness()` trong code:

```csharp
protected override double GetFitness(GetFitnessArgs args)
{
    // Guard: quá ít trade → kết quả không tin được, trả về giá trị thấp nhất
    if (args.TotalTrades < 30)
        return double.MinValue;

    // Ví dụ: tối đa hóa Profit Factor, nhưng chỉ khi drawdown chấp nhận được
    if (args.MaxEquityDrawdownPercentages > 25)
        return double.MinValue;

    return args.ProfitFactor;
}
```

**Toàn bộ properties có trong `GetFitnessArgs`:**

| Property | Ý nghĩa |
|----------|---------|
| `NetProfit` | Tổng lợi nhuận ròng |
| `ProfitFactor` | Tổng win / tổng loss |
| `MaxBalanceDrawdown` | Max balance DD (absolute) |
| `MaxBalanceDrawdownPercentages` | Max balance DD (%) |
| `MaxEquityDrawdown` | Max equity DD (absolute) |
| `MaxEquityDrawdownPercentages` | Max equity DD (%) |
| `WinningTrades` | Số trade thắng |
| `LosingTrades` | Số trade thua |
| `TotalTrades` | Tổng số trade |
| `AverageTrade` | Lợi nhuận trung bình mỗi trade |
| `Swaps` | Tổng swap cost |
| `Commissions` | Tổng commission |
| `Equity` | Equity cuối kỳ |
| `History` | Tất cả trade history |
| `Positions` | Các vị thế đang mở cuối kỳ |
| `PendingOrders` | Pending orders còn lại cuối kỳ |

**Lưu ý:**
- Không có Sharpe/Sortino trong `GetFitnessArgs` — phải tự tính từ `History` nếu cần
- Luôn guard điều kiện tối thiểu (trade count, drawdown threshold) — trả về `double.MinValue` nếu không đạt để optimizer loại bỏ

---

## 4. Viết cBot code để hỗ trợ Optimization — Quy tắc cụ thể

### 4.1 Parameter types — Loại nào optimize được, loại nào không

| Type | Optimize được? | Cần có |
|------|---------------|--------|
| `int` | Có | `MinValue`, `MaxValue`, `Step` |
| `double` | Có | `MinValue`, `MaxValue`, `Step` |
| `enum` | Có (iterate qua values) | — |
| `bool` | **Không** | — |
| `string` | **Không** | — |
| `DataSeries` | **Không** | — |
| `Symbol` | **Không** | — |
| `TimeFrame` | **Không** (nhưng có thể được mặc định) | — |
| `Color`, `DateTime` | **Không** | — |

**Hệ quả:** Nếu muốn optimize một logic on/off: dùng `int` (0 = off, 1 = on) hoặc `enum` thay vì `bool`.

### 4.2 Khai báo Parameter đúng để Optimizer hoạt động

```csharp
// Đúng — optimizer có đủ thông tin để scan
[Parameter("KSL Level", Group = "Execution", DefaultValue = 2, MinValue = 1, MaxValue = 4, Step = 1)]
public int KslLevel { get; set; }

[Parameter("TP Multiplier", Group = "Execution", DefaultValue = 2.0, MinValue = 0.5, MaxValue = 5.0, Step = 0.5)]
public double TpMultiplier { get; set; }

// Sai — optimizer không thể scan bool, không có range
[Parameter("Use Filter", DefaultValue = true)]
public bool UseFilter { get; set; }  // convert sang int nếu muốn optimize

// Sai — thiếu MinValue/MaxValue/Step → optimizer không biết scan range
[Parameter("Period", DefaultValue = 14)]
public int Period { get; set; }
```

### 4.3 Những gì KHÔNG được làm trong cBot nếu cần optimize

| Không nên | Lý do |
|-----------|-------|
| Load symbol phụ trong `OnStart()` mà không guard null | Crash (NullReferenceException) khi optimizer chạy |
| Đọc/ghi file ngoài (CSV, log) trong `OnBar()`/`OnTick()` | I/O nặng × hàng nghìn passes = optimization cực chậm hoặc crash |
| Gọi `LoadMoreHistory()` mà không có max-retry guard | Infinite loop trong backtesting |
| External API call hay HTTP request trong trading logic | Timeout + crash trong optimization environment |
| Dùng `Server.Time.Now` làm seed cho random | Genetic algo đã xử lý diversity — seed cố định tốt hơn cho reproducibility |
| Quá nhiều parameter optimize cùng lúc (> 5) | Genetic algo stagnate sớm, kết quả không tin được; grid search mất hàng tuần |

### 4.4 Thiết kế parameter để optimize có ý nghĩa

- **Step phải có economic meaning:** SL multiplier step = 0.5 (không phải 0.001); period step = 1 (không phải 0.1 vô nghĩa)
- **Phân nhóm rõ:** Dùng `Group` attribute để tách signal parameters, risk parameters, execution parameters — giúp biết cái gì đang optimize cái gì
- **Không optimize risk parameters cùng signal parameters:** `RiskPercent` thay đổi sẽ distort performance comparison giữa các passes
- **Không optimize parameters không ảnh hưởng đến logic trading:** Path, Label, timezone offset, log level

---

## 5. Entry — Quy tắc và Lưu ý

### 5.1 Timing — Không được dùng dữ liệu nến chưa đóng

- **Trong `OnBar()`:** Dùng `Last(1)`, `Bars.ClosePrices.Last(1)` — `Last(0)` là nến đang hình thành, chưa confirmed
- **Tốt hơn:** Dùng `OnBarClosed()` — event fire sau khi nến đóng hoàn toàn, không nhầm lẫn được
- **Với CSV signal:** Timestamp phải so sánh với bar **close time đã đóng** của nến signal, không phải bar open của nến tiếp theo; phải quy về cùng timezone với `Server.Time` trước khi so sánh

### 5.2 Pending Order vs Market Order

| | Market Order | Pending Order (Stop/Limit) |
|---|-------------|--------------------------|
| Fill | Ngay lập tức, theo bid/ask hiện tại | Khi giá chạm target |
| Backtest accuracy | Tốt | Cẩn thận: fill đúng price, không có gap simulation |
| Slippage risk live | Có | Cao hơn nếu market gap qua entry |
| Expiry | N/A | **Phải set expiry** — không để pending vô hạn |

### 5.3 Signal deduplication — Bắt buộc

- Mỗi signal chỉ được trigger **một lần** cho một bar — phải đánh dấu signal đã xử lý
- Không để `OnBar()` trigger lại cùng signal nếu logic check trùng bar

---

## 6. Stop Loss — Quy tắc

- **Bắt buộc:** Mọi lệnh — market order và pending order — đều phải có SL. Không có ngoại lệ
- **Không nới SL sau khi vào lệnh:** Đây là vi phạm quản lý vốn cơ bản — tăng risk sau khi biết lệnh đang thua
- **SL dựa trên market structure hoặc ATR** — không phải con số cảm tính
- **SL ladder (multi-leg):** Chỉ dịch SL theo hướng có lợi (Buy: chỉ tăng; Sell: chỉ giảm) — không bao giờ dịch xa thêm
- **SL distance phải đủ xa khỏi `Symbol.StopLevel`:** cTrader từ chối lệnh nếu SL quá gần entry
- Khi dùng ATR-based SL: tính `SL = Entry ± ATR * Multiplier` từ **actual fill price**, không phải target entry

---

## 7. Take Profit — Quy tắc

- R:R tối thiểu 1:1 tính từ entry thực (actual fill) đến TP1
- **Multi-leg TP:** Toàn bộ legs trong một cluster phải dùng cùng SL tại mỗi thời điểm
- TP profile (multipliers) phải là **bảng cố định** trong code — không tính dynamic từ các điều kiện thay đổi
- Không thay đổi TP profile khi cluster đang mở — SL ladder sẽ tính BaseRange sai
- Log từng leg khi close: leg number, TP giá, PnL, SL mới của các leg còn lại

---

## 8. Capital Management — Quy tắc Code

### 8.1 Position sizing

```csharp
// Đúng — dùng Symbol API, đúng với mọi instrument
var risk   = Account.Balance * RiskPercent / 100.0;  // Balance, không phải Equity
var volume = Symbol.VolumeForFixedRisk(risk, slPips, RoundingMode.Down);
volume     = Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);

if (volume < Symbol.VolumeInUnitsMin)
{
    Print($"[{Label}] Volume below minimum — skip trade");
    return;
}
```

- Dùng **Balance** (không phải Equity) để tránh floating PnL ảnh hưởng đến sizing của lệnh tiếp theo
- Không tự viết công thức `lots = risk / (slPips * pipValue)` — pip value khác nhau theo instrument, account currency, và leverage
- Luôn check `volume >= Symbol.VolumeInUnitsMin` trước khi đặt lệnh

### 8.2 Ba lớp bảo vệ vốn bắt buộc

| Lớp | Cơ chế | Lưu ý code |
|-----|--------|-----------|
| Per-trade SL | Stop Loss trên mỗi lệnh | Đã nói ở mục 6 |
| Daily Loss Limit | Dừng trading trong ngày khi equity ngày giảm quá ngưỡng | Reset theo `Server.Time.Date`, không phải UTC cố định |
| Account DD Limit | Dừng bot khi drawdown từ peak vượt ngưỡng | Dùng `Account.Equity`, không phải `Account.Balance` để detect floating loss |

**Daily halt phải đóng hết:** Khi daily limit hit — cancel toàn bộ pending, close toàn bộ position của bot — không chỉ ngừng mở mới.

### 8.3 Position tracking

- Mọi lệnh phải được gắn `Label` unique của bot → filter bằng `Positions.FindAll(Label, SymbolName)`
- Không quản lý lệnh không có Label của mình — tránh can thiệp manual trade hoặc lệnh từ bot khác
- Khi bot restart giữa chừng: phải reconstruct state từ các lệnh đang mở — không assume trạng thái clean

---

## 9. Checklist trước khi chạy Backtest

- [ ] Data type: tick data (validate) hoặc 1-min (initial scan) — ghi rõ loại dùng
- [ ] Spread: variable spread thực tế, không phải fixed 0
- [ ] Commission và swap: nhập đúng thông số broker
- [ ] Timezone: `CsvTimeOffsetHours` (hoặc tương đương) canh đúng với `Server.Time`
- [ ] Spread filter trong bot (nếu có) phải ≤ spread nhập trong backtest settings
- [ ] Signal file cover đủ toàn bộ period backtest
- [ ] Bot compile không lỗi
- [ ] `LoadMoreHistory()` được guard nếu có dùng

## 10. Checklist trước khi chạy Optimization

- [ ] Parameters cần optimize đều là `int` hoặc `double` và có `MinValue`, `MaxValue`, `Step`
- [ ] Không optimize quá 5 parameters cùng lúc
- [ ] Risk parameters (`RiskPercent`, daily DD%) được fix riêng, không nằm trong optimize run
- [ ] Không có file I/O nặng trong `OnBar()`/`OnTick()` (sẽ làm chậm nghiêm trọng)
- [ ] Symbol phụ trong `OnStart()` được guard null nếu bot multi-symbol
- [ ] Custom `GetFitness()` có guard `TotalTrades < N → double.MinValue` để loại bỏ sparse passes
- [ ] Out-of-sample period đã được lock — không dùng để scan
- [ ] Sau optimize: validate lại top parameters trên tick data trước khi kết luận

---

*Tài liệu này dựa trên cTrader Automate API documentation và platform behavior thực tế.*
*Cập nhật khi cTrader API có thay đổi hoặc khi phát hiện behavior mới.*
