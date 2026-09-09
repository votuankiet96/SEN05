# Audit: công thức & cách tính lot size — độ an toàn cho mọi loại tài khoản

Ngày 2026-09-06. Đối tượng audit: `Combo/Combo/Combo.cs` và `MA Cross/MA Cross/MA Cross.cs`,
region `Risk & Position Sizing` (`CalculateVolume` + `CapVolumeByMargin`) và region
`Position Management`. Cả 2 cBot dùng **cùng một khối code** (chỉ khác tiền tố log và
cách đặt lệnh: Combo = Pending Stop giá tuyệt đối, MA Cross = Market + SL/TP theo pips
so với giá khớp).

Chưa build/test lại — máy không có compiler. Đây là audit đọc code + suy luận, không sửa code.

**Cập nhật 2026-09-06:**
- **Vòng 2 (đối chứng NỘI BỘ, Phần F):** dữ liệu backtest thật (MARGIN GUARD log), 3 báo
  cáo margin/leverage có sẵn của dự án, lịch sử code. B1 xác nhận bằng số thật.
- **Vòng 3 (đối chứng NGOÀI, Phần G):** tài liệu cTrader Algo chính thức, forum Spotware
  (bug đã xác nhận), luật FTMO chính thức, best-practice quản trị rủi ro. **Công thức
  sizing = chuẩn ngành, không có lỗi công thức.** Mọi rủi ro audit chỉ ra đều được nguồn
  ngoài xác nhận + có giải pháp trùng best-practice. Phát hiện MỚI từ research ngoài:
  `GetEstimatedMargin` có bug báo margin quá thấp cho dynamic-leverage CFD → B2 nâng cấp;
  lệch luật FTMO cụ thể (mốc CEST, baseline restart) → B7/B10 nâng lên [TRUNG BÌNH].
- **Bot trong repo:** grep toàn bộ `Sources/`, `Data/cBots/`, `.algo` → **đúng 2 cBot**
  (Combo, MA Cross), dùng **cùng khối code sizing** (đọc trực tiếp cả 2, xác nhận giống hệt).

---

## Phần A — Công thức HIỆN TẠI đảm bảo đúng những gì

```
riskAmount      = Account.Balance × RiskPercent / 100
requestedVolume = riskAmount / (stopLossPips × Symbol.PipValue)
                  (stopLossPips = KSL × ATR / Symbol.PipSize)
volume          = NormalizeVolumeInUnits( min(requestedVolume, VolumeInUnitsMax), Down )
volume          = CapVolumeByMargin(volume)   // giảm nếu GetEstimatedMargin > min(FreeMargin, Equity×MaxMarginPercent/100)
```

Những điều **đúng và an toàn** (không cần đụng):

1. **Chiều làm tròn an toàn.** `RoundingMode.Down` + margin-cap chỉ giảm → volume cuối
   luôn ≤ requestedVolume → **rủi ro thực (nếu SL khớp đúng giá SL) luôn ≤ RiskPercent%**.
   Không có đường nào làm rủi ro *vượt* mức khai báo trong điều kiện khớp lệnh bình thường.
2. **Nhất quán tiền tệ.** `Balance`, `Equity`, `FreeMargin`, `PipValue`,
   `GetEstimatedMargin` đều theo tiền tài khoản → công thức đúng cho tài khoản USD/EUR/GBP/…
   (đã audit riêng `PipValue` 6 symbol, PASS — xem `pipvalue-currency-conversion-audit-2026-09-02.md`).
3. **Guard chia 0 / giá trị vô lý.** `stopLossPips <= 0 || Symbol.PipValue <= 0 → return 0`.
   ATR ≤ 0 và entry ≤ 0 đã bị loại ở khâu đọc CSV. Balance âm → riskAmount âm →
   requestedVolume âm → `< VolumeInUnitsMin` → skip. **Suy biến an toàn (fail-safe).**
4. **Sàn / trần volume broker.** `< VolumeInUnitsMin` → bỏ lệnh, log rõ. `> VolumeInUnitsMax`
   → kẹp về max, log rõ.
5. **2 tầng margin độc lập, lấy MIN.** `min(FreeMargin, Equity×MaxMarginPercent/100)` —
   FreeMargin lo trường hợp đang có nhiều lệnh khác mở; MaxMarginPercent là trần chủ động
   luôn kiểm tra. Đúng như thiết kế 2026-09-02.
6. **MA Cross sạch hơn Combo ở khâu slippage entry** — SL/TP đặt theo *pips so với giá
   khớp thật*, nên rủi ro = `volume × stopLossPips × PipValue` đúng tuyệt đối so với entry
   thật (xem F4).

---

## Phần B — Phát hiện (xếp theo mức độ)

### B1 — [CAO] Margin-cap KHÔNG phải notional-cap: không an toàn trên tài khoản đòn bẩy cao

`CapVolumeByMargin` chặn **margin** (`GetEstimatedMargin`), không chặn **notional/đòn bẩy
hiệu lực của riêng lệnh đó**. Sắp xếp lại ràng buộc:

```
estMargin ≤ Equity × MaxMarginPercent/100
estMargin = Notional / EffectiveLeverage
⇒ Notional ≤ Equity × (MaxMarginPercent/100) × EffectiveLeverage
```

Cùng một `MaxMarginPercent = 50` (default hiện tại) cho phép notional rất khác nhau:

| Đòn bẩy hiệu lực (symbol/tài khoản) | Notional tối đa 1 lệnh | Gap ngược bao nhiêu % thì mất 100% Equity (TRƯỚC khi chạm SL) |
|---|---|---|
| 1:15  (US30.cash / FTMO) | 7.5 × Equity | ~13.3% |
| 1:30  (FX majors, nhiều broker) | 15 × Equity | ~6.7% |
| 1:100 | 50 × Equity | ~2.0% |
| 1:500 (FX retail phổ biến) | **250 × Equity** | **~0.4%** |

