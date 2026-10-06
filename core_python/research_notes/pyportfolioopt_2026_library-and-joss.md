# S004 — PyPortfolio/PyPortfolioOpt (thư viện tối ưu danh mục) + bài JOSS đi kèm

Trạng thái nạp: đã đọc README đầy đủ và bài JOSS; đã tái lập ví dụ README; đã đối chiếu với S001–S003 (không trùng chủ đề, mục 8).

Nhãn trạng thái: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Nguồn

- Loại: **thư viện mã nguồn mở** (phần mềm) có tài liệu và một bài báo phần mềm (JOSS). Không phải nghiên cứu thực nghiệm về hiệu quả đầu tư.
- URL: https://github.com/PyPortfolio/PyPortfolioOpt (đã bỏ tham số theo dõi `fbclid`). Tài liệu: https://pyportfolioopt.readthedocs.io/ (repo có thư mục `docs/` dạng `.rst`).
- Repo: tạo 2018-05-29; commit mới nhất lúc đọc `a6638d2e06dae6f444fd022cfd4b3c528902a85b` (2026-07-07, sửa link README); bản phát hành mới nhất `v1.6.0` (2026-02-26); giấy phép MIT; 6072 sao / 1176 fork / 117 issue mở lúc đọc (chỉ phản ánh độ phổ biến và mức hoạt động, không phải độ đúng). Người đóng góp nhiều nhất: robertmartin8 (623), phschiele (36), SeaPea1 (31), fkiraly (21).
- Bài JOSS: Martin, R. A. (2021). *PyPortfolioOpt: portfolio optimization in Python*. Journal of Open Source Software, 6(61), 3066. https://doi.org/10.21105/joss.03066. Nộp 2021-02-25, đăng 2021-05-07, biên tập viên Vissarion Fisikopoulos, người phản biện @omendezmorales và @SteveDiamond, giấy phép CC BY 4.0; mô tả phiên bản v1.4.1.
- Ngày đọc: 2026-10-03.
- Phạm vi đã đọc: **README đầy đủ** (sha256 `aa39aacf964b967b8b23063f91eeb5997136e10e40fb62e7c5a848e348a00123`); **bài JOSS trang 1–4** (sha256 PDF `e2a750e31eeff865a3888ba3fd8d060691386a9233bffe08ac93dfc15fcd4c44`; chưa đọc các trang tham khảo còn lại nếu có); **docs `.rst`: chỉ đọc các đoạn then chốt qua tìm kiếm** (UserGuide, RiskModels, BlackLitterman, OtherOptimizers, MeanVariance, FAQ — không đọc toàn bộ); **mã nguồn thư viện chưa đọc** (chỉ chạy thử ví dụ README).

## 2. Thư viện cung cấp gì (theo README, `nguồn-báo-cáo`)

- Ước lượng lợi suất kỳ vọng: trung bình lịch sử, trung bình có trọng số mũ, CAPM.
- Mô hình rủi ro (hiệp phương sai): mẫu, semicovariance, hàm mũ, co rút (thủ công, Ledoit–Wolf với 3 mục tiêu, Oracle Approximating Shrinkage), Minimum Covariance Determinant.
- Hàm mục tiêu: Sharpe cực đại, phương sai cực tiểu, efficient return / efficient risk, utility bậc hai cực đại.
- Ràng buộc: long/short, market-neutral (chỉ với efficient risk/return), giới hạn vị thế, chính quy hoá L2 (tham số `gamma`).
- Black–Litterman; trình tối ưu khác: mean-semivariance, mean-CVaR, Hierarchical Risk Parity, Critical Line Algorithm; `DiscreteAllocation` đổi trọng số thành số cổ phiếu mua được.
- README nêu rõ: "nothing about this project constitutes investment advice".

## 3. Tôi đã thử gì (`tái-lập`)

Cài `pyportfolioopt` 1.6.0 vào môi trường tạm riêng (Python 3.14.4, không động tới venv của hệ), chạy đúng ví dụ "Getting started" của README trên `tests/resources/stock_prices.csv` của repo (sha256 `1f3bc5da6d4b589a34704be69a1a8cd382f643dbedbefbc73319c3b21d9d9c71`; 7126 dòng × 20 mã, 1989-12-29 → 2018-04-11).

