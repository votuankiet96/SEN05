# Phương pháp validate/tối ưu tham số chiến lược trading (time series) — chống overfitting

> Tài liệu THAM CHIẾU SỐNG. Mục đích: một phiên Claude mới đọc vào là hiểu ngay
> hệ thống `bo_workflow/core_engine/` đã làm được gì (walk-forward 4 chặng +
> Monte Carlo block-bootstrap + plateau selection), các kỹ thuật này đứng ở đâu
> so với chuẩn học thuật/cộng đồng quant, và nên đầu tư tiếp vào đâu — không
> cần hỏi lại từ đầu. Đây là RESEARCH THUẦN — không kèm code, không kèm kết quả
> backtest thật. Mọi số liệu/tên phương pháp đều có nguồn trích ở cuối file;
> không có số liệu tự bịa.
>
> Bối cảnh áp dụng (nhắc lại để không quên khi đọc lại sau này): 1 người dùng
> cá nhân, tài khoản FTMO thật, 2 cBot đơn giản (Combo, MA Cross — mỗi bot chỉ
> optimize tối đa ~3-4 tham số: KslLevel, KtpLevel, RiskPercent, đôi khi thêm 1
> tham số exit), hạ tầng là 1 máy Windows (VM-BO20) chạy `ctrader-cli` qua
> Python (`bo_workflow/core_engine/`), không phải cụm tính toán. Chi phí phải
> tính theo đúng quy mô này, không theo quy mô quỹ định lượng.

## Mục lục

