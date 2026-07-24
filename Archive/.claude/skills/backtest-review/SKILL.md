---
name: backtest-review
description: Đánh giá kết quả backtest có đáng tin không, cảnh báo nếu bị ảnh hưởng bởi bugs đã biết. TRIGGER khi user nói: "backtest", "kết quả backtest", "review backtest", "đánh giá backtest", "sharpe", "win rate", "profit factor", "drawdown", "strategy có tốt không", "strategy hoạt động không", "kết quả có tin được không", "backtest ra gì", "chạy backtest xong", "xem kết quả", "check results", "review results", "is the strategy good".
---

# Skill: /backtest-review

Đánh giá kết quả backtest có đáng tin hay không. Cảnh báo nếu kết quả bị ảnh hưởng bởi bugs đã biết.

## Khi nào dùng

Sau khi chạy backtest xong, trước khi tin vào kết quả.

## Các bước thực hiện

### Bước 1: Kiểm tra bugs đã fix chưa

Grep từng file để xác định bugs critical còn tồn tại hay đã được sửa:

**Bug 1 — Look-ahead bias (execution.py)**

Tìm pattern: pending order fill trên cùng bar (thay vì T+1). Đọc `core_python/strategies/combo/core/execution.py` khoảng line 108-142 (hàm `backtest_symbol`) và line 474-499 (hàm `backtest_fast`). Kiểm tra xem:
- Order có fill ngay trên bar hiện tại khi price touch entry level không?
- Hay đã chuyển sang fill tại bar tiếp theo (T+1)?

Nếu vẫn fill cùng bar → BUG CHƯA FIX → đánh dấu ❌

**Bug 2 — MA trailing phá breakeven (execution.py)**

Đọc khoảng line 170-195. Kiểm tra:
- Sau partial TP, có lưu `breakeven_price` không?
- MA trailing logic có guard không cho SL vượt qua breakeven không?

Nếu không có guard → BUG CHƯA FIX → đánh dấu ❌

**Bug 3 — Sharpe/Sortino inflation (metrics.py)**

Đọc `core_python/strategies/combo/core/metrics.py` khoảng line 70-85. Kiểm tra:
- Có check minimum sample size trước khi annualize không?
- Sharpe có dùng `sqrt(1512)` bất kể data length không?

Nếu vẫn dùng sqrt(1512) không check sample → BUG CHƯA FIX → đánh dấu ❌

**Bug 4 — Monte Carlo bootstrap (monte_carlo.py)**

Đọc `core_python/strategies/combo/core/monte_carlo.py` khoảng line 50-60. Kiểm tra:
- Dùng `np.random.permutation` (random shuffle) hay block bootstrap?

Nếu vẫn dùng permutation → BUG CHƯA FIX → đánh dấu ❌

### Bước 2: Đánh giá chỉ số backtest

Hỏi user hoặc đọc output backtest gần nhất. Đánh giá từng chỉ số:

**Sharpe Ratio:**
- Nếu bug Sharpe CHƯA FIX:
  - Sharpe hiển thị / 38 ≈ Sharpe thực tế ước tính (với 3 tháng data)
  - Ví dụ: Sharpe = 15.2 → thực tế ≈ 0.4
- Nếu bug ĐÃ FIX:
  - < 1.0: bình thường
  - 1.0 - 2.0: tốt
  - 2.0 - 3.0: rất tốt (cần kiểm tra kỹ)
  - \> 3.0: suspicious — có thể overfitting

**Win Rate:**
- < 40%: thấp nhưng chấp nhận nếu R:R tốt
- 40-60%: bình thường cho trend-following
- 60-70%: tốt
- \> 70%: suspicious cho trend-following — có thể curve-fitted
- \> 80%: gần chắc chắn overfitted hoặc có bug

**Profit Factor:**
- < 1.0: strategy lỗ
- 1.0 - 1.5: yếu
- 1.5 - 2.5: tốt
- 2.5 - 3.0: rất tốt (kiểm tra kỹ)
- \> 3.0: suspicious
- \> 5.0: gần chắc chắn bug hoặc overfitting

**Số lượng trades:**
- < 30: KHÔNG ĐỦ để kết luận — bất kỳ metric nào cũng không đáng tin
- 30-100: tối thiểu, cần thêm data
- 100-300: đủ để phân tích cơ bản
- \> 300: tốt

**Max Drawdown:**
- \> 5%: cần chú ý nếu target FTMO
- \> 10%: vượt giới hạn FTMO 10%
- \> 20%: rất rủi ro

### Bước 3: Đánh giá tổng hợp

Dựa trên bugs và metrics, đưa ra kết luận:

**Nếu có bất kỳ bug CRITICAL nào chưa fix:**
→ "Kết quả KHÔNG TIN CẬY — cần fix bugs trước"
→ Liệt kê cụ thể bugs nào ảnh hưởng và ảnh hưởng thế nào

**Nếu tất cả bugs đã fix nhưng metrics suspicious:**
→ "Kết quả CẦN KIỂM TRA THÊM — có dấu hiệu overfitting"
→ Gợi ý chạy walk-forward hoặc out-of-sample test

**Nếu tất cả bugs đã fix và metrics hợp lý:**
→ "Kết quả CÓ THỂ TIN CẬY — nhưng nên test thêm trên out-of-sample data"

### Bước 4: Báo cáo kết quả

```
=== ĐÁNH GIÁ KẾT QUẢ BACKTEST ===

TRẠNG THÁI BUGS:
1. Look-ahead bias:    ✅ Đã fix / ❌ CHƯA FIX → backtest lạc quan 5-20%
2. Breakeven guard:    ✅ Đã fix / ❌ CHƯA FIX → một số trades lỗ hơn expected
3. Sharpe inflation:   ✅ Đã fix / ❌ CHƯA FIX → Sharpe thực ≈ X.X (thay vì Y.Y)
4. Monte Carlo:        ✅ Đã fix / ❌ CHƯA FIX → confidence interval quá hẹp

CHỈ SỐ:
- Sharpe:        X.X  ✅/⚠️/❌
- Win rate:      XX%  ✅/⚠️/❌
- Profit Factor: X.X  ✅/⚠️/❌
- Trades:        XXX  ✅/⚠️
- Max DD:        X.X% ✅/⚠️/❌

KẾT LUẬN: [TIN CẬY / CẦN KIỂM TRA / KHÔNG TIN CẬY]
→ [Gợi ý hành động tiếp theo]
→ Xem chi tiết bugs: IMPROVEMENT_TASKS.md
```

## Lưu ý

- Skill này CHỈ ĐỌC code và đánh giá, không thay đổi gì
- Luôn tham chiếu đến `IMPROVEMENT_TASKS.md` để user biết cách fix
- Giải thích bằng tiếng Việt đơn giản, tránh thuật ngữ kỹ thuật phức tạp
- Nếu user chưa có kết quả backtest cụ thể → hướng dẫn cách chạy backtest trước
