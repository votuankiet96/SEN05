# Chủ đề — Xây dựng "sóng Dow" (swing high/low) để đặt stop-loss

Ngày: 2026-10-11. Câu hỏi của người dùng: nghiên cứu cách xây dựng chỉ báo sóng Dow để đặt SL tối ưu theo đỉnh/đáy Dow gần nhất; cách cũ chỉ dựa trên tính toán sơ bộ, cần tài liệu rõ ràng, làm cẩn trọng. Yêu cầu bổ sung: **chưa code**, tìm kiếm "vừa đủ".

Cách làm: 4 nhánh tác tử phụ chạy song song (định nghĩa Dow cổ điển · thuật toán phát hiện cực trị · cách triển khai thực tế · bằng chứng về stop-loss), mỗi nhánh được yêu cầu trích dẫn, phân loại V/R/U và tìm bằng chứng phản bác; tôi dừng chúng sớm theo yêu cầu "vừa đủ". Sau đó **tôi tự đối chiếu lại với văn bản gốc** các nguồn mà đề xuất dựa vào nhiều nhất (đánh dấu ✔ ở bảng nguồn). Mục chưa có ✔ là do tác tử đọc, tôi chưa kiểm lại. Một phép đo trên dữ liệu thật của hệ (T002) đo đặc tính của cách cũ.

Nhãn: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở INDEX.md). Thêm ✔ = tôi đã đối chiếu văn bản gốc. Khi nguồn tự mâu thuẫn hoặc các tác giả bất đồng, ghi cả hai bên.

## 1. Nguồn đã đọc

