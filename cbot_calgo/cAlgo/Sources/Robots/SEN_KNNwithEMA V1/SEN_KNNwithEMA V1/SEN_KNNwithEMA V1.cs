using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;

namespace cAlgo.Robots;

/*
 * SEN_KNNwithEMAV1 - CSV signal executor with single-order risk management.
 *
 * Workflow:
 *   1) OnStart loads CSV signals (bartime, side, sl_price) into memory.
 *   2) OnBar runs when the next bar opens, matches signals exactly at the closed bar close time.
 *   3) Each signal may open one market order if bot exposure is below MaxOpenPositions.
 *   4) SL is the fixed sl_price from CSV.
 *   5) TP is calculated from the actual fill entry using RiskRewardRatio:
 *      BUY  TP = entry + |entry - sl_price| * RiskRewardRatio
 *      SELL TP = entry - |entry - sl_price| * RiskRewardRatio
 *
 * Risk:
 *   RiskPercent = risk per order, sized from Account.Balance.
 *   MaxOpenPositions caps total open positions for this bot and symbol.
 *   Daily DD guard uses Account.Equity, flattens bot positions, and resets on the next server day.
 */

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_KNNwithEMAV1 : Robot
{
    // --- Identity ---
    [Parameter("Label", Group = "Identity", DefaultValue = "SEN_KNNwithEMAV1")]
    public string Label { get; set; }

    // --- File ---
    [Parameter("Signals CSV Path", Group = "File",
        DefaultValue = @"D:\Auto Trading\SEN05\raw_signal_exports\ai_trend_GOLD_M45_signals.csv")]
    public string SignalsCsvPath { get; set; }

    [Parameter("Signal Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -24, MaxValue = 24, Step = 1)]
    public int SignalTimeOffsetHours { get; set; }

    // --- Execution ---
    [Parameter("Risk Reward Ratio", Group = "Execution",
        DefaultValue = 1.5, MinValue = 0.5, MaxValue = 10.0, Step = 0.25)]
    public double RiskRewardRatio { get; set; }

    [Parameter("Max Spread / SL % (0=off)", Group = "Execution",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 25.0, Step = 2.5)]
    public double MaxSpreadToStopLossPercent { get; set; }

    // --- Risk ---
    [Parameter("Risk % per Order", Group = "Risk",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Max Open Positions", Group = "Risk",
        DefaultValue = 5, MinValue = 1, MaxValue = 50, Step = 1)]
    public int MaxOpenPositions { get; set; }

    [Parameter("Allow Opposite Positions", Group = "Risk", DefaultValue = true)]
    public bool AllowOppositePositions { get; set; }

    [Parameter("Max Daily Drawdown %", Group = "Risk",
        DefaultValue = 5.0, MinValue = 1.0, MaxValue = 50.0, Step = 0.5)]
    public double MaxDailyDrawdownPercent { get; set; }

    // --- Runtime state ---
    private readonly Dictionary<DateTime, List<SignalDefinition>> _signalsByTime = new();
    private readonly HashSet<int> _processedSignalLines = new();
    private DateTime _firstSignalTime = DateTime.MaxValue;
    private DateTime _lastSignalTime = DateTime.MinValue;

    // --- Daily drawdown state ---
    private DateTime _drawdownDay;
    private double _dayStartEquity;
    private bool _dailyDrawdownTriggered;

    // --- Diagnostics ---
    private int _signalsMatched;
    private int _ordersOpened;
    private int _ordersFailed;
    private int _skipMaxPositions;
    private int _skipOpposite;
    private int _skipSpread;
    private int _skipVolume;
    private int _skipInvalidStopLoss;
    private int _dailyDdTriggerCount;

    protected override void OnStart()
    {
        ResetDailyDrawdownBaseline();

        if (!ValidateParameters())
            return;

        var existingPositions = GetMyPositions();
        if (existingPositions.Length > 0)
            Print("[{0}] WARNING: {1} existing bot position(s) found. MaxOpenPositions will include them.",
                Label, existingPositions.Length);

        Positions.Opened += OnPositionOpened;
        Positions.Closed += OnPositionClosed;
        Positions.Modified += OnPositionModified;

        if (!LoadSignalsFromCsv())
            return;

        Print("[{0}] Started | Symbol: {1} | TF: {2} | RR: {3:F2} | RiskPerOrder: {4:F1}% | MaxOpenPositions: {5} | AllowOpposite: {6} | DailyDD: {7:F1}% | MaxSpread/SL: {8}",
            Label,
            SymbolName,
            TimeFrame,
            RiskRewardRatio,
            RiskPercent,
            MaxOpenPositions,
            AllowOppositePositions,
            MaxDailyDrawdownPercent,
            MaxSpreadToStopLossPercent > 0 ? MaxSpreadToStopLossPercent.ToString("F2", CultureInfo.InvariantCulture) + "%" : "off");
        Print("[{0}] Signals loaded: {1} | First: {2:yyyy-MM-dd HH:mm} | Last: {3:yyyy-MM-dd HH:mm} | SignalTime: BarCloseTime | OffsetHours: {4}",
            Label, CountLoadedSignals(), _firstSignalTime, _lastSignalTime, SignalTimeOffsetHours);
    }

    protected override void OnTick()
    {
        // Daily DD is checked on every tick so protection does not wait for the next bar close.
        CheckDailyDrawdownLimit();
    }

    protected override void OnBar()
    {
        if (Bars.Count < 2)
            return;

        if (CheckDailyDrawdownLimit())
            return;

        var closedBarCloseTime = TrimToMinute(Bars.OpenTimes.Last(0));
        var signals = FindSignalsAtCloseTime(closedBarCloseTime);
        if (signals.Count == 0)
            return;

        foreach (var signal in signals)
        {
            // Mark processed before opening so broker errors do not cause retry spam.
            if (!_processedSignalLines.Add(signal.SourceLine))
                continue;

            _signalsMatched++;
            TryOpenOrder(signal);
        }
    }

    protected override void OnStop()
    {
        Positions.Opened -= OnPositionOpened;
        Positions.Closed -= OnPositionClosed;
        Positions.Modified -= OnPositionModified;

        Print("[{0}] Stopped | SignalsMatched: {1} | OrdersOpened: {2} | OrdersFailed: {3} | SkipMaxPositions: {4} | SkipOpposite: {5} | SkipSpread: {6} | SkipVolume: {7} | SkipInvalidSL: {8} | DailyDDTriggered: {9}",
            Label,
            _signalsMatched,
            _ordersOpened,
            _ordersFailed,
            _skipMaxPositions,
            _skipOpposite,
            _skipSpread,
            _skipVolume,
            _skipInvalidStopLoss,
            _dailyDdTriggerCount);
    }

    private void TryOpenOrder(SignalDefinition signal)
    {
        var tradeType = signal.TradeType;
        var isBuy = tradeType == TradeType.Buy;
        var positions = GetMyPositions();

        if (positions.Length >= MaxOpenPositions)
        {
            _skipMaxPositions++;
            Print("[{0}] SKIP line {1} {2}: max open positions reached ({3}/{4}).",
                Label, signal.SourceLine, tradeType, positions.Length, MaxOpenPositions);
            return;
        }

        if (!AllowOppositePositions && HasOppositePosition(tradeType, positions))
        {
            _skipOpposite++;
            Print("[{0}] SKIP line {1} {2}: opposite position is already open and AllowOppositePositions=false.",
                Label, signal.SourceLine, tradeType);
            return;
        }

        var estEntry = isBuy ? Symbol.Ask : Symbol.Bid;
        if (!IsStopLossOnCorrectSide(tradeType, estEntry, signal.StopLossPrice))
        {
            _skipInvalidStopLoss++;
            Print("[{0}] SKIP line {1} {2}: csvSL {3} is on wrong side of entry estimate {4}.",
                Label, signal.SourceLine, tradeType, signal.StopLossPrice, estEntry);
            return;
        }

        var stopLossPips = Math.Abs(estEntry - signal.StopLossPrice) / Symbol.PipSize;
        if (stopLossPips <= 0)
        {
            _skipInvalidStopLoss++;
            Print("[{0}] SKIP line {1} {2}: invalid SL distance (csvSL={3}, estEntry={4}).",
                Label, signal.SourceLine, tradeType, signal.StopLossPrice, estEntry);
            return;
        }

        if (MaxSpreadToStopLossPercent > 0)
        {
            var spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            var spreadPct = spreadPips / stopLossPips * 100.0;
            if (spreadPct > MaxSpreadToStopLossPercent)
            {
                _skipSpread++;
                Print("[{0}] SKIP line {1} {2}: spread {3:F1}p = {4:F2}% of SL ({5:F1}p), limit {6:F2}%.",
                    Label, signal.SourceLine, tradeType, spreadPips, spreadPct, stopLossPips, MaxSpreadToStopLossPercent);
                return;
            }
        }

        var volume = CalculateVolumeInUnits(stopLossPips, RiskPercent);
        if (volume < Symbol.VolumeInUnitsMin)
        {
            _skipVolume++;
            Print("[{0}] SKIP line {1} {2}: risk {3:F2}% produces volume below minimum. SL distance: {4:F1}p.",
                Label, signal.SourceLine, tradeType, RiskPercent, stopLossPips);
            return;
        }

        var takeProfitPips = stopLossPips * RiskRewardRatio;
        var result = ExecuteMarketOrder(
            tradeType,
            SymbolName,
            volume,
            GetOrderLabel(signal.SourceLine),
            stopLossPips,
            takeProfitPips);

        if (!result.IsSuccessful)
        {
            _ordersFailed++;
            Print("[{0}] ERROR opening line {1} {2}: {3}",
                Label, signal.SourceLine, tradeType, result.Error);
            return;
        }

        var position = result.Position;
        if (!IsStopLossOnCorrectSide(tradeType, position.EntryPrice, signal.StopLossPrice))
        {
            _ordersFailed++;
            Print("[{0}] WARNING line {1} {2}: csvSL {3} on wrong side of actual entry {4} after fill. Closing invalid position.",
                Label, signal.SourceLine, tradeType, signal.StopLossPrice, position.EntryPrice);
            var closeResult = ClosePosition(position);
            if (!closeResult.IsSuccessful)
                Print("[{0}] ERROR closing invalid position {1}: {2}", Label, position.Id, closeResult.Error);
            return;
        }

        var exactTakeProfit = CalculateTakeProfitPrice(position.EntryPrice, signal.StopLossPrice, tradeType);
        var modifyResult = ModifyPosition(position, signal.StopLossPrice, exactTakeProfit, ProtectionType.Absolute);
        if (!modifyResult.IsSuccessful)
        {
            Print("[{0}] WARNING line {1} {2}: exact SL/TP modify failed ({3}). Initial pips-based protection retained.",
                Label, signal.SourceLine, tradeType, modifyResult.Error);
        }

        _ordersOpened++;
        Print("[{0}] ORDER OPENED | Line: {1} | {2} | Entry: {3} | SL: {4} | TP: {5} | RR: {6:F2} | SL: {7:F1}p | Lots: {8:F2} | Risk: {9:F2}%",
            Label,
            signal.SourceLine,
            tradeType,
            position.EntryPrice,
            signal.StopLossPrice,
            exactTakeProfit,
            RiskRewardRatio,
            Math.Abs(position.EntryPrice - signal.StopLossPrice) / Symbol.PipSize,
            Symbol.VolumeInUnitsToQuantity(volume),
            RiskPercent);
    }

    private double CalculateVolumeInUnits(double stopLossPips, double riskPercent)
    {
        if (stopLossPips <= 0)
            return 0;

        // Use realized balance so floating PnL does not inflate/deflate next order size.
        var riskAmount = Account.Balance * riskPercent / 100.0;
        var volume = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);
        return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
    }

    private double CalculateTakeProfitPrice(double entryPrice, double stopLossPrice, TradeType tradeType)
    {
        var riskDistance = Math.Abs(entryPrice - stopLossPrice);
        return tradeType == TradeType.Buy
            ? entryPrice + riskDistance * RiskRewardRatio
            : entryPrice - riskDistance * RiskRewardRatio;
    }

    private bool HasOppositePosition(TradeType tradeType, Position[] positions)
    {
        var opposite = tradeType == TradeType.Buy ? TradeType.Sell : TradeType.Buy;
        return positions.Any(position => position.TradeType == opposite);
    }

    private Position[] GetMyPositions()
    {
        return Positions
            .Where(position => IsMyBotLabel(position.Label) && position.SymbolName == SymbolName)
            .ToArray();
    }

    private bool IsMyBotLabel(string label)
    {
        if (string.IsNullOrWhiteSpace(label))
            return false;

        // Keep compatibility with previous V1 labels so DD/max-position guards still see them.
        return label == Label
            || label.StartsWith(Label + "_S", StringComparison.Ordinal)
            || label.StartsWith(Label + "_C", StringComparison.Ordinal);
    }

    private string GetOrderLabel(int sourceLineId)
    {
        return $"{Label}_S{sourceLineId}";
    }

    private void ResetDailyDrawdownBaseline()
    {
        _drawdownDay = Server.Time.Date;
        _dayStartEquity = Account.Equity;
        _dailyDrawdownTriggered = false;

        Print("[{0}] Daily DD baseline reset | Day: {1:yyyy-MM-dd} | StartEquity: {2:F2} | Balance: {3:F2} | Limit: {4:F1}%",
            Label, _drawdownDay, _dayStartEquity, Account.Balance, MaxDailyDrawdownPercent);
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
            Print("[{0}] DAILY DD LIMIT HIT | DD: {1:F2}% | Equity: {2:F2} | StartEquity: {3:F2} | Balance: {4:F2}. Paused until tomorrow.",
                Label, drawdownPercent, Account.Equity, _dayStartEquity, Account.Balance);
        }

        CloseMyPositions();
        return true;
    }

    private void CloseMyPositions()
    {
        foreach (var position in GetMyPositions())
        {
            var result = ClosePosition(position);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR closing position {1} ({2}): {3}", Label, position.Id, position.Label, result.Error);
        }
    }

    private bool ValidateParameters()
    {
        if (string.IsNullOrWhiteSpace(SignalsCsvPath))
        {
            Print("[{0}] ERROR: Signals CSV Path is empty.", Label);
            Stop();
            return false;
        }

        if (RiskRewardRatio < 0.5 || RiskRewardRatio > 10.0)
        {
            Print("[{0}] ERROR: Risk Reward Ratio must be from 0.5 to 10.", Label);
            Stop();
            return false;
        }

        if (MaxSpreadToStopLossPercent < 0 || MaxSpreadToStopLossPercent > 25.0)
        {
            Print("[{0}] ERROR: Max Spread / SL % must be from 0 to 25. Use 0 to disable.", Label);
            Stop();
            return false;
        }

        if (RiskPercent <= 0 || RiskPercent > 10.0)
        {
            Print("[{0}] ERROR: Risk % per Order must be > 0 and <= 10.", Label);
            Stop();
            return false;
        }

        if (MaxOpenPositions < 1)
        {
            Print("[{0}] ERROR: Max Open Positions must be at least 1.", Label);
            Stop();
            return false;
        }

        if (MaxDailyDrawdownPercent <= 0 || MaxDailyDrawdownPercent > 50)
        {
            Print("[{0}] ERROR: Max Daily Drawdown % must be > 0 and <= 50.", Label);
            Stop();
            return false;
        }

        return true;
    }

    private bool LoadSignalsFromCsv()
    {
        _signalsByTime.Clear();
        _processedSignalLines.Clear();
        _firstSignalTime = DateTime.MaxValue;
        _lastSignalTime = DateTime.MinValue;

        if (!System.IO.File.Exists(SignalsCsvPath))
        {
            Print("[{0}] ERROR: CSV file not found: {1}", Label, SignalsCsvPath);
            Stop();
            return false;
        }

        string[] lines;
        try
        {
            lines = System.IO.File.ReadAllLines(SignalsCsvPath);
        }
        catch (Exception ex)
        {
            Print("[{0}] ERROR: cannot read CSV: {1}", Label, ex.Message);
            Stop();
            return false;
        }

        if (lines.Length < 2)
        {
            Print("[{0}] ERROR: CSV must have a header row and at least one signal row.", Label);
            Stop();
            return false;
        }

        var header = SplitCsvLine(lines[0]);
        var timeIndex = FindColumnIndex(header, "bartime", "time", "datetime");
        var sideIndex = FindColumnIndex(header, "side", "size", "signal", "direction");
        var stopLossIndex = FindColumnIndex(header, "sl_price", "sl", "stoploss", "stop_loss", "stop_loss_price");

        if (timeIndex < 0 || sideIndex < 0 || stopLossIndex < 0)
        {
            Print("[{0}] ERROR: CSV header must contain bartime, side, and sl_price. Header: {1}", Label, lines[0]);
            Stop();
            return false;
        }

        for (var i = 1; i < lines.Length; i++)
        {
            if (string.IsNullOrWhiteSpace(lines[i]))
                continue;

            var fields = SplitCsvLine(lines[i]);
            var maxIndex = Math.Max(timeIndex, Math.Max(sideIndex, stopLossIndex));
            if (fields.Length <= maxIndex)
            {
                Print("[{0}] WARNING: malformed row skipped at line {1}", Label, i + 1);
                continue;
            }

            if (!DateTime.TryParseExact(fields[timeIndex].Trim(), "yyyy-MM-dd HH:mm",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out var sourceTime))
            {
                Print("[{0}] WARNING: invalid bartime at line {1}: {2}", Label, i + 1, fields[timeIndex]);
                continue;
            }

            if (!TryParseTradeType(fields[sideIndex].Trim(), out var tradeType))
            {
                Print("[{0}] WARNING: invalid side at line {1}: {2}", Label, i + 1, fields[sideIndex]);
                continue;
            }

            var stopLossText = fields[stopLossIndex].Trim();
            if (string.IsNullOrWhiteSpace(stopLossText) ||
                !double.TryParse(stopLossText, NumberStyles.Float, CultureInfo.InvariantCulture, out var stopLossPrice) ||
                stopLossPrice <= 0)
            {
                Print("[{0}] WARNING: invalid SL value skipped at line {1}: {2}", Label, i + 1, stopLossText);
                continue;
            }

            var signalTime = TrimToMinute(sourceTime.AddHours(SignalTimeOffsetHours));
            AddSignal(new SignalDefinition(signalTime, tradeType, stopLossPrice, i + 1));
        }

        if (CountLoadedSignals() == 0)
        {
            Print("[{0}] ERROR: no valid signals loaded from CSV.", Label);
            Stop();
            return false;
        }

        return true;
    }

    private void AddSignal(SignalDefinition signal)
    {
        if (!_signalsByTime.TryGetValue(signal.Time, out var list))
        {
            list = new List<SignalDefinition>();
            _signalsByTime[signal.Time] = list;
        }

        list.Add(signal);

        if (signal.Time < _firstSignalTime)
            _firstSignalTime = signal.Time;

        if (signal.Time > _lastSignalTime)
            _lastSignalTime = signal.Time;
    }

    private List<SignalDefinition> FindSignalsAtCloseTime(DateTime closeTime)
    {
        return _signalsByTime.TryGetValue(closeTime, out var signals)
            ? new List<SignalDefinition>(signals)
            : new List<SignalDefinition>();
    }

    private int CountLoadedSignals()
    {
        var count = 0;
        foreach (var list in _signalsByTime.Values)
            count += list.Count;

        return count;
    }

    private void OnPositionOpened(PositionOpenedEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] OPENED {1} | Label: {2} | Entry: {3} | SL: {4} | TP: {5} | Lots: {6:F2}",
            Label,
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

        Print("[{0}] CLOSED {1} | Label: {2} | Net: {3:F2} | Pips: {4:F1} | Reason: {5}",
            Label, position.TradeType, position.Label, position.NetProfit, position.Pips, args.Reason);
    }

    private void OnPositionModified(PositionModifiedEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] MODIFIED {1} | Label: {2} | SL: {3} | TP: {4}",
            Label, position.TradeType, position.Label, position.StopLoss, position.TakeProfit);
    }

    private static bool IsStopLossOnCorrectSide(TradeType tradeType, double entryPrice, double stopLossPrice)
    {
        return tradeType == TradeType.Buy
            ? stopLossPrice < entryPrice
            : stopLossPrice > entryPrice;
    }

    private static bool TryParseTradeType(string text, out TradeType tradeType)
    {
        if (text.Equals("BUY", StringComparison.OrdinalIgnoreCase) ||
            text.Equals("LONG", StringComparison.OrdinalIgnoreCase))
        {
            tradeType = TradeType.Buy;
            return true;
        }

        if (text.Equals("SELL", StringComparison.OrdinalIgnoreCase) ||
            text.Equals("SHORT", StringComparison.OrdinalIgnoreCase))
        {
            tradeType = TradeType.Sell;
            return true;
        }

        tradeType = TradeType.Buy;
        return false;
    }

    private static string[] SplitCsvLine(string line)
    {
        return line.Split(',');
    }

    private static int FindColumnIndex(string[] header, params string[] names)
    {
        for (var i = 0; i < header.Length; i++)
        {
            var column = NormalizeHeader(header[i]);
            foreach (var name in names)
            {
                if (column == NormalizeHeader(name))
                    return i;
            }
        }

        return -1;
    }

    private static string NormalizeHeader(string value)
    {
        return value.Trim().Replace(" ", "").Replace("-", "").Replace("_", "").ToLowerInvariant();
    }

    private static DateTime TrimToMinute(DateTime value)
    {
        return new DateTime(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0);
    }

    private sealed class SignalDefinition
    {
        public SignalDefinition(DateTime time, TradeType tradeType, double stopLossPrice, int sourceLine)
        {
            Time = time;
            TradeType = tradeType;
            StopLossPrice = stopLossPrice;
            SourceLine = sourceLine;
        }

        public DateTime Time { get; }
        public TradeType TradeType { get; }
        public double StopLossPrice { get; }
        public int SourceLine { get; }
    }
}
