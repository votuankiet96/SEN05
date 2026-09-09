# Audit đầy đủ lot size và currency conversion của cTrader/cBot

Ngày: 2026-09-06  
Phạm vi: account FTMO USD `7563609`; Combo và MA Cross; DE40/FR40/SP35,
HK50, JP225, US30, BTCUSD và XAUUSD.  
Trọng tâm: từ `RiskMoney` đã có và khoảng cách Entry-SL đến
`VolumeInUnits`/lots. Không đánh giá cách chọn `RiskMoney`.

## Cập nhật hậu build 2026-09-07

Người dùng đã build lại cả hai bot. Binary mới được xác nhận bằng hash:

- Combo: `C4996CCE46E213A85E3E782002E5CD131B3FBB2FC43E363112F5C340CD93D561`;
- MA Cross: `55C88E48DEDFC62FF99680CDA388E51E3772CDD2787E112993F11DB73FFAB6B6`.

Bảy backtest Ticks, 01-08/01/2025, account FTMO `7563609` đều tạo report đúng
kỳ và `failed=0`: Combo JP225/HK50/US30/GER40/XAUUSD, MA Cross JP225/HK50.
Bản vá probe-scaling vì vậy đã được xác nhận chạy thật:

- Combo/JP225: từ `0/33` order ở binary cũ thành `placed=13`, 11 trade;
- Combo/HK50: volume binary lỗi `5.80` trở về `5.41` (đối chứng snapshot cũ
  `5.42`); bốn trade còn lại bằng đối chứng hoặc lệch tối đa một step;
- Combo/US30 và XAUUSD: mọi volume trùng đối chứng;
- Combo/GER40: mọi volume trùng hoặc lệch một step do tỷ giá tại lúc sizing;
- MA Cross/JP225: `placed=6`; MA Cross/HK50: `placed=5`, đều `failed=0`.

Phần mô tả binary lỗi bên dưới được giữ làm lịch sử chẩn đoán. Trạng thái mới
nhất là **PASS trong phạm vi conversion/units đã kiểm tra**; giới hạn về
Bid/Ask, pending-fill và gross-vs-net vẫn còn.

## Kết luận trực tiếp

### A. Pipeline chuẩn của cTrader

```text
RiskMoney (deposit asset = USD)
  + Entry, StopLoss
  + metadata của symbol tại broker
        BaseAsset, QuoteAsset, PipSize/TickSize,
        LotSize, VolumeInUnitsMin/Max/Step
  -> D = abs(Entry - StopLoss), theo đơn vị giá
  -> M_quote = D × VolumeInUnits
  -> C(t, side) = conversion QuoteAsset -> USD tại thời điểm định giá
  -> LossUSDPerUnit = D × C(t, side)
  -> RawVolumeInUnits = RiskMoneyUSD / LossUSDPerUnit
  -> clamp max + normalize xuống volume hợp lệ; dưới min thì bỏ lệnh
  -> Lots = VolumeInUnits / LotSize             (chỉ biểu diễn)
  -> gửi order bằng VolumeInUnits
```

Đối với Open API thuần, các field `volume`, `filledVolume`, `minVolume`,
`maxVolume`, `stepVolume` và `lotSize` là **cents of a unit**: `100` protocol
units = `1.00` Algo volume-unit. Algo API C# đang dùng trong repo đã trả
`double` theo volume-units nên không nhân/chia 100.

### B. Pipeline của source hiện tại

```text
D = abs(Entry - SL)
SLpips = D / PipSize

PipValueNow =
  PipSize                                        nếu QuoteAsset == USD
  Convert(PipSize × 1,000,000, Quote -> USD)
    / 1,000,000                                  nếu QuoteAsset != USD

RawVolumeInUnits = RiskMoneyUSD / (SLpips × PipValueNow)
normalize xuống, chặn min/max
CapVolumeByMargin(...)
gửi VolumeInUnits vào PlaceStopOrder/ExecuteMarketOrder
```