| ID | Nguồn | Loại | Ai đọc | Ghi chú |
|---|---|---|---|---|
| S014 | Nelson, S. A. (1902/03). *The ABC of Stock Speculation* (lời của Dow). archive.org `abcofstockspecul00nelsiala` | sách gốc | tác tử | định nghĩa xu hướng bằng đỉnh/đáy; Chương "stop orders" |
| S015 | Hamilton, W. P. (1922). *The Stock Market Barometer*. archive.org `stockmarketbarom00hami` | sách gốc (OCR) | tác tử | |
| S016 | Schannep, J. (2008). *Dow Theory for the 21st Century*, excerpt Part I (văn bản Rhea 1932 in lại). catalogimages.wiley.com/images/db/pdf/9780470240595.excerpt.pdf | excerpt sách | tác tử | excerpt đọc được; việc gán nội dung cho Rhea là thứ cấp (chưa đọc sách Rhea) |
| S017 | Schabacker, R. W. (1930). *Stock Market Theory and Practice*, Ch. 22 (tr. 536–539). archive.org `bwb_Y0-EGF-847` | sách gốc | tác tử | stop và "săn stop" |
| S018 | Brown, Goetzmann, Kumar (1998). J. Finance 53(4):1311–1333; đọc working paper NYU FIN-98-013 (archive.nyu.edu/handle/2451/26858) | bài học thuật | tác tử | chưa so với bản tạp chí |
| S019 | Lo, Mamaysky, Wang (2000). J. Finance 55(4):1705–1765. nber.org/papers/w7613 | bài học thuật | tác tử | kernel regression, lag chống lookahead |
| S020 | Glattfelder, Dupuis, Olsen (2011). Quant. Finance 11(4):599–614. arXiv:0809.1040 | bài học thuật | tác tử + **tôi ✔** (Algorithm 1–2, câu overshoot) | PDF sha256 `6a1d110b04dd0bd3…` |
| S021 | Adegboye, Kampouridis, Otero. Artif. Intell. Rev., doi 10.1007/s10462-022-10307-0; Rayment, Kampouridis, Adegboye (slide, kampouridis.net) | bài học thuật | tác tử | xác nhận DC là hồi cứu; chọn θ |
| S022 | Hanna (2018). Int. Rev. Financial Analysis 55:93–110, doi 10.1016/j.irfa.2017.11.001 | bài học thuật | tác tử | bộ lọc bull/bear Lunde–Timmermann; nguyên tắc P4/P5 |
| S023 | Kole, van Dijk. EUR working paper repub.eur.nl/pub/41558 (bản tạp chí J. Appl. Econometrics 2017: chưa kiểm) | working paper | tác tử | độ nhạy theo censoring |
| S024 | Zhang, Hadjiliadis. arXiv:0911.1575 | bài học thuật | tác tử | drawdown/drawup = CUSUM |
| S025 | TradingView Help Center, "ZigZag". tradingview.com/support/solutions/43000591664/ | tài liệu chính thức | tác tử + **tôi ✔** | |
| S026 | TradingView Pine docs: repainting; operators; strategies (đọc một phần ≈100.000/261.325 ký tự) | tài liệu chính thức | tác tử | |
| S027 | LuxAlgo/PineTS PR #322 (cài đặt `ta.pivothigh/low`) | mã mở | tác tử | câu "verified against TradingView" là lời báo cáo |
| S028 | joshyattridge/smart-money-concepts `smc.py` + issue #101 / PR #95, #103 | mã mở | tác tử | số PF trong issue do một người dùng tự đo |
| S029 | jbn/ZigZag `zigzag/core.pyx` (PyPI `zigzag`) | mã mở | tác tử | |
| S030 | MetaQuotes ZigZag (bản copy pastebin D4nRVWLY, chưa đối chiếu byte với bản gốc) + tài liệu MT5 Fractals | mã/tài liệu | tác tử | |
| S031 | cTrader Algo: how-to Fractals; tham chiếu `Fractals` | tài liệu chính thức | tác tử | thuật toán built-in không có tài liệu |
| S032 | scipy docs: `find_peaks`, `peak_prominences`, `argrelextrema` | tài liệu chính thức | tác tử | |
| S033 | Kaminski, Lo (2014). J. Financial Markets 18:234–254, doi 10.1016/J.FINMAR.2013.07.001 | bài học thuật | tác tử (chỉ abstract; PDF bị chặn) | số 50–100 bp qua CXO (thứ cấp) |
| S034 | Han, Zhou, Zhu (2016). "Taming momentum crashes". SSRN 2407199 | working paper | tác tử | chưa xác minh đã đăng tạp chí nào |
| S035 | Clare, Seaton, Smith, Thomas (working paper; J. Asset Management 2013). openaccess.city.ac.uk/17842 | bài học thuật | tác tử + **tôi ✔** (Table 4B, 2 câu kết luận) | PDF sha256 `b6e1ea4ab1c489d3…` |
| S036 | Lei, Li (2009). Financial Services Review 18(1):23–51 | bài học thuật | tác tử (abstract) | |
| S037 | Osler (2005) "Stop-loss orders and price cascades in currency markets", NY Fed Staff Report 150 (JIMF 24(2)); Osler (2003) J. Finance, NY Fed SR 125 | working paper | tác tử + **tôi ✔** (SR 150) | PDF sha256 `0132a12fe1116a46…` |
| S038 | "The Original Turtle Trading Rules" (OriginalTurtles.org 2003; bản PDF oxfordstrat.com) | tài liệu practitioner | tác tử | chưa xác minh byline Curtis Faith |
| S039 | StockCharts ChartSchool "Chandelier Exit"; Elder SafeZone qua LuxAlgo (thứ cấp) | tài liệu practitioner | tác tử | sách Elder chưa đọc |
| S040 | GitHub `ddkklau/CTA_TrendFollowing_StopLoss_Research` (README) | repo cá nhân | tác tử | chất lượng thấp, một tác giả, chưa bình duyệt |
| S041 | Carr, López de Prado. arXiv:1408.1159 (abstract) | bài học thuật | tác tử | cảnh báo overfit khi hiệu chỉnh quy tắc trên backtest |
| T002 | Đo trên dữ liệu thật của hệ: 489 tín hiệu `ma_cross` bằng cách Dow cũ (mục 4) | phép đo | tôi | |

Chưa đọc (chỉ qua tóm tắt thứ cấp hoặc không truy cập được): Rhea (1932) sách gốc; Edwards & Magee (*Technical Analysis of Stock Trends*, Ch. 27–28); Murphy; Pring; Kaufman; Wilder; Elder (sách); Kase; Tharp; Sweeney; Williams; Alexander (1961); Fama & Blume (1966); Neftci (1991); Cowles (1933); Pagan & Sossounov (2003); Lunde & Timmermann (2004); Tsang (2010); Aloud et al.; Bakhach et al.; Kavajecz & Odders-White (2004); Aggarwal & Lucey (2007); Park & Irwin (2007); Curcio et al. (1997); mã nguồn ZigZag++ của DevLucem, LuxAlgo Market Structure, MQL5 `ZigZag.mq5`/`Fractals.mq5` (mql5.com chặn 403); pandas-ta, vectorbt, freqtrade, backtrader; cTrader ZigZag; trang Pine reference chính thức cho `ta.pivothigh` (đọc qua bản render, không nêu hoà/thời điểm).

