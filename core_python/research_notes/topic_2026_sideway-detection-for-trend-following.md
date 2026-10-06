# Chủ đề — Phát hiện sideway (thị trường đi ngang) để hỗ trợ chiến lược trend following

Ngày: 2026-10-04. Câu hỏi của người dùng: tìm phương án phát hiện sideway để hỗ trợ các chiến lược theo xu hướng, dựa trên tài liệu, nghiên cứu thực tiễn và kinh nghiệm cộng đồng — phải có bằng chứng cụ thể.

Ghi chú gộp 5 nguồn mới (S009–S013), các khẳng định cộng đồng đã kiểm (và loại), và một phép thử trên dữ liệu thật của hệ (T001). Nhãn: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Nguồn đã đọc

| ID | Nguồn | Loại | Phần đã đọc | sha256 tệp |
|---|---|---|---|---|
| S009 | Gurrib, I. (2018). *Performance of the Average Directional Index as a market timing tool for the most actively traded USD based currency pairs*. Banks and Bank Systems 13(3), 58–70. doi:10.21511/bbs.13(3).2018.06 (CC BY-NC 4.0) | bài tạp chí có bình duyệt, 14 trang | tóm tắt, quy tắc giao dịch, bảng kết quả, kết luận | `a62cb51d0b5a99643530cf2bd4868697f6f5670f19cca1bddc1ae5e209e20bb7` |
| S010 | Mitra, S. K. (2012). *Is Hurst Exponent Value Useful in Forecasting Financial Time Series?* Asian Social Science 8(8), 111–120. doi:10.5539/ass.v8n8p111 (bản PDF tải từ bản sao trên mql5.com, đúng bản in của tạp chí) | bài tạp chí, 10 trang | tóm tắt, mục 5.3, Bảng 3–4, kết luận | `747c3d4962e4e51ec5c597b9bc1c55fcb3cd3bac25db134fa9353d180035956d` |
| S011 | Kurth, J. G., Eisler, Z., Rej, A., Bouchaud, J.-P. (2026). *Is Trend Still Your Friend? A Microstructural Account of the Demise of Short-Term Trend-Following*. arXiv:2607.01550v1 (q-fin.TR, 2026-07-02) | preprint, 31 trang (tác giả gồm CFM) | tóm tắt, mở đầu, mục 5 (phân rã trong hợp đồng), phụ lục D.1, kết luận | `b13145a7fb78577a7c4c17717028a2bae2744cc46edd3783caec1b1c648be1d5` |
| S012 | Hurst, B., Ooi, Y. H., Pedersen, L. H. (2017). *A Century of Evidence on Trend-Following Investing* (AQR; PDF qua trendfollowing.com) | bài nghiên cứu của nhà quản lý quỹ (có xung đột lợi ích: AQR bán sản phẩm trend), 16 trang | phần "smile", khủng hoảng, môi trường sau 2008, phí | `f3aa7661b53e3e1e7386f655403e82bea5ca575b7a699d61e06292da55b45a60` |
| S013 | Baltas, N., Kosowski, R. (2015). *Demystifying Time-Series Momentum Strategies: Volatility Estimators, Trading Rules and Pairwise Correlations* (bản 2015-10-12 trên kho Imperial College Spiral, hdl 10044/1/41472; SSRN 2140091) | working paper học thuật, 46 trang | tóm tắt, mở đầu, định nghĩa quy tắc TREND (pt. 23–24), thảo luận Bảng III và Hình 5 | `282118f1dbaaa14b69cde640a026f2fa900381f0b0e2f6e4f761bb945586f717` |

Chưa đọc: sách gốc của Wilder (1978, ADX), Kaufman (*Trading Systems and Methods*, Efficiency Ratio), bài gốc của Dreiss (Choppiness Index), LeBaron (1992), Lempérière et al. (2014), Moskowitz–Ooi–Pedersen (2012). Bảng/hình số của S012, S013 chỉ đọc phần chữ, không đọc số trong bảng.

## 2. Mỗi nguồn nói gì

