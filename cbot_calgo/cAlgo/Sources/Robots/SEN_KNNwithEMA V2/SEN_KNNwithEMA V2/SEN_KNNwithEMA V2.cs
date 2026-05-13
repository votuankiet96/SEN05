using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

public enum SignalTimeReference
{
    BarCloseTime,
    BarOpenTime
}

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_KNNwithEMAV2 : Robot
{
    private const int SplitLegCount = 2;
    private const string LegCommentPrefix = "SEN_SPLIT";
    private const string TakeProfitLegTag = "TP1";
    private const string RunnerLegTag = "RUNNER";

    [Parameter("Label", DefaultValue = "SEN_KNNwithEMA_AI_M45")]
    public string Label { get; set; }

    [Parameter("Signals CSV Path", DefaultValue = @"D:\Auto Trading\SEN05\raw_signal_exports\ai_trend_GOLD_M45_signals.csv")]
    public string SignalsCsvPath { get; set; }

    [Parameter("Risk % per Trade", DefaultValue = 0.5, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Take Profit (R)", DefaultValue = 1.5, MinValue = 0.1, MaxValue = 10.0, Step = 0.1)]
    public double TakeProfitR { get; set; }

    [Parameter("Max Open Positions", DefaultValue = 2, MinValue = 2, MaxValue = 20, Step = 1)]
    public int MaxOpenPositions { get; set; }

    [Parameter("Runner EMA Fast", DefaultValue = 13, MinValue = 2, MaxValue = 200, Step = 1)]
    public int RunnerEmaFastPeriod { get; set; }

    [Parameter("Runner EMA Slow", DefaultValue = 34, MinValue = 3, MaxValue = 300, Step = 1)]
    public int RunnerEmaSlowPeriod { get; set; }

    [Parameter("Move Runner SL to BE", DefaultValue = true)]
    public bool MoveRunnerStopToBreakEven { get; set; }

    [Parameter("BE Buffer (pips)", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 50.0, Step = 0.1)]
    public double BreakEvenBufferPips { get; set; }

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
    private ExponentialMovingAverage _runnerEmaFast;
    private ExponentialMovingAverage _runnerEmaSlow;

    protected override void OnStart()
    {
        if (!ValidateParameters())
            return;

        _startingEquity = Account.Equity;
        _runnerEmaFast = Indicators.ExponentialMovingAverage(Bars.ClosePrices, RunnerEmaFastPeriod);
        _runnerEmaSlow = Indicators.ExponentialMovingAverage(Bars.ClosePrices, RunnerEmaSlowPeriod);

        if (!LoadSignalsFromCsv())
            return;

        Positions.Opened += OnPositionOpened;
        Positions.Closed += OnPositionClosed;
        Positions.Modified += OnPositionModified;

        Print("[{0}] Started on {1} {2}. CSV: {3}", Label, SymbolName, TimeFrame, SignalsCsvPath);
        Print("[{0}] Signals loaded: {1}. First: {2:yyyy-MM-dd HH:mm}. Last: {3:yyyy-MM-dd HH:mm}. Time reference: {4}. Offset hours: {5}. Match inside closed bar: {6}.",
            Label, CountLoadedSignals(), _firstSignalTime, _lastSignalTime, SignalTimeReference, SignalTimeOffsetHours, MatchSignalInsideClosedBar);
        Print("[{0}] Split TP enabled: {1} equal legs. TP leg closes at {2:F2}R. Runner exits on EMA{3}/EMA{4} reverse cross. Move runner SL to BE: {5}, BE buffer: {6:F1} pips.",
            Label, SplitLegCount, TakeProfitR, RunnerEmaFastPeriod, RunnerEmaSlowPeriod, MoveRunnerStopToBreakEven, BreakEvenBufferPips);

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

        ManageRunnerExits();

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
        if (positions.Length + SplitLegCount > MaxOpenPositions)
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: not enough position slots for split entry ({4} open, need {5}, max {6}).",
                Label, signal.SourceLine, signal.Time, signal.TradeType, positions.Length, SplitLegCount, MaxOpenPositions);
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

        var totalVolumeInUnits = CalculateVolumeInUnits(stopLossPips);
        if (totalVolumeInUnits < Symbol.VolumeInUnitsMin)
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: risk {4:F2}% produces volume below minimum. SL distance: {5:F1} pips.",
                Label, signal.SourceLine, signal.Time, signal.TradeType, RiskPercent, stopLossPips);
            return;
        }

        var legVolumeInUnits = CalculateSplitLegVolume(totalVolumeInUnits);
        if (legVolumeInUnits < Symbol.VolumeInUnitsMin)
        {
            Print("[{0}] SKIP line {1} {2:yyyy-MM-dd HH:mm} {3}: split leg volume is below minimum. Total lots: {4:F2}, leg lots: {5:F2}. Increase risk or reduce SL distance.",
                Label,
                signal.SourceLine,
                signal.Time,
                signal.TradeType,
                Symbol.VolumeInUnitsToQuantity(totalVolumeInUnits),
                Symbol.VolumeInUnitsToQuantity(legVolumeInUnits));
            return;
        }

        var takeProfitPips = stopLossPips * TakeProfitR;
        var takeProfitPosition = OpenSignalLeg(signal, TakeProfitLegTag, legVolumeInUnits, stopLossPips, takeProfitPips, true);
        if (takeProfitPosition == null)
            return;

        var runnerPosition = OpenSignalLeg(signal, RunnerLegTag, legVolumeInUnits, stopLossPips, null, false);

        Print("[{0}] SIGNAL line {1} {2:yyyy-MM-dd HH:mm} -> SPLIT {3} | SL: {4} | TP leg: {5:F2}R | Leg lots: {6:F2} | Total lots used: {7:F2} | Risk target: {8:F2}% | Runner opened: {9}",
            Label,
            signal.SourceLine,
            signal.Time,
            signal.TradeType,
            signal.StopLossPrice,
            TakeProfitR,
            Symbol.VolumeInUnitsToQuantity(legVolumeInUnits),
            Symbol.VolumeInUnitsToQuantity(legVolumeInUnits * SplitLegCount),
            RiskPercent,
            runnerPosition != null);
    }

    private Position OpenSignalLeg(SignalDefinition signal, string legTag, double volumeInUnits, double stopLossPips, double? takeProfitPips, bool hasFixedTakeProfit)
    {
        var comment = BuildLegComment(legTag, signal.SourceLine);
        var result = ExecuteMarketOrder(signal.TradeType, SymbolName, volumeInUnits, Label, stopLossPips, takeProfitPips, comment);

        if (!result.IsSuccessful)
        {
            Print("[{0}] ERROR opening {1} leg for line {2} {3} at {4:yyyy-MM-dd HH:mm}: {5}",
                Label, legTag, signal.SourceLine, signal.TradeType, signal.Time, result.Error);
            return null;
        }

        var position = result.Position;
        var exactTakeProfit = hasFixedTakeProfit
            ? CalculateTakeProfitPrice(position.EntryPrice, signal.StopLossPrice, signal.TradeType)
            : (double?)null;
        var modifyResult = ModifyPosition(position, signal.StopLossPrice, exactTakeProfit, ProtectionType.Absolute);

        if (!modifyResult.IsSuccessful)
        {
            Print("[{0}] WARNING: opened {1} leg line {2} {3} but exact SL/TP modify failed: {4}. Initial SL/TP pips were used.",
                Label, legTag, signal.SourceLine, position.TradeType, modifyResult.Error);
        }

        Print("[{0}] OPEN {1} line {2} {3} | Entry: {4} | SL: {5} | TP: {6} | Lots: {7:F2}",
            Label,
            legTag,
            signal.SourceLine,
            position.TradeType,
            position.EntryPrice,
            signal.StopLossPrice,
            exactTakeProfit.HasValue ? exactTakeProfit.Value.ToString(CultureInfo.InvariantCulture) : "runner",
            Symbol.VolumeInUnitsToQuantity(volumeInUnits));

        return position;
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

    private double CalculateSplitLegVolume(double totalVolumeInUnits)
    {
        return Symbol.NormalizeVolumeInUnits(totalVolumeInUnits / SplitLegCount, RoundingMode.Down);
    }

    private double CalculateTakeProfitPrice(double entryPrice, double stopLossPrice, TradeType tradeType)
    {
        var riskDistance = Math.Abs(entryPrice - stopLossPrice);
        return tradeType == TradeType.Buy
            ? entryPrice + riskDistance * TakeProfitR
            : entryPrice - riskDistance * TakeProfitR;
    }

    private void ManageRunnerExits()
    {
        if (!HasEnoughBarsForRunnerExit())
            return;

        var bearishCross = IsBearishRunnerEmaCross();
        var bullishCross = IsBullishRunnerEmaCross();
        if (!bearishCross && !bullishCross)
            return;

        var closedBarCloseTime = TrimToMinute(Bars.OpenTimes.Last(0));
        foreach (var position in GetMyPositions())
        {
            if (!IsRunnerLeg(position))
                continue;

            var shouldClose = position.TradeType == TradeType.Buy
                ? bearishCross
                : bullishCross;

            if (!shouldClose)
                continue;

            var result = ClosePosition(position);
            if (result.IsSuccessful)
            {
                Print("[{0}] RUNNER EXIT {1} | EMA{2}/EMA{3} reverse cross at {4:yyyy-MM-dd HH:mm} | Entry: {5} | Pips: {6:F1}",
                    Label,
                    position.TradeType,
                    RunnerEmaFastPeriod,
                    RunnerEmaSlowPeriod,
                    closedBarCloseTime,
                    position.EntryPrice,
                    position.Pips);
            }
            else
            {
                Print("[{0}] ERROR closing runner {1} on EMA reverse cross at {2:yyyy-MM-dd HH:mm}: {3}",
                    Label, position.TradeType, closedBarCloseTime, result.Error);
            }
        }
    }

    private bool HasEnoughBarsForRunnerExit()
    {
        return Bars.Count >= Math.Max(RunnerEmaFastPeriod, RunnerEmaSlowPeriod) + 2;
    }

    private bool IsBearishRunnerEmaCross()
    {
        var fastPrevious = _runnerEmaFast.Result.Last(2);
        var slowPrevious = _runnerEmaSlow.Result.Last(2);
        var fastCurrent = _runnerEmaFast.Result.Last(1);
        var slowCurrent = _runnerEmaSlow.Result.Last(1);

        if (double.IsNaN(fastPrevious) || double.IsNaN(slowPrevious) || double.IsNaN(fastCurrent) || double.IsNaN(slowCurrent))
            return false;

        return fastPrevious >= slowPrevious && fastCurrent < slowCurrent;
    }

    private bool IsBullishRunnerEmaCross()
    {
        var fastPrevious = _runnerEmaFast.Result.Last(2);
        var slowPrevious = _runnerEmaSlow.Result.Last(2);
        var fastCurrent = _runnerEmaFast.Result.Last(1);
        var slowCurrent = _runnerEmaSlow.Result.Last(1);

        if (double.IsNaN(fastPrevious) || double.IsNaN(slowPrevious) || double.IsNaN(fastCurrent) || double.IsNaN(slowCurrent))
            return false;

        return fastPrevious <= slowPrevious && fastCurrent > slowCurrent;
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

        if (MaxOpenPositions < SplitLegCount)
        {
            Print("[{0}] ERROR: Max Open Positions must be at least {1} because each signal opens TP1 and RUNNER legs.",
                Label, SplitLegCount);
            Stop();
            return false;
        }

        if (RunnerEmaFastPeriod >= RunnerEmaSlowPeriod)
        {
            Print("[{0}] ERROR: Runner EMA Fast must be lower than Runner EMA Slow. Current: EMA{1}/EMA{2}.",
                Label, RunnerEmaFastPeriod, RunnerEmaSlowPeriod);
            Stop();
            return false;
        }

        if (BreakEvenBufferPips < 0)
        {
            Print("[{0}] ERROR: BE Buffer must be zero or positive.", Label);
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

    private void TryMoveRunnerStopToBreakEven(Position takeProfitPosition)
    {
        if (!MoveRunnerStopToBreakEven)
            return;

        if (!TryGetLegSourceLine(takeProfitPosition, out var sourceLine))
            return;

        var runner = FindRunnerPosition(sourceLine, takeProfitPosition.TradeType);
        if (runner == null)
        {
            Print("[{0}] TP1 line {1} closed, but no matching runner is open for breakeven move.",
                Label, sourceLine);
            return;
        }

        var breakEvenStopLoss = CalculateBreakEvenStopLoss(runner);
        if (!CanMoveStopLossToBreakEven(runner, breakEvenStopLoss))
        {
            Print("[{0}] SKIP runner BE move line {1} {2}: target SL {3} is not valid/better than current SL {4}.",
                Label, sourceLine, runner.TradeType, breakEvenStopLoss, runner.StopLoss);
            return;
        }

        var result = ModifyPosition(runner, breakEvenStopLoss, runner.TakeProfit, ProtectionType.Absolute);
        if (result.IsSuccessful)
        {
            Print("[{0}] RUNNER line {1} SL moved to BE | {2} | Entry: {3} | New SL: {4} | Buffer: {5:F1} pips",
                Label, sourceLine, runner.TradeType, runner.EntryPrice, breakEvenStopLoss, BreakEvenBufferPips);
        }
        else
        {
            Print("[{0}] ERROR moving runner line {1} SL to BE: {2}",
                Label, sourceLine, result.Error);
        }
    }

    private Position FindRunnerPosition(int sourceLine, TradeType tradeType)
    {
        foreach (var position in GetMyPositions())
        {
            if (position.TradeType == tradeType && IsRunnerLeg(position) && TryGetLegSourceLine(position, out var positionSourceLine) && positionSourceLine == sourceLine)
                return position;
        }

        return null;
    }

    private double CalculateBreakEvenStopLoss(Position position)
    {
        var buffer = BreakEvenBufferPips * Symbol.PipSize;
        return position.TradeType == TradeType.Buy
            ? position.EntryPrice + buffer
            : position.EntryPrice - buffer;
    }

    private bool CanMoveStopLossToBreakEven(Position position, double breakEvenStopLoss)
    {
        if (position.TradeType == TradeType.Buy)
        {
            var improvesStopLoss = !position.StopLoss.HasValue || breakEvenStopLoss > position.StopLoss.Value;
            return improvesStopLoss && breakEvenStopLoss < Symbol.Bid;
        }

        var improvesSellStopLoss = !position.StopLoss.HasValue || breakEvenStopLoss < position.StopLoss.Value;
        return improvesSellStopLoss && breakEvenStopLoss > Symbol.Ask;
    }

    private bool IsTakeProfitLeg(Position position)
    {
        return IsLeg(position, TakeProfitLegTag);
    }

    private bool IsRunnerLeg(Position position)
    {
        return IsLeg(position, RunnerLegTag);
    }

    private bool IsLeg(Position position, string legTag)
    {
        var parts = SplitLegComment(position.Comment);
        return parts.Length == 3 &&
               parts[0] == LegCommentPrefix &&
               parts[1] == legTag;
    }

    private bool TryGetLegSourceLine(Position position, out int sourceLine)
    {
        sourceLine = 0;
        var parts = SplitLegComment(position.Comment);
        return parts.Length == 3 &&
               parts[0] == LegCommentPrefix &&
               int.TryParse(parts[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out sourceLine);
    }

    private string BuildLegComment(string legTag, int sourceLine)
    {
        return string.Join("|", LegCommentPrefix, legTag, sourceLine.ToString(CultureInfo.InvariantCulture));
    }

    private static string[] SplitLegComment(string comment)
    {
        return string.IsNullOrWhiteSpace(comment)
            ? Array.Empty<string>()
            : comment.Split('|');
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

        Print("[{0}] OPENED {1} | Volume: {2:F2} lots | Entry: {3} | SL: {4} | TP: {5} | Comment: {6}",
            Label,
            position.TradeType,
            Symbol.VolumeInUnitsToQuantity(position.VolumeInUnits),
            position.EntryPrice,
            position.StopLoss,
            position.TakeProfit,
            position.Comment);
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var position = args.Position;
        if (position.Label != Label || position.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Net: {2:F2} | Pips: {3:F1} | Reason: {4}",
            Label, position.TradeType, position.NetProfit, position.Pips, args.Reason);

        if (args.Reason == PositionCloseReason.TakeProfit && IsTakeProfitLeg(position))
            TryMoveRunnerStopToBreakEven(position);
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