- Trọng số tối ưu Sharpe cực đại khớp README **đến 4 chữ số thập phân cho cả 20 mã** (GOOG 0,0458; AAPL 0,0674; FB 0,2008; BABA 0,0849; AMZN 0,0352; BBY 0,0159; MA 0,3287; PFE 0,2039; SBUX 0,0173; còn lại 0).
- Hiệu suất khớp: lợi suất kỳ vọng 29,9%, biến động 21,8%, Sharpe 1,38.
- Phân bổ rời rạc 10 000 USD khớp: MA 19, PFE 57, FB 12, BABA 4, AAPL 4, GOOG 1, SBUX 2, BBY 2, còn dư 17,46 USD.
- 11/20 trọng số bằng đúng 0.
- Ý nghĩa: ví dụ README **chạy lại được và ra đúng số**. Nhưng Sharpe 1,38 là **trong mẫu** (tối ưu và đo trên cùng dữ liệu) trên 20 mã do tác giả "informally selected" (README) và có nhiều mã tăng mạnh (GOOG, AAPL, FB, AMZN, MA…); README tự nói "interesting but not useful in itself". Không có kiểm tra ngoài mẫu trong README.

## 4. Khẳng định về hiệu quả ngoài mẫu trong tài liệu và mức dẫn chứng (`nguồn-báo-cáo`, chưa kiểm)

| Khẳng định (vị trí) | Dẫn chứng tại chỗ |
|---|---|
| MVO cho nhiều trọng số bằng 0; "a large body of research" cho thấy điều đó làm danh mục MVO kém hơn ngoài mẫu (README) | không có trích dẫn |
| Ma trận hiệp phương sai mẫu có sai số ước lượng lớn, nguy hiểm trong MVO vì bộ tối ưu dồn trọng số vào sai số (README) | không có trích dẫn |
| Danh mục phương sai tối thiểu "perform much better out of sample" (docs RiskModels) | Kritzman, Page & Turkington (2010) |
| Danh mục phương sai tối thiểu "consistently outperform" Sharpe cực đại ngoài mẫu, kể cả đo bằng Sharpe (docs UserGuide) | không có trích dẫn tại chỗ |
| HRP "seems to robustly outperform" MVO ngoài mẫu (docs UserGuide) | López de Prado (2016) ở trang OtherOptimizers |
| Trung bình lịch sử là prior "completely uninformative" (docs BlackLitterman) | không có trích dẫn tại chỗ |

Cảnh báo có trong docs: `efficient_risk` / `efficient_return` với mục tiêu vô lý sẽ "fail silently and return weird weights. Caveat emptor" (docs MeanVariance); ràng buộc số lượng tài sản không lồi nên cần bộ giải số nguyên (docs FAQ).

## 5. Bài JOSS nói gì (đã đọc trang 1–4)

- Là bài báo mô tả phần mềm: tóm tắt, tuyên bố nhu cầu (so sánh mã CVXPY thuần với API `EfficientFrontier`), danh sách phương pháp v1.4.1, lộ trình (tối ưu drawdown có điều kiện, risk parity, mô-men bậc cao, mô hình nhân tố), lời cảm ơn, tài liệu tham khảo. **Không có đánh giá hiệu suất đầu tư thực nghiệm nào.**
- Tuyên bố trong bài: "downloaded over 160,000 times, cited in academic publications (Jansen, 2020; Snow, 2020)" (tính tại thời điểm 2021) và "to the best of our knowledge, PyPortfolioOpt was the first project offering an API for general portfolio optimization" — `nguồn-báo-cáo`, tôi không kiểm chứng.
- Kết luận: bài báo là dẫn chứng cho việc phần mềm tồn tại, được phản biện về mặt phần mềm và mô tả đúng chức năng; **không** là dẫn chứng rằng các phương pháp tối ưu danh mục sinh lời ngoài mẫu.

## 6. Tài liệu mà nguồn dẫn (chưa mở; chép đúng chuỗi từ nguồn, dùng làm danh sách cần đọc)

