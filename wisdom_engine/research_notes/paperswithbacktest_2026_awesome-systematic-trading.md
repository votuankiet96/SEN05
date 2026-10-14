# S005 — paperswithbacktest/awesome-systematic-trading (danh sách tuyển chọn + thống kê của nhà cung cấp)

Trạng thái nạp: đã đọc README (phần mở đầu, thống kê, bảng chiến lược, cấu trúc các mục) và các script dựng bảng; **không mở bất kỳ liên kết nào trong danh sách**. Đã đối chiếu với S001–S004 (mục 9).

Nhãn trạng thái: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Nguồn

- Loại: **danh sách tài nguyên tuyển chọn (awesome list) do một công ty duy trì**, kèm bảng "chiến lược" và thống kê tự công bố. Không phải nghiên cứu; các mục trong danh sách chỉ là con trỏ tới nguồn khác, **việc có mặt trong danh sách không phải là bằng chứng rằng mục đó đúng**.
- URL: https://github.com/paperswithbacktest/awesome-systematic-trading. Trang chủ khai báo: https://paperswithbacktest.com (sản phẩm thương mại).
- Repo: tạo 2022-02-05; commit mới nhất lúc đọc `ddfee8bb548bd6914191cb8fdb695d533ae16b0d` (2026-09-28, "Merge pull request #115 …/codex/github-acquisition-path"); **không khai báo giấy phép**; 14 526 sao / 1741 fork / 18 issue mở lúc đọc (độ phổ biến, không phải độ đúng). Người commit chính: edarchimbaud (156 commit; người thứ hai 2).
- **Xung đột lợi ích cần ghi**: phần đầu README quảng bá sản phẩm trả phí của chính chủ repo ("A free account includes one full strategy unlock, one clone and $1 of research-agent credit… Backtester includes 100 strategy unlocks per month…") và dùng liên kết có tham số theo dõi `utm_*`. Điều này không làm sai nội dung nhưng nghĩa là các thống kê trong README do bên bán dịch vụ đó tự công bố.
- Ngày đọc: 2026-10-03. Tải README (92 960 byte, 663 dòng, sha256 `be1eda7e86bb27ab0d3f4c0399c8d421aa5adfb598ef7f43362fedd5ac227c41`), 4 script, `paper_meta.json`, 59 file `static/strategies/*.py`.
- Phạm vi đã đọc: README phần mở đầu (dòng 1–135) và bảng "Strategies" (dòng 386–495) đọc từng dòng; các mục Libraries/Books/Videos/Blogs/Courses chỉ đếm số dòng bảng, không đọc từng mục; `build_strategies_table.py` đọc phần đầu và phần thực thi; `update_counts.py` chỉ đọc đầu file; `paper_meta.json` chỉ phân tích cấu trúc; mã `static/strategies` chỉ quét bằng tìm kiếm và đọc một mẫu.

## 2. Cấu trúc và số đếm (đã xác minh)

README tự khai: 136 thư viện, 55 sách, 22 video, 61 dòng chiến lược. **Tôi đếm lại từ các bảng và khớp cả bốn**: thư viện 27+5+10+10+7+2+7+3+1+5+15+4+10+5+7+10+4+4 = 136; sách 7+2+5+4+23+7+7 = 55; video 22; bảng chiến lược 12+12+3+4+8+10+12 = 61. (Repo có `scripts/update_counts.py` tự đếm lại để các số không trôi, và workflow kiểm tra liên kết hỏng.) Ngoài ra có blogs (14 dòng) và courses (11 dòng).

## 3. Thống kê nhà cung cấp tự công bố (`nguồn-báo-cáo`, **chưa kiểm chứng được**)

Nguyên văn README: "We have coded and run 4,843 of these papers over their own full history."

