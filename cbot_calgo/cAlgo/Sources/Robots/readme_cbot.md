# Quy tắc Phát triển cBot — Backtest, Tối ưu hóa & Logic Giao dịch

> **Dành cho AI assistant.**
> Đây là bộ quy tắc dựa trên cách cTrader hoạt động thực tế — không phải lý thuyết chung.
> Đọc và áp dụng toàn bộ trước khi hỗ trợ viết hoặc review bất kỳ cBot nào.
> **Ưu tiên tuyệt đối: bảo toàn vốn trước, lợi nhuận sau.**

---

## 1. Backtester — Cách hoạt động thực tế

### 1.1 Hai chế độ dữ liệu

| Chế độ | Độ chính xác | Tốc độ | Khi nào dùng |
|---|---|---|---|
| **Dữ liệu tick** | Cao nhất — dùng độ chênh lệch giá lịch sử thực tế, `OnTick()` được gọi đúng mỗi tick | Chậm | Kiểm chứng cuối cùng trước khi triển khai |
| **Dữ liệu 1 phút** | Thấp hơn — chỉ có giá mở cửa, độ chênh lệch giá cố định do người dùng nhập, `OnTick()` chỉ được gọi 1 lần/phút tại thời điểm đóng nến | Nhanh | Quét thô ban đầu khi tối ưu hóa |

**Hệ quả quan trọng khi dùng dữ liệu 1 phút:**
- `OnTick()` chỉ được gọi **một lần mỗi phút** tại thời điểm đóng nến — code dựa vào tần suất tick sẽ hoạt động khác hoàn toàn so với live
- Lệnh chờ luôn khớp chính xác tại giá mục tiêu — không có mô phỏng nhảy giá
- Độ chênh lệch giá là giá trị cố định do người dùng nhập — nếu nhập thấp hơn thực tế, kết quả bị thổi phồng

### 1.2 Cấu hình backtest

Backtester cho phép thiết lập: Vốn ban đầu, Hoa hồng (tính theo mỗi triệu), Độ chênh lệch giá (cố định hoặc khoảng min/max), Khoảng thời gian.

**Lưu ý khi code:**
- Hoa hồng trong cTrader tính theo **mỗi triệu đơn vị tiền tệ** — không phải mỗi lot. Kiểm tra đúng thông số từ nhà môi giới trước khi backtest
- Nếu cBot có tham số lọc độ chênh lệch giá: phải đảm bảo giá trị đó trong cài đặt backtest ≤ ngưỡng của bot, không thì bot sẽ không mở lệnh nào cả

### 1.3 Các chỉ số trong báo cáo backtest

cTrader tạo báo cáo HTML đầy đủ với các chỉ số chính:

- Lợi nhuận ròng, Hệ số lợi nhuận, Tỷ lệ Sharpe, Tỷ lệ Sortino
- Sụt giảm số dư tối đa (%), Sụt giảm "equity" tối đa (%)
- Số lệnh thắng, Số lệnh thua, Tổng lệnh, Lợi nhuận trung bình mỗi lệnh
- Tổng phí lãi suất qua đêm ("swap"), Tổng hoa hồng
- Biểu đồ đường "equity"

---

## 2. Backtester — Giới hạn cụ thể của nền tảng

### 2.1 Những gì backtester KHÔNG mô phỏng được

| Giới hạn | Hệ quả khi code |
|---|---|
| **Không mô phỏng trượt giá** | Lệnh chờ (Buy Stop/Sell Stop) khớp chính xác tại giá mục tiêu — trên live sẽ bị khớp tệ hơn nhiều khi thị trường nhảy giá |
| **Không mô phỏng khớp lệnh một phần** | Lệnh lớn luôn khớp 100% — thực tế không đúng với công cụ thanh khoản thấp |
| **`OnTick()` với dữ liệu 1 phút = 1 lần/phút** | Code dùng tick để kéo Stop Loss hoặc kiểm tra "equity" sẽ phản ứng chậm hơn thực tế |
| **`LoadMoreHistory()` không hoạt động trong backtest** | Gọi hàm này trong backtest có thể gây vòng lặp vô tận — phải kiểm tra điều kiện |
| **Không hỗ trợ tối ưu hóa đa symbol** | Bot dùng nhiều symbol không thể tối ưu hóa qua công cụ tích hợp |
| **Tải symbol phụ trong `OnStart()` khi tối ưu hóa** | Gây crash (`NullReferenceException`) — phải kiểm tra null trước khi dùng |

