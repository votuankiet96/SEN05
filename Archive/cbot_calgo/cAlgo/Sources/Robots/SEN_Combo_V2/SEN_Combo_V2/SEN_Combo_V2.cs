using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using cAlgo.API;

namespace cAlgo.Robots;

/*
 * SEN_Combo_V2 - FTMO-focused CSV signal executor.
 *
 * High-level workflow:
 *   1) OnStart loads external CSV signals into memory.
 *   2) On every closed cTrader bar, the bot checks whether the closed bar contains a CSV signal.
 *   3) If the signal is opposite to existing exposure, opposite pending orders are cancelled and opposite positions are closed.
 *   4) If exposure already exists in the same direction, the new same-direction signal is ignored.
 *   5) If the signal is tradable, the bot opens a TP-leg cluster: 1 to 4 child orders with shared Entry/SL and separate TP levels.
 *   6) When a lower TP leg closes by TakeProfit, remaining higher legs have their SL moved up/down to lock the previous milestone.
 *
 * Supported CSV formats:
 *   bartime,side,atr
 *   bartime,symbol,side,atr
 *
 * Entry / SL / TP:
 *   BaseRange = max(ATR, signal candle High - Low)
 *   BUY  -> up to 4 Buy Stops  | Entry = High + offset | SL = Entry - KSL * BaseRange | TP legs = Entry + KTP * BaseRange
 *   SELL -> up to 4 Sell Stops | Entry = Low - offset  | SL = Entry + KSL * BaseRange | TP legs = Entry - KTP * BaseRange
 *   Entry offset is a percentage of BaseRange.
 *
 * Management:
 *   Pending orders expire after N closed bars.
 *   Opposite signals close current positions, then place the new signal order.
 *   Same-direction signals are ignored while bot exposure already exists in that direction.
 *   FTMO daily and total loss guards halt trading and flatten bot exposure before rule limits are reached.
 */

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_Combo_V2 : Robot
{
    // File inputs: where signals come from and how their timestamps are aligned to cTrader server time.
    [Parameter("Signal CSV Path", Group = "File",
        DefaultValue = @"D:\Auto Trading\SEN05\raw_signals\combo\combo_HK50_H4_20230103_20260512_signals.csv")]
    public string CsvPath { get; set; }

    [Parameter("CSV Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -12, MaxValue = 14, Step = 1)]
    public int CsvTimeOffsetHours { get; set; }

    [Parameter("Bot Label", Group = "Identity", DefaultValue = "SEN_Combo_V2")]
    public string BotLabel { get; set; }

    // Execution inputs: define how entry, SL, TP and signal validity are calculated after a signal bar closes.
    [Parameter("X Offset (% BaseRange)", Group = "Execution",
        DefaultValue = 5.0, MinValue = 0.1, MaxValue = 20.0, Step = 0.1)]
    public double XOffsetBaseRangePercent { get; set; }

    [Parameter("KSL Fibonacci Level (1-4)", Group = "Execution",
        DefaultValue = 2, MinValue = 1, MaxValue = 4, Step = 1)]
    public int KslFibLevel { get; set; }

    [Parameter("TP Profile (1-15)", Group = "Execution",
        DefaultValue = 15, MinValue = 1, MaxValue = 15, Step = 1)]
    public int TpProfile { get; set; }

    [Parameter("Max Spread / SL % (0=off)", Group = "Execution",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 25.0, Step = 2.5)]
    public double MaxSpreadToStopLossPercent { get; set; }

    [Parameter("Cancel Pending After Bars", Group = "Execution",
        DefaultValue = 3, MinValue = 1, MaxValue = 50, Step = 1)]
    public int CancelPendingAfterBars { get; set; }

    [Parameter("Max Signal Bar Clock Hours", Group = "Execution",
        DefaultValue = 0, MinValue = 0, MaxValue = 168, Step = 1)]
    public double MaxSignalBarClockHours { get; set; }

    // Risk inputs: RiskPercent is the total cluster risk, then split evenly across active TP legs.
    [Parameter("Risk % per Trade", Group = "Risk",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 3.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("FTMO Initial Capital", Group = "FTMO",
        DefaultValue = 10000.0, MinValue = 1000.0, MaxValue = 200000.0, Step = 1000.0)]
    public double FtmoInitialCapital { get; set; }

    [Parameter("FTMO Daily Loss %", Group = "FTMO",
        DefaultValue = 5.0, MinValue = 1.0, MaxValue = 20.0, Step = 0.5)]
    public double FtmoDailyLossPercent { get; set; }

    [Parameter("Daily Buffer %", Group = "FTMO",
        DefaultValue = 1.5, MinValue = 0.0, MaxValue = 10.0, Step = 0.1)]
    public double FtmoDailyLossBufferPercent { get; set; }

    [Parameter("FTMO Maximum Loss %", Group = "FTMO",
        DefaultValue = 10.0, MinValue = 1.0, MaxValue = 30.0, Step = 0.5)]
    public double FtmoMaximumLossPercent { get; set; }

    [Parameter("Maximum Loss Buffer %", Group = "FTMO",
        DefaultValue = 3.0, MinValue = 0.0, MaxValue = 10.0, Step = 0.1)]
    public double FtmoMaximumLossBufferPercent { get; set; }

    [Parameter("FTMO Day Offset Hours", Group = "FTMO",
        DefaultValue = 0, MinValue = -14, MaxValue = 14, Step = 1)]
    public int FtmoDayOffsetHours { get; set; }

    // Runtime state: loaded signals and pending-order birth bars are kept in memory for fast bar-by-bar checks.
    private readonly Dictionary<DateTime, SignalInfo> _signals = new();
    private readonly Dictionary<long, int> _pendingOrderCreatedBarCounts = new();
    private SignalInfo[] _signalsByTime = Array.Empty<SignalInfo>();

    // FTMO state: the daily guard follows the FTMO reset day, while total loss is static for 2-Step accounts.
    // Both guards use Account.Equity when checking the live limit because FTMO includes floating PnL, swaps and commissions.
    private DateTime _ftmoDay;
    private double _ftmoDayStartBalance;
    private bool _ftmoDailyLossTriggered;
    private bool _ftmoMaximumLossTriggered;

    // Counters printed in OnStop. They are intentionally simple diagnostics, not trading logic.
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
    private int _skipOppositeCancelCount;
    private int _skipLongSignalBarCount;
    private int _skipBrokerDistanceCount;
    private int _ftmoDailyGuardTriggeredCount;
    private int _ftmoMaximumGuardTriggeredCount;
    private int _placeOrderErrorCount;

    private const int FitnessMinTrades = 30;
    private const double FitnessMaxEquityDrawdownPercent = 8.0;
    private const double FitnessMaxBalanceDrawdownPercent = 8.0;

    protected override void OnStart()
    {
        // Subscribe once at startup so order/position events can update counters and trigger SL ladder.
        PendingOrders.Filled += OnPendingOrderFilled;
        PendingOrders.Cancelled += OnPendingOrderCancelled;
        Positions.Closed += OnPositionClosed;

        // Fail fast on invalid settings. This avoids running a bot that can place inconsistent TP/SL levels.
        if (!ValidateParameters())
        {
            Stop();
            return;
        }

        // Restore persisted FTMO risk baselines before any trading decision is made.
        InitializeRiskState();

        var offsetDescription = string.Format(CultureInfo.InvariantCulture, "{0:F1}% BaseRange", XOffsetBaseRangePercent);
        var tpLevels = GetTpLevels();
        var tpMultipliers = GetTpMultipliers();
        var tpProfileDescription = $"Profile {TpProfile} -> Levels [{FormatLevels(tpLevels)}] -> Multipliers [{FormatMultipliers(tpMultipliers)}]";

        Print("[{0}] Symbol info | Symbol: {1} | PipSize: {2} | TickSize: {3} | Digits: {4} | VolumeMin: {5} | Offset: {6} | TP Profile: {7} | Execution: StopOrderOnly | SignalBarMode: ClosedBarContainsSignal | MaxSignalBarClockHours: {8:F1}",
            BotLabel,
            SymbolName,
            Symbol.PipSize,
            Symbol.TickSize,
            Symbol.Digits,
            Symbol.VolumeInUnitsMin,
            offsetDescription,
            tpProfileDescription,
            MaxSignalBarClockHours);

        Print("[{0}] FTMO guard | Initial: {1:F2} | Daily rule/buffer/guard: {2:F1}%/{3:F1}%/{4:F1}% | Max loss rule/buffer/guard: {5:F1}%/{6:F1}%/{7:F1}% | Day offset: {8:+#;-#;0}h | Daily floor: {9:F2} | Daily equity room: {10:F2} | Total floor: {11:F2}",
            BotLabel,
            FtmoInitialCapital,
            FtmoDailyLossPercent,
            FtmoDailyLossBufferPercent,
            GetFtmoDailyGuardPercent(),
            FtmoMaximumLossPercent,
            FtmoMaximumLossBufferPercent,
            GetFtmoMaximumLossGuardPercent(),
            FtmoDayOffsetHours,
            GetFtmoDailyEquityFloor(),
            Account.Equity - GetFtmoDailyEquityFloor(),
            GetFtmoMaximumLossEquityFloor());

        // CSV signals are static for this run; LoadSignals parses and filters them by symbol if a symbol column exists.
        LoadSignals();

        if (_signals.Count == 0)
        {
            Print("[{0}] ERROR: no valid signals loaded. Stopping.", BotLabel);
            Stop();
        }

        // If cTrader restarts with pending orders already present, track them from the current bar.
        TrackExistingPendingOrders();
    }

    protected override void OnTick()
    {
        // Risk guards are checked on every tick so protection does not wait until the next bar close.
        CheckRiskLimits();
    }

    protected override void OnBarClosed()
    {
        // If a risk guard has triggered, CheckRiskLimits also flattens exposure and blocks new trades.
        if (CheckRiskLimits())
            return;

        // Pending expiry is bar-based, so it belongs in OnBarClosed rather than OnTick.
        CancelExpiredPendingOrders();

        if (_signals.Count == 0)
            return;

        // Last(1) is the just-closed signal bar; Last(0) is the new live bar that has just opened.
        var barTime = TrimToMinute(Bars.OpenTimes.Last(1));
        var nextBarTime = TrimToMinute(Bars.OpenTimes.Last(0));

        // Find a CSV signal whose timestamp is inside the closed bar interval [barTime, nextBarTime).
        if (!TryGetSignalForClosedBar(barTime, nextBarTime, out var signal, out var signalsInBar))
        {
            _barsWithoutSignal++;
            return;
        }

        _matchedSignalCount++;
        if (signalsInBar > 1)
            _multiSignalBarCount++;

        // Optional guard for abnormal bars, weekend/session gaps, or bad historical data spacing.
        var signalBarClockHours = (nextBarTime - barTime).TotalHours;
        if (MaxSignalBarClockHours > 0 && signalBarClockHours > MaxSignalBarClockHours)
        {
            _skipLongSignalBarCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: signal bar clock span {3:F2}h > MaxSignalBarClockHours {4:F2}h. NextBar: {5:yyyy-MM-dd HH:mm} | SignalTime: {6:yyyy-MM-dd HH:mm}",
                BotLabel,
                signal.Side,
                barTime,
                signalBarClockHours,
                MaxSignalBarClockHours,
                nextBarTime,
                signal.Time);
            return;
        }

        // Design note:
        // There is intentionally no session/time-of-day filter here. This executor treats the CSV signal
        // source as the authority on when a setup exists, then applies execution/risk guards only. A future
        // research option is to add optional session windows after reviewing per-symbol and per-hour
        // expectancy, but it should default to off so it does not override the signal model prematurely.

        // Convert CSV BUY/SELL into cTrader TradeType so order, pending and position APIs use the same direction.
        var tradeType = GetTradeType(signal);

        // Reverse rule: before placing a new signal, remove any old pending/position in the opposite direction.
        if (!CancelOppositePendingOrders(tradeType))
        {
            _skipOppositeCancelCount++;
            return;
        }

        if (!CloseOppositePositions(tradeType))
        {
            _skipCloseOppositeCount++;
            return;
        }

        // Same-direction rule: do not stack another cluster while any same-direction child leg is still alive.
        //
        // Design note:
        // This is intentionally conservative: one symbol/direction can have only one active cluster at a
        // time. Even if the current cluster has already reached TP1 and its remaining legs are protected
        // at break-even, the bot skips new same-direction signals instead of pyramiding. A future optional
        // re-entry mode could allow new clusters only after existing same-direction exposure is risk-free,
        // but that changes exposure, margin usage and trend-stacking behavior, so it should be tested as
        // a separate strategy variant.
        var sameDirectionExposureCount = GetMyOpenOrderCount(tradeType);
        if (sameDirectionExposureCount > 0)
        {
            _skipSameDirectionSignalCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: same-direction exposure already exists ({3} position/pending order(s)).",
                BotLabel, signal.Side, barTime, sameDirectionExposureCount);
            return;
        }

        // At this point the signal is clean: no opposite exposure remains and no same-direction cluster exists.
        PlaceSignalOrder(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar);
    }

    protected override void OnStop()
    {
        PersistRiskState();

        Print("[{0}] Stopped. Signals loaded: {1}", BotLabel, _signals.Count);
        Print("[{0}] Summary | Signals matched: {1} | Multi-signal bars: {2} | Bars without signal: {3} | Stop orders placed: {4} | Stop fills: {5} | Pending cancelled: {6} | Pending expired: {7} | Skip SameDirection: {8} | Skip Spread: {9} | Skip Volume: {10} | Skip OppositeCancel: {11} | Skip CloseOpposite: {12} | Skip LongBar: {13} | Skip BrokerDistance: {14} | Place errors: {15}",
            BotLabel,
            _matchedSignalCount,
            _multiSignalBarCount,
            _barsWithoutSignal,
            _stopOrdersPlacedCount,
            _pendingFilledCount,
            _pendingCancelledCount,
            _pendingExpiredCount,
            _skipSameDirectionSignalCount,
            _skipSpreadFilterCount,
            _skipVolumeCount,
            _skipOppositeCancelCount,
            _skipCloseOppositeCount,
            _skipLongSignalBarCount,
            _skipBrokerDistanceCount,
            _placeOrderErrorCount);
        Print("[{0}] FTMO Summary | Daily guard hits: {1} | Maximum loss guard hits: {2} | Current FTMO day: {3:yyyy-MM-dd} | Day start balance: {4:F2} | Daily floor: {5:F2} | Total floor: {6:F2} | Current equity: {7:F2}",
            BotLabel,
            _ftmoDailyGuardTriggeredCount,
            _ftmoMaximumGuardTriggeredCount,
            _ftmoDay,
            _ftmoDayStartBalance,
            GetFtmoDailyEquityFloor(),
            GetFtmoMaximumLossEquityFloor(),
            Account.Equity);
    }

    private bool ValidateParameters()
    {
        // Empty path means there is no signal source, so the bot cannot make any trading decision.
        if (string.IsNullOrWhiteSpace(CsvPath))
        {
            Print("[{0}] ERROR: CSV path is empty.", BotLabel);
            return false;
        }

        // KSL uses a separate 1-4 Fibonacci table because SL levels are intentionally closer than TP extensions.
        if (KslFibLevel < 1 || KslFibLevel > 4)
        {
            Print("[{0}] ERROR: KSL Fibonacci level must be from 1 to 4.", BotLabel);
            return false;
        }

        // TP Profile encodes only valid, strictly increasing TP level combinations for optimizer cleanliness.
        if (TpProfile < 1 || TpProfile > 15)
        {
            Print("[{0}] ERROR: TP Profile must be from 1 to 15.", BotLabel);
            return false;
        }

        // GetTpLevelsByProfile maps the selected profile into its active Fibonacci TP levels.
        var tpLevels = GetTpLevels();
        if (tpLevels.Any(level => !IsValidFibLevel(level)))
        {
            Print("[{0}] ERROR: TP Fibonacci levels must be from 1 to 4.", BotLabel);
            return false;
        }

        // R:R below 1:1 can still work with a high-win-rate signal model, but it is fragile in forward
        // testing. Warn loudly instead of blocking so research can still test those configurations.
        var kslMultiplier = GetKslMultiplier();
        var tpMultipliers = GetTpMultipliers();
        if (tpMultipliers.Length > 0 && kslMultiplier > 0)
        {
            var minRiskReward = tpMultipliers[0] / kslMultiplier;
            if (minRiskReward < 1.0)
            {
                var breakevenWinRate = 100.0 / (1.0 + minRiskReward);
                Print("[{0}] WARNING: Min RR {1:F3} < 1.0. TP1={2:F3} / KSL={3:F3}. This setup requires WR > {4:F1}% before costs/slippage.",
                    BotLabel,
                    minRiskReward,
                    tpMultipliers[0],
                    kslMultiplier,
                    breakevenWinRate);
            }
        }

        if (XOffsetBaseRangePercent <= 0 || XOffsetBaseRangePercent > 20.0)
        {
            Print("[{0}] ERROR: X Offset % BaseRange must be from 1 to 20.", BotLabel);
            return false;
        }

        if (MaxSpreadToStopLossPercent < 0 || MaxSpreadToStopLossPercent > 25.0)
        {
            Print("[{0}] ERROR: Max Spread / SL % must be from 0 to 25. Use 0 to disable the filter.", BotLabel);
            return false;
        }

        // RiskPercent is total cluster risk, not per leg; code later splits it by active TP leg count.
        if (RiskPercent <= 0 || RiskPercent > 3.0)
        {
            Print("[{0}] ERROR: Risk percent must be > 0 and <= 3.", BotLabel);
            return false;
        }

        if (CancelPendingAfterBars < 1)
        {
            Print("[{0}] ERROR: Cancel pending after bars must be at least 1.", BotLabel);
            return false;
        }

        if (FtmoInitialCapital <= 0)
        {
            Print("[{0}] ERROR: FTMO initial capital must be greater than 0.", BotLabel);
            return false;
        }

        if (FtmoDailyLossPercent <= 0 || FtmoDailyLossPercent > 100)
        {
            Print("[{0}] ERROR: FTMO daily loss percent must be > 0 and <= 100.", BotLabel);
            return false;
        }

        if (FtmoDailyLossBufferPercent < 0 || FtmoDailyLossBufferPercent >= FtmoDailyLossPercent)
        {
            Print("[{0}] ERROR: Daily buffer percent must be >= 0 and lower than FTMO daily loss percent.", BotLabel);
            return false;
        }

        if (FtmoMaximumLossPercent <= 0 || FtmoMaximumLossPercent > 100)
        {
            Print("[{0}] ERROR: FTMO maximum loss percent must be > 0 and <= 100.", BotLabel);
            return false;
        }

        if (FtmoMaximumLossBufferPercent < 0 || FtmoMaximumLossBufferPercent >= FtmoMaximumLossPercent)
        {
            Print("[{0}] ERROR: Maximum loss buffer percent must be >= 0 and lower than FTMO maximum loss percent.", BotLabel);
            return false;
        }

        return true;
    }

    private void LoadSignals()
    {
        // Reload from scratch to avoid stale signals if this method is ever reused in a future refresh flow.
        _signals.Clear();
        _signalsByTime = Array.Empty<SignalInfo>();

        if (!System.IO.File.Exists(CsvPath))
        {
            Print("[{0}] ERROR: CSV file not found: {1}", BotLabel, CsvPath);
            return;
        }

        var lines = System.IO.File.ReadAllLines(CsvPath);
        if (lines.Length == 0)
            return;

        // Header is optional. If present, columns are matched by name; otherwise fixed order is assumed.
        var header = SplitCsvLine(lines[0]);
        var hasHeader = header.Any(cell => cell.Equals("bartime", StringComparison.OrdinalIgnoreCase));

        // Supported header aliases make the loader a little more tolerant of exported signal files.
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

        // Parse each CSV row into a normalized SignalInfo keyed by signal time.
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

            // If CSV has a symbol column, this bot only imports rows for the current chart symbol.
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

            // Same timestamp collision: the later CSV row overwrites the earlier row by design.
            _signals[signal.Time] = signal;
            loaded++;
        }

        _signalsByTime = _signals.Values
            .OrderBy(signal => signal.Time)
            .ToArray();

        Print("[{0}] CSV loaded: {1} signals | skipped: {2} | symbol: {3} | offset: {4}h | {5}",
            BotLabel, loaded, skipped, SymbolName, CsvTimeOffsetHours, CsvPath);
    }

    private bool TryParseSignal(string timeText, string sideText, string atrText, out SignalInfo signal)
    {
        signal = default;

        // Signal time must match the supported export formats exactly to avoid timezone/date ambiguity.
        if (!TryParseTime(timeText.Trim(), out var sourceTime))
            return false;

        // Normalize multiple possible signal encodings into BUY/SELL.
        var side = sideText.Trim().ToUpperInvariant();
        if (side is "1" or "BUY" or "LONG")
            side = "BUY";
        else if (side is "-1" or "SELL" or "SHORT")
            side = "SELL";
        else
            return false;

        if (!double.TryParse(atrText.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var atr) || atr <= 0)
            return false;

        // Apply CSV time offset here so all later matching uses cTrader/server-aligned signal time.
        signal = new SignalInfo(TrimToMinute(sourceTime.AddHours(CsvTimeOffsetHours)), side, atr);
        return true;
    }

    private bool TryParseTime(string text, out DateTime time)
    {
        return DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out time)
            || DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out time);
    }

    private void PlaceSignalOrder(SignalInfo signal, DateTime barTime, DateTime nextBarTime, double signalBarClockHours, int signalsInBar)
    {
        // The signal bar is the just-closed bar. Entry/SL/TP are all derived from that completed bar.
        var high = Bars.HighPrices.Last(1);
        var low = Bars.LowPrices.Last(1);

        // BUY and SELL share the same distance formulas but mirror direction around entry.
        var isBuy = signal.Side == "BUY";
        var signalRange = high - low;

        // BaseRange is the common scale for SL and TP: use the larger value between CSV ATR and candle range.
        //
        // Design note:
        // The strategy intentionally does not cap BaseRange here. Large signal bars can be valid breakout
        // conditions, and risk sizing naturally scales volume down when SL distance expands. This preserves
        // the original signal-following behavior across H1-H4 tests. A future research option is to add
        // optional range-quality filters, such as MinBaseRangePips, MaxBaseRangePips, or
        // MaxSignalRangeAtrMultiple, but they should default to off and be validated by symbol/timeframe
        // backtests before being used live.
        var baseRange = Math.Max(signal.Atr, signalRange);
        var kslMultiplier = GetKslMultiplier();
        var tpMultipliers = GetTpMultipliers();
        var tradeType = isBuy ? TradeType.Buy : TradeType.Sell;

        // Entry offset controls how far price must break beyond the signal candle before the bot enters.
        // It uses the same volatility-aware BaseRange scale as SL/TP, which avoids hard-coded pip distances
        // across forex, metals, crypto and indices.
        var entryOffset = GetEntryOffset(baseRange);
        var entryOffsetPips = entryOffset / Symbol.PipSize;
        var entryPrice = isBuy ? high + entryOffset : low - entryOffset;

        // Initial SL is shared by every leg in the cluster.
        var stopLossPrice = isBuy
            ? entryPrice - kslMultiplier * baseRange
            : entryPrice + kslMultiplier * baseRange;

        // cTrader order APIs expect SL/TP distances in pips when using relative protection.
        var stopLossPips = Math.Abs(entryPrice - stopLossPrice) / Symbol.PipSize;
        var firstTakeProfitPips = tpMultipliers[0] * baseRange / Symbol.PipSize;
        var spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
        var spreadToStopLossPercent = stopLossPips > 0
            ? spreadPips / stopLossPips * 100.0
            : 0;
        var spreadToFirstTakeProfitPercent = firstTakeProfitPips > 0
            ? spreadPips / firstTakeProfitPips * 100.0
            : 0;

        // Optional spread filter scaled to the setup's own SL distance rather than fixed pips.
        // MaxSpreadToStopLossPercent = 0 keeps the original behavior. When enabled, it avoids
        // placing a cluster when the current trading cost is too large relative to the planned risk.
        if (MaxSpreadToStopLossPercent > 0 && spreadToStopLossPercent > MaxSpreadToStopLossPercent)
        {
            _skipSpreadFilterCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: spread {3:F1}p is {4:F2}% of SL, above Max Spread / SL {5:F2}%. SL: {6:F1}p | TP1: {7:F1}p | Spread/TP1: {8:F2}%",
                BotLabel,
                signal.Side,
                barTime,
                spreadPips,
                spreadToStopLossPercent,
                MaxSpreadToStopLossPercent,
                stopLossPips,
                firstTakeProfitPips,
                spreadToFirstTakeProfitPercent);
            return;
        }

        if (!PassesBrokerDistanceGuard(signal, barTime, entryPrice, stopLossPips, firstTakeProfitPips))
        {
            _skipBrokerDistanceCount++;
            return;
        }

        // RiskPercent is total cluster risk. Divide it evenly across active TP legs.
        var legRiskPercent = RiskPercent / tpMultipliers.Length;
        var legVolume = CalculateRiskVolume(stopLossPips, legRiskPercent);

        // If the split leg volume is below broker minimum, placing tiny child orders would fail or distort risk.
        if (legVolume < Symbol.VolumeInUnitsMin)
        {
            _skipVolumeCount++;
            Print("[{0}] ERROR: total risk {1:F1}% split into {2} TP legs gives leg risk {3:F3}% and volume {4}, below symbol minimum {5}. SL: {6:F1}p.",
                BotLabel, RiskPercent, tpMultipliers.Length, legRiskPercent, legVolume, Symbol.VolumeInUnitsMin, stopLossPips);
            return;
        }

        // Create one child order per active TP multiplier. All legs share entry/SL and differ only by TP.
        //
        // Partial cluster policy:
        // If one or more legs fail to place, keep any legs that were already placed successfully.
        // This is intentional. A partial cluster usually carries less total risk than planned because
        // each leg is sized independently, and it preserves some participation in the signal instead
        // of discarding the entire trade opportunity. The trade-off is that live results may differ
        // from the ideal full-leg structure: missing middle legs can remove intermediate profit-taking
        // and SL-ladder milestones. The summary log below makes that visible for review.
        var requestedLegCount = tpMultipliers.Length;
        var placedLegCount = 0;
        var pendingExpiration = GetPendingOrderExpiration(barTime, nextBarTime);
        for (var i = 0; i < tpMultipliers.Length; i++)
        {
            var legNumber = i + 1;
            var tpMultiplier = tpMultipliers[i];

            // TP price mirrors around entry: above entry for BUY, below entry for SELL.
            var takeProfitPrice = isBuy
                ? entryPrice + tpMultiplier * baseRange
                : entryPrice - tpMultiplier * baseRange;
            var takeProfitPips = Math.Abs(takeProfitPrice - entryPrice) / Symbol.PipSize;
            var riskReward = takeProfitPips / stopLossPips;

            // Stop-order-only execution waits for breakout confirmation beyond the signal candle.
            if (PlaceSignalStopOrder(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar, tradeType, legNumber, requestedLegCount, legVolume, legRiskPercent, entryPrice, entryOffset, stopLossPrice, takeProfitPrice, stopLossPips, takeProfitPips, signalRange, baseRange, kslMultiplier, tpMultiplier, riskReward, pendingExpiration))
                placedLegCount++;
        }

        var summaryLevel = placedLegCount == requestedLegCount ? "SIGNAL CLUSTER COMPLETE" : "SIGNAL CLUSTER PARTIAL";
        var tpLevels = GetTpLevels();
        Print("[{0}] {1} | {2} at {3:yyyy-MM-dd HH:mm} | Mode: {4} | TP Profile: {5} -> Levels [{6}] -> Multipliers [{7}] | Legs placed: {8}/{9} | TotalRisk planned: {10:F1}% | TotalRisk placed approx: {11:F3}% | LegRisk: {12:F3}% | Offset: {13:F1}% BaseRange ({14:F1}p) | Spread: {15:F1}p | Spread/SL: {16:F2}% | Spread/TP1: {17:F2}% | Max Spread/SL: {18}",
            BotLabel,
            summaryLevel,
            signal.Side,
            barTime,
            "StopOrder",
            TpProfile,
            FormatLevels(tpLevels),
            FormatMultipliers(tpMultipliers),
            placedLegCount,
            requestedLegCount,
            RiskPercent,
            legRiskPercent * placedLegCount,
            legRiskPercent,
            XOffsetBaseRangePercent,
            entryOffsetPips,
            spreadPips,
            spreadToStopLossPercent,
            spreadToFirstTakeProfitPercent,
            MaxSpreadToStopLossPercent > 0 ? MaxSpreadToStopLossPercent.ToString("F2", CultureInfo.InvariantCulture) + "%" : "off");
    }

    private bool PlaceSignalStopOrder(SignalInfo signal, DateTime barTime, DateTime nextBarTime, double signalBarClockHours, int signalsInBar, TradeType tradeType, int legNumber, int legCount, double volume, double legRiskPercent, double entryPrice, double entryOffset, double stopLossPrice, double takeProfitPrice, double stopLossPips, double takeProfitPips, double signalRange, double baseRange, double kslMultiplier, double tpMultiplier, double riskReward, DateTime pendingExpiration)
    {
        // Stop order mode places pending child orders at the breakout price.
        var result = PlaceStopOrder(
            tradeType,
            SymbolName,
            volume,
            entryPrice,
            GetLegLabel(legNumber),
            stopLossPips,
            takeProfitPips,
            ProtectionType.Relative,
            pendingExpiration);

        // Stop order placement can fail due to invalid price distance, broker limits, market state, etc.
        if (!result.IsSuccessful)
        {
            _placeOrderErrorCount++;
            Print("[{0}] ERROR placing {1} stop at {2}: {3}", BotLabel, signal.Side, entryPrice, result.Error);
            return false;
        }

        _stopOrdersPlacedCount++;

        // Track birth bar for Cancel Pending After Bars.
        if (result.PendingOrder != null)
            _pendingOrderCreatedBarCounts[result.PendingOrder.Id] = Bars.Count;

        Print("[{0}] {1} STOP leg {2}/{3} placed | Bar: {4:yyyy-MM-dd HH:mm} -> {5:yyyy-MM-dd HH:mm} ({6:F2}h) | SignalTime: {7:yyyy-MM-dd HH:mm} | SignalsInBar: {8} | Entry: {9} | Expiration: {10:yyyy-MM-dd HH:mm} | Offset: {11:F1}% BaseRange = {12:F1}p ({13} price) | SL: {14} ({15:F1}p) | TP: {16} ({17:F1}p) | ATR: {18:F4} | Range: {19:F4} | Base: {20:F4} | KSL: {21:F3} | KTP: {22:F3} | RR: {23:F2} | TotalRisk: {24:F1}% | LegRisk: {25:F3}% | Lots: {26:F2} | PipSize: {27}",
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
            XOffsetBaseRangePercent,
            entryOffset / Symbol.PipSize,
            entryOffset,
            stopLossPrice,
            stopLossPips,
            takeProfitPrice,
            takeProfitPips,
            signal.Atr,
            signalRange,
            baseRange,
            kslMultiplier,
            tpMultiplier,
            riskReward,
            RiskPercent,
            legRiskPercent,
            Symbol.VolumeInUnitsToQuantity(volume),
            Symbol.PipSize);
        return true;
    }

    private double CalculateRiskVolume(double stopLossPips, double riskPercent)
    {
        // A non-positive SL distance is invalid and would make risk sizing undefined.
        if (stopLossPips <= 0)
            return 0;

        // Use realized account balance so floating PnL does not resize the next cluster.
        var riskAmount = Account.Balance * riskPercent / 100.0;

        // Let cTrader convert fixed cash risk + SL pips into symbol-specific volume units.
        var volume = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);

        // Normalize down to avoid accidentally exceeding the intended risk after broker volume rounding.
        return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
    }

    private bool PassesBrokerDistanceGuard(SignalInfo signal, DateTime barTime, double entryPrice, double stopLossPips, double firstTakeProfitPips)
    {
        var minStopLossPips = GetMinimumDistancePips(Symbol.MinStopLossDistance, entryPrice);
        var minTakeProfitPips = GetMinimumDistancePips(Symbol.MinTakeProfitDistance, entryPrice);

        if (stopLossPips >= minStopLossPips && firstTakeProfitPips >= minTakeProfitPips)
            return true;

        Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: broker minimum distance guard failed. SL: {3:F1}p / min {4:F1}p | TP1: {5:F1}p / min {6:F1}p | MinDistanceType: {7}",
            BotLabel,
            signal.Side,
            barTime,
            stopLossPips,
            minStopLossPips,
            firstTakeProfitPips,
            minTakeProfitPips,
            Symbol.MinDistanceType);
        return false;
    }

    private double GetMinimumDistancePips(double minimumDistance, double referencePrice)
    {
        if (minimumDistance <= 0 || Symbol.PipSize <= 0)
            return 0;

        return Symbol.MinDistanceType == SymbolMinDistanceType.Percentage
            ? Math.Abs(referencePrice) * minimumDistance / 100.0 / Symbol.PipSize
            : minimumDistance;
    }

    private DateTime GetPendingOrderExpiration(DateTime barTime, DateTime nextBarTime)
    {
        var barSpan = nextBarTime - barTime;
        if (barSpan <= TimeSpan.Zero)
            barSpan = TimeSpan.FromMinutes(1);

        return Server.Time.AddTicks(barSpan.Ticks * CancelPendingAfterBars);
    }

    private double GetEntryOffset(double baseRange)
    {
        return baseRange * XOffsetBaseRangePercent / 100.0;
    }

    private double[] GetTpMultipliers()
    {
        // Convert active TP level numbers into the actual Fibonacci multipliers used by price formulas.
        return GetTpLevels()
            .Select(GetFibMultiplier)
            .ToArray();
    }

    private int[] GetTpLevels()
    {
        return GetTpLevelsByProfile(TpProfile);
    }

    private static int[] GetTpLevelsByProfile(int profile)
    {
        // TP Profile compresses the valid combinations C(4,1)+C(4,2)+C(4,3)+C(4,4) into 15 optimizer-safe choices.
        // This prevents invalid heatmap holes such as TP1 >= TP2 and removes duplicate no-op parameters.
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
        // SL levels intentionally use a more conservative table than TP extensions.
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
        // TP Fibonacci extension table. Level 5 is intentionally not supported in this strategy version.
        return fibLevel switch
        {
            1 => 1.272,
            2 => 1.618,
            3 => 2.272,
            4 => 2.618,
            _ => 2.272
        };
    }

    private static bool IsValidFibLevel(int fibLevel)
    {
        return fibLevel >= 1 && fibLevel <= 4;
    }

    private string GetLegLabel(int legNumber)
    {
        // Leg-specific labels let events identify which TP milestone has closed.
        return $"{BotLabel}_L{legNumber}";
    }

    private bool IsMyBotLabel(string label)
    {
        // Manage both legacy positions labelled exactly BotLabel and new leg-labelled positions.
        if (string.IsNullOrWhiteSpace(label))
            return false;

        return label == BotLabel || label.StartsWith($"{BotLabel}_L", StringComparison.Ordinal);
    }

    private int GetLegNumber(string label)
    {
        // Legacy positions without _L suffix return 0, so they are managed but excluded from SL ladder.
        var prefix = $"{BotLabel}_L";
        if (!label.StartsWith(prefix, StringComparison.Ordinal))
            return 0;

        return int.TryParse(label[prefix.Length..], NumberStyles.Integer, CultureInfo.InvariantCulture, out var legNumber)
            ? legNumber
            : 0;
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
        // Use string matching to stay tolerant of cTrader enum naming while still detecting TP closures.
        return args.Reason.ToString().IndexOf("TakeProfit", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private void ApplyStopLossLadder(Position closedPosition)
    {
        // reachedLeg is the leg that has just closed by TP, e.g. L1/L2/L3.
        var reachedLeg = GetLegNumber(closedPosition.Label);

        // Active TP multipliers are needed to reconstruct previous TP prices from remaining positions.
        var activeTpMultipliers = GetTpMultipliers();

        // No ladder after the final leg, and legacy non-leg positions cannot drive ladder movement.
        if (reachedLeg <= 0 || reachedLeg >= activeTpMultipliers.Length)
            return;

        // Only higher-numbered legs remain eligible. Lower/equal legs already hit TP or should not be moved.
        var remainingPositions = GetMyPositions()
            .Where(position => position.TradeType == closedPosition.TradeType)
            .Select(position => new
            {
                Position = position,
                LegNumber = GetLegNumber(position.Label)
            })
            .Where(item => item.LegNumber > reachedLeg)
            .OrderBy(item => item.LegNumber)
            .ToArray();

        if (remainingPositions.Length == 0)
        {
            // Useful log for gap cases where other legs closed before this event was processed.
            Print("[{0}] SL LADDER | TP leg {1} hit but no higher leg remains open.",
                BotLabel, reachedLeg);
            return;
        }

        foreach (var item in remainingPositions)
        {
            // Calculate the new protective SL: after TP1 -> Entry, after TP2 -> TP1, after TP3 -> TP2.
            if (!TryGetLadderStopLossPrice(item.Position, item.LegNumber, reachedLeg, activeTpMultipliers, out var newStopLossPrice))
            {
                Print("[{0}] SL LADDER SKIP | TP leg {1} hit | Remaining label: {2} | Could not calculate new SL.",
                    BotLabel, reachedLeg, item.Position.Label);
                continue;
            }

            // Round to symbol digits so modification requests use a broker-valid price precision.
            newStopLossPrice = Math.Round(newStopLossPrice, Symbol.Digits);

            // Never loosen protection. BUY SL only moves up; SELL SL only moves down.
            if (!ShouldImproveStopLoss(item.Position, newStopLossPrice))
            {
                Print("[{0}] SL LADDER SKIP | TP leg {1} hit | Remaining leg {2} | Current SL: {3} already protects better/equal than target SL: {4}.",
                    BotLabel, reachedLeg, item.LegNumber, item.Position.StopLoss, newStopLossPrice);
                continue;
            }

            var oldStopLoss = item.Position.StopLoss;

            // ProtectionType.Absolute because newStopLossPrice is a real price, not a pip distance.
            var result = ModifyPosition(item.Position, newStopLossPrice, item.Position.TakeProfit, ProtectionType.Absolute);
            if (!result.IsSuccessful)
            {
                Print("[{0}] SL LADDER ERROR | TP leg {1} hit | Remaining leg {2} | Failed moving SL {3} -> {4}: {5}",
                    BotLabel, reachedLeg, item.LegNumber, oldStopLoss, newStopLossPrice, result.Error);
                continue;
            }

            Print("[{0}] SL LADDER MOVED | TP leg {1} hit | Remaining leg {2} | SL: {3} -> {4} | TP: {5}",
                BotLabel, reachedLeg, item.LegNumber, oldStopLoss, newStopLossPrice, item.Position.TakeProfit);
        }
    }

    private bool TryGetLadderStopLossPrice(Position position, int positionLegNumber, int reachedLeg, double[] activeTpMultipliers, out double stopLossPrice)
    {
        stopLossPrice = 0;

        // First target reached: protect all remaining legs at break-even.
        if (reachedLeg == 1)
        {
            stopLossPrice = position.EntryPrice;
            return true;
        }

        // For later targets, TakeProfit is required to infer the BaseRange used when the leg was created.
        if (positionLegNumber <= 0 || positionLegNumber > activeTpMultipliers.Length || !position.TakeProfit.HasValue)
            return false;

        // Remaining leg TP distance = BaseRange * that leg's TP multiplier.
        var positionTpMultiplier = activeTpMultipliers[positionLegNumber - 1];

        // After TP2 lock to TP1; after TP3 lock to TP2.
        var lockTpMultiplier = activeTpMultipliers[reachedLeg - 2];
        if (positionTpMultiplier <= 0)
            return false;

        // Reconstruct BaseRange from this remaining position's actual entry and TP price.
        var baseDistance = Math.Abs(position.TakeProfit.Value - position.EntryPrice) / positionTpMultiplier;

        // BUY lock price is above entry; SELL lock price is below entry.
        stopLossPrice = position.TradeType == TradeType.Buy
            ? position.EntryPrice + lockTpMultiplier * baseDistance
            : position.EntryPrice - lockTpMultiplier * baseDistance;
        return true;
    }

    private static bool ShouldImproveStopLoss(Position position, double newStopLossPrice)
    {
        // If no SL exists for any reason, adding one is always an improvement.
        if (!position.StopLoss.HasValue)
            return true;

        // Improvement direction is trade-specific: BUY wants higher SL, SELL wants lower SL.
        return position.TradeType == TradeType.Buy
            ? newStopLossPrice > position.StopLoss.Value
            : newStopLossPrice < position.StopLoss.Value;
    }

    private TradeType GetTradeType(SignalInfo signal)
    {
        // Convert normalized signal side into the enum cTrader order APIs require.
        return signal.Side == "BUY" ? TradeType.Buy : TradeType.Sell;
    }

    private TradeType GetOppositeTradeType(TradeType tradeType)
    {
        // Used by reverse-signal cleanup.
        return tradeType == TradeType.Buy ? TradeType.Sell : TradeType.Buy;
    }

    private void CancelMyPendingOrders()
    {
        // Cancel every pending child order belonging to this bot, used by FTMO protection.
        foreach (var order in GetMyPendingOrders())
        {
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR cancelling pending order {1}: {2}", BotLabel, order.Id, result.Error);
            else
                _pendingOrderCreatedBarCounts.Remove(order.Id);
        }
    }

    private bool CancelOppositePendingOrders(TradeType tradeType)
    {
        // Reverse signal rule: pending orders in the old direction are invalid once a new opposite signal appears.
        var oppositeTradeType = GetOppositeTradeType(tradeType);
        var allCancelled = true;

        foreach (var order in GetMyPendingOrders().Where(order => order.TradeType == oppositeTradeType))
        {
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
            {
                allCancelled = false;
                Print("[{0}] ERROR cancelling opposite pending order {1}: {2}", BotLabel, order.Id, result.Error);
            }
            else
            {
                _pendingOrderCreatedBarCounts.Remove(order.Id);
            }
        }

        return allCancelled;
    }

    private void CancelExpiredPendingOrders()
    {
        // Expiry is tracked per pending order by comparing current Bars.Count to the order's creation Bars.Count.
        foreach (var order in GetMyPendingOrders())
        {
            // Existing pending orders found after restart are tracked from the first observed bar.
            if (!_pendingOrderCreatedBarCounts.TryGetValue(order.Id, out var createdBarCount))
            {
                _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
                continue;
            }

            var barsAlive = Bars.Count - createdBarCount;
            if (barsAlive < CancelPendingAfterBars)
                continue;

            // Once a pending leg is too old, cancel it. Usually all same-entry legs expire together.
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
            {
                Print("[{0}] ERROR cancelling expired pending order {1}: {2}", BotLabel, order.Id, result.Error);
                continue;
            }

            _pendingOrderCreatedBarCounts.Remove(order.Id);
            _pendingExpiredCount++;
            Print("[{0}] PENDING EXPIRED {1} | Order: {2} | Bars alive: {3}/{4}",
                BotLabel, order.TradeType, order.Id, barsAlive, CancelPendingAfterBars);
        }
    }

    private PendingOrder[] GetMyPendingOrders()
    {
        // Prefix-aware label matching includes SEN_Combo_V2_L1..L4 as one logical bot cluster.
        return PendingOrders
            .Where(order => IsMyBotLabel(order.Label) && order.SymbolName == SymbolName)
            .ToArray();
    }

    private Position[] GetMyPositions()
    {
        // Prefix-aware label matching keeps reverse cleanup and FTMO protection aware of every child leg.
        return Positions
            .Where(position => IsMyBotLabel(position.Label) && position.SymbolName == SymbolName)
            .ToArray();
    }

    private int GetMyOpenOrderCount(TradeType tradeType)
    {
        // Same-direction exposure means both filled positions and still-pending child orders.
        return GetMyPositions().Count(position => position.TradeType == tradeType)
            + GetMyPendingOrders().Count(order => order.TradeType == tradeType);
    }

    private bool CloseOppositePositions(TradeType tradeType)
    {
        // Reverse signal rule: if new signal is BUY, close SELL positions; if SELL, close BUY positions.
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
            // If any close fails, block the new signal to avoid holding both directions at once.
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
        // Used by FTMO protection to flatten all live bot exposure.
        foreach (var position in GetMyPositions())
        {
            var result = ClosePosition(position);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR closing position {1}: {2}", BotLabel, position.Id, result.Error);
        }
    }

    private void TrackExistingPendingOrders()
    {
        // Rebuild minimal expiry tracking for pending orders that already existed before OnStart.
        foreach (var order in GetMyPendingOrders())
            _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
    }

    private void InitializeRiskState()
    {
        RestoreFtmoDailyLossState();
        RestoreFtmoMaximumLossState();
    }

    private void RestoreFtmoDailyLossState()
    {
        var today = GetFtmoCurrentDay();
        var savedDayText = LocalStorage.GetString(GetStorageKey("FtmoDay"), LocalStorageScope.Instance);
        var savedBalanceText = LocalStorage.GetString(GetStorageKey("FtmoDayStartBalance"), LocalStorageScope.Instance);
        var savedTriggeredText = LocalStorage.GetString(GetStorageKey("FtmoDailyTriggered"), LocalStorageScope.Instance);

        if (DateTime.TryParseExact(savedDayText, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var savedDay)
            && savedDay.Date == today
            && TryParseStorageDouble(savedBalanceText, out var savedBalance)
            && savedBalance > 0)
        {
            _ftmoDay = today;
            _ftmoDayStartBalance = savedBalance;
            _ftmoDailyLossTriggered = bool.TryParse(savedTriggeredText, out var savedTriggered) && savedTriggered;

        Print("[{0}] FTMO daily baseline restored | FTMO Day: {1:yyyy-MM-dd} | Start Balance: {2:F2} | Guard Floor: {3:F2} | Equity Room: {4:F2} | Triggered: {5} | Current Equity: {6:F2}",
            BotLabel,
            _ftmoDay,
            _ftmoDayStartBalance,
            GetFtmoDailyEquityFloor(),
            Account.Equity - GetFtmoDailyEquityFloor(),
            _ftmoDailyLossTriggered,
            Account.Equity);
            return;
        }

        ResetFtmoDailyLossBaseline();
    }

    private void RestoreFtmoMaximumLossState()
    {
        var savedTriggeredText = LocalStorage.GetString(GetStorageKey("FtmoMaximumTriggered"), LocalStorageScope.Instance);
        _ftmoMaximumLossTriggered = bool.TryParse(savedTriggeredText, out var savedTriggered) && savedTriggered;

        PersistRiskState(true);

        Print("[{0}] FTMO maximum loss state restored | Equity Floor: {1:F2} | Current Equity: {2:F2} | Triggered: {3} | Rule/Buffer/Guard: {4:F1}%/{5:F1}%/{6:F1}%",
            BotLabel,
            GetFtmoMaximumLossEquityFloor(),
            Account.Equity,
            _ftmoMaximumLossTriggered,
            FtmoMaximumLossPercent,
            FtmoMaximumLossBufferPercent,
            GetFtmoMaximumLossGuardPercent());
    }

    private void ResetFtmoDailyLossBaseline()
    {
        // FTMO's daily loss calculation is anchored to balance at the 00:00 CE(S)T reset,
        // while the live check uses equity so open PnL, swaps and commissions are included.
        // FtmoDayOffsetHours lets the user align Server.Time to that reset day.
        _ftmoDay = GetFtmoCurrentDay();
        _ftmoDayStartBalance = Account.Balance;
        _ftmoDailyLossTriggered = false;
        PersistRiskState(true);

        Print("[{0}] FTMO daily baseline reset | FTMO Day: {1:yyyy-MM-dd} | Start Balance: {2:F2} | Guard Floor: {3:F2} | Current Equity: {4:F2} | Equity Room: {5:F2} | Rule/Buffer/Guard: {6:F1}%/{7:F1}%/{8:F1}%",
            BotLabel,
            _ftmoDay,
            _ftmoDayStartBalance,
            GetFtmoDailyEquityFloor(),
            Account.Equity,
            Account.Equity - GetFtmoDailyEquityFloor(),
            FtmoDailyLossPercent,
            FtmoDailyLossBufferPercent,
            GetFtmoDailyGuardPercent());
    }

    private bool CheckRiskLimits()
    {
        if (CheckFtmoMaximumLossLimit())
            return true;

        return CheckFtmoDailyLossLimit();
    }

    private bool CheckFtmoMaximumLossLimit()
    {
        var equityFloor = GetFtmoMaximumLossEquityFloor();
        if (Account.Equity > equityFloor && !_ftmoMaximumLossTriggered)
            return false;

        if (_ftmoMaximumLossTriggered && !HasBotExposure())
            return true;

        if (!_ftmoMaximumLossTriggered)
        {
            _ftmoMaximumLossTriggered = true;
            _ftmoMaximumGuardTriggeredCount++;
            PersistRiskState(true);
            Print("[{0}] FTMO MAXIMUM LOSS GUARD HIT | Equity: {1:F2} <= Guard Floor: {2:F2} | Initial: {3:F2} | Rule/Buffer/Guard: {4:F1}%/{5:F1}%/{6:F1}%. Trading halted until storage/label is reset after review.",
                BotLabel,
                Account.Equity,
                equityFloor,
                FtmoInitialCapital,
                FtmoMaximumLossPercent,
                FtmoMaximumLossBufferPercent,
                GetFtmoMaximumLossGuardPercent());
        }

        FlattenBotExposure();
        return true;
    }

    private bool CheckFtmoDailyLossLimit()
    {
        // New FTMO reset day means the bot may trade again with a fresh balance baseline.
        if (GetFtmoCurrentDay() != _ftmoDay)
            ResetFtmoDailyLossBaseline();

        if (_ftmoDayStartBalance <= 0)
            return false;

        var equityFloor = GetFtmoDailyEquityFloor();
        if (Account.Equity > equityFloor && !_ftmoDailyLossTriggered)
            return false;

        if (_ftmoDailyLossTriggered && !HasBotExposure())
            return true;

        // Once triggered, keep enforcing flattening until the next FTMO reset day resets the flag.
        if (!_ftmoDailyLossTriggered)
        {
            _ftmoDailyLossTriggered = true;
            _ftmoDailyGuardTriggeredCount++;
            PersistRiskState(true);
            Print("[{0}] FTMO DAILY LOSS GUARD HIT | Equity: {1:F2} <= Guard Floor: {2:F2} | Day Start Balance: {3:F2} | Initial: {4:F2} | Rule/Buffer/Guard: {5:F1}%/{6:F1}%/{7:F1}%. Trading halted until next FTMO reset day.",
                BotLabel,
                Account.Equity,
                equityFloor,
                _ftmoDayStartBalance,
                FtmoInitialCapital,
                FtmoDailyLossPercent,
                FtmoDailyLossBufferPercent,
                GetFtmoDailyGuardPercent());
        }

        // Protection action: remove pending exposure and close all bot positions.
        FlattenBotExposure();
        return true;
    }

    private DateTime GetFtmoCurrentDay()
    {
        // Shift Server.Time into the desired FTMO reset clock before taking Date.
        // Example: if Server.Time is UTC and FTMO reset is CEST, use +2 during CEST.
        return Server.Time.AddHours(FtmoDayOffsetHours).Date;
    }

    private double GetFtmoDailyGuardPercent()
    {
        return FtmoDailyLossPercent - FtmoDailyLossBufferPercent;
    }

    private double GetFtmoMaximumLossGuardPercent()
    {
        return FtmoMaximumLossPercent - FtmoMaximumLossBufferPercent;
    }

    private double GetFtmoDailyEquityFloor()
    {
        return _ftmoDayStartBalance - FtmoInitialCapital * GetFtmoDailyGuardPercent() / 100.0;
    }

    private double GetFtmoMaximumLossEquityFloor()
    {
        return FtmoInitialCapital - FtmoInitialCapital * GetFtmoMaximumLossGuardPercent() / 100.0;
    }

    private void FlattenBotExposure()
    {
        CancelMyPendingOrders();
        CloseMyPositions();
    }

    private bool HasBotExposure()
    {
        return GetMyPendingOrders().Length > 0 || GetMyPositions().Length > 0;
    }

    private void PersistRiskState(bool flush)
    {
        LocalStorage.SetString(GetStorageKey("FtmoDay"), _ftmoDay.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), LocalStorageScope.Instance);
        LocalStorage.SetString(GetStorageKey("FtmoDayStartBalance"), _ftmoDayStartBalance.ToString("R", CultureInfo.InvariantCulture), LocalStorageScope.Instance);
        LocalStorage.SetString(GetStorageKey("FtmoDailyTriggered"), _ftmoDailyLossTriggered.ToString(CultureInfo.InvariantCulture), LocalStorageScope.Instance);
        LocalStorage.SetString(GetStorageKey("FtmoMaximumTriggered"), _ftmoMaximumLossTriggered.ToString(CultureInfo.InvariantCulture), LocalStorageScope.Instance);

        if (flush)
            LocalStorage.Flush(LocalStorageScope.Instance);
    }

    private void PersistRiskState()
    {
        PersistRiskState(true);
    }

    private string GetStorageKey(string suffix)
    {
        // cTrader LocalStorage keys can contain only Latin letters, numbers and spaces.
        // Bot labels and broker symbols may include underscores, dots, slashes or other separators,
        // so normalize every part before calling LocalStorage.
        return CollapseStorageKeySpaces(string.Join(" ",
            SanitizeStorageKeyPart(BotLabel),
            SanitizeStorageKeyPart(SymbolName),
            SanitizeStorageKeyPart(suffix)));
    }

    private static bool TryParseStorageDouble(string value, out double result)
    {
        return double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out result);
    }

    private static string SanitizeStorageKeyPart(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return "Empty";

        var chars = value
            .Select(character => IsStorageKeyCharacter(character) ? character : ' ')
            .ToArray();

        var sanitized = CollapseStorageKeySpaces(new string(chars));
        return string.IsNullOrWhiteSpace(sanitized) ? "Empty" : sanitized;
    }

    private static bool IsStorageKeyCharacter(char character)
    {
        return character == ' '
            || character is >= 'A' and <= 'Z'
            || character is >= 'a' and <= 'z'
            || character is >= '0' and <= '9';
    }

    private static string CollapseStorageKeySpaces(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return "Empty";

        var parts = value
            .Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);

        return parts.Length == 0 ? "Empty" : string.Join(" ", parts);
    }

    private bool IsCurrentSymbol(string csvSymbol)
    {
        // Accept both cTrader SymbolName and Symbol.Name because brokers may expose either in exports.
        return csvSymbol.Trim().Equals(SymbolName, StringComparison.OrdinalIgnoreCase)
            || csvSymbol.Trim().Equals(Symbol.Name, StringComparison.OrdinalIgnoreCase);
    }

    private static int FindColumn(string[] header, params string[] names)
    {
        // Return the first header index that matches any accepted alias.
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
        // Signals are expected to be simple comma-separated rows without quoted commas.
        return line.Split(',').Select(part => part.Trim()).ToArray();
    }

    private static DateTime TrimToMinute(DateTime value)
    {
        // CSV matching is minute-level; seconds are intentionally removed to avoid tiny timestamp mismatches.
        return new DateTime(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0, value.Kind);
    }

    private bool TryGetSignalForClosedBar(DateTime barTime, DateTime nextBarTime, out SignalInfo signal, out int signalsInBar)
    {
        signal = default;
        signalsInBar = 0;

        if (_signalsByTime.Length == 0)
            return false;

        // If multiple CSV signals fall inside one cTrader bar, use the latest timestamp in that bar.
        SignalInfo latestSignal = default;
        var hasSignal = false;
        var startIndex = LowerBoundSignalIndex(barTime);

        for (var i = startIndex; i < _signalsByTime.Length; i++)
        {
            var candidate = _signalsByTime[i];

            // Closed-bar interval rule: include barTime, exclude nextBarTime.
            if (candidate.Time >= nextBarTime)
                break;

            signalsInBar++;
            // Keep the latest signal time only.
            if (hasSignal && candidate.Time <= latestSignal.Time)
                continue;

            latestSignal = candidate;
            hasSignal = true;
        }

        if (!hasSignal)
            return false;

        // Output the signal selected for this closed bar.
        signal = latestSignal;

        if (signalsInBar > 1)
        {
            Print("[{0}] Multiple signals in cTrader bar | Bar: {1:yyyy-MM-dd HH:mm} -> {2:yyyy-MM-dd HH:mm} | Count: {3} | Using latest: {4:yyyy-MM-dd HH:mm} {5}",
                BotLabel,
                barTime,
                nextBarTime,
                signalsInBar,
                signal.Time,
                signal.Side);
        }

        return true;
    }

    private int LowerBoundSignalIndex(DateTime time)
    {
        var low = 0;
        var high = _signalsByTime.Length;

        while (low < high)
        {
            var mid = low + (high - low) / 2;
            if (_signalsByTime[mid].Time < time)
                low = mid + 1;
            else
                high = mid;
        }

        return low;
    }

    private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
    {
        var position = args.Position;

        // Ignore fills from other bots, manual orders, or other symbols.
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        // Once filled, the order no longer needs pending-expiry tracking.
        _pendingOrderCreatedBarCounts.Remove(args.PendingOrder.Id);
        _pendingFilledCount++;

        Print("[{0}] FILLED {1} | Label: {2} | Leg: {3} | Entry: {4} | SL: {5} | TP: {6}",
            BotLabel, position.TradeType, position.Label, GetLegNumber(position.Label), position.EntryPrice, position.StopLoss, position.TakeProfit);
    }

    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        var order = args.PendingOrder;

        // Ignore cancelled orders that do not belong to this bot/symbol.
        if (!IsMyBotLabel(order.Label) || order.SymbolName != SymbolName)
            return;

        // Remove tracking entry regardless of cancellation reason.
        _pendingOrderCreatedBarCounts.Remove(order.Id);
        _pendingCancelledCount++;

        Print("[{0}] PENDING CANCELLED {1} | Label: {2} | Reason: {3}", BotLabel, order.TradeType, order.Label, args.Reason);
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var position = args.Position;

        // Ignore closed positions outside this bot/symbol namespace.
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Label: {2} | Leg: {3} | Net PnL: {4:F2} | Pips: {5:F1} | Reason: {6}",
            BotLabel, position.TradeType, position.Label, GetLegNumber(position.Label), position.NetProfit, position.Pips, args.Reason);

        // Only TP closes advance the SL ladder. SL/reverse/manual/DD closes must not move remaining stops.
        if (IsTakeProfitClose(args))
            ApplyStopLossLadder(position);
    }

    protected override double GetFitness(GetFitnessArgs args)
    {
        if (args.TotalTrades < FitnessMinTrades)
            return double.MinValue;

        if (args.MaxEquityDrawdownPercentages > FitnessMaxEquityDrawdownPercent)
            return double.MinValue;

        if (args.MaxBalanceDrawdownPercentages > FitnessMaxBalanceDrawdownPercent)
            return double.MinValue;

        if (args.AverageTrade <= 0)
            return double.MinValue;

        if (args.ProfitFactor <= 1.0)
            return double.MinValue;

        if (args.NetProfit <= 0)
            return double.MinValue;

        return args.ProfitFactor;
    }

    private readonly struct SignalInfo
    {
        // Immutable normalized signal row after CSV parsing and time offset alignment.
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
