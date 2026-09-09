# Combo / US500.cash / H1 — tick backtest, 01/01–28/02/2026 (2 tháng)

Chạy bằng standalone cTrader CLI 5.9.0.38 trên VM-BO20, 2026-09-05, dùng
`Combo.algo` đã build sẵn (hash trong `input.json`). Không sửa source/algo/GUI.
Cùng khuôn với lượt GOLD/US100 cùng ngày.

## Input

- Broker/account: FTMO Platform / 7563609; vốn đầu kỳ USD 10,000; data mode `Ticks`.
  Tick cache US500.cash Jan–Feb 2026 đã có sẵn.
- Signal: `Z:\Desktop\og_program\runtime\exports\combo_US500_H1_full_history_signals.csv`.
- KslLevel `2` → 1.0 × ATR; KtpLevel `4` → 2.618 × ATR; RiskPercent 1%;
  MaxMarginPercent 50%; 3 FTMO guard OFF.
- Commission/spread 0; swaps USD -64.76. Kiểm tra THỰC THI, không phải hiệu suất đủ chi phí.

## Kết quả performance (report.json)

| Metric | Giá trị |
|---|---:|
| Ending balance | 8,666.42 |
| Net profit | -1,333.58 (ROI -13.34%) |
| Profit factor | 0.71 (long 0.52 / short 0.92) |
| Closed trades | 62 (33 long / 29 short) |
| Winning / losing | 15 / 47 |

## Signal → order (đối chiếu log.txt ↔ events.json ↔ OnStop summary)

- CSV 4530 signal; 4188 trước khi bot khởi động; **86 trong kỳ**; 256 sau kỳ.
- 86/86 signal trong kỳ được xử lý. **83 pending stop order đặt thành công, 0 reject.**
- events.json: 83 `Create Stop Order` = 62 `Stop Order Filled` + 20 `Order cancelled`
  + 1 lệnh còn treo cuối kỳ (bartime 2026-02-27 20:00).
- 20 cancelled = 11 hết hạn 3 nến + 9 huỷ do đảo chiều.
- 62 vị thế = 43 SL + 13 TP + 6 đóng do đảo chiều.
- 15 lần đảo chiều = 6 đóng vị thế + 9 huỷ lệnh chờ.
- 3 tín hiệu bị bỏ vì đã có exposure cùng hướng.
- **7 lần margin-cap** (US500 leverage hiệu lực thấp hơn US100 → volume risk-based
  hay chạm trần 50% Equity hơn): ví dụ 2026-01-07 08:00 cắt 19.40 → 10.36 units
  (risk thực ~0.53%). 1 trong 7 lần vừa bị cap vừa hết hạn 3 nến. 0 lần margin-block.
- SL/TP của cả 83 lệnh khớp `Entry ± {1.0|2.618} × ATR` với giá trị bot tự log.
  Không sai direction, không sai entry.

## Artifact

`report.json` / `report.html` / `events.json` / `log.txt` / `bot-log.txt`
(native, copy từ GUI instance `Combo\c83cd268-2312-4cdd-aaf0-ddb304df74dc\Backtesting`) /
`params.cbotset` / `gui-instance-parameters.cbotset` / `run.ps1` / `arguments.json` /
`input.json` / `run-summary.json`.

File đối chứng signal-by-signal:
`research/reports/combo-US500-jan-feb2026-signal-trace-2026-09-05.csv`.
