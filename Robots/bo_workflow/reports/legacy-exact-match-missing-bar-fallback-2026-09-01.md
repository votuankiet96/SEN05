# Backup — cơ chế exact-match/missing-bar-fallback (đã bị xoá 2026-09-01)

> **Vì sao file này tồn tại**: dự án không dùng git, nên khi xoá 1 cơ chế
> khỏi source, không có lịch sử version nào để khôi phục lại. Người dùng yêu
> cầu "ghi nhớ cơ chế này lại, để nếu cần tôi sẽ yêu cầu dùng lại" — file này
> là bản backup ĐẦY ĐỦ, NGUYÊN VẸN source code trước khi xoá, đủ để dán đè
> lại `Combo.cs`/`MA Cross.cs` nguyên trạng nếu người dùng quyết định phục
> hồi. Đừng chỉ dựa vào mô tả bằng lời — copy nguyên khối code bên dưới.

## Bối cảnh quyết định xoá

Cơ chế này (thiết kế ban đầu bởi Codex, xem `AGENT.md` §8 entry
`2026-08-28 19:26 — Codex`) giải quyết vấn đề: CSV signal sinh ra từ dữ liệu
nến Capital.com, nhưng backtest chạy trên dữ liệu nến FTMO — 2 broker không
luôn chia nến giống hệt nhau (xem `reports/missing-bar-followup-2026-09-01.md`
để hiểu đầy đủ hiện tượng này và mức độ ảnh hưởng đã đo được). Cơ chế cũ:
mỗi tín hiệu CSV được gắn nhãn "ExactMatch" (nếu FTMO có đúng 1 nến bắt đầu
đúng giờ CSV ghi) hoặc "FallbackAligned" (nếu không, phải chờ tick khả dụng
đầu tiên sau đó, với 1 trần thời gian chờ tuyệt đối = 3× độ dài nến danh
nghĩa trước khi bỏ cuộc).

**Quyết định xoá (2026-09-01, người dùng chủ động)**: lý lẽ — khi live
trading thật, bot chỉ phản ứng theo thời gian thực trên CHÍNH tài khoản đặt
lệnh (FTMO) — không hề có khái niệm "có khớp đúng 1 nến của broker khác hay
không" ý nghĩa gì trong thực tế. Backtest nên phản ánh đúng: thấy tín hiệu
(đủ thời gian trôi qua kể từ khi nến CSV đóng) → đặt lệnh ngay tại tick khả
dụng đầu tiên, không cần biết FTMO có "nến khớp đúng" hay không, và không
giới hạn thời gian chờ (đã xác nhận qua phân tích: `MA Cross.cs` vốn ĐÃ
KHÔNG có giới hạn thời gian chờ nào, chỉ riêng `Combo.cs` có — và chính giới
hạn đó đã gây mất tín hiệu thật trong 1 số trường hợp thực nghiệm, xem
`missing-bar-followup-2026-09-01.md` §6.3).

**Bản thay thế** (đang chạy, xem `Combo.cs`/`MA Cross.cs` hiện tại): 1 luồng
xử lý tín hiệu duy nhất, `ProcessScheduledSignals()`, gọi từ `OnTick()`, chờ
tới `AvailableTime` (bartime + độ dài nến danh nghĩa) rồi xử lý tại tick khả
dụng đầu tiên — KHÔNG còn phân biệt exact/fallback, KHÔNG còn giới hạn thời
gian chờ. `Combo.cs` cũng bỏ luôn field `ExpiresAt` khỏi `PendingOrderLifetime`
(pending order giờ chỉ có đúng 1 kiểu hết hạn: đếm 3 nến chart thật).

## Cách phục hồi nếu người dùng yêu cầu lại

1. Copy nguyên khối code tương ứng bên dưới, ghi đè lại đúng
   `Combo/Combo/Combo.cs` hoặc `MA Cross/MA Cross/MA Cross.cs`.
2. Lưu ý: nếu giữa lúc xoá và lúc phục hồi có thêm thay đổi KHÁC vào file
   (vd `ReconcileExistingExposure` được thêm SAU khi bản dưới đây được lưu) —
   phải tự MERGE thủ công, không phải chỉ dán đè mù quáng. Luôn đọc kỹ diff
   giữa bản đang chạy và bản backup trước khi ghi đè.
3. Cân nhắc: quyết định xoá dựa trên lý lẽ "phản ánh đúng thực tế live
   trading" khá vững — trước khi phục hồi, nên hỏi lại người dùng có thực sự
   muốn quay lại logic "khớp đúng nến FTMO" không, hay chỉ cần 1 phần khác
   của cơ chế cũ (vd riêng ý tưởng "trần thời gian chờ" mà không cần cả khái
   niệm exact-match).

---

## Bản gốc `Combo/Combo/Combo.cs` (trước 2026-09-01, có `SignalAlignment`)

