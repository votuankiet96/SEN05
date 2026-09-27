---
title: 3 phương án block-bootstrap trong montecarlo.py — non_overlapping/moving/stationary
date: 2026-09-22
status: đã triển khai + verify bằng dữ liệu thật (US30/H1/Combo Q1-2026)
---

# Bối cảnh

`optimize/montecarlo.py` ban đầu chỉ có ĐÚNG 1 cách cắt khối để resample (non-overlapping,
Carlstein 1986 — xem `time-series-strategy-optimization-methodology.md` mục 4). Research trước
đã ghi nhận đây là biến thể ĐƠN GIẢN NHẤT trong họ block-bootstrap, có 2 nhược điểm lý thuyết:
(a) lãng phí thông tin ở ranh giới khối cố định, (b) độ dài khối cố định=5 không suy ra từ 1 quy
tắc thống kê. Người dùng chốt: thêm 2 phương án nâng cao (moving, stationary) làm SONG SONG với
bản hiện có (cùng tinh thần `run_dual()` cho field balance/minEquity), không thay thế.

# Thuật toán — xác nhận qua nguồn gốc, không suy đoán

## 1. Non-overlapping (Carlstein, 1986) — mặc định, không đổi

Cắt khối tại vị trí CỐ ĐỊNH: `days[0:5], days[5:10], days[10:15]...`. Chỉ có `n/L` khối (n=số
ngày, L=độ dài khối). Rút có hoàn lại trong đúng `n/L` khối đó.

## 2. Moving/overlapping (Künsch, 1989; Liu & Singh, 1992)

Khối `B_t = {ngày_t, ..., ngày_{t+L-1}}` cho MỌI `t` từ 1 tới `N = n-L+1` — tận dụng hết mọi cụm
L-ngày-liên-tiếp có thật, không chỉ đúng bội số của L. Với 69 ngày/block=5: non-overlapping chỉ có
14 khối, moving có tới 65 khối. Rút có hoàn lại y hệt cơ chế cũ, chỉ khác **kho khối để chọn**.

## 3. Stationary (Politis & Romano, 1994)

Không cắt sẵn khối — đi bộ tuần tự trên dữ liệu coi như **1 VÒNG TRÒN** (hết ngày cuối quay lại
ngày đầu — kỹ thuật circular wrap-around, tiền thân là circular block bootstrap của chính Politis
& Romano 1992). Mỗi bước, sau khi lấy 1 ngày, có xác suất `p = 1/mean_block_days` để NHẢY sang 1
điểm bắt đầu mới hoàn toàn ngẫu nhiên. Độ dài khối vì vậy là biến ngẫu nhiên phân phối HÌNH HỌC,
kỳ vọng = `mean_block_days` — không cố định như 2 phương án kia. Ưu điểm lý thuyết: chuỗi resample
ra giữ đúng tính "dừng" (stationary) thống kê, loại bỏ tính phi-dừng giả tạo do độ dài khối cố
định gây ra.

# Thiết kế code (`core_engine/optimize/montecarlo.py`)

- `run(..., *, field="balance", method="non_overlapping")` — thêm trục `method`, ĐỘC LẬP với trục
  `field` đã có (2 trục tự do phối, không ép cứng thành 1 tổ hợp).
- `_cut_blocks(days, block_days, method)` — dùng chung cho `non_overlapping`/`moving`, chỉ khác
  `range()` (bước nhảy `block_days` vs bước nhảy 1).
- `_resample_stationary(days, mean_block_days, rng)` — vòng lặp KHÁC HẲN hình dạng (đi bộ tuần tự
  + nhảy ngẫu nhiên), không dùng `_cut_blocks`.
- `run_methods_compare(equity_points, config, *, field="balance")` — chạy song song cả 3
  `BLOCK_METHODS`, trả về 3 nhánh đặt tên rõ, KHÔNG gộp thành 1 số (cùng nguyên tắc đã áp dụng cho
  `run_dual()` — 2 việc thống kê khác nhau không nên ép về 1 con số).