**S013 Baltas & Kosowski — bằng chứng trực tiếp nhất về "đứng ngoài khi không có xu hướng".**
- Quy tắc TREND: hồi quy log-giá ngày 12 tháng theo thời gian, t-stat Newey–West của độ dốc; `+1` nếu t > +2, `−1` nếu t < −2, **0 (đứng ngoài) nếu không** — tức một bộ lọc sideway bằng ý nghĩa thống kê.
- So với quy tắc thường (SIGN: dấu lợi suất 12 tháng), trên 75 hợp đồng tương lai 1974–2013, nến tháng: Sharpe trước phí **1,04 (SIGN) so với 0,99 (TREND)**, "not statistically different" (p-value Ledoit–Wolf 2008); turnover của SIGN "almost three times as high" → TREND chỉ cần khoảng một phần ba giao dịch. Ở cấp từng tài sản, ΔSharpe "on average insignificant" (tăng ở hợp đồng này, giảm ở hợp đồng kia).
- Nguyên văn: "Contrary to expectations … the turnover and costs reduction … does not lead to significantly higher risk-adjusted performance." → `nguồn-báo-cáo`.
- Ý nghĩa: đứng ngoài khi xu hướng không có ý nghĩa thống kê **giảm phí**, không **tăng lợi thế gộp**.

**S011 Kurth et al. — hai phát hiện liên quan.**
- Từ khoảng 2009, trend ngắn hạn ("fast signals (horizons of days to weeks) most affected") mất lãi trên các hợp đồng có tick nhỏ so với biến động; còn nguyên trên hợp đồng tick lớn (~100 hợp đồng tương lai, 1995–2025). `nguồn-báo-cáo`.
- Phân loại ngày theo biến động **nhân quả** (so với trung vị 252 ngày trước): "trends are generally more profitable in periods of low volatility – the LeBaron effect"; "low-volatility days contribute a disproportionate amount to the gains". `nguồn-báo-cáo` — ngược với kinh nghiệm cộng đồng "chỉ trade trend khi thị trường sôi động".

**S012 AQR (Hurst, Ooi, Pedersen).** Lãi của trend following tập trung ở năm thị trường cổ phiếu tăng/giảm mạnh ("smile", 1880–2013, sau phí mô phỏng); giai đoạn sau 2008 kém vì "a lack of clear trends — and even a number of sharp trend reversals". Mô tả theo năm, không phải bộ lọc dùng được trước khi vào lệnh. `nguồn-báo-cáo`, có xung đột lợi ích.

**S009 Gurrib (ADX).** Hệ: mua khi +DI cắt lên −DI **và ADX > 20**, bán ngược lại (kèm Parabolic SAR), 7 cặp USD, nến tuần/tháng 2000–2018, **không tính phí**. Rất ít lệnh trong 18 năm (Bảng 1: hệ tháng 2–8 lệnh mỗi cặp, hệ tuần 6–28 lệnh); hệ tháng 5/7 cặp tổng lợi suất âm, hệ tuần Sharpe từ −2,123 (CHF) đến 0,996 (CNY), đa số gần 0; tác giả tự kết luận cần thử ở tần suất cao hơn. **Không so sánh có lọc ADX với không lọc** → không chứng minh được ADX lọc sideway hiệu quả. `nguồn-báo-cáo`, bằng chứng yếu.

**S010 Mitra (Hurst).** 12 chỉ số, 10 năm, chia cửa sổ 60 ngày: tương quan dương giữa Hurst và lãi của quy tắc MA10 (vd DJI 0,51, p<0,001; HSI −0,07); lãi trung bình 60 ngày: H<0,45 **−0,91%**, 0,45–0,55 **+1,39%**, H>0,55 **+4,30%** (không tính phí). **Lỗi phương pháp (`lỗi-nguồn`)**: Hurst và lợi nhuận đo trên **cùng một cửa sổ 60 ngày** → đây là quan hệ **đồng thời**, không phải bộ lọc biết được trước khi vào lệnh; bài không chứng minh Hurst dự báo được.

## 3. Kinh nghiệm cộng đồng — đã kiểm và kết luận

