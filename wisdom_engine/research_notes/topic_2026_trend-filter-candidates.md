# Chủ đề — Ứng viên khối lọc trend mới (t-stat, Supertrend, Ichimoku) và khối `tstat`

Ngày: 2026-10-26. Người dùng: "research và tìm thêm khoảng 3 phương pháp để lọc trend nữa, chỉ tìm thôi, làm tập trung và nhanh gọn" → chọn t-stat: "hãy triển khai t-stat trước … nền tảng research có chưa?" → "research bên ngoài về phương pháp nhé, chứ ko chỉ trong này" → "Làm nhé: khung tham chiếu như KNN và Zigzag; cho phép (làm cả `des`, lưu research_notes)".

Nhãn: `nguồn-báo-cáo` (đã đọc bản gốc, nguồn nói chưa kiểm) · `tóm-tắt-tìm-kiếm` (CHỈ đọc tóm tắt kết quả tìm, KHÔNG mở trang gốc) · `đo` (chạy trên dữ liệu SQL hoặc mô phỏng của hệ) · `suy-luận` (phân tích của tôi).
Phương pháp: 6 lượt WebSearch chế độ standard (3 cho ứng viên, 3 cho phương pháp t-stat), tải lại bản gốc S013 để đọc §4.2 và Hình 5–6, rồi mô phỏng và đo trên dữ liệu của hệ (T009, script `scripts/t009_tstat_check.py`).

## 1. Ba ứng viên, và những gì KHÔNG đề xuất lại

| Họ | Luật hướng (+1 / −1 / 0) | Tham số | Bằng chứng | Nguồn |
|---|---|---|---|---|
| **T-stat độ dốc** (đã làm: khối `tstat`) | hồi quy log-giá theo thời gian trên N nến gần nhất; +1 nếu t của độ dốc > +2, −1 nếu < −2, 0 nếu không | N, độ trễ Newey–West, ngưỡng | tốt nhất trong ba (học thuật, nến NGÀY); xem §2 | S112 |
| **Supertrend** (chưa làm) | dải = (H+L)/2 ± hệ số × ATR, dải chỉ nhích theo hướng xu hướng; đổi hướng khi close vượt dải đối diện; không có trung tính | ATR 10, hệ số 3 | chỉ blog/script nhà bán; hệ số nhỏ ⇒ nhiều whipsaw; điểm yếu đi ngang; không có nghiên cứu độc lập | S116, S084 |
| **Ichimoku** (chưa làm) | +1 giá trên mây, −1 dưới mây, 0 trong mây (trung tính có sẵn); có thể thêm Tenkan/Kijun, màu mây, Chikou | 9 / 26 / 52, dịch 26 | chỉ script/blog; tác giả script nhận xét kém khi đi ngang; không số liệu độc lập | S117 |

Không đề xuất lại: ADX, ER, Choppiness, Hurst — đã đo trên dữ liệu hệ (F055–F057: ADX cao kém hơn ADX thấp ở 5/6 phép đo, ER/CHOP không nhất quán trong/ngoài mẫu); ms_fractal đã nghiên cứu từ 15/10 (S059–S062), chưa thành khối. Loại: "Momentum Regression [BackQuant]" (S118) — hồi quy lợi suất điều chỉnh biến động, không phải quy tắc TREND.

## 2. T-stat độ dốc — nguồn gốc và bằng chứng bên ngoài

**Bản gốc (S112, đọc lại 2026-10-26; mã băm `282118f1…f717` trùng bản S013 đã đọc ở ghi chú sideway).** Quy tắc TREND (§4.2, pt. 23–24): hồi quy OLS `log F_τ = α + β·τ + e_τ` trên log-giá NGÀY 12 tháng gần nhất; độ có ý nghĩa do t-stat Newey–West của β (Newey–West 1987, **không nêu số lag**); +1 nếu t > +2, −1 nếu t < −2, 0 nếu không. Tác giả chủ ý giữ đơn giản "to avoid data mining".
- Phát hiện mới so với ghi chú sideway: Hình 6 cho thấy TREND = 0 chỉ ở **~9% (đầu 2009) → ~18% (đầu 2013)** số hợp đồng; Hình 5: turnover giảm 55–85% (~2/3) so với SIGN. ⇒ lợi ích "ít giao dịch" đến từ độ DÍNH của tín hiệu (cần t vượt ngưỡng mới đổi hướng), KHÔNG phải từ việc đứng ngoài nhiều như F052 diễn đạt. `nguồn-báo-cáo`.

**Hồi quy trên log-giá là hồi quy giả (I(1)); ±2 không phải kiểm định 5%.** Mô phỏng của hệ (§3) xác nhận. Văn liệu kiểm định xu hướng bền với I(0)/I(1): Vogelsang (1998), Bunzel–Vogelsang (2005), Harvey–Leybourne–Taylor (2007, 2009), Astill–Harvey–Leybourne–Taylor (2014, áp cho chỉ số cổ phiếu; chọn ngưỡng I(0)/I(1) bằng kiểm nghiệm nghiệm đơn vị) — chặt chẽ nhưng phức tạp, không đưa vào khối (S114). `tóm-tắt-tìm-kiếm`.

