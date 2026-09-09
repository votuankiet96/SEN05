# So sánh 5 Exit Mode — kiểm chứng thiết kế vs thực tế (2026-08-28)

> File này là báo cáo CHI TIẾT ĐẦY ĐỦ của 1 lần kiểm chứng — khác với
> `AGENT.md` (trạng thái sống, tóm tắt). Đọc file này khi cần hiểu SÂU cơ chế
> đằng sau 2 vấn đề hạ tầng đã phát hiện, hoặc khi cần tái tạo/mở rộng phương
> pháp kiểm chứng này cho lần chạy sau.

## 1. Mục tiêu & phương pháp

Không đánh giá **hiệu suất** ($ lời/lỗ) — mục tiêu DUY NHẤT là kiểm chứng
**mỗi Exit Mode có đặt lệnh/quản lý lệnh đúng như thiết kế trong code không**,
kiểu A/B test: giữ nguyên mọi điều kiện, chỉ đổi đúng 1 biến `Exit`.

**Điều kiện cố định cho cả 5 lần chạy:**
- Symbol/Timeframe: `HK50.cash` / `H1`
- Khoảng thời gian: đầu 2025 → 2026-08-27 (dùng chung 1 file signal)
- `SignalFilePath`: `Z:\Desktop\og_program\runtime\exports\combo_HK50_H1_20200629_20260827_signals.csv`
- `KslLevel = KtpLevel = Fib0618` (index 3) — chọn để **mọi mode đều hợp lệ**
  (PartialScaleOut4 cần KtpLevel≤Fib1272, LadderRunner cần ≤Fib1618 — Fib0618
  thoả cả 2 với dư khoảng trống), KHÔNG phải mức đã tối ưu hiệu suất.
- `Reversal = Immediate`, `SizingMode = PercentRisk`, `RiskPercent = 2`,
  `RiskBase = Floating`, Position Management (3 guard FTMO) đều OFF.
- Biến duy nhất thay đổi: `Exit` (0=FixedTP, 1=PartialScaleOut4,
  2=LadderRunner, 3=PartialBreakeven, 4=FibCompensating3).

**Nguồn dữ liệu** (đã archive thủ công, xem `AGENT.md §7.3` để biết quy ước):

```
Documents\cAlgo\Data\cBots\Combo\7d011f42-48cb-4ded-9634-16836d196705-Default\ArchivedRuns\
  FixedTP_20260828-1754\
  PartialScaleOut4_20260828-1738\
  LadderRunner_20260828-1739\
  PartialBreakeven_20260828-1740\
  FibCompensating3_20260828-1744\
```

Mỗi folder có `log.txt`, `events.json`, `report.html`, `parameters.cbotset`
— phân tích dựa hoàn toàn vào `events.json` (mảng sự kiện lệnh) và
`log.txt` (log thô, có message lỗi broker mà `events.json` không có).

---

## 2. Kết quả cấu trúc đặt lệnh — đối chiếu thiết kế

### 2.1. Bảng tổng hợp đầy đủ

| Mode | Basket | Chân/basket (thiết kế) | Chân/basket (thực tế) | Tạo lệnh | Khớp | Hết hạn | SL Hit | TP Hit | Bị đóng ngang | Điều chỉnh SL |
|---|---|---|---|---|---|---|---|---|---|---|
| FixedTP | 434 | 1 | **1 — 100%** | 434 | 135 | 299 | 83 | 52 | 0 | 0 |
| PartialScaleOut4 | 434 | 4 | **4 — 100%** | 1,736 | 925 | 811 | 620 | 301 | 4 | 0 |
| LadderRunner | 434 | 3 ladder+1 Runner | **4 — 100%** | 1,736 | 926 | 810 | 680 | 229 | 17 | 533 |
| PartialBreakeven | 434 | 2 | **2 — 100%** | 868 | 401 | 467 | 325 | 54 | 22 | 49 |
| FibCompensating3 | 434 | 3 | **3 — 100%** | 1,302 | 641 | 661 | 510 | 126 | 5 | 57 |

**Kiểm tra cân đối** (mọi lệnh khớp phải kết thúc bằng đúng 1 trong 3: SL Hit
/ TP Hit / Bị đóng ngang — không được thiếu/thừa): đúng tuyệt đối 100% ở cả
5 mode — `SLHit + TPHit + PosClosed == Filled` không lệch 1 lệnh nào.

Không basket nào ở bất kỳ mode nào bị thiếu chân do dưới sàn volume broker
(0 lần "risk tính ra dưới mức sàn" trong log ở cả 5 lần chạy).

