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
 *   BUY  -> Buy Stop  | Entry = High + X | SL = Low - X  | TP = Entry + KTP * ATR
 *   SELL -> Sell Stop | Entry = Low - X  | SL = High + X | TP = Entry - KTP * ATR
 */

[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_Combo : Robot
{
    [Parameter("Signal CSV Path", Group = "File",
        DefaultValue = @"D:\Auto Trading\SEN05\raw_signals\combo\combo_US30_H1_signals.csv")]
    public string CsvPath { get; set; }

    [Parameter("CSV Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -12, MaxValue = 14, Step = 1)]
    public int CsvTimeOffsetHours { get; set; }

    [Parameter("Bot Label", Group = "Identity", DefaultValue = "SEN_Combo")]
    public string BotLabel { get; set; }

    [Parameter("X Offset", Group = "Execution",
        DefaultValue = 10.0, MinValue = 1, MaxValue = 50, Step = 0.5)]
    public double XOffset { get; set; }

    [Parameter("KTP Fibonacci Level (1-5)", Group = "Execution",
        DefaultValue = 3, MinValue = 1, MaxValue = 5, Step = 1)]
    public int KtpFibLevel { get; set; }

    [Parameter("Cancel Pending On New Signal", Group = "Execution", DefaultValue = true)]
    public bool CancelPendingOnNewSignal { get; set; }

    [Parameter("Risk % per Trade", Group = "Risk",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 3.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    [Parameter("Max Open Orders", Group = "Risk",
        DefaultValue = 1, MinValue = 1, MaxValue = 20, Step = 1)]
    public int MaxOpenOrders { get; set; }

    private readonly Dictionary<DateTime, SignalInfo> _signals = new();

    protected override void OnStart()
    {
        PendingOrders.Filled += OnPendingOrderFilled;
        PendingOrders.Cancelled += OnPendingOrderCancelled;
        Positions.Closed += OnPositionClosed;

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
    }

    protected override void OnBarClosed()
    {
        if (_signals.Count == 0)
            return;

        var barTime = TrimToMinute(Bars.OpenTimes.Last(1));
        if (!_signals.TryGetValue(barTime, out var signal))
            return;

        if (CancelPendingOnNewSignal)
            CancelMyPendingOrders();

        var openOrderCount = GetMyOpenOrderCount();
        if (openOrderCount >= MaxOpenOrders)
        {
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: max open orders reached ({3}/{4}).",
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

        if (RiskPercent <= 0 || RiskPercent > 3.0)
        {
            Print("[{0}] ERROR: Risk percent must be > 0 and <= 3.", BotLabel);
            return false;
        }

        if (MaxOpenOrders < 1)
        {
            Print("[{0}] ERROR: Max open orders must be at least 1.", BotLabel);
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
        var ktpMultiplier = GetKtpMultiplier();
        var tradeType = isBuy ? TradeType.Buy : TradeType.Sell;
        var entryPrice = isBuy ? high + XOffset : low - XOffset;
        var stopLossPrice = isBuy ? low - XOffset : high + XOffset;
        var takeProfitPrice = isBuy
            ? entryPrice + ktpMultiplier * signal.Atr
            : entryPrice - ktpMultiplier * signal.Atr;

        var stopLossPips = Math.Abs(entryPrice - stopLossPrice) / Symbol.PipSize;
        var takeProfitPips = Math.Abs(takeProfitPrice - entryPrice) / Symbol.PipSize;
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

        Print("[{0}] {1} STOP placed | Bar: {2:yyyy-MM-dd HH:mm} | Entry: {3} | SL: {4} ({5:F1}p) | TP: {6} ({7:F1}p) | ATR: {8:F4} | KTP: {9:F3} | Risk: {10:F1}% | Lots: {11:F2}",
            BotLabel,
            signal.Side,
            barTime,
            entryPrice,
            stopLossPrice,
            stopLossPips,
            takeProfitPrice,
            takeProfitPips,
            signal.Atr,
            ktpMultiplier,
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

    private void CancelMyPendingOrders()
    {
        foreach (var order in GetMyPendingOrders())
        {
            var result = CancelPendingOrder(order);
            if (!result.IsSuccessful)
                Print("[{0}] ERROR cancelling pending order {1}: {2}", BotLabel, order.Id, result.Error);
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

    private int GetMyOpenOrderCount()
    {
        return GetMyPositions().Length + GetMyPendingOrders().Length;
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

        Print("[{0}] FILLED {1} | Entry: {2} | SL: {3} | TP: {4}",
            BotLabel, position.TradeType, position.EntryPrice, position.StopLoss, position.TakeProfit);
    }

    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        var order = args.PendingOrder;
        if (order.Label != BotLabel || order.SymbolName != SymbolName)
            return;

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
