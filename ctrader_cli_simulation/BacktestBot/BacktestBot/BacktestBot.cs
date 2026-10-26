using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

// BacktestBot = bot DUY NHAT chay nhieu CHIEN LUOC x nhieu SYMBOL x nhieu KHUNG cung luc tren 1 tai khoan backtest
// (so du + margin chung). Thay ComboMulti / MACrossMulti / MACDBreakMulti (cung quy luat vao/thoat/khoi luong).
// Moi (chien luoc, symbol, khung) la 1 KENH doc lap: toi da 1 nhom lenh / kenh, tin hieu cung huong bo qua, nguoc huong
// thi dong/huy nhom CUA KENH DO (kenh khac giu nguyen) roi mo nhom moi. RiskPercent la TONG rui ro cua 1 nhom (1 tin
// hieu), chia deu cho LegCount chan, tinh tren so du HIEN TAI cua tai khoan chung. Risk cua moi kenh nam trong cot
// risk_percent cua file tin hieu; RiskPercent chi la fallback cho file cu khong co cot nay.
// KIEU LENH DO CHIEN LUOC QUYET DINH (bang Strategies): combo = lenh CHO STOP tai gia "entry" (tin hieu BAT BUOC co
// entry, han hieu luc 3 khung, tin hieu qua han bi bo); ma_cross / macd_break = lenh MARKET (khong han hieu luc).
// File tin hieu: cot bat buoc bartime, atr, signal, strategy, tf, symbol (+ entry cho combo), risk_percent tuy chon;
// thieu cot bat buoc thi bot DUNG,
// khong doan. Dong khong hop le bi loai va duoc dem (xem dong OnStop).
// Nhan lenh: <ma chien luoc>_<symbol rut gon>_<TF>_L<n>, vd cb_US30_H1_L1 (symbol rut gon = ten cTrader cat tai dau
// cham dau tien). Bot KHONG parse nhan: nhan cua tung kenh duoc dung san tu mot ham duy nhat roi so sanh bang nhau.
// Tin hieu cua symbol nao duoc xu ly o tick DAU TIEN cua CHINH symbol do (OnTick cho symbol bieu do, Symbol.Tick cho
// cac symbol con lai); khoi luong tinh theo thong so cua tung symbol. Symbol/khung cua bieu do chi la "gia" de chay
// backtest - bot khong dung chung de quyet dinh.
[Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess, AddIndicators = false)]
public class BacktestBot : Robot
{
    private const int PendingLifetimeBars = 3;
    private const int MaxLegs = 3;

    // 2 enum SL/TP giu NGUYEN thu tu va ten nhu Combo.cs (cung cong thuc phi^(n/2)); bo_workflow doi chieu
    // enum voi metadata .algo theo thu tu nay.
    public enum SlFibLevel
    {
        Fib0618,
        Fib0786,
        Fib1000,
        Fib1272,
        Fib1618,
        Fib2058,
        Fib2618,
        Fib3330,
        Fib4236,
        Fib5388
    }

    public enum TpFibLevel
    {
        Fib1000,
        Fib1272,
        Fib1618,
        Fib2058,
        Fib2618,
        Fib3330,
        Fib4236,
        Fib5388,
        Fib6854,
        Fib8719
    }

    #region Parameters

    [Parameter("Signal File Path", Group = "Data Source", DefaultValue = "")]
    public string SignalFilePath { get; set; }

    [Parameter("KSL Level (SL = KSL x ATR)", Group = "Protection", DefaultValue = SlFibLevel.Fib1618)]
    public SlFibLevel KslLevel { get; set; }

    [Parameter("KTP Level (TP = KTP x ATR)", Group = "Protection", DefaultValue = TpFibLevel.Fib2618)]
    public TpFibLevel KtpLevel { get; set; }

    // KtpLevel la TP cua chan 1 (va cua tin hieu neu LegCount = 1); chan 2..3 dung Leg2Ktp, Leg3Ktp. Toi da 3 chan.
    [Parameter("Leg Count (1..3)", Group = "Protection", DefaultValue = 1, MinValue = 1, MaxValue = 3)]
    public int LegCount { get; set; }

    [Parameter("Leg 2 KTP Level", Group = "Protection", DefaultValue = TpFibLevel.Fib3330)]
    public TpFibLevel Leg2Ktp { get; set; }

    [Parameter("Leg 3 KTP Level", Group = "Protection", DefaultValue = TpFibLevel.Fib4236)]
    public TpFibLevel Leg3Ktp { get; set; }

    // TONG rui ro cua mot nhom leg (1 tin hieu), chia deu cho LegCount chan.
    [Parameter("Risk % Balance (total per leg group)", Group = "Risk Management", DefaultValue = 0.2, MinValue = 0.01)]
    public double RiskPercent { get; set; }

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

    // Bang chien luoc: token trong cot "strategy" cua file tin hieu (= token Redis), ma 2 chu cai dung trong nhan lenh,
    // va kieu lenh. Them chien luoc moi = them 1 dong o day (neu kieu lenh moi thi them nhanh trong PlaceOrder).
    private sealed class StrategyDef
    {
        public StrategyDef(string token, string code, bool isStopOrder)
        {
            Token = token;
            Code = code;
            IsStopOrder = isStopOrder;
        }

        public string Token { get; }
        public string Code { get; }
        public bool IsStopOrder { get; }
    }

    private static readonly StrategyDef[] Strategies =
    {
        new StrategyDef("combo", "cb", true),
        new StrategyDef("ma_cross", "mc", false),
        new StrategyDef("macd_break", "mb", false)
    };

    // Kenh = (chien luoc, symbol, khung). Labels = nhan cua tung chan (L1..L3), dung san tu BuildLabels.
    private sealed class Channel
    {
        public StrategyDef Strategy { get; set; }
        public string SymbolName { get; set; }
        public string TimeFrameName { get; set; }
        public double RiskPercent { get; set; }
        public string[] Labels { get; set; }
    }

    private sealed class SignalRow
    {
        public Channel Channel { get; set; }
        public TimeSpan BarDuration { get; set; }
        public DateTime BarTime { get; set; }
        public DateTime AvailableTime { get; set; }
        // MaxValue = khong co han hieu luc (lenh MARKET); lenh STOP duoc dat trong InitializeSignalSchedule.
        public DateTime ExpireTime { get; set; } = DateTime.MaxValue;
        public int Direction { get; set; }
        public double Atr { get; set; }
        public double EntryPrice { get; set; }
        public bool IsHandled { get; set; }

        public string SymbolName => Channel.SymbolName;
        public string TimeFrameName => Channel.TimeFrameName;
    }

