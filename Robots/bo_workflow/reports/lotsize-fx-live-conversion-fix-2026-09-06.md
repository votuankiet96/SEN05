# Sửa quy đổi tiền tệ trong tính lot size: `Symbol.PipValue` (snapshot) → `PipValueNow()` (live)

Ngày: 2026-09-06
Tác giả: Claude (Sonnet 5)
Phạm vi: `Combo/Combo/Combo.cs` + `MA Cross/MA Cross/MA Cross.cs`, region `Risk & Position Sizing`
Trạng thái: **build lần 1 (09-06 08:44) lộ bug JP225 → đã sửa `PipValueNow()` (probe-scaling), CHƯA build lại.**
Xem §15 cho kết quả build lần 1 và bản vá.

---

## 1. Tóm tắt điều hành

Cả hai cBot tính khối lượng lệnh theo công thức risk %:

```
requestedVolume = (Balance × RiskPercent/100) / (stopLossPips × Symbol.PipValue)
```

`Symbol.PipValue` là **giá trị tiền tài khoản của 1 pip cho 1 unit**, đã bao gồm bước
quy đổi tiền tệ từ đồng quote của symbol sang USD. Tài liệu chính thức cTrader xác nhận
giá trị này là **ảnh chụp (snapshot) tại thời điểm cBot khởi động** và **không cập nhật
real-time**. Với các symbol có đồng quote khác USD (DE40/FR40/SP35 quote EUR, JP225 quote
JPY, HK50 quote HKD), tỷ giá quy đổi bị "đóng băng" ở mức đầu run — trong một backtest
hoặc phiên live kéo dài, rủi ro tiền thật mỗi lệnh trôi dần khỏi `RiskPercent` đã khai báo
khi tỷ giá EUR/JPY/HKD thay đổi.

**Thay đổi:** thêm một helper `PipValueNow()` tính pip value **tại thời điểm mỗi tín hiệu**
bằng `Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize)` — chính là thuật toán quy
đổi mà cTrader dùng cho `Symbol.PipValue`, chỉ khác là **không đóng băng**. Thay
`Symbol.PipValue` → `PipValueNow()` tại 3 vị trí/file (guard, công thức sizing, dòng log
`actualRiskAmount`).

**Không đụng:** công thức risk %, logic margin-cap (`CapVolumeByMargin`), gap-cap/PM, các
đề xuất D1–D7 của audit safety, `GetEstimatedMargin`, `NormalizeVolumeInUnits`. Không thêm
`[Parameter]`, không thêm region. Tổng thay đổi: +14 dòng helper (gồm comment) + đổi 3 dòng
mỗi file.

---

## 2. Bối cảnh

- Người dùng giao dịch qua tài khoản **FTMO**, đồng tiền nạp (deposit asset) là **USD**.
- Hai cBot chiến lược đang phát triển: **Combo** (đặt Pending Stop tại `entry` từ CSV, SL/TP
  giá tuyệt đối) và **MA Cross** (đặt Market Order, SL/TP theo pips tương đối với giá khớp).
  Cả hai đọc tín hiệu + ATR từ CSV do `core_python` (VM-OG8) tính sẵn — cBot chỉ lo
  Entry/SL/TP/quản lý vốn.
- Người dùng đang **tự viết lại phần tính lot size bằng Python** trong hệ thống riêng, nên
  đã yêu cầu đào sâu tận gốc cơ chế lot size + quy đổi tiền tệ cho **6 nhóm symbol**: US30
  (US30.cash), BTCUSD, GOLD (XAUUSD), DE40 (GER40.cash) / FR40 (FRA40.cash) / SP35
  (SPN35.cash), JP225 (JP225.cash), HK50 (HK50.cash).
- Trong quá trình rà soát, xác định `Symbol.PipValue` snapshot là **điểm sai lệch cấu trúc
  duy nhất** còn tồn tại trong đường tính lot size (công thức, chiều quy đổi, bậc số đều đã
  kiểm chứng đúng — xem `reports/pipvalue-currency-conversion-audit-2026-09-02.md` PASS 6/6
  và `reports/lotsize-pipeline-reference.md`).
- Người dùng đã cân nhắc 5 phương án xử lý (B/A/C/D/E ở §7 bên dưới) và **duyệt approach C**.

### Nguyên lý gốc (đã kiểm chứng, để đối chiếu bản Python)

cTrader luôn tính `volume` theo **units of base asset**. Giá quote nghĩa là "bao nhiêu
quote-ccy cho 1 unit base". Do đó **1 unit dịch chuyển 1.0 đơn vị giá = 1.0 quote-ccy**,
luôn luôn, **không có "contract multiplier"** trong mô hình units của cTrader. Suy ra:

```
P/L (quote ccy)  = (exit − entry) × side × volumeUnits
P/L (acct ccy)   = P/L(quote) × FX(quoteCcy → acctCcy)

volumeUnits      = riskMoney_acct / ( slDistancePrice × FX(quoteCcy → acctCcy) )
```