→ Trên tài khoản 1:500 với `MaxMarginPercent=50`, **một cú gap 0.4% ngược hướng trước khi
giá kịp chạm SL là cháy tài khoản** — dù "risk 1%" trên giấy vẫn đúng tuyệt đối. Ngay cả
default cũ `MaxMarginPercent=10` cũng chỉ nâng ngưỡng gap-cháy lên ~2% ở 1:500.

**⚠️ Xác nhận bằng số liệu THẬT — vấn đề bén ngay trên chính tài khoản FTMO đang dùng,
không cần tài khoản đòn bẩy cao:** đối chứng `estimatedMargin` từ log MARGIN GUARD của 4
lượt backtest phiên này + gap% lịch sử thật trong `max-margin-percent-safe-ceiling-2026-09-03.md`:

| Symbol (lượt thật) | Đòn bẩy hiệu lực đo được | Gap% tệ nhất lịch sử | **Lỗ 1 cú gap @ MaxMargin=50% hiện tại** |
|---|---:|---:|---:|
| US500.cash (Combo) | 15.0 (75,336÷5,020) | 5.38% (2018-12-26) | **~40% Equity** |
| US100.cash (MA Cross) | 15.3 (165,614÷10,838) | 6.61% (2018-12-26) | **~51% Equity** |
| GER40.cash / DE40 (MA Cross) | 12.4 (96,186÷7,764) | 9.95% (Brexit 2016) | **~62% Equity** |
| BTCUSD (Combo) | 1.31 (47,430÷36,083) | 9.03% (2018-03-18) | ~5.9% Equity (an toàn — đòn bẩy thấp tự chặn) |

→ Trên FTMO indices (đòn bẩy hiệu lực ~12–15, đo thật), `MaxMarginPercent=50` cho phép
**một cú gap qua đêm/cuối tuần cỡ đã từng xảy ra thật ăn 40–62% Equity** — gấp 4–6 lần
hạn mức max-drawdown 10% của FTMO. Đây **không phải kịch bản giả định "tài khoản 1:500"** —
nó là rủi ro hiện hữu trên chính tài khoản đang chạy.

Margin-cap hiện tại **chỉ đủ an toàn cho BTCUSD** (đòn bẩy hiệu lực ~1.2–1.3 tự giới hạn
notional). Với mọi symbol còn lại, và với mọi tài khoản đòn bẩy cao hơn FTMO, nó **không
chặn được tail risk**.

**Ghi chú:** margin-cap bám vào một con số (`EffectiveLeverage`) mà cBot **không kiểm soát
và không biết trước** — nó do broker/symbol quyết định, có thể là *dynamic leverage theo
bậc notional*. Không có tầng nào trong code hiện tại đặt trần độc lập với con số đó.

### B2 — [TRUNG BÌNH] Không sanity-check giá trị `GetEstimatedMargin` trả về (+ null-check có thể là dead code)

```csharp
double? estimatedMargin = Symbol.GetEstimatedMargin(tradeType, volume);
if (estimatedMargin == null)
    return volume;   // <-- trả về volume risk-based đầy đủ, KHÔNG chặn gì
```

Hai vấn đề:
1. **Null-check có thể vô nghĩa.** Tài liệu cTrader Algo hiện tại ghi chữ ký
   `public abstract double GetEstimatedMargin(TradeType, double)` — **`double` KHÔNG nullable**
   (xem G2). Nếu đúng vậy, `estimatedMargin == null` luôn `false` → nhánh fail-open này là
   **dead code**. (Nếu bản cAlgo đang cài trả `double?` thì nhánh sống, và nó fail-OPEN như
   mô tả — cần xác minh khi build.)
2. **Quan trọng hơn: không kiểm giá trị trả về có hợp lý không.** cTrader có **bug đã được
   Spotware xác nhận**: backtest CFD với dynamic leverage báo margin **thấp hơn thực rất
   nhiều** (case forum: $30 thay vì $3,246 — xem G2). Một `estimatedMargin` rác-thấp như vậy
   → `estimatedMargin <= marginBudget` → **cap KHÔNG kích hoạt** → lệnh đi với volume
   risk-based đầy đủ (đúng case B1). Không có check `estimatedMargin <= 0 || IsNaN` hay
   "margin < X% notional lý thuyết thì nghi ngờ".

An toàn hơn: fail-CLOSED khi giá trị bất thường — dùng ước tính notional dự phòng
(`volume × Bid × PipValue / PipSize ÷ AssumedLeverage`) và lấy MAX với giá trị API.

### B3 — [TRUNG BÌNH] Margin phi tuyến (tiered) không được kiểm lại sau khi scale

```csharp
double scaledVolume = volume * (marginBudget / estimatedMargin.Value) * 0.98;
double normalizedVolume = NormalizeVolumeInUnits(scaledVolume, Down);
// ... KHÔNG gọi lại GetEstimatedMargin(normalizedVolume) để xác nhận
```

Comment code tự nhận "Margin xấp xỉ tuyến tính theo volume đối với CFD chuẩn". Với
**dynamic leverage theo bậc** (cTrader hỗ trợ, và FTMO/nhiều broker dùng cho index/vàng/
crypto), margin **không** tuyến tính: cắt volume một nửa có thể rơi xuống bậc đòn bẩy
cao hơn → margin/unit *giảm*, nên `normalizedVolume` vẫn có thể *vẫn vượt* `marginBudget`
(hiếm, vì thường lệch theo hướng an toàn) hoặc bị cắt *quá tay* (thường gặp hơn — mất cơ
hội, không nguy hiểm). Chưa có vòng lặp/verify.

