# S002 — Folder Google Drive "Stats Arbitrage" (7 notebook Colab)

Trạng thái nạp: đã đọc 7/7 notebook, đã đối chiếu với S001 (cùng họ giao dịch cặp / hồi quy về trung bình) → xem mục 8. Tác giả không xác định.

Nhãn trạng thái: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Nguồn

- Loại: bộ notebook mã nguồn của cá nhân (không rõ tác giả) trên Google Drive, mỗi file một ô code duy nhất, **không có ô giải thích, không trích dẫn tài liệu nào**. Không qua bình duyệt.
- URL folder: https://drive.google.com/drive/folders/1JFL6ePEJzYGVoqF_5_Hc8zw-TgaFT8WL (đã bỏ tham số theo dõi `fbclid`). Tên folder "Stats Arbitrage". Folder công khai; danh sách lấy qua `embeddedfolderview`.
- Ngày đọc: 2026-10-03. Tải từng file bằng `drive.google.com/uc?export=download`. Drive chỉ hiện "Sep 16" cho ngày sửa (không có năm); phần "End Period 2026-09-16" trong output cho thấy các notebook chạy ngày 2026-09-16.
- Metadata Colab chỉ có `authorship_tag` (mã băm), không có tên người → tác giả không xác định.
- Đã đọc toàn bộ mã và mọi output dạng chữ; **không xem 8 output hình** (2 hình ở mỗi file Cointegration, Cross sectional, Distance, PCA; Kalman, Residual, VECM không có hình).
- Dữ liệu: Yahoo Finance qua `yfinance`, `auto_adjust=True`, cổ phiếu sàn NSE (Ấn Độ) giá ngày. Cài thư viện không ghim phiên bản (`!pip -q install ...`).

## 2. Bảng 7 notebook và kết quả lưu kèm (`nguồn-báo-cáo`)

Mọi kết quả: không có chi phí giao dịch/trượt giá/phí vay; một lần chạy; "OOS" = 30% cuối của mẫu khi nguồn có chia 70/30.

| Notebook | Vũ trụ / phương pháp | Chia | Kết quả lưu kèm |
|---|---|---|---|
| Cointegrration based | HDFCBANK/ICICIBANK; Engle–Granger trên log giá của train; hệ số hedge OLS trên train; z-score 60 ngày, vào ±2, "thoát" 0,5 | 70/30 | **p đồng tích hợp (train) = 0,2957** (không đạt). OOS 2023-03-23→2026-09-16, 863 quan sát: Sharpe 0,06, CAGR −0,61%, vol 18,02%, MDD −27,6%, tích luỹ −2,06%, thời gian có vị thế 100% |
| Cross sectional mean reversion | 20 cổ phiếu; ngược xu hướng 5 ngày theo z-score chéo, mua 20% dưới / bán 20% trên, chia vol 20 ngày, 50%/50% | không chia (toàn mẫu 2017-01-02→2026-09-16) | Sharpe 0,23, CAGR 1,7%, vol 9,27%, MDD −20,9%, tích luỹ 17,39%, đợt sụt dài nhất 2222 ngày |
| Distance based | 20 cổ phiếu → chọn cặp có khoảng cách bình phương nhỏ nhất trên train (190 cặp) = HDFCBANK/KOTAKBANK (0,024436); z-score 60 ngày, vào ±2 | 70/30 | OOS 2023-10-19→2026-09-16: Sharpe 1,24, CAGR 29,3%, vol 22,88%, MDD −18,86%, tích luỹ 108,6%, thời gian có vị thế 100% |
| Kalman filter dynamo | HDFCBANK/ICICIBANK; hệ số hedge động bằng Kalman (`pykalman`, siêu tham số đặt tay) | 70/30 | **Không có kết quả lưu** (output chỉ là log cài đặt) |
| PCA based | 20 cổ phiếu; PCA 5 thành phần trên train (giải thích 58,17% phương sai), z-score phần dư 60 ngày, vào ±2 | 70/30 | OOS 2023-10-19→2026-09-16: Sharpe −0,56, CAGR −8,52%, vol 14,15%, MDD −35,92%, tích luỹ −22,5%, thời gian có vị thế 49% |
| Residual mean reversion | 20 cổ phiếu + NSEI; phần dư so với chỉ số thị trường (OLS trên train), z-score 60 ngày, vào ±2 | 70/30 | OOS 2023-10-17→2026-09-16: Sharpe −0,92, CAGR −5,32%, vol 5,77%, MDD −18,97%, tích luỹ −14,43%, thời gian có vị thế 100% |
| VECM based | HDFCBANK/ICICIBANK; Johansen + VECM trên train | 70/30 | **Không có kết quả lưu** (output chỉ là log cài đặt) |

