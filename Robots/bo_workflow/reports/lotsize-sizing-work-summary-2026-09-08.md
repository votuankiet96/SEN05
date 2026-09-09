# Báo cáo: công việc trên phần tính lot size (Combo + MA Cross)

Ngày: 2026-09-08
Tác giả: Claude (Sonnet 5)
Phạm vi: `Combo/Combo/Combo.cs`, `MA Cross/MA Cross/MA Cross.cs` — region `Risk & Position Sizing` + logging
Trạng thái build: **`Combo.algo` đã build 2026-09-08 02:20** (đủ mọi thay đổi bên dưới). **`MA Cross.algo` còn bản 2026-09-07 00:15** — chưa có nhóm log mới, **cần build lại**.

---

## 0. Tóm tắt điều hành

Ba việc đã làm, theo thứ tự thời gian:

1. **Sửa quy đổi tiền tệ trong sizing** — `Symbol.PipValue` là ảnh chụp tỷ giá lúc cBot khởi động (tài liệu cTrader xác nhận), làm risk % thực tế trôi 5–15% trên symbol quote EUR/JPY trong run dài. Thay bằng helper `PipValueNow()` quy đổi **live tại mỗi tín hiệu**. Bản vá lần 1 làm JP225 hỏng hoàn toàn (volume = 0) do `Asset.Convert` làm tròn về 2 chữ số USD → thêm probe-scaling ×1.000.000. Đã build + verify 7 backtest.

2. **Thêm log chi tiết để kiểm định thiết kế bằng tay** — trước đây log không in số tiền rủi ro, volume, pip value hay P/L kỳ vọng; không thể đối chiếu "thiết kế sizing" với "lệnh thật trong backtest". Đã thêm 4 dòng log/lệnh: `units + lots`, `RISK_DETAIL`, `FX`, `CLOSED`.

3. **Phát hiện qua backtest FR40 (2026-09-08)** — rủi ro **thực tế** của từng lệnh lệch xa thiết kế (từ −$11 tới −$261 trên cùng ngân sách ~$95–101), KHÔNG phải lỗi sizing hay quy đổi tiền: nguyên nhân là **trượt giá lệnh chờ qua khe thị trường đóng cửa** và **cơ chế đảo chiều đóng lệnh giữa chừng**. Công thức risk % chỉ đảm bảo "−1% *nếu* khớp tại giá trigger *và* SL khớp tại mức SL" — hai điều kiện đó không phải lúc nào cũng đúng.

---

## 1. Bối cảnh

### 1.1. Hai cBot và vai trò của sizing

- Người dùng giao dịch qua **FTMO** (prop firm), đồng tiền nạp = **USD**.
- **Combo**: đọc CSV `(bartime, atr, entry, signal)` từ `core_python` (VM-OG8), đặt **Pending Stop** tại đúng cột `entry`, SL/TP là **giá tuyệt đối** (`entry − dir × KSL×ATR`, `entry + dir × KTP×ATR`), lệnh chờ sống tối đa 3 nến.
- **MA Cross**: đọc CSV `(bartime, atr, signal)`, đặt **Market Order**, SL/TP tính theo **pips tương đối** với giá khớp.
- Cả hai: `RiskPercent` mặc định 1%, `MaxMarginPercent` mặc định 50%. `KslLevel`/`KtpLevel` là 2 enum Fibonacci riêng (SL 8 mức 0.618→3.618, TP 9 mức 0.236→6.854).

### 1.2. Công thức sizing (giống nhau ở cả 2 bot)

```
riskAmount      = Account.Balance × RiskPercent / 100
requestedVolume = riskAmount / (stopLossPips × pipValue)
volume          = NormalizeVolumeInUnits( min(requestedVolume, VolumeInUnitsMax), RoundingMode.Down )
volume          = CapVolumeByMargin(volume, …)   // giảm nếu margin cần > min(FreeMargin, Equity × MaxMarginPercent/100)
```

- `stopLossPips` (Combo) = `|EntryPrice − stopLossPrice| / Symbol.PipSize`
- `stopLossPips` (MA Cross) = `KSL_ratio × ATR / Symbol.PipSize`
- `pipValue` = giá trị tiền tài khoản của **1 pip / 1 unit**, đã gồm quy đổi tiền tệ quote → USD.

Công thức này là chuẩn Spotware (đã đối chiếu forum + OpenAPI + P/L thật, `reports/pipvalue-currency-conversion-audit-2026-09-02.md` PASS 6/6). **Không có lỗi công thức.**