    // Hang doi tin hieu CUA MOT SYMBOL, sap theo gio co hieu luc; Next = tin hieu ke tiep chua xu ly.
    private sealed class SymbolQueue
    {
        public List<SignalRow> Signals { get; } = new();
        public int Next { get; set; }
    }

    private sealed class PendingOrderLifetime
    {
        public DateTime ExpiresAt { get; set; }
    }

    private readonly Dictionary<(string Strategy, string SymbolName, string TimeFrameName, DateTime BarTime), SignalRow> _signals = new();
    private readonly Dictionary<(string Strategy, string SymbolName, string TimeFrameName), Channel> _channels = new();
    // Moi nhan lenh (cua moi kenh, moi chan) -> kenh so huu. Dung de nhan ra "lenh cua bot" va de phat hien 2 kenh trung nhan.
    private readonly Dictionary<string, Channel> _labelOwners = new();
    private readonly Dictionary<string, SymbolQueue> _queues = new();
    private readonly Dictionary<string, Symbol> _symbols = new();
    private readonly Dictionary<long, PendingOrderLifetime> _pendingOrderLifetimes = new();

    private DateTime _runStartedAt;
    // Moc het han som nhat cua cac lenh cho dang giu: tick nao truoc moc nay khong can duyet danh sach lenh.
    private DateTime _nextExpiry = DateTime.MaxValue;

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
    private int _signalRowsUnknownSymbol;
    private int _signalsExpiredBeforePlacement;
    // Dong CSV bi loai khi doc file, theo ly do (khong bao gio doan/sua ngam).
    private int _rowsDroppedStrategy;
    private int _rowsDroppedTimeframe;
    private int _rowsDroppedSymbol;
    private int _rowsDroppedEntry;
    private int _rowsDroppedInvalid;

    #endregion

    #region Lifecycle

    protected override void OnStart()
    {
        if (LegCount < 1 || LegCount > MaxLegs)
        {
            Print("BacktestBot: LegCount must be between 1 and {0} (got {1}); bot stopped.", MaxLegs, LegCount);
            Stop();
            return;
        }

        _runStartedAt = Server.Time;
        _initialBalance = Account.Balance;
        Positions.Closed += OnPositionClosed;
        PendingOrders.Filled += OnPendingOrderFilled;
        PendingOrders.Cancelled += OnPendingOrderCancelled;
        if (!LoadSignalFile())
        {
            Stop();
            return;
        }

        InitializeSignalSchedule();
        SubscribeOtherSymbolTicks();
    }

    // Tick cua symbol BIEU DO (giong Combo.cs); tick cua cac symbol khac di qua Symbol.Tick (SubscribeOtherSymbolTicks).
    protected override void OnTick()
    {
        OnSymbolTick(SymbolName);
    }

    // Het han lenh cho TRUOC khi xu ly tin hieu moi: lenh het han cua 1 kenh khong duoc chan tin hieu moi cua kenh do.
    private void OnSymbolTick(string symbolName)
    {
        ExpirePendingOrders();
        ProcessScheduledSignals(symbolName);
    }

    protected override void OnBarClosed()
    {
        UpdatePositionManagement(Bars.OpenTimes.Last(0));
    }

    protected override void OnStop()
    {
        Print(
            "BacktestBot: loaded={0}, processed={1}, before-start={2}, not-processed={3}, placed={4}, failed={5}, guard-skipped={6}, pending-expired={7}, same-direction-skipped={8}, reversed={9}, window-expired-skipped={10}; " +
            "dropped-strategy={11}, dropped-timeframe={12}, dropped-symbol={13}, dropped-entry={14}, dropped-invalid={15}, unknown-symbol={16}.",
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
            _signalsExpiredBeforePlacement,
            _rowsDroppedStrategy,
            _rowsDroppedTimeframe,
            _rowsDroppedSymbol,
            _rowsDroppedEntry,
            _rowsDroppedInvalid,
            _signalRowsUnknownSymbol);
    }

    #endregion

    #region Entry Logic

    private void HandleSignal(SignalRow signal)
    {
        if (signal.IsHandled)
            return;

        signal.IsHandled = true;
        _signalsProcessed++;

        // Giong OE (signal_window_problem): tin hieu da qua han hieu luc (vd cho qua cuoi tuan/nghi le roi moi toi
        // luot xu ly) thi BO QUA, truoc ca luat dao chieu - tin qua han khong duoc dong nhom cu. Dong thoi tranh gui
        // lenh voi han trong qua khu.
        if (Server.Time >= signal.ExpireTime)
        {
            _signalsExpiredBeforePlacement++;
            Print(
                "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, {1} signal skipped: validity window ended {2:yyyy-MM-dd HH:mm:ss} before it could be placed (symbol {3}).",
                signal.BarTime,
                SideText(signal),
                signal.ExpireTime,
                signal.SymbolName);
            return;
        }

        if (_accountBreached || _haltedForDay)
        {
            _signalsSkippedByGuard++;
            return;
        }

        if (!ReconcileExistingExposure(signal))
            return;

        PlaceOrder(signal);
    }

