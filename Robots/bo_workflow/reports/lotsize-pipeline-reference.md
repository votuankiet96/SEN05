# Cấu trúc & pipeline tính lot size + quy đổi tiền tệ — tài liệu tham chiếu

Mục đích: hiểu **tận gốc** lot size được tính như thế nào, quy đổi tiền ra sao, cho 6 nhóm
symbol đang dùng — đủ để **viết lại bằng Python** không phụ thuộc `Symbol.PipValue` của cAlgo.

Phần "quản trị rủi ro" (risk % nên bao nhiêu, cap margin, cap gap...) **KHÔNG thuộc tài liệu
này** — đó là quyết định riêng. Ở đây chỉ trả lời: *"cho trước số tiền muốn rủi ro và khoảng
cách SL, đặt bao nhiêu volume, quy đổi tiền thế nào?"*

Tài khoản tham chiếu: **FTMO, deposit currency = USD**.

---

## 0. TL;DR — công thức gốc

```
volumeUnits = riskMoney_USD  /  ( slDistancePrice  ×  FX(quoteCcy → USD) )
```

- `slDistancePrice` = |entry − stopLoss|, đo bằng **giá gốc của symbol** (quote currency).
- `FX(quoteCcy → USD)` = giá trị USD của **1 đơn vị quote currency**, tại thời điểm đặt lệnh:

| quote currency | FX(quoteCcy → USD) | Nhóm |
|---|---|---|
| USD | `1.0` | US30, BTCUSD, GOLD |
| EUR | `EURUSD` (nhân) | DE40, FR40, SP35 |
| JPY | `1 / USDJPY` (chia) | JP225 |
| HKD | `1 / USDHKD` (chia) | HK50 |

- Rồi `volumeUnits = floor(volumeUnits / stepVolume) × stepVolume`, kẹp `[minVolume, maxVolume]`.
- `lots = volumeUnits / lotSize` — **chỉ để hiển thị**, không dùng trong tính toán.
- **Khái niệm "pip" không cần thiết** cho sizing — nó triệt tiêu (§3).

Kiểm chứng: công thức này tái tạo **chính xác** volume mà cBot đã đặt trong mọi backtest thật
cho cả 6 nhóm (§6).

---

## 1. Pipeline đầy đủ (8 bước)

| # | Bước | Input | Output | Đơn vị | Ghi chú |
|---|---|---|---|---|---|
| 1 | Tín hiệu | — | direction (±1), entry ref price, ATR | giá (quote ccy) | entry & ATR do hệ thống signal cung cấp |
| 2 | SL price | entry, ATR, hệ số KSL | `slPrice = entry − dir × KSL × ATR` | giá (quote ccy) | KSL = bội số ATR (vd 1.0) |
| 3 | SL distance | entry, slPrice | `slDistancePrice = abs(entry − slPrice) = KSL × ATR` | giá (quote ccy) | luôn dương |
| 4 | Risk money | balance/equity, risk model | `riskMoney_USD` | USD | thuộc "quản trị rủi ro", ngoài phạm vi tài liệu |
| 5 | **FX rate** | quote ccy, USD, thời điểm | `fx = FX(quoteCcy → USD)` | USD / (1 quote-ccy unit) | §4 — **điểm cấu trúc tiền quan trọng nhất** |
| 6 | Raw volume | riskMoney, slDistance, fx | `vRaw = riskMoney_USD / (slDistancePrice × fx)` | units (of base asset) | §3 chứng minh |
| 7 | Normalize | vRaw, stepVolume, min, max | `v = clamp(floor(vRaw/step)×step, min, max)` | units | **floor** (làm tròn XUỐNG) — Spotware staff: *"risk sẽ không bao giờ chính xác 100% do phải làm tròn"* |
| 8 | Đặt lệnh | v (units), direction | order | — | API cTrader nhận **units**; `ExecuteMarketOrder(dir, sym, v, ...)` / `PlaceStopOrder(...)` |

Nếu tính đủ chi phí (live, không phải FTMO-backtest-commission-0): bước 6 thêm term commission:
`vRaw = riskMoney_USD / (slDistancePrice × fx + commissionPerUnit_USD_roundTrip)`
— commission thường "USD per million USD notional"; quy về per-unit cần notional.

---