### 1.3. 6 nhóm symbol và hệ số quy đổi

| Nhóm | Symbol cTrader | Quote | `pipValue` = PipSize × FX |
|---|---|---|---|
| US30 | US30.cash | USD | 1.0 × 1 = **1.0** |
| BTCUSD | BTCUSD | USD | **1.0** |
| GOLD | XAUUSD | USD | **1.0** |
| DE40 | GER40.cash | EUR | 1.0 × **EURUSD** (≈ 1.08–1.17) |
| FR40 | FRA40.cash | EUR | 1.0 × **EURUSD** (giống DE40) |
| SP35 | SPN35.cash | EUR | 1.0 × **EURUSD** (giống DE40) |
| JP225 | JP225.cash | JPY | 1.0 × **1/USDJPY** (≈ 0.0064) |
| HK50 | HK50.cash | HKD | 1.0 × **1/USDHKD** (≈ 0.1286) |

`PipSize = 1.0` cho cả 6 nhóm (pipPosition = 0), nên `pipValue` **chính là hệ số FX**.

### 1.4. Vì sao đây là việc quan trọng

Người dùng đang **viết lại phần tính lot size bằng Python** trong hệ thống riêng. cBot phải là bản tham chiếu đúng để đối chiếu. Sai lệch trong quy đổi tiền hoặc thiếu minh bạch ở log → không cross-check được.

---

## 2. Vấn đề 1 — quy đổi tiền tệ bị "đóng băng"

### 2.1. Phát hiện

Tài liệu cTrader Algo (`help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/`) nói `Symbol.PipValue` (và `Symbol.TickValue`) là giá trị **tại lúc cBot khởi động / indicator khởi tạo**, sau đó **giữ nguyên, không cập nhật real-time**.

- Symbol quote USD: vô hại (hệ số USD→USD luôn = 1.0).
- Symbol quote EUR/JPY/HKD: `PipValue` chứa một hệ số FX bị chốt cứng ở mức lúc `OnStart`. Trong backtest, đó là tỷ giá **ngày đầu kỳ test**, giữ nguyên suốt kỳ.

### 2.2. Tác động định lượng

| Nhóm | Độ trôi ước tính | Vì sao |
|---|---|---|
| US30 / BTC / GOLD | **0%** | Không có hệ số FX |
| HK50 (HKD) | **≤ ~1.3%** | HKD neo dải 7.75–7.85 với USD |
| DE40 / FR40 / SP35 (EUR) | **~1–4%** (backtest 2 tháng) → **5–8%** (6 tháng) | EURUSD dao động rộng |
| JP225 (JPY) | **10–15%** (backtest dài) | JPY xu hướng mạnh |

Trôi = risk-per-trade thực tế lệch khỏi `RiskPercent` khai báo → sai lệch equity curve và max drawdown % khi đánh giá bot theo chuẩn FTMO. **KHÔNG ảnh hưởng logic tín hiệu** (entry/SL/TP/thắng-thua tính từ CSV + ATR).

### 2.3. Xử lý — helper `PipValueNow()` (approach C)

Thay `Symbol.PipValue` bằng helper tính pip value **tại mỗi tín hiệu**:

```csharp
private double PipValueNow()
{
    if (Symbol.QuoteAsset.Name == Account.Asset.Name)
        return Symbol.PipSize;                       // US30/GOLD/BTC: không quy đổi

    const double ProbeUnits = 1_000_000.0;
    return Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize * ProbeUnits) / ProbeUnits;
}
```

- `Asset.Convert` là **đúng thuật toán cTrader dùng cho margin/P&L thật** (open-api/symbol-rate-conversion), chỉ khác là không bị đóng băng. Backtest: dùng tỷ giá lịch sử chính xác tại bar đó; live: tỷ giá hiện tại.
- Thay `Symbol.PipValue` → `pipValue` tại **3 call-site/file**: guard trong `CalculateVolume`, công thức `requestedVolume`, dòng log rủi ro trong `CapVolumeByMargin`.
- `CalculateVolume(…, out double pipValue)` — trả ra để dòng log dùng đúng giá trị đã sizing.
- **Không đụng**: công thức risk%, logic `CapVolumeByMargin`, `GetEstimatedMargin`, `NormalizeVolumeInUnits`, enum SL/TP. Không thêm `[Parameter]`/state/region.

### 2.4. Bug JP225 và bản vá probe-scaling

