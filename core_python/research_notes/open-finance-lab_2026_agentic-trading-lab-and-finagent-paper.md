# S006 — Open-Finance-Lab/AgenticTrading (nền tảng tác tử LLM giao dịch) + bài workshop arXiv:2512.02227

Trạng thái nạp: đã đọc README gốc, README của `orchestration/`, tài liệu thí nghiệm nội bộ (probe), hướng dẫn live trading, `leaderboard.json`, LICENSE và **toàn bộ 11 trang bài báo** (công bố duy nhất mà repo dẫn). **Không đọc mã nền tảng** (dashboard/backend, orchestration/FinAgents), issue/PR, kết quả bảng xếp hạng. Đã tái lập phần số học của bài và benchmark S&P 500; đã tìm mã thí nghiệm của bài trong repo (không thấy). Đã đối chiếu với S001–S005 và bộ nhớ (mục 10).

Nhãn trạng thái: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Nguồn

- Loại — **ba phần bản chất khác nhau, phải tách khi trích dẫn**:
  (a) repo mã nguồn + README của một nền tảng thử nghiệm tác tử LLM (sản phẩm có demo trực tuyến và chức năng giao dịch thật tuỳ chọn);
  (b) tài liệu thí nghiệm nội bộ của nhóm phát triển (báo cáo đính kèm PR, không bình duyệt);
  (c) bài workshop *Orchestration Framework for Financial Agents: From Algorithmic Trading to Agentic Trading* — Jifeng Li (SecureFinAI Lab, Columbia), Arnav Grover (Purdue), Abraham Alpuerto (RPI), Yupeng Cao (Stevens), Xiao-Yang Liu (Columbia, tác giả liên hệ). Trang NeurIPS ghi "Poster in Workshop: Generative AI in Finance"; trang không nêu quy trình bình duyệt.
- URL: https://github.com/Open-Finance-Lab/AgenticTrading (đã bỏ tham số theo dõi `fbclid`) · https://arxiv.org/abs/2512.02227v1 (cs.MA, cs.AI, cs.CE, cs.LG; nộp 2025-12-01 21:50:22 UTC; không DOI) · https://neurips.cc/virtual/2025/132518.
- Repo: tạo 2025-05-21; commit đọc `870a7f0fbf8b222dceafe4917bf90a0812972fd1` ("Merge pull request #609 … docs/h6-board-results"), pushed 2026-10-03T04:51:13Z; 767 sao / 156 fork / 110 issue mở; không có release; 38 346 KB; cây 1779 mục (`truncated: false`): dashboard 885, orchestration 579, docs 249, packaging 33. Người commit nhiều nhất: FlyM1ss 780, Allan-Feng 439, MrParamecium 377, jifeng-l 132.
- Giấy phép: `LICENSE` = **OpenMDW-1.0**, "Copyright (c) SecureFinAI Lab" (đọc toàn văn 51 dòng: cho phép rộng rãi; khi phân phối phải giữ bản giấy phép + thông báo bản quyền; quyền chấm dứt nếu kiện về sáng chế; "AS IS"; văn bản định nghĩa đối tượng là "Model Materials" = mô hình học máy + dữ liệu/tài liệu/phần mềm đi kèm). GitHub API trả `NOASSERTION`. Không đánh giá pháp lý; chưa dùng mã nên chưa cần.
- Bối cảnh / xung đột lợi ích: nền tảng là sản phẩm của chính nhóm tác giả bài (demo trên vercel, "SecureFinAI Contest 2026"); mọi số hiệu suất do chính họ công bố.
- Ngày đọc: 2026-10-03. Tệp đã đọc:

| Tệp | Dòng / byte | sha256 |
|---|---|---|
| README.md | 161 / 8 865 | `8d2a894fba0c4c55ecf4a386be3b36f7d4b2f9bacac3912d6c35e9bdc030336f` |
| LICENSE | 51 / 2 637 | `a4a4801044fb20fbcf7e1ea73ccfc1a9b9efeaec310fb92d11f7cf3402adc0b6` |
| orchestration/README.md | 389 / 17 563 | `bccaf854b85ee3e6f7b0bf9751509e111ee6ca59571548b302df41d061098530` |
| docs/superpowers/probe-results/2026-08-09-instruction-sensitivity.md | 314 / 19 777 | `5114bd22e516f0dc7866470909ced6f3f3f47e8ddfe37e93c893b1f55d7e2951` |
| docs/source/lab/live_trading.rst | 221 / 8 893 | `d8cb315808300705cd6a45810b7223986dd84e7990ec89c5cfa1ffa7ee24ddfc` |
| dashboard/config/leaderboard.json | 156 / 4 149 | `24f672407acb8662d8e9bc29de26f4c08a5cbee0fa45b40fa0a88c4b794ce7e8` |
| arXiv 2512.02227v1 (PDF, 11 trang) | 892 989 byte | `7400363ba026e26f6f8814c7874844afc8ea36e5071fc544f69244c60bd349e6` |

- **Chưa đọc**: mã nguồn nền tảng và khung FinAgent; docs còn lại (249 mục); issue/PR; kết quả bảng xếp hạng; trang demo; readthedocs; "bài khảo sát có hệ thống về giao dịch tác tử" mà README nhắc nhưng không dẫn. Hình 2–3 của bài chỉ xem bằng mắt, không số hoá. Tarball repo @870a7f0 (70 MB sau giải nén) tải chỉ để `grep`.

## 2. Nội dung đã đọc

**2.1 Nền tảng (README gốc).** "Open-source experimental platform for LLM-powered trading agents": tạo tác tử (chọn mô hình, nguồn dữ liệu, prompt), backtest, paper trading, bảng xếp hạng mở (mô hình LLM, chiến lược baseline, chỉ số thị trường), và "Go live" với tài khoản Robinhood thật dưới hạn mức mỗi lệnh — tắt mặc định (mục 2.5). Roadmap (Agent Cards, adapter MCP, managed runtime, stress test…) là kế hoạch; "đã làm": tin tức cảm xúc, adapter TradingAgents (chạy cục bộ rồi phát lại quyết định qua SDK); adapter MCP/broker/dữ liệu "còn phía trước".

**2.2 Khung FinAgent (`orchestration/README.md`).** 12 thành phần: 3 hạ tầng (DAG Planner, Orchestrator, Registration Bus) + 8 nhóm tác tử chức năng (Data, Alpha, Risk, Transaction Cost, Portfolio Construction, Execution, Backtest, Audit) + Memory Agent (Neo4j + bộ nhớ vector); bốn lớp giao thức MCP/ACP/A2A/ANP. Mục "Performance & Benchmarks" và "First application of MCP/A2A to quantitative finance": xem mục 5.

**2.3 Bài báo.**
- Thiết kế: ánh xạ từng thành phần của hệ giao dịch thuật toán sang tác tử; MCP cho tin điều khiển giữa orchestrator và tác tử, A2A giữa các tác tử; GPT-4o / Llama3 / FinGPT được nêu là mô hình dùng. "Signal diagnostics … computed by tool modules and never exposed to LLMs".
- Thí nghiệm cổ phiếu: **vũ trụ tĩnh 7 mã** (AAPL, MSFT, GOOGL, JPM, TSLA, NVDA, META); dữ liệu giờ (Polygon, yfinance); mẫu 09/2022–01/2025; cửa sổ test **24/04/2024→31/12/2024** (chú thích Hình 2), cửa sổ huấn luyện cuộn 3 tháng; vốn $100 000; GPT-4o dùng để "survey prior factor studies and draft feature lists"; baseline SPY/QQQ/IWM/VTI (mua giữ) + EW 7 mã (cân bằng lại hàng tuần).
- Thí nghiệm BTC: dữ liệu phút (Polygon) 05–08/2025; test **27/07→13/08/2025** (17 ngày, ≈23 500 quan sát phút), cửa sổ cuộn 7 ngày; baseline mua giữ. Phụ lục B: >100 đặc trưng; XGBoost dự báo lợi suất 1 phút kế tiếp (300 cây, depth 6, lr 0,08, subsample/colsample 0,8, L1 0,01, L2 0,05, 512 bin), huấn luyện lại mỗi 24 giờ (cửa sổ tối thiểu 7 ngày); tín hiệu = trộn dự báo mô hình với 5 thành phần price-action, trọng số của mô hình 0,10/0,20/0,40 tuỳ chất lượng tín hiệu (phần còn lại là price-action); 4 chế độ thị trường (xu hướng mạnh, breakout, đi ngang, biến động cao) đổi hệ số kích thước 1,8/2,5/0,7/0,8; kiểm soát rủi ro bằng ngưỡng rút vốn tích luỹ (1/2/3% → cắt 20/30/50%), dừng lỗ −0,8% (co giãn 0,5–1,5×), giới hạn rút vốn −3,0%, giữ tối thiểu 8 phút.
- **Bảng 1** (đối chiếu trong bài; BTC không có cột Annual Return):

