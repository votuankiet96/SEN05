using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using cAlgo.API;

namespace cAlgo.Robots;

/*
 * SEN_Combo - simple CSV signal executor.
 *
 * Supported CSV formats:
 *   bartime,side,atr
 *   bartime,symbol,side,atr
 *
 * Entry / SL / TP:
 *   BaseRange = max(ATR, signal candle High - Low)
 *   BUY  -> Buy Stop  | Entry = High + X | SL = Entry - KSL * BaseRange | TP = Entry + KTP * BaseRange
 *   SELL -> Sell Stop | Entry = Low - X  | SL = Entry + KSL * BaseRange | TP = Entry - KTP * BaseRange
 *
 * Management:
 *   Pending orders expire after N closed bars.
 *   Opposite signals close current positions, then place the new signal order.
 *   Same-direction exposure is capped by Max Same Direction Orders.
 *   Daily drawdown protection halts trading and flattens bot exposure for the day.
 */

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_Combo : Robot
{
    [Parameter("Signal CSV Path", Group = "File",
        DefaultValue = @"D:\Auto Trading\SEN05\raw_signals\combo\combo_HK50_H4_20230103_20260512_signals.csv")]
    public string CsvPath { get; set; }

    [Parameter("CSV Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -12, MaxValue = 14, Step = 1)]
    public int CsvTimeOffsetHours { get; set; }

    [Parameter("Bot Label", Group = "Identity", DefaultValue = "SEN_Combo")]
    public string BotLabel { get; set; }

    [Parameter("X Offset", Group = "Execution",
        DefaultValue = 10.0, MinValue = 1, MaxValue = 50, Step = 0.5)]
    public double XOffset { get; set; }

    [Parameter("KSL Fibonacci Level (1-5)", Group = "Execution",
        DefaultValue = 2, MinValue = 1, MaxValue = 5, Step = 1)]
    public int KslFibLevel { get; set; }

    [Parameter("KTP Fibonacci Level (1-5)", Group = "Execution",
        DefaultValue = 4, MinValue = 1, MaxValue = 5, Step = 1)]
    public int KtpFibLevel { get; set; }

    [Parameter("Minimum RR", Group = "Execution",
        DefaultValue = 1.5, MinValue = 1.0, MaxValue = 5.0, Step = 0.1)]
    public double MinimumRiskReward { get; set; }

    [Parameter("Cancel Pending On New Signal", Group = "Execution", DefaultValue = false)]
    public bool CancelPendingOnNewSignal { get; set; }

    [Parameter("Cancel Pending After Bars", Group = "Execution",
        DefaultValue = 3, MinValue = 1, MaxValue = 50, Step = 1)]
    public int CancelPendingAfterBars { get; set; }

    [Parameter("Risk % per Trade", Group = "Risk",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 3.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Max Same Direction Orders", Group = "Risk",
        DefaultValue = 3, MinValue = 1, MaxValue = 20, Step = 1)]
    public int MaxOpenOrders { get; set; }

    [Parameter("Max Daily Drawdown %", Group = "Risk",
        DefaultValue = 10.0, MinValue = 1.0, MaxValue = 100.0, Step = 0.5)]
    public double MaxDailyDrawdownPercent { get; set; }

    private readonly Dictionary<DateTime, SignalInfo> _signals = new();
    private readonly Dictionary<long, int> _pendingOrderCreatedBarCounts = new();
    private DateTime _drawdownDay;
    private double _dayStartEquity;
    private bool _dailyDrawdownTriggered;

    protected override void OnStart()
    {
        PendingOrders.Filled += OnPendingOrderFilled;
        PendingOrders.Cancelled += OnPendingOrderCancelled;
        Positions.Closed += OnPositionClosed;

        ResetDailyDrawdownBaseline();

        if (!ValidateParameters())
        {
            Stop();
            return;
        }

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
        CheckDailyDrawdownLimit();
    }

    protected override void OnBarClosed()
    {
        if (CheckDailyDrawdownLimit())
            return;

        CancelExpiredPendingOrders();

        if (_signals.Count == 0)
            return;

        var barTime = TrimToMinute(Bars.OpenTimes.Last(1));
        if (!_signals.TryGetValue(barTime, out var signal))
            return;

        var tradeType = GetTradeType(signal);

        if (CancelPendingOnNewSignal)
            CancelMyPendingOrders();
        else
            CancelOppositePendingOrders(tradeType);

        if (!CloseOppositePositions(tradeType))
            return;

        var openOrderCount = GetMyOpenOrderCount(tradeType);
        if (openOrderCount >= MaxOpenOrders)
        {
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: max same-direction orders reached ({3}/{4}).",
                BotLabel, signal.Side, barTime, openOrderCount, MaxOpenOrders);
            return;
        }

        PlaceSignalStopOrder(signal, barTime);
    }

    protected override void OnStop()
    {
        Print("[{0}] Stopped. Signals loaded: {1}", BotLabel, _signals.Count);
    }

    private bool ValidateParameters()
    {
        if (string.IsNullOrWhiteSpace(CsvPath))
        {
            Print("[{0}] ERROR: CSV path is empty.", BotLabel);
            return false;
        }

        if (KtpFibLevel < 1 || KtpFibLevel > 5)
        {
            Print("[{0}] ERROR: KTP Fibonacci level must be from 1 to 5.", BotLabel);
            return false;
        }

        if (KslFibLevel < 1 || KslFibLevel > 5)
        {
            Print("[{0}] ERROR: KSL Fibonacci level must be from 1 to 5.", BotLabel);
            return false;
        }

        if (MinimumRiskReward < 1.0)
        {
            Print("[{0}] ERROR: Minimum RR must be at least 1.0.", BotLabel);
            return false;
        }

        var kslMultiplier = GetKslMultiplier();
        var ktpMultiplier = GetKtpMultiplier();
        var riskReward = ktpMultiplier / kslMultiplier;
        if (riskReward < MinimumRiskReward)
        {
            Print("[{0}] ERROR: KTP/KSL RR {1:F2} is below Minimum RR {2:F2}. KSL={3:F3}, KTP={4:F3}.",
                BotLabel, riskReward, MinimumRiskReward, kslMultiplier, ktpMultiplier);
            return false;
        }

        if (RiskPercent <= 0 || RiskPercent > 3.0)
        {
            Print("[{0}] ERROR: Risk percent must be > 0 and <= 3.", BotLabel);
            return false;
        }

        if (MaxOpenOrders < 1)
        {
            Print("[{0}] ERROR: Max same-direction orders must be at least 1.", BotLabel);
            return false;
        }

        if (CancelPendingAfterBars < 1)
        {
            Print("[{0}] ERROR: Cancel pending after bars must be at least 1.", BotLabel);
            return false;
        }

        if (MaxDailyDrawdownPercent <= 0 || MaxDailyDrawdownPercent > 100)
        {
            Print("[{0}] ERROR: Max daily drawdown percent must be > 0 and <= 100.", BotLabel);
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

        var timeIndex = hasHeader ? FindColumn(header, "bartime", "time", "date") : 0;
        var symbolIndex = hasHeader ? FindColumn(header, "symbol", "symbolname") : -1;
        var sideIndex = hasHeader ? FindColumn(header, "side", "signal", "direction") : 1;
        var atrIndex = hasHeader ? FindColumn(header, "atr") : 2;

        if (timeIndex < 0 || sideIndex < 0 || atrIndex < 0)
        {
            Print("[{0}] ERROR: CSV header must include bartime, side and atr.", BotLabel);
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
        if (side is "1" or "BUY" or "LONG")
            side = "BUY";
        else if (side is "-1" or "SELL" or "SHORT")
            side = "SELL";
        else
            return false;

        if (!double.TryParse(atrText.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var atr) || atr <= 0)
            return false;

        signal = new SignalInfo(TrimToMinute(sourceTime.AddHours(CsvTimeOffsetHours)), side, atr);
        return true;
    }

    private bool TryParseTime(string text, out DateTime time)
    {
        return DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out time)
            || DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out time);
    }

    private void PlaceSignalStopOrder(SignalInfo signal, DateTime barTime)
    {
        var high = Bars.HighPrices.Last(1);
        var low = Bars.LowPrices.Last(1);

        var isBuy = signal.Side == "BUY";
        var signalRange = high - low;
        var baseRange = Math.Max(signal.Atr, signalRange);
        var kslMultiplier = GetKslMultiplier();
        var ktpMultiplier = GetKtpMultiplier();
        var tradeType = isBuy ? TradeType.Buy : TradeType.Sell;
        var entryPrice = isBuy ? high + XOffset : low - XOffset;
        var stopLossPrice = isBuy
            ? entryPrice - kslMultiplier * baseRange
            : entryPrice + kslMultiplier * baseRange;
        var takeProfitPrice = isBuy
            ? entryPrice + ktpMultiplier * baseRange
            : entryPrice - ktpMultiplier * baseRange;

        var stopLossPips = Math.Abs(entryPrice - stopLossPrice) / Symbol.PipSize;
        var takeProfitPips = Math.Abs(takeProfitPrice - entryPrice) / Symbol.PipSize;
        var riskReward = takeProfitPips / stopLossPips;
        if (riskReward < MinimumRiskReward)
        {
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: RR {3:F2} < Minimum RR {4:F2}. KSL={5:F3}, KTP={6:F3}.",
                BotLabel, signal.Side, barTime, riskReward, MinimumRiskReward, kslMultiplier, ktpMultiplier);
            return;
        }

        var volume = CalculateRiskVolume(stopLossPips);

        if (volume < Symbol.VolumeInUnitsMin)
        {
            Print("[{0}] ERROR: risk {1:F1}% with SL {2:F1}p gives volume {3}, below symbol minimum {4}.",
                BotLabel, RiskPercent, stopLossPips, volume, Symbol.VolumeInUnitsMin);
            return;
        }

        var result = PlaceStopOrder(
            tradeType,
            SymbolName,
            volume,
            entryPrice,
            BotLabel,
            stopLossPips,
            takeProfitPips,
            ProtectionType.Relative);

        if (!result.IsSuccessful)
        {
            Print("[{0}] ERROR placing {1} stop at {2}: {3}", BotLabel, signal.Side, entryPrice, result.Error);
            return;
        }

        if (result.PendingOrder != null)
            _pendingOrderCreatedBarCounts[result.PendingOrder.Id] = Bars.Count;

        Print("[{0}] {1} STOP placed | Bar: {2:yyyy-MM-dd HH:mm} | Entry: {3} | SL: {4} ({5:F1}p) | TP: {6} ({7:F1}p) | ATR: {8:F4} | Range: {9:F4} | Base: {10:F4} | KSL: {11:F3} | KTP: {12:F3} | RR: {13:F2} | Risk: {14:F1}% | Lots: {15:F2}",
            BotLabel,
            signal.Side,
            barTime,
            entryPrice,
            stopLossPrice,
            stopLossPips,
            takeProfitPrice,
            takeProfitPips,
            signal.Atr,
            signalRange,
            baseRange,
            kslMultiplier,
            ktpMultiplier,
            riskReward,
            RiskPercent,
            Symbol.VolumeInUnitsToQuantity(volume));
    }

    private double CalculateRiskVolume(double stopLossPips)
    {
        if (stopLossPips <= 0)
            return 0;

        var riskAmount = Account.Equity * RiskPercent / 100.0;
        var volume = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);
        return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
    }

    private double GetKtpMultiplier()
    {
        return KtpFibLevel switch
        {
            1 => 1.272,
            2 => 1.618,
            3 => 2.272,
            4 => 2.618,
            5 => 4.236,
            _ => 2.272
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
            5 => 2.618,
            _ => 1.272
        };
    }

    private TradeType GetTradeType(SignalInfo signal)
    {
        return signal.Side == "BUY" ? TradeType.Buy : TradeType.Sell;
    }

    private TradeType GetOppositeTradeType(TradeType tradeType)
    {
        return tradeType == TradeType.Buy ? TradeType.Sell : TradeType.Buy;
    }

    private void CancelMyPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
        {
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR cancelling pending order {1}: {2}", BotLabel, order.Id, result.Error);
            else
                _pendingOrderCreatedBarCounts.Remove(order.Id);
        }
    }

    private void CancelOppositePendingOrders(TradeType tradeType)
    {
        var oppositeTradeType = GetOppositeTradeType(tradeType);
        foreach (var order in GetMyPendingOrders().Where(order => order.TradeType == oppositeTradeType))
        {
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR cancelling opposite pending order {1}: {2}", BotLabel, order.Id, result.Error);
            else
                _pendingOrderCreatedBarCounts.Remove(order.Id);
        }
    }

    private void CancelExpiredPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
        {
            if (!_pendingOrderCreatedBarCounts.TryGetValue(order.Id, out var createdBarCount))
            {
                _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
                continue;
            }

            var barsAlive = Bars.Count - createdBarCount;
            if (barsAlive < CancelPendingAfterBars)
                continue;

            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
            {
                Print("[{0}] ERROR cancelling expired pending order {1}: {2}", BotLabel, order.Id, result.Error);
                continue;
            }

            _pendingOrderCreatedBarCounts.Remove(order.Id);
            Print("[{0}] PENDING EXPIRED {1} | Order: {2} | Bars alive: {3}/{4}",
                BotLabel, order.TradeType, order.Id, barsAlive, CancelPendingAfterBars);
        }
    }

    private PendingOrder[] GetMyPendingOrders()
    {
        return PendingOrders
            .Where(order => order.Label == BotLabel && order.SymbolName == SymbolName)
            .ToArray();
    }

    private Position[] GetMyPositions()
    {
        return Positions.FindAll(BotLabel, SymbolName);
    }

    private int GetMyOpenOrderCount(TradeType tradeType)
    {
        return GetMyPositions().Count(position => position.TradeType == tradeType)
            + GetMyPendingOrders().Count(order => order.TradeType == tradeType);
    }

    private bool CloseOppositePositions(TradeType tradeType)
    {
        var oppositeTradeType = GetOppositeTradeType(tradeType);
        var oppositePositions = GetMyPositions()
            .Where(position => position.TradeType == oppositeTradeType)
            .ToArray();

        if (oppositePositions.Length == 0)
            return true;

        Print("[{0}] REVERSE SIGNAL {1}: closing {2} opposite position(s).",
            BotLabel, tradeType, oppositePositions.Length);

        var allClosed = true;
        foreach (var position in oppositePositions)
        {
            var result = ClosePosition(position);
            if (!result.IsSuccessful)
            {
                allClosed = false;
                Print("[{0}] ERROR closing opposite position {1}: {2}", BotLabel, position.Id, result.Error);
            }
        }

        return allClosed;
    }

    private void CloseMyPositions()
    {
        foreach (var position in GetMyPositions())
        {
            var result = ClosePosition(position);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR closing position {1}: {2}", BotLabel, position.Id, result.Error);
        }
    }

    private void TrackExistingPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
            _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
    }

    private void ResetDailyDrawdownBaseline()
    {
        _drawdownDay = Server.Time.Date;
        _dayStartEquity = Account.Equity;
        _dailyDrawdownTriggered = false;

        Print("[{0}] Daily drawdown baseline reset | Day: {1:yyyy-MM-dd} | Equity: {2:F2} | Limit: {3:F1}%",
            BotLabel, _drawdownDay, _dayStartEquity, MaxDailyDrawdownPercent);
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
            Print("[{0}] DAILY DRAWDOWN LIMIT HIT | DD: {1:F2}% | Equity: {2:F2} | Start Equity: {3:F2}. Trading halted until next day.",
                BotLabel, drawdownPercent, Account.Equity, _dayStartEquity);
        }

        CancelMyPendingOrders();
        CloseMyPositions();
        return true;
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

    private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
    {
        var position = args.Position;
        if (position.Label != BotLabel || position.SymbolName != SymbolName)
            return;

        _pendingOrderCreatedBarCounts.Remove(args.PendingOrder.Id);

        Print("[{0}] FILLED {1} | Entry: {2} | SL: {3} | TP: {4}",
            BotLabel, position.TradeType, position.EntryPrice, position.StopLoss, position.TakeProfit);
    }

    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        var order = args.PendingOrder;
        if (order.Label != BotLabel || order.SymbolName != SymbolName)
            return;

        _pendingOrderCreatedBarCounts.Remove(order.Id);

        Print("[{0}] PENDING CANCELLED {1} | Reason: {2}", BotLabel, order.TradeType, args.Reason);
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var position = args.Position;
        if (position.Label != BotLabel || position.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Net PnL: {2:F2} | Pips: {3:F1} | Reason: {4}",
            BotLabel, position.TradeType, position.NetProfit, position.Pips, args.Reason);
    }

    private readonly struct SignalInfo
    {
        public SignalInfo(DateTime time, string side, double atr)
        {
            Time = time;
            Side = side;
            Atr = atr;
        }

        public DateTime Time { get; }
        public string Side { get; }
        public double Atr { get; }
    }
}