**Build lần 1 (2026-09-06 08:44)** dùng `Symbol.QuoteAsset.Convert(Account.Asset, Symbol.PipSize)` (không scale). Kết quả 3 backtest Combo/H1:

| Symbol | Kết quả |
|---|---|
| US30.cash | `placed=31, failed=0` ✓ |
| GER40.cash | `placed=38, failed=0`, risk log hợp lý ✓ (`Asset.Convert` compile + chạy OK cho EUR) |
| JP225.cash | **`placed=0, failed=33`** — mọi lệnh volume = 0 |

**Chẩn đoán**: `Asset.Convert(to, value)` **làm tròn kết quả về số chữ số của đồng đích** (USD = 2 chữ số). Quy đổi `Symbol.PipSize` (= 1.0) JPY sang USD ≈ **0.0064 → cắt còn 0.00** → `pipValue = 0` → `pipValue <= 0` → `CalculateVolume` trả 0 → `_orderFailures++` (im lặng). EUR không dính (1 EUR ≈ 1.03 USD > 0.01). HKD sẽ **không hard-fail** nhưng **sai ~7%** (0.1286 → cắt còn 0.12).

**Bản vá**: quy đổi **1 triệu pip** rồi chia lại — kết quả đủ lớn để vượt xa mức cắt 2 chữ số. Sai số còn ~5e-9/unit, bỏ qua được. Vẫn là approach C, chỉ thêm 1 hằng số scale.

### 2.5. Kết quả sau build lần 2 (2026-09-07 00:15) — VERIFIED

7 backtest Ticks 01–08/01/2025 trên account FTMO 7563609:

| Bot / symbol | Kết quả |
|---|---|
| Combo / JP225 | `placed=13, failed=0` — lỗi 0/33 đã hết |
| Combo / HK50 | `placed=6, failed=0`; volume đầu `5.41` (không còn `5.80` như snapshot cũ) |
| Combo / US30 · GER40 · XAUUSD | `failed=0`; volume trùng đối chứng hoặc lệch tối đa 1 step |
| MA Cross / JP225 · HK50 | `placed` OK, `failed=0` |

No-op check US30 (build lần 1 vs binary cũ 09-04): 21/21 lệnh không bị margin-cap có **volume trùng khít từng cent** → công thức sizing là no-op tuyệt đối cho symbol quote USD, đúng thiết kế.

Chi tiết đầy đủ: `reports/lotsize-fx-live-conversion-fix-2026-09-06.md` (§15–§16).

---

## 3. Vấn đề 2 — log không đủ để kiểm định thiết kế

### 3.1. Thiếu gì

Trước đây, lúc đặt lệnh log chỉ có:
```
Combo: … pending Buy placed at 8361.4; SL=8345.88, TP=8402.03; valid for the next 3 chart bar(s).
```
Không có: số tiền rủi ro, `RiskPercent` → budget $, `pipValue`/FX, `ATR`, hệ số KSL/KTP, volume (units/lots), volume raw trước round-down, est.margin, P/L kỳ vọng nếu dính SL/TP. Lúc đóng lệnh chỉ có dòng `Trade` của cTrader + `events.json`. → **không đối chiếu được "thiết kế sizing" với "lệnh thật".**

### 3.2. Đã thêm (thuần `Print`, không đổi hành vi)

| Dòng | Nội dung |
|---|---|
| `placed` / `market placed` (cũ, giữ nguyên) | thêm `volume={U} units ({L} lots)` qua `Symbol.VolumeInUnitsToQuantity` |
| **`RISK_DETAIL`** | `balance`, `risk%` → `budget $`, `ATR`, `KSL={r}xATR` → `SL pips`, `KTP={r}xATR` → `TP pips`, `R:R`, `pipValue`, `risk-based raw {X} → placed {Y} units, step {S}`, `est.margin`, `expected loss if SL hit=$`, `expected profit if TP hit=$`. MA Cross thêm `fill`/`SL`/`TP` giá tuyệt đối từ `result.Position` |
| **`FX`** (helper `LogConversionRate`) | symbol quote ≠ USD → `1 {ccy} = {factor:F8} USD (<=> {1/factor:F5} {ccy} per USD); via Asset.Convert at placement tick`. factor = `pipValue / PipSize` = đúng hệ số sizing. Quote = USD → `no conversion (factor 1.0)` |
| **`CLOSED`** (helper `LogClosedTrade`, trong `OnPositionClosed`) | `reason={StopLoss\|TakeProfit\|Closed}`, `entry`, `close`, `pips`, `volume units (lots)`, `gross/commission/swap/net $`, `balance now $`. Số từ `History`/`HistoricalTrade`, fallback `Position` |

