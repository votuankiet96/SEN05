using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

[Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess, AddIndicators = false)]
public class Combo : Robot
{
    private const string Label = "ComboCsvPending";
    private const int PendingLifetimeBars = 3;

    // SL va TP dung 2 enum RIENG (khong con dung chung FibLevel) - xac nhan
    // nguoi dung 2026-09-03 sau khi phat hien 5/10 muc FibLevel cu (<1x ATR)
    // qua hep, la nguyen nhan chinh gay margin-cap "an" mat RiskPercent (xem
    // thao luan dai ve CapVolumeByMargin). SL can muc RONG (>=1x ATR, khong
    // nam lot trong bien do nhieu cua 1 nen), TP can day du ca muc hep (da co
    // bang chung lich su muc hep TP tung cho win rate cao) lan muc rat rong -
    // 2 nhu cau khac han nhau nen khong con gop chung 1 danh sach duoc nua.
    // Dieu chinh lai 2026-09-03 (lan 2): keo 2 muc cao nhat cua SL (4.236/
    // 4.618) xuong vung 0.5-1.0 (0.618/0.786) - van giu 8 muc; TP bo muc cao
    // nhat (Fib11090), con 9 muc.
    public enum SlFibLevel
    {
        Fib0618,
        Fib0786,
        Fib1000,
        Fib1272,
        Fib1618,
        Fib2000,
        Fib2618,
        Fib3618
    }

    public enum TpFibLevel
    {
        Fib0236,
        Fib0618,
        Fib1000,
        Fib1618,
        Fib2618,
        Fib3618,
        Fib4236,
        Fib4618,
        Fib6854
    }

    #region Parameters

    [Parameter("Signal File Path", Group = "Data Source", DefaultValue = "")]
    public string SignalFilePath { get; set; }

    [Parameter("KSL Level (SL = KSL x ATR)", Group = "Protection", DefaultValue = SlFibLevel.Fib1000)]
    public SlFibLevel KslLevel { get; set; }

    [Parameter("KTP Level (TP = KTP x ATR)", Group = "Protection", DefaultValue = TpFibLevel.Fib2618)]
    public TpFibLevel KtpLevel { get; set; }

    [Parameter("Risk % Balance", Group = "Risk Management", DefaultValue = 1.0, MinValue = 0.01)]
    public double RiskPercent { get; set; }

    // Per-trade margin budget; default 50% was selected on 2026-09-03.
    // With fixed effective leverage: notional ~= margin * effective leverage.
    // This is neither a portfolio cap nor a guarantee against gap losses.
    [Parameter("Max Margin % Equity Per Trade", Group = "Risk Management", DefaultValue = 50.0, MinValue = 0.1, MaxValue = 100.0)]
    public double MaxMarginPercent { get; set; }

    [Parameter("Enable Daily Loss Limit", Group = "Position Management", DefaultValue = false)]
    public bool EnableDailyLossLimit { get; set; }

    [Parameter("Max Daily Loss %", Group = "Position Management", DefaultValue = 5.0, MinValue = 0.1)]
    public double MaxDailyLossPercent { get; set; }

    [Parameter("Enable Max Drawdown", Group = "Position Management", DefaultValue = false)]
    public bool EnableMaxDrawdown { get; set; }

    [Parameter("Max Total Drawdown %", Group = "Position Management", DefaultValue = 10.0, MinValue = 0.1)]
    public double MaxTotalDrawdownPercent { get; set; }

    [Parameter("Enable Max Consecutive Losses", Group = "Position Management", DefaultValue = false)]
    public bool EnableMaxConsecutiveLosses { get; set; }

    [Parameter("Max Consecutive Losses", Group = "Position Management", DefaultValue = 5, MinValue = 1)]
    public int MaxConsecutiveLosses { get; set; }

    #endregion

    #region Indicators & State

    private sealed class SignalRow
    {
        public DateTime BarTime { get; set; }
        public DateTime AvailableTime { get; set; }
        public int Direction { get; set; }
        public double Atr { get; set; }
        public double EntryPrice { get; set; }
        public bool IsHandled { get; set; }
    }

    private sealed class PendingOrderLifetime
    {
        public int BarsRemaining { get; set; }
    }

    private readonly Dictionary<DateTime, SignalRow> _signals = new();
    private readonly List<SignalRow> _scheduledSignals = new();
    private readonly Dictionary<long, PendingOrderLifetime> _pendingOrderLifetimes = new();

    private DateTime _runStartedAt;
    private TimeSpan _signalBarDuration;
    private bool _schedulingReady;
    private int _nextScheduledSignalIndex;

    private double _initialBalance;
    private double _dailyReferenceBalance;
    private DateTime _currentDay = DateTime.MinValue;
    private bool _accountBreached;
    private bool _haltedForDay;
    private int _consecutiveLosses;