## 3. Lỗi và mâu thuẫn tôi xác minh (`lỗi-nguồn`)

1. **Lệnh "thoát vị thế" không có tác dụng** ở 5/7 notebook (Cointegration, Distance, Kalman, VECM, Residual). Chuỗi lệnh: đặt `Signal = 0` khi `|z| < 0,5` rồi `.replace(0, nan).ffill()`; bước `replace` biến mọi số 0 (kể cả điểm thoát) thành NaN rồi điền tiếp vị thế cũ, nên vị thế chỉ đổi khi gặp tín hiệu đảo chiều. Chú thích trong code ghi "Exit around equilibrium". Output xác nhận: "Time in Market 100.0%" ở cả 3 notebook có kết quả lưu. **Tôi tái lập (`tái-lập`)**: chạy nguyên văn các dòng đó trên chuỗi z-score tổng hợp (spread AR(1) φ=0,9, 3000 bước, z-score 60 ngày): sau lần vào lệnh đầu tiên có 0/2891 bar không có vị thế, 39 lần đảo chiều, dù có 925 bar |z|<0,5; bản có thoát đúng (viết lại có trạng thái) chỉ ở trong thị trường 23% thời gian. Hệ quả: các kết quả Sharpe/CAGR của 5 notebook này là của chiến lược "luôn nắm vị thế, đảo chiều ở ±2", không phải chiến lược "vào ±2, thoát ở 0,5" như mô tả.
2. **Cointegration**: p = 0,2957 trên train cho thấy cặp không đồng tích hợp nhưng mã vẫn chạy chiến lược; không có điều kiện dừng.
3. **VECM**: tín hiệu dựa trên spread có trọng số VECM (`beta[0]·logP1 + beta[1]·logP2`) nhưng lợi nhuận tính theo `R1 − R2` (bỏ qua hệ số VECM); mã cũng không kiểm kết quả Johansen (so `trace_stat` với `critical_5`) trước khi chọn `coint_rank=1`.
4. **Residual / PCA gọi là "market-neutral"**: lợi nhuận tính bằng `Σ signal × lợi suất cổ phiếu` (không phải lợi suất phần dư) và chuẩn hoá bằng `signal / Σ|signal|`, tức đặt tổng độ lớn bằng 1 chứ không ép tổng vị thế mua bằng tổng vị thế bán; không có chân bù thị trường. Ở Residual, z-score áp lên lợi suất phần dư theo ngày, không phải phần dư tích luỹ.
5. **Kalman**: tín hiệu dùng trạng thái đã lọc tại chính ngày t (đã hấp thụ quan sát ngày t) để tính phần dư ngày t (`suy-luận`: làm phần dư nhỏ đi so với dùng trạng thái dự báo trước; chưa kiểm). Siêu tham số `0,0001·I` và `0,001` đặt tay, không ước lượng. Không có kết quả lưu để đánh giá.
6. Đặc trưng RSI/Vol được tính nhưng không dùng trong tín hiệu ở nhiều notebook (mã thừa).

## 4. Hạn chế nguồn không nêu (đọc từ code, hoặc phân tích của tôi)

- Từ code: không chi phí giao dịch, trượt giá, phí vay; một lần chạy, một cửa sổ OOS; không hiệu chỉnh nhiều phép thử (Distance chọn 1 trong 190 cặp bằng dữ liệu train — 20 mã theo số mã PCA in ra với cùng bộ lọc; cùng tác giả, Cointegration chọn HDFCBANK/ICICIBANK được p = 0,2957 và Distance chọn HDFCBANK/KOTAKBANK cho Sharpe 1,24 → kết quả phụ thuộc mạnh vào cặp).
- `suy-luận`: 20 mã là các cổ phiếu vốn hoá lớn hiện nay (header ghi "NIFTY 50 STOCK UNIVERSE" nhưng chỉ có 20 mã) → thiên lệch sống sót/chọn lọc nhìn lại, chưa kiểm thành phần chỉ số theo thời gian. Chiến lược chéo hàng ngày có vòng quay cao nên chi phí có thể ăn mòn phần lớn lợi nhuận (chưa đo).
- Không có phân tích độ bền (đổi cửa sổ 60, ngưỡng ±2/0,5, tỉ lệ chia) hay kiểm ý nghĩa thống kê ngoài "Prob. Sharpe Ratio" của quantstats.