## 2. Cấu trúc symbol — bảng đầy đủ 6 nhóm

Số liệu thật từ `report.json → usedSymbols` của các backtest đã chạy.

| Nhóm | cTrader symbol | quote | base | lotSize | stepVolume | minVolume¹ | pipPosition | digits | PipSize² | TickSize³ |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| **US30** | `US30.cash` | USD | US 30 Index | 1 | 0.01 | 0.01 | 0 | 2 | 1.0 | 0.01 |
| **BTCUSD** | `BTCUSD` | USD | BTCUSD | 1 | 0.01 | 0.01 | 0 | 2 | 1.0 | 0.01 |
| **GOLD** | `XAUUSD` | USD | XAU | 100 | 1 | 1 | 0 | 2 | 1.0 | 0.01 |
| **DE40** | `GER40.cash` | EUR | German 30 Index | 1 | 0.01 | 0.01 | 0 | 2 | 1.0 | 0.01 |
| **FR40** | `FRA40.cash` | EUR | France 40 Index | 1 | 0.01 | 0.01 | 0 | 2 | 1.0 | 0.01 |
| **SP35** | `SPN35.cash` | EUR | Spain 35 Cash Index | 1 | 0.01 | 0.01 | 0 | 2 | 1.0 | 0.01 |
| **JP225** | `JP225.cash` | JPY | Japan 225 Index | 10 | 0.1 | 0.1 | 0 | 2 | 1.0 | 0.01 |
| **HK50** | `HK50.cash` | HKD | Hong Kong Index | 1 | 0.01 | 0.01 | 0 | 2 | 1.0 | 0.01 |

¹ minVolume: giả định = stepVolume (chưa đọc riêng field minVolume; cần xác nhận nếu Python
cần chặn chính xác). `maxVolume` cũng chưa đọc — thường rất lớn.
² `PipSize = 10^(−pipPosition)`. Cả 6 nhóm pipPosition=0 → **PipSize = 1.0** (1 pip = 1.0 điểm giá).
³ `TickSize = 10^(−digits)`. Cả 6 digits=2 → **TickSize = 0.01**. Vậy 1 pip = 100 tick.

**Nhận xét cốt lõi:**
- **DE40 = FR40 = SP35**: quote EUR, cấu trúc y hệt nhau. Quy đổi giống hệt (× EURUSD).
  (Đã kiểm: cả 3 cho PipValue ≈ $1.18/pip/unit trong Jan 2026 — xem §6.)
- `lotSize` khác nhau (GOLD 100, JP225 10, còn lại 1) nhưng **không vào công thức sizing** — chỉ
  đổi `units ↔ lots` khi hiển thị.
- `stepVolume` = độ phân giải volume: GOLD chỉ đặt được số nguyên units (1, 2, 3…), JP225 bước
  0.1, còn lại bước 0.01.

### Conversion symbol mà engine dùng (từ `usedSymbols`)

| Nhóm | quote | Conversion symbol | base / quote của nó | Chiều |
|---|---|---|---|---|
| US30, BTCUSD, GOLD | USD | *(không cần)* | — | — |
| DE40, FR40, SP35 | EUR | `EURUSD` | base EUR, quote USD | **NHÂN** giá EURUSD |
| JP225 | JPY | `USDJPY` | base USD, quote JPY | **CHIA** cho giá USDJPY |
| HK50 | HKD | `USDHKD` | base USD, quote HKD | **CHIA** cho giá USDHKD |

Cả 6 nhóm: quy đổi **1 hop trực tiếp** (engine không phải dựng chuỗi trung gian).

---

## 3. Nguyên lý gốc — vì sao "pip" triệt tiêu, vì sao không có contract multiplier

### 3.1 cTrader tính volume bằng **units of base asset**

Giá quote của symbol = *"bao nhiêu quote-currency cho 1 đơn vị base-asset"*.
- `XAUUSD = 4351.83` → 1 oz vàng (base XAU) giá 4351.83 USD (quote).
- `GER40.cash = 24632.45` → 1 "chỉ số Đức" (base) giá 24632.45 EUR (quote).
- `JP225.cash = 39560.9` → 1 "chỉ số Nhật" (base) giá 39560.9 JPY (quote).

