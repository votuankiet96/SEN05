# Chủ đề — Repo `je-suis-tm/quant-trading` ("17 trading strategies in Python"): cơ chế từng chiến lược và độ phù hợp với khung nhỏ hơn H1

Ngày: 2026-10-21. Người dùng đưa bài đăng giới thiệu repo (17 chiến lược Python miễn phí) và hỏi: "tìm hiểu nguồn này và phân tích cho tôi rõ hơn những cơ chế chiến lược đầu tư … Có chiến lược nào phù hợp cho khung thời gian nhỏ hơn H1 không?"

Nhãn: `đọc mã` (đọc mã nguồn thật, KHÔNG chạy) · `nguồn-báo-cáo` (nguồn nói, chưa kiểm) · `lỗi-nguồn` (mã mâu thuẫn với chính mô tả/README hoặc có nhìn trước; xác minh bằng đọc mã, chưa chạy) · `suy-luận` (phân tích của tôi, chưa đo). **Không có phép đo nào trên dữ liệu của hệ, không chạy mã nào của repo** (theo quy ước: đọc để hiểu cơ chế).

**Tóm tắt trả lời:** (1) Trong 17 mục chỉ 9 là chiến lược kỹ thuật một tài sản dùng OHLC; 8 mục còn lại là cặp thống kê, vĩ mô–FX, quyền chọn, công cụ tính chỉ số hoặc dự án nghiên cứu. (2) Chỉ **London Breakout** và **Dual Thrust** (cùng họ phá khoảng giá đầu phiên, opening range breakout) được thiết kế cho khung trong ngày; các chiến lược kỹ thuật còn lại chỉ minh hoạ trên nến ngày. (3) README nói thẳng mọi backtest "không ma sát" (không trượt giá, phí, thanh khoản) nên repo KHÔNG có bằng chứng lãi/lỗi nào ở khung nhỏ. (4) Mã có nhiều chỗ lệch/nhìn trước (mục 4), đừng chép nguyên.

## 0. Nguồn

- **S085** — GitHub `je-suis-tm/quant-trading`, nhánh mặc định, commit `611b73f` (2024-04-14), clone nông vào thư mục scratchpad mới (không chạy mã). Đã đọc: `README.md`, 9 script kỹ thuật (MACD, Heikin-Ashi, London Breakout, Awesome, Dual Thrust, Parabolic SAR, Bollinger, RSI, Shooting Star), Pair trading, Options Straddle (phần đầu, hàm tín hiệu), VIX Calculator (đầu), README của Monte Carlo / Oil Money / Ore Money / Smart Farmers. **CHƯA đọc:** Portfolio Optimization (repo `graph-theory`), Wisdom of Crowds (repo `machine-learning`), các script con của Oil Money và Smart Farmers.
- **S086** — Quantiacs "Intro to Algorithmic Trading with Heikin-Ashi" (nguồn luật Heikin-Ashi mà README dẫn): URL trả HTTP 404; Wayback không lấy được → luật gốc CHƯA đối chiếu.
- **S087** — StockSharp Designer "Heikin Ashi Reversal" (doc.stocksharp.com/en/designer-gallery/Heikin_Ashi_Reversal; CHỈ tóm tắt tìm kiếm): ví dụ cách dùng phổ biến — vào long khi thân nến HA dương sau nến không dương.

## 1. 17 mục thật sự là gì

