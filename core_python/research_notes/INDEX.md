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