→ Nếu giá dịch `ΔP`, thì giá trị của **1 unit base** thay đổi `ΔP` **quote-currency**. Luôn luôn.
Không có hệ số nhân riêng ("contract size per unit" = 1 theo định nghĩa units model của cTrader).

```
P/L (quote ccy) = (exitPrice − entryPrice) × direction × volumeUnits
P/L (acct ccy)  = P/L(quote ccy) × FX(quoteCcy → acctCcy)
```

Kiểm chứng thật (audit 2026-09-02, suy từ P/L lệnh đóng):
- XAUUSD: `6.92 điểm × 7 units = $48.44` gross ✓ (1 USD/điểm/unit)
- GER40.cash: `45.95 điểm × 0.99 units × 1.02549 = $46.65` gross ✓ (1 EUR/điểm/unit, × EURUSD)
- JP225.cash: `131.46 điểm × 59.6 units × (1/156.42) = $50.09` gross ✓ (1 JPY/điểm/unit, ÷ USDJPY)
- HK50.cash: `95.64 điểm × 8.4 units × (1/7.7741) = $103.34` gross ✓ (1 HKD/điểm/unit, ÷ USDHKD)

### 3.2 PipSize triệt tiêu trong công thức sizing

Công thức "chính thống" (Spotware staff, forum): `V = riskMoney / (slPips × PipValue)`, với
- `slPips = slDistancePrice / PipSize`
- `PipValue = PipSize × 1 × FX`   (giá trị 1 pip / 1 unit theo acct ccy)

Thay vào:
```
V = riskMoney / ( (slDistancePrice / PipSize) × (PipSize × FX) )
  = riskMoney / ( slDistancePrice × FX )
```
→ **PipSize biến mất.** Không cần biết pip là gì; làm thẳng trên giá gốc và FX rate.

### 3.3 Đơn vị (dimensional check)

```
[riskMoney]        = USD
[slDistancePrice]  = quote-ccy-price-points
[FX]               = USD / (quote-ccy-unit)   ... nhưng "quote-ccy-unit" ≡ "1 price-point cho 1 base-unit"
[slDistance × FX]  = USD / base-unit
[V]                = USD / (USD / base-unit) = base-units  ✓
```

---

## 4. Quy đổi FX — chi tiết (điểm "cấu trúc tiền" quan trọng nhất)

### 4.1 Thuật toán chính thức cTrader (open-api/symbol-rate-conversion)

Đi theo chuỗi conversion, mỗi hop:
```
if hopSymbol.baseAsset == currentAsset:   rate = rate × price
else:                                     rate = rate × (1 / price)
```
`price` = **Bid** khi tính cho vị thế **Long**, **Ask** khi tính cho **Short** (chuẩn P&L cTrader).

### 4.2 Áp cho 6 nhóm (quy đổi quote → USD)

| Nhóm | "currentAsset" ban đầu | hop symbol | base của hop | So sánh | Kết quả |
|---|---|---|---|---|---|
| US30/BTC/GOLD | USD | *(không có hop)* | — | — | `fx = 1.0` |
| DE40/FR40/SP35 | EUR | `EURUSD` | **EUR** | base == current | `fx = EURUSD_price` (**nhân**) |
| JP225 | JPY | `USDJPY` | **USD** | base ≠ current | `fx = 1 / USDJPY_price` (**chia**) |
| HK50 | HKD | `USDHKD` | **USD** | base ≠ current | `fx = 1 / USDHKD_price` (**chia**) |

Trực giác:
- `EURUSD = 1.18` nghĩa là "1 EUR = 1.18 USD" → giá trị USD của lời/lỗ tính bằng EUR = **× 1.18**.
- `USDJPY = 156` nghĩa là "1 USD = 156 JPY" → 1 JPY = 1/156 USD → lời/lỗ tính bằng JPY = **÷ 156**.
- `USDHKD = 7.77` → 1 HKD = 1/7.77 USD → lời/lỗ tính bằng HKD = **÷ 7.77**.

### 4.3 Nguồn & thời điểm của FX rate — CẠM BẪY LỚN