Vì `SLpips = D/PipSize` và `PipValueNow = PipSize×C`, công thức source rút
gọn thành `RiskMoney/(D×C)`: **không thiếu conversion và không convert hai
lần**.

### C. Hai pipeline có giống nhau không?

- **Source `.cs` và binary build 2026-09-07: PASS trong phạm vi conversion và
  units đã kiểm tra.** Probe-scaling đã chạy thật trên JP225/HK50 ở cả hai
  cBot; USD-quoted symbols không hồi quy.
- **Binary build 2026-09-06 08:44-08:45 là bằng chứng lỗi lịch sử:** FAIL với
  JP225 và sai precision với HK50. JP225 đặt `0/33` lệnh; HK50 tăng volume
  `7.01%-7.28%`. Không dùng binary cũ này nữa.
- **Mức “exact” tuyệt đối: PARTIAL.** `Asset.Convert` không nhận `TradeType`,
  trong khi tài liệu Open API dùng Bid và đổi sang Ask cho P/L short. Combo
  còn có thời gian từ đặt pending đến fill. Vì vậy đây là nominal gross
  stop-risk tại lúc sizing, không phải bảo đảm loss thực tế đúng từng cent.

## 1. Cơ chế tiền tệ thật của cTrader

### 1.1 P/L phát sinh ở QuoteAsset rồi mới về DepositAsset

Tài liệu Open API nói tính P/L phải xét position size và conversion từ quote
asset của symbol sang deposit currency. Runtime report của FTMO xác nhận:

| Nhóm | BaseAsset runtime | QuoteAsset runtime | Conversion được nạp |
|---|---|---|---|
| DE40 | German 30 Index | EUR | EURUSD |
| FR40 | France 40 Index | EUR | EURUSD |
| SP35 | Spain 35 Cash Index | EUR | EURUSD |
| HK50 | Hong Kong Index | HKD | USDHKD |
| JP225 | Japan 225 Index | JPY | USDJPY |
| US30 | US 30 Index | USD | không cần |
| BTCUSD | BTCUSD | USD | không cần |
| XAUUSD | XAU | USD | không cần |

Do đó DE40 không sinh P/L tự nhiên bằng USD. Một price-point trên một
volume-unit tạo một EUR; engine quy đổi EUR sang USD để ghi P/L account.
HK50 và JP225 tương tự với HKD và JPY.

### 1.2 Direct conversion và conversion chain

Algo API `Asset.Convert`/`AssetConverter.Convert` nhận rõ asset nguồn và asset
đích. Trong backtest, tài liệu chính thức xác nhận nó dùng historical rates
và tự dựng conversion chain, kể cả nhiều hop.

Open API thuần cung cấp `ProtoOASymbolsForConversionReq(firstAssetId,
lastAssetId)`. Ứng dụng phải nhận chain, subscribe spot Bid/Ask của từng hop,
rồi nhân hoặc chia theo quan hệ BaseAsset/QuoteAsset. Với account này, cả ba
nhóm khác USD chỉ cần một hop:

```text
EUR -> USD: EURUSD, C = EURUSD
JPY -> USD: USDJPY, C = 1 / USDJPY
HKD -> USD: USDHKD, C = 1 / USDHKD
USD -> USD: C = 1
```

Không được suy luận các quan hệ này từ nickname `DE40`, `J225` hay `GOLD`;
phải đọc `BaseAsset`, `QuoteAsset` và conversion chain runtime.

### 1.3 Realtime, startup snapshot và thời điểm conversion

Các cơ chế phải phân biệt:

| Cơ chế | Thời điểm tỷ giá |
|---|---|
| `Symbol.PipValue`, `Symbol.TickValue` | snapshot lúc cBot/indicator khởi tạo; giữ nguyên |
| `Asset.Convert` trong live | rate hiện tại lúc gọi |
| `Asset.Convert` trong backtest | historical rate tại thời điểm backtest đang xử lý |
| Unrealized P/L | backend định giá vị thế đang mở theo market hiện tại |
| Realized P/L | conversion tại quá trình định giá/đóng của engine, không phải snapshot sizing |