| | Ours | SPY | QQQ | IWM | VTI | EW | BTC Ours | BTC B&H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Total return | 20,42% | 16,60% | 21,59% | 11,45% | 16,29% | **47,46%** | 8,39% | 3,80% |
| Annual return | 31,08% | 25,07% | 32,94% | 17,10% | 24,59% | 76,07% | – | – |
| Volatility | 11,83% | 13,49% | 18,38% | 21,61% | 13,72% | 22,54% | 24,23% | 25,82% |
| Sharpe | 2,63 | 1,86 | 1,79 | 0,79 | 1,79 | **3,37** | 0,378 | 0,170 |
| Max drawdown | −3,59% | −8,89% | −14,13% | −11,60% | −9,06% | −16,21% | −2,80% | −5,26% |

- Tóm tắt bài chỉ nêu "S&P 500 index yielded 15,97%" (không nêu EW). Thân bài (mục 1 và 3.2) có nêu EW 47,46%/Sharpe 3,37 và nhận "the agentic strategy trades off some total return for tighter risk".
- Phụ lục C (Bảng 2): so sánh 6 dự án mã nguồn mở (QuantAgent 306 sao, Alpha Arena 549, TradingAgents 24 800, AI Hedge Fund 42 300, ContestTrade 465, StockAgent 402; số liệu ~11/2025). Dùng làm **con trỏ khám phá**, không phải đánh giá chất lượng; số sao ≠ độ đúng.

**2.4 Tài liệu probe `2026-08-09-instruction-sensitivity.md`** (báo cáo nội bộ; câu hỏi: một chỉ dẫn giao dịch có làm đổi lợi suất của một LLM cố định không?). Kết quả hợp lệ: DeepSeek V4 Pro, temperature 0, vốn $100 000, 161 bước, cửa sổ 2026-04-15→2026-05-15: `aggressive_momentum` **+3,83%**; hai lần chạy cùng chỉ dẫn vô nghĩa **+0,33%** và **+0,13%** → "nhiễu nền" 0,20 điểm %, tín hiệu 3,61, tỉ số 18,1×; dải baseline thụ động +2,24%…+5,95%. Hai lượt trước chạy ở vốn $10 000 bị tác giả tự **rút lại** (xem F031). Số tiền chi: $3,61 (trong hạn mức $4,97).

**2.5 `live_trading.rst`** (mô tả thiết kế; mã chưa đọc): hai cổng độc lập mới cho lệnh ra thị trường (bật theo từng tác tử **và** `ROBINHOOD_EXECUTE=true` ở máy chủ, mặc định tắt; khi tắt vẫn lấy danh mục thật, gọi mô hình, review lệnh với broker nhưng ghi `skipped`); mọi lệnh qua cổng rủi ro: không có báo giá → từ chối; trần $25/lệnh (`ROBINHOOD_MAX_ORDER_USD`, đọc theo từng lệnh, giá trị không hợp lệ rơi về 25 chứ không tắt trần); trần 10 000 cổ phiếu; bán tối đa lượng đang giữ, không giữ thì từ chối (không mở short); số lượng làm tròn xuống; một lượt live/tài khoản; `idempotency_key`; token broker mã hoá Fernet, thiếu khoá thì từ chối đọc/ghi chứ không tự suy ra khoá.