| | cAlgo hiện tại | Python NÊN làm |
|---|---|---|
| Rate dùng | `Symbol.PipValue` = **ảnh chụp lúc bot khởi động**, giữ nguyên cả run (tài liệu chính thức: *"not updated in real time and it remains constant"*) | Lấy `EURUSD`/`USDJPY`/`USDHKD` **tại thời điểm mỗi tín hiệu** |
| Sai lệch | Backtest GER40 6 tháng size mọi lệnh bằng EURUSD ngày đầu → risk-tiền-thật trôi theo % FX đã dịch | ≈ 0 nếu quy đổi động |

Mức ảnh hưởng theo nhóm (nếu dùng snapshot):
- **US30 / BTCUSD / GOLD**: 0% — quote USD, `fx = 1.0` vĩnh viễn, không có gì để trôi.
- **HK50**: ≤ ~1.3% — HKD neo USD trong biên 7.75–7.85 (HKMA giữ).
- **DE40 / FR40 / SP35**: theo biến động EURUSD kể từ đầu run (EURUSD 2025 chạy từ ~1.02 → ~1.17).
- **JP225**: lớn nhất — USDJPY biến động mạnh (140→160 = +14% trong 2024; 150→161 nửa đầu 2025).

Side Bid/Ask của cặp FX: chênh < 0.01% → **dùng mid cũng được** cho sizing. Chỉ cần đúng khi
so khớp P&L tuyệt đối.

---

## 5. PipValue / TickValue được tính từ đâu (nếu cần tái tạo)

```
PipValue_quote_per_unit  = PipSize × 1        (= PipSize, vì multiplier = 1)
PipValue_acct_per_unit   = PipSize × FX(quoteCcy → acctCcy)

TickValue_acct_per_unit  = TickSize × FX(quoteCcy → acctCcy)
```
Nguyên tắc chính thức Spotware: *"quote asset == deposit currency ⇒ tick value == tick size"*.

Đối chiếu số đo thật:

| Symbol | PipSize | FX (đầu Jan 2026) | PipValue tính | PipValue đo thật (từ P/L) |
|---|---:|---|---:|---:|
| US30.cash / XAUUSD / BTCUSD | 1.0 | 1.0 | **$1.0000** | 1.00000000 |
| GER40.cash | 1.0 | EURUSD ≈ 1.18 | **≈ $1.18** | ≈ 1.177–1.20 (session 2026), 1.0255 (Jan 2025) |
| FRA40.cash | 1.0 | EURUSD ≈ 1.18 | **≈ $1.18** | ≈ 1.179–1.181 (session 2026) |
| SPN35.cash | 1.0 | EURUSD ≈ 1.18 | **≈ $1.18** | ≈ 1.181–1.192 (session 2026) |
| JP225.cash | 1.0 | 1/USDJPY | **≈ $0.0064** | 0.00639309 (USDJPY 156.42, Jan 2025) |
| HK50.cash | 1.0 | 1/USDHKD | **≈ $0.1286** | 0.12863217 (USDHKD 7.7741, Jan 2025) |

(GER40 giá trị 1.0255 vs 1.18 là do 2 kỳ khác nhau: Jan **2025** EURUSD ~1.026 vs Jan **2026**
~1.18 — chính là minh hoạ cạm bẫy snapshot ở §4.3.)

---

## 6. Ví dụ tính tay — mỗi nhóm 1 lệnh, đối chiếu volume bot đã đặt

`vRaw = riskMoney_USD / (slDistancePrice × fx)`, rồi `floor` theo `stepVolume`.