`PipSize` triệt tiêu trong công thức volume: `(slDist / PipSize) × (PipSize × FX) = slDist × FX`.
Và `PipValue_acct_per_unit = PipSize × 1 × FX` — đã verify khớp chính xác cho cả 6 nhóm từ
P/L của lệnh đã đóng thật (bảng ở §9).

Chiều quy đổi FX (thuật toán chính thức `open-api/symbol-rate-conversion`), mỗi hop:

```
if convSymbol.baseAsset == currentAsset:  rate = rate × price
else:                                     rate = rate × (1 / price)
price = Bid (vị thế Long) / Ask (vị thế Short)
```

- EUR (DE40/FR40/SP35): `EURUSD` có base = EUR = quoteCcy của symbol → **× EURUSD**
- JPY (JP225): `USDJPY` có base = USD = acctCcy → **÷ USDJPY**
- HKD (HK50): `USDHKD` có base = USD = acctCcy → **÷ USDHKD**
- USD (US30/BTCUSD/GOLD): không cần hop → **× 1.0**

---

## 3. Vấn đề

### 3.1. `Symbol.PipValue` là snapshot, không phải giá trị live

Tài liệu cTrader Algo (`help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/`)
mô tả `Symbol.PipValue` (và `Symbol.TickValue`) là giá trị tiền của 1 pip **tại thời điểm
cBot khởi động / indicator khởi tạo** — sau đó **giữ nguyên, không cập nhật real-time**.

Với symbol quote USD, điều này vô hại: hệ số quy đổi USD→USD luôn = 1.0, không có gì để
trôi. Với symbol quote EUR/JPY/HKD, `PipValue` chứa một hệ số FX (EURUSD / 1÷USDJPY /
1÷USDHKD) bị chốt cứng ở mức lúc `OnStart`.

### 3.2. Hệ quả với position sizing

Công thức `requestedVolume = riskAmount / (stopLossPips × PipValue)`:

- Khi `PipValue` bị chốt ở mức FX cũ mà tỷ giá thật đã đổi, `requestedVolume` bị lệch tỷ lệ
  nghịch với sai số FX.
- **Chỉ ảnh hưởng số $ rủi ro thực tế nếu dính SL** (và qua đó ảnh hưởng equity curve, max
  drawdown %) — **KHÔNG ảnh hưởng logic tín hiệu**: entry price, SL price, TP price, hướng
  lệnh, thắng/thua từng lệnh đều tính từ CSV + ATR, hoàn toàn không phụ thuộc `PipValue`.
- Với backtest, `PipValue` được chốt theo tỷ giá lịch sử tại **ngày bắt đầu kỳ test**, rồi
  giữ nguyên suốt kỳ. Backtest càng dài, độ trôi tích luỹ càng lớn.

### 3.3. Vì sao Python "nên tránh lỗi này ngay từ đầu"

Bản Python người dùng tự viết không có `Symbol.PipValue`. Nếu bản Python quy đổi động (lấy
EURUSD/USDJPY/USDHKD tại đúng thời điểm mỗi tín hiệu) thì nó **đúng hơn** bản cBot hiện tại
— và khi đối chiếu chéo hai bên sẽ thấy lệch. Sửa cBot sang quy đổi động khiến hai bản
cùng một chuẩn, dễ đối chiếu.

---

## 4. Hiện trạng TRƯỚC

Cả hai file dùng `Symbol.PipValue` trực tiếp tại **3 vị trí runtime** trong region
`Risk & Position Sizing`:

### Combo.cs (tương tự MA Cross.cs, chỉ khác tiền tố log)

```csharp
private double CalculateVolume(double stopLossPips, TradeType tradeType)
{
    if (!double.IsFinite(stopLossPips) || stopLossPips <= 0
        || !double.IsFinite(Symbol.PipValue) || Symbol.PipValue <= 0)   // (1) guard
        return 0;

    double riskAmount = Account.Balance * RiskPercent / 100.0;
    double requestedVolume = riskAmount / (stopLossPips * Symbol.PipValue);  // (2) sizing
    ...
    return CapVolumeByMargin(volume, tradeType, stopLossPips);
}

private double CapVolumeByMargin(double volume, TradeType tradeType, double stopLossPips)
{
    ...
    double actualRiskAmount = normalizedVolume * stopLossPips * Symbol.PipValue;  // (3) log
    double actualRiskPercent = Account.Balance > 0 ? actualRiskAmount / Account.Balance * 100.0 : 0;
    Print("... Real risk on this trade is now about ${4:F2} ({5:F2}% of balance) ...");
    return normalizedVolume;
}
```

Cả 3 chỗ đọc cùng một snapshot. `Symbol.PipValue` được gọi mỗi lần `CalculateVolume` chạy,
nhưng giá trị nó trả về **không đổi trong suốt vòng đời cBot** — đó chính là vấn đề.