    private int _signalsProcessed;
    private int _signalsBeforeStart;
    private int _ordersPlaced;
    private int _orderFailures;
    private int _expiredOrders;
    private int _signalsSkippedByGuard;
    private int _signalsSkippedSameDirection;
    private int _reversalsExecuted;
    private int _marginCapped;
    private int _marginBlocked;

    #endregion

    #region Lifecycle

    protected override void OnStart()
    {
        _runStartedAt = Server.Time;
        _initialBalance = Account.Balance;
        Positions.Closed += OnPositionClosed;
        PendingOrders.Filled += OnPendingOrderFilled;
        LoadSignalFile();
        InitializeSignalSchedule();
    }

    protected override void OnTick()
    {
        ProcessScheduledSignals();
    }

    protected override void OnBarClosed()
    {
        ExpirePendingOrders();
        UpdatePositionManagement(Bars.OpenTimes.Last(0));
    }

    protected override void OnStop()
    {
        Print(
            "Combo: loaded={0}, processed={1}, before-start={2}, not-processed={3}, placed={4}, failed={5}, guard-skipped={6}, pending-expired={7}, same-direction-skipped={8}, reversed={9}, margin-capped={10}, margin-blocked={11}.",
            _signals.Count,
            _signalsProcessed,
            _signalsBeforeStart,
            _signals.Values.Count(signal => !signal.IsHandled),
            _ordersPlaced,
            _orderFailures,
            _signalsSkippedByGuard,
            _expiredOrders,
            _signalsSkippedSameDirection,
            _reversalsExecuted,
            _marginCapped,
            _marginBlocked);
    }

    #endregion

    #region Entry Logic

    private void HandleSignal(SignalRow signal)
    {
        if (signal.IsHandled)
            return;

        signal.IsHandled = true;
        _signalsProcessed++;

        if (_accountBreached || _haltedForDay)
        {
            _signalsSkippedByGuard++;
            return;
        }

        if (!ReconcileExistingExposure(signal))
            return;

        PlacePendingOrder(signal);
    }

    // Chi tinh signal quyet dinh KHI NAO/HUONG NAO (xem og_program/core_python/
    // strategies/combo.py - da bo state machine _alternating_signals ben do,
    // moi bar tu danh gia doc lap, khong con dam bao tu dong khong lap huong).
    // O day la noi cBot tu quyet dinh co vao lenh tiep hay khong dua tren
    // exposure THAT (Positions/PendingOrders cua chinh cTrader, khong can bien
    // trang thai rieng) - dung 1 nguyen tac: moi symbol toi da 1 huong exposure
    // (vi the dang mo HOAC lenh cho chua khop) duoi Label nay tai 1 thoi diem.
    //   - Tin hieu CUNG huong voi exposure dang co -> bo qua (tra ve false).
    //   - Tin hieu NGUOC huong -> dong vi the dang mo + huy lenh cho dang co,
    //     roi cho dat lenh moi (tra ve true).
    //   - Khong co exposure nao -> cho dat lenh moi binh thuong (tra ve true).
    private bool ReconcileExistingExposure(SignalRow signal)
    {
        int newDirection = signal.Direction;
        List<Position> positions = Positions.Where(position => position.Label == Label && position.SymbolName == SymbolName).ToList();
        List<PendingOrder> pendingOrders = PendingOrders.Where(order => order.Label == Label && order.SymbolName == SymbolName).ToList();

        bool hasSameDirection = positions.Any(position => DirectionOf(position.TradeType) == newDirection)
                              || pendingOrders.Any(order => DirectionOf(order.TradeType) == newDirection);
        if (hasSameDirection)
        {
            _signalsSkippedSameDirection++;
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, {1} signal skipped: already have same-direction exposure open.",
                signal.BarTime,
                signal.Direction == 1 ? "Buy" : "Sell");
            return false;
        }

        List<Position> opposingPositions = positions.Where(position => DirectionOf(position.TradeType) != newDirection).ToList();
        List<PendingOrder> opposingOrders = pendingOrders.Where(order => DirectionOf(order.TradeType) != newDirection).ToList();
        if (opposingPositions.Count > 0 || opposingOrders.Count > 0)
            _reversalsExecuted++;

