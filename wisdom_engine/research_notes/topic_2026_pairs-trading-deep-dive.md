# Chủ đề — Giao dịch cặp (pairs trading): cách chọn cặp, bằng chứng, và áp dụng cho ngoại hối / chỉ số / tài sản

Ngày: 2026-10-22. Người dùng: "đào sâu ý tưởng pair trading, research và phân tích sâu hơn, và làm sao để tìm được các pairs đó?"; sau đó: "nhanh lên, đặc biệt coi các pairs của thị trường ngoại hối forex, indices, asset". Bổ sung cho nhóm "Giao dịch cặp / stat-arb" đã có (S001–S003, F001–F018: ba repo mã).

Nhãn: `nguồn-báo-cáo` (nguồn nói) · `đọc-nguồn-gốc` (đọc văn bản bài gốc, kiểm số) · `tóm-tắt-tìm-kiếm` (chỉ thấy tóm tắt thứ cấp, chưa đọc bài gốc) · `suy-luận` (phân tích của tôi). **Không có phép đo nào trên dữ liệu của hệ, không chạy mã** (theo quy ước: nghiên cứu để hiểu).

**Tóm tắt:** (1) Bằng chứng mạnh nhất là ở cổ phiếu Mỹ và đã SUY GIẢM: sau chi phí, các biến thể cổ điển gần như hoà vốn hoặc âm sau 1990–2009. (2) Bằng chứng cho CHỈ SỐ, NGOẠI HỐI, TÀI SẢN KHÁC mỏng và nghiêng về tiêu cực; chỗ có bằng chứng tốt là spread hợp đồng tương lai có liên kết vật lý (dầu–xăng, đậu tương–dầu đậu, khí–điện), vốn không có trong danh sách symbol của hệ. (3) Đồng tích hợp KHÔNG bền: cặp đồng tích hợp năm nay chỉ còn đồng tích hợp năm sau ở mức ngẫu nhiên. (4) Quy trình chọn cặp đúng = tiên nghiệm kinh tế → kiểm định thống kê có hiệu chỉnh đa phép thử → lọc khả năng giao dịch (half-life, chi phí) → kiểm tra ngoài mẫu và tái ước lượng liên tục.

## 0. Nguồn

- **S088** Krauss, *Statistical arbitrage pairs trading strategies: review and outlook*, IWQW Discussion Paper 09/2015 (FAU), 62 trang, 5 nhóm phương pháp, >90 bài — `đọc-nguồn-gốc` (trích văn bản bằng pdftotext).
- **S089** Gatev, Goetzmann, Rouwenhorst, NBER w7032 (bản làm việc, dữ liệu 1962–1997) — `đọc-nguồn-gốc`. Bản RFS 2006 (1962–2002, tới 11%/năm) chỉ qua S088.
- **S090** Avellaneda & Lee, *Statistical arbitrage in the U.S. equities market* (math.nyu.edu, bản 2008) — `đọc-nguồn-gốc`.
- **S091** Clegg & Krauss, *Pairs trading with partial cointegration*, FAU DP 05/2016 — `đọc-nguồn-gốc`.
- **S092** Stübinger & Endres, *Pairs trading with a mean-reverting jump-diffusion model on high-frequency data*, FAU DP 10/2017 — `đọc-nguồn-gốc`.
- **S093** Rad, Low, Faff, *The profitability of pairs trading strategies: distance, cointegration and copula methods*, Quantitative Finance 16(10), 2016 — trang tóm tắt của Bond University (đọc phần abstract).
- **S094** Huck & Afawubo, *Pairs trading and selection methods: is cointegration superior?*, Applied Economics 47(6), 2015 — abstract (IDEAS).
- **S095** Psaradellis, Laws, Pantelous, Sermpinis, *Pairs trading, technical analysis and data snooping: mean reversion vs momentum*, SSRN 2018 — `tóm-tắt-tìm-kiếm` (qua CXO Advisory), chưa đọc bài gốc.
- **S096** Sarmento & Horta (sách SpringerBriefs 2020) qua bài Hudson & Thames *Employing machine learning for trading pairs selection* — `đọc` bài thứ cấp.
- **S097** Do & Faff (2010, 2012), Clegg (2014), cùng một số luận văn về cặp chỉ số/ngoại hối — `tóm-tắt-tìm-kiếm`: trang abstract Bond University, CXO Advisory (`.../essential-assumption-of-pairs-trading-wrong`), các luận văn qua kết quả tìm kiếm.