Ghi chú: `Combo.cs` (và giờ cả `MA Cross.cs`) đã có sẵn các finite guard
(`!double.IsFinite(...)`) từ các phiên trước, và `GetEstimatedMargin` đã trả về `double`
(không còn `double?`) — nên finding "NaN bypass guard" / "nullable dead code" của audit
vòng trước đã được xử lý từ trước, không liên quan thay đổi này.

### Các chỗ CỐ TÌNH giữ nguyên `Symbol.PipSize`

`stopLossPips` / `takeProfitPips` được tính từ khoảng cách giá chia `Symbol.PipSize`
(Combo ~L238-239, MA Cross tương ứng). `PipSize = 10^(−pipPosition)` là hằng số cấu hình
symbol, **không dính tiền tệ**, không phải snapshot — giữ nguyên tuyệt đối.

---

## 5. Thay đổi đã thực hiện

### 5.1. Helper mới — đầu region `Risk & Position Sizing`, cả 2 file (giống hệt nhau)

```csharp
// Gia tri tien tai khoan cua 1 pip cho 1 unit, tinh TAI THOI DIEM GOI.
// KHONG dung Symbol.PipValue: tai lieu cTrader xac nhan no la anh chup ty gia
// luc OnStart va khong cap nhat -> sizing symbol quote EUR/JPY/HKD se troi
// theo FX trong run dai. Quote == tien tai khoan (US30/GOLD/BTCUSD): tra ve
// Symbol.PipSize, khong co quy doi. Quote khac: Asset.Convert (backtest dung
// ty gia lich su dung tai bar do; live dung ty gia hien tai) - cung co che
// cTrader dung cho margin/P&L that. Tuong duong dung Symbol.PipValue nhung
// khong bi dong bang (da doi chieu PipValue == PipSize x FX cho ca 6 nhom).
private double PipValueNow()
    => Symbol.QuoteAsset.Name == Account.Asset.Name
        ? Symbol.PipSize
        : Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize);
```

### 5.2. Ba vị trí đổi `Symbol.PipValue` → `pipValue` (biến local từ `PipValueNow()`)

```csharp
private double CalculateVolume(double stopLossPips, TradeType tradeType)
{
    double pipValue = PipValueNow();                       // gọi 1 lần / tín hiệu
    if (!double.IsFinite(stopLossPips) || stopLossPips <= 0
        || !double.IsFinite(pipValue) || pipValue <= 0)    // (1) guard
        return 0;

    double riskAmount = Account.Balance * RiskPercent / 100.0;
    double requestedVolume = riskAmount / (stopLossPips * pipValue);   // (2) sizing
    ...
    return CapVolumeByMargin(volume, tradeType, stopLossPips, pipValue);  // truyền xuống
}

private double CapVolumeByMargin(double volume, TradeType tradeType,
                                 double stopLossPips, double pipValue)   // +1 tham số
{
    ...
    double actualRiskAmount = normalizedVolume * stopLossPips * pipValue;   // (3) log
    ...
}
```

`pipValue` được truyền xuống `CapVolumeByMargin` như tham số thay vì gọi `PipValueNow()`
lần thứ hai — đảm bảo guard, sizing và dòng log báo "risk thực tế" trên **cùng một lệnh**
dùng **cùng một giá trị pip value** (không có khả năng lệch do 2 lần gọi ở 2 tick khác nhau).

### 5.3. Vị trí dòng (sau sửa)

| File | Helper | guard | sizing | signature `CapVolumeByMargin` | call-site | log `actualRiskAmount` |
|---|---|---|---|---|---|---|
| `Combo.cs` | L343-346 | L351-352 | L356 | L415 | L389 | L436 |
| `MA Cross.cs` | L295-298 | L303-304 | L308 | L352 | L341 | L399 |

Grep xác nhận: sau sửa, **không còn `Symbol.PipValue` nào trên code path runtime** ở cả 2
file — chỉ còn xuất hiện trong comment giải thích.

### 5.4. Import

Không cần đổi `using`. `Asset` nằm trong `cAlgo.API.Internals`; cả 2 file đã `using
cAlgo.API` + `using cAlgo.API.Internals` (MA Cross còn có thêm `GlobalUsings.cs` khai báo
global). `Symbol.QuoteAsset` → `Asset`, `Account.Asset` → `Asset`,
`Asset.Convert(Asset, double)` → `double` — tất cả đã xác nhận qua reflection
`cAlgo.API.dll` ProductVersion 5.9.13.

---

## 6. Hiện trạng SAU

- **Symbol quote USD (US30.cash, BTCUSD, XAUUSD):** `Symbol.QuoteAsset.Name == "USD" ==
  Account.Asset.Name` → nhánh `? Symbol.PipSize`. `PipSize = 1.0` cho cả 3 (pipPosition=0).
  Không gọi `Convert`, không có FX. Kết quả **giống hệt `Symbol.PipValue` cũ** (vốn cũng =
  1.0). → sizing của 3 nhóm này **không đổi một chút nào** (kỳ vọng byte-identical trong
  verify no-op).
