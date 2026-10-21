# Chủ đề — 5 chiến lược ứng viên cho `we_core` (điểm vào lệnh BUY/SELL và điều kiện vào lệnh rõ ràng)

Ngày: 2026-10-20. Người dùng: "research và lưu 5 chiến lược giao dịch, mỗi chiến lược phải xác định và mô tả được rõ điểm vào lệnh buy/sell và điều kiện vào lệnh (như các chiến lược tôi đang dùng). Chưa code gì cả."

Nhãn: `nguồn-báo-cáo` (nguồn nói, chưa kiểm) · `lỗi-nguồn` · `suy-luận` (phân tích của tôi, chưa đo). **Không có số đo nào trên dữ liệu của hệ trong ghi chú này** (không SQL, không Redis): mọi con số hiệu quả là của nguồn. Sách gốc (Curtis Faith/Turtle, Connors–Alvarez 2008, Connors–Raschke 1995, Crabel 1990) CHƯA đọc được; luật lấy từ trang thứ cấp và mã cài đặt, nên chỗ các nguồn khác nhau được ghi rõ là mâu thuẫn.

## 0. Tiêu chí chọn và mốc so sánh với 3 chiến lược hiện có

Tiêu chí (theo yêu cầu): có điều kiện vào lệnh khách quan tính được từ OHLC (không cần tin tức, không cần tick), có điểm vào BUY/SELL xác định, chạy được trên khung M10–H4 của các symbol hệ này. `we_core` chỉ phát tín hiệu và giá vào (`entry_price`), không tính SL/TP/thoát — nên mỗi mục dưới đây tách "VÀO LỆNH" (cần cho hệ) khỏi "THOÁT/STOP của nguồn" (chỉ để tham khảo, thuộc hệ đặt lệnh).

| | họ | hướng so với xu hướng | giá vào lệnh | cần theo dõi lệnh đang mở? |
|---|---|---|---|---|
| combo (hiện có) | cắt MA20 + MACD | thuận động lượng | lệnh chờ: high+X / low−X | không |
| ma_cross (hiện có) | trạng thái SMA13/34 + MACD | thuận xu hướng | close | không |
| macd_break (hiện có) | phá đỉnh/đáy close 100 nến + MACD | thuận xu hướng | close | không |
| **1. Donchian / Turtle** | phá kênh N nến | thuận xu hướng | lệnh chờ ở biên kênh | S1 có "skip rule" cần |
| **2. Connors RSI(2)** | hồi quy ngắn hạn trong xu hướng | NGƯỢC nhịp, thuận xu hướng lớn | close (mua khi đóng cửa) | không |
| **3. Holy Grail (Raschke)** | hồi về EMA20 trong xu hướng mạnh | thuận xu hướng | lệnh chờ ở biên nến chạm EMA | không |
| **4. Phá khoảng giá phiên Á (London breakout)** | phá khoảng giá theo giờ | trung tính | close nến phá (hoặc lệnh chờ ở biên khoảng) | không |
| **5. NR7 (nén biên độ)** | phá sau nến hẹp nhất 7 nến | trung tính (hai chiều) | lệnh chờ hai phía của nến NR7 | không |

## 1. Donchian / Turtle breakout (System 1: 20→10, System 2: 55→20)

**Nguồn:** S071 (bài cài đặt MQL5 "The Original Turtle Trading Rules", có mã), S072 (tổng hợp cộng đồng), cùng nguồn gốc với S038 đã nạp. `nguồn-báo-cáo`.

**Điều kiện:** không có bộ lọc; chỉ cần đủ N nến lịch sử. N = 20 (System 1) hoặc 55 (System 2). N_ATR = ATR 20 theo Wilder (`N_t = (N_{t−1}×19 + TR_t)/20`).

**Điểm vào BUY:** `high của nến hiện tại > max(high của N nến TRƯỚC, không tính nến hiện tại)`. Là lệnh chờ đặt tại `max(high N nến trước) + 1 tick` (mã nguồn so `high`, không so `close`). **Điểm vào SELL:** đối xứng, `low < min(low của N nến trước)`; lệnh chờ tại `min(low N nến trước) − 1 tick`.

**Skip rule (chỉ System 1, theo từng hướng):** bỏ tín hiệu nếu lệnh System 1 liền trước CÙNG hướng là lệnh có lãi; System 2 không có luật này (và một nguồn nói: sau khi bỏ, System 1 chỉ bật lại khi có nến không phá mới hoặc có tín hiệu System 2). Mâu thuẫn trong nguồn: bài MQL5 định nghĩa "lãi" bằng văn bản là "giá đi thuận trước khi dừng/thoát" nhưng mã dùng tổng P&L của lệnh đã đóng > 0 — hai phép thử khác nhau (`lỗi-nguồn`, S071).