`balanceAtEntry = Account.Balance` chốt đầu hàm đặt lệnh (tránh lệch commission vài cent với MA Cross market order).

### 3.3. Ví dụ đọc log — 1 lệnh Combo/FR40 thật (2026-09-08, balance $10,096)

```
15/01 07:05 | pending Buy placed at 8361.4; SL=8345.88, TP=8402.03; volume=5.59 units (5.5900 lots); valid for the next 3 chart bar(s).
15/01 07:05 | RISK_DETAIL … balance=$10096.35, risk=1.00% => budget $100.96; ATR=15.52104, KSL=1.000xATR => SL 15.5 pips,
              KTP=2.618xATR => TP 40.6 pips, R:R=1:2.62; pipValue=1.16364000 USD/pip/unit;
              risk-based raw 5.59 units => placed 5.59 units (5.5900 lots), step 0.01; est.margin=$3697.29;
              expected loss if SL hit=$100.96, expected profit if TP hit=$264.31.
15/01 07:05 | FX … 1 EUR = 1.16364000 USD (<=> 0.85937 EUR per USD); via Asset.Convert at placement tick.
15/01 08:02 | CLOSED position 10 Buy reason=StopLoss: entry=8382.42, close=8343.12, -39.3 pips;
              volume=5.59 units (5.5900 lots); gross=$-258.50, commission=$-3.32, swap=$0.00, net=$-261.82; balance now $9834.53.
```

Kiểm định bằng tay, từng bước:

| Bước | Tính | Log | ✓ |
|---|---|---|---|
| budget | 10096.35 × 1% | `$100.96` | ✓ |
| SL pips | 1.000 × ATR 15.521 | `SL 15.5 pips` | ✓ |
| FX | EURUSD | `pipValue 1.16364` ↔ `FX: 1 EUR = 1.16364 USD` | ✓ (cùng số) |
| raw units | 100.96 / (15.521 × 1.16364) | `raw 5.59` | ✓ |
| round-down | 5.59 → 5.59 (step 0.01) | `placed 5.59` | ✓ không mất |
| expected loss @SL | 5.59 × 15.521 × 1.16364 | `$100.96` = budget | ✓ |
| **net thực tế** | kỳ vọng −$100.96 | `net=$-261.82` | **chênh 2.6×** → xem Vấn đề 3 |

→ Toàn bộ chuỗi `signal → SL → FX → volume → risk` truy được bằng tay. Và chênh lệch **kỳ vọng vs thật** hiện ngay ra khi so `RISK_DETAIL` với `CLOSED`.

---

## 4. Vấn đề 3 — rủi ro THỰC TẾ lệch khỏi thiết kế (phát hiện qua backtest FR40, 2026-09-08)

Run: Combo/FRA40.cash/H1, 01/01–01/03/2026, balance $10,000, risk 1%, MaxMargin 100%.
Summary: `placed=64, failed=0, pending-expired=11, same-direction-skipped=10, reversed=20`.

Người dùng hỏi: vì sao 1 lệnh lỗ $261 mà lệnh khác chỉ lỗ $11.70, trong khi ngân sách rủi ro đều ~$95–101?

### 4.1. Lệnh −$261.82 (position 10) — stop entry trượt 21 điểm qua khe thị trường đóng cửa

Bằng chứng `events.json` (serial 44–47):

| Sự kiện | Thời gian | Giá | Ghi chú |
|---|---|---|---|
| `Create Stop Order` | 15/01 07:05:00 | trigger **8361.4** | Tín hiệu từ bar **01:00** (`bartime=2026-01-15 01:00`) |
| `Stop Order Filled` | 15/01 07:05:00 (**cùng giây**) | fill **8382.42** | +21.02 điểm trên trigger, khớp NGAY |
| `Stop Loss Hit` | 15/01 08:02:00 | close **8343.12** | SL ở 8345.88 (tuyệt đối) → trượt thêm ~2.8 điểm |

**Nguyên nhân**: FR40.cash đóng cửa 01:00–07:00. Lệnh chờ chỉ đặt được lúc **07:05 khi thị trường mở**, nhưng lúc đó giá đã nhảy lên ~8382 — **đã vượt qua trigger 8361.4** → buy-stop biến thành **market buy khớp ngay tại 8382.42**.