- **Symbol quote EUR/JPY/HKD (GER40/FRA40/SPN35, JP225, HK50):** nhánh
  `: Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize)`.
  - Backtest: `Convert` dùng **tỷ giá lịch sử chính xác tại bar đang xử lý** (không phải bar
    đầu kỳ) — đây là cùng cơ chế cTrader dùng để tính margin và P/L của lệnh thật, nên
    sizing giờ nhất quán với phần còn lại của engine.
  - Live: `Convert` dùng tỷ giá hiện tại.
  - `Convert(Account.Asset, Symbol.PipSize)` = "đổi `PipSize` đơn vị đồng quote sang đồng
    tài khoản" = `PipSize × FX(quoteCcy → USD)` — chính xác định nghĩa của `PipValue`, chỉ
    khác là tính lại mỗi tín hiệu.
- Dòng log `MARGIN GUARD - order size reduced ... Real risk on this trade is now about $X
  (Y% of balance)` giờ báo con số **đúng theo tỷ giá tại lệnh đó**, không phải tỷ giá đầu run.
- `PipValueNow()` được gọi 1 lần mỗi khi có tín hiệu cần sizing (không phải mỗi tick) — chi
  phí không đáng kể.

---

## 7. Lý do chọn approach C

Người dùng hỏi thẳng: phương án nào **chuẩn xác nhất theo tài liệu cTrader + diễn đàn**, và
**sát thực tiễn thị trường nhất**? Năm phương án đã cân nhắc:

| PA | Mô tả | Vì sao KHÔNG chọn |
|---|---|---|
| **B** | Giữ nguyên `Symbol.PipValue`, chỉ ghi chú hạn chế | Không sửa gì — sai lệch cấu trúc vẫn còn, bản Python sẽ lệch khỏi bản cBot |
| **A** | Giữ `Symbol.PipValue` cho sizing, rồi **rescale volume** theo tỷ lệ `FX_now / FX_start` | Phải tự lấy `FX_start` (lại là một snapshot), tự quản 2 nguồn tỷ giá, thêm code; chỉ là "vá" lên trên một giá trị đã biết sai. Dùng làm fallback nếu C compile lỗi. |
| **C** ✅ | Helper `PipValueNow()` gọi `Asset.Convert` live mỗi tín hiệu | **Tái tạo chính xác thuật toán quy đổi tài liệu hoá của cTrader** (`open-api/symbol-rate-conversion`), chỉ bỏ phần đóng băng. Dùng đúng cơ chế `Asset.Convert` mà engine dùng cho margin/P&L. 4 dòng, không state, không `OnStart`, không `[Parameter]`. |
| **D** | Tự nạp symbol FX (`MarketData.GetSymbol("EURUSD")`) và tự nhân/chia theo bảng cứng | Phải hard-code chiều nhân/chia cho từng nhóm quote; dễ sai khi thêm symbol mới; trùng lặp logic mà `Asset.Convert` đã làm sẵn đúng. |
| **E** | Bỏ `PipValue`, tính sizing thẳng trên giá: `volume = riskMoney / (slDist × FX)` | Đúng về nguyên lý (và là công thức bản Python nên dùng) nhưng với cBot thì vẫn phải lấy `FX` từ đâu đó → quay lại D; đồng thời viết lại toàn bộ công thức, rủi ro regression cao hơn nhiều so với việc chỉ đổi một hàm. |

**Approach C thắng vì:**
1. **Đúng chuẩn tài liệu.** cTrader tài liệu hoá rõ `Asset.Convert` và thuật toán hop
   Bid/Ask nhân/chia. `PipValueNow()` không phát minh gì mới — nó chỉ gọi đúng API đó tại
   thời điểm cần, thay vì đọc bản chụp cũ.
2. **Sát thực tiễn.** Trong backtest, `Convert` trả về tỷ giá lịch sử đúng tại bar; trong
   live, tỷ giá hiện tại. Đây đúng là hệ số mà cTrader dùng để quy đổi margin và P/L thật
   của lệnh — sizing giờ khớp với phần còn lại của engine.
3. **Tối giản, không mở rộng bề mặt.** Không thêm tham số, không thêm trạng thái, không
   `OnStart`, không region mới. Đúng ràng buộc "clean, tinh gọn, đơn giản" của người dùng.
4. **Rủi ro regression thấp.** Với 3 nhóm quote USD, nhánh `? Symbol.PipSize` cho kết quả
   toán học **giống hệt** giá trị cũ → không thể gây thay đổi. Chỉ 3 nhóm quote khác USD bị
   ảnh hưởng, và bị ảnh hưởng đúng theo hướng làm cho sizing **chính xác hơn**.

