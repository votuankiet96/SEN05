# S003 — zazhang: "ep-chan-book-algo-trading" (notebook tái hiện ví dụ sách của Ernie Chan)

Trạng thái nạp: đã đọc, đã tái lập cả 3 kết quả hiệu suất của notebook (cả 3 khớp chính xác), đã đối chiếu với S001/S002 (mục 9).

Nhãn trạng thái: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Nguồn

- Loại: notebook học tập của cá nhân tái hiện các ví dụ trong sách "Algorithmic Trading Winning Strategies and their Rational[e]" của Ernie Chan (tên theo README của repo; **tôi chưa mở cuốn sách**, nhà xuất bản/năm chưa kiểm chứng). Không qua bình duyệt. Mã gốc của sách viết bằng MATLAB; repo là bản Python 2.7.
- URL: https://github.com/zazhang/ep-chan-book-algo-trading (đã bỏ tham số theo dõi `fbclid`). Tệp chính: `notebook/EP Chan Book.ipynb`; thêm `src/hurst.py`; 4 tệp dữ liệu `.mat` trong `data/`.
- Repo: tạo 2018-07-17, đẩy lần cuối 2018-07-17, 3 commit duy nhất (HEAD `0f3f06508990ee41f3dd711e50293ff7b974336f`), giấy phép MIT (cho mã; **dữ liệu `.mat` do tác giả repo nói lấy từ bộ dữ liệu của Chan, điều khoản dữ liệu không rõ**). 480 sao / 102 fork lúc đọc (chỉ phản ánh độ phổ biến).
- Tác giả: zazhang. Không phải tác giả sách.
- Ngày đọc: 2026-10-03. Cách đọc: tải `notebook/EP Chan Book.ipynb` (sha256 `5b43e52148c3e2f02752e3509cf5f42d39d058a401184534420aba1e9b30cb25`), `src/hurst.py` (`36077f20…27da`), `README.markdown` (`54a6cdd7…6f`) và 4 tệp `.mat` từ raw.githubusercontent.com.
- Đã đọc: 73/73 ô (38 markdown + 35 code) và mọi output dạng chữ; notebook không có output hình.
- Môi trường (metadata): kernel Python 2.7.15 (macOS), pandas 0.23.0, numpy 1.14.3, statsmodels 0.9.0, pykalman 0.9.5.
- Tác giả tự ghi chưa xong: README "TODO: Complete Trading calender spread, 07/16/2018"; các ô 55, 57, 59, 61 chỉ có `# TODO`.

## 2. Nội dung theo ô

- Ô 0–12: kiểm định tính dừng trên chỉ số Shanghai (tushare, dữ liệu tháng 2014-04→2018-04): ADF (ô 6), Hurst (ô 9), Variance Ratio (ô 11).
- Ô 13–21: kiểm định đồng tích hợp AAPL/IBM (IEX, 2014-04→2018-04).
- Ô 22–34: Bollinger band cho cặp USO/GLD (tệp `inputData_ETF.mat`, 1500 ngày 2006-04-26→2012-04-09, 67 mã).
- Ô 35–38: hệ số hedge động bằng Kalman cho EWA/EWC (mã lấy từ QuantStart theo lời tác giả); **không có chiến lược/hiệu suất đi kèm**.
- Ô 39–49: hồi quy trung bình chéo trên 51 chuỗi (tệp `inputDataOHLCDaily_20120504.mat`).
- Ô 50–61: chênh lệch kỳ hạn (calendar spread) — chưa làm xong.
- Ô 62–71: động lượng chéo trên 497 cổ phiếu (tệp `inputDataOHLCDaily_stocks_20120424.mat`).

## 3. Kết quả hiệu suất nguồn báo cáo và kết quả tái lập

Tái lập bằng Python 3.14 trong môi trường tạm riêng (numpy 2.5.3, pandas 3.0.6, scipy 1.18.1; không động tới venv của hệ), chạy lại đúng logic từng ô trên dữ liệu `.mat` của repo. Mã tái lập: `scratchpad/src_chan/repro_chan.py` (không lưu trong repo này). Tất cả kết quả: không chi phí giao dịch.