### 2.2. Kiểm tra tỷ lệ TP:SL đúng công thức (mẫu, mọi basket đều khớp)

- **FixedTP**: `tpDist/slDist = 1.0000` ở mọi basket — đúng vì
  `KtpLevel=KslLevel=Fib0618`.
- **PartialScaleOut4 / LadderRunner**: ladder tại index 3,4,5(,6) từ
  `KtpLevel=3` → TP = 0.618/0.786/1.0(/1.272)×ATR — đúng công thức
  `entryPrice + signal × ToRatio((FibLevel)(startIndex+i)) × atr`.
- **FibCompensating3**: tỷ lệ TP1:TP2:TP3 so với SL luôn đúng
  `2.0583 : 3.2363 : 4.2362` ở MỌI basket (KslLevel=Fib0618 → yêu cầu
  TP1≥2×0.618=1.236 → mức Fib nhỏ nhất thoả là Fib1272=1.272, nên
  1.272/0.618=2.0583; 2.0/0.618=3.2363; 2.618/0.618=4.2362 — khớp chính xác
  `FindCompensatingTp1Index`).

### 2.3. Tỷ trọng volume giữa các chân (mẫu, mọi basket đều khớp)

- **PartialScaleOut4 / LadderRunner (3 chân ladder đầu)**: tỷ trọng đúng
  golden-ratio `44.7% / 27.6% / 17.1%` (+`10.6%` cho chân 4: TP4 với
  PartialScaleOut4, Runner với LadderRunner).
- **PartialBreakeven**: đúng `61.8% (chân A) / 38.2% (chân B)`.
- **FibCompensating3**: đúng chia ĐỀU `33.3%` × 3 (volume 3 chân bằng nhau
  tuyệt đối trong mọi basket mẫu đã kiểm tra).

---

## 3. Kiểm chứng cơ chế đặc thù từng mode — đối chiếu trực tiếp thiết kế

| Mode | Cơ chế thiết kế | Bằng chứng thực tế |
|---|---|---|
| FixedTP | Đặt xong là xong, không có logic chạy thêm | 0 lần điều chỉnh SL — khớp |
| PartialScaleOut4 | Đặt xong là xong (4 chân TP tĩnh) | 0 lần điều chỉnh SL — khớp |
| LadderRunner | Chân Runner được BẬT trail sau khi 1 chân ladder cùng basket thắng, dời SL liên tục MỖI BAR | Runner bị dời SL từ **1 đến 39 lần/vị thế** (phân bố rải đều, không phải 1 lần cố định) — khớp đúng "trail liên tục" |
| PartialBreakeven | Chân B dời SL về breakeven ĐÚNG 1 LẦN khi chân A thắng, không lặp lại | **100% (49/49)** vị thế bị điều chỉnh đúng 1 lần duy nhất, **100%** SL mới = EntryPrice — khớp tuyệt đối |
| FibCompensating3 | Chân xa nhất còn mở dời SL về breakeven khi 1 chân khác cùng basket thắng | **100% (57/57)** lần điều chỉnh có SL mới = EntryPrice — khớp tuyệt đối |

**Kết luận phần 2-3: không phát hiện bất kỳ sai lệch logic nào giữa code và
hành vi thực tế ở cả 5/5 mode.**

---

## 4. Hai vấn đề hạ tầng đã phát hiện (KHÔNG phải lỗi logic Exit Mode)

### 4.1. `ModifyExpirationTime` thất bại ~10.14% — do giờ đóng phiên HK50

| Mode | ExpireModFail / Tổng lệnh | Tỷ lệ |
|---|---|---|
| FixedTP | 44 / 434 | 10.14% |
| PartialBreakeven | 88 / 868 | 10.14% |
| FibCompensating3 | 132 / 1,302 | 10.14% |
| PartialScaleOut4 | 176 / 1,736 | 10.14% |
| LadderRunner | 176 / 1,736 | 10.14% |

**Tỷ lệ giống hệt nhau tới 4 chữ số thập phân ở cả 5/5 mode** — bằng chứng
dứt khoát đây là đặc tính của TẦNG THỰC THI (thời điểm đặt lệnh so với giờ
đóng phiên), hoàn toàn độc lập với ExitMode logic.

**Cơ chế xác nhận qua đối chiếu ngày-giờ thực tế** (mẫu từ lần chạy
FibCompensating3, `log.txt`):

