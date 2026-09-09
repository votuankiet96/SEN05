# Missing-bar theo dõi lại — US30 2026 ngắn hạn (2026-09-01)

> Nối tiếp `signal-alignment-baseline-2026-08-31.md`. Lần trước kiểm chứng
> trên toàn bộ lịch sử (2024-2026); lần này lặp lại trên khung NGẮN
> (2026-01-04/02 → 2026-08-26/30) để trả lời trực tiếp câu hỏi: **hiện tượng
> missing-bar còn xảy ra không, và ảnh hưởng thế nào** — trong bối cảnh CSV
> signal lấy từ broker Capital.com còn backtest chạy trên dữ liệu FTMO.

## Nguồn dữ liệu (đã archive)

| Bot | Fallback | Archive |
|---|---|---|
| Combo/H4 | OFF | `Combo\...1bae1864...\ArchivedRuns\US30_H4_FallbackOFF_2026short_CANONICAL_20260901-0025` |
| Combo/H4 | ON | `Combo\...1bae1864...\ArchivedRuns\US30_H4_FallbackON_2026short_20260901-0035` |
| MA Cross/M30 | OFF | `MA Cross\...a08e1adc...\ArchivedRuns\US30_M30_FallbackOFF_2026short_20260901-0013` |
| MA Cross/M30 | ON | `MA Cross\...a08e1adc...\ArchivedRuns\US30_M30_FallbackON_2026short_20260901-0015` |

Khung thời gian thực tế cả 4 lượt: Combo `2026-01-04 → 2026-08-26`, MA Cross
`2026-01-02 → 2026-08-30`.

## 1. Hiện tượng missing-bar CÒN XẢY RA — có quy luật, không ngẫu nhiên

| Bot | Trong khung test | Exact match | Fallback (thiếu bar) | Tỷ lệ thiếu bar |
|---|---|---|---|---|
| Combo/H4 | 62 | 53 | **9** | **14.52%** |
| MA Cross/M30 | 203 | 200 | **3** | **1.48%** |

Combo (H4, nến thô hơn) có tỷ lệ thiếu bar cao hơn nhiều MA Cross (M30, nến
mịn hơn) — hợp lý: khung càng thô, một lần lệch lưới nến giữa 2 broker càng
dễ khiến "hoàn toàn không có bar khớp", trong khi khung mịn có nhiều bar/ngày
hơn nên xác suất tình cờ trùng 1 bar nào đó cao hơn.

**Bằng chứng "có quy luật, không ngẫu nhiên"** — giờ (UTC) của các bartime bị
thiếu lặp lại rõ rệt, không rải đều:

- Combo/H4 (9 trường hợp): `23:00` (×2), `10:00/11:00` (×3), `14:00` (×4).
- MA Cross/M30 (3 trường hợp): `21:30/22:30` (cả 3).

Đây chính là dấu hiệu **lệch lưới nến hệ thống** giữa Capital.com (nguồn sinh
CSV signal) và FTMO (nguồn chạy backtest) tại các mốc giờ cố định — khớp
chính xác giả thuyết bạn nêu: "signal có nhưng backtest trên signal đó thì
không có lệnh" do 2 broker không chia bar giống hệt nhau ở 1 số khung giờ.
Cơ chế `Enable Missing-Bar Fallback` đang bắt đúng và phục hồi đúng các
trường hợp này.

## 2. Ảnh hưởng — 3 tầng, đã lượng hoá đầy đủ

### Tầng 1 — P&L trực tiếp của các lệnh fallback

| Bot | Lệnh phục hồi | Khớp | Không khớp | Kết quả từng lệnh khớp | Net P&L trực tiếp |
|---|---|---|---|---|---|
| Combo/H4 | 9 | 6 | 3 (hết hạn 3-bar) | 1 TP (+260.76), 5 SL (−103.27, −102.06, −98.51, −102.72, −103.69) | **−$249.49** |
| MA Cross/M30 | 3 | 2 | 1 (`NoMoney`) | 2 SL (−121.66, −113.63) | **−$235.29** |