- Sizing đúng cho SL 15.5 pips: 5.59 units, `expected loss if SL hit=$100.96`.
- SL 8345.88 (giá tuyệt đối, tính từ giá tín hiệu 8361.4, **không đổi khi fill lệch**) giờ cách giá khớp thật **36.5 pips**, không phải 15.5. R:R đảo ngược.
- Riêng 21 điểm trượt entry = `5.59 × 21.02 × 1.16364 ≈ $137` lỗ "chết" trước khi lệnh bắt đầu.
- Đóng tại −39.3 pips → −$258.50 gross = **2.56× ngân sách rủi ro**.

### 4.2. Lệnh −$11.70 (position 15) — đảo chiều đóng lệnh giữa chừng

| Sự kiện | Thời gian | Giá |
|---|---|---|
| `Stop Order Filled` | 22/01 17:29 | Sell 2.93 @ **8138.02** (trigger 8138.1 — khớp sạch) |
| `Position closed` | 23/01 16:00 | close **8140.82**, −2.8 pips |

`reason=Closed` — **không phải** SL/TP. Một ngày sau, tín hiệu **Buy** ngược hướng xuất hiện → `ReconcileExistingExposure()` **đóng lệnh Sell tại giá thị trường** trước khi vào lệnh ngược. Lúc đó giá mới đi ngược **2.8 pips** → khoá lỗ vặt $9.65 + phí $1.74 + swap $0.31 = −$11.70. Lệnh **chưa bao giờ chạm SL/TP**.

### 4.3. Sizing đảm bảo gì / KHÔNG đảm bảo gì

| | Đảm bảo | Không đảm bảo |
|---|---|---|
| Công thức risk% | Lỗ ≈ `RiskPercent%` balance **NẾU** (a) khớp tại giá trigger **VÀ** (b) SL khớp tại đúng mức SL | Trượt giá lệnh chờ; gap xuyên qua SL; đảo chiều đóng lệnh giữa chừng (P/L = giá đang ở đâu lúc đó) |
| `CapVolumeByMargin` | Margin 1 lệnh ≤ `MaxMarginPercent%` Equity | Không phải trần notional; không chặn gap loss |

Trong run này: **`reversed=20 / placed=64` = 31% số lệnh đóng sớm do đảo chiều** — P/L của chúng nằm rải rác từ lỗ vặt tới lời vặt, không phải −1%. Cộng thêm các lệnh trượt giá kiểu position 10.

### 4.4. Đây KHÔNG phải lỗi quy đổi tiền

`pipValue = 1.16364` = EURUSD tại bar đó (dòng `FX` xác nhận). Sizing đúng chuẩn. Vấn đề nằm ở **giá tín hiệu bị cũ khi index mở cửa** và **cơ chế đảo chiều** — tầng thực thi lệnh, không phải tầng tính lot.

---

## 5. Đã tối ưu hơn thế nào

| Khía cạnh | TRƯỚC | SAU |
|---|---|---|
| **Quy đổi FX trong sizing** | Ảnh chụp lúc `OnStart`, trôi 5–15% cho EUR/JPY trong run dài | Live mỗi tín hiệu qua `Asset.Convert`, sai số ~5e-9/unit; đúng cơ chế cTrader dùng cho margin/P&L thật |
| **JP225 sizing** (từ build 09-06) | volume = 0, 33/33 lệnh fail do rounding | probe-scaling ×1e6 → chạy đúng, verify 7 backtest |
| **HK50 sizing** | sai ~7% nếu quy đổi trực tiếp | đúng nhờ probe-scaling |
| **Kiểm định bằng tay** | Bất khả — chỉ có giá SL/TP + `events.json` | Đầy đủ: `budget`, `pipValue`, `ATR`, `KSL/KTP×ATR`, `raw→placed units`, `expected loss/profit`, tỷ giá FX + nghịch đảo |
| **Truy vết chênh P/L** | Không rõ do đâu | So `RISK_DETAIL` (kỳ vọng) với `CLOSED` (thật) + `reason` → thấy ngay gap / slippage / reversal / round-down |
| **Round-down loss** | Ẩn hoàn toàn | Hiện: `risk-based raw {X} => placed {Y} units, step {S}` — quan trọng với XAUUSD (step 1) trên lệnh nhỏ |
| **Đối chiếu với bản Python** | Không có mốc | `pipValue`/`FX` line là "sự thật" của cBot để cross-check |

