using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using cAlgo.API;

namespace cAlgo.Robots;

/*
 * SEN_SignalExecutor_V0 - generic CSV signal execution template for research.
 *
 * Scope:
 *   - Executes signals for the chart symbol only.
 *   - Uses closed cTrader bars only; matched bar OHLC comes from cTrader, not the CSV.
 *   - Supports market, stop-breakout, and limit-pullback execution.
 *   - Provides simple risk sizing, SL/TP modes, guards, duplicate prevention, and logs.
 *   - Does not provide retail/FTMO policy, multi-symbol execution, or restart recovery guarantees.
 *
 * Required CSV header:
 *   bartime,side,atr
 *
 * Optional CSV columns:
 *   signal_id,symbol,entry_price,sl_price,tp_price
 *
 * CSV atr must be in price units, not pips. Example: 10 pips on EURUSD with PipSize 0.0001
 * must be exported as 0.0010.
 */

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_SignalExecutor_V0 : Robot
{
    [Parameter("Signal CSV Path", Group = "01 Signal",
        DefaultValue = @"D:\Auto Trading\SEN05\raw_signals\combo\combo_HK50_H4_20230103_20260512_signals.csv",
        Description = "CSV header required: bartime, side/signal, atr. ATR must be price units.")]
    public string CsvPath { get; set; }

    [Parameter("CSV Time Offset Hours", Group = "01 Signal",
        DefaultValue = 0, MinValue = -12, MaxValue = 14, Step = 1,
        Description = "Adds hours to CSV bartime before matching cTrader server bars. Do not optimize.")]
    public int CsvTimeOffsetHours { get; set; }

    [Parameter("Bot Label", Group = "01 Signal", DefaultValue = "SEN_SignalExecutor_V0",
        Description = "Used to label and filter this bot's positions and pending orders.")]
    public string BotLabel { get; set; }

    [Parameter("Max Signal Bar Clock Hours", Group = "01 Signal",
        DefaultValue = 0, MinValue = 0, MaxValue = 168, Step = 1,
        Description = "0 disables. Skips signal bars whose actual open-to-open span exceeds this many hours.")]
    public double MaxSignalBarClockHours { get; set; }

    [Parameter("Execution Preset", Group = "02 Preset", DefaultValue = ExecutionPresetOption.SimpleMarketRR,
        Description = "Presets override detailed Entry/Range/SL/TP modes. Custom uses detailed settings.")]
    public ExecutionPresetOption ExecutionPreset { get; set; }

    [Parameter("Entry Mode", Group = "03 Entry", DefaultValue = EntryModeOption.MarketNextBar,
        Description = "Used only when preset is Custom: MarketNextBar, StopBreakout, or LimitPullback.")]
    public EntryModeOption EntryMode { get; set; }

    [Parameter("Entry Price Source", Group = "03 Entry", DefaultValue = EntryPriceSourceOption.SignalHighLow,
        Description = "CsvEntryPrice requires entry_price column. Signal OHLC comes from matched cTrader bar.")]
    public EntryPriceSourceOption EntryPriceSource { get; set; }

    [Parameter("Entry Offset Mode", Group = "03 Entry", DefaultValue = EntryOffsetModeOption.PercentBaseRange,
        Description = "Used by pending-order entry modes. MarketNextBar ignores entry offset.")]
    public EntryOffsetModeOption EntryOffsetMode { get; set; }

    [Parameter("Entry Offset Value", Group = "03 Entry",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 500.0, Step = 1.0,
        Description = "Meaning depends on Entry Offset Mode: percent, pips, or raw price units.")]
    public double EntryOffsetValue { get; set; }

    [Parameter("Expiration Bars", Group = "03 Entry",
        DefaultValue = 1, MinValue = 1, MaxValue = 50, Step = 1,
        Description = "Pending orders expire this many bars after the next bar open. Market ignores this.")]
    public int ExpirationBars { get; set; }

    [Parameter("Base Range Mode", Group = "04 Range", DefaultValue = BaseRangeModeOption.MaxAtrAndCandleRange,
        Description = "Matched candle range comes from cTrader. CSV ATR must be price units.")]
    public BaseRangeModeOption BaseRangeMode { get; set; }

    [Parameter("Fixed Range Pips", Group = "04 Range",
        DefaultValue = 100.0, MinValue = 0.1, MaxValue = 10000.0, Step = 1.0,
        Description = "Used only when Base Range Mode = FixedPips.")]
    public double FixedRangePips { get; set; }

    [Parameter("Stop Loss Mode", Group = "05 Stop Loss", DefaultValue = StopLossModeOption.BaseRangeFib,
        Description = "CsvStopLossPrice requires sl_price/stop_loss column.")]
    public StopLossModeOption StopLossMode { get; set; }

    [Parameter("KSL Fibonacci Level (1-4)", Group = "05 Stop Loss",
        DefaultValue = 2, MinValue = 1, MaxValue = 4, Step = 1,
        Description = "BaseRangeFib multipliers: 1=1.000, 2=1.272, 3=1.618, 4=2.000.")]
    public int KslFibLevel { get; set; }

    [Parameter("Stop Loss Value", Group = "05 Stop Loss",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10000.0, Step = 0.1,
        Description = "Used by AtrMultiple as ATR multiple and by FixedPips as pips.")]
    public double StopLossValue { get; set; }

    [Parameter("SL Buffer Pips", Group = "05 Stop Loss",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 1000.0, Step = 0.1,
        Description = "Used only by CandleExtreme. Buy SL below matched low; sell SL above matched high.")]
    public double StopLossBufferPips { get; set; }

    [Parameter("Take Profit Mode", Group = "06 Take Profit", DefaultValue = TakeProfitModeOption.RiskReward,
        Description = "CsvTakeProfitPrice requires tp_price/take_profit column.")]
    public TakeProfitModeOption TakeProfitMode { get; set; }

    [Parameter("Risk Reward", Group = "06 Take Profit",
        DefaultValue = 1.5, MinValue = 0.1, MaxValue = 20.0, Step = 0.1,
        Description = "Used by RiskReward TP mode.")]
    public double RiskReward { get; set; }

    [Parameter("TP Profile (1-15)", Group = "06 Take Profit",
        DefaultValue = 15, MinValue = 1, MaxValue = 15, Step = 1,
        Description = "Used by FibProfile. Encodes all valid TP combinations from levels 1-4.")]
    public int TpProfile { get; set; }

    [Parameter("Fixed TP Pips", Group = "06 Take Profit",
        DefaultValue = 150.0, MinValue = 0.1, MaxValue = 10000.0, Step = 1.0,
        Description = "Used only when Take Profit Mode = FixedPips.")]
    public double FixedTakeProfitPips { get; set; }

    [Parameter("Volume Mode", Group = "07 Risk", DefaultValue = VolumeModeOption.FixedRiskPercent,
        Description = "FixedRiskPercent is recommended for fair cross-symbol research.")]
    public VolumeModeOption VolumeMode { get; set; }

    [Parameter("Risk % per Signal", Group = "07 Risk",
        DefaultValue = 0.5, MinValue = 0.01, MaxValue = 3.0, Step = 0.01,
        Description = "Total signal risk. Multi-TP legs split this value equally. Keep fixed during optimization.")]
    public double RiskPercent { get; set; }

    [Parameter("Fixed Volume Lots", Group = "07 Risk",
        DefaultValue = 0.01, MinValue = 0.01, MaxValue = 100.0, Step = 0.01,
        Description = "Used only when Volume Mode = FixedLots. This is per leg.")]
    public double FixedVolumeLots { get; set; }

    [Parameter("Max Spread / SL % (0=off)", Group = "08 Guards",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 50.0, Step = 0.5,
        Description = "Skips if spread pips divided by SL pips exceeds this percent.")]
    public double MaxSpreadToStopLossPercent { get; set; }

    [Parameter("Min Risk Reward", Group = "08 Guards",
        DefaultValue = 1.0, MinValue = 0.0, MaxValue = 20.0, Step = 0.1,
        Description = "Skips if first TP distance divided by SL distance is below this value.")]
    public double MinRiskReward { get; set; }

    [Parameter("Exposure Policy", Group = "08 Guards", DefaultValue = ExposurePolicyOption.EverySignal,
        Description = "EverySignal, SkipIfAnyOpenPosition, or CloseOppositeThenEnter.")]
    public ExposurePolicyOption ExposurePolicy { get; set; }

    [Parameter("Enable SL Ladder", Group = "09 Advanced", DefaultValue = false,
        Description = "Only meaningful with FibProfile multi-leg trades. No restart recovery guarantee in V0.")]
    public bool EnableStopLossLadder { get; set; }

    [Parameter("Log Level", Group = "09 Advanced", DefaultValue = ExecutorLogLevel.Basic,
        Description = "Verbose prints TradePlan details and skip diagnostics. Do not optimize.")]
    public ExecutorLogLevel LogLevel { get; set; }

    private readonly List<SignalInfo> _signals = new();
    private SignalInfo[] _signalsByTime = Array.Empty<SignalInfo>();
    private readonly HashSet<string> _processedSignalKeys = new(StringComparer.Ordinal);

    private CsvSchema _csvSchema;
    private EffectiveSettings _settings;

    private int _barsWithoutSignal;
    private int _matchedSignalCount;
    private int _duplicateSignalCount;
    private int _multiSignalBarCount;
    private int _tradePlansBuilt;
    private int _ordersPlaced;
    private int _ordersFailed;
    private int _pendingFilledCount;
    private int _pendingCancelledCount;
    private int _skipExposureCount;
    private int _skipSpreadCount;
    private int _skipRiskRewardCount;
    private int _skipBrokerDistanceCount;
    private int _skipVolumeCount;
    private int _skipInvalidPlanCount;
    private int _slLadderMoves;

    private const int FitnessMinTrades = 30;
    private const double FitnessMaxEquityDrawdownPercent = 25.0;

    protected override void OnStart()
    {
        Positions.Opened += OnPositionOpened;
        Positions.Closed += OnPositionClosed;
        Positions.Modified += OnPositionModified;
        PendingOrders.Filled += OnPendingOrderFilled;
        PendingOrders.Cancelled += OnPendingOrderCancelled;

        _settings = ResolveEffectiveSettings();

        if (!ValidateParameters())
        {
            Stop();
            return;
        }

        Print("[{0}] Broker distance | MinStopLossDistance: {1} | MinTakeProfitDistance: {2} | PipSize: {3}",
            BotLabel, Symbol.MinStopLossDistance, Symbol.MinTakeProfitDistance, Symbol.PipSize);

        LogEffectiveConfig();
        LoadSignals();

        if (_signals.Count == 0)
        {
            Print("[{0}] ERROR: no valid signals loaded. Stopping.", BotLabel);
            Stop();
            return;
        }

        Print("[{0}] Started | Symbol: {1} | TF: {2} | Signals: {3} | DuplicateKey: matched cTrader bar open time + side + chart symbol",
            BotLabel, SymbolName, TimeFrame, _signals.Count);
    }

    protected override void OnBarClosed()
    {
        if (Bars.Count < 2 || _signalsByTime.Length == 0)
            return;

        var matchedBarOpenTime = TrimToMinute(Bars.OpenTimes.Last(1));
        var nextBarOpenTime = TrimToMinute(Bars.OpenTimes.Last(0));
        var barSpan = nextBarOpenTime - matchedBarOpenTime;
        if (barSpan <= TimeSpan.Zero)
            barSpan = TimeSpan.FromMinutes(1);

        var signals = GetSignalsForClosedBar(matchedBarOpenTime, nextBarOpenTime);
        if (signals.Count == 0)
        {
            _barsWithoutSignal++;
            return;
        }

        if (signals.Count > 1)
            _multiSignalBarCount++;

        var barClockHours = barSpan.TotalHours;
        if (MaxSignalBarClockHours > 0 && barClockHours > MaxSignalBarClockHours)
        {
            _skipInvalidPlanCount += signals.Count;
            Print("[{0}] {1} signal(s) at {2:yyyy-MM-dd HH:mm} skipped: bar clock span {3:F2}h > MaxSignalBarClockHours {4:F2}h.",
                BotLabel, signals.Count, matchedBarOpenTime, barClockHours, MaxSignalBarClockHours);
            return;
        }

        foreach (var signal in signals)
        {
            _matchedSignalCount++;

            var duplicateKey = GetDuplicateKey(matchedBarOpenTime, signal.Side);
            if (!_processedSignalKeys.Add(duplicateKey))
            {
                _duplicateSignalCount++;
                if (LogLevel == ExecutorLogLevel.Verbose)
                    Print("[{0}] DUPLICATE SKIP | Key: {1} | CSV time: {2:yyyy-MM-dd HH:mm} | SourceLine: {3}",
                        BotLabel, duplicateKey, signal.Time, signal.SourceLine);
                continue;
            }

            if (!ApplyExposurePolicy(signal.Side))
            {
                _skipExposureCount++;
                continue;
            }

            if (!TryBuildTradePlan(signal, matchedBarOpenTime, nextBarOpenTime, barSpan, signals.Count, out var plan, out var reason))
            {
                _skipInvalidPlanCount++;
                Print("[{0}] PLAN SKIP | {1} at {2:yyyy-MM-dd HH:mm} | Line: {3} | Reason: {4}",
                    BotLabel, signal.Side, matchedBarOpenTime, signal.SourceLine, reason);
                continue;
            }

            _tradePlansBuilt++;

            if (!ValidateTradePlan(plan, out reason))
            {
                Print("[{0}] VALIDATION SKIP | {1} at {2:yyyy-MM-dd HH:mm} | Line: {3} | Reason: {4}",
                    BotLabel, signal.Side, matchedBarOpenTime, signal.SourceLine, reason);
                continue;
            }

            PlaceTradePlan(plan);
        }
    }

    protected override void OnStop()
    {
        Positions.Opened -= OnPositionOpened;
        Positions.Closed -= OnPositionClosed;
        Positions.Modified -= OnPositionModified;
        PendingOrders.Filled -= OnPendingOrderFilled;
        PendingOrders.Cancelled -= OnPendingOrderCancelled;

        Print("[{0}] Stopped | Signals loaded: {1} | Matched: {2} | Duplicates skipped: {3} | Multi-signal bars: {4} | Bars without signal: {5}",
            BotLabel, _signals.Count, _matchedSignalCount, _duplicateSignalCount, _multiSignalBarCount, _barsWithoutSignal);
        Print("[{0}] Summary | Plans: {1} | OrdersPlaced: {2} | OrdersFailed: {3} | PendingFilled: {4} | PendingCancelled: {5} | Skip Exposure: {6} | Skip Spread: {7} | Skip RR: {8} | Skip BrokerDistance: {9} | Skip Volume: {10} | Skip InvalidPlan: {11} | SLLadderMoves: {12}",
            BotLabel,
            _tradePlansBuilt,
            _ordersPlaced,
            _ordersFailed,
            _pendingFilledCount,
            _pendingCancelledCount,
            _skipExposureCount,
            _skipSpreadCount,
            _skipRiskRewardCount,
            _skipBrokerDistanceCount,
            _skipVolumeCount,
            _skipInvalidPlanCount,
            _slLadderMoves);
    }

    private EffectiveSettings ResolveEffectiveSettings()
    {
        var settings = new EffectiveSettings
        {
            EntryMode = EntryMode,
            EntryPriceSource = EntryPriceSource,
            EntryOffsetMode = EntryOffsetMode,
            BaseRangeMode = BaseRangeMode,
            StopLossMode = StopLossMode,
            TakeProfitMode = TakeProfitMode
        };

        switch (ExecutionPreset)
        {
            case ExecutionPresetOption.SimpleMarketRR:
                settings.EntryMode = EntryModeOption.MarketNextBar;
                settings.EntryPriceSource = EntryPriceSourceOption.SignalClose;
                settings.EntryOffsetMode = EntryOffsetModeOption.None;
                settings.BaseRangeMode = BaseRangeModeOption.MaxAtrAndCandleRange;
                settings.StopLossMode = StopLossModeOption.BaseRangeFib;
                settings.TakeProfitMode = TakeProfitModeOption.RiskReward;
                break;

            case ExecutionPresetOption.BreakoutFib:
                settings.EntryMode = EntryModeOption.StopBreakout;
                settings.EntryPriceSource = EntryPriceSourceOption.SignalHighLow;
                settings.EntryOffsetMode = EntryOffsetModeOption.PercentBaseRange;
                settings.BaseRangeMode = BaseRangeModeOption.MaxAtrAndCandleRange;
                settings.StopLossMode = StopLossModeOption.BaseRangeFib;
                settings.TakeProfitMode = TakeProfitModeOption.FibProfile;
                break;

            case ExecutionPresetOption.PullbackRR:
                settings.EntryMode = EntryModeOption.LimitPullback;
                settings.EntryPriceSource = EntryPriceSourceOption.SignalClose;
                settings.EntryOffsetMode = EntryOffsetModeOption.PercentBaseRange;
                settings.BaseRangeMode = BaseRangeModeOption.MaxAtrAndCandleRange;
                settings.StopLossMode = StopLossModeOption.BaseRangeFib;
                settings.TakeProfitMode = TakeProfitModeOption.RiskReward;
                break;
        }

        return settings;
    }

    private void LogEffectiveConfig()
    {
        var presetNote = ExecutionPreset == ExecutionPresetOption.Custom
            ? "Custom uses detailed settings."
            : "Preset overrides detailed Entry/Range/SL/TP mode parameters.";

        Print("[{0}] Effective Config | Preset: {1} | {2}", BotLabel, ExecutionPreset, presetNote);
        Print("[{0}] Effective Entry | Mode: {1} | Source: {2} | Offset: {3} {4} | ExpirationBars: {5}",
            BotLabel,
            _settings.EntryMode,
            _settings.EntryPriceSource,
            _settings.EntryOffsetMode,
            EntryOffsetValue.ToString("F3", CultureInfo.InvariantCulture),
            _settings.EntryMode == EntryModeOption.MarketNextBar ? "(ignored)" : "",
            _settings.EntryMode == EntryModeOption.MarketNextBar ? 0 : ExpirationBars);
        Print("[{0}] Effective Protection | BaseRange: {1} | SL: {2} | KSL: {3} ({4:F3}) | TP: {5} | RR: {6:F2} | TP Profile: {7} [{8}]",
            BotLabel,
            _settings.BaseRangeMode,
            _settings.StopLossMode,
            KslFibLevel,
            GetKslMultiplier(),
            _settings.TakeProfitMode,
            RiskReward,
            TpProfile,
            FormatLevels(GetTpLevelsByProfile(TpProfile)));
    }

    private bool ValidateParameters()
    {
        if (string.IsNullOrWhiteSpace(CsvPath))
        {
            Print("[{0}] ERROR: CSV path is empty.", BotLabel);
            return false;
        }

        if (Symbol.PipSize <= 0)
        {
            Print("[{0}] ERROR: invalid Symbol.PipSize: {1}", BotLabel, Symbol.PipSize);
            return false;
        }

        if (KslFibLevel < 1 || KslFibLevel > 4 || TpProfile < 1 || TpProfile > 15)
        {
            Print("[{0}] ERROR: KSL must be 1-4 and TP Profile must be 1-15.", BotLabel);
            return false;
        }

        if (RiskPercent <= 0 || RiskPercent > 3.0 || FixedVolumeLots <= 0)
        {
            Print("[{0}] ERROR: risk/volume values are invalid.", BotLabel);
            return false;
        }

        if (EntryOffsetValue < 0 || FixedRangePips <= 0 || StopLossValue <= 0 || FixedTakeProfitPips <= 0)
        {
            Print("[{0}] ERROR: range/offset/SL/TP numeric values are invalid.", BotLabel);
            return false;
        }

        if (RiskReward <= 0 || MinRiskReward < 0 || MaxSpreadToStopLossPercent < 0)
        {
            Print("[{0}] ERROR: RR and guard values are invalid.", BotLabel);
            return false;
        }

        return true;
    }

    private void LoadSignals()
    {
        _signals.Clear();
        _signalsByTime = Array.Empty<SignalInfo>();

        if (!System.IO.File.Exists(CsvPath))
        {
            Print("[{0}] ERROR: CSV file not found: {1}", BotLabel, CsvPath);
            return;
        }

        var lines = System.IO.File.ReadAllLines(CsvPath);
        if (lines.Length == 0)
        {
            Print("[{0}] ERROR: CSV file is empty: {1}", BotLabel, CsvPath);
            return;
        }

        var header = SplitCsvLine(lines[0]);
        if (!TryBuildCsvSchema(header, out _csvSchema, out var schemaError))
        {
            Print("[{0}] ERROR: invalid CSV schema: {1}", BotLabel, schemaError);
            return;
        }

        if (!ValidateCsvSchemaForSettings(_csvSchema, out schemaError))
        {
            Print("[{0}] ERROR: CSV schema does not support selected modes: {1}", BotLabel, schemaError);
            return;
        }

        var loaded = 0;
        var skipped = 0;

        for (var i = 1; i < lines.Length; i++)
        {
            var parts = SplitCsvLine(lines[i]);
            if (parts.Length == 0 || string.IsNullOrWhiteSpace(lines[i]))
                continue;

            if (!TryParseSignal(parts, i + 1, out var signal, out var reason))
            {
                skipped++;
                if (LogLevel == ExecutorLogLevel.Verbose)
                    Print("[{0}] CSV row skipped | Line: {1} | Reason: {2}", BotLabel, i + 1, reason);
                continue;
            }

            _signals.Add(signal);
            loaded++;
        }

        _signalsByTime = _signals.OrderBy(signal => signal.Time).ToArray();

        Print("[{0}] CSV loaded: {1} signals | skipped: {2} | symbol: {3} | offset: {4}h | {5}",
            BotLabel, loaded, skipped, SymbolName, CsvTimeOffsetHours, CsvPath);
    }

    private bool TryBuildCsvSchema(string[] header, out CsvSchema schema, out string error)
    {
        schema = new CsvSchema
        {
            TimeIndex = FindColumn(header, "bartime", "time", "datetime", "date"),
            SymbolIndex = FindColumn(header, "symbol", "symbolname"),
            SideIndex = FindColumn(header, "side", "signal", "direction"),
            AtrIndex = FindColumn(header, "atr"),
            SignalIdIndex = FindColumn(header, "signal_id", "id"),
            EntryPriceIndex = FindColumn(header, "entry_price", "entry", "entryprice"),
            StopLossPriceIndex = FindColumn(header, "sl_price", "stop_loss", "stoploss", "sl"),
            TakeProfitPriceIndex = FindColumn(header, "tp_price", "take_profit", "takeprofit", "tp")
        };

        if (schema.TimeIndex < 0)
        {
            error = "missing time column: bartime/time/datetime/date";
            return false;
        }

        if (schema.SideIndex < 0)
        {
            error = "missing side column: side/signal/direction";
            return false;
        }

        if (schema.AtrIndex < 0)
        {
            error = "missing atr column. ATR must be price units.";
            return false;
        }

        error = "";
        return true;
    }

    private bool ValidateCsvSchemaForSettings(CsvSchema schema, out string error)
    {
        if (_settings.EntryPriceSource == EntryPriceSourceOption.CsvEntryPrice && schema.EntryPriceIndex < 0)
        {
            error = "EntryPriceSource=CsvEntryPrice requires entry_price column.";
            return false;
        }

        if (_settings.StopLossMode == StopLossModeOption.CsvStopLossPrice && schema.StopLossPriceIndex < 0)
        {
            error = "StopLossMode=CsvStopLossPrice requires sl_price/stop_loss column.";
            return false;
        }

        if (_settings.TakeProfitMode == TakeProfitModeOption.CsvTakeProfitPrice && schema.TakeProfitPriceIndex < 0)
        {
            error = "TakeProfitMode=CsvTakeProfitPrice requires tp_price/take_profit column.";
            return false;
        }

        error = "";
        return true;
    }

    private bool TryParseSignal(string[] parts, int sourceLine, out SignalInfo signal, out string reason)
    {
        signal = default;

        var requiredIndex = new[]
        {
            _csvSchema.TimeIndex,
            _csvSchema.SideIndex,
            _csvSchema.AtrIndex,
            _csvSchema.SymbolIndex,
            _csvSchema.SignalIdIndex,
            _csvSchema.EntryPriceIndex,
            _csvSchema.StopLossPriceIndex,
            _csvSchema.TakeProfitPriceIndex
        }.Max();

        if (parts.Length <= requiredIndex)
        {
            reason = "not enough columns";
            return false;
        }

        if (_csvSchema.SymbolIndex >= 0 && !IsCurrentSymbol(parts[_csvSchema.SymbolIndex]))
        {
            reason = "row symbol does not match chart symbol";
            return false;
        }

        if (!TryParseTime(parts[_csvSchema.TimeIndex].Trim(), out var csvTime))
        {
            reason = "invalid bartime";
            return false;
        }

        if (!TryParseSide(parts[_csvSchema.SideIndex], out var side))
        {
            reason = "invalid side/signal";
            return false;
        }

        if (!double.TryParse(parts[_csvSchema.AtrIndex].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var atr) || atr <= 0)
        {
            reason = "invalid atr. ATR must be price units and > 0";
            return false;
        }

        if (!TryParseOptionalPrice(parts, _csvSchema.EntryPriceIndex, out var entryPrice, out reason)
            || !TryParseOptionalPrice(parts, _csvSchema.StopLossPriceIndex, out var stopLossPrice, out reason)
            || !TryParseOptionalPrice(parts, _csvSchema.TakeProfitPriceIndex, out var takeProfitPrice, out reason))
            return false;

        var signalId = _csvSchema.SignalIdIndex >= 0 ? parts[_csvSchema.SignalIdIndex].Trim() : "";

        signal = new SignalInfo(
            TrimToMinute(csvTime.AddHours(CsvTimeOffsetHours)),
            side,
            atr,
            sourceLine,
            signalId,
            entryPrice,
            stopLossPrice,
            takeProfitPrice);

        reason = "";
        return true;
    }

    private bool TryBuildTradePlan(SignalInfo signal, DateTime matchedBarOpenTime, DateTime nextBarOpenTime, TimeSpan barSpan, int signalsInBar, out TradePlan plan, out string reason)
    {
        plan = default;

        var signalHigh = Bars.HighPrices.Last(1);
        var signalLow = Bars.LowPrices.Last(1);
        var signalClose = Bars.ClosePrices.Last(1);
        var signalRange = signalHigh - signalLow;

        if (!TryCalculateBaseRange(signal, signalRange, out var baseRange, out reason))
            return false;

        if (!TryCalculateEntryPrice(signal, signalHigh, signalLow, signalClose, baseRange, out var entryPrice, out var entryOffset, out reason))
            return false;

        var tradeType = signal.Side == SignalSide.Buy ? TradeType.Buy : TradeType.Sell;
        if (!TryCalculateStopLoss(signal, tradeType, entryPrice, signalHigh, signalLow, baseRange, out var stopLossPrice, out reason))
            return false;

        var stopLossPips = Math.Abs(entryPrice - stopLossPrice) / Symbol.PipSize;
        if (stopLossPips <= 0)
        {
            reason = "SL distance is not positive";
            return false;
        }

        if (!TryCalculateTakeProfits(signal, tradeType, entryPrice, stopLossPrice, baseRange, out var takeProfitPrices, out reason))
            return false;

        var takeProfitPips = takeProfitPrices.Select(tp => Math.Abs(tp - entryPrice) / Symbol.PipSize).ToArray();
        var firstRiskReward = takeProfitPips[0] / stopLossPips;
        var expiration = _settings.EntryMode == EntryModeOption.MarketNextBar ? (DateTime?)null : nextBarOpenTime.AddTicks(barSpan.Ticks * ExpirationBars);

        if (!TryCalculateVolume(stopLossPips, takeProfitPrices.Length, out var volumePerLeg, out reason))
            return false;

        plan = new TradePlan(signal, tradeType, matchedBarOpenTime, nextBarOpenTime, signalsInBar, _settings.EntryMode, entryPrice, entryOffset, stopLossPrice, takeProfitPrices, stopLossPips, takeProfitPips, firstRiskReward, volumePerLeg, expiration, signal.Atr, signalRange, baseRange);
        return true;
    }

    private bool TryCalculateBaseRange(SignalInfo signal, double signalRange, out double baseRange, out string reason)
    {
        baseRange = _settings.BaseRangeMode switch
        {
            BaseRangeModeOption.MaxAtrAndCandleRange => Math.Max(signal.Atr, signalRange),
            BaseRangeModeOption.AtrOnly => signal.Atr,
            BaseRangeModeOption.CandleRangeOnly => signalRange,
            BaseRangeModeOption.FixedPips => FixedRangePips * Symbol.PipSize,
            _ => 0
        };

        if (baseRange <= 0)
        {
            reason = string.Format(CultureInfo.InvariantCulture, "invalid BaseRange. Mode={0}, ATR={1}, CandleRange={2}", _settings.BaseRangeMode, signal.Atr, signalRange);
            return false;
        }

        reason = "";
        return true;
    }

    private bool TryCalculateEntryPrice(SignalInfo signal, double signalHigh, double signalLow, double signalClose, double baseRange, out double entryPrice, out double entryOffset, out string reason)
    {
        entryOffset = GetEntryOffset(baseRange);

        if (_settings.EntryMode == EntryModeOption.MarketNextBar)
        {
            entryPrice = signal.Side == SignalSide.Buy ? Symbol.Ask : Symbol.Bid;
            reason = "";
            return true;
        }

        if (_settings.EntryPriceSource == EntryPriceSourceOption.CsvEntryPrice)
        {
            if (!signal.EntryPrice.HasValue)
            {
                entryPrice = 0;
                reason = "EntryPriceSource=CsvEntryPrice but signal has no entry_price";
                return false;
            }

            entryPrice = signal.EntryPrice.Value;
            reason = "";
            return true;
        }

        if (_settings.EntryMode == EntryModeOption.StopBreakout)
        {
            entryPrice = signal.Side == SignalSide.Buy ? signalHigh + entryOffset : signalLow - entryOffset;
            reason = "";
            return true;
        }

        var referencePrice = _settings.EntryPriceSource == EntryPriceSourceOption.SignalClose
            ? signalClose
            : signal.Side == SignalSide.Buy ? signalHigh : signalLow;
        entryPrice = signal.Side == SignalSide.Buy ? referencePrice - entryOffset : referencePrice + entryOffset;
        reason = "";
        return true;
    }

    private double GetEntryOffset(double baseRange)
    {
        return _settings.EntryOffsetMode switch
        {
            EntryOffsetModeOption.None => 0,
            EntryOffsetModeOption.PercentBaseRange => baseRange * EntryOffsetValue / 100.0,
            EntryOffsetModeOption.Pips => EntryOffsetValue * Symbol.PipSize,
            EntryOffsetModeOption.Price => EntryOffsetValue,
            _ => 0
        };
    }

    private bool TryCalculateStopLoss(SignalInfo signal, TradeType tradeType, double entryPrice, double signalHigh, double signalLow, double baseRange, out double stopLossPrice, out string reason)
    {
        stopLossPrice = 0;

        if (_settings.StopLossMode == StopLossModeOption.CsvStopLossPrice)
        {
            if (!signal.StopLossPrice.HasValue)
            {
                reason = "StopLossMode=CsvStopLossPrice but signal has no sl_price";
                return false;
            }

            stopLossPrice = signal.StopLossPrice.Value;
            return IsStopLossOnCorrectSide(tradeType, entryPrice, stopLossPrice, out reason);
        }

        if (_settings.StopLossMode == StopLossModeOption.CandleExtreme)
        {
            var buffer = StopLossBufferPips * Symbol.PipSize;
            stopLossPrice = tradeType == TradeType.Buy ? signalLow - buffer : signalHigh + buffer;
            return IsStopLossOnCorrectSide(tradeType, entryPrice, stopLossPrice, out reason);
        }

        var stopLossDistance = _settings.StopLossMode switch
        {
            StopLossModeOption.BaseRangeFib => GetKslMultiplier() * baseRange,
            StopLossModeOption.AtrMultiple => StopLossValue * signal.Atr,
            StopLossModeOption.FixedPips => StopLossValue * Symbol.PipSize,
            _ => 0
        };

        if (stopLossDistance <= 0)
        {
            reason = "SL distance is not positive";
            return false;
        }

        stopLossPrice = tradeType == TradeType.Buy ? entryPrice - stopLossDistance : entryPrice + stopLossDistance;
        reason = "";
        return true;
    }

    private static bool IsStopLossOnCorrectSide(TradeType tradeType, double entryPrice, double stopLossPrice, out string reason)
    {
        if (tradeType == TradeType.Buy && stopLossPrice >= entryPrice)
        {
            reason = "buy SL must be below entry";
            return false;
        }

        if (tradeType == TradeType.Sell && stopLossPrice <= entryPrice)
        {
            reason = "sell SL must be above entry";
            return false;
        }

        reason = "";
        return true;
    }

    private bool TryCalculateTakeProfits(SignalInfo signal, TradeType tradeType, double entryPrice, double stopLossPrice, double baseRange, out double[] takeProfitPrices, out string reason)
    {
        takeProfitPrices = Array.Empty<double>();
        var direction = tradeType == TradeType.Buy ? 1.0 : -1.0;
        var stopLossDistance = Math.Abs(entryPrice - stopLossPrice);

        switch (_settings.TakeProfitMode)
        {
            case TakeProfitModeOption.RiskReward:
                takeProfitPrices = new[] { entryPrice + direction * stopLossDistance * RiskReward };
                break;
            case TakeProfitModeOption.FibProfile:
                takeProfitPrices = GetTpMultipliers().Select(multiplier => entryPrice + direction * multiplier * baseRange).ToArray();
                break;
            case TakeProfitModeOption.FixedPips:
                takeProfitPrices = new[] { entryPrice + direction * FixedTakeProfitPips * Symbol.PipSize };
                break;
            case TakeProfitModeOption.CsvTakeProfitPrice:
                if (!signal.TakeProfitPrice.HasValue)
                {
                    reason = "TakeProfitMode=CsvTakeProfitPrice but signal has no tp_price";
                    return false;
                }
                takeProfitPrices = new[] { signal.TakeProfitPrice.Value };
                break;
        }

        if (takeProfitPrices.Length == 0)
        {
            reason = "no take profit prices calculated";
            return false;
        }

        foreach (var takeProfitPrice in takeProfitPrices)
        {
            if (tradeType == TradeType.Buy && takeProfitPrice <= entryPrice)
            {
                reason = "buy TP must be above entry";
                return false;
            }
            if (tradeType == TradeType.Sell && takeProfitPrice >= entryPrice)
            {
                reason = "sell TP must be below entry";
                return false;
            }
        }

        reason = "";
        return true;
    }

    private bool TryCalculateVolume(double stopLossPips, int legCount, out double volumePerLeg, out string reason)
    {
        volumePerLeg = VolumeMode == VolumeModeOption.FixedLots
            ? Symbol.NormalizeVolumeInUnits(Symbol.QuantityToVolumeInUnits(FixedVolumeLots), RoundingMode.Down)
            : Symbol.NormalizeVolumeInUnits(Symbol.VolumeForFixedRisk(Account.Balance * (RiskPercent / legCount) / 100.0, stopLossPips, RoundingMode.Down), RoundingMode.Down);

        if (volumePerLeg < Symbol.VolumeInUnitsMin)
        {
            _skipVolumeCount++;
            reason = string.Format(CultureInfo.InvariantCulture, "volume {0} below minimum {1}", volumePerLeg, Symbol.VolumeInUnitsMin);
            return false;
        }

        reason = "";
        return true;
    }

    private bool ValidateTradePlan(TradePlan plan, out string reason)
    {
        if (plan.EntryPrice <= 0 || plan.StopLossPrice <= 0 || plan.TakeProfitPrices.Any(tp => tp <= 0))
        {
            reason = "entry/SL/TP price must be positive";
            _skipInvalidPlanCount++;
            return false;
        }

        if (plan.FirstRiskReward < MinRiskReward)
        {
            reason = string.Format(CultureInfo.InvariantCulture, "first RR {0:F2} < MinRiskReward {1:F2}", plan.FirstRiskReward, MinRiskReward);
            _skipRiskRewardCount++;
            return false;
        }

        var spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
        var spreadToStopLossPercent = spreadPips / plan.StopLossPips * 100.0;
        if (MaxSpreadToStopLossPercent > 0 && spreadToStopLossPercent > MaxSpreadToStopLossPercent)
        {
            reason = string.Format(CultureInfo.InvariantCulture, "spread {0:F1}p is {1:F2}% of SL, limit {2:F2}%", spreadPips, spreadToStopLossPercent, MaxSpreadToStopLossPercent);
            _skipSpreadCount++;
            return false;
        }

        if (plan.StopLossPips < Symbol.MinStopLossDistance || plan.TakeProfitPips[0] < Symbol.MinTakeProfitDistance)
        {
            reason = string.Format(CultureInfo.InvariantCulture, "broker distance failed. SL {0:F1}p / min {1:F1}p | TP1 {2:F1}p / min {3:F1}p",
                plan.StopLossPips, Symbol.MinStopLossDistance, plan.TakeProfitPips[0], Symbol.MinTakeProfitDistance);
            _skipBrokerDistanceCount++;
            return false;
        }

        reason = "";
        return true;
    }

    private void PlaceTradePlan(TradePlan plan)
    {
        if (LogLevel == ExecutorLogLevel.Verbose)
            LogTradePlan(plan);

        for (var i = 0; i < plan.TakeProfitPrices.Length; i++)
        {
            var legNumber = i + 1;
            var label = GetLegLabel(plan.Signal.SourceLine, legNumber);
            var takeProfitPips = plan.TakeProfitPips[i];
            var success = plan.EntryMode switch
            {
                EntryModeOption.MarketNextBar => PlaceMarketLeg(plan, legNumber, label, takeProfitPips),
                EntryModeOption.StopBreakout => PlaceStopLeg(plan, legNumber, label, takeProfitPips),
                EntryModeOption.LimitPullback => PlaceLimitLeg(plan, legNumber, label, takeProfitPips),
                _ => false
            };

            if (success)
                _ordersPlaced++;
            else
                _ordersFailed++;
        }
    }

    private bool PlaceMarketLeg(TradePlan plan, int legNumber, string label, double takeProfitPips)
    {
        var result = ExecuteMarketOrder(plan.TradeType, SymbolName, plan.VolumePerLeg, label, plan.StopLossPips, takeProfitPips);
        if (!result.IsSuccessful)
        {
            Print("[{0}] ERROR opening {1} market leg {2}/{3} for line {4}: {5}",
                BotLabel, plan.Signal.Side, legNumber, plan.TakeProfitPrices.Length, plan.Signal.SourceLine, result.Error);
            return false;
        }

        var position = result.Position;
        var exactStopLoss = GetPriceFromPips(position.EntryPrice, plan.TradeType, plan.StopLossPips, isTakeProfit: false);
        var exactTakeProfit = GetPriceFromPips(position.EntryPrice, plan.TradeType, takeProfitPips, isTakeProfit: true);
        var modifyResult = ModifyPosition(position, exactStopLoss, exactTakeProfit, ProtectionType.Absolute);
        if (!modifyResult.IsSuccessful)
            Print("[{0}] WARNING line {1} leg {2}: exact market SL/TP modify failed ({3}). Relative protection retained.",
                BotLabel, plan.Signal.SourceLine, legNumber, modifyResult.Error);

        Print("[{0}] {1} MARKET leg {2}/{3} opened | Bar: {4:yyyy-MM-dd HH:mm} -> {5:yyyy-MM-dd HH:mm} | SignalTime: {6:yyyy-MM-dd HH:mm} | Entry: {7} | SL: {8:F1}p | TP: {9:F1}p | RR: {10:F2} | Base: {11:F4} | ATR: {12:F4} | Range: {13:F4} | Lots: {14:F2}",
            BotLabel, plan.Signal.Side, legNumber, plan.TakeProfitPrices.Length, plan.MatchedBarOpenTime, plan.NextBarOpenTime,
            plan.Signal.Time, position.EntryPrice, plan.StopLossPips, takeProfitPips, takeProfitPips / plan.StopLossPips,
            plan.BaseRange, plan.Atr, plan.SignalRange, Symbol.VolumeInUnitsToQuantity(plan.VolumePerLeg));
        return true;
    }

    private bool PlaceStopLeg(TradePlan plan, int legNumber, string label, double takeProfitPips)
    {
        var result = PlaceStopOrder(plan.TradeType, SymbolName, plan.VolumePerLeg, plan.EntryPrice, label,
            plan.StopLossPips, takeProfitPips, ProtectionType.Relative, plan.Expiration);
        return LogPendingResult(result, plan, legNumber, "STOP", takeProfitPips);
    }

    private bool PlaceLimitLeg(TradePlan plan, int legNumber, string label, double takeProfitPips)
    {
        var result = PlaceLimitOrder(plan.TradeType, SymbolName, plan.VolumePerLeg, plan.EntryPrice, label,
            plan.StopLossPips, takeProfitPips, ProtectionType.Relative, plan.Expiration);
        return LogPendingResult(result, plan, legNumber, "LIMIT", takeProfitPips);
    }

    private bool LogPendingResult(TradeResult result, TradePlan plan, int legNumber, string orderMode, double takeProfitPips)
    {
        if (!result.IsSuccessful)
        {
            Print("[{0}] ERROR placing {1} {2} leg {3}/{4} for line {5} at {6}: {7}",
                BotLabel, plan.Signal.Side, orderMode, legNumber, plan.TakeProfitPrices.Length, plan.Signal.SourceLine, plan.EntryPrice, result.Error);
            return false;
        }

        Print("[{0}] {1} {2} leg {3}/{4} placed | Bar: {5:yyyy-MM-dd HH:mm} -> {6:yyyy-MM-dd HH:mm} | SignalTime: {7:yyyy-MM-dd HH:mm} | Target: {8} | Expiration: {9:yyyy-MM-dd HH:mm} | Offset: {10:F4} | SL: {11:F1}p | TP: {12:F1}p | RR: {13:F2} | Base: {14:F4} | ATR: {15:F4} | Range: {16:F4} | Lots: {17:F2}",
            BotLabel, plan.Signal.Side, orderMode, legNumber, plan.TakeProfitPrices.Length, plan.MatchedBarOpenTime,
            plan.NextBarOpenTime, plan.Signal.Time, plan.EntryPrice, plan.Expiration, plan.EntryOffset,
            plan.StopLossPips, takeProfitPips, takeProfitPips / plan.StopLossPips, plan.BaseRange, plan.Atr,
            plan.SignalRange, Symbol.VolumeInUnitsToQuantity(plan.VolumePerLeg));
        return true;
    }

    private void LogTradePlan(TradePlan plan)
    {
        Print("[{0}] PLAN | Line: {1} | SignalId: {2} | Side: {3} | Mode: {4} | Bar: {5:yyyy-MM-dd HH:mm} | Entry: {6} | SL: {7} ({8:F1}p) | TP pips: [{9}] | Base: {10:F4} | ATR: {11:F4} | Range: {12:F4} | SignalsInBar: {13}",
            BotLabel, plan.Signal.SourceLine, string.IsNullOrWhiteSpace(plan.Signal.SignalId) ? "-" : plan.Signal.SignalId,
            plan.Signal.Side, plan.EntryMode, plan.MatchedBarOpenTime, plan.EntryPrice, plan.StopLossPrice,
            plan.StopLossPips, string.Join(", ", plan.TakeProfitPips.Select(pips => pips.ToString("F1", CultureInfo.InvariantCulture))),
            plan.BaseRange, plan.Atr, plan.SignalRange, plan.SignalsInBar);
    }

    private double GetPriceFromPips(double entryPrice, TradeType tradeType, double pips, bool isTakeProfit)
    {
        var direction = tradeType == TradeType.Buy ? 1.0 : -1.0;
        if (!isTakeProfit)
            direction *= -1.0;
        return Math.Round(entryPrice + direction * pips * Symbol.PipSize, Symbol.Digits);
    }

    private bool ApplyExposurePolicy(SignalSide side)
    {
        if (ExposurePolicy == ExposurePolicyOption.EverySignal)
            return true;

        var positions = GetMyPositions();
        var pendingOrders = GetMyPendingOrders();

        if (ExposurePolicy == ExposurePolicyOption.SkipIfAnyOpenPosition)
        {
            if (positions.Length == 0 && pendingOrders.Length == 0)
                return true;

            Print("[{0}] EXPOSURE SKIP | {1} signal skipped: existing bot exposure on {2}. Positions: {3} | Pending: {4}",
                BotLabel, side, SymbolName, positions.Length, pendingOrders.Length);
            return false;
        }

        var tradeType = side == SignalSide.Buy ? TradeType.Buy : TradeType.Sell;
        var oppositeType = tradeType == TradeType.Buy ? TradeType.Sell : TradeType.Buy;

        foreach (var order in pendingOrders.Where(order => order.TradeType == oppositeType))
        {
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
            {
                Print("[{0}] ERROR cancelling opposite pending order {1}: {2}", BotLabel, order.Id, result.Error);
                return false;
            }
        }

        foreach (var position in positions.Where(position => position.TradeType == oppositeType))
        {
            var result = ClosePosition(position);
            if (!result.IsSuccessful)
            {
                Print("[{0}] ERROR closing opposite position {1}: {2}", BotLabel, position.Id, result.Error);
                return false;
            }
        }

        return true;
    }

    private Position[] GetMyPositions()
    {
        return Positions.Where(position => IsMyBotLabel(position.Label) && position.SymbolName == SymbolName).ToArray();
    }

    private PendingOrder[] GetMyPendingOrders()
    {
        return PendingOrders.Where(order => IsMyBotLabel(order.Label) && order.SymbolName == SymbolName).ToArray();
    }

    private bool IsMyBotLabel(string label)
    {
        return !string.IsNullOrWhiteSpace(label)
            && (label == BotLabel || label.StartsWith($"{BotLabel}_C", StringComparison.Ordinal));
    }

    private string GetLegLabel(int sourceLine, int legNumber) => $"{BotLabel}_C{sourceLine}_L{legNumber}";

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

    private void ApplyStopLossLadder(Position closedPosition)
    {
        if (!EnableStopLossLadder || _settings.TakeProfitMode != TakeProfitModeOption.FibProfile)
            return;

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

        foreach (var position in remainingPositions)
        {
            var legNumber = GetLegNumber(position.Label);
            if (!TryGetLadderStopLossPrice(position, legNumber, reachedLeg, tpMultipliers, out var newStopLossPrice))
                continue;

            newStopLossPrice = Math.Round(newStopLossPrice, Symbol.Digits);
            if (!ShouldImproveStopLoss(position, newStopLossPrice))
                continue;

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

    private List<SignalInfo> GetSignalsForClosedBar(DateTime barTime, DateTime nextBarTime)
    {
        var result = new List<SignalInfo>();
        var startIndex = LowerBoundSignalIndex(barTime);
        for (var i = startIndex; i < _signalsByTime.Length; i++)
        {
            if (_signalsByTime[i].Time >= nextBarTime)
                break;
            result.Add(_signalsByTime[i]);
        }
        return result;
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

    private string GetDuplicateKey(DateTime matchedBarOpenTime, SignalSide side)
    {
        // Intentionally uses matched cTrader bar open time, not CSV signal timestamp.
        return string.Format(CultureInfo.InvariantCulture, "{0}|{1:yyyyMMddHHmm}|{2}", SymbolName, matchedBarOpenTime, side);
    }

    private bool IsCurrentSymbol(string csvSymbol)
    {
        return csvSymbol.Trim().Equals(SymbolName, StringComparison.OrdinalIgnoreCase)
            || csvSymbol.Trim().Equals(Symbol.Name, StringComparison.OrdinalIgnoreCase);
    }

    private static bool TryParseSide(string text, out SignalSide side)
    {
        var normalized = text.Trim().ToUpperInvariant();
        if (normalized is "1" or "BUY" or "LONG")
        {
            side = SignalSide.Buy;
            return true;
        }
        if (normalized is "-1" or "SELL" or "SHORT")
        {
            side = SignalSide.Sell;
            return true;
        }
        side = default;
        return false;
    }

    private static bool TryParseTime(string text, out DateTime time)
    {
        return DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out time)
            || DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out time)
            || DateTime.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.None, out time);
    }

    private static bool TryParseOptionalPrice(string[] parts, int index, out double? price, out string reason)
    {
        price = null;
        reason = "";
        if (index < 0)
            return true;

        var text = parts[index].Trim();
        if (string.IsNullOrWhiteSpace(text))
            return true;

        if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) || value <= 0)
        {
            reason = "invalid optional price column";
            return false;
        }
        price = value;
        return true;
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

    private static string[] SplitCsvLine(string line) => line.Split(',').Select(part => part.Trim()).ToArray();

    private static DateTime TrimToMinute(DateTime value) => new(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0, value.Kind);

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

    private static string FormatLevels(int[] values) => string.Join(",", values);

    private static bool IsTakeProfitClose(PositionClosedEventArgs args) => args.Reason.ToString().IndexOf("TakeProfit", StringComparison.OrdinalIgnoreCase) >= 0;

    private void OnPositionOpened(PositionOpenedEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] OPENED {1} | Label: {2} | Entry: {3} | SL: {4} | TP: {5} | Lots: {6:F2}",
            BotLabel, position.TradeType, position.Label, position.EntryPrice, position.StopLoss, position.TakeProfit,
            Symbol.VolumeInUnitsToQuantity(position.VolumeInUnits));
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var position = args.Position;
        if (!IsMyBotLabel(position.Label) || position.SymbolName != SymbolName)
            return;

        Print("[{0}] CLOSED {1} | Label: {2} | Cluster: {3} | Leg: {4} | Net PnL: {5:F2} | Pips: {6:F1} | Reason: {7}",
            BotLabel, position.TradeType, position.Label, GetClusterId(position.Label), GetLegNumber(position.Label),
            position.NetProfit, position.Pips, args.Reason);

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
        Print("[{0}] FILLED {1} PENDING | Label: {2} | Entry: {3} | SL: {4} | TP: {5}",
            BotLabel, position.TradeType, position.Label, position.EntryPrice, position.StopLoss, position.TakeProfit);
    }

    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        var order = args.PendingOrder;
        if (!IsMyBotLabel(order.Label) || order.SymbolName != SymbolName)
            return;

        _pendingCancelledCount++;
        Print("[{0}] CANCELLED {1} PENDING | Order: {2} | Label: {3}",
            BotLabel, order.TradeType, order.Id, order.Label);
    }

    protected override double GetFitness(GetFitnessArgs args)
    {
        if (args.TotalTrades < FitnessMinTrades)
            return double.MinValue;
        if (args.MaxEquityDrawdownPercentages > FitnessMaxEquityDrawdownPercent)
            return double.MinValue;
        if (args.NetProfit <= 0)
            return double.MinValue;
        if (args.ProfitFactor <= 1.0)
            return double.MinValue;
        return double.IsFinite(args.ProfitFactor) ? args.ProfitFactor : 10.0;
    }

    public enum ExecutionPresetOption { SimpleMarketRR, BreakoutFib, PullbackRR, Custom }
    public enum EntryModeOption { MarketNextBar, StopBreakout, LimitPullback }
    public enum EntryPriceSourceOption { SignalHighLow, SignalClose, CsvEntryPrice }
    public enum EntryOffsetModeOption { None, PercentBaseRange, Pips, Price }
    public enum BaseRangeModeOption { MaxAtrAndCandleRange, AtrOnly, CandleRangeOnly, FixedPips }
    public enum StopLossModeOption { BaseRangeFib, AtrMultiple, CandleExtreme, CsvStopLossPrice, FixedPips }
    public enum TakeProfitModeOption { RiskReward, FibProfile, CsvTakeProfitPrice, FixedPips }
    public enum VolumeModeOption { FixedRiskPercent, FixedLots }
    public enum ExposurePolicyOption { EverySignal, SkipIfAnyOpenPosition, CloseOppositeThenEnter }
    public enum ExecutorLogLevel { Basic, Verbose }
    private enum SignalSide { Buy, Sell }

    private struct EffectiveSettings
    {
        public EntryModeOption EntryMode { get; set; }
        public EntryPriceSourceOption EntryPriceSource { get; set; }
        public EntryOffsetModeOption EntryOffsetMode { get; set; }
        public BaseRangeModeOption BaseRangeMode { get; set; }
        public StopLossModeOption StopLossMode { get; set; }
        public TakeProfitModeOption TakeProfitMode { get; set; }
    }

    private struct CsvSchema
    {
        public int TimeIndex { get; init; }
        public int SymbolIndex { get; init; }
        public int SideIndex { get; init; }
        public int AtrIndex { get; init; }
        public int SignalIdIndex { get; init; }
        public int EntryPriceIndex { get; init; }
        public int StopLossPriceIndex { get; init; }
        public int TakeProfitPriceIndex { get; init; }
    }

    private readonly struct SignalInfo
    {
        public SignalInfo(DateTime time, SignalSide side, double atr, int sourceLine, string signalId, double? entryPrice, double? stopLossPrice, double? takeProfitPrice)
        {
            Time = time;
            Side = side;
            Atr = atr;
            SourceLine = sourceLine;
            SignalId = signalId;
            EntryPrice = entryPrice;
            StopLossPrice = stopLossPrice;
            TakeProfitPrice = takeProfitPrice;
        }

        public DateTime Time { get; }
        public SignalSide Side { get; }
        public double Atr { get; }
        public int SourceLine { get; }
        public string SignalId { get; }
        public double? EntryPrice { get; }
        public double? StopLossPrice { get; }
        public double? TakeProfitPrice { get; }
    }

    private readonly struct TradePlan
    {
        public TradePlan(SignalInfo signal, TradeType tradeType, DateTime matchedBarOpenTime, DateTime nextBarOpenTime, int signalsInBar, EntryModeOption entryMode, double entryPrice, double entryOffset, double stopLossPrice, double[] takeProfitPrices, double stopLossPips, double[] takeProfitPips, double firstRiskReward, double volumePerLeg, DateTime? expiration, double atr, double signalRange, double baseRange)
        {
            Signal = signal;
            TradeType = tradeType;
            MatchedBarOpenTime = matchedBarOpenTime;
            NextBarOpenTime = nextBarOpenTime;
            SignalsInBar = signalsInBar;
            EntryMode = entryMode;
            EntryPrice = entryPrice;
            EntryOffset = entryOffset;
            StopLossPrice = stopLossPrice;
            TakeProfitPrices = takeProfitPrices;
            StopLossPips = stopLossPips;
            TakeProfitPips = takeProfitPips;
            FirstRiskReward = firstRiskReward;
            VolumePerLeg = volumePerLeg;
            Expiration = expiration;
            Atr = atr;
            SignalRange = signalRange;
            BaseRange = baseRange;
        }

        public SignalInfo Signal { get; }
        public TradeType TradeType { get; }
        public DateTime MatchedBarOpenTime { get; }
        public DateTime NextBarOpenTime { get; }
        public int SignalsInBar { get; }
        public EntryModeOption EntryMode { get; }
        public double EntryPrice { get; }
        public double EntryOffset { get; }
        public double StopLossPrice { get; }
        public double[] TakeProfitPrices { get; }
        public double StopLossPips { get; }
        public double[] TakeProfitPips { get; }
        public double FirstRiskReward { get; }
        public double VolumePerLeg { get; }
        public DateTime? Expiration { get; }
        public double Atr { get; }
        public double SignalRange { get; }
        public double BaseRange { get; }
    }
}