    // Luat vao lenh xet theo KENH (chien luoc + symbol + khung cua tin hieu):
    //   - Tin hieu CUNG huong voi exposure cua kenh -> bo qua (tra ve false).
    //   - Tin hieu NGUOC huong -> dong vi the + huy lenh cho CUA KENH DO, roi cho dat lenh moi (tra ve true).
    //   - Kenh khong co exposure -> dat lenh moi binh thuong (tra ve true).
    // Exposure cua kenh khac (chien luoc khac, khung khac, symbol khac) hoan toan khong lien quan - co the dang mua
    // khi kenh nay ban.
    private bool ReconcileExistingExposure(SignalRow signal)
    {
        int newDirection = signal.Direction;
        string channel = signal.Channel.Strategy.Token + " " + signal.TimeFrameName;
        List<Position> positions = Positions.Where(position => IsChannel(position.Label, position.SymbolName, signal)).ToList();
        List<PendingOrder> pendingOrders = PendingOrders.Where(order => IsChannel(order.Label, order.SymbolName, signal)).ToList();

        bool hasSameDirection = positions.Any(position => DirectionOf(position.TradeType) == newDirection)
                              || pendingOrders.Any(order => DirectionOf(order.TradeType) == newDirection);
        if (hasSameDirection)
        {
            _signalsSkippedSameDirection++;
            Print(
                "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, {1} signal skipped: already have same-direction exposure open (symbol {2}).",
                signal.BarTime,
                SideText(signal),
                signal.SymbolName);
            return false;
        }

        List<Position> opposingPositions = positions.Where(position => DirectionOf(position.TradeType) != newDirection).ToList();
        List<PendingOrder> opposingOrders = pendingOrders.Where(order => DirectionOf(order.TradeType) != newDirection).ToList();
        if (opposingPositions.Count > 0 || opposingOrders.Count > 0)
            _reversalsExecuted++;

        foreach (Position position in opposingPositions)
        {
            Print(
                "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, reversal - closing existing {1} {2} position {3} before opening opposite direction (symbol {4}).",
                signal.BarTime,
                channel,
                position.TradeType,
                position.Id,
                signal.SymbolName);
            TradeResult result = position.Close();
            if (!result.IsSuccessful)
            {
                _signalsSkippedByGuard++;
                Print("BacktestBot: signal skipped: could not close position {0}: {1}.", position.Id, result.Error);
                return false;
            }
        }

        foreach (PendingOrder order in opposingOrders)
        {
            Print(
                "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, reversal - cancelling existing {1} {2} pending order {3} before opening opposite direction (symbol {4}).",
                signal.BarTime,
                channel,
                order.TradeType,
                order.Id,
                signal.SymbolName);
            TradeResult result = order.Cancel();
            if (!result.IsSuccessful)
            {
                _signalsSkippedByGuard++;
                Print("BacktestBot: signal skipped: could not cancel pending order {0}: {1}.", order.Id, result.Error);
                return false;
            }
            _pendingOrderLifetimes.Remove(order.Id);
        }

        return true;
    }

    private static int DirectionOf(TradeType tradeType) => tradeType == TradeType.Buy ? 1 : -1;

    private static string SideText(SignalRow signal)
        => signal.Channel.Strategy.Code + " " + signal.TimeFrameName + " " + (signal.Direction == 1 ? "Buy" : "Sell");

    // Dat LegCount lenh (moi chan 1 lenh) cung SL, TP rieng tung chan, nhan = Channel.Labels[leg - 1]. Giong OE
    // (posman._place_legs): khoi luong tinh 1 lan cho ca nhom (duoi muc toi thieu => bo ca nhom), dat tuan tu, chan dau
    // tien bi tu choi thi DUNG - cac chan da nhan van giu nguyen. Kieu lenh do chien luoc quyet dinh:
    //   - STOP (combo): cho tai gia entry, SL/TP la gia tuyet doi, engine tu het han o ExpireTime (giong GTD cua OE).
    //   - MARKET (ma_cross, macd_break): khop ngay, SL/TP la khoang cach (pips) tinh tu gia khop nen trong backtest cac
    //     chan khop cung gia (o live co the lech vai tick).
    private void PlaceOrder(SignalRow signal)
    {
        Symbol symbol = GetTradingSymbol(signal.SymbolName);
        if (symbol == null)
        {
            _orderFailures++;
            return;
        }

        bool isStopOrder = signal.Channel.Strategy.IsStopOrder;
        double balanceAtEntry = Account.Balance;
        // Cung mot khoang SL nhung tinh theo cach cua tung kieu lenh (khong gop chung) de khoi luong khop tung bit voi
        // cac bot cu: STOP di tu gia tuyet doi (|entry - SL|), MARKET di tu he so x ATR.
        double stopLossPrice = isStopOrder ? GetStopLossPrice(signal) : 0.0;
        double stopLossPips = isStopOrder
            ? Math.Abs(signal.EntryPrice - stopLossPrice) / symbol.PipSize
            : ToRatio(KslLevel) * signal.Atr / symbol.PipSize;
        TradeType tradeType = signal.Direction == 1 ? TradeType.Buy : TradeType.Sell;
        double riskPercent = signal.Channel.RiskPercent;
        double volume = CalculateVolume(symbol, stopLossPips, riskPercent, out double pipValue);
        if (volume <= 0)
        {
            _orderFailures++;
            return;
        }

        for (int leg = 1; leg <= LegCount; leg++)
        {
            TpFibLevel ktpLevel = LegKtpLevel(leg);
            string label = signal.Channel.Labels[leg - 1];
            double takeProfitPips;
            string fillDetail = string.Empty;

            if (isStopOrder)
            {
                double takeProfitPrice = GetTakeProfitPrice(signal, ktpLevel);
                takeProfitPips = Math.Abs(signal.EntryPrice - takeProfitPrice) / symbol.PipSize;

                // expiration = ExpireTime: de ENGINE tu het han lenh dung moc (giong GTD cua OE) ke ca khi thi
                // truong dong cua/nghi le va khong co tick - neu chi huy bang OnTick thi lenh qua han van bi
                // khop o tick mo cua dau tien (gap) truoc khi bot kip huy. Bot van giu ExpirePendingOrders lam du phong.
                TradeResult result = PlaceStopOrder(
                    tradeType,
                    signal.SymbolName,
                    volume,
                    signal.EntryPrice,
                    label,
                    stopLossPrice,
                    takeProfitPrice,
                    ProtectionType.Absolute,
                    signal.ExpireTime);

                if (!result.IsSuccessful || result.PendingOrder == null)
                {
                    _orderFailures++;
                    Print(
                        "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, pending {1} at {2} was rejected: {3} (leg {4}/{5}, symbol {6}).",
                        signal.BarTime,
                        SideText(signal),
                        signal.EntryPrice,
                        result.Error,
                        leg,
                        LegCount,
                        signal.SymbolName);
                    break;
                }

                _pendingOrderLifetimes[result.PendingOrder.Id] = new PendingOrderLifetime { ExpiresAt = signal.ExpireTime };
                if (signal.ExpireTime < _nextExpiry)
                    _nextExpiry = signal.ExpireTime;
                _ordersPlaced++;
                Print(
                    "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, executed={1:yyyy-MM-dd HH:mm:ss}, pending {2} placed at {3}; SL={4}, TP={5}; volume={7:F2} units ({8:F4} lots); valid until {6:yyyy-MM-dd HH:mm:ss}; leg {9}/{10}; symbol {11}.",
                    signal.BarTime,
                    Server.Time,
                    SideText(signal),
                    signal.EntryPrice,
                    stopLossPrice,
                    takeProfitPrice,
                    signal.ExpireTime,
                    volume,
                    symbol.VolumeInUnitsToQuantity(volume),
                    leg,
                    LegCount,
                    signal.SymbolName);
            }
            else
            {
                takeProfitPips = ToRatio(ktpLevel) * signal.Atr / symbol.PipSize;

                TradeResult result = ExecuteMarketOrder(
                    tradeType,
                    signal.SymbolName,
                    volume,
                    label,
                    stopLossPips,
                    takeProfitPips);

                if (!result.IsSuccessful)
                {
                    _orderFailures++;
                    Print(
                        "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, market {1} was rejected: {2} (leg {3}/{4}, symbol {5}).",
                        signal.BarTime,
                        SideText(signal),
                        result.Error,
                        leg,
                        LegCount,
                        signal.SymbolName);
                    break;
                }

                _ordersPlaced++;
                Print(
                    "BacktestBot: bartime={0:yyyy-MM-dd HH:mm}, executed={1:yyyy-MM-dd HH:mm:ss}, market {2} placed; SL={3} pips, TP={4} pips; volume={5:F2} units ({6:F4} lots); leg {7}/{8}; symbol {9}.",
                    signal.BarTime,
                    Server.Time,
                    SideText(signal),
                    stopLossPips,
                    takeProfitPips,
                    volume,
                    symbol.VolumeInUnitsToQuantity(volume),
                    leg,
                    LegCount,
                    signal.SymbolName);

                // Lenh MARKET co ngay gia khop + SL/TP tuyet doi (result.Position); lenh STOP chi biet luc khop
                // (xem dong FILLED).
                Position filled = result.Position;
                fillDetail = $"fill={filled.EntryPrice}, SL={filled.StopLoss}, TP={filled.TakeProfit}; ";
            }

            // Chi tiet sizing de doi chieu thiet ke voi hanh vi dat lenh that: pipValue la gia tri CalculateVolume da
            // thuc su dung, raw = volume risk-based TRUOC normalize, budget la ngan sach CUA MOI CHAN (RiskPercent chia
            // deu LegCount).
            double riskBudget = balanceAtEntry * riskPercent / 100.0 / LegCount;
            double riskBasedRawVolume = riskBudget / (stopLossPips * pipValue);
            Print(
                "BacktestBot: RISK_DETAIL bartime={0:yyyy-MM-dd HH:mm} {1}: balance=${2:F2}, risk={3:F2}% => budget ${4:F2}; " +
                "ATR={5:F5}, KSL={6:F3}xATR => SL {7:F1} pips, KTP={8:F3}xATR => TP {9:F1} pips, R:R=1:{10:F2}; " +
                "pipValue={11:F8} USD/pip/unit; risk-based raw {12:F2} units => placed {13:F2} units ({14:F4} lots), step {15}; " +
                "{16}expected loss if SL hit=${17:F2}, expected profit if TP hit=${18:F2}; leg {19}/{20}; symbol {21}.",
                signal.BarTime,
                SideText(signal),
                balanceAtEntry,
                riskPercent / LegCount,
                riskBudget,
                signal.Atr,
                ToRatio(KslLevel),
                stopLossPips,
                ToRatio(ktpLevel),
                takeProfitPips,
                takeProfitPips / stopLossPips,
                pipValue,
                riskBasedRawVolume,
                volume,
                symbol.VolumeInUnitsToQuantity(volume),
                symbol.VolumeInUnitsStep,
                fillDetail,
                volume * stopLossPips * pipValue,
                volume * takeProfitPips * pipValue,
                leg,
                LegCount,
                signal.SymbolName);
        }
    }