Nguyên tắc giữ xuyên suốt: **không đổi công thức risk%, không đổi logic margin, không thêm `[Parameter]`/state/region, không hard-code tên symbol quy đổi.** Mọi thay đổi là (a) 1 helper quy đổi, (b) thuần `Print`.

---

## 6. Việc còn mở / đề xuất

### 6.1. Build

- **`MA Cross.algo` chưa build lại** (còn bản 2026-09-07 00:15) — chưa có `RISK_DETAIL`/`FX`/`CLOSED`. Cần build.
- `Combo.algo` đã build 2026-09-08 02:20, đủ mọi thay đổi.

### 6.2. Gap risk (Vấn đề 3) — chưa xử lý

Công thức risk% + `CapVolumeByMargin` KHÔNG chặn được kiểu lỗ position-10. Các hướng (chưa chốt, cần bàn):

| Hướng | Mô tả | Đánh đổi |
|---|---|---|
| **a. Huỷ tín hiệu nếu giá đã chạy quá xa** | Khi đặt được lệnh, nếu giá hiện tại đã vượt `EntryPrice` quá X% ATR → bỏ tín hiệu | Mất một số lệnh; đơn giản, an toàn |
| **b. Tính lại SL/TP theo giá khớp thật** | Sau khi fill, `ModifyStopLossPrice` để SL cách **giá khớp** đúng KSL×ATR (không phải cách giá tín hiệu) | Giữ risk 1% đúng, nhưng R:R theo giá khớp; cần xử lý fill event |
| **c. Chuyển Combo sang Market Order** | Như MA Cross — bỏ pending, vào ngay tại giá mở cửa | Đổi bản chất chiến lược (mất "chỉ vào nếu breakout tới `entry`") |
| **d. Lọc ở tầng Python** | `core_python` xuất cột `valid_until` / bỏ tín hiệu qua đêm cho index | Ngoài repo này |
| **e. Gap-cap D1–D3** | Trần notional / cắt volume theo kịch bản gap lịch sử | Phức tạp; audit safety đã bàn, chưa code |

Đề xuất khởi điểm: **(a)** — rẻ nhất, đúng tinh thần "chỉ vào nếu breakout còn hiệu lực", 1 tham số ngưỡng.

### 6.3. Cơ chế đảo chiều (Vấn đề 3, position 15)

`reversed=31%` là con số lớn. Không phải bug — nhưng nếu người dùng muốn "để lệnh chạy tới SL/TP thay vì đóng theo tín hiệu ngược", đó là quyết định thiết kế exit riêng (`ReversalMode`), hiện code chỉ có 1 kiểu (`Immediate`).

### 6.4. Tài liệu

- `CLAUDE.md` mục "Quy ước API" đã cập nhật (sizing dùng `PipValueNow()`, có ghi chú probe-scaling + verify 09-07).
- Câu cũ "`Symbol.PipValue` ... risk% có thể trôi nhẹ" nên rà lại cho khớp trạng thái mới.

---

## 7. Files & phiên bản

| File | Vai trò | Trạng thái |
|---|---|---|
| `Combo/Combo/Combo.cs` | `PipValueNow()`, `CalculateVolume(out pipValue)`, `RISK_DETAIL`+`FX`+`CLOSED` | sửa 2026-09-08 02:17 |
| `MA Cross/MA Cross/MA Cross.cs` | y hệt | sửa 2026-09-08 02:17 |
| `Combo.algo` | build | **2026-09-08 02:20 — đủ** |
| `MA Cross.algo` | build | 2026-09-07 00:15 — **cần build lại** |
| `reports/lotsize-fx-live-conversion-fix-2026-09-06.md` | báo cáo chi tiết vấn đề 1 (§15 bug JP225, §16 verify) | — |
| `reports/pipvalue-currency-conversion-audit-2026-09-02.md` | audit gốc, PipValue đo từ P/L thật, PASS 6/6 | — |
| `reports/lotsize-pipeline-reference.md` | tài liệu nguyên lý + hàm Python tham chiếu | — |
| `reports/lotsize-runtime-order-walkthrough-2026-09-07.md` | walkthrough lệnh runtime (Codex) | — |

Backtest chẩn đoán Vấn đề 3: `Documents/cAlgo/Data/cBots/Combo/58899c72-…-Default/Backtesting/` (FRA40.cash H1, 01/01–01/03/2026).