**2.6 `leaderboard.json`** (đọc trực tiếp): 12 mục = 2 chỉ số (`^GSPC`, `^DJI`) + 3 baseline (mua giữ ngang trọng số, mean-variance, ngang trọng số theo chỉ số) + 7 LLM (Claude Haiku 4.5, GPT-5.5, Gemini 3.1 Pro Preview, Claude Sonnet 4.6, DeepSeek V4 Pro, Qwen3.7 Plus, Nemotron 3 Nano 30B), `mode: safe_trading`, `auto_compute:false`; cửa sổ **một tháng** 2026-04-15→2026-05-15, vốn 100 000; "season" 10 ngày giao dịch từ 2026-08-12. Không đọc kết quả.

## 3. Khẳng định chính và trạng thái

| # | Khẳng định | Trạng thái | Ghi chú |
|---|---|---|---|
| 1 | Cổ phiếu: 20,42% / Sharpe 2,63 / MDD −3,59% so với "S&P 500 15,97%" | `nguồn-báo-cáo`; phần S&P 500 **`tái-lập`** | FRED `SP500`: 5071,63 (24/04/2024) → 5881,63 (31/12/2024) = **+15,97%** (đúng cửa sổ, chỉ số giá không cổ tức). Phần "Ours": mã/log không công bố → không tái lập |
| 2 | BTC/USDT 27/07→13/08/2025: 8,39% / Sharpe 0,378 / MDD −2,80% so với mua giữ 3,80% | `nguồn-báo-cáo`; mua giữ **không tái lập** | Binance spot 1 phút, tôi tính: +4,57% (mở 27/07 00:00 → đóng 13/08 23:59 UTC), +3,26% (đóng→đóng theo ngày), +1,88% (mở 27/07 → mở 13/08); bài dùng Polygon, mốc giờ không nêu → không ghim được 3,80% |
| 3 | "Ours" có volatility và drawdown thấp nhất, Sharpe cao hơn mọi ETF | `nguồn-báo-cáo` (Bảng 1 khớp nội bộ, mục 7) | nhưng EW cùng 7 mã: lợi suất 47,46% > 20,42% và Sharpe 3,37 > 2,63; QQQ 21,59% > 20,42% |
| 4 | Dữ liệu đánh giá không bao giờ lộ cho LLM; phép tính số do module công cụ | `nguồn-báo-cáo` (thiết kế) | không kiểm được: mã/log không công bố |
| 5 | MCP/A2A + memory agent cho khả năng kiểm toán | `nguồn-báo-cáo` (thiết kế) | không có thí nghiệm riêng cho từng thành phần |
| 6 | README orchestration: "1000+ factors / 10+ years / 20+ DAG tasks / 100K+ interactions", "chi tiết trong `Papers/`", "first application of MCP/A2A to quantitative finance" | dẫn chiếu: **`lỗi-nguồn`** (mục 5); "first": `nguồn-báo-cáo`, chưa tìm tài liệu để kiểm | |
| 7 | Probe: chỉ dẫn làm đổi lợi suất 18× nhiễu nền (một mô hình, một cửa sổ) | `nguồn-báo-cáo` (số học nội bộ `tái-lập` trong làm tròn) | đường cong thô không lưu → không tái suy được chữ số (tác giả tự nói) |
| 8 | Probe: kết luận "gate fails" ở vốn $10k là sai vì độ phân giải | `nguồn-báo-cáo`; số học `tái-lập` | 249,40/10 000 = 2,494%; /100 000 = 0,249% |
| 9 | Live trading: hai cổng + cổng rủi ro | `nguồn-báo-cáo` (tài liệu thiết kế) | chưa đọc mã thực thi |

## 4. Hạn chế nguồn tự nêu