| Kết quả của nguồn (ô) | Nguồn báo cáo | Tái lập đúng logic nguồn | Trạng thái |
|---|---|---|---|
| USO/GLD Bollinger, lookback 20, vào ±1 (ô 32) | Sharpe 1,572; APR 34,2505% | Sharpe 1,5722; APR 34,2505%; hedge ratio dòng 20–22 khớp 6 chữ số | `tái-lập` |
| Hồi quy trung bình chéo 51 chuỗi 2008-05-22→2012-04-30 (ô 49) | Sharpe 0,9214; APR 12,7533% | Sharpe 0,9214; APR 12,7533% | `tái-lập` |
| Động lượng chéo cổ phiếu 252/25/50, cửa sổ 2007-05-15→2007-12-31 (ô 69) | Sharpe 4,202; APR 38,6203% | Sharpe 4,2023; APR 38,6203% khi cắt theo vị trí `[i0:i1]` (159 dòng, bỏ dòng cuối) như cách viết `dailyret[idx_start:idx_end]` của nguồn; cắt theo nhãn bao gồm cả hai đầu (160 dòng) cho 4,0632 / 37,2118% | `tái-lập` |

## 4. Phát hiện quan trọng từ tái lập (`tái-lập` + `lỗi-nguồn`)

1. **Con số USO/GLD bị thổi phồng bởi cách xử lý ngày không có vị thế (ô 32).** 745/1499 ngày (50%) không có vị thế (`mkt_value = 0` nên `ret = 0/0 = NaN`), và `ret.fillna(method='pad')` chép lợi suất của ngày liền trước sang các ngày đó. Nếu ngày không vị thế lãi = 0: **Sharpe 0,8727; APR 12,5663%** (so với 1,5722 và 34,2505%). Chỉ tính các ngày có vị thế (754 ngày): Sharpe 1,2324 (số này đo cái khác — điều kiện theo đang có vị thế — không so sánh trực tiếp). Nguồn không nêu hay giải thích việc điền này.
2. **Quy tắc thoát vị thế trong mã khác với mô tả (ô 22, 29–30).** Văn bản nói thoát khi z-score về `exitZscore = 0`; trong mã, các dòng dùng `long_exit`/`short_exit` bị comment, vị thế mỗi ngày chỉ phụ thuộc điều kiện vào lệnh `|z| > 1` → thực tế vị thế mở khi `|z| > 1` và đóng ngay khi `|z| ≤ 1`, tham số `exitZscore` không có tác dụng. (50% ngày không vị thế khớp với quy tắc này.)
3. **Hiệu suất động lượng phụ thuộc hoàn toàn vào cửa sổ chọn (ô 69).** Cùng chiến lược, cùng mã nguồn, chỉ đổi cửa sổ: cửa sổ nguồn (2007-05-15→2007-12-31) Sharpe 4,06–4,20; **toàn bộ phần có vị thế 2007-05-16→2012-04-24 (1246 ngày): Sharpe −0,4805; APR −9,2609%**. Theo năm (chỉ ngày có vị thế): 2007 +4,077; 2008 −0,559; 2009 −1,996; 2010 +0,174; 2011 +0,553; 2012 (78 ngày) −0,776. Cửa sổ nguồn dùng là ~160 ngày đầu tiên có tín hiệu sau 252 ngày khởi động; nguồn không giải thích vì sao chọn cửa sổ đó. Không so được với con số của chính cuốn sách (chưa mở sách).
4. **Cách tính lợi suất ở ô 47 dùng `cl.shift(-1)`**: `daily_return[t] = (P_t − P_{t+1}) / P_{t+1}` (âm của lợi suất ngày sau, theo mẫu số giá ngày sau). Kết hợp với `weights.shift(1)`, hai lần đảo dấu triệt tiêu: tương quan giữa chuỗi lãi của nguồn và chuỗi viết lại chuẩn (vị thế quyết định từ lợi suất ngày t−1, ăn lợi suất ngày t) bằng 0,9905 khi lệch nhãn ngày 1 dòng → **không có nhìn trước trong P&L** nhưng nhãn ngày lệch 1 dòng; bản chuẩn cho Sharpe 1,0201 / APR 14,2316% (so với 0,9214 / 12,7533%) — chênh do định nghĩa lợi suất.
5. **Dữ liệu ô 39–49 là hợp đồng tương lai/FX (AUD, CL, ZB, ZN…, 51 mã) nhưng văn bản nói "thường áp dụng cho cổ phiếu, không phải tương lai hay tiền tệ".** Nguồn không giải thích.
6. **`src/hurst.py` lỗi khi chạy**: dùng `log10` nhưng chỉ import `log, polyfit, var, subtract` → `NameError: name 'log10' is not defined` (tôi gọi thử hàm với chuỗi tổng hợp). Bản trong notebook (ô 9) import đúng `log10`.
7. **Ô 9 tính Hurst chỉ với 3 độ trễ (2, 3, 4)** vì `lags = range(2, lag_range)` với `lag_range = 5` → H = 0,5089 là hồi quy trên 3 điểm. `suy-luận`: không đáng tin để kết luận.
8. **Ô 6 (ADF trên chỉ số Shanghai theo tháng)**: tuple in ra cho thấy dùng 11 độ trễ và chỉ 37 quan sát hiệu dụng (tổng 49 điểm); kết luận "chuỗi dừng, dù biểu đồ không giống" nên được xem là mong manh (`suy-luận`).
9. **Ô 12 giải thích sai/không khớp output** của kiểm định variance ratio: văn bản nói "test statistics bằng 1 nghĩa là bác bỏ random walk, bằng 0 là có thể random walk", trong khi output ô 11 in `Test Statistic 2.055; P-value 0.040` (thống kê z, không phải cờ 0/1). Kết luận bác bỏ ở mức 95% theo p = 0,040 vẫn khớp.
10. Ô 72 viết "implemented most important contents in E.P. Chan's book" trong khi phần chênh lệch kỳ hạn chưa làm xong (ô 55–61 là `# TODO`).