```csharp
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

[Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess, AddIndicators = false)]
public class Combo : Robot
{
    private const string Label = "ComboCsvPending";
    private const int PendingLifetimeBars = 3;

    private enum SignalAlignment
    {
        ExactMatch,
        FallbackAligned
    }

    public enum FibLevel
    {
        Fib0236,
        Fib0382,
        Fib0500,
        Fib0618,
        Fib0786,
        Fib1000,
        Fib1272,
        Fib1618,
        Fib2000,
        Fib2618
    }

    #region Parameters

    [Parameter("Signal File Path", Group = "Data Source", DefaultValue = "")]
    public string SignalFilePath { get; set; }

    [Parameter("KSL Level (SL = KSL x ATR)", Group = "Protection", DefaultValue = FibLevel.Fib0618)]
    public FibLevel KslLevel { get; set; }

    [Parameter("KTP Level (TP = KTP x ATR)", Group = "Protection", DefaultValue = FibLevel.Fib0618)]
    public FibLevel KtpLevel { get; set; }

    [Parameter("Risk % Balance", Group = "Risk Management", DefaultValue = 1.0, MinValue = 0.01)]
    public double RiskPercent { get; set; }

    [Parameter("Enable Daily Loss Limit", Group = "Position Management", DefaultValue = false)]
    public bool EnableDailyLossLimit { get; set; }

    [Parameter("Max Daily Loss %", Group = "Position Management", DefaultValue = 5.0, MinValue = 0.1)]
    public double MaxDailyLossPercent { get; set; }

    [Parameter("Enable Max Drawdown", Group = "Position Management", DefaultValue = false)]
    public bool EnableMaxDrawdown { get; set; }

    [Parameter("Max Total Drawdown %", Group = "Position Management", DefaultValue = 10.0, MinValue = 0.1)]
    public double MaxTotalDrawdownPercent { get; set; }

    [Parameter("Enable Max Consecutive Losses", Group = "Position Management", DefaultValue = false)]
    public bool EnableMaxConsecutiveLosses { get; set; }

    [Parameter("Max Consecutive Losses", Group = "Position Management", DefaultValue = 5, MinValue = 1)]
    public int MaxConsecutiveLosses { get; set; }

    #endregion

    #region Indicators & State

    private sealed class SignalRow
    {
        public DateTime BarTime { get; set; }
        public DateTime AvailableTime { get; set; }
        public int Direction { get; set; }
        public double Atr { get; set; }
        public double EntryPrice { get; set; }
        public bool IsHandled { get; set; }
    }

    private sealed class PendingOrderLifetime
    {
        public int BarsRemaining { get; set; }
        public DateTime? ExpiresAt { get; set; }
    }

    private readonly Dictionary<DateTime, SignalRow> _signals = new();
    private readonly List<SignalRow> _scheduledSignals = new();
    private readonly Dictionary<long, PendingOrderLifetime> _pendingOrderLifetimes = new();

    private DateTime _runStartedAt;
    private TimeSpan _signalBarDuration;
    private bool _fallbackReady;
    private int _nextScheduledSignalIndex;

    private double _initialBalance;
    private double _dailyReferenceBalance;
    private DateTime _currentDay = DateTime.MinValue;
    private bool _accountBreached;
    private bool _haltedForDay;
    private int _consecutiveLosses;

    private int _exactMatchedSignals;
    private int _fallbackAlignedSignals;
    private int _fallbackSignalsExpired;
    private int _signalsBeforeStart;
    private int _ordersPlaced;
    private int _orderFailures;
    private int _expiredOrders;
    private int _signalsSkippedByGuard;
    private int _signalsSkippedSameDirection;
    private int _reversalsExecuted;

    #endregion

    #region Lifecycle

    protected override void OnStart()
    {
        _runStartedAt = Server.Time;
        _initialBalance = Account.Balance;
        Positions.Closed += OnPositionClosed;
        LoadSignalFile();
        InitializeSignalSchedule();
    }

    protected override void OnTick()
    {
        ExpireFallbackOrders();
        ProcessFallbackSignals();
    }

    protected override void OnBarClosed()
    {
        // CSV bartime labels the opening of the bar whose completed OHLC generated the signal.
        // Matching it here places the pending order at the next bar open without look-ahead.
        ExpirePendingOrders();

        DateTime closedBarTime = Bars.OpenTimes.Last(0);
        UpdatePositionManagement(closedBarTime);
        if (_signals.TryGetValue(closedBarTime, out SignalRow signal))
            HandleSignal(signal, SignalAlignment.ExactMatch);
    }

    protected override void OnStop()
    {
        Print(
            "Combo: loaded={0}, exact={1}, fallback={2}, fallback-expired={3}, before-start={4}, not-processed={5}, placed={6}, failed={7}, guard-skipped={8}, pending-expired={9}, same-direction-skipped={10}, reversed={11}.",
            _signals.Count,
            _exactMatchedSignals,
            _fallbackAlignedSignals,
            _fallbackSignalsExpired,
            _signalsBeforeStart,
            _signals.Values.Count(signal => !signal.IsHandled),
            _ordersPlaced,
            _orderFailures,
            _signalsSkippedByGuard,
            _expiredOrders,
            _signalsSkippedSameDirection,
            _reversalsExecuted);
    }

    #endregion

    #region Entry Logic

    private void HandleSignal(SignalRow signal, SignalAlignment alignment)
    {
        if (signal.IsHandled)
            return;

        signal.IsHandled = true;
        if (alignment == SignalAlignment.ExactMatch)
            _exactMatchedSignals++;
        else
            _fallbackAlignedSignals++;

        if (_accountBreached || _haltedForDay)
        {
            _signalsSkippedByGuard++;
            return;
        }

        if (!ReconcileExistingExposure(signal))
        {
            _signalsSkippedSameDirection++;
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, alignment={1}, {2} signal skipped: already have same-direction exposure open.",
                signal.BarTime,
                alignment,
                signal.Direction == 1 ? "Buy" : "Sell");
            return;
        }

        PlacePendingOrder(signal, alignment);
    }

    private bool ReconcileExistingExposure(SignalRow signal)
    {
        int newDirection = signal.Direction;
        List<Position> positions = Positions.Where(position => position.Label == Label && position.SymbolName == SymbolName).ToList();
        List<PendingOrder> pendingOrders = PendingOrders.Where(order => order.Label == Label && order.SymbolName == SymbolName).ToList();

        bool hasSameDirection = positions.Any(position => DirectionOf(position.TradeType) == newDirection)
                              || pendingOrders.Any(order => DirectionOf(order.TradeType) == newDirection);
        if (hasSameDirection)
            return false;

        List<Position> opposingPositions = positions.Where(position => DirectionOf(position.TradeType) != newDirection).ToList();
        List<PendingOrder> opposingOrders = pendingOrders.Where(order => DirectionOf(order.TradeType) != newDirection).ToList();
        if (opposingPositions.Count > 0 || opposingOrders.Count > 0)
            _reversalsExecuted++;

        foreach (Position position in opposingPositions)
        {
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, reversal - closing existing {1} position {2} before opening opposite direction.",
                signal.BarTime,
                position.TradeType,
                position.Id);
            position.Close();
        }

        foreach (PendingOrder order in opposingOrders)
        {
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, reversal - cancelling existing {1} pending order {2} before opening opposite direction.",
                signal.BarTime,
                order.TradeType,
                order.Id);
            _pendingOrderLifetimes.Remove(order.Id);
            order.Cancel();
        }

        return true;
    }

    private static int DirectionOf(TradeType tradeType) => tradeType == TradeType.Buy ? 1 : -1;

    private void PlacePendingOrder(SignalRow signal, SignalAlignment alignment)
    {
        GetProtectionPrices(signal, out double stopLossPrice, out double takeProfitPrice);
        double stopLossPips = Math.Abs(signal.EntryPrice - stopLossPrice) / Symbol.PipSize;
        double volume = CalculateVolume(stopLossPips);
        if (volume <= 0)
        {
            _orderFailures++;
            return;
        }

        bool isBuy = signal.Direction == 1;

        TradeResult result = PlaceStopOrder(
            isBuy ? TradeType.Buy : TradeType.Sell,
            SymbolName,
            volume,
            signal.EntryPrice,
            Label,
            stopLossPrice,
            takeProfitPrice,
            ProtectionType.Absolute);

        if (!result.IsSuccessful || result.PendingOrder == null)
        {
            _orderFailures++;
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, alignment={1}, pending {2} at {3} was rejected: {4}.",
                signal.BarTime,
                alignment,
                isBuy ? "Buy" : "Sell",
                signal.EntryPrice,
                result.Error);
            return;
        }

        _pendingOrderLifetimes[result.PendingOrder.Id] = new PendingOrderLifetime
        {
            BarsRemaining = PendingLifetimeBars,
            ExpiresAt = alignment == SignalAlignment.FallbackAligned
                ? GetFallbackExpiry(signal)
                : null
        };
        _ordersPlaced++;
        if (alignment == SignalAlignment.FallbackAligned)
        {
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, alignment={1}, executed={2:yyyy-MM-dd HH:mm:ss}, pending {3} placed at {4}; SL={5}, TP={6}; valid-until={7:yyyy-MM-dd HH:mm}.",
                signal.BarTime,
                alignment,
                Server.Time,
                isBuy ? "Buy" : "Sell",
                signal.EntryPrice,
                stopLossPrice,
                takeProfitPrice,
                GetFallbackExpiry(signal));
        }
        else
        {
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, alignment={1}, executed={2:yyyy-MM-dd HH:mm:ss}, pending {3} placed at {4}; SL={5}, TP={6}; valid for the next {7} chart bar(s).",
                signal.BarTime,
                alignment,
                Server.Time,
                isBuy ? "Buy" : "Sell",
                signal.EntryPrice,
                stopLossPrice,
                takeProfitPrice,
                PendingLifetimeBars);
        }
    }

    #endregion

    #region Exit Logic

    private void GetProtectionPrices(SignalRow signal, out double stopLossPrice, out double takeProfitPrice)
    {
        double direction = signal.Direction;
        stopLossPrice = signal.EntryPrice - direction * ToRatio(KslLevel) * signal.Atr;
        takeProfitPrice = signal.EntryPrice + direction * ToRatio(KtpLevel) * signal.Atr;
    }

    #endregion

    #region Risk & Position Sizing

    private double CalculateVolume(double stopLossPips)
    {
        if (stopLossPips <= 0 || Symbol.PipValue <= 0)
            return 0;

        double riskAmount = Account.Balance * RiskPercent / 100.0;
        double requestedVolume = riskAmount / (stopLossPips * Symbol.PipValue);
        if (requestedVolume < Symbol.VolumeInUnitsMin)
        {
            Print(
                "Combo: {0}% balance risk calculates to {1} units, below broker minimum {2}; signal is not ordered.",
                RiskPercent,
                requestedVolume,
                Symbol.VolumeInUnitsMin);
            return 0;
        }

        if (requestedVolume > Symbol.VolumeInUnitsMax)
        {
            Print(
                "Combo: {0}% balance risk calculates to {1} units, above broker maximum {2}; volume is capped.",
                RiskPercent,
                requestedVolume,
                Symbol.VolumeInUnitsMax);
        }

        return Symbol.NormalizeVolumeInUnits(
            Math.Min(requestedVolume, Symbol.VolumeInUnitsMax),
            RoundingMode.Down);
    }

    #endregion

    #region Position Management

    private void UpdatePositionManagement(DateTime barTime)
    {
        RolloverDayIfNeeded(barTime);

        if (EnableMaxDrawdown && !_accountBreached)
        {
            double threshold = _initialBalance * (1 - MaxTotalDrawdownPercent / 100.0);
            if (Account.Equity <= threshold)
            {
                _accountBreached = true;
                ForceCloseAll();
                Print("Combo: max drawdown reached; trading halted for this run.");
            }
        }

        if (EnableDailyLossLimit && !_haltedForDay && !_accountBreached)
        {
            double threshold = _dailyReferenceBalance * (1 - MaxDailyLossPercent / 100.0);
            if (Account.Equity <= threshold)
            {
                _haltedForDay = true;
                ForceCloseAll();
                Print("Combo: daily loss limit reached; trading halted until the next UTC day.");
            }
        }
    }

    private void RolloverDayIfNeeded(DateTime barTime)
    {
        DateTime today = barTime.Date;
        if (today <= _currentDay)
            return;

        _currentDay = today;
        _dailyReferenceBalance = Account.Balance;
        _haltedForDay = false;
        _consecutiveLosses = 0;
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        Position position = args.Position;
        if (position.Label != Label || position.SymbolName != SymbolName)
            return;

        _consecutiveLosses = position.NetProfit < 0 ? _consecutiveLosses + 1 : 0;
        if (EnableMaxConsecutiveLosses && !_haltedForDay && !_accountBreached
            && _consecutiveLosses >= MaxConsecutiveLosses)
        {
            _haltedForDay = true;
            Print("Combo: max consecutive losses reached; trading halted until the next UTC day.");
        }
    }

    private void ForceCloseAll()
    {
        foreach (PendingOrder order in PendingOrders.Where(order => order.Label == Label && order.SymbolName == SymbolName).ToList())
            order.Cancel();

        foreach (Position position in Positions.Where(position => position.Label == Label && position.SymbolName == SymbolName).ToList())
            position.Close();

        _pendingOrderLifetimes.Clear();
    }

    #endregion

    #region Helpers

    private void LoadSignalFile()
    {
        _signals.Clear();

        if (string.IsNullOrWhiteSpace(SignalFilePath) || !System.IO.File.Exists(SignalFilePath))
        {
            Print("Combo: signal file was not found at '{0}'.", SignalFilePath);
            return;
        }

        string[] lines = System.IO.File.ReadAllLines(SignalFilePath);
        if (lines.Length < 2)
            return;

        string[] header = lines[0].Split(',').Select(column => column.Trim().ToLowerInvariant()).ToArray();
        int barTimeIndex = Array.IndexOf(header, "bartime");
        int atrIndex = Array.IndexOf(header, "atr");
        int entryIndex = Array.IndexOf(header, "entry");
        int signalIndex = Array.IndexOf(header, "signal");

        if (barTimeIndex < 0 || atrIndex < 0 || entryIndex < 0 || signalIndex < 0)
        {
            Print("Combo: CSV requires bartime, atr, entry, and signal columns.");
            return;
        }

        for (int lineIndex = 1; lineIndex < lines.Length; lineIndex++)
        {
            string[] columns = lines[lineIndex].Split(',');
            if (columns.Length <= Math.Max(Math.Max(barTimeIndex, atrIndex), Math.Max(entryIndex, signalIndex)))
                continue;

            if (!DateTime.TryParse(
                    columns[barTimeIndex],
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out DateTime barTime)
                || !double.TryParse(columns[atrIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out double atr)
                || !double.TryParse(columns[entryIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out double entryPrice)
                || !int.TryParse(columns[signalIndex], NumberStyles.Integer, CultureInfo.InvariantCulture, out int direction)
                || (direction != 1 && direction != -1)
                || atr <= 0
                || entryPrice <= 0)
                continue;

            _signals[barTime] = new SignalRow
            {
                BarTime = barTime,
                Direction = direction,
                Atr = atr,
                EntryPrice = entryPrice
            };
        }

        Print("Combo: loaded {0} valid signal row(s) from CSV.", _signals.Count);
    }

    private void InitializeSignalSchedule()
    {
        _scheduledSignals.Clear();
        _nextScheduledSignalIndex = 0;
        _fallbackReady = TryGetNominalBarDuration(TimeFrame, out _signalBarDuration);

        if (!_fallbackReady)
        {
            Print(
                "Combo: timeframe '{0}' has no fixed time duration; missing-bar fallback is unavailable, but exact matching remains active.",
                TimeFrame.ShortName);
            return;
        }

        foreach (SignalRow signal in _signals.Values)
        {
            signal.AvailableTime = signal.BarTime.Add(_signalBarDuration);
            _scheduledSignals.Add(signal);
        }

        _scheduledSignals.Sort((left, right) => left.AvailableTime.CompareTo(right.AvailableTime));
        Print(
            "Combo: missing-bar fallback is active with chart timeframe {0} ({1}); CSV timeframe must match the chart timeframe.",
            TimeFrame.ShortName,
            _signalBarDuration);
    }

    private void ProcessFallbackSignals()
    {
        if (!_fallbackReady)
            return;

        bool positionManagementUpdated = false;
        while (_nextScheduledSignalIndex < _scheduledSignals.Count)
        {
            SignalRow signal = _scheduledSignals[_nextScheduledSignalIndex];
            if (signal.AvailableTime > Server.Time)
                return;

            _nextScheduledSignalIndex++;
            if (signal.IsHandled)
                continue;

            if (signal.AvailableTime < _runStartedAt)
            {
                signal.IsHandled = true;
                _signalsBeforeStart++;
                continue;
            }

            if (Bars.OpenTimes.GetIndexByExactTime(signal.BarTime) >= 0)
                continue;

            DateTime expiresAt = GetFallbackExpiry(signal);
            if (Server.Time >= expiresAt)
            {
                signal.IsHandled = true;
                _fallbackSignalsExpired++;
                Print(
                    "Combo: bartime={0:yyyy-MM-dd HH:mm} has no exact FTMO bar and expired at {1:yyyy-MM-dd HH:mm} before a tradable tick arrived.",
                    signal.BarTime,
                    expiresAt);
                continue;
            }

            if (!positionManagementUpdated)
            {
                UpdatePositionManagement(Server.Time);
                positionManagementUpdated = true;
            }

            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm} has no exact FTMO bar; nominal-close={1:yyyy-MM-dd HH:mm}, fallback-execution={2:yyyy-MM-dd HH:mm:ss}.",
                signal.BarTime,
                signal.AvailableTime,
                Server.Time);
            HandleSignal(signal, SignalAlignment.FallbackAligned);
        }
    }

    private void ExpirePendingOrders()
    {
        // Exact-match orders preserve the original three-chart-bar lifetime. Fallback orders
        // use an absolute source-time expiry and are handled from OnTick instead.
        foreach (KeyValuePair<long, PendingOrderLifetime> item in _pendingOrderLifetimes.ToList())
        {
            PendingOrder order = PendingOrders.FirstOrDefault(candidate => candidate.Id == item.Key);
            if (order == null)
            {
                _pendingOrderLifetimes.Remove(item.Key);
                continue;
            }

            if (item.Value.ExpiresAt.HasValue)
                continue;

            int barsRemaining = item.Value.BarsRemaining - 1;
            if (barsRemaining > 0)
            {
                item.Value.BarsRemaining = barsRemaining;
                continue;
            }

            order.Cancel();
            _pendingOrderLifetimes.Remove(item.Key);
            _expiredOrders++;
            Print("Combo: pending order {0} cancelled after {1} chart bars.", order.Id, PendingLifetimeBars);
        }
    }

    private void ExpireFallbackOrders()
    {
        foreach (KeyValuePair<long, PendingOrderLifetime> item in _pendingOrderLifetimes.ToList())
        {
            if (!item.Value.ExpiresAt.HasValue || Server.Time < item.Value.ExpiresAt.Value)
                continue;

            PendingOrder order = PendingOrders.FirstOrDefault(candidate => candidate.Id == item.Key);
            if (order == null)
            {
                _pendingOrderLifetimes.Remove(item.Key);
                continue;
            }

            order.Cancel();
            _pendingOrderLifetimes.Remove(item.Key);
            _expiredOrders++;
            Print(
                "Combo: fallback pending order {0} cancelled at its three-source-period expiry {1:yyyy-MM-dd HH:mm}.",
                order.Id,
                item.Value.ExpiresAt.Value);
        }
    }

    private DateTime GetFallbackExpiry(SignalRow signal)
    {
        return signal.AvailableTime.AddTicks(_signalBarDuration.Ticks * PendingLifetimeBars);
    }

    private static bool TryGetNominalBarDuration(TimeFrame timeFrame, out TimeSpan duration)
    {
        duration = TimeSpan.Zero;
        string shortName = timeFrame.ShortName;
        if (string.IsNullOrWhiteSpace(shortName) || shortName.Length < 2
            || !int.TryParse(shortName.Substring(1), NumberStyles.None, CultureInfo.InvariantCulture, out int units)
            || units <= 0)
            return false;

        duration = shortName[0] switch
        {
            'm' => TimeSpan.FromMinutes(units),
            'h' => TimeSpan.FromHours(units),
            'D' => TimeSpan.FromDays(units),
            'W' => TimeSpan.FromDays(7.0 * units),
            _ => TimeSpan.Zero
        };

        return duration > TimeSpan.Zero;
    }

    private static double ToRatio(FibLevel level) => level switch
    {
        FibLevel.Fib0236 => 0.236,
        FibLevel.Fib0382 => 0.382,
        FibLevel.Fib0500 => 0.5,
        FibLevel.Fib0618 => 0.618,
        FibLevel.Fib0786 => 0.786,
        FibLevel.Fib1000 => 1.0,
        FibLevel.Fib1272 => 1.272,
        FibLevel.Fib1618 => 1.618,
        FibLevel.Fib2000 => 2.0,
        FibLevel.Fib2618 => 2.618,
        _ => throw new ArgumentOutOfRangeException(nameof(level))
    };

    #endregion
}
```