### 2.2 Backtest đa symbol — Lưu ý đặc thù

- Chạy backtest đa symbol trên cùng khoảng thời gian có thể cho kết quả **khác nhau** tùy vào symbol nào được chọn làm biểu đồ gốc — đây là hạn chế đã biết của cTrader
- Tối ưu hóa không chạy được cho bot đa symbol → nếu cần tối ưu hóa: phải chạy từng symbol riêng lẻ và so sánh thủ công

### 2.3 Chế độ trực quan vs chế độ thường

- **Chế độ trực quan**: Phát lại từng tick/nến theo thời gian thực, có thể điều chỉnh tốc độ — dùng để debug, kiểm tra logic xử lý từng lệnh
- **Chế độ thường**: Chạy hết toàn bộ khoảng thời gian ngay lập tức — dùng để lấy kết quả thống kê

---

## 3. Công cụ Tối ưu hóa — Cách hoạt động thực tế

### 3.1 Hai phương thức tối ưu hóa

**Tìm kiếm lưới (toàn diện):**
- Tạo lưới tất cả tổ hợp tham số và chạy tất cả
- Toàn diện nhưng cực kỳ chậm khi dùng dữ liệu tick và nhiều tham số
- Ví dụ: 5 tham số × 10 giá trị mỗi tham số = 100.000 lần chạy — có thể mất hàng ngày đến hàng tuần

**Thuật toán di truyền (mặc định, khuyến nghị):**
- Mô phỏng chọn lọc tự nhiên: mỗi lần chạy = một "cá thể", tham số = "gene"
- Bắt đầu với quần thể ngẫu nhiên, sau đó lai ghép và đột biến qua nhiều vòng lặp
- Dừng khi điểm đánh giá bão hòa (không cải thiện thêm)
- Mang tính "heuristic" — không cho kết quả giống nhau mỗi lần chạy
- Các tham số nội bộ (kích thước quần thể, tỷ lệ đột biến, v.v.) **không thể thay đổi**

**Quy trình đúng:**
1. Dùng dữ liệu 1 phút để quét thô ban đầu (nhanh)
2. Thu hẹp khoảng giá trị → chạy lại với dữ liệu tick để kiểm chứng (chính xác)
3. Kiểm chứng kết quả trên dữ liệu ngoài mẫu

### 3.2 Tiêu chí đánh giá có sẵn trong công cụ tối ưu hóa

| Tiêu chí | Nên dùng khi nào |
|---|---|
| Lợi nhuận ròng | Không nên dùng đơn lẻ — dễ bị thổi phồng bởi vài lệnh may mắn |
| Hệ số lợi nhuận | Tốt — cân bằng giữa tổng thắng và tổng thua |
| Sụt giảm "equity" tối đa (%) | Dùng để giảm thiểu rủi ro — thường kết hợp với tiêu chí khác |
| Tỷ lệ Sharpe | Tốt cho lợi nhuận điều chỉnh theo rủi ro |
| Tỷ lệ Sortino | Tốt hơn Sharpe nếu phân phối lợi nhuận lệch (chỉ tính rủi ro chiều xuống) |
| **Tùy chỉnh** | Viết `GetFitness()` khi muốn kết hợp nhiều tiêu chí |

**Khuyến nghị:** Không tối ưu hóa theo lợi nhuận ròng đơn thuần — dễ khớp quá mức với dữ liệu lịch sử. Dùng hệ số lợi nhuận hoặc hàm tùy chỉnh kết hợp điều kiện tối thiểu (ví dụ: số lệnh > 50).

### 3.3 Hàm đánh giá tùy chỉnh — Khi nào và cách dùng

Khi tiêu chí tích hợp không đủ, ghi đè `GetFitness()` trong code:

```csharp
protected override double GetFitness(GetFitnessArgs args)
{
    // Quá ít lệnh → kết quả không đáng tin, trả về giá trị thấp nhất
    if (args.TotalTrades < 30)
        return double.MinValue;

    // Loại bỏ nếu sụt giảm equity quá cao
    if (args.MaxEquityDrawdownPercentages > 25)
        return double.MinValue;

    return args.ProfitFactor;
}
```

**Toàn bộ thuộc tính có trong `GetFitnessArgs`:**