- Quyết định: KHÔNG làm 1 hàm chéo 3×2=6 nhánh (method×field) — giữ 2 hàm so sánh tách biệt
  (`run_dual` cho field, `run_methods_compare` cho method), mỗi hàm cố định trục còn lại ở mặc
  định. Cần đủ 6 tổ hợp thì gọi `run()` trực tiếp — đã đủ tự do phối 2 tham số.
- `block_days` đổi nghĩa nhẹ khi `method="stationary"`: không còn là ĐỘ DÀI CỐ ĐỊNH mà là ĐỘ DÀI
  TRUNG BÌNH mong muốn — dùng lại đúng 1 field cấu hình cho cả 3 phương án, không thêm field mới.

# Kết quả thật — US30.cash/H1/Combo, Q1-2026 (69 ngày, cùng seed=20260911, paths=10000)

| Phương pháp | max_dd p50 | max_dd p95 | max_dd p99 | p_daily_breach | p_total_breach |
|---|---:|---:|---:|---:|---:|
| non_overlapping (cũ) | 5.27% | 10.25% | 12.71% | 0% | **2.46%** |
| moving | 5.60% | 11.06% | 13.87% | 0% | **4.12%** |
| stationary | 5.50% | 10.82% | 13.59% | 0% | **3.55%** |

**Phát hiện quan trọng — SỬA LẠI 1 nhận định cũ**: `time-series-strategy-optimization-methodology.md`
mục 4 (viết trước khi có số đo thật) từng nhận định "sai số [giữa các biến thể bootstrap] thường
không đủ lớn để đổi kết luận thực hành". Số đo thật trên mẫu 69-ngày này KHÔNG khớp nhận định đó:
`p_total_breach` từ non_overlapping (2.46%) lên moving (4.12%) — **tăng gần gấp đôi**, đủ lớn để
đổi kết luận nếu ngưỡng chấp nhận rủi ro của người dùng nằm giữa khoảng đó (ví dụ đặt ngưỡng 3%).
Đúng chiều lý thuyết dự đoán: non-overlapping "lãng phí" khối → đánh giá THẤP rủi ro đuôi hơn thực
tế. Với mẫu NHỎ (69 ngày/14 khối cố định) sai khác giữa các phương pháp rõ hơn hẳn so với mẫu lớn
(khi n/L đã đủ lớn, 3 phương pháp hội tụ gần nhau) — đã cập nhật lại nhận định này trong
`time-series-strategy-optimization-methodology.md`.

**Ý nghĩa thực hành**: với dữ liệu MỎNG (vài tháng, như demo hiện tại), nên ưu tiên đọc
`moving`/`stationary` thay vì chỉ tin `non_overlapping` — bản cũ có xu hướng lạc quan giả tạo về
rủi ro vi phạm luật FTMO khi mẫu ít.

# Nguồn

- [The Bootstrap for Network Dependent Processes (khảo sát circular wrap-around, Politis-Romano 1992/1994)](https://arxiv.org/pdf/2101.12312)
- [A note on the stationary bootstrap's variance](https://projecteuclid.org/journals/annals-of-statistics/volume-37/issue-1/A-note-on-the-stationary-bootstraps-variance/10.1214/07-AOS567.pdf)
- [Consistency and application of moving block bootstrap for non-stationary time series](https://arxiv.org/pdf/0711.4493)
- [The moving block bootstrap for time series — SAS blog (tóm tắt thuật toán Künsch/Liu-Singh rõ ràng)](https://blogs.sas.com/content/iml/2021/01/13/moving-block-bootstrap-sas.html)
- [Automatic Block-Length Selection for the Dependent Bootstrap — Politis & White (2004)](https://public.econ.duke.edu/~ap172/Politis_White_2004.pdf)
- Đối chứng thực nghiệm: `runs/research/grid/us30_h1_combo_2026q1_montecarlo_dual/runs/0001__combo__US30.cash__h1__single__ksl2__ktp4__r0p5/report.json.gz` (đường dẫn tính từ `bo_workflow/` — `runs/` đã dời ra khỏi `core_engine/` ngày 2026-09-22)
