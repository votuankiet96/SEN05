---
title: Monte Carlo (block-bootstrap) — equity vs balance làm chuỗi daily-return, đối chiếu chuẩn ngành
date: 2026-09-22
status: research xong, CHƯA code — cần chốt hướng sửa trước khi đụng optimize/montecarlo.py
---

# Câu hỏi gốc

`core_engine/optimize/montecarlo.py::_daily_returns()` cần 1 giá trị/ngày để dựng chuỗi
daily-return cho block-bootstrap. Lượt sửa 2026-09-22 trước đó đổi thứ tự ưu tiên
`point.get("balance", point.get("equity", initial))` → `point.get("equity", point.get("balance", initial))`
dựa trên xác nhận academy.ftmo.com rằng luật FTMO tính trên **equity** (gồm lãi/lỗ nổi), không phải
balance. Khi soát lại 6 `report.json.gz` THẬT đã lưu (US30/JP225/HK50 parity + walk-forward demo),
phát hiện **field tên `"equity"` không bao giờ tồn tại** trong output cTrader thật — field thật là
`balance`, `minEquity`, `maxEquity`, `timestamp`. Tức lượt sửa trước **không có tác dụng gì trên dữ
liệu thật** (luôn rơi xuống `balance` vì `"equity"` luôn `None`). Cần research xem chuẩn ngành dùng
gì để quyết định sửa đúng.

# 1. Chuẩn ngành cho chuỗi return của 1 tài khoản/quỹ: mark-to-market (equity), không phải chỉ số dư đã chốt (balance)

GIPS (Global Investment Performance Standards) yêu cầu tính Time-Weighted Return dựa trên **fair
value / mark-to-market** tại mỗi mốc định giá — với tài sản định giá được hàng ngày (như tài khoản
forex/CFD, không phải private equity kém thanh khoản), chuẩn là **định giá mỗi ngày theo giá thị
trường thực** (bao gồm vị thế đang mở), không phải chỉ chốt khi lệnh đóng. Đây là lý do NAV của quỹ
luôn là **mark-to-market NAV** (giá trị tài sản ròng tính theo giá thị trường tại thời điểm định giá),
không phải "giá trị đã thực hiện". Balance-only (chỉ tính lệnh đã đóng) tương đương bỏ qua toàn bộ
phần mark-to-market của vị thế đang mở — không phải cách một quỹ/tài khoản chuyên nghiệp báo cáo
return của mình.

→ Kết luận: **chuỗi return dùng để "chain" (nhân dồn) qua các ngày nên dựa trên equity (mark-to-
market), không phải balance thuần** — đúng hướng tôi đã sửa lần trước, chỉ là sai TÊN field.

# 2. Chuẩn ngành cho rule daily-loss của prop firm: balance-based vs equity-based là 2 MÔ HÌNH KHÁC NHAU, không phải lỗi đặt tên

Khảo sát nhiều prop firm (không riêng FTMO) xác nhận đây là 2 trường phái tồn tại song song:

- **Balance-based**: đo lỗ so với lệnh ĐÃ ĐÓNG. Lỗ nổi $3,000 không đụng tới hạn mức nếu lệnh
  hồi phục trước khi đóng.
- **Equity-based**: đo trên equity SỐNG (real-time), gồm cả lỗ nổi. Cùng ví dụ trên: equity tụt
  xuống $97,000 ngay khi lỗ nổi $3,000 xuất hiện — đã dùng $3,000/$5,000 hạn mức ngày, dù chưa
  đóng lệnh nào.
- Mốc tham chiếu phổ biến nhất: **balance đầu ngày** (không phải equity đầu ngày) làm baseline,
  rồi so với **equity thấp nhất đạt được trong ngày** (một số firm dùng "trailing" theo equity
  cao nhất đạt được, khắt khe hơn nữa — không phải trường hợp FTMO).

FTMO cụ thể (đã verify academy.ftmo.com lượt trước, giữ nguyên): `PreviousDayBalance − CurrentEquity
> 5%×InitialCapital` — đúng khuôn "balance đầu ngày làm mốc, equity SỐNG (liên tục, tức giá trị THẤP
NHẤT trong ngày là điểm nguy hiểm nhất) để so sánh". Tức là:

- **Mốc bắt đầu ngày** → cần `balance` (số dư thật đầu ngày, không lẫn lỗ nổi của ngày trước).
- **Giá trị kiểm tra vi phạm** → cần **equity thấp nhất trong ngày**, đúng bằng field `minEquity`
  đã có sẵn trong report (khớp hoàn toàn với cách `output_util/selection.py::ftmo_screen()` ĐANG
  làm đúng: `min_equity = point.get("minEquity", point.get("equity", point.get("balance")))`).

# 3. Chuẩn ngành cho Monte Carlo/bootstrap trên chiến lược trading

- Nên mô phỏng ở cấp **daily return** (không phải từng lệnh riêng lẻ) khi có vị thế có thể
  overlap/giữ qua nhiều ngày — daily-return giữ đúng hiệu ứng compounding. Đúng với thiết kế hiện
  tại của `montecarlo.py` (đã ở cấp ngày, không phải cấp lệnh).
- **Block bootstrap** (không phải resample từng ngày độc lập) là kỹ thuật chuẩn để giữ lại
  autocorrelation/volatility clustering (chuỗi ngày xấu thường đi liền nhau) — đúng với thiết kế
  hiện tại (`block_days=5`), đã bàn kỹ nguyên lý này ở phiên trước.