**Thoát/stop của nguồn (tham khảo, hệ không làm):** thoát khi giá ĐÓNG cửa vượt biên kênh ngắn: System 1 = đáy 10 nến (long) / đỉnh 10 nến (short), System 2 = 20 nến. Stop 2N. Thêm vị thế mỗi N/2 (bài MQL5 ghi cả "N" ở phần lý thuyết và "N/2" ở phần luật; mã dùng N/2; `lỗi-nguồn`), tối đa 4 đơn vị; một diễn đàn ghi thoát System 2 short là 10 nến (mâu thuẫn với 20 nến của ba nguồn còn lại).

**Khớp với hệ (`suy-luận`):** gần nhất với `macd_break` (đỉnh/đáy 100 nến + MACD, vào ở close, so `close >= prior_high_close`) — khác ở: so `high/low` thay `close`, N = 20/55 thay 100, không MACD, entry là lệnh chờ ở biên kênh thay vì close. Có thể coi là một biến thể có tham số N; nếu thêm thì giá trị mới là bộ lọc "skip rule" và entry ở biên kênh, nhưng skip rule cần biết lệnh trước đó thắng hay thua — hệ không theo dõi lệnh, nên chỉ làm được phiên bản System 2 (không skip) hoặc xấp xỉ skip bằng lọc lặp cùng chiều.

## 2. Connors RSI(2): hồi quy ngắn hạn trong xu hướng lớn

**Nguồn:** S073 (LuxAlgo, trang khái niệm RSI-2, mô tả luật), S074 (Connors & Alvarez 2008, *Short Term Trading Strategies That Work* — CHƯA đọc, chỉ được S073 dẫn), S075 (hai trang backtest của Backtrex trên S&P 500 / Nasdaq 100, chỉ có bản tóm tắt tìm kiếm). `nguồn-báo-cáo`.

**Chỉ báo:** RSI chu kỳ 2 theo Wilder (`AvgGain/AvgLoss` làm mượt với trọng số (n−1)/n, hạt giống bằng trung bình đơn giản; AvgLoss = 0 thì RSI = 100). Bộ lọc xu hướng: SMA 200.

**Điểm vào BUY (vào ở nến đóng cửa thoả điều kiện, entry = close):** `close > SMA200` **và** `RSI(2) < 10` (bản chặt hơn `< 5`). **Điểm vào SELL (đối xứng, S073):** `close < SMA200` **và** `RSI(2) > 90` (chặt hơn `> 95`).

**Biến thể có nguồn:** (a) cần hai nến đóng liên tiếp vượt ngưỡng, hoặc tổng RSI(2) của ≥ 2 nến < 35 (bản "hay được trích"); (b) "R3": RSI(2) giảm ba nến liên tiếp, nến đầu giảm từ mức < 60, vào khi RSI(2) < 10, thoát khi RSI(2) > 70.

**Thoát của nguồn (tham khảo):** long thoát khi `close > SMA5` (hoặc RSI hồi); thoát bên SELL không được nêu rõ trong S073 (đối xứng là `suy-luận`). Connors không dùng stop-loss ("stop làm hại kết quả" theo trang thứ cấp, chưa kiểm sách).

**Mâu thuẫn/giới hạn:** ngưỡng 10 hay 5 (nguồn nói Connors thử 0–10 và thấy `< 5` lãi hơn `< 10`); nguồn gốc là cổ phiếu/chỉ số ngày (SMA200 ngày), không phải khung M10–H4: SMA200 của H1 chỉ ≈ 8 ngày giao dịch nên ý nghĩa "xu hướng lớn" khác (`suy-luận`). Backtrex (một tác giả) trên Nasdaq 100 2016–2026: +26,9% tổng 10 năm (≈ 2,4%/năm) so với 20,2%/năm của chỉ số, S&P 500 cũng yếu — bằng chứng yếu cho "còn hiệu lực" (`nguồn-báo-cáo`, chưa kiểm).

**Khớp với hệ (`suy-luận`):** khác ba chiến lược hiện có về TÍNH CHẤT (mua nhịp giảm trong xu hướng tăng, ngược nhịp) nên đa dạng hoá tốt nhất về mặt ý tưởng; entry = close giống `ma_cross`/`macd_break` (không cần lệnh chờ). RSI(2) dưới 10 có thể kéo dài nhiều nến liền nhau → cần chống trùng (chỉ nến đầu) giống các chiến lược hiện có. Cần thêm hàm RSI Wilder vào `indicator.py` (hệ có `rma` sẵn, dùng được).

