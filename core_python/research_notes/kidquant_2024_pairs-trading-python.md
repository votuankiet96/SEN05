# S001 — KidQuant: "Pairs Trading With Python" (PairsTrading.ipynb)

Trạng thái nạp: đã đọc, đã đối chiếu với cái cũ (`research_notes/` lúc đó trống; bộ nhớ và code của hệ không có khái niệm tương ứng) → toàn bộ là mới.

Nhãn trạng thái dùng trong file này: `nguồn-báo-cáo` (nguồn nói, tôi chưa kiểm) · `tái-lập` (tôi chạy lại và ra kết quả) · `lỗi-nguồn` (tôi xác minh lỗi/mâu thuẫn ngay trong nguồn) · `suy-luận` (phân tích của tôi, cần nguồn học thuật để nâng cấp).

## 1. Nguồn

- Loại: notebook hướng dẫn (tutorial/demo code) của cá nhân trên GitHub. Không qua bình duyệt; nội dung không có trích dẫn tài liệu học thuật (chỉ nêu tên phương pháp: Johansen, Engle–Granger, Phillips–Ouliaris, Hurst, half-life, Kalman).
- URL: https://github.com/KidQuant/Pairs-Trading-With-Python/blob/master/PairsTrading.ipynb (đã bỏ tham số theo dõi `fbclid`).
- Repo `KidQuant/Pairs-Trading-With-Python`: tạo 2019-09-04, đẩy lần cuối 2024-04-01, giấy phép không khai báo. 838 sao / 143 fork lúc đọc (chỉ phản ánh độ phổ biến, không phải độ đúng).
- Người sửa theo git: KidQuant; "Andre" (commit 2019); vr-marco sửa lỗi nhãn heatmap ngày 2022-11-12 (commit edc8e90: nhãn trục sai thứ tự nên các cặp hiển thị không khớp cặp thật — bản hiện tại đã sửa).
- Phiên bản đã đọc: commit `8e86c88272e5e0aec6970fb3b95dc7f39813702f` (2024-04-01, "Updates to address the optimal trading window as well as the length of the exploratory data analysis."). sha256 file: `6af95656744246336cc1737cf2a203f32528ae0347fb21d0aa0c87ac0c9318dd`.
- Ngày đọc: 2026-10-03. Cách đọc: tải file `.ipynb` gốc từ raw.githubusercontent.com, trích toàn bộ chữ.
- Phạm vi đã đọc: 65/65 ô (34 markdown + 31 code) và mọi output dạng chữ. **Không xem 16 output hình** → mọi nhận xét về hình (heatmap, đồ thị z-score, đường cong cửa sổ ở ô 63) là chưa kiểm.
- Môi trường sinh output (metadata): kernel Python 3.12.2. `requirements.txt` của repo ghim pandas 1.5.0 / numpy 1.23.3 (2022) → không phải môi trường đã sinh output 2024 (chưa thử cài).

## 2. Nội dung theo ô

- Ô 3–15: dừng/không dừng và kiểm định ADF trên dữ liệu tổng hợp.
- Ô 16–27: đồng tích hợp, kiểm định Engle–Granger (`statsmodels.tsa.stattools.coint`), tương quan khác đồng tích hợp.
- Ô 28–36: quét mọi cặp trong 11 mã (AAPL, ADBE, ORCL, EBAY, MSFT, QCOM, HPQ, JNPR, AMD, IBM, SPY), Yahoo Finance cột `Close`, 2013-01-01 → 2019-01-01.
- Ô 37–43: spread (hồi quy OLS), tỉ lệ giá ADBE/MSFT, z-score.
- Ô 44–58: tín hiệu, chia train/test 70/30, đặc trưng MA5/MA60/STD60, hàm `trade()`, một con số lợi nhuận.
- Ô 59–64: hạn chế và tìm cửa sổ tối ưu.

## 3. Khẳng định của nguồn và trạng thái

| # | Khẳng định (vị trí) | Trạng thái |
|---|---|---|
| a | Giao dịch cặp là hồi quy về trung bình, "luôn được hedge trước biến động thị trường", "thường là chiến lược alpha cao khi có thống kê chặt" (ô 1) | `nguồn-báo-cáo`, nguồn không đưa dẫn chứng → không dùng làm bằng chứng |
| b | Tương quan cao ≠ đồng tích hợp: hai bước ngẫu nhiên có drift, corr 0,9955 nhưng p Engle–Granger 0,7687 (ô 24) | `nguồn-báo-cáo` (demo tổng hợp, không đặt seed); chưa tái lập |
| c | 11 mã, 55 cặp: 6 cặp có p<0,05 — (AAPL,ORCL) (AAPL,SPY) (ADBE,MSFT) (AMD,MSFT) (HPQ,ORCL) (ORCL,SPY) (ô 33); cặp được chọn ADBE/MSFT, p = 0,04453 (ô 35) | `nguồn-báo-cáo` (cần dữ liệu Yahoo); chưa tái lập |
| d | Chạy `trade(ADBE, MSFT, 60, 5)` trên `iloc[881:]` cho "lợi nhuận" 370,68 (ô 57) | `nguồn-báo-cáo`, có nhiều lỗi (mục 5) → không dùng |
| e | Tìm cửa sổ dài l∈[0,254] trên train: tốt nhất l=250; trên test l=250 cho 2351,67, tốt nhất l=247 cho 2362,94 (ô 60–62) | `nguồn-báo-cáo`; chưa tái lập |