    // cTrader KHONG ghi log gi khi lenh cho khop (chi co trong events.json) -> dong nay lap khoang trong do:
    // thoi diem khop + gia khop + truot so voi trigger + SL tinh lai TU GIA KHOP THAT.
    private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
    {
        PendingOrder order = args.PendingOrder;
        if (!IsOurs(order.Label))
            return;

        _pendingOrderLifetimes.Remove(order.Id);

        Symbol symbol = GetTradingSymbol(order.SymbolName);
        if (symbol == null)
            return;

        Position pos = args.Position;
        int direction = pos.TradeType == TradeType.Buy ? 1 : -1;
        double entrySlippagePips = direction * (pos.EntryPrice - order.TargetPrice) / symbol.PipSize;
        double pipValue = PipValueNow(symbol);
        double slFromFillPips = pos.StopLoss.HasValue
            ? Math.Abs(pos.EntryPrice - pos.StopLoss.Value) / symbol.PipSize
            : double.NaN;

        // Giu nguyen khuon "FILLED pending <id> => position <id> <Buy|Sell> ... at <gia>" cua Combo.cs
        // (dashboard/data_access.py doc dong nay); nhan lenh (co khung) va symbol o cuoi dong.
        Print(
            "BacktestBot: FILLED pending {0} => position {1} {2} {3:F2} units at {4}, executed={5:yyyy-MM-dd HH:mm:ss}; " +
            "trigger was {6}, entry slippage {7:F1} pips {8} than trigger; " +
            "SL {9} is now {10:F1} pips from fill => real risk if SL hit=${11:F2} (vs budget at placement); label {12}; symbol {13}.",
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
            pos.VolumeInUnits * slFromFillPips * pipValue,
            order.Label,
            order.SymbolName);
    }

    // Lenh cho bi huy: bot tu huy (dao chieu, het han du phong, Position Management) hoac ENGINE tu het han dung moc
    // (Reason = Expired, do tham so expiration luc dat). Chi dem + ghi log khi engine het han; giu nguyen cau chu
    // "cancelled after" de evidence/dashboard van nhan ra lenh het han.
    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        PendingOrder order = args.PendingOrder;
        if (!IsOurs(order.Label))
            return;

        _pendingOrderLifetimes.Remove(order.Id);
        if (args.Reason != PendingOrderCancellationReason.Expired)
            return;