### B4 — [TRUNG BÌNH, tình huống] Combo size theo `signal.EntryPrice`, không theo giá khớp thật

Combo: `stopLossPips = |signal.EntryPrice − stopLossPrice| / PipSize`, SL đặt **giá tuyệt
đối** `stopLossPrice`. Nếu Stop Order khớp lệch bất lợi (Buy stop khớp *cao hơn*
`signal.EntryPrice` do slippage/gap-through), thì khoảng cách **entry thật → SL cố định**
LỚN HƠN `stopLossPips` đã dùng để tính volume →

```
rủi ro thực = volume × (entryThật − stopLossPrice) × VPP  >  RiskPercent% mục tiêu
```

Backtest tick: slippage nhỏ (thấy ~0.01–0.02%, không đáng kể). Live / gap cuối tuần:
có thể lớn. MA Cross **không dính lỗi này** (SL theo pips so với fill). Combo có thể bù
bằng cách tính volume trên `stopLossPips` cộng thêm 1 buffer nhỏ, hoặc chấp nhận và ghi rõ.

### B5 — [TRUNG BÌNH] "Risk = RiskPercent%" là **kịch bản tốt nhất**, không phải chặn trên

Công thức đảm bảo đúng risk% **CHỈ KHI SL khớp đúng tại giá SL**. Thực tế:
- SL khớp trượt (fast market) → lỗ > risk%.
- Gap xuyên SL (mở cửa đầu tuần, tin tức) → lỗ có thể gấp nhiều lần risk%.
- Không có tham số `SlippageBufferPips` / hệ số hao để size thận trọng hơn mức lý thuyết.

Với tài khoản prop (FTMO) có luật lỗ ngày/tổng cứng, một SL trượt mạnh đơn lẻ đủ để vi
phạm. Không nguy hiểm cho tài khoản retail thường, nhưng "an toàn mọi loại tài khoản" thì
phải hoặc (a) size dưới mức lý thuyết một chút, hoặc (b) tối thiểu tài liệu hoá rõ đây là
best-case.

### B6 — [TRUNG BÌNH] Position Management — phanh thảm hoạ — MẶC ĐỊNH TẮT + kiểm theo nến, không theo tick

- `EnableDailyLossLimit`, `EnableMaxDrawdown`, `EnableMaxConsecutiveLosses` đều
  `DefaultValue = false`. Đây là lớp duy nhất chặn "cháy tài khoản" khi B1/B4/B5 xảy ra —
  và nó tắt sẵn. CLAUDE.md nói "Người dùng giao dịch qua FTMO → Position Management theo
  chuẩn FTMO nên có mặt trong mọi cBot" — *có mặt* nhưng *không bật*.
- `UpdatePositionManagement` chỉ chạy trong `OnBarClosed` + đầu `ProcessScheduledSignals`
  (khi có signal tới). **Không chạy mỗi tick.** Trên H1, ngưỡng equity-breach chỉ được
  kiểm mỗi giờ. Một cú sập trong nến không bị bắt tới tận nến đóng kế tiếp — với luật cứng
  của prop firm, đây là lỗ hổng thật.
- `ForceCloseAll()` đóng ở market → trên gap, khớp xa ngưỡng → lúc đó đã vi phạm rồi.

### B7 — [TRUNG BÌNH] `MaxDrawdown` dùng `_initialBalance = Account.Balance` lúc OnStart — sai baseline khi restart live

`_initialBalance = Account.Balance` lúc OnStart; `threshold = _initialBalance × (1 −
MaxTotalDrawdownPercent/100)`.

- **Backtest:** `_initialBalance` = `--balance` = đúng vốn ban đầu. OK.
- **Live:** nếu bot được **khởi động lại** khi tài khoản đã có lời/lỗ (P&L ≠ 0), thì
  `_initialBalance` = balance *lúc restart*, KHÔNG phải vốn cấp ban đầu. Ví dụ: tài khoản
  $10k, đã lời lên $10.8k, restart bot → `_initialBalance = 10,800` → threshold =
  $9,720. Nhưng luật FTMO (tĩnh) huỷ tài khoản ở **$9,000** (10% dưới $10k). Bot cho phép
  drawdown SÂU HƠN luật → vi phạm FTMO trước khi bot kịp `ForceCloseAll`. Ngược lại nếu
  restart sau khi lỗ, bot lại quá chặt.
  → **Cần `[Parameter] InitialCapital` cấu hình cứng, không lấy từ `Account.Balance`.**
- FTMO Academy (nguồn chính thức, xem G3) mô tả Max Loss là **tĩnh 10% dưới vốn ban đầu**
  → công thức tĩnh của bot khớp *nếu* baseline đúng. (Vài nguồn bên thứ ba mô tả bản
  "ratcheting/khoá lời" — nếu FTMO dùng bản đó cho sản phẩm của người dùng thì bot quá
  lỏng sau khi tài khoản tăng; cần xác nhận đúng loại tài khoản.)

### B8 — [THẤP, đã biết] `PipValue` là ảnh chụp lúc cBot khởi động

Đã tài liệu hoá (CLAUDE.md + audit 2026-09-02). Với symbol quote khác tiền tài khoản
(EUR/JPY/HKD…), `Symbol.PipValue` không cập nhật real-time → risk% thực trôi nhẹ theo tỷ
giá trong run/phiên dài. Không nghiêm trọng cho backtest ngắn; đáng lưu ý cho tài khoản
non-USD chạy bot dài ngày.

### B9 — [THẤP] `Account.Balance` giả định = vốn giao dịch được