| Thuộc tính | Ý nghĩa |
|---|---|
| `NetProfit` | Tổng lợi nhuận ròng |
| `ProfitFactor` | Tổng thắng / tổng thua |
| `MaxBalanceDrawdown` | Sụt giảm số dư tối đa (tuyệt đối) |
| `MaxBalanceDrawdownPercentages` | Sụt giảm số dư tối đa (%) |
| `MaxEquityDrawdown` | Sụt giảm "equity" tối đa (tuyệt đối) |
| `MaxEquityDrawdownPercentages` | Sụt giảm "equity" tối đa (%) |
| `WinningTrades` | Số lệnh thắng |
| `LosingTrades` | Số lệnh thua |
| `TotalTrades` | Tổng số lệnh |
| `AverageTrade` | Lợi nhuận trung bình mỗi lệnh |
| `Swaps` | Tổng phí lãi suất qua đêm |
| `Commissions` | Tổng hoa hồng |
| `Equity` | Giá trị tài khoản cuối kỳ |
| `History` | Toàn bộ lịch sử lệnh |
| `Positions` | Các vị thế đang mở cuối kỳ |
| `PendingOrders` | Lệnh chờ còn lại cuối kỳ |

**Lưu ý:**
- Tỷ lệ Sharpe và Sortino **không có** trong `GetFitnessArgs` — phải tự tính từ `History` nếu cần
- Luôn kiểm tra điều kiện tối thiểu và trả về `double.MinValue` nếu không đạt — để công cụ tối ưu hóa loại bỏ các lần chạy không hợp lệ

---

## 4. Viết code cBot để hỗ trợ Tối ưu hóa

### 4.1 Kiểu tham số — Loại nào tối ưu hóa được, loại nào không

| Kiểu | Tối ưu hóa được? | Yêu cầu |
|---|---|---|
| `int` | Có | `MinValue`, `MaxValue`, `Step` |
| `double` | Có | `MinValue`, `MaxValue`, `Step` |
| `enum` | Có (duyệt qua các giá trị) | — |
| `bool` | **Không** | — |
| `string` | **Không** | — |
| `DataSeries` | **Không** | — |
| `Symbol` | **Không** | — |
| `TimeFrame` | **Không** (nhưng có thể được mặc định theo biểu đồ) | — |
| `Color`, `DateTime` | **Không** | — |

**Hệ quả:** Nếu muốn tối ưu hóa một điều kiện bật/tắt — dùng `int` (0 = tắt, 1 = bật) hoặc `enum` thay vì `bool`.

### 4.2 Khai báo tham số đúng để công cụ tối ưu hóa hoạt động

```csharp
// Đúng — công cụ tối ưu hóa có đủ thông tin để quét
[Parameter("Mức SL", Group = "Thực thi", DefaultValue = 2, MinValue = 1, MaxValue = 4, Step = 1)]
public int KslLevel { get; set; }

[Parameter("Hệ số TP", Group = "Thực thi", DefaultValue = 2.0, MinValue = 0.5, MaxValue = 5.0, Step = 0.5)]
public double TpMultiplier { get; set; }

// Sai — kiểu bool không thể tối ưu hóa, không có khoảng giá trị
[Parameter("Dùng bộ lọc", DefaultValue = true)]
public bool UseFilter { get; set; }  // → đổi sang int nếu muốn tối ưu hóa

// Sai — thiếu MinValue/MaxValue/Step → công cụ tối ưu hóa không biết khoảng quét
[Parameter("Chu kỳ", DefaultValue = 14)]
public int Period { get; set; }
```

### 4.3 Những gì KHÔNG được làm trong cBot nếu cần tối ưu hóa

| Không nên làm | Lý do |
|---|---|
| Tải symbol phụ trong `OnStart()` mà không kiểm tra null | Crash (`NullReferenceException`) khi công cụ tối ưu hóa chạy |
| Đọc/ghi file bên ngoài trong `OnBar()`/`OnTick()` | Thao tác file nặng × hàng nghìn lần chạy = tối ưu hóa cực chậm hoặc crash |
| Gọi `LoadMoreHistory()` không có giới hạn số lần thử | Vòng lặp vô tận trong backtest |
| Gọi API ngoài hoặc kết nối mạng trong logic giao dịch | Timeout và crash trong môi trường tối ưu hóa |
| Tối ưu hóa quá nhiều tham số cùng lúc (> 5) | Thuật toán di truyền bão hòa sớm; tìm kiếm lưới mất hàng tuần |

### 4.4 Thiết kế tham số để tối ưu hóa có ý nghĩa