- Bài: "Future work includes longer horizons and markets, **ablations on gating/memory/messaging**, … and **releasing benchmarks and logs for replication**." → tác giả thừa nhận chưa có ablation và chưa công bố benchmark/log để tái lập.
- Bài (chú thích Bảng 1): "Sharpe ratios are computed from daily/weekly returns with Rf = 0 due to short evaluation horizons."
- Probe: (i) nhiễu nền dựa trên **một cặp** chạy "so it is an estimate with no error bar of its own"; (ii) chưa đo chỉ dẫn-so-với-chỉ dẫn (`contrarian_reversion` bị cắt vì ngân sách); (iii) neo prompt mặc định chưa chạy (run công bố +7,49% ở temperature mặc định so với +3,83% ở temperature 0 — chênh 3,7 điểm % chưa quy được cho prompt hay temperature); (iv) mẫu số lợi suất bị đổi trong review nên chữ số phụ thuộc định nghĩa; (v) Nemotron "unmeasured", không phải "flat".
- Live trading: "LLM-driven research tools, not investment advice, and nothing in the Lab predicts market outcomes."

## 5. Lỗi / mâu thuẫn xác minh ngay trong nguồn (`lỗi-nguồn`)

1. **Cơ sở Sharpe không đồng nhất trong cùng bảng** — chi tiết và số tính lại ở mục 7a–7b (F027).
2. Cùng đại lượng, hai giá trị: Sharpe BTC 0,378 / 0,170 (Bảng 1, mục 1) so với 0,380 / 0,168 (Phụ lục B.5); Excess Hình 3: "4.60%" (khung chú giải) so với "+4.59%" (chú thích, Phụ lục; 8,39 − 3,80 = 4,59).
3. "S&P 500": tóm tắt/mục 1 dùng **15,97% (chỉ số giá)**, Bảng 1 dùng **SPY 16,60%** — hai đại lượng khác nhau, bài không giải thích chênh 0,63 điểm %.
4. Ngày kết thúc cửa sổ test cổ phiếu: 12/2024 (tóm tắt; Hình 2: 31/12/2024) so với 01/2025 (mục 1: "04/2024 to 01/2025"); mục 3.2 "09/2022 to 01/2025" là cả mẫu. Định dạng ngày lẫn DD/MM (tóm tắt: "27/07/2025") và MM/DD (Bảng 2: "10/20/2025"; Hình 3: "07/27 to 08/13") — giải mã được bằng ngữ cảnh.
5. Câu kết luận thiếu động từ: "we show a total return over the S&P 500 index and ETFs". Nếu hiểu "vượt", Bảng 1 mâu thuẫn: QQQ 21,59% > 20,42%, EW 47,46%.
6. `orchestration/README.md`: (a) thư mục `Papers/` được dẫn cho "detailed benchmark results" **không tồn tại** ở @870a7f0 (0/1779 đường dẫn chứa "papers", không phân biệt hoa/thường; cây không bị cắt); (b) các số "1000+ novel factors / 10+ years / 20+ parallel tasks / 100K+ interactions" không có trong bài (0 lần "1000+"/"novel factors") và không có tệp kết quả đi kèm; (c) bảng giao thức giải nghĩa MCP là "Multi-agent Control Protocol", còn mục "Implementation Highlights" của chính README và bài dùng "Model Context Protocol". (Dòng 146, 157, 192, 305–313 của tệp.)
7. Probe: tổng chi cho các lượt không hợp lệ ghi $2,2431, nhưng các lượt được liệt kê cộng lại $2,2135 (DeepSeek $10k: 0,397+0,538+0,453+0,470 = 1,858; Nemotron 0,3555) → thiếu **$0,0296** không giải thích được từ chính tài liệu (không ảnh hưởng kết luận). Các số khác tính lại khớp (mục 7g); 3,61 vs 3,60 và 3,71 vs 3,70 là làm tròn.
8. `leaderboard.json`: mục `spy_index` mang nhãn mô hình "SPY" nhưng `symbols: ["^GSPC"]` (chỉ số, không phải ETF) — nhãn lệch nhỏ.

## 6. Hạn chế nguồn không nêu (`suy-luận`, kèm căn cứ)

