# Audit `Symbol.PipValue` và quy đổi tiền tệ cho position sizing

Ngày kiểm tra: 2026-09-02  
Phạm vi: `Combo.cs` và `MA Cross.cs`, tài khoản FTMO có đồng tiền nạp là USD.

## Kết luận

**PASS 6/6 symbol: XAUUSD, BTCUSD, US30.cash, GER40.cash, JP225.cash và
HK50.cash.** Không phát hiện trường hợp cTrader đổi sai chiều tiền tệ, sai bậc
số, hoặc khiến volume risk-based sai do `Symbol.PipValue`.

- Ba symbol quote trực tiếp bằng USD (`XAUUSD`, `BTCUSD`, `US30.cash`) cho
  PipValue thực tế đúng `$1 / pip / volume-unit` với cấu hình broker hiện tại.
- `GER40.cash` quote EUR: engine tự nạp `EURUSD` và đổi theo chiều nhân
  EUR -> USD.
- `JP225.cash` quote JPY: engine tự nạp `USDJPY` và đổi theo chiều nghịch
  JPY -> USD, tức chia cho USDJPY.
- `HK50.cash` quote HKD: engine tự nạp `USDHKD` và đổi theo chiều nghịch
  HKD -> USD, tức chia cho USDHKD.

Không sửa bất kỳ file `.cs` nào trong audit này.

## Điểm cần hiểu chính xác

Code của cả hai cBot dùng cùng công thức:

```text
riskAmount     = Account.Balance * RiskPercent / 100
requestedVolume = riskAmount / (stopLossPips * Symbol.PipValue)
```

Biến `volume` ở đây là **volume in units**, không phải lúc nào cũng là số lot.
Quan hệ là `quantityLots = volumeUnits / lotSize`. Ví dụ:

- XAUUSD: `7 units / 100 units-per-lot = 0.07 lot`.
- JP225.cash: `59.6 units / 10 units-per-lot = 5.96 lot`.
- US30.cash có `lotSize=1`, nên số units và số lot bằng nhau.

Điều này khớp cách API nhận volume và cách report cTrader đồng thời ghi hai
field `volume`/`quantity`.

## Phương pháp kiểm chứng

Với mỗi symbol:

1. Đọc trực tiếp `report.json` hoặc JSON nhúng trong `report.html`, `log.txt`
   và history/event gốc.
2. Xác nhận `depositAsset`, `quoteAsset`, `lotSize`, `stepVolume`,
   `pipPosition`, và conversion symbol mà engine đã nạp.
3. Suy ngược PipValue từ sizing:

   ```text
   PipValue = riskAmount / (SL-distance-in-pips * rawVolume)
   ```

   Do code floor volume theo `stepVolume`, phép này cho một khoảng hẹp thay
   vì luôn cho một điểm chính xác.
4. Kiểm tra độc lập bằng P/L của vị thế đã đóng:

   ```text
   PipValue_realized = abs(grossProfit) / (abs(pips) * volumeUnits)
   ```

5. Với EUR/HKD/JPY, đối chiếu đúng chiều và bậc số với tỷ giá lịch sử đầu
   tháng 01/2025.

## Kết quả tổng hợp

| Symbol | Quote -> tài khoản | Conversion symbol trong report | PipValue suy từ P/L thật (USD/pip/unit) | Kết quả |
|---|---|---|---:|---|
| XAUUSD | USD -> USD | Không cần | `1.00000000` | PASS |
| BTCUSD | USD -> USD | Không cần | `1.00003389`* | PASS |
| US30.cash | USD -> USD | Không cần | `1.00000000` | PASS |
| GER40.cash | EUR -> USD | `EURUSD` | `1.02548884` | PASS |
| JP225.cash | JPY -> USD | `USDJPY` | `0.00639309` | PASS |
| HK50.cash | HKD -> USD | `USDHKD` | `0.12863217` | PASS |

\* Sai số rất nhỏ của BTCUSD là do `grossProfit` trong report chỉ giữ hai số
lẻ; sizing vẫn floor chính xác về `0.07` unit khi dùng PipValue bằng 1.

## Bằng chứng chi tiết

### XAUUSD

Artifact:
`research/cli_runs/Combo_XAUUSD_h4_20260902-191855/`

