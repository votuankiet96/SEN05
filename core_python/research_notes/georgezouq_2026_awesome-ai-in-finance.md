# S008 — georgezouq/awesome-ai-in-finance (danh sách tuyển chọn "Awesome AI in Finance")

Trạng thái nạp: đã đọc toàn bộ `README.md` (418 dòng), `chinese.md` (30), `contributing.md` (20) và `LICENSE`; phân tích cấu trúc bằng chương trình; kiểm trạng thái của **182 repo GitHub** (trang công khai, nguồn cấp `commits.atom`, cờ `isArchived`; không dùng API) và của **76 URL ngoài GitHub**. Đọc **một phần** hai loại nội dung được liệt kê: trang 1 của PDF "Reinforcement Learning for Trading" (NeurIPS, 7 trang) và metadata/tóm tắt của 6 bài arXiv (qua API arXiv). **Không đọc mã hay tài liệu của dự án nào** — đây là chỉ mục con trỏ; việc một mục có mặt trong danh sách không phải bằng chứng rằng mục đó đúng hay hiệu quả. Danh mục đầy đủ 263 dòng (từng mục, URL, repo, commit cuối, tuổi, lưu trữ, chuyển hướng, trạng thái liên kết, mô tả) nằm ở `georgezouq_2026_awesome-ai-in-finance_catalog.csv`. Đối chiếu với S001–S007 ở mục 9.

Nhãn trạng thái: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Nguồn