- **Bước nhảy phải có ý nghĩa thực tế:** Hệ số SL bước nhảy = 0.5 (không phải 0.001 tùy tiện); chu kỳ bước nhảy = 1 (không phải 0.1)
- **Phân nhóm rõ ràng:** Dùng thuộc tính `Group` để tách nhóm tín hiệu, quản lý rủi ro, thực thi lệnh
- **Không tối ưu hóa tham số rủi ro cùng tham số tín hiệu:** Thay đổi `RiskPercent` làm lệch kết quả so sánh giữa các lần chạy
- **Không tối ưu hóa tham số không ảnh hưởng đến logic giao dịch:** Đường dẫn file, nhãn bot, lệch múi giờ, mức log

---

## 5. Vào lệnh — Quy tắc

### 5.1 Timing — Không dùng dữ liệu nến chưa đóng

- **Trong `OnBar()`:** Dùng `Last(1)` hoặc `Bars.ClosePrices.Last(1)` — `Last(0)` là nến đang hình thành, chưa xác nhận
- **Tốt hơn:** Dùng `OnBarClosed()` — sự kiện này chỉ kích hoạt sau khi nến đóng hoàn toàn, không thể nhầm lẫn
- **Với tín hiệu từ file CSV:** Thời điểm tín hiệu phải khớp với **thời điểm đóng cửa** của nến tín hiệu; phải quy về cùng múi giờ với `Server.Time` trước khi so sánh

### 5.2 Lệnh thị trường vs Lệnh chờ

| | Lệnh thị trường | Lệnh chờ (Dừng/Giới hạn) |
|---|---|---|
| Khớp lệnh | Ngay lập tức theo giá mua/bán hiện tại | Khi giá chạm mục tiêu |
| Độ chính xác backtest | Tốt | Khớp đúng giá, không có mô phỏng nhảy giá |
| Rủi ro trượt giá trên live | Có | Cao hơn nếu thị trường nhảy giá qua mức vào lệnh |
| Hết hạn | Không áp dụng | **Phải đặt thời hạn** — không để lệnh chờ tồn tại vô hạn |

### 5.3 Tránh tín hiệu trùng lặp — Bắt buộc

- Mỗi tín hiệu chỉ được kích hoạt **một lần** cho một nến — phải đánh dấu tín hiệu đã xử lý
- Không để `OnBar()` kích hoạt lại cùng tín hiệu do logic kiểm tra trùng nến

---

## 6. Stop Loss — Quy tắc

- **Bắt buộc:** Mọi lệnh — lệnh thị trường và lệnh chờ — đều phải có Stop Loss. Không có ngoại lệ
- **Không nới Stop Loss sau khi vào lệnh:** Đây là vi phạm quản lý vốn cơ bản — tăng rủi ro khi đã biết lệnh đang thua
- **Stop Loss phải dựa trên cấu trúc thị trường hoặc ATR** — không phải con số cảm tính
- **Thang Stop Loss (nhiều lệnh cùng cụm):** Chỉ dịch chuyển theo hướng có lợi (Mua: chỉ tăng; Bán: chỉ giảm) — không bao giờ dịch ra xa thêm
- **Khoảng cách Stop Loss phải đủ xa khỏi `Symbol.StopLevel`:** cTrader từ chối lệnh nếu Stop Loss quá gần giá vào lệnh
- Khi dùng Stop Loss theo ATR: tính từ **giá khớp thực tế**, không phải giá mục tiêu ban đầu

---

## 7. Take Profit — Quy tắc

- Tỷ lệ Rủi ro:Lợi nhuận tối thiểu 1:1 tính từ giá khớp thực tế đến Take Profit đầu tiên
- **Nhiều lệnh cùng cụm:** Toàn bộ lệnh trong một cụm phải dùng cùng Stop Loss tại mỗi thời điểm
- Bộ hệ số Take Profit phải là **bảng cố định** trong code — không tính động theo điều kiện thay đổi
- Không thay đổi bộ hệ số Take Profit khi cụm lệnh đang mở — thang Stop Loss sẽ tính sai khoảng cơ sở
- Ghi log từng lệnh khi đóng: số thứ tự lệnh, giá Take Profit, lợi nhuận, Stop Loss mới của các lệnh còn lại

---

## 8. Quản lý Vốn — Quy tắc Code

### 8.1 Tính khối lượng lệnh