Cả 2 mẫu đều lỗ ở khung ngắn này — nhưng cỡ mẫu quá nhỏ (6 và 2 lệnh khớp)
để kết luận bất kỳ điều gì về HIỆU SUẤT của tín hiệu được phục hồi; đây chỉ
là 1 lần rút mẫu ngẫu nhiên nhỏ, không phải bằng chứng cơ chế fallback "gây
lỗ" (đúng như đã ghi nhận ở báo cáo trước với mẫu lớn hơn nhiều).

### Tầng 2 — hiệu ứng lan qua cỡ lệnh (risk % tính theo balance đang trôi)

- **Combo**: chênh lệch balance cuối kỳ ON−OFF = **−$287.75**, trong khi P&L
  trực tiếp 9 lệnh fallback chỉ **−$249.49** → còn **−$38.26** phát sinh từ
  việc các lệnh exact-match SAU ĐÓ tính lại khối lượng theo balance đã bị
  fallback làm thay đổi trước đó (risk 1%/balance khác đi → volume khác đi
  dù cùng entry/hướng).
- **MA Cross**: phát hiện cụ thể hơn — đúng **1 lệnh exact-match
  (`2026-03-06 05:00`) đổi kết quả từ PLACED (bản OFF) sang REJECTED margin
  (bản ON)** — vì 2 lệnh fallback đặt trước đó đã tiêu bớt margin, khiến lệnh
  exact này (đủ margin nếu không có fallback) giờ không đủ nữa. Đây là bằng
  chứng trực tiếp: bật fallback có thể làm ĐỔI KẾT QUẢ accept/reject của 1
  lệnh exact-match khác, không chỉ ảnh hưởng P&L — cần lưu ý khi so sánh
  "nhánh exact giữ nguyên tuyệt đối" ở mẫu nhỏ/margin-nhạy như MA Cross.

### Tầng 3 — vấn đề hạ tầng lịch sử (`ModifyExpirationTime`) — KHÔNG CÒN TỒN TẠI

Bản 5-ExitMode cũ dùng `PendingOrder.ModifyExpirationTime()` của cTrader để
đặt hạn pending order, và vấn đề session-gap khiến lệnh sửa đó fail ~10.14%.
Kiến trúc hiện tại **không gọi API đó nữa** — Combo tự đếm hạn bằng bar-count
nội bộ (`ExpirePendingOrders`, exact-match) và bằng mốc thời gian tuyệt đối
nội bộ (`ExpireFallbackOrders`, fallback), rồi tự gọi `order.Cancel()` trực
tiếp. Đã kiểm tra cả 4 log — **0 dòng "Order modified"/"TechnicalError"** ở
bất kỳ lượt nào. Vấn đề gốc không còn đường nào xảy ra trong kiến trúc mới.

## 3. Kết luận

1. Hiện tượng missing-bar **vẫn tồn tại thật**, tỷ lệ khác nhau rõ theo
   timeframe (H4 ~14.5%, M30 ~1.5%), và **có quy luật theo giờ cố định** —
   xác nhận đúng nguyên nhân là lệch lưới nến Capital.com ↔ FTMO, không phải
   nhiễu ngẫu nhiên.
2. Cơ chế fallback hoạt động đúng thiết kế — phục hồi chính xác các tín hiệu
   này, không sai direction/entry/SL/TP.
3. Ảnh hưởng thực tế có 2 tầng: trực tiếp (P&L của chính lệnh phục hồi) và
   gián tiếp (thay đổi cỡ lệnh — và với MA Cross là cả kết quả accept/reject
   — của các lệnh exact-match sau đó, do risk tính theo balance động).
4. Vấn đề hạ tầng `ModifyExpirationTime` của kiến trúc cũ đã biến mất hoàn
   toàn trong kiến trúc hiện tại — không cần theo dõi tiếp.

## 4. Phát hiện thêm khi audit sâu cơ chế fallback (cùng ngày)

Soi ngày-trong-tuần của toàn bộ 9 mốc fallback Combo/H4: **9/9 (100%) rơi
đúng Chủ Nhật hoặc Thứ Hai** — gần như chắc chắn là lệch giờ MỞ CỬA PHIÊN ĐẦU
TUẦN giữa Capital.com và FTMO, không phải lệch rải rác ngẫu nhiên trong tuần
như giả thuyết ban đầu. MA Cross (mẫu chỉ 3, quá nhỏ) không cho pattern sạch
tương tự (2 Thứ Tư, 1 Thứ Hai). **Đây là quan sát thực nghiệm nội tại (internal
validity) của riêng mẫu H4/US30 vừa test — CHƯA chắc đúng cho timeframe/symbol
khác** (người dùng lưu ý rõ, xem mục 5 bên dưới).