---

## 8. Lý do các chi tiết thiết kế của helper

### 8.1. Vì sao có nhánh `Symbol.QuoteAsset.Name == Account.Asset.Name ? Symbol.PipSize`

Ba lý do:

- **Tránh gọi `Convert` khi không cần.** Với US30/BTC/GOLD, quy đổi USD→USD là vô nghĩa;
  gọi `Convert` chỉ thêm rủi ro (nếu `Convert` có hành vi lạ khi from == to). Nhánh tắt cho
  kết quả xác định.
- **Đảm bảo no-op tuyệt đối cho 3 nhóm USD.** `Symbol.PipSize` là hằng số cấu hình
  (`= 1.0`), giống chính xác giá trị `Symbol.PipValue` cũ trả về cho các symbol này. Verify
  no-op kỳ vọng report **byte-identical** — nếu lệch dù chỉ 1 cent là có bug.
- **Đọc code rõ nghĩa.** Người đọc thấy ngay: "quote == tiền tài khoản thì pip value chính
  là pip size, không có tầng quy đổi".

### 8.2. Vì sao `Convert(Account.Asset, Symbol.PipSize)` chứ không phải hằng số / bảng cứng

`Asset.Convert(Asset to, double value)` nhận `value` đơn vị của asset gọi hàm
(`Symbol.QuoteAsset`) và trả về số đơn vị tương ứng của `to` (`Account.Asset`). Truyền
`Symbol.PipSize` nghĩa là "1 pip tính bằng đồng quote thì bằng bao nhiêu đồng tài khoản" —
đúng bằng `PipSize × FX(quote → acct)`. cTrader tự lo:
- chọn đúng symbol chuyển đổi (EURUSD / USDJPY / USDHKD / chuỗi nhiều hop nếu cần),
- chọn đúng chiều nhân hay chia (dựa trên base asset của symbol chuyển đổi),
- chọn đúng Bid/Ask.

Bảng cứng (approach D) phải tự làm cả 3 việc trên và tự bảo trì khi danh mục symbol đổi.

### 8.3. Vì sao truyền `pipValue` xuống `CapVolumeByMargin` thay vì gọi `PipValueNow()` lần nữa

- **Nhất quán trong 1 lệnh.** Guard, công thức sizing và dòng log "risk thực tế %" phải nói
  về cùng một pip value. Hai lần gọi `PipValueNow()` ở hai thời điểm khác nhau (dù rất gần)
  về lý thuyết có thể trả về hai tỷ giá khác nhau.
- **Rẻ hơn.** Một lần `Convert` mỗi lệnh thay vì hai.
- **Đúng pattern hiện có.** `stopLossPips` cũng đang được truyền xuống `CapVolumeByMargin`
  theo đúng cách này — thêm `pipValue` cạnh nó là nhất quán với code xung quanh.

### 8.4. Vì sao KHÔNG thêm `[Parameter]` bật/tắt

Quy đổi động là **hành vi đúng theo tài liệu**, không phải một tuỳ chọn chiến lược. Thêm
switch để "quay lại snapshot" chỉ tạo một chế độ sai có thể chọn nhầm. Người dùng đã yêu
cầu rõ: không thêm tính năng, không thêm tham số.

### 8.5. Vì sao đặt trong region `Risk & Position Sizing` chứ không phải `Helpers`

Entry §8 của AGENT.md ban đầu ghi "thêm vào `#region Helpers`". Khi triển khai, đặt helper
ngay đầu region `Risk & Position Sizing`, sát `CalculateVolume` — vì nó chỉ phục vụ đúng
việc sizing, và CLAUDE.md yêu cầu "mỗi method làm đúng một việc, giữ ranh giới region rõ
ràng để người dùng sửa từng phần". Đặt cạnh nơi dùng giúp người đọc thấy toàn bộ mạch
tính volume trong một chỗ.

---

## 9. Tác động định lượng theo 6 nhóm

Độ trôi = sai số giữa tỷ giá đầu run (snapshot cũ) và tỷ giá tại thời điểm lệnh. Ảnh hưởng
**tỷ lệ nghịch** lên volume và **tỷ lệ thuận** lên $ risk thực tế nếu dính SL.

| Nhóm | Symbol | Quote | FX quy đổi | Độ trôi ước tính | Ghi chú |
|---|---|---|---|---|---|
| US30 | US30.cash | USD | × 1.0 | **0%** | Nhánh tắt, không có FX |
| BTCUSD | BTCUSD | USD | × 1.0 | **0%** | Nhánh tắt |
| GOLD | XAUUSD | USD | × 1.0 | **0%** | Nhánh tắt |
| HK50 | HK50.cash | HKD | ÷ USDHKD | **≤ ~1.3%** | HKD neo dải 7.75–7.85 với USD; trôi tối đa bằng biên độ neo |
| DE40 | GER40.cash | EUR | × EURUSD | **~1–4%** (2 tháng) → **5–8%** (6 tháng) | EURUSD dao động rộng |
| FR40 | FRA40.cash | EUR | × EURUSD | như DE40 | Cấu trúc **giống hệt** DE40 (quote EUR, lotSize 1, step 0.01) |
| SP35 | SPN35.cash | EUR | × EURUSD | như DE40 | Cấu trúc giống hệt DE40 |
| JP225 | JP225.cash | JPY | ÷ USDJPY | **10–15%** (backtest dài) | **Tệ nhất** — JPY xu hướng mạnh, biên độ lớn |