1. [Đối chiếu nhanh: đã có / chưa có](#1-đối-chiếu-nhanh-đã-có--chưa-có)
2. [Walk-Forward Analysis/Optimization (Pardo)](#2-walk-forward-analysisoptimization-pardo)
3. [Parameter stability / Plateau / Sensitivity analysis (Pardo)](#3-parameter-stability--plateau--sensitivity-analysis-pardo)
4. [Monte Carlo block bootstrap cho chuỗi lệnh/equity](#4-monte-carlo-block-bootstrap-cho-chuỗi-lệnhequity)
5. [Deflated Sharpe Ratio (DSR)](#5-deflated-sharpe-ratio-dsr)
6. [Probability of Backtest Overfitting / CSCV](#6-probability-of-backtest-overfitting--cscv)
7. [Combinatorial Purged Cross-Validation (CPCV)](#7-combinatorial-purged-cross-validation-cpcv)
8. [White's Reality Check / Hansen's SPA test](#8-whites-reality-check--hansens-spa-test)
9. [Monte Carlo Permutation Test (Aronson / Masters)](#9-monte-carlo-permutation-test-aronson--masters)
10. [Multi-symbol cross-check](#10-multi-symbol-cross-check)
11. [Bảng so sánh tổng hợp](#11-bảng-so-sánh-tổng-hợp)
12. [Xếp hạng ưu tiên triển khai tiếp theo](#12-xếp-hạng-ưu-tiên-triển-khai-tiếp-theo)
13. [Nguồn tham khảo](#13-nguồn-tham-khảo)

---

## 1. Đối chiếu nhanh: đã có / chưa có

Đọc trực tiếp `bo_workflow/core_engine/facilitator.py` (`run_walkforward()`,
`run_montecarlo()`), `core_engine/opt_util/montecarlo.py`, và
`core_engine/output_process/selection.py` (`plateau()`, `rank()`, `eligible()`)
cho thấy 3 kỹ thuật đã bàn trong `CLAUDE.md` mục
"combo-optimization-methodology" **đã có code thật, không chỉ là kế hoạch**:

| Kỹ thuật đã bàn trong CLAUDE.md | Trạng thái thật | File |
|---|---|---|
| Walk-Forward Optimization (rolling, 12mo:3mo) | ✅ Đã code, đúng loại rolling, đúng tỉ lệ mặc định | `planner.walkforward_windows()`, `facilitator.run_walkforward()` |
| Parameter perturbation / vùng "cao nguyên" | ✅ Đã code dưới tên `plateau()`, tích hợp thẳng vào bước chọn tham số của walk-forward | `selection.plateau()` |
| Trade reshuffling / block-bootstrap Monte Carlo | ✅ Đã code, nhưng là block-bootstrap trên **return NGÀY** (không phải xáo từng lệnh), khối cố định không chồng lấn | `montecarlo.run()` |
| Multi-symbol cross-check | ⚠️ Kiến trúc hỗ trợ sẵn (specs/rows đều khoá theo `(symbol, timeframe, window)`) nhưng chưa có runner "so sánh chéo symbol" chuyên dụng | — |
| CPCV / Deflated Sharpe Ratio | ❌ Chưa có dòng code nào | — |

Phần còn lại của tài liệu này mô tả từng kỹ thuật, đối chiếu với đúng đoạn code
trên, và bổ sung 3 kỹ thuật uy tín tương đương mà CLAUDE.md chưa nhắc tới:
Probability of Backtest Overfitting (PBO/CSCV), White's Reality Check/Hansen's
SPA, và Monte Carlo Permutation Test (Aronson/Masters).

---

## 2. Walk-Forward Analysis/Optimization (Pardo)

**Cơ chế**: chia time series thành các cặp cửa sổ In-Sample (IS, dùng để
optimize/chọn tham số) → Out-of-Sample (OOS, dùng để kiểm chứng, KHÔNG optimize
lại). Hai biến thể:
- **Anchored**: điểm bắt đầu IS cố định, IS dài dần ra theo mỗi vòng.
- **Rolling**: cả điểm bắt đầu lẫn kết thúc IS đều trượt về sau, độ dài IS cố
  định — Pardo khuyến nghị biến thể này cho hầu hết trường hợp.

Robert Pardo (*Design, Testing, and Optimization of Trading Systems*, 1992;
tái bản 2008) định nghĩa **Walk-Forward Efficiency (WFE)** =
hiệu suất OOS (đã annualize) ÷ hiệu suất IS (đã annualize) để đo mức "sống
sót" ra ngoài mẫu của tham số đã chọn. Ngưỡng diễn giải phổ biến (tổng hợp từ
nhiều nguồn thứ cấp trích dẫn Pardo, xem mục nguồn): WFE ≥ 0.5 chấp nhận được;
0.3–0.5 mất phần lớn edge, nghi ngờ; < 0.3 gần như chắc chắn overfit. Pardo
cũng khuyến nghị tỉ lệ độ dài **IS : OOS phổ biến 4–6 lần** (vd OOS 3 tháng thì
IS nên 12–18 tháng).

**Đối chiếu với code thật**:
- `planner.walkforward_windows()` (dòng 138-165): đúng kiểu **rolling** —
  `cursor` (mốc bắt đầu IS) trượt theo `step_months` mỗi vòng, không neo cố
  định. Mặc định `is_months=12`, `oos_months=3`, `step_months=oos_months` → tỉ
  lệ IS:OOS = **4x**, đúng ở CẬN DƯỚI khoảng khuyến nghị 4-6x của Pardo — hợp
  lệ, không sai, nhưng không có biên dư nếu muốn thử IS dài hơn để tăng độ tin
  cậy của bước chọn tham số. `step_months` mặc định bằng `oos_months` nghĩa là
  các cửa sổ OOS liên tiếp nhau khít, không chồng lấn, không hở ngày nào (đúng
  comment trong code về mốc "loại trừ").
- `facilitator.run_walkforward()` (dòng 97-192): implement đúng 4 chặng chuẩn
  (chia cửa sổ → quét lưới trên IS → chọn 1 tổ hợp/cửa sổ theo luật đã CHỐT
  TRƯỚC → chạy đúng 1 lần trên OOS). Điểm đáng chú ý: code tự chặn cứng bằng
  `ValueError` nếu thiếu cờ `parity_certified` — đúng tinh thần "luật chọn phải
  chốt trước khi nhìn kết quả, nếu không số đo OOS mất sạch ý nghĩa" mà chính
  comment trong file ghi rõ (dòng 52-54).
- **Khoảng trống nhỏ**: `walkforward_table()` (dòng 195-229) trả về cả
  `train_*` và `test_*` cho từng metric (net_profit, profit_factor,
  total_trades, win_rate, max_equity_drawdown_pct) nhưng KHÔNG tính sẵn tỉ số
  WFE tường minh (test/train) — người dùng phải tự chia 2 cột khi đọc bảng.
  Đây là bổ sung RẺ (thêm 1-2 cột derived trong hàm đã có, không cần chạy lại
  backtest nào) nếu muốn có con số WFE chuẩn Pardo ngay trong bảng.

**Chi phí/giá trị cho quy mô hệ thống này**: đã triển khai đầy đủ, đúng kiến
trúc chuẩn. Không cần làm gì thêm ngoài việc tuỳ chọn thêm cột WFE derived.
Đây là nền tảng đúng nhất và đáng tin cậy nhất trong toàn bộ hệ thống hiện có.

---

## 3. Parameter stability / Plateau / Sensitivity analysis (Pardo)

**Cơ chế**: Pardo cảnh báo việc chọn tổ hợp tham số theo "đỉnh tối ưu" đơn lẻ
trên lưới quét (1 ô có điểm số cao nhất) rất dễ là nhiễu thống kê — một tổ hợp
tốt THẬT phải nằm giữa một vùng ("cao nguyên", plateau) mà các tổ hợp lân cận
cũng đều tốt, chứ không phải một đỉnh cô lập rơi xuống ngay khi lệch 1 nấc
tham số. Đây là ý tưởng định tính ("visual inspection của bản đồ độ nhạy tham
số"/sensitivity analysis) phổ biến trong tài liệu Pardo và cộng đồng system
trading, không đi kèm 1 công thức percentile cụ thể duy nhất — mỗi implement
tự hình thức hoá theo cách riêng.

**Đối chiếu với code thật** — `selection.plateau()` (dòng 59-100):
- Với mỗi ô lưới (toạ độ theo `dimensions`, ví dụ `("KslLevel", "KtpLevel")`),
  tìm "hàng xóm" trong bán kính Chebyshev = 1 **theo THỨ HẠNG thực tế của các
  mức đã quét** (không phải theo số thứ tự enum thô) — comment dòng 76-79 giải
  thích đúng lý do: nếu lưới quét thưa (cách mức, không liên tục theo enum),
  dùng số enum thô sẽ khiến không ô nào có hàng xóm. Đây là một xử lý kỹ hơn
  mức "định tính" của Pardo gốc — cần thiết vì Combo/MA Cross chỉ quét vài mức
  Fib rời rạc, không phải lưới liên tục.
- Yêu cầu tối thiểu `WF_PLATEAU_MIN_NEIGHBORS = 4` hàng xóm hợp lệ mới chấm
  điểm — loại các ô ở rìa lưới có ít hàng xóm để tránh đánh giá sai.
  `WF_PLATEAU_PERCENTILE = 0.25` — chấm điểm ô bằng giá trị **percentile thứ
  25 (KÉM nhất trong 25% dưới)** của các hàng xóm, KHÔNG phải trung bình hay
  max. Đây là lựa chọn **bi quan có chủ đích**: chọn tham số mà ngay cả kịch
  bản xấu trong vùng lân cận cũng vẫn ổn, thay vì chọn theo hàng xóm tốt nhất
  — hợp lý hơn ý tưởng gốc của Pardo (vốn chỉ mô tả định tính), vì nó định
  lượng hoá đúng thứ Pardo muốn tránh (đỉnh cô lập ăn may).
- Có sẵn `"best"` (chọn theo `rank()`, tức đỉnh thô) làm **đối chứng A/B**
  ngay trong `WF_RULES = ("plateau", "best")` — cho phép đo trực tiếp
  "plateau có thực sự tốt hơn best không" trên dữ liệu thật, đúng tinh thần
  khoa học (không chỉ tin lý thuyết).

**Chi phí/giá trị**: đã triển khai, khớp tốt với khuyến nghị chuẩn, còn cẩn
thận hơn ở khâu xử lý lưới thưa. Không cần làm gì thêm ngay — có thể tinh
chỉnh `percentile`/`min_neighbors` sau khi có đủ dữ liệu thật để so sánh
"plateau" vs "best" trên OOS.

---

## 4. Monte Carlo block bootstrap cho chuỗi lệnh/equity

**Cơ chế chung**: return tài chính có tự tương quan chuỗi thời gian
(autocorrelation, volatility clustering — bằng chứng thực nghiệm rộng rãi
trong tài chính định lượng) → bootstrap i.i.d. (xáo từng quan sát độc lập) phá
vỡ cấu trúc phụ thuộc này, làm sai lệch phương sai ước lượng (thường làm hẹp
giả tạo khoảng tin cậy). Họ phương pháp **block bootstrap** giữ nguyên từng
khối quan sát liên tiếp khi resample để bảo toàn tương quan cục bộ:
- **Non-overlapping block bootstrap** (Carlstein, 1986): chia chuỗi thành các
  khối liên tiếp, KHÔNG chồng lấn, độ dài cố định; resample bằng cách chọn
  ngẫu nhiên (có hoàn lại) trong các khối đó rồi nối lại.
- **Moving/overlapping block bootstrap** (Künsch, 1989; Liu & Singh, 1992):
  khối có thể bắt đầu ở BẤT KỲ vị trí nào trong chuỗi gốc (chồng lấn nhau) —
  tận dụng hết thông tin, không "lãng phí" vùng biên giữa 2 khối cố định.
- **Stationary bootstrap** (Politis & Romano, 1994): độ dài khối NGẪU NHIÊN
  theo phân phối hình học (kỳ vọng = 1/p) thay vì cố định — đảm bảo chuỗi
  resample có tính dừng (stationary), thường được khuyến nghị làm mặc định khi
  cần khoảng tin cậy cho thống kê của chuỗi phụ thuộc thời gian.

**Đối chiếu với code thật** — `montecarlo.py` (`run()`, dòng 22-64):
- Đầu vào là **return theo NGÀY** suy từ equity points (`_daily_returns()`),
  không phải xáo từng LỆNH — lựa chọn hợp lý hơn xáo lệnh nếu 1 ngày có thể có
  nhiều lệnh hoặc lệnh giữ qua đêm, vì gộp về đơn vị ngày tự động giữ đúng thứ
  tự các lệnh xảy ra trong cùng 1 ngày.
- Cách chia khối: `blocks = [days[i:i+block_days] for i in range(0, len(days), block_days)]`
  — đây là **non-overlapping block bootstrap kiểu Carlstein (1986)** với độ
  dài khối CỐ ĐỊNH (`block_days=5`, tương ứng 1 tuần giao dịch), KHÔNG PHẢI
  moving/overlapping (Künsch) và KHÔNG PHẢI stationary bootstrap
  (Politis-Romano) như tài liệu thường khuyến nghị làm mặc định. Đây là biến
  thể ĐƠN GIẢN NHẤT trong họ block-bootstrap.
- Resample bằng `rng.choice(blocks)` lặp lại tới khi đủ độ dài — đúng cơ chế
  "concatenate random blocks with replacement" chuẩn của block bootstrap.
- Đầu ra: percentile p50/p95/p99 của max drawdown qua các path mô phỏng, và
  xác suất vi phạm daily loss limit/total loss limit FTMO
  (`p_daily_breach`/`p_total_breach`) — đây là ứng dụng thực tế trực tiếp, trả
  lời đúng câu hỏi "tổ hợp tham số này có thực sự an toàn cho luật FTMO không,
  hay việc chưa breach trong 1 lần backtest lịch sử chỉ là may mắn về THỨ TỰ
  lệnh".

**Nhận xét kỹ thuật (không phải lỗi, là điểm có thể nâng cấp)**:
1. Non-overlapping block cố định có 2 nhược điểm lý thuyết nhỏ so với
   moving/stationary bootstrap: (a) lãng phí thông tin ở đúng ranh giới giữa 2
   khối 5-ngày cố định (không bao giờ lấy mẫu 1 khối "thứ Tư→thứ Ba tuần sau"),
   (b) độ dài khối cố định = 5 không được chọn theo phương pháp thống kê nào
   (vd quy tắc chọn độ dài khối tối ưu của Politis & White, 2004) mà là số mặc
   định hợp lý theo trực giác (1 tuần giao dịch).
2. **[2026-09-22, ĐÍNH CHÍNH bằng số đo thật]** Nhận định gốc ở đây từng viết
   "sai số này thường không đủ lớn để đổi kết luận thực hành" — SAI với mẫu
   NHỎ. Đã code thêm `moving`/`stationary` làm song song với bản cũ
   (`run_methods_compare()`) và đo trên đúng 69 ngày US30/H1/Combo Q1-2026:
   `p_total_breach` tăng từ 2.46% (non_overlapping) lên 4.12% (moving) —
   **gần gấp đôi**, đủ lớn để đổi kết luận nếu ngưỡng chấp nhận rủi ro nằm
   giữa khoảng đó. Chi tiết đầy đủ + bảng số +
   nguồn: `montecarlo-block-bootstrap-variants.md`.
3. **[2026-09-22, người dùng lưu ý] "5 ngày = 1 tuần giao dịch" giả định thị
   trường có nghỉ cuối tuần (US30/HK50/JP225/GER40...) — với BTCUSD giao dịch
   **24/7, không có nghỉ cuối tuần**, cơ sở trực giác đó không còn đúng (không
   có "ranh giới tuần" tự nhiên nào để 5 ngày liên tiếp bám theo). `block_days`
   đã là 1 field của `MonteCarloConfig` (không hardcode) nên đổi được ngay,
   không cần sửa code — chỉ cần truyền `block_days` khác (vd 7, hoặc số khác
   tuỳ có muốn giữ ý nghĩa "tuần" hay không) khi chạy Monte Carlo cho BTCUSD,
   KHÔNG dùng mặc định 5 như các symbol có nghỉ cuối tuần. Vẫn CHƯA có 1 quy
   tắc thống kê để chọn số tối ưu cho trường hợp 24/7 này (đồng dạng nhược
   điểm (b) ở mục 1) — nếu cần chặt hơn, đây đúng là chỗ Politis & White (2004)
   sẽ giúp.

**Chi phí/giá trị cho quy mô hệ thống này**: đã triển khai, đúng HƯỚNG LỚN
(block thay vì xáo từng ngày/lệnh — đúng cảnh báo "không phải kỹ thuật nào
cũng dùng nguyên bản cho time series" mà CLAUDE.md tự ghi). Nâng cấp lên
stationary bootstrap là thay đổi NHỎ (vài chục dòng, đổi cách chia `blocks`
sang độ dài ngẫu nhiên hình học) nhưng lợi ích tăng thêm khiêm tốn cho mục
đích hiện tại — xếp vào nhóm "cải tiến khi rảnh", không cấp thiết.

---

## 5. Deflated Sharpe Ratio (DSR)

**Cơ chế**: Sharpe Ratio (SR) quan sát được sau khi đã thử NHIỀU tổ hợp tham
số (multiple testing/selection bias) bị thổi phồng một cách có hệ thống — kể
cả khi TẤT CẢ tổ hợp đều thuần nhiễu (skill thật = 0), SR lớn nhất trong N tổ
hợp độc lập vẫn tăng theo **log(N)** một cách kỳ vọng. Bailey & López de Prado
đưa ra công thức xấp xỉ kỳ vọng SR lớn nhất trong N thử nghiệm độc lập (dùng
hằng số Euler-Mascheroni γ≈0.5772 và hàm phân phối chuẩn tắc) — một ví dụ cụ
thể được trích dẫn rộng rãi: giả sử SR thật của mọi tổ hợp = 0, sau ~1.000 thử
nghiệm độc lập, kỳ vọng SR lớn nhất quan sát được đã lên tới **~3.26** dù
không hề có skill thật. DSR = xác suất SR thật > 0 SAU KHI đã trừ hao mức
"nhiễu kỳ vọng" đó, còn hiệu chỉnh thêm cho return không chuẩn (skewness/
kurtosis) qua phân bố Sharpe ratio phi chuẩn.

**Chưa có trong code** — không tìm thấy DSR ở bất kỳ đâu trong `facilitator.py`,
`montecarlo.py`, hay `selection.py`.

**Đầu vào cần có** (đều đã có sẵn từ kết quả optimize cũ, không cần chạy lại
backtest): số trial N thực tế đã chạy trong 1 lưới optimize (đếm số dòng
`store.rows()`), phương sai của SR/profit-factor/net-profit across trials
(tính trực tiếp từ bảng kết quả), độ dài mẫu T (số ngày/số lệnh của run được
chọn), và skewness/kurtosis của chuỗi return (tính từ `events.json`/equity
points đã lưu).

**Chi phí/giá trị cho quy mô hệ thống này**: **THẤP/RẺ** — thuần post-processing
trên dữ liệu ĐÃ CÓ SẴN (không tốn thêm 1 lần backtest nào), chỉ cần code công
thức (vài chục dòng Python, `numpy`/`scipy.stats`). Giá trị CAO — trả lời trực
tiếp câu hỏi cốt lõi của toàn bộ mục đích tài liệu này: "chọn tổ hợp tốt nhất
từ vài nghìn tổ hợp KSL×KTP có phải chỉ là trúng nhiễu do thử quá nhiều lần
không?" — bằng 1 con số duy nhất, dễ báo cáo, được cộng đồng/nền tảng quant
thương mại công nhận rộng rãi.

---

## 6. Probability of Backtest Overfitting / CSCV

**Cơ chế**: Bailey, Borwein, López de Prado, Zhu đề xuất **Combinatorially
Symmetric Cross-Validation (CSCV)** — chia bảng kết quả (N trial × hiệu suất
theo thời gian) thành nhiều cách cắt đối xứng thành 2 nửa IS/OOS, với MỖI cách
cắt: chọn "winner" theo IS (đúng luật chọn đang dùng, vd theo net_profit hay
theo plateau), rồi xem winner đó xếp hạng ở đâu trên OOS (trên hay dưới trung
vị của toàn bộ trial trên OOS). **PBO** = tỉ lệ % số lần "winner chọn theo IS"
lại rơi xuống DƯỚI trung vị OOS — PBO cao (theo hướng dẫn gốc, thường lấy mốc
so sánh với 50%) nghĩa là chính QUY TRÌNH chọn tham số của bạn có xu hướng
chọn nhiễu, không phải bằng chứng về 1 tổ hợp cụ thể.

**Chưa có trong code**, nhưng **hạ tầng dữ liệu để tính gần như đã có sẵn**:
`run_walkforward()` đã tách rõ ràng IS (`train`) và OOS (`test`) theo từng cửa
sổ thời gian, và `walkforward_table()` đã ghép sẵn kết quả IS/OOS theo hàng.
PBO/CSCV gốc dùng cách cắt ĐỐI XỨNG ngẫu nhiên (không nhất thiết theo trục thời
gian) trong khi walk-forward hiện tại cắt theo trục thời gian cố định (rolling
window) — 2 cách cắt khác nhau nhưng cùng mục đích, và có thể tái dùng gần hết
pipeline dữ liệu hiện có (bảng `runs` theo `(symbol, timeframe, window)`) để
implement 1 biến thể CSCV mà không cần chạy thêm backtest mới, chỉ cần thêm 1
hàm tính toán hậu kỳ trong `output_process/`.

**Chi phí/giá trị**: THẤP-TRUNG BÌNH (cao hơn DSR một chút vì cần logic cắt
đối xứng + xử lý nhiều cách chia, nhưng vẫn thuần post-processing, không backtest
thêm). Giá trị CAO, bổ sung tốt cho DSR (DSR nhìn 1 con số SR lớn nhất; PBO
nhìn cả QUY TRÌNH chọn tham số có hệ thống hay không) — 2 kỹ thuật này thường
được trình bày đi cùng nhau trong tài liệu gốc.

---

## 7. Combinatorial Purged Cross-Validation (CPCV)

**Cơ chế**: chia dữ liệu thành N nhóm liên tiếp không chồng lấn theo thời
gian; sinh MỌI tổ hợp chọn k nhóm làm test (`C(N,k)` tổ hợp, phần còn lại N-k
nhóm làm train), với 2 cơ chế chống rò rỉ:
- **Purging**: loại khỏi tập train mọi quan sát có "nhãn"/kết quả phụ thuộc
  vào một khoảng thời gian chồng lấn với tập test (trong bài toán ML gốc:
  nhãn triple-barrier của 1 quan sát có thể kéo dài nhiều ngày sau thời điểm
  quan sát).
- **Embargo**: thêm khoảng đệm thời gian ngay sau tập test trước khi được
  phép dùng lại cho train, để chặn rò rỉ do tự tương quan chuỗi giữa 2 đoạn
  liền kề.

Kết quả là NHIỀU "đường" (path) backtest OOS khác nhau từ CÙNG 1 bộ dữ liệu →
một PHÂN PHỐI hiệu suất OOS thay vì 1 con số duy nhất từ 1 lần chạy walk-forward
tuần tự → cho phép suy luận thống kê chặt hơn (tính PBO/DSR trực tiếp trên
phân phối path này).

**Rào cản kỹ thuật THẬT cho hệ thống này** (không chỉ là "nhiều việc hơn"):
1. **Khái niệm "nhãn"/"purging" của AFML được thiết kế cho 1 MÔ HÌNH ML** dự
   báo từng quan sát (label = kết quả trong tương lai gần của quan sát đó).
   Combo/MA Cross KHÔNG phải mô hình ML — chúng là rule cố định chạy y hệt tại
   mọi thời điểm. Khái niệm gần nhất cần "dịch" sang là: 1 LỆNH mở tại thời
   điểm gần biên giữa 2 nhóm N có thể có SL/TP chạm ở một thời điểm nằm trong
   nhóm KHÁC — đây là leakage NHẸ HƠN nhiều so với leakage feature của ML,
   nhưng vẫn cần xử lý đúng nếu muốn làm CPCV nghiêm túc, không có sẵn công
   thức "chuẩn" cho trường hợp rule-based backtest như thế này trong tài liệu
   gốc.
2. **`ctrader-cli`/cTrader Automate chỉ nhận backtest trên 1 khoảng
   `[start, end]` LIÊN TỤC** — không hỗ trợ chạy trên dữ liệu có "lỗ hổng"
   thời gian (train = hợp của k nhóm rời rạc, bỏ qua nhóm test nằm giữa). Để
   làm đúng CPCV, phải tự cắt N-k đoạn liên tục, chạy N-k lần backtest riêng
   biệt trên từng đoạn, rồi TỰ GHÉP kết quả (P&L, equity) lại bằng tay ở tầng
   Python — nghĩa là cần thiết kế MỚI hoàn toàn 1 tầng "ghép nhiều run rời rạc
   thành 1 path liên tục" trong `output_process/`, không tái dùng được luồng
   "1 spec = 1 lần gọi CLI" hiện tại.
3. **Chi phí compute nhân theo số path**: ví dụ N=10 nhóm, k=2 nhóm test →
   `C(10,2) = 45` path — mỗi path lại cần quét TOÀN BỘ lưới tham số (KSL×KTP,
   hiện đã hàng trăm-nghìn tổ hợp) trên phần train của path đó → tổng số lần
   backtest tăng gấp ~45 lần so với 1 lượt walk-forward hiện tại, trên hạ tầng
   1 máy Windows đơn lẻ (trần song song đã đặt cứng `MAX_PARALLEL_CAP=16` ở
   `facilitator.py` dòng 38) — không khả thi trong ngắn hạn về thời gian chạy.

**Chi phí/giá trị cho quy mô hệ thống này**: giá trị LÝ THUYẾT cao nhất trong
các kỹ thuật được khảo sát (nhiều bài so sánh thực nghiệm cho thấy CPCV có PBO
thấp hơn/DSR cao hơn walk-forward truyền thống — xem mục nguồn), nhưng CHI PHÍ
KỸ THUẬT VÀ COMPUTE RẤT CAO cho đúng quy mô "1 người dùng, 2 bot đơn giản, máy
đơn lẻ" — nên XẾP SAU walk-forward/DSR/PBO/permutation test, chỉ cân nhắc nếu
các kỹ thuật rẻ hơn đã làm hết mà vẫn còn nghi ngờ overfit dai dẳng cần thêm
bằng chứng mạnh hơn.

---

## 8. White's Reality Check / Hansen's SPA test

**Cơ chế**: kiểm định thống kê hình thức (dùng bootstrap) cho câu hỏi "hiệu
suất TỐT NHẤT trong K chiến lược/tổ hợp đã thử có thực sự vượt trội hơn 1
benchmark (vd return=0, hay buy-and-hold) một cách có ý nghĩa thống kê, SAU
KHI đã tính đến việc đã thử K lần (data snooping)?".
- **White's Reality Check** (White, 2000, *Econometrica*): bootstrap trên
  phân phối joint của K chênh lệch hiệu suất so với benchmark, lấy giá trị lớn
  nhất, dựng phân phối null bằng resampling — nếu hiệu suất tốt nhất quan sát
  được vượt phần lớn phân phối null này thì có bằng chứng thống kê.
- **Hansen's SPA test** (Hansen, 2005, *Journal of Business & Economic
  Statistics*): cải tiến Reality Check bằng cách chuẩn hoá (studentize) theo
  phương sai riêng của từng chiến lược và loại bớt các chiến lược "rõ ràng
  kém" khỏi cấu hình bất lợi nhất (least favourable configuration) → mạnh hơn
  (power cao hơn), ít bị các chiến lược nhiễu/tệ pha loãng kết quả so với RC.

Cả 2 đã được áp dụng thực nghiệm rộng rãi để tái kiểm định "lợi nhuận" của các
quy tắc phân tích kỹ thuật cổ điển (nhiều bài academic re-test hàng trăm-nghìn
technical trading rule trên chỉ số/FX — xem mục nguồn).

**Đối chiếu bối cảnh**: đây là kỹ thuật giải đúng LOẠI câu hỏi mà DSR/PBO cũng
giải (chống multiple-testing/data-snooping bias khi đã thử nhiều tổ hợp), và
NẶNG HƠN về triển khai — cần tự code bootstrap toàn bộ phân phối joint của
chênh lệch hiệu suất, bên dưới lại cần đúng 1 lớp block/stationary bootstrap
(thường dùng Politis-Romano) để tôn trọng tự tương quan chuỗi thời gian → chồng
2-3 lớp kỹ thuật lên nhau. Với quy mô "2 bot, mỗi bot chỉ 3-4 tham số" (không
phải kiểm định hàng nghìn quy tắc kỹ thuật khác nhau như trong các bài academic
gốc dùng RC/SPA), giá trị gia tăng SO VỚI DSR/PBO đã có là KHÔNG NHIỀU — DSR/PBO
trả lời gần như cùng câu hỏi với chi phí thấp hơn hẳn.

**Chi phí/giá trị**: biết tên, hiểu đúng cơ chế để không nhầm khi đọc tài liệu
khác — nhưng **không đáng tự code riêng** ở quy mô này khi đã có DSR/PBO.

---

## 9. Monte Carlo Permutation Test (Aronson / Masters)

**Cơ chế**: khác hẳn Monte Carlo bootstrap ở mục 4 (bootstrap trên CHUỖI LỆNH/
RETURN ĐÃ SINH RA để đo phân phối RỦI RO — DD, breach), permutation test đặt
câu hỏi khác: **"quy tắc chiến lược có thực sự có sức dự báo, hay chỉ đang
khớp nhiễu trên đúng bộ dữ liệu giá này?"**. Cách làm: hoán vị (permute) dữ
liệu GIÁ/RETURN gốc để tạo ra nhiều bản dữ liệu "không còn cấu trúc dự báo thật
nhưng vẫn giữ phân phối thống kê tương tự" (giả thuyết H0: quy tắc vô dụng),
rồi chạy LẠI ĐÚNG quy tắc đó trên từng bản hoán vị → dựng phân phối hiệu suất
"thuần may rủi" → so sánh hiệu suất thật với phân phối này để lấy p-value.
David Aronson hệ thống hoá kỹ thuật này trong *Evidence-Based Technical
Analysis* (2006, Wiley) như một trong các công cụ chính chống lại "data mining
bias" (kết quả đẹp chỉ vì đã thử/khớp quá nhiều). Timothy Masters
(*Permutation and Randomization Tests for Trading System Development*, ấn bản
gần nhất 2020) mô tả chi tiết thuật toán + code C++ cho cùng nhóm kỹ thuật,
nhấn mạnh: phần lớn kết quả backtest báo cáo trong cộng đồng "vô nghĩa về mặt
thống kê" vì chưa từng so sánh với baseline "may rủi trên dữ liệu tương tự".

**Điểm cần cẩn trọng riêng cho hệ thống này** (không có trong tài liệu gốc,
đây là suy luận áp dụng vào kiến trúc thật của repo): tín hiệu entry của
Combo/MA Cross **không tính trực tiếp từ giá trong cBot** mà đọc từ **CSV do
`core_python` sinh sẵn trên VM-OG8** (theo `bo-workflow-backtest-pipeline.md`
và cấu trúc `SignalFilePath`). Muốn làm permutation test ĐÚNG CHUẨN Aronson/
Masters (hoán vị GIÁ GỐC rồi tính lại tín hiệu từ giá đã hoán vị), phải phối
hợp với hệ `core_python` bên VM-OG8 để regenerate lại CSV tín hiệu trên dữ
liệu giá đã permute — đây là việc CROSS-SYSTEM (Python trên 2 máy), không tự
làm gọn trong 1 cBot hay 1 script `bo_workflow/` được. Một biến thể RẺ HƠN
nhưng YẾU HƠN: chỉ hoán vị THỨ TỰ các dòng tín hiệu trong CSV có sẵn (giữ
nguyên từng tín hiệu, xáo timestamp gắn liền) — cách này rẻ (thuần local, tái
dùng ngay hạ tầng backtest hiện tại) nhưng đổi hẳn ý nghĩa: chỉ kiểm định
"THỜI ĐIỂM vào lệnh so với giá có ý nghĩa không", không kiểm định được toàn bộ
"quy tắc sinh tín hiệu có dự báo thật không" như bản gốc.

**Chi phí/giá trị cho quy mô hệ thống này**:
- Compute: RẺ HƠN walk-forward — permutation test chạy N lần backtest (N ~
  100-1.000 theo khuyến nghị Masters) với ĐÚNG 1 bộ tham số đã chọn, KHÔNG
  nhân với lưới tham số KSL×KTP như optimize thông thường.
- Engineering: TRUNG BÌNH nếu làm bản đúng chuẩn (cần phối hợp VM-OG8 để
  regenerate signal từ giá hoán vị), THẤP nếu chấp nhận bản rút gọn (hoán vị
  thứ tự tín hiệu, yếu hơn về ý nghĩa thống kê).
- Giá trị: CAO và trả lời đúng câu hỏi mà KHÔNG kỹ thuật nào ở trên trả lời
  trực tiếp — DSR/PBO chỉ so sánh CÁC TRIAL VỚI NHAU (không so với "hoàn toàn
  không có edge"); walk-forward chỉ chứng minh tham số ỔN ĐỊNH qua thời gian
  nhưng không loại trừ khả năng CẢ CHIẾN LƯỢC lẫn cách chọn tham số đều đang
  khớp đúng nhiễu đặc thù của bộ dữ liệu/signal CSV này.

---

## 10. Multi-symbol cross-check

Không phải 1 kỹ thuật academic riêng biệt có tên/công thức thống kê chính thức
— là thực hành phổ biến trong cộng đồng quant retail/system trading (kiểm
chứng "out-of-universe": tham số thắng trên 1 symbol có còn hoạt động hợp lý
khi backtest — KHÔNG optimize lại — trên symbol khác không). CLAUDE.md đã bàn
đúng và đầy đủ mục này (mục "Các hướng đã bàn" #2 + cảnh báo về correlation
giữa các symbol làm giảm tính độc lập của bằng chứng) — tài liệu này XÁC NHẬN
LẠI đánh giá đó là hợp lý, không có căn cứ học thuật nào tốt hơn để bổ sung.

**Đối chiếu kiến trúc**: `core_engine` đã hỗ trợ sẵn multi-symbol ở tầng dữ
liệu (`by_window` trong `run_walkforward()` khoá theo `(symbol, timeframe,
window)`, `_signal_path()` tra CSV theo từng `(symbol, timeframe)` riêng) —
nghĩa là chạy cùng 1 experiment trên nhiều symbol đã là chuyện "đổi config",
không cần code thêm gì. Cái còn thiếu chỉ là 1 bước TỔNG HỢP/SO SÁNH chéo
symbol chuyên dụng (hiện `screen()`/`rank()` xếp hạng trong phạm vi 1
`experiment`, chưa có hàm "so tham số X có ổn định qua N symbol không").

**Chi phí/giá trị**: RẺ (hạ tầng đã sẵn sàng, chỉ thiếu 1 hàm tổng hợp hậu kỳ
nhỏ) — nên làm SỚM, song song với DSR/PBO.

---

## 11. Bảng so sánh tổng hợp

| # | Kỹ thuật | Nguồn chính | Trạng thái trong code | Chi phí compute | Chi phí engineering | Giá trị chống overfit (bối cảnh này) |
|---|---|---|---|---|---|---|
| 1 | Walk-Forward (rolling, WFE) | Pardo 1992/2008 | ✅ Đã có, khớp chuẩn | Đã trả (chạy 1 lần) | Không cần thêm (chỉ nên thêm cột WFE derived) | Cao — nền tảng chính |
| 2 | Plateau/sensitivity | Pardo (định tính) | ✅ Đã có, hình thức hoá tốt hơn bản gốc | Đã trả (post-processing) | Không cần thêm | Cao |
| 3 | Block-bootstrap Monte Carlo | Carlstein 1986 (bản đang dùng); Künsch 1989, Politis-Romano 1994 (bản nâng cao hơn) | ✅ Đã có (bản đơn giản nhất họ) | Rẻ (post-processing) | Thấp nếu nâng cấp lên stationary bootstrap | Cao cho mục đích rủi ro FTMO; trung bình nếu cần CI chặt |
| 4 | Deflated Sharpe Ratio | Bailey & López de Prado 2014 | ❌ Chưa có | Gần như 0 (dữ liệu đã có) | Thấp | Cao — trực tiếp trả lời câu hỏi cốt lõi |
| 5 | PBO / CSCV | Bailey, Borwein, López de Prado, Zhu | ❌ Chưa có | Gần như 0 (tái dùng dữ liệu walk-forward) | Thấp-trung bình | Cao, bổ sung tốt cho DSR |
| 6 | Multi-symbol cross-check | Thực hành cộng đồng | ⚠️ Hạ tầng có sẵn, chưa có runner tổng hợp | Trung bình (chạy thêm backtest trên symbol khác) | Thấp | Trung bình (correlation giữa symbol làm giảm độc lập bằng chứng) |
| 7 | Monte Carlo Permutation Test | Aronson 2006; Masters 2006/2020 | ❌ Chưa có | Rẻ (N lần backtest, không nhân lưới) | Trung bình (bản chuẩn cần phối hợp VM-OG8); thấp (bản rút gọn, yếu hơn) | Cao — câu hỏi khác biệt, chưa kỹ thuật nào ở trên trả lời |
| 8 | CPCV | López de Prado 2018 (AFML) | ❌ Chưa có | Rất cao (nhân theo `C(N,k)` path × lưới tham số) | Rất cao (ctrader-cli không backtest đoạn rời rạc, cần tầng ghép path mới) | Cao về lý thuyết, nhưng không tương xứng chi phí ở quy mô này |
| 9 | White's RC / Hansen's SPA | White 2000; Hansen 2005 | ❌ Chưa có | Trung bình-cao (bootstrap joint distribution) | Cao (chồng nhiều lớp kỹ thuật) | Thấp GIA TĂNG (DSR/PBO đã che phủ phần lớn giá trị, rẻ hơn) |

---

## 12. Xếp hạng ưu tiên triển khai tiếp theo

Thứ tự dưới đây tính trên đúng tiêu chí "đã có nền walk-forward + Monte Carlo
block-bootstrap + plateau selection — hướng nào đáng đầu tư TIẾP THEO nhất":

1. **Deflated Sharpe Ratio + PBO/CSCV** — chi phí thấp nhất trong mọi kỹ thuật
   CHƯA CÓ (thuần post-processing, dùng ngay dữ liệu optimize/walk-forward đã
   chạy trước đây, không tốn thêm 1 lần backtest), giá trị cao nhất vì trả lời
   trực tiếp đúng câu hỏi "chọn tổ hợp tốt nhất từ hàng nghìn tổ hợp có phải
   chỉ trúng nhiễu không". Nên làm cùng lúc vì 2 kỹ thuật bổ trợ nhau và dùng
   chung hạ tầng dữ liệu.
2. **Monte Carlo Permutation Test (Aronson/Masters)** — chi phí compute rẻ
   hơn cả walk-forward (không nhân lưới tham số), trả lời câu hỏi khác biệt
   quan trọng ("chiến lược có edge thật không") mà nhóm kỹ thuật (1) chưa chạm
   tới. Cân nhắc làm bản rút gọn (hoán vị thứ tự tín hiệu, tại chỗ) trước, chỉ
   đầu tư bản chuẩn (phối hợp VM-OG8 hoán vị giá gốc) nếu bản rút gọn cho tín
   hiệu đáng ngờ.
3. **Multi-symbol cross-check** — hạ tầng gần như đã sẵn sàng
   (`(symbol, timeframe, window)` đã là khoá chính trong toàn bộ pipeline),
   chỉ thiếu 1 hàm tổng hợp so sánh chéo — chi phí thấp, nên làm song song với
   (1)/(2) khi có kết quả walk-forward mới cần kiểm chứng thêm.
4. **Cải tiến nhỏ trên nền đã có**: thêm cột WFE tường minh vào
   `walkforward_table()`; cân nhắc nâng `montecarlo.py` từ non-overlapping
   block bootstrap (Carlstein) lên stationary bootstrap (Politis-Romano) —
   cả 2 đều rẻ, không cấp thiết, làm khi rảnh tay giữa các đợt research khác.
5. **CPCV** — giá trị lý thuyết cao nhất nhưng rào cản kỹ thuật thật (không
   backtest được đoạn thời gian rời rạc qua `ctrader-cli`) + chi phí compute
   nhân theo tổ hợp path trên hạ tầng 1 máy đơn lẻ → HOÃN, chỉ quay lại nếu
   (1)-(4) đã làm hết mà vẫn còn nghi ngờ overfit dai dẳng cần bằng chứng mạnh
   hơn nữa.
6. **White's Reality Check / Hansen's SPA** — biết tên, hiểu cơ chế để tham
   chiếu khi đọc thêm tài liệu, nhưng KHÔNG cần tự code riêng — (1) đã che phủ
   phần lớn giá trị của nhóm kỹ thuật này với chi phí thấp hơn nhiều.

---

## 13. Nguồn tham khảo

- Robert E. Pardo, *Design, Testing, and Optimization of Trading Systems*
  (1992), *The Evaluation and Optimization of Trading Strategies* (tái bản mở
  rộng, 2008) — nguồn gốc Walk-Forward Analysis, Walk-Forward Efficiency,
  khuyến nghị tỉ lệ IS:OOS 4-6x, rolling vs anchored. (Tổng hợp qua các nguồn
  thứ cấp trích dẫn trực tiếp Pardo: [Better System Trader — phỏng vấn Robert
  Pardo](https://bettersystemtrader.com/060-strategy-optimization-with-robert-pardo/),
  [Hispatrading — Robust Walk Forward Optimization](https://hispatrading.es/robust-walk-forward-optimization-rwfo/),
  [Wikipedia — Walk forward optimization](https://en.wikipedia.org/wiki/Walk_forward_optimization).)
- Marcos López de Prado, *Advances in Financial Machine Learning* (Wiley,
  2018) — Purged Cross-Validation, Embargo, Combinatorial Purged
  Cross-Validation (CPCV), công thức số path `C(N,k)`.
  ([QuantInsti — Cross-Validation in Finance: Purging, Embargo,
  Combinatorial](https://blog.quantinsti.com/cross-validation-embargo-purging-combinatorial/),
  [Quantoisseur — Combinatorial Purged Cross-Validation
  Explained](https://quantoisseur.com/2019/11/05/combinatorial-purged-cross-validation-explained/).)
- David H. Bailey, Marcos López de Prado, "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting, and Non-Normality"
  (SSRN 2460551; *Journal of Portfolio Management*, 2014).
  ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551),
  [davidhbailey.com — bản PDF đầy đủ](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).)
- David H. Bailey, Jonathan Borwein, Marcos López de Prado, Qiji Jim Zhu,
  "The Probability of Backtest Overfitting" (SSRN 2326253; *Journal of
  Computational Finance*) — CSCV, định nghĩa PBO.
  ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253),
  [davidhbailey.com — bản PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).)
- Dimitris N. Politis, Joseph P. Romano, "The Stationary Bootstrap" (*Journal
  of the American Statistical Association*, 1994) — stationary bootstrap, độ
  dài khối ngẫu nhiên hình học.
  ([MetricGate — Stationary Bootstrap (Politis-Romano)
  Calculator](https://metricgate.com/docs/stationary-bootstrap-politis-romano/).)
- Hans R. Künsch, "The Jackknife and the Bootstrap for General Stationary
  Observations" (*Annals of Statistics*, 1989) — moving/overlapping block
  bootstrap. Edward Carlstein (1986) — non-overlapping block bootstrap (bản
  gần nhất với cách `montecarlo.py` hiện đang implement).
- Halbert White, "A Reality Check for Data Snooping" (*Econometrica* 68(5),
  2000).
- Peter Reinhard Hansen, "A Test for Superior Predictive Ability" (*Journal of
  Business & Economic Statistics* 23(4), 2005; SSRN 264569).
  ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=264569).)
  Ứng dụng thực nghiệm trên technical trading rules: "Re-Examining the
  Profitability of Technical Analysis with White's Reality Check and Hansen's
  SPA Test"
  ([ResearchGate](https://www.researchgate.net/publication/256066609_Re-Examining_the_Profitability_of_Technical_Analysis_with_White's_Reality_Check_and_Hansen's_SPA_Test)).
- David Aronson, *Evidence-Based Technical Analysis: Applying the Scientific
  Method and Statistical Inference to Trading Signals* (Wiley, 2006) — Monte
  Carlo Permutation method, data-mining bias, các phương pháp giảm thiểu (OOS
  testing, randomization/bootstrap, data-mining correction factor).
  ([CXO Advisory — tổng hợp theo chương](https://www.cxoadvisory.com/technical-trading/evidence-based-technical-analysis-applying-the-scientific-method-and-statistical-inference-to-trading-signals-chapter-by-chapter-review/).)
- Timothy Masters, *Permutation and Randomization Tests for Trading System
  Development: Algorithms in C++* (ấn bản 2020) — thuật toán chi tiết cho
  Monte Carlo permutation test, kiểm định overfitting ở giai đoạn sớm nhất,
  giới hạn drawdown tương lai.
  ([Amazon — trang sách](https://www.amazon.com/Permutation-Randomization-Trading-System-Development/dp/B084QLXFKW),
  [timothymasters.info](http://www.timothymasters.info/market-trading.html).)

### File code thật đã đọc trong repo (đối chiếu, không phải nguồn học thuật)

- `bo_workflow/core_engine/facilitator.py` — `run_walkforward()` (dòng
  97-192), hằng số `WF_MIN_TRADES`/`WF_DIMENSIONS`/`WF_PLATEAU_MIN_NEIGHBORS`/
  `WF_PLATEAU_PERCENTILE`/`WF_RULES` (dòng 55-59), `run_montecarlo()` (dòng
  237-249), `walkforward_table()` (dòng 195-229).
- `bo_workflow/core_engine/planner.py` — `walkforward_windows()` (dòng
  138-165): rolling window, `is_months=12`/`oos_months=3`/`step_months=oos_months`
  mặc định.
- `bo_workflow/core_engine/opt_util/montecarlo.py` — `run()` (dòng 22-64),
  `_daily_returns()` (dòng 67-80): block bootstrap non-overlapping trên return
  ngày, `block_days=5` mặc định, ngưỡng FTMO `daily_loss_pct=5.0`/
  `max_loss_pct=10.0` mặc định.
- `bo_workflow/core_engine/output_process/selection.py` — `plateau()` (dòng
  59-100), `rank()` (dòng 36-56), `eligible()` (dòng 21-33).