## 1. Bản chất của giao dịch cặp

- **Ý tưởng:** hai tài sản có quan hệ cân bằng dài hạn thì chênh lệch (spread) giữa chúng dao động quanh một mức; khi spread lệch xa, mua chân rẻ, bán chân đắt, chờ hội tụ (GGR: "tìm hai cổ phiếu có giá di chuyển cùng nhau; khi spread nới rộng, bán bên thắng, mua bên thua").
- **Spread:** `s = log P_i − γ·log P_j − μ` (Vidyamurthy; γ là hệ số bảo hộ, μ là "phần bù" giữa hai tài sản); Krauss: log hay giá thô chỉ là hai cách nhìn, log không tự tạo hồi quy trung bình.
- **Mô hình hoá spread:** Ornstein–Uhlenbeck `dX = κ(μ − X)dt + σ dW`, độ lệch chuẩn cân bằng `σ_eq = σ/√(2κ)`, `half-life = ln2/κ` (xấp xỉ qua AR(1): hồi quy ΔX trên X_{t−1}, hệ số `b = e^{−κΔt}`). Lợi nhuận mỗi chu kỳ vào-ra `r = m − a − c` (a ngưỡng vào, m ngưỡng ra, c chi phí); chu kỳ ngẫu nhiên, Bertram tìm ngưỡng tối ưu theo lợi nhuận hoặc Sharpe trên đơn vị thời gian.
- **Tương quan ≠ đồng tích hợp:** tương quan cao của giá/lợi suất có thể giả; đồng tích hợp là có tổ hợp tuyến tính DỪNG của hai chuỗi không dừng (đã có ở F001).

## 2. Các cách chọn cặp (từ S088 trừ khi ghi khác)

| Cách | Chọn thế nào | Điểm mạnh | Điểm yếu đã nêu |
|---|---|---|---|
| **Khoảng cách (GGR)** | Chuẩn hoá chỉ số tổng lợi suất trong 12 tháng; ghép đối tác có tổng bình phương chênh lệch (SSD) nhỏ nhất, lấy top 5/20; giao dịch 6 tháng; vào khi lệch > 2σ (σ trong giai đoạn hình thành); ra khi hai đường cắt nhau (S089) | Không mô hình, dễ làm, ít bị snooping | SSD nhỏ nhất = phương sai spread + bình phương trung bình spread ⇒ chọn spread biến động THẤP (lợi nhuận hạn chế); không kiểm định đồng tích hợp; 32% cặp không hội tụ (Do & Faff) |
| **Tương quan lợi suất (Chen et al. 2012)** | Hình thành 5 năm; tương quan lợi suất tháng; so mỗi cổ phiếu với danh mục 50 đối tác tương quan cao nhất | Dùng thông tin nhiều hơn một đối tác (thô ~1,70%/tháng; một đối tác giảm ~1/3) | Tương quan không bảo đảm quan hệ cân bằng; có thể giả |
| **Đồng tích hợp (Vidyamurthy 2004)** | (1) tiền chọn theo giống nhau của lợi suất nhân tố chung; (2) hồi quy `p_i = μ + γ p_j + ε`, đo tần suất cắt trung bình (zero-crossing) thay cho kiểm định nghiêm ngặt; (3) vào khi lệch kσ | Cơ sở kinh tế lượng; tính được γ, half-life | Heuristic chưa kiểm nghiệm rộng; tối ưu k của tác giả sai (mất thứ tự thời gian) |
| **Đồng tích hợp có kiểm định** | Engle–Granger (ADF trên phần dư) và/hoặc Johansen; xếp theo t-stat ADF hoặc Sharpe trong mẫu; chọn top (Dunis 2010; Caldeira & Moura 2013) | Spread biến động gần gấp đôi cặp khoảng cách (Huck & Afawubo), hội tụ nhiều hơn | Dương tính giả khi quét nhiều cặp (mục 3); đồng tích hợp không bền |
| **Nhân tố / PCA / cụm** | Dư lượng sau khi trừ nhân tố (PCA hoặc ETF ngành), mô hình OU trên dư lượng (Avellaneda–Lee, S090); hoặc PCA → OPTICS gom cụm → ghép cặp trong cụm (Sarmento–Horta, S096) | Thu hẹp không gian tìm kiếm, bắt được cặp đa biến | PCA cần 10–30 nhân tố để giải thích ~50% phương sai; dùng PCA tĩnh cho dữ liệu thay đổi; cụm ít ổn định |
| **Chuỗi thời gian (OU, Kalman, Elliott 2005)** | Ước lượng trạng thái bằng Kalman/EM; ngưỡng theo `σ/√(2κ)` | Hệ số bảo hộ biến thiên (Kalman tốt nhất trong Dunis 2010) | Giả định Gauss; cần cặp có quan hệ cân bằng sẵn |
| **Copula** (Rad et al.) | Phụ thuộc phi tuyến giữa hai lợi suất | Số cơ hội ổn định sau 2009 | Ít ứng dụng thực nghiệm; lãi sau chi phí chỉ 5 bp/tháng (F210) |