PipValue đã verify từ P/L lệnh đóng thật (audit 2026-09-02):

| Symbol | PipValue đo được (USD/pip/unit) | = PipSize × FX |
|---|---:|---|
| XAUUSD / US30.cash | `1.00000000` | 1.0 × 1 |
| BTCUSD | `1.00003389`* | 1.0 × 1 |
| GER40.cash | `1.02548884` | 1.0 × EURUSD (≈1.0255) |
| JP225.cash | `0.00639309` | 1.0 × (1 ÷ USDJPY ≈ 156.42) |
| HK50.cash | `0.12863217` | 1.0 × (1 ÷ USDHKD ≈ 7.7741) |

\* sai số do report chỉ giữ 2 số lẻ ở `grossProfit`.

**Ý nghĩa thực tế:** với JP225 backtest 1–2 năm, một lệnh khai báo risk 1% có thể thực sự
rủi ro ~0.85–1.15% tuỳ giai đoạn — đủ để làm sai lệch max drawdown % và equity curve khi
đánh giá bot theo chuẩn FTMO (10% tổng / 5% ngày). Sau sửa, con số risk bám sát 1% ở mọi
thời điểm.

---

## 10. Những gì KHÔNG thay đổi (ranh giới scope)

- **Công thức risk %:** `riskAmount = Balance × RiskPercent / 100` — nguyên vẹn.
- **Logic margin-cap:** `CapVolumeByMargin` chỉ nhận thêm 1 tham số; toàn bộ tính toán
  `GetEstimatedMargin` / `MaxMarginPercent` / free-margin / `MarginSafetyFactor 0.98` /
  counter `_marginCapped`/`_marginBlocked` — nguyên vẹn.
- **`Symbol.PipSize`** trong tính `stopLossPips`/`takeProfitPips` — nguyên vẹn.
- **`NormalizeVolumeInUnits(..., RoundingMode.Down)`**, kẹp min/max — nguyên vẹn.
- **`GetEstimatedMargin`** — không đụng.
- **Gap-cap / notional-cap / Position Management / D1–D7** từ audit safety — ngoài scope,
  không đụng.
- **Enum `SlFibLevel` / `TpFibLevel`**, cơ chế scheduler exact-first/fallback, reconcile
  exposure — không đụng.
- **Không thêm** `[Parameter]`, region, trạng thái, hook `OnStart`.

---

## 11. Kế hoạch verify (sau khi người dùng build GUI)

### 11.1. Build

Người dùng build cả `Combo.algo` và `MA Cross.algo` trong cTrader Automate IDE, gửi lại lỗi
compile nếu có. Điểm cần chú ý khi build:

- `Asset.Name` — nếu không resolve, đổi sang so sánh `Account.Currency` (string chắc chắn
  tồn tại) với tên đồng quote.
- `Symbol.QuoteAsset.Convert(Asset, double)` — nếu overload không khớp, thử
  `Symbol.QuoteAsset.Convert(Account.Asset.Name, Symbol.PipSize)` (overload nhận string).
- Fallback cuối nếu `Asset.Convert` hoàn toàn không dùng được: approach A (rescale volume
  theo `MarketData.GetSymbol("EURUSD").Bid` / snapshot đầu run) — messier, chỉ dùng khi bắt
  buộc.

### 11.2. Ba nhóm kiểm chứng

| # | Mục tiêu | Symbol | Kỳ | Kỳ vọng |
|---|---|---|---|---|
| **A. No-op** | Chứng minh 3 nhóm USD không đổi | US30.cash + XAUUSD + BTCUSD | bất kỳ (2 tháng đủ) | `report.json` / `events.json` **byte-identical** với bản build trước fix |
| **B. Drift** | Đo độ trôi đã sửa | JP225.cash + GER40.cash | kỳ dài (01/2025 → hiện tại) | Volume/risk mỗi lệnh lệch bản cũ theo đúng hướng FX; risk % thực tế bám `RiskPercent` ở mọi giai đoạn |
| **C. Peg** | HK50 trong biên neo | HK50.cash | kỳ dài | Thay đổi ≤ ~1.3%, không có nhảy bậc |

Chạy qua `ctrader-cli` (async, Start-Job + poll `report.json`) hoặc GUI, tên symbol đầy đủ
(`US30.cash`, `XAUUSD`, ...), tick data.