## 5. Đánh giá và mức dùng (đánh giá của tôi)

- Kết quả 5 notebook có số liệu: 1 dương mạnh (Distance, Sharpe 1,24), 1 dương nhẹ (Cross sectional, 0,23 toàn mẫu), 3 gần 0 hoặc âm. Với một lần chạy, không chi phí, chọn cặp trong mẫu: **không đủ để kết luận có hay không có lợi thế**; kết quả mạnh nhất cũng là kết quả chịu nhiều nguy cơ chọn lọc nhất.
- Dùng được làm mẫu mã (khung train/test, z-score cuốn chiếu, `shift(1)` để tránh nhìn trước, báo cáo `quantstats`) và làm danh mục họ phương pháp (khoảng cách, đồng tích hợp, VECM, Kalman, PCA, phần dư, chéo).
- **Không dùng làm bằng chứng lợi nhuận.** Mọi mã phải sửa lỗi mục 3 trước khi dùng thật.
- Liên quan tới og_program: hệ chưa có chiến lược cặp/chéo; chưa dẫn tới khuyến nghị thay đổi nào.

## 6. Chưa tái lập

Mọi kết quả trong bảng mục 2 (cần dữ liệu Yahoo/NSE và `yfinance`, `quantstats`, `statsmodels`, `pykalman`, `scikit-learn`, không có trong venv của hệ; tôi không cài vào venv production). Mục tái lập duy nhất đã làm: logic thoát vị thế (mục 3.1) trên dữ liệu tổng hợp.

## 7. sha256 các file đã đọc

```
9527b20c066c557b0e35cae9c527b2f7461a81961e4e5db7f886bffefec89ca5  Cointegrration based.ipynb      (id 1T9GfIsqNdAdoSEe_IbL529aSI9rHHBy4)
0e9549d5cf20181d968f25c032ec8a627d749fb2f9caf3e2abbb5aea32e2a8cd  Cross sectional mean reversion.ipynb (id 1dUmS5UaBGsnouIIsC7tCikwLCEXK1uF6)
3b49866f480f7d6d31ccf87f0c8428f7563e3a3ad6137c741f1f68b34c3c2fa2  Distance based.ipynb            (id 1Wyp9KSePkNEPiCyuu_ybhWf6UfeqZgYk)
a7b12345fdef3358b47b71f51a1fe1070bcf3292bd160aa102a165565244e21e  Kalman filter dynamo.ipynb      (id 1IuSfgqfu3KOJvKNxJ8XH7it9YxbAi6zp)
28eac81d69825471bd0c5d59ecf100902e50701e0cb8ded485284209d8519b20  PCA based.ipynb                 (id 1xpZugUviCEiFlzk0XcEMtTvoGoK0Ya9A)
83041f9d09207ca05db78d6d46af626e75dffe6366bd970ea73bea34bed91c30  Residual mean reversion.ipynb  (id 1_r6IkUBm0UlGGGVeHXok79h1EBjUvvnX)
1b5743e083fc9a6a167270c9b9dbdeb242cec8aee52bb1059eda4ab6f82e51c3  VECM based.ipynb                (id 1Xr6cBfh3MqkfxNHkT4exOzlWo3G68ANw)
```

## 8. Đối chiếu với cái cũ (S001)

- Cùng họ phương pháp (z-score cuốn chiếu 60 ngày trên spread/tỉ lệ, hồi quy về trung bình, không chi phí). Khác tham số: S001 vào/ra ±1 với cửa sổ 60/5; S002 vào ±2, thoát 0,5 (thoát hỏng, mục 3.1).
- Tinh chỉnh/củng cố F008 (S001): lãi của quy tắc z-score phụ thuộc việc chuỗi thật sự hồi quy — S002 Cointegration (p = 0,2957, không đồng tích hợp) cho Sharpe ≈ 0,06 (**phù hợp**, một cặp một lần chạy, không phải bằng chứng độc lập).
- Mới so với S001: các họ khoảng cách / VECM / Kalman / PCA / phần dư / chéo (S001 chỉ nhắc Kalman, Hurst, half-life ở phần "hướng cải thiện" mà không làm).

## 9. Bước kiểm chứng tiếp theo (đề xuất, chưa làm)

1. Tái lập có seed/phiên bản ghim trong môi trường tạm riêng, rồi sửa lỗi thoát vị thế và thêm chi phí để xem kết quả còn lại gì.
2. Nếu quan tâm: thử họ phương pháp này trên dữ liệu SQL thật của hệ, train/test đúng thứ tự, có chi phí.