## 4. Giới hạn do chính nguồn nêu (ô 59–64)

Ít mã; chỉ ~5 năm; overfitting; tín hiệu không tính việc giá hai mã cắt nhau; gợi ý Hurst exponent, half-life, Kalman filter; "chiến lược này không hoàn hảo". Nguồn cũng thừa nhận có thiên lệch so sánh nhiều lần (ô 31) nhưng không hiệu chỉnh.

## 5. Lỗi và mâu thuẫn nội tại tôi xác minh (`lỗi-nguồn`)

1. Ô 15 đọc ngược kết quả ADF: viết "fail to reject the null hypothesis… A is likely stationary", trong khi ô 13 chú thích cần p nhỏ để kết luận dừng và ô 14 cho p(A) = 2,9e-05 (tức bác bỏ H0).
2. Ô 11 ghi H0 (có nghiệm đơn vị) là I(0); ô 19 dùng I(1) cho trường hợp tương ứng.
3. Ô 25 ghi p = 0,7092 trong khi output ô 24 là 0,7687 (không đặt seed nên văn bản là từ một lần chạy khác).
4. Ô 26–27 "tương quan thấp nhưng đồng tích hợp": Y2 là nhiễu trắng quanh 20 (dừng theo cách sinh) và Y3 là sóng vuông; nguồn không chạy bước 1 của quy trình tự nêu ở ô 19 (kiểm nghiệm từng chuỗi) nên ví dụ không minh hoạ đồng tích hợp theo nghĩa dùng để giao dịch.
5. Ô 34 viết "AAPL/EBAY và ABDE/MSFT" trong khi output ô 33 có 6 cặp, không có AAPL/EBAY; ô 41 nhắc "ADBE/SYMC" nhưng SYMC không có trong danh sách mã; ô 59 nói "5 năm" nhưng dữ liệu 6 năm → văn bản cũ chưa theo kịp bản 2024.
6. Ô 57 chạy trên `iloc[881:]`, trong khi ô 47/60/62 chia tại 1057 → hai điểm cắt khác nhau trong cùng notebook; đoạn 881–1056 thuộc "train" theo ô 47.
7. Ô 61 viết cửa sổ tối ưu của train "far from optimal" trên test, nhưng số ở ô 62 cho thấy 250 → 2351,67 so với tốt nhất 2362,94 (chênh ~0,5%).
8. Z-score tính trong `trade()` (gọi (60,5)) là (MA60 − MA5)/STD5, khác đặc trưng mô tả ở ô 48–49 là (MA5 − MA60)/STD60 (khác dấu và khác cửa sổ độ lệch chuẩn); chú thích trong code ngược với điều kiện (`# Sell short if the z-score is > 1` đặt trên `if zscore[i] < -1`; `# …between -.5 and .5` nhưng ngưỡng là 0,75).
9. Lỗi nhỏ: ô 4 ghi quy tắc thực nghiệm là 66% (giá trị chuẩn thường được nêu là ~68%; đây là kiến thức nền của tôi, chưa trích nguồn).

## 6. Hạn chế nguồn không nêu (đọc từ code, hoặc phân tích của tôi)

- Từ code (`lỗi-nguồn`): `trade()` không có chi phí giao dịch, trượt giá, phí vay/ký quỹ; mỗi thanh thoả điều kiện vào lệnh đều cộng thêm vị thế (không giới hạn, không kiểm vị thế đang mở); vị thế còn mở ở cuối mẫu không được tính vào lãi lỗ; kết quả là số tuyệt đối theo đơn vị giá, không phải % hay Sharpe/drawdown.
- Từ code: chọn cặp bằng toàn mẫu 2013–2018 (ô 33) rồi mới chia train/test (ô 47) → giai đoạn test đã "lộ" cho bước chọn cặp. Hệ số OLS ở ô 38 ước lượng trên toàn mẫu và không được dùng trong `trade()` (hàm dùng tỉ lệ giá). Z-score ô 42 dùng mean/std toàn mẫu (chỉ để vẽ).
- Từ code: ngưỡng ±1 và cửa sổ 5/60 chọn bằng mắt từ biểu đồ (ô 51: "Looking at the plot, it's pretty clear…"); một cặp, một giai đoạn, một lần chia.
- `suy-luận` từ số của nguồn: 11 mã cho C(11,2) = 55 cặp; ở α = 0,05 nếu cả 55 đều không đồng tích hợp thì kỳ vọng ≈ 2,75 cặp dương tính giả (gần đúng, các phép thử không độc lập vì dùng chung chuỗi); ngưỡng Bonferroni 0,05/55 ≈ 0,00091; p của cặp được chọn (0,04453) không vượt ngưỡng đó. Cần nguồn học thuật để nâng cấp trạng thái.
- `suy-luận`: không có kiểm tra quan hệ đồng tích hợp có bền theo thời gian hay không.

