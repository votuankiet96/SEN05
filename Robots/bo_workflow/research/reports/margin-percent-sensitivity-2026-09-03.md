# Độ nhạy `MaxMarginPercent` trên Combo/US30.cash/H1 (01-30/01/2026)

Chạy 2026-09-03 qua `ctrader-cli`, 6 tiến trình **song song** (theo yêu cầu người dùng), cùng
`SignalFilePath = Z:\Desktop\og_program\runtime\exports\combo_US30_H1_full_history_signals.csv`
(vừa export lại), `RiskPercent=1, KslLevel=3, KtpLevel=7`, `Combo.algo` build
9/2/2026 22:49. Kết quả lưu tại
`research/cli_batches/Combo_US30.cash_h1_parallelgrid_20260903-013357/`.

## Bảng tổng hợp

| MaxMarginPercent | Lệnh đặt | Bị cap | %cap | Bị block hẳn | Risk% thực tế (min/avg/max) | NetProfit | ROI% | PF | MaxEquityDD% |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 5%   | 31 | 31 | 100% | 0 | 0.03 / 0.09 / 0.19 | $156.53  | 1.57  | 2.61 | 0.48 |
| 10%  | 31 | 31 | 100% | 0 | 0.06 / 0.19 / 0.38 | $312.20  | 3.12  | 2.60 | 0.95 |
| 20%  | 31 | 31 | 100% | 0 | 0.13 / 0.38 / 0.75 | $633.52  | 6.34  | 2.56 | 1.94 |
| 30%  | 31 | 30 | 97%  | 0 | 0.19 / 0.55 / 0.97 | $967.28  | 9.67  | 2.55 | 2.86 |
| 50%  | 31 | 17 | 55%  | 0 | 0.32 / 0.61 / 0.91 | $1,612.85 | 16.13 | 2.72 | 3.28 |
| 100% (tắt hẳn, chỉ còn FreeMargin) | 31 | 4 | 13% | 0 | 0.64 / 0.84 / 0.94 | $1,870.25 | 18.70 | 2.48 | 4.52 |

Tất cả 22 lệnh khớp thành vị thế thật (`totalTrades.all=22`, `winningTrades.all=11` — không đổi
qua mọi mức, vì tín hiệu/entry/SL/TP không đổi, chỉ volume đổi) — nghĩa là **PF gần như không đổi
đáng kể (2.48–2.72)** dù NetProfit chênh nhau ~12 lần giữa 5% và 100%: cấu trúc thắng/thua của
chiến lược ổn định, chỉ có SIZE của mỗi lệnh khác nhau.

Ở mọi mức, lý do cap in ra đều là "MaxMarginPercent limit hit" kể cả ở 100% — không phải bug:
với kiến trúc hiện tại (chỉ 1 exposure/lần, đóng lệnh cũ trước khi mở lệnh mới), tại đúng thời
điểm tính volume luôn không có lệnh nào đang mở, nên `FreeMargin == Equity` chính xác — 2 trần
`MaxMarginPercent×Equity` và `FreeMargin` trùng giá trị tuyệt đối tại 100%, code gán nhãn lý do
cho `MaxMarginPercent` khi bằng nhau (chỉ là cách đặt tên trong log, không ảnh hưởng số volume
tính ra).

## Đọc bảng

- **5%→30%**: gần như MỌI lệnh đều bị cap (97-100%) — `RiskPercent=1%` gần như vô nghĩa, risk
  thực tế trần trên chỉ đạt tối đa ~0.97% (ở 30%) hoặc thấp hơn nhiều ở các mức nhỏ hơn.
- **50%**: bắt đầu thấy rõ hiệu ứng "một phần lệnh thoát cap" — 55% lệnh vẫn bị cap, risk thực tế
  đã lên được tới 0.91% (gần chạm 1% khai báo) cho lệnh SL rộng nhất.
- **100% (tắt hẳn)**: chỉ còn 13% lệnh bị cap (4/31) — đây là mức DUY NHẤT risk% thực tế tối đa
  chạm gần đúng 1% khai báo (0.94%) cho hầu hết lệnh, nhưng KHÔNG có nghĩa "không cap gì cả" —
  4 lệnh margin lớn nhất vẫn bị FreeMargin/MaxMarginPercent(=Equity) chặn lại, đúng là hành vi
  "Fix A" cũ (đã từng gây `NOT_ENOUGH_MARGIN_BALANCE` ở phiên bản TRƯỚC KHI có Fix A, tức phiên
  bản gốc chưa vá gì — mức 100% ở đây vẫn AN TOÀN hơn bản gốc vì Fix A/FreeMargin luôn còn đó).
- Không mức nào bị `margin-blocked` (lệnh bị bỏ hẳn vì volume sau cap nhỏ hơn min) — kể cả ở 5%,
  cho thấy tín hiệu US30/H1 giai đoạn này không có SL nào quá hẹp tới mức bị chặn hoàn toàn.
- **ROI/NetProfit tăng gần như tuyến tính theo MaxMarginPercent** (đúng như dự đoán từ công thức
  `volume_final ≈ marginBudget × Leverage/Price` — tuyến tính theo marginBudget) — nhưng
  **MaxEquityDrawdown% cũng tăng gần như cùng tỷ lệ** (0.48%→4.52%, x9.4 lần trong khi NetProfit
  x12 lần) — không có "bữa trưa miễn phí": lợi nhuận cao hơn đi kèm biến động tài khoản cao hơn
  tương ứng, đúng bản chất đòn bẩy (không phải edge chiến lược tốt lên).

## Chưa kết luận

Báo cáo chỉ nêu số liệu khách quan — **chưa chọn mức khuyến nghị**, để bàn tiếp dựa trên đây.
Điểm cần cân nhắc khi quyết định: MaxEquityDD 4.52% ở mức 100% đã gần chạm nửa hạn mức FTMO daily
5% CHỈ TỪ 1 THÁNG BACKTEST — hạn mức đó tính theo NGÀY chứ không phải theo cả tháng, nên con số
DD 4.52% "toàn kỳ" chưa nói lên liệu có ngày nào riêng lẻ vượt 5% hay không (cần xem thêm dữ liệu
theo ngày nếu muốn trả lời chính xác câu đó).
