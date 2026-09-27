---
title: DSR + PBO/CSCV — công thức xác nhận từ nguồn gốc, thiết kế module, verify bằng dữ liệu thật
date: 2026-09-22
status: đã code (`optimize/dsr.py`, `optimize/pbo.py`) + wire vào `readout.run_dsr()`/`readout.run_pbo()`, verify bằng dữ liệu walk-forward THẬT (US30/H1 wf01_is, 9 trial × 69 ngày)
---

# Bối cảnh

Roadmap 2026-09-22 (chốt trước đó) xác định DSR + PBO/CSCV là 2 module tiếp
theo cần code trong `optimize/`. Người dùng yêu cầu rõ: tuân thủ phương pháp
CHUẨN dựa trên research/evidence, không suy đoán công thức.

# 1. Xác nhận công thức — lần fetch PDF gốc đầu tiên BỊ SAI, đã phát hiện + sửa

Fetch trực tiếp PDF gốc (`davidhbailey.com/dhbpapers/deflated-sharpe.pdf`) qua
WebFetch lần đầu cho ra công thức `E[max SR] = (1-γ)Φ⁻¹(1-1/N) + γ·ln(N)` —
**thiếu hệ số `σ_SR` (độ lệch chuẩn Sharpe across trial)** và số hạng thứ 2 bị
biến dạng thành `ln(N)` thay vì `Φ⁻¹(1-1/(N·e))` — do công cụ fetch phải tóm
tắt PDF nén nhị phân (FlateDecode), không đọc trực tiếp text. **Không dùng
công thức này** — đối chiếu lại qua 2 nguồn độc lập trước khi code:
- QuantPy (Medium) — mô tả PSR đầy đủ, đúng dạng chuẩn.
- marti.ai blog "How to detect false strategies? The Deflated Sharpe Ratio"
  — **trích trực tiếp CODE THẬT** (`estimated_sharpe` đi vào mẫu số hiệu
  chỉnh skew/kurtosis, không phải benchmark) — nguồn quyết định giải quyết 1
  mâu thuẫn giữa 2 nguồn phụ khác (xem mục 2).
- ml4trading.io (docs diagnostic) — công thức khớp cấu trúc, nhưng ĐẶT SAI 1
  biến (dùng `SR₀` thay vì `ŜR` trong số hạng skew/kurtosis) — bị 2 nguồn kia
  phủ quyết.

# 2. Công thức CUỐI CÙNG dùng để code (đã cross-verify ≥2 nguồn/mỗi công thức)

```
E[max Z | N]  = (1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))     γ = 0.5772156649 (Euler-Mascheroni)
SR0           = σ_SR · E[max Z]                          σ_SR = pstdev(Sharpe của N trial)
DSR           = Φ[ (ŜR-SR0)·√(T-1) / √(1-γ̂₃·ŜR+((γ̂₄-1)/4)·ŜR²) ]
```
- `ŜR` (Sharpe QUAN SÁT của trial đang xét — thường là trial thắng) đi vào
  **CẢ tử số lẫn mẫu số** (số hạng hiệu chỉnh skew/kurtosis).
- `SR0` **CHỈ** xuất hiện ở tử số, làm ngưỡng so sánh.
- `γ̂₃`=skewness, `γ̂₄`=kurtosis **Pearson** (KHÔNG trừ 3 — phân phối chuẩn ra
  đúng 3.0, không phải excess kurtosis=0).
- `T` = độ dài mẫu (số ngày) của trial đang xét (KHÔNG phải N).

**Verify độc lập bằng số** (không chỉ tin nguồn): với `σ_SR=1` (dựng 500 giá
trị +1/500 giá trị -1 → pstdev đúng bằng 1.0), `N=1000` → code tính ra
`E[max SR] = 3.2551` — khớp con số **~3.26** đã trích dẫn rộng rãi trong
research_notes trước đó (Bailey & López de Prado). Đây là bằng chứng số học
trực tiếp công thức code đúng, không chỉ dựa vào đọc lại tài liệu.

# 3. CSCV/PBO — xác nhận qua implementation Python thật (không chỉ đọc paper)

PDF gốc PBO cũng bị lỗi rendering tương tự (binary/FlateDecode) — **thay vì
đoán, đọc thẳng source code thật** của `esvhd/pypbo` (GitHub, MIT license, cài
đặt theo đúng paper Bailey-Borwein-López de Prado-Zhu) qua WebFetch trên
raw.githubusercontent.com:

```
M: T ngày (hàng) x N trial (cột)
Chia T thành S khối liên tiếp bằng nhau (S chẵn)
Với MỌI cách chọn S/2 khối làm HỌC (liệt kê HẾT C(S,S/2), không random vài cách):
  - Ghép S/2 khối đó = tập HỌC, S/2 khối còn lại = tập KIỂM
  - Tính hiệu suất (metric_func, paper gốc dùng Sharpe, code dùng mean-return
    đơn giản hơn — paper xác nhận metric khác cũng hợp lệ) mỗi trial trên HỌC
  - winner = argmax hiệu suất HỌC (rankdata + argmax trong pypbo)
  - Xếp hạng winner đó trên tập KIỂM (rank 1..N, ascending — rankdata)
  - omega (ω) = rank / (N+1)
  - lambda (λ) = logit(ω) = ln(ω/(1-ω))
PBO = tỷ lệ % số cách chia có λ <= 0 (winner rơi xuống nửa DƯỚI trung vị KIỂM)
```