Dữ liệu thật 01-08/01/2025 cho thấy USD-per-point-per-unit của các trade đóng
thay đổi trong tuần theo conversion rate: GER40 khoảng
`1.0315186..1.0315630`, HK50 `0.1285687..0.1285766`, JP225
`0.00631727..0.00631790`.

Tài liệu của `VolumeForFixedRisk` nói input amount là deposit currency, nên
helper xử lý currency conversion nội bộ và trả volume-units. Tuy nhiên tài
liệu không nói helper này refresh FX mỗi lần hay dùng cùng snapshot monetary
metadata. Không được khẳng định dynamic conversion của helper nếu chưa probe
runtime riêng.

### 1.4 Bid, Ask và Mid

Tài liệu Open API đưa thuật toán chain với `symbol.Bid`, và ghi thay bằng
`symbol.Ask` khi tính P/L cho short. Vì vậy side có ảnh hưởng về nguyên tắc.

`Asset.Convert(Asset to, double value)` không có tham số `TradeType`; tài liệu
Algo cũng không công bố side/mid cụ thể mà nó chọn. Do đó:

- có thể tin nó tự tìm đúng chain và historical/current rate;
- chưa đủ bằng chứng để nói helper hiện tại tái tạo chính xác Bid/Ask của cả
  long và short;
- sai khác dự kiến cỡ spread của conversion chain, thường nhỏ với
  EURUSD/USDJPY/USDHKD nhưng không bằng không.

## 2. Contract size, units và lots

### 2.1 Không dùng `price movement × lots` chung cho mọi symbol

cTrader gửi lệnh theo `VolumeInUnits`. `LotSize` chỉ là số volume-units trong
một lot:

```text
VolumeInUnits = Lots × LotSize
Lots          = VolumeInUnits / LotSize
```

Runtime FTMO:

| Nhóm | LotSize | USD/point/volume-unit ở 01-08/01/2025 | USD/point/lot |
|---|---:|---:|---:|
| DE40/FR40/SP35 | 1 | ~1.03155 | ~1.03155 |
| HK50 | 1 | ~0.128571 | ~0.128571 |
| JP225 | 10 | ~0.00631753 | ~0.0631753 |
| US30 | 1 | 1 | 1 |
| BTCUSD | 1 | 1 | 1 |
| XAUUSD | 100 | 1 | 100 |

Gold là ví dụ quan trọng: một volume-unit XAUUSD dịch $1 tạo $1 gross P/L,
nhưng một lot có 100 units nên dịch $1 tạo $100/lot. Công thức sizing dùng
units nên **không nhân 100**; chỉ khi hiển thị lots mới chia cho 100.

JP225 cũng vậy: một unit tạo 1 JPY/point rồi đổi sang USD; một lot gồm 10
units. BTCUSD và US30 cùng có `LotSize=1` và quote USD trên account này nên
trông giống nhau, nhưng code đúng là dựa trên metadata, không dựa trên tên.

Tài liệu Spotware OpenAPI.Net còn nêu trực tiếp: khi QuoteAsset trùng
DepositAsset, tick value bằng tick size. Điều này và P/L thật của tám nhóm
đều xác nhận cTrader đã định nghĩa volume-unit ở tầng sau contract; repo
không cần một contract multiplier hard-code khác.

### 2.2 Open API scale

Algo API và Open API không dùng cùng representation:

| API | Ví dụ biểu diễn 10 units |
|---|---:|
| cTrader Algo C# `double volume` | `10.0` |
| Open API Proto `volume` | `1000` cents |

Khi viết client Open API/Python cần chia `lotSize`, `minVolume`, `maxVolume`,
`stepVolume`, `volume`, `filledVolume` cho 100 trước khi dùng như Algo
volume-units; khi gửi ngược lại nhân 100 và dùng integer. Repo hiện dùng Algo
API nên không có nguy cơ x100 ở code path hiện tại.

## 3. Hai cách conversion tương đương