- Markowitz, H. (1952). Portfolio Selection. *The Journal of Finance*, 7(1), 77–91. https://doi.org/10.1111/j.1540-6261.1952.tb01525.x
- Kritzman, M., Page, S. & Turkington, D. (2010). In defense of optimization: The fallacy of 1/N. *Financial Analysts Journal*, 66(2), 31–39.
- López de Prado, M. (2016). Building Diversified Portfolios that Outperform Out of Sample. *The Journal of Portfolio Management*, 42(4), 59–69. https://doi.org/10.3905/jpm.2016.42.4.059 (SSRN 2708678)
- Bailey, D. H. & López de Prado, M. (2013). An Open-Source Implementation of the Critical-Line Algorithm for Portfolio Optimization. *Algorithms*, 6(1), 169–196. https://doi.org/10.3390/a6010169
- Black, F. & Litterman, R. (1991). *The Journal of Fixed Income* (docs ghi tiêu đề "Combining investor views with market equilibrium"; bài JOSS ghi "Asset Allocation", 1(2), 7–18, https://doi.org/10.3905/jfi.1991.408013).
- Ledoit, O. & Wolf, M. "Honey, I Shrunk the Sample Covariance Matrix", *The Journal of Portfolio Management*, 30(4), 110–119, https://doi.org/10.3905/jpm.2004.110 (docs ghi năm 2003, JOSS ghi 2004 — **lệch năm giữa hai nơi trong chính nguồn**). Thêm Ledoit & Wolf (2001) (docs không ghi tên tạp chí).
- Estrada, J. "Mean-Semivariance Optimization: A Heuristic Approach" (docs ghi năm 2006, JOSS ghi 2008 *Journal of Applied Finance* 18(1) — **lệch năm trong chính nguồn**); SSRN 1028206.
- Chen et al. (2010). Shrinkage Algorithms for MMSE Covariance Estimation. *IEEE Transactions on Signal Processing*, 58(10), 5016–5029 (arXiv 0907.4698).
- Walters, J. (2013). The Factor Tau in the Black-Litterman Model. SSRN 1701467. Walters (2014) The Black-Litterman Model in Detail.
- Boyd, S. & Vandenberghe, L. (2004). *Convex Optimization*. Cambridge University Press.

## 6b. Lỗi/mâu thuẫn nhỏ tôi thấy trong chính nguồn (`lỗi-nguồn`)

Hai chỗ lệch năm trích dẫn nêu ở mục 6 (Ledoit–Wolf 2003/2004; Estrada 2006/2008) giữa docs và bài JOSS; docs ghi tên bài Black–Litterman khác bài JOSS; README gõ nhầm "constitues", "responsibiltiy". Không ảnh hưởng chức năng.

## 7. Đánh giá và mức dùng (đánh giá của tôi)

- **Mức tin cậy cao** cho việc thư viện làm đúng những gì README mô tả ở ví dụ đã chạy (tái lập đúng từng số). Chưa kiểm các hàm khác.
- **Mức tin cậy thấp / chưa kiểm** cho mọi khẳng định "vượt trội ngoài mẫu": phần lớn không kèm trích dẫn tại chỗ, và hai trích dẫn rõ ràng (Kritzman et al. 2010; López de Prado 2016) tôi chưa đọc.
- Liên quan tới og_program: hệ chỉ sinh tín hiệu một công cụ; chia vốn/khối lượng là việc của OF. Thư viện này liên quan gián tiếp (phân bổ vốn giữa các nguồn tín hiệu hoặc các mã) nếu sau này làm; chưa dẫn tới khuyến nghị nào, chưa thử trên dữ liệu của hệ.

## 8. Đối chiếu với cái cũ

Không trùng chủ đề với S001–S003 (giao dịch cặp, stat-arb, sách Chan): đây là chủ đề mới "xây dựng danh mục / phân bổ vốn". Điểm chung duy nhất là chủ đề trong-mẫu so với ngoài-mẫu (liên hệ F005, F007, F016).

## 9. Bước kiểm chứng tiếp theo (đề xuất, chưa làm)

1. Đọc Kritzman et al. (2010) và López de Prado (2016) để biết bằng chứng thật của hai khẳng định ngoài mẫu.
2. Nếu quan tâm: tự kiểm ngoài mẫu trên dữ liệu của hệ (huấn luyện rolling, đánh giá kỳ sau, so với chia đều 1/N, có chi phí).