**Quy tắc lọc khả năng giao dịch thường dùng:**
- Avellaneda–Lee: tốc độ hồi quy `κ > 252/30 = 8,4/năm` (thời gian hồi quy < nửa cửa sổ ước lượng 60 ngày), nếu không loại.
- Sarmento–Horta: Engle–Granger đạt; Hurst của spread < 0,5; half-life từ 1 ngày đến 365 ngày; ≥ 12 lần cắt trung bình mỗi năm trong giai đoạn hình thành.
- Do & Faff: cùng ngành (48 ngành Fama–French) và ưu tiên nhiều lần cắt không; Dunis: t-stat ADF và IR trong mẫu dự báo được IR ngoài mẫu.

## 3. Cạm bẫy thống kê khi quét nhiều cặp

- **Đa phép thử:** n tài sản cho `n(n−1)/2` cặp; ở mức 5% kỳ vọng 5% số cặp là dương tính giả (Caldeira–Moura 1.225 cặp ⇒ ~61; S&P 500 125.000 cặp ⇒ ~1.250 ở mức 1%). Cách xử lý được nêu: kiểm soát FWER (Cummins & Bucca 2012; Romano–Wolf), FDR, kiểm định SPA/White; Bonferroni = α/m (đã có ở F006).
- **Snooping khi thử nhiều biến thể:** Do & Faff thử 29 tổ hợp chọn lọc; Psaradellis dùng FDR cho 18.412 quy tắc.
- **Độ nhạy tham số:** Huck (2013): lãi rất nhạy với độ dài giai đoạn hình thành (tốt ở 6, 18, 24 tháng; sụt ở 12 tháng GGR chọn tuỳ ý).
- **Đồng tích hợp không bền:** Clegg (2014): ~5% cặp S&P 500 đồng tích hợp trong một năm điển hình, và 4,9% (ADF) / 4,5% (PP) cặp đã đồng tích hợp còn đồng tích hợp năm sau ≈ mức ngẫu nhiên (qua CXO, S097); Clegg–Krauss: tính chất đồng tích hợp "không bền", cú sốc cố định của từng công ty làm spread có thành phần bước ngẫu nhiên.
- **Không hội tụ:** 32% cặp khoảng cách không hội tụ (Do & Faff); lỗ khi buộc đóng.

## 4. Bằng chứng hiệu quả và suy giảm