- Sharpe trung vị của các bản tái hiện: 0,37; 48% đạt t-stat > 1,96 ("Half the published record cannot be distinguished from zero on its own sample").
- Cửa sổ kiểm thử trung vị 34 năm.
- Beta trung vị +0,17 so với S&P 500; sau khi loại beta, information ratio trung vị 0,21.
- Trên 2838 bài có số liệu cả hai phía ngày công bố: "no measurable decay after publication once the market period is controlled for, to within a fifth of a percentage point a year".
- Phương pháp và lưu ý: nằm ở wiki của họ (`paperswithbacktest.com/wiki`), **tôi chưa đọc**.

Tại sao không kiểm chứng được: `build_strategies_table.py` lấy chuỗi NAV từ dataset Hugging Face `paperswithbacktest/Strategies-NAV` và cần `HF_ACCESS_TOKEN`; tôi gọi API công khai của dataset và nhận **HTTP 401** (không công khai). `scripts/paper_meta.json` chỉ có `title` và `markets` của 3803 bài (cộng 3 mục bước pipeline), **không có số liệu hiệu suất**; con số 4843 trong README cũng không khớp 3803 trong file này (không rõ vì sao — có thể file siêu dữ liệu chưa đủ).

## 4. Bảng "Strategies" và cách nó được dựng (đã đọc)

- README: "Showing the 61 strongest of 1,687 replications that clear a t-statistic of 1.96 over at least 10 years, up to 12 per asset class. Sharpe ratios are measured on each strategy's own active window, not on a common calendar, and are gross of trading costs. Series with an annualised volatility outside 1% to 100% are treated as degenerate and dropped."
- Script: Sharpe = trung bình/độ lệch chuẩn của `pct_change` NAV × √252 (sau khi cắt phần đầu/cuối phẳng); `years = số lợi suất / 252`; `t = Sharpe × √years` (giả định lợi suất độc lập, không hiệu chỉnh tự tương quan, **không hiệu chỉnh nhiều phép thử**); lọc `t > 1,96`, `years ≥ 10`, volatility 1–100%, phải có tiêu đề và nhóm tài sản; mỗi nhóm lấy 12 dòng Sharpe cao nhất.
- Hệ quả (rút từ chính các điều kiện lọc): bảng là **lát cắt trên cùng đã chọn theo kết quả** của hơn 4000 ứng viên, trước chi phí → các Sharpe trong bảng (đến 3,39) không phải kỳ vọng hợp lý cho một chiến lược chọn ngẫu nhiên trong danh mục. README tự nói rõ điều này ("the strongest", "gross of trading costs").

Số học tôi tính lại từ công thức của script: với `t = Sharpe × √năm`, cần ≈ (1,96 / Sharpe)² năm để đạt t = 1,96; Sharpe 0,4 → 24,0 năm (khớp câu "about 24 of them" trong README). Giả định lợi suất độc lập, đuôi nhẹ — thực tế đòi hỏi dài hơn nếu có tự tương quan hay đuôi dày (`suy-luận`).

## 5. Điểm bất thường tôi thấy trong bảng (`lỗi-nguồn` / `suy-luận`)

1. **Nhóm "Cryptocurrencies": 6/8 dòng có "Years tested" từ 22 đến 35 năm** (23, 16, 16, 34, 34, 35, 22, 35), trong khi thị trường tiền mã hoá không có dữ liệu dài như vậy (đây là kiến thức nền của tôi, chưa trích nguồn). Mã cho thấy nhóm lấy từ **trường `markets` trong siêu dữ liệu bài báo**, còn "years" đo từ **NAV bản tái hiện**. `suy-luận`: bản tái hiện có thể không chạy trên chính tài sản mà bài báo nghiên cứu (hoặc dùng proxy); không thể xác nhận vì không có NAV.
2. **Nhiều dòng "strategy" là bài báo không mô tả một chiến lược giao dịch** theo tiêu đề: "Important Characteristics, Weaknesses and Errors in German Equity Data from Thomson" (Sharpe 1,68, t 10,2), "Analytical Solution for Kelly's Criterion for Multiple Outcomes", "Optimal Annuity Risk Management", "Inconsistent investment and consumption problems", "Rational Decision-Making Under Uncertainty: Observed Betting Patterns on a Biased Coin". README không giải thích cách một bài được ánh xạ thành chuỗi NAV có thể giao dịch (wiki chưa đọc). Vì vậy các số trong bảng nên được hiểu là **đầu ra của quy trình tái hiện của họ**, không phải khẳng định của bài báo gốc.
3. `paper_meta.json` có một "thị trường" mang giá trị `**Datasets:**` — dấu hiệu siêu dữ liệu được trích tự động và có nhiễu.

