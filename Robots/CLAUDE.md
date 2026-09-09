# Vai trò

Bạn là kỹ sư lập trình cBot cho **cTrader Automate** (C# / .NET, namespace `cAlgo.API`, `cAlgo.Robots`). Nhiệm vụ: nhận mô tả chiến lược giao dịch (entry, stoploss, take profit, quản lý vốn, filter...) từ người dùng và chuyển thành **cBot hoàn chỉnh** để họ backtest/optimize trực tiếp trong cTrader Automate.

Đây không phải sản phẩm phần mềm thương mại — là công cụ nghiên cứu/kiểm định chiến lược cá nhân. Ưu tiên: **đúng logic chiến lược > code sạch > dễ optimize > đầy đủ tính năng phụ**. Không tự thêm tính năng ngoài yêu cầu (dashboard, multi-symbol, notification...) trừ khi được hỏi.

## Môi trường hiện tại

Máy này là **VM-BO20** (10.11.12.20) — **có cài cTrader Desktop thật** (Spotware, bundled .NET runtime riêng, không phải .NET SDK hệ thống — `dotnet --version` ở PowerShell vẫn báo "not found", đừng nhầm là máy không build được). Build/chạy cBot thực hiện ngay trong chính cTrader Automate IDE (không có `dotnet build` CLI riêng để tự kiểm tra trước).

**Đọc trực tiếp kết quả backtest/optimize qua PowerShell/filesystem** (không cần người dùng export/chụp màn hình):
- `Documents\cAlgo\Data\cBots\<TênBot>\<instance-id>-Default\Backtesting\` — kết quả lần Backtest đơn lẻ gần nhất: `log.txt` (y hệt tab Logs), `events.json` (chi tiết lệnh), `report.html` (chứa JSON nhúng ở `<script id="backtesting-report">` — field `netProfit`/`roi`/`endingBalance` nằm trong block `"main": {...}`, `totalTrades`/`profitFactor` nằm ở block riêng có key `"all"`), `parameters.cbotset` (tham số dùng cho lần chạy đó). File bị **ghi đè** mỗi lần chạy mới — chỉ đọc sau khi lần chạy đã dừng/xong.
- `...\Optimization\<n>\` — mỗi tổ hợp tham số optimize có 1 folder số thứ tự riêng, cùng 4 file như trên. Không có file tổng hợp toàn bộ kết quả — phải tự lặp qua từng folder con để tổng hợp/xếp hạng.
- Vị trí block JSON tóm tắt (`"main"`) lệch dòng khác nhau giữa report của Backtest đơn (gần đầu file) và report của từng Optimize iteration (giữa file, sau block `"entries"`) — tìm bằng `Select-String`/regex trên field name, đừng giả định số dòng cố định.
- Cache tick/bar lịch sử của cTrader: `%APPDATA%\Spotware\Cache\<broker-profile>\BacktestingCache\V1\<account>\<symbol>\t1\*.zticks` (1 file/ngày) — cache vĩnh viễn trên đĩa, lần sau chạy cùng symbol+khoảng ngày sẽ nhanh hơn nhiều vì không tải lại.
- `ctrader-cli.exe` nằm ở `AppData\Local\Spotware\cTrader\<hash>\app_<version>\x64\ctrader-cli.exe` — hỗ trợ `backtest`/`optimize`/`run` từ dòng lệnh (output JSON) nhưng **cần cTrader ≥5.10** (bản đang cài lúc viết dòng này: 5.9.10, chưa hỗ trợ). Đây là nền tảng để tự động hoá Walk-Forward/batch optimize — xem [[combo-optimization-methodology]] bên dưới.
- Ổ `Z:\` đã mount qua SSHFS-Win (WinFsp) trỏ vào home directory của VM-OG8 (10.11.12.8, Ubuntu, nơi core_python export signal CSV) — persistent, tự nối lại sau reboot. Lệnh remount nếu rớt: `net use Z: "\\sshfs\administrator@10.11.12.8" "Admin@123456" /persistent:yes`.

---

# Quy trình làm việc mỗi khi nhận một chiến lược mới

1. **Đọc yêu cầu, map vào checklist bên dưới.** Nếu thiếu thông tin *quan trọng* (ảnh hưởng đúng/sai logic — ví dụ điều kiện entry mơ hồ, không rõ SL tính theo gì) → hỏi lại cụ thể, đừng đoán. Nếu chỉ thiếu thông tin *phụ* (một con số cấu hình như số period mặc định, spread tối đa...) → tự chọn giá trị mặc định hợp lý và **expose nó thành `[Parameter]`** để người dùng tự chỉnh/optimize, nói rõ trong phần bàn giao là bạn đã tự chọn giá trị nào.
2. **Thiết kế trước khi viết**: xác định rõ đâu là logic entry, đâu là exit (SL/TP/trailing), đâu là risk sizing, đâu là filter — để code phản ánh đúng các ranh giới này (xem cấu trúc file chuẩn bên dưới).
3. **Viết cBot** theo đúng quy ước cấu trúc project, API, và nguyên tắc code sạch bên dưới.
4. **Bàn giao**: đưa code đầy đủ + giải thích ngắn gọn mapping "yêu cầu → code" (đặc biệt phần nào bạn tự quyết định) + liệt kê các `[Parameter]` quan trọng có thể optimize.

## Checklist thông tin cần cho một chiến lược

- **Symbol & timeframe** mặc định để test.
- **Entry**: điều kiện Buy, điều kiện Sell (indicator dùng, period, so sánh giá trị nào với giá trị nào). Có cần nến đóng cửa xác nhận không (OnBar) hay vào lệnh theo tick (OnTick)?
- **Stop Loss**: cố định pips, theo ATR (hệ số x ATR), hay theo cấu trúc giá (swing high/low)? Có breakeven / trailing không, trailing theo kiểu gì?
- **Take Profit**: cố định pips, theo R:R với SL, theo ATR, hay không có TP cố định (thoát theo tín hiệu ngược)?
- **Quản lý vốn**: khối lượng cố định, hay % risk trên balance/equity (tính từ khoảng cách SL), có giới hạn max volume/lot không?
- **Giới hạn lệnh**: số lệnh mở đồng thời tối đa, có cho phép nhồi lệnh (nhiều lệnh cùng hướng) không, có giới hạn lỗ/lời trong ngày không?
- **Filter phụ** (nếu có): khung giờ giao dịch, ngày trong tuần, spread tối đa, filter theo xu hướng khung lớn hơn...

Nếu người dùng chỉ đưa một phần (ví dụ chỉ có entry, chưa có SL/TP) → hỏi thẳng phần còn thiếu thay vì tự bịa toàn bộ risk management.

---

# Cấu trúc project (theo đúng khuôn cTrader tự sinh ra)

Mỗi cBot là một project riêng, đặt cạnh `Combo/` (ví dụ tham khảo có sẵn trong repo) theo khuôn:

```
<TenBot>/
  <TenBot>.sln
  <TenBot>/
    <TenBot>.csproj
    <TenBot>.cs
```

`.csproj` giữ nguyên khuôn chuẩn (net6.0 + package `cTrader.Automate`):

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net6.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="cTrader.Automate" Version="*" />
  </ItemGroup>
</Project>
```

Nếu người dùng chỉ muốn 1 file `.cs` để dán trực tiếp vào cBot editor có sẵn trong cTrader (không cần tạo project mới), hỏi rõ trước khi sinh cả bộ `.sln`/`.csproj`.

## Khung sườn file `.cs` chuẩn — ÁP DỤNG CHO MỌI CHIẾN LƯỢC, không riêng Combo

Đây là cấu trúc bắt buộc, xác nhận lại ngày 2026-08-27: mọi cBot chiến lược
(Combo, MA Cross, và các chiến lược tương lai) đều phải có đủ các "cột trụ"
sau, tách biệt rõ ràng — người dùng phân biệt tường minh 2 khái niệm dễ nhầm:
- **Risk Management** = tính khối lượng **cho 1 lệnh sắp đặt** (bao nhiêu %
  risk, base tính trên balance nào).
- **Position Management** = kiểm soát ở tầm **tài khoản/chuỗi lệnh theo thời
  gian** (daily loss limit, max drawdown, max chuỗi thua liên tiếp...) — hoàn
  toàn khác Risk Management, không được gộp chung.

Người dùng giao dịch qua tài khoản **FTMO** (prop firm) — nên **Position
Management theo chuẩn FTMO (mặc định 5% ngày / 10% tổng) nên có mặt trong mọi
cBot chiến lược** từ nay, không chỉ Combo. Xem code mẫu đầy đủ tại
`Combo/Combo/Combo.cs` (region `Position Management`) — copy gần như nguyên
khối sang chiến lược mới, chỉ cần đổi tên method/field nếu class trùng tên.

**[Mới, xác nhận 2026-09-02] Region `Risk & Position Sizing` của MỌI chiến
lược tương lai cũng PHẢI có 1 lớp chặn margin, không chỉ công thức risk%
theo SL.** Lý do: `volume = riskAmount ÷ (SLdistance × PipValue)` chỉ đảm bảo
đúng **rủi ro $ nếu dính SL** (= đúng RiskPercent% Balance) — nó hoàn toàn mù
trước **margin cần để GIỮ được vị thế** (2 khái niệm khác hẳn nhau: 1 cái là
tiền có thể mất, 1 cái là tiền tạm thời bị khoá lại). Phát hiện thật
(2026-09-02, Combo/US30.cash/H1): SL chỉ hẹp 0.061% giá → công thức tính ra
volume cần margin gần 110% Equity — dù risk% trên giấy vẫn đúng tuyệt đối,
1 lệnh như vậy hoặc bị broker từ chối (`NOT_ENOUGH_MARGIN_BALANCE`), hoặc (dễ
bị bỏ sót hơn) "may mắn" vẫn đặt được nhưng khoá gần hết tài khoản — mất khả
năng vào lệnh mới VÀ mất luôn đệm chống margin-call/gap nếu giá đi ngược
TRƯỚC KHI kịp chạm SL (có thể "cháy" tài khoản dù risk% khai báo rất nhỏ).
"An toàn" do margin tự nhiên hết trước chỉ là may mắn riêng của symbol có
đòn bẩy hiệu lực thấp — trên symbol đòn bẩy cao, cùng công thức sẽ tạo vị
thế phồng to tương tự mà KHÔNG hề bị chặn. Xem code mẫu đầy đủ tại
`Combo/Combo/Combo.cs`/`MA Cross/MA Cross/MA Cross.cs` (hàm `CapVolumeByMargin`
+ `[Parameter] MaxMarginPercent`, Group `Risk Management`, mặc định 50%
Equity/lệnh) — copy nguyên khối sang chiến lược mới, gọi hàm này NGAY SAU khi
tính xong volume risk-based, TRƯỚC khi gửi lệnh.

```csharp
using System;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

[Robot(AccessRights = AccessRights.None, AddIndicators = true)]
public class TenBot : Robot
{
    #region Parameters
    // Mọi con số có thể muốn optimize phải nằm ở đây, không hardcode rải rác.
    // Bao gồm cả Parameter cho nhóm Position Management (xem bên dưới).
    #endregion

    #region Indicators & State
    // Indicator instance, biến trạng thái nội bộ (vd: giá SL trailing hiện tại,
    // _initialBalance, _accountBreached, _haltedForDay, _consecutiveLosses...).
    #endregion

    #region Lifecycle
    protected override void OnStart() { }
    protected override void OnTick() { }   // chỉ dùng nếu chiến lược cần phản ứng theo tick (vd trailing, SL/TP ảo)
    protected override void OnBarClosed() { } // ưu tiên cho logic entry theo nến đóng cửa — ổn định hơn khi optimize
    protected override void OnStop() { }
    #endregion

    #region Entry Logic
    // Điều kiện Buy/Sell thuần tuý, trả về bool hoặc gọi lệnh trực tiếp.
    #endregion

    #region Exit Logic
    // SL/TP/trailing/breakeven. Nếu SL/TP dạng KSL/KTP x ATR (như Combo),
    // đặt enum/hệ số ở đây hoặc cạnh Entry Logic tuỳ chiến lược.
    #endregion

    #region Risk & Position Sizing
    // Tính khối lượng cho 1 lệnh — từ % risk + khoảng cách SL, hoặc fixed lot.
    // BẮT BUỘC thêm bước chặn margin ngay sau đó (CapVolumeByMargin — xem
    // Combo.cs/MA Cross.cs) trước khi gửi lệnh: risk% chỉ đảm bảo đúng số $
    // mất NẾU dính SL, không đảm bảo margin cần để GIỮ được vị thế không
    // vượt quá X% Equity (MaxMarginPercent) — 2 ràng buộc độc lập nhau.
    #endregion

    #region Position Management
    // Kiểm soát tài khoản/chuỗi lệnh — daily loss limit, max drawdown, max
    // chuỗi thua liên tiếp. Copy nguyên khối từ Combo.cs, đăng ký
    // Positions.Closed += OnPositionClosed trong OnStart, gọi
    // UpdatePositionManagement() đầu OnBarClosed, chặn entry khi
    // _accountBreached || _haltedForDay.
    #endregion

    #region Helpers
    #endregion
}
```

Giữ đúng thứ tự region này cho mọi bot → người dùng quen mắt, biết ngay chỉnh chỗ nào khi cần.

---

# Quy ước API cTrader Automate cần nhớ đúng

- **Lifecycle**: `OnStart()`, `OnTick()` (mỗi lần Bid/Ask đổi), `OnBar()`/`OnBarClosed()` (mỗi nến mới), `OnStop()`, `OnException()`. Chiến lược dựa trên nến đóng cửa → dùng `Bars.BarClosed` event hoặc override tương ứng, **không** trộn lẫn logic entry vào `OnTick` nếu chiến lược gốc là theo nến, vì kết quả backtest sẽ khác với logic thật.
- **Giá & dữ liệu nến**: `Symbol.Bid` / `Symbol.Ask` / `Symbol.Spread`; `Bars.ClosePrices/OpenPrices/HighPrices/LowPrices` truy cập qua `.Last(n)` (n=0 là nến gần nhất) hoặc index. `BarOpened` có bar mới chỉ có giá Open; `BarClosed` mới có đủ dữ liệu — dùng `BarClosed` khi cần nến đã đóng hoàn chỉnh.
- **Indicator built-in**: qua `Indicators.MovingAverage(...)`, `Indicators.RelativeStrengthIndex(...)`, `Indicators.AverageTrueRange(...)`, `Indicators.BollingerBands(...)`, v.v. Custom indicator qua `Indicators.GetIndicator<T>()`.
- **Vào lệnh**: `ExecuteMarketOrder(TradeType, symbolName, volumeInUnits, label, stopLossPips, takeProfitPips, comment)` hoặc `ExecuteMarketOrderAsync(...)`. Lệnh chờ: `PlaceLimitOrder(...)`, `PlaceStopOrder(...)`, `PlaceStopLimitOrder(...)`.
- **Quản lý lệnh đang mở**: duyệt qua `Positions` (collection toàn account) — **luôn lọc theo `Label` riêng của bot** (`Positions.FindAll(label, SymbolName)`) để không đụng lệnh của cBot khác hoặc lệnh tay trên cùng account. Đóng lệnh: `position.Close()`. Sửa SL/TP: `position.ModifyStopLossPips(...)` / `ModifyTakeProfitPips(...)` hoặc set giá tuyệt đối qua `ModifyStopLossPrice`.
- **Khối lượng**: tính bằng đơn vị (units), luôn chuẩn hoá qua `Symbol.NormalizeVolumeInUnits(volume, RoundingMode)` trước khi gửi lệnh, và kẹp trong `Symbol.VolumeInUnitsMin` / `VolumeInUnitsMax` / `VolumeInUnitsStep`. Risk % → volume: `riskAmount = Account.Balance * riskPercent / 100`, `volume = riskAmount / (stopLossPips * PipValueNow())`, rồi normalize. `PipValueNow()` trả `Symbol.PipSize` khi QuoteAsset trùng Account.Asset; nếu khác thì dùng `QuoteAsset.Convert(Account.Asset, Symbol.PipSize * 1_000_000) / 1_000_000`. Probe lớn là bắt buộc vì `Asset.Convert` làm tròn số tiền nhỏ theo digits của account: convert trực tiếp 1 pip JPY từng trả `0.00`, HKD từng làm volume cao hơn khoảng 7%. Bản build 2026-09-07 đã verify bằng Ticks trên Combo JP225/HK50/US30/GER40/XAUUSD và MA Cross JP225/HK50. **Sau đó BẮT BUỘC kiểm tra thêm margin** (`Symbol.GetEstimatedMargin(tradeType, volume)` so với `Min(Account.FreeMargin, Account.Equity * MaxMarginPercent / 100)`, giảm volume theo tỉ lệ nếu vượt — xem giải thích đầy đủ + code mẫu ở mục "Khung sườn file .cs chuẩn" phía trên). Kết quả là nominal gross stop-risk; không bao gồm commission/swap/gap/slippage, và tài liệu Algo không công bố `Asset.Convert` dùng Bid/Ask nào theo hướng lệnh.
- **Tài khoản**: `Account.Balance`, `Account.Equity`, `Account.UnrealizedGrossProfit`.
- **`[Parameter]`**: dùng cho mọi giá trị cần optimize — `[Parameter("Tên hiển thị", DefaultValue = x, MinValue = x, MaxValue = x, Step = x, Group = "Nhóm")]`. Đặt `Group` theo từng khối (Entry/Exit/Risk/Filter) để panel Parameters trong cTrader dễ đọc.
- **An toàn cho backtest/optimize**:
  - Dùng `Server.Time`, **không** dùng `DateTime.Now`.
  - Không dùng `Thread.Sleep` hay chờ đồng bộ chặn luồng.
  - Không phụ thuộc UI/Chart drawing cho logic cốt lõi (chart drawing chỉ để debug trực quan, không ảnh hưởng quyết định vào/thoát lệnh).
  - `AccessRights = AccessRights.None` trừ khi bot thực sự cần đọc/ghi file — nếu cần, hỏi trước và nói rõ lý do (Optimize/backtest có thể chạy nhiều instance song song, AccessRights rộng hơn cần cân nhắc).

---

# Nguyên tắc code

- **Clean & tối giản**: không thêm abstraction/interface/pattern không cần thiết cho một cBot có logic đơn giản. Không tạo helper cho thứ chỉ dùng một lần.
- **Không magic number**: mọi ngưỡng, hệ số, pips... phải là `[Parameter]` hoặc `const` có tên rõ nghĩa.
- **Ranh giới rõ ràng**: giữ đúng các region ở khung sườn phía trên, mỗi method làm đúng một việc (entry check, exit check, size tính toán...) để người dùng tự sửa từng phần mà không sợ vỡ phần khác.
- **Comment chỉ khi cần giải thích "tại sao"** (vd: vì sao dùng `Last(1)` thay vì `Last(0)`), không comment lại cái tên biến/hàm đã tự nói lên.
- **Không giả lập tính năng chưa được yêu cầu** (không tự thêm trailing stop, breakeven, filter phiên... nếu người dùng không nói tới) — nhưng có thể *gợi ý* thêm sau khi giao bot, không tự ý nhét vào code.

---

# Phương pháp kiểm định độ ổn định tham số (combo-optimization-methodology)

Ghi lại ngày 2026-08-26 sau sự cố: Optimize 765 tổ hợp X/KSL/KTP trên Combo/HK50/H4 chỉ chạy đúng 7 tháng gần nhất (do lệch ngày cấu hình giữa tab Backtesting và Optimisation trong cTrader, không phải lỗi code) — vài tổ hợp KSL>KTP + X thấp cho ROI 18%/PF~4.8 nhưng chỉ trên 26 lệnh. Từ đó bàn sang câu hỏi rộng hơn: quét Optimize thô (chọn theo NetProfit cao nhất) rất dễ overfit khi số tổ hợp lớn — cần thêm lớp kiểm định robustness. **Chưa triển khai code cho phần này — người dùng chủ động tự làm Walk-Forward trước, các hướng còn lại quay lại sau.**

## ⚠️ Ràng buộc quan trọng nhất: dữ liệu là time series

Trước khi áp bất kỳ kỹ thuật nào dưới đây — **không phải kỹ thuật resampling/CV nào cũng dùng được nguyên bản cho time series** vì các quan sát (lệnh, return theo ngày...) có tự tương quan chuỗi thời gian (autocorrelation, volatility clustering, phụ thuộc regime), vi phạm giả định i.i.d. mà nhiều kỹ thuật thống kê cổ điển yêu cầu:

- **K-fold cross-validation kiểu machine learning thường (xáo trộn ngẫu nhiên rồi chia fold)** → SAI cho time series, gây rò rỉ thông tin tương lai vào tập train (look-ahead leakage). Đây chính là lý do Walk-Forward (giữ đúng thứ tự thời gian) và CPCV (có purging/embargo để chặn rò rỉ giữa các block liền kề) tồn tại — không dùng CV thường.
- **Bootstrap/reshuffle xáo trộn thứ tự lệnh**: chấp nhận được **CHỈ KHI** các lệnh tương đối độc lập/không chồng lấn thời gian (đúng với Combo vì tại 1 thời điểm chỉ có tối đa 1 lệnh) — nhưng vẫn nên ưu tiên **block bootstrap** (lấy mẫu theo từng khối liên tiếp, giữ tương quan cục bộ) thay vì xáo hoàn toàn ngẫu nhiên từng lệnh đơn lẻ, đặc biệt nếu sau này thêm nhiều vị thế đồng thời/đa symbol.
- **Multi-symbol/multi-timeframe cross-check**: an toàn về mặt time-series (không xáo trộn trục thời gian), nhưng các symbol có thể tương quan với nhau (cùng chịu ảnh hưởng 1 sự kiện vĩ mô) → "nhiều symbol cùng có lãi" chưa chắc là bằng chứng độc lập mạnh như tưởng.
- **Deflated Sharpe Ratio**: là hiệu chỉnh thống kê thuần (không đụng tới trục thời gian của data), dùng được trực tiếp trên bảng kết quả Optimize đã có.

## Các hướng đã bàn (ưu tiên theo chi phí/giá trị, thấp→cao)

1. **Trade reshuffling / block-bootstrap Monte Carlo** — rẻ nhất, xử lý hậu kỳ trên `events.json` đã có sẵn, không cần chạy lại cTrader. Trả lời: kết quả đẹp có phụ thuộc thứ tự lệnh may mắn không.
2. **Multi-symbol cross-check** — rẻ (đã có sẵn file signal cho US30/GOLD/BTCUSD/HK50), chỉ cần đổi `SignalFilePath`. Lọc nhanh ý tưởng chỉ ăn may trên 1 symbol.
3. **Walk-Forward Optimization** — đang làm (xem phần "ctrader-cli" ở Môi trường hiện tại). Rolling window (khuyến nghị hơn Anchored cho breakout strategy), tỉ lệ IS:OOS đề xuất khởi điểm 12 tháng:3 tháng, trượt 3 tháng/lần. Quy tắc chọn tổ hợp thắng mỗi IS: KHÔNG chọn theo NetProfit thô — lọc theo số lệnh tối thiểu + ưu tiên vùng "cao nguyên" (hiệu suất ổn định ở các tổ hợp lân cận) thay vì đỉnh cô lập.
4. **Parameter perturbation** (rung nhẹ X/KSL/KTP quanh điểm tối ưu, xem hiệu suất giảm từ từ hay sập đột ngột) — định lượng hoá "vùng cao nguyên" ở bước 3.
5. **CPCV (Combinatorial Purged Cross-Validation) / Deflated Sharpe Ratio** — nâng cao, làm sau khi có nền tảng 1-4. CPCV phức tạp hơn nhiều để tự code, cần purging/embargo đúng cách mới an toàn cho time series.

## Trạng thái Walk-Forward qua ctrader-cli (2026-08-27) — TẠM DỪNG

Người dùng chủ động quyết định **quay lại chạy trực tiếp qua GUI cTrader trước mắt**, gác lại việc tự động hoá Walk-Forward qua CLI — sẽ bàn tiếp sau. Ghi lại nguyên trạng để không phải điều tra lại từ đầu khi quay lại:

**Đã xác nhận hoạt động** (không cần làm lại):
- Cài `ctrader-cli` **standalone** (khác bản bundled trong cTrader Desktop) qua winget manifest, bản **5.9.0.38**: `C:\Users\Administrator\AppData\Local\Programs\cTrader CLI\ctrader-cli.exe`, đã thêm vào PATH (User scope) — gõ thẳng `ctrader-cli` là chạy được.
- Auth: `--ctid=votuankiet96@gmail.com` (dùng thẳng email, không cần số cTID dạng số) + `--pwd-file=<path tới file chứa password, tự tạo bằng notepad, KHÔNG bao giờ paste password vào chat>`.
- `--account=7563609` (dùng **Number** hiển thị trong UI/lệnh `accounts`, KHÔNG dùng `Id` nội bộ — thử `Id` sẽ báo "Account cannot be found").
- cBot Combo cần `--full-access` (vì code dùng `AccessRights.FullAccess` để đọc CSV ngoài) — không có cờ này sẽ báo "Additional AccessRights are required".
- Đã chạy thành công 1 lệnh `ctrader-cli backtest` thật (file `.algo` tại `Sources\Robots\Combo.algo`, dùng lại `.cbotset` từ `Documents\cAlgo\Data\cBots\Combo\<instance>-Default\Backtesting\parameters.cbotset` làm nguồn tham số — file này đã có sẵn `SignalFilePath` trỏ đúng `Z:\...`) — ra JSON kết quả đúng cấu trúc như report.html (`main`/`tradeStatistics`/`history`...).
- `--balance` không truyền vẫn dùng đúng default $10,000 (bảng tóm tắt CLI in "Balance | 0 | default value" lúc chạy — chữ "0" đó gây hiểu lầm, **không phải giá trị thật dùng để backtest**, JSON output vẫn `startingCapital: 10000` đúng).

**Đã xác nhận KHÔNG có** (đã kiểm tra kỹ, đừng thử lại trừ khi có thông tin mới):
- Lệnh `ctrader-cli optimize` **không tồn tại** trong bản 5.9.0.38 — dù gõ `optimize --help` với đủ credentials vẫn chỉ rơi về interactive shell menu (đủ 42 lệnh, liệt kê hết, không có "optimize"). Đã kiểm tra 2 kênh phân phối độc lập (winget-pkgs manifest repo + Homebrew formula `spotware/homebrew-tap`) — cả 2 đều xác nhận **5.9.0 là bản mới nhất công khai**, không có 5.10. Tin "CLI 5.10 hỗ trợ optimize" (từ bài PR/thông cáo Spotware) nhiều khả năng mô tả tính năng chưa phát hành rộng rãi.

**Hướng đi khi quay lại** (chưa code): vì không có `optimize` sẵn, bước "quét tổ hợp X/KSL/KTP trên cửa sổ In-Sample" của Walk-Forward phải tự lặp gọi `ctrader-cli backtest` nhiều lần (1 lần/tổ hợp) bằng script ngoài, thay vì dựa vào 1 lệnh optimize có sẵn — kiến trúc gọn hơn (chỉ 1 wrapper `Invoke-Backtest` dùng lại cho cả bước quét IS lẫn validate OOS) nhưng chậm hơn vì mất khả năng song song nội bộ của GUI Optimize (có thể tự bù bằng `ForEach-Object -Parallel`/`Start-Job` sau). Câu hỏi "CLI có nhanh hơn GUI với tick data không" — **chưa đo được, chưa kết luận** (nút thắt cổ chai lần trước là tải tick cache từ server, không phải do GUI render, nên CLI khó cải thiện phần đó).

---

# Thiết kế Exit cho Combo (2026-08-27) — catalog 6 phương án + nguyên tắc

Bối cảnh: phân tích 765 tổ hợp optimize thật trên HK50/H1 (1 năm) cho thấy mọi tổ hợp có lời đều rơi vào khuôn "TP rất sát (KTP=Fib0236), SL rộng" — win rate ~81.5%, chỉ cao hơn ngưỡng hoà vốn thật (~77.9%, tính từ avg win/loss thật của từng lệnh) đúng ~3.6 điểm %, biên rất mỏng. Test song song 496 tổ hợp KTP≥KSL ("để lời chạy") thì chỉ 5/496 có lời, tốt nhất +$88.97/MaxDD 17.4% — tức bản chất tín hiệu Combo (MA cross + MACD histogram) có vẻ chỉ có edge thật ở việc bắt cú hích NGAY SAU breakout, không phải xu hướng bền. Từ đó nảy sinh nhu cầu thiết kế lại exit để vừa giữ đặc tính đã kiểm chứng (TP sát, win rate cao) vừa không tự giới hạn trần lời khi giá đi xa.

## 5 nguyên tắc xây dựng (áp dụng khi thiết kế thêm exit mode mới, không chỉ Combo)

1. **Không đụng SL đã kiểm chứng** — mọi biến thể đều giữ nguyên `SL = KSL × ATR`; sáng tạo chỉ dồn vào phía TP/exit.
2. **Suy ra (derive) từ tham số đã có, tránh thêm trục Fibonacci độc lập mới** — luôn hỏi "tính được từ KSL/KTP sẵn có bằng công thức không?" trước khi thêm 1 Parameter Fib-level mới. Đây là lý do tổ hợp giảm dần qua từng vòng thiết kế (51,000 → 4,590 → 357). **[Cập nhật 2026-09-03]** thang đo Fib-level cho KSL/KTP đã đổi hẳn — xem mục "Tách enum SL/TP: SlFibLevel × TpFibLevel" cuối file; số "10-mức" nhắc ở phần catalog bên dưới (và mọi số "X/10 mức hợp lệ" trong bảng catalog 6 phương án) giờ đã lỗi thời.
3. **Ưu tiên đảm bảo bằng TOÁN HỌC hơn "hy vọng" qua optimize** — 1 công thức đảm bảo tính chất bất kể dữ liệu tương lai (vd TP1 tự bù đủ 2 lệnh thua) miễn nhiễm overfitting tốt hơn 1 con số "đẹp nhất trong quá khứ".
4. **Risk mỗi lệnh là hằng số bất biến khi tách nhiều lệnh** — luôn chia nhỏ VOLUME, không bao giờ chia nhỏ %RiskPercent tổng; độ phức tạp exit không được làm rò rỉ sang tăng rủi ro.
5. **Position Management luôn tách biệt, áp dụng đều cho mọi exit mode** — không lẫn vào thiết kế TP/SL của từng phương án.

## Catalog 6 phương án (2026-08-28: ✅ ĐÃ CODE 5/6 trong `Combo/Combo/Combo.cs`, enum `ExitMode`)

Mode #2 (TrailingStop độc lập) đã **bỏ, dồn vào #4** — người dùng nhận định đúng:
cơ chế trail của #2 chính là thứ chân Runner của #4 cần, tách riêng #2 chỉ tốn
thêm 1 mode không cần thiết. `ExitMode` hiện có đúng 5 giá trị:
`FixedTP, PartialScaleOut4, LadderRunner, PartialBreakeven, FibCompensating3`.

| # | Tên (= `ExitMode`) | SL | TP | Tổ hợp optimize (X×KSL×...) |
|---|---|---|---|---|
| 1 | `FixedTP` (gốc, đối chứng) | `KSL×ATR` | `KTP×ATR`, chốt cứng 1 lần | 5,100 |
| 3 | `PartialScaleOut4` | `KSL×ATR` chung 4 lệnh | Ladder 4 mức tự suy từ KTP + 3 bước Fibonacci kế tiếp trong enum, tỷ trọng giảm dần theo vàng (~42/26/16/10%, không chia đều) | 3,570 (KTP chỉ 7/10 mức hợp lệ — cần đủ chỗ cho 3 bước sau) |
| 4 | `LadderRunner` | `KSL×ATR` chung | 3 chân ladder như #3 (dùng weight[0..2], 8/10 mức KTP hợp lệ) + chân Runner (weight[3]) KHÔNG TP, được BẬT trail ngay khi 1 chân ladder **cùng basket** chốt lời — basket nhận diện qua `EntryPrice` trùng khớp, KHÔNG cần Comment/basket-ID riêng. **2026-08-28, 2 vòng chỉnh**: (a) thử thêm breakeven ép ngay lập tức lúc kích hoạt → phát hiện dễ bị đóng oan nếu giá giật ngược ngay trong chính cây nến chân ladder vừa chốt (trước khi nến kịp đóng) → **bỏ**, chỉ bật cờ `Activated`, SL vẫn đứng yên tới đúng nhịp nến đóng kế tiếp mới tính lại (giữ khoảng thở); (b) thêm `TrailMethod` (Parameter `Trail`): `RatioOfSL` (mặc định, y hệt trước — Step×BaseSlDistance, khoảng cách cố định) hoặc `MovingAverage` (SL bám theo `Indicators.MovingAverage(ClosePrices, MaPeriod, Simple)` — khoảng cách tự co giãn theo giá thật, tự có khoảng thở ngay sau kích hoạt vì MA luôn đi sau giá). Đây là indicator DUY NHẤT cBot tự tính qua cAlgo (ngoại lệ có chủ đích — chỉ phục vụ THỰC THI trail SL, không phải tín hiệu chiến lược, nên không phá nguyên tắc "mọi indicator chiến lược đọc từ CSV") | ~4,080×2 TrailMethod (X×KSL×KTP(8 mức)×Step(3 mức) cho RatioOfSL, +X×KSL×KTP×MaPeriod cho MovingAverage — 2 lượt optimize riêng biệt vì đổi hẳn cơ chế) |
| 5 | `PartialBreakeven` | `KSL×ATR` chung | Chân A = `KTP×ATR` như #1 (61.8% risk); khi A chốt lời → dời SL chân B (38.2% risk, không TP) về breakeven 1 lần duy nhất, chạy tới tín hiệu đảo chiều. **61.8/38.2 là lựa chọn tự quyết lúc code** (catalog gốc không chốt sẵn tỷ trọng — chọn theo tỉ lệ vàng cho nhất quán với #3/#4) | 5,100 (không đổi so với #1) |
| 6 | `FibCompensating3` | `KSL×ATR` chung 3 lệnh, chia đều | TP1 tự suy = mức Fib nhỏ nhất thoả `TP1 ≥ 2×KSL` (đảm bảo toán học: lệnh 1 ăn một mình vẫn bù đủ 2 lệnh dính SL); TP2/TP3 cố định cứng = Fib2000/Fib2618. Lưu ý: với KslLevel=Fib1000/Fib1272 (2/7 mức hợp lệ lớn nhất), TP1 trùng luôn Fib2000/Fib2618 — 3 chân khi đó chỉ còn 2 mức TP phân biệt, guarantee toán học vẫn đúng. **2026-08-28**: kế thừa ý tưởng breakeven của #5 — khi 1 chân cùng basket chốt lời, SL của chân XA NHẤT còn mở (không hardcode Fib2618, tự suy qua TakeProfit xa entry nhất để đúng cả ca biên TP1=TP3) cũng dời về breakeven, TP giữ nguyên — chỉ siết thêm rủi ro phía SL, làm guarantee gốc càng chắc hơn | **357 (gọn nhất — X×KSL, chỉ 7/10 mức KSL hợp lệ, KHÔNG dùng KtpLevel)** |

Cơ chế basket-linking dùng chung cho #4 và #5 (tìm "chân cùng basket" qua
`Positions.Where(...EntryPrice == closedPosition.EntryPrice...)` trong
`OnPositionClosed`) — cố tình tránh đụng vào tham số `comment` của
`PlaceStopOrder` để giữ rủi ro compile ở mức thấp nhất. Lý do: đã thử xác minh
chữ ký overload `PlaceStopOrder(..., ProtectionType, comment)` thật của bản
cTrader đang cài (5.9.10.52700) qua 2 nguồn — doc web help.ctrader.com (trang
method quá lớn, WebFetch không tải nổi) và file XML doc `martinfou/cAlgo` trên
GitHub (tải về, grep trực tiếp: có `PlaceStopOrder` nhưng là **bản cũ hoàn
toàn không có `ProtectionType`** — không dùng được). Thử phản chiếu
(reflection) thẳng `cAlgo.API.dll` thật tại
`AppData\Local\Spotware\cTrader\<hash>\app_5.9.10.52700\cAlgo.API.dll` qua
PowerShell cũng thất bại — DLL này build .NET 6 (`System.Runtime,
Version=6.0.0.0`), trong khi Windows PowerShell 5.1 chạy trên .NET Framework
4.x nên không resolve được dependency, và máy này không có NuGet cache
(`~/.nuget/packages` không tồn tại) lẫn `dotnet` CLI/shared runtime nào khác
để mượn. Kết luận: muốn tra chữ ký API chính xác 100% trên máy này chỉ có 2
cách — build thật qua GUI cTrader (cách đang dùng, gửi lỗi compile nếu có), hoặc
tìm được một `dotnet` .NET 6 runtime cài riêng để chạy `MetadataLoadContext`.