## 7. Tôi đã thử gì

Chạy nguyên văn hàm `trade()` (ô 56, trích bằng chương trình từ file .ipynb) trên chuỗi tổng hợp 1500 bước: S2 hằng số 100, S1 = 100 × ratio, ratio = 1 + AR(1) với σ = 0,02, gọi `trade(S1, S2, 60, 5)`, 200 seed (0–199), chuỗi có chỉ số nguyên. Môi trường: pandas 3.0.5, numpy 2.5.1 — `.venv` sản xuất của repo, chỉ chạy, không cài thêm (kiểm ngày 2026-10-04: dist-info của hai gói có mốc tháng 8/2026; không mục nào trong `.venv` đổi sau 2026-10-03 00:00, không tính `__pycache__`). **Lệch quy tắc tự đặt** (đáng lẽ phải dùng venv scratch); ghi lại để minh bạch.

| Chuỗi ratio | Lãi trung bình | Độ lệch chuẩn | Tỉ lệ seed dương |
|---|---|---|---|
| AR(1), φ = 0,95 (hồi quy về trung bình) | 4741,40 | 507,67 | 200/200 |
| Bước ngẫu nhiên, φ = 1,00 (đối chứng) | 227,03 | 2700,71 | 63% (chưa kiểm ý nghĩa) |

Kết luận có giới hạn (`tái-lập`, trên dữ liệu tổng hợp): hướng giao dịch thực tế của `trade()` khớp hồi quy về trung bình (hai chỗ đảo dấu ở mục 5.8 triệt tiêu nhau), và lãi phụ thuộc việc ratio thật sự hồi quy. Đơn vị là đơn vị giá nên không so sánh được với 370,68 của nguồn. Không nói gì về thị trường thật.

**Tương thích phiên bản (`tái-lập`, kiểm lại 2026-10-04 trong venv scratch với pandas 3.0.6 / numpy 2.5.3 — không phải phiên bản của lần chạy gốc; lần gốc trên 3.0.5 chưa kiểm lại, để không chạy thêm trong `.venv` sản xuất):** `trade()` nguyên văn truy cập vị trí bằng `zscore[i]`, `S1[i]`, `S2[i]`, `ratios[i]` với `i` nguyên chạy từ 0. Trên chuỗi có chỉ số nguyên (`RangeIndex`) hàm chạy bình thường; trên chuỗi có **chỉ số thời gian (`DatetimeIndex`, đúng dạng dữ liệu Yahoo của notebook)** hàm ném `KeyError: 0` vì pandas 3 không còn dùng số nguyên làm vị trí khi chỉ số là thời gian. Vì vậy phép thử ở trên dùng chuỗi chỉ số nguyên, và notebook của nguồn (viết cho pandas 1.5, Python 3.12 khi sinh output) không chạy nguyên văn được trên pandas hiện hành với dữ liệu có chỉ số ngày; muốn tái lập trên dữ liệu thật phải đổi sang `.iloc[i]`.

## 8. Chưa tái lập

- Mọi kết quả trên dữ liệu thật (6 cặp, p = 0,04453, 370,68, 2351,67/2362,94, cửa sổ 250): cần dữ liệu Yahoo cùng `statsmodels`/`yfinance`, tôi không cài thêm vào venv production của hệ.
- Các demo ADF/Engle–Granger tổng hợp (ô 14, 20, 24, 26) vì thiếu `statsmodels` trong venv.

## 9. Đánh giá và mức dùng (đánh giá của tôi)

- Dùng được như tài liệu minh hoạ trực quan khái niệm (dừng, ADF, tương quan khác đồng tích hợp) và như danh mục cạm bẫy khi tự xây backtest (thiên lệch chọn mẫu, so sánh nhiều lần, chi phí, vị thế cộng dồn).
- **Không dùng làm bằng chứng** rằng giao dịch cặp có lợi nhuận hay "alpha cao": con số 370,68 không đạt yêu cầu tối thiểu (mục 5–6).
- Các định nghĩa thống kê trong nguồn có nhiều lỗi ký hiệu và diễn giải → phải đối chiếu tài liệu gốc/sách giáo khoa (chưa mở) trước khi dựa vào.
- Liên quan tới og_program: hệ hiện chỉ có chiến lược trên một công cụ (combo, ma_cross); chưa có chiến lược cặp. Nguồn này chưa dẫn tới khuyến nghị thay đổi nào cho hệ.

## 10. Bước kiểm chứng tiếp theo (đề xuất, chưa làm)

1. Đối chiếu khái niệm với tài liệu học thuật gốc (cần bạn gửi hoặc cho phép tôi tìm).
2. Tái lập có đặt seed bằng môi trường tạm riêng (không đụng venv production).
3. Nếu quan tâm đến cặp: thử trên dữ liệu SQL thật của hệ, có chi phí và tách train/test đúng thứ tự.
