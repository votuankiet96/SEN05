# Combo / XAUUSD / H1 — tick backtest, 01/01–28/02/2026 (2 tháng)

Chạy bằng standalone cTrader CLI 5.9.0.38 trên VM-BO20, 2026-09-05, dùng
`Combo.algo` đã build sẵn (hash trong `input.json`). Không sửa source/algo/GUI.

## Input

- Broker/account: FTMO Platform / 7563609; vốn đầu kỳ USD 10,000; data mode `Ticks`
  (`report.json` xác nhận `tickDataFromServer`). Tick cache XAUUSD 01/01–28/02/2026
  đã có sẵn trên đĩa (tải từ 2026-09-03) nên chạy chỉ mất ~77 giây.
- Signal: `Z:\Desktop\og_program\runtime\exports\combo_GOLD_H1_full_history_signals.csv`
  (SHA-256 trong `input.json`). Cột `bartime,atr,entry,signal`.
- KslLevel = `2` → SlFibLevel.Fib1000 = **1.0 × ATR**.
- KtpLevel = `4` → TpFibLevel.Fib2618 = **2.618 × ATR**.
- RiskPercent = 1% balance; MaxMarginPercent = 50% equity. 3 FTMO guard: OFF.
- Commission/spread = 0 (default CLI). `report.json` có swaps USD -20.38. Đây là
  bài kiểm tra THỰC THI (signal → lệnh), không phải ước lượng hiệu suất đủ chi phí.
- `--end=28/02/2026 23:59`; report lưu endDate = 28/02 00:00 và nhãn `1m 27d`,
  nhưng bot log xử lý hết ngày 27/02 (tín hiệu cuối trong kỳ: bartime 2026-02-27 11:00).

## Kết quả performance (report.json)

| Metric | Giá trị |
|---|---:|
| Ending balance / equity | 9,338.84 |
| Net profit | -661.16 (ROI -6.61%) |
| Profit factor | 0.77 (long 1.36 / short 0.38) |
| Closed trades | 49 (24 long / 25 short) |
| Winning / losing | 11 / 38 |
| Max equity drawdown | ~6.6% |

## Signal → order → position (đối chiếu 3 nguồn: log.txt, events.json, OnStop summary)

- CSV có 4593 signal; **4265 trước khi bot khởi động** (AvailableTime < ~2026-01-01 23:05),
  **81 trong kỳ**, 247 sau kỳ (`not-processed`, AvailableTime > 28/02).
- 81/81 signal trong kỳ được scheduler xử lý (`processed=81`, `not-processed` = signal ngoài kỳ).
- **71 pending stop order đặt thành công, 0 bị broker từ chối** (`failed=2` là 2 lần
  SIZE TOO SMALL — xem dưới, không phải reject).
- events.json: 71 `Create Stop Order` = 49 `Stop Order Filled` + 22 `Order cancelled`.
  22 cancelled = **16 hết hạn sau 3 nến** + **6 bị huỷ do tín hiệu đảo chiều**.
- 49 position: 28 `Stop Loss Hit` + 10 `Take Profit Hit` + 11 `Position closed` (đảo chiều).
- 17 lần đảo chiều (`reversed=17`) = 11 đóng vị thế đang mở + 6 huỷ lệnh chờ đang treo.
- 8 tín hiệu bị bỏ vì đã có exposure cùng hướng (`same-direction-skipped=8`).
- 2 tín hiệu (2026-01-29 15:00, 2026-02-02 11:00) bị bỏ vì SL quá rộng (ATR ~111–118):
  risk 1% chỉ ra 0.77 / 0.82 units < mức tối thiểu 1.00 của broker → không gửi lệnh
  (`CalculateVolume` trả 0). Đây là hành vi thiết kế đúng, không phải lỗi.
- **margin-capped = 0, margin-blocked = 0**: ở MaxMarginPercent=50% không tín hiệu GOLD
  nào bị cắt volume (khớp nhận định trong CLAUDE.md: index/GOLD hầu như không bị cap ở 50%).
- SL/TP của cả 71 lệnh đặt đều khớp `Entry ± {1.0|2.618} × ATR` với đúng giá trị bot tự
  log (sai khác < 0.01). Không sai direction, không sai entry (pending đặt đúng cột `entry`).

## Artifact

- `report.json` / `report.html` — báo cáo raw + readable của cTrader.
- `events.json` — order/fill/exit events (copy từ GUI instance
  `Combo\4453c79b-3d35-4559-b2b0-5ff5c6087160\Backtesting`).
- `log.txt` — CLI console + bot log gộp; `bot-log.txt` — bot log native.
- `params.cbotset` (CLI) / `gui-instance-parameters.cbotset` (bản cTrader ghi ra).
- `run.ps1` — runner async tái lập được (chỉ chạy lại trong thư mục MỚI).
- `arguments.json`, `input.json` (hash algo + CSV + CLI version), `run-summary.json`.

File đối chứng signal-by-signal: `research/reports/combo-GOLD-jan-feb2026-signal-trace-2026-09-05.csv`.