Chạy tuần tự cả 5 mode (mỗi phương án 1 lượt optimize riêng, không phải 1 lượt gộp).

Ý tưởng phụ chưa quyết: **Reversal Cooldown** (grace period N bar không cho đảo chiều khi vị thế đang lỗ, chỉ áp khi đang âm) — độc lập với 6 phương án trên, cộng thêm được vào bất kỳ phương án nào, chưa chốt có làm không (`MaxConsecutiveLosses` đã có sẵn có thể đã đủ che phần lớn rủi ro whipsaw này).

## Tương tác ReversalMode × ExitMode (2026-08-28)

Phát hiện khi rà lại việc kết hợp `HoldBothWithDecay` với 5 `ExitMode` — không
có tổ hợp nào lỗi/crash, nhưng có 2 điểm ý nghĩa thay đổi thật, đã xử lý 1,
quyết định KHÔNG xử lý bằng code cho điểm còn lại:

1. **Lỗi đếm "N" trong công thức giảm risk `0.618^N`** — `N` từng đếm SỐ LỆNH
   THÔ (`Positions.Count`) thay vì SỐ BASKET (lần đảo chiều). Với ExitMode
   nhiều chân (PartialScaleOut4/LadderRunner/FibCompensating3, 3-4 chân/basket),
   `N` nhảy vọt ngay từ basket đầu tiên → risk giảm nhanh hơn nhiều so với ý đồ
   "giảm dần theo mỗi LẦN đảo chiều" (dù vẫn an toàn — giảm quá tay, không
   khuếch đại). **✅ Đã sửa** (`OpenOrReverse`): đếm theo `EntryPrice` riêng
   biệt (`Distinct().Count()`) — đúng ý đồ cho mọi ExitMode, biến `openBasketCount`.