| Nhóm | riskMoney | entry | slPrice | slDistance | fx | vRaw = risk/(dist×fx) | floor(step) | Bot đặt thật |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| **XAUUSD** | $50 | 2629.72 | 2636.3816 | 6.6616 | 1.0 | 50 / 6.6616 = **7.506** | step 1 → 7 | **7 units** ✓ |
| **US30.cash** | $100 | 48253.4 | 48223.8538 | 29.5462 | 1.0 | 100 / 29.5462 = **3.385** | step 0.01 → 3.38 | **3.38 units** ✓ (trước margin-cap) |
| **BTCUSD** | $50 | 93800 | 93097.2631 | 702.7369 | 1.0 | 50 / 702.737 = **0.0712** | step 0.01 → 0.07 | **0.07 units** ✓ |
| **GER40.cash** | $50 | 19923.9 | 19874.9935 | 48.9065 | EURUSD 1.02549 | 50 / (48.9065 × 1.02549) = **0.997** | step 0.01 → 0.99 | **0.99 units** ✓ |
| **JP225.cash** | $50 | 39560.9 | 39429.7360 | 131.1640 | 1/156.419 = 0.0063931 | 50 / (131.164 × 0.0063931) = **59.63** | step 0.1 → 59.6 | **59.6 units** ✓ |
| **HK50.cash** | $100 | 19597.2 | 19689.6442 | 92.4442 | 1/7.77409 = 0.128632 | 100 / (92.4442 × 0.128632) = **8.409** | step 0.01 → 8.40 | **8.40 units** ✓ |
| **FRA40.cash** | $100 | 8136.43 | 8127.6905 | 8.7395 | EURUSD ≈ 1.179 | 100 / (8.7395 × 1.179) = **9.70** | step 0.01 → 9.70 | **9.70 units** ✓ (trước margin-cap → 7.21) |
| **SPN35.cash** | $100 | 17606.5 | 17648.113 | 41.613 | EURUSD ≈ 1.181 | 100 / (41.613 × 1.181) = **2.035** | step 0.01 → 2.03 | **2.03 units** ✓ |

→ Công thức gốc `V = riskMoney / (slDist × FX)` khớp **8/8** ví dụ (sai số ≤ 1 stepVolume do floor).
`fx` của GER40/JP225/HK50 lấy từ P/L thật (audit 09-02); của FRA40/SPN35 back-derive từ session
2026 (≈ EURUSD Jan 2026 ≈ 1.18).

---

## 7. Hàm Python tham chiếu

```python
from math import floor

# Đặc tả symbol (từ report.json usedSymbols / broker spec). "mult" = 1 cho mọi CFD units-model của cTrader.
SYMBOLS = {
    "US30":   dict(ct="US30.cash",   quote="USD", step=0.01, min=0.01, lot_size=1),
    "BTCUSD": dict(ct="BTCUSD",      quote="USD", step=0.01, min=0.01, lot_size=1),
    "GOLD":   dict(ct="XAUUSD",      quote="USD", step=1.0,  min=1.0,  lot_size=100),
    "DE40":   dict(ct="GER40.cash",  quote="EUR", step=0.01, min=0.01, lot_size=1),
    "FR40":   dict(ct="FRA40.cash",  quote="EUR", step=0.01, min=0.01, lot_size=1),
    "SP35":   dict(ct="SPN35.cash",  quote="EUR", step=0.01, min=0.01, lot_size=1),
    "JP225":  dict(ct="JP225.cash",  quote="JPY", step=0.1,  min=0.1,  lot_size=10),
    "HK50":   dict(ct="HK50.cash",   quote="HKD", step=0.01, min=0.01, lot_size=1),
}

def fx_quote_to_usd(quote_ccy: str, rates: dict) -> float:
    """rates: {'EURUSD': ..., 'USDJPY': ..., 'USDHKD': ...} tại thời điểm tín hiệu (mid hoặc side theo dir)."""
    if quote_ccy == "USD":
        return 1.0
    if quote_ccy == "EUR":
        return rates["EURUSD"]          # base EUR == currentAsset -> nhân
    if quote_ccy == "JPY":
        return 1.0 / rates["USDJPY"]    # base USD != JPY -> chia
    if quote_ccy == "HKD":
        return 1.0 / rates["USDHKD"]    # base USD != HKD -> chia
    raise ValueError(f"quote currency chưa hỗ trợ: {quote_ccy}")

def compute_volume_units(group: str, risk_money_usd: float, entry: float, sl_price: float,
                         rates: dict) -> float:
    s = SYMBOLS[group]
    sl_distance = abs(entry - sl_price)                 # giá gốc (quote ccy)
    fx = fx_quote_to_usd(s["quote"], rates)
    if sl_distance <= 0 or fx <= 0:
        return 0.0
    v_raw = risk_money_usd / (sl_distance * fx)         # <-- CÔNG THỨC GỐC
    v = floor(v_raw / s["step"]) * s["step"]            # normalize XUỐNG
    v = round(v, 10)                                    # dọn sai số float
    if v < s["min"]:
        return 0.0                                       # dưới sàn -> KHÔNG đặt lệnh (không ép lên min)
    # (kẹp maxVolume nếu có)
    return v

def verify_pl_usd(group: str, entry: float, exit_price: float, direction: int,
                  volume_units: float, rates: dict) -> float:
    """direction: +1 buy, -1 sell. Đối chiếu với gross P/L của broker."""
    s = SYMBOLS[group]
    fx = fx_quote_to_usd(s["quote"], rates)
    return (exit_price - entry) * direction * volume_units * fx     # KHÔNG có lot_size, KHÔNG có multiplier
```