## 3. Holy Grail (Raschke / Connors, *Street Smarts*): hồi về EMA20 trong xu hướng mạnh

**Nguồn:** S076 (script TradingView "Holy Grail setup" của Konscio, đọc mô tả), S077 (Capital.com và tradingsetupsreview — chỉ bản tóm tắt tìm kiếm); sách S078 (Connors & Raschke 1995, *Street Smarts*) CHƯA đọc. `nguồn-báo-cáo`, độ tin cậy THẤP nhất trong 5 (không nguồn nào chép nguyên văn sách; không có mã chuẩn).

**Điều kiện (theo các nguồn thứ cấp):** ADX(14) > 30 (một nguồn: "và đang tăng"); xu hướng: EMA20 trên EMA50 (BUY) / dưới (SELL) theo một script — nguồn khác không nêu cách xác định hướng (không thấy điều kiện +DI/−DI).

**Điểm vào BUY:** trong xu hướng tăng mạnh, nến có `low` hồi về chạm EMA20 (nến "setup") mà giá chưa thủng EMA50; vào khi giá phá `high` của nến setup: lệnh chờ tại `high nến setup + 1 tick` đặt cho nến kế. **Điểm vào SELL:** đối xứng — xu hướng giảm mạnh, nến có `high` chạm EMA20 từ dưới, vào khi giá phá `low` của nến setup (lệnh chờ tại `low nến setup − 1 tick`).

**Mâu thuẫn/chưa rõ:** SMA20 (một bản viết tay) hay EMA20 (Capital.com, script); "chạm" là bóng nến, đóng cửa vượt, hay chỉ tiến sát EMA — không nguồn nào nói; thời hạn hiệu lực của lệnh chờ không nêu; thoát/stop không có luật nguồn (script chỉ gợi ý fractal làm vị trí stop). Chưa xác nhận ADX period và ngưỡng ở nguồn nào ngoài "14 và 30" qua tóm tắt.

**Khớp với hệ (`suy-luận`):** về cấu trúc giống `combo` (nến liên quan tới một MA20 + lệnh chờ ở mép nến), khác ở bộ lọc ADX > 30 và hướng hồi về MA thay vì cắt qua MA. **Cảnh báo từ dữ liệu hệ:** T001 (F057) thấy ADX cao làm `breakout_atr` kém hơn (5/6 phép đo) — đó là chiến lược phá đỉnh chứ không phải hồi về MA, không suy trực tiếp sang đây, nhưng cho thấy bộ lọc ADX chưa được chứng minh trên dữ liệu hệ.

## 4. Phá khoảng giá phiên Á (London breakout)

**Nguồn:** S079 (Backtrex, backtest EUR/USD H1 2016-10 → 2026-10, đọc đủ luật và kết quả), S080 (diễn đàn BabyPips, TradingView, MQL5 blog — chỉ tóm tắt tìm kiếm; nhiều trang là sản phẩm EA thương mại, không kiểm chứng độc lập). `nguồn-báo-cáo`.

**Điều kiện:** khoảng giá phiên Á = `high` và `low` của 00:00–06:00 UTC (nến H1); khung hiệu lực của tín hiệu = 07:00–10:00 UTC ("London kill zone").

**Điểm vào BUY (bản Backtrex):** nến ĐÓNG cửa vượt lên trên `high` phiên Á, trong khung 07:00–10:00 UTC; entry = close nến đó. **Điểm vào SELL:** nến đóng cửa xuống dưới `low` phiên Á, cùng khung giờ. Biến thể: lệnh chờ ngay biên khoảng (chạm là vào); chỉ lấy lần phá ĐẦU TIÊN trong ngày; bắt buộc độ rộng khoảng nằm trong [min, max]; thêm bộ lọc xu hướng khung lớn — bản Backtrex không dùng bộ lọc nào và nhận mọi lần phá.

**Kết quả của nguồn (một lần chạy, EUR/USD):** 1.230 lệnh, thắng 36,26% (hoà vốn cần ≈ 39% với tỉ lệ thắng/thua 1,5:1), PF 0,87, tổng −28% so với mua giữ +0,1%, drawdown tối đa −31,4%, chỉ 3/10 năm dương (2018, 2021, 2023), thua dài nhất 15 lệnh; stop 35 pip, take-profit 70 pip, phí 0,02%/chiều. Tác giả kết luận hướng phá một mình mang rất ít thông tin. Tác giả một script TradingView cũng không khuyên dùng mặc định (100+ lệnh, "không thành công lắm").

