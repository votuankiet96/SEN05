// ============================================================
// KHAI BÁO THƯ VIỆN — những "bộ công cụ" bên ngoài mà bot cần dùng
// ============================================================
using System;                       // Các hàm cơ bản của C#: DateTime, Math, v.v.
using System.Collections.Generic;   // Cho phép dùng Dictionary (bảng tra cứu key-value)
using System.Globalization;         // Xử lý định dạng số/ngày tháng theo chuẩn quốc tế (dấu chấm thập phân)
using System.IO;                    // Đọc/ghi file trên ổ đĩa
using System.Linq;                  // Cho phép lọc, tìm kiếm trong danh sách (Where, Count, Any...)
using cAlgo.API;                    // Toàn bộ API của cTrader: Robot, Position, PendingOrder, v.v.
using cAlgo.API.Indicators;         // Các chỉ báo kỹ thuật của cTrader: SMA, EMA, ATR, v.v.

// ============================================================
// KHAI BÁO NAMESPACE — tên "không gian" chứa code này, tránh xung đột tên với bot khác
// ============================================================
namespace cAlgo.Robots;

/*
 * SEN_Combo_V0 - Bot thực thi tín hiệu từ file CSV theo chiến lược Combo gốc.
 *
 * Luồng hoạt động tổng quan:
 *   1) OnStart: khi bot khởi động, đọc toàn bộ tín hiệu từ file CSV vào bộ nhớ.
 *   2) Mỗi khi một bar (nến) đóng lại, bot kiểm tra xem bar đó có tín hiệu trong CSV không.
 *   3) Nếu tín hiệu mới NGƯỢC chiều với lệnh hiện tại → hủy pending orders ngược chiều,
 *      đóng positions ngược chiều, rồi đặt cụm lệnh mới.
 *   4) Nếu tín hiệu mới CÙNG chiều với lệnh/pending đang có → bỏ qua tín hiệu đó.
 *   5) Bot đặt Stop Order (lệnh chờ breakout) theo ExitMode đã chọn:
 *        FixedOnly: 1 leg duy nhất, TP cố định = Entry ± KTP × ATR.
 *        TwoLegs:   leg 1 = TP cố định; leg 2 = không có TP, SL được trail theo SMA20 mỗi bar.
 *
 * Định dạng CSV được hỗ trợ:
 *   bartime,side,atr                   ← 3 cột (dùng cho 1 symbol)
 *   bartime,symbol,side,atr            ← 4 cột (dùng cho nhiều symbol trong 1 file)
 *
 * Công thức Entry / SL / TP (theo giáo trình Combo gốc):
 *   BUY  -> Buy Stop  | Entry = High + X  | SL = Low  - X  | TP = Entry + KTP × ATR
 *   SELL -> Sell Stop | Entry = Low  - X  | SL = High + X  | TP = Entry - KTP × ATR
 *   X là khoảng dịch cố định tính bằng đơn vị giá, khác nhau theo từng symbol.
 *   High và Low lấy từ bar tín hiệu (bar vừa đóng, Last(1)).
 *
 * Trail SMA20 (chỉ dùng cho Leg 2 khi ExitMode = TwoLegs):
 *   BUY leg 2:  mỗi bar đóng → SL mới = max(SL hiện tại, SMA20)   ← SL chỉ tăng lên
 *   SELL leg 2: mỗi bar đóng → SL mới = min(SL hiện tại, SMA20)   ← SL chỉ giảm xuống
 *   SL chỉ dịch về phía có lợi, KHÔNG BAO GIỜ nới rộng rủi ro.
 *
 * Quản lý lệnh:
 *   Pending orders tự động hủy sau N bar nếu chưa được kích hoạt.
 *   Tín hiệu ngược chiều sẽ đóng positions hiện tại và pending orders, rồi đặt cụm lệnh mới.
 *   Tín hiệu cùng chiều bị bỏ qua khi đã có exposure cùng chiều.
 *   Bảo vệ drawdown hàng ngày: dừng giao dịch và đóng toàn bộ lệnh khi vượt ngưỡng lỗ.
 */

// ============================================================
// ENUM ExitModeOption — định nghĩa 2 chế độ thoát lệnh có thể chọn
// ============================================================
public enum ExitModeOption
{
    FixedOnly = 1,  // Chỉ 1 leg, dùng TP cố định tính từ ATR
    TwoLegs   = 2,  // 2 leg: leg 1 có TP cố định, leg 2 trail SL theo SMA20
}