---

## Bản gốc `MA Cross/MA Cross/MA Cross.cs` (trước 2026-09-01, có `SignalAlignment`)

```csharp
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

[Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess, AddIndicators = false)]
public class MACross : Robot
{
    private const string Label = "MACrossCsvMarket";

    private enum SignalAlignment
    {
        ExactMatch,
        FallbackAligned
    }

    public enum FibLevel
    {
        Fib0236,
        Fib0382,
        Fib0500,
        Fib0618,
        Fib0786,
        Fib1000,
        Fib1272,
        Fib1618,
        Fib2000,
        Fib2618
    }

    #region Parameters

    [Parameter("Signal File Path", Group = "Data Source", DefaultValue = "")]
    public string SignalFilePath { get; set; }

    [Parameter("KSL Level (SL = KSL x ATR)", Group = "Protection", DefaultValue = FibLevel.Fib0618)]
    public FibLevel KslLevel { get; set; }

    [Parameter("KTP Level (TP = KTP x ATR)", Group = "Protection", DefaultValue = FibLevel.Fib0618)]
    public FibLevel KtpLevel { get; set; }

    [Parameter("Risk % Balance", Group = "Risk Management", DefaultValue = 1.0, MinValue = 0.01)]
    public double RiskPercent { get; set; }

    [Parameter("Enable Daily Loss Limit", Group = "Position Management", DefaultValue = false)]
    public bool EnableDailyLossLimit { get; set; }

    [Parameter("Max Daily Loss %", Group = "Position Management", DefaultValue = 5.0, MinValue = 0.1)]
    public double MaxDailyLossPercent { get; set; }

    [Parameter("Enable Max Drawdown", Group = "Position Management", DefaultValue = false)]
    public bool EnableMaxDrawdown { get; set; }

    [Parameter("Max Total Drawdown %", Group = "Position Management", DefaultValue = 10.0, MinValue = 0.1)]
    public double MaxTotalDrawdownPercent { get; set; }

    [Parameter("Enable Max Consecutive Losses", Group = "Position Management", DefaultValue = false)]
    public bool EnableMaxConsecutiveLosses { get; set; }

    [Parameter("Max Consecutive Losses", Group = "Position Management", DefaultValue = 5, MinValue = 1)]
    public int MaxConsecutiveLosses { get; set; }

    #endregion

    #region Indicators & State

    private sealed class SignalRow
    {
        public DateTime BarTime { get; set; }
        public DateTime AvailableTime { get; set; }
        public int Direction { get; set; }
        public double Atr { get; set; }
        public bool IsHandled { get; set; }
    }

    private readonly Dictionary<DateTime, SignalRow> _signals = new();
    private readonly List<SignalRow> _scheduledSignals = new();

    private DateTime _runStartedAt;
    private TimeSpan _signalBarDuration;
    private bool _fallbackReady;
    private int _nextScheduledSignalIndex;

    private double _initialBalance;
    private double _dailyReferenceBalance;
    private DateTime _currentDay = DateTime.MinValue;
    private bool _accountBreached;
    private bool _haltedForDay;
    private int _consecutiveLosses;

    private int _exactMatchedSignals;
    private int _fallbackAlignedSignals;
    private int _signalsBeforeStart;
    private int _ordersPlaced;
    private int _orderFailures;
    private int _signalsSkippedByGuard;

    #endregion

    #region Lifecycle

    protected override void OnStart()
    {
        _runStartedAt = Server.Time;
        _initialBalance = Account.Balance;
        Positions.Closed += OnPositionClosed;
        LoadSignalFile();
        InitializeSignalSchedule();
    }

    protected override void OnTick()
    {
        ProcessFallbackSignals();
    }

    protected override void OnBarClosed()
    {
        // CSV bartime labels the opening of the bar whose completed close generated the signal.
        // Executing here submits the market order at the next bar open without look-ahead.
        DateTime closedBarTime = Bars.OpenTimes.Last(0);
        UpdatePositionManagement(closedBarTime);
        if (_signals.TryGetValue(closedBarTime, out SignalRow signal))
            HandleSignal(signal, SignalAlignment.ExactMatch);
    }

    protected override void OnStop()
    {
        Print(
            "MA Cross: loaded={0}, exact={1}, fallback={2}, before-start={3}, not-processed={4}, placed={5}, failed={6}, guard-skipped={7}.",
            _signals.Count,
            _exactMatchedSignals,
            _fallbackAlignedSignals,
            _signalsBeforeStart,
            _signals.Values.Count(signal => !signal.IsHandled),
            _ordersPlaced,
            _orderFailures,
            _signalsSkippedByGuard);
    }

    #endregion

    #region Entry Logic

    private void HandleSignal(SignalRow signal, SignalAlignment alignment)
    {
        if (signal.IsHandled)
            return;

        signal.IsHandled = true;
        if (alignment == SignalAlignment.ExactMatch)
            _exactMatchedSignals++;
        else
            _fallbackAlignedSignals++;

        if (_accountBreached || _haltedForDay)
        {
            _signalsSkippedByGuard++;
            return;
        }

        PlaceMarketOrder(signal, alignment);
    }

    private void PlaceMarketOrder(SignalRow signal, SignalAlignment alignment)
    {
        double stopLossPips = ToRatio(KslLevel) * signal.Atr / Symbol.PipSize;
        double takeProfitPips = ToRatio(KtpLevel) * signal.Atr / Symbol.PipSize;
        double volume = CalculateVolume(stopLossPips);
        if (volume <= 0)
        {
            _orderFailures++;
            return;
        }

        TradeResult result = ExecuteMarketOrder(
            signal.Direction == 1 ? TradeType.Buy : TradeType.Sell,
            SymbolName,
            volume,
            Label,
            stopLossPips,
            takeProfitPips);

        if (!result.IsSuccessful)
        {
            _orderFailures++;
            Print(
                "MA Cross: bartime={0:yyyy-MM-dd HH:mm}, alignment={1}, market {2} was rejected: {3}.",
                signal.BarTime,
                alignment,
                signal.Direction == 1 ? "Buy" : "Sell",
                result.Error);
            return;
        }

        _ordersPlaced++;
        Print(
            "MA Cross: bartime={0:yyyy-MM-dd HH:mm}, alignment={1}, executed={2:yyyy-MM-dd HH:mm:ss}, market {3} placed; SL={4} pips, TP={5} pips.",
            signal.BarTime,
            alignment,
            Server.Time,
            signal.Direction == 1 ? "Buy" : "Sell",
            stopLossPips,
            takeProfitPips);
    }

    #endregion

    #region Exit Logic

    // The market-order overload sets the static SL/TP from the actual fill price.

    #endregion

    #region Risk & Position Sizing

    private double CalculateVolume(double stopLossPips)
    {
        if (stopLossPips <= 0 || Symbol.PipValue <= 0)
            return 0;

        double riskAmount = Account.Balance * RiskPercent / 100.0;
        double requestedVolume = riskAmount / (stopLossPips * Symbol.PipValue);
        if (requestedVolume < Symbol.VolumeInUnitsMin)
        {
            Print(
                "MA Cross: {0}% balance risk calculates to {1} units, below broker minimum {2}; signal is not ordered.",
                RiskPercent,
                requestedVolume,
                Symbol.VolumeInUnitsMin);
            return 0;
        }

        if (requestedVolume > Symbol.VolumeInUnitsMax)
        {
            Print(
                "MA Cross: {0}% balance risk calculates to {1} units, above broker maximum {2}; volume is capped.",
                RiskPercent,
                requestedVolume,
                Symbol.VolumeInUnitsMax);
        }

        return Symbol.NormalizeVolumeInUnits(
            Math.Min(requestedVolume, Symbol.VolumeInUnitsMax),
            RoundingMode.Down);
    }

    #endregion

    #region Position Management

    private void UpdatePositionManagement(DateTime barTime)
    {
        RolloverDayIfNeeded(barTime);

        if (EnableMaxDrawdown && !_accountBreached)
        {
            double threshold = _initialBalance * (1 - MaxTotalDrawdownPercent / 100.0);
            if (Account.Equity <= threshold)
            {
                _accountBreached = true;
                ForceCloseAll();
                Print("MA Cross: max drawdown reached; trading halted for this run.");
            }
        }

        if (EnableDailyLossLimit && !_haltedForDay && !_accountBreached)
        {
            double threshold = _dailyReferenceBalance * (1 - MaxDailyLossPercent / 100.0);
            if (Account.Equity <= threshold)
            {
                _haltedForDay = true;
                ForceCloseAll();
                Print("MA Cross: daily loss limit reached; trading halted until the next UTC day.");
            }
        }
    }

    private void RolloverDayIfNeeded(DateTime barTime)
    {
        DateTime today = barTime.Date;
        if (today <= _currentDay)
            return;

        _currentDay = today;
        _dailyReferenceBalance = Account.Balance;
        _haltedForDay = false;
        _consecutiveLosses = 0;
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        Position position = args.Position;
        if (position.Label != Label || position.SymbolName != SymbolName)
            return;

        _consecutiveLosses = position.NetProfit < 0 ? _consecutiveLosses + 1 : 0;
        if (EnableMaxConsecutiveLosses && !_haltedForDay && !_accountBreached
            && _consecutiveLosses >= MaxConsecutiveLosses)
        {
            _haltedForDay = true;
            Print("MA Cross: max consecutive losses reached; trading halted until the next UTC day.");
        }
    }

    private void ForceCloseAll()
    {
        foreach (PendingOrder order in PendingOrders.Where(order => order.Label == Label && order.SymbolName == SymbolName).ToList())
            order.Cancel();

        foreach (Position position in Positions.Where(position => position.Label == Label && position.SymbolName == SymbolName).ToList())
            position.Close();
    }

    #endregion

    #region Helpers

    private void LoadSignalFile()
    {
        _signals.Clear();

        if (string.IsNullOrWhiteSpace(SignalFilePath) || !System.IO.File.Exists(SignalFilePath))
        {
            Print("MA Cross: signal file was not found at '{0}'.", SignalFilePath);
            return;
        }

        string[] lines = System.IO.File.ReadAllLines(SignalFilePath);
        if (lines.Length < 2)
            return;

        string[] header = lines[0].Split(',').Select(column => column.Trim().ToLowerInvariant()).ToArray();
        int barTimeIndex = Array.IndexOf(header, "bartime");
        int atrIndex = Array.IndexOf(header, "atr");
        int signalIndex = Array.IndexOf(header, "signal");

        if (barTimeIndex < 0 || atrIndex < 0 || signalIndex < 0)
        {
            Print("MA Cross: CSV requires bartime, atr, and signal columns.");
            return;
        }

        for (int lineIndex = 1; lineIndex < lines.Length; lineIndex++)
        {
            string[] columns = lines[lineIndex].Split(',');
            if (columns.Length <= Math.Max(barTimeIndex, Math.Max(atrIndex, signalIndex)))
                continue;

            if (!DateTime.TryParse(
                    columns[barTimeIndex],
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out DateTime barTime)
                || !double.TryParse(columns[atrIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out double atr)
                || !int.TryParse(columns[signalIndex], NumberStyles.Integer, CultureInfo.InvariantCulture, out int direction)
                || (direction != 1 && direction != -1)
                || atr <= 0)
                continue;

            _signals[barTime] = new SignalRow
            {
                BarTime = barTime,
                Direction = direction,
                Atr = atr
            };
        }

        Print("MA Cross: loaded {0} valid signal row(s) from CSV.", _signals.Count);
    }

    private void InitializeSignalSchedule()
    {
        _scheduledSignals.Clear();
        _nextScheduledSignalIndex = 0;
        _fallbackReady = TryGetNominalBarDuration(TimeFrame, out _signalBarDuration);

        if (!_fallbackReady)
        {
            Print(
                "MA Cross: timeframe '{0}' has no fixed time duration; missing-bar fallback is unavailable, but exact matching remains active.",
                TimeFrame.ShortName);
            return;
        }

        foreach (SignalRow signal in _signals.Values)
        {
            signal.AvailableTime = signal.BarTime.Add(_signalBarDuration);
            _scheduledSignals.Add(signal);
        }

        _scheduledSignals.Sort((left, right) => left.AvailableTime.CompareTo(right.AvailableTime));
        Print(
            "MA Cross: missing-bar fallback is active with chart timeframe {0} ({1}); CSV timeframe must match the chart timeframe.",
            TimeFrame.ShortName,
            _signalBarDuration);
    }

    private void ProcessFallbackSignals()
    {
        if (!_fallbackReady)
            return;

        bool positionManagementUpdated = false;
        while (_nextScheduledSignalIndex < _scheduledSignals.Count)
        {
            SignalRow signal = _scheduledSignals[_nextScheduledSignalIndex];
            if (signal.AvailableTime > Server.Time)
                return;

            _nextScheduledSignalIndex++;
            if (signal.IsHandled)
                continue;

            if (signal.AvailableTime < _runStartedAt)
            {
                signal.IsHandled = true;
                _signalsBeforeStart++;
                continue;
            }

            if (Bars.OpenTimes.GetIndexByExactTime(signal.BarTime) >= 0)
                continue;

            if (!positionManagementUpdated)
            {
                UpdatePositionManagement(Server.Time);
                positionManagementUpdated = true;
            }

            Print(
                "MA Cross: bartime={0:yyyy-MM-dd HH:mm} has no exact FTMO bar; nominal-close={1:yyyy-MM-dd HH:mm}, fallback-execution={2:yyyy-MM-dd HH:mm:ss}.",
                signal.BarTime,
                signal.AvailableTime,
                Server.Time);
            HandleSignal(signal, SignalAlignment.FallbackAligned);
        }
    }

    private static bool TryGetNominalBarDuration(TimeFrame timeFrame, out TimeSpan duration)
    {
        duration = TimeSpan.Zero;
        string shortName = timeFrame.ShortName;
        if (string.IsNullOrWhiteSpace(shortName) || shortName.Length < 2
            || !int.TryParse(shortName.Substring(1), NumberStyles.None, CultureInfo.InvariantCulture, out int units)
            || units <= 0)
            return false;

        duration = shortName[0] switch
        {
            'm' => TimeSpan.FromMinutes(units),
            'h' => TimeSpan.FromHours(units),
            'D' => TimeSpan.FromDays(units),
            'W' => TimeSpan.FromDays(7.0 * units),
            _ => TimeSpan.Zero
        };

        return duration > TimeSpan.Zero;
    }

    private static double ToRatio(FibLevel level) => level switch
    {
        FibLevel.Fib0236 => 0.236,
        FibLevel.Fib0382 => 0.382,
        FibLevel.Fib0500 => 0.5,
        FibLevel.Fib0618 => 0.618,
        FibLevel.Fib0786 => 0.786,
        FibLevel.Fib1000 => 1.0,
        FibLevel.Fib1272 => 1.272,
        FibLevel.Fib1618 => 1.618,
        FibLevel.Fib2000 => 2.0,
        FibLevel.Fib2618 => 2.618,
        _ => throw new ArgumentOutOfRangeException(nameof(level))
    };

    #endregion
}
```