| Khẳng định | Nguồn | Trạng thái |
|---|---|---|
| ADX > 20–25 = có xu hướng, dưới đó = sideway | Wilder 1978 (chưa đọc sách); quy ước phổ biến | `nguồn-báo-cáo` (quy ước); **T001 không ủng hộ** (mục 4) |
| Choppiness Index (Dreiss, đầu 1990s): `100·log10(ΣTR_n / (maxHigh_n − minLow_n)) / log10(n)`; > 61,8 = sideway, < 38,2 = trend | trang trợ giúp TradingView (CHOP); LuxAlgo library | công thức `nguồn-báo-cáo`; chính LuxAlgo ghi ngưỡng là "Fibonacci borrowings, not statistical guarantees" — không tìm thấy kiểm định học thuật nào |
| Efficiency Ratio (Kaufman): `|close − close_n| / Σ|Δclose|`, 0 = nhiễu, 1 = xu hướng thẳng | sách Kaufman (chưa đọc) | `nguồn-báo-cáo`; không tìm thấy kiểm định học thuật |
| "Lọc ADX tăng win rate từ 51% lên 64% trên S&P 500 2000–2023", "loại 38% whipsaw", "lọc 45% tín hiệu giả trên 5.000 lần cắt" | trang blog/giao dịch (kết quả tìm kiếm) | **loại** — không có dữ liệu, mã hay nguồn gốc |
| "Mulvey & Liu (2016): Sharpe 1,4 khi có xu hướng, −0,3 khi sideway" | blog regimeforecast.com | **loại** — tìm theo tác giả/năm/chủ đề không ra bài nào như vậy; không kiểm chứng được |

## 4. T001 — kiểm thử trên dữ liệu thật của hệ (`tái-lập`)

**Thiết kế** (mã: `scripts/t001_sideway_regime_test.py`, `scripts/t001_sideway_analyze.py`; chỉ đọc DP6):
- Lệnh của `breakout_atr` (lookback 100, ATR 10, có SELL, thoát bằng stop ATR trailing) và `sma_trend` (SMA 200, có SELL, thoát khi cắt ngược SMA), 11 symbol (US30, US500, US100, DE40, UK100, FR40, SP35, HK50, J225, GOLD, BTCUSD) × M30/H1/H4, dữ liệu 2019-01-01 → 2026-10-02: **99 274 lệnh** (breakout M30 29 314 / H1 16 388 / H4 4 930; sma M30 28 417 / H1 15 864 / H4 4 361). Tệp lệnh (scratchpad, không lưu repo) sha256 `fad76247…3663`.
- Đo tại **nến vào lệnh**, chỉ dùng dữ liệu quá khứ: ADX(14, Wilder), CHOP(14), ER(20), và hạng biến động `ATR14/close` so với 500 nến trước (kiểm hiệu ứng LeBaron).
- Lãi/lỗ mỗi lệnh tính bằng bội số ATR lúc vào, **chưa trừ phí/spread**.
- Ngưỡng chia ba nhóm (tercile) tính trên **IS = 2019–2022**, áp nguyên cho **OOS = 2023–2026**. So sánh nhóm "xu hướng" với nhóm "sideway" bằng Welch t.

**Kết quả gốc (chưa lọc):** breakout M30 TB +0,053 ATR/lệnh (IS, t=+3,9) / +0,060 (OOS, t=+4,6), win 36%; H1 +0,091 / +0,062; H4 −0,025 / +0,016 (≈0). sma_trend ≈ 0 ở mọi khung (win ≈ 14%).

**Welch t của (nhóm "xu hướng" − nhóm "sideway"), IS / OOS** — dương = bộ lọc giúp đúng hướng mong đợi:

| | ADX cao vs thấp | ER cao vs thấp | CHOP thấp vs cao | Biến động THẤP vs CAO |
|---|---|---|---|---|
| breakout M30 | −0,4 / **−2,3** | +1,8 / **−2,2** | +1,5 / −1,5 | **+2,9** / +0,8 |
| breakout H1 | **−2,7** / +0,3 | −0,7 / +0,7 | +1,7 / +0,4 | +1,1 / +1,2 |
| breakout H4 | −1,7 / **−2,9** | −1,0 / −1,2 | +0,6 / **+3,1** | +0,6 / **+2,3** |
| sma_trend M30 | −0,7 / −0,4 | +1,5 / +0,3 | +1,4 / +1,0 | −1,7 / −1,1 |
| sma_trend H1 | +0,1 / +1,1 | −0,3 / +1,0 | +0,8 / **−2,1** | −0,6 / +1,3 |
| sma_trend H4 | −0,2 / −0,7 | −0,1 / +0,3 | −0,2 / −0,7 | +0,5 / **+2,5** |

