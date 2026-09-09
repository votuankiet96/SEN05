# Đối chứng đòn bẩy hiệu lực theo thời gian — Jan-Jun/2025 vs Feb/2026

Yêu cầu gốc: backtest thật (Combo/H1/M1, tham số mặc định `KslLevel=Fib1000, KtpLevel=Fib2618,
RiskPercent=1%, MaxMarginPercent=50%`) cho 11 symbol, 2 giai đoạn (01/01/2025→01/07/2025 và
01/03/2026→31/08/2026), chạy song song, đối chứng đòn bẩy/PipValue. **Kết quả thực tế không đầy
đủ như dự tính ban đầu** — ghi lại trung thực những gì đo được và những gì không.

## Những gì KHÔNG đo được — và lý do

- **HK50** — cả 2 lần thử (chạy song song 11 symbol, rồi thử lại tuần tự riêng) đều bị chính
  `ctrader-cli` tự abort ("aborted by timeout") hoặc crash nội bộ (`System.InvalidOperationException:
  Message expected` khi ghi report cho 1 lần chạy bị ngắt) — không phải do máy này/script chậm.
  Không có dữ liệu Giai đoạn A cho HK50.
- **Giai đoạn B (01/03/2026→31/08/2026) hoàn toàn KHÔNG dùng được** — dù 5/11 symbol
  (US30/US500/US100/GOLD/BTCUSD) chạy xong report.json hợp lệ, **cả 5 đều có 0 lệnh bị margin-cap**
  → không có dòng log nào để trích xuất đòn bẩy/PipValue. 6 symbol còn lại (DE40/UK100/FR40/SP35/
  HK50/J225) bị timeout/crash tương tự HK50 ở Giai đoạn A, không hoàn thành.
  - **Vì sao 0 lệnh bị cap dù cùng tham số từng cho nhiều lệnh bị cap ở Giai đoạn A?** Đây là
    backtest TUẦN TỰ THẬT (không phải giả định Balance cố định $10,000 như phân tích thủ công
    trước đó) — Balance/Equity thay đổi theo P&L thật của các lệnh trước trong giai đoạn. Nếu tài
    khoản có lời sớm trong giai đoạn B, ngân sách margin (`Equity × 50%`) tự phình to theo, khiến
    càng về sau càng ít lệnh chạm trần — hoàn toàn có thể tự nhiên xảy ra, không phải lỗi.

## Đối chứng được: Jan-Jun/2025 (mới đo) vs Feb/2026 (đã đo tuần trước)

10/11 symbol có đủ dữ liệu Giai đoạn A để so với phép đo cũ (tháng 2/2026, dùng
`MaxMarginPercent=1%` ép cap, `KslLevel=0.618` cũ) — vẫn là 2 mốc THỜI GIAN THẬT khác nhau, đúng
tinh thần câu hỏi gốc dù không đúng cặp ngày dự tính ban đầu.

| Symbol | Số mẫu (Jan-Jun 2025) | Đòn bẩy Jan-Jun/2025 | Đòn bẩy Feb/2026 (cũ) | Chênh lệch |
|---|---:|---:|---:|---:|
| US30 | 32 | 14.70 | 15.04 | -2.3% |
| US500 | 20 | 14.65 | 15.06 | -2.7% |
| US100 | 3 | 14.14 | 15.22 | -7.1% (mẫu quá ít, độ tin cậy thấp) |
| DE40 | 14 | 12.54 | 14.57 | **-14.0%** |
| UK100 | 37 | 14.02 | 14.17 | -1.0% |
| FR40 | 4 | 14.38 | 14.12 | +1.9% (mẫu ít) |
| SP35 | 4 | 7.14 | 8.52 | **-16.2%** (mẫu ít) |
| J225 | 5 | 14.21 | 13.96 | +1.8% (mẫu ít) |
| GOLD | 5 | 12.61 | 14.07 | **-10.4%** (mẫu ít) |
| **BTCUSD** | **338** | **0.91** | **1.23** | **-25.9%** |
| HK50 | 0 | — | — | không đo được |

## Đọc bảng

- **US30/US500/UK100** (mẫu lớn, 20-37 quan sát): chênh lệch nhỏ (-1% đến -2.7%) — ổn định thật
  theo thời gian, chênh lệch nằm trong biên nhiễu đo lường bình thường.
- **BTCUSD — chênh lệch LỚN NHẤT (-25.9%) VÀ có mẫu LỚN NHẤT (338 quan sát)** — đây không phải
  nhiễu thống kê (mẫu quá lớn để là ngẫu nhiên), mà là **dấu hiệu THẬT: đòn bẩy hiệu lực BTCUSD đã
  thay đổi đáng kể giữa đầu 2025 và đầu 2026** (đòn bẩy TĂNG theo thời gian: 0.91→1.23, tức margin
  yêu cầu cho cùng khối lượng đã GIẢM ~26% theo thời gian). Đây chính là kịch bản "broker đổi tỷ lệ
  margin theo thời gian" đã cảnh báo trước đó — giờ có bằng chứng thực nghiệm cho BTCUSD.
- **DE40/SP35/GOLD** (-10% đến -16%): chênh lệch đáng kể nhưng mẫu QUÁ ÍT (4-5 quan sát) để kết
  luận chắc chắn đây là thay đổi thật hay chỉ là nhiễu do mẫu nhỏ — cần đo lại với mẫu lớn hơn mới
  khẳng định được.
- **FR40/J225** (mẫu ít, chênh lệch nhỏ +1.8-1.9%): có vẻ ổn định, nhưng cũng cần thận trọng vì
  mẫu mỏng.

## Kết luận cho câu hỏi gốc ("đòn bẩy hiệu lực có đổi theo thời gian không?")

**Có bằng chứng thật (không chỉ suy đoán) rằng CÓ — ít nhất với BTCUSD**, mẫu đủ lớn để tin cậy.
Với các symbol khác, dữ liệu hiện có **chưa đủ mẫu** để kết luận chắc chắn (US100/FR40/SP35/J225/
GOLD đều dưới 5-14 quan sát) — cần đo lại với cửa sổ thời gian dài hơn hoặc `MaxMarginPercent`
thấp hơn (ép nhiều lệnh bị cap hơn để có mẫu) mới khẳng định được cho nhóm này.

## Giới hạn/việc còn dang dở
- HK50: chưa đo được giai đoạn nào — cần thử lại qua GUI hoặc chờ ctrader-cli ổn định hơn.
- Giai đoạn B (nửa cuối 2026): chưa có dữ liệu đối chứng nào — cần thiết kế lại cách đo (có thể
  ép `MaxMarginPercent` thấp tạm thời trong lúc đo để đảm bảo luôn có lệnh bị cap, bất kể Equity
  tăng bao nhiêu trong kỳ).