Nếu tài khoản có **credit/bonus** cộng vào Balance (một số broker retail, tài khoản
khuyến mãi), `riskAmount = Balance × RiskPercent/100` tính trên cả phần bonus → rủi ro
trên vốn thật cao hơn khai báo. FTMO không làm vậy nên hiện không ảnh hưởng, nhưng "mọi
loại tài khoản" thì cần trừ `Account.Credit` (nếu API có) khỏi cơ sở tính.

### B10 — [TRUNG BÌNH] Daily Loss của bot ≠ luật FTMO về cả mốc thời gian LẪN công thức

FTMO chính thức (G3): Max Daily Loss = **5% của Vốn Ban Đầu**, tính lại lúc **nửa đêm
CE(S)T** (UTC+1/+2), ngưỡng = `Balance lúc nửa đêm CEST hôm trước − 5% × Vốn Ban Đầu`,
so theo **equity** (gồm cả P&L nổi + phí + swap).

Bot: `RolloverDayIfNeeded` dùng `barTime.Date` (**UTC**), `_dailyReferenceBalance =
Account.Balance` đầu ngày, ngưỡng = `_dailyReferenceBalance × (1 − MaxDailyLossPercent/100)`.

3 điểm lệch:
1. **Mốc reset**: UTC vs nửa đêm CE(S)T → lệch 1–2 giờ. Cửa sổ ngày của bot lệch pha với
   luật → có thể vi phạm trong "khe" giữa 2 định nghĩa ngày.
2. **Cơ sở trừ**: bot trừ `5% × balance-đầu-ngày` (%), FTMO trừ `5% × Vốn-Ban-Đầu` ($ cố
   định). Sau khi tài khoản tăng, `5% × balance hiện tại > 5% × vốn ban đầu` → bot LỎNG
   hơn luật. Sau khi giảm thì chặt hơn.
3. **Dùng `Account.Balance` đầu ngày** — nếu bot restart giữa ngày, mất mốc balance
   nửa-đêm thật.
→ Cần: mốc reset theo offset giờ cấu hình được (mặc định CE(S)T), và trừ theo
`InitialCapital` cố định.

### B11 — [THÔNG TIN] `Account.Balance` (risk) vs `Account.Equity` (margin) — không phải lỗi

Trộn 2 cơ sở là **đúng**: risk% nên theo Balance (định nghĩa luật FTMO cũng vậy), margin
nên theo Equity (tiền thật đang có). Hệ quả cần biết: khi đang có lệnh lỗ nổi (chưa
đóng), Balance chưa giảm → lệnh mới vẫn size theo Balance cũ → hơi "aggressive" trong lúc
đang drawdown. Đây là lựa chọn thiết kế của người dùng (CLAUDE.md), không sửa.

---

## Phần C — Ma trận theo loại tài khoản

| Loại tài khoản | Công thức risk% | Margin-cap hiện tại | Rủi ro còn lại |
|---|---|---|---|
| **FTMO indices, notional lớn** (đang dùng, đòn bẩy hiệu lực đo ~12–15) | OK | **KHÔNG đủ** — B1 xác nhận số thật: 1 gap lịch sử ăn **40–62% Equity** @ MaxMargin 50% | B1 [CAO], B5, B6, B10 |
| **FTMO, tài khoản NHỎ / SL rộng** (notional nhỏ → tier đòn bẩy có thể tới 1:50) | OK | **KHÔNG đủ** — notional cho tới **25× Equity** | B1 tệ hơn |
| **FTMO Forex major** (tới 1:100 — nếu mở rộng bot sang FX) | OK | **KHÔNG đủ** — notional tới **50× Equity** | B1 rất nghiêm trọng |
| **FTMO BTCUSD** (đòn bẩy hiệu lực ~1.3) | OK | **Đủ** (notional ≤ ~0.65× Equity) | Chủ yếu B5, B6 |
| **Retail đòn bẩy 1:500** | OK | **KHÔNG đủ** (notional tới 250× Equity, gap 0.4% = cháy) | B1 cực nghiêm trọng |
| **Tài khoản tiền tệ ≠ USD** | OK (PipValue tự quy đổi) | OK | B8 (drift PipValue snapshot — chính thức, nếu chạy dài) |
| **Tài khoản có bonus/credit** | **Lệch** (B9: risk tính cả bonus) | OK | B9 — trừ Credit khỏi cơ sở |
| **Cent / micro account** | OK (đơn vị nhất quán) | phụ thuộc đòn bẩy | B1 nếu đòn bẩy cao |
| **Netting (không phải hedging)** | OK | OK | `ReconcileExistingExposure` đóng lệnh ngược trước khi mở — đúng cả 2 chế độ; cần xác nhận `Close()` + mở lệnh mới đồng bộ trên live (backtest thì đồng bộ) |
| **Bot restart giữa đời tài khoản (live)** | OK | OK | **B7 [TB]** — `_initialBalance` sai baseline → PM lệch luật FTMO |

---

## Phần D — Khuyến nghị (ưu tiên từ trên xuống)

### D1 — [BẮT BUỘC cho "mọi loại tài khoản"] Thêm trần NOTIONAL / gap độc lập với margin

Đây là mảnh còn thiếu. **Chính dự án đã thiết kế sẵn công thức này** trong
`max-margin-percent-safe-ceiling-2026-09-03.md` (`Lỗ khi gap = gap% × Đòn_bẩy × Equity ×
MaxMarginPercent%`) nhưng ghi rõ **"Chưa triển khai code"** — D1 = code hoá nó, tách thành
tham số RIÊNG.