**Cùng họ với MA.** Levine & Pedersen (2016, S113): hồi quy xu hướng OLS, HP, Kalman, TSMOM, MA crossover xấp xỉ cùng một họ bộ lọc tuyến tính; Lempérière et al. (2014, S115) dùng tín hiệu `(giá − EMA)/σ` — gần với t-stat. ⇒ hướng của khối sẽ trùng nhiều với MA/MACD trong chiến lược của hệ. `tóm-tắt-tìm-kiếm`.

**Intraday:** hai lượt tìm riêng KHÔNG thấy bằng chứng hay tham số intraday cho t-stat độ dốc (chỉ nến ngày/tháng). Khung N nến khung lớn của hệ là suy diễn của hệ. `tóm-tắt-tìm-kiếm`.

## 3. Hiệu chuẩn thống kê (`đo`, mô phỏng, 26/10/2026)

Bước ngẫu nhiên KHÔNG có xu hướng (log-giá = tổng bước chuẩn), 4.000 chuỗi mỗi N; tỷ lệ cửa sổ có |t| ≥ 2:

| N | t OLS thường (độ dốc) | Newey–West lag 4 | lag 10 | lag N/4 | t của lợi suất trung bình (iid) |
|---|---|---|---|---|---|
| 50 | 82% | 70% | 67% | 67% (lag 12) | 5% |
| 100 | 87% | 76% | 69% | 68% (lag 25) | 5% |
| 250 | 92% | 84% | 77% | 68% (lag 62) | 4% |

Chuỗi dài 60.000 bước, N = 100, Newey–West lag 4: độ dốc 74,8%, lợi suất trung bình 5,5%. ⇒ hai chế độ của khối có tính chất hoàn toàn khác nhau (§5).

## 4. T009 — đo trên dữ liệu thật (`đo`; SQL SELECT; 5 symbol US30, GOLD, BTCUSD, EURUSD, DE40)

**A. Hướng của khối** trên nến khung tham chiếu H1 / H2 / H4 từ 2022-01-01 (trung bình 5 symbol; "đổi hướng" = đổi dấu, bỏ qua nến trung tính):

| Khối | Trung tính | Đổi hướng/năm (H1 / H2 / H4) | Đoạn cùng hướng trung vị (nến) | Đồng thuận knn | Đồng thuận zigzag |
|---|---|---|---|---|---|
| knn | 0,1% | 646 / 326 / 164 | 7 | — | — |
| breakout | 0,0–0,1% | 40 / 21 / 11 | ~120–128 | — | — |
| zigzag | 0% | 22,8 / 11,1 / 5,5 | ~201–213 | — | — |
| tstat slope N=50 | 28–29% | 100 / 50 / 24 | ~29 | 62–63% | 65–66% |
| **tstat slope N=100 (mặc định)** | **23–25%** | **51 / 25 / 12** | **~60–66** | **54–56%** | **76–79%** |
| tstat slope N=200 | 17–18% | 26 / 13 / 7 | ~133–148 | 51–52% | 82–84% |
| tstat return N=50 / 100 / 200 | 94–95% | 14 / 7 / 3 (N=50) … 3 / 1 / 0,7 (N=200) | 2–4 | 72–94% | 78–99% |

**B. Tỷ lệ tín hiệu raw giữ lại** (đường chạy thật, khung trend tra bảng, gộp 5 symbol, từ 2024-01-01):

| Ô | knn | zigzag | slope 50 | **slope 100** | slope 200 | return 50 / 100 / 200 |
|---|---|---|---|---|---|---|
| combo vào H1 | 33,9% | 49,7% | 35,7% | **37,9%** | 41,1% | 1,7 / 2,3 / 3,0% |
| combo vào H4 | 19,9% | 48,8% | 24,9% | **35,7%** | 41,3% | 0,2 / 1,8 / 1,9% |
| ma_cross vào M30 | 84,6% | 47,6% | 26,8% | **34,4%** | 39,6% | 1,0 / 1,8 / 2,3% |
| macd_break vào H1 | 94,0% | 35,6% | 36,0% | **22,2%** | 18,3% | 2,5 / 0,2 / 0,9% |
| macd_break vào H4 | 93,1% | 30,8% | 54,7% | **31,4%** | 11,3% | 3,1 / 0 / 0% |

Đọc kết quả: (1) chế độ `slope` trên dữ liệu thật y như trên bước ngẫu nhiên: vùng trung tính ~25% (N=100), đúng với F245; (2) chế độ `return` — kiểm định đúng cỡ — cho 94–95% nến trung tính, tức giá H1–H4 hầu như không có xu hướng có ý nghĩa thống kê, nên giữ lại 0–3% tín hiệu: là bộ kiểm định, không dùng làm bộ lọc mặc định; (3) N dài giữ ít tín hiệu macd_break hơn (đột phá thường xảy ra khi độ dốc dài còn chưa đổi); (4) slope N=100 cho tỷ lệ giữ lại 22–38% ở mọi ô, nằm giữa knn và zigzag về độ chậm. Mặc định chọn: slope / N=100 / ngưỡng 2 / lag Newey–West 4 (người dùng duyệt "chọn mặc định sau khi đo"). CHƯA đo lãi/lỗi.

