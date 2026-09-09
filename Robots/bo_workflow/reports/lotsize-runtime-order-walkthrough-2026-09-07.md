# Lot size — diễn giải theo một tiến trình đặt lệnh thật

Ngày ghi nhận: 2026-09-07  
Phạm vi: chỉ Combo và MA Cross trong repo hiện tại.

## Sự thật đã code

- Hai bot tính `VolumeInUnits`, không tính lots để gửi order.
- Công thức lõi: `riskAmount / (stopLossPips * PipValueNow())`.
- `PipValueNow()` dùng `Symbol.PipSize` nếu QuoteAsset trùng Account.Asset; nếu khác thì gọi
  `QuoteAsset.Convert(Account.Asset, PipSize * 1_000_000) / 1_000_000`.
- Volume được kiểm tra min/max, normalize xuống và có thể bị `CapVolumeByMargin` giảm thêm.
- Combo gửi `VolumeInUnits` vào `PlaceStopOrder`; MA Cross gửi vào `ExecuteMarketOrder`.
- Lots chỉ được tính để ghi log bằng `Symbol.VolumeInUnitsToQuantity(volume)`.
- Không hard-code `LotSize`, contract size, PipSize, FX symbol hay FX rate theo tên symbol.
- Hằng số `ProbeUnits=1_000_000` chỉ chống rounding khi convert; `MarginSafetyFactor=0.98` chỉ
  tạo đệm khi margin cap kích hoạt; `RoundingMode.Down` là chính sách làm tròn bảo thủ.

## Ví dụ runtime thật: Combo / HK50

Backtest Ticks 01-08/01/2025, FTMO `7563609`, Balance đầu `$10,000`, Risk mặc định `1%`:

```text
Pending Sell dự kiến: Entry 19,597.20; SL 19,740.6889
Khoảng cách dự kiến: 143.4889 points
QuoteAsset: HKD; AccountAsset: USD
RiskMoney: $100
Volume tính và gửi: 5.41 units
Runtime LotSize: 1 -> 5.41 lots
```

Bản chất phép tính:

```text
1 unit mất khoảng 143.4889 HKD nếu chạm SL
143.4889 HKD được cTrader convert thành khoảng $18.48
$100 / $18.48 ≈ 5.41 units
```

Lệnh thực tế khớp tại `19,596.43`, đóng tại `19,741.47`, khoảng cách thật `145.04` points và
report ghi gross P/L `-$100.89`. Chênh so với `$100` dự kiến đến từ giá thực thi và conversion
tại thời điểm đóng; sizing là nominal gross stop-risk.

Artifacts:

- `research/cli_runs/Combo_HK50.cash_h1_20260907-001841/log.txt`
- `research/cli_runs/Combo_HK50.cash_h1_20260907-001841/report.json`
- `reports/lotsize-currency-conversion-full-audit-CODEX-2026-09-06.md`

## Quy ước trả lời tiếp theo

Khi trả lời câu hỏi về sizing, phân biệt rõ ba lớp: code bot tự tính, API cTrader cung cấp và
hành vi broker/runtime. Không suy diễn tính năng chưa có trong hai source. Trả lời ngắn, chỉ nêu
chi tiết đủ để giải quyết câu hỏi.