**Vì sao phải là tham số riêng, không phải chỉ hạ `MaxMarginPercent` xuống mức an toàn
(1–3%)?** `margin-percent-sensitivity-2026-09-03.md` đo thật: ở `MaxMarginPercent ≤ 30%`,
**97–100% lệnh bị cap, `RiskPercent=1%` gần như vô nghĩa** (risk thực chỉ 0.03–0.55%). Muốn
`RiskPercent` thật sự có tác dụng thì `MaxMarginPercent` phải ~50–100%. → Hai mục tiêu
(để RiskPercent hoạt động **vs** chặn tail-risk gap) **xung đột nếu dùng chung 1 knob**.
Giải pháp: giữ `MaxMarginPercent` cao cho RiskPercent hoạt động, thêm **gap-cap riêng** chỉ
cắt khi notional/SL-hẹp thật sự nguy hiểm.

Đề xuất 1 trong 2 (hoặc cả 2, lấy MIN):

**Cách 1 — trần theo % lỗ khi gap** (trực quan, đúng mối lo của người dùng):
```
[Parameter] MaxGapLossPercent   // vd 25 (%Equity)
[Parameter] AssumedAdverseGapPercent  // vd 2.0 (%giá) — "gap tệ nhất cần chịu được"

VPP = Symbol.PipValue / Symbol.PipSize;   // giá trị 1 điểm giá / 1 unit, tiền tài khoản
maxLossOnGap = Equity × MaxGapLossPercent/100;
gapCapVolume = maxLossOnGap / (Symbol.Bid × AssumedAdverseGapPercent/100 × VPP);
volume = min(volume, NormalizeVolumeInUnits(gapCapVolume, Down));
```
Ý nghĩa: "một cú gap `AssumedAdverseGapPercent`% ngược hướng, TRƯỚC khi chạm SL, không
được làm lỗ nổi quá `MaxGapLossPercent`% Equity". Hoàn toàn account-agnostic (mọi số hạng
tiền tài khoản, **không phụ thuộc đòn bẩy broker**). Tự động bóp mạnh đúng lúc SL hẹp
bất thường (SLdist/price nhỏ) — chính là case nguy hiểm.

**Cách 2 — trần đòn bẩy hiệu lực / notional** (đơn giản hơn):
```
[Parameter] MaxNotionalMultiple   // vd 10 (lần Equity)
notional = volume × Symbol.Bid × VPP;
if (notional > Equity × MaxNotionalMultiple)
    volume = min(volume, NormalizeVolumeInUnits(Equity × MaxNotionalMultiple / (Symbol.Bid × VPP), Down));
```

Đặt hàm này **cạnh `CapVolumeByMargin`**, gọi ngay sau, cùng cơ chế log + counter.

### D2 — [CAO] Sanity-check `GetEstimatedMargin` + fail-CLOSED khi bất thường
- Tính `notionalMargin = volume × Bid × (PipValue/PipSize) / AssumedLeverage` (AssumedLeverage
  cấu hình, mặc định ~15 cho index / theo asset class).
- `effMargin = Max(GetEstimatedMargin(...), notionalMargin × 0.5)` — nếu API trả số thấp
  bất thường (bug CFD dynamic-leverage đã xác nhận, G2) thì vẫn có sàn.
- Nếu `GetEstimatedMargin` null / ≤ 0 / NaN → dùng `notionalMargin`, log cảnh báo.
- Không bao giờ để "không ước tính được margin" ⇒ đi lệnh full volume.

### D3 — [CAO] Bật sẵn + đồng bộ luật FTMO cho Position Management
- `EnableMaxDrawdown` → `DefaultValue = true` (tối thiểu). Max-DD là luật huỷ tài khoản.
- Gọi `UpdatePositionManagement` trong `OnTick` (không chỉ `OnBarClosed`) — độ trễ 1 nến
  quá nhiều cho luật cứng prop firm.
- Thêm `[Parameter] InitialCapital` (mặc định 0 = tự lấy `Account.Balance` @ OnStart như
  hiện tại; >0 = dùng số cứng) → sửa B7 baseline sai khi restart live.
- Daily-loss: mốc reset theo `[Parameter] DayResetUtcOffsetHours` (mặc định +2 ~ CEST mùa
  hè / +1 mùa đông — hoặc để người dùng set), trừ theo `InitialCapital × MaxDailyLossPercent`
  thay vì balance-đầu-ngày → khớp công thức FTMO (B10, G3).

### D4 — [TRUNG BÌNH] Verify lại sau khi scale margin (B3)
Sau `normalizedVolume`, gọi lại `GetEstimatedMargin(tradeType, normalizedVolume)`; nếu
vẫn `> marginBudget`, lặp giảm 1 step tới khi đạt (giới hạn 3–4 vòng). Xử lý dynamic
leverage.

### D5 — [TRUNG BÌNH] Tham số hoá hệ số hao + (tuỳ chọn) buffer rủi ro
- `MarginSafetyFactor` (đang hardcode `0.98`) → `[Parameter]`, và với Combo (pending,
  khớp trễ) nên cho phép đặt thấp hơn (vd 0.90) vì giá đã dịch về phía stop khi khớp.
- Cân nhắc `RiskSlippageFactor` (vd 0.95) nhân vào `requestedVolume` để size dưới mức lý
  thuyết — hoặc ít nhất ghi rõ trong tài liệu bàn giao rằng risk% là best-case.

### D6 — [THẤP] Trừ `Account.Credit` khỏi cơ sở risk nếu API hỗ trợ (B9); cho phép chọn
mốc reset ngày theo offset giờ server (B10); thêm chế độ `TrailingDrawdown` cho PM (B7).

### D7 — [THẤP] Combo: cân nhắc size theo `stopLossPips` đã cộng buffer, hoặc sau khi khớp
kiểm `Position.EntryPrice` vs `signal.EntryPrice` và log nếu slippage vượt ngưỡng (B4).

---