## 6. Các mã `static/strategies` (59 file, bản QuantConnect cũ)

- 58/59 file có dòng chú thích dẫn `quantpedia.com/strategies/...`; 25 file ghi "QC implementation".
- 49/59 dùng `CustomFeeModel` với phí = giá × số lượng × 0,00005 (**0,005% giá trị lệnh ≈ 0,5 bp mỗi lệnh**); tìm kiếm không thấy mô hình trượt giá. 20/59 phụ thuộc lớp dữ liệu riêng (`QuantpediaFutures`, `QuandlValue`, `data_tools`) nên không chạy được nếu không có dữ liệu đó.
- `suy-luận`: mức phí cố định 0,5 bp không đại diện cho mọi loại tài sản.
- Mẫu đọc: `asset-class-trend-following.py` (5 ETF, giữ khi trên SMA 210 ngày, rebalance hàng tháng, bắt đầu 2000-01-01).

## 7. Đã thử và chưa tái lập

- Đã làm: đếm lại số lượng (mục 2), kiểm tra số học (1,96/Sharpe)² (mục 4), gọi thử API Hugging Face (401).
- **Chưa tái lập** thống kê tổng thể (dữ liệu NAV không công khai) và bất kỳ Sharpe nào trong bảng; chưa chạy mã `static/strategies` (cần QuantConnect/dữ liệu riêng).

## 8. Đánh giá và mức dùng (đánh giá của tôi)

- **Dùng như bản đồ khám phá nguồn**: có mục lục thư viện/sách/video theo chủ đề; các số đếm đúng. Các mục cụ thể phải được mở và đánh giá từng cái mới được nạp (chưa làm).
- **Không dùng làm bằng chứng hiệu quả chiến lược**: thống kê do bên bán dịch vụ tự công bố, dữ liệu gốc không công khai, bảng chọn theo kết quả, trước chi phí, có bất thường ở mục 5.
- Có một điểm đáng ghi như "tiên nghiệm thận trọng" (vẫn là `nguồn-báo-cáo`): chính nhà cung cấp nói Sharpe trung vị của các bài báo được tái hiện chỉ 0,37 trước chi phí và một nửa không phân biệt được với 0. Phù hợp (không chứng minh) với việc S001–S003 cho thấy các con số đẹp thường giảm mạnh khi sửa cài đặt.
- Liên quan tới og_program: chưa dẫn tới khuyến nghị nào; có thể dùng bảng chủ đề để tìm nguồn về các họ chiến lược đang chạy (xu hướng/hồi quy trên chỉ báo) — cần bạn chọn mục cụ thể để nạp.

## 9. Đối chiếu với cái cũ

- **Củng cố** F013/F015/F016/F018 ở cấp độ chung: số hiệu suất công bố nhạy với cài đặt và cửa sổ; ở đây là nhà cung cấp tự thừa nhận Sharpe trung vị thấp.
- **Liên hệ** F021 (S004): nhận định "nhiều khẳng định ngoài mẫu thiếu dẫn chứng" cũng áp dụng cho README này (thống kê không kèm dữ liệu).
- Không trùng chủ đề trực tiếp với S001–S004: đây là nguồn mức "chỉ mục + khảo sát thống kê".

## 10. Bước kiểm chứng tiếp theo (đề xuất, chưa làm)

1. Đọc wiki của họ (phương pháp tái hiện và cách ánh xạ bài báo → chuỗi NAV) trước khi dựa vào bất kỳ con số nào.
2. Chọn 1–2 mục cụ thể trong danh sách (sách/bài báo) để nạp thành nguồn riêng.
