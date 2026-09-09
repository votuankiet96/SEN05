# CLI backtest US30 — 2025-01-01 đến 2026-09-02

Hai lượt được chạy tuần tự bằng `ctrader-cli 5.9.0.38`, broker
`FTMO Platform`, account demo `7563609`, data mode `Ticks`. Pipeline chỉ xác
nhận hoàn tất sau khi `report.json` parse được và `testingPeriod` khớp kỳ yêu
cầu. Sau cả hai lượt còn 0 tiến trình `ctrader-cli.exe`.

## Input

| Strategy | Symbol | Period | CSV | KSL/KTP | Risk |
|---|---|---|---|---|---:|
| Combo | US30.cash | H4 | `combo_US30_H4_full_history_signals.csv` | Fib0618/Fib0618 | 1% balance |
| MA Cross | US30.cash | M30 | `ma_cross_US30_M30_full_history_signals.csv` | Fib0618/Fib0618 | 1% balance |

Chỉ `SignalFilePath` được override trong `params.cbotset`; KSL, KTP, Risk và
các FTMO guard dùng default của file `.algo` đã build. Các guard đều OFF.

## Kết quả performance

| Metric | Combo H4 | MA Cross M30 |
|---|---:|---:|
| Starting capital | 10,000.00 | 10,000.00 |
| Ending balance | 9,766.53 | 10,408.53 |
| Net profit | -233.47 | +408.53 |
| ROI | -2.33% | +4.09% |
| Profit factor | 0.97 | 1.03 |
| Closed trades | 153 | 310 |
| Winning / losing | 79 / 74 | 157 / 153 |
| Win rate | 51.63% | 50.65% |
| Max balance drawdown | 15.54% | 18.00% |
| Max equity drawdown | 17.43% | 18.94% |

Không xếp hạng trực tiếp hai kết quả này vì khác bot và timeframe. Công cụ so
sánh tự động cũng đánh dấu `Comparable=False`, lý do `Bot` và `Period` khác.

## Signal → order → position

### Combo H4 — pending stop

- CSV có 1,101 signal; 900 trước kỳ test và 201 trong kỳ.
- 201/201 signal trong kỳ được scheduler xử lý; `not-processed=0`.
- 200 pending order được đặt thành công, 0 thất bại.
- 1 signal bị bỏ qua vì đã có exposure cùng hướng.
- 153/200 pending order fill thành vị thế: **76.50% số order đã đặt**, tương
  đương **76.12% số signal trong kỳ**.
- 47 pending không fill và đều giải thích được:
  - 36 bị hủy khi hết hạn sau 3 chart bars;
  - 11 bị hủy khi có signal đảo chiều.
- Có thêm 1 reversal đóng position đang mở. Cuối kỳ: 0 pending order và 0
  position còn mở; `history.items=153` khớp số fill/closed trade.

### MA Cross M30 — market order

- CSV có 2,936 signal; 2,375 trước kỳ test và 561 trong kỳ.
- 561/561 signal trong kỳ được scheduler xử lý; `not-processed=0`.
- 310 market order thành công và mở position: **55.26%**.
- 251 market order bị broker từ chối: **44.74%**, toàn bộ được cBot ghi
  `NOT_ENOUGH_MARGIN_BALANCE` / `NoMoney`.
- Với market order, tầng “broker accepted” và “position opened” trùng nhau.
  Cuối kỳ: 0 position mở; `history.items=310` khớp số order thành công.

## Artifact

- Combo raw run:
  `research/cli_runs/Combo_US30.cash_h4_20260902-034316/`
- MA Cross raw run:
  `research/cli_runs/MA Cross_US30.cash_m30_20260902-040711/`
- Comparison CSV/JSON/Markdown:
  `research/cli_comparisons/US30_2025-current_Combo-H4_vs_MA-Cross-M30_20260902-041052/`

Mỗi raw run có `params.cbotset`, `command.txt`, `log.txt`, `report.json` và
`run-summary.json`. Hash CSV được lưu trong manifest để phát hiện thay đổi dữ
liệu giữa các lần chạy.

## Đối chiếu lịch bar CSV và FTMO

Đối chiếu trực tiếp `bartime` trong CSV với lưới timestamp chart thực tế lưu
trong `report.json/equity.points` cho thấy hai lịch không trùng tuyệt đối:

| Strategy | Signal trong kỳ | Có timestamp FTMO khớp | Không có timestamp khớp |
|---|---:|---:|---:|
| Combo H4 | 201 | 170 | 31 (15.42%) |
| MA Cross M30 | 561 | 550 | 11 (1.96%) |

Code hiện hành không còn yêu cầu exact-match. Mỗi signal có
`AvailableTime = bartime + timeframe`; cBot xử lý tại tick FTMO đầu tiên ở
hoặc sau mốc đó. Vì vậy không có bar khớp **không đồng nghĩa mất signal**:
cả hai lượt vẫn có `not-processed=0`.

Đối chiếu timestamp xử lý trong log:

- Combo: 161/201 được xử lý trong vòng 1 phút từ `AvailableTime`; 40 phải
  chờ lâu hơn. Phần lớn delay là 5 hoặc 65 phút; 9 trường hợp chờ qua cuối
  tuần khoảng 2 ngày.
- MA Cross: 540/561 trong vòng 1 phút; 21 phải chờ lâu hơn, từ 5 phút tới
  khoảng 2 ngày.

Ví dụ:

- Combo `bartime=2025-06-16 10:00` không có H4 timestamp FTMO khớp nhưng vẫn
  được đặt lúc 14:00, đúng `AvailableTime`.
- Combo `bartime=2025-06-02 18:00`, mốc danh nghĩa 22:00 nhưng tick giao dịch
  đầu tiên là 22:05.
- MA Cross `bartime=2025-06-18 21:00`, mốc danh nghĩa 21:30 nhưng lệnh được
  thực thi lúc 22:05.

Các cụm delay cố định quanh giờ nghỉ ngày/cuối tuần cho thấy đây là chênh
lịch phiên có quy luật giữa nguồn CSV và FTMO, không phải offset ngẫu nhiên
đơn giản có thể cộng/trừ cố định cho mọi signal.

## Kết luận kiểm định

- Cả hai cBot đọc và xử lý toàn bộ signal nằm trong kỳ test; không có signal
  còn `not-processed`.
- Combo thực thi đúng cơ chế pending: đặt tại signal, chỉ thành position nếu
  giá fill; các order không fill đều được truy nguyên thành expiry/reversal.
- MA Cross thực thi đúng cơ chế market, nhưng tỷ lệ reject margin rất cao
  (251/561). Đây là vấn đề sizing/margin đã biết, không phải pipeline CLI bỏ
  sót signal.