Ví dụ số thật từ pypbo README được trích: N=200, T=2000, S=16 → 12,780 tổ hợp
— khớp `math.comb(16,8)=12870` (chênh lệch nhỏ do làm tròn số liệu ví dụ gốc,
không phải sai công thức).

# 4. Thiết kế code — đúng ranh giới module đã thống nhất

- **`optimize/dsr.py`, `optimize/pbo.py`**: THUẦN hàm, 0 I/O, không biết gì về
  `store`/report ở đâu — giống hệt triết lý `montecarlo.py`. Input cùng 1
  hình dạng: `Mapping[str, Sequence[float]]` (nhãn trial → chuỗi %lời/lỗ theo
  ngày) — không dùng dataclass riêng, tránh câu hỏi "Trial nên định nghĩa ở
  file nào" (đã cân nhắc, chọn hình dạng đơn giản nhất, giống
  `montecarlo.run()` nhận thẳng `equity_points`).
- **`models.py`**: nâng `optimize/montecarlo.py::_daily_returns()` (riêng tư)
  lên thành `daily_returns()` (public) — cả DSR/PBO ĐỀU cần đúng bước này
  (report đã lưu → chuỗi ngày), đúng tiền lệ `coerce_float`/`parse_point_time`
  đã áp dụng trước đó trong phiên. Đổi tên tham số `field`→`field_name` để
  không đụng `dataclasses.field` đã import sẵn trong `models.py`.
- **`output_util/readout.py`**: thêm `run_dsr()`/`run_pbo()` — đúng khuôn
  `run_montecarlo()` đã có (đọc N report THẬT đủ điều kiện tin qua
  `selection.eligible()`, trích chuỗi ngày, rồi mới gọi hàm thuần). Helper
  riêng tư `_load_trial_returns()` dùng chung cho cả 2 (không trùng lặp code,
  không import riêng tư giữa `dsr.py`↔`pbo.py`).
- **Không đặt `Trial` dataclass dùng chung** giữa 2 file — cân nhắc kỹ (xem
  thảo luận với người dùng), chọn hình dạng dữ liệu đơn giản nhất thay vì tạo
  thêm 1 khái niệm mới phải quyết định "ai sở hữu".

# 5. Verify bằng dữ liệu THẬT (không chỉ test tổng hợp)

Chạy trên đúng 9 trial thật (lưới 3×3 KSL×KTP nội bộ cửa sổ `wf01_is`, walk-
forward demo US30/H1/Combo, 69 ngày):

```
DSR: winner=ksl1/ktp3 (net_profit thật cao nhất trong 9, khớp trực giác)
     winner_sharpe=0.0934, benchmark(SR0)=0.0346, dsr=0.689
PBO: blocks=16, splits_tested=12870, pbo=0.854
```

**Đọc kết quả**: DSR=0.689 (chưa mạnh, hợp lý vì mẫu mỏng — chỉ 9 trial/69
ngày). PBO=0.854 RẤT CAO — cảnh báo mạnh: với đúng 3×3 lưới NỘI BỘ 1 cửa sổ
walk-forward (không phải lưới 10×10 ĐỘC LẬP full-history đã bàn thiết kế
trước đó), cách chọn winner cực kỳ nhạy với cách chia thời gian. **Đây là bộ
dữ liệu DEMO có sẵn, KHÔNG PHẢI lưới 10×10 chuẩn đã thiết kế** — chưa nên coi
là kết luận cuối cùng về chiến lược, chỉ là bằng chứng code chạy đúng trên dữ
liệu thật + minh hoạ đúng hiện tượng "mẫu mỏng → PBO cao" đã bàn trước đó.

# Nguồn

- David H. Bailey, Marcos López de Prado, "The Deflated Sharpe Ratio" (SSRN 2460551, *JPM* 2014).
- David H. Bailey, Jonathan Borwein, Marcos López de Prado, Qiji Jim Zhu, "The Probability of Backtest Overfitting" (SSRN 2326253).
- [Is Your Sharpe Ratio Lying to You? — QuantPy (Medium)](https://medium.com/@TheQuantPy/is-your-sharpe-ratio-lying-to-you-meet-the-probabilistic-sharpe-ratio-d06077e423e8)
- [How to detect false strategies? The Deflated Sharpe Ratio — marti.ai (có code thật)](https://marti.ai/qfin/2018/05/30/deflated-sharpe-ratio.html)
- [Deflated Sharpe Ratio — ml4trading.io diagnostic docs](https://www.ml4trading.io/docs/diagnostic/methods/deflated-sharpe-ratio/)
- [esvhd/pypbo — Python implementation CSCV/PBO (source đọc trực tiếp qua raw.githubusercontent.com)](https://github.com/esvhd/pypbo)
- [README — R package `pbo` (CRAN)](https://cran.r-project.org/web/packages/pbo/readme/README.html)
- Đối chứng thực nghiệm: `runs/research/walkforward/us30_h1_combo_wf_demo/` (window `wf01_is`, 9 run thật).