```csharp
// Đúng — dùng API của nền tảng, đúng với mọi loại công cụ giao dịch
var soTienRuiRo = Account.Balance * PhanTramRuiRo / 100.0;  // Balance, không phải Equity
var khoiLuong   = Symbol.VolumeForFixedRisk(soTienRuiRo, slPips, RoundingMode.Down);
khoiLuong       = Symbol.NormalizeVolumeInUnits(khoiLuong, RoundingMode.Down);

if (khoiLuong < Symbol.VolumeInUnitsMin)
{
    Print($"[{Label}] Khối lượng dưới mức tối thiểu — bỏ qua lệnh");
    return;
}
```

- Dùng **Balance** (số dư), không phải **Equity** — tránh lãi/lỗ chưa chốt ảnh hưởng đến khối lượng lệnh tiếp theo
- Không tự viết công thức `lots = ruiRo / (slPips * giaTriPip)` — giá trị pip khác nhau theo công cụ giao dịch, tiền tệ tài khoản, và đòn bẩy
- Luôn kiểm tra `khoiLuong >= Symbol.VolumeInUnitsMin` trước khi đặt lệnh

### 8.2 Ba lớp bảo vệ vốn bắt buộc

| Lớp | Cơ chế | Lưu ý khi code |
|---|---|---|
| Stop Loss mỗi lệnh | Stop Loss trên từng lệnh | Xem mục 6 |
| Giới hạn lỗ ngày | Dừng giao dịch trong ngày khi "equity" ngày giảm quá ngưỡng | Reset theo `Server.Time.Date`, không phải UTC cố định |
| Giới hạn sụt giảm tài khoản | Dừng bot khi sụt giảm từ đỉnh vượt ngưỡng | Dùng `Account.Equity`, không phải `Account.Balance` để phát hiện lỗ chưa chốt |

**Khi chạm giới hạn ngày phải đóng hết:** Hủy toàn bộ lệnh chờ, đóng toàn bộ vị thế của bot — không chỉ ngừng mở lệnh mới.

### 8.3 Theo dõi lệnh

- Mọi lệnh phải gắn nhãn (`Label`) duy nhất của bot → lọc bằng `Positions.FindAll(Label, SymbolName)`
- Không quản lý lệnh không có nhãn của mình — tránh can thiệp vào lệnh thủ công hoặc lệnh từ bot khác
- Khi bot khởi động lại giữa chừng: phải khôi phục trạng thái từ các vị thế đang mở — không giả định trạng thái sạch

---

## 9. Checklist trước khi chạy Backtest

- [ ] Kiểu dữ liệu: tick (kiểm chứng) hoặc 1 phút (quét ban đầu) — ghi rõ loại đang dùng
- [ ] Độ chênh lệch giá: dùng giá trị thực tế từ nhà môi giới, không cố định = 0
- [ ] Hoa hồng và phí lãi suất qua đêm: nhập đúng thông số từ nhà môi giới
- [ ] Múi giờ: tham số lệch giờ canh đúng với `Server.Time`
- [ ] Bộ lọc độ chênh lệch giá trong bot (nếu có) phải ≤ giá trị nhập trong cài đặt backtest
- [ ] File tín hiệu phủ đủ toàn bộ khoảng thời gian backtest
- [ ] Bot biên dịch không có lỗi
- [ ] `LoadMoreHistory()` đã có kiểm tra điều kiện nếu có dùng

## 10. Checklist trước khi chạy Tối ưu hóa

- [ ] Tham số cần tối ưu hóa đều là `int` hoặc `double` và có `MinValue`, `MaxValue`, `Step`
- [ ] Không tối ưu hóa quá 5 tham số cùng lúc
- [ ] Tham số rủi ro (`RiskPercent`, giới hạn lỗ ngày) cố định riêng, không nằm trong lần tối ưu hóa
- [ ] Không có thao tác file nặng trong `OnBar()`/`OnTick()` — sẽ làm chậm nghiêm trọng
- [ ] Symbol phụ trong `OnStart()` đã kiểm tra null nếu là bot đa symbol
- [ ] Hàm `GetFitness()` tùy chỉnh có điều kiện loại bỏ lần chạy quá ít lệnh (`double.MinValue`)
- [ ] Dữ liệu ngoài mẫu đã được khóa — không dùng để quét tối ưu hóa
- [ ] Sau tối ưu hóa: kiểm chứng lại các tham số tốt nhất trên dữ liệu tick trước khi kết luận

---

*Tài liệu dựa trên cTrader Automate API và hành vi thực tế của nền tảng.*
*Cập nhật khi API thay đổi hoặc phát hiện hành vi mới.*