## Phần E — Không cần đụng

- Chiều làm tròn, guard chia 0, xử lý Balance âm, sàn/trần volume broker: **đều đúng**.
- 2 tầng `min(FreeMargin, MaxMarginPercent×Equity)`: đúng thiết kế.
- Trộn Balance (risk) / Equity (margin): đúng.
- MA Cross đặt SL theo pips-so-với-fill: đúng và tốt hơn Combo, giữ nguyên.
- `PipValue` quy đổi tiền tệ: đã audit PASS, chỉ lưu ý drift (B8), không phải bug.

---

## Phần F — Đối chứng nhiều nguồn (vòng 2, 2026-09-06)

### F1 — Toàn bộ bot trong repo

`Glob **/*.cs` + `Sources/{Export,Indicators,Plugins}` (rỗng) + `.algo` files + `Data/cBots/`:
**đúng 2 cBot** — `Combo/Combo/Combo.cs`, `MA Cross/MA Cross/MA Cross.cs`. Đã đọc trực tiếp
region `Risk & Position Sizing` của cả 2: **cùng một khối `CalculateVolume` + `CapVolumeByMargin`**
(chỉ khác tiền tố log "Combo:"/"MA Cross:" và cách `stopLossPips` được suy ra — Combo từ
|entry−SLprice|, MA Cross từ `KSL×ATR/PipSize`, hai biểu thức **cho cùng một giá trị**).
→ Mọi phát hiện B1–B11 áp dụng **y hệt cho cả 2 bot**.

Legacy (`reports/legacy-exact-match-missing-bar-fallback-2026-09-01.md`, bản trước 2026-09-01):
`CalculateVolume` **không có bước margin nào cả** — chỉ `riskAmount/(SLpips×PipValue)` →
normalize. Code hiện tại là bản cải tiến rõ rệt. Nếu sau này khôi phục exit-mode nhiều chân
(catalog cũ chia VOLUME theo chân) → cần re-audit tương tác margin/chân, hiện không nằm trong
code path.

### F2 — Xác nhận B1 bằng dữ liệu backtest THẬT

Trích `estimatedMargin` từ log `MARGIN GUARD` của 4 lượt phiên này, back-tính đòn bẩy hiệu
lực = notional ÷ estimatedMargin:

| Lượt | Signal wanted | estMargin log | notional ước tính | **Đòn bẩy đo** | Khớp báo cáo cũ? |
|---|---:|---:|---:|---:|---|
| Combo BTCUSD h1 | 0.54 u @ ~87,834 | $36,082.77 | ~$47,430 | **1.31** | ✓ (report: 1.23) |
| Combo US500 h1 | 10.95 u @ ~6,880 | $5,020.60 | ~$75,336 | **15.0** | ✓ (report: 15.06) |
| MACross US100 m10 | 6.52 u @ ~25,401 | $10,837.87 | ~$165,614 | **15.3** | ✓ (report: 15.22) |
| MACross GER40 m30 | 2.55 u @ ~24,600 | $5,063.79 | ~$62,730* | **12.4** | ✓ (report: 12.5–14.6) |

*(GER40 quote EUR — notional/đòn bẩy tính trên giá EUR; con số minh hoạ.)*

→ Đòn bẩy hiệu lực đo được **khớp** với `max-margin-percent-safe-ceiling-2026-09-03.md` và
`leverage-pipvalue-crosscheck-2026-09-04.md`. B1 dùng đúng những con số này → **không phải
giả thuyết**.

### F3 — 3 báo cáo margin/leverage có sẵn của dự án — tất cả CỦNG CỐ audit này

| Báo cáo | Nói gì | Liên hệ audit |
|---|---|---|
| `max-margin-percent-safe-ceiling-2026-09-03.md` | Trần `MaxMarginPercent` **an toàn theo gap lịch sử** cho index chỉ **1–3.5%** (target 2.5%), tối đa 3–6% (target 5%). BTCUSD rộng (22.5%) do đòn bẩy thấp. **"Chưa triển khai code."** | = **D1**. Default 50% hiện tại cao gấp **10–40×** trần này. |
| `margin-percent-sensitivity-2026-09-03.md` | Ở `MaxMarginPercent ≤ 30%`: 97–100% lệnh bị cap, `RiskPercent` vô nghĩa. Ở 50%: risk thực chạm ~0.9%. NetProfit **và** MaxEquityDD đều tăng tuyến tính theo `MaxMarginPercent`. **"Chưa chọn mức khuyến nghị."** | Lý do **không thể** chỉ hạ `MaxMarginPercent` → phải là knob riêng (D1). |
| `leverage-pipvalue-crosscheck-2026-09-04.md` | Đòn bẩy hiệu lực **đổi theo thời gian có thật** — BTCUSD 0.91→1.23 (−26%, mẫu 338, tin cậy). Broker có thể siết đòn bẩy quanh sự kiện lớn. | = **B3**. Một `MaxMarginPercent` hiệu chỉnh theo đòn bẩy đo hôm nay có thể lệch an toàn ngày mai. Gap-cap (D1) không phụ thuộc đòn bẩy → miễn nhiễm. |

### F4 — Mâu thuẫn trong tài liệu dự án (chưa được hoà giải)

Cùng ngày **2026-09-03**, 2 quyết định ngược hướng:
- **CLAUDE.md / memory**: nâng default `MaxMarginPercent` 10% → **50%** — lý do ghi rõ là
  *"ở 50%, hầu hết index/GOLD hầu như không bao giờ bị cap"* (tối ưu **tần suất cap** /
  để RiskPercent hoạt động).
- **`max-margin-percent-safe-ceiling` report**: trần **an toàn gap** cho index là **1–6%**.