Đặt `C = USD cho một đơn vị QuoteAsset` và `D = |Entry-SL|`.

Cách A, đổi risk USD về currency P/L trước:

```text
RiskQuote = RiskUSD / C
Units = RiskQuote / D
      = RiskUSD / (D × C)
```

Cách B, đổi loss-per-unit sang USD trước:

```text
LossQuotePerUnit = D
LossUSDPerUnit = D × C
Units = RiskUSD / LossUSDPerUnit
      = RiskUSD / (D × C)
```

Hai cách giống nhau về đại số. Source hiện dùng cách B dưới dạng pips:

```text
SLpips = D / PipSize
PipValueUSDPerUnit = PipSize × C

SLpips × PipValue = D × C
```

Nếu dùng `VolumeForFixedRisk(RiskUSD, SLpips)`, không convert RiskUSD lần nữa.
Nếu tính manual như repo, chỉ convert QuoteAsset -> DepositAsset một lần trong
money-per-unit.

## 4. API helper của cTrader và manual calculation

| API | Vai trò | Conversion | Nhận xét audit |
|---|---|---|---|
| `VolumeForFixedRisk` | amount deposit currency + SL pips -> units | nội bộ | hợp lệ, nhưng doc cảnh báo imprecise; refresh timing không nêu |
| `VolumeForProportionalRisk` | % balance/equity + SL pips -> units | nội bộ | ngoài phạm vi chọn RiskMoney |
| `PipValue`/`TickValue` | monetary value/account currency | đã convert | snapshot lúc start; không convert thêm |
| `Asset.Convert` | convert asset nguồn -> đích | trực tiếp/chain | historical trong backtest; có precision rounding thực nghiệm |
| `QuantityToVolumeInUnits` | lots -> units | không liên quan FX | dùng runtime LotSize |
| `VolumeInUnitsToQuantity` | units -> lots | không liên quan FX | dùng runtime LotSize |
| `NormalizeVolumeInUnits` | đưa volume về broker-valid grid | không liên quan FX | repo dùng `Down`, đúng mục tiêu không vượt risk |

Spotware staff xác nhận `VolumeForFixedRisk`/`VolumeForProportionalRisk`
không gồm commission và swap. Gross stop-risk và net realized loss phải được
phân biệt.

Có quyền tính manual. Metadata tối thiểu phải lấy runtime:

- `BaseAsset`, `QuoteAsset`, `Account.Asset`;
- conversion chain/rate tại đúng thời điểm;
- `PipSize` hoặc trực tiếp price distance;
- `LotSize`, `VolumeInUnitsMin/Max/Step`;
- `NormalizeVolumeInUnits` nếu ở Algo API;
- `PipValue`/`TickValue` chỉ khi chấp nhận snapshot hoặc dùng làm đối chứng;
- commission/swap/PnL conversion fee nếu mục tiêu là net-risk thay vì gross
  price-risk.

Không cần đồng thời dùng `TickValue`, `PipValue`, `LotSize` và manual FX trong
cùng một công thức. Làm vậy dễ nhân contract/conversion hai lần.

## 5. Kiểm định riêng từng nhóm

### DE40 / FR40 / SP35

- QuoteAsset runtime: EUR; P/L tự nhiên: EUR.
- LotSize runtime: 1; step: 0.01 unit.
- Chain: EURUSD; conversion EUR->USD theo chiều nhân.
- P/L thật xác nhận khoảng `$1.03155/point/lot` trong tuần test Jan 2025.
- Source dùng metadata chung, không hard-code ba nickname: PASS.

### HK50

- QuoteAsset: HKD; LotSize 1; step 0.01.
- Chain: USDHKD; conversion HKD->USD theo `1/USDHKD`.
- P/L thật: khoảng `$0.128571/point/unit`.
- Binary 08:44 convert một HKD thành `0.12`, tạo volume lớn hơn khoảng 7.1%:
  FAIL.
- Source probe-scaling dự kiến khôi phục khoảng `0.12857`: PENDING BUILD.

### JP225