| # | Mục (README) | Loại | Dữ liệu/khung trong mã | Dùng cho tín hiệu 1 symbol OHLC? |
|---|---|---|---|---|
| 1 | MACD oscillator | kỹ thuật, xu hướng | ngày, Yahoo (mã nhập) | có — trùng lõi `ma_cross` |
| 2 | Pair trading | thống kê (2 tài sản) | ngày, NVDA–AMD 2013–14 | không (hai symbol + kiểm định đồng tích hợp) |
| 3 | Heikin-Ashi | kỹ thuật, nến | ngày, NVDA 2015–18 | có; hướng luật đáng ngờ (mục 2.4) |
| 4 | London Breakout | kỹ thuật, theo giờ, TRONG NGÀY | 1 phút, GBPUSD 06/2018 | có, cần cửa sổ giờ |
| 5 | Awesome oscillator | kỹ thuật | ngày | có — trùng lõi `ma_cross` |
| 6 | Oil Money | vĩ mô–FX (quantamental) | ngày/tháng; dầu, tiền tệ | không (cần chuỗi ngoài OHLC của symbol) |
| 7 | Dual Thrust | kỹ thuật, theo giờ, TRONG NGÀY | 1 phút, GBPUSD | có, cần phiên + biên độ ngày trước |
| 8 | Parabolic SAR | kỹ thuật, trạng thái | ngày, EA 2016–18 | có |
| 9 | Bollinger Bands (mẫu hình W) | kỹ thuật, mẫu hình | 1 phút, GBPUSD 06/2018 | phải viết lại ngưỡng (mục 2.7) |
| 10 | RSI (+ mẫu hình) | kỹ thuật, dao động | ngày, FCAU 2016–18 | RSI 30/70 có; mẫu hình đầu-vai không |
| 11 | Monte Carlo | nghiên cứu (kết luận phủ định) | ngày, cổ phiếu | không |
| 12 | Options Straddle | quyền chọn | chuỗi quyền chọn Euro Stoxx 50, 7–8/2019 | không (cần giá quyền chọn) |
| 13 | Portfolio Optimization | danh mục (repo khác) | — | không; CHƯA đọc |
| 14 | Smart Farmers | nông nghiệp, tối ưu lồi | dữ liệu mùa vụ | không |
| 15 | VIX Calculator | công cụ tính chỉ số | quyền chọn hàng hoá (Henry Hub) | không (không phải tín hiệu) |
| 16 | Wisdom of Crowds | ML đồng thuận phân tích viên (repo khác) | — | không; CHƯA đọc |
| 17 | Shooting Star | kỹ thuật, nến | ngày, Vodafone 2000–21 | có về dữ liệu; mã có nhìn trước (mục 2.9) |

(Ore Money chỉ có README "sắp ra", không nằm trong 17.) `nguồn-báo-cáo` + `đọc mã` — S085.

**Dữ liệu và chi phí:** README: "all trades are frictionless. No slippage, no surcharge, no illiquidity"; "no HFT … ultra high frequency data are very expensive". Dữ liệu 1 phút duy nhất trong repo là `data/gbpusd.csv` (GBPUSD mid, 2018-06-01 00:00 → 2018-06-29 16:58, 29.698 dòng, múi giờ EST của histdata); các script còn lại tải nến ngày từ Yahoo. Tác giả chỉ có hai nhận xét hiệu quả, đều không kèm số: MACD có Sharpe cao hơn Awesome (ít lệnh hơn, lãi hơn, drawdown lớn hơn) và RSI thoát theo RSI "inefficient and unprofitable". Không có số liệu hiệu quả nào cho chiến lược trong ngày. `nguồn-báo-cáo` — S085.

## 2. Cơ chế từng chiến lược kỹ thuật (đọc mã)

### 2.1 MACD oscillator (`MACD Oscillator backtest.py`)
Thực chất là **cắt hai SMA** trên `Close` (ma1, ma2 do người dùng nhập; gợi ý 12/26 cổ điển hay 10/21), KHÔNG phải MACD (không EMA, không signal line). Vị thế = 1 khi ma1 ≥ ma2, ngược lại 0 (chỉ mua/thoát); `signals = positions.diff()`. `rolling(min_periods=1)` nên các giá trị đầu là trung bình thiếu kỳ. Dữ liệu ngày. Lõi gần như trùng `ma_cross` (SMA13/34 + MACD hist) của hệ.

### 2.2 Awesome oscillator (`Awesome Oscillator backtest.py`)
AO = SMA5 − SMA34 của giá trung bình (High+Low)/2. Mô tả hai cách tín hiệu: cắt SMA và "saucer". Trong mã, nhánh saucer gán `signals = ±1` rồi **bị hai câu lệnh `if ma1>ma2` / `if ma1<ma2` ghi đè ở mọi nến có hai SMA khác nhau** (sau 34 nến đầu gần như luôn đúng), nên thực chất chỉ còn cắt SMA5/34 long-only (thoát khi đảo); điều kiện saucer còn dùng màu nến (Open vs Close) thay vì màu cột AO và gán +1 cho điều kiện mô tả là "bearish saucer". Mã so với MACD(EMA) làm nhóm đối chứng (Sharpe, drawdown). `lỗi-nguồn`.