- **GGR (S089, 1962–1997):** cổ phiếu CRSP, bỏ mã có ngày không giao dịch, 12 tháng hình thành, 6 tháng giao dịch, 2σ; vị thế giữ TB 1,5 tháng, 2,4 lần/cặp; lãi vượt trội ~12%/năm cho top cặp; "chờ một ngày" giảm lãi 5,98% → 3,99% (≈200 bp) ⇒ chi phí khứ hồi ước ≥ 83 bp/cặp (spread hiệu dụng 42 bp); Sharpe năm sau "chờ một ngày": top 5 1,52, top 20 2,13, so với 0,41 của thị trường; cặp ngẫu nhiên cùng ngành (bootstrap) cho p-value rất thấp. Bản RFS (S088): tới 11%/năm 1962–2002.
- **Suy giảm:** Do & Faff: lãi giảm do tỉ lệ cặp không hội tụ tăng, có chi phí gần như không lãi (S088, S097); Rad–Low–Faff (S093, Mỹ 1962–2014): lãi vượt trội tháng TB trước chi phí 91/85/43 bp (khoảng cách/đồng tích hợp/copula), sau chi phí 38/33/5 bp; từ 2009 số cơ hội của khoảng cách và đồng tích hợp giảm mạnh, copula ổn định; mọi phương pháp tốt hơn lúc biến động cao; Huck–Afawubo (S094): khoảng cách không có lãi vượt trội ý nghĩa, đồng tích hợp "high, stable and robust".
- **Clegg–Krauss (S091, S&P 500 1990–2015, sau chi phí, vốn đầu tư đầy đủ):** GGR / CI1 / CI2 lãi TB 1,11% / 1,61% / 1,09%/năm (vượt trội âm −1,80% / −1,31% / −1,82%), Sharpe −0,55 / −0,24 / −0,37; đồng tích hợp một phần (PCI) 12,3%/năm, Sharpe 1,11, ~6,5 lệnh/cặp/kỳ 6 tháng, thời gian giữ 16,7 so với 48–65 (đơn vị theo bảng, nghi ngày giao dịch) — tác giả tự so, cùng bộ dữ liệu.
- **Avellaneda–Lee (S090):** PCA Sharpe sau chi phí 1,44 (1997–2007) nhưng 0,9 (2003–2007); ETF 1,1 và cũng suy giảm sau 2002; dùng khối lượng ("thời gian giao dịch") nâng ETF lên 1,51 (2003–2007). Ngưỡng: mở khi |s| > 1,25; đóng bán khống khi s < 0,75; đóng mua khi s > −0,50 (chọn bằng mô phỏng 2000–2004).
- **Trong ngày:** Bowen et al. (S088, FTSE 100, nến 60 phút, 2007): ~20%/năm nhưng 15 bp chi phí + trễ thực thi 60 phút xoá sạch. Stübinger–Endres (S092, nến 1 phút, dầu khí S&P 500 1998–2015, chi phí 5 bp/cổ phiếu/nửa vòng): 98,55% trước và 60,61% sau chi phí, Sharpe 5,30; theo giai đoạn 3 năm sau chi phí: 1998–2000 247,6%, 2001–03 107,9%, 2004–06 42,4%, 2007–09 70,1%, **2010–12 −2,4% (Sharpe −0,29), 2013–15 +5,1% (Sharpe 0,57)**; tác giả cho rằng 1998–2000 có thể do bid-ask bounce (giá phân số) ⇒ số tổng thể bị chi phối bởi giai đoạn đầu.

## 5. Bằng chứng riêng cho ngoại hối, chỉ số, tài sản khác

- **Spread hợp đồng tương lai có liên kết vật lý (tốt):** crack spread (Girma–Paulson 1999: > 15%/năm sau USD 100/vòng), soy crush (Simon 1999), spark spread (Emery–Liu 2002); Cummins–Bucca (2012): 861 spread năng lượng 2003–2010 với kiểm soát snooping, lãi ngày của chiến lược tốt nhất 0,07–0,55%, Sharpe thường > 2. Spread vàng–bạc: không lãi (Wahab–Cohn 1994). Các liên kết này có biên chuyển đổi vật lý; không có tương đương trong danh sách symbol của hệ. (S088)
- **Chỉ số quốc tế:** có các ứng dụng đồng tích hợp (Alexander 1999 chỉ số quốc tế 1990–1998; Galenko–Popova–Popova 2012 chỉ số quốc tế 2003–2009, thống kê arbitrage đa biến lãi kỳ vọng dương) — chi tiết và chi phí CHƯA đọc (S088 bảng 3).
- **Chỉ số/ETF, hợp đồng tương lai (tóm tắt tìm kiếm, luận văn):** cặp ETF S&P 500–FTSE 100 có quan hệ dừng nhưng 85 ngày ngoài mẫu không lãi sau chi phí; chỉ 19/44 hợp đồng tương lai S&P 500 đồng tích hợp với spot theo cả Engle–Granger và Johansen. (S097)
- **Dữ liệu FDR trên chỉ số, tiền tệ, hàng hoá (S095, tóm tắt CXO):** 15 cặp (hợp đồng tương lai hàng hoá, chỉ số cổ phiếu, tiền tệ so với USD), 18.412 quy tắc kỹ thuật (trung bình và động lượng) trên chênh lệch lợi suất ngày 1990–2016, chi phí 0,02–0,05% một chiều. Trong mẫu: FDR dự báo được 5/15 cặp theo lãi TB, 14/15 theo Sharpe; **ngoài mẫu 2016: Sharpe dương ở 4/15 cặp, mọi danh mục nhiều cặp âm — tác giả kết luận "không hấp dẫn" sau hiệu chỉnh.**
- **Ngoại hối (tóm tắt tìm kiếm):** nghiên cứu 6 cặp tiền tệ (EUR/USD, GBP/USD, USD/CAD, USD/INR, USD/JPY, USD/NZD) "rất ít cặp tốt"; luận văn 2013: EUR/USD–GBP/USD không đồng tích hợp ở 5%; không thấy nghiên cứu có đánh giá chi phí về hồi quy trung bình trong ngày của spread này. (S097)
- **`suy-luận`, kiểm bằng số học:** `log(EURUSD) − log(GBPUSD) = log(EURGBP)` (đồng nhất thức tam giác, bỏ qua spread): cặp "EURUSD–GBPUSD tỉ lệ 1" chính là tỷ giá chéo EURGBP; tương tự EURUSD×USDJPY = EURJPY, EURUSD×USDCAD = EURCAD. Giao dịch cặp giữa các cặp ngoại hối chính vì vậy = giao dịch hồi quy trung bình của một tỷ giá chéo (hoặc rổ) với chi phí hai chân; nếu nhà môi giới có chéo EURGBP thì giao dịch trực tiếp rẻ hơn một chân spread.