Hai phân tích **chưa từng đối chiếu với nhau**. 50% là lựa chọn có ý thức của người dùng
cho *tần suất cap*, nhưng theo phân tích gap của chính dự án nó để lại tail-risk 40–62%
Equity/gap trên FTMO indices. **Cách hoà giải đúng = D1** (2 knob riêng), không phải chọn
1 trong 2 con số.

### F5 — Điểm audit vòng 1 cần chỉnh

- B1 vòng 1 khung theo "tài khoản 1:500" → **quá hẹp**. Vấn đề bén **ngay trên FTMO** (đòn
  bẩy ~12–15). Đã cập nhật B1 với bảng số thật.
- Không tìm thấy phát hiện vòng 1 nào **sai**. Các mục B2–B11 giữ nguyên.

---

## Phần G — Đối chứng NGUỒN NGOÀI (tài liệu cTrader, luật FTMO, best-practice quản trị rủi ro)

Vòng 3, 2026-09-06 — research web ngoài hệ thống. Nguồn ở cuối.

### G1 — Công thức sizing: khớp chuẩn ngành

`volume = (Balance × Risk%/100) ÷ (SL_pips × PipValue)` rồi `floor` theo step — là **công
thức chuẩn**, cả tài liệu cTrader, forum cTrader chính thức, EarnForex, và các hướng dẫn
tính lot size đều dùng đúng dạng này. Combo/MA Cross **khớp 100%**. Ví dụ đối chứng: tài
khoản $10k, risk 1%, SL 50 pip EURUSD (PipValue $10/pip/lot ~ $0.001/pip/unit... theo
đơn vị): $100 ÷ (50 × PipValue) — cùng cấu trúc.

### G2 — cTrader API: xác nhận + cạm bẫy đã biết (tài liệu chính thức + forum Spotware)

| Điểm | Nguồn chính thức nói gì | Ảnh hưởng audit |
|---|---|---|
| **`PipValue` là snapshot** | help.ctrader.com: *"monetary value of one pip... **when you started your cBot**... **not updated in real time and it remains constant**"* | **Xác nhận B8** — chính thức, không phải suy đoán. `TickValue` cũng vậy. |
| **`PipValue` khoá theo symbol khởi động** | Forum: bot chạy trên symbol A, giao dịch symbol B → `Symbol.PipValue` vẫn là của A | Combo/MA Cross **chỉ giao dịch đúng chart symbol** (`SymbolName`) → **không dính**. Nhưng nếu sau này làm đa-symbol thì thành bug sizing nghiêm trọng. |
| **`GetEstimatedMargin` bỏ qua exposure trong backtest** | help.ctrader.com: *"During backtesting or optimisation... **without taking account exposure into consideration**"* | Giải thích vì sao tầng `min(FreeMargin, …)` cần thiết riêng (F3). Trong backtest tuần tự, `FreeMargin` vẫn đúng vì bot chỉ giữ 1 exposure/lần. |
| **`GetEstimatedMargin` chữ ký `double` (non-nullable)** | help.ctrader.com reference: `public abstract double GetEstimatedMargin(TradeType, double)` | **B2 điểm 1**: null-check trong code có thể là dead code. |
| **Bug backtest CFD dynamic-leverage** | Forum, **Spotware xác nhận** ("We managed to reproduce... will fix"): backtest stock CFD báo margin **$30 thay vì $3,246** | **B2 điểm 2 + B3**: engine margin backtest KHÔNG đáng tin tuyệt đối cho symbol dynamic-leverage. Bug này ở stock CFD/PepperStone; index/FTMO chưa chắc dính, nhưng cho thấy `GetEstimatedMargin` có thể trả số rác-thấp → cap không kích hoạt. Cần sanity-check. |
| **Bug `NormalizeVolumeInUnits` precision** | Forum, Spotware xác nhận: `NormalizeVolumeInUnits(20, Down)` trên GOLD trả **19.9** (lỗi floating-point, cTrader 4.0, hứa fix) | Edge case: volume sau normalize có thể *thấp hơn* số truyền vào một chút → nếu tụt dưới `VolumeInUnitsMin` (đã check TRƯỚC normalize) → broker báo "bad volume" → `_orderFailures++`. Suy biến an toàn (không đặt lệnh sai size), nhưng nên clamp `max(normalized, VolumeInUnitsMin)` hoặc check lại sau normalize. |

### G3 — Luật FTMO chính thức (ftmo.com / academy.ftmo.com) vs code Position Management

| Luật FTMO (2-Step Challenge) | Chi tiết chính thức | Code bot |
|---|---|---|
| **Max Daily Loss** | 5% Vốn Ban Đầu. Reset **nửa đêm CE(S)T**. Ngưỡng = `Balance nửa-đêm-CEST hôm trước − 5%×Vốn Ban Đầu`. Theo **equity** (gồm P&L nổi, phí, swap). | Reset **UTC**, trừ **5%×balance-đầu-ngày** (%), theo equity. → **B10**: lệch mốc giờ + lệch công thức. |
| **Max Loss / tổng DD** | Equity không được xuống dưới **90% Vốn Ban Đầu**. FTMO Academy: **TĨNH** (không đổi theo tài khoản tăng). *(Vài nguồn bên thứ 3: có bản ratcheting — cần xác nhận đúng loại tài khoản người dùng.)* | `_initialBalance × (1 − 10%/100)`, `_initialBalance = Account.Balance @ OnStart`. → **B7**: đúng công thức tĩnh NẾU baseline đúng; sai nếu restart bot giữa đời tài khoản. |
| **Đòn bẩy FTMO** | Forex tới **1:100**, Indices tới **1:50**, Metals tới **1:30**, Crypto ~**1:3.3** | Đòn bẩy *hiệu lực* đo được cho index chỉ ~12–15 (dynamic tiering ở notional lớn). **Ở notional NHỎ (tài khoản bé / SL rộng), tier có thể tới 1:50** → `MaxMarginPercent=50` cho notional tới **25× Equity** trên index, **50× trên Forex nếu mở rộng**. → **B1 bị hiểu NHẸ cho tài khoản nhỏ / nếu dùng FX major.** |