- QuoteAsset: JPY; LotSize 10; step 0.1.
- Chain: USDJPY; conversion JPY->USD theo `1/USDJPY`.
- P/L thật: khoảng `$0.00631753/point/unit`, tương đương
  `$0.0631753/point/lot` trong tuần test.
- Binary 08:44 convert một JPY thành `0.00`, nên `placed=0, failed=33`: FAIL.
- Source probe-scaling dự kiến khôi phục khoảng `0.00632`: PENDING BUILD.

### US30

- QuoteAsset và DepositAsset đều USD; LotSize 1; step 0.01.
- Không có FX hop. Một unit và một lot đều khoảng `$1/point`.
- Current helper trả `PipSize`, đúng metadata FTMO: PASS.

### BTCUSD

- QuoteAsset USD, LotSize 1, step 0.01.
- Một unit/lot tạo `$1` cho mỗi `$1` price move.
- Giống US30 ở các metadata cần cho phép thử này; code không hard-code tên:
  PASS.
- Volume cuối có thể bị margin cap rất mạnh do notional/leverage; đó là bước
  độc lập với conversion.

### XAUUSD

- BaseAsset XAU, QuoteAsset USD, LotSize 100, step 1 unit.
- Một unit tạo `$1` cho mỗi `$1` price move; một lot tạo `$100/point`.
- Source tính units rồi gửi units, không áp logic index và không hard-code
  `100`: PASS.

## 6. Numerical test đồng nhất

Input chung: `RiskMoney = $500`, `Entry-SL distance = 100 price-points`.
Conversion factor lấy từ median gross P/L thật của các trade đóng trên FTMO,
01-08/01/2025. Với ba symbol quote USD dùng giá trị định nghĩa chính xác 1.
Đây là volume theo stop-risk trước margin cap độc lập.

| Nhóm | C: USD/point/unit | Raw units | Normalize units | Lots | Nominal loss |
|---|---:|---:|---:|---:|---:|
| DE40 | 1.03155858 | 4.847034 | 4.84 | 4.84 | $499.27 |
| HK50 | 0.12857070 | 38.889110 | 38.88 | 38.88 | $499.88 |
| JP225 | 0.00631753 | 791.448622 | 791.4 | 79.14 | $499.97 |
| US30 | 1 | 5 | 5.00 | 5.00 | $500.00 |
| BTCUSD | 1 | 5 | 5.00 | 5.00 | $500.00 |
| XAUUSD | 1 | 5 | 5 | 0.05 | $500.00 |

Kết quả source có probe sẽ giống cột normalize nếu rate tại lúc sizing bằng
rate trong bảng. Binary hiện tại không giống ở HK50/JP225: HK50 dùng xấp xỉ
`0.12`, cho `41.66 units` và nominal loss theo factor thật khoảng `$535.63`;
JP225 trả volume 0 và bỏ mọi signal.

## 7. Numerical test bằng lệnh backtest thật

Các run 01-08/01/2025 đều Ticks, FTMO, vốn đầu $10,000, nominal RiskMoney lệnh
đầu $100. `Expected` dùng price distance từ log, conversion factor suy độc lập
từ gross P/L, rồi normalize theo runtime step.

| Nhóm | D từ signal | Expected units | cBot đặt/fill | Kết luận |
|---|---:|---:|---:|---|
| DE40 | 53.95339 | 1.79 | 1.79 | PASS |
| HK50 | 143.48892 | 5.42 | 5.42 đối chứng cũ; **5.80 binary hiện tại** | current binary FAIL precision |
| JP225 | 136.11960 | 116.2 | 116.2 đối chứng cũ; **0 order binary hiện tại** | current binary FAIL |
| US30 | 123.55196 | 0.80 | 0.80 | PASS |
| BTCUSD | 399.58858 | 0.25 | 0.05 sau margin cap | conversion PASS; cap độc lập |
| XAUUSD | 5.41807 | 18 | 18 (`0.18 lot`) | PASS |

