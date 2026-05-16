using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;

namespace cAlgo.Robots;

/*
 * SEN_KNNwithEMA v2 - CSV signal executor with multi-leg Fibonacci TP cluster.
 *
 * High-level workflow:
 *   1) OnStart loads CSV signals (bartime, side, sl_price) into memory.
 *   2) OnBar fires when the next bar opens, matches CSV signals exactly at the closed bar close time,
 *      then opens a market-order position cluster.
 *   3) Each cluster = N market orders (legs), same SL from CSV, different Fibonacci TP levels.
 *   4) Legs are labelled {Label}_C{sourceLineId}_L{legNumber} for cluster-aware SL ladder.
 *   5) When a TP leg closes, remaining higher legs have their SL advanced (break-even → lock profit).
 *   6) Daily drawdown guard checked on every tick and bar close — halts and flattens without killing bot.
 *
 * Entry / SL / TP:
 *   SL      = sl_price from CSV (exact Dow wave swing level, absolute price)
 *   BaseRange per leg = |actualEntryPrice - csvSL|  (computed after fill)
 *   TP leg i = actualEntry ± FibMultiplier[i] × BaseRange
 *
 * Risk:
 *   RiskPercent = total cluster risk (e.g. 3% for 3 legs = 1% per leg)
 *   Sizing uses Account.Balance (not Equity) so floating PnL does not affect next cluster size.
 *   Daily DD guard uses Account.Equity (includes floating PnL) for early warning.
 *
 * Reversal:
 *   ReversalMode enum controls behaviour when a new signal conflicts with open opposite positions.
 *   Check runs BEFORE MaxOpenClusters so CloseOppositeAndEnter can reduce cluster count first.
 *
 * Operational contract:
 *   Do not change TpProfile while a cluster is active. SL ladder reconstructs BaseRange from
 *   the current profile multipliers, so a mid-run profile change will produce incorrect ladder prices.
 */