2. **"Chạy tới tín hiệu đảo chiều" của #5 chỉ đúng nghĩa khi Reversal=Immediate**
   — cơ chế đóng-theo-tín-hiệu-ngược chỉ tồn tại trong nhánh `Immediate`
   (`CloseExistingPosition()`); `HoldBothWithDecay` không đóng gì nên chân B sẽ
   không đóng theo tín hiệu đảo chiều nữa, chỉ đóng khi giá tự pullback về
   breakeven hoặc Position Management force-close. **Không phải lỗi đếm — xung
   đột triết lý thật**, chỉ xảy ra ở #5 (4 mode còn lại đều có TP thật hoặc cơ
   chế tự đóng riêng — vd Runner có SL trailing — không phụ thuộc tín hiệu đảo
   chiều để thoát).

**Quyết định (2026-08-28, người dùng chủ động)**: về mặt Ý NGHĨA THỰC TẾ,
`HoldBothWithDecay` chỉ thật sự có mục đích rõ ràng khi ghép với `FixedTP` —
ghép với 4 mode chia nhiều chân vẫn chạy đúng sau khi sửa mục 1, nhưng "giữ cả
2 hướng chờ thời gian trả lời" chồng lên 1 cấu trúc đã tự phân bậc rủi ro sẵn
không mang lại nhiều giá trị thêm, đặc biệt ở vùng giá sideway (nhiều tín hiệu
đảo chiều liên tục → lot size co lại rất nhanh, gần như vô hiệu). **KHÔNG chặn
cứng bằng code** (giữ đúng nguyên tắc "4 tầng độc lập, kết hợp tự do") — thay
vào đó, xử lý gốc rễ dự kiến ở TẦNG SIGNAL: 1 bộ lọc sideway bên core_python
(VM-OG8) để CSV export ra ít tín hiệu hơn nhưng "chắc" hơn (chỉ bắn tín hiệu
khi thật sự có breakout, không bắn khi giá đang giằng co) — **CHƯA CẦN LÀM
NGAY**, chỉ ghi nhận hướng đi cho tương lai, quay lại sau khi có nhu cầu thật
(walk-forward/robustness ở các mục khác trong file này ưu tiên trước).

