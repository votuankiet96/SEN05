# Trần `MaxMarginPercent` an toàn theo stress-test gap lịch sử — 11 symbol

Phương pháp (đề xuất và thống nhất trong phiên thảo luận 2026-09-03): `MaxMarginPercent`
không nên chọn theo đường cong NetProfit/ROI của backtest/optimize (dữ liệu thường không chứa
sự kiện gap thảm khốc nên đường cong luôn "đẹp dần" một cách gây hiểu lầm) — mà phải suy ra từ
**gap % tệ nhất từng xảy ra thật trong lịch sử** của chính symbol đó, kết hợp đòn bẩy hiệu lực
thật (đo thực nghiệm, không phải con số đòn bẩy quảng cáo chung của tài khoản).

```
Lỗ tệ nhất nếu gặp gap = gap%_tệ_nhất × Đòn_bẩy × Equity × MaxMarginPercent%
MaxMarginPercent%(an toàn) = target_loss% ÷ (gap%_tệ_nhất × Đòn_bẩy)
```

`target_loss%` = phần ngân sách FTMO daily-loss (5%) chấp nhận để 1 CÚ GAP DUY NHẤT ăn vào —
không phải toàn bộ ngân sách ngày (còn phải chừa cho các lệnh thua bình thường khác).

## Dữ liệu nguồn

- **Gap%**: `|Open[i] − Close[i-1]| / Close[i-1]` trên nến **D1**, lấy từ Data Warehouse nội bộ
  (`DWH.Fact_OHLCV`, SQL Server 10.11.12.6), 2,000-5,400 nến/symbol, hầu hết từ 2014/2015 hoặc
  sớm hơn tới 2026-09. Khớp đúng các sự kiện lịch sử thật (Brexit 6/2016, COVID crash 3/2020).
- **Đòn bẩy hiệu lực**: đo thực nghiệm qua `ctrader-cli backtest` (Combo, `MaxMarginPercent=1%`
  ép mọi lệnh bị cap, đọc `estimatedMargin` thật từ log `MARGIN GUARD`), 01-28/02/2026,
  27-110 lệnh/symbol tuỳ mật độ tín hiệu. Với symbol không quote USD (DE40/FR40/SP35: EUR,
  UK100: GBP, HK50: HKD, J225: JPY) — quy đổi giá về USD bằng tỷ giá thật cùng kỳ trước khi
  tính (EURUSD≈1.1598, GBPUSD≈1.3371, USDHKD≈7.8 neo cố định, USDJPY≈158.1, lấy từ DWH).

## Bảng kết quả đầy đủ

| Symbol | Gap%_tệ nhất | Ngày | Đòn bẩy hiệu lực | An toàn @1.25% | An toàn @2.5% | An toàn @5% |
|---|---:|---|---:|---:|---:|---:|
| US30   | 5.41%  | 2018-12-26 | 15.04 | 1.54% | 3.07% | 6.14% |
| US500  | 5.38%  | 2018-12-26 | 15.06 | 1.54% | 3.09% | 6.18% |
| US100  | 6.61%  | 2018-12-26 | 15.22 | 1.24% | 2.48% | 4.96% |
| DE40   | 9.95%  | 2016-06-23 | 14.57 | 0.86% | 1.72% | 3.45% |
| UK100  | 3.82%  | 2020-03-08 | 14.17 | 2.31% | 4.62% | 9.25% |
| FR40   | 14.70% | 2020-03-12 | 14.12 | 0.60% | 1.20% | 2.41% |
| SP35   | 16.72% | 2016-06-24 | 8.52  | 0.88% | 1.75% | 3.51% |
| HK50   | 3.97%  | 2016-02-10 | 9.16  | 3.44% | 6.88% | 13.77% |
| J225   | 3.78%  | 2018-12-26 | 13.96 | 2.37% | 4.74% | 9.48% |
| GOLD   | 1.83%  | 2026-03-01 | 14.07 | 4.85% | 9.71% | 19.42% |
| BTCUSD | 9.03%  | 2018-03-18 | 1.23  | 11.25%| 22.5% | 45.0% |

(3 cột cuối = `target_loss%` lần lượt 1.25% / 2.5% / 5% — tương ứng 1/4, 1/2, và toàn bộ hạn
mức FTMO daily 5%, để 1 cú gap tệ nhất lịch sử ăn vào. Công thức tuyến tính theo target nên có
thể nội suy mức khác dễ dàng.)

## Đọc bảng — phát hiện quan trọng nhất

**Ở mốc tham chiếu 2.5% (khuyến nghị làm điểm khởi đầu thảo luận), hầu hết INDEX cho ra trần an
toàn THẤP HƠN HẲN mức mặc định 10% đang cấu hình** — đặc biệt DE40 (1.72%), FR40 (1.20%), SP35
(1.75%) do từng có gap cực đoan thật (Brexit, COVID) kết hợp đòn bẩy ~14-15. Ngược lại **BTCUSD
có trần an toàn RỘNG hơn nhiều** (22.5%) vì đòn bẩy hiệu lực quá thấp (~1.23) tự nó đã giới hạn
notional dù % margin cho phép cao — gap tệ nhất tuyệt đối cao (9%) nhưng đòn bẩy thấp bù lại.

**GOLD đứng giữa** (9.71%) — gần khớp mức mặc định 10% hiện tại, không cần chỉnh nhiều nếu giữ
target 2.5%.

## Giới hạn của phân tích — cần nêu rõ

- Gap đo trên **D1** (khoảng cách cuối tuần/qua đêm) — không bắt được flash-crash TRONG PHIÊN
  (gap giữa 2 tick liên tiếp lúc thị trường đang mở, tin cực mạnh) — nếu loại rủi ro này lớn hơn
  gap cuối tuần cho 1 symbol cụ thể, trần tính ra ở đây có thể LẠC QUAN hơn thực tế.
- Gap lịch sử "tệ nhất" chỉ tệ nhất **TRONG QUÁ KHỨ ĐÃ QUAN SÁT** — không phải trần cứng của
  tương lai, thị trường luôn có thể tạo kỷ lục mới.
- Đòn bẩy đo thực nghiệm trên 1 tháng dữ liệu (02/2026) — giả định ổn định theo thời gian; broker
  có thể đổi tỷ lệ margin bất kỳ lúc nào (đặc biệt quanh sự kiện lớn, nhiều broker CHỦ ĐỘNG siết
  đòn bẩy trước sự kiện rủi ro cao — nếu vậy đòn bẩy THỰC lúc gap xảy ra có thể khác lúc đo).
- Chưa nhân với xác suất xảy ra — bảng này trả lời "nếu gap tệ nhất lịch sử lặp lại thì mất bao
  nhiêu", không trả lời "xác suất gap đó xảy ra trong 1 năm bất kỳ là bao nhiêu" (cần mô hình
  thống kê riêng nếu muốn định lượng thêm).