- Không có nguồn nào chỉ ra Monte Carlo loại này cần tách "return-chaining" và "breach-check" ra 2
  chuỗi riêng — đó là hệ quả suy ra từ mục 1+2 khi áp dụng cụ thể vào cấu trúc dữ liệu cTrader thật
  (dưới đây), không phải 1 thông lệ có tên riêng trong tài liệu ngành.

# Tổng hợp — vì sao KHÔNG THỂ chỉ đổi tên field (`equity`→`minEquity`) mà đủ

Ghép mục 1+2: bản thân ngành **không dùng 1 con số duy nhất mỗi ngày** cho 2 mục đích — NAV/return
(cần giá trị mark-to-market **tại 1 thời điểm chốt**, ví dụ cuối ngày) và breach-check (cần
**giá trị THẤP NHẤT bất kỳ lúc nào trong ngày**) là 2 khái niệm khác nhau về bản chất thống kê (1
cái là "điểm", 1 cái là "cực trị"). cTrader report chỉ cho 3 field/ngày: `balance` (điểm, nhưng bỏ
sót lỗ nổi), `minEquity`/`maxEquity` (cực trị, không phải điểm chốt ngày).

→ Nếu chỉ đổi 1 field ưu tiên (bản chất code hiện tại đang làm), BUỘC phải hy sinh 1 trong 2 mục
đích:
- Dùng `balance` (hiện tại): chuỗi return đúng để "chain" qua ngày (không bị lệch do dùng cực trị
  làm mốc carry-forward), nhưng breach-check trong `run()` (đang so `day_start − balance` đơn
  thuần từ CHÍNH chuỗi return đã resample) sẽ **bỏ sót mọi ca lỗ nổi giữa ngày mà cuối ngày hồi
  phục** — đúng lỗ hổng ban đầu user hỏi.
- Dùng `minEquity`: breach-check khớp đúng luật FTMO thật, nhưng dùng cực trị (đáy) làm giá trị
  "chốt" để nhân dồn sang ngày kế **làm lệch chuỗi return** — ngày sau sẽ tính % thay đổi so với
  ĐÁY của ngày trước thay vì vốn thực mang sang, phóng đại biến động một cách giả tạo qua nhiều
  ngày resample liên tiếp.

**Hướng đúng theo chuẩn ngành (mục 1+2 cộng lại), chưa code**: tách `_daily_returns()` thành 2
chuỗi song song cho mỗi ngày — (a) return dựa trên `balance` (carry-forward, đúng chuẩn NAV/GIPS
cho phần "điểm chốt"), và (b) tỷ lệ sụt giảm trong ngày `(balance_đầu_ngày − minEquity)/balance_đầu_ngày`
(đúng luật FTMO cho phần "cực trị"). `run()` khi resample 1 block ngày sẽ áp dụng (a) để tiến hóa
`balance` mô phỏng như hiện tại, NHƯNG check `daily_bad` bằng cách áp tỷ lệ sụt giảm lịch sử (b) của
đúng ngày đó lên `balance` đầu-ngày MÔ PHỎNG (không phải áp lại số $ tuyệt đối lịch sử) — giữ đúng
tính chất "tỷ lệ" khi resample sang bối cảnh vốn khác.

# Trạng thái

Chỉ dừng ở research — **chưa sửa code**. Cần chốt có triển khai hướng 2-chuỗi song song ở trên
không (phức tạp hơn 1 field-swap nhưng là cách duy nhất khớp cả chuẩn NAV lẫn đúng luật FTMO cùng
lúc), hay chấp nhận giữ `balance`-only (đơn giản, chấp nhận giới hạn đã nêu, coi Monte Carlo này chỉ
đo rủi ro trình tự trên VỐN ĐÃ CHỐT, không đo rủi ro lỗ nổi trong ngày).

# Nguồn

- [Prop Firm Daily Drawdown Rules: Never Breach Your Limits](https://newyorkcityservers.com/blog/prop-firm-daily-drawdown-rules)
- [Daily Loss Limit Explained for Prop Firm Traders (2026 Guide)](https://www.futureshive.com/blog/daily-loss-limit-prop-firm-guide-2026)
- [The Complete Guide to Prop Firm Daily Loss Limits (2026) | Vigil](https://runvigil.app/blog/prop-firm-daily-loss-limit-guide)
- [Prop Firm Drawdown Rules Explained: Daily vs Max (2026 Guide) | The5ers](https://the5ers.com/prop-firm-drawdown-rules-explained-daily-max-and-trailing-limits-in-2026/)
- [Prop Firm Drawdown Rules Explained: Daily vs Max - ThinkCapital](https://www.thinkcapital.com/prop-firm-drawdown-rules/)
- [A Better Way To Run Bootstrap Return Tests: Block Resampling | Seeking Alpha](https://seekingalpha.com/article/3966418-a-better-way-to-run-bootstrap-return-tests-block-resampling)
- [Monte Carlo Simulations in Trading: A Practical Guide to Strategy Validation | QuantProof](https://quantproof.io/blog/monte-carlo-simulations-trading-strategy-validation)
- [GIPS Standards Handbook for Firms](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/)
- [GLOBAL INVESTMENT PERFORMANCE STANDARDS (GIPS®) FOR FIRMS 2020 (PDF)](https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf)
- academy.ftmo.com (đã verify lượt trước — Daily Loss = PreviousDayBalance − CurrentEquity > 5%×InitialCapital; Max Loss = InitialBalance − CurrentEquity > 10%×InitialCapital)
- Đối chiếu thực nghiệm: 6 `report.json.gz` thật trong `runs/research/{grid,walkforward}/**` — xác nhận field thật là `balance`/`minEquity`/`maxEquity`/`timestamp`, không có `equity`.
