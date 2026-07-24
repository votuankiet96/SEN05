using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;

namespace cAlgo.Robots;

/*
 * SEN_KNN_Combo_V0 - KNN-filtered Combo CSV signal executor.
 *
 * Workflow:
 *   1) Python KNN Combo exports closed entry-timeframe signals.
 *   2) OnBarClosed matches CSV signals inside the just-closed bar interval.
 *   3) EntryStopPercent = 0 opens a market-order TP-leg cluster immediately.
 *      EntryStopPercent > 0 places a High/Low breakout stop-order cluster.
 *   4) No reverse cleanup and no same-direction skip: every signal is tradable.
 *   5) SL/TP use the Combo BaseRange formula: max(csv ATR, signal candle high-low).
 *   6) When a TP leg closes, remaining legs in the same cluster advance their SL.
 *
 * Expected CSV columns:
 *   bartime,side,atr
 *   bartime,symbol,side,atr
 *   bartime,signal,atr
 *   bartime,symbol,signal,atr
 */

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_KNN_Combo_V0 : Robot
{
    [Parameter("Signal CSV Path", Group = "File",
        DefaultValue = @"D:\Auto Trading\SEN05\raw_signals\knn_combo\knn_combo_US30_H1_20250101_20260514_signals.csv")]
    public string CsvPath { get; set; }

    [Parameter("CSV Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -12, MaxValue = 14, Step = 1)]
    public int CsvTimeOffsetHours { get; set; }

    [Parameter("Bot Label", Group = "Identity", DefaultValue = "SEN_KNN_Combo_V0")]
    public string BotLabel { get; set; }

    [Parameter("Entry Stop x (%)", Group = "Execution",
        DefaultValue = 0, MinValue = 0, MaxValue = 100, Step = 5)]
    public int EntryStopPercent { get; set; }

    [Parameter("Expiration Bars", Group = "Execution",
        DefaultValue = 1, MinValue = 1, MaxValue = 4, Step = 1)]
    public int ExpirationBars { get; set; }

    [Parameter("KSL Fibonacci Level (1-4)", Group = "Execution",
        DefaultValue = 2, MinValue = 1, MaxValue = 4, Step = 1)]
    public int KslFibLevel { get; set; }

    [Parameter("TP Profile (1-15)", Group = "Execution",
        DefaultValue = 15, MinValue = 1, MaxValue = 15, Step = 1)]
    public int TpProfile { get; set; }

    [Parameter("Max Spread / SL % (0=off)", Group = "Execution",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 25.0, Step = 2.5)]
    public double MaxSpreadToStopLossPercent { get; set; }

    [Parameter("Max Signal Bar Clock Hours", Group = "Execution",
        DefaultValue = 0, MinValue = 0, MaxValue = 168, Step = 1)]
    public double MaxSignalBarClockHours { get; set; }

    [Parameter("Risk % per Trade", Group = "Risk",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 3.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Max Daily Drawdown %", Group = "Risk",
        DefaultValue = 10.0, MinValue = 1.0, MaxValue = 100.0, Step = 0.5)]
    public double MaxDailyDrawdownPercent { get; set; }

    [Parameter("Max Concurrent Clusters (0=off)", Group = "Risk",
        DefaultValue = 5, MinValue = 0, MaxValue = 20, Step = 1)]
    public int MaxConcurrentClusters { get; set; }

    private readonly List<SignalInfo> _signals = new();

    private DateTime _drawdownDay;
    private double _dayStartEquity;
    private bool _dailyDrawdownTriggered;

    private int _barsWithoutSignal;
    private int _matchedSignalCount;
    private int _multiSignalBarCount;
    private int _clustersOpened;
    private int _clustersPartial;
    private int _clustersFailed;
    private int _legsPlaced;
    private int _legsFailed;
    private int _skipLongSignalBarCount;
    private int _skipSpreadFilterCount;
    private int _skipVolumeCount;
    private int _skipInvalidRangeCount;
    private int _placeOrderErrorCount;
    private int _slLadderMoves;
    private int _dailyDdTriggerCount;
    private int _skipMaxClustersCount;
    private int _stopOrdersPlaced;
    private int _pendingFilledCount;
    private int _pendingCancelledCount;

    protected override void OnStart()
    {
        Positions.Opened += OnPositionOpened;
        Positions.Closed += OnPositionClosed;
        Positions.Modified += OnPositionModified;
        PendingOrders.Filled += OnPendingOrderFilled;
        PendingOrders.Cancelled += OnPendingOrderCancelled;

        ResetDailyDrawdownBaseline();

        if (!ValidateParameters())
        {
            Stop();
            return;
        }

        LoadSignals();
        if (_signals.Count == 0)
        {
            Print("[{0}] ERROR: no valid KNN Combo signals loaded. Stopping.", BotLabel);
            Stop();
            return;
        }

        var tpLevels = GetTpLevels();
        var tpMultipliers = GetTpMultipliers();
        var executionMode = EntryStopPercent == 0 ? "MarketOrderOnNextBar" : "StopOrderBreakout";
        Print("[{0}] Started | Symbol: {1} | TF: {2} | Mode: {3} | EntryStop: {4}% | ExpirationBars: {5} | KSL: {6} ({7:F3}) | TP Profile: {8} -> Levels [{9}] -> Multipliers [{10}] | Risk: {11:F1}% | DailyDD: {12:F1}%",
            BotLabel,
            SymbolName,
            TimeFrame,
            executionMode,
            EntryStopPercent,
            ExpirationBars,
            KslFibLevel,
            GetKslMultiplier(),
            TpProfile,
            FormatLevels(tpLevels),
            FormatMultipliers(tpMultipliers),
            RiskPercent,
            MaxDailyDrawdownPercent);
    }

    protected override void OnTick()
    {
        CheckDailyDrawdownLimit();
    }

    protected override void OnBarClosed()
    {
        if (Bars.Count < 2)
            return;

        if (CheckDailyDrawdownLimit())
            return;

        if (_signals.Count == 0)
            return;

        var barTime = TrimToMinute(Bars.OpenTimes.Last(1));
        var nextBarTime = TrimToMinute(Bars.OpenTimes.Last(0));

        var signals = GetSignalsForClosedBar(barTime, nextBarTime);
        if (signals.Count == 0)
        {
            _barsWithoutSignal++;
            return;
        }

        if (signals.Count > 1)
            _multiSignalBarCount++;

        var signalBarClockHours = (nextBarTime - barTime).TotalHours;
        if (MaxSignalBarClockHours > 0 && signalBarClockHours > MaxSignalBarClockHours)
        {
            _skipLongSignalBarCount += signals.Count;
            Print("[{0}] {1} signal(s) at {2:yyyy-MM-dd HH:mm} skipped: bar clock span {3:F2}h > MaxSignalBarClockHours {4:F2}h.",
                BotLabel, signals.Count, barTime, signalBarClockHours, MaxSignalBarClockHours);
            return;
        }

        for (var i = 0; i < signals.Count; i++)
        {
            var openClusters = GetOpenClusterCount();
            if (MaxConcurrentClusters > 0 && openClusters >= MaxConcurrentClusters)
            {
                var skipped = signals.Count - i;
                _skipMaxClustersCount += skipped;
                Print("[{0}] {1} signal(s) at {2:yyyy-MM-dd HH:mm} skipped: {3} concurrent clusters open, max {4}.",
                    BotLabel, skipped, barTime, openClusters, MaxConcurrentClusters);
                break;
            }
            _matchedSignalCount++;
            OpenSignalCluster(signals[i], barTime, nextBarTime, signalBarClockHours, signals.Count);
        }
    }

    protected override void OnStop()
    {
        Positions.Opened -= OnPositionOpened;
        Positions.Closed -= OnPositionClosed;
        Positions.Modified -= OnPositionModified;
        PendingOrders.Filled -= OnPendingOrderFilled;
        PendingOrders.Cancelled -= OnPendingOrderCancelled;

        Print("[{0}] Stopped | Signals loaded: {1} | Signals matched: {2} | Multi-signal bars: {3} | Bars without signal: {4}",
            BotLabel, _signals.Count, _matchedSignalCount, _multiSignalBarCount, _barsWithoutSignal);
        Print("[{0}] Summary | ClustersOpened: {1} | Partial: {2} | Failed: {3} | LegsPlaced: {4} | LegsFailed: {5} | Skip LongBar: {6} | Skip Spread: {7} | Skip Volume: {8} | Skip InvalidRange: {9} | Skip MaxClusters: {10} | Place errors: {11} | SLLadderMoves: {12} | DailyDDHits: {13}",
            BotLabel,
            _clustersOpened,
            _clustersPartial,
            _clustersFailed,
            _legsPlaced,
            _legsFailed,
            _skipLongSignalBarCount,
            _skipSpreadFilterCount,
            _skipVolumeCount,
            _skipInvalidRangeCount,
            _skipMaxClustersCount,
            _placeOrderErrorCount,
            _slLadderMoves,
            _dailyDdTriggerCount);
        Print("[{0}] Pending summary | StopOrdersPlaced: {1} | PendingFilled: {2} | PendingCancelled: {3}",
            BotLabel, _stopOrdersPlaced, _pendingFilledCount, _pendingCancelledCount);
    }

    private bool ValidateParameters()
    {
        if (string.IsNullOrWhiteSpace(CsvPath))
        {
            Print("[{0}] ERROR: CSV path is empty.", BotLabel);
            return false;
        }

        if (KslFibLevel < 1 || KslFibLevel > 4)
        {
            Print("[{0}] ERROR: KSL Fibonacci level must be from 1 to 4.", BotLabel);
            return false;
        }

        if (TpProfile < 1 || TpProfile > 15)
        {
            Print("[{0}] ERROR: TP Profile must be from 1 to 15.", BotLabel);
            return false;
        }

        var tpLevels = GetTpLevels();
        if (tpLevels.Length == 0 || tpLevels.Any(level => !IsValidFibLevel(level)))
        {
            Print("[{0}] ERROR: TP Profile produced invalid TP levels.", BotLabel);
            return false;
        }

        if (MaxSpreadToStopLossPercent < 0 || MaxSpreadToStopLossPercent > 25.0)
        {
            Print("[{0}] ERROR: Max Spread / SL % must be from 0 to 25. Use 0 to disable.", BotLabel);
            return false;
        }

        if (EntryStopPercent < 0 || EntryStopPercent > 100)
        {
            Print("[{0}] ERROR: Entry Stop x (%) must be from 0 to 100. Use 0 for market-order baseline.", BotLabel);
            return false;
        }

        if (ExpirationBars < 1 || ExpirationBars > 4)
        {
            Print("[{0}] ERROR: Expiration Bars must be from 1 to 4.", BotLabel);
            return false;
        }

        if (RiskPercent <= 0 || RiskPercent > 3.0)
        {
            Print("[{0}] ERROR: Risk percent must be > 0 and <= 3.", BotLabel);
            return false;
        }

        if (MaxDailyDrawdownPercent <= 0 || MaxDailyDrawdownPercent > 100)
        {
            Print("[{0}] ERROR: Max daily drawdown percent must be > 0 and <= 100.", BotLabel);
            return false;
        }

        if (MaxConcurrentClusters < 0)
        {
            Print("[{0}] ERROR: Max concurrent clusters must be >= 0. Use 0 to disable.", BotLabel);
            return false;
        }

        return true;
    }

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

        var header = SplitCsvLine(lines[0]);
        var hasHeader = header.Any(cell => cell.Equals("bartime", StringComparison.OrdinalIgnoreCase));
        var timeIndex = hasHeader ? FindColumn(header, "bartime", "time", "date", "datetime") : 0;
        var symbolIndex = hasHeader ? FindColumn(header, "symbol", "symbolname") : -1;
        var sideIndex = hasHeader ? FindColumn(header, "side", "signal", "direction") : 1;
        var atrIndex = hasHeader ? FindColumn(header, "atr") : 2;

        if (timeIndex < 0 || sideIndex < 0 || atrIndex < 0)
        {
            Print("[{0}] ERROR: CSV header must include bartime, side/signal, and atr.", BotLabel);
            return;
        }

        var loaded = 0;
        var skipped = 0;
        var startLine = hasHeader ? 1 : 0;

        for (var i = startLine; i < lines.Length; i++)
        {
            var rawLine = lines[i];
            if (string.IsNullOrWhiteSpace(rawLine))
                continue;

            var parts = SplitCsvLine(rawLine);
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

            if (!TryParseSignal(parts[timeIndex], parts[sideIndex], parts[atrIndex], i + 1, out var signal))
            {
                skipped++;
                continue;
            }

            _signals.Add(signal);
            loaded++;
        }

        Print("[{0}] CSV loaded: {1} signals | skipped: {2} | symbol: {3} | offset: {4}h | {5}",
            BotLabel, loaded, skipped, SymbolName, CsvTimeOffsetHours, CsvPath);
    }

    private bool TryParseSignal(string timeText, string sideText, string atrText, int sourceLine, out SignalInfo signal)
    {
        signal = default;

        if (!TryParseTime(timeText.Trim(), out var sourceTime))
            return false;

        var side = sideText.Trim().ToUpperInvariant();
        if (side is "1" or "BUY" or "LONG")
            side = "BUY";
        else if (side is "-1" or "SELL" or "SHORT")
            side = "SELL";
        else
            return false;

        if (!double.TryParse(atrText.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var atr) || atr <= 0)
            return false;

        signal = new SignalInfo(TrimToMinute(sourceTime.AddHours(CsvTimeOffsetHours)), side, atr, sourceLine);
        return true;
    }

    private static bool TryParseTime(string text, out DateTime time)
    {
        return DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out time)
            || DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out time);
    }

    private void OpenSignalCluster(SignalInfo signal, DateTime barTime, DateTime nextBarTime, double signalBarClockHours, int signalsInBar)
    {
        var high = Bars.HighPrices.Last(1);
        var low = Bars.LowPrices.Last(1);
        var isBuy = signal.Side == "BUY";
        var tradeType = isBuy ? TradeType.Buy : TradeType.Sell;
        var signalRange = high - low;
        var baseRange = Math.Max(signal.Atr, signalRange);

        if (baseRange <= 0 || Symbol.PipSize <= 0)
        {
            _skipInvalidRangeCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: invalid BaseRange. ATR: {3} | Range: {4}",
                BotLabel, signal.Side, barTime, signal.Atr, signalRange);
            return;
        }

        var kslMultiplier = GetKslMultiplier();
        var tpMultipliers = GetTpMultipliers();
        var stopLossPips = kslMultiplier * baseRange / Symbol.PipSize;
        var firstTakeProfitPips = tpMultipliers[0] * baseRange / Symbol.PipSize;
        var spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
        var spreadToStopLossPercent = stopLossPips > 0 ? spreadPips / stopLossPips * 100.0 : 0;
        var spreadToFirstTakeProfitPercent = firstTakeProfitPips > 0 ? spreadPips / firstTakeProfitPips * 100.0 : 0;
        var useStopOrder = EntryStopPercent > 0;
        var entryOffset = useStopOrder ? GetEntryOffset(baseRange) : 0;
        var stopEntryPrice = isBuy ? high + entryOffset : low - entryOffset;
        var pendingExpiration = GetPendingOrderExpiration(barTime, nextBarTime);

        if (MaxSpreadToStopLossPercent > 0 && spreadToStopLossPercent > MaxSpreadToStopLossPercent)
        {
            _skipSpreadFilterCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: spread {3:F1}p is {4:F2}% of SL, limit {5:F2}%.",
                BotLabel, signal.Side, barTime, spreadPips, spreadToStopLossPercent, MaxSpreadToStopLossPercent);
            return;
        }

        var legRiskPercent = RiskPercent / tpMultipliers.Length;
        var legVolume = CalculateRiskVolume(stopLossPips, legRiskPercent);
        if (legVolume < Symbol.VolumeInUnitsMin)
        {
            _skipVolumeCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: leg volume {3} below minimum {4}. SL: {5:F1}p | LegRisk: {6:F3}%",
                BotLabel, signal.Side, barTime, legVolume, Symbol.VolumeInUnitsMin, stopLossPips, legRiskPercent);
            return;
        }

        var requestedLegCount = tpMultipliers.Length;
        var placedLegCount = 0;
        for (var i = 0; i < tpMultipliers.Length; i++)
        {
            var legNumber = i + 1;
            var tpMultiplier = tpMultipliers[i];
            var takeProfitPips = tpMultiplier * baseRange / Symbol.PipSize;
            var riskReward = takeProfitPips / stopLossPips;

            var orderPlaced = useStopOrder
                ? PlaceSignalStopOrder(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar,
                    tradeType, legNumber, requestedLegCount, legVolume, legRiskPercent, stopLossPips,
                    takeProfitPips, signalRange, baseRange, kslMultiplier, tpMultiplier, riskReward,
                    stopEntryPrice, entryOffset, pendingExpiration)
                : PlaceSignalMarketOrder(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar,
                    tradeType, legNumber, requestedLegCount, legVolume, legRiskPercent, stopLossPips,
                    takeProfitPips, signalRange, baseRange, kslMultiplier, tpMultiplier, riskReward);

            if (orderPlaced)
            {
                placedLegCount++;
            }
        }

        if (placedLegCount == 0)
            _clustersFailed++;
        else if (placedLegCount < requestedLegCount)
        {
            _clustersPartial++;
            _clustersOpened++;
        }
        else
            _clustersOpened++;

        var clusterMode = useStopOrder ? "STOP" : "MARKET";
        var entryTargetText = useStopOrder ? stopEntryPrice.ToString(CultureInfo.InvariantCulture) : "market";
        Print("[{0}] {1} CLUSTER {2} | {3} at {4:yyyy-MM-dd HH:mm} -> entry bar {5:yyyy-MM-dd HH:mm} | TP Profile: {6} -> Levels [{7}] -> Multipliers [{8}] | Legs placed: {9}/{10} | TotalRisk planned: {11:F1}% | LegRisk: {12:F3}% | EntryStop: {13}% | EntryTarget: {14} | Expiration: {15:yyyy-MM-dd HH:mm} | ATR: {16:F4} | Range: {17:F4} | Base: {18:F4} | KSL: {19:F3} | Spread: {20:F1}p | Spread/SL: {21:F2}% | Spread/TP1: {22:F2}%",
            BotLabel,
            clusterMode,
            placedLegCount == requestedLegCount ? "COMPLETE" : placedLegCount == 0 ? "FAILED" : "PARTIAL",
            signal.Side,
            barTime,
            nextBarTime,
            TpProfile,
            FormatLevels(GetTpLevels()),
            FormatMultipliers(tpMultipliers),
            placedLegCount,
            requestedLegCount,
            RiskPercent,
            legRiskPercent,
            EntryStopPercent,
            entryTargetText,
            pendingExpiration,
            signal.Atr,
            signalRange,
            baseRange,
            kslMultiplier,
            spreadPips,
            spreadToStopLossPercent,
            spreadToFirstTakeProfitPercent);
    }

    private bool PlaceSignalMarketOrder(SignalInfo signal, DateTime barTime, DateTime nextBarTime, double signalBarClockHours, int signalsInBar, TradeType tradeType, int legNumber, int legCount, double volume, double legRiskPercent, double stopLossPips, double takeProfitPips, double signalRange, double baseRange, double kslMultiplier, double tpMultiplier, double riskReward)
    {
        var result = ExecuteMarketOrder(
            tradeType,
            SymbolName,
            volume,
            GetLegLabel(signal.SourceLine, legNumber),
            stopLossPips,
            takeProfitPips);

        if (!result.IsSuccessful)
        {
            _placeOrderErrorCount++;
            _legsFailed++;
            Print("[{0}] ERROR opening {1} market leg {2}/{3} for line {4}: {5}",
                BotLabel, signal.Side, legNumber, legCount, signal.SourceLine, result.Error);
            return false;
        }

        var position = result.Position;
        var isBuy = tradeType == TradeType.Buy;
        var exactStopLoss = isBuy
            ? position.EntryPrice - kslMultiplier * baseRange
            : position.EntryPrice + kslMultiplier * baseRange;
        var exactTakeProfit = isBuy
            ? position.EntryPrice + tpMultiplier * baseRange
            : position.EntryPrice - tpMultiplier * baseRange;

        var modifyResult = ModifyPosition(position, exactStopLoss, exactTakeProfit, ProtectionType.Absolute);
        if (!modifyResult.IsSuccessful)
            Print("[{0}] WARNING line {1} leg {2}/{3}: exact SL/TP modify failed ({4}). Initial pips-based protection retained.",
                BotLabel, signal.SourceLine, legNumber, legCount, modifyResult.Error);

        _legsPlaced++;
        Print("[{0}] {1} MARKET leg {2}/{3} opened | Bar: {4:yyyy-MM-dd HH:mm} -> {5:yyyy-MM-dd HH:mm} ({6:F2}h) | SignalTime: {7:yyyy-MM-dd HH:mm} | SignalsInBar: {8} | Entry: {9} | SL: {10} ({11:F1}p) | TP: {12} ({13:F1}p) | ATR: {14:F4} | Range: {15:F4} | Base: {16:F4} | KSL: {17:F3} | KTP: {18:F3} | RR: {19:F2} | TotalRisk: {20:F1}% | LegRisk: {21:F3}% | Lots: {22:F2}",
            BotLabel,
            signal.Side,
            legNumber,
            legCount,
            barTime,
            nextBarTime,
            signalBarClockHours,
            signal.Time,
            signalsInBar,
            position.EntryPrice,
            exactStopLoss,
            stopLossPips,
            exactTakeProfit,
            takeProfitPips,
            signal.Atr,
            signalRange,
            baseRange,
            kslMultiplier,
            tpMultiplier,
            riskReward,
            RiskPercent,
            legRiskPercent,
            Symbol.VolumeInUnitsToQuantity(volume));
        return true;
    }

    private bool PlaceSignalStopOrder(SignalInfo signal, DateTime barTime, DateTime nextBarTime, double signalBarClockHours, int signalsInBar, TradeType tradeType, int legNumber, int legCount, double volume, double legRiskPercent, double stopLossPips, double takeProfitPips, double signalRange, double baseRange, double kslMultiplier, double tpMultiplier, double riskReward, double entryPrice, double entryOffset, DateTime pendingExpiration)
    {
        var result = PlaceStopOrder(
            tradeType,
            SymbolName,
            volume,
            entryPrice,
            GetLegLabel(signal.SourceLine, legNumber),
            stopLossPips,
            takeProfitPips,
            ProtectionType.Relative,
            pendingExpiration);

        if (!result.IsSuccessful)
        {
            _placeOrderErrorCount++;
            _legsFailed++;
            Print("[{0}] ERROR placing {1} stop leg {2}/{3} for line {4} at {5}: {6}",
                BotLabel, signal.Side, legNumber, legCount, signal.SourceLine, entryPrice, result.Error);
            return false;
        }

        _stopOrdersPlaced++;
        _legsPlaced++;
        Print("[{0}] {1} STOP leg {2}/{3} placed | Bar: {4:yyyy-MM-dd HH:mm} -> {5:yyyy-MM-dd HH:mm} ({6:F2}h) | SignalTime: {7:yyyy-MM-dd HH:mm} | SignalsInBar: {8} | Target: {9} | Expiration: {10:yyyy-MM-dd HH:mm} | Offset: {11}% BaseRange = {12:F1}p | SL: {13:F1}p | TP: {14:F1}p | ATR: {15:F4} | Range: {16:F4} | Base: {17:F4} | KSL: {18:F3} | KTP: {19:F3} | RR: {20:F2} | TotalRisk: {21:F1}% | LegRisk: {22:F3}% | Lots: {23:F2}",
            BotLabel,
            signal.Side,
            legNumber,
            legCount,
            barTime,
            nextBarTime,
            signalBarClockHours,
            signal.Time,
            signalsInBar,
            entryPrice,
            pendingExpiration,
            EntryStopPercent,
            entryOffset / Symbol.PipSize,
            stopLossPips,
            takeProfitPips,
            signal.Atr,
            signalRange,
            baseRange,
            kslMultiplier,
            tpMultiplier,
            riskReward,
            RiskPercent,
            legRiskPercent,
            Symbol.VolumeInUnitsToQuantity(volume));
        return true;
    }

    private double CalculateRiskVolume(double stopLossPips, double riskPercent)
    {
        if (stopLossPips <= 0)
            return 0;

        var riskAmount = Account.Balance * riskPercent / 100.0;
        var volume = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);
        return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
    }

    private DateTime GetPendingOrderExpiration(DateTime barTime, DateTime nextBarTime)
    {
        var barSpan = nextBarTime - barTime;
        if (barSpan <= TimeSpan.Zero)
            barSpan = TimeSpan.FromMinutes(1);

        return barTime.AddTicks(barSpan.Ticks * (ExpirationBars + 1));
    }

    private double GetEntryOffset(double baseRange)
    {
        return baseRange * EntryStopPercent / 100.0;
    }

    private int[] GetTpLevels() => GetTpLevelsByProfile(TpProfile);

    private double[] GetTpMultipliers() => GetTpLevels().Select(GetFibMultiplier).ToArray();

    private static int[] GetTpLevelsByProfile(int profile)
    {
        return profile switch
        {
            1 => new[] { 1 },
            2 => new[] { 2 },
            3 => new[] { 3 },
            4 => new[] { 4 },
            5 => new[] { 1, 2 },
            6 => new[] { 1, 3 },
            7 => new[] { 1, 4 },
            8 => new[] { 2, 3 },
            9 => new[] { 2, 4 },
            10 => new[] { 3, 4 },
            11 => new[] { 1, 2, 3 },
            12 => new[] { 1, 2, 4 },
            13 => new[] { 1, 3, 4 },
            14 => new[] { 2, 3, 4 },
            15 => new[] { 1, 2, 3, 4 },
            _ => Array.Empty<int>()
        };
    }

    private double GetKslMultiplier()
    {
        return KslFibLevel switch
        {
            1 => 1.000,
            2 => 1.272,
            3 => 1.618,
            4 => 2.000,
            _ => 1.272
        };
    }

    private static double GetFibMultiplier(int fibLevel)
    {
        return fibLevel switch
        {
            1 => 1.272,
            2 => 1.618,
            3 => 2.272,
            4 => 2.618,
            _ => 2.272
        };
    }

    private static bool IsValidFibLevel(int fibLevel) => fibLevel >= 1 && fibLevel <= 4;

    private string GetLegLabel(int sourceLine, int legNumber) => $"{BotLabel}_C{sourceLine}_L{legNumber}";

    private bool IsMyBotLabel(string label)
    {
        if (string.IsNullOrWhiteSpace(label))
            return false;

        return label == BotLabel || label.StartsWith($"{BotLabel}_C", StringComparison.Ordinal);
    }

    private int GetClusterId(string label)
    {
        var prefix = $"{BotLabel}_C";
        if (!label.StartsWith(prefix, StringComparison.Ordinal))
            return -1;

        var rest = label[prefix.Length..];
        var legMarker = rest.IndexOf("_L", StringComparison.Ordinal);
        if (legMarker < 0)
            return -1;

        return int.TryParse(rest[..legMarker], NumberStyles.Integer, CultureInfo.InvariantCulture, out var clusterId)
            ? clusterId
            : -1;
    }

    private int GetLegNumber(string label)
    {
        var marker = label.LastIndexOf("_L", StringComparison.Ordinal);
        if (marker < 0)
            return 0;

        return int.TryParse(label[(marker + 2)..], NumberStyles.Integer, CultureInfo.InvariantCulture, out var legNumber)
            ? legNumber
            : 0;
    }

    private Position[] GetMyPositions()
    {
        return Positions
            .Where(position => IsMyBotLabel(position.Label) && position.SymbolName == SymbolName)
            .ToArray();
    }

    private PendingOrder[] GetMyPendingOrders()
    {
        return PendingOrders
            .Where(order => IsMyBotLabel(order.Label) && order.SymbolName == SymbolName)
            .ToArray();
    }

    private int GetOpenClusterCount()
    {
        var openClusters = GetMyPositions()
            .Select(position => GetClusterId(position.Label))
            .Where(id => id >= 0)
            .Distinct();

        var pendingClusters = GetMyPendingOrders()
            .Select(order => GetClusterId(order.Label))
            .Where(id => id >= 0)
            .Distinct();

        return openClusters.Union(pendingClusters).Count();
    }

    private void ApplyStopLossLadder(Position closedPosition)
    {
        var reachedLeg = GetLegNumber(closedPosition.Label);
        var clusterId = GetClusterId(closedPosition.Label);
        var tpMultipliers = GetTpMultipliers();

        if (reachedLeg <= 0 || clusterId < 0 || reachedLeg >= tpMultipliers.Length)
            return;

        var remainingPositions = GetMyPositions()
            .Where(position => GetClusterId(position.Label) == clusterId
                && position.TradeType == closedPosition.TradeType
                && GetLegNumber(position.Label) > reachedLeg)
            .OrderBy(position => GetLegNumber(position.Label))
            .ToArray();

        if (remainingPositions.Length == 0)
        {
            Print("[{0}] SL LADDER | C{1} TP leg {2} hit but no higher leg remains open.",
                BotLabel, clusterId, reachedLeg);
            return;
        }

        foreach (var position in remainingPositions)
        {
            var legNumber = GetLegNumber(position.Label);
            if (!TryGetLadderStopLossPrice(position, legNumber, reachedLeg, tpMultipliers, out var newStopLossPrice))
            {
                Print("[{0}] SL LADDER SKIP | C{1} TP leg {2} hit | Remaining leg {3}: cannot calculate new SL.",
                    BotLabel, clusterId, reachedLeg, legNumber);
                continue;
            }

            newStopLossPrice = Math.Round(newStopLossPrice, Symbol.Digits);
            if (IsTakeProfitAlreadyReachable(position))
            {
                Print("[{0}] SL LADDER SKIP | C{1} TP leg {2} hit | Remaining leg {3}: TP {4} is already reachable by market price. Waiting for platform close.",
                    BotLabel, clusterId, reachedLeg, legNumber, position.TakeProfit);
                continue;
            }

            if (!IsValidStopLossMove(position, newStopLossPrice, out var invalidReason))
            {
                Print("[{0}] SL LADDER SKIP | C{1} TP leg {2} hit | Remaining leg {3}: target SL {4} is not valid now ({5}).",
                    BotLabel, clusterId, reachedLeg, legNumber, newStopLossPrice, invalidReason);
                continue;
            }

            if (!ShouldImproveStopLoss(position, newStopLossPrice))
            {
                Print("[{0}] SL LADDER SKIP | C{1} TP leg {2} hit | Remaining leg {3}: current SL {4} already protects >= target {5}.",
                    BotLabel, clusterId, reachedLeg, legNumber, position.StopLoss, newStopLossPrice);
                continue;
            }

            var oldStopLoss = position.StopLoss;
            var result = ModifyPosition(position, newStopLossPrice, position.TakeProfit, ProtectionType.Absolute);
            if (!result.IsSuccessful)
            {
                Print("[{0}] SL LADDER ERROR | C{1} TP leg {2} hit | Remaining leg {3}: failed moving SL {4} -> {5}: {6}",
                    BotLabel, clusterId, reachedLeg, legNumber, oldStopLoss, newStopLossPrice, result.Error);
                continue;
            }

            _slLadderMoves++;
            Print("[{0}] SL LADDER MOVED | C{1} TP leg {2} hit | Remaining leg {3}: SL {4} -> {5} | TP: {6}",
                BotLabel, clusterId, reachedLeg, legNumber, oldStopLoss, newStopLossPrice, position.TakeProfit);
        }
    }

    private bool TryGetLadderStopLossPrice(Position position, int positionLegNumber, int reachedLeg, double[] activeTpMultipliers, out double stopLossPrice)
    {
        stopLossPrice = 0;

        if (reachedLeg == 1)
        {
            stopLossPrice = position.EntryPrice;
            return true;
        }

        if (positionLegNumber <= 0 || positionLegNumber > activeTpMultipliers.Length || !position.TakeProfit.HasValue)
            return false;

        var positionTpMultiplier = activeTpMultipliers[positionLegNumber - 1];
        if (positionTpMultiplier <= 0)
            return false;

        var lockTpMultiplier = activeTpMultipliers[reachedLeg - 2];
        var baseDistance = Math.Abs(position.TakeProfit.Value - position.EntryPrice) / positionTpMultiplier;
        stopLossPrice = position.TradeType == TradeType.Buy
            ? position.EntryPrice + lockTpMultiplier * baseDistance
            : position.EntryPrice - lockTpMultiplier * baseDistance;
        return true;
    }

    private static bool ShouldImproveStopLoss(Position position, double newStopLossPrice)
    {
        if (!position.StopLoss.HasValue)
            return true;

        return position.TradeType == TradeType.Buy
            ? newStopLossPrice > position.StopLoss.Value
            : newStopLossPrice < position.StopLoss.Value;
    }

    private bool IsTakeProfitAlreadyReachable(Position position)
    {
        if (!position.TakeProfit.HasValue)
            return false;

        var tolerance = Symbol.PipSize > 0 ? Symbol.PipSize : 0;
        return position.TradeType == TradeType.Buy
            ? Symbol.Bid >= position.TakeProfit.Value - tolerance
            : Symbol.Ask <= position.TakeProfit.Value + tolerance;
    }

    private bool IsValidStopLossMove(Position position, double newStopLossPrice, out string reason)
    {
        reason = "";
        if (Symbol.PipSize <= 0)
        {
            reason = "invalid pip size";
            return false;
        }

        var referencePrice = position.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
        var minStopLossPips = GetMinimumDistancePips(Symbol.MinStopLossDistance, referencePrice);
        var minStopLossDistance = minStopLossPips * Symbol.PipSize;

        if (position.TradeType == TradeType.Buy)
        {
            var maxStopLoss = Symbol.Bid - minStopLossDistance;
            if (newStopLossPrice >= maxStopLoss)
            {
                reason = string.Format(CultureInfo.InvariantCulture, "buy SL {0} must be below Bid {1} by at least {2:F1}p", newStopLossPrice, Symbol.Bid, minStopLossPips);
                return false;
            }
        }
        else
        {
            var minStopLoss = Symbol.Ask + minStopLossDistance;
            if (newStopLossPrice <= minStopLoss)
            {
                reason = string.Format(CultureInfo.InvariantCulture, "sell SL {0} must be above Ask {1} by at least {2:F1}p", newStopLossPrice, Symbol.Ask, minStopLossPips);
                return false;
            }
        }

        if (position.TakeProfit.HasValue)
        {
            var minTakeProfitPips = GetMinimumDistancePips(Symbol.MinTakeProfitDistance, referencePrice);
            var minTakeProfitDistance = minTakeProfitPips * Symbol.PipSize;
            if (position.TradeType == TradeType.Buy && position.TakeProfit.Value <= Symbol.Bid + minTakeProfitDistance)
            {
                reason = string.Format(CultureInfo.InvariantCulture, "buy TP {0} is too close to Bid {1}", position.TakeProfit.Value, Symbol.Bid);
                return false;
            }

            if (position.TradeType == TradeType.Sell && position.TakeProfit.Value >= Symbol.Ask - minTakeProfitDistance)
            {
                reason = string.Format(CultureInfo.InvariantCulture, "sell TP {0} is too close to Ask {1}", position.TakeProfit.Value, Symbol.Ask);
                return false;
            }
        }

        return true;
    }

    private double GetMinimumDistancePips(double minimumDistance, double referencePrice)
    {
        if (minimumDistance <= 0 || Symbol.PipSize <= 0)
            return 0;

        return Symbol.MinDistanceType == SymbolMinDistanceType.Percentage
            ? Math.Abs(referencePrice) * minimumDistance / 100.0 / Symbol.PipSize
            : minimumDistance;
    }

    private void ResetDailyDrawdownBaseline()
    {
        _drawdownDay = Server.Time.Date;
        _dayStartEquity = Account.Equity;
        _dailyDrawdownTriggered = false;

        Print("[{0}] Daily drawdown baseline reset | Day: {1:yyyy-MM-dd} | Start Equity: {2:F2} | Balance: {3:F2} | Limit: {4:F1}%",
            BotLabel, _drawdownDay, _dayStartEquity, Account.Balance, MaxDailyDrawdownPercent);
    }

    private bool CheckDailyDrawdownLimit()
    {
        if (Server.Time.Date != _drawdownDay)
            ResetDailyDrawdownBaseline();

        if (_dayStartEquity <= 0)
            return false;

        var drawdownPercent = (_dayStartEquity - Account.Equity) / _dayStartEquity * 100.0;
        if (drawdownPercent < MaxDailyDrawdownPercent && !_dailyDrawdownTriggered)
            return false;

        if (!_dailyDrawdownTriggered)
        {
            _dailyDrawdownTriggered = true;
            _dailyDdTriggerCount++;
            Print("[{0}] DAILY DRAWDOWN LIMIT HIT | DD: {1:F2}% | Equity: {2:F2} | Start Equity: {3:F2} | Balance: {4:F2}. Trading halted until next day.",
                BotLabel, drawdownPercent, Account.Equity, _dayStartEquity, Account.Balance);
        }

        CloseMyPositions();
        return true;
    }

    private void CloseMyPositions()
    {
        CancelMyPendingOrders();

        foreach (var position in GetMyPositions())
        {
            var result = ClosePosition(position);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR closing position {1}: {2}", BotLabel, position.Id, result.Error);
        }
    }

    private void CancelMyPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
        {
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR cancelling pending order {1}: {2}", BotLabel, order.Id, result.Error);
        }
    }

    private List<SignalInfo> GetSignalsForClosedBar(DateTime barTime, DateTime nextBarTime)
    {
        var result = new List<SignalInfo>();
        foreach (var candidate in _signals)
        {
            if (candidate.Time >= barTime && candidate.Time < nextBarTime)
                result.Add(candidate);
        }
        return result;
    }

    private void OnPositionOpened(PositionOpenedEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] OPENED {1} | Label: {2} | Entry: {3} | SL: {4} | TP: {5} | Lots: {6:F2}",
            BotLabel,
            position.TradeType,
            position.Label,
            position.EntryPrice,
            position.StopLoss,
            position.TakeProfit,
            Symbol.VolumeInUnitsToQuantity(position.VolumeInUnits));
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Label: {2} | Cluster: {3} | Leg: {4} | Net PnL: {5:F2} | Pips: {6:F1} | Reason: {7}",
            BotLabel,
            position.TradeType,
            position.Label,
            GetClusterId(position.Label),
            GetLegNumber(position.Label),
            position.NetProfit,
            position.Pips,
            args.Reason);

        if (IsTakeProfitClose(args))
            ApplyStopLossLadder(position);
    }

    private void OnPositionModified(PositionModifiedEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] MODIFIED {1} | Label: {2} | SL: {3} | TP: {4}",
            BotLabel, position.TradeType, position.Label, position.StopLoss, position.TakeProfit);
    }

    private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        _pendingFilledCount++;
        Print("[{0}] FILLED {1} STOP | Label: {2} | Entry: {3} | SL: {4} | TP: {5}",
            BotLabel, position.TradeType, position.Label, position.EntryPrice, position.StopLoss, position.TakeProfit);
    }

    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        var order = args.PendingOrder;
        if (!IsMyBotLabel(order.Label) || order.SymbolName != SymbolName)
            return;

        _pendingCancelledCount++;
        Print("[{0}] CANCELLED {1} STOP | Order: {2} | Label: {3}",
            BotLabel, order.TradeType, order.Id, order.Label);
    }

    private bool IsCurrentSymbol(string csvSymbol)
    {
        return csvSymbol.Trim().Equals(SymbolName, StringComparison.OrdinalIgnoreCase)
            || csvSymbol.Trim().Equals(Symbol.Name, StringComparison.OrdinalIgnoreCase);
    }

    private static int FindColumn(string[] header, params string[] names)
    {
        for (var i = 0; i < header.Length; i++)
        {
            var column = header[i].Trim();
            if (names.Any(name => column.Equals(name, StringComparison.OrdinalIgnoreCase)))
                return i;
        }

        return -1;
    }

    private static string[] SplitCsvLine(string line)
    {
        return line.Split(',').Select(part => part.Trim()).ToArray();
    }

    private static DateTime TrimToMinute(DateTime value)
    {
        return new DateTime(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0, value.Kind);
    }

    private static string FormatMultipliers(double[] values)
    {
        return string.Join(", ", values.Select(value => value.ToString("F3", CultureInfo.InvariantCulture)));
    }

    private static string FormatLevels(int[] values)
    {
        return string.Join(",", values);
    }

    private static bool IsTakeProfitClose(PositionClosedEventArgs args)
    {
        return args.Reason.ToString().IndexOf("TakeProfit", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private readonly struct SignalInfo
    {
        public SignalInfo(DateTime time, string side, double atr, int sourceLine)
        {
            Time = time;
            Side = side;
            Atr = atr;
            SourceLine = sourceLine;
        }

        public DateTime Time { get; }
        public string Side { get; }
        public double Atr { get; }
        public int SourceLine { get; }
    }
}