| Cụm độ trễ | ExpireTime (dự định) | Lúc log ghi nhận FAILED | Giải thích |
|---|---|---|---|
| ~3200 phút (~53h) | **Thứ 6, 20:00** | **Thứ 2, 01:20** | Đúng khoảng nghỉ cuối tuần |
| ~320 phút (~5.3h) | **20:00 mỗi ngày** | **01:20 hôm sau** | Đúng khoảng nghỉ hàng ngày của HK50 (20:00→01:20) |
| ~5 phút | Giữa phiên | Ngay sau đó | Không liên quan giờ đóng phiên, có thể là trễ xử lý bình thường |

**Cơ chế suy luận**: `PlaceStopOrder(...)` (tạo lệnh) được cTrader xử lý
ĐỒNG BỘ, ngay lập tức — mọi "Create Stop Order" đều đúng giờ. Nhưng
`PendingOrder.ModifyExpirationTime(...)` (gọi SAU khi tạo, trong cùng hàm
`PlaceLeg`) có vẻ được xử lý như **1 yêu cầu round-trip riêng, chờ tick giá kế
tiếp mới thật sự áp dụng** — nếu tick kế tiếp đó rơi vào SAU khi phiên đã
đóng, chỉ được xử lý khi phiên MỞ LẠI, lúc đó `ExpireTime` đã ở QUÁ KHỨ nên bị
từ chối với `"TechnicalError"`.

**Hậu quả**: ~10% pending order không có đúng hạn 1-bar như thiết kế — với
`Reversal=Immediate` (như 5 lần chạy này) vẫn được dọn khi tín hiệu kế tiếp
tới (`CancelExistingPendingOrder()`), nhưng với `Reversal=HoldBothWithDecay`
(không có cơ chế huỷ nào) hậu quả sẽ nặng hơn — **CHƯA kiểm chứng bằng dữ
liệu thật cho HoldBothWithDecay, chỉ mới suy luận lý thuyết.**

**Chưa có fix.** 2 hướng đã bàn với người dùng nhưng CHƯA CHỌN — xem
`AGENT.md §5` mục 3.

### 4.2. Margin reject giảm dần theo số chân/basket

| Mode | Số chân | Margin reject / Tổng lệnh | Tỷ lệ |
|---|---|---|---|
| FixedTP | 1 | 131 / 434 | **30.18%** |
| PartialBreakeven | 2 | 131 / 868 | 15.09% |
| FibCompensating3 | 3 | 157 / 1,302 | 12.06% |
| PartialScaleOut4 | 4 | 139 / 1,736 | 8.01% |
| LadderRunner | 4 | 138 / 1,736 | 7.95% |

**Quan hệ đơn điệu rõ ràng theo số chân**: 1 chân → 30.18%, 2 → 15.09%,
3 → 12.06%, 4 → ~8%. Risk luôn cố định 2% dù chia mấy chân (nguyên tắc #4
trong CLAUDE.md: chia nhỏ VOLUME, không chia risk%) — nhưng vì chia thành
NHIỀU LỆNH ĐỘC LẬP (không phải 1 lệnh đa chân atomic), mỗi lệnh nhỏ hơn thì
càng ít khả năng đơn lẻ vượt margin còn trống tại thời điểm khớp — nên
càng chia nhiều chân, tỷ lệ bị từ chối/lệnh càng thấp, dù TỔNG risk giống hệt
nhau.

**Phát hiện sâu hơn riêng cho FibCompensating3 — thứ tự chân bị từ chối
KHÔNG đều:**

| Chân (thứ tự đặt lệnh trong code) | Mục tiêu TP | Số lần bị từ chối margin |
|---|---|---|
| Chân 1 (đặt lệnh **đầu tiên**) | TP1 — mức GẦN nhất, mang guarantee toán học | **131/157 (83%)** |
| Chân 2 | TP2 = Fib2000 | 26/157 (17%) |
| Chân 3 (đặt lệnh **cuối cùng**) | TP3 = Fib2618 — mức XA nhất | **0/157 (0%)** |

**Suy luận**: cTrader có vẻ xử lý khớp lệnh của các order cùng giá trigger
theo thứ tự **NGƯỢC** với thứ tự tạo lệnh (chân đặt SAU được thử khớp
TRƯỚC, ăn margin còn trống trước; chân đặt ĐẦU bị đẩy xuống cuối hàng đợi,
dễ hết margin nhất). **Hệ quả nghiêm trọng**: chân TP1 — chân MANG đảm bảo
toán học "1 mình bù đủ 2 chân thua" — lại là chân dễ KHÔNG ĐƯỢC ĐẶT NHẤT khi
margin căng (thường đúng lúc account đang thua lỗ, tức đúng lúc cần guarantee
này nhất). **Chưa kiểm tra thứ tự chân bị từ chối cho PartialScaleOut4 /
LadderRunner (4 chân) — nên làm nếu cần xác nhận quy luật "đảo ngược thứ tự"
này có tổng quát hay chỉ riêng FibCompensating3.**

