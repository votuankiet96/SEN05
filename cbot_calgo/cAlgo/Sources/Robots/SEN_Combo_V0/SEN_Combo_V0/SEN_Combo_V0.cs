using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;

namespace cAlgo.Robots;

/*
 * SEN_Combo_V0 - CSV signal executor implementing the original Combo strategy.
 *
 * High-level workflow:
 *   1) OnStart loads external CSV signals into memory.
 *   2) On every closed bar, the bot checks whether the closed bar contains a CSV signal.
 *   3) If the signal is opposite to existing exposure, opposite pending orders are cancelled
 *      and opposite positions are closed.
 *   4) If exposure already exists in the same direction, the new same-direction signal is ignored.
 *   5) The bot places a stop-order cluster based on ExitMode:
 *        FixedOnly: one leg with fixed TP = Entry ± KTP × ATR.
 *        TwoLegs:   leg 1 = fixed TP; leg 2 = no TP, SL trailed via SMA20 each bar.
 *
 * Supported CSV formats:
 *   bartime,side,atr
 *   bartime,symbol,side,atr
 *
 * Entry / SL / TP (original Combo lecture formulas):
 *   BUY  -> Buy Stop  | Entry = High + X  | SL = Low  - X  | TP = Entry + KTP × ATR
 *   SELL -> Sell Stop | Entry = Low  - X  | SL = High + X  | TP = Entry - KTP × ATR
 *   X is a fixed price-unit offset per symbol (not a % of range).
 *   High and Low are from the signal bar (just-closed bar, Last(1)).
 *
 * SMA20 trailing (Leg 2, TwoLegs mode only):
 *   BUY leg 2:  each bar close → New SL = max(Current SL, SMA20)
 *   SELL leg 2: each bar close → New SL = min(Current SL, SMA20)
 *   SL only moves in the profitable direction, never loosens.
 *
 * Management:
 *   Pending orders expire after N closed bars.
 *   Opposite signals close current positions and pending orders, then place the new cluster.
 *   Same-direction signals are ignored while any same-direction exposure exists.
 *   Daily drawdown protection halts trading and flattens bot exposure for the day.
 */