- Kỳ test: 01/01/2025 -> 15/01/2025, Ticks; RiskPercent `0.5%`, vốn đầu
  `$10,000`, riskAmount `$50`.
- `depositAsset=USD`, `quoteAsset=USD`, `lotSize=100`, `stepVolume=1`,
  `pipPosition=0`.
- Lệnh đầu: Sell entry `2629.72`, SL `2636.381646175`, volume `7 units`
  (`0.07 lot`). Nếu PipValue bằng 1, raw volume là `7.50565231`; floor bước
  1 thành đúng 7.
- History: `6.92 pips * 7 units = $48.44` gross, suy ra PipValue đúng `1`.

### BTCUSD

Artifact:
`research/cli_runs/Combo_BTCUSD_h4_20260902-192230/`

- Kỳ test: 01/01/2025 -> 15/01/2025, Ticks; RiskPercent `0.5%`, vốn đầu
  `$10,000`, riskAmount `$50`.
- `depositAsset=USD`, `quoteAsset=USD`, `lotSize=1`, `stepVolume=0.01`,
  `pipPosition=0`.
- Lệnh đầu: Buy entry `93800`, SL `93097.2630858611`, volume `0.07`.
  Với PipValue bằng 1, raw volume là `0.07115038`; floor bước 0.01 thành
  đúng 0.07.
- History: `505.84 pips * 0.07 unit = $35.4088`, report làm tròn gross thành
  `$35.41`; suy ra PipValue `1.00003389` chỉ do làm tròn cent.

### US30.cash

Artifact GUI margin-fixed:
`C:/Users/Administrator/Documents/cAlgo/Data/cBots/Combo/8403a83c-ae3a-44ea-a888-6e9b87c23741-Default/Backtesting/`

- `depositAsset=USD`, `quoteAsset=USD`, `lotSize=1`, `stepVolume=0.01`,
  `pipPosition=0`.
- Lệnh đầu dự kiến: entry `48253.4`, SL `48223.853803249214`; RiskPercent
  `1%`, riskAmount `$100`. Với PipValue bằng 1, raw volume `3.38453036`,
  floor thành đúng `3.38` như log.
- Margin cap đã giảm `3.38 -> 3.04`; đây là hành vi A+C đã được duyệt, không
  phải lỗi PipValue.
- Vị thế thật: `31 pips * 3.04 = $94.24` gross chính xác. Ví dụ thứ hai:
  `145.75 pips * 1.77 = $257.9775`, report làm tròn `$257.98`.

### GER40.cash

Artifact:
`research/cli_runs/Combo_GER40.cash_h4_20260901-2339/`

- Kỳ test: 01/01/2025 -> 02/01/2025, Ticks; RiskPercent `0.5%`, vốn đầu
  `$10,000`, riskAmount `$50`.
- `depositAsset=USD`; `GER40.cash` quote EUR, `lotSize=1`, `stepVolume=0.01`,
  `pipPosition=0`; report tự nạp thêm `EURUSD`.
- Lệnh: Buy entry `19923.9`, SL `19874.9934725785`, volume `0.99`.
- Vì floor bước 0.01, sizing suy ra PipValue nằm trong
  `(1.0223584, 1.0326853] USD/pip/unit`.
- History: `$46.65 / (45.95 pips * 0.99) = 1.02548884 USD/pip/unit`.
- Federal Reserve H.10 ghi EURUSD ngày 02/01/2025 khoảng `1.0261`, nên kết
  quả đúng chiều, đúng bậc và sai khác khoảng 0.06%.

### JP225.cash

Artifact:
`research/cli_runs/Combo_JP225.cash_h4_20260902-192536/`

- Kỳ test: 01/01/2025 -> 15/01/2025, Ticks; RiskPercent `0.5%`, vốn đầu
  `$10,000`, riskAmount `$50`.
- `depositAsset=USD`; `JP225.cash` quote JPY, `lotSize=10`, `stepVolume=0.1`,
  `pipPosition=0`; report tự nạp thêm `USDJPY`.
- Lệnh đầu: Buy entry `39560.9`, SL `39429.7359917193`, volume `59.6 units`
  (`5.96 lot`). Sizing suy ra PipValue `0.00639600898 USD/pip/unit`, tương
  ứng USDJPY `156.3475`.