## 2. Mỗi nguồn nói gì — theo chủ đề

### 2.1. Định nghĩa Dow Theory cổ điển

- **Xu hướng = chuỗi đỉnh/đáy.** Dow (S014): "It is a bull period as long as the average of one high point exceeds that of previous high points. It is a bear period when the low point becomes lower than the previous low points." Dow cũng thừa nhận: "It is often difficult to judge whether the end of an advance has come… it may only be an unusually pronounced secondary movement."
- **Không có quy tắc khách quan cho "swing đáng kể".** Hamilton (S015): "We cannot dogmatize about the depth of such movements, in duration or extent."
- **Các ngưỡng cổ điển mâu thuẫn nhau** (ghi cả hai bên): thời gian của phản ứng thứ cấp — 10–60 ngày, trung bình 30–40 (Dow, S014) · 40–60 ngày (Hamilton kể lại lời Dow, S015) · 3 tuần–3 tháng (Rhea, S016); độ sâu — ít nhất 3/8 nhịp chính (Dow) · 33–66% (Rhea); Schaefer (qua Russell, thứ cấp) bỏ hẳn mọi giới hạn. Schannep (S016): "The percentage price movement is just a generality and should not be taken as a requirement."
- **Bộ lọc gần "khách quan" nhất** (S016, văn bản Rhea in lại): một rally/decline là biến động đảo chiều ròng **>3%** giá chỉ số; "line" dao động ≈5% trong 2–3 tuần. Tất cả là ngưỡng cố định, không co giãn theo biến động. Blog Blay (thứ cấp, chưa kiểm) nói Rhea còn loại nhịp ≤15 ngày.
- **Brown–Goetzmann–Kumar (S018) không hình thức hoá swing:** họ mã hoá 255 xã luận của Hamilton thành bull/bear/neutral và mô hình hoá bằng momentum 30/60 ngày (hồi quy, mạng nơ-ron Elman), không dùng đỉnh/đáy; tự nhận các "theorems" của Rhea "are vague enough to admit a variety of statistical interpretations". Không cho quy tắc swing dùng được.
- **Không có nguồn cổ điển nào nói về khung thời gian phút so với H4.** Dow/Hamilton/Rhea làm việc với chỉ số giá ngày. Dow (qua Nelson) có ý "swings within swings" (cấu trúc phân cấp). Từ vựng hiện đại (ICT: short/intermediate/long-term high; LuxAlgo: BOS/CHoCH, internal vs swing structure) là thứ cấp.

### 2.2. Thuật toán phát hiện cực trị

- **Directional Change (S020 ✔):** Algorithm 2 giữ `x_ext`; ở chế độ lên cập nhật `x_ext` khi giá cao hơn, và khi `(x − x_ext)/x_ext ≤ −Δ` thì đếm một DC, đảo chế độ, đặt `x_ext = x`. Tức cực trị chỉ được **chốt** khi giá đã đảo ngược ≥ Δ. Đo trên 13 cặp FX tick 2002–2007, Δ từ 0,01% đến 5,05%: "a directional change ∆xdc is followed by an overshoot of the same magnitude … making the total move double the size of the directional-change threshold".
- **Xác nhận là hồi cứu (S021):** "we can only confirm a DC event in hindsight, i.e., after there has been a price change of θ"; điểm cực trị cuối chỉ biết sau DC ngược chiều. Chọn θ: "it is not easy to know which DC threshold results return a more informative DC summary".
- **Tương đương toán học với CUSUM hai phía (S024):** drawdown/drawup "are known as CUSUM stopping times".
- **Không có nguyên tắc chọn ngưỡng (S022, S023):** Hanna: ngưỡng 20%/10% "Commonly cited, but otherwise arbitrary"; đề ra P4 (đổi nhỏ tham số → đổi nhỏ kết quả) và P5 (kéo dài chuỗi không đổi điểm lịch sử). Kole–van Dijk: độ nhạy lớn nhất ở bước censoring; ngưỡng thấp cho kết quả tốt hơn ex post nhưng "it is not obvious that working with lower thresholds leads to better forecasts or allocations ex ante".
- **Phương pháp không causal:** Lo–Mamaysky–Wang (S019): kernel regression với lag d = 3 ngày để phần tính lợi suất không nhìn trước, nhưng cực trị được tìm trên cửa sổ đã làm trơn bằng cả giá sau cực trị; bandwidth 0,3×h* chọn "ad hoc"; tìm cực trị trên giá thô "identifies too many extrema". scipy `find_peaks`/prominence/`argrelextrema` (S032) là xử lý cả mảng (hai phía), chỉ dùng ex post.
- **Khoảng trống:** không tìm thấy nghiên cứu nào so sánh các thuật toán swing (n-bar, ZigZag, DC, prominence) về chất lượng/độ nhạy/ổn định giữa khung thời gian; không so ATR-scaled với % cố định; không biện minh cho bất đối xứng left≠right.