        foreach (Position position in opposingPositions)
        {
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, reversal - closing existing {1} position {2} before opening opposite direction.",
                signal.BarTime,
                position.TradeType,
                position.Id);
            TradeResult result = position.Close();
            if (!result.IsSuccessful)
            {
                _signalsSkippedByGuard++;
                Print("Combo: signal skipped: could not close position {0}: {1}.", position.Id, result.Error);
                return false;
            }
        }

        foreach (PendingOrder order in opposingOrders)
        {
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, reversal - cancelling existing {1} pending order {2} before opening opposite direction.",
                signal.BarTime,
                order.TradeType,
                order.Id);
            TradeResult result = order.Cancel();
            if (!result.IsSuccessful)
            {
                _signalsSkippedByGuard++;
                Print("Combo: signal skipped: could not cancel pending order {0}: {1}.", order.Id, result.Error);
                return false;
            }
            _pendingOrderLifetimes.Remove(order.Id);
        }

        return true;
    }

    private static int DirectionOf(TradeType tradeType) => tradeType == TradeType.Buy ? 1 : -1;

    private void PlacePendingOrder(SignalRow signal)
    {
        double balanceAtEntry = Account.Balance;
        GetProtectionPrices(signal, out double stopLossPrice, out double takeProfitPrice);
        double stopLossPips = Math.Abs(signal.EntryPrice - stopLossPrice) / Symbol.PipSize;
        bool isBuy = signal.Direction == 1;
        double volume = CalculateVolume(stopLossPips, isBuy ? TradeType.Buy : TradeType.Sell, out double pipValue);
        if (volume <= 0)
        {
            _orderFailures++;
            return;
        }

        TradeResult result = PlaceStopOrder(
            isBuy ? TradeType.Buy : TradeType.Sell,
            SymbolName,
            volume,
            signal.EntryPrice,
            Label,
            stopLossPrice,
            takeProfitPrice,
            ProtectionType.Absolute);

        if (!result.IsSuccessful || result.PendingOrder == null)
        {
            _orderFailures++;
            Print(
                "Combo: bartime={0:yyyy-MM-dd HH:mm}, pending {1} at {2} was rejected: {3}.",
                signal.BarTime,
                isBuy ? "Buy" : "Sell",
                signal.EntryPrice,
                result.Error);
            return;
        }

        _pendingOrderLifetimes[result.PendingOrder.Id] = new PendingOrderLifetime { BarsRemaining = PendingLifetimeBars };
        _ordersPlaced++;
        Print(
            "Combo: bartime={0:yyyy-MM-dd HH:mm}, executed={1:yyyy-MM-dd HH:mm:ss}, pending {2} placed at {3}; SL={4}, TP={5}; volume={7:F2} units ({8:F4} lots); valid for the next {6} chart bar(s).",
            signal.BarTime,
            Server.Time,
            isBuy ? "Buy" : "Sell",
            signal.EntryPrice,
            stopLossPrice,
            takeProfitPrice,
            PendingLifetimeBars,
            volume,
            Symbol.VolumeInUnitsToQuantity(volume));

        // Chi tiet sizing de doi chieu THIET KE voi hanh vi dat lenh THAT qua
        // backtest (dong nay + dong "FX" + dong "CLOSED" luc dong lenh). pipValue
        // la gia tri CalculateVolume da thuc su dung (tra ra qua out). raw =
        // volume risk-based TRUOC normalize/margin-cap - de thay round-down DOWN
        // an bao nhieu.
        double takeProfitPips = Math.Abs(signal.EntryPrice - takeProfitPrice) / Symbol.PipSize;
        double riskBudget = balanceAtEntry * RiskPercent / 100.0;
        double riskBasedRawVolume = riskBudget / (stopLossPips * pipValue);
        Print(
            "Combo: RISK_DETAIL bartime={0:yyyy-MM-dd HH:mm} {1}: balance=${2:F2}, risk={3:F2}% => budget ${4:F2}; " +
            "ATR={5:F5}, KSL={6:F3}xATR => SL {7:F1} pips, KTP={8:F3}xATR => TP {9:F1} pips, R:R=1:{10:F2}; " +
            "pipValue={11:F8} USD/pip/unit; risk-based raw {12:F2} units => placed {13:F2} units ({14:F4} lots), step {15}; " +
            "est.margin=${16:F2}; expected loss if SL hit=${17:F2}, expected profit if TP hit=${18:F2}.",
            signal.BarTime,
            isBuy ? "Buy" : "Sell",
            balanceAtEntry,
            RiskPercent,
            riskBudget,
            signal.Atr,
            ToRatio(KslLevel),
            stopLossPips,
            ToRatio(KtpLevel),
            takeProfitPips,
            takeProfitPips / stopLossPips,
            pipValue,
            riskBasedRawVolume,
            volume,
            Symbol.VolumeInUnitsToQuantity(volume),
            Symbol.VolumeInUnitsStep,
            Symbol.GetEstimatedMargin(isBuy ? TradeType.Buy : TradeType.Sell, volume),
            volume * stopLossPips * pipValue,
            volume * takeProfitPips * pipValue);
        LogConversionRate(signal.BarTime, pipValue);
    }

    // cTrader KHONG ghi log gi khi lenh cho khop (chi co trong events.json) ->
    // giua "placed" va "CLOSED" khong biet lenh da thanh vi the hay chua, luc
    // nao, gia nao. Dong nay lap khoang trong do: thoi diem khop + gia khop +
    // truot so voi trigger + SL tinh lai TU GIA KHOP THAT (day la cho rui ro
    // thuc te phong len khi trigger bi bo qua trong phien mo cua - xem
    // reports/lotsize-sizing-work-summary-2026-09-08.md Van de 3).
    private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
    {
        PendingOrder order = args.PendingOrder;
        if (order.Label != Label || order.SymbolName != SymbolName)
            return;

        Position pos = args.Position;
        int direction = pos.TradeType == TradeType.Buy ? 1 : -1;
        double entrySlippagePips = direction * (pos.EntryPrice - order.TargetPrice) / Symbol.PipSize;
        double pipValue = PipValueNow();
        double slFromFillPips = pos.StopLoss.HasValue
            ? Math.Abs(pos.EntryPrice - pos.StopLoss.Value) / Symbol.PipSize
            : double.NaN;

        Print(
            "Combo: FILLED pending {0} => position {1} {2} {3:F2} units at {4}, executed={5:yyyy-MM-dd HH:mm:ss}; " +
            "trigger was {6}, entry slippage {7:F1} pips {8} than trigger; " +
            "SL {9} is now {10:F1} pips from fill => real risk if SL hit=${11:F2} (vs budget at placement).",
            order.Id,
            pos.Id,
            pos.TradeType,
            pos.VolumeInUnits,
            pos.EntryPrice,
            Server.Time,
            order.TargetPrice,
            Math.Abs(entrySlippagePips),
            entrySlippagePips >= 0 ? "worse" : "better",
            pos.StopLoss,
            slFromFillPips,
            pos.VolumeInUnits * slFromFillPips * pipValue);
    }

    #endregion

    #region Exit Logic

    private void GetProtectionPrices(SignalRow signal, out double stopLossPrice, out double takeProfitPrice)
    {
        double direction = signal.Direction;
        stopLossPrice = signal.EntryPrice - direction * ToRatio(KslLevel) * signal.Atr;
        takeProfitPrice = signal.EntryPrice + direction * ToRatio(KtpLevel) * signal.Atr;
    }

    #endregion

    #region Risk & Position Sizing

    // Gia tri tien tai khoan cua 1 pip cho 1 unit, tinh TAI THOI DIEM GOI.
    // KHONG dung Symbol.PipValue: tai lieu cTrader xac nhan no la anh chup ty gia
    // luc OnStart va khong cap nhat -> sizing symbol quote EUR/JPY/HKD se troi
    // theo FX trong run dai. Quote == tien tai khoan (US30/GOLD/BTCUSD): tra ve
    // Symbol.PipSize, khong co quy doi. Quote khac: Asset.Convert (backtest dung
    // ty gia lich su dung tai bar do; live dung ty gia hien tai) - cung co che
    // cTrader dung cho margin/P&L that. Tuong duong dung Symbol.PipValue nhung
    // khong bi dong bang (da doi chieu PipValue == PipSize x FX cho ca 6 nhom).
    private double PipValueNow()
    {
        if (Symbol.QuoteAsset.Name == Account.Asset.Name)
            return Symbol.PipSize;

        // Asset.Convert lam tron ket qua ve so chu so cua tien tai khoan (USD = 2
        // chu so) -> quy doi 1 pip JPY (~0.0064 USD) ra 0.00 -> moi volume = 0
        // (lan build dau: JP225 placed=0 / failed=33). Quy doi mot luong LON roi
        // chia lai de giu du chu so co nghia; HKD/EUR cung chinh xac hon nho buoc nay.
        const double ProbeUnits = 1_000_000.0;
        return Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize * ProbeUnits) / ProbeUnits;
    }

    private double CalculateVolume(double stopLossPips, TradeType tradeType, out double pipValue)
    {
        pipValue = PipValueNow();
        if (!double.IsFinite(stopLossPips) || stopLossPips <= 0
            || !double.IsFinite(pipValue) || pipValue <= 0)
            return 0;

        double riskAmount = Account.Balance * RiskPercent / 100.0;
        double requestedVolume = riskAmount / (stopLossPips * pipValue);
        if (!double.IsFinite(requestedVolume))
            return 0;
        if (requestedVolume < Symbol.VolumeInUnitsMin)
        {
            Print(
                "Combo: SIZE TOO SMALL - signal skipped. Risking {0}% of balance (${1:F2}) over a stop-loss of " +
                "{2:F1} pips only needs {3:F2} units, which is below the broker minimum of {4:F2}. No order sent.",
                RiskPercent,
                riskAmount,
                stopLossPips,
                requestedVolume,
                Symbol.VolumeInUnitsMin);
            return 0;
        }

        if (requestedVolume > Symbol.VolumeInUnitsMax)
        {
            Print(
                "Combo: SIZE ABOVE BROKER MAX - risking {0}% of balance over the current stop-loss would need " +
                "{1:F2} units, above the broker maximum of {2:F2}. Volume is capped to the broker maximum before " +
                "the margin check below runs.",
                RiskPercent,
                requestedVolume,
                Symbol.VolumeInUnitsMax);
        }

        double volume = Symbol.NormalizeVolumeInUnits(
            Math.Min(requestedVolume, Symbol.VolumeInUnitsMax),
            RoundingMode.Down);
        if (!double.IsFinite(volume) || volume < Symbol.VolumeInUnitsMin)
            return 0;

        return CapVolumeByMargin(volume, tradeType, stopLossPips, pipValue);
    }

    // Volume o tren (CalculateVolume) chi dam bao DUNG so tien lo NEU DINH
    // SL (% balance) - hoan toan doc lap voi margin can de GIU duoc vi the
    // (rui ro-neu-dinh-SL != tien BI KHOA de giu lenh mo, 2 khai niem khac
    // han nhau). Ham nay ap 2 tran DOC LAP, lay MIN:
    //   1) MaxMarginPercent x Equity - tran CHU DONG, LUON kiem tra, khong
    //      cho 1 lenh khoa qua X% Equity DU MARGIN DANG DU BAO NHIEU. Quyet
    //      dinh nguoi dung 2026-09-02 sau khi phat hien ban dau (chi co tran
    //      free-margin ben duoi) van cho phep 1 lenh "nuot" toi 98% Equity
    //      (vi du that: lenh dau tien tren tai khoan moi, free margin luc do
    //      = TOAN BO balance) - khoa gan het tien vua khien KHONG THE vao
    //      lenh moi khac (het cho trong), vua mat het "dem" chong margin-
    //      call/gap NEU gia di nguoc TRUOC KHI kip cham SL (that su co the
    //      "chay tai khoan" du risk% tren giay van dung, xem thao luan dai
    //      voi nguoi dung).
    //   2) Free margin thuc te - lop chan CUOI cung (Fix A ban dau, giu
    //      lai): can thiet rieng cho truong hop DANG co nhieu lenh/vi the
    //      khac mo cung luc an bot margin trong - luc do ngan sach margin co
    //      the con THAP HON ca MaxMarginPercent.
    // Truoc khi co ham nay, lenh loai nay VAN duoc "Placing Stop Order...
    // SUCCEEDED" luc DAT (dat pending khong giu margin ngay), roi bi broker
    // tu choi ngam (NOT_ENOUGH_MARGIN_BALANCE) luc GIA CHAM va co gang KHOP -
    // _ordersPlaced da tang nham, bot khong he biet lenh nay chua bao gio
    // thanh vi the that (da gap that, xem log audit 2026-09-02).
    private double CapVolumeByMargin(double volume, TradeType tradeType, double stopLossPips, double pipValue)
    {
        double estimatedMargin = Symbol.GetEstimatedMargin(tradeType, volume);
        if (!double.IsFinite(estimatedMargin))
            return 0;

        double maxMarginByPercent = Account.Equity * MaxMarginPercent / 100.0;
        double marginBudget = Math.Min(Account.FreeMargin, maxMarginByPercent);
        if (!double.IsFinite(marginBudget) || marginBudget <= 0)
            return 0;
        if (estimatedMargin <= marginBudget)
            return volume;

        // Margin xap xi tuyen tinh theo volume doi voi CFD chuan - giam volume
        // theo dung ti le ngan-sach-margin/margin-can, tru hao 2% (an toan)
        // vi gia/spread co the doi chut giua luc uoc tinh va luc lenh thuc
        // su gui di.
        const double MarginSafetyFactor = 0.98;
        double scaledVolume = volume * (marginBudget / estimatedMargin) * MarginSafetyFactor;
        double normalizedVolume = Symbol.NormalizeVolumeInUnits(scaledVolume, RoundingMode.Down);

        // "reason" cho nguoi doc log biet DUNG vi sao bi giam (2 tran doc
        // lap co the trung nhau, in ca 2 so de doi chieu thay vi chi noi
        // "margin khong du" chung chung.
        bool cappedByPercent = maxMarginByPercent <= Account.FreeMargin;
        string reason = cappedByPercent
            ? string.Format(
                CultureInfo.InvariantCulture,
                "MaxMarginPercent limit hit ({0:F1}% of equity = ${1:F2} budget, free margin was ${2:F2})",
                MaxMarginPercent,
                maxMarginByPercent,
                Account.FreeMargin)
            : string.Format(
                CultureInfo.InvariantCulture,
                "free margin ran low (${0:F2} left, MaxMarginPercent budget was ${1:F2})",
                Account.FreeMargin,
                maxMarginByPercent);

        if (!double.IsFinite(normalizedVolume) || normalizedVolume < Symbol.VolumeInUnitsMin)
        {
            _marginBlocked++;
            Print(
                "Combo: MARGIN GUARD - order skipped. Signal wanted {0:F2} units, which needs about ${1:F2} margin. " +
                "Limit hit: {2}. After shrinking the size to fit that limit, only {3:F2} units would be left, which " +
                "is below the broker minimum of {4:F2} - so no order was sent.",
                volume,
                estimatedMargin,
                reason,
                normalizedVolume,
                Symbol.VolumeInUnitsMin);
            return 0;
        }

        _marginCapped++;
        double actualRiskAmount = normalizedVolume * stopLossPips * pipValue;
        double actualRiskPercent = Account.Balance > 0 ? actualRiskAmount / Account.Balance * 100.0 : 0;
        Print(
            "Combo: MARGIN GUARD - order size reduced. Signal wanted {0:F2} units, which needs about ${1:F2} margin. " +
            "Limit hit: {2}. Size was cut from {0:F2} to {3:F2} units. Real risk on this trade is now about " +
            "${4:F2} ({5:F2}% of balance) instead of the requested {6:F2}%.",
            volume,
            estimatedMargin,
            reason,
            normalizedVolume,
            actualRiskAmount,
            actualRiskPercent,
            RiskPercent);

        return normalizedVolume;
    }

    #endregion

    #region Position Management

    // Research guards sampled at bars/signals; not a hard FTMO account limit.

    private void UpdatePositionManagement(DateTime barTime)
    {
        RolloverDayIfNeeded(barTime);

        if (EnableMaxDrawdown && !_accountBreached)
        {
            double threshold = _initialBalance * (1 - MaxTotalDrawdownPercent / 100.0);
            if (Account.Equity <= threshold)
            {
                _accountBreached = true;
                ForceCloseAll();
                Print("Combo: max drawdown reached; trading halted for this run.");
            }
        }

        if (EnableDailyLossLimit && !_haltedForDay && !_accountBreached)
        {
            double threshold = _dailyReferenceBalance * (1 - MaxDailyLossPercent / 100.0);
            if (Account.Equity <= threshold)
            {
                _haltedForDay = true;
                ForceCloseAll();
                Print("Combo: daily loss limit reached; trading halted until the next UTC day.");
            }
        }
    }

    private void RolloverDayIfNeeded(DateTime barTime)
    {
        DateTime today = barTime.Date;
        if (today <= _currentDay)
            return;

        _currentDay = today;
        _dailyReferenceBalance = Account.Balance;
        _haltedForDay = false;
        _consecutiveLosses = 0;
    }

    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        Position position = args.Position;
        if (position.Label != Label || position.SymbolName != SymbolName)
            return;

        LogClosedTrade(position, args.Reason);

        _consecutiveLosses = position.NetProfit < 0 ? _consecutiveLosses + 1 : 0;
        if (EnableMaxConsecutiveLosses && !_haltedForDay && !_accountBreached
            && _consecutiveLosses >= MaxConsecutiveLosses)
        {
            _haltedForDay = true;
            Print("Combo: max consecutive losses reached; trading halted until the next UTC day.");
        }
    }

    private void ForceCloseAll()
    {
        foreach (PendingOrder order in PendingOrders.Where(order => order.Label == Label && order.SymbolName == SymbolName).ToList())
            order.Cancel();

        foreach (Position position in Positions.Where(position => position.Label == Label && position.SymbolName == SymbolName).ToList())
            position.Close();

        _pendingOrderLifetimes.Clear();
    }

    #endregion

    #region Helpers

    // Ty gia quy doi quote-asset -> account-asset TAI TICK dat lenh (backtest:
    // ty gia lich su tai bar do). factor = so tien tai khoan cho 1 don vi quote
    // = dung he so sizing da dung (pipValue / PipSize). In ca nghich dao cho de
    // doc voi JPY/HKD. Quote == account (US30/GOLD/BTCUSD): khong quy doi.
    private void LogConversionRate(DateTime barTime, double pipValue)
    {
        string quote = Symbol.QuoteAsset.Name;
        string acct = Account.Asset.Name;
        if (quote == acct)
        {
            Print(
                "Combo: FX bartime={0:yyyy-MM-dd HH:mm}: quote asset {1} == account asset, no conversion (factor 1.0).",
                barTime,
                quote);
            return;
        }

        double factor = pipValue / Symbol.PipSize;
        Print(
            "Combo: FX bartime={0:yyyy-MM-dd HH:mm}: 1 {1} = {2:F8} {3} (<=> {4:F5} {1} per {3}); " +
            "via Asset.Convert at placement tick.",
            barTime,
            quote,
            factor,
            acct,
            factor > 0 ? 1.0 / factor : 0.0);
    }

    // Chi tiet lenh da dong de doi chieu voi so ky vong luc dat ("RISK_DETAIL"):
    // gia dong, pips, gross/commission/swap/net THAT, balance sau lenh. reason
    // phan biet SL / TP / dong tay (reversal hoac Position Management force-close).
    // Uu tien so lieu tu History (da chot); fallback ve Position neu History
    // chua kip cap nhat luc su kien ban ra.
    private void LogClosedTrade(Position position, PositionCloseReason reason)
    {
        HistoricalTrade trade = History.FirstOrDefault(item => item.PositionId == position.Id);
        Print(
            "Combo: CLOSED position {0} {1} reason={2}: entry={3}, close={4}, {5:F1} pips; " +
            "volume={6:F2} units ({7:F4} lots); gross=${8:F2}, commission=${9:F2}, swap=${10:F2}, " +
            "net=${11:F2}; balance now ${12:F2}.",
            position.Id,
            position.TradeType,
            reason,
            position.EntryPrice,
            trade?.ClosingPrice ?? 0.0,
            trade?.Pips ?? position.Pips,
            position.VolumeInUnits,
            Symbol.VolumeInUnitsToQuantity(position.VolumeInUnits),
            trade?.GrossProfit ?? position.GrossProfit,
            trade?.Commissions ?? position.Commissions,
            trade?.Swap ?? position.Swap,
            trade?.NetProfit ?? position.NetProfit,
            trade?.Balance ?? Account.Balance);
    }

    private void LoadSignalFile()
    {
        _signals.Clear();

        if (string.IsNullOrWhiteSpace(SignalFilePath) || !System.IO.File.Exists(SignalFilePath))
        {
            Print("Combo: signal file was not found at '{0}'.", SignalFilePath);
            return;
        }

        string[] lines = System.IO.File.ReadAllLines(SignalFilePath);
        if (lines.Length < 2)
            return;

        string[] header = lines[0].Split(',').Select(column => column.Trim().ToLowerInvariant()).ToArray();
        int barTimeIndex = Array.IndexOf(header, "bartime");
        int atrIndex = Array.IndexOf(header, "atr");
        int entryIndex = Array.IndexOf(header, "entry");
        int signalIndex = Array.IndexOf(header, "signal");

        if (barTimeIndex < 0 || atrIndex < 0 || entryIndex < 0 || signalIndex < 0)
        {
            Print("Combo: CSV requires bartime, atr, entry, and signal columns.");
            return;
        }

        for (int lineIndex = 1; lineIndex < lines.Length; lineIndex++)
        {
            string[] columns = lines[lineIndex].Split(',');
            if (columns.Length <= Math.Max(Math.Max(barTimeIndex, atrIndex), Math.Max(entryIndex, signalIndex)))
                continue;

            if (!DateTime.TryParse(
                    columns[barTimeIndex],
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out DateTime barTime)
                || !double.TryParse(columns[atrIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out double atr)
                || !double.TryParse(columns[entryIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out double entryPrice)
                || !int.TryParse(columns[signalIndex], NumberStyles.Integer, CultureInfo.InvariantCulture, out int direction)
                || (direction != 1 && direction != -1)
                || !double.IsFinite(atr) || atr <= 0
                || !double.IsFinite(entryPrice) || entryPrice <= 0)
                continue;

            _signals[barTime] = new SignalRow
            {
                BarTime = barTime,
                Direction = direction,
                Atr = atr,
                EntryPrice = entryPrice
            };
        }

        Print("Combo: loaded {0} valid signal row(s) from CSV.", _signals.Count);
    }

    private void InitializeSignalSchedule()
    {
        _scheduledSignals.Clear();
        _nextScheduledSignalIndex = 0;
        _schedulingReady = TryGetNominalBarDuration(TimeFrame, out _signalBarDuration);

        if (!_schedulingReady)
        {
            Print(
                "Combo: timeframe '{0}' has no fixed time duration; signal scheduling is unavailable, no orders will be placed.",
                TimeFrame.ShortName);
            return;
        }

        foreach (SignalRow signal in _signals.Values)
        {
            signal.AvailableTime = signal.BarTime.Add(_signalBarDuration);
            _scheduledSignals.Add(signal);
        }

        _scheduledSignals.Sort((left, right) => left.AvailableTime.CompareTo(right.AvailableTime));
        Print(
            "Combo: signal scheduling active with chart timeframe {0} ({1}); CSV timeframe must match the chart timeframe.",
            TimeFrame.ShortName,
            _signalBarDuration);
    }

    // Duy nhat 1 luong xu ly tin hieu (khong con phan biet exact-match FTMO
    // bar vs missing-bar fallback nua - nguoi dung quyet dinh 2026-09-01: bo
    // han co che nay, vi thuc te khi live trading, bot chi phan ung theo thoi
    // gian thuc tren CHINH tai khoan dat lenh (FTMO) - khong co khai niem "co
    // khop dung 1 nen cua broker khac hay khong". Tin hieu duoc xu ly ngay tai
    // tick kha dung dau tien sau AvailableTime (bartime + do dai nen danh
    // nghia), khong gioi han thoi gian cho - neu thi truong dong cua lau, cu
    // cho toi khi mo lai roi xu ly, giong het cach MA Cross.cs da lam tu truoc.
    private void ProcessScheduledSignals()
    {
        if (!_schedulingReady)
            return;

        bool positionManagementUpdated = false;
        while (_nextScheduledSignalIndex < _scheduledSignals.Count)
        {
            SignalRow signal = _scheduledSignals[_nextScheduledSignalIndex];
            if (signal.AvailableTime > Server.Time)
                return;

            _nextScheduledSignalIndex++;
            if (signal.IsHandled)
                continue;

            if (signal.AvailableTime < _runStartedAt)
            {
                signal.IsHandled = true;
                _signalsBeforeStart++;
                continue;
            }

            if (!positionManagementUpdated)
            {
                UpdatePositionManagement(Server.Time);
                positionManagementUpdated = true;
            }

            HandleSignal(signal);
        }
    }

    private void ExpirePendingOrders()
    {
        foreach (KeyValuePair<long, PendingOrderLifetime> item in _pendingOrderLifetimes.ToList())
        {
            PendingOrder order = PendingOrders.FirstOrDefault(candidate => candidate.Id == item.Key);
            if (order == null)
            {
                _pendingOrderLifetimes.Remove(item.Key);
                continue;
            }

            int barsRemaining = item.Value.BarsRemaining - 1;
            if (barsRemaining > 0)
            {
                item.Value.BarsRemaining = barsRemaining;
                continue;
            }

            TradeResult result = order.Cancel();
            if (!result.IsSuccessful)
            {
                Print("Combo: could not expire pending order {0}: {1}; will retry next bar.", order.Id, result.Error);
                continue;
            }
            _pendingOrderLifetimes.Remove(item.Key);
            _expiredOrders++;
            Print("Combo: pending order {0} cancelled after {1} chart bars.", order.Id, PendingLifetimeBars);
        }
    }

    private static bool TryGetNominalBarDuration(TimeFrame timeFrame, out TimeSpan duration)
    {
        duration = TimeSpan.Zero;
        string shortName = timeFrame.ShortName;
        if (string.IsNullOrWhiteSpace(shortName) || shortName.Length < 2
            || !int.TryParse(shortName.Substring(1), NumberStyles.None, CultureInfo.InvariantCulture, out int units)
            || units <= 0)
            return false;

        duration = shortName[0] switch
        {
            'm' => TimeSpan.FromMinutes(units),
            'h' => TimeSpan.FromHours(units),
            'D' => TimeSpan.FromDays(units),
            'W' => TimeSpan.FromDays(7.0 * units),
            _ => TimeSpan.Zero
        };

        return duration > TimeSpan.Zero;
    }

    // Ca 2 ham deu dung dung so ti le Fibonacci "chuan" (luy thua cua ty le
    // vang phi=1.618: phi^2=2.618, phi^3=4.236... cong them vai muc mo rong
    // pho bien khac nhu 3.618/4.618 - KHONG lam tron thanh 5.0/10.0, xac nhan
    // nguoi dung 2026-09-03) - Fib11090 (~phi^5=11.09) la muc chuan gan 10x
    // ATR nhat, khong phai so tron 10.
    private static double ToRatio(SlFibLevel level) => level switch
    {
        SlFibLevel.Fib0618 => 0.618,
        SlFibLevel.Fib0786 => 0.786,
        SlFibLevel.Fib1000 => 1.0,
        SlFibLevel.Fib1272 => 1.272,
        SlFibLevel.Fib1618 => 1.618,
        SlFibLevel.Fib2000 => 2.0,
        SlFibLevel.Fib2618 => 2.618,
        SlFibLevel.Fib3618 => 3.618,
        _ => throw new ArgumentOutOfRangeException(nameof(level))
    };

    private static double ToRatio(TpFibLevel level) => level switch
    {
        TpFibLevel.Fib0236 => 0.236,
        TpFibLevel.Fib0618 => 0.618,
        TpFibLevel.Fib1000 => 1.0,
        TpFibLevel.Fib1618 => 1.618,
        TpFibLevel.Fib2618 => 2.618,
        TpFibLevel.Fib3618 => 3.618,
        TpFibLevel.Fib4236 => 4.236,
        TpFibLevel.Fib4618 => 4.618,
        TpFibLevel.Fib6854 => 6.854,
        _ => throw new ArgumentOutOfRangeException(nameof(level))
    };

    #endregion
}