- **Một đường đi cho mỗi thí nghiệm**, một cửa sổ, không khoảng tin cậy, không nói đã thử bao nhiêu cấu hình (không hiệu chỉnh nhiều phép thử). Theo số của chính bài: cổ phiếu t ≈ 2,63×√0,688 = **2,18**; BTC t ≈ 0,378×√17 = **1,56 (< 1,96)**, 17 lệnh (11 thắng = 64,7%). Giả định lợi suất độc lập (F029, áp dụng F024).
- **Vũ trụ 7 mã mega-cap tĩnh**; bài không nêu tiêu chí chọn (đã đọc mục 3.1–3.2). EW của chính 7 mã này đánh bại phương pháp ở cả lợi suất và Sharpe → benchmark thích hợp nhất là baseline cùng vũ trụ, và tóm tắt không nêu nó (F028).
- Hình 2: đường "Ours" **phẳng ≈ 1,0 từ đầu cửa sổ đến khoảng đầu tháng 7/2024** (quan sát bằng mắt, chưa số hoá); bài không giải thích (có thể chưa vào lệnh). Total return/Sharpe/volatility vì thế tính trên cửa sổ chứa đoạn phẳng ~2 tháng đầu.
- **Chi phí giao dịch**: "slippage", "fees", "transaction cost" mỗi từ xuất hiện đúng 1 lần trong cả bài (mô tả thiết kế), **không có tham số nào** → không rõ Bảng 1 là gộp hay ròng chi phí.
- **Không có ablation**: tín hiệu số đến từ XGBoost + luật price-action (Phụ lục A: "Alpha Agents do not compute predictions"; Phụ lục B); LLM soạn danh sách đặc trưng (GPT-4o, mục 3.2) và điều phối. Không thể quy hiệu suất cho thành phần "tác tử/LLM" (F030).
- Phụ lục B nêu hàng chục hằng số đặt tay (trọng số 40/40/20, hệ số ×10/×5/×100/×20, ngưỡng 0,05/0,10, percentile 45/50/35/40, mức cắt rút vốn…); bài chỉ nói cửa sổ validation "not to tune the strategy on realized test outcomes" — không mô tả cách chọn chúng; với 17 ngày test, số bậc tự do lớn.
- Calmar BTC 166,06 dựa trên quy đổi hình học của lợi suất 17 ngày ra năm (≈464%/năm) — ngoại suy không có ý nghĩa thực tế.
- Bảng xếp hạng: **một lần chạy mỗi mô hình trên một cửa sổ một tháng**, trong khi chính probe đo nhiễu nền ≈0,20 điểm % ở temperature 0 (n=1 cặp) → chênh lệch nhỏ giữa các mô hình không xếp hạng được; một số mục LLM không khai báo `temperature` (Claude/GPT/Gemini).

## 7. Đã làm và kết quả