**Chưa có fix.** Hướng khả dĩ: đảo ngược vòng lặp trong
`PlaceFibCompensating3Legs` (đặt TP3 trước, TP1 sau cùng) để TP1 được ưu
tiên margin — **CHƯA làm, cần hỏi người dùng trước** (xem `AGENT.md §5` mục 2).

### 4.3. Tỷ lệ bị đóng ngang giữa chừng (do Reversal=Immediate) — khớp lý thuyết

| Mode | Có chân "chạy vô hạn, không TP"? | Bị đóng ngang / Filled |
|---|---|---|
| FixedTP | Không | 0/135 = 0% |
| PartialScaleOut4 | Không | 4/925 = 0.43% |
| FibCompensating3 | Không | 5/641 = 0.78% |
| LadderRunner | Có (Runner) | 17/926 = 1.84% |
| PartialBreakeven | Có (chân B) | 22/401 = **5.49%** |

Khớp đúng suy luận lý thuyết đã bàn trước đó trong cuộc hội thoại: mode nào
có chân "không TP, chạy tới tín hiệu đảo chiều mới đóng" thì tỷ lệ bị cắt
ngang giữa chừng cao hơn hẳn (PartialBreakeven cao nhất, gấp ~13 lần
PartialScaleOut4) — vì các chân này CÓ THỂ vẫn đang mở khi 1 tín hiệu đảo
chiều mới tới, và dưới `Reversal=Immediate` sẽ bị `CloseExistingPosition()`
đóng ngay bất kể đang lời/lỗ dở dang.

---

## 5. Kết luận tổng thể

1. **Cả 5/5 Exit Mode đều thực thi ĐÚNG 100% theo thiết kế trong code** — đã
   kiểm chứng bằng dữ liệu backtest thật (không chỉ đọc code): đúng số
   chân/basket, đúng tỷ lệ TP:SL, đúng tỷ trọng volume, đúng cơ chế
   breakeven/trailing riêng của từng mode, cân đối đóng lệnh khớp tuyệt đối
   100%. **Không phát hiện sai lệch logic nào.**
2. **2 vấn đề tồn tại là vấn đề TẦNG THỰC THI/HẠ TẦNG** (broker margin engine,
   giờ đóng phiên của symbol), ảnh hưởng có QUY LUẬT RÕ RÀNG và đã lượng hoá
   đầy đủ — không phải lỗi thiết kế Exit Mode, nhưng **có hệ quả thực tế đáng
   kể** (đặc biệt phát hiện ở §4.2: chân mang guarantee toán học của
   FibCompensating3 dễ bị "mất" nhất khi margin căng) — cần người dùng quyết
   định có đáng sửa hay chấp nhận làm giới hạn đã biết.

---

## 6. Phương pháp luận (để tái sử dụng cho lần kiểm chứng sau)

Các kỹ thuật PowerShell đã dùng, tái sử dụng được cho bất kỳ file
`events.json`/`log.txt` nào của cTrader:

- **Tái tạo basket** (lô lệnh của 1 tín hiệu): nhóm sự kiện `Create Stop
  Order` theo khoá `"$entryPrice|$sl|$type"` — mọi chân của 1 tín hiệu luôn
  đặt CHUNG entryPrice+SL (xem `OpenOrReverse` trong `Combo.cs`).
- **Kiểm tra cân đối đóng lệnh**: `Filled == SLHit + TPHit + PositionClosed`
  phải khớp tuyệt đối — lệch là có lệnh bị đếm sai/rơi mất.
- **Đếm số lần điều chỉnh SL / 1 vị thế**: nhóm sự kiện `Position Modified
  (S/L)` theo `positionId` — phân biệt cơ chế "1 lần" (breakeven tĩnh) vs
  "nhiều lần" (trail liên tục).
- **Phát hiện lỗi margin/expiration**: grep `log.txt` (không phải
  `events.json` — 2 loại lỗi này KHÔNG xuất hiện trong `events.json`) tìm
  `NOT_ENOUGH_MARGIN_BALANCE` và regex
  `Modifying pending order.*?FAILED` (kèm trích `ExpireTime:` để đối chiếu
  ngày-giờ).
- **Xác định vị trí chân trong basket** (đặt đầu/giữa/cuối): dùng thứ tự
  `orderId` tăng dần trong danh sách `Create Stop Order` của cùng 1 basket
  (orderId thấp = tạo trước).