// ============================================================
// KHAI BÁO CLASS BOT CHÍNH
// [Robot(...)]: đánh dấu đây là một cBot hợp lệ cho cTrader
//   AccessRights.FullAccess: bot được phép đọc file, kết nối mạng, v.v.
//   AddIndicators = true: cho phép bot thêm chỉ báo lên chart
// ============================================================
[Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
public class SEN_Combo_V0 : Robot   // Bot này kế thừa từ lớp Robot của cTrader
{
    // ============================================================
    // NHÓM THAM SỐ: FILE — cấu hình nguồn dữ liệu tín hiệu
    // ============================================================

    // Đường dẫn đến file CSV chứa tín hiệu giao dịch (do engine Python tạo ra)
    // Người dùng có thể thay đổi đường dẫn này trong cửa sổ cài đặt bot
    [Parameter("Signal CSV Path", Group = "File",
        DefaultValue = @"Z:\SEN05_Autotrading\raw_signals\combo\combo_US30_H4_20170430_20260602_signals.csv")]
    public string CsvPath { get; set; }

    // Số giờ cộng thêm (hoặc trừ bớt) vào thời gian trong CSV để khớp với giờ broker
    // Ví dụ: CSV dùng UTC+0, broker dùng UTC+2 → đặt CsvTimeOffsetHours = 2
    // Phạm vi: -12 đến +14 giờ, bước nhảy 1 giờ
    [Parameter("CSV Time Offset Hours", Group = "File",
        DefaultValue = 0, MinValue = -12, MaxValue = 14, Step = 1)]
    public int CsvTimeOffsetHours { get; set; }

    // Tên nhãn (label) của bot — dùng để đánh dấu các lệnh do bot này đặt
    // Giúp phân biệt lệnh của bot này với các bot khác hoặc lệnh thủ công trên cùng tài khoản
    [Parameter("Bot Label", Group = "Identity", DefaultValue = "SEN_Combo_V0")]
    public string BotLabel { get; set; }

    // ============================================================
    // NHÓM THAM SỐ: EXECUTION — cấu hình cách vào lệnh
    // ============================================================

    // X: khoảng dịch entry và SL so với đỉnh/đáy của bar tín hiệu, tính bằng đơn vị giá
    // Mỗi symbol có giá trị X khác nhau: US30=10, HK50=15, J225=15, US100/DE40/UK100=5, US500=1
    // BUY:  Entry = High + X  |  SL = Low - X
    // SELL: Entry = Low  - X  |  SL = High + X
    [Parameter("X Offset (price units)", Group = "Execution",
        DefaultValue = 10.0, MinValue = 0, MaxValue = 50.0, Step = 0.5)]
    public double XOffset { get; set; }

    // Chọn mức Fibonacci để tính khoảng cách TP (Take Profit)
    // Có 6 mức: 1.272 / 1.618 / 2.000 / 2.272 / 2.618 / 3.618
    // Level 4 = 2.272 ≈ 2.3 là mặc định theo giáo trình Combo
    // TP = Entry ± KTP × ATR
    [Parameter("KTP Fibonacci Level (1-6)", Group = "Execution",
        DefaultValue = 4, MinValue = 1, MaxValue = 6, Step = 1)]
    public int KtpFibLevel { get; set; }

    // Chế độ thoát lệnh: FixedOnly (1 leg) hoặc TwoLegs (2 leg)
    [Parameter("Exit Mode", Group = "Execution", DefaultValue = ExitModeOption.FixedOnly)]
    public ExitModeOption ExitMode { get; set; }

    // Bộ lọc spread: nếu spread / khoảng cách SL (tính bằng %) vượt quá ngưỡng này thì bỏ qua lệnh
    // Đặt 0 để tắt bộ lọc này (không kiểm tra spread)
    // Ví dụ: đặt 5 → nếu spread chiếm hơn 5% khoảng cách SL thì không đặt lệnh
    [Parameter("Max Spread / SL % (0=off)", Group = "Execution",
        DefaultValue = 0.0, MinValue = 0.0, MaxValue = 25.0, Step = 2.5)]
    public double MaxSpreadToStopLossPercent { get; set; }

    // Số bar tối đa một pending order được phép tồn tại trước khi tự động bị hủy
    // Ví dụ: trên khung H4, đặt 3 = lệnh chờ tối đa 12 giờ, nếu không fill thì hủy
    [Parameter("Cancel Pending After Bars", Group = "Execution",
        DefaultValue = 3, MinValue = 1, MaxValue = 50, Step = 1)]
    public int CancelPendingAfterBars { get; set; }

    // Độ dài tối đa của bar tín hiệu tính bằng giờ đồng hồ thực
    // Dùng để loại bỏ các bar bất thường như bar qua cuối tuần hoặc bar qua kỳ nghỉ lễ
    // Đặt 0 để tắt bộ lọc này
    // Ví dụ: trên H4, đặt 6 → loại bỏ nếu bar kéo dài hơn 6 giờ thực tế
    [Parameter("Max Signal Bar Clock Hours", Group = "Execution",
        DefaultValue = 0, MinValue = 0, MaxValue = 168, Step = 1)]
    public double MaxSignalBarClockHours { get; set; }

    // ============================================================
    // NHÓM THAM SỐ: RISK — cấu hình quản lý rủi ro
    // ============================================================

    // Phần trăm tài khoản chấp nhận rủi ro cho MỖI cụm lệnh (toàn bộ cluster)
    // Nếu ExitMode = TwoLegs, risk được chia đều: mỗi leg chịu RiskPercent / 2
    // Ví dụ: Risk=1%, TwoLegs → mỗi leg risk 0.5%
    [Parameter("Risk % per Trade", Group = "Risk",
        DefaultValue = 1.0, MinValue = 0.1, MaxValue = 3.0, Step = 0.1)]
    public double RiskPercent { get; set; }

    // Ngưỡng drawdown tối đa cho phép trong một ngày giao dịch
    // Được đo từ equity đầu ngày (bao gồm cả lãi/lỗ đang mở)
    // Khi chạm ngưỡng: hủy tất cả pending orders, đóng tất cả positions, dừng giao dịch đến hôm sau
    [Parameter("Max Daily Drawdown %", Group = "Risk",
        DefaultValue = 10.0, MinValue = 1.0, MaxValue = 100.0, Step = 0.5)]
    public double MaxDailyDrawdownPercent { get; set; }

    // ============================================================
    // BIẾN NỘI BỘ — dữ liệu bot lưu trong bộ nhớ trong suốt quá trình chạy
    // ============================================================

    // Bảng tra cứu tín hiệu: key = thời gian bar, value = thông tin tín hiệu (side + ATR)
    // Được nạp 1 lần khi bot khởi động, không thay đổi sau đó
    private readonly Dictionary<DateTime, SignalInfo> _signals = new();

    // Bảng ghi nhớ: key = ID của pending order, value = số bar lúc lệnh đó được đặt
    // Dùng để tính tuổi của từng pending order → biết khi nào cần hủy vì hết hạn
    private readonly Dictionary<long, int> _pendingOrderCreatedBarCounts = new();

    // Đường trung bình động SMA20, dùng để trail SL cho Leg 2 trong chế độ TwoLegs
    // Luôn được khởi tạo, nhưng chỉ được áp dụng khi ExitMode = TwoLegs
    private MovingAverage _sma20;

    // ── Các biến cho bảo vệ drawdown hàng ngày ──

    // Ngày hiện tại đang được theo dõi (để phát hiện khi sang ngày mới)
    private DateTime _drawdownDay;

    // Equity tài khoản tại thời điểm đầu ngày (mốc baseline để tính % drawdown)
    private double   _dayStartEquity;

    // Cờ đánh dấu: hôm nay đã kích hoạt drawdown chưa? true = đã kích hoạt, đang bị khóa
    private bool     _dailyDrawdownTriggered;

    // ── Các bộ đếm thống kê — không ảnh hưởng logic giao dịch, chỉ dùng để báo cáo ──

    private int _barsWithoutSignal;           // Số bar không có tín hiệu nào trong CSV
    private int _matchedSignalCount;          // Số bar có tín hiệu khớp
    private int _multiSignalBarCount;         // Số bar có từ 2 tín hiệu trở lên (lấy cái mới nhất)
    private int _stopOrdersPlacedCount;       // Tổng số stop order đã đặt thành công
    private int _pendingFilledCount;          // Số lệnh pending đã được kích hoạt (fill)
    private int _pendingCancelledCount;       // Số lệnh pending bị hủy thủ công bởi bot
    private int _pendingExpiredCount;         // Số lệnh pending bị hủy vì hết hạn (quá N bar)
    private int _skipSameDirectionSignalCount;// Số tín hiệu bỏ qua vì đã có lệnh cùng chiều
    private int _skipSpreadFilterCount;       // Số tín hiệu bỏ qua vì spread quá rộng
    private int _skipVolumeCount;             // Số tín hiệu bỏ qua vì volume tính ra dưới mức tối thiểu
    private int _skipCloseOppositeCount;      // Số tín hiệu bỏ qua vì không đóng được lệnh ngược chiều
    private int _skipLongSignalBarCount;      // Số tín hiệu bỏ qua vì bar dài hơn MaxSignalBarClockHours
    private int _placeOrderErrorCount;        // Số lần đặt lệnh bị lỗi (từ broker)

    // ============================================================
    // OnStart — Hàm này chạy MỘT LẦN DUY NHẤT khi bot được khởi động
    // ============================================================
    protected override void OnStart()
    {
        // Đăng ký 3 "sự kiện lắng nghe":
        // Khi có pending order được fill (kích hoạt) → gọi hàm OnPendingOrderFilled
        PendingOrders.Filled    += OnPendingOrderFilled;
        // Khi có pending order bị hủy → gọi hàm OnPendingOrderCancelled
        PendingOrders.Cancelled += OnPendingOrderCancelled;
        // Khi có position bị đóng → gọi hàm OnPositionClosed
        Positions.Closed        += OnPositionClosed;

        // Ghi nhớ equity hiện tại làm mốc đầu ngày để tính drawdown
        ResetDailyDrawdownBaseline();

        // Kiểm tra tất cả tham số người dùng nhập vào có hợp lệ không
        // Nếu có tham số sai → in lỗi và dừng bot
        if (!ValidateParameters())
        {
            Stop();   // Dừng bot ngay lập tức
            return;   // Thoát khỏi hàm OnStart, không chạy tiếp
        }

        // Khởi tạo chỉ báo SMA20 dựa trên giá đóng cửa của các bar
        // Luôn tạo dù đang dùng FixedOnly, vì cần sẵn sàng nếu người dùng đổi sang TwoLegs
        _sma20 = Indicators.SimpleMovingAverage(Bars.ClosePrices, 20);

        // Lấy hệ số KTP từ bảng Fibonacci (ví dụ level 4 → 2.272)
        var ktpMultiplier = GetKtpMultiplier(KtpFibLevel);

        // In thông tin cài đặt ban đầu ra cửa sổ log để kiểm tra trước khi chạy
        Print("[{0}] Symbol info | Symbol: {1} | PipSize: {2} | TickSize: {3} | Digits: {4} | VolumeMin: {5} | X: {6} | KTP: {7:F3} (Level {8}) | ExitMode: {9} | Execution: StopOrderOnly | MaxSignalBarClockHours: {10:F1}",
            BotLabel, SymbolName,
            Symbol.PipSize, Symbol.TickSize, Symbol.Digits, Symbol.VolumeInUnitsMin,
            XOffset, ktpMultiplier, KtpFibLevel, ExitMode,
            MaxSignalBarClockHours);

        // Đọc toàn bộ file CSV vào Dictionary _signals trong bộ nhớ
        LoadSignals();

        // Nếu sau khi đọc CSV mà không có tín hiệu nào hợp lệ → dừng bot
        if (_signals.Count == 0)
        {
            Print("[{0}] ERROR: no valid signals loaded. Stopping.", BotLabel);
            Stop();
        }

        // Nếu bot được khởi động lại giữa chừng (đã có pending orders từ phiên trước),
        // ghi nhớ lại các pending orders đó để quản lý thời hạn hủy đúng cách
        TrackExistingPendingOrders();
    }

    // ============================================================
    // OnTick — Hàm này chạy MỖI LẦN GIÁ THAY ĐỔI (tick)
    // Mục đích duy nhất: kiểm tra drawdown thật nhanh, không đợi đến bar đóng
    // ============================================================
    protected override void OnTick()
    {
        // Kiểm tra xem hôm nay đã lỗ quá giới hạn chưa
        // Nếu có → đóng lệnh và dừng giao dịch ngay trong tick này (không đợi bar đóng)
        CheckDailyDrawdownLimit();
    }

    // ============================================================
    // OnBarClosed — Hàm này chạy MỖI KHI MỘT BAR (NẾN) ĐÓNG LẠI
    // Đây là trái tim của bot — toàn bộ logic giao dịch xảy ra ở đây
    // ============================================================
    protected override void OnBarClosed()
    {
        // Bước 1: Kiểm tra drawdown một lần nữa khi bar đóng
        // Nếu đã kích hoạt drawdown → return (không làm gì thêm trong bar này)
        if (CheckDailyDrawdownLimit())
            return;

        // Bước 2: Cập nhật trailing SL cho Leg 2 TRƯỚC KHI kiểm tra tín hiệu mới
        // Lý do: đảm bảo SL luôn được trail đúng, kể cả bar không có tín hiệu mới
        ApplySma20Trailing();

        // Bước 3: Quét và hủy các pending orders đã tồn tại quá số bar cho phép
        CancelExpiredPendingOrders();

        // Bước 4: Nếu không có tín hiệu nào trong bộ nhớ → thoát sớm, không làm gì
        if (_signals.Count == 0)
            return;

        // Lấy thời gian MỞ của bar vừa đóng (bar tín hiệu) — làm tròn đến phút
        // Last(1) = bar thứ 1 tính từ phải sang (bar vừa đóng)
        var barTime     = TrimToMinute(Bars.OpenTimes.Last(1));

        // Lấy thời gian MỞ của bar hiện tại (bar đang hình thành) — làm tròn đến phút
        // Last(0) = bar đang hình thành (chưa đóng)
        var nextBarTime = TrimToMinute(Bars.OpenTimes.Last(0));

        // Bước 5: Tìm tín hiệu trong CSV có thời gian nằm trong khoảng [barTime, nextBarTime)
        // Nếu không tìm thấy → tăng bộ đếm và bỏ qua bar này
        if (!TryGetSignalForClosedBar(barTime, nextBarTime, out var signal, out var signalsInBar))
        {
            _barsWithoutSignal++;   // Ghi nhận: bar này không có tín hiệu
            return;
        }

        // Có tín hiệu! Tăng bộ đếm
        _matchedSignalCount++;

        // Nếu trong 1 bar có từ 2 tín hiệu trở lên → ghi nhận (bot tự xử lý bằng cách lấy cái mới nhất)
        if (signalsInBar > 1)
            _multiSignalBarCount++;

        // Tính độ dài thực tế của bar tín hiệu tính bằng giờ đồng hồ
        // nextBarTime - barTime = khoảng thời gian thực tế bar đó kéo dài
        var signalBarClockHours = (nextBarTime - barTime).TotalHours;

        // Bước 6: Bộ lọc bar dài bất thường
        // Nếu MaxSignalBarClockHours > 0 VÀ bar này dài hơn ngưỡng → bỏ qua
        // Mục đích: loại bỏ bar qua cuối tuần/nghỉ lễ có giá bất thường
        if (MaxSignalBarClockHours > 0 && signalBarClockHours > MaxSignalBarClockHours)
        {
            _skipLongSignalBarCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: bar clock {3:F2}h > MaxSignalBarClockHours {4:F2}h. NextBar: {5:yyyy-MM-dd HH:mm}",
                BotLabel, signal.Side, barTime, signalBarClockHours, MaxSignalBarClockHours, nextBarTime);
            return;
        }

        // Bước 7: Xác định chiều lệnh từ tín hiệu (BUY hoặc SELL)
        var tradeType = GetTradeType(signal);

        // Bước 8: Hủy tất cả pending orders NGƯỢC chiều với tín hiệu mới
        // Ví dụ: tín hiệu BUY → hủy tất cả Sell Stop orders đang chờ
        CancelOppositePendingOrders(tradeType);

        // Bước 9: Đóng tất cả positions NGƯỢC chiều với tín hiệu mới
        // Ví dụ: tín hiệu BUY → đóng tất cả SELL positions đang mở
        // Nếu đóng không thành công → ghi nhận và bỏ qua tín hiệu này (không đặt lệnh mới)
        if (!CloseOppositePositions(tradeType))
        {
            _skipCloseOppositeCount++;  // Ghi nhận: bỏ qua vì không đóng được lệnh ngược chiều
            return;
        }

        // Bước 10: Kiểm tra xem đã có position hoặc pending CÙNG chiều chưa
        // Đếm tổng số positions + pending orders cùng chiều
        var sameDirectionCount = GetMyOpenOrderCount(tradeType);

        // Nếu đã có lệnh cùng chiều → bỏ qua tín hiệu này (không mở thêm)
        // Nguyên tắc: không đuổi theo, không scale in — 1 tín hiệu = 1 cụm lệnh
        if (sameDirectionCount > 0)
        {
            _skipSameDirectionSignalCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: same-direction exposure exists ({3} order/position).",
                BotLabel, signal.Side, barTime, sameDirectionCount);
            return;
        }

        // Bước 11: Tất cả điều kiện đều qua → đặt cụm lệnh stop order
        PlaceSignalOrder(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar);
    }

    // ============================================================
    // OnStop — Hàm này chạy KHI BOT DỪNG (người dùng bấm Stop hoặc cTrader đóng)
    // In ra bảng tổng kết để người dùng đánh giá hiệu quả hoạt động
    // ============================================================
    protected override void OnStop()
    {
        // In số tín hiệu đã nạp được từ CSV
        Print("[{0}] Stopped. Signals loaded: {1}", BotLabel, _signals.Count);

        // In bảng tổng kết toàn bộ hoạt động:
        // Matched=tín hiệu khớp | MultiBar=bar có nhiều tín hiệu | NoSignal=bar không có tín hiệu
        // Placed=lệnh đặt được | Filled=lệnh được fill | Cancelled=lệnh bị hủy | Expired=lệnh hết hạn
        // SkipSameDir=bỏ qua cùng chiều | SkipSpread=bỏ qua vì spread | SkipVol=bỏ qua vì volume thấp
        // SkipCloseOpp=bỏ qua vì không đóng được ngược chiều | SkipLongBar=bỏ qua vì bar dài
        // Errors=số lần đặt lệnh bị lỗi
        Print("[{0}] Summary | Matched: {1} | MultiBar: {2} | NoSignal: {3} | Placed: {4} | Filled: {5} | Cancelled: {6} | Expired: {7} | SkipSameDir: {8} | SkipSpread: {9} | SkipVol: {10} | SkipCloseOpp: {11} | SkipLongBar: {12} | Errors: {13}",
            BotLabel,
            _matchedSignalCount, _multiSignalBarCount, _barsWithoutSignal,
            _stopOrdersPlacedCount, _pendingFilledCount, _pendingCancelledCount, _pendingExpiredCount,
            _skipSameDirectionSignalCount, _skipSpreadFilterCount, _skipVolumeCount,
            _skipCloseOppositeCount, _skipLongSignalBarCount, _placeOrderErrorCount);
    }

    // ============================================================
    // ValidateParameters — Kiểm tra hợp lệ tất cả tham số người dùng đã nhập
    // Trả về true nếu tất cả hợp lệ, false nếu có lỗi
    // ============================================================
    private bool ValidateParameters()
    {
        // Kiểm tra đường dẫn CSV không được để trống
        if (string.IsNullOrWhiteSpace(CsvPath))
        {
            Print("[{0}] ERROR: CSV path is empty.", BotLabel);
            return false;
        }

        // X Offset phải > 0 (không có offset = không có breakout buffer)
        // và <= 200 (giá trị quá lớn sẽ làm SL/Entry vô lý)
        if (XOffset <= 0 || XOffset > 200.0)
        {
            Print("[{0}] ERROR: X Offset must be > 0 and <= 200 price units.", BotLabel);
            return false;
        }

        // Level KTP phải nằm trong bảng Fibonacci đã định nghĩa (1 đến 6)
        if (KtpFibLevel < 1 || KtpFibLevel > 6)
        {
            Print("[{0}] ERROR: KTP Fibonacci level must be 1 to 6.", BotLabel);
            return false;
        }

        // MaxSpreadToStopLossPercent phải từ 0 đến 25%
        // 0 = tắt bộ lọc; 25 = chỉ lọc spread cực kỳ bất thường
        if (MaxSpreadToStopLossPercent < 0 || MaxSpreadToStopLossPercent > 25.0)
        {
            Print("[{0}] ERROR: Max Spread / SL % must be 0 to 25. Use 0 to disable.", BotLabel);
            return false;
        }

        // Risk % phải > 0 (không thể risk 0%) và <= 3% (giới hạn an toàn tối đa)
        if (RiskPercent <= 0 || RiskPercent > 3.0)
        {
            Print("[{0}] ERROR: Risk percent must be > 0 and <= 3.", BotLabel);
            return false;
        }

        // Phải chờ ít nhất 1 bar trước khi hủy pending order hết hạn
        if (CancelPendingAfterBars < 1)
        {
            Print("[{0}] ERROR: Cancel pending after bars must be >= 1.", BotLabel);
            return false;
        }

        // Drawdown % phải > 0 và <= 100%
        if (MaxDailyDrawdownPercent <= 0 || MaxDailyDrawdownPercent > 100)
        {
            Print("[{0}] ERROR: Max daily drawdown percent must be > 0 and <= 100.", BotLabel);
            return false;
        }

        // Tất cả hợp lệ
        return true;
    }

    // ============================================================
    // ApplySma20Trailing — Cập nhật trailing SL cho Leg 2 theo SMA20
    // Được gọi mỗi khi bar đóng, chỉ hoạt động khi ExitMode = TwoLegs
    // ============================================================
    private void ApplySma20Trailing()
    {
        // Chỉ thực hiện nếu đang ở chế độ TwoLegs; nếu không thì thoát ngay
        if (ExitMode != ExitModeOption.TwoLegs)
            return;

        // Lấy giá trị SMA20 của bar vừa đóng (Last(1))
        var sma20Value = _sma20.Result.Last(1);

        // Nếu SMA20 chưa tính được (chưa đủ 20 bar dữ liệu) → thoát, không trail
        if (double.IsNaN(sma20Value))
            return;

        // Duyệt qua TẤT CẢ positions đang mở của bot này
        foreach (var position in GetMyPositions())
        {
            // Chỉ xử lý những position có label là Leg 2 (_L2) — đây là leg trailing
            // Leg 1 (_L1) có TP cố định, không cần trail
            if (!IsTrailingLeg(position.Label))
                continue;  // Bỏ qua leg này, sang position tiếp theo

            // Tính SL mới dựa trên SMA20:
            // BUY leg 2:  SL mới = giá trị LỚN HƠN giữa SL hiện tại và SMA20
            //             → SL chỉ tăng lên (dịch về phía entry, thu hẹp rủi ro)
            // SELL leg 2: SL mới = giá trị NHỎ HƠN giữa SL hiện tại và SMA20
            //             → SL chỉ giảm xuống (dịch về phía entry, thu hẹp rủi ro)
            // Nếu position chưa có SL thì dùng giá trị cực đại/cực tiểu để so sánh
            var newSl = position.TradeType == TradeType.Buy
                ? Math.Max(position.StopLoss ?? double.MinValue, sma20Value)
                : Math.Min(position.StopLoss ?? double.MaxValue, sma20Value);

            // Làm tròn SL mới theo số chữ số thập phân của symbol
            // (ví dụ US30 có 1 chữ số thập phân → làm tròn đến 0.1)
            newSl = Math.Round(newSl, Symbol.Digits);

            // Chỉ gửi lệnh sửa SL nếu SL mới thực sự tốt hơn SL cũ
            // Tránh gửi lệnh thừa hoặc nới rộng SL
            if (!ShouldImproveStopLoss(position, newSl))
                continue;  // SL mới không tốt hơn → bỏ qua

            // Lưu lại SL cũ để in log so sánh
            var oldSl  = position.StopLoss;

            // Gửi lệnh sửa position: thay SL mới, giữ nguyên TP
            // ProtectionType.Absolute = newSl là giá tuyệt đối (không phải pips)
            var result = ModifyPosition(position, newSl, position.TakeProfit, ProtectionType.Absolute);

            // Nếu sửa thất bại → in log lỗi
            if (!result.IsSuccessful)
                Print("[{0}] SMA20 TRAIL ERROR | {1} | Label: {2} | SL {3} -> {4} failed: {5}",
                    BotLabel, position.TradeType, position.Label, oldSl, newSl, result.Error);
            else
                // Sửa thành công → in log xác nhận (SL cũ → SL mới, giá trị SMA20)
                Print("[{0}] SMA20 TRAIL | {1} | Label: {2} | SL: {3} -> {4} | SMA20: {5}",
                    BotLabel, position.TradeType, position.Label, oldSl, newSl,
                    sma20Value.ToString("F" + Symbol.Digits, CultureInfo.InvariantCulture));
        }
    }

    // ============================================================
    // LoadSignals — Đọc file CSV và nạp tất cả tín hiệu vào bộ nhớ
    // Chỉ chạy 1 lần khi bot khởi động
    // ============================================================
    private void LoadSignals()
    {
        // Xóa toàn bộ tín hiệu cũ (nếu có từ lần load trước)
        _signals.Clear();

        // Kiểm tra file CSV có tồn tại trên ổ đĩa không
        if (!System.IO.File.Exists(CsvPath))
        {
            Print("[{0}] ERROR: CSV file not found: {1}", BotLabel, CsvPath);
            return;  // File không tồn tại → thoát, không làm gì
        }

        // Đọc toàn bộ file CSV vào mảng lines (mỗi phần tử = 1 dòng)
        var lines = System.IO.File.ReadAllLines(CsvPath);

        // Nếu file trống → thoát
        if (lines.Length == 0)
            return;

        // Đọc dòng đầu tiên, tách theo dấu phẩy → mảng các ô header
        var header    = SplitCsvLine(lines[0]);

        // Kiểm tra xem dòng đầu tiên có phải là header không
        // (header hợp lệ phải chứa từ "bartime" trong một ô nào đó)
        var hasHeader = header.Any(cell => cell.Equals("bartime", StringComparison.OrdinalIgnoreCase));

        // Tìm vị trí cột (index) của từng trường dữ liệu trong header
        // Nếu có header: tìm theo tên cột (không phân biệt hoa/thường)
        // Nếu không có header: dùng vị trí mặc định (time=0, side=1, atr=2)
        var timeIndex   = hasHeader ? FindColumn(header, "bartime", "time", "date") : 0;
        var symbolIndex = hasHeader ? FindColumn(header, "symbol", "symbolname")     : -1;  // -1 = không có cột symbol
        var sideIndex   = hasHeader ? FindColumn(header, "side", "signal", "direction") : 1;
        var atrIndex    = hasHeader ? FindColumn(header, "atr")                      : 2;

        // Nếu thiếu một trong 3 cột bắt buộc (time, side, atr) → báo lỗi và thoát
        if (timeIndex < 0 || sideIndex < 0 || atrIndex < 0)
        {
            Print("[{0}] ERROR: CSV header must include bartime, side and atr.", BotLabel);
            return;
        }

        // Bộ đếm để báo cáo kết quả sau khi load xong
        var loaded    = 0;   // Số dòng load thành công
        var skipped   = 0;   // Số dòng bỏ qua vì lỗi format

        // Xác định dòng bắt đầu đọc dữ liệu (bỏ qua dòng header nếu có)
        var startLine = hasHeader ? 1 : 0;

        // Duyệt từng dòng dữ liệu trong file CSV
        for (var i = startLine; i < lines.Length; i++)
        {
            var rawLine = lines[i];  // Lấy dòng thứ i

            // Bỏ qua dòng trống hoặc chỉ có khoảng trắng
            if (string.IsNullOrWhiteSpace(rawLine))
                continue;

            // Tách dòng thành các ô theo dấu phẩy
            var parts         = SplitCsvLine(rawLine);

            // Tìm index lớn nhất cần thiết để đảm bảo dòng có đủ cột
            var requiredIndex = Math.Max(timeIndex, Math.Max(sideIndex, atrIndex));

            // Nếu dòng này không đủ số cột cần thiết → bỏ qua
            if (parts.Length <= requiredIndex)
            {
                skipped++;
                continue;
            }

            // Nếu file có cột symbol, kiểm tra symbol trong dòng này có khớp với symbol đang chạy không
            // Nếu không khớp → bỏ qua dòng này (CSV có thể chứa tín hiệu cho nhiều symbol)
            if (symbolIndex >= 0)
            {
                if (parts.Length <= symbolIndex || !IsCurrentSymbol(parts[symbolIndex]))
                    continue;
            }

            // Phân tích 3 giá trị: time, side, atr → tạo đối tượng SignalInfo
            // Nếu phân tích thất bại (format sai) → bỏ qua dòng này
            if (!TryParseSignal(parts[timeIndex], parts[sideIndex], parts[atrIndex], out var signal))
            {
                skipped++;
                continue;
            }

            // Lưu tín hiệu vào Dictionary, key là thời gian bar
            // Nếu có 2 tín hiệu cùng thời gian → cái sau ghi đè cái trước
            _signals[signal.Time] = signal;
            loaded++;
        }

        // In kết quả sau khi load xong
        Print("[{0}] CSV loaded: {1} signals | skipped: {2} | symbol: {3} | offset: {4}h | {5}",
            BotLabel, loaded, skipped, SymbolName, CsvTimeOffsetHours, CsvPath);
    }

    // ============================================================
    // TryParseSignal — Phân tích một dòng CSV thành đối tượng SignalInfo
    // Trả về true nếu phân tích thành công, false nếu có lỗi format
    // ============================================================
    private bool TryParseSignal(string timeText, string sideText, string atrText, out SignalInfo signal)
    {
        // Khởi tạo signal = rỗng (sẽ được gán giá trị nếu thành công)
        signal = default;

        // Phân tích chuỗi thời gian (cắt khoảng trắng trước khi parse)
        // Nếu không phân tích được thời gian → trả về false
        if (!TryParseTime(timeText.Trim(), out var sourceTime))
            return false;

        // Chuẩn hóa giá trị side về chữ hoa để dễ so sánh
        var side = sideText.Trim().ToUpperInvariant();

        // Chấp nhận nhiều cách viết cho BUY: "1", "BUY", "LONG" → đều thành "BUY"
        if      (side is "1" or "BUY"  or "LONG")  side = "BUY";
        // Chấp nhận nhiều cách viết cho SELL: "-1", "SELL", "SHORT" → đều thành "SELL"
        else if (side is "-1" or "SELL" or "SHORT") side = "SELL";
        // Giá trị không nhận ra → trả về false
        else    return false;

        // Phân tích giá trị ATR (số thực, dùng dấu chấm thập phân chuẩn quốc tế)
        // ATR phải > 0 (ATR âm hoặc bằng 0 là vô nghĩa)
        if (!double.TryParse(atrText.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var atr) || atr <= 0)
            return false;

        // Tạo đối tượng SignalInfo với:
        //   - Thời gian đã làm tròn đến phút và cộng thêm offset giờ nếu có
        //   - Side: "BUY" hoặc "SELL"
        //   - ATR: giá trị ATR tại bar đó
        signal = new SignalInfo(TrimToMinute(sourceTime.AddHours(CsvTimeOffsetHours)), side, atr);
        return true;
    }

    // ============================================================
    // TryParseTime — Phân tích chuỗi thời gian từ CSV
    // Hỗ trợ 2 định dạng: có giây ("2024-01-15 08:00:00") và không giây ("2024-01-15 08:00")
    // ============================================================
    private bool TryParseTime(string text, out DateTime time)
    {
        // Thử định dạng đầy đủ có giây trước
        // Nếu không khớp → thử định dạng không có giây
        return DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out time)
            || DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm",    CultureInfo.InvariantCulture, DateTimeStyles.None, out time);
    }

    // ============================================================
    // PlaceSignalOrder — Tính toán và đặt cụm lệnh stop order cho một tín hiệu
    // Đây là hàm quan trọng nhất: tính Entry, SL, TP và đặt lệnh thực tế
    // ============================================================
    private void PlaceSignalOrder(SignalInfo signal, DateTime barTime, DateTime nextBarTime,
        double signalBarClockHours, int signalsInBar)
    {
        // Lấy giá cao nhất (High) của bar tín hiệu vừa đóng
        var high  = Bars.HighPrices.Last(1);

        // Lấy giá thấp nhất (Low) của bar tín hiệu vừa đóng
        var low   = Bars.LowPrices.Last(1);

        // Xác định có phải lệnh MUA không (true = BUY, false = SELL)
        var isBuy = signal.Side == "BUY";

        // Xác định TradeType (Buy hoặc Sell) cho lệnh sẽ đặt
        var tradeType    = isBuy ? TradeType.Buy : TradeType.Sell;

        // Lấy hệ số KTP từ bảng Fibonacci theo level đã chọn
        var ktpMultiplier = GetKtpMultiplier(KtpFibLevel);

        // ── TÍNH GIÁ ENTRY ──────────────────────────────────────────────────
        // BUY:  Entry = High của bar tín hiệu + X  → đặt lệnh breakout phía TRÊN bar
        // SELL: Entry = Low  của bar tín hiệu - X  → đặt lệnh breakout phía DƯỚI bar
        // Lý do: chờ giá phá vỡ vùng đỉnh/đáy bar để xác nhận xu hướng
        var entryPrice = isBuy ? high + XOffset : low - XOffset;

        // ── TÍNH GIÁ STOP LOSS ──────────────────────────────────────────────
        // BUY:  SL = Low  của bar tín hiệu - X  → bên DƯỚI toàn bộ bar + buffer X
        // SELL: SL = High của bar tín hiệu + X  → bên TRÊN toàn bộ bar + buffer X
        // Lý do: SL được đặt bên ngoài cấu trúc cây nến, không dễ bị quét bởi noise
        var slPrice = isBuy ? low - XOffset : high + XOffset;

        // ── TÍNH KHOẢNG CÁCH SL ─────────────────────────────────────────────
        // slDist = |Entry - SL| = (High - Low) + 2×X
        // (khoảng cách từ entry đến SL = toàn bộ thân nến + X cả 2 phía)
        var slDist = Math.Abs(entryPrice - slPrice);   // = (High - Low) + 2 * X

        // Chuyển khoảng cách SL từ đơn vị giá sang pips (để tính volume)
        var slPips = slDist / Symbol.PipSize;

        // ── TÍNH GIÁ TAKE PROFIT (Leg 1) ────────────────────────────────────
        // TP distance = KTP × ATR (Fibonacci extension theo ATR của bar tín hiệu)
        var tpDist   = ktpMultiplier * signal.Atr;

        // BUY:  TP1 = Entry + tpDist  → phía TRÊN entry
        // SELL: TP1 = Entry - tpDist  → phía DƯỚI entry
        var tpPrice1 = isBuy ? entryPrice + tpDist : entryPrice - tpDist;

        // Chuyển khoảng cách TP sang pips (để log và tính RR)
        var tpPips1  = tpDist / Symbol.PipSize;

        // ── TÍNH SPREAD VÀ BỘ LỌC SPREAD ───────────────────────────────────
        // Tính spread hiện tại (Ask - Bid) đổi sang pips
        var spreadPips          = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;

        // Spread chiếm bao nhiêu % của khoảng cách SL
        var spreadToSlPercent   = slPips  > 0 ? spreadPips / slPips  * 100.0 : 0;

        // Spread chiếm bao nhiêu % của khoảng cách TP (chỉ để log, không dùng để lọc)
        var spreadToTpPercent   = tpPips1 > 0 ? spreadPips / tpPips1 * 100.0 : 0;

        // Kiểm tra bộ lọc spread: nếu spread quá lớn so với SL → bỏ qua lệnh
        // Spread lớn = chi phí giao dịch cao → tỷ lệ RR thực tế bị xấu đi đáng kể
        if (MaxSpreadToStopLossPercent > 0 && spreadToSlPercent > MaxSpreadToStopLossPercent)
        {
            _skipSpreadFilterCount++;
            Print("[{0}] {1} at {2:yyyy-MM-dd HH:mm} skipped: spread {3:F1}p is {4:F2}% of SL ({5:F2}%), above limit {6:F2}%.",
                BotLabel, signal.Side, barTime,
                spreadPips, spreadToSlPercent, slPips, MaxSpreadToStopLossPercent);
            return;  // Thoát, không đặt lệnh
        }

        // ── TÍNH KHỐI LƯỢNG GIAO DỊCH ───────────────────────────────────────
        // Số leg: FixedOnly = 1 leg, TwoLegs = 2 legs
        var legCount      = ExitMode == ExitModeOption.TwoLegs ? 2 : 1;

        // Risk cho mỗi leg = tổng risk / số leg
        // Ví dụ: Risk=1%, TwoLegs → mỗi leg risk 0.5%
        var legRiskPercent = RiskPercent / legCount;

        // Tính volume cho mỗi leg dựa trên: khoảng cách SL + % risk leg
        // Công thức: volume = (Balance × legRisk%) / (slPips × pip value)
        var legVolume     = CalculateRiskVolume(slPips, legRiskPercent);

        // Nếu volume tính ra nhỏ hơn mức tối thiểu của broker → không thể đặt lệnh
        // (thường xảy ra khi tài khoản quá nhỏ hoặc SL quá rộng)
        if (legVolume < Symbol.VolumeInUnitsMin)
        {
            _skipVolumeCount++;
            Print("[{0}] ERROR: {1}% risk / {2} leg(s) = {3:F3}% leg risk → volume {4} below minimum {5}. SL: {6:F1}p.",
                BotLabel, RiskPercent, legCount, legRiskPercent, legVolume, Symbol.VolumeInUnitsMin, slPips);
            return;  // Thoát, không đặt lệnh
        }

        // Bộ đếm số leg đã đặt thành công trong cụm này
        var placedCount = 0;

        // ── ĐẶT LEG 1: có TP cố định ────────────────────────────────────────
        // Leg 1 luôn có TP cố định = Entry ± KTP × ATR
        if (PlaceOneLeg(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar,
            tradeType, 1, legCount, legVolume, legRiskPercent,
            entryPrice, slPrice, tpPrice1, high, low, ktpMultiplier))
            placedCount++;  // Nếu đặt thành công → tăng bộ đếm

        // ── ĐẶT LEG 2: chỉ khi ExitMode = TwoLegs, không có TP ─────────────
        // Leg 2 KHÔNG có TP cố định (truyền null), SL được trail theo SMA20 mỗi bar
        if (ExitMode == ExitModeOption.TwoLegs)
        {
            if (PlaceOneLeg(signal, barTime, nextBarTime, signalBarClockHours, signalsInBar,
                tradeType, 2, legCount, legVolume, legRiskPercent,
                entryPrice, slPrice, null, high, low, ktpMultiplier))  // null = không có TP
                placedCount++;
        }

        // Xác định trạng thái cụm lệnh: COMPLETE = đủ leg, PARTIAL = thiếu leg
        var summary  = placedCount == legCount ? "SIGNAL CLUSTER COMPLETE" : "SIGNAL CLUSTER PARTIAL";

        // Tính tỷ lệ Risk:Reward (RR) = khoảng cách TP / khoảng cách SL
        var rr       = slPips > 0 ? tpPips1 / slPips : 0;

        // In tóm tắt toàn bộ thông tin cụm lệnh vừa đặt
        Print("[{0}] {1} | {2} at {3:yyyy-MM-dd HH:mm} | Mode: {4} | KTP: {5:F3} (L{6}) | Legs: {7}/{8} | TotalRisk: {9:F1}% | LegRisk: {10:F3}% | X: {11} | Entry: {12} | SL: {13} ({14:F1}p) | TP1: {15} ({16:F1}p) | RR: {17:F2} | ATR: {18:F4} | High: {19} | Low: {20} | Spread/SL: {21:F2}% | Spread/TP: {22:F2}%",
            BotLabel, summary, signal.Side, barTime,
            ExitMode, ktpMultiplier, KtpFibLevel,
            placedCount, legCount,
            RiskPercent, legRiskPercent,
            XOffset, entryPrice, slPrice, slPips,
            tpPrice1, tpPips1, rr,
            signal.Atr, high, low,
            spreadToSlPercent, spreadToTpPercent);
    }

    // ============================================================
    // PlaceOneLeg — Đặt một stop order cho 1 leg cụ thể
    // Trả về true nếu đặt thành công, false nếu thất bại
    // ============================================================
    private bool PlaceOneLeg(SignalInfo signal, DateTime barTime, DateTime nextBarTime,
        double signalBarClockHours, int signalsInBar,
        TradeType tradeType, int legNumber, int legCount,
        double volume, double legRiskPercent,
        double entryPrice, double slPrice, double? tpPrice,
        double high, double low, double ktpMultiplier)
    {
        // Tính lại khoảng cách SL bằng pips cho lệnh này
        var slPips = Math.Abs(entryPrice - slPrice) / Symbol.PipSize;

        // Tính khoảng cách TP bằng pips (nếu có TP; Leg 2 TwoLegs không có TP = null)
        double? tpPips = tpPrice.HasValue
            ? (double?)(Math.Abs(tpPrice.Value - entryPrice) / Symbol.PipSize)
            : null;

        // ── GỬI LỆNH STOP ORDER ĐẾN BROKER ─────────────────────────────────
        // PlaceStopOrder = đặt lệnh chờ dạng breakout (Buy Stop hoặc Sell Stop)
        // Tham số: chiều lệnh, symbol, volume, giá entry, label nhận diện,
        //          SL (pips), TP (pips hoặc null), kiểu bảo vệ (Relative = pips)
        var result = PlaceStopOrder(
            tradeType,               // Buy hoặc Sell
            SymbolName,              // Symbol đang chạy
            volume,                  // Khối lượng đã tính
            entryPrice,              // Giá kích hoạt lệnh
            GetLegLabel(legNumber),  // Nhãn: "SEN_Combo_V0_L1" hoặc "_L2"
            slPips,                  // Khoảng cách SL tính bằng pips (tương đối so với entry)
            tpPips,                  // Khoảng cách TP tính bằng pips (null = không có TP)
            ProtectionType.Relative);// SL/TP tính tương đối từ entry (pips), không phải giá tuyệt đối

        // Nếu broker trả về lỗi → tăng bộ đếm lỗi và trả về false
        if (!result.IsSuccessful)
        {
            _placeOrderErrorCount++;
            Print("[{0}] ERROR placing {1} leg {2} stop at {3}: {4}",
                BotLabel, signal.Side, legNumber, entryPrice, result.Error);
            return false;
        }

        // Lệnh đặt thành công → tăng bộ đếm tổng số lệnh đặt
        _stopOrdersPlacedCount++;

        // Ghi nhớ số bar hiện tại tại thời điểm đặt lệnh
        // Dùng để tính tuổi lệnh sau này → biết khi nào cần hủy vì hết hạn
        if (result.PendingOrder != null)
            _pendingOrderCreatedBarCounts[result.PendingOrder.Id] = Bars.Count;

        // Xác định loại leg để hiển thị trong log: "FIXED" hoặc "TRAIL"
        var legType  = legNumber == 2 && ExitMode == ExitModeOption.TwoLegs ? "TRAIL" : "FIXED";

        // Tạo chuỗi mô tả TP để in log
        // Leg có TP → in giá và pips; Leg không có TP → in "none (SMA20 trailing)"
        var tpLabel  = tpPrice.HasValue
            ? tpPrice.Value.ToString(CultureInfo.InvariantCulture) + " (" + (tpPips ?? 0).ToString("F1", CultureInfo.InvariantCulture) + "p)"
            : "none (SMA20 trailing)";

        // In đầy đủ thông tin của lệnh vừa đặt
        Print("[{0}] {1} STOP leg {2}/{3} [{4}] | Bar: {5:yyyy-MM-dd HH:mm}->{6:yyyy-MM-dd HH:mm} ({7:F2}h) | SigTime: {8:yyyy-MM-dd HH:mm} | SigInBar: {9} | Entry: {10} | SL: {11} ({12:F1}p) | TP: {13} | KTP: {14:F3} | ATR: {15:F4} | High: {16} | Low: {17} | X: {18} | LegRisk: {19:F3}% | Lots: {20:F2} | PipSize: {21}",
            BotLabel, signal.Side,
            legNumber, legCount, legType,
            barTime, nextBarTime, signalBarClockHours,
            signal.Time, signalsInBar,
            entryPrice, slPrice, slPips,
            tpLabel, ktpMultiplier,
            signal.Atr, high, low, XOffset,
            legRiskPercent,
            Symbol.VolumeInUnitsToQuantity(volume),   // Chuyển volume đơn vị sang lots để dễ đọc
            Symbol.PipSize);

        return true;  // Đặt lệnh thành công
    }

    // ============================================================
    // CalculateRiskVolume — Tính khối lượng giao dịch dựa trên % rủi ro
    // Công thức: volume = (Balance × risk%) / (slPips × pip value)
    // ============================================================
    private double CalculateRiskVolume(double stopLossPips, double riskPercent)
    {
        // SL không hợp lệ (= 0 hoặc âm) → trả về 0, không thể tính volume
        if (stopLossPips <= 0)
            return 0;

        // Số tiền tuyệt đối chấp nhận rủi ro = Balance × risk%
        // Ví dụ: Balance=10000, riskPercent=1% → riskAmount=100 USD
        var riskAmount = Account.Balance * riskPercent / 100.0;

        // Yêu cầu cTrader tính volume phù hợp để nếu hit SL thì lỗ đúng riskAmount
        // RoundingMode.Down = làm tròn xuống để không vượt quá % risk đã định
        var volume     = Symbol.VolumeForFixedRisk(riskAmount, stopLossPips, RoundingMode.Down);

        // Chuẩn hóa volume theo quy tắc của broker (bước nhảy volume tối thiểu)
        // Ví dụ: volume=0.073 → chuẩn hóa xuống 0.07 nếu bước nhảy là 0.01
        return Symbol.NormalizeVolumeInUnits(volume, RoundingMode.Down);
    }

    // ============================================================
    // GetKtpMultiplier — Tra bảng Fibonacci để lấy hệ số KTP theo level
    // ============================================================
    private static double GetKtpMultiplier(int level) => level switch
    {
        1 => 1.272,   // Fibonacci 127.2%
        2 => 1.618,   // Tỷ lệ vàng
        3 => 2.000,   // 200%
        4 => 2.272,   // ≈ 2.3 — mặc định theo giáo trình Combo
        5 => 2.618,   // Fibonacci 261.8%
        6 => 3.618,   // Fibonacci 361.8%
        _ => 2.272,   // Fallback nếu level không hợp lệ → dùng level 4
    };

    // ============================================================
    // GetLegLabel — Tạo nhãn cho từng leg
    // Ví dụ: BotLabel="SEN_Combo_V0", legNumber=1 → "SEN_Combo_V0_L1"
    // ============================================================
    private string GetLegLabel(int legNumber) => $"{BotLabel}_L{legNumber}";

    // ============================================================
    // IsMyBotLabel — Kiểm tra xem một label có thuộc bot này không
    // Trả về true nếu label = BotLabel hoặc bắt đầu bằng "BotLabel_L"
    // ============================================================
    private bool IsMyBotLabel(string label)
    {
        // Label rỗng → không phải của bot này
        if (string.IsNullOrWhiteSpace(label))
            return false;

        // Khớp chính xác với BotLabel (ví dụ "SEN_Combo_V0")
        // HOẶC bắt đầu bằng "SEN_Combo_V0_L" (ví dụ "SEN_Combo_V0_L1", "SEN_Combo_V0_L2")
        return label == BotLabel || label.StartsWith($"{BotLabel}_L", StringComparison.Ordinal);
    }

    // ============================================================
    // IsTrailingLeg — Kiểm tra xem position này có phải là Leg 2 (leg trailing) không
    // Leg 2 có label dạng "SEN_Combo_V0_L2" — đây là leg được trail SL theo SMA20
    // ============================================================
    private bool IsTrailingLeg(string label) => label == GetLegLabel(2);

    // ============================================================
    // GetLegNumber — Trích xuất số leg từ label
    // Ví dụ: "SEN_Combo_V0_L1" → trả về 1, "SEN_Combo_V0_L2" → trả về 2
    // Trả về 0 nếu label không đúng format
    // ============================================================
    private int GetLegNumber(string label)
    {
        // Tiền tố cần tìm: "SEN_Combo_V0_L"
        var prefix = $"{BotLabel}_L";

        // Nếu label không bắt đầu bằng tiền tố → không phải leg của bot này → trả về 0
        if (!label.StartsWith(prefix, StringComparison.Ordinal))
            return 0;

        // Lấy phần chữ số sau tiền tố và chuyển thành số nguyên
        // label[prefix.Length..] = chuỗi từ vị trí sau tiền tố đến hết (ví dụ "1" hoặc "2")
        return int.TryParse(label[prefix.Length..], NumberStyles.Integer, CultureInfo.InvariantCulture, out var n)
            ? n : 0;
    }

    // ============================================================
    // ShouldImproveStopLoss — Kiểm tra SL mới có tốt hơn SL cũ không
    // "Tốt hơn" = dịch về phía giảm rủi ro (chỉ được cải thiện, không được nới rộng)
    // ============================================================
    private static bool ShouldImproveStopLoss(Position position, double newSl)
    {
        // Nếu position chưa có SL → bất kỳ SL nào cũng là cải thiện
        if (!position.StopLoss.HasValue)
            return true;

        // BUY: SL cải thiện = SL mới LỚN HƠN SL cũ (dịch lên trên, gần entry hơn)
        // SELL: SL cải thiện = SL mới NHỎ HƠN SL cũ (dịch xuống dưới, gần entry hơn)
        return position.TradeType == TradeType.Buy
            ? newSl > position.StopLoss.Value
            : newSl < position.StopLoss.Value;
    }

    // ============================================================
    // GetTradeType — Chuyển đổi từ signal.Side thành TradeType của cTrader
    // "BUY" → TradeType.Buy  |  "SELL" → TradeType.Sell
    // ============================================================
    private TradeType GetTradeType(SignalInfo signal) =>
        signal.Side == "BUY" ? TradeType.Buy : TradeType.Sell;

    // ============================================================
    // GetOppositeTradeType — Trả về chiều ngược lại
    // Buy → Sell  |  Sell → Buy
    // ============================================================
    private static TradeType GetOppositeTradeType(TradeType t) =>
        t == TradeType.Buy ? TradeType.Sell : TradeType.Buy;

    // ============================================================
    // GetMyPendingOrders — Lấy danh sách tất cả pending orders thuộc bot này
    // Lọc theo: label đúng VÀ đúng symbol đang chạy
    // ============================================================
    private PendingOrder[] GetMyPendingOrders() =>
        PendingOrders.Where(o => IsMyBotLabel(o.Label) && o.SymbolName == SymbolName).ToArray();

    // ============================================================
    // GetMyPositions — Lấy danh sách tất cả positions đang mở thuộc bot này
    // Lọc theo: label đúng VÀ đúng symbol đang chạy
    // ============================================================
    private Position[] GetMyPositions() =>
        Positions.Where(p => IsMyBotLabel(p.Label) && p.SymbolName == SymbolName).ToArray();

    // ============================================================
    // GetMyOpenOrderCount — Đếm tổng số lệnh (positions + pending) cùng chiều t
    // Dùng để kiểm tra "đã có exposure cùng chiều chưa"
    // ============================================================
    private int GetMyOpenOrderCount(TradeType t) =>
        GetMyPositions().Count(p => p.TradeType == t)           // Đếm positions đang mở cùng chiều
        + GetMyPendingOrders().Count(o => o.TradeType == t);    // Đếm pending orders cùng chiều

    // ============================================================
    // CancelMyPendingOrders — Hủy TẤT CẢ pending orders của bot này
    // Được gọi khi drawdown hàng ngày bị kích hoạt → dọn sạch toàn bộ
    // ============================================================
    private void CancelMyPendingOrders()
    {
        // Duyệt qua toàn bộ pending orders của bot này
        foreach (var order in GetMyPendingOrders())
        {
            // Gửi lệnh hủy đến broker
            var r = CancelPendingOrder(order);

            // Nếu hủy thất bại → in lỗi
            if (!r.IsSuccessful)
                Print("[{0}] ERROR cancelling pending {1}: {2}", BotLabel, order.Id, r.Error);
            else
                // Hủy thành công → xóa khỏi bảng theo dõi tuổi lệnh (không cần track nữa)
                _pendingOrderCreatedBarCounts.Remove(order.Id);
        }
    }

    // ============================================================
    // CancelOppositePendingOrders — Hủy các pending orders NGƯỢC chiều với tín hiệu mới
    // Được gọi khi có tín hiệu đảo chiều: tín hiệu BUY → hủy Sell Stop orders
    // ============================================================
    private void CancelOppositePendingOrders(TradeType tradeType)
    {
        // Xác định chiều ngược lại (nếu tín hiệu = Buy thì ngược = Sell)
        var opp = GetOppositeTradeType(tradeType);

        // Lọc và hủy từng pending order ngược chiều
        foreach (var order in GetMyPendingOrders().Where(o => o.TradeType == opp))
        {
            var r = CancelPendingOrder(order);
            if (!r.IsSuccessful)
                Print("[{0}] ERROR cancelling opposite pending {1}: {2}", BotLabel, order.Id, r.Error);
            else
                // Hủy thành công → xóa khỏi bảng theo dõi tuổi lệnh
                _pendingOrderCreatedBarCounts.Remove(order.Id);
        }
    }

    // ============================================================
    // CancelExpiredPendingOrders — Hủy pending orders đã tồn tại quá số bar cho phép
    // Được gọi mỗi khi bar đóng
    // ============================================================
    private void CancelExpiredPendingOrders()
    {
        // Duyệt qua tất cả pending orders của bot
        foreach (var order in GetMyPendingOrders())
        {
            // Nếu order này chưa có trong bảng theo dõi (ví dụ bot restart giữa chừng)
            // → ghi nhận bar hiện tại làm thời điểm tạo, bỏ qua lần này
            if (!_pendingOrderCreatedBarCounts.TryGetValue(order.Id, out var createdAt))
            {
                _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
                continue;
            }

            // Tính tuổi của lệnh = số bar hiện tại - số bar lúc đặt lệnh
            var age = Bars.Count - createdAt;

            // Nếu chưa đủ tuổi → bỏ qua, không hủy
            if (age < CancelPendingAfterBars)
                continue;

            // Đã đủ tuổi → hủy lệnh
            var r = CancelPendingOrder(order);

            // Nếu hủy thất bại → in lỗi và tiếp tục vòng lặp
            if (!r.IsSuccessful)
            {
                Print("[{0}] ERROR cancelling expired pending {1}: {2}", BotLabel, order.Id, r.Error);
                continue;
            }

            // Hủy thành công → dọn dẹp, tăng bộ đếm, in log
            _pendingOrderCreatedBarCounts.Remove(order.Id);
            _pendingExpiredCount++;
            Print("[{0}] PENDING EXPIRED {1} | Order: {2} | Age: {3}/{4} bars",
                BotLabel, order.TradeType, order.Id, age, CancelPendingAfterBars);
        }
    }

    // ============================================================
    // CloseOppositePositions — Đóng tất cả positions NGƯỢC chiều với tín hiệu mới
    // Trả về true nếu tất cả đóng thành công (hoặc không có gì cần đóng)
    // Trả về false nếu có bất kỳ lệnh đóng nào thất bại
    // ============================================================
    private bool CloseOppositePositions(TradeType tradeType)
    {
        // Xác định chiều ngược lại
        var opp       = GetOppositeTradeType(tradeType);

        // Lọc lấy danh sách positions ngược chiều
        var positions = GetMyPositions().Where(p => p.TradeType == opp).ToArray();

        // Nếu không có position ngược chiều nào → trả về true (thành công)
        if (positions.Length == 0)
            return true;

        // Có positions ngược chiều → in thông báo đảo chiều
        Print("[{0}] REVERSE SIGNAL {1}: closing {2} opposite position(s).",
            BotLabel, tradeType, positions.Length);

        // Cờ theo dõi xem tất cả đóng có thành công không
        var allClosed = true;

        // Duyệt và đóng từng position ngược chiều
        foreach (var p in positions)
        {
            var r = ClosePosition(p);

            // Nếu đóng thất bại → đánh dấu allClosed = false, in lỗi
            if (!r.IsSuccessful)
            {
                allClosed = false;
                Print("[{0}] ERROR closing opposite position {1}: {2}", BotLabel, p.Id, r.Error);
            }
        }

        // Trả về kết quả: true nếu tất cả đóng thành công
        return allClosed;
    }

    // ============================================================
    // CloseMyPositions — Đóng TẤT CẢ positions của bot này
    // Được gọi khi drawdown hàng ngày bị kích hoạt → dọn sạch toàn bộ
    // ============================================================
    private void CloseMyPositions()
    {
        foreach (var p in GetMyPositions())
        {
            var r = ClosePosition(p);
            if (!r.IsSuccessful)
                Print("[{0}] ERROR closing position {1}: {2}", BotLabel, p.Id, r.Error);
        }
    }

    // ============================================================
    // TrackExistingPendingOrders — Ghi nhớ các pending orders đang tồn tại khi bot khởi động
    // Cần thiết khi bot bị restart giữa chừng: tránh tính sai tuổi lệnh
    // ============================================================
    private void TrackExistingPendingOrders()
    {
        // Duyệt tất cả pending orders hiện có của bot → ghi bar hiện tại làm "ngày sinh"
        // Lưu ý: đây là ước tính, không biết chính xác bao giờ lệnh đó được đặt
        foreach (var order in GetMyPendingOrders())
            _pendingOrderCreatedBarCounts[order.Id] = Bars.Count;
    }

    // ============================================================
    // ResetDailyDrawdownBaseline — Thiết lập lại mốc drawdown cho ngày mới
    // Được gọi: lúc bot khởi động VÀ mỗi khi sang ngày mới
    // ============================================================
    private void ResetDailyDrawdownBaseline()
    {
        // Lưu ngày hôm nay để phát hiện khi nào sang ngày mới
        _drawdownDay           = Server.Time.Date;

        // Lưu equity tại thời điểm này làm mốc baseline để tính % drawdown
        // Dùng Equity (không phải Balance) vì Equity bao gồm cả lãi/lỗ đang nổi (floating)
        _dayStartEquity        = Account.Equity;

        // Reset cờ: ngày mới = chưa bị kích hoạt drawdown
        _dailyDrawdownTriggered = false;

        // In thông tin baseline để kiểm tra
        Print("[{0}] DD baseline reset | Day: {1:yyyy-MM-dd} | StartEquity: {2:F2} | Balance: {3:F2} | Limit: {4:F1}%",
            BotLabel, _drawdownDay, _dayStartEquity, Account.Balance, MaxDailyDrawdownPercent);
    }

    // ============================================================
    // CheckDailyDrawdownLimit — Kiểm tra xem drawdown hàng ngày có vượt giới hạn không
    // Trả về true nếu đã vượt giới hạn (và đã xử lý), false nếu bình thường
    // Được gọi cả trong OnTick (real-time) và OnBarClosed
    // ============================================================
    private bool CheckDailyDrawdownLimit()
    {
        // Nếu ngày hiện tại khác ngày đã lưu → sang ngày mới → reset baseline
        if (Server.Time.Date != _drawdownDay)
            ResetDailyDrawdownBaseline();

        // Nếu baseline = 0 (không hợp lệ) → bỏ qua, trả về false (không block giao dịch)
        if (_dayStartEquity <= 0)
            return false;

        // Tính % drawdown hiện tại = (equity đầu ngày - equity hiện tại) / equity đầu ngày × 100
        // dd > 0 = đang lỗ; dd < 0 = đang lãi (equity tăng so với đầu ngày)
        var dd = (_dayStartEquity - Account.Equity) / _dayStartEquity * 100.0;

        // Nếu drawdown CHƯA vượt ngưỡng VÀ chưa từng bị kích hoạt hôm nay → trả về false (OK)
        if (dd < MaxDailyDrawdownPercent && !_dailyDrawdownTriggered)
            return false;

        // Drawdown vừa chạm ngưỡng LẦN ĐẦU HÔM NAY → in cảnh báo và đánh dấu đã kích hoạt
        if (!_dailyDrawdownTriggered)
        {
            _dailyDrawdownTriggered = true;
            Print("[{0}] DAILY DD LIMIT HIT | DD: {1:F2}% | Equity: {2:F2} | StartEquity: {3:F2}. Halted until next day.",
                BotLabel, dd, Account.Equity, _dayStartEquity);
        }

        // Dù là lần đầu hay đã kích hoạt rồi, mỗi lần gọi đều phải:
        // 1. Hủy tất cả pending orders (không để lệnh mới fill vào)
        CancelMyPendingOrders();

        // 2. Đóng tất cả positions đang mở (cắt lỗ ngay)
        CloseMyPositions();

        // Trả về true để báo hiệu: đang trong trạng thái drawdown, không làm gì thêm
        return true;
    }

    // ============================================================
    // OnPendingOrderFilled — Sự kiện: một pending order vừa được FILL (kích hoạt)
    // Được gọi tự động bởi cTrader khi broker báo lệnh chờ đã vào thị trường
    // ============================================================
    private void OnPendingOrderFilled(PendingOrderFilledEventArgs args)
    {
        // Lấy thông tin position vừa được tạo từ pending order đó
        var pos = args.Position;

        // Chỉ xử lý nếu position này thuộc bot này VÀ đúng symbol đang chạy
        // (cùng tài khoản có thể có nhiều bot hoặc lệnh thủ công)
        if (!IsMyBotLabel(pos.Label) || pos.SymbolName != SymbolName)
            return;

        // Xóa order ID này khỏi bảng theo dõi tuổi lệnh (đã fill rồi, không cần hủy hết hạn nữa)
        _pendingOrderCreatedBarCounts.Remove(args.PendingOrder.Id);

        // Tăng bộ đếm số lệnh đã fill
        _pendingFilledCount++;

        // In thông tin lệnh vừa được fill để kiểm tra
        Print("[{0}] FILLED {1} | Label: {2} | Leg: {3} | Entry: {4} | SL: {5} | TP: {6}",
            BotLabel, pos.TradeType, pos.Label, GetLegNumber(pos.Label),
            pos.EntryPrice, pos.StopLoss, pos.TakeProfit);
    }

    // ============================================================
    // OnPendingOrderCancelled — Sự kiện: một pending order vừa bị HỦY
    // Được gọi khi bot tự hủy HOẶC broker hủy (ví dụ: hết margin, symbol bị đóng cửa)
    // ============================================================
    private void OnPendingOrderCancelled(PendingOrderCancelledEventArgs args)
    {
        var order = args.PendingOrder;  // Lấy thông tin lệnh bị hủy

        // Chỉ xử lý nếu lệnh thuộc bot này VÀ đúng symbol
        if (!IsMyBotLabel(order.Label) || order.SymbolName != SymbolName)
            return;

        // Xóa khỏi bảng theo dõi tuổi lệnh
        _pendingOrderCreatedBarCounts.Remove(order.Id);

        // Tăng bộ đếm số lệnh bị hủy
        _pendingCancelledCount++;

        // In thông tin: lệnh nào, chiều nào, lý do hủy là gì
        Print("[{0}] PENDING CANCELLED {1} | Label: {2} | Reason: {3}",
            BotLabel, order.TradeType, order.Label, args.Reason);
    }

    // ============================================================
    // OnPositionClosed — Sự kiện: một position vừa được ĐÓNG
    // Có thể đóng bởi: TP hit, SL hit, bot chủ động đóng, người dùng đóng tay
    // ============================================================
    private void OnPositionClosed(PositionClosedEventArgs args)
    {
        var pos = args.Position;  // Lấy thông tin position vừa đóng

        // Chỉ xử lý nếu position thuộc bot này VÀ đúng symbol
        if (!IsMyBotLabel(pos.Label) || pos.SymbolName != SymbolName)
            return;

        // In kết quả của lệnh vừa đóng:
        // Chiều lệnh, label, số leg, lãi/lỗ ròng (USD), lãi/lỗ (pips), lý do đóng
        Print("[{0}] CLOSED {1} | Label: {2} | Leg: {3} | NetPnL: {4:F2} | Pips: {5:F1} | Reason: {6}",
            BotLabel, pos.TradeType, pos.Label, GetLegNumber(pos.Label),
            pos.NetProfit, pos.Pips, args.Reason);
    }

    // ============================================================
    // TryGetSignalForClosedBar — Tìm tín hiệu trong CSV cho bar vừa đóng
    // Tìm tất cả tín hiệu có thời gian nằm trong [barTime, nextBarTime)
    // Nếu có nhiều tín hiệu trong 1 bar → lấy cái mới nhất (timestamp lớn nhất)
    // ============================================================
    private bool TryGetSignalForClosedBar(DateTime barTime, DateTime nextBarTime,
        out SignalInfo signal, out int signalsInBar)
    {
        // Khởi tạo giá trị mặc định cho output
        signal      = default;
        signalsInBar = 0;

        SignalInfo latest   = default;  // Tín hiệu mới nhất tìm được
        var        hasSignal = false;   // Đã tìm thấy ít nhất 1 tín hiệu chưa

        // Duyệt qua toàn bộ tín hiệu trong bộ nhớ để tìm tín hiệu thuộc bar này
        foreach (var candidate in _signals.Values)
        {
            // Kiểm tra: thời gian tín hiệu phải nằm trong khoảng [barTime, nextBarTime)
            // barTime     = thời gian mở của bar vừa đóng
            // nextBarTime = thời gian mở của bar đang hình thành (= kết thúc của bar vừa đóng)
            if (candidate.Time < barTime || candidate.Time >= nextBarTime)
                continue;  // Tín hiệu này không thuộc bar này → bỏ qua

            // Tăng bộ đếm số tín hiệu trong bar này
            signalsInBar++;

            // Nếu đã có tín hiệu trước đó VÀ cái mới này không mới hơn → giữ cái cũ
            if (hasSignal && candidate.Time <= latest.Time)
                continue;

            // Tín hiệu này mới hơn → cập nhật latest
            latest    = candidate;
            hasSignal = true;
        }

        // Không tìm thấy tín hiệu nào trong bar này → trả về false
        if (!hasSignal)
            return false;

        // Gán tín hiệu tìm được cho output
        signal = latest;

        // Nếu có nhiều tín hiệu trong 1 bar → in cảnh báo (bất thường)
        if (signalsInBar > 1)
            Print("[{0}] Multiple signals in bar | {1:yyyy-MM-dd HH:mm}->{2:yyyy-MM-dd HH:mm} | Count: {3} | Using: {4:yyyy-MM-dd HH:mm} {5}",
                BotLabel, barTime, nextBarTime, signalsInBar, signal.Time, signal.Side);

        return true;  // Tìm thấy tín hiệu
    }

    // ============================================================
    // IsCurrentSymbol — Kiểm tra tên symbol trong CSV có khớp với symbol bot đang chạy không
    // So sánh không phân biệt hoa/thường; thử khớp cả SymbolName lẫn Symbol.Name
    // ============================================================
    private bool IsCurrentSymbol(string csvSymbol) =>
        csvSymbol.Trim().Equals(SymbolName,    StringComparison.OrdinalIgnoreCase)
        || csvSymbol.Trim().Equals(Symbol.Name, StringComparison.OrdinalIgnoreCase);

    // ============================================================
    // FindColumn — Tìm vị trí (index) của một cột trong header CSV
    // Tìm theo danh sách tên có thể, không phân biệt hoa/thường
    // Trả về -1 nếu không tìm thấy
    // ============================================================
    private static int FindColumn(string[] header, params string[] names)
    {
        // Duyệt từng ô trong header
        for (var i = 0; i < header.Length; i++)
        {
            var col = header[i].Trim();  // Tên cột (đã cắt khoảng trắng)

            // Kiểm tra tên cột có khớp với bất kỳ tên nào trong danh sách tìm không
            if (names.Any(n => col.Equals(n, StringComparison.OrdinalIgnoreCase)))
                return i;  // Tìm thấy → trả về index của cột này
        }
        return -1;  // Không tìm thấy
    }

    // ============================================================
    // SplitCsvLine — Tách một dòng CSV thành mảng các ô
    // Tách theo dấu phẩy và cắt khoảng trắng thừa ở đầu/cuối mỗi ô
    // ============================================================
    private static string[] SplitCsvLine(string line) =>
        line.Split(',').Select(p => p.Trim()).ToArray();

    // ============================================================
    // TrimToMinute — Làm tròn DateTime xuống phút (bỏ giây và mili giây)
    // Ví dụ: 2024-01-15 08:34:27 → 2024-01-15 08:34:00
    // Mục đích: đảm bảo key trong Dictionary _signals đồng nhất (không bị lệch giây)
    // ============================================================
    private static DateTime TrimToMinute(DateTime value) =>
        new(value.Year, value.Month, value.Day, value.Hour, value.Minute, 0, value.Kind);

    // ============================================================
    // SignalInfo — Cấu trúc dữ liệu lưu thông tin của 1 tín hiệu từ CSV
    // readonly struct = bất biến sau khi tạo, tiết kiệm bộ nhớ hơn class
    // ============================================================
    private readonly struct SignalInfo
    {
        // Constructor: tạo SignalInfo với 3 trường bắt buộc
        public SignalInfo(DateTime time, string side, double atr)
        {
            Time = time;  // Thời gian bar (đã làm tròn đến phút và cộng offset giờ)
            Side = side;  // Chiều lệnh: "BUY" hoặc "SELL"
            Atr  = atr;   // Giá trị ATR tại bar đó (dùng để tính khoảng cách TP)
        }

        public DateTime Time { get; }  // Thời gian bar tín hiệu
        public string   Side { get; }  // "BUY" hoặc "SELL"
        public double   Atr  { get; }  // ATR của bar tín hiệu (từ CSV)
    }
}