Đồng thời audit code (`InitializeSignalSchedule`, `ProcessFallbackSignals`,
`ExpireFallbackOrders`, `GetFallbackExpiry`) xác nhận phần lõi đúng và hiệu
quả: cơ chế con trỏ-chỉ-tăng O(N) cho cả backtest, không có race-condition
giữa nhánh exact/fallback (chống trùng bằng `IsHandled`), tách đúng 2 kiểu
hết hạn (exact = đếm nến chart, fallback = thời gian tuyệt đối).

## 5. Đề xuất cải thiện — ĐÃ GHI NHẬN, CHƯA TRIỂN KHAI (chờ quyết định người dùng)

Người dùng lưu ý quan trọng (2026-09-01): mọi đề xuất dưới đây phải **tổng
quát cho MỌI timeframe** (mới chỉ test H4 + M30), không được thiết kế/hiểu
riêng theo đặc thù của 2 timeframe đã test. Đã rà lại — công thức gốc
(`GetFallbackExpiry`, `TryGetNominalBarDuration`) vốn đã tổng quát sẵn (suy
ra từ chính `TimeFrame` đang chạy, không hardcode số giờ cụ thể); chỉ MỨC ĐỘ
NGHIÊM TRỌNG của đề xuất 1 là khác nhau theo độ mịn timeframe (nặng hơn với
M1-M15, nhẹ hơn với D1/W1).

**Mục tiêu chung khi cân nhắc 3 đề xuất**: cơ chế hiện tại đã làm TỐT phần
"không bỏ sót tín hiệu" (0/12 tín hiệu fallback bị bỏ sót hoàn toàn trong mẫu
vừa test — số bị huỷ là do giá không chạm, y hệt số phận bình thường của lệnh
chờ, không phải "mất vì thiếu bar"). Không gian cải thiện còn lại nằm ở: (a)
ĐỘ CHÍNH XÁC của thời điểm/giá thực thi khi phục hồi — càng gần "nếu FTMO có
đúng bar thì sẽ ra sao" càng tốt, và (b) 1 đường rò rỉ dữ liệu lý thuyết CHƯA
lộ ra trong mẫu vừa test (cửa sổ hết hạn không né lịch nghỉ dài — xem đề xuất
2) — đây mới là chỗ có thể THỰC SỰ làm mất tín hiệu nếu gặp đúng kỳ nghỉ dài
bất thường.

### Đề xuất 1 — [Nhẹ] Chốt thời điểm thực thi fallback vào đúng mốc đóng nến FTMO

Hiện tại: fallback thực thi tại **tick sống đầu tiên** sau mốc đóng nến danh
nghĩa (`AvailableTime`) — có thể rơi giữa 1 nến FTMO đang hình thành, giá lúc
đó có thể bất thường (vừa mở phiên, thanh khoản mỏng). Nhánh exact luôn thực
thi tại ranh giới đóng nến sạch.

Đề xuất: đổi fallback sang chờ tới **đúng lần đóng nến FTMO kế tiếp** tính từ
`AvailableTime`, thay vì tick đầu tiên bắt được — vẫn giữ nguyên tắc "không
được biết trước tín hiệu" (không lùi về trước mốc danh nghĩa), nhưng thực thi
tại ranh giới sạch giống hệt nhánh exact.

### Đề xuất 2 — [Trung bình] Cửa sổ hết hạn fallback phải "né" lịch nghỉ

Hiện tại: hạn hiệu lực lệnh fallback = `AvailableTime + 3×duration`, tính
bằng **thời gian tuyệt đối cố định** — KHÔNG né cuối tuần/kỳ nghỉ như cách
đếm "3 nến chart" của nhánh exact (nến chart tự nhiên không sinh ra lúc đóng
cửa nên tự né). Rủi ro: nếu tín hiệu rơi sát 1 kỳ nghỉ dài hơn bình thường
(lễ Tết, sự cố thị trường...), cửa sổ tuyệt đối có thể bị "ăn hết" trước khi
thị trường mở lại → **tín hiệu bị mất thật sự**, không chỉ lệch thời điểm.
Đã kiểm tra: 3 lệnh Cancelled trong mẫu vừa test KHÔNG dính trường hợp này
(đều Thứ 2→Thứ 3, không chạm cuối tuần) — nhưng rủi ro thiết kế vẫn có thật,
áp dụng cho MỌI timeframe, chỉ chưa lộ ra ở mẫu này.