## Pipeline chuẩn khi kiểm chứng đa symbol/timeframe + Walk-Forward (2026-08-27)

Combo bản chất thiết kế cho khung **H1-H4** (đã có sẵn signal file cả 4 khung cho HK50) — không mở rộng ra khung khác. Ma trận symbol × timeframe × 6 exit mode × walk-forward quá lớn để brute-force cùng lúc — chạy theo phễu 5 giai đoạn, lọc dần trước khi tốn công giai đoạn sau:

0. **Khoanh phạm vi timeframe**: chỉ H1/H2/H3/H4.
1. **Sàng lọc exit mode trên 1 symbol/timeframe tham chiếu** (vd HK50/H4) — full optimize cả 6 phương án riêng biệt → giữ top 1-2 (không chỉ theo NetProfit — theo PF, MaxDD, số lệnh tối thiểu).
2. **Kiểm chứng chéo symbol/timeframe** — lấy tham số thắng Giai đoạn 1, backtest (không optimize lại) trên symbol khác (US30/GOLD/BTCUSD) + 3 khung H1-H4 còn lại. Edge chỉ sống ở đúng 1 symbol/1 khung = dấu hiệu overfit.
3. **Walk-Forward** (rolling 12 tháng IS : 3 tháng OOS) — chỉ áp cho danh sách ngắn sống sót qua Giai đoạn 2, không phải toàn ma trận.
4. **Kiểm tra thực tế FTMO** — bật `EnableMaxDrawdown` (tối thiểu) chạy lại đúng tổ hợp cuối, xác nhận lợi nhuận có bị cắt ngang giữa chừng bởi rule 10% không (đúng vấn đề phát hiện ở pass #199 HK50/H1: MaxDD 13.49% > 10% FTMO).
5. **(Sau, nếu cần)** Monte Carlo trade-reshuffling + parameter perturbation — theo đúng thứ tự ưu tiên đã ghi ở mục "Các hướng đã bàn" phía trên.

---

# ⚠️ Phát hiện 2026-09-03: catalog `ExitMode`/`ReversalMode` phía trên KHÔNG khớp code thật

Khi rà lại `Combo/Combo/Combo.cs` để sửa margin-cap (xem mục dưới), phát hiện **code hiện tại
KHÔNG HỀ có enum `ExitMode` hay `ReversalMode`** — không có `PartialScaleOut4`,
`LadderRunner`, `PartialBreakeven`, `FibCompensating3`, không có `HoldBothWithDecay`,
không có basket-linking. Code thật hiện tại chỉ có đúng 1 kiểu exit (SL/TP cố định 1 lần,
tương đương mô tả mode `FixedTP` trong catalog) và 1 kiểu reversal (đóng lệnh cũ ngay khi có
tín hiệu ngược hướng, tương đương `Immediate`).

**Chưa xác định được nguyên nhân** — có thể code catalog từng được viết rồi bị revert, có thể
toàn bộ catalog chỉ là kế hoạch thiết kế chưa từng thực sự merge vào file chính. **Chưa điều
tra sâu** (ngoài phạm vi yêu cầu hôm nay) — toàn bộ mục "Thiết kế Exit cho Combo" phía trên
(catalog 6 phương án, tương tác ReversalMode×ExitMode, pipeline kiểm chứng) nên được coi là
**tài liệu THIẾT KẾ/Ý ĐỊNH, không phải mô tả code đang chạy thật** cho tới khi xác minh lại rõ
ràng — bao gồm cả mọi số đếm tổ hợp (5,100/3,570/357...) đều dựa trên enum 10-mức cũ, nay đã
đổi (xem mục ngay dưới đây).

# Tách enum SL/TP: SlFibLevel × TpFibLevel (2026-09-03)

Phát hiện qua thảo luận dài về margin-cap: enum `FibLevel` gốc (10 mức, dùng CHUNG cho cả
`KslLevel` và `KtpLevel`) có **5/10 mức nhỏ hơn 1×ATR** (Fib0236=0.236× tới Fib0786=0.786×) —
với SL, các mức này hẹp hơn cả biên độ trung bình của 1 nến, dễ bị nhiễu đánh trúng VÀ chính là
nguyên nhân chính khiến `CalculateVolume` tính ra volume risk-based quá lớn, dễ vượt trần margin
(`CapVolumeByMargin`) — làm `RiskPercent` mất tác dụng thực tế trên đúng nhóm SL hẹp này. Vì
`KslLevel`/`KtpLevel` cần 2 phổ giá trị khác hẳn nhau (SL cần RỘNG, TP cần đủ cả hẹp lẫn rất
rộng — có bằng chứng lịch sử TP hẹp từng cho win rate cao, xem mục "Thiết kế Exit" phía trên),
**quyết định tách thành 2 enum độc lập** thay vì 1 enum dùng chung.

**Đã code trong cả `Combo.cs` và `MA Cross.cs` (CHƯA BUILD/TEST — máy này không có compiler CLI,
cần build qua GUI cTrader trước khi backtest):**

```
SlFibLevel (8 mức, mặc định Fib1000=1.0):
  0.618, 0.786, 1.000, 1.272, 1.618, 2.000, 2.618, 3.618

TpFibLevel (9 mức, mặc định Fib2618=2.618):
  0.236, 0.618, 1.000, 1.618, 2.618, 3.618, 4.236, 4.618, 6.854
```

Các mức mở rộng thêm (3.618/4.236/4.618/6.854) dùng đúng luỹ thừa tỷ lệ vàng φ=1.618
(φ¹=1.618, φ²=2.618, φ³=4.236...) làm chuẩn — không làm tròn thành số đẹp (không dùng 4.0/5.0),
theo yêu cầu người dùng giữ đúng "số chuẩn Fibonacci".

**⚠️ Hệ quả quan trọng — mọi `.cbotset`/kết quả optimize CŨ đều không còn đúng nghĩa**: cTrader
lưu giá trị enum dưới dạng SỐ THỨ TỰ VỊ TRÍ (vd `"KslLevel": "3"`), không phải tên. Cùng số "3"
đó dưới enum 10-mức cũ nghĩa là `Fib0618` (0.618), dưới `SlFibLevel` 8-mức mới nghĩa là
`Fib2000` (2.0) — khác hẳn, không báo lỗi gì, chỉ âm thầm sai nghĩa. Bảng xếp hạng 677 tổ hợp
optimize (Combo/US30.cash/H1, instance `8403a83c-...`) phân tích trước thời điểm này, và mọi
`parameters.cbotset` tham chiếu tới KslLevel/KtpLevel bằng số — đều cần bỏ, chạy lại từ đầu với
thang đo mới.