## 6. Quy trình tìm cặp đề xuất (tổng hợp từ các nguồn trên; ngưỡng là quy ước của nguồn, không phải chuẩn)

1. **Tiên nghiệm kinh tế trước** (thu hẹp không gian, giảm đa phép thử): cùng nhóm/khu vực/yếu tố (Do & Faff, Dunis, Vidyamurthy).
2. **Sàng lọc nhanh:** tương quan lợi suất cao và/hoặc SSD nhỏ trên giá chuẩn hoá (trên giá đã chuẩn hoá, SSD nhỏ nhất ⇔ tương quan Pearson lớn nhất; Perlin); đồng thời yêu cầu phương sai spread đủ lớn so với chi phí (khắc phục điểm yếu của SSD).
3. **Kiểm định đồng tích hợp trên log giá có hệ số chặn:** Engle–Granger (ADF trên phần dư, hệ số hiệu chỉnh sai số âm), đối chiếu Johansen; **hiệu chỉnh đa phép thử** (Bonferroni/Holm/FDR hoặc FWER kiểu Cummins–Bucca).
4. **Lọc khả năng giao dịch:** half-life qua AR(1) (A–L: τ < nửa cửa sổ ước lượng; S–H: 1 ngày–365 ngày), Hurst < 0,5, ≥ 12 lần cắt trung bình/năm, biên độ spread × số vòng kỳ vọng > chi phí khứ hồi cả hai chân.
5. **Kiểm tra độ bền:** chia nhiều giai đoạn con, cửa sổ cuộn; đồng tích hợp ít bền (Clegg) ⇒ tái ước lượng định kỳ; xếp hạng bằng t-stat ADF/Sharpe trong mẫu và chỉ giao dịch số ít cặp đầu (Dunis), rồi đánh giá NGOÀI mẫu.
6. **Hệ số bảo hộ:** OLS tĩnh, cuộn hay Kalman (Dunis: Kalman tốt nhất trong mẫu của họ); tính cả hệ số chặn.
7. **Quy tắc vào/ra:** ±2σ (GGR) hoặc s-score ±1,25 với ra 0,5/0,75 (A–L) hoặc ngưỡng tối ưu Bertram; **bắt buộc có thoát khi mất đồng tích hợp/không hội tụ** (32% cặp không hội tụ) và giới hạn thời gian giữ.

## 7. Áp dụng vào 15 symbol của hệ (`suy-luận`, chưa đo)