**Đọc kết quả:**
- **ADX: không giúp, thậm chí ngược.** Với breakout, vào lệnh lúc ADX đã cao cho kết quả **kém hơn** lúc ADX thấp ở 5/6 phép đo (có ý nghĩa ở M30 OOS, H1 IS, H4 OOS). `suy-luận` hợp lý: breakout bắt đầu từ vùng tích luỹ, khi ADX còn thấp; ADX cao thì xu hướng đã đi xa.
- **ER, CHOP: không nhất quán** giữa IS và OOS, đổi dấu theo khung và chiến lược → không có bằng chứng dùng được.
- **Biến động thấp tốt hơn** với breakout: cùng dấu dương ở cả 3 khung và cả 2 giai đoạn (6/6), nhưng chỉ 2/6 có |t|>2 — **phù hợp hiệu ứng LeBaron (S011)**, bằng chứng yếu–trung bình. Với sma_trend không nhất quán (M30 ngược dấu).
- **Hạn chế**: 4 chỉ báo × 6 tổ hợp × 2 giai đoạn = 48 phép so sánh → ~2–3 kết quả |t|>2 có thể do ngẫu nhiên; lệnh giữa các symbol chồng thời gian, tương quan → t bị phóng đại; chưa trừ phí (lợi thế gộp của breakout ~0,05–0,09 ATR/lệnh có thể bị spread ăn gần hết — `suy-luận`, cần spread thật); một bộ tham số mỗi chiến lược; không walk-forward chọn ngưỡng.

## 5. Tổng hợp — bằng chứng nói gì

1. **Không tìm thấy bằng chứng nào (học thuật hay trên dữ liệu của hệ) rằng ADX/CHOP/ER đo trước khi vào lệnh làm tăng lợi thế gộp của chiến lược trend.** Các con số "cải thiện win rate" trong cộng đồng không có nguồn gốc kiểm được. Trên dữ liệu của hệ, ADX cao còn đi kèm kết quả kém hơn ở breakout.
2. **Bằng chứng tốt nhất (S013)**: đứng ngoài khi xu hướng không có ý nghĩa thống kê (t-stat độ dốc) **không làm tăng Sharpe gộp** nhưng **giảm giao dịch ~2/3** → lợi ích thật nằm ở **chi phí**. Với hệ OG (khung ngắn, phí theo spread), đây là hướng có căn cứ nhất — nhưng phải đo bằng **phí thật**.
3. **Biến động**: tài liệu (LeBaron 1992 qua S011, có phân loại nhân quả) và T001 (breakout, 6/6 cùng dấu) cùng chỉ ra trend **tốt hơn khi biến động thấp** — ngược với trực giác "chỉ trade khi thị trường sôi động".
4. **Khung ngắn**: S011 cho thấy trend vài ngày–vài tuần đã suy yếu từ 2009 trên nhiều hợp đồng; T001 cũng thấy sma_trend ≈ 0 ở mọi khung. Bộ lọc sideway không cứu được một chiến lược không có lợi thế gốc.
5. **Đo đồng thời không phải bộ lọc** (S010): mọi chỉ báo "regime" phải được kiểm ở dạng biết trước khi vào lệnh.

## 6. Phương án cho og_program (đề xuất, chưa làm; xếp theo mức bằng chứng)

1. **Bộ lọc t-stat xu hướng (theo S013)**: chỉ cho phép tín hiệu khi |t| của độ dốc hồi quy log-giá trên N nến > 2; đo bằng **lợi nhuận sau spread thật** và số lệnh, không chỉ win rate.
2. **Bộ lọc biến động (LeBaron)**: bỏ/giảm khối lượng lệnh khi biến động tương đối ở nhóm cao so với lịch sử gần; kiểm walk-forward theo từng symbol.
3. **Không dùng ADX > 25** làm bộ lọc cho breakout_atr khi chưa có bằng chứng mới — dữ liệu hệ đang chỉ ngược lại.
4. Trước mọi bộ lọc: lấy **spread/phí thật** theo symbol để biết breakout_atr có còn lợi thế sau phí không (T001 mới là trước phí).

## 7. Đối chiếu với cái cũ

- Củng cố F024 (S005, t = Sharpe×√năm) và F029 (S006): kết quả ngắn/ít lệnh không đủ ý nghĩa; ở đây dùng t-stat theo lệnh và tách IS/OOS.
- Liên hệ F019 (S003, Hurst mẫu nhỏ, mong manh) — S010 thêm một lỗi khác: Hurst đo đồng thời.
- Liên hệ F033 (cache/khoá kết quả), F031 (độ phân giải đo): T001 lưu mã + tham số + hash để tái lập.
- Mâu thuẫn với kinh nghiệm cộng đồng (ADX cao = nên trade trend) — ghi cả hai bên ở mục 3–4; chưa có nguồn học thuật nào ủng hộ phía cộng đồng.