**Điểm cần cẩn thận:** giờ London lệch theo giờ mùa hè/đông so với UTC (07:00 UTC mùa đông ≈ 06:00 UTC mùa hè), giờ máy chủ broker cũng đổi; `bartime` của hệ là UTC-naive nên phải tự xử lý DST nếu muốn đúng giờ địa phương; mỗi symbol có phiên riêng (chỉ số cổ phiếu mở cửa 13:30/14:30 UTC, vàng và BTC gần như 24 giờ) nên "khoảng phiên Á" cần định nghĩa lại cho từng nhóm (`suy-luận`).

**Khớp với hệ (`suy-luận`):** `combo` đã có `session_hours_utc` (lọc giờ); chiến lược này cần thêm: khoảng giá theo giờ trong từng ngày (trạng thái trong ngày: phải có đủ nến từ 00:00 UTC của ngày đó; 6 giờ phiên Á = 6 nến H1 hoặc 36 nến M10, đều nằm trong 300 nến warm-up của cửa sổ làm mới của worker), và chống trùng "một lần/ngày". Bằng chứng duy nhất độc lập tìm được là ÂM trên EUR/USD.

## 5. NR7: phá sau nến có biên độ hẹp nhất 7 nến (Crabel; Connors–Raschke)

**Nguồn:** S081 (Oxfordstrat, trang NR7 + Crabel Stretch: định nghĩa, kết quả, 42 thị trường futures 1980–2016, đã đọc), S082 (TD Ameritrade tickertape và thepatternsite — chỉ tóm tắt tìm kiếm), S083 (Crabel 1990, *Day Trading with Short Term Price Patterns and Opening Range Breakout* — chưa đọc), S078 (Street Smarts, chưa đọc). `nguồn-báo-cáo`.

**Điều kiện (setup):** biên độ nến hiện tại (`high − low`) nhỏ hơn biên độ của MỖI nến trong 6 nến trước (so riêng từng nến); độ dài NR 6 là mặc định, thử 1–20 (≥ 6 tốt hơn theo S081).

**Điểm vào (hai chiều, đặt cho nến KẾ TIẾP):** BUY = lệnh chờ mua `1 tick trên high của nến NR7`; SELL = lệnh chờ bán `1 tick dưới low của nến NR7`. Lệnh nào khớp trước là vị thế, lệnh còn lại làm stop (mô tả TD Ameritrade — thứ cấp). **Biến thể Crabel (opening range breakout):** vào lệnh theo giá MỞ cửa của ngày: BUY tại `Open + Stretch`, SELL tại `Open − Stretch` với `Stretch = 2 × SMA10(min(High−Open, Open−Low))`; stop của biến thể: `Entry ∓ 6×ATR(20)` hoặc `Open ∓ Stretch`, thoát cuối ngày thứ N. Biến thể Patternz: chờ ĐÓNG cửa ngoài biên nến NR7, vào ở giá mở nến sau.

**Kết quả của nguồn:** sau chi phí ($50 khứ hồi) "chưa giao dịch được nếu không có luật bổ sung", xếp hạng C; không có số trong văn bản (chỉ biểu đồ). Trang không nêu thời hạn hiệu lực của lệnh chờ chưa khớp.

**Khớp với hệ (`suy-luận`):** đây là ứng viên DUY NHẤT mà setup không có hướng: một nến NR7 sinh CẢ BUY lẫn SELL cho cùng nến, trong khi hệ phát MỘT `signal` (±1) cho mỗi nến (schema CSV/Redis một dòng một nến, `signal` là mã cTrader BUY=1/SELL=2). Muốn dùng phải quyết: (a) bản "chờ đóng cửa ngoài biên rồi vào" (một hướng, như Patternz) thay vì hai lệnh chờ; (b) lệnh chờ hai chiều (OCO), nghĩa là đổi hợp đồng đầu ra với BO20; hoặc (c) lọc hướng bằng xu hướng khung lớn để chỉ phát một chiều. Nên chọn (a) hoặc (c) nếu không muốn đổi schema.

## 6. Ý tưởng khác tìm thấy (CHƯA nghiên cứu sâu, ngoài 5 mục trên)