public enum ExitModeOption
{
    FixedOnly = 1,
    TwoLegs   = 2,
}

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_Combo_V0 : Robot
{
    // ── File ────────────────────────────────────────────────────────────────
    [Parameter("Signal CSV Path", Group = "File",
        DefaultValue = @"Z:\SEN05_Autotrading\raw_signals\combo\combo_US30_H4_20170430_20260602_signals.csv")]
    public string CsvPath { get; set; }

    [Parameter("CSV Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -12, MaxValue = 14, Step = 1)]
    public int CsvTimeOffsetHours { get; set; }

    [Parameter("Bot Label", Group = "Identity", DefaultValue = "SEN_Combo_V0")]
    public string BotLabel { get; set; }

    // ── Execution ────────────────────────────────────────────────────────────
    // X is a fixed price-unit offset specific to each symbol/index.
    // Example values: US30=10, HK50=15, J225=15, US100/DE40/UK100=5, US500=1.
    [Parameter("X Offset (price units)", Group = "Execution",
        DefaultValue = 10.0, MinValue = 0, MaxValue = 50.0, Step = 0.5)]
    public double XOffset { get; set; }

    // KTP selects one of 6 Fibonacci extension levels for the fixed-TP leg.
    // Level 4 = 2.272 ≈ 2.3 as specified in the Combo lecture.
    [Parameter("KTP Fibonacci Level (1-6)", Group = "Execution",
        DefaultValue = 4, MinValue = 1, MaxValue = 6, Step = 1)]
    public int KtpFibLevel { get; set; }

    [Parameter("Exit Mode", Group = "Execution", DefaultValue = ExitModeOption.FixedOnly)]
    public ExitModeOption ExitMode { get; set; }

    [Parameter("Max Spread / SL % (0=off)", Group = "Execution",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 25.0, Step = 2.5)]
    public double MaxSpreadToStopLossPercent { get; set; }

    [Parameter("Cancel Pending After Bars", Group = "Execution",
        DefaultValue = 3, MinValue = 1, MaxValue = 50, Step = 1)]
    public int CancelPendingAfterBars { get; set; }

    [Parameter("Max Signal Bar Clock Hours", Group = "Execution",
        DefaultValue = 0, MinValue = 0, MaxValue = 168, Step = 1)]
    public double MaxSignalBarClockHours { get; set; }

    // ── Risk ─────────────────────────────────────────────────────────────────
    // RiskPercent is total cluster risk, split evenly across active legs.
    [Parameter("Risk % per Trade", Group = "Risk",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 3.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Max Daily Drawdown %", Group = "Risk",
        DefaultValue = 10.0, MinValue = 1.0, MaxValue = 100.0, Step = 0.5)]
    public double MaxDailyDrawdownPercent { get; set; }

    // ── Runtime state ────────────────────────────────────────────────────────
    private readonly Dictionary<DateTime, SignalInfo> _signals = new();
    private readonly Dictionary<long, int> _pendingOrderCreatedBarCounts = new();

    // SMA20 is used to trail the SL for Leg 2 in TwoLegs mode.
    private MovingAverage _sma20;

    // Daily drawdown guard — measured from account equity to include floating PnL.
    private DateTime _drawdownDay;
    private double   _dayStartEquity;
    private bool     _dailyDrawdownTriggered;

    // Diagnostic counters — not trading logic.
    private int _barsWithoutSignal;
    private int _matchedSignalCount;
    private int _multiSignalBarCount;
    private int _stopOrdersPlacedCount;
    private int _pendingFilledCount;
    private int _pendingCancelledCount;
    private int _pendingExpiredCount;
    private int _skipSameDirectionSignalCount;
    private int _skipSpreadFilterCount;
    private int _skipVolumeCount;
    private int _skipCloseOppositeCount;
    private int _skipLongSignalBarCount;
    private int _placeOrderErrorCount;

    // ── Lifecycle ────────────────────────────────────────────────────────────

    protected override void OnStart()
    {
        PendingOrders.Filled    += OnPendingOrderFilled;
        PendingOrders.Cancelled += OnPendingOrderCancelled;
        Positions.Closed        += OnPositionClosed;

        ResetDailyDrawdownBaseline();

        if (!ValidateParameters())
        {
            Stop();
            return;
        }

        // SMA20 trailing is always initialised; it is only applied when ExitMode = TwoLegs.
        _sma20 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 20);

        var ktpMultiplier = GetKtpMultiplier(KtpFibLevel);
        Print("[{0}] Symbol info | Symbol: {1} | PipSize: {2} | TickSize: {3} | Digits: {4} | VolumeMin: {5} | X: {6} | KTP: {7:F3} (Level {8}) | ExitMode: {9} | Execution: StopOrderOnly | MaxSignalBarClockHours: {10:F1}",
            BotLabel, SymbolName,
            Symbol.PipSize, Symbol.TickSize, Symbol.Digits, Symbol.VolumeInUnitsMin,
            XOffset, ktpMultiplier, KtpFibLevel, ExitMode,
            MaxSignalBarClockHours);

        LoadSignals();

        if (_signals.Count == 0)
        {
            Print("[{0}] ERROR: no valid signals loaded. Stopping.", BotLabel);
            Stop();
        }

        TrackExistingPendingOrders();
    }

    protected override void OnTick()
    {
        // Daily drawdown guard is checked every tick for fast response.
        CheckDailyDrawdownLimit();
    }

    protected override void OnBarClosed()
    {
        if (CheckDailyDrawdownLimit())
            return;

        // Trail Leg 2 SL before checking for a new signal so trailing is always up-to-date.
        ApplySma20Trailing();

        CancelExpiredPendingOrders();

        if (_signals.Count == 0)
            return;

        var barTime     = TrimToMinute(Bars.OpenTimes.Last(1));
        var nextBarTime = TrimToMinute(Bars.OpenTimes.Last(0));

        if (!TryGetSignalForClosedBar(barTime, nextBarTime, out var signal, out var signalsInBar))
        {
            _barsWithoutSignal++;
            return;
        }

        _matchedSignalCount++;
        if (signalsInBar > 1)
            _multiSignalBarCount++;

        var signalBarClockHours = (nextBarTime - barTime).TotalHours;
        if (MaxSignalBarClockHours > 0 && signalBarClockHours > MaxSignalBarClockHours)
        {
            _skipLongSignalBarCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: bar clock {3:F2}h > MaxSignalBarClockHours {4:F2}h. NextBar: {5:yyyy-MM-dd HH:mm}",
                BotLabel, signal.Side, barTime, signalBarClockHours, MaxSignalBarClockHours, nextBarTime);
            return;
        }

        var tradeType = GetTradeType(signal);

        CancelOppositePendingOrders(tradeType);
        if (!CloseOppositePositions(tradeType))
        {
            _skipCloseOppositeCount++;
            return;
        }

        var sameDirectionCount = GetMyOpenOrderCount(tradeType);
        if (sameDirectionCount > 0)
        {
            _skipSameDirectionSignalCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: same-direction exposure exists ({3} order/position).",
                BotLabel, signal.Side, barTime, sameDirectionCount);
            return;
        }

        PlaceSignalOrder(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar);
    }

    protected override void OnStop()
    {
        Print("[{0}] Stopped. Signals loaded: {1}", BotLabel, _signals.Count);
        Print("[{0}] Summary | Matched: {1} | MultiBar: {2} | NoSignal: {3} | Placed: {4} | Filled: {5} | Cancelled: {6} | Expired: {7} | SkipSameDir: {8} | SkipSpread: {9} | SkipVol: {10} | SkipCloseOpp: {11} | SkipLongBar: {12} | Errors: {13}",
            BotLabel,
            _matchedSignalCount, _multiSignalBarCount, _barsWithoutSignal,
            _stopOrdersPlacedCount, _pendingFilledCount, _pendingCancelledCount, _pendingExpiredCount,
            _skipSameDirectionSignalCount, _skipSpreadFilterCount, _skipVolumeCount,
            _skipCloseOppositeCount, _skipLongSignalBarCount, _placeOrderErrorCount);
    }

    // ── Validation ───────────────────────────────────────────────────────────

    private bool ValidateParameters()
    {
        if (string.IsNullOrWhiteSpace(CsvPath))
        {
            Print("[{0}] ERROR: CSV path is empty.", BotLabel);
            return false;
        }

        if (XOffset <= 0 || XOffset > 200.0)
        {
            Print("[{0}] ERROR: X Offset must be > 0 and <= 200 price units.", BotLabel);
            return false;
        }

        if (KtpFibLevel < 1 || KtpFibLevel > 6)
        {
            Print("[{0}] ERROR: KTP Fibonacci level must be 1 to 6.", BotLabel);
            return false;
        }

        if (MaxSpreadToStopLossPercent < 0 || MaxSpreadToStopLossPercent > 25.0)
        {
            Print("[{0}] ERROR: Max Spread / SL % must be 0 to 25. Use 0 to disable.", BotLabel);
            return false;
        }

        if (RiskPercent <= 0 || RiskPercent > 3.0)
        {
            Print("[{0}] ERROR: Risk percent must be > 0 and <= 3.", BotLabel);
            return false;
        }

        if (CancelPendingAfterBars < 1)
        {
            Print("[{0}] ERROR: Cancel pending after bars must be >= 1.", BotLabel);
            return false;
        }

        if (MaxDailyDrawdownPercent <= 0 || MaxDailyDrawdownPercent > 100)
        {
            Print("[{0}] ERROR: Max daily drawdown percent must be > 0 and <= 100.", BotLabel);
            return false;
        }

        return true;
    }

    // ── SMA20 trailing (Leg 2, TwoLegs mode) ────────────────────────────────

    private void ApplySma20Trailing()
    {
        if (ExitMode != ExitModeOption.TwoLegs)
            return;

        var sma20Value = _sma20.Result.Last(1);
        if (double.IsNaN(sma20Value))
            return;

        foreach (var position in GetMyPositions())
        {
            if (!IsTrailingLeg(position.Label))
                continue;

            var newSl = position.TradeType == TradeType.Buy
                ? Math.Max(position.StopLoss ?? double.MinValue, sma20Value)
                : Math.Min(position.StopLoss ?? double.MaxValue, sma20Value);

            newSl = Math.Round(newSl, Symbol.Digits);

            if (!ShouldImproveStopLoss(position, newSl))
                continue;

            var oldSl  = position.StopLoss;
            var result = ModifyPosition(position, newSl, position.TakeProfit, ProtectionType.Absolute);
            if (!result.IsSuccessful)
                Print("[{0}] SMA20 TRAIL ERROR | {1} | Label: {2} | SL {3} -> {4} failed: {5}",
                    BotLabel, position.TradeType, position.Label, oldSl, newSl, result.Error);
            else
                Print("[{0}] SMA20 TRAIL | {1} | Label: {2} | SL: {3} -> {4} | SMA20: {5}",
                    BotLabel, position.TradeType, position.Label, oldSl, newSl,
                    sma20Value.ToString("F" + Symbol.Digits, CultureInfo.InvariantCulture));
        }
    }

    // ── CSV loading ──────────────────────────────────────────────────────────

    private void LoadSignals()
    {
        _signals.Clear();

        if (!System.IO.File.Exists(CsvPath))
        {
            Print("[{0}] ERROR: CSV file not found: {1}", BotLabel, CsvPath);
            return;
        }

        var lines = System.IO.File.ReadAllLines(CsvPath);
        if (lines.Length == 0)
            return;

        var header    = SplitCsvLine(lines[0]);
        var hasHeader = header.Any(cell => cell.Equals("bartime", StringComparison.OrdinalIgnoreCase));

        var timeIndex   = hasHeader ? FindColumn(header, "bartime", "time", "date") : 0;
        var symbolIndex = hasHeader ? FindColumn(header, "symbol", "symbolname")     : -1;
        var sideIndex   = hasHeader ? FindColumn(header, "side", "signal", "direction") : 1;
        var atrIndex    = hasHeader ? FindColumn(header, "atr")                      : 2;

        if (timeIndex < 0 || sideIndex < 0 || atrIndex < 0)
        {
            Print("[{0}] ERROR: CSV header must include bartime, side and atr.", BotLabel);
            return;
        }

        var loaded    = 0;
        var skipped   = 0;
        var startLine = hasHeader ? 1 : 0;

        for (var i = startLine; i < lines.Length; i++)
        {
            var rawLine = lines[i];
            if (string.IsNullOrWhiteSpace(rawLine))
                continue;

            var parts         = SplitCsvLine(rawLine);
            var requiredIndex = Math.Max(timeIndex, Math.Max(sideIndex, atrIndex));
            if (parts.Length <= requiredIndex)
            {
                skipped++;
                continue;
            }

            if (symbolIndex >= 0)
            {
                if (parts.Length <= symbolIndex || !IsCurrentSymbol(parts[symbolIndex]))
                    continue;
            }

            if (!TryParseSignal(parts[timeIndex], parts[sideIndex], parts[atrIndex], out var signal))
            {
                skipped++;
                continue;
            }

            _signals[signal.Time] = signal;
            loaded++;
        }

        Print("[{0}] CSV loaded: {1} signals | skipped: {2} | symbol: {3} | offset: {4}h | {5}",
            BotLabel, loaded, skipped, SymbolName, CsvTimeOffsetHours, CsvPath);
    }

    private bool TryParseSignal(string timeText, string sideText, string atrText, out SignalInfo signal)
    {
        signal = default;

        if (!TryParseTime(timeText.Trim(), out var sourceTime))
            return false;

        var side = sideText.Trim().ToUpperInvariant();
        if      (side is "1" or "BUY"  or "LONG")  side = "BUY";
        else if (side is "-1" or "SELL" or "SHORT") side = "SELL";
        else    return false;

        if (!double.TryParse(atrText.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var atr) || atr <= 0)
            return false;

        signal = new SignalInfo(TrimToMinute(sourceTime.AddHours(CsvTimeOffsetHours)), side, atr);
        return true;
    }

    private bool TryParseTime(string text, out DateTime time)
    {
        return DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out time)
            || DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm",    CultureInfo.InvariantCulture, DateTimeStyles.None, out time);
    }

    // ── Order placement ──────────────────────────────────────────────────────

    private void PlaceSignalOrder(SignalInfo signal, DateTime barTime, DateTime nextBarTime,
        double signalBarClockHours, int signalsInBar)
    {
        // High and Low of the just-closed signal bar (Last(1) in OnBarClosed).
        var high  = Bars.HighPrices.Last(1);
        var low   = Bars.LowPrices.Last(1);
        var isBuy = signal.Side == "BUY";

        var tradeType    = isBuy ? TradeType.Buy : TradeType.Sell;
        var ktpMultiplier = GetKtpMultiplier(KtpFibLevel);

        // Entry: breakout beyond signal candle by X price units.
        var entryPrice = isBuy ? high + XOffset : low - XOffset;

        // SL: anchored to the opposite side of the signal candle, extended by X.
        // This places SL outside the full candle structure, not relative to entry.
        var slPrice = isBuy ? low - XOffset : high + XOffset;

        // SL distance spans the entire candle range plus X on both sides.
        var slDist = Math.Abs(entryPrice - slPrice);   // = (High - Low) + 2 * X
        var slPips = slDist / Symbol.PipSize;

        // TP: fixed extension from entry using ATR from the CSV signal bar.
        var tpDist   = ktpMultiplier * signal.Atr;
        var tpPrice1 = isBuy ? entryPrice + tpDist : entryPrice - tpDist;
        var tpPips1  = tpDist / Symbol.PipSize;

        var spreadPips          = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
        var spreadToSlPercent   = slPips  > 0 ? spreadPips / slPips  * 100.0 : 0;
        var spreadToTpPercent   = tpPips1 > 0 ? spreadPips / tpPips1 * 100.0 : 0;

        if (MaxSpreadToStopLossPercent > 0 && spreadToSlPercent > MaxSpreadToStopLossPercent)
        {
            _skipSpreadFilterCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: spread {3:F1}p is {4:F2}% of SL ({5:F2}%), above limit {6:F2}%.",
                BotLabel, signal.Side, barTime,
                spreadPips, spreadToSlPercent, slPips, MaxSpreadToStopLossPercent);
            return;
        }

        var legCount      = ExitMode == ExitModeOption.TwoLegs ? 2 : 1;
        var legRiskPercent = RiskPercent / legCount;
        var legVolume     = CalculateRiskVolume(slPips, legRiskPercent);

        if (legVolume < Symbol.VolumeInUnitsMin)
        {
            _skipVolumeCount++;
            Print("[{0}] ERROR: {1}% risk / {2} leg(s) = {3:F3}% leg risk → volume {4} below minimum {5}. SL: {6:F1}p.",
                BotLabel, RiskPercent, legCount, legRiskPercent, legVolume, Symbol.VolumeInUnitsMin, slPips);
            return;
        }

        var placedCount = 0;

        // Leg 1: fixed TP at Entry ± KTP × ATR.
        if (PlaceOneLeg(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar,
            tradeType, 1, legCount, legVolume, legRiskPercent,
            entryPrice, slPrice, tpPrice1, high, low, ktpMultiplier))
            placedCount++;

        // Leg 2 (TwoLegs only): no fixed TP — SL trailed via SMA20 each bar close.
        if (ExitMode == ExitModeOption.TwoLegs)
        {
            if (PlaceOneLeg(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar,
                tradeType, 2, legCount, legVolume, legRiskPercent,
                entryPrice, slPrice, null, high, low, ktpMultiplier))
                placedCount++;
        }

        var summary  = placedCount == legCount ? "SIGNAL CLUSTER COMPLETE" : "SIGNAL CLUSTER PARTIAL";
        var rr       = slPips > 0 ? tpPips1 / slPips : 0;
        Print("[{0}] {1} | {2} at {3:yyyy-MM-dd HH:mm} | Mode: {4} | KTP: {5:F3} (L{6}) | Legs: {7}/{8} | TotalRisk: {9:F1}% | LegRisk: {10:F3}% | X: {11} | Entry: {12} | SL: {13} ({14:F1}p) | TP1: {15} ({16:F1}p) | RR: {17:F2} | ATR: {18:F4} | High: {19} | Low: {20} | Spread/SL: {21:F2}% | Spread/TP: {22:F2}%",
            BotLabel, summary, signal.Side, barTime,
            ExitMode, ktpMultiplier, KtpFibLevel,
            placedCount, legCount,
            RiskPercent, legRiskPercent,
            XOffset, entryPrice, slPrice, slPips,
            tpPrice1, tpPips1, rr,
            signal.Atr, high, low,
            spreadToSlPercent, spreadToTpPercent);
    }

    private bool PlaceOneLeg(SignalInfo signal, DateTime barTime, DateTime nextBarTime,
        double signalBarClockHours, int signalsInBar,
        TradeType tradeType, int legNumber, int legCount,
        double volume, double legRiskPercent,
        double entryPrice, double slPrice, double? tpPrice,
        double high, double low, double ktpMultiplier)
    {
        var slPips = Math.Abs(entryPrice - slPrice) / Symbol.PipSize;
        double? tpPips = tpPrice.HasValue
            ? (double?)(Math.Abs(tpPrice.Value - entryPrice) / Symbol.PipSize)
            : null;

        var result = PlaceStopOrder(
            tradeType,
            SymbolName,
            volume,
            entryPrice,
            GetLegLabel(legNumber),
            slPips,
            tpPips,
            ProtectionType.Relative);

        if (!result.IsSuccessful)
        {
            _placeOrderErrorCount++;
            Print("[{0}] ERROR placing {1} leg {2} stop at {3}: {4}",
                BotLabel, signal.Side, legNumber, entryPrice, result.Error);
            return false;
        }

        _stopOrdersPlacedCount++;

        if (result.PendingOrder != null)
            _pendingOrderCreatedBarCounts[result.PendingOrder.Id] = Bars.Count;

        var legType  = legNumber == 2 && ExitMode == ExitModeOption.TwoLegs ? "TRAIL" : "FIXED";
        var tpLabel  = tpPrice.HasValue
            ? tpPrice.Value.ToString(CultureInfo.InvariantCulture) + " (" + (tpPips ?? 0).ToString("F1", CultureInfo.InvariantCulture) + "p)"
            : "none (SMA20 trailing)";
        Print("[{0}] {1} STOP leg {2}/{3} [{4}] | Bar: {5:yyyy-MM-dd HH:mm}->{6:yyyy-MM-dd HH:mm} ({7:F2}h) | SigTime: {8:yyyy-MM-dd HH:mm} | SigInBar: {9} | Entry: {10} | SL: {11} ({12:F1}p) | TP: {13} | KTP: {14:F3} | ATR: {15:F4} | High: {16} | Low: {17} | X: {18} | LegRisk: {19:F3}% | Lots: {20:F2} | PipSize: {21}",
            BotLabel, signal.Side,
            legNumber, legCount, legType,
            barTime, nextBarTime, signalBarClockHours,
            signal.Time, signalsInBar,
            entryPrice, slPrice, slPips,
            tpLabel, ktpMultiplier,
            signal.Atr, high, low, XOffset,
            legRiskPercent,
            Symbol.VolumeInUnitsToQuantity(volume),
            Symbol.PipSize);
        return true;
    }

    // ── Risk sizing ──────────────────────────────────────────────────────────

    private double CalculateRiskVolume(double stopLossPips, double riskPercent)
    {
        if (stopLossPips <= 0)
            return 0;

        var riskAmount = Account.Balance * riskPercent / 100.0;
        var volume     = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);
        return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
    }

    // ── KTP Fibonacci table ──────────────────────────────────────────────────

    private static double GetKtpMultiplier(int level) => level switch
    {
        1 => 1.272,
        2 => 1.618,
        3 => 2.000,
        4 => 2.272,   // ≈ 2.3, default per Combo lecture
        5 => 2.618,
        6 => 3.618,
        _ => 2.272,
    };

    // ── Label helpers ────────────────────────────────────────────────────────

    private string GetLegLabel(int legNumber) => $"{BotLabel}_L{legNumber}";

    private bool IsMyBotLabel(string label)
    {
        if (string.IsNullOrWhiteSpace(label))
            return false;
        return label == BotLabel || label.StartsWith($"{BotLabel}_L", StringComparison.Ordinal);
    }

    // Trailing legs are always Leg 2, identified by label suffix _L2.
    private bool IsTrailingLeg(string label) => label == GetLegLabel(2);

    private int GetLegNumber(string label)
    {
        var prefix = $"{BotLabel}_L";
        if (!label.StartsWith(prefix, StringComparison.Ordinal))
            return 0;
        return int.TryParse(label[prefix.Length..], NumberStyles.Integer, CultureInfo.InvariantCulture, out var n)
            ? n : 0;
    }

    // ── Position helpers ─────────────────────────────────────────────────────

    private static bool ShouldImproveStopLoss(Position position, double newSl)
    {
        if (!position.StopLoss.HasValue)
            return true;
        return position.TradeType == TradeType.Buy
            ? newSl > position.StopLoss.Value
            : newSl < position.StopLoss.Value;
    }

    private TradeType GetTradeType(SignalInfo signal) =>
        signal.Side == "BUY" ? TradeType.Buy : TradeType.Sell;

    private static TradeType GetOppositeTradeType(TradeType t) =>
        t == TradeType.Buy ? TradeType.Sell : TradeType.Buy;

    private PendingOrder[] GetMyPendingOrders() =>
        PendingOrders.Where(o => IsMyBotLabel(o.Label) && o.SymbolName == SymbolName).ToArray();

    private Position[] GetMyPositions() =>
        Positions.Where(p => IsMyBotLabel(p.Label) && p.SymbolName == SymbolName).ToArray();

    private int GetMyOpenOrderCount(TradeType t) =>
        GetMyPositions().Count(p => p.TradeType == t)
        + GetMyPendingOrders().Count(o => o.TradeType == t);

    // ── Order management ─────────────────────────────────────────────────────

    private void CancelMyPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
        {
            var r = CancelPendingOrder(order);
            if (!r.IsSuccessful)
                Print("[{0}] ERROR cancelling pending {1}: {2}", BotLabel, order.Id, r.Error);
            else
                _pendingOrderCreatedBarCounts.Remove(order.Id);
        }
    }

    private void CancelOppositePendingOrders(TradeType tradeType)
    {
        var opp = GetOppositeTradeType(tradeType);
        foreach (var order in GetMyPendingOrders().Where(o => o.TradeType == opp))
        {
            var r = CancelPendingOrder(order);
            if (!r.IsSuccessful)
                Print("[{0}] ERROR cancelling opposite pending {1}: {2}", BotLabel, order.Id, r.Error);
            else
                _pendingOrderCreatedBarCounts.Remove(order.Id);
        }
    }

    private void CancelExpiredPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
        {
            if (!_pendingOrderCreatedBarCounts.TryGetValue(order.Id, out var createdAt))
            {
                _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
                continue;
            }

            var age = Bars.Count - createdAt;
            if (age < CancelPendingAfterBars)
                continue;

            var r = CancelPendingOrder(order);
            if (!r.IsSuccessful)
            {
                Print("[{0}] ERROR cancelling expired pending {1}: {2}", BotLabel, order.Id, r.Error);
                continue;
            }

            _pendingOrderCreatedBarCounts.Remove(order.Id);
            _pendingExpiredCount++;
            Print("[{0}] PENDING EXPIRED {1} | Order: {2} | Age: {3}/{4} bars",
                BotLabel, order.TradeType, order.Id, age, CancelPendingAfterBars);
        }
    }

    private bool CloseOppositePositions(TradeType tradeType)
    {
        var opp       = GetOppositeTradeType(tradeType);
        var positions = GetMyPositions().Where(p => p.TradeType == opp).ToArray();

        if (positions.Length == 0)
            return true;

        Print("[{0}] REVERSE SIGNAL {1}: closing {2} opposite position(s).",
            BotLabel, tradeType, positions.Length);

        var allClosed = true;
        foreach (var p in positions)
        {
            var r = ClosePosition(p);
            if (!r.IsSuccessful)
            {
                allClosed = false;
                Print("[{0}] ERROR closing opposite position {1}: {2}", BotLabel, p.Id, r.Error);
            }
        }
        return allClosed;
    }

    private void CloseMyPositions()
    {
        foreach (var p in GetMyPositions())
        {
            var r = ClosePosition(p);
            if (!r.IsSuccessful)
                Print("[{0}] ERROR closing position {1}: {2}", BotLabel, p.Id, r.Error);
        }
    }

    private void TrackExistingPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
            _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
    }

    // ── Daily drawdown ───────────────────────────────────────────────────────

    private void ResetDailyDrawdownBaseline()
    {
        _drawdownDay           = Server.Time.Date;
        _dayStartEquity        = Account.Equity;
        _dailyDrawdownTriggered = false;

        Print("[{0}] DD baseline reset | Day: {1:yyyy-MM-dd} | StartEquity: {2:F2} | Balance: {3:F2} | Limit: {4:F1}%",
            BotLabel, _drawdownDay, _dayStartEquity, Account.Balance, MaxDailyDrawdownPercent);
    }

    private bool CheckDailyDrawdownLimit()
    {
        if (Server.Time.Date != _drawdownDay)
            ResetDailyDrawdownBaseline();

        if (_dayStartEquity <= 0)
            return false;

        var dd = (_dayStartEquity - Account.Equity) / _dayStartEquity * 100.0;
        if (dd < MaxDailyDrawdownPercent && !_dailyDrawdownTriggered)
            return false;

        if (!_dailyDrawdownTriggered)
        {
            _dailyDrawdownTriggered = true;
            Print("[{0}] DAILY DD LIMIT HIT | DD: {1:F2}% | Equity: {2:F2} | StartEquity: {3:F2}. Halted until next day.",
                BotLabel, dd, Account.Equity, _dayStartEquity);
        }

        CancelMyPendingOrders();
        CloseMyPositions();
        return true;
    }

    // ── Event handlers ───────────────────────────────────────────────────────

    private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
    {
        var pos = args.Position;
        if (!IsMyBotLabel(pos.Label) || pos.SymbolName != SymbolName)
            return;

        _pendingOrderCreatedBarCounts.Remove(args.PendingOrder.Id);
        _pendingFilledCount++;

        Print("[{0}] FILLED {1} | Label: {2} | Leg: {3} | Entry: {4} | SL: {5} | TP: {6}",
            BotLabel, pos.TradeType, pos.Label, GetLegNumber(pos.Label),
            pos.EntryPrice, pos.StopLoss, pos.TakeProfit);
    }

    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        var order = args.PendingOrder;
        if (!IsMyBotLabel(order.Label) || order.SymbolName != SymbolName)
            return;

        _pendingOrderCreatedBarCounts.Remove(order.Id);
        _pendingCancelledCount++;

        Print("[{0}] PENDING CANCELLED {1} | Label: {2} | Reason: {3}",
            BotLabel, order.TradeType, order.Label, args.Reason);
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var pos = args.Position;
        if (!IsMyBotLabel(pos.Label) || pos.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Label: {2} | Leg: {3} | NetPnL: {4:F2} | Pips: {5:F1} | Reason: {6}",
            BotLabel, pos.TradeType, pos.Label, GetLegNumber(pos.Label),
            pos.NetProfit, pos.Pips, args.Reason);
    }

    // ── CSV helpers ──────────────────────────────────────────────────────────

    private bool TryGetSignalForClosedBar(DateTime barTime, DateTime nextBarTime,
        out SignalInfo signal, out int signalsInBar)
    {
        signal      = default;
        signalsInBar = 0;

        SignalInfo latest   = default;
        var        hasSignal = false;

        foreach (var candidate in _signals.Values)
        {
            if (candidate.Time < barTime || candidate.Time >= nextBarTime)
                continue;

            signalsInBar++;
            if (hasSignal && candidate.Time <= latest.Time)
                continue;

            latest    = candidate;
            hasSignal = true;
        }

        if (!hasSignal)
            return false;

        signal = latest;

        if (signalsInBar > 1)
            Print("[{0}] Multiple signals in bar | {1:yyyy-MM-dd HH:mm}->{2:yyyy-MM-dd HH:mm} | Count: {3} | Using: {4:yyyy-MM-dd HH:mm} {5}",
                BotLabel, barTime, nextBarTime, signalsInBar, signal.Time, signal.Side);

        return true;
    }

    private bool IsCurrentSymbol(string csvSymbol) =>
        csvSymbol.Trim().Equals(SymbolName,    StringComparison.OrdinalIgnoreCase)
        || csvSymbol.Trim().Equals(Symbol.Name, StringComparison.OrdinalIgnoreCase);

    private static int FindColumn(string[] header, params string[] names)
    {
        for (var i = 0; i < header.Length; i++)
        {
            var col = header[i].Trim();
            if (names.Any(n => col.Equals(n, StringComparison.OrdinalIgnoreCase)))
                return i;
        }
        return -1;
    }

    private static string[] SplitCsvLine(string line) =>
        line.Split(',').Select(p => p.Trim()).ToArray();

    private static DateTime TrimToMinute(DateTime value) =>
        new(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0, value.Kind);

    // ── Signal data ──────────────────────────────────────────────────────────

    private readonly struct SignalInfo
    {
        public SignalInfo(DateTime time, string side, double atr)
        {
            Time = time;
            Side = side;
            Atr  = atr;
        }

        public DateTime Time { get; }
        public string   Side { get; }
        public double   Atr  { get; }
    }
}