### 2.3 Parabolic SAR (`Parabolic SAR backtest.py`)
SAR của Wilder: AF khởi 0,02, bước 0,02, tối đa 0,2 ("chung cho FX và hàng hoá"; cổ phiếu 0,01); EP = đỉnh/đáy cực trị của xu hướng; đảo chiều khi High/Low chạm SAR. Vị thế = 1 khi `real sar < Close` (long-only), `signals = diff` (vào/thoát). Tín hiệu đảo luôn xen kẽ nên không cần lọc chống trùng. Dữ liệu ngày (EA). Là bộ đo trạng thái xu hướng khác với SMA/MACD của hệ.

### 2.4 Heikin-Ashi (`Heikin-Ashi backtest.py`)
HA close = (O+H+L+C)/4; HA open = (HA open + HA close) kỳ trước / 2 (khởi bằng Open đầu); HA high/low = max/min của HA open, HA close, High, Low. Luật trong mã (`signals = +1` là mua — kiểm bằng `portfolio()`: tiền giảm khi +1):
- **"Long":** HA open > HA close (nến HA GIẢM), HA open == HA high (không bóng trên), thân lớn hơn thân nến trước, nến trước cũng giảm; cộng dồn tối đa `stls = 3` vị thế.
- **"Thoát" (bán hết):** HA open < HA close (nến HA TĂNG), HA open == HA low (không bóng dưới), nến trước cũng tăng; bỏ qua nếu không có vị thế.
Hướng này NGƯỢC cách dùng phổ biến (S087: long khi thân HA dương); bài gốc Quantiacs 404 (S086) nên không biết mã chép sai hay tác giả cố ý; kết quả mẫu (NVDA 2015–18) là giai đoạn tăng mạnh. Duy nhất script này có thống kê đầy đủ (Omega, Sortino, MDD, Calmar). `lỗi-nguồn?` (chưa đối chiếu nguồn gốc).

### 2.5 London Breakout (`London Breakout backtest.py`) — TRONG NGÀY
Dữ liệu 1 phút GBPUSD (trung bình bid/ask), giờ EST (UTC−5), KHÔNG xử lý giờ mùa hè. Quy trình:
1. **Khoảng giá:** giá cao nhất/thấp nhất của giờ cuối trước London = EST 02:00–02:59 (= GMT 07:00–07:59).
2. **Vào lệnh:** từ EST 03:00 (GMT 08:00) trong 30 phút (`open_minutes = 30`), giá vượt biên trên → mua, thủng biên dưới → bán; chỉ lần đầu (cumsum); bỏ qua nếu vượt biên quá `risky_stop` = 0,01 (100 pip) vì coi là biến động bất thường.
3. **Thoát:** cắt/chốt cố định ±`risky_stop/2` = ±0,005 (50 pip) quanh giá vào; hết phiên London (EST 12:00 = GMT 17:00) thoát hết.
Ngưỡng là giá tuyệt đối (0,01) nên không dùng được nguyên cho chỉ số hay vàng (cần quy theo ATR hay %). Cơ chế "chênh lệch thông tin giữa phiên Á và phiên Âu".

### 2.6 Dual Thrust (`Dual Thrust backtest.py`) — TRONG NGÀY
Cùng dữ liệu/múi giờ với London Breakout. Mỗi ngày lấy OHLC của phiên EST 03:00–12:00; `range = max(HH(high) − LL(close), HH(close) − LL(low))` trên `rg = 5` ngày. Tại giờ mở (EST 03:00): `upper = giá mở + param × range`, `lower = giá mở − (1 − param) × range` (param = 0,5). Giá trong ngày vượt `upper` → mua, thủng `lower` → bán, **đảo chiều** nếu đi từ ngưỡng này sang ngưỡng kia; không có stop; đóng hết lúc EST 12:00. **Lỗi:** README nói dựa trên "ngày trước" nhưng cửa sổ `rolling(rg)` GỒM ngày hiện tại, tức ngưỡng lúc 03:00 dùng high/low/close của cả phiên hôm đó (nhìn trước). `lỗi-nguồn` (đọc mã, chưa chạy).