### 11.3. Đối chiếu với bản Python

Sau khi có kết quả, đối chiếu volume mỗi lệnh của cBot (đã quy đổi động) với hàm
`compute_volume_units()` trong `reports/lotsize-pipeline-reference.md` chạy trên cùng
tín hiệu + cùng nguồn tỷ giá — hai bên phải khớp trong sai số ≤ 1 `stepVolume` (do floor).

---

## 12. Rủi ro & điểm cần theo dõi

1. **`Asset.Name` / overload `Convert`** chưa build thật bao giờ trên máy này — reflection
   xác nhận chữ ký nhưng GUI build là bằng chứng cuối. Xem §11.1 cho fallback.
2. **Backtest cache:** nếu engine cache pip value theo cách nào đó, verify no-op sẽ lộ ra
   (kết quả lệch cho US30/GOLD/BTC = có vấn đề). Đó là lý do nhóm A phải byte-identical.
3. **`Convert` trong backtest có thể trả về giá tại tick hiện tại chứ không phải bar close**
   — với sizing tại `OnBarClosed` thì gần như trùng, chênh lệch không đáng kể cho mục đích
   risk %. Không xử lý.
4. **Kết quả optimize / `.cbotset` cũ:** thay đổi này KHÔNG đụng enum nên `.cbotset` vẫn
   đọc được, nhưng mọi bảng kết quả backtest EUR/JPY/HKD chạy TRƯỚC fix có risk % hơi lệch —
   nếu dùng để ra quyết định chiến lược trên các symbol đó, nên chạy lại trên binary mới.
   US30/GOLD/BTC không cần chạy lại.

---

## 13. Nguồn

- cTrader Algo `Symbol` API (PipValue là snapshot lúc khởi tạo):
  `https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/`
- cTrader Open API — symbol rate conversion (thuật toán hop nhân/chia Bid/Ask):
  `https://help.ctrader.com/open-api/symbol-rate-conversion/`
- Công thức volume của Spotware staff (Panagiotis Charalampous, forum ctrader.com):
  `Volume = maxAmountRisked / ((StopLossInPips + CommissionInPips) × PipValue)`
- `reports/pipvalue-currency-conversion-audit-2026-09-02.md` — PipValue đo từ P/L thật, PASS 6/6
- `reports/lotsize-pipeline-reference.md` — pipeline 8 bước + nguyên lý gốc + hàm Python tham chiếu
- Federal Reserve H.10 — tỷ giá EURUSD/USDJPY/USDHKD lịch sử đối chiếu

---

## 14. Phiên bản

- `Combo/Combo/Combo.cs` — helper `PipValueNow()` (block body sau vá §15); 3 call-site đổi sang `pipValue`.
- `MA Cross/MA Cross/MA Cross.cs` — y hệt.
- Không file `.cs` nào khác bị đụng. Không sửa `.csproj` / `.sln` / `GlobalUsings.cs`.
- Binary lỗi build lần 1: 2026-09-06 08:44–08:45 (bản expression-body, có bug JP225).
- Binary đã vá và verify: Combo `C4996CCE...`, MA Cross `55C88E48...`, build 2026-09-07 00:15.

---

## 15. Kết quả build lần 1 (2026-09-06) + bản vá `PipValueNow()`

Người dùng build cả 2 `.algo` lúc 08:44–08:45 và chạy 3 backtest Combo/H1, Jan 2026,
RiskPercent 1%, MaxMarginPercent 50%, KslLevel 2 / KtpLevel 4:

| Symbol | Quote | Kết quả | Kết luận |
|---|---|---|---|
| US30.cash | USD | `placed=31, failed=0` | Nhánh `Symbol.PipSize` chạy đúng |
| GER40.cash | EUR | `placed=38, failed=0`, risk log hợp lý ($77.72 = 0.93%) | `Asset.Convert` **compile + chạy OK** cho EUR |
| JP225.cash | JPY | **`placed=0, failed=33`**, `events.json` rỗng, netProfit 0 | **BUG — mọi lệnh `volume = 0`** |

### 15.1. Chẩn đoán bug JP225

- `failed=33` không kèm dòng Print nào → tất cả rơi vào nhánh `if (volume <= 0)` **im lặng**
  trong `PlacePendingOrder` (không phải "SIZE TOO SMALL", không phải margin block).
- `stopLossPips` không đổi so với audit 09-02 (đã PASS cho JP225) → thủ phạm là `pipValue`.
- GER40 (cũng nhánh `Convert`, EUR) chạy tốt → `Asset.Convert` không hỏng hoàn toàn, chỉ hỏng
  khi **kết quả quy đổi rất nhỏ**.