        _expiredOrders++;
        Print(
            "BacktestBot: pending order {0} ({1}) cancelled after {2} bars of its timeframe (symbol {3}).",
            order.Id,
            order.Label,
            PendingLifetimeBars,
            order.SymbolName);
    }

    #endregion

    #region Exit Logic

    private double GetStopLossPrice(SignalRow signal)
        => signal.EntryPrice - signal.Direction * ToRatio(KslLevel) * signal.Atr;

    private static double GetTakeProfitPrice(SignalRow signal, TpFibLevel ktpLevel)
        => signal.EntryPrice + signal.Direction * ToRatio(ktpLevel) * signal.Atr;

    private TpFibLevel LegKtpLevel(int leg) => leg switch
    {
        1 => KtpLevel,
        2 => Leg2Ktp,
        _ => Leg3Ktp
    };

    // Het han lenh cho theo DONG HO (khong dem nen cua bieu do): moi lenh co ExpiresAt rieng = gio co hieu luc
    // cua tin hieu + 3 x do dai khung cua kenh. Phan chinh do ENGINE tu het han (expiration luc dat); day la du phong.
    // Chay moi tick cua MOI symbol nen thoat som neu chua toi moc het han som nhat (_nextExpiry).
    private void ExpirePendingOrders()
    {
        if (_pendingOrderLifetimes.Count == 0 || Server.Time < _nextExpiry)
            return;

        List<long> due = null;
        foreach (KeyValuePair<long, PendingOrderLifetime> item in _pendingOrderLifetimes)
        {
            if (Server.Time >= item.Value.ExpiresAt)
                (due ??= new List<long>()).Add(item.Key);
        }

        if (due != null)
        {
            foreach (long orderId in due)
            {
                PendingOrder order = PendingOrders.FirstOrDefault(candidate => candidate.Id == orderId);
                if (order == null)
                {
                    _pendingOrderLifetimes.Remove(orderId);
                    continue;
                }

                string orderLabel = order.Label;
                string orderSymbol = order.SymbolName;
                TradeResult result = order.Cancel();
                if (!result.IsSuccessful)
                {
                    Print("BacktestBot: could not expire pending order {0}: {1}; will retry on next tick.", orderId, result.Error);
                    continue;
                }
                _pendingOrderLifetimes.Remove(orderId);
                _expiredOrders++;
                Print(
                    "BacktestBot: pending order {0} ({1}) cancelled after {2} bars of its timeframe (symbol {3}).",
                    orderId,
                    orderLabel,
                    PendingLifetimeBars,
                    orderSymbol);
            }
        }

        _nextExpiry = _pendingOrderLifetimes.Count == 0
            ? DateTime.MaxValue
            : _pendingOrderLifetimes.Values.Min(lifetime => lifetime.ExpiresAt);
    }

    #endregion

    #region Risk & Position Sizing

    // Gia tri tien tai khoan cua 1 pip cho 1 unit cua `symbol`, tinh TAI THOI DIEM GOI (giong Combo.cs). KHONG dung
    // Symbol.PipValue vi no la anh chup ty gia luc OnStart. Quote == tien tai khoan: PipSize; quote khac:
    // Asset.Convert voi luong LON roi chia lai (convert 1 pip nho bi lam tron ve 0.00).
    private double PipValueNow(Symbol symbol)
    {
        if (symbol.QuoteAsset.Name == Account.Asset.Name)
            return symbol.PipSize;

        const double ProbeUnits = 1_000_000.0;
        return symbol.QuoteAsset.Convert(Account.Asset, symbol.PipSize * ProbeUnits) / ProbeUnits;
    }

    // Chi sizing theo risk% cua khoang cach SL, tren Account.Balance HIEN TAI (tai khoan chung cua moi kenh va
    // moi symbol) - KHONG chan margin (CapVolumeByMargin da go 2026-09-11, xem Combo.cs).
    private double CalculateVolume(Symbol symbol, double stopLossPips, double riskPercent, out double pipValue)
    {
        pipValue = PipValueNow(symbol);
        if (!double.IsFinite(stopLossPips) || stopLossPips <= 0
            || !double.IsFinite(pipValue) || pipValue <= 0)
            return 0;

        // Ngan sach cua MOT chan = rui ro cua chien luoc chia deu LegCount (chung SL nen khoi luong cac chan bang nhau).
        // 1 chan duoi muc toi thieu = ca nhom duoi muc toi thieu => bo ca nhom.
        double riskAmount = Account.Balance * riskPercent / 100.0 / LegCount;
        double requestedVolume = riskAmount / (stopLossPips * pipValue);
        if (!double.IsFinite(requestedVolume))
            return 0;
        if (requestedVolume < symbol.VolumeInUnitsMin)
        {
            Print(
                "BacktestBot: SIZE TOO SMALL - signal skipped. Risking {0}% of balance (${1:F2}) per leg over a stop-loss of " +
                "{2:F1} pips only needs {3:F2} units, which is below the broker minimum of {4:F2}. No order sent (symbol {5}).",
                riskPercent / LegCount,
                riskAmount,
                stopLossPips,
                requestedVolume,
                symbol.VolumeInUnitsMin,
                symbol.Name);
            return 0;
        }

        if (requestedVolume > symbol.VolumeInUnitsMax)
        {
            Print(
                "BacktestBot: SIZE ABOVE BROKER MAX - risking {0}% of balance per leg over the current stop-loss would need " +
                "{1:F2} units, above the broker maximum of {2:F2}. Volume is capped to the broker maximum (symbol {3}).",
                riskPercent / LegCount,
                requestedVolume,
                symbol.VolumeInUnitsMax,
                symbol.Name);
        }

        double volume = symbol.NormalizeVolumeInUnits(
            Math.Min(requestedVolume, symbol.VolumeInUnitsMax),
            RoundingMode.Down);
        if (!double.IsFinite(volume) || volume < symbol.VolumeInUnitsMin)
            return 0;

        return volume;
    }

    #endregion

    #region Position Management

    // Research guards sampled at bars/signals; not a hard FTMO account limit. Tinh theo TAI KHOAN (moi kenh chung).

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
                Print("BacktestBot: max drawdown reached; trading halted for this run.");
            }
        }

        if (EnableDailyLossLimit && !_haltedForDay && !_accountBreached)
        {
            double threshold = _dailyReferenceBalance * (1 - MaxDailyLossPercent / 100.0);
            if (Account.Equity <= threshold)
            {
                _haltedForDay = true;
                ForceCloseAll();
                Print("BacktestBot: daily loss limit reached; trading halted until the next UTC day.");
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
        if (!IsOurs(position.Label))
            return;

        LogClosedTrade(position, args.Reason);

        _consecutiveLosses = position.NetProfit < 0 ? _consecutiveLosses + 1 : 0;
        if (EnableMaxConsecutiveLosses && !_haltedForDay && !_accountBreached
            && _consecutiveLosses >= MaxConsecutiveLosses)
        {
            _haltedForDay = true;
            Print("BacktestBot: max consecutive losses reached; trading halted until the next UTC day.");
        }
    }

    private void ForceCloseAll()
    {
        foreach (PendingOrder order in PendingOrders.Where(order => IsOurs(order.Label)).ToList())
            order.Cancel();

        foreach (Position position in Positions.Where(position => IsOurs(position.Label)).ToList())
            position.Close();

        _pendingOrderLifetimes.Clear();
        _nextExpiry = DateTime.MaxValue;
    }

    #endregion

    #region Helpers

    // Symbol theo ten: symbol cua bieu do dung chinh doi tuong Symbol (giu hanh vi y het ban 1 symbol), symbol khac
    // lay qua Symbols.GetSymbol. Ket qua duoc nho; null neu tai khoan khong co symbol do.
    private Symbol GetTradingSymbol(string symbolName)
    {
        if (_symbols.TryGetValue(symbolName, out Symbol cached))
            return cached;

        Symbol symbol = null;
        if (symbolName == SymbolName)
        {
            symbol = Symbol;
        }
        else
        {
            try
            {
                symbol = Symbols.GetSymbol(symbolName);
            }
            catch (Exception exception)
            {
                Print("BacktestBot: could not get symbol '{0}': {1}", symbolName, exception.Message);
            }
        }

        _symbols[symbolName] = symbol;
        return symbol;
    }

    // Ten symbol rut gon cho nhan lenh: ten cTrader cat tai dau cham dau tien (US30.cash -> US30, XAUUSD giu nguyen).
    private static string ShortSymbol(string symbolName)
    {
        int dot = symbolName.IndexOf('.');
        return dot > 0 ? symbolName.Substring(0, dot) : symbolName;
    }

    // Nhan cua MOI chan cua 1 kenh: <ma chien luoc>_<symbol rut gon>_<TF>_L<n>, vd cb_US30_H1_L1. DUY NHAT noi dung
    // ra nhan lenh - bot khong bao gio tach nhan nguoc lai, chi so sanh bang voi cac chuoi nay.
    private static string[] BuildLabels(StrategyDef strategy, string symbolName, string timeFrameName)
    {
        string prefix = $"{strategy.Code}_{ShortSymbol(symbolName)}_{timeFrameName}_L";
        string[] labels = new string[MaxLegs];
        for (int leg = 1; leg <= MaxLegs; leg++)
            labels[leg - 1] = prefix + leg;
        return labels;
    }

    private static StrategyDef FindStrategy(string token)
    {
        string normalized = token.Trim().ToLowerInvariant();
        return Strategies.FirstOrDefault(strategy => strategy.Token == normalized);
    }

    // Kenh theo (chien luoc, symbol, khung); tao moi neu chua co. Tra ve null neu nhan cua kenh moi trung nhan cua mot
    // kenh KHAC (vd 2 ten cTrader cung cat ra 1 symbol rut gon) - bot se dung, khong de 2 kenh dung chung nhan.
    private Channel GetOrCreateChannel(StrategyDef strategy, string symbolName, string timeFrameName, double riskPercent)
    {
        (string, string, string) key = (strategy.Token, symbolName, timeFrameName);
        if (_channels.TryGetValue(key, out Channel existing))
        {
            if (Math.Abs(existing.RiskPercent - riskPercent) > 1e-9)
            {
                Print("BacktestBot: channel {0}/{1}/{2} has conflicting risk values {3} and {4}; bot stopped.",
                    strategy.Token, symbolName, timeFrameName, existing.RiskPercent, riskPercent);
                return null;
            }
            return existing;
        }

        Channel channel = new Channel
        {
            Strategy = strategy,
            SymbolName = symbolName,
            TimeFrameName = timeFrameName,
            RiskPercent = riskPercent,
            Labels = BuildLabels(strategy, symbolName, timeFrameName)
        };

        foreach (string label in channel.Labels)
        {
            if (_labelOwners.TryGetValue(label, out Channel owner))
            {
                Print(
                    "BacktestBot: label '{0}' would belong to two channels ({1}/{2}/{3} and {4}/{5}/{6}); bot stopped.",
                    label,
                    owner.Strategy.Token,
                    owner.SymbolName,
                    owner.TimeFrameName,
                    strategy.Token,
                    symbolName,
                    timeFrameName);
                return null;
            }
        }

        foreach (string label in channel.Labels)
            _labelOwners[label] = channel;
        _channels[key] = channel;
        return channel;
    }

    // Cua bot nay (moi chien luoc, moi kenh, moi symbol).
    private bool IsOurs(string label) => label != null && _labelOwners.ContainsKey(label);

    // Cua DUNG kenh (chien luoc + symbol + khung) cua tin hieu nay: nhan nam trong 4 nhan dung san cua kenh VA lenh
    // nam tren dung symbol do.
    private static bool IsChannel(string label, string symbolName, SignalRow signal)
        => symbolName == signal.SymbolName
           && label != null
           && Array.IndexOf(signal.Channel.Labels, label) >= 0;

    // Chi tiet lenh da dong: gia dong, pips, gross/commission/swap/net THAT, balance sau lenh, nhan (de biet khung)
    // va symbol.
    private void LogClosedTrade(Position position, PositionCloseReason reason)
    {
        HistoricalTrade trade = History.FirstOrDefault(item => item.PositionId == position.Id);
        Symbol symbol = GetTradingSymbol(position.SymbolName);
        Print(
            "BacktestBot: CLOSED position {0} {1} {13} reason={2}: entry={3}, close={4}, {5:F1} pips; " +
            "volume={6:F2} units ({7:F4} lots); gross=${8:F2}, commission=${9:F2}, swap=${10:F2}, " +
            "net=${11:F2}; balance now ${12:F2}. symbol {14}.",
            position.Id,
            position.TradeType,
            reason,
            position.EntryPrice,
            trade?.ClosingPrice ?? 0.0,
            trade?.Pips ?? position.Pips,
            position.VolumeInUnits,
            symbol != null ? symbol.VolumeInUnitsToQuantity(position.VolumeInUnits) : 0.0,
            trade?.GrossProfit ?? position.GrossProfit,
            trade?.Commissions ?? position.Commissions,
            trade?.Swap ?? position.Swap,
            trade?.NetProfit ?? position.NetProfit,
            trade?.Balance ?? Account.Balance,
            position.Label,
            position.SymbolName);
    }

    // File tin hieu: cot BAT BUOC bartime, atr, signal, strategy, tf (H1..), symbol (ten symbol cTrader); cot entry chi
    // bat buoc cho chien luoc lenh STOP (combo). Thieu cot bat buoc => tra false (bot dung), khong doan. Dong khong hop
    // le bi loai va duoc dem theo ly do. Khoa tin hieu = (chien luoc, symbol, khung, bartime).
    private bool LoadSignalFile()
    {
        _signals.Clear();

        if (string.IsNullOrWhiteSpace(SignalFilePath) || !System.IO.File.Exists(SignalFilePath))
        {
            Print("BacktestBot: signal file was not found at '{0}'; bot stopped.", SignalFilePath);
            return false;
        }

        string[] lines = System.IO.File.ReadAllLines(SignalFilePath);
        if (lines.Length < 2)
        {
            Print("BacktestBot: signal file has no data rows; bot stopped.");
            return false;
        }

        string[] header = lines[0].Split(',').Select(column => column.Trim().ToLowerInvariant()).ToArray();
        int barTimeIndex = Array.IndexOf(header, "bartime");
        int atrIndex = Array.IndexOf(header, "atr");
        int signalIndex = Array.IndexOf(header, "signal");
        int strategyIndex = Array.IndexOf(header, "strategy");
        int timeFrameIndex = Array.IndexOf(header, "tf");
        int symbolIndex = Array.IndexOf(header, "symbol");
        int entryIndex = Array.IndexOf(header, "entry");
        int riskIndex = Array.IndexOf(header, "risk_percent");

        List<string> missingColumns = new();
        if (barTimeIndex < 0) missingColumns.Add("bartime");
        if (atrIndex < 0) missingColumns.Add("atr");
        if (signalIndex < 0) missingColumns.Add("signal");
        if (strategyIndex < 0) missingColumns.Add("strategy");
        if (timeFrameIndex < 0) missingColumns.Add("tf");
        if (symbolIndex < 0) missingColumns.Add("symbol");
        if (missingColumns.Count > 0)
        {
            Print("BacktestBot: signal file is missing required column(s): {0}; bot stopped.", string.Join(", ", missingColumns));
            return false;
        }

        int requiredColumns = new[] { barTimeIndex, atrIndex, signalIndex, strategyIndex, timeFrameIndex, symbolIndex }.Max();

        for (int lineIndex = 1; lineIndex < lines.Length; lineIndex++)
        {
            if (string.IsNullOrWhiteSpace(lines[lineIndex]))
                continue;

            string[] columns = lines[lineIndex].Split(',');
            if (columns.Length <= requiredColumns)
            {
                _rowsDroppedInvalid++;
                continue;
            }

            StrategyDef strategy = FindStrategy(columns[strategyIndex]);
            if (strategy == null)
            {
                _rowsDroppedStrategy++;
                continue;
            }

            if (!DateTime.TryParse(
                    columns[barTimeIndex],
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out DateTime barTime)
                || !double.TryParse(columns[atrIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out double atr)
                // Cot "signal" theo ProtoOATradeSide (BUY=1, SELL=2) hoac quy uoc cu (SELL=-1): nhan ca hai,
                // LUON chuan hoa ve -1 truoc khi luu vao SignalRow.Direction (SL/TP dung Direction lam he so nhan gia).
                || !int.TryParse(columns[signalIndex], NumberStyles.Integer, CultureInfo.InvariantCulture, out int rawSignal)
                || (rawSignal != 1 && rawSignal != -1 && rawSignal != 2)
                || !double.IsFinite(atr) || atr <= 0)
            {
                _rowsDroppedInvalid++;
                continue;
            }

            // Chien luoc lenh STOP: entry BAT BUOC va phai > 0; chien luoc MARKET bo qua cot entry (neu co).
            double entryPrice = 0.0;
            if (strategy.IsStopOrder
                && (entryIndex < 0
                    || entryIndex >= columns.Length
                    || !double.TryParse(columns[entryIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out entryPrice)
                    || !double.IsFinite(entryPrice)
                    || entryPrice <= 0))
            {
                _rowsDroppedEntry++;
                continue;
            }

            string timeFrameName = columns[timeFrameIndex].Trim().ToUpperInvariant();
            if (!TryGetNominalBarDuration(timeFrameName, out TimeSpan barDuration))
            {
                _rowsDroppedTimeframe++;
                continue;
            }

            string symbolName = columns[symbolIndex].Trim();
            string shortSymbol = ShortSymbol(symbolName);
            if (shortSymbol.Length == 0 || shortSymbol.Contains('_'))
            {
                _rowsDroppedSymbol++;
                continue;
            }

            double channelRisk = RiskPercent;
            if (riskIndex >= 0
                && (riskIndex >= columns.Length
                    || !double.TryParse(columns[riskIndex], NumberStyles.Float, CultureInfo.InvariantCulture, out channelRisk)
                    || !double.IsFinite(channelRisk)
                    || channelRisk <= 0))
            {
                _rowsDroppedInvalid++;
                continue;
            }

            Channel channel = GetOrCreateChannel(strategy, symbolName, timeFrameName, channelRisk);
            if (channel == null)
                return false;

            _signals[(strategy.Token, symbolName, timeFrameName, barTime)] = new SignalRow
            {
                Channel = channel,
                BarDuration = barDuration,
                BarTime = barTime,
                Direction = rawSignal == 1 ? 1 : -1,
                Atr = atr,
                EntryPrice = entryPrice
            };
        }

        Print(
            "BacktestBot: loaded {0} valid signal row(s) from CSV ({1}) on {2} channel(s).",
            _signals.Count,
            string.Join(", ", Strategies.Select(item => item.Token + "=" + _signals.Values.Count(row => row.Channel.Strategy == item))),
            _channels.Count);

        int dropped = _rowsDroppedStrategy + _rowsDroppedTimeframe + _rowsDroppedSymbol + _rowsDroppedEntry + _rowsDroppedInvalid;
        if (dropped > 0)
        {
            Print(
                "BacktestBot: dropped {0} CSV row(s): unknown strategy {1}, bad timeframe {2}, bad symbol {3}, STOP strategy without a usable entry {4}, unreadable values {5}.",
                dropped,
                _rowsDroppedStrategy,
                _rowsDroppedTimeframe,
                _rowsDroppedSymbol,
                _rowsDroppedEntry,
                _rowsDroppedInvalid);
        }

        return true;
    }

    // Moi tin hieu co hieu luc luc bartime + do dai khung cua kenh (nen da dong). Tin hieu cua chien luoc lenh STOP het
    // han sau them PendingLifetimeBars khung (ExpireTime); tin hieu MARKET khong co han (ExpireTime = MaxValue). Moi SYMBOL
    // co 1 hang doi rieng sap theo gio hieu luc, hoa nhau thi khung NGAN truoc (H1, H2, H3, H4) roi toi token chien luoc:
    // thu tu chi anh huong khoi luong vi so du dung chung (dong lenh cua kenh nay doi so du cua kenh sau). Tin hieu truoc
    // luc bat dau bi danh dau da xu ly ngay (khong vao hang doi); symbol khong co tin hieu nao sau luc bat dau thi
    // khong tao hang doi va khong duoc nghe tick.
    private void InitializeSignalSchedule()
    {
        _queues.Clear();

        foreach (SignalRow signal in _signals.Values)
        {
            signal.AvailableTime = signal.BarTime.Add(signal.BarDuration);
            if (signal.Channel.Strategy.IsStopOrder)
                signal.ExpireTime = signal.AvailableTime.Add(TimeSpan.FromTicks(signal.BarDuration.Ticks * PendingLifetimeBars));
        }

        foreach (IGrouping<string, SignalRow> group in _signals.Values.GroupBy(signal => signal.SymbolName).ToList())
        {
            Symbol symbol = GetTradingSymbol(group.Key);
            if (symbol == null)
            {
                int ignored = group.Count();
                _signalRowsUnknownSymbol += ignored;
                foreach (SignalRow signal in group)
                    signal.IsHandled = true;
                Print("BacktestBot: ignored {0} signal row(s) of unknown symbol '{1}'.", ignored, group.Key);
                continue;
            }

            SymbolQueue queue = new SymbolQueue();
            foreach (SignalRow signal in group
                         .OrderBy(item => item.AvailableTime)
                         .ThenBy(item => item.BarDuration)
                         .ThenBy(item => item.BarTime)
                         .ThenBy(item => item.Channel.Strategy.Token, StringComparer.Ordinal))
            {
                if (signal.AvailableTime < _runStartedAt)
                {
                    signal.IsHandled = true;
                    _signalsBeforeStart++;
                    continue;
                }

                queue.Signals.Add(signal);
            }

            if (queue.Signals.Count > 0)
                _queues[group.Key] = queue;
        }

        Print(
            "BacktestBot: signal scheduling active for {0} symbol(s) [{1}], {2} timeframe(s) [{3}] and {4} strateg(ies) [{5}].",
            _queues.Count,
            string.Join(",", _queues.Keys.OrderBy(name => name)),
            _signals.Values.Select(signal => signal.TimeFrameName).Distinct().Count(),
            string.Join(",", _signals.Values.Select(signal => signal.TimeFrameName).Distinct().OrderBy(name => name)),
            _signals.Values.Select(signal => signal.Channel.Strategy.Token).Distinct().Count(),
            string.Join(",", _signals.Values.Select(signal => signal.Channel.Strategy.Token).Distinct().OrderBy(name => name)));
    }

    // Nghe tick cua moi symbol CON LAI co tin hieu de xu ly tin hieu cua symbol do o tick dau tien cua chinh no.
    // Symbol cua bieu do khong dang ky o day (da co OnTick) de khong xu ly hai lan.
    private void SubscribeOtherSymbolTicks()
    {
        foreach (string symbolName in _queues.Keys.ToList())
        {
            if (symbolName == SymbolName)
                continue;

            Symbol other = GetTradingSymbol(symbolName);
            if (other == null)
                continue;

            string captured = symbolName;
            other.Tick += args => OnSymbolTick(captured);
        }
    }

    // Tin hieu cua `symbolName` duoc xu ly ngay tai tick kha dung dau tien cua CHINH symbol do sau AvailableTime
    // (bartime + do dai khung), khong gioi han thoi gian cho - giong Combo.cs. Khi thi truong dong cua, chi tick cua
    // symbol do (khong phai symbol khac) moi kich hoat viec dat lenh.
    private void ProcessScheduledSignals(string symbolName)
    {
        if (!_queues.TryGetValue(symbolName, out SymbolQueue queue))
            return;

        bool positionManagementUpdated = false;
        while (queue.Next < queue.Signals.Count)
        {
            SignalRow signal = queue.Signals[queue.Next];
            if (signal.AvailableTime > Server.Time)
                return;

            queue.Next++;
            if (signal.IsHandled)
                continue;

            if (!positionManagementUpdated)
            {
                UpdatePositionManagement(Server.Time);
                positionManagementUpdated = true;
            }

            HandleSignal(signal);
        }
    }

    // "H1" / "m45" / "D1" / "W1" -> do dai khung. Chu cai khong phan biet hoa thuong: M = phut, H = gio, D = ngay,
    // W = tuan. Khung khong co do dai co dinh (vd "Month1") => false.
    private static bool TryGetNominalBarDuration(string timeFrameName, out TimeSpan duration)
    {
        duration = TimeSpan.Zero;
        if (string.IsNullOrWhiteSpace(timeFrameName) || timeFrameName.Length < 2
            || !int.TryParse(timeFrameName.Substring(1), NumberStyles.None, CultureInfo.InvariantCulture, out int units)
            || units <= 0)
            return false;

        duration = char.ToUpperInvariant(timeFrameName[0]) switch
        {
            'M' => TimeSpan.FromMinutes(units),
            'H' => TimeSpan.FromHours(units),
            'D' => TimeSpan.FromDays(units),
            'W' => TimeSpan.FromDays(7.0 * units),
            _ => TimeSpan.Zero
        };

        return duration > TimeSpan.Zero;
    }

    // Ca 2 ham deu dung dung 1 cong thuc: phi^(n/2), phi=1.618034 (xem Combo.cs).
    private static double ToRatio(SlFibLevel level) => level switch
    {
        SlFibLevel.Fib0618 => 0.618,   // phi^-1
        SlFibLevel.Fib0786 => 0.786,   // phi^-0.5
        SlFibLevel.Fib1000 => 1.0,     // phi^0
        SlFibLevel.Fib1272 => 1.272,   // phi^0.5
        SlFibLevel.Fib1618 => 1.618,   // phi^1
        SlFibLevel.Fib2058 => 2.058,   // phi^1.5
        SlFibLevel.Fib2618 => 2.618,   // phi^2
        SlFibLevel.Fib3330 => 3.330,   // phi^2.5
        SlFibLevel.Fib4236 => 4.236,   // phi^3
        SlFibLevel.Fib5388 => 5.388,   // phi^3.5
        _ => throw new ArgumentOutOfRangeException(nameof(level))
    };

    private static double ToRatio(TpFibLevel level) => level switch
    {
        TpFibLevel.Fib1000 => 1.0,     // phi^0
        TpFibLevel.Fib1272 => 1.272,   // phi^0.5
        TpFibLevel.Fib1618 => 1.618,   // phi^1
        TpFibLevel.Fib2058 => 2.058,   // phi^1.5
        TpFibLevel.Fib2618 => 2.618,   // phi^2
        TpFibLevel.Fib3330 => 3.330,   // phi^2.5
        TpFibLevel.Fib4236 => 4.236,   // phi^3
        TpFibLevel.Fib5388 => 5.388,   // phi^3.5
        TpFibLevel.Fib6854 => 6.854,   // phi^4
        TpFibLevel.Fib8719 => 8.719,   // phi^4.5
        _ => throw new ArgumentOutOfRangeException(nameof(level))
    };

    #endregion
}