Đề xuất: đổi sang đếm **3 nến chart thật** tính từ `AvailableTime` trở đi
(dò qua `Bars`, tự động né khoảng đóng cửa) — thay vì cộng trừ `TimeSpan`
tuyệt đối. Miễn nhiễm với vấn đề trên ở MỌI timeframe mà không cần biết
trước lịch nghỉ cụ thể của symbol nào. Đánh đổi: phức tạp hơn code hiện tại.

### Đề xuất 3 — [Lớn hơn, giá trị cao nhất về lâu dài] Chuyển đối chiếu 2 lịch lên tầng Python

Thay vì để cBot tự đoán lúc chạy runtime ("chờ tới X rồi bắt tick đầu tiên"),
nếu Python (`core_python`) có được **lịch nến (chỉ cần mốc thời gian, không
cần giá)** của FTMO cho đúng symbol/timeframe, Python có thể tự đối chiếu
TRƯỚC, ngoại tuyến — với mỗi tín hiệu, tìm đúng nến FTMO gần nhất hợp lệ
(không lùi về trước mốc xác nhận), xuất thêm 1 cột `ftmo_execute_at` vào CSV.
cBot lúc đó chỉ việc đọc và thực thi đúng mốc đã tính sẵn — không cần đoán,
không cần cơ chế fallback runtime phức tạp trong C# nữa.

Ưu điểm: Python có toàn quyền nhìn thấy CẢ 2 lịch cùng lúc (không bị giới hạn
"chỉ biết tới hiện tại" như cBot lúc chạy), dễ áp thuật toán khớp-gần-nhất
phức tạp hơn, dễ kiểm tra/debug hơn (mở CSV xem trực tiếp).

**Nút thắt duy nhất, đang treo từ trước**: cần xác minh khả năng lấy lịch
nến FTMO cho Python dùng (qua cTrader Open API, hoặc viết 1 cBot phụ tự
export `Bars` ra CSV) — **CHƯA điều tra**. Đây cũng chính là nút thắt chung
với hướng "chuyển hẳn signal sang data FTMO" đã bàn trước đó — điều tra 1 lần
có thể mở khoá cả 2 hướng.

**Trạng thái: CHỜ NGƯỜI DÙNG QUYẾT ĐỊNH ƯU TIÊN — chưa triển khai đề xuất
nào trong 3 đề xuất trên.**

## 6. Mở rộng phân tích trên dữ liệu mới, đa symbol/timeframe (2026-09-01, tiếp)

Sau khi có `Combo.cs` bản `ReconcileExistingExposure`, backtest lại US30/H4
(2024-2026, mẫu LỚN hơn nhiều — 49 fallback thay vì 9) và HK50/H2 lần đầu
(99 fallback). Kết hợp thêm 2 archive MA Cross sẵn có (US30/M30, HK50/M45,
2025+) — tổng 4 dataset, 2 symbol × pha trộn timeframe, đủ để tách bạch
"do symbol" hay "do timeframe".

### 6.1. Tỷ lệ missing-bar phụ thuộc SYMBOL rõ hơn timeframe

| Bot/Symbol/TF | Exact | Fallback (recovered+failed) | Tỷ lệ |
|---|---|---|---|
| Combo US30/H4 | 256 | 49 + 0 | 16.07% |
| Combo HK50/H2 | 257 | 75 + 24 | **27.81%** |
| MA Cross US30/M30 | 548 | 6 + 0 | 1.08% |
| MA Cross HK50/M45 | 210 | 84 + 0 | **28.57%** |

Giả thuyết cũ "timeframe thô hơn → tỷ lệ cao hơn" KHÔNG giải thích được vì
sao MA Cross/M30 (mịn) trên HK50 cao gấp ~26 lần chính nó trên US30. Nguyên
nhân nhiều khả năng nằm ở đặc thù riêng HK50 (múi giờ Hong Kong, lịch nghỉ lễ
khác Mỹ, cấu trúc phiên khác) hơn là do độ thô nến — cần coi symbol là biến
số chính, không chỉ timeframe, khi đánh giá mức độ nghiêm trọng ở nơi khác.