### 2.3. Cách các cài đặt phổ biến định nghĩa và xử lý

| Cài đặt | Causal? | Độ trễ xác nhận | Phần cực trị chưa xác nhận | Nguồn |
|---|---|---|---|---|
| TradingView ZigZag built-in | pivot: có; đường cuối: không | legs/2 bar | "The latest solid line … is not final" | S025 ✔ |
| Pine `ta.pivothigh/low` | có, sau `right` bar | đúng `right` bar | chỉ trả pivot đã đủ | S026, S027 |
| MetaQuotes ZigZag (Depth/Deviation/Backstep) | không | không cố định; điểm cuối bị dời | dời/xoá khi có cực trị mới | S030 |
| MT5/cTrader Fractals (5 nến) | có sau 2 bar; mẫu cTrader so với nến đang chạy → có thể repaint | 2 bar | | S030, S031 |
| PyPI `zigzag` | đầu ra backdate pivot | tới khi đảo ≥ ngưỡng | mặc định ép gán pivot cuối ở bar cuối | S029 |
| smartmoneyconcepts `swing_highs_lows` | **không** (cửa sổ trung tâm; tác giả xác nhận ở issue #101) | — | bar đầu/cuối gán nhãn nhân tạo | S028 |
| scipy `find_peaks` | không (cả mảng) | — | — | S032 |

- **Luật hoà không thống nhất, ngay trong TradingView:** Help Center ZigZag ✔: pivot high phải ">" mọi bar trước và ">=" mọi bar sau (đúng luật cũ của hệ). PineTS PR #322 mô tả `ta.pivothigh` làm ngược lại (bar trái bằng không loại, bar phải bằng loại), nhưng đó là lời báo cáo "verified against TradingView", tôi và tác tử chưa tự kiểm trên chart; tài liệu chính thức `ta.pivothigh` không nói gì về hoà.
- **Nến bao ngoài (vừa là đỉnh vừa là đáy) không có chuẩn:** MetaQuotes bỏ qua; smartmoneyconcepts ưu tiên high.
- **smartmoneyconcepts lệch 1 bar** (đọc code): sau `swing_length *= 2`, `rolling(2L)` phủ trái L−1 bar, phải L bar, trong khi docstring nói L mỗi bên. Một người dùng tự báo profit factor 7,32 (có lookahead) so với 1,82 (chờ xác nhận) trên XAUUSD M15 10 năm; số này chưa kiểm.
- **Pine docs (S026):** script dựa pivot thuộc nhóm "potentially misleading" vì vẽ lùi `right` bar; dùng khung lớn hơn cần `[1]` cùng `lookahead_on`.
- **Mọi cài đặt "không repaint" đạt được bằng cách chỉ dùng cực trị sau khi đủ nến/biên độ xác nhận;** mọi cài đặt có cực trị đang chạy đều vẽ lại đoạn cuối (S025, S030, trang LuxAlgo ZigZag).

### 2.4. Bằng chứng về stop-loss

- **Không tìm thấy bằng chứng chặt chẽ cho swing stop vượt ATR stop sau chi phí; cũng không có bằng chứng ngược theo kiểu so sánh trực tiếp.** Đây là chỗ trống, không phải kết luận "ATR hơn". Các khẳng định "structure stops outperform" gặp trên blog đều không kèm dữ liệu.
- Ablation công khai duy nhất mở được (S040, chất lượng thấp): 6 kiểu stop trên 13 hợp đồng tương lai hàng hoá Trung Quốc 2020–2025, **không kiểu nào là swing**; chênh nhau trong nhiễu (tác giả: "Moderate edge — +0.12 Sharpe vs no-stop; not decisive").
- **Học thuật về stop nói không thống nhất, và khác tài sản/tần suất so với hệ này:** Kaminski–Lo (S033): stop "can increase expected return" ở tần suất dài nếu có momentum (random walk thì "stopping premium" âm); Han–Zhou–Zhu (S034): stop 15% trên momentum cổ phiếu tháng 1926–2013 đưa tháng tệ nhất từ −49,79% lên −17,43%, stop theo vol cho Sharpe 0,535 so với 0,399 của stop cố định 15% (mẫu gồm micro-cap; Alpha Architect báo yếu hơn ở mid/large cap — thứ cấp); Lei–Li (S036): giá trị chủ yếu từ giảm rủi ro chứ không tăng lợi suất; Clare et al. (S035 ✔): "popular stop loss rules do not add value" và "a change of trend is the best stop loss".
- **Clare et al. Table 4B ✔ (S&P 500 hàng ngày 07/1988–06/2011, phí 0,2%, breakout 200 ngày):** Sharpe theo stop 3%/5%/7%/10%/12%/15% = −0,11 / 0,08 / 0,31 / 0,52 / 0,54 / 0,49 → stop quá chặt gây hại rõ. Chỉ là chỉ số cổ phiếu hàng ngày.
- **Osler (S037 ✔, RBS order book 08/1999–04/2000, 9.655 lệnh, USD/JPY, GBP/USD, EUR/USD):** stop-loss chiếm 43% theo volume; "Almost 10 percent" lệnh đặt tại mức tận cùng 00. **Stop-loss buy đã khớp tập trung ngay TRÊN số tròn** (14,3% có đuôi giá trong [01,10] so với 6,9% trong [90,99]), stop-loss sell ngay dưới; take-profit thì cụm đúng tại mức. Tác giả tự giới hạn: "statistical analysis cannot prove that the connection is causal". Hiệu ứng nhỏ về kinh tế (số 0,061% so với 0,054% trong 15 phút: do tác tử đọc, tôi chưa đối chiếu). Đây là bằng chứng về **số tròn trên FX majors thập niên 1990**, không phải về swing high/low; không tìm thấy nghiên cứu bình duyệt nào cho thấy stop tụ tại swing.
- **Sách cổ điển đặt stop dưới swing nhưng luôn có đệm và cảnh báo bị săn:** Schabacker (S017): đặt stop "well away from the purchase price, by studying resistance levels and placing the stop below such prices"; "bear traders 'gunning for stop-loss orders'". Dow (S014): "The difficulty with stop orders is that they are frequently exercised when the event shows that the loss need not have been taken." Edwards & Magee (thứ cấp, chưa kiểm sách): stop dưới minor bottom cộng đệm %, chỉ nâng sau khi giá "3 days away".
- **Quy ước practitioner có nguồn:** Turtle (S038): stop 2N, rủi ro tối đa 2%/lệnh, "More volatile markets would have wider stops, but they would also have fewer contracts per Unit"; stop ½N cho "better profitability" nhưng "incurred many more losses". Chandelier (S039): đỉnh 22 ngày − 3×ATR(22), neo vào đỉnh cửa sổ chứ không phải swing xác nhận. Elder SafeZone (thứ cấp): stop = đáy trước − k×"average penetration", k 2–3. **Không tìm thấy nguồn có tên nêu "buffer bao nhiêu ATR sau swing"**; các con số 0,5–1,0×ATR gặp trên blog không có gốc. Các mốc 2–3 ATR đều cho khung ngày.
- **Hygiene backtest:** stop và target cùng một nến → mặc định ưu tiên stop (thứ cấp, diễn đàn); broker emulator TradingView suy đường đi trong nến theo khoảng cách open tới high/low và gap khớp ở open (tài liệu, đọc một phần); khi cascade giá "gappy". Carr–López de Prado (S041): hiệu chỉnh quy tắc bằng backtest "contributes to backtest overfitting" → bảng KSL×ATR đã tối ưu nên được so bằng walk-forward.

## 3. Phát hiện (ID tiếp từ F059)

Mỗi mục: nội dung — nhãn — nguồn. Trạng thái mới/đã có: toàn bộ là **mới** (chưa có nguồn nào trước đó về chủ đề này trong INDEX).

- F060 Xu hướng Dow = đỉnh/đáy cao dần (thấp dần) chừng nào chưa bị phá — `nguồn-báo-cáo` — S014
- F061 Không có quy tắc khách quan cho "swing đáng kể"; Hamilton: "We cannot dogmatize…" — `nguồn-báo-cáo` — S014, S015
- F062 Ngưỡng cổ điển mâu thuẫn giữa các tác giả (10–60 / 40–60 ngày / 3 tuần–3 tháng; 3/8 / 33–66%) — `nguồn-báo-cáo` (mâu thuẫn nội bộ giữa nguồn) — S014–S016
- F063 Bộ lọc cổ điển gần khách quan nhất: đảo chiều >3% (Rhea); mọi ngưỡng là cố định tuyệt đối, không co giãn theo biến động — `nguồn-báo-cáo` — S016
- F064 Brown–Goetzmann–Kumar không hình thức hoá swing; không cho quy tắc dùng được — `nguồn-báo-cáo` — S018
- F065 Không có nguồn cổ điển về khung phút so với H4; thang thời gian gắn với chỉ số giá ngày — `nguồn-báo-cáo` (khoảng trống) — S014–S016
- F066 Xác nhận cực trị kiểu DC/ZigZag: chốt khi giá đảo ngược ≥ Δ; overshoot trung bình ≈ Δ (tổng ≈ 2Δ) — `nguồn-báo-cáo` ✔ — S020
- F067 Xác nhận DC là hồi cứu; tương đương CUSUM hai phía — `nguồn-báo-cáo` — S021, S024
- F068 Không có nguyên tắc chọn ngưỡng đảo chiều; lợi ích ngưỡng thấp chỉ thấy ex post — `nguồn-báo-cáo` — S022, S023
- F069 Không có nghiên cứu so sánh thuật toán swing, so ATR với % cố định, hoặc biện minh left≠right — `suy-luận` (kết quả tìm kiếm âm) — (khoảng trống)
- F070 Cửa sổ đối xứng/kernel/prominence không causal; chỉ dùng ex post — `nguồn-báo-cáo` — S019, S032
- F071 Pivot đúng nghĩa causal chỉ biết sau `right` bar; ZigZag-kiểu cực trị đang chạy vẽ lại đoạn cuối — `nguồn-báo-cáo` ✔ (TradingView ZigZag) — S025, S026, S030
- F072 Luật hoà không thống nhất (doc ZigZag ">" trước, ">=" sau; PineTS mô tả `ta.pivothigh` ngược lại); nến bao ngoài không có chuẩn — `nguồn-báo-cáo` (phần PineTS: thứ cấp) — S025, S027, S028, S030
- F073 smartmoneyconcepts `swing_highs_lows` có lookahead, lệch 1 bar; PF 7,32 so với 1,82 là số tự đo của một người dùng — `nguồn-báo-cáo` (code: đọc; số: yếu) — S028
- F074 Pine: script dựa pivot "potentially misleading"; khung lớn cần `[1]` + `lookahead_on` — `nguồn-báo-cáo` — S026
- F075 Không tìm thấy bằng chứng chặt chẽ cho swing stop > ATR stop sau chi phí (cũng không có bằng chứng ngược trực tiếp); ablation công khai duy nhất không có kiểu swing và chênh trong nhiễu — `suy-luận` (khoảng trống) + `nguồn-báo-cáo` (chất lượng thấp) — S040
- F076 Học thuật về stop-loss không thống nhất, đều khác tài sản/tần suất so với hệ (cổ phiếu/chỉ số, tháng–ngày) — `nguồn-báo-cáo` — S033–S036
- F077 Clare et al.: stop % chặt (3–5%) có Sharpe thấp hơn nhiều so với 10–12% trên S&P 500 hàng ngày; "a change of trend is the best stop loss" — `nguồn-báo-cáo` ✔ — S035
- F078 Han–Zhou–Zhu: stop theo vol (3 sd daily vol tháng trước) Sharpe 0,535 so với 0,399 của stop cố định 15% — bằng chứng hiếm cho vol-scaled > fixed, nhưng momentum cổ phiếu tháng — `nguồn-báo-cáo` — S034
- F079 Osler: stop-loss tụ ngay sau số tròn (buy: 14,3% [01,10] so với 6,9% [90,99]), take-profit tụ đúng tại mức; ≈10% lệnh ở mức tận cùng 00 — `nguồn-báo-cáo` ✔ — S037
- F080 Osler không chứng minh nhân quả; bằng chứng về số tròn FX majors thập niên 1990, không phải swing; không tìm thấy bằng chứng bình duyệt cho stop tụ tại swing; ngoại suy sang chỉ số/vàng/crypto M10–H4 là chưa kiểm — `nguồn-báo-cáo` ✔ + `suy-luận` — S037
- F081 Sách cổ điển/practitioner đặt stop dưới swing kèm đệm và cảnh báo bị săn stop; không có nguồn có tên cho "đệm bao nhiêu ATR" — `nguồn-báo-cáo` — S014, S017, S038, S039
- F082 Turtle: stop rộng hơn ở thị trường biến động nhưng ít hợp đồng hơn; stop ½N → nhiều lệnh thua hơn — `nguồn-báo-cáo` — S038
- F083 Hiệu chỉnh quy tắc bằng backtest dễ overfit → so với bảng KSL×ATR đã tối ưu cần walk-forward, không tối ưu hai phía trên cùng mẫu — `nguồn-báo-cáo` (cảnh báo chung) + `suy-luận` (áp dụng) — S041
- F084 Chuẩn hoá ngưỡng đảo chiều theo ATR hợp lý về nguyên lý (tần suất swing phụ thuộc θ/σ; Schabacker: "normal past action of the individual stock") nhưng chưa có kiểm chứng trực tiếp — `suy-luận` — S020, S017
- F085 Cách Dow cũ của hệ (trái 3 chặt / phải 5 không chặt, xen kẽ, thay cùng loại nếu cực trị hơn, loại đảo chiều <0,5×ATR14 tại bar pivot, stop = swing cuối nếu đúng phía): không có tham số nào có nguồn cổ điển; luật hoà khớp doc ZigZag của TradingView — `suy-luận` (đối chiếu F061–F062, F072) — T002 (mô tả thuật toán)
- F086 T002: stop của cách cũ cách entry trung vị **3,51 ATR(5)** (p90 5,54); 12–16% tín hiệu xa hơn 5 ATR; <2% gần hơn 1 ATR; 1/489 tín hiệu không có stop — `tái-lập` — T002
- F087 T002: tuổi pivot được dùng trung vị **12 nến** (p90 18), đúng kỳ vọng từ độ trễ xác nhận 5 nến cộng thời gian từ pivot đến tín hiệu — `tái-lập` — T002
- F088 T002: **7,8% (38/488)** tín hiệu có mức stop đã bị giá chạm/xuyên giữa lúc pivot hình thành và nến tín hiệu (US30 M30 11%, BTCUSD M10 6%, GOLD M45 8%); nguyên nhân dự kiến: chỉ lấy swing cuối, không kiểm đã bị phá — `tái-lập` (số) + `suy-luận` (nguyên nhân) — T002

## 4. T002 — đo cách Dow cũ trên dữ liệu thật của hệ (`tái-lập`)

**Thiết kế.** Chụp đầu ra pipeline `ma_cross` của `we_core` ngày 2026-10-11, **trước khi xoá Dow khỏi `we_core`** (mã Dow trong `we_core` ở thời điểm đó chưa từng được commit; thuật toán cùng họ với `core_python/strategies/dow_stop.py` ở git `1b985d8`), trên nến mới nhất tải từ SQL DP6 (chỉ `SELECT`): US30 M30 (6.000 nến), BTCUSD M10 (8.000), GOLD M45 (4.000). Trên mỗi dòng có tín hiệu, đo: |entry − `sl_dow`| / `atr` (ATR(5) của chiến lược `ma_cross`, không phải ATR14 nội bộ của Dow), tuổi của pivot (số nến từ nến có đúng giá `sl_dow` đến nến tín hiệu) và việc giá có chạm/xuyên mức đó trong khoảng đó không.

**Kết quả** (489 tín hiệu; 488 có `sl_dow`):

| Ca | Tín hiệu | Khoảng cách stop / ATR(5): trung vị · p90 | >5 ATR | <1 ATR | Tuổi pivot: trung vị · p90 | Stop đã bị phá trước khi vào |
|---|---|---|---|---|---|---|
| US30 M30 | 149 | 3,26 · 5,80 | 16% | 2% | 12 · 17 | 17/148 (11%) |
| BTCUSD M10 | 236 | 3,56 · 5,65 | 15% | 0% | 11 · 18 | 13/236 (6%) |
| GOLD M45 | 104 | 3,44 · 5,23 | 12% | 1% | 12 · 18 | 8/104 (8%) |
| Gộp | 489 | **3,51 · 5,54** | | | **12 · 18** | **38/488 (7,8%)** |

**Giới hạn.** Mẫu nhỏ (3 ca, chỉ `ma_cross`); khoảng cách đo theo ATR(5), chọn ATR khác sẽ đổi con số; chỉ đo đặc tính của stop (khoảng cách, độ cũ, độ lệch thời), **không** đo lợi nhuận hay tỉ lệ bị quét; tìm pivot bằng so khớp giá chính xác theo thứ tự ngược nên có thể trúng nến cùng giá sớm hơn (nhỏ). Không còn tái chạy nguyên trạng vì mã Dow đã xoá khỏi `we_core`; muốn lặp lại phải dựng lại thuật toán từ `dow_stop.py` (git `1b985d8`) hoặc mô tả ở F085. Không lưu script (yêu cầu "chưa code"). Cũng không dùng để so với KSL×ATR.

## 5. Đề xuất thiết kế "sóng Dow" v1 (`suy-luận` — chưa kiểm bằng dữ liệu, chưa triển khai)

Dựa trên F061–F088; chưa có bằng chứng thực nghiệm cho các lựa chọn dưới đây, nên là giả thuyết cần kiểm trên dữ liệu của hệ.

1. **Xác nhận bằng đảo chiều ≥ m × ATR** (ATR tại thời điểm xét, không dùng dữ liệu tương lai) thay cho đếm nến cố định: độ trễ co theo biến động, số swing giữa khung M10–H4 tương đương nhau. Nên giữ họ n-bar + ngưỡng ATR (cách cũ) làm đối chứng, vì văn liệu không nêu thuật toán nào hơn.
2. **Giữ danh sách các swing luân phiên**, không chỉ swing cuối: lệnh BUY lấy đáy gần nhất **chưa bị phá** và nằm dưới giá vào đủ xa; nếu đã bị phá thì lùi về đáy cũ hơn. Nhằm xử lý F088 và đúng định nghĩa xu hướng của Dow (F060).
3. **Tách "vị trí swing" và "đệm"**: stop = swing ∓ b×ATR, b là tham số kiểm định riêng; có sàn/trần khoảng cách stop tính theo ATR; khi vượt trần phải có quy tắc rõ (bỏ lệnh hoặc dùng stop KSL). Cơ sở cho đệm: F079, F081 (bằng chứng yếu).
4. **Cực trị tạm thời** (thấp nhất từ đỉnh đã xác nhận gần nhất) tách riêng; tại thời điểm t vẫn nhân quả. Thử có/không dùng.
5. **Quy tắc ghi rõ và có test:** luật hoà; nến bao ngoài; thứ tự high/low trong một nến (bất định với dữ liệu OHLC; chọn quy ước thận trọng); test cắt dữ liệu tại t rồi so (kết quả tại t không đổi); test nối dài chuỗi (điểm lịch sử không đổi — nguyên tắc P5 của Hanna); khung lớn chỉ dùng nến đã đóng.
6. **Cách chọn tham số:** so 3 họ (cách cũ · đảo chiều ATR · đảo chiều ATR + cực trị tạm thời) với baseline KSL×ATR bằng đúng cùng tín hiệu và chi phí; đo bằng bội số rủi ro (R) sau chi phí, tỉ lệ bị quét stop, phân phối khoảng cách stop; chia in-sample/out-of-sample trước, lưới tham số nhỏ để tránh overfit (F083).

**Quyết định chờ người dùng:** (1) "tối ưu" theo tiêu chí nào (kỳ vọng R cao nhất, hay ít bị quét nhất với rủi ro tối đa cho trước); (2) áp dụng cho chiến lược nào, và khi swing stop quá xa thì bỏ lệnh hay quay về KSL; (3) WE hiện không tính SL (CLAUDE.md mục 1) — thêm cột stop vào CSV/Redis là đổi schema, phải báo VM-BO20.

## 6. Hạn chế của ghi chú này

- Bốn nhánh được dừng sớm theo yêu cầu "tìm vừa đủ": nhiều nguồn chỉ có abstract hoặc tóm tắt thứ cấp (xem "Chưa đọc" ở mục 1).
- Phần lớn nội dung do tác tử phụ đọc; chỉ các mục có ✔ được tôi đối chiếu lại. Các số liệu không có ✔ (ví dụ Kaminski–Lo 50–100 bp, Han–Zhou–Zhu, số liệu 0,061% của Osler, PF 7,32 của smartmoneyconcepts) nên xem là `nguồn-báo-cáo` chưa kiểm.
- Không có kiểm thử nào về lợi nhuận của stop trên dữ liệu hệ; T002 chỉ mô tả đặc tính của cách cũ.