- Loại: **awesome list** do một cá nhân duy trì, nhận bổ sung qua pull request của cộng đồng. Không phải nghiên cứu, không có phương pháp luận, không có tiêu chí chọn (mục 4.1).
- URL: https://github.com/georgezouq/awesome-ai-in-finance · mô tả "🔬 A curated list of awesome LLMs & deep learning strategies & tools in financial market." · trang chủ khai báo một bài Medium "How people use AI in finance" (chưa đọc).
- Repo: tạo 2018-08-29 · pushed 2026-09-08T03:00:12Z · commit đọc `2863ab2389aa1b1b7c68af5fa87c47236ed61421` ("Merge pull request #251 from georgezouq/curate-approved-finance-prs") · 6 629 sao / 832 fork / 134 người theo dõi · 52 mục mở (issue + PR; 30 mục đầu trang đầu **đều là PR** dạng "Add <sản phẩm> to <mục>", mới nhất #280 ngày 2026-10-04) · 568 KB · người đóng góp (số commit trên nhánh mặc định): georgezouq 182, anmorgan24 4, donbagger 4, stjepanjurekovic 3, jsmontelius89 2, heubme2020 2, peerchemist 2, GAJETOso 2. Các commit mới nhất là chuỗi "Merge reviewed PR #164/#167/#191/#201/#211/#213/#216 with normalized documentation" lúc 02:57–02:58 ngày 2026-09-08, rồi "Merge … PR #251" lúc 03:00.
- Giấy phép: **CC0-1.0** (LICENSE 121 dòng "Creative Commons Legal Code — CC0 1.0 Universal"; GitHub API cũng trả `cc0-1.0`). CC0 cho phép sao chép; mô tả trong tệp CSV đi kèm là trích từ README. Giấy phép của **từng dự án được liệt kê** là riêng và chưa kiểm cái nào.
- Cây repo (13 mục, không bị cắt): `README.md`, `chinese.md`, `contributing.md`, `LICENSE`, `media/logo.svg`, `.gitignore` và **5 tệp cấu hình IDE `.idea/` đã bị commit** (ví dụ `workspace.xml` 11 510 byte) — vệ sinh repo kém, không ảnh hưởng nội dung.
- Ngày tải: 2026-10-04. Tệp và sha256 (tải từ `raw.githubusercontent.com` đúng commit):

| Tệp | Dòng / byte | sha256 |
|---|---|---|
| README.md | 418 / 48 131 | `357cefe9c811dbc79bea63057cb8665c63ed43e45ef869d60c0672538d0a9ea3` |
| chinese.md | 30 / 2 504 | `46756a4eeb0399a732fb3113ac725ef17e09218d3d923ffc9fe3eb1747e10356` |
| contributing.md | 20 / 1 075 | `8bfce5e1d927e4ddc20d2644a44e1f0e5e906734bd56ef835c8755f64c53924f` |
| LICENSE | 121 / 7 048 | `a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499` |

- **Chưa đọc / chưa làm**: nội dung các dự án, khoá học và bài viết được liệt kê (trừ 1 PDF đọc trang 1 và metadata 6 arXiv); bài Medium của trang chủ; ba mục SSRN (không tải ổn định: lỗi TLS, một lần thử trả HTTP 403); giấy phép từng dự án; các PR mở còn lại (chỉ đọc 30 PR đầu).

## 2. Cấu trúc và số đếm (tái lập bằng chương trình)

README tự mô tả: "This list contains the research, tools and code that people use to beat the market." (khẳng định chung, không kèm bằng chứng). Mục lục có 25 liên kết neo (không tính vào số mục). Phân tích từng dòng gạch đầu dòng có liên kết:

| Mục chính (H2) | Số mục | Mục con |
|---|---:|---|
| Agents | 16 | – |
| LLMs | 15 | – |
| Skills | 9 | – |
| MCP Servers | 25 | Market Data 13 · Trading Execution 8 · Research & Analysis 4 |
| Papers | 11 | – |
| Courses & Books & Blogs | 15 | – |
| Strategies & Research | 60 | Time Series Data 18 · Portfolio Management 6 · High Frequency Trading 3 · Event Drive 2 · Crypto Currencies Strategies 8 · Technical Analysis 17 · Lottery & Gamble 1 · Arbitrage 5 |
| Data Sources | 37 | Traditional Markets 17 · Crypto Currencies 14 · News 1 · Alternative 2 · Prediction Markets 3 |
| Research Tools | 19 | – |
| Trading System | 20 | Traditional Market 11 · Crypto Currencies 7 · Plugins 2 |
| TA Lib | 4 | – |
| Exchange API | 13 | Mục con 5 · Framework 1 · Visualizing 3 · GYM Environment 4 |
| Articles | 3 | – |
| Others | 16 | Mục con 8 · Other Resource 8 |
| **Tổng** | **263** | |

- **258 mục duy nhất**: 5 URL xuất hiện hai lần — KeepRule (dòng 153 và 405), stockpredictionai (162 và 198; cùng 🌟🌟), CongressionalStockBrain (251 và 309, hai cách viết tên), Philidor (275 và 406), CoinMarketCapBacktesting (341 và 355).
- **184 mục trỏ tới github.com = 182 repo duy nhất**; 79 mục trỏ tới miền khác (arxiv.org 6, papers.ssrn.com 3, manning.com 3, tradingview.com 2, còn lại là trang sản phẩm/dịch vụ, khoá học, bài viết).
- `chinese.md`: 11 mục (FinBERT, RL-Stock, ssq, wondertrader, easytrader, openctp, 4 bài viết, awesome-quant-china). Mục "ssq" là "利用神经网络和LSTM预测双色球" (dự đoán xổ số bằng LSTM).
- **23 mục có biểu tượng 🌟** (12 mục 1 sao, 8 mục 2 sao, 3 mục 3 sao — Nof1, OpenBB, Stock-Prediction-Models); README **không có chú giải ký hiệu** → không dùng làm thang đánh giá.

## 3. Bản đồ chủ đề và các mục đáng chú ý (con trỏ, chưa mở cái nào)

Tệp CSV đi kèm có đủ 263 mục. Cột: `line`, `section`, `name`, `url`, `stars`, `github_repo`, `last_commit_utc`, `age_months` (tính tới 2026-10-04), `archived` (từ cờ `isArchived`), `redirected_to`, `link_status`, `error`, `description`. Dưới đây là phần liên quan tới việc phát triển/đánh giá chiến lược, trích đúng mô tả của README.

**Vệ sinh backtest / kiểm định** (liên quan tới các phát hiện F005–F018, F031, F033):
- Quant Research — https://github.com/Jimmy7892/quant-research-skill — "Agent skill for backtest validation using parameter stability, selection-bias checks, and walk-forward evaluation."
- AI Trader Team — https://github.com/TLSRUF/ai-trader-team — "…deterministic position-sizing, portfolio-risk, and walk-forward backtesting tools."
- TraderHarness — https://github.com/HephaestLab/TraderHarness — "Contamination-resistant A-share backtesting environment for LLM trading agents, with point-in-time masking, entity/date anonymization, fingerprinted replay, and trajectory (SFT) export."
- TraceArena — https://github.com/tonyhyworld/TraceArena — "auditable multi-agent investment evaluation with evidence-linked actions, deterministic simulated settlement, and reproducible replay; no brokerage connection."
- CFA Institute Bias Detection — https://github.com/CFA-Institute-RPC/skills/tree/main/skills/bias-detection — "Claude skill for bias detection in investment analysis."
- Tự nhận "walk-forward" nhưng chưa kiểm: DeepAlpha, finclaw ("484 alpha factors").

**Thực thi lệnh / liên quan OF** (mục MCP Servers › Trading Execution, 8 mục): alpacahq/alpaca-mcp-server, krakenfx/kraken-cli, okx/agent-trade-kit, QuantConnect/mcp-server, koreainvestment/open-trading-api (năm mục này ghi **Official**), ariadng/metatrader-mcp-server (MT5), rcontesti/IB_MCP, mcpdotdirect/evm-mcp-server. Ở mục Exchange API còn có "Trade It" (docs.tradeit.app/mcp; "MCP for trading on common brokerages (Robinhood, ETrade, Schwab, Webull, Public, tastytrade, Coinbase, Kraken so far)"). **Không có mục nào dành cho cTrader** (chỉ Pineify nhắc cTrader trong mô tả). Dùng làm tài liệu tham khảo thiết kế (so với F034), không phải khuyến nghị; mọi MCP server nhận khoá giao dịch là mã bên thứ ba — danh sách không kiểm bảo mật (`suy-luận`).

**Chỉ báo kỹ thuật / đối chiếu tính toán** (mục TA Lib + vài mục khác): finta (70+ chỉ báo, pandas; **đã lưu trữ**, commit cuối 2022-07-24), pandas_talib, tulipnode (Tulip Indicators, 100+ hàm), techan.js, Wickra ("500+ technical-analysis indicators", lõi Rust, có binding Python), kukapay/crypto-indicators-mcp. **TA-Lib bản gốc không có trong danh sách** (đã tìm: chỉ có `pandas_talib`). Các triển khai này có thể dùng để đối chiếu chỉ báo của OG — chưa đánh giá.

**Dữ liệu**: atilaahmettaner/tradingview-mcp ("30+ tools for real-time TradingView market data, technical analysis, screeners, and backtesting") và mục "TradingView" (tradingview.com) — **cùng câu hỏi điều khoản như S007/F037** nếu dựa trên endpoint không chính thức (chưa đọc, không kết luận); massive-com/mcp_massive (Polygon.io — nguồn dữ liệu của bài trong S006); yahoo-finance, Tushare, Quandl (đổi tới data.nasdaq.com), alpha_vantage_mcp (ghi Official), FRED MCP, SEC EDGAR MCP.

**Danh mục / phân bổ vốn**: skfolio, DeepDow, PGPortfolio, ml-quant-trading, qtrader (**đã lưu trữ**), Deep-Reinforcement-Stock-Trading (cùng họ chủ đề với S004; PyPortfolioOpt **không** có trong danh sách).

**Tác tử LLM và benchmark**: Nof1 (🌟🌟🌟; "Each model is given $10,000 of real money, in real markets, with identical prompts and input data."), TradingAgents và AI Hedge Fund (cả hai cũng có trong Bảng 2 của bài ở S006), FinRobot, FinGPT, PIXIU (đổi tới The-FinAI/PIXIU), nofx, ATLAS, Vibe-Trading, MarS (Microsoft).

**Sách / khoá học**: Advances in Financial Machine Learning (sách của López de Prado) — **không phải** bài López de Prado (2016) mà S004 trích ở F021 (JPM 2016); nội dung chưa kiểm. NYU RL in Finance (Coursera; URL đổi slug); Udacity AI for Trading (URL đổi tới `ai-trading-strategies--nd881`); QuantResearch (letianzj).

**Bài báo — mục Papers (11 mục) và hai bài arXiv trong mục LLMs.** Kết quả kiểm ngày 2026-10-04 (số dòng là dòng trong README):

| Dòng | Mục | Kiểm tra | Trạng thái |
|---|---|---|---|
| 126 | Bachelier, *The Theory of Speculation*, 1900 (PDF goldseek) | HTTP 200 | chưa đọc |
| 127 | Osborne, *Brownian Motion in the Stock Market*, 1959 (m.e-m-h.org) | HTTP 200 | chưa đọc |
| 128 | "An Investigation into the Use of Reinforcement Learning Techniques within the Algorithmic Trading Domain", 2015 (doc.ic.ac.uk, PDF) | HTTP 200; tên tác giả chỉ suy từ tên tệp `j.cumming.pdf`, chưa xác minh | chưa đọc |
| 129 | arXiv:1706.10059, A Deep RL Framework for the Financial Portfolio Management Problem | API arXiv: Zhengyao Jiang et al. (3 tác giả), 2017-06-30; tiêu đề khớp | `tái-lập` (metadata) |
| 130 | "Reinforcement Learning for Trading" (Moody & Saffell, PDF NeurIPS, 7 trang) | đọc trang 1: tác giả John Moody & Matthew Saffell (Oregon Graduate Institute). Tóm tắt nêu lợi nhuận, Sharpe và *differential Sharpe ratio*, và "tính dự đoán được (predictability) của chỉ số S&P 500 hằng tháng giai đoạn 1970–1994" | `nguồn-báo-cáo`; **năm không khớp**: README ghi 1994, đường dẫn proceedings của NeurIPS ghi 1998 → `lỗi-nguồn` |
| 131 | arXiv:0907.4290, Dragon-Kings, Black Swans and the Prediction of Crises | API arXiv: Didier Sornette, 2009-07-24; tiêu đề khớp | `tái-lập` (metadata) |
| 132 | arXiv:1807.02787, Financial Trading as a Game: A Deep RL Approach | API arXiv: Chien Yi Huang (1 tác giả), 2018-07-08; tiêu đề khớp | `tái-lập` (metadata) |
| 133 | Ritter, *Machine Learning for Trading* (cims.nyu.edu, PDF) | HTTP 403 | không đọc được |
| 134 | SSRN 3197726, *Ten Financial Applications of Machine Learning, 2018* (slides) | lỗi TLS | không đọc được |
| 135 | arXiv:2011.09607, FinRL | API arXiv: Xiao-Yang Liu et al. (7 tác giả), v2 2020-11-19; tiêu đề khớp | `tái-lập` (metadata) |
| 136 | SSRN 3690996, *Deep RL for Automated Stock Trading: An Ensemble Strategy* | lỗi TLS; một lần thử trước trả HTTP 403 | không đọc được |

Trong mục **LLMs**: arXiv:2504.13125 "LLMs Meet Finance: Fine-Tuning Foundation Models for the Open FinLLM Leaderboard" (Varun Rao et al., 6 tác giả, 2025-04-17) và arXiv:2511.07322 "FinRpt: Dataset, Evaluation System and LLM-based Multi-agent Framework for Equity Research Report Generation" (Song Jin et al., 4 tác giả, v3, 2025-11-10) — tiêu đề khớp README. SSRN 4835311 "Financial Statement Analysis with Large Language Models" (mục LLMs): lỗi TLS, chưa kiểm. Khẳng định "GPT-4 can outperform professional financial analysts in predicting future earnings changes … superior trading strategies with higher Sharpe ratios and alphas" là mô tả của README, chưa đối chiếu với bài.

**Mục ít/không có giá trị bằng chứng** (ghi để khỏi nạp nhầm): "Lottery & Gamble" (LotteryPredict — LSTM dự đoán xổ số), ssq (cùng ý tưởng, ở `chinese.md`), Pizzint ("Pentagon Pizza Index"), các bot Gekko (xem 4.2), và các sản phẩm có số liệu tự quảng bá (4.4).

## 4. Chất lượng danh sách — bằng chứng

**4.1 Không có tiêu chí chọn.** README không nêu tiêu chí nhận mục. `contributing.md` hướng dẫn cách mở PR (6 bước). Bước 4 yêu cầu "Make sure you follow guidelines above", nhưng tệp không có phần hướng dẫn nào ở trên (đã đọc đủ 20 dòng) → tham chiếu treo (`lỗi-nguồn`). Ký hiệu 🌟 không có chú giải (mục 2).

**4.2 Độ mới và tình trạng bảo trì** (đo ngày 2026-10-04). Với 182 repo GitHub duy nhất: đọc nguồn cấp `commits.atom` của nhánh mặc định (mục đầu tiên = commit cuối) và trang repo công khai (cờ `isArchived`).

- Truy cập được: **179 / 182**. Ba repo trả HTTP 404 ở cả hai địa chỉ: `butor/blackbird`, `cryptosun2049/openfinclaw`, `sudoscripter/macd`.
- **Đã lưu trữ (archived): 15 repo** (16 mục, vì `jimmywumadchester/coinmarketcapbacktesting` xuất hiện hai lần). Theo ngày commit cuối: blampe/ibpy (2017-01-19) · jimmywumadchester/coinmarketcapbacktesting (2017-09-08) · mkmarek/forex.analytics (2017-09-10) · mounirlabaied/gekko-strat-hl (2018-03-28) · filangel/qtrader (2018-06-26) · mounirlabaied/quantresearchdev (2018-11-13) · cloggy45/gekko-bot-resources (2020-02-07) · huseinzol05/stock-prediction-models (2021-01-05; mục **"Stock-Prediction-Models" 🌟🌟🌟**) · h256/gekko-quasar-ui (2021-06-27) · enigmampc/catalyst (2021-09-22) · maxbbraun/trump2cash (2022-01-24; mục 🌟) · deviavir/zenbot (2022-02-14) · peerchemist/finta (2022-07-24; mục TA Lib "finta") · chaos-genius/chaos_genius (2024-09-12) · iusztinpaul/hands-on-llms (2024-12-09; mục LLMs).
- Tuổi commit cuối của 179 repo truy cập được (gồm 15 repo đã lưu trữ): trung vị **13,9 tháng**; ≤6 tháng: 74 (41,3%); 6–12 tháng: 12 (6,7%); 12–24: 11 (6,1%); 24–60: 22 (12,3%); >60 tháng: 58 (32,4%). Chỉ tính 164 repo không lưu trữ: trung vị 7,9 tháng.
- Theo mục chính (trung vị tháng kể từ commit cuối; tỉ lệ mục trên 24 tháng; đếm theo mục GitHub, kể cả trùng): Agents 0,6 (0%) · MCP Servers 0,5 (0%) · Skills 2,0 (0%) · LLMs 3,6 (0%) · Data Sources 7,2 (40%) · Research Tools 7,8 (44%) · Others 21,0 (33%) · Exchange API 33,3 (58%) · Trading System 54,7 (61%) · Courses & Books & Blogs 37,3 (71%) · **Strategies & Research 74,6 (72%)** · Articles 67,3 (100%, n=2) · **TA Lib 75,2 (100%, n=4)**.
- Chuyển hướng: **20 repo** đã đổi tên hoặc chuyển chủ; README vẫn dùng địa chỉ cũ (địa chỉ cũ tự chuyển hướng). Ví dụ `backtrader/backtrader → mementum/backtrader`, `ai4finance-llc/finrl-library → AI4Finance-Foundation/FinRL`, `chancefocus/pixiu → The-FinAI/PIXIU`, `openbb-finance/openbb → openbq-org/OpenBB` (mục 🌟🌟🌟 OpenBB; địa chỉ mới 73 816 sao, không lưu trữ, commit cuối 2026-10-02).
- Bot Gekko: 13 mục nhắc Gekko. Bot gốc `askmike/gekko` (không có trong danh sách) ghi trên trang repo: **"This repo is not maintained anymore"**, commit cuối 2020-02-16 (kiểm ngày 2026-10-04).
- Năm repo có commit mới nhất (2026-10-03…04): koala73/worldmonitor, heubme2020/datasinking, tauricresearch/tradingagents, dgunning/edgartools, wickra-lib/wickra.

**4.3 Liên kết ngoài GitHub** (76 URL duy nhất; 79 mục). Lần kiểm đầu, ngày 2026-10-04: GET, theo chuyển hướng, đọc tối đa 2 KB, timeout 20 giây.

- **60 trả HTTP 200**; 13 trong số này đổi địa chỉ cuối (phần lớn là chuẩn hoá đường dẫn hoặc đổi tên sản phẩm: `thenof1.com → nof1.ai`, `twitter.com → x.com`, `quandl.com → data.nasdaq.com`, `api.coinpaprika.com → docs.coinpaprika.com`, khoá Udacity đổi slug).
- **9 không kết nối được**: `papers.ssrn.com` ×3 (lỗi TLS); `docs.parsecapi.com` và `agentmarket.cloud` (không phân giải DNS); `synthical.com`, `filingfirehose.com`, webinar trên quantopian.com (timeout); `kandi.openweaver.com` (lỗi chứng chỉ TLS).
- **2 trả HTTP 403**: PDF của Ritter 2017 (cims.nyu.edu), `valueray.com/api`.
- **2 trả HTTP 402** (yêu cầu thanh toán): `congressionalstockbrain.com`, `api.dexpaprika.com`.
- **3 mã khác**: 429 `spzco.com`; 503 `agentservices.to`; 525 `marginsafe.ai` (lỗi TLS phía máy chủ).
- Ý nghĩa: đây là ảnh chụp một lần từ một máy. Mã 402/403/429/503/525 **không chứng minh** dịch vụ đã ngừng; mã 0 chỉ có nghĩa "không tải được từ máy này lúc này".

**4.4 Khẳng định hiệu quả trong mô tả — tự công bố, chưa kiểm** (`nguồn-báo-cáo`): InvicTrade "74% historical win rate"; tiêu đề liên kết Reddit "A ChatGPT trading algorithm delivered 500% returns in stock market…"; CRNG "Matches 86% of real market metrics vs 14% for NumPy"; Nof1 (thí nghiệm bằng tiền thật, mô tả trong README); DeepAlpha "walk-forward validated"; finclaw; MarS "A Financial Market Simulation Engine Powered by Generative Foundation Model". Nhiều mục là sản phẩm/dịch vụ có thu phí, và hàng đợi PR mở có nhiều PR dạng quảng bá ("Add tradefloor", "Add Resolved Markets", "Add Quantral to Research Tools"…). Tôi không xác minh người nộp PR có phải chủ sản phẩm; đây là suy luận từ nội dung PR (`suy-luận`), nên có xung đột lợi ích cấu trúc. README không đánh dấu mục nào là thương mại.

**4.5 Trùng lặp với nguồn đã nạp.** So URL với README của S005 (awesome-systematic-trading, tải ngày 2026-10-03): 533 URL duy nhất ở S005 và 258 ở S008; **27 URL trùng**, trong đó **24 repo GitHub** (13,2% trong 182 repo của S008; 17,5% trong 137 repo của S005): FinGPT, r2, blackbird, PENDAX, edgartools, FinancePy, tf-quant-finance, ml-quant-trading, DeepDow, bitcoin-arbitrage, CryptoInscriber, finta, QuantConnect/Lean, pyfolio, zipline, rqalpha, skfolio, QTradeX, ai-hedge-fund, tushare, Wickra, Gekko-Datasets, TradingGym, zvt; cộng ba URL không phải GitHub (khoá Udacity, khoá NYU RL, cfte.education). Hai danh sách phần lớn **bổ sung** nhau, không thay thế.

## 5. Liên quan tới og_program

- **Gián tiếp**: là bản đồ để tìm nguồn, không dẫn tới khuyến nghị đổi hệ thống. Các con trỏ có khả năng hữu ích nhất để nạp tiếp (bạn chọn): (1) công cụ/quy trình kiểm định backtest (Quant Research, TraderHarness, TraceArena, CFA bias detection); (2) triển khai chỉ báo độc lập để đối chiếu MACD/ATR/MA của OG (finta — đã lưu trữ, tulipnode, Wickra); (3) tài liệu MCP/API chính thức của các broker làm tham khảo thiết kế cho OF (năm mục "Official"); (4) bài López de Prado 2016 mà S004 cần đọc (F021) — không có trong danh sách này.
- Mọi mục dựa trên dữ liệu TradingView cần đặt cạnh F037 (S007) trước khi dùng.

## 6. Đã làm

Đọc đủ 4 tệp (README, chinese.md, contributing.md, LICENSE). Phân tích README bằng chương trình: đếm, tách mục, tìm trùng URL/tên, đếm 🌟, phân loại miền. Đọc trang 1 của bài Moody & Saffell (PDF NeurIPS, đọc bằng ảnh). Kiểm 6 mã arXiv qua API arXiv (tồn tại, tiêu đề, tác giả, ngày). Kiểm trạng thái 182 repo GitHub (trang công khai, `commits.atom`, cờ `isArchived`) và 76 URL ngoài GitHub. So URL với S005. Đọc 30 PR mở đầu tiên. Kiểm trang repo `askmike/gekko` để đánh giá các bot Gekko. Tạo `georgezouq_2026_awesome-ai-in-finance_catalog.csv` (263 dòng).

Lưu ý về công cụ: lần kiểm lưu trữ đầu dùng chuỗi nhận diện không khớp, làm 15 repo đã lưu trữ bị ghi là "chưa lưu trữ" trong một bản trung gian. Cột `archived` trong catalog và mục 4.2 đã lấy từ cờ `isArchived` của trang repo, nên là số đúng.

## 7. Chưa tái lập / không làm

Không đọc mã hay tài liệu của dự án nào. Không xác minh được ba bài SSRN và SSRN 4835311. Không kiểm khẳng định hiệu quả nào (mục 4.4). Không kiểm giấy phép từng dự án. Không phân loại "thương mại" bằng nội dung trang; chỉ dựa vào mô tả và miền URL.

## 8. Đánh giá và mức dùng (đánh giá của tôi)

- Dùng như **chỉ mục khám phá**, có phân loại chủ đề và độ mới đo được. Không dùng làm bằng chứng cho bất kỳ chiến lược, công cụ hay con số nào.
- Điểm đáng lưu ý: danh sách không có cơ chế loại mục chết. 15 repo đã lưu trữ và phần lớn mục "Strategies & Research" và "TA Lib" không được cập nhật trên 2 năm. Phần Agents / LLMs / MCP Servers mới hơn nhiều (trung vị dưới 4 tháng).
- So với S005, danh sách này bổ sung chủ đề LLM/tác tử/MCP; phần chiến lược cổ điển thì kế thừa nhiều repo cũ.

## 9. Đối chiếu với cái cũ

- S005 (mục 4.5): 24 repo trùng; phần còn lại bổ sung nhau. S006 (Bảng 2 của bài): TradingAgents và AI Hedge Fund có mặt ở đây. S007: các mục TradingView (mục 3). S004: PyPortfolioOpt không có ở đây; skfolio, DeepDow, PGPortfolio là các lựa chọn cùng chủ đề. S001–S003: không có mục tương ứng.
- Không mâu thuẫn với nguồn nào đã nạp. Có một mâu thuẫn **trong** nguồn này: năm của bài Moody & Saffell (1994 trong README, 1998 trong đường dẫn NeurIPS) — mục 3, dòng 130.

## 10. Bước tiếp theo (đề xuất, chưa làm; cần bạn chọn)

1. Chọn 1–3 con trỏ ở mục 5 để nạp thành nguồn riêng (mỗi nguồn một ghi chú).
2. Nếu cần kiểm bài Moody & Saffell đầy đủ: hiện mới đọc trang 1 (7 trang). Đọc toàn bài trước khi dựa vào khẳng định "predictability 1970–1994".
3. Nếu cần ba bài SSRN và SSRN 4835311: bạn gửi PDF, hoặc cho phép tôi tìm bản thay thế.