(a) **Bảng 1, cổ phiếu — `tái-lập` từ số của bài.** Độ dài cửa sổ ngụ ý `ln(1+Total)/ln(1+Annual)` = 0,6864–0,6867 năm (250,5–250,7 ngày), khớp 24/04→31/12/2024 (251 ngày) → "Annual Return" là quy đổi hình học theo độ dài cửa sổ (lệch 0,03–0,16 điểm %). **Sharpe = Annual Return / Volatility** ở cả 6 cột (2,627; 1,858; 1,792; 0,791; 1,792; 3,375 so với 2,63; 1,86; 1,79; 0,79; 1,79; 3,37; sai lệch ≤ 0,005).
(b) **Bảng 1, BTC.** Phụ lục B.5: lợi suất ngày TB 0,48% (mua giữ 0,23%); volatility năm hoá 24,23% (25,82%). Sharpe = `TB ngày / (vol ÷ √365)` = 0,48/1,2683 = **0,3785** (bài 0,378) và 0,23/1,3515 = **0,1702** (bài 0,170); với √252 ra 0,3145/0,1414 — không khớp. Tức **Sharpe BTC là Sharpe ngày không năm hoá**; năm hoá sẽ là ≈7,22 (√365) và 3,25. Calmar: 8,39% trong 17 ngày → 463,9%/năm ÷ 2,80 = 165,7 (bài 166,06); 3,80% → 122,7%/năm ÷ 5,26 = 23,33 (bài 23,30). Tóm tắt đặt "Sharpe 2,63" (năm hoá) cạnh "Sharpe 0,38" (ngày) như cùng thước đo.
(c) **Ý nghĩa thống kê theo số của bài** (giả định độc lập): cổ phiếu 2,63×√0,6877 = 2,18; BTC 0,378×√17 = 1,56; cách khác 0,48%/(1,2683%/√17) = 1,56.
(d) **Benchmark S&P 500 — `tái-lập`.** FRED series `SP500` (CSV công khai, sha256 `516f30a8b9454bff…`), đóng cửa 2024-04-24 = 5071,63; 2024-12-31 = 5881,63 → +15,97% (bài 15,97%). Các mốc lân cận cho 16,00% (23/04), 16,50% (25/04), 15,71% (đến 02/01/2025) → cửa sổ 24/04→31/12 là cửa sổ bài dùng.
(e) **BTC mua giữ — không tái lập.** Binance spot BTCUSDT 1 phút, 25 920 nến (27/07 00:00 → 13/08 23:59 UTC; sha256 `a9ff5c0c6625bd55…`). Quét lưới 30 phút (đóng ngày 27/07 → đóng ngày 13/08, n=2304): min −0,50%, trung vị 1,72%, max 4,72%; chỉ 4,3% cặp rơi vào [3,70%; 3,90%] → 3,80% phụ thuộc mốc giờ. Volatility năm hoá của lợi suất ngày (đóng UTC, n=17) 27,2% so với 25,82% của bài; Sharpe ngày 0,139 so với 0,170 — không khớp, phụ thuộc cách đo.
(f) **Mã thí nghiệm của bài có trong repo không?** Tìm trong bản @870a7f0 (tệp văn bản): không tệp nào chứa "20.42" hoặc "47.46"; không chuỗi 7 mã theo thứ tự của bài; không `max_bin`, không `learning_rate` 0,08, không `RobustScaler`; không tệp `.py` nào chứa cả "xgboost" lẫn "btc"/"bitcoin". "8.39" chỉ trùng giá AAPL `178.39`; "0.378" chỉ trùng một Sharpe 0,3783 không liên quan trong `test_backtest.json`. Ví dụ `examples/2_out_of_sample_inference.ipynb` dùng **vũ trụ 10 mã** (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, WMT) và cửa sổ **2024-09-01→2025-01-01**, tối ưu prompt 2010–2023 — khác thiết kế của bài. (Chỉ tìm theo từ khoá/tham số; không loại trừ mã nằm nơi khác hoặc đặt tên khác.)
(g) **Số học tài liệu probe.** Nhiễu |0,33−0,13| = 0,20; trung bình hai điều khiển 0,23; tín hiệu 3,83−0,23 = 3,60 (tài liệu 3,61); spread 3,70 (3,71); chi phí 3 lượt hợp lệ 1,3669 → 0,4556/lượt; 120 lượt gọi bị tính phí nhưng không ra quyết định = (184−158)+(203−155)+(180−158)+(184−160); ở $10k nhiễu 1,19 / tín hiệu 0,66 / spread 1,60 (tài liệu 1,18 / 0,66 / 1,59); 1 cổ phiếu trung vị 2,494% (vốn $10k) so với 0,249% ($100k); 1/30 vốn ngang trọng số = $333,33 → 1 cổ phiếu trung vị ($10k) so với 13 ($100k) — khớp. Chưa kiểm: giá cổ phiếu mở cửa sổ (min 45,40; trung vị 249,40; max 910,92), "6/30 mã không mua nổi", "34,6% tiền mặt kẹt" (cần danh sách giá). Phép thô của tôi: tiền mặt kẹt 34,6% × thị trường tăng 2,24–5,95% ≈ 0,8–2,1 điểm %, trong khi khoảng cách giữa các lượt $10k (−0,25…−1,85%) và baseline thụ động (+2,24…+5,95%) là 2,5–7,8 điểm % → câu "that gap is the cash drag" chưa được số của chính tài liệu chứng minh đủ (`suy-luận`; baseline tính ở $100k nên so sánh vốn khác nhau).

## 8. Chưa tái lập / không kiểm được

