using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

public enum SignalTimeReference
{
    BarCloseTime,
    BarOpenTime
}

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_KNNwithEMA : Robot
{
    [Parameter("Label", DefaultValue = "SEN_KNNwithEMA_AI_M45")]
    public string Label { get; set; }

    [Parameter("Signals CSV Path", DefaultValue = @"D:\Auto Trading\SEN05\raw_signal_exports\ai_trend_GOLD_M45_signals.csv")]
    public string SignalsCsvPath { get; set; }

    [Parameter("Risk % per Trade", DefaultValue = 0.5, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Take Profit (R)", DefaultValue = 1.5, MinValue = 0.1, MaxValue = 10.0, Step = 0.1)]
    public double TakeProfitR { get; set; }

    [Parameter("Max Open Positions", DefaultValue = 1, MinValue = 1, MaxValue = 20, Step = 1)]
    public int MaxOpenPositions { get; set; }

    [Parameter("Signal Time Reference", DefaultValue = SignalTimeReference.BarCloseTime)]
    public SignalTimeReference SignalTimeReference { get; set; }

    [Parameter("Signal Time Offset Hours", DefaultValue = 0, MinValue = -24, MaxValue = 24, Step = 1)]
    public int SignalTimeOffsetHours { get; set; }

    [Parameter("Match Signal Inside Closed Bar", DefaultValue = true)]
    public bool MatchSignalInsideClosedBar { get; set; }

    [Parameter("Skip Opposite If Open", DefaultValue = true)]
    public bool SkipOppositeIfOpen { get; set; }

    [Parameter("Max Account DD %", DefaultValue = 30.0, MinValue = 1.0, MaxValue = 90.0, Step = 1.0)]
    public double MaxAccountDrawdownPercent { get; set; }

    private readonly Dictionary<DateTime, List<SignalDefinition>> _signalsByTime = new();
    private readonly List<SignalDefinition> _allSignals = new();
    private readonly HashSet<int> _processedSignalLines = new();
    private double _startingEquity;
    private DateTime _firstSignalTime = DateTime.MaxValue;
    private DateTime _lastSignalTime = DateTime.MinValue;

    protected override void OnStart()
    {
        if (!ValidateParameters())
            return;

        _startingEquity = Account.Equity;

        if (!LoadSignalsFromCsv())
            return;

        Positions.Opened += OnPositionOpened;
        Positions.Closed += OnPositionClosed;
        Positions.Modified += OnPositionModified;

        Print("[{0}] Started on {1} {2}. CSV: {3}", Label, SymbolName, TimeFrame, SignalsCsvPath);
        Print("[{0}] Signals loaded: {1}. First: {2:yyyy-MM-dd HH:mm}. Last: {3:yyyy-MM-dd HH:mm}. Time reference: {4}. Offset hours: {5}. Match inside closed bar: {6}.",
            Label, CountLoadedSignals(), _firstSignalTime, _lastSignalTime, SignalTimeReference, SignalTimeOffsetHours, MatchSignalInsideClosedBar);

        if (!IsGoldSymbol())
            Print("[{0}] WARNING: CSV signals are for Gold. Current symbol is {1}.", Label, SymbolName);

        if (!IsLikelyM45TimeFrame())
            Print("[{0}] WARNING: CSV signals are M45. Current chart timeframe is {1}.", Label, TimeFrame);
    }

    protected override void OnBar()
    {
        if (Bars.Count < 2)
            return;

        if (IsEquityGuardTriggered())
            return;

        var closedBarOpenTime = TrimToMinute(Bars.OpenTimes.Last(1));
        var closedBarCloseTime = TrimToMinute(Bars.OpenTimes.Last(0));
        var signalTime = SignalTimeReference == SignalTimeReference.BarCloseTime
            ? closedBarCloseTime
            : closedBarOpenTime;

        var signals = FindSignalsForClosedBar(closedBarOpenTime, closedBarCloseTime, signalTime);
        if (signals.Count == 0)
            return;

        foreach (var signal in signals)
        {
            if (!_processedSignalLines.Add(signal.SourceLine))
                continue;

            TryOpenFromSignal(signal);
        }
    }

    protected override void OnStop()
    {
        Positions.Opened -= OnPositionOpened;
        Positions.Closed -= OnPositionClosed;
        Positions.Modified -= OnPositionModified;

        Print("[{0}] Stopped.", Label);
    }

    private void TryOpenFromSignal(SignalDefinition signal)
    {
        if (double.IsNaN(signal.StopLossPrice) || signal.StopLossPrice <= 0)
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: missing/invalid SL.",
                Label, signal.SourceLine, signal.Time, signal.TradeType);
            return;
        }

        var positions = GetMyPositions();
        if (positions.Length >= MaxOpenPositions)
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: max open positions reached ({4}/{5}).",
                Label, signal.SourceLine, signal.Time, signal.TradeType, positions.Length, MaxOpenPositions);
            return;
        }

        if (SkipOppositeIfOpen && HasOppositePosition(signal.TradeType, positions))
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: opposite position is already open.",
                Label, signal.SourceLine, signal.Time, signal.TradeType);
            return;
        }

        var entryEstimate = signal.TradeType == TradeType.Buy ? Symbol.Ask : Symbol.Bid;
        if (!IsStopLossOnCorrectSide(signal.TradeType, entryEstimate, signal.StopLossPrice))
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: SL {4} is on the wrong side of entry estimate {5}.",
                Label, signal.SourceLine, signal.Time, signal.TradeType, signal.StopLossPrice, entryEstimate);
            return;
        }

        var stopLossPips = Math.Abs(entryEstimate - signal.StopLossPrice) / Symbol.PipSize;
        if (stopLossPips <= 0)
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: invalid SL distance.",
                Label, signal.SourceLine, signal.Time, signal.TradeType);
            return;
        }

        var volumeInUnits = CalculateVolumeInUnits(stopLossPips);
        if (volumeInUnits < Symbol.VolumeInUnitsMin)
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: risk {4:F2}% produces volume below minimum. SL distance: {5:F1} pips.",
                Label, signal.SourceLine, signal.Time, signal.TradeType, RiskPercent, stopLossPips);
            return;
        }

        var takeProfitPips = stopLossPips * TakeProfitR;
        var result = ExecuteMarketOrder(signal.TradeType, SymbolName, volumeInUnits, Label, stopLossPips, takeProfitPips);

        if (!result.IsSuccessful)
        {
            Print("[{0}] ERROR opening line {1} {2} at {3:yyyy-MM-dd HH:mm}: {4}",
                Label, signal.SourceLine, signal.TradeType, signal.Time, result.Error);
            return;
        }

        var position = result.Position;
        var exactTakeProfit = CalculateTakeProfitPrice(position.EntryPrice, signal.StopLossPrice, signal.TradeType);
        var modifyResult = ModifyPosition(position, signal.StopLossPrice, exactTakeProfit, ProtectionType.Absolute);

        if (!modifyResult.IsSuccessful)
        {
            Print("[{0}] WARNING: opened line {1} {2} but exact SL/TP modify failed: {3}. Initial SL/TP pips were used.",
                Label, signal.SourceLine, position.TradeType, modifyResult.Error);
        }

        Print("[{0}] SIGNAL line {1} {2:yyyy-MM-dd HH:mm} -> OPEN {3} | Entry: {4} | SL: {5} | TP: {6} | Lots: {7:F2} | Risk: {8:F2}%",
            Label,
            signal.SourceLine,
            signal.Time,
            signal.TradeType,
            position.EntryPrice,
            signal.StopLossPrice,
            exactTakeProfit,
            Symbol.VolumeInUnitsToQuantity(volumeInUnits),
            RiskPercent);
    }

    private double CalculateVolumeInUnits(double stopLossPips)
    {
        var riskAmount = Account.Equity * RiskPercent / 100.0;
        var rawVolume = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);
        var rawLots = Symbol.VolumeInUnitsToQuantity(rawVolume);
        var roundedLots = Math.Floor(rawLots * 100.0) / 100.0;

        if (roundedLots <= 0)
            return 0;

        var roundedVolume = Symbol.QuantityToVolumeInUnits(roundedLots);
        return Symbol.NormalizeVolumeInUnits(roundedVolume, RoundingMode.Down);
    }

    private double CalculateTakeProfitPrice(double entryPrice, double stopLossPrice, TradeType tradeType)
    {
        var riskDistance = Math.Abs(entryPrice - stopLossPrice);
        return tradeType == TradeType.Buy
            ? entryPrice + riskDistance * TakeProfitR
            : entryPrice - riskDistance * TakeProfitR;
    }

    private bool LoadSignalsFromCsv()
    {
        _signalsByTime.Clear();
        _allSignals.Clear();
        _processedSignalLines.Clear();
        _firstSignalTime = DateTime.MaxValue;
        _lastSignalTime = DateTime.MinValue;

        if (string.IsNullOrWhiteSpace(SignalsCsvPath))
        {
            Print("[{0}] ERROR: Signals CSV Path is empty.", Label);
            Stop();
            return false;
        }

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
            Print("[{0}] ERROR: cannot read CSV file: {1}", Label, ex.Message);
            Stop();
            return false;
        }

        if (lines.Length < 2)
        {
            Print("[{0}] ERROR: CSV must include a header and at least one signal row.", Label);
            Stop();
            return false;
        }

        var header = SplitCsvLine(lines[0]);
        var timeIndex = FindColumnIndex(header, "bartime", "time", "datetime");
        var sideIndex = FindColumnIndex(header, "side", "size", "signal", "direction");
        var stopLossIndex = FindColumnIndex(header, "sl_price", "sl", "stoploss", "stop_loss", "stop_loss_price");

        if (timeIndex < 0 || sideIndex < 0 || stopLossIndex < 0)
        {
            Print("[{0}] ERROR: CSV header must contain bartime, side/size, and sl_price/sl columns. Header: {1}",
                Label, lines[0]);
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
                Print("[{0}] WARNING: malformed CSV row skipped at line {1}: {2}", Label, i + 1, lines[i]);
                continue;
            }

            var timeText = fields[timeIndex].Trim();
            var sideText = fields[sideIndex].Trim();
            var stopLossText = fields[stopLossIndex].Trim();

            if (!DateTime.TryParseExact(timeText, "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var sourceTime))
            {
                Print("[{0}] WARNING: invalid bartime skipped at line {1}: {2}", Label, i + 1, timeText);
                continue;
            }

            if (!TryParseTradeType(sideText, out var tradeType))
            {
                Print("[{0}] WARNING: invalid side/size skipped at line {1}: {2}", Label, i + 1, sideText);
                continue;
            }

            var stopLossPrice = double.NaN;
            if (!string.IsNullOrWhiteSpace(stopLossText) &&
                !double.TryParse(stopLossText, NumberStyles.Float, CultureInfo.InvariantCulture, out stopLossPrice))
            {
                Print("[{0}] WARNING: invalid SL value at line {1}: {2}", Label, i + 1, stopLossText);
                stopLossPrice = double.NaN;
            }

            var signalTime = TrimToMinute(sourceTime.AddHours(SignalTimeOffsetHours));
            var signal = new SignalDefinition(signalTime, tradeType, stopLossPrice, i + 1);
            AddSignal(signal);
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
        if (!_signalsByTime.TryGetValue(signal.Time, out var signalsAtTime))
        {
            signalsAtTime = new List<SignalDefinition>();
            _signalsByTime.Add(signal.Time, signalsAtTime);
        }

        signalsAtTime.Add(signal);
        _allSignals.Add(signal);

        if (signal.Time < _firstSignalTime)
            _firstSignalTime = signal.Time;

        if (signal.Time > _lastSignalTime)
            _lastSignalTime = signal.Time;
    }

    private List<SignalDefinition> FindSignalsForClosedBar(DateTime closedBarOpenTime, DateTime closedBarCloseTime, DateTime exactSignalTime)
    {
        var result = new List<SignalDefinition>();
        var includedLines = new HashSet<int>();

        if (_signalsByTime.TryGetValue(exactSignalTime, out var exactSignals))
            AddSignals(result, includedLines, exactSignals);

        if (!MatchSignalInsideClosedBar)
            return result;

        foreach (var signal in _allSignals)
        {
            if (signal.Time > closedBarOpenTime && signal.Time <= closedBarCloseTime)
            {
                if (includedLines.Add(signal.SourceLine))
                    result.Add(signal);
            }
        }

        return result;
    }

    private static void AddSignals(List<SignalDefinition> target, HashSet<int> includedLines, IEnumerable<SignalDefinition> source)
    {
        foreach (var signal in source)
        {
            if (includedLines.Add(signal.SourceLine))
                target.Add(signal);
        }
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
        return value.Trim().Replace(" ", string.Empty).Replace("-", string.Empty).Replace("_", string.Empty).ToLowerInvariant();
    }

    private bool TryParseTradeType(string sideText, out TradeType tradeType)
    {
        if (sideText.Equals("BUY", StringComparison.OrdinalIgnoreCase) ||
            sideText.Equals("LONG", StringComparison.OrdinalIgnoreCase))
        {
            tradeType = TradeType.Buy;
            return true;
        }

        if (sideText.Equals("SELL", StringComparison.OrdinalIgnoreCase) ||
            sideText.Equals("SHORT", StringComparison.OrdinalIgnoreCase))
        {
            tradeType = TradeType.Sell;
            return true;
        }

        tradeType = TradeType.Buy;
        return false;
    }

    private bool ValidateParameters()
    {
        if (RiskPercent <= 0)
        {
            Print("[{0}] ERROR: Risk % must be positive.", Label);
            Stop();
            return false;
        }

        if (TakeProfitR <= 0)
        {
            Print("[{0}] ERROR: Take Profit R must be positive.", Label);
            Stop();
            return false;
        }

        return true;
    }

    private bool IsStopLossOnCorrectSide(TradeType tradeType, double entryPrice, double stopLossPrice)
    {
        return tradeType == TradeType.Buy
            ? stopLossPrice < entryPrice
            : stopLossPrice > entryPrice;
    }

    private bool HasOppositePosition(TradeType tradeType, Position[] positions)
    {
        foreach (var position in positions)
        {
            if (position.TradeType != tradeType)
                return true;
        }

        return false;
    }

    private Position[] GetMyPositions()
    {
        return Positions.FindAll(Label, SymbolName);
    }

    private bool IsEquityGuardTriggered()
    {
        if (_startingEquity <= 0)
            return false;

        var drawdownPercent = (_startingEquity - Account.Equity) / _startingEquity * 100.0;
        if (drawdownPercent < MaxAccountDrawdownPercent)
            return false;

        Print("[{0}] EQUITY GUARD triggered. Start equity: {1:F2}, current equity: {2:F2}, DD: {3:F2}%. Bot stopped.",
            Label, _startingEquity, Account.Equity, drawdownPercent);
        Stop();
        return true;
    }

    private DateTime TrimToMinute(DateTime value)
    {
        return new DateTime(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0);
    }

    private int CountLoadedSignals()
    {
        var count = 0;
        foreach (var signals in _signalsByTime.Values)
            count += signals.Count;

        return count;
    }

    private bool IsGoldSymbol()
    {
        return SymbolName.IndexOf("GOLD", StringComparison.OrdinalIgnoreCase) >= 0 ||
               SymbolName.IndexOf("XAU", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private bool IsLikelyM45TimeFrame()
    {
        var timeFrameText = TimeFrame.ToString();
        return timeFrameText.IndexOf("45", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private void OnPositionOpened(PositionOpenedEventArgs args)
    {
        var position = args.Position;
        if (position.Label != Label || position.SymbolName != SymbolName)
            return;

        Print("[{0}] OPENED {1} | Volume: {2:F2} lots | Entry: {3} | SL: {4} | TP: {5}",
            Label,
            position.TradeType,
            Symbol.VolumeInUnitsToQuantity(position.VolumeInUnits),
            position.EntryPrice,
            position.StopLoss,
            position.TakeProfit);
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var position = args.Position;
        if (position.Label != Label || position.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Net: {2:F2} | Pips: {3:F1} | Reason: {4}",
            Label, position.TradeType, position.NetProfit, position.Pips, args.Reason);
    }

    private void OnPositionModified(PositionModifiedEventArgs args)
    {
        var position = args.Position;
        if (position.Label != Label || position.SymbolName != SymbolName)
            return;

        Print("[{0}] MODIFIED {1} | SL: {2} | TP: {3}",
            Label, position.TradeType, position.StopLoss, position.TakeProfit);
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