### 6.2. Pattern ngày-trong-tuần: US30 xác nhận mạnh hơn, HK50 có thêm nguồn lệch khác

- **Combo US30/H4** (mẫu 49, lớn hơn nhiều lần kiểm tra trước với 9 mẫu):
  **100% rơi Chủ Nhật/Thứ Hai** (45 Mon + 4 Sun, 0 case các ngày khác) — xác
  nhận RẤT CHẮC giả thuyết lệch giờ mở phiên đầu tuần Capital.com↔FTMO, với
  cỡ mẫu đáng tin cậy hơn nhiều so với lần trước.
- **HK50** (cả Combo/H2 lẫn MA Cross/M45): Thứ Hai vẫn chiếm đa số (~67%)
  nhưng **trải đều Tue-Fri** với số lượng đáng kể (26/99 Combo, 33/84 MA
  Cross không phải Mon/Sun) — khác hẳn US30. Gợi ý HK50 có ít nhất 1 nguồn
  lệch khác NGOÀI vấn đề đầu tuần — khả năng do đặc thù phiên giao dịch/múi
  giờ Hong Kong (session structure khác instrument kiểu Mỹ).

### 6.3. Phát hiện quan trọng nhất: 24 tín hiệu MẤT THẬT SỰ — chỉ ở Combo/HK50/H2

Đây là bằng chứng THỰC TẾ đầu tiên cho rủi ro đã cảnh báo lý thuyết ở Đề xuất
2 (§5) — "cửa sổ hết hạn tuyệt đối không né lịch nghỉ dài có thể làm mất tín
hiệu thật". Trước đây (mẫu US30 2026-ngắn) chưa case nào dính; lần này dính
thật, 24/24 case failed đều RIÊNG Combo/HK50/H2 (0 ở 3 dataset còn lại):

- **1 cụm 6 case liên tiếp trong ~35 giờ** (2026-04-05 22:00 → 2026-04-07
  09:00, UTC) — trùng thời điểm lễ **Thanh Minh (Ching Ming)** ở Hong Kong —
  rất có khả năng là 1 kỳ nghỉ dài thật khiến cửa sổ chờ 6 tiếng (3×H2, công
  thức `AvailableTime + 3×duration`) "ăn hết" trước khi HK50 mở cửa lại.
- **1 case đúng đêm Giáng Sinh** (2025-12-24 06:00 UTC).
- 24 case còn lại rải các ngày khác trong tuần (Fri×3, Mon×7, Sun×3, Thu×2,
  Tue×5, Wed×4) — không có pattern giờ-trong-ngày rõ như pattern ngày-trong-
  tuần, cần thêm dữ liệu mới kết luận được gì khác.

### 6.4. Xác nhận qua code: khác biệt KIẾN TRÚC thật giữa Combo và MA Cross (không phải bug)

Đọc `MA Cross.cs` xác nhận: `ProcessFallbackSignals()` **hoàn toàn không có**
cơ chế hết hạn nào (không có `GetFallbackExpiry`/`Expire*` như Combo) — chờ
**vô thời hạn** tới tick khả dụng đầu tiên rồi bắn Market Order ngay. Giải
thích tại sao MA Cross/HK50/M45 có case gap **125 giờ (~5.2 ngày)** vẫn
"recovered" bình thường (0 case failed ở cả 2 dataset MA Cross), trong khi
Combo (Pending Order, cần giới hạn tuổi thọ vì lệnh sống hẳn trong sổ lệnh
broker) buộc phải có trần 3×duration.

**Ý nghĩa cho Đề xuất 2 (§5)**: rủi ro lý thuyết giờ đã CÓ BẰNG CHỨNG THẬT
(24 case) — nâng độ ưu tiên của đề xuất "đổi cửa sổ hết hạn Combo sang đếm
nến chart thật thay vì thời gian tuyệt đối" từ "rủi ro chưa lộ" lên "đã xác
nhận gây mất tín hiệu thật, ít nhất với HK50/H2". Vẫn CHƯA triển khai — ghi
nhận độ ưu tiên tăng, chờ quyết định người dùng.