HK50 current-binary test mới dùng đúng report period, ticks và signal như đối
chứng. Năm trade có volume ratio mới/cũ lần lượt `1.07011`, `1.07109`,
`1.07218`, `1.07237`, `1.07276`. Entry/exit timestamp và price trùng nhau,
nên khác biệt được cô lập vào sizing/conversion.

## 8. Audit code theo pipeline

| Bước | Combo | MA Cross | Verdict |
|---|---|---|---|
| Entry-SL distance | `PlacePendingOrder`, L275-278: absolute signal entry/SL | `PlaceMarketOrder`, L238-241: ATR distance/pips | PASS theo thiết kế; Combo có fill slippage |
| Quote->USD | `PipValueNow`, L343-353 | `PipValueNow`, L295-305 | source PASS về chiều; binary hiện tại FAIL JP/HK |
| Gross USD/unit | `stopLossPips × pipValue` L363-364 | L315-316 | PASS đại số |
| Min/max | L367-388 | L319-340 | PASS, runtime metadata |
| Normalize | L391-395, `RoundingMode.Down` | L343-347 | PASS |
| Margin cap | `CapVolumeByMargin` L423+ | L360+ | PASS về tách lớp; có thể giảm dưới risk-volume |
| Lots conversion | không dùng | không dùng | PASS: order API cần units |
| Order volume | `PlaceStopOrder(... volume ...)` L285-293 | `ExecuteMarketOrder(... volume ...)` L248-254 | PASS |
| Contract hard-code | không có | không có | PASS |
| Open API cents scaling | không áp dụng | không áp dụng | PASS |

## 9. Structural risks còn lại

1. **Đã đóng blocker build.** Binary mới ngày 2026-09-07 đã xác nhận
   probe-scaling chạy thật trên JP225/HK50 ở cả Combo và MA Cross.
2. **Bid/Ask của `Asset.Convert` không được tài liệu hoá theo TradeType.**
   Current helper không nhận direction, nên không thể chứng minh exact
   side-specific P/L. Đề nghị ghi là current conversion estimate, không ghi
   “chọn đúng Bid/Ask”.
3. **Combo sizing trước fill.** Conversion và distance được chốt khi đặt
   pending; lệnh có thể fill trong ba bar sau ở giá khác. MA Cross dùng
   market-order SL pips từ fill nên sát nominal model hơn.
4. **Gross và net khác nhau.** Commission, swap, slippage, gap và
   `PnLConversionFeeRate` không nằm trong công thức hiện tại. Điều này không
   làm sai currency conversion của gross price-risk, nhưng actual net loss có
   thể vượt RiskMoney.
5. **Probe là workaround precision dựa trên hành vi thực nghiệm.** Forum cũng
   ghi nhận phải convert lượng lớn rồi chia lại. Tài liệu chính thức gọi kết
   quả là exact nhưng không mô tả rounding. Với USD hai digits và probe 1e6,
   sai số do rounding tối đa khoảng `5e-9` mỗi unit; nếu truncate thì dưới
   `1e-8`, không ảnh hưởng volume step hiện tại.
6. **Không tự floor trong cBot.** Current code dùng
   `NormalizeVolumeInUnits`, là lựa chọn đúng. Python/Open API phải đọc min,
   max, step runtime; không giả định `min == step` cho broker khác.

## 10. Quyết định kỹ thuật đề xuất

Giữ thiết kế tối giản hiện tại:

```text
PipValueNow = precise historical/current Convert(PipSize, QuoteAsset -> Account.Asset)
VolumeUnits = RiskMoney / (SLpips × PipValueNow)
NormalizeVolumeInUnits(..., Down)
```

Probe-scaling là thay đổi cần thiết và đủ nhỏ. Không thêm bảng symbol, contract
multiplier, FX symbol hard-code hoặc tự dựng conversion chain trong cBot.

Các kiểm tra build, JP225/HK50, US30/XAUUSD và GER40 đã hoàn tất ngày
2026-09-07. Khi mô tả cơ chế vẫn phải bỏ khẳng định `Asset.Convert` chọn đúng
Bid/Ask và dùng thuật ngữ “nominal gross stop-risk”.