## 5. Khẳng định khác của nguồn

- Ô 7: nửa đời hồi quy trung bình = −log(2)/λ, với λ là hệ số hồi quy của Δy theo y(t−1) — `nguồn-báo-cáo` (công thức theo sách), tôi chưa đối chiếu sách.
- Ô 20–21: AAPL/IBM không đồng tích hợp (stat −1,124; p = 0,8766) "như kỳ vọng" — `nguồn-báo-cáo`; dữ liệu IEX/tushare lấy lúc chạy, không có trong repo → không tái lập được. Nguồn không giải thích vì sao kỳ vọng như vậy.

## 6. Hạn chế nguồn không nêu

- Không chi phí giao dịch/trượt giá/phí vay ở mọi ví dụ; mọi ví dụ đánh giá trong mẫu (không chia train/test) bằng tham số của sách.
- `suy-luận`: tệp cổ phiếu có 497 mã "tính tới 2012-04-24" áp ngược về 2007 → rủi ro thiên lệch sống sót; cách tập mã được lập không được ghi trong repo nên không kiểm được.
- Nguồn không so con số của mình với con số trong sách (dù mục đích là "so sánh kết quả với sách", ô 22).

## 7. Đánh giá và mức dùng (đánh giá của tôi)

- Là nguồn **tái lập được** (3/3 kết quả khớp) — điểm mạnh so với S001/S002 — nhưng bản thân các con số cho thấy: (a) kết quả đẹp nhất phụ thuộc vào xử lý ngày không vị thế (mục 4.1) và vào cửa sổ chọn (mục 4.3); (b) không có chi phí.
- Dùng được để: hiểu quy trình kiểm định (ADF, Hurst, variance ratio, CADF), làm mẫu mã hồi quy trung bình/động lượng chéo/Kalman, và như ví dụ về cách một con số hiệu suất đổi hẳn theo chi tiết cài đặt.
- **Không dùng làm bằng chứng** rằng các chiến lược đó sinh lời. Con số đáng tin hơn của notebook (sau sửa): USO/GLD Sharpe ~0,87 trước chi phí; động lượng chéo toàn kỳ 2007–2012 Sharpe ~−0,48 trước chi phí.
- Bằng chứng gốc phải là cuốn sách (chưa mở) và tài liệu học thuật mà sách dẫn.
- Liên quan tới og_program: hệ chưa có chiến lược cặp/chéo/động lượng chéo; chưa dẫn tới khuyến nghị thay đổi nào.

## 8. Chưa tái lập

Kết quả trên dữ liệu tushare (Shanghai) và IEX (AAPL/IBM) ở các ô 4–21 (dữ liệu lấy qua API lúc chạy, không có trong repo). Ô 36–38 (Kalman) không có kết quả hiệu suất để tái lập. Calendar spread (ô 50–61) chưa làm xong.

## 9. Đối chiếu với cái cũ (S001, S002)

- **Củng cố** nhận định về vệ sinh backtest (F007, F013): cùng kiểu lỗi "cách xử lý vị thế/ngày không vị thế làm kết quả không phải của chiến lược mô tả" (S002: `ffill` làm thoát hỏng; S003: `pad` thổi phồng Sharpe, thoát bị comment).
- **Liên kết** với S001: S001 gợi ý Hurst, half-life, Kalman làm hướng cải thiện (ô 59); S003 có triển khai Hurst (3 độ trễ, lỗi ở `hurst.py`), công thức nửa đời (chỉ nêu), Kalman (chỉ hệ số hedge, không P&L).
- **Mới**: động lượng chéo cổ phiếu; kết quả theo năm; bằng chứng tái lập được.