- **Supertrend (ATR 10, hệ số 3):** dải = `(H+L)/2 ± 3×ATR(10)`, dải "cuối" chỉ dịch theo hướng xu hướng; BUY khi `close` vượt dải trên (đường chuyển xuống dải dưới), SELL khi `close` xuống dưới dải dưới (S084, tóm tắt tìm kiếm; các nguồn khác nhau về cách làm mượt ATR — Wilder 14 hay chu kỳ 10 — nên phải đối chiếu mã nền tảng). Họ gần `ma_cross` (trạng thái xu hướng), entry = close.
- **Turtle Soup (Raschke):** ngược của breakout kênh 20 nến — vào ngược hướng khi phá kênh thất bại (chỉ thấy tên ở một diễn đàn).
- **Squeeze (Bollinger trong Keltner) breakout, Ichimoku, Bollinger %b hồi quy:** chưa tìm nguồn luật.
- Nhóm stat-arb (cặp, hồi quy trung bình chéo, động lượng chéo) đã có ở S001–S003: cần vị thế nhiều chân, không hợp một chân của OF; chất lượng bằng chứng thấp (F004–F019).

## 7. Những điều chưa biết và cần người dùng quyết nếu muốn đi tiếp

1. **Chưa có bằng chứng lãi/lỗi cho mục nào trên dữ liệu hệ** và hệ chưa có spread/phí theo symbol (T001 đã ghi). Bằng chứng ngoài: Donchian/Turtle (cổ điển, có mã) chưa kiểm số; RSI(2) yếu ở 2016–2026; London breakout ÂM trên EUR/USD; NR7 "chưa giao dịch được" sau phí; Holy Grail không có số.
2. **Hợp đồng `signal` một chiều mỗi nến:** 1, 2, 3, 4 phù hợp; 5 (NR7) cần bản một chiều.
3. **Cần trạng thái ngoài chỉ báo:** 1 (skip rule), 4 (khoảng giá trong ngày + một lần/ngày). 2, 3, 5 chỉ cần chỉ báo và so sánh theo nến.
4. **Khung thời gian:** nguồn gốc của 1, 2, 5 là KHUNG NGÀY; khung M10–H4 là ngoại suy chưa kiểm (đặc biệt SMA200 và RSI(2) của Connors).
5. **Tham số chọn trước khi đo** (tránh overfit, F083/F041): N Donchian (20 hay 55), ngưỡng RSI(2) (10 hay 5), khung giờ phiên theo symbol, độ dài NR (6).

Gợi ý thứ tự nếu người dùng muốn thử (`suy-luận`, chưa đo): (1) RSI(2) vì khác tính chất nhất và entry là close, (2) Donchian N=55 vì rất gần `macd_break` và không cần trạng thái lệnh, (3) Holy Grail nếu chấp nhận bằng chứng yếu; (4) London breakout và (5) NR7 sau cùng vì bằng chứng hiện có âm hoặc cần đổi hợp đồng đầu ra.

## 8. Nguồn (URL và độ đọc)

- S071: https://www.mql5.com/en/articles/23448 — đã đọc (bài + mã Turtle).
- S072: https://pro.stockalarm.io/blog/turtle-trading-system-explained và các script TradingView (jbyeMkat, ZSJlXc15), diễn đàn metalsmine/energyexch — chỉ tóm tắt tìm kiếm.
- S073: https://www.luxalgo.com/library/concept/rsi-2/ — đã đọc.
- S074: Connors & Alvarez (2008) *Short Term Trading Strategies That Work* — chưa đọc (qua S073).
- S075: https://backtrex.com/en/backtests/connors-rsi-2-nasdaq-100 và `…-sp-500` — chỉ tóm tắt tìm kiếm.
- S076: https://my.tradingview.com/script/1HQtlUIa — đã đọc phần mô tả (không có mã).
- S077: https://capital.com/en-ae/learn/trading-strategies/holy-grail-trading-strategy và https://tradingsetupsreview.com/the-holy-grail-trading-setup — chỉ tóm tắt tìm kiếm.
- S078: Connors & Raschke (1995) *Street Smarts* — chưa đọc.
- S079: https://backtrex.com/en/backtests/london-breakout-eur-usd — đã đọc.
- S080: forums.babypips.com (London Breakout 1H), TradingView londonbreakout, mql5.com/en/blogs/post/774527 — chỉ tóm tắt tìm kiếm.
- S081: https://oxfordstrat.com/?p=3707 — đã đọc.
- S082: https://tickertape.tdameritrade.com/trading/trading-nr7-setup-15828 và https://www.thepatternsite.com/nr7.html — chỉ tóm tắt tìm kiếm.
- S083: Crabel (1990) *Day Trading with Short Term Price Patterns and Opening Range Breakout* — chưa đọc.
- S084: https://tapeboard.com/glossary/what-is-the-supertrend-indicator, https://mudrex.com/learn/supertrend-indicator/, TradingView "Supertrend with Bands" — chỉ tóm tắt tìm kiếm.