- **Số cặp:** 15 symbol ⇒ 105 cặp = 36 chỉ số–chỉ số (9 chỉ số) + 6 ngoại hối–ngoại hối + các cặp có vàng/BTC. Ở 5% không hiệu chỉnh kỳ vọng ~5,25 dương tính giả; Bonferroni 0,05/105 = 0,000476. Nếu chỉ thử cặp có tiên nghiệm (ví dụ nhóm Mỹ 3 cặp, nhóm châu Âu 6 cặp, HK50–J225, vài cặp ngoại hối) ~10–16 cặp thì ngưỡng nới thành 0,003–0,005.
- **Không có cặp nào trong danh sách có biên chuyển đổi vật lý** (khác crack/crush/spark): mọi cặp là "giá trị tương đối thống kê" cần kiểm chứng và tái kiểm định.
- **Ngoại hối:** các cặp chính chia sẻ chân USD ⇒ spread = tỷ giá chéo (mục 5); bằng chứng đồng tích hợp yếu.
- **Chỉ số:** các chỉ số cùng khu vực khác cấu phần (Nasdaq nghiêng công nghệ, Dow tính theo giá) ⇒ tỉ số dịch chuyển theo chủ đề; cần kiểm bằng cửa sổ ngắn cuộn thay vì nhiều năm.
- **Vàng, BTC:** không có liên kết kinh tế xác lập với phần còn lại; chỉ cho qua nếu thống kê rất mạnh sau hiệu chỉnh.
- **Hợp đồng của hệ:** mỗi (symbol, khung) phát một `signal` ±1 vào lệnh; cặp cần hai chân phối hợp, tỉ lệ bảo hộ (quy đổi giá trị hợp đồng khác nhau) và thoát khi hội tụ — hệ chỉ phát tín hiệu vào lệnh nên cần quyết thiết kế riêng (chưa làm).

## 8. Việc cần quyết (chưa làm gì)

1. Có muốn quét thử 105 cặp trên SQL của hệ (chỉ SELECT) theo mục 6 — log giá H1/H4, Engle–Granger với hiệu chỉnh đa phép thử, half-life, Hurst, cắt trung bình, độ bền giữa các năm — để xem có cặp nào đáng giữ hay không.
2. Nếu có cặp: thiết kế tín hiệu hai chân và thoát nằm ở đâu.
3. Hay chỉ giữ ở mức ghi nhận.

## 9. Thực hành của cộng đồng forex (thêm 2026-10-22; nguồn thực hành, giai thoại, KHÔNG có số liệu hiệu quả kiểm chứng được)

- **Nguồn:** S098 bài thực hành FXEmpire "Forex statistical arbitrage made simple" (tác giả kể kinh nghiệm cá nhân); S099 diễn đàn BabyPips "Statistical Arb/Pairs trading strategy!" (03/2012, người khởi xướng Kelton); S100 các trang bán chỉ báo/EA trên MQL5 và một bài TradersPost (CHỈ tóm tắt tìm kiếm, là tuyên bố của nhà bán).
- **Cặp hay nêu:** EURUSD–GBPUSD (phổ biến nhất, được chọn vì spread thấp; người dùng diễn đàn nhận xét thực chất là giao dịch EURGBP), AUDUSD–NZDUSD và chéo AUDNZD (tương quan "thường trên 0,7"), AUDUSD–GBPUSD, EURUSD–NZDUSD (vendor), USDCAD–USDNOK, NZDCHF–AUDCHF, EURJPY–CHFJPY, CADJPY với dầu; EURCHF/USDCHF được nêu làm ví dụ quan hệ vỡ (SNB 2015).
- **Cách chọn:** lý do kinh tế (chân tiền tệ chung, tiền tệ hàng hoá, khu vực) → sàng bằng tương quan (ma trận ~90 ngày; ngưỡng > 0,7 ở S098, > 0,80 ở diễn đàn) → ưu tiên spread thấp; một số chỉ nhìn chồng biểu đồ. Tác giả S098 cho rằng kiểm định đồng tích hợp "không hữu ích" khi giao dịch; vendor/hướng dẫn khác nhấn mạnh đồng tích hợp thay cho tương quan. Cảnh báo: tương quan AUD–NZD rơi từ ~80% xuống ~15% giữa 2014 và 2016.
- **Khung vào lệnh:** không đồng thuận. Diễn đàn: M1 (trước đó thử M5/M15/M30), giữ 30 phút–vài giờ; người khác D1, giữ 1–7 ngày. S098: giữ "tuần hoặc tháng" và cho rằng stat arb tần suất trung–cao rất khó với trader độc lập. Vendor: "chạy mọi khung" (tuyên bố, không kiểm chứng).
- **Tín hiệu mua/bán:** (a) z-score của spread `A − β·B` (β thường 1, cân bằng giá trị đô la hai chân): vào khi |z| ≈ 2 (vendor 1,5–2,0; một chỉ báo 2,5 với cửa sổ 100 nến; tác giả S098 vào ở 2,5σ), z > +2 bán spread (bán A, mua B), z < −2 mua spread; thoát khi z ≈ 0 (vendor: ngưỡng thoát nhỏ hơn ngưỡng vào); S098 không dùng stop-loss mà khuyến nghị time stop (số ngày dựa vào thời gian hội tụ lịch sử); (b) chồng hai biểu đồ (diễn đàn): vào khi lệch ~20 pip, thêm lệnh ở ~40 và ~60 pip, mua cặp yếu bán cặp mạnh, lot bằng nhau, thoát khi hai đường chạm nhau, không stop.
- **Rủi ro cộng đồng tự nêu:** cộng thêm vào vị thế đang lỗ là mẫu thổi tài khoản kinh điển; chi phí spread/hoa hồng/trượt giá và phí qua đêm (swap) hai chân; quan hệ vỡ, can thiệp ngân hàng trung ương; phân phối lệch âm (thắng nhỏ thường xuyên, thua lớn hiếm); tác giả S098 tự kể lỗ nặng khi cộng thêm vào AUD/NZD. Kết quả tự báo (+43% tài khoản trong 13 lệnh; +14% hai tuần; 16% trong 3 tuần) không có sao kê hay nhật ký lệnh.
- **Ý nghĩa cho hệ (`suy-luận`):** trong 4 cặp ngoại hối của hệ chỉ có EURUSD–GBPUSD trùng với cặp cộng đồng hay nêu nhất, và nó chính là EURGBP (F216); USDJPY, USDCAD không có cặp quen thuộc tương ứng trong danh sách này (USDCAD thường đi với USDNOK hoặc dầu, không có trong hệ).