Ví dụ:
```python
rates = {"EURUSD": 1.02549, "USDJPY": 156.419, "USDHKD": 7.77409}
compute_volume_units("GOLD",  50,  2629.72, 2636.3816, rates)   # -> 7.0
compute_volume_units("DE40",  50, 19923.9,  19874.9935, rates)  # -> 0.99
compute_volume_units("JP225", 50, 39560.9,  39429.7360, rates)  # -> 59.6
```

---

## 8. Cạm bẫy (checklist khi code Python)

1. **FX snapshot vs live** (§4.3) — dùng rate **tại thời điểm tín hiệu**, không phải 1 rate cố
   định. Ảnh hưởng: JP225 > DE40/FR40/SP35 ≫ HK50 ≈ 0 ≈ US-quote.
2. **units ≠ lots** — API/công thức dùng **units of base**. `lots = units / lotSize`. GOLD
   lotSize 100, JP225 10. Nếu nhầm → sai 10–100×.
3. **Nhân hay chia FX** — nhân khi conversion-symbol có `base == quote-ccy-của-symbol` (EURUSD);
   chia khi `base == account-ccy` (USDJPY, USDHKD). Nhầm chiều → sai bình phương tỷ giá.
4. **floor, không round** — normalize XUỐNG bội `stepVolume`. Risk thực **luôn ≤** mục tiêu
   (Spotware staff: không bao giờ chính xác 100%). GOLD step = 1 → mất tới ~1 unit.
5. **Dưới sàn thì bỏ lệnh** — `vRaw < minVolume` → **không đặt** (đừng ép lên min → sẽ vượt risk).
6. **Commission** — FTMO backtest = 0. Live/mô hình đủ chi phí: cộng `commissionRoundTrip_per_unit`
   vào mẫu số bước 6. Commission "USD/triệu USD notional" → per-unit cần notional = `entry × fx`.
7. **Spread không tính 2 lần** — nếu `slDistance` đo từ giá fill thật thì gross loss đã gồm
   spread; đừng cộng thêm spread cố định.
8. **`minVolume`/`maxVolume`** — tài liệu này giả định `minVolume = stepVolume`; xác nhận field
   thật từ broker spec nếu Python cần chặn chính xác.
9. **Contract multiplier = 1** đúng cho **cash CFD units-model của cTrader**. Nếu chuyển sang
   broker/nền tảng khác (vd MT5 "€25/point DAX") thì phải thêm multiplier — kiểm lại bằng
   `verify_pl_usd` vs 1 lệnh đóng thật.

---

## 9. Nguồn

- cTrader Algo — Symbol reference (PipValue/TickValue "snapshot", QuoteAsset/BaseAsset):
  https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/
- Spotware OpenAPI — Calculating Symbol Tick/Pip Value ("quote==deposit ⇒ tick value == tick size"):
  https://spotware.github.io/OpenAPI.Net/calculating-symbol-tick-value/
- cTrader OpenAPI — Symbol rate conversion (thuật toán nhân/chia, Bid long / Ask short):
  https://help.ctrader.com/open-api/symbol-rate-conversion/
- cTrader Algo — Currency converter guide (`AssetConverter` / `Asset.Convert`, backtest dùng
  rate lịch sử): https://help.ctrader.com/ctrader-algo/guides/currency-conversion/
- Forum — công thức sizing chính thống (Panagiotis Charalampous, Spotware):
  https://community.ctrader.com/forum/cbot-support/44375/ ,
  https://community.ctrader.com/forum/cbot-support/35892/
- Đối chứng nội bộ: `reports/pipvalue-currency-conversion-audit-2026-09-02.md` (PipValue suy từ
  P/L lệnh đóng thật, 6 symbol) · `report.json usedSymbols` các run trong `research/cli_runs/`.