### 2.7 Bollinger Bands — đáy W (`Bollinger Bands Pattern Recognition backtest.py`)
Bollinger(20, 2σ) trên giá. Quét lùi `period = 75` nến tìm 5 nút của chữ W: nến hiện tại có giá > dải trên (điều kiện 4, chưa có vị thế); nút giữa j nơi đường giữa ≈ giá ≈ dải trên hiện tại (điều kiện 2); nút đáy thứ nhất k chạm dải dưới (1); nút đáy thứ hai m nằm trên dải dưới nhưng giá thấp hơn nút đầu (3). Vào mua khi đủ 4 điều kiện; thoát khi độ lệch chuẩn < beta (co hẹp dải). `alpha = beta = 0,0001` tuyệt đối (cỡ 1 pip FX, không scale-free); `period = 75` được chú thích là "3 tháng" nhưng demo chạy trên dữ liệu 1 phút nên là 75 phút. Cần viết lại bằng đơn vị σ/ATR mới dùng được cho symbol khác. `đọc mã`.

### 2.8 RSI (`RSI Pattern Recognition backtest.py`)
- **RSI 30/70 (là thứ `main()` thật sự gọi):** RSI(14) kiểu Wilder (SMMA); vị thế +1 khi RSI < 30, −1 khi RSI > 70, `signals = diff`. Ngày, FCAU.
- **Mẫu hình đầu-vai (`pattern_recognition`, KHÔNG được `main()` gọi):** README nói nhận mẫu hình trên RSI nhưng mã nhận trên **giá Close** với `delta = 0,2` đơn vị giá tuyệt đối (chỉ hợp cổ phiếu vài đô); vào SHORT khi khớp 7 nút, thoát sau 5 nến hoặc RSI tăng 4; tác giả tự nhận cách thoát này "inefficient and unprofitable". `lỗi-nguồn`.

### 2.9 Shooting Star (`Shooting Star backtest.py`)
Short khi đủ 8 điều kiện: nến giảm (Open ≥ Close); bóng dưới < 0,2 × thân; thân < 0,5 × |trung bình CÓ DẤU của (Open − Close) trên TOÀN mẫu| (không phải độ lớn thân trung bình: số này gần 0 nên điều kiện rất chặt); bóng trên ≥ 2 × thân; Close[t] ≥ Close[t−1] ≥ Close[t−2]; **nến SAU có high ≤ high và close ≤ close** (điều kiện 7–8). Thoát khi giá đóng lệch hơn ±5% so với giá vào hoặc sau 7 nến. Điều kiện 3 dùng thống kê toàn mẫu và điều kiện 7–8 dùng nến kế tiếp (`shift(-1)`) nên tín hiệu ở nến t cần dữ liệu t+1: không giao dịch được thật. Chỉ có hướng bán. `lỗi-nguồn`.

### 2.10 Ví dụ số minh hoạ (số tự đặt; phép tính kiểm bằng mã tự viết theo công thức, KHÔNG chạy mã của repo)

