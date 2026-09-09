# Combo / US100.cash / H1 — tick backtest, 01/01–28/02/2026 (2 tháng)

Chạy bằng standalone cTrader CLI 5.9.0.38 trên VM-BO20, 2026-09-05, dùng
`Combo.algo` đã build sẵn (hash trong `input.json`). Không sửa source/algo/GUI.
Cùng khuôn với lượt GOLD/XAUUSD cùng ngày.

## Input

- Broker/account: FTMO Platform / 7563609; vốn đầu kỳ USD 10,000; data mode `Ticks`
  (`report.json` = `tickDataFromServer`). Tick cache US100.cash Jan–Feb 2026 đã có
  sẵn → chạy ~87 giây.
- Signal: `Z:\Desktop\og_program\runtime\exports\combo_US100_H1_full_history_signals.csv`
  (SHA-256 trong `input.json`).
- KslLevel `2` → 1.0 × ATR; KtpLevel `4` → 2.618 × ATR; RiskPercent 1%;
  MaxMarginPercent 50%; 3 FTMO guard OFF.
- Commission/spread 0; swaps USD -43.00. Đây là kiểm tra THỰC THI, không phải
  ước lượng hiệu suất đủ chi phí.

## Kết quả performance (report.json)

| Metric | Giá trị |
|---|---:|
| Ending balance | 9,222.44 |
| Net profit | -777.56 (ROI -7.78%) |
| Profit factor | 0.83 (long 0.46 / short 1.22) |
| Closed trades | 61 (28 long / 33 short) |
| Winning / losing | 15 / 46 |

## Signal → order (đối chiếu log.txt ↔ events.json ↔ OnStop summary)

- CSV 4461 signal; 4136 trước khi bot khởi động; **83 trong kỳ**; 242 sau kỳ.
- 83/83 signal trong kỳ được xử lý. **79 pending stop order đặt thành công, 0 reject.**
- events.json: 79 `Create Stop Order` = 61 `Stop Order Filled` + 17 `Order cancelled`
  + 1 lệnh còn treo cuối kỳ (bartime 2026-02-27 20:00, đặt lúc 21:00, kỳ test kết
  thúc ~21:50 nên chưa kịp khớp/hết hạn).
- 17 cancelled = 9 hết hạn 3 nến + 8 huỷ do đảo chiều.
- 61 vị thế = 39 SL + 14 TP + 8 đóng do đảo chiều.
- 16 lần đảo chiều = 8 đóng vị thế + 8 huỷ lệnh chờ.
- 4 tín hiệu bị bỏ vì đã có exposure cùng hướng.
- **1 lần margin-cap** (bartime 2026-01-13 08:00): volume risk-based 3.20 → 2.88
  units do vượt trần 50% Equity; risk thực ~0.90%. 0 lần margin-block.
- SL/TP của cả 79 lệnh khớp `Entry ± {1.0|2.618} × ATR` với giá trị bot tự log.
  Không sai direction, không sai entry.

## Artifact

`report.json` / `report.html` / `events.json` / `log.txt` (CLI+bot) / `bot-log.txt`
(native, copy từ GUI instance `Combo\c76c4d50-b9d0-4b41-86f5-f224fd5c6e4a\Backtesting`) /
`params.cbotset` / `gui-instance-parameters.cbotset` / `run.ps1` / `arguments.json` /
`input.json` / `run-summary.json`.

File đối chứng signal-by-signal:
`research/reports/combo-US100-jan-feb2026-signal-trace-2026-09-05.csv`.
