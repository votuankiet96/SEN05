---
title: Pipeline chuẩn cho toàn bộ bo_workflow — Grid → {Walk-forward ∥ DSR+PBO} → Final backtest → Monte Carlo
date: 2026-09-22
status: CHỐT — quyết định kiến trúc áp dụng cho MỌI (symbol, timeframe, strategy) từ nay
---

# Bối cảnh

Sau sự cố look-ahead bias (dùng Grid full-period để thu hẹp phạm vi tìm kiếm của
walk-forward — xem [[verify-design-before-costly-execution]]), người dùng yêu cầu
dựng lại pipeline dựa trên RESEARCH/EVIDENCE thật, không tự suy luận. Đây là kết quả
research + quyết định cuối — **áp dụng cho MỌI (symbol, timeframe, strategy) từ nay**,
không riêng US30/H1/Combo.

# Bằng chứng đã tìm

1. **López de Prado, "Advances in Financial Machine Learning"** (qua tổng hợp
   reasonabledeviations.com/notes/adv_fin_ml) — walk-forward và CPCV (nền tảng của
   CSCV/PBO) là **2 KỸ THUẬT BỔ SUNG CHO NHAU, không phải kế tiếp/lồng nhau**:
   walk-forward "dễ hiểu nhưng dễ overfit vì chỉ kiểm trên ĐÚNG 1 lịch sử"; CPCV giải
   quyết đúng điểm yếu đó bằng cách tạo NHIỀU lịch sử thay thế từ CÙNG 1 bộ dữ liệu.
   Không có hướng dẫn nào nói CPCV/PBO cần NHÚNG vào bên trong từng cửa sổ walk-forward.

2. **`sebdsg/Launch-market-validation` (GitHub, đọc thẳng code thật `sim/gate.py`)** —
   1 pipeline THẬT ("five-hurdle promotion gate") kết hợp walk-forward + DSR + PBO +
   2 hurdle khác (stability/jitter, đặc thù cho universe mô phỏng, không áp dụng trực
   tiếp cho dữ liệu lịch sử thật của mình). Bằng chứng cấu trúc quan trọng nhất, đọc
   trực tiếp từ code:
   ```python
   wf, wf_best = _walk_forward(factory, grid_params, gen, seeds[0], n)
   dsr_crit, pbo_crit = _dsr_pbo(factory, grid_params, gen, seeds[0], n, ...)
   ```
   Walk-forward VÀ DSR/PBO đều nhận **CÙNG 1 `grid_params`** làm input — **ĐỘC LẬP**
   với nhau (không cái nào sinh input cho cái kia). DSR/PBO tính **ĐÚNG 1 LẦN** trên
   TOÀN BỘ lưới gốc — KHÔNG lặp lại cho từng cửa sổ walk-forward.

# Pipeline CHỐT (áp dụng chung, không riêng US30/H1/Combo)

```
[1] Grid — ĐỘC LẬP, full lịch sử có sẵn, 1 (symbol, timeframe, strategy)
    (đủ N×N tham số muốn quét, KHÔNG thu hẹp bởi bất kỳ dữ liệu nào khác)
       │
       ├──> [2] Walk-forward — CHẠY THẬT qua ctrader-cli (backtest RIÊNG, không
       │    dùng lại report của [1] vì khác khoảng ngày). Tự quét ĐỦ lưới TRONG
       │    TỪNG cửa sổ (không narrow bằng dữ liệu ngoài cửa sổ đó — bài học
       │    look-ahead bias). Trả lời: "tham số nào SỐNG QUA THỜI GIAN khác?"
       │    → cho ra 1 (hoặc vài) TỔ HỢP THAM SỐ cụ thể.
       │
       └──> [3] DSR + PBO — THUẦN HẬU KỲ trên report CỦA [1] (0 backtest thêm).
            Tính ĐÚNG 1 LẦN trên toàn bộ lưới gốc — KHÔNG lặp cho từng cửa sổ.
            Trả lời: "CÓ NÊN TIN kết quả rút ra từ cả quá trình tìm kiếm này
            không?" → 1 "đèn tín hiệu" (xanh/đỏ) cho TOÀN BỘ quá trình, KHÔNG
            gắn với 1 tổ hợp cụ thể nào.

    [2] và [3] chạy SONG SONG, ĐỘC LẬP — cùng dựa vào [1], không ảnh hưởng
    cách nhau tìm kiếm/tính toán.

[4] Quyết định: lấy tổ hợp [2] chọn ra, XÉT THÊM đèn tín hiệu [3] (DSR thấp +
    PBO cao = cẩn trọng với CẢ kết luận, không phải "đổi tổ hợp khác") → nếu
    đèn xanh, backtest FULL LẠI 1 lần với tổ hợp đã chọn, full lịch sử.

[5] Monte Carlo (dual field balance/minEquity + 3 phương án bootstrap) trên
    report của [4] — đo rủi ro trình tự/vi phạm luật FTMO cho tổ hợp CUỐI.
```

# Bài học đã gộp vào pipeline này (không lặp lại)

- **Không dùng Grid full-period để thu hẹp phạm vi tìm kiếm của walk-forward**
  (look-ahead bias — xem [[verify-design-before-costly-execution]]).
- **[2026-09-25] Cờ `parity_certified` đã bỏ khỏi toàn hệ thống** (trước đây: bắt buộc
  trong mọi config). Người dùng tự đối chiếu GUI↔CLI bằng tay khi chạy
  (`evidence.read_gui_report`/`compare_history` vẫn giữ làm công cụ), và nhờ Claude
  kiểm tra khi thấy lệch. Lý do bỏ: dashboard tự đặt True cho mọi symbol nên cờ
  không còn chặn được gì, lại gây hiểu lầm "đã chứng nhận".
- **DSR/PBO tính 1 lần trên Grid gốc**, không nhúng lặp lại theo từng cửa sổ walk-forward
  (tưởng tượng thêm bước này lúc trước là dư thừa, không có căn cứ).

# Trạng thái

Đây là quyết định KIẾN TRÚC áp dụng cho mọi (symbol, timeframe, strategy) tương lai,
không chỉ US30/H1/Combo đang thử nghiệm. Lần thử nghiệm đầu tiên: US30/H1/Combo — xem
kết quả trong `runs/research/grid/us30_h1_combo_full_grid/` và
`runs/research/walkforward/us30_h1_combo_full_wf/` khi hoàn tất.

# Nguồn

- Marcos López de Prado, *Advances in Financial Machine Learning* (2018) — tóm tắt qua
  [reasonabledeviations.com/notes/adv_fin_ml](https://reasonabledeviations.com/notes/adv_fin_ml/).
- [sebdsg/Launch-market-validation (GitHub)](https://github.com/sebdsg/Launch-market-validation) —
  `src/mquant/sim/gate.py` (đọc trực tiếp qua raw.githubusercontent.com).
- Đối chiếu bổ sung: so sánh CPCV vs walk-forward (PBO/DSR thực nghiệm) — kết quả tìm
  qua khảo sát chung, xem thêm `time-series-strategy-optimization-methodology.md` mục 7.