- **`Asset.Convert(to, value)` làm tròn/cắt kết quả về số chữ số của đồng đích** (`depositAssetDigits`
  = 2 cho USD — xác nhận trong report.html). Quy đổi `Symbol.PipSize` (= 1.0) JPY sang USD ≈
  **0.0064 USD → cắt còn 0.00** → `pipValue = 0` → `pipValue <= 0` → `CalculateVolume` trả 0 →
  `volume <= 0` → `_orderFailures++` (im lặng). EUR không dính vì 1 EUR ≈ 1.03 USD > 0.01.
- HKD (chưa test) sẽ KHÔNG hard-fail (0.1286 → cắt còn 0.12) nhưng **sai ~7%** — tệ hơn cả
  vấn đề snapshot ban đầu.

### 15.2. Bản vá — probe-scaling

Quy đổi một lượng LỚN (1 triệu pip) để kết quả đủ lớn, vượt xa mức cắt 2 chữ số, rồi chia lại:

```csharp
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
```

- JP225: `Convert(1_000_000 JPY → USD)` ≈ 6393.09 USD (2-dp là thừa chính xác), `/1e6` = 0.00639309. ✓
- GER40: `Convert(1_000_000 EUR)` ≈ 1.03e6, `/1e6` = 1.03. ✓ (như cũ)
- HK50: `Convert(1_000_000 HKD)` ≈ 128 632, `/1e6` = 0.128632. ✓ (đủ chính xác, không còn sai 7%)
- Sai số cắt còn ~5e-9/unit — bỏ qua được.
- US30/GOLD/BTC: vẫn nhánh `return Symbol.PipSize` — không đụng.

Đây vẫn là approach C (dùng `Asset.Convert` live), chỉ thêm 1 hằng số scale để né rounding của API.
Không thêm `[Parameter]`, không thêm state.

### 15.3. No-op check US30 — ĐÃ XÁC NHẬN (một phần)

So `events.json` của US30 build-lần-1 (Jan 2026) với run cũ (binary 09-04, `Symbol.PipValue`) trên
đúng 31 lệnh Jan trùng nhau:

- **21/31 lệnh KHÔNG bị margin-cap: volume trùng khít từng cent** (0.8, 0.57, 1.09, 1.19, …).
  → **công thức sizing là no-op tuyệt đối cho symbol quote USD** (đúng như thiết kế: nhánh
  `Symbol.PipSize`, và audit 09-02 đã chứng minh `Symbol.PipValue == Symbol.PipSize == 1.0` cho US30).
- 10/31 lệnh bị margin-cap: volume lệch nhẹ (vd 1.44 → 1.52). Nguyên nhân: `Symbol.GetEstimatedMargin`
  trả margin khác nhau giữa 2 run ($7105 vs $6722 cho cùng 2.09 units) — **KHÔNG do thay đổi này**
  (code không đụng `GetEstimatedMargin` / logic cap). Hai run dùng **cấu hình backtest khác nhau**:
  commission model `usdPerMillionUsdVolume` (run cũ) vs `usdPer1Lot` (run mới), và khoảng ngày
  khác (cũ: Jan–Jun; mới: Jan). Cần 1 run đối chứng cùng cấu hình để đóng hẳn điểm này, nhưng
  21/21 lệnh không-cap trùng khít đã đủ chứng minh phần sizing.

### 15.4. Việc còn lại

Đã hoàn tất ngày 2026-09-07; xem §16.

## 16. Kết quả hậu build (2026-09-07) — VERIFIED

Cả hai `.algo` đã được build lại, rồi chạy bảy backtest Ticks 01-08/01/2025 trên đúng account
FTMO `7563609`:

| Bot / symbol | Kết quả |
|---|---|
| Combo / JP225 | `placed=13`, `failed=0`, 11 trade; lỗi `0/33` đã hết |
| Combo / HK50 | `placed=6`, `failed=0`, 5 trade; volume đầu `5.41`, không còn `5.80` |
| Combo / US30 | `placed=9`, `failed=0`; toàn bộ volume trùng đối chứng |
| Combo / GER40 | `placed=9`, `failed=0`; trùng hoặc lệch tối đa một volume step |
| Combo / XAUUSD | `placed=7`, `failed=0`; toàn bộ volume trùng đối chứng |
| MA Cross / JP225 | `placed=6`, `failed=0` |
| MA Cross / HK50 | `placed=5`, `failed=0`; lệnh đầu suy ra pip value khoảng `0.1286` USD |

Probe-scaling đã được xác nhận chạy thật, không còn blocker build/test. Kết quả chỉ chứng minh
nominal gross stop-risk và conversion/units trong phạm vi trên; commission, swap, gap/slippage và
Bid/Ask nội bộ của `Asset.Convert` không nằm trong kết luận exact.

Artifacts:

- `research/diagnostics/lotsize-currency-audit-2026-09-06/postbuild-validation-runs.json`
- `research/diagnostics/lotsize-currency-audit-2026-09-06/postbuild-regression-additional-runs.json`
- `reports/lotsize-currency-conversion-full-audit-CODEX-2026-09-06.md`