### G4 — Best-practice quản trị rủi ro ngoài (nhiều nguồn giáo dục trading) — ỦNG HỘ D1/D3

- *"position sizes should be conservative enough that **an overnight gap through your stop
  doesn't create catastrophic losses beyond your planned risk**"* → chính xác là **D1**.
- *"your largest single position should be small enough that a **20–30% gap-down doesn't
  damage the account beyond your weekly drawdown limit**"* → **D1** với `AssumedAdverseGapPercent`
  cỡ 20–30% cho tài sản biến động (crypto), thấp hơn cho index/FX.
- *"hard caps on **notional exposure**, **kill-switches**, and **manual override**"* là
  chuẩn tổ chức → **D1** (notional cap) + **D3** (Position Management = kill-switch).
- Sizing nên tham chiếu **ATR / historical gap data / implied vol** → đúng phương pháp
  `max-margin-percent-safe-ceiling` đã dùng.
- **Guaranteed Stop Loss (GSL)** — sản phẩm 1 số broker CFD, đảm bảo giá SL bất kể gap
  (có phí). Nếu broker của người dùng hỗ trợ → phương án loại bỏ gap risk triệt để (đánh
  đổi phí + không phải mọi broker/symbol có).

### G5 — Kết luận đối chứng nguồn ngoài

- **Công thức sizing của bot = chuẩn ngành, không có lỗi công thức.**
- **Mọi rủi ro audit chỉ ra đều được nguồn ngoài xác nhận** là rủi ro thật, có tên gọi
  riêng trong nghề (gap risk, notional exposure cap, kill-switch), và **giải pháp audit đề
  xuất (D1 gap/notional-cap, D3 kill-switch bật sẵn + realtime) trùng khớp best-practice**.
- **Cạm bẫy API mới phát hiện qua research ngoài**: `GetEstimatedMargin` có bug báo margin
  quá thấp cho dynamic-leverage CFD (Spotware xác nhận) → **B2 nâng mức lên: phải sanity-check
  giá trị margin, không tin mù**.
- **Lệch luật FTMO cụ thể** (mốc nửa đêm CEST, trừ theo Vốn-Ban-Đầu, baseline khi restart)
  → B7/B10 nâng từ [THẤP] lên [TRUNG BÌNH], cần `[Parameter] InitialCapital` + offset giờ.

### Nguồn

- cTrader Algo — Symbol reference: https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/
- cTrader Algo — Margin estimations: https://help.ctrader.com/ctrader-algo/guides/estimated-margin/
- cTrader Forum — tính volume theo stop loss: https://community.ctrader.com/forum/cbot-support/35892/
- cTrader Forum — CFD backtesting margin bug (Spotware xác nhận): https://community.ctrader.com/forum/ctrader-algo/42466/
- cTrader Forum — bug `Symbol.PipValue` khoá theo symbol: https://community.ctrader.com/forum/ctrader-algo/40794/
- cTrader Forum — bug `NormalizeVolumeInUnits` precision: https://communityuat.ctrader.com/forum/ctrader-support/35338
- EarnForex — Position Sizer robot cho cTrader: https://www.earnforex.com/ctrader-robots/cTrader-Position-Sizer/
- FTMO Academy — Maximum Daily Loss: https://academy.ftmo.com/lesson/maximum-daily-loss/
- FTMO Academy — Maximum Loss: https://academy.ftmo.com/lesson/maximum-loss/
- FTMO — Trading Objectives: https://ftmo.com/en/trading-objectives/
- FTMO leverage theo asset class: https://bestpropfirmguide.com/faqs/ftmo/leverage/
- Best-practice gap/overnight risk: https://tradeology.app/academy/swing-trading/overnight-gap-risk-management , https://www.chartguys.com/articles/position-sizing

---

## Kết luận 1 dòng

**Công thức sizing = chuẩn ngành, không có lỗi công thức** (đối chứng tài liệu cTrader +
forum Spotware + EarnForex + hướng dẫn tính lot size — tất cả khớp). Đối chứng **7 nguồn**
(dữ liệu backtest thật · 3 báo cáo margin/leverage của dự án · lịch sử code · tài liệu
cTrader Algo · luật FTMO chính thức · best-practice quản trị rủi ro) đều **xác nhận cùng
một lỗ hổng**: margin-cap phụ thuộc đòn bẩy broker nên **chỉ đủ an toàn cho BTCUSD**; trên
FTMO indices `MaxMarginPercent=50` để lại tail-risk **40–62% Equity cho một cú gap cỡ đã
từng xảy ra** (gấp 4–6× hạn mức FTMO 10%), và bị hiểu NHẸ cho tài khoản nhỏ / nếu mở rộng
sang FX (FTMO Forex tới 1:100). Ưu tiên: **(D1)** gap/notional-cap độc lập với đòn bẩy —
đúng công thức `max-margin-percent-safe-ceiling` đã thiết kế nhưng chưa code, và trùng
best-practice "gap-through-stop" bên ngoài; **(D2)** sanity-check `GetEstimatedMargin`
(bug báo margin quá thấp cho dynamic-leverage CFD đã được Spotware xác nhận); **(D3)** bật
sẵn Position Management, kiểm theo tick, `InitialCapital` cấu hình cứng + mốc reset ngày
theo giờ CE(S)T để khớp đúng luật FTMO.