- **Heikin-Ashi — mã mua trong đà giảm:** 4 nến giảm O/H/L/C = 100/101/98/99, 99/99,5/96/97, 97/97,2/93/94, 94/94,1/90/91. Nến 0: HA open 100 ≠ HA high 101 → không. Nến 1: HA open 99,75 > HA close 97,875, HA open = HA high = 99,75 (High thật 99,5 thấp hơn), thân 1,875 > 0,5 của nến 0, nến 0 cũng giảm → "long" (+1). Nến 2 (HA open 98,81 / HA close 95,30) và nến 3 (97,06 / 92,28) cũng đạt → cộng dồn tới 3 lệnh mua ngay trong chuỗi giảm. Đây là minh hoạ cụ thể cho chỗ ngược thông lệ ở §2.4.
- **Parabolic SAR — một bước và một lần đảo chiều:** SAR 9,40, EP 11,40, AF 0,08 → SAR kế = 9,40 + 0,08 × (11,40 − 9,40) = 9,56; nến kế có Low 9,50 < 9,56 → đảo chiều: SAR mới = EP = 11,40 (nằm trên giá), AF về 0,02, mã thoát lệnh mua (Close ≤ SAR).
- **RSI(14) Wilder:** avg tăng 0,30, avg giảm 0,10 → RS 3 → RSI 75 (> 70: vị thế bán khống). Nến kế giảm 0,5: avg tăng = (0,30 × 13 + 0)/14 = 0,2786, avg giảm = (0,10 × 13 + 0,5)/14 = 0,1286 → RSI 68,4 → về dưới 70, thoát.
- **Dual Thrust:** giá mở 1,3300; HH(High, 5 ngày) 1,3400, LL(Close) 1,3200, HH(Close) 1,3380, LL(Low) 1,3180 → range1 = range2 = 0,0200 → range 0,0200; param 0,5 → Upper 1,3400, Lower 1,3200 (cần đi 100 pip từ giá mở mới vào lệnh).
- **London Breakout:** biên 07:00–07:59 GMT = 1,3220–1,3250. 08:07 giá 1,3253 (vượt 3 pip, < 100 pip) → MUA 1,3253; thoát khi giá > 1,3303 hoặc < 1,3203 hoặc 17:00 GMT. Nếu lúc mở phiên giá nhảy lên 1,3352 (vượt biên 102 pip > 100 pip) → bỏ, không vào.
- **Pair trading:** giá thực asset2 52,0 so với mô hình 50,4 → phần dư 1,6; σ phần dư 0,8 → z = 2,0 (asset2 "đắt" 2σ so với asset1) → mua asset1, bán asset2 nếu z vượt ngưỡng.
- **Straddle:** strike 3.500, call 50 + put 48 = chi phí 98 → hoà vốn 3.402 và 3.598; lỗ tối đa 98 nếu đáo hạn đúng 3.500.
- **Shooting Star:** nến Open 100,0, Close 99,6, High 101,2, Low 99,55 → thân 0,4; bóng dưới 0,05 < 0,2 × 0,4 = 0,08; bóng trên 1,2 ≥ 2 × 0,4 = 0,8; nếu Close hai nến trước tăng dần và nến sau có High ≤ 101,2, Close ≤ 99,6 → bán tại 99,6 (điều kiện 3 phụ thuộc thống kê toàn mẫu nên không đặt số).

## 3. Các mục không phải tín hiệu kỹ thuật (tóm tắt)