## 5. Khối `tstat` đã triển khai (2026-10-26)

- `we_core/src/trend_methods/tstat.py` (MỚI) và bản sao byte-for-byte `des/src/trend_methods/tstat.py` (khối chỉ import numpy/pandas); đăng ký ở `rules/trend_tf.py` của cả hai hệ (import + `METHODS` + `POLICY`: khung tham chiếu riêng tra `trend_tf_by_symbol` như knn/zigzag — người dùng chốt —, KHÔNG dời tín hiệu, warm-up nến trend chung 300). Config/CLI/dashboard tự nhận tên `tstat`; KHÔNG đổi config, worker, Redis, DE.
- Tham số cố định trong file (như các khối khác, đè được qua `params`): `TSTAT_MODE` (`slope` đúng S112 / `return`), `TSTAT_LOOKBACK` 100, `TSTAT_THRESHOLD` 2,0, `TSTAT_NW_LAG` (None = ⌊4·(N/100)^(2/9)⌋ = 4). Sai số chuẩn Newey–West hạt nhân Bartlett, không hiệu chỉnh cỡ mẫu nhỏ.
- Cột: `trend_tstat_t`, `trend_tstat_fit` (đường khớp OLS tại nến cuối cửa sổ, chỉ để vẽ, tô theo hướng), `trend_bias_tstat`. Cửa sổ kết thúc đúng ở nến đang xét (nhân quả); thiếu nến hoặc giá không dương/NaN ⇒ NaN ⇒ trung tính.
- Kiểm chứng: khớp bản đại số ma trận Newey–West độc lập tới 2e-12 (mô phỏng) và 7e-12 (dữ liệu thật), cả hai chế độ; nhân quả 48/48 phép cắt trên dữ liệu thật; gương `close → K/close` đổi dấu t đúng; kết quả không phụ thuộc kích thước khối; tính nhanh (41.000 nến H1: 0,07 s). Test: `we_core` 340 → 373, `des` 292 → 323 (31 test khối + dashboard); ruff/vulture sạch.
- `des`: khối đã đăng ký nhưng CHƯA là nguồn đầu ra (des có đúng ba db lọc trend db12–db14; thêm nguồn thứ tư = sửa `OUTPUT_SOURCES`, config và `_validate_config`, chưa được yêu cầu); test "nguồn đầu ra khớp khối" đổi thành nguồn ⊆ khối.

## 6. Chưa làm / việc kế tiếp

- Giá trị thật của bộ lọc: cần backtest BO20 qua một nguồn mới (`redis.extra_trend_outputs`, DB/tiền tố mới, nạp bằng cách như db4); hệ này chỉ đo cỡ và hình dạng.
- Supertrend, Ichimoku: chưa làm. Nếu làm Supertrend: đo mức đồng thuận với zigzag trước, kiểm warm-up (có trạng thái). Ichimoku: nhiều biến thể, không trạng thái, warm-up ~80 nến.
- Dashboard 8517 và `we-worker` đang chạy mã cũ trong bộ nhớ: khối mới chỉ hiện ở dashboard sau lần restart kế (không ảnh hưởng Redis).

## 7. Nguồn

| ID | Nguồn |
|---|---|
| S112 | Baltas, N., Kosowski, R. (2015) Demystifying Time-Series Momentum Strategies — bản gốc đọc lại: https://spiral.imperial.ac.uk/bitstreams/93f06c22-150a-4240-8115-912988dd2c67/download (§4.2, Hình 5–6) |
| S113 | Levine, A., Pedersen, L. H. (2016) Which Trend Is Your Friend?, Financial Analysts Journal 72(3): https://ideas.repec.org/a/taf/ufajxx/v72y2016i3p51-66.html |
| S114 | Astill, Harvey, Leybourne, Taylor (2014) Robust tests for a linear trend with an application to equity indices: https://eprints.nottingham.ac.uk/32665/ ; Harvey, Leybourne, Taylor (2009) Econometric Theory 25(4): https://ideas.repec.org/a/cup/etheor/v25y2009i04p995-1029_09.html (cùng Vogelsang 1998, Bunzel–Vogelsang 2005) |
| S115 | Lempérière et al. (2014) Two centuries of trend following: https://arxiv.org/abs/1404.3274 ; arXiv 2606.20145 (trend strength φ = t-stat của xu hướng) |
| S116 | Supertrend: https://www.luxalgo.com/library/concept/supertrend/ , https://forextester.com/blog/supertrend-indicator/ , https://mudrex.com/learn/supertrend-indicator/ |
| S117 | Ichimoku: https://capital.com/en-au/learn/technical-analysis/ichimoku-cloud-strategy , https://www.luxalgo.com/library/indicator/g2YGKu0q-ichimoku-cloud-all-signals/ |
| S118 | "Momentum Regression [BackQuant]": https://www.luxalgo.com/library/indicator/kRv35WXq-momentum-regression/ (loại) |