public enum ReversalMode
{
    SkipNewSignalIfOppositeOpen,
    CloseOppositeAndEnter,
    AllowHedging
}

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_KNNwithEMA : Robot
{
    // --- Identity ---
    [Parameter("Label", Group = "Identity", DefaultValue = "SEN_KNNwithEMA")]
    public string Label { get; set; }

    // --- File ---
    [Parameter("Signals CSV Path", Group = "File",
        DefaultValue = @"D:\Auto Trading\SEN05\raw_signal_exports\ai_trend_GOLD_M45_signals.csv")]
    public string SignalsCsvPath { get; set; }

    [Parameter("Signal Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -24, MaxValue = 24, Step = 1)]
    public int SignalTimeOffsetHours { get; set; }

    // --- Execution ---
    [Parameter("TP Profile (1-15)", Group = "Execution",
        DefaultValue = 11, MinValue = 1, MaxValue = 15, Step = 1)]
    public int TpProfile { get; set; }

    [Parameter("Max Spread / SL % (0=off)", Group = "Execution",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 25.0, Step = 2.5)]
    public double MaxSpreadToStopLossPercent { get; set; }

    // --- Risk ---
    [Parameter("Risk % per Trade", Group = "Risk",
        DefaultValue = 3.0, MinValue = 0.1, MaxValue = 10.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Max Open Clusters", Group = "Risk",
        DefaultValue = 1, MinValue = 1, MaxValue = 5, Step = 1)]
    public int MaxOpenClusters { get; set; }

    [Parameter("Reversal Mode", Group = "Risk",
        DefaultValue = ReversalMode.CloseOppositeAndEnter)]
    public ReversalMode ReversalMode { get; set; }

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
    private int _clustersOpened;
    private int _clustersPartial;
    private int _clustersFailed;
    private int _legsPlaced;
    private int _legsFailedToPlace;
    private int _skipMaxClusters;
    private int _skipReversal;
    private int _skipSpread;
    private int _skipVolume;
    private int _slLadderMoves;
    private int _dailyDdTriggerCount;

    // -------------------------------------------------------------------------
    // Lifecycle
    // -------------------------------------------------------------------------

    protected override void OnStart()
    {
        ResetDailyDrawdownBaseline();

        if (!ValidateParameters())
            return;

        var existingPositions = GetMyPositions();
        if (existingPositions.Length > 0)
            Print("[{0}] WARNING: {1} existing position(s) found. Do not change TpProfile while a cluster is active.",
                Label, existingPositions.Length);

        Positions.Opened += OnPositionOpened;
        Positions.Closed += OnPositionClosed;
        Positions.Modified += OnPositionModified;

        if (!LoadSignalsFromCsv())
            return;

        var tpLevels = GetTpLevels();
        var tpMults = GetTpMultipliers();
        Print("[{0}] Started | Symbol: {1} | TF: {2} | TpProfile: {3} -> Levels [{4}] -> Multipliers [{5}] | RiskPerCluster: {6:F1}% | LegRisk: {7:F3}% | MaxClusters: {8} | ReversalMode: {9} | DailyDD: {10:F1}%",
            Label, SymbolName, TimeFrame, TpProfile, FormatLevels(tpLevels), FormatMultipliers(tpMults),
            RiskPercent, RiskPercent / tpMults.Length, MaxOpenClusters, ReversalMode, MaxDailyDrawdownPercent);
        Print("[{0}] Signals loaded: {1} | First: {2:yyyy-MM-dd HH:mm} | Last: {3:yyyy-MM-dd HH:mm} | SignalTime: BarCloseTime | OffsetHours: {4}",
            Label, CountLoadedSignals(), _firstSignalTime, _lastSignalTime,
            SignalTimeOffsetHours);
    }

    protected override void OnTick()
    {
        // Daily DD checked on every tick so protection does not wait for the next bar close (up to 45 min on M45).
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
            // Mark processed BEFORE opening so broker errors do not cause retry spam.
            if (!_processedSignalLines.Add(signal.SourceLine))
                continue;

            _signalsMatched++;
            TryOpenCluster(signal);
        }
    }

    protected override void OnStop()
    {
        Positions.Opened -= OnPositionOpened;
        Positions.Closed -= OnPositionClosed;
        Positions.Modified -= OnPositionModified;

        Print("[{0}] Stopped | SignalsMatched: {1} | ClustersOpened: {2} | Partial: {3} | Failed: {4} | LegsPlaced: {5} | LegsFailed: {6} | SkipMaxClusters: {7} | SkipReversal: {8} | SkipSpread: {9} | SkipVolume: {10} | SLLadderMoves: {11} | DailyDDTriggered: {12}",
            Label, _signalsMatched, _clustersOpened, _clustersPartial, _clustersFailed,
            _legsPlaced, _legsFailedToPlace, _skipMaxClusters, _skipReversal,
            _skipSpread, _skipVolume, _slLadderMoves, _dailyDdTriggerCount);
    }

    // -------------------------------------------------------------------------
    // Cluster entry
    // -------------------------------------------------------------------------

    private void TryOpenCluster(SignalDefinition signal)
    {
        var tradeType = signal.TradeType;
        var isBuy = tradeType == TradeType.Buy;

        // Step 1: ReversalMode — must run BEFORE MaxOpenClusters
        // Reason: CloseOppositeAndEnter reduces cluster count first, so the
        // subsequent MaxOpenClusters check sees the correct post-close count.
        if (HasOppositePositions(tradeType))
        {
            switch (ReversalMode)
            {
                case ReversalMode.SkipNewSignalIfOppositeOpen:
                    _skipReversal++;
                    Print("[{0}] SKIP line {1} {2}: opposite position open (ReversalMode=Skip).",
                        Label, signal.SourceLine, tradeType);
                    return;

                case ReversalMode.CloseOppositeAndEnter:
                    if (!CloseOppositePositions(tradeType))
                    {
                        _skipReversal++;
                        Print("[{0}] SKIP line {1} {2}: failed to close all opposite positions.",
                            Label, signal.SourceLine, tradeType);
                        return;
                    }
                    break;

                case ReversalMode.AllowHedging:
                    break;
            }
        }

        // Step 2: MaxOpenClusters
        var clusterCount = GetMyClusterCount();
        if (clusterCount >= MaxOpenClusters)
        {
            _skipMaxClusters++;
            Print("[{0}] SKIP line {1} {2}: max open clusters reached ({3}/{4}).",
                Label, signal.SourceLine, tradeType, clusterCount, MaxOpenClusters);
            return;
        }

        // Step 3: Estimate SL distance from current market price
        var estEntry = isBuy ? Symbol.Ask : Symbol.Bid;

        if (!IsStopLossOnCorrectSide(tradeType, estEntry, signal.StopLossPrice))
        {
            Print("[{0}] SKIP line {1} {2}: csvSL {3} is on wrong side of entry estimate {4}.",
                Label, signal.SourceLine, tradeType, signal.StopLossPrice, estEntry);
            return;
        }

        var estSLpips = Math.Abs(estEntry - signal.StopLossPrice) / Symbol.PipSize;
        if (estSLpips <= 0)
        {
            Print("[{0}] SKIP line {1} {2}: invalid SL distance (csvSL={3}, estEntry={4}).",
                Label, signal.SourceLine, tradeType, signal.StopLossPrice, estEntry);
            return;
        }

        // Step 4: Spread filter
        if (MaxSpreadToStopLossPercent > 0)
        {
            var spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            var spreadPct = spreadPips / estSLpips * 100.0;
            if (spreadPct > MaxSpreadToStopLossPercent)
            {
                _skipSpread++;
                Print("[{0}] SKIP line {1} {2}: spread {3:F1}p = {4:F2}% of SL ({5:F1}p), limit {6:F2}%.",
                    Label, signal.SourceLine, tradeType, spreadPips, spreadPct, estSLpips, MaxSpreadToStopLossPercent);
                return;
            }
        }

        // Step 5: Volume check
        var tpMults = GetTpMultipliers();
        var legCount = tpMults.Length;
        var legRiskPercent = RiskPercent / legCount;
        var legVolume = CalculateVolumeInUnits(estSLpips, legRiskPercent);

        if (legVolume < Symbol.VolumeInUnitsMin)
        {
            _skipVolume++;
            Print("[{0}] SKIP line {1} {2}: leg volume below minimum. LegRisk: {3:F3}% | SL: {4:F1}p.",
                Label, signal.SourceLine, tradeType, legRiskPercent, estSLpips);
            return;
        }

        // Step 6: Open cluster
        OpenCluster(signal, tradeType, isBuy, estSLpips, tpMults, legCount, legRiskPercent, legVolume);
    }

    private void OpenCluster(SignalDefinition signal, TradeType tradeType, bool isBuy,
        double estSLpips, double[] tpMults, int legCount, double legRiskPercent, double legVolume)
    {
        var placedCount = 0;

        for (var i = 0; i < tpMults.Length; i++)
        {
            var legNumber = i + 1;
            var tpMult = tpMults[i];
            var estTPpips = estSLpips * tpMult;
            var legLabel = GetLegLabel(signal.SourceLine, legNumber);

            // Market order with relative SL/TP so position is never naked even if ModifyPosition fails.
            var result = ExecuteMarketOrder(
                tradeType,
                SymbolName,
                legVolume,
                legLabel,
                estSLpips,
                estTPpips);

            if (!result.IsSuccessful)
            {
                _legsFailedToPlace++;
                Print("[{0}] ERROR leg {1}/{2} line {3} {4}: {5}",
                    Label, legNumber, legCount, signal.SourceLine, tradeType, result.Error);
                continue;
            }

            var pos = result.Position;

            // Post-fill SL side check: slippage may have moved actual entry past csvSL,
            // making the setup geometrically invalid (SL would be in profit direction).
            if (!IsStopLossOnCorrectSide(tradeType, pos.EntryPrice, signal.StopLossPrice))
            {
                _legsFailedToPlace++;
                Print("[{0}] WARNING leg {1}/{2} line {3}: csvSL {4} on wrong side of actual entry {5} after fill. Closing invalid position.",
                    Label, legNumber, legCount, signal.SourceLine, signal.StopLossPrice, pos.EntryPrice);
                ClosePosition(pos);
                continue;
            }

            placedCount++;
            _legsPlaced++;

            // Pin exact absolute SL/TP using actual fill price.
            // BaseRange is computed per-leg because market order fills can differ slightly between legs.
            var baseRange = Math.Abs(pos.EntryPrice - signal.StopLossPrice);
            var exactTP = isBuy
                ? pos.EntryPrice + tpMult * baseRange
                : pos.EntryPrice - tpMult * baseRange;

            var modResult = ModifyPosition(pos, signal.StopLossPrice, exactTP, ProtectionType.Absolute);
            if (!modResult.IsSuccessful)
                Print("[{0}] WARNING leg {1}/{2} line {3}: exact SL/TP modify failed ({4}). Relative pips-based values retained.",
                    Label, legNumber, legCount, signal.SourceLine, modResult.Error);

            Print("[{0}] LEG PLACED {1}/{2} | Line: {3} | {4} | Entry: {5} | SL: {6} | TP: {7} (Fib {8:F3}x) | BaseRange: {9:F1}p | Lots: {10:F2} | LegRisk: {11:F3}%",
                Label, legNumber, legCount, signal.SourceLine, tradeType,
                pos.EntryPrice, signal.StopLossPrice, exactTP, tpMult,
                baseRange / Symbol.PipSize,
                Symbol.VolumeInUnitsToQuantity(legVolume),
                legRiskPercent);
        }

        // Log cluster outcome regardless of result (signal already marked processed, no retry).
        if (placedCount == 0)
        {
            _clustersFailed++;
            Print("[{0}] CLUSTER FAILED: 0/{1} legs placed | Line: {2} {3}",
                Label, legCount, signal.SourceLine, tradeType);
        }
        else if (placedCount < legCount)
        {
            _clustersPartial++;
            _clustersOpened++;
            Print("[{0}] CLUSTER PARTIAL: {1}/{2} legs placed | Line: {3} {4} | TotalRisk approx: {5:F3}%",
                Label, placedCount, legCount, signal.SourceLine, tradeType, legRiskPercent * placedCount);
        }
        else
        {
            _clustersOpened++;
            Print("[{0}] CLUSTER COMPLETE: {1}/{2} legs placed | Line: {3} {4} | TotalRisk: {5:F1}%",
                Label, placedCount, legCount, signal.SourceLine, tradeType, RiskPercent);
        }
    }

    // -------------------------------------------------------------------------
    // SL Ladder
    // -------------------------------------------------------------------------

    private void ApplyStopLossLadder(Position closedPosition)
    {
        var reachedLeg = GetLegNumber(closedPosition.Label);
        var clusterId = GetClusterId(closedPosition.Label);
        var tpMults = GetTpMultipliers();

        // No ladder if: not a labelled leg, last leg of cluster, or unknown cluster.
        if (reachedLeg <= 0 || clusterId < 0 || reachedLeg >= tpMults.Length)
            return;

        var remaining = GetMyPositions()
            .Where(p => GetClusterId(p.Label) == clusterId
                     && p.TradeType == closedPosition.TradeType
                     && GetLegNumber(p.Label) > reachedLeg)
            .OrderBy(p => GetLegNumber(p.Label))
            .ToArray();

        if (remaining.Length == 0)
        {
            Print("[{0}] SL LADDER | C{1} TP{2} hit — no higher leg remains open.",
                Label, clusterId, reachedLeg);
            return;
        }

        foreach (var pos in remaining)
        {
            var posLeg = GetLegNumber(pos.Label);

            if (!TryGetLadderStopLossPrice(pos, posLeg, reachedLeg, tpMults, out var newSL))
            {
                Print("[{0}] SL LADDER SKIP | C{1} TP{2} hit | Leg {3}: cannot calculate new SL.",
                    Label, clusterId, reachedLeg, posLeg);
                continue;
            }

            newSL = Math.Round(newSL, Symbol.Digits);

            if (!ShouldImproveStopLoss(pos, newSL))
            {
                Print("[{0}] SL LADDER SKIP | C{1} TP{2} hit | Leg {3}: current SL {4} already protects >= target {5}.",
                    Label, clusterId, reachedLeg, posLeg, pos.StopLoss, newSL);
                continue;
            }

            var oldSL = pos.StopLoss;
            var modResult = ModifyPosition(pos, newSL, pos.TakeProfit, ProtectionType.Absolute);
            if (!modResult.IsSuccessful)
            {
                Print("[{0}] SL LADDER ERROR | C{1} TP{2} hit | Leg {3}: move SL {4} -> {5} failed: {6}",
                    Label, clusterId, reachedLeg, posLeg, oldSL, newSL, modResult.Error);
                continue;
            }

            _slLadderMoves++;
            Print("[{0}] SL LADDER MOVED | C{1} TP{2} hit | Leg {3}: SL {4} -> {5} | TP: {6}",
                Label, clusterId, reachedLeg, posLeg, oldSL, newSL, pos.TakeProfit);
        }
    }

    private bool TryGetLadderStopLossPrice(Position pos, int posLeg, int reachedLeg, double[] tpMults, out double newSL)
    {
        newSL = 0;

        // TP1 hit → break-even for all remaining legs.
        if (reachedLeg == 1)
        {
            newSL = pos.EntryPrice;
            return true;
        }

        // TP2+ hit → lock at the price of the previous TP level.
        if (posLeg <= 0 || posLeg > tpMults.Length || !pos.TakeProfit.HasValue)
            return false;

        var posMultiplier = tpMults[posLeg - 1];
        if (posMultiplier <= 0)
            return false;

        // Reconstruct BaseRange from this leg's actual entry and TP.
        var baseRange = Math.Abs(pos.TakeProfit.Value - pos.EntryPrice) / posMultiplier;

        // Lock SL at the Fibonacci level that was just hit (reachedLeg - 1 index = previous TP).
        var lockMult = tpMults[reachedLeg - 2];
        newSL = pos.TradeType == TradeType.Buy
            ? pos.EntryPrice + lockMult * baseRange
            : pos.EntryPrice - lockMult * baseRange;

        return true;
    }

    private static bool ShouldImproveStopLoss(Position pos, double newSL)
    {
        if (!pos.StopLoss.HasValue)
            return true;

        return pos.TradeType == TradeType.Buy
            ? newSL > pos.StopLoss.Value
            : newSL < pos.StopLoss.Value;
    }

    // -------------------------------------------------------------------------
    // Daily drawdown protection
    // -------------------------------------------------------------------------

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

        var dd = (_dayStartEquity - Account.Equity) / _dayStartEquity * 100.0;
        if (dd < MaxDailyDrawdownPercent && !_dailyDrawdownTriggered)
            return false;

        if (!_dailyDrawdownTriggered)
        {
            _dailyDrawdownTriggered = true;
            _dailyDdTriggerCount++;
            Print("[{0}] DAILY DD LIMIT HIT | DD: {1:F2}% | Equity: {2:F2} | StartEquity: {3:F2} | Balance: {4:F2}. Paused until tomorrow.",
                Label, dd, Account.Equity, _dayStartEquity, Account.Balance);
        }

        // Retry flatten on every check after trigger: broker reject or connection error
        // on a prior attempt may have left positions open despite the DD flag being set.
        CloseMyPositions();
        return true;
    }

    private void CloseMyPositions()
    {
        foreach (var pos in GetMyPositions())
        {
            var result = ClosePosition(pos);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR closing position {1} ({2}): {3}", Label, pos.Id, pos.Label, result.Error);
        }
    }

    // -------------------------------------------------------------------------
    // Opposite position handling
    // -------------------------------------------------------------------------

    private bool HasOppositePositions(TradeType tradeType)
    {
        var opposite = tradeType == TradeType.Buy ? TradeType.Sell : TradeType.Buy;
        return GetMyPositions().Any(p => p.TradeType == opposite);
    }

    private bool CloseOppositePositions(TradeType tradeType)
    {
        // Close ALL opposite-direction positions across all clusters.
        // If any close fails, block the new signal to avoid holding both directions.
        var opposite = tradeType == TradeType.Buy ? TradeType.Sell : TradeType.Buy;
        var toClose = GetMyPositions().Where(p => p.TradeType == opposite).ToArray();

        if (toClose.Length == 0)
            return true;

        Print("[{0}] REVERSAL {1}: closing {2} opposite position(s).", Label, tradeType, toClose.Length);

        var allClosed = true;
        foreach (var pos in toClose)
        {
            var result = ClosePosition(pos);
            if (!result.IsSuccessful)
            {
                allClosed = false;
                Print("[{0}] ERROR closing opposite position {1} ({2}): {3}", Label, pos.Id, pos.Label, result.Error);
            }
        }

        return allClosed;
    }

    // -------------------------------------------------------------------------
    // Position helpers
    // -------------------------------------------------------------------------

    private Position[] GetMyPositions()
    {
        return Positions
            .Where(p => IsMyBotLabel(p.Label) && p.SymbolName == SymbolName)
            .ToArray();
    }

    private int GetMyClusterCount()
    {
        var positions = GetMyPositions();

        var namedClusters = positions
            .Select(p => GetClusterId(p.Label))
            .Where(id => id >= 0)
            .Distinct()
            .Count();

        // Legacy v1 positions (label == Label exactly, no _C{id}) have ClusterId = -1.
        // Group all of them as one extra cluster so MaxOpenClusters is not bypassed.
        var hasLegacy = positions.Any(p => GetClusterId(p.Label) < 0);
        return namedClusters + (hasLegacy ? 1 : 0);
    }

    private bool IsMyBotLabel(string label)
    {
        if (string.IsNullOrWhiteSpace(label))
            return false;

        return label == Label || label.StartsWith(Label + "_C", StringComparison.Ordinal);
    }

    private string GetLegLabel(int sourceLineId, int legNumber)
    {
        return $"{Label}_C{sourceLineId}_L{legNumber}";
    }

    private int GetClusterId(string label)
    {
        // Format: {Label}_C{id}_L{leg}
        var prefix = Label + "_C";
        if (!label.StartsWith(prefix, StringComparison.Ordinal))
            return -1;

        var rest = label[prefix.Length..];
        var underscoreIdx = rest.IndexOf('_');
        if (underscoreIdx < 0)
            return -1;

        return int.TryParse(rest[..underscoreIdx], NumberStyles.Integer, CultureInfo.InvariantCulture, out var id)
            ? id : -1;
    }

    private int GetLegNumber(string label)
    {
        var lIdx = label.LastIndexOf("_L", StringComparison.Ordinal);
        if (lIdx < 0)
            return 0;

        return int.TryParse(label[(lIdx + 2)..], NumberStyles.Integer, CultureInfo.InvariantCulture, out var leg)
            ? leg : 0;
    }

    // -------------------------------------------------------------------------
    // Risk sizing
    // -------------------------------------------------------------------------

    private double CalculateVolumeInUnits(double stopLossPips, double legRiskPercent)
    {
        if (stopLossPips <= 0)
            return 0;

        // Use realized balance so floating PnL does not inflate/deflate next leg size.
        var riskAmount = Account.Balance * legRiskPercent / 100.0;
        var volume = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);
        return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
    }

    // -------------------------------------------------------------------------
    // TP Profile (identical to SEN_Combo)
    // -------------------------------------------------------------------------

    private int[] GetTpLevels() => GetTpLevelsByProfile(TpProfile);

    private double[] GetTpMultipliers() => GetTpLevels().Select(GetFibMultiplier).ToArray();

    private static int[] GetTpLevelsByProfile(int profile)
    {
        return profile switch
        {
            1  => new[] { 1 },
            2  => new[] { 2 },
            3  => new[] { 3 },
            4  => new[] { 4 },
            5  => new[] { 1, 2 },
            6  => new[] { 1, 3 },
            7  => new[] { 1, 4 },
            8  => new[] { 2, 3 },
            9  => new[] { 2, 4 },
            10 => new[] { 3, 4 },
            11 => new[] { 1, 2, 3 },
            12 => new[] { 1, 2, 4 },
            13 => new[] { 1, 3, 4 },
            14 => new[] { 2, 3, 4 },
            15 => new[] { 1, 2, 3, 4 },
            _  => Array.Empty<int>()
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
            _ => 1.272
        };
    }

    private static string FormatMultipliers(double[] values) =>
        string.Join(", ", values.Select(v => v.ToString("F3", CultureInfo.InvariantCulture)));

    private static string FormatLevels(int[] values) =>
        string.Join(",", values);

    // -------------------------------------------------------------------------
    // Validation
    // -------------------------------------------------------------------------

    private bool ValidateParameters()
    {
        if (string.IsNullOrWhiteSpace(SignalsCsvPath))
        {
            Print("[{0}] ERROR: Signals CSV Path is empty.", Label);
            Stop();
            return false;
        }

        if (TpProfile < 1 || TpProfile > 15)
        {
            Print("[{0}] ERROR: TpProfile must be 1-15.", Label);
            Stop();
            return false;
        }

        if (RiskPercent <= 0 || RiskPercent > 10.0)
        {
            Print("[{0}] ERROR: Risk % must be > 0 and <= 10.", Label);
            Stop();
            return false;
        }

        if (MaxDailyDrawdownPercent <= 0 || MaxDailyDrawdownPercent > 50)
        {
            Print("[{0}] ERROR: Max Daily Drawdown % must be > 0 and <= 50.", Label);
            Stop();
            return false;
        }

        var tpLevels = GetTpLevels();
        if (tpLevels.Length == 0)
        {
            Print("[{0}] ERROR: TpProfile {1} produced no TP levels.", Label, TpProfile);
            Stop();
            return false;
        }

        return true;
    }

    // -------------------------------------------------------------------------
    // Signal loading (retained from v1, minor cleanup)
    // -------------------------------------------------------------------------

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
        var slIndex = FindColumnIndex(header, "sl_price", "sl", "stoploss", "stop_loss", "stop_loss_price");

        if (timeIndex < 0 || sideIndex < 0 || slIndex < 0)
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
            var maxIdx = Math.Max(timeIndex, Math.Max(sideIndex, slIndex));
            if (fields.Length <= maxIdx)
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

            var slText = fields[slIndex].Trim();
            var slPrice = double.NaN;
            if (!string.IsNullOrWhiteSpace(slText) &&
                !double.TryParse(slText, NumberStyles.Float, CultureInfo.InvariantCulture, out slPrice))
            {
                Print("[{0}] WARNING: invalid SL value at line {1}: {2}", Label, i + 1, slText);
                slPrice = double.NaN;
            }

            var signalTime = TrimToMinute(sourceTime.AddHours(SignalTimeOffsetHours));
            AddSignal(new SignalDefinition(signalTime, tradeType, slPrice, i + 1));
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

        if (signal.Time < _firstSignalTime) _firstSignalTime = signal.Time;
        if (signal.Time > _lastSignalTime) _lastSignalTime = signal.Time;
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

    // -------------------------------------------------------------------------
    // Position event handlers
    // -------------------------------------------------------------------------

    private void OnPositionOpened(PositionOpenedEventArgs args)
    {
        var pos = args.Position;
        if (!IsMyBotLabel(pos.Label) || pos.SymbolName != SymbolName)
            return;

        Print("[{0}] OPENED {1} | Label: {2} | Entry: {3} | SL: {4} | TP: {5} | Lots: {6:F2}",
            Label, pos.TradeType, pos.Label,
            pos.EntryPrice, pos.StopLoss, pos.TakeProfit,
            Symbol.VolumeInUnitsToQuantity(pos.VolumeInUnits));
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var pos = args.Position;
        if (!IsMyBotLabel(pos.Label) || pos.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Label: {2} | Net: {3:F2} | Pips: {4:F1} | Reason: {5}",
            Label, pos.TradeType, pos.Label, pos.NetProfit, pos.Pips, args.Reason);

        if (IsTakeProfitClose(args))
            ApplyStopLossLadder(pos);
    }

    private void OnPositionModified(PositionModifiedEventArgs args)
    {
        var pos = args.Position;
        if (!IsMyBotLabel(pos.Label) || pos.SymbolName != SymbolName)
            return;

        Print("[{0}] MODIFIED {1} | Label: {2} | SL: {3} | TP: {4}",
            Label, pos.TradeType, pos.Label, pos.StopLoss, pos.TakeProfit);
    }

    // -------------------------------------------------------------------------
    // Static utilities
    // -------------------------------------------------------------------------

    private static bool IsTakeProfitClose(PositionClosedEventArgs args) =>
        args.Reason.ToString().IndexOf("TakeProfit", StringComparison.OrdinalIgnoreCase) >= 0;

    private static bool IsStopLossOnCorrectSide(TradeType tradeType, double entryPrice, double stopLossPrice) =>
        tradeType == TradeType.Buy ? stopLossPrice < entryPrice : stopLossPrice > entryPrice;

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

    private static string[] SplitCsvLine(string line) => line.Split(',');

    private static int FindColumnIndex(string[] header, params string[] names)
    {
        for (var i = 0; i < header.Length; i++)
        {
            var col = NormalizeHeader(header[i]);
            foreach (var name in names)
                if (col == NormalizeHeader(name))
                    return i;
        }

        return -1;
    }

    private static string NormalizeHeader(string value) =>
        value.Trim().Replace(" ", "").Replace("-", "").Replace("_", "").ToLowerInvariant();

    private static DateTime TrimToMinute(DateTime value) =>
        new(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0);

    // -------------------------------------------------------------------------
    // Signal record
    // -------------------------------------------------------------------------

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