## 11. Bằng chứng tái lập

- Script tính độc lập:
  `research/diagnostics/lotsize-currency-audit-2026-09-06/analyze_runtime.py`
- Metadata/P&L factor:
  `research/diagnostics/lotsize-currency-audit-2026-09-06/runtime-evidence.json`
- Bảng numerical đồng nhất:
  `research/diagnostics/lotsize-currency-audit-2026-09-06/numerical-tests.csv`
- HK50 current binary vs đối chứng:
  `research/diagnostics/lotsize-currency-audit-2026-09-06/hk50-build1-comparison.csv`
- Run HK50 mới:
  `research/cli_runs/Combo_HK50.cash_h1_20260906-160154/`
- Snapshot GUI build-lần-1 US30/GER40/JP225:
  `research/diagnostics/lotsize-currency-audit-2026-09-06/build1-gui/`
- Danh sách bảy run hậu build:
  `research/diagnostics/lotsize-currency-audit-2026-09-06/postbuild-validation-runs.json`
  và `postbuild-regression-additional-runs.json`.

## 12. Bản đồ code tính lot size

| Vai trò | Combo | MA Cross |
|---|---|---|
| Tham số risk/margin | L63, L69 | L56, L62 |
| Tạo khoảng SL | `GetProtectionPrices` L324-328; đổi sang pips L275-278 | ATR sang pips L238-241 |
| QuoteAsset → AccountAsset | `PipValueNow` L343-353 | `PipValueNow` L295-305 |
| RiskMoney và raw units | `CalculateVolume` L356-364 | `CalculateVolume` L308-316 |
| Min/max và normalize | L367-395 | L319-347 |
| Margin cap | `CapVolumeByMargin` L423-491 | `CapVolumeByMargin` L360-424 |
| Gửi volume-units | `PlaceStopOrder` L285-293 | `ExecuteMarketOrder` L248-254 |

Hai bot không tính `Lots` và không cần `LotSize` để gửi lệnh. Nếu cần hiển thị
lots thì dùng `Symbol.VolumeInUnitsToQuantity(volume)` hoặc
`lots = volume / Symbol.LotSize`; tuyệt đối không đưa lots trở lại order API
như thể đó là units.

## 13. Nguồn

Nguồn chính thức:

- cTrader Algo, Symbol API: PipValue/TickValue snapshot; LotSize;
  VolumeInUnits; normalize; fixed/proportional risk:
  https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/
- cTrader Algo, currency converter và historical conversion trong backtest:
  https://help.ctrader.com/ctrader-algo/guides/currency-conversion/
- cTrader Open API, P/L cần conversion QuoteAsset -> DepositAsset:
  https://help.ctrader.com/open-api/profit-loss-calculation/
- cTrader Open API, conversion chain, SymbolsForConversion, Bid/Ask:
  https://help.ctrader.com/open-api/symbol-rate-conversion/
- cTrader Open API model messages: volume/lot/min/max/step theo cents;
  PnLConversionFeeRate:
  https://help.ctrader.com/open-api/model-messages/
- Spotware OpenAPI.Net, tick/pip value và trường hợp quote=deposit:
  https://spotware.github.io/OpenAPI.Net/calculating-symbol-tick-value/

Xác minh cộng đồng có trả lời từ Spotware:

- Công thức fixed-risk, commission conversion và normalize; risk không exact
  tuyệt đối do volume grid:
  https://community.ctrader.com/forum/cbot-support/44375/
- `VolumeForFixedRisk`/`VolumeForProportionalRisk` không gồm commission/swap:
  https://community.ctrader.com/forum/ctrader-algo/44158/
- Báo cáo thực nghiệm `Asset.Convert` làm tròn mạnh và workaround convert
  lượng lớn rồi chia lại (phần rounding là báo cáo người dùng, không phải cam
  kết chính thức của Spotware):
  https://community.ctrader.com/forum/connect-api-support/44296/