- **Pair trading (#2):** Engle–Granger hai bước (OLS Y~X, ADF phần dư p ≤ 0,05, hệ số hiệu chỉnh âm) trên cửa sổ 250 nến, kiểm lại mỗi nến; khi đồng tích hợp bắt đầu, chuẩn hoá phần dư thành z và đặt ngưỡng `z[bắt đầu] ± std(phần dư)`; z vượt ngưỡng trên → dài/ngắn một cặp, dưới → ngược lại; đóng khi mất đồng tích hợp. **Lệch đơn vị:** z không thứ nguyên nhưng cộng với độ lệch chuẩn tính theo giá (README nói "một sigma"). Mẫu NVDA–AMD 2013–14, tác giả cảnh báo chính cặp này sau đó vỡ quan hệ. `lỗi-nguồn` (đơn vị).
- **Oil Money (#6):** hồi quy tiền tệ các nước xuất khẩu dầu theo giá dầu để tìm chênh lệch. Kết quả tác giả tự báo: NOK bị EUR chi phối; sau khi mô hình "vỡ" lại có đà mạnh nên rút ra quy tắc momentum ±2σ phần dư (lãi ~2%, dò tham số thấy thời gian giữ lệnh quan trọng hơn mức chốt/cắt); R² giảm mạnh 2015–2018 (lệnh trừng phạt), RUB bị địa chính trị chi phối. Ore Money mới là README. `nguồn-báo-cáo`.
- **Options Straddle (#12) / VIX Calculator (#15):** tín hiệu straddle = 1 khi |giá call − giá put| < ngưỡng cùng strike, cùng ngày đáo hạn (mua cả call và put hưởng biến động); dữ liệu là chuỗi quyền chọn Euro Stoxx 50 tháng 7–8/2019. VIX Calculator tính chỉ số theo phương pháp CBOE (Riemann, Taylor) cho quyền chọn hàng hoá — công cụ đo, không phải chiến lược.
- **Monte Carlo (#11):** kết luận PHỦ ĐỊNH — độ chính xác hướng giá ≈ tung đồng xu và không phụ thuộc số lần mô phỏng (500–1.500); không dự đoán được đuôi béo (GE 2018: thấp nhất trong 500 mô phỏng 10,99 so với 6,71 thực; Nvidia 2008: 6,086 so với 5,9). `nguồn-báo-cáo`.
- **Smart Farmers (#14):** tối ưu lồi kế hoạch trồng trọt để dự báo cung nông sản; **Portfolio Optimization (#13)** và **Wisdom of Crowds (#16)** nằm ở repo khác, CHƯA đọc.

## 4. Lỗi và điểm lệch của nguồn (tổng hợp, đều `lỗi-nguồn` trừ khi ghi khác)

1. README: mọi backtest không ma sát (không trượt giá/phí/thanh khoản) — kết quả không áp dụng được thẳng cho khung nhỏ nơi chi phí chiếm tỉ lệ lớn (`nguồn-báo-cáo`).
2. "MACD oscillator" là cắt SMA, không phải MACD (EMA + signal line).
3. Awesome: nhánh saucer bị nhánh cắt SMA ghi đè; điều kiện saucer dùng màu nến, gán nhãn ngược mô tả.
4. Heikin-Ashi: hướng vào/thoát ngược cách dùng phổ biến; bài gốc 404 (chưa đối chiếu).
5. Dual Thrust: cửa sổ `rolling` gồm ngày hiện tại (nhìn trước) trong khi README nói "ngày trước".
6. Shooting Star: điều kiện 7–8 dùng nến kế tiếp, điều kiện 3 dùng thống kê toàn mẫu và là trung bình CÓ DẤU (nhìn trước; ngưỡng thân gần 0).
7. RSI: mẫu hình "trên RSI" thật ra trên Close với ngưỡng tuyệt đối 0,2; `main()` không gọi nó.
8. Bollinger W và London Breakout: ngưỡng/khoảng là giá tuyệt đối (1 pip, 100 pip) chỉ hợp GBPUSD; `period = 75` "3 tháng" nhưng chạy trên dữ liệu 1 phút.
9. Pair trading: ngưỡng lẫn đơn vị (z + độ lệch chuẩn theo giá).
10. Mẫu dữ liệu cực nhỏ (1 tháng 1 phút; 1–2 năm ngày; 1 mã mỗi chiến lược): không có kiểm định ngoài mẫu hay so với mua-giữ.

## 5. Khung thời gian nhỏ hơn H1

### 5.1 Repo có gì cho khung nhỏ
Chỉ **London Breakout** và **Dual Thrust** (cùng họ phá khoảng giá đầu phiên) được thiết kế cho trong ngày: cần dữ liệu từ 1–15 phút, lấy giờ làm cấu trúc (khoảng giá phiên Á, giờ mở phiên Âu, thoát cuối phiên). Bollinger W chỉ demo trên dữ liệu 1 phút (không có lý do thiết kế). Sáu mục kỹ thuật còn lại minh hoạ trên nến ngày. `suy-luận` từ mã.

### 5.2 Độ phù hợp với hệ (tín hiệu vào lệnh, M10–M45, 15 symbol)

| Chiến lược | Thiết kế cho | Chạy được ở M10–M45? | Vướng mắc chính |
|---|---|---|---|
| London Breakout | trong ngày | tốt nhất ở M10/M20/M30 | cửa sổ giờ theo symbol + giờ mùa hè (dữ liệu UTC); "chỉ lần đầu trong ngày" cần trạng thái theo ngày; thoát/stop nằm ngoài hệ |
| Dual Thrust | trong ngày | được | cần phiên + biên độ nhiều ngày (mục 5.3c); lỗi nhìn trước trong mã; đảo chiều trong ngày |
| Parabolic SAR | xu hướng (ngày) | được, nhưng đảo chiều dày ở khung nhỏ | cần lọc xu hướng khung lớn như `ma_cross`; tín hiệu đã xen kẽ |
| Heikin-Ashi | xu hướng (ngày) | được | hướng luật cần xác định lại; HA open đệ quy phụ thuộc điểm khởi (ảnh hưởng giảm một nửa mỗi nến) |
| MACD(SMA) / Awesome | xu hướng (ngày) | được | trùng lõi `ma_cross`: ít giá trị mới |
| Bollinger W / RSI đầu-vai / Shooting Star | mẫu hình (ngày) | không nên như hiện tại | ngưỡng tuyệt đối, nhìn trước, tín hiệu hiếm và nhiễu ở khung nhỏ |
| Pair trading, Oil Money, Straddle, VIX, MC, Smart Farmers | khác | không | cần hai symbol/dữ liệu ngoài OHLC |

### 5.3 Ràng buộc khi đưa chiến lược trong ngày vào hệ (`suy-luận`, chưa đo)
a. **M45 không chia đều giờ:** nến M45 mở 00:00, 00:45, 01:30, 02:15, 03:00, … (log DE 21/10: nến M45 mở 06:00 đóng 06:45, nến trước mở 05:15), nên mốc 07:00 nằm GIỮA một nến; cửa sổ theo giờ chỉ khớp M10/M20/M30 (và H1 trở lên).
b. **Giờ mùa hè:** phiên Luân Đôn mở 08:00 giờ địa phương = 08:00 UTC mùa đông / 07:00 UTC mùa hè; repo dùng EST cố định và bỏ qua DST. Mỗi symbol có phiên riêng (FX, chỉ số châu Âu mở ~07:00–08:00 UTC; US30/US500/US100 ~13:30–14:30 UTC; HK50/J225 phiên Á; GOLD, BTCUSD gần 24 giờ).
c. **Cửa sổ 500 nến của DE:** ở M10 là 3,5 ngày lịch (chỉ báo/khoảng giá cần nhiều ngày sẽ thiếu): Dual Thrust `rg = 5` ngày cần 720 nến M10 > 500 (`rg ≤ 3` mới đủ; chỉ số có khoảng nghỉ nên còn ít hơn); London Breakout chỉ cần dưới 1 ngày nên đủ.
d. **Hệ chỉ phát tín hiệu vào lệnh:** cả hai chiến lược trong ngày dựa vào thoát (khung ±50 pip, đóng cuối phiên, đảo chiều); phần đó thuộc hệ đặt lệnh (BO20/DE+OF), không nằm trong `we_core`.
e. **Chi phí:** biên độ nến co theo √thời gian (xấp xỉ bước ngẫu nhiên: M10 ≈ 0,41×, M30 ≈ 0,71×, M45 ≈ 0,87× biên độ H1), spread cố định chiếm tỉ lệ ATR lớn hơn ở khung nhỏ; hệ chưa có dữ liệu spread nên chưa lượng hoá được.
f. **Trạng thái:** tất cả chiến lược hiện có chỉ giữ MỘT trạng thái (chống trùng cùng chiều); "một lần/ngày" của London Breakout cần đặt lại theo ngày/phiên.

### 5.4 Liên hệ nguồn đã nạp
London Breakout kiểu đóng cửa EUR/USD H1 2016–2026: 1.230 lệnh, thắng 36,26%, PF 0,87 (F179, S079); ORB Crabel trên 42 hợp đồng tương lai 1980–2016 sau chi phí "chưa giao dịch được nếu không có luật bổ sung" (F182, S081). Repo này không có số liệu nào để đối chiếu. `suy-luận`.

## 6. Việc cần quyết (chưa làm gì, chưa code, chưa đo)

1. Có đưa họ **phá khoảng giá phiên (London Breakout/Dual Thrust)** vào hệ không; nếu có: dạng tín hiệu (đóng cửa vượt biên hay lệnh chờ ở biên), khung (M10/M20/M30), phiên theo từng symbol (giờ UTC + mùa hè), "một lần/ngày", cách quy ngưỡng theo ATR.
2. Có muốn **Parabolic SAR / Heikin-Ashi** như khối trạng thái bổ sung chạy M10–M45 sau lọc xu hướng khung lớn (cùng khuôn `ma_cross`) hay thôi.
3. Hay giữ ở mức ghi nhận (đã có 5 ứng viên ngày 20/10: `topic_2026_candidate-strategies.md`).
4. Nếu muốn có bằng chứng: đo số tín hiệu và độ nén phiên trên dữ liệu hệ (chỉ khi được yêu cầu rõ; theo quy ước này đợt chưa đo).
