# research_notes — mục lục

Mỗi nguồn có **một ghi chú chi tiết riêng** (số liệu, trích nguyên văn, kết quả tái lập, hạn chế, nguồn gốc). File này chỉ để tra nhanh và chống nạp trùng; chi tiết luôn nằm trong ghi chú.

Nhãn: `nguồn-báo-cáo` (nguồn nói, chưa kiểm) · `tái-lập` (đã chạy lại) · `lỗi-nguồn` (lỗi/mâu thuẫn xác minh ngay trong nguồn) · `suy-luận` (phân tích của tôi).
Nạp nguồn mới: tra bảng Nguồn (URL/DOI/tiêu đề) → trùng thì dừng; mỗi phát hiện xếp **đã có / mới / tinh chỉnh / mâu thuẫn** (mâu thuẫn thì ghi cả hai bên và báo người dùng); đối chiếu với số đo trên hệ.

## Nguồn

| ID | Ghi chú | Nguồn | Chủ đề |
|---|---|---|---|
| S001 | kidquant_2024_pairs-trading-python.md | KidQuant/Pairs-Trading-With-Python, PairsTrading.ipynb @ 8e86c88 | giao dịch cặp, đồng tích hợp |
| S002 | drive-stats-arbitrage_2026_7-notebooks.md | Google Drive "Stats Arbitrage" (7 notebook, không rõ tác giả) | stat-arb: khoảng cách, Kalman, VECM, PCA |
| S003 | zazhang_2018_ep-chan-book-notebook.md | zazhang/ep-chan-book-algo-trading @ 0f3f065 | hồi quy trung bình, Bollinger cặp, động lượng chéo |
| S004 | pyportfolioopt_2026_library-and-joss.md | PyPortfolio/PyPortfolioOpt @ a6638d2 (v1.6.0) + JOSS 10.21105/joss.03066 | tối ưu danh mục |
| S005 | paperswithbacktest_2026_awesome-systematic-trading.md | paperswithbacktest/awesome-systematic-trading @ ddfee8b | chỉ mục nguồn; thống kê nhà cung cấp |
| S006 | open-finance-lab_2026_agentic-trading-lab-and-finagent-paper.md | Open-Finance-Lab/AgenticTrading @ 870a7f0 + arXiv:2512.02227 | tác tử LLM giao dịch; vệ sinh đánh giá |
| S007 | deepentropy_2026_tvscreener-and-tradingview-terms.md | deepentropy/tvscreener @ 6faf496 + Điều khoản sử dụng TradingView (mục 3) | nguồn dữ liệu TradingView; giới hạn sử dụng |
| S008 | georgezouq_2026_awesome-ai-in-finance.md (+ `_catalog.csv`, 263 dòng) | georgezouq/awesome-ai-in-finance @ 2863ab2 (README sha256 357cefe9…; CC0-1.0) | chỉ mục tài nguyên AI/LLM/tác tử/MCP trong tài chính; độ mới và liên kết |
| S009 | topic_2026_sideway-detection-for-trend-following.md | Gurrib (2018) ADX trên forex, doi 10.21511/bbs.13(3).2018.06 | bộ lọc ADX |
| S010 | topic_2026_sideway-detection-for-trend-following.md | Mitra (2012) Hurst và quy tắc MA, doi 10.5539/ass.v8n8p111 | Hurst / độ bền xu hướng |
| S011 | topic_2026_sideway-detection-for-trend-following.md | Kurth, Eisler, Rej, Bouchaud (2026) arXiv:2607.01550v1 | suy giảm trend ngắn hạn; hiệu ứng LeBaron |
| S012 | topic_2026_sideway-detection-for-trend-following.md | Hurst, Ooi, Pedersen (2017) A Century of Evidence on Trend-Following (AQR) | trend following dài hạn; "smile" |
| S013 | topic_2026_sideway-detection-for-trend-following.md | Baltas & Kosowski (2015) Demystifying TSMOM, Imperial hdl 10044/1/41472 (SSRN 2140091) | quy tắc TREND (t-stat), turnover |
| T001 | topic_2026_sideway-detection-for-trend-following.md §4 (+ `scripts/t001_*.py`) | Kiểm thử trên DP6: 99.274 lệnh breakout_atr/sma_trend, 11 symbol × M30/H1/H4, 2019–2026 | ADX/CHOP/ER/biến động tại lúc vào lệnh |
| S014 | topic_2026_dow-swing-stoploss.md | Nelson (1902/03) *The ABC of Stock Speculation* (lời Dow), archive.org abcofstockspecul00nelsiala | Dow: xu hướng = đỉnh/đáy; stop |
| S015 | topic_2026_dow-swing-stoploss.md | Hamilton (1922) *The Stock Market Barometer*, archive.org stockmarketbarom00hami | Dow: "cannot dogmatize" về độ sâu/thời gian |
| S016 | topic_2026_dow-swing-stoploss.md | Schannep (2008) *Dow Theory for the 21st Century*, excerpt Wiley (văn bản Rhea 1932 in lại) | Dow: bộ lọc 3%, 33–66%, 3 tuần–3 tháng |
| S017 | topic_2026_dow-swing-stoploss.md | Schabacker (1930) *Stock Market Theory and Practice* Ch. 22, archive.org bwb_Y0-EGF-847 | stop và săn stop (cổ điển) |
| S018 | topic_2026_dow-swing-stoploss.md | Brown, Goetzmann, Kumar (1998) J. Finance 53(4):1311–1333 (working paper NYU FIN-98-013) | kiểm định Dow Theory của Hamilton |
| S019 | topic_2026_dow-swing-stoploss.md | Lo, Mamaysky, Wang (2000) J. Finance 55(4):1705–1765, NBER w7613 | kernel regression; lag chống lookahead |
| S020 | topic_2026_dow-swing-stoploss.md | Glattfelder, Dupuis, Olsen (2011) Quant. Finance 11(4):599–614, arXiv:0809.1040 | Directional Change; overshoot ≈ Δ |
| S021 | topic_2026_dow-swing-stoploss.md | Adegboye, Kampouridis, Otero, doi 10.1007/s10462-022-10307-0; Rayment et al. (slide) | DC xác nhận hồi cứu; chọn θ |
| S022 | topic_2026_dow-swing-stoploss.md | Hanna (2018) IRFA 55:93–110, doi 10.1016/j.irfa.2017.11.001 | bộ lọc bull/bear; nguyên tắc P4/P5 |
| S023 | topic_2026_dow-swing-stoploss.md | Kole, van Dijk, EUR working paper repub.eur.nl/pub/41558 | độ nhạy censoring; lợi ích ex post |
| S024 | topic_2026_dow-swing-stoploss.md | Zhang, Hadjiliadis, arXiv:0911.1575 | drawdown/drawup = CUSUM |
| S025 | topic_2026_dow-swing-stoploss.md | TradingView Help Center "ZigZag", tradingview.com/support/solutions/43000591664/ | pivot ">" trước / ">=" sau; đường cuối chưa chốt |
| S026 | topic_2026_dow-swing-stoploss.md | TradingView Pine docs: repainting, operators, strategies (đọc một phần) | repaint pivot; request.security; broker emulator |
| S027 | topic_2026_dow-swing-stoploss.md | LuxAlgo/PineTS PR #322 (`ta.pivothigh/low`) | luật hoà `ta.pivothigh` (thứ cấp) |
| S028 | topic_2026_dow-swing-stoploss.md | joshyattridge/smart-money-concepts `smc.py` + issue #101 | swing có lookahead; lệch 1 bar |
| S029 | topic_2026_dow-swing-stoploss.md | jbn/ZigZag `zigzag/core.pyx` (PyPI zigzag) | ZigZag Python; ép gán pivot cuối |
| S030 | topic_2026_dow-swing-stoploss.md | MetaQuotes ZigZag (bản copy pastebin D4nRVWLY) + tài liệu MT5 Fractals | ZigZag/Fractals MetaTrader; repaint |
| S031 | topic_2026_dow-swing-stoploss.md | cTrader Algo: how-to Fractals (help.ctrader.com) | Fractals cTrader |
| S032 | topic_2026_dow-swing-stoploss.md | scipy docs: find_peaks, peak_prominences, argrelextrema | cực trị theo mảng (không causal) |
| S033 | topic_2026_dow-swing-stoploss.md | Kaminski, Lo (2014) J. Financial Markets 18:234–254 (chỉ abstract) | stop-loss và momentum |
| S034 | topic_2026_dow-swing-stoploss.md | Han, Zhou, Zhu (2016) SSRN 2407199 | stop theo vol trên momentum cổ phiếu |
| S035 | topic_2026_dow-swing-stoploss.md | Clare, Seaton, Smith, Thomas (working paper; J. Asset Mgmt 2013), openaccess.city.ac.uk/17842 | stop % trên S&P 500; Table 4B |
| S036 | topic_2026_dow-swing-stoploss.md | Lei, Li (2009) Financial Services Review 18(1):23–51 (abstract) | stop-loss giảm rủi ro hơn tăng lãi |
| S037 | topic_2026_dow-swing-stoploss.md | Osler (2005) NY Fed SR 150 (JIMF 24(2)); Osler (2003) NY Fed SR 125 (J. Finance) | stop-loss tụ quanh số tròn (FX) |
| S038 | topic_2026_dow-swing-stoploss.md | The Original Turtle Trading Rules (OriginalTurtles.org 2003) | stop 2N; sizing theo khoảng cách stop |
| S039 | topic_2026_dow-swing-stoploss.md | StockCharts ChartSchool Chandelier Exit; Elder SafeZone (qua LuxAlgo, thứ cấp) | stop ATR/noise (practitioner) |
| S040 | topic_2026_dow-swing-stoploss.md | GitHub ddkklau/CTA_TrendFollowing_StopLoss_Research (README) | ablation 6 kiểu stop (không có swing) |
| S041 | topic_2026_dow-swing-stoploss.md | Carr, López de Prado, arXiv:1408.1159 (abstract) | overfit khi hiệu chỉnh quy tắc bằng backtest |
| T002 | topic_2026_dow-swing-stoploss.md §4 | Đo trên DP6 (chỉ SELECT): 489 tín hiệu ma_cross bằng cách Dow cũ (US30 M30, BTCUSD M10, GOLD M45) | khoảng cách stop, tuổi pivot, stop đã bị phá |
| S042 | topic_2026_dashboard-frontend-ux-standards.md | web.dev *Interaction to Next Paint (INP)*, web.dev/articles/inp | INP: ngưỡng, 3 pha, loại tương tác |
| S043 | topic_2026_dashboard-frontend-ux-standards.md | web.dev *Optimize long tasks* | tác vụ dài > 50 ms, nhường luồng chính |
| S044 | topic_2026_dashboard-frontend-ux-standards.md | web.dev *Optimize Interaction to Next Paint* | tối ưu INP trong trình xử lý sự kiện |
| S045 | topic_2026_dashboard-frontend-ux-standards.md | MDN *Animation performance and frame rate* | ngân sách 16,7 ms mỗi khung |
| S046 | topic_2026_dashboard-frontend-ux-standards.md | Nielsen *Response Times: The 3 Important Limits* (nngroup.com) | ngưỡng phản hồi 0,1/1/10 s |
| S047 | topic_2026_dashboard-frontend-ux-standards.md | NN/g *Skeleton Screens 101* | skeleton/spinner/thanh tiến trình |
| S048 | topic_2026_dashboard-frontend-ux-standards.md | NN/g *10 Usability Heuristics* (chỉ đoạn đầu mỗi định nghĩa) | heuristic #1, #3, #7, #9 |
| S049 | topic_2026_dashboard-frontend-ux-standards.md | Laws of UX *Doherty Threshold* (gốc Doherty & Thadani 1982, chưa đọc) | phản hồi trong 400 ms |
| S050 | topic_2026_dashboard-frontend-ux-standards.md | TradingView Lightweight Charts, demo *Infinite history* | mẫu tải lịch sử chính thức |
| S051 | topic_2026_dashboard-frontend-ux-standards.md | Lightweight Charts docs `ChartOptionsBase`, `TimeScaleOptions` (bản mới; vendor v4.2.1) | autoSize, tuỳ chọn trục thời gian |
| S052 | topic_2026_dashboard-frontend-ux-standards.md | MDN *AbortController* và *abort()* | huỷ request đang chạy |
| S053 | topic_2026_dashboard-frontend-ux-standards.md | MDN *ARIA live regions* | aria-live, role=status/alert |
| S054 | topic_2026_dashboard-frontend-ux-standards.md | MDN *Understanding WCAG — Color contrast* (thay trang W3C bị 403) | 1.4.3, 1.4.11 |
| S055 | topic_2026_dashboard-frontend-ux-standards.md | MDN *Understanding WCAG — Operable* | 2.1.1, 2.4.7, 2.5.8 |
| S056 | topic_2026_dashboard-frontend-ux-standards.md | MDN *Understanding WCAG — Robust* | 4.1.2, 4.1.3 |
| S057 | topic_2026_dashboard-frontend-ux-standards.md | Chartability (chartability.fizz.studio; Elavsky và cộng sự, Eurographics 2022) | truy cập được cho biểu đồ: POUR + CAF |
| S058 | topic_2026_dashboard-frontend-ux-standards.md | Google HTML/CSS Style Guide | tách cấu trúc/trình bày/hành vi |
| S059 | topic_2026_ms-fractal-trading-range-trend.md | TradingView *MS_TradingRange_WilliamsFractal* (tác giả hphuc0212, đóng nguồn, https://www.tradingview.com/script/GU8dWC8D-MS-TradingRange-WilliamsFractal/), đọc 2026-10-15 | trang script: tên, nhóm, ảnh Vàng H1 độ dài 16 |
| S060 | topic_2026_ms-fractal-trading-range-trend.md | Mã Pine `WFractal_Lib` v48 + chỉ báo chính do người dùng dán (2026-10-15); tóm tắt bằng lời, KHÔNG lưu mã gốc (đóng nguồn) | thuật toán pivot → zigzag → "sóng chính" → BOS/CHOCH |
| S061 | topic_2026_ms-fractal-trading-range-trend.md | LuxAlgo Library, *Market Structure CHoCH/BOS (Fractal)* (TradingView, 20/06/2023) | khái niệm CHoCH/BOS dựa fractal, tham số `Length` |
| S062 | topic_2026_ms-fractal-trading-range-trend.md | `smartmoneyconcepts` (joshyattridge): `swing_highs_lows`, `bos_choch` (qua bản tóm tắt, không nguyên văn) | swing có nhìn trước, cờ ghi tại nến pivot |
| S063 | topic_2026_dashboard-frontend-ux-standards.md §7 | Lightweight Charts docs `ISeriesApi` (`update`, `setData`) | update thêm/sửa nến cuối, setData thay toàn bộ |
| S064 | topic_2026_dashboard-frontend-ux-standards.md §7 | Lightweight Charts demo *Realtime updates* | setData một lần rồi update từng tick |
| S065 | topic_2026_dashboard-frontend-ux-standards.md §7 | Van Der Donckt và cộng sự, *Data Point Selection for Line Chart Visualization* (arXiv:2304.00900) | giảm mẫu biểu đồ đường: MinMax/LTTB/M4, visual stability |
| S066 | topic_2026_dashboard-frontend-ux-standards.md §7 | MDN *Using server-sent events* | SSE: giới hạn 6 kết nối HTTP/1.1, tự nối lại |
| S067 | topic_2026_dashboard-frontend-ux-standards.md §7 | MDN *Page Visibility API* | không poll khi tab ẩn, timer bị giảm tần suất |
| S068 | topic_2026_dashboard-frontend-ux-standards.md §7 | Lightweight Charts docs `TimeScaleOptions` bản 5.2 | conflation có ở v5.2, không có ở vendor v4.2.1 |
| S069 | topic_2026_dashboard-frontend-ux-standards.md §7 | Blog SSE vs WebSocket vs polling (oneuptime, back4app, codemia, openreplay) — CHỈ tóm tắt tìm kiếm | thứ cấp, không có số đo Flask |
| S070 | topic_2026_dashboard-frontend-ux-standards.md §7 | apidog *API Caching with ETag and Cache-Control* — CHỈ tóm tắt tìm kiếm | thứ cấp, 304 chỉ tiết kiệm payload |
| T003 | topic_2026_dashboard-frontend-ux-standards.md §4 | Đo dashboard `we_core` (SQL chỉ SELECT, Chrome headless): thời gian server/trình duyệt, số request, độ mượt, kiểm a11y tự động | hiệu năng và truy cập được của dashboard hiện tại |
| T004 | topic_2026_dashboard-frontend-ux-standards.md §4b | Gói C: so `payload.py` cũ (iterrows) và mới (theo cột) xen kẽ trên cùng máy — fuzz 16.500 phép gọi, 292 ca dữ liệu lưu, 24 ca SQL sống giống từng byte; thời gian `/api/scan`; gzip; Chrome thật | kết quả sau khi tăng tốc dashboard (vector hoá + gzip) |
| T005 | topic_2026_ms-fractal-trading-range-trend.md §3–4 | Bản Python `scripts/t005_ms_fractal_port.py` (tái tạo từ mã S060): đối chiếu ảnh tác giả (Vàng H1), kiểm không nhìn trước (792/792), nhịp đổi hướng, tín hiệu còn lại, độ lệch cửa sổ worker/des | nhịp và số tín hiệu của khối MS Fractal ở H1/H4 |
| T006 | topic_2026_ms-fractal-trading-range-trend.md §5 | Pipeline worker (SQL SELECT, 132 cặp, từ 2022-01-01): số tín hiệu DB1 khi khung trend KNN = H1/H2/H3/H4, khi khung trend ≠ khung vào lệnh, và tách lớp lọc (setup thô → chỉ alternating → chỉ trend → trend + alternating) | ảnh hưởng của khung trend lên số tín hiệu |
| T007 | topic_2026_ma-cross-state-rule.md §3 | Thay luật `ma_cross` trong bộ nhớ (SMA13>SMA34 & MACD>0, trend rồi chống trùng), chạy pipeline thật trên 44 cặp M10–M45 (SQL SELECT, từ 2022-01-01 đến 2026-10-14); so luật cũ dựng lại nguyên văn, kiểm bộ đo với Redis; sau khi triển khai: mã thật khớp số đo 220/220 phép | số tín hiệu, trần theo số lần trend đổi hướng, trí nhớ chống trùng |
| T008 | topic_2026_ms-fractal-trading-range-trend.md §5 (F156–F158) + `scripts/t008_tune_trend_tf.py` | Chọn khung trend (H1–H4, ≥ khung vào) cho SỐ tín hiệu nhiều nhất theo từng (chiến lược, symbol, khung vào lệnh) bằng đúng đường `run_strategy_with_trend_reference` (SQL SELECT, 11 symbol, từ 2024-01-01 tới 2026-10-16); sau khi đưa bảng vào config: chụp 176 cặp "kiểu worker" so với bản chụp trước và với bảng; sau restart: đối chiếu Redis với SQL, bản chụp và log | khung trend theo ô, số tín hiệu DB1 trước/sau, ô gần hoà |
| S071 | topic_2026_candidate-strategies.md §1 | MQL5 *Automating Classic Market Methods (Part 5): The Original Turtle Trading Rules*, mql5.com/en/articles/23448 (có mã) | Donchian/Turtle: vào 20/55, thoát 10/20, skip rule, N, stop 2N |
| S072 | topic_2026_candidate-strategies.md §1 | pro.stockalarm.io "turtle trading system explained", script TradingView jbyeMkat/ZSJlXc15, metalsmine/energyexch (CHỈ tóm tắt tìm kiếm) | Turtle: nhất quán luật; một lỗi gõ ở diễn đàn |
| S073 | topic_2026_candidate-strategies.md §2 | LuxAlgo Library "RSI-2", luxalgo.com/library/concept/rsi-2/ | Connors RSI(2): SMA200, <10/<5, >90/>95, thoát SMA5 |
| S074 | topic_2026_candidate-strategies.md §2 | Connors & Alvarez (2008) *Short Term Trading Strategies That Work* — CHƯA đọc (qua S073) | nguồn gốc RSI(2) |
| S075 | topic_2026_candidate-strategies.md §2 | Backtrex Connors RSI(2) trên Nasdaq 100 / S&P 500 (CHỈ tóm tắt tìm kiếm) | kết quả yếu 2016–2026 |
| S076 | topic_2026_candidate-strategies.md §3 | TradingView "Holy Grail setup" (Konscio), my.tradingview.com/script/1HQtlUIa — mô tả, không có mã | Holy Grail: EMA20/50, ADX, nến setup, phá biên |
| S077 | topic_2026_candidate-strategies.md §3 | Capital.com "holy grail trading strategy", tradingsetupsreview.com (CHỈ tóm tắt tìm kiếm) | Holy Grail: SMA/EMA20, ADX(14)>30 |
| S078 | topic_2026_candidate-strategies.md §3, §5 | Connors & Raschke (1995) *Street Smarts* — CHƯA đọc | Holy Grail, NR7 |
| S079 | topic_2026_candidate-strategies.md §4 | Backtrex "London breakout EUR/USD", backtrex.com/en/backtests/london-breakout-eur-usd | phá khoảng phiên Á: luật và kết quả 2016–2026 |
| S080 | topic_2026_candidate-strategies.md §4 | BabyPips London Breakout 1H, TradingView londonbreakout, mql5.com/en/blogs/post/774527 (CHỈ tóm tắt tìm kiếm; nhiều trang là EA thương mại) | biến thể, bẫy giờ/DST |
| S081 | topic_2026_candidate-strategies.md §5 | Oxfordstrat "NR7 + Crabel Stretch", oxfordstrat.com/?p=3707 | NR7, ORB Crabel, 42 futures 1980–2016 |
| S082 | topic_2026_candidate-strategies.md §5 | TD Ameritrade tickertape NR7 setup; thepatternsite.com/nr7.html (CHỈ tóm tắt tìm kiếm) | NR7: lệnh chờ hai phía, biến thể đóng cửa |
| S083 | topic_2026_candidate-strategies.md §5 | Crabel (1990) *Day Trading with Short Term Price Patterns and Opening Range Breakout* — CHƯA đọc | nguồn gốc NR7/ORB |
| S084 | topic_2026_candidate-strategies.md §6 | tapeboard.com, mudrex.com, TradingView "Supertrend with Bands" (CHỈ tóm tắt tìm kiếm) | Supertrend ATR10×3, dải cuối |

## Phát hiện (ID giữ ổn định; chi tiết ở ghi chú)

**Giao dịch cặp / stat-arb**
- F001 Tương quan cao ≠ đồng tích hợp (demo corr 0,9955, p Engle–Granger 0,7687) — `nguồn-báo-cáo` — S001
- F002 Quét 55 cặp/11 mã: 6 cặp p<0,05; ADBE/MSFT p=0,04453 — `nguồn-báo-cáo` — S001
- F003 "Pairs trading luôn hedge / alpha cao": không dẫn chứng, không dùng làm bằng chứng — S001
- F004 Lỗi diễn giải ADF và ví dụ đồng tích hợp trên hai chuỗi dừng — `lỗi-nguồn` — S001
- F010 Chọn 1 cặp/190 theo khoảng cách (NSE): OOS Sharpe 1,24, CAGR 29,3% — một lần chạy, không chi phí — `nguồn-báo-cáo` — S002
- F011 HDFCBANK/ICICIBANK: p đồng tích hợp 0,2957, OOS Sharpe 0,06 — S002
- F012 Stat-arb 20 mã NSE (PCA / phần dư / ngược chéo): OOS Sharpe −0,56 / −0,92 / 0,23 — S002

**Vệ sinh backtest**
- F005 Chọn cặp bằng toàn mẫu rồi mới chia train/test → test bị lộ — `lỗi-nguồn` — S001
- F006 55 phép thử không hiệu chỉnh: kỳ vọng ≈2,75 dương tính giả; Bonferroni ≈0,00091 — `suy-luận` — S001
- F007 `trade()` thiếu chi phí/trượt giá/phí vay, vị thế cộng dồn, điểm cắt lệch → không dùng làm bằng chứng — `lỗi-nguồn` — S001
- F008 `trade()` trên dữ liệu tổng hợp: AR(0,95) lãi TB 4741 (200/200 dương) so với bước ngẫu nhiên 227 (63% dương) — `tái-lập` — S001
- F009 Kết luận "tối ưu train xa tối ưu test" mâu thuẫn số in ra (chênh ~0,5%) — `lỗi-nguồn` — S001
- F013 `Signal.replace(0,nan).ffill()` biến điểm thoát thành "giữ vị thế" → luôn có vị thế (5/7 notebook) — `tái-lập` + `lỗi-nguồn` — S002
- F014 Kalman/VECM không lưu kết quả; VECM ra tín hiệu theo hệ số nhưng tính lãi theo R1−R2; "market-neutral" không ép cân bằng — `lỗi-nguồn` — S002
- F015 USO/GLD Bollinger: Sharpe 1,572 tái lập, nhưng 50% ngày không vị thế bị `fillna(pad)`; nếu lãi = 0 thì Sharpe 0,873 — `tái-lập` + `lỗi-nguồn` — S003
- F016 Động lượng chéo 252/25/50: cửa sổ nguồn Sharpe 4,20 tái lập; toàn kỳ −0,48; đổi dấu theo năm — `tái-lập` — S003
- F017 Hồi quy trung bình chéo 51 chuỗi: Sharpe 0,9214 tái lập; không nhìn trước; trước chi phí — `tái-lập` — S003
- F018 `exitZscore` không có tác dụng (dòng exit bị comment); cùng họ F013 — `lỗi-nguồn` — S003
- F031 Độ phân giải đo phải tính trước khi chạy: 1 cổ phiếu = 2,49% vốn ở $10k so với hiệu ứng ≈0,6 điểm %; nâng vốn $100k đảo kết luận — S006
- F033 Khoá cache kết quả thiếu tham số (`initial_equity`, prompt) và cấu hình trôi sau khi đã có run → so sánh khác cơ sở — `nguồn-báo-cáo` — S006

**Kiểm định tính dừng**
- F019 Mẫu quá nhỏ (ADF 37 quan sát hiệu dụng, Hurst 3 độ trễ); `hurst.py` lỗi `NameError: log10` — `lỗi-nguồn` — S003

**Chất lượng bằng chứng nghiên cứu đã công bố**
- F023 Nhà cung cấp tự nói chạy 4.843 bài: Sharpe trung vị 0,37, 48% t>1,96, không suy giảm sau công bố; dữ liệu NAV không công khai — `nguồn-báo-cáo` — S005
- F024 t = Sharpe×√năm ⇒ cần ≈(1,96/Sharpe)² năm; Sharpe 0,4 → 24 năm — `tái-lập` (số học) — S005
- F025 Bảng "chiến lược" của S005 là lát cắt chọn theo kết quả, trước chi phí; có bất thường (crypto 22–35 năm) — `lỗi-nguồn` + `suy-luận` — S005
- F026 59 mã QuantConnect cũ: phí cố định 0,005%/lệnh, không thấy trượt giá — `nguồn-báo-cáo` — S005
- F027 Bảng 1 của bài S006 trộn cơ sở Sharpe: cổ phiếu = lợi suất năm/vol; BTC = Sharpe ngày không năm hoá — `tái-lập` + `lỗi-nguồn` — S006
- F028 Baseline cùng vũ trụ (EW 7 mã) đánh bại phương pháp (47,46% vs 20,42%) nhưng tóm tắt chỉ so S&P 500 (15,97% tái lập từ FRED) — S006
- F029 Theo số của bài: t cổ phiếu ≈2,18; t BTC ≈1,56 (<1,96), 17 lệnh — `tái-lập` + `suy-luận` — S006
- F030 Không ablation; mã/log thí nghiệm không có trong repo; phí/trượt giá không có tham số — S006
- F035 README orchestration: số liệu "1000+ factors…" và thư mục `Papers/` dẫn chiếu treo — `lỗi-nguồn` — S006

**Tối ưu danh mục**
- F020 Ví dụ README PyPortfolioOpt chạy lại đúng 4 chữ số (Sharpe 1,38 trong mẫu, 11/20 trọng số = 0) — `tái-lập` — S004
- F021 "Min-variance/HRP vượt max-Sharpe ngoài mẫu": chỉ 2 trích dẫn rõ, chưa kiểm — `nguồn-báo-cáo` — S004
- F022 Cảnh báo chính thức: mục tiêu vô lý → "fail silently and return weird weights" — `nguồn-báo-cáo` — S004

**Tác tử LLM và vận hành giao dịch thật**
- F032 `temperature=0` không tái lập (lệch 0,20 điểm % giữa hai lần chạy giống hệt, n=1 cặp) — `nguồn-báo-cáo` — S006
- F034 Mẫu an toàn giao dịch thật (hai cổng, review-only, trần $25/lệnh, không short, idempotency key) — tham chiếu thiết kế cho OF, chưa kiểm mã — S006
- F036 Tuân thủ chỉ dẫn ≠ hiệu quả; đầu ra bị cắt vẫn bị tính phí (120 lượt không ra quyết định) — S006

**Nguồn dữ liệu TradingView**
- F037 **Điều khoản TradingView mục 3**: dữ liệu chỉ cấp phép "display-only"; cấm "non-display usage" gồm automated trading/order generation, algorithmic decision-making, "any processing"; cấm dùng API thương mại nếu không có thỏa thuận riêng — văn bản gốc đã đọc; áp dụng cho DP chưa xác minh — S007 §5
- F038 `tvscreener` = lớp bọc mỏng endpoint không tài liệu, giả tiêu đề trình duyệt; chỉ ảnh chụp giá trị hiện tại, không lịch sử; không retry; `stream()` trả `None` khi lỗi; `with_history()` classmethod bị che — `tái-lập` — S007 §2, §6
- F039 Khung thời gian trong tên trường chỉ {1,5,15,30,60,120,240,1D,1W,1M}; tham số MACD(12,26), ATR(14) — không có M10/M20/M45/H3, MACD(5,25,5), ATR(5); 13.219 trường — `tái-lập` — S007 §3
- F040 `update_mode=delayed_streaming_900` chỉ là nhãn tĩnh; maintainer đo trễ 9–40 giây (1 ngày, 2 mã) — `nguồn-báo-cáo` — S007 §7
- F041 Lỗi lõi nhiều tuần (đa khung thời gian lỗi ở 0.4.0 trên PyPI từ 2026-07-13, sửa ở 0.4.1 ngày 2026-09-08); giấy phép mâu thuẫn: LICENSE Apache-2.0, pyproject/PyPI MIT — `lỗi-nguồn` — S007 §6
- F042 Không có tiêu chí chọn mục; hướng dẫn đóng góp trỏ tới phần 'guidelines above' không tồn tại; 🌟 không có chú giải — `lỗi-nguồn` — S008 §4.1
- F043 Độ mới (2026-10-04): 179/182 repo truy cập được, 15 đã lưu trữ; trung vị commit cuối 13,9 tháng; 32% repo >60 tháng — `tái-lập` (đo) — S008 §4.2
- F044 'Strategies & Research': 72% mục GitHub >24 tháng không cập nhật; 'TA Lib': 4/4 — `tái-lập` — S008 §4.2
- F045 Đã lưu trữ: mục 🌟🌟🌟 'Stock-Prediction-Models' và 'finta' (TA Lib); bot gốc askmike/gekko ghi 'not maintained anymore' (commit cuối 2020-02-16) — `tái-lập` — S008 §3, §4.2
- F046 20 repo đổi tên/chuyển chủ, README giữ địa chỉ cũ (ví dụ OpenBB `openbb-finance` → `openbq-org`, 73 816 sao) — `tái-lập` — S008 §4.2
- F047 Liên kết ngoài (76 URL): 60 HTTP 200; 9 không kết nối (3 SSRN, DNS, timeout, TLS); 2 HTTP 403; 2 HTTP 402; 3 mã khác (429/503/525) — `tái-lập` (một lần kiểm) — S008 §4.3
- F048 Trùng cấp repo với S005: 24 repo (27 URL); hai danh sách bổ sung nhau — `tái-lập` — S008 §4.5
- F049 Khẳng định hiệu quả tự công bố (InvicTrade '74% win rate', CRNG '86%', Reddit '500% returns') — chưa kiểm — `nguồn-báo-cáo` — S008 §4.4
- F050 Moody & Saffell 'Reinforcement Learning for Trading': README ghi 1994, đường dẫn NeurIPS ghi 1998 (`lỗi-nguồn`); trang 1 nêu 'predictability' của S&P 500 hằng tháng 1970–1994 (`nguồn-báo-cáo`, chưa đọc toàn bài) — S008 §3
- F051 Mục Trading Execution: 8 MCP server, 5 ghi 'Official'; không có mục cTrader — `tái-lập` (đếm) — S008 §3

**Phát hiện sideway cho trend following**
- F052 Đứng ngoài khi xu hướng không có ý nghĩa thống kê (|t| độ dốc < 2): Sharpe trước phí 0,99 so với 1,04 (không khác biệt), giao dịch còn ~1/3 → lợi ích nằm ở phí — `nguồn-báo-cáo` — S013
- F053 Trend lãi hơn khi biến động THẤP (hiệu ứng LeBaron, phân loại nhân quả); trend vài ngày–vài tuần suy yếu từ 2009 trên hợp đồng tick nhỏ — `nguồn-báo-cáo` — S011
- F054 Hurst cao đi kèm lãi MA cao (+4,30% so với −0,91% mỗi 60 ngày) nhưng đo cùng cửa sổ, không phải bộ lọc biết trước — `lỗi-nguồn` — S010
- F055 Hệ ADX>20 trên forex: 2–28 lệnh/18 năm, đa số Sharpe âm hoặc ≈0, không so với không lọc — `nguồn-báo-cáo` (bằng chứng yếu) — S009
- F056 Số liệu cộng đồng ("win 51%→64% nhờ ADX", "Mulvey & Liu 2016 Sharpe 1,4/−0,3") không có nguồn gốc kiểm được → loại — `lỗi-nguồn` — topic §3
- F057 Trên dữ liệu hệ, breakout_atr vào lúc ADX cao cho kết quả kém hơn lúc ADX thấp (5/6 phép đo, |t| tới 2,9); ER, CHOP không nhất quán IS/OOS — `tái-lập` — T001
- F058 Trên dữ liệu hệ, breakout_atr tốt hơn khi biến động thấp (6/6 cùng dấu, 2/6 |t|>2) — phù hợp F053, bằng chứng yếu–trung bình — `tái-lập` — T001
- F059 breakout_atr trước phí: +0,05–0,09 ATR/lệnh ở M30/H1 (t≈4), ≈0 ở H4; sma_trend ≈0 ở mọi khung — chưa trừ spread — `tái-lập` — T001

**Sóng Dow và stop-loss theo swing** (ghi chú: topic_2026_dow-swing-stoploss.md)
- F060 Xu hướng Dow = đỉnh/đáy cao dần (thấp dần) chừng nào chưa bị phá — `nguồn-báo-cáo` — S014
- F061 Không có quy tắc khách quan cho "swing đáng kể" (Hamilton: "We cannot dogmatize…") — `nguồn-báo-cáo` — S014, S015
- F062 Ngưỡng cổ điển mâu thuẫn giữa tác giả (10–60 / 40–60 ngày / 3 tuần–3 tháng; 3/8 / 33–66%) — `nguồn-báo-cáo` — S014–S016
- F063 Bộ lọc cổ điển gần khách quan nhất: đảo chiều >3% (Rhea); mọi ngưỡng cố định, không co giãn theo biến động — `nguồn-báo-cáo` — S016
- F064 Brown–Goetzmann–Kumar không hình thức hoá swing; không cho quy tắc dùng được — `nguồn-báo-cáo` — S018
- F065 Không có nguồn cổ điển về khung phút so với H4 — `nguồn-báo-cáo` (khoảng trống) — S014–S016
- F066 DC/ZigZag: cực trị chốt khi giá đảo ngược ≥ Δ; overshoot trung bình ≈ Δ (tổng ≈ 2Δ); đã đối chiếu văn bản gốc — `nguồn-báo-cáo` ✔ — S020
- F067 Xác nhận DC là hồi cứu; tương đương CUSUM hai phía — `nguồn-báo-cáo` — S021, S024
- F068 Không có nguyên tắc chọn ngưỡng đảo chiều; lợi ích ngưỡng thấp chỉ thấy ex post — `nguồn-báo-cáo` — S022, S023
- F069 Không có nghiên cứu so sánh thuật toán swing, so ATR với % cố định, hay biện minh left≠right — `suy-luận` (tìm kiếm âm) — (khoảng trống)
- F070 Kernel/cửa sổ đối xứng/prominence không causal; chỉ dùng ex post — `nguồn-báo-cáo` — S019, S032
- F071 Pivot causal chỉ biết sau `right` bar; ZigZag-kiểu cực trị đang chạy vẽ lại đoạn cuối (TradingView ZigZag: "not final") — `nguồn-báo-cáo` ✔ — S025, S026, S030
- F072 Luật hoà không thống nhất (doc ZigZag ">" trước, ">=" sau; PineTS mô tả `ta.pivothigh` ngược lại); nến bao ngoài không có chuẩn — `nguồn-báo-cáo` — S025, S027, S028, S030
- F073 smartmoneyconcepts `swing_highs_lows` có lookahead, lệch 1 bar; PF 7,32 so với 1,82 là số tự đo một người dùng — `nguồn-báo-cáo` (số: yếu) — S028
- F074 Pine: script dựa pivot "potentially misleading"; khung lớn cần `[1]` + `lookahead_on` — `nguồn-báo-cáo` — S026
- F075 Không có bằng chứng chặt chẽ cho swing stop > ATR stop sau chi phí (cũng không có bằng chứng ngược trực tiếp); ablation công khai duy nhất không có kiểu swing — `suy-luận` + `nguồn-báo-cáo` (chất lượng thấp) — S040
- F076 Học thuật về stop-loss không thống nhất, khác tài sản/tần suất so với hệ — `nguồn-báo-cáo` — S033–S036
- F077 Stop % chặt (3–5%) Sharpe thấp hơn nhiều so với 10–12% trên S&P 500 hàng ngày; "a change of trend is the best stop loss" — `nguồn-báo-cáo` ✔ — S035
- F078 Stop theo vol Sharpe 0,535 so với 0,399 của stop cố định 15% (momentum cổ phiếu tháng) — `nguồn-báo-cáo` — S034
- F079 Stop-loss tụ ngay sau số tròn (buy: 14,3% [01,10] so với 6,9% [90,99]); take-profit tụ đúng tại mức — `nguồn-báo-cáo` ✔ — S037
- F080 Osler không chứng minh nhân quả; bằng chứng về số tròn FX majors thập niên 1990, không phải swing; ngoại suy sang chỉ số/vàng/crypto M10–H4 chưa kiểm — `nguồn-báo-cáo` ✔ + `suy-luận` — S037
- F081 Sách cổ điển/practitioner đặt stop dưới swing kèm đệm và cảnh báo bị săn; không có nguồn có tên cho "đệm bao nhiêu ATR" — `nguồn-báo-cáo` — S014, S017, S038, S039
- F082 Turtle: thị trường biến động → stop rộng hơn nhưng ít hợp đồng hơn; stop ½N → nhiều lệnh thua hơn — `nguồn-báo-cáo` — S038
- F083 Hiệu chỉnh bằng backtest dễ overfit → so với bảng KSL×ATR cần walk-forward — `nguồn-báo-cáo` + `suy-luận` — S041
- F084 Chuẩn hoá ngưỡng đảo chiều theo ATR hợp lý về nguyên lý, chưa có kiểm chứng trực tiếp — `suy-luận` — S020, S017
- F085 Cách Dow cũ của hệ (trái 3 chặt / phải 5 không chặt, xen kẽ, loại đảo chiều <0,5×ATR14) không có tham số nào có nguồn cổ điển; luật hoà khớp doc ZigZag TradingView — `suy-luận` — T002
- F086 Stop cách cũ cách entry trung vị 3,51 ATR(5) (p90 5,54); 12–16% xa hơn 5 ATR; 1/489 không có stop — `tái-lập` — T002
- F087 Tuổi pivot được dùng trung vị 12 nến (p90 18) — `tái-lập` — T002
- F088 7,8% (38/488) tín hiệu có mức stop đã bị giá chạm/xuyên trước nến tín hiệu (nguyên nhân dự kiến: chỉ lấy swing cuối) — `tái-lập` + `suy-luận` — T002

**Chuẩn frontend và UI/UX cho dashboard `signal_display`** (ghi chú: topic_2026_dashboard-frontend-ux-standards.md)
- F089 INP tốt ≤ 200 ms, cần cải thiện 200–500, kém > 500 (phân vị 75); 3 pha input delay/processing/presentation delay; chỉ tính click/tap/phím, KHÔNG tính hover/zoom/scroll — `nguồn-báo-cáo` ✔ — S042
- F090 Tác vụ dài = > 50 ms; xử lý bằng cách nhường luồng chính (`scheduler.yield`, dự phòng `setTimeout` bọc Promise); không còn khuyến nghị `isInputPending` — `nguồn-báo-cáo` ✔ — S043
- F091 Tối ưu INP: làm tối thiểu trong trình xử lý sự kiện, nhường luồng, tránh layout thrashing, DOM nhỏ, hoãn việc không gấp bằng `requestAnimationFrame`, `content-visibility` — `nguồn-báo-cáo` ✔ — S044
- F092 60 fps = 16,7 ms cho script + style + layout + paint; `transform`/`opacity` rẻ hơn thuộc tính đổi hình học — `nguồn-báo-cáo` ✔ — S045
- F093 Nielsen: 0,1 s tức thì, 1 s giữ mạch nghĩ, 10 s giữ chú ý; > 1 s phải báo hệ thống đang làm việc; > 10 s cần chỉ báo phần trăm và cách ngắt — `nguồn-báo-cáo` ✔ — S046
- F094 Doherty: phản hồi trong 400 ms; perceived performance (hoạt ảnh, thanh tiến trình) làm chờ dễ chịu hơn — `nguồn-báo-cáo` ✔ (trang tổng hợp) — S049
- F095 Spinner 2–10 s (một module), skeleton cho tải cả màn hình dưới 10 s, dưới 1 s không cần, trên 10 s thanh tiến trình; skeleton không thay tối ưu hiệu năng — `nguồn-báo-cáo` ✔ — S047
- F096 Heuristic NN/g #1 visibility of system status, #3 user control and freedom, #7 flexibility and efficiency, #9 lỗi bằng ngôn ngữ thường — `nguồn-báo-cáo` ✔ (đoạn đầu) — S048
- F097 Mẫu tải lịch sử chính thức của Lightweight Charts: `subscribeVisibleLogicalRangeChange`, ngưỡng `from < 10`, nạp `50 − from` nến, gộp bằng `setData`; demo KHÔNG có cờ chống tải chồng — `nguồn-báo-cáo` ✔ — S050
- F098 `autoSize: true` dùng ResizeObserver trên container (không theo cửa sổ), bỏ qua width/height; vendor v4.2.1 có `autoSize` và `ResizeObserver` — `nguồn-báo-cáo` ✔ + `tái-lập` (grep) — S051
- F099 Tuỳ chọn trục thời gian dùng được ở v4.2.1: fixLeftEdge/fixRightEdge/rightOffset/shiftVisibleRangeOnNewBar/lockVisibleTimeRangeOnResize/rightBarStaysOnScroll/minBarSpacing; `enableConflation` có trong docs mới nhưng KHÔNG có trong vendor v4.2.1 — `nguồn-báo-cáo` ✔ + `tái-lập` (grep) — S051
- F100 `AbortController.abort()` huỷ fetch/đọc body/stream; lý do mặc định `AbortError` (MDN không viết rõ fetch reject bằng lỗi này: kiến thức chung) — `nguồn-báo-cáo` ✔ — S052
- F101 `aria-live="polite"` cho cập nhật quan trọng không dồn dập; `assertive` chỉ khẩn cấp; `role=status`/`alert`; vùng live phải có sẵn trong DOM trước khi đổi nội dung — `nguồn-báo-cáo` ✔ — S053
- F102 WCAG 1.4.3 chữ ≥ 4,5:1 (lớn ≥ 3:1); 1.4.11 thành phần giao diện và đồ hoạ ≥ 3:1 — `nguồn-báo-cáo` ✔ (qua MDN) — S054
- F103 WCAG 2.5.8 mục tiêu ≥ 24×24 px (AA, có ngoại lệ); 2.1.1 mọi chức năng dùng được bằng bàn phím; 2.4.7 vòng focus nhìn thấy — `nguồn-báo-cáo` ✔ (qua MDN) — S055
- F104 WCAG 4.1.2 tên/vai trò xác định được; 4.1.3 thông báo trạng thái phải đến được công nghệ hỗ trợ (chi tiết kỹ thuật W3C không đọc được) — `nguồn-báo-cáo` ✔ (qua MDN) — S056
- F105 Chartability: POUR + CAF; "50 test, 14 critical" chỉ từ nguồn thứ cấp — `nguồn-báo-cáo` — S057
- F106 Google style guide: tách cấu trúc/trình bày/hành vi; HTML đúng mục đích; `<!doctype html>`, charset utf-8 — `nguồn-báo-cáo` ✔ — S058
- F107 Dashboard hiện tại: rê chuột/zoom/kéo chart giữ 60 fps (khoảng giữa khung 16,7 ms, 0 khung > 50 ms, 0 tác vụ dài; luồng chính bận 19–38% tắt trend, 24–53% bật) — `tái-lập` — T003
- F108 Dashboard hiện tại: `/api/scan` 500 nến 0,21–0,39 s (tắt trend) / 0,34–0,45 s (bật), 2000 nến 0,64–0,96 / 0,85–1,09 s, 20000 nến 5,8–7,5 / 7,5–9,5 s; dựng payload bằng vòng lặp Python chiếm ~64%/82%/90% ở 500/2000/20000 nến; JS trình duyệt sau khi nhận 4–21 ms; bật trend thêm ~47–130 ms (KNN tính hai lần) — `tái-lập` — T003
- F109 Dashboard hiện tại: mỗi lần Refresh bật trend bắn 2–3 request (tự tải thêm lịch sử), ổn định sau ~1,0–1,2 s; tắt trend 1 request (2 ở 300 nến); cơ chế chưa kiểm chứng — `tái-lập` + `suy-luận` — T003
- F110 Dashboard hiện tại: số "raw → filtered" sai sau khi gộp lịch sử (`stats` không được gộp; ví dụ 70,5% so với đúng 31,6%, có lần 136,4%) — `tái-lập` — T003
- F111 Dashboard hiện tại, kiểm a11y: 11/29 tổ hợp màu dưới ngưỡng (10 chữ gợi ý 3,17:1; nút Export 3,30:1); không có viewport meta/`<main>`/heading/vùng live/mô tả cho 14 canvas; chart không thao tác được bằng bàn phím; mọi điều khiển có tên truy cập được; thứ tự Tab hợp lý — `tái-lập` — T003
- F112 Chrome headless ở máy này trễ ~25 s ở lần điều hướng đầu (cả trang tối giản) — không phải lỗi dashboard — `tái-lập` — T003
- F113 INP đo đến lần vẽ kế tiếp, không chờ dữ liệu bất đồng bộ → vấn đề của dashboard là độ trễ cảm nhận (không có chỉ báo "đang tải", vượt 1 s khi bật trend), không phải INP — `suy-luận` — S042, S046, S049, T003
- F114 Gói C, đầu ra không đổi: vector hoá 4 hàm `payload.py` giống hệt bản `iterrows` — fuzz 16.500 phép gọi 0 khác, 292 ca dữ liệu lưu 0 khác, 24 ca SQL sống giống từng byte phản hồi HTTP; khác duy nhất: cột OHLC object chứa None (cũ ném TypeError, mới trả NaN) — `tái-lập` — T004
- F115 Gói C, thời gian `/api/scan` trước → sau (SQL sống, trung vị 3 chiến lược × trend): 500 nến 334 → 167 ms, 2.000 nến 949 → 263 ms, 5.000 nến 2.092 → 428 ms, 20.000 nến 7.867 → 1.066 ms; riêng dựng payload 180 → 14, 716 → 30, 1.829 → 64, 7.012 → 200 ms — `tái-lập` — T004
- F116 Gói C, gzip mức 5: JSON 95 → 16 KB (500 nến), 486 → 81 KB (2.000, trend), 4,9 MB → 0,78 MB (20.000); trang chủ 194 → 59 KB; CPU nén 1,3 / 7,4 / 74 ms; mức 9 chậm gấp 4–5 lần chỉ nhỉnh ~3%; cùng máy không thấy khác biệt, chỉ có lợi qua mạng/đường hầm — `tái-lập` — T004
- F117 `round()` của Python khác `np.round` ở ca sát nửa (2.675 → 2.67 so với 2.68): vector hoá phải giữ `round()` trên từng giá trị — `tái-lập` — T004
- F118 Màu histogram/trend phải xét trên giá trị ĐÃ làm tròn (−0,004 → −0,0 vẫn xanh; 0,999 → hướng 1; NaN ra xám); tô theo giá trị thô là lỗi — `tái-lập` — T004
- F119 Chưa làm trong Gói C: KNN tính hai lần (≤ 13 ms, cần sửa backend), số raw → filtered sai sau khi gộp lịch sử (F110), các mục frontend §5 — `tái-lập` + `suy-luận` — T004

**Khối xác nhận xu hướng MS Fractal (TradingView) và khung trend của KNN** (ghi chú: topic_2026_ms-fractal-trading-range-trend.md)
- F120 Nguồn: script TradingView `MS_TradingRange_WilliamsFractal` (hphuc0212) đóng nguồn và không có mô tả; ảnh xem trước là Vàng H1 độ dài 16 (tức 16 giờ mỗi bên); LuxAlgo (S061) chỉ nêu khái niệm CHoCH/BOS dựa fractal, `smartmoneyconcepts` (S062) dùng nến sau pivot nên có nhìn trước nếu dùng đúng tại nến swing — `nguồn-báo-cáo` — S059, S061, S062
- F121 Thuật toán (đọc từ mã người dùng dán): pivot cao/thấp theo `length` nến mỗi bên → zigzag xen kẽ (cùng loại mà xa hơn thì kéo dài đoạn) → "sóng chính" bị thay khi một đầu đoạn mới có giá đóng cửa vượt phạm vi sóng chính cũ → mỗi nến: đóng cửa vượt đỉnh (thủng đáy) sóng chính và nến thật sự cắt ngang mức đó thì ghi BOS (cùng hướng sóng chính) hoặc CHOCH (ngược), mỗi phía một lần cho mỗi sóng chính; mã chính KHÔNG định nghĩa "hướng xu hướng" (phải suy ra) — `nguồn-báo-cáo` (đọc mã) — S060
- F122 Bản Python tái tạo khớp ảnh tác giả (Vàng H1, độ dài 16, 36 điểm xoay, nhãn CHOCH ở 4.450 ngày 31/08, các đường BOS) khi so bằng mắt; chưa so từng nhãn với TradingView thật — `tái-lập` (yếu) — T005
- F123 Bản port không nhìn trước: chạy trên tiền tố [:c] cho đúng kết quả của lần chạy toàn bộ rồi cắt, 792/792 phép so (3 symbol × 2 độ dài × 12 điểm cắt × 11 mảng) — `tái-lập` — T005
- F124 Điều kiện "nến phải cắt ngang" bỏ sót ~1% số sóng chính có cú phá (gap): H4 độ dài 10 → 27/1.854, 16 → 13/1.160; giá bằng nhau trong cửa sổ fractal chỉ ~1,4–1,6% pivot nên quy tắc xử lý giá bằng nhau ít ảnh hưởng — `tái-lập` — T005
- F125 Nhịp xu hướng (hướng = lần phá gần nhất, 11 symbol, 2024-10 → 2026-10): H1 độ dài 4/8/16/24 → 65/38/24/16 lần đổi hướng/năm, pha trung vị 2,6/4,3/7,4/10,2 ngày; H4 độ dài 3/4/5/8/10/16 → 23/20/16/11/8,7/5,6 lần/năm, pha trung vị 7,2/8,3/10,1/16/19/27 ngày; KNN H4 148 lần/năm, pha trung vị 1,2 ngày (40% pha < 1 ngày). Độ dài 16 là số của tác giả trên H1 (16 giờ mỗi bên) ≈ độ dài 3–4 trên H4 — `tái-lập` + `suy-luận` — T005, T006, S059
- F126 Với thứ tự lọc trend trước, alternating sau: mỗi pha xu hướng tối đa 1 tín hiệu (1.115 tín hiệu `combo` ở 1.115 pha khác nhau, 2.171 `macd_break` ở 2.171 pha); tín hiệu còn lại so với DB0 (combo / macd_break M10–M45 / macd_break H1–H4): H4 độ dài 16 → 3,1% / 4,5% / 25%; H4 độ dài 5 → 7,1% / 12% / 59%; H1 độ dài 16 → 8,8% / 18% / 71%; H1 độ dài 4 → 11,9% / 40% / 89%; KNN H4 → 13,5% / 57% / 98%; số gộp "macd_break" bị kéo bởi M10–M45 (~86% setup) — `tái-lập` — T005, T006
- F127 Độ lệch khi chỉ tính đoạn ngắn (tỉ lệ nến có hướng khác bản tính từ đầu): worker (warm-up 300 nến khung trend) H4/5 1,2%, H4/16 10,6% (4,7% ở 600 nến), H1/16 6,5% (1,6% ở 600, 0,5% ở 1000), H1/4 1,0% (0,15% ở 600); des (500 nến) H4/5 0,7%, H4/16 6,5%, H1/16 3,2%, H1/4 0,3% — độ dài càng lớn càng cần nhiều "trí nhớ" — `tái-lập` — T005
- F128 Thử dự báo (lợi suất tương lai theo ATR14, bootstrap theo quý/tuần): không độ dài MS Fractal nào hơn KNN/EMA50/Donchian-20 một cách ổn định; CHOCH lên ở độ dài 16 dương nhưng không nhất quán giữa độ dài/giai đoạn; người dùng không cần phép đo này → không dùng để quyết định — `tái-lập` (yếu) — T005
- F129 KNN đổi hướng/năm theo khung trend (2 năm gần nhất, 11 symbol): H4 148 (pha trung vị 1,17 ngày), H3 196 (0,88), H2 297 (0,58), H1 582 (0,29) — `tái-lập` — T006
- F130 Số tín hiệu DB1 (KNN, trend trước alternating sau; "DB0" = đã qua alternating, chưa lọc trend; 132 cặp, từ 2022-01-01, dữ liệu cắt 2026-10-14; cột H4 và DB0 khớp bản chụp trước 132/132): combo DB0 35.807 → H4 4.835 / H3 5.710 (+18%) / H2 7.379 (+53%) / H1 11.997 (+148%); macd_break M10–M45 DB0 25.655 → 14.730 / 17.115 (+16%) / 20.114 (+37%) / 23.667 (+61%); macd_break H1–H4 DB0 4.108 → ~4.008–4.050 (+1%) — `tái-lập` — T006
- F131 Khung trend ≠ khung vào lệnh: trend nhỏ hơn khung vào lệnh → giữ nhiều; trend lớn hơn → giữ ít (nguyên nhân chính ở bước lọc trend, xem F132; "DB0" ở đây đã qua alternating, chưa lọc trend). combo, % của DB0 khung vào lệnh (trend H1/H2/H3/H4): vào H1 21,3/10,8/12,8/13,3; H2 40,9/20,7/11,4/9,9; H3 47,0/33,7/21,0/13,4; H4 48,4/41,1/30,7/21,3. Cặp đối xứng 11 symbol: vào H3 + trend H2 = 1.956 so với vào H2 + trend H3 = 976 (2,0×, 11/11 symbol; US30 155 so với 93); vào H4 + trend H2 so với vào H2 + trend H4 = 2,11×; H4+H3 so với H3+H4 = 1,73×; H2+H1 so với H1+H2 = 1,91×; H3+H1 so với H1+H3 = 1,26×. macd_break H1–H4 gần như không phụ thuộc khung trend (≥ 96% lọt). Chỉ là số lượng, chưa có đánh giá lãi/lỗ; cấu hình hiện chỉ cho MỘT `default_trend_tf` cho mỗi chiến lược — `tái-lập` (số) + `suy-luận` (cơ chế) — T006
- F132 Tách lớp lọc của `combo` (11 symbol, cùng điều kiện F130): setup thô 53.866 → chỉ alternating (= DB0) 35.807 (giữ 66,5%) → thêm trend (trend trước, alternating sau) từng ô khác nhau; số ở F130/F131 đều là `signal` cuối đã qua cả hai bộ lọc. Bất đối xứng khi ghép khung nằm chủ yếu ở bước trend: vào H3 + trend H2 giữ 42% setup thô, vào H2 + trend H3 giữ 15% (alternating sau đó giữ 54% và 49%, gần nhau; 11/11 symbol; US30 763/1.163 → 303/188 → 155/93); trend cùng khung vào lệnh giữ gần hằng số 27–28% setup thô ở cả 4 khung; alternating giữ ~45–59% số setup đã qua trend (vào H1: 31–51%). Cơ chế "alternating nạp lại" tôi từng ghi KHÔNG phải nguồn chính (với `macd_break` xem F133); cơ chế đúng chưa kiểm — `tái-lập` (số) + `suy-luận` (cơ chế) — T006
- F133 Tách lớp của `macd_break` (11 symbol, cùng điều kiện F132): setup thô 164.167 / 80.517 / 54.041 / 36.954 / 27.916 / 15.070 / 10.340 / 7.751 (vào M10/M20/M30/M45/H1/H2/H3/H4) → chỉ alternating 13.002 / 5.993 / 4.027 / 2.633 / 1.893 / 1.004 / 681 / 530 (giữ 6,6–7,9%); bước trend qua được 93–97% setup thô khi vào M30–H4 (H3/H4 với trend H1: 92,5% / 90,0%), riêng M10 chỉ 93% / 82% / 72% / 66% và M20 96% / 95% / 91% / 84% (trend H1…H4); alternating giữ 4–8% số setup đã qua trend, gần như không đổi theo khung trend. Nên số tín hiệu cuối H1–H4 chênh ≤ 4% giữa các khung trend; lời giải thích "setup thưa" trước đó là SAI (lý do: setup phá đỉnh/đáy 100 nến gần như luôn cùng hướng KNN — `suy-luận`) — `tái-lập` — T006
- F134 Tỷ lệ giữ lại theo từng mốc "không lọc" (`combo`, 11 symbol, đo lại từng symbol, cộng khớp F132 100%): ba mốc — (a) không lọc gì 53.866; (b) chỉ chống trùng (= DB0) 35.807 = 66,5% của (a); (c) trend + chống trùng (= DB1). Gộp 4 khung vào lệnh, so với (a): trend H1 22,3%, H2 13,7%, H3 10,6%, H4 9,0%; chỉ trend (tắt chống trùng): 40,1% / 26,7% / 23,9% / 23,5%. Hai bộ lọc không nhân với nhau (H4: 23,5% × 66,5% = 15,6% nhưng đo 9,0%). Dao động giữa 11 symbol hẹp (trend H4: 8,6–9,6%). Số so với DB0 chính là F131 — `tái-lập` — T006

**`ma_cross` theo luật trạng thái (đã triển khai và áp dụng ở `we_core` 16/10; `des` chưa)** (ghi chú: topic_2026_ma-cross-state-rule.md)
- F135 Bộ đo hợp lệ: luật `ma_cross` cũ dựng lại nguyên văn cho DB0 103.956 (Redis 104.054), DB1 trend H4 61.342 (61.402), cặp liền nhau cùng chiều 16.806 / 39.575 (16.829 / 39.615) — lệch ≤ 0,1% do mốc dữ liệu; lần đo đầu có lỗi khoá cache KNN theo id() (lệch tới 2,8%), đã sửa và đo lại — `tái-lập` — T007
- F136 Luật đề xuất (BUY: SMA13 > SMA34 và MACD hist > 0; SELL ngược lại): setup thô 1.581.660 = 42% số nến (44 cặp M10–M45); chỉ trend (chưa chống trùng) vẫn 23–32% số nến → chống trùng bắt buộc — `tái-lập` — T007
- F137 Số tín hiệu trend trước + chống trùng sau (11 symbol, M10–M45; trong ngoặc: luật cũ): DB0 110.813 (103.956, +7%); trend H1 72.421 (86.384, −16%), H2 45.631 (72.607, −37%), H3 31.603 (65.315, −52%), H4 23.863 (61.342, −61%); mỗi năm mỗi cặp: 527 / 344 / 217 / 150 / 113 (cấu hình cũ: luật cũ + H4 = 291) — `tái-lập` — T007
- F138 Tín hiệu bị chặn trên bởi số lần trend đổi hướng (≤ lần đổi + 1 ở 176/176; bằng 60% / 75% / 78% / 78% số lần đổi hướng với trend H1 / H2 / H3 / H4); 0 cặp liền nhau cùng chiều; BUY 50%; với trend lớn số tín hiệu gần như không phụ thuộc khung vào lệnh — `tái-lập` — T007
- F139 Trí nhớ chống trùng: 0 khoảng cùng chiều giữa hai setup đã qua bộ lọc dài hơn 300 nến (warm-up worker), lớn nhất 244 (M10, trend H4), 177 (M10, trend H2); cửa sổ 500 nến của des đủ — `tái-lập` — T007
- F140 Sau khi triển khai (16/10): mã sản phẩm `ma_cross` khớp số đo 220/220 phép (44 cặp × DB0 + 4 khung trend) và các bất biến; dashboard + export CSV chạy đúng với khung trend mặc định H2 — `tái-lập` — T007
- F141 Sau restart `we-worker` 16/10 02:32:46 UTC (bootstrap 349,8 s, 352/352 ghi, 0 lỗi): Redis khớp tham chiếu SQL từng dòng ở DB0 `ma_cross` 44/44, DB1 `ma_cross` (H2) 44/44, DB1 `combo` (H1) 44/44; DB0 `combo` và DB0/DB1 `macd_break` giống hệt trước restart; số dòng DB0 `ma_cross` +6,6%, DB1 `ma_cross` −25,6%, DB1 `combo` +148%; 0 cặp liền nhau cùng chiều — `tái-lập` — T007
- F142 Quy ước ghép trend của `merge_trend_reference` (trend đóng ≤ giờ MỞ nến vào lệnh) là tham số ẩn nhạy, nhất là ở `combo` khi khung trend gần khung vào lệnh: đổi sang ≤ giờ ĐÓNG thì DB1 `combo` (trend H1) +141% (vào H1/H2/H3/H4: +211%/+123%/+103%/+101%; đường chéo ma trận +210–227%), nhưng `ma_cross` (trend H2) chỉ +4% và `macd_break` (trend H4) +3%; hướng trend khác ở 9,9–28,1% số nến (combo H1) so với 0,4–9,9% (hai chiến lược kia); cơ chế: nến mạnh tạo setup cũng đổi hướng KNN chính nến đó (tự xác nhận); cùng quy ước trong order_gateway live cũ (git) và des, DE hiện tại chưa kiểm; mọi số F129–F134 điều kiện theo quy ước MỞ; các số +141% / +4% / +3% đo lúc mã chạy cách A, đo lại theo cách B (16/10): combo +120%, ma_cross +8%, macd_break +5%, đường chéo +242–258% (F142b) — `tái-lập` + `suy-luận` (cơ chế) — T006
- F143 So thứ tự lọc trên cấu hình production (A = trend rồi chống trùng dùng 15–16/10, B = chống trùng rồi trend: cũ trước 15/10 và đã ĐỔI LẠI từ 16/10; trend MỞ; 11 symbol từ 2022): số tín hiệu combo A 11.997 / B 13.325, ma_cross A 45.631 / B 42.931, macd_break A 18.738 / B 14.162; tập trùng nhau chỉ 76% / 30% / 55% của A; cặp liền nhau cùng chiều A = 0, B = 36% / 57% / 32% số cặp; B ⊆ DB0 (0% ngoài DB0) còn A có 23,8% / 69,6% / 45,2% ngoài DB0; B bỏ cả đợt khi nến đầu lệch trend (A bắn lại ở 2.860 / 31.732 / 8.467 đợt); khoảng nhớ >300 nến: A 6 / 0 / 104, B 0 / 0 / 0 — `tái-lập` — T006
- F144 "Vào lệnh đúng lúc trend xuất hiện": phân loại tín hiệu DB1 theo quan hệ setup/trend ở nến trước nến bắn — cách A (trend rồi chống trùng) có loại "trend mới xuất hiện khi setup đang có" 0,9% / 49,1% / 6,7% (combo / ma_cross / macd_break), cách B (chống trùng rồi trend) = 0 vì quyết định ngay ở nến setup đầu tiên của đợt; độ trễ loại này trung vị 1 / 3 / 1 nến; người dùng chọn cách B 16/10 — `tái-lập` — T006
- F145 "Trí nhớ" thực của đường delta ngắn hơn 300 nến: chỉ báo hợp lệ từ nến 19 / 33 / 100 nên warm-up 300 nến chỉ nhớ 281 / 267 / 200 nến (combo / ma_cross / macd_break); delta lệch bản toàn lịch sử đúng khi khoảng giữa hai setup thô cùng chiều > 200 nến ở macd_break (34/34 DB0, 25/34 DB1; khoảng ≤ 200: 0/311 lệch; 704 cửa sổ ngẫu nhiên 3 ngày: 0 lệch); số khoảng vượt bộ nhớ thực: cách B 34, cách A 655 + 10 (combo); sửa khẳng định cũ "288 < 300 nên rủi ro = 0" — `tái-lập` — T006
- F146 Quy tắc ghép trend "nến tham chiếu mới nhất ĐÃ CÓ lúc nến tín hiệu chốt" (khung tham chiếu nhỏ hơn: lấy cả nến đóng cùng lúc; lớn hơn/bằng: nến đã đóng trước đó): giống hệt mã hiện tại khi khung lớn chia đều khung tín hiệu; khác ở combo vào H2/H3/H4 với tham chiếu H1 (DB1 13.325 → 21.253, +59,5%) và ma_cross/macd_break chỉ ở M45/H3 (+2,2% / +0,6%); mã hiện tại bỏ qua mọi nến tham chiếu nằm trong lòng nến tín hiệu (trễ một nến tín hiệu); CHỈ ĐO, chưa đổi mã, khung bằng nhau là giả định — `tái-lập` — T006
- F147 Kiểm look-ahead của ghép trend bằng cắt dữ liệu tại giờ đóng nến tín hiệu rồi chạy lại: quy tắc hiện tại 0 lệch / 165 nến mẫu, quy tắc F146 (nến tham chiếu mới nhất đã có lúc chốt) 0 lệch, đối chứng âm cố tình nhìn trước 13 lệch — không có look-ahead ở cả hai; điều không bắt được là thứ tự xử lý cùng thời điểm khi live; tham chiếu nhỏ hơn thì trend trùng thông tin một phần với nến tín hiệu (không phải nhìn trước) — `tái-lập` — T006
- F148 Khung tham chiếu BẰNG khung tín hiệu, lấy CHÍNH nến tín hiệu thay vì nến trước: combo H1/H1 qua lọc trend 19,4% → 67,1% số tín hiệu (3.303 → 11.424, gấp 3,46; H2/H3/H4 gấp 3,4–3,6), 71% tín hiệu thêm là nến trend đổi hướng ngay trên nến tín hiệu; macd_break H4/H4 chỉ +8%; không phải look-ahead nhưng trend không còn "có trước"; chất lượng chưa đo — `tái-lập` — T006
- F149 Tỷ lệ tín hiệu còn lại so với DB0 (không lọc trend) theo quy tắc ghép: hiện tại combo 37% / ma_cross 39% / macd_break 48%; theo quy tắc F146 combo 59% (vào H2/H3/H4 với tham chiếu H1: 48/57/60% → 93/97/98%, tức bộ lọc trend H1 gần như vô hiệu vì nến H1 cuối nằm trong nến tín hiệu), ma_cross 40%, macd_break 48%; lấy chính nến cả khi bằng nhau: combo 82% — `tái-lập` — T006
- F150 Phương án khung trend theo quy tắc ≥ khung tín hiệu: combo hiện tại 13.325 (vi phạm) vs mặc định H3 7.706 (21,5% DB0) vs H4 8.720 (24%) vs trend = khung vào lệnh 6.942 (19%); chế độ trend = khung vào lệnh gần như không lọc ở ma_cross (83% DB0, 92.247 tín hiệu) và macd_break (91,5%, 27.243) nhưng lọc mạnh ở combo (19%); KNN đổi hướng ~10% số nến mọi khung — `tái-lập` (số), `suy-luận` (cơ chế) — T006
- F151 Triển khai quy tắc khung trend ≥ khung tín hiệu + combo mặc định H4, kiểm chứng: 176 cặp trước/sau — 132 cặp ma_cross/macd_break giống hệt từng cột, combo DB0 giống hệt, combo DB1 13.325 → 8.720 đúng phép đo ma trận; Chrome thật 18/18 cho ô Trend TF; dashboard giữ lựa chọn khung trend khi đổi chiến lược (có từ trước) — `tái-lập` — T006
- F152 Số tín hiệu combo H3/H4 hiện tại (Redis live, 11 symbol, 4,79 năm): H3 DB0 5.822 → DB1 trend H4 694 (≈ 12/tháng cho cả 11 symbol, ≈ 1,1/symbol-tháng; trend H3 cho 1.121), H4 DB0 4.388 → DB1 trend H4 868 (≈ 15/tháng, ≈ 1,4/symbol-tháng); đều đặn theo năm; khớp SQL 693 / 866 — `tái-lập` — T006
- F153 combo × 11 symbol × H1–H4 chưa lọc trend, từ 2024-01-01 tới 16/10/2026: 21.053 tín hiệu (H1 10.009 / H2 5.060 / H3 3.432 / H4 2.552; BUY ≈ SELL), ≈ 629/tháng cho cả 44 cặp, ≈ 27 / 14 / 9 / 7 mỗi symbol-tháng theo H1…H4; SP35 ít nhất (941), BTCUSD nhiều nhất; khớp Redis DB0 44/44 — `tái-lập` — T006
- F154 combo: tỷ lệ còn lại theo khung trend được phép × khung vào × symbol (2024→16/10/2026): vào H1 trend H4 giữ 34% (3.380) > H3 27% > H1 20% > H2 16%; vào H2 tốt nhất trend H2 (19%), vào H3 trend H3 (20%), vào H4 trend H4 (19%); khung trend tốt nhất gần như giống nhau ở mọi symbol nên chọn riêng theo symbol chỉ hơn trend H4 chung +8% (5.554 so với 5.123); BTCUSD nhiều nhất, SP35 ít nhất; chọn theo số lượng, chưa theo chất lượng — `tái-lập` — T006
- F155 Kiểm lại BTCUSD bằng cách tính độc lập (Redis DB0 + hướng KNN + ghép bisect): khớp bảng F154 14/14 dòng; vào H1 không lọc 1.423 → trend H4 485 (34%) / H3 392 (28%) / H1 273 (19%) / H2 253 (18%); số theo năm đều, BUY/SELL cân bằng; H2 thấp hơn H1 là mẫu chung của cả 11 symbol — `tái-lập` — T006
- F156 Bảng khung trend theo (chiến lược, symbol, khung vào) chọn theo SỐ tín hiệu nhiều nhất (H1–H4 ≥ khung vào, 2024-01-01 → 16/10/2026, cách B, KNN): combo 5.123 (H4 chung, 24,3% DB0 21.059) → 5.554 (26,4%, +8%) với H1→H4, H2→H2 (FR40/J225→H4), H3→H3, H4→H4; ma_cross 25.109 (H2 chung, 38,5% DB0 65.203) → 37.413 (57,4%, +49%) với M10→H4 và M20/M30/M45→H1 giống nhau ở cả 11 symbol; macd_break 8.333 (H4 chung, 47,5% DB0 17.561) → 14.118 (80,4%, +69%); 52/176 ô chênh < 5% giữa khung nhất và nhì; chọn khung lọc YẾU nhất, chưa có đánh giá lãi/lỗi, chọn và đo cùng đoạn dữ liệu — `tái-lập` — T008
- F157 Đưa bảng vào config, chụp 176 cặp kiểu worker (SQL SELECT): khung trend tra ra == bảng 176/176; DB0 giống hệt bản trước 176/176; DB1 của 48 ô giữ khung cũ giống hệt 48/48 (128 ô đổi); bất biến signal ⊆ raw_signal / cùng chiều trend ở 176/176; số tín hiệu từ 2024 == F156 176/176; Chrome thật 26/26; test we_core 271 → 281 — `tái-lập` — T008
- F158 Tác động lên Redis khi áp dụng (cùng điểm cắt 14/10, từ 2022, đo TRƯỚC restart; đã áp dụng ở F159): DB1 combo 8.720 → 9.407 (+7,9%; 26,3% DB0), ma_cross 42.931 → 63.675 (+48,3%; 57,5% DB0), macd_break 14.162 → 23.782 (+67,9%; 79,9% DB0); DB0/schema/tên key không đổi; tên key không chứa khung trend, chỉ log `pair_refreshed` ghi khung từng cặp — `tái-lập` — T008
- F159 Sau restart `we-worker` 16/10 12:01:16 UTC (bootstrap 360,5 s, 352/352 ghi, 0 lỗi): DB0 giống hệt trước; DB0 + DB1 == tham chiếu SQL 352/352 LIST và == bản chụp kiểu worker 352/352; khung trend trong log == bảng 176/176; DB1 65.956 → 97.057 dòng (+47,2%); tỷ lệ DB1/DB0 trước → sau: combo 24,4% → 26,3%, ma_cross 38,7% → 57,5%, macd_break 47,6% → 79,9% (ô gần như không lọc: ma_cross M30/M45 85%, macd_break M20+ 86–95%); chưa quan sát đường delta với bảng mới — `tái-lập` — T008
- F160 Đo THẬT lỗi "DB1 cũ sau nến trend mới": DF báo theo đợt 6 lần/ngày (03:03, 07:07, 11:11, 15:15, 19:19, 23:23 UTC; 79–88 trigger/đợt); đợt 11:11 16/10 (cấu hình cũ): Redis DB1 == tính đúng từ SQL ở 176/176 cặp, 0 dòng sai, DB0 lệch 0; mô hình xấu nhất dự đoán 4 (cũ) / 1 (bảng mới) dòng sai nhưng thực tế 0 — mô hình quá bi quan, không có bằng chứng bảng mới làm tăng lỗi; mẫu nhỏ (1 đợt, 13 tín hiệu DB1 trong cửa sổ) — `tái-lập` — T008
- F161 Tài liệu Lightweight Charts: `update` thêm nến mới hoặc sửa nến cuối (`historicalUpdate` chậm hơn), `setData` thay toàn bộ; demo chính thức `setData` một lần rồi `update` từng tick; không tìm được benchmark nào so hai cách (§7.2).
- F162 Giảm mẫu (arXiv:2304.00900, biểu đồ đường): MinMax/LTTB tiết kiệm dữ liệu nhất, MinMax ổn định thị giác nhất, M4 không thể pixel-perfect khi có anti-aliasing; không nói về nến (§7.2).
- F163 SSE (MDN): 6 kết nối/miền ở HTTP/1.1, 100 luồng ở HTTP/2, tự nối lại; nguồn thứ cấp: polling trễ trung bình ≈ nửa chu kỳ, Redis Pub/Sub không bù tin mất (§7.2).
- F164 Page Visibility (MDN): dashboard không nên poll khi tab ẩn; setInterval bị giảm tần suất ở tab ẩn (§7.2).
- F165 Yêu cầu có điều kiện (ETag/304, nguồn thứ cấp) chỉ tiết kiệm payload, không tiết kiệm công tính (§7.2).
- F166 Conflation có trong docs v5.2, không có ở vendor v4.2.1; công sức nâng v5 chưa kiểm (§7.2).
- F167 Cả hai hệ cập nhật toàn bộ (`setData`/`renderChart`) mỗi lần đổi, chưa dùng `update` tăng dần; lợi ích chưa đo (§7.3).
- F168 `des` đã có dấu vân tay/`changed:false` (tương đương 304); we_core quét lại từ đầu nhưng Auto mặc định Tắt và dữ liệu SQL đổi theo đợt DF 6 lần/ngày (§7.3).
- F169 Không trang nào dùng `visibilitychange` (grep 0): tab ẩn vẫn gọi API (§7.3).
- F170 Không có `AbortController` ở cả hai; chỉ bỏ kết quả cũ (§7.3).
- F171 Giảm mẫu ít áp dụng cho nến (mất OHLC; des ≤ 1.200 nến, we_core mặc định 500) (§7.3).
- F172 SSE/WebSocket lợi nhỏ (trễ trung bình ~0,5 s ở poll 1 s) so với chi phí; nhịp nhẹ đã 4–6 ms (§7.3).
- F173 `/trend` chậm hơn trang chính (195–244 ms so với 77 ms) do đọc thêm chuỗi nến trend + KNN hai lần; chưa sửa (§7.3).

**Chiến lược ứng viên cho `we_core`** (ghi chú: topic_2026_candidate-strategies.md; chưa có số đo trên dữ liệu hệ, chưa code)
- F174 Donchian/Turtle: vào khi `high` nến hiện tại vượt đỉnh N nến TRƯỚC (không tính nến hiện tại; mã nguồn so `high`, thoát so `close`), System 1 N=20 thoát 10, System 2 N=55 thoát 20, N = ATR20 Wilder, stop 2N — `nguồn-báo-cáo` — S071, S072
- F175 Turtle: skip rule (System 1, theo hướng) cần biết lệnh trước thắng/thua, hệ không theo dõi lệnh; bài MQL5 tự mâu thuẫn ("lãi" = giá đi thuận hay P&L > 0; thêm vị thế N hay N/2); một diễn đàn ghi thoát System 2 short là 10 nến so với 20 nến của ba nguồn — `lỗi-nguồn` — S071, S072
- F176 Connors RSI(2): BUY khi `close > SMA200` và RSI(2, Wilder) < 10 (chặt: < 5), SELL đối xứng (> 90 / > 95), thoát khi `close > SMA5`; không stop; ngưỡng 10 hay 5 tuỳ nguồn (nguồn nói < 5 lãi hơn < 10 trong thử nghiệm của Connors) — `nguồn-báo-cáo` (thứ cấp, sách chưa đọc) — S073, S074
- F177 Connors RSI(2): thiết kế cho cổ phiếu/chỉ số ngày; backtest của một tác giả trên Nasdaq 100 2016–2026 +26,9% tổng 10 năm (≈ 2,4%/năm) so với 20,2%/năm của chỉ số; S&P 500 cũng yếu; khung M10–H4 là ngoại suy chưa kiểm — `nguồn-báo-cáo` + `suy-luận` — S073, S075
- F178 Holy Grail: ADX(14) > 30 (một nguồn: và đang tăng), pullback về EMA20 (một bản viết tay dùng SMA20), vào khi phá `high`/`low` của nến setup; hướng xu hướng theo EMA20 vs EMA50 ở một script, nguồn khác không nêu; không nguồn nào nêu thoát, thời hạn lệnh chờ, hay định nghĩa "chạm" — `nguồn-báo-cáo` (độ tin cậy thấp, sách chưa đọc) — S076, S077, S078
- F179 London breakout: khoảng phiên Á = high/low 00:00–06:00 UTC (H1), vào khi nến ĐÓNG cửa vượt/xuống dưới biên trong 07:00–10:00 UTC; EUR/USD H1 2016-10 → 2026-10: 1.230 lệnh, thắng 36,26%, PF 0,87, −28% (mua giữ +0,1%), DD −31,4%, 3/10 năm dương, stop 35 / TP 70 pip; tác giả kết luận hướng phá mang rất ít thông tin — `nguồn-báo-cáo` (một lần chạy, không lọc) — S079
- F180 London breakout: giờ London lệch theo mùa so với UTC, giờ máy chủ broker đổi; mỗi symbol có phiên khác (chỉ số cổ phiếu, vàng, BTC); bản của Backtrex nhận mọi lần phá (không "một lần/ngày"), không lọc độ rộng khoảng hay xu hướng; bằng chứng độc lập tìm được là âm — `nguồn-báo-cáo` + `suy-luận` — S079, S080
- F181 NR7: nến có biên độ nhỏ hơn mỗi nến trong 6 nến trước; lệnh chờ BUY 1 tick trên `high` / SELL 1 tick dưới `low` của nến NR7 đặt cho nến kế, lệnh khớp trước là vị thế, lệnh kia làm stop (TD Ameritrade); biến thể Crabel: `Open ± 2×SMA10(min(High−Open, Open−Low))`; biến thể Patternz: chờ đóng cửa ngoài biên rồi vào ở giá mở nến sau — `nguồn-báo-cáo` — S081, S082, S083
- F182 NR7/Crabel ORB trên 42 futures 1980–2016: sau chi phí "chưa giao dịch được nếu không có luật bổ sung", xếp hạng C, NR_length ≥ 6 tốt hơn; thời hạn lệnh chờ không nêu — `nguồn-báo-cáo` — S081
- F183 NR7 là ứng viên duy nhất mà setup không có hướng (sinh cả BUY lẫn SELL cho cùng nến), không khớp hợp đồng một `signal` ±1 mỗi nến của hệ; cần bản một chiều (đóng cửa ngoài biên, hoặc lọc hướng bằng xu hướng) hoặc đổi hợp đồng đầu ra — `suy-luận` — S081
- F184 Supertrend (ATR 10, hệ số 3): dải `(H+L)/2 ± 3×ATR`, dải cuối chỉ dịch theo xu hướng, BUY khi `close` vượt dải trên, SELL khi `close` dưới dải dưới; nguồn khác nhau về cách làm mượt ATR (Wilder 14 hay chu kỳ 10) — `nguồn-báo-cáo` — S084
- F185 Mức độ đã kiểm trong đợt này: KHÔNG có phép đo nào trên dữ liệu hệ; chỉ 5/14 nguồn được đọc thật (S071, S073, S076 một phần, S079, S081), 6 nguồn chỉ có tóm tắt tìm kiếm (S072, S075, S077, S080, S082, S084) và 3 là sách chưa đọc (S074, S078, S083) — `suy-luận` — topic §8