- History độc lập: `$50.09 / (131.46 pips * 59.6) = 0.00639309479`, tương
  ứng USDJPY `156.4188`.
- Hai phép suy ngược lệch nhau khoảng 0.046%; cả hai đều đúng chiều `1/USDJPY`
  và đúng bậc. Federal Reserve H.10 ghi USDJPY ngày 03/01/2025 khoảng
  `157.20`, chênh dưới 1% so với giá broker/snapshot dùng trong report.

### HK50.cash

Artifact:
`C:/Users/Administrator/Documents/cAlgo/Data/cBots/Combo/1bae1864-5246-4220-b6c4-e8fd3dbaae4e-Default/ArchivedRuns/HK50_H2_ReconcileExposure_20260901-0945/`

- Kỳ GUI: 01/01/2025 -> 26/08/2026, Ticks; RiskPercent `1%`, vốn đầu
  `$10,000`, riskAmount `$100`.
- `depositAsset=USD`; `HK50.cash` quote HKD, `lotSize=1`, `stepVolume=0.01`,
  `pipPosition=0`; report tự nạp thêm `USDHKD`.
- Lệnh đầu: Sell entry `19597.2`, SL `19689.6442111244`, volume `8.40`.
  Sizing suy ra PipValue trong `(0.128624673, 0.128777798]`.
- History: `$103.34 / (95.64 pips * 8.4) = 0.128632172`, tương ứng USDHKD
  `7.7741049`.
- Federal Reserve H.10 ghi USDHKD ngày 02/01/2025 khoảng `7.7768`; kết quả
  đúng chiều `1/USDHKD`, đúng bậc và sai khác khoảng 0.04%.

## Giới hạn quan trọng của API

Tài liệu chính thức của cTrader nói `Symbol.PipValue` là giá trị tiền của một
pip **tại lúc cBot khởi động/indicator khởi tạo**, sau đó giữ nguyên và không
cập nhật real-time. Vì vậy kết luận của audit phải được hiểu chính xác:

- cTrader đã quy đổi **đúng tại thời điểm khởi tạo**;
- với symbol quote USD, vấn đề tỷ giá chéo không tồn tại;
- với EUR/JPY/HKD, một cBot chạy lâu hoặc một backtest kéo dài có thể dùng
  snapshot tỷ giá ban đầu để sizing, nên risk tiền thật về sau có thể lệch
  khỏi RiskPercent khi tỷ giá thay đổi;
- HKD thường biến động ít quanh peg; EUR và JPY có thể tạo sai lệch đáng kể
  hơn trên thời gian dài.

Đây là đặc tính được tài liệu hoá của API, không phải lỗi sai chiều quy đổi
phát hiện trong sáu phép thử trên. Chưa thay đổi code để xử lý điểm này.

Nguồn tham khảo:

- cTrader Algo `Symbol` API: https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/
- Federal Reserve H.10, tuần 06/01/2025: https://www.federalreserve.gov/releases/h10/20250106/

## Phiên bản đã đối chiếu

- `Combo/Combo/Combo.cs` SHA-256:
  `DD7A748D054684F664ED22F5579EB2AB96C807E3737C34DCF9B52491FF2A2336`
- `MA Cross/MA Cross/MA Cross.cs` SHA-256:
  `31E3F5B0DE26239499186983DDC7CC4F14D910F3AA8D19E7F4F089B0622178D5`
- `Combo.algo` dùng cho XAUUSD/BTCUSD/JP225.cash SHA-256:
  `F55E7FBA663B6A212C7F19D6E63E2482F922E0B7865411CE19DA7B66780C04B7`

Hai source hiện dùng cùng công thức PipValue/volume và cùng margin-cap. Các
artifact GER40/HK50 cũ hơn nhưng công thức sizing liên quan không thay đổi;
XAUUSD/BTCUSD/JP225.cash và GUI US30 xác nhận lại trên binary/source đã có
margin fix.

## Quyết định còn mở

Audit này không thay đổi kết luận riêng về margin: `PipValue` đúng không đồng
nghĩa vị thế đã có một trần exposure chủ động. Đề xuất `MaxMarginPercent`
(giới hạn margin mỗi lệnh theo % Equity, độc lập với free margin còn lại) vẫn
chưa được người dùng duyệt con số mặc định và chưa được code.