- Mọi số hiệu suất của "Ours" (cổ phiếu, BTC) và đường cong; mọi số của SPY/QQQ/IWM/VTI/EW trong Bảng 1 (Yahoo trả HTTP 429; Stooq yêu cầu thử thách JavaScript — chỉ kiểm được chỉ số S&P 500 qua FRED); mua giữ BTC 3,80%.
- Kết quả probe và bảng xếp hạng (đường cong thô không lưu; chưa chạy nền tảng; không gọi LLM).
- Toàn bộ khẳng định kiến trúc (độ trễ, "sub-second", số tác tử, bộ nhớ) và hành vi của cổng rủi ro live trading (chưa đọc mã, chưa chạy).

## 9. Đánh giá và mức dùng (đánh giá của tôi)

- **Không dùng làm bằng chứng cho hiệu quả giao dịch của "tác tử LLM"**: kết quả là một đường đi cho mỗi thí nghiệm, 17 ngày với BTC, không ablation, không phí, mã và log không công bố, benchmark cùng vũ trụ tốt hơn phương pháp.
- **Dùng được**: (1) các bài học phương pháp có tính tổng quát và kiểm được bằng số học — độ phân giải đo so với kích thước hiệu ứng (F031), khoá bộ nhớ đệm phải gồm mọi tham số quyết định kết quả và cấu hình trôi (F033), cơ sở Sharpe phải đi kèm số (F027), baseline cùng vũ trụ (F028); (2) mẫu an toàn vận hành giao dịch thật làm tham chiếu thiết kế cho OF (F034) — là mô tả tài liệu, chưa kiểm mã; (3) Bảng 2 + mục 2.1 làm con trỏ tới các dự án khác (TradingAgents arXiv:2412.20138, AI Hedge Fund, ContestTrade, QuantAgent, Alpha Arena, StockAgent) — chưa mở cái nào.
- Liên quan tới og_program: **gián tiếp**. Hệ OG hiện chạy luật (combo, ma_cross), không dùng LLM; không có đề xuất đổi chiến lược từ nguồn này.

## 10. Đối chiếu với cái cũ

- **Củng cố** F024 (t = Sharpe×√năm): áp dụng cho số của bài → F029 (không lặp lại công thức). **Củng cố** F013/F015/F016 (hiệu suất công bố nhạy với cài đặt/cửa sổ) ở dạng khác: S006 thêm một trường hợp mà *thước đo* (Sharpe) đổi cơ sở giữa hai cột của cùng bảng.
- **Liên hệ** F003/F021/F023: README orchestration thêm một ví dụ khẳng định định lượng không kèm tệp chứng cứ (F035).
- **Mới**: F027–F036 (INDEX). Không mâu thuẫn với S001–S005.
- Đối chiếu với bộ nhớ về hệ (ghi theo bộ nhớ, **chưa kiểm lại trong phiên này**): `project_ma_cross_trend_wait` ghi việc kiểm bằng walk-forward không nhìn trước (224 960 cặp, 0 lệch) và "baseline tắt trend khớp 100%" — phù hợp tinh thần "đối chứng/ablation" mà S006 thừa nhận còn thiếu (F030); không mâu thuẫn. Pipeline OG là xác định (pandas), khác LLM temperature 0 (F032).

## 11. Bước kiểm chứng tiếp theo (đề xuất, chưa làm)

1. Nếu cần dựa vào khẳng định nào của bài: lấy nến phút BTC từ Polygon cùng mốc giờ, hoặc xin tác giả log (bài hứa "releasing benchmarks and logs"), rồi tái lập mua giữ trước.
2. **Giả thuyết cần kiểm trên mã thật (chưa phải phát hiện)**: khoá/đường ghi kết quả của `core_python` (Redis DB2/DB3 `L_PastSignal*`) có gồm mọi tham số quyết định kết quả (chu kỳ MA, cờ trend, `SIGNAL_START_DATE`…) không, hay đổi tham số có thể để lại mục cũ dưới khoá cũ — bài học từ F033.
3. Muốn dùng mẫu an toàn F034 cho OF: đọc mã `ROBINHOOD_*` thật trước khi coi là hành vi đã kiểm chứng.
4. Nếu quan tâm hướng LLM-agent: mở TradingAgents (arXiv:2412.20138) như nguồn riêng; đừng suy từ bảng so sánh của bài này.