## 10. Sáu cặp từ 4 symbol ngoại hối của hệ = sáu tỷ giá chéo (`suy-luận`, đồng nhất thức; chưa đo tương quan thật)

4 symbol (EURUSD, GBPUSD, USDJPY, USDCAD) cho 6 cặp. Với hệ số bảo hộ 1 trên log giá, mỗi cặp đúng bằng một tỷ giá chéo (bỏ qua spread):

| Cặp | Dấu tương quan thường gặp (theo cấu trúc báo giá) | Spread (log) | = tỷ giá chéo | Spread cao ⇒ |
|---|---|---|---|---|
| EURUSD – GBPUSD | dương (USD là đồng định giá cả hai) | EURUSD − GBPUSD | EURGBP | bán EURUSD, mua GBPUSD (ngược chiều) |
| USDJPY – USDCAD | dương (USD là đồng gốc cả hai) | USDJPY − USDCAD | CADJPY | bán USDJPY, mua USDCAD (ngược chiều) |
| EURUSD – USDJPY | âm | EURUSD + USDJPY | EURJPY | bán cả hai (cùng chiều) |
| EURUSD – USDCAD | âm | EURUSD + USDCAD | EURCAD | bán cả hai (cùng chiều) |
| GBPUSD – USDJPY | âm | GBPUSD + USDJPY | GBPJPY | bán cả hai (cùng chiều) |
| GBPUSD – USDCAD | âm | GBPUSD + USDCAD | GBPCAD | bán cả hai (cùng chiều) |

- Nhân tố USD vào hai nhóm với dấu ngược nhau (nhóm "USD là đồng định giá": EURUSD, GBPUSD; nhóm "USD là đồng gốc": USDJPY, USDCAD) nên cùng nhóm thường tương quan dương, khác nhóm thường âm; độ lớn thật chưa đo.
- Tương quan dương ⇒ spread là HIỆU, hai chân ngược chiều; tương quan âm ⇒ spread là TỔNG, hai chân cùng chiều (hoặc đảo nghịch một chân rồi dùng hiệu). Cùng một bộ máy tính, chỉ khác dấu.
- Hệ số bảo hộ khác 1 (do hồi quy) cho tỷ giá chéo có trọng số, còn lại phơi nhiễm USD ròng.
- Nguồn thực hành (S098) nêu "tương quan ma trận ~90 ngày" nhưng KHÔNG nêu cỡ nến, không nói tương quan giá hay lợi suất; Chen et al. (S088) dùng tương quan lợi suất tháng, Perlin dùng giá đã chuẩn hoá (SSD nhỏ nhất ⇔ tương quan lớn nhất).

