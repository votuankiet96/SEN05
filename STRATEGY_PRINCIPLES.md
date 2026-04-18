# Nguyên tắc chiến lược — Combo v2

> Tài liệu này là "hiến pháp" của chiến lược giao dịch.
> Mọi thay đổi PHẢI tuân thủ các nguyên tắc bên dưới.
> Nếu muốn thay đổi nguyên tắc → phải có lý do rõ ràng, có backtest chứng minh, và ghi lại vào đây.

---

## 1. Triết lý giao dịch

**"Giao dịch đảo chiều quanh MA trên khung H4, xác nhận bằng MACD, tự động reverse khi tín hiệu ngược."**

Cụ thể:
- **Loại chiến lược**: Mean-reversion (đảo chiều về trung bình) — KHÔNG phải trend-following
- **Tín hiệu vào lệnh**: Giá cắt qua MA (crossover), xác nhận bởi MACD histogram và nến
- **Cơ chế đặc biệt**: Khi đang giữ lệnh và có tín hiệu ngược chiều → đóng lệnh hiện tại, mở lệnh mới ngược chiều (Reversal)
- **Khung thời gian**: H4 (4 giờ) — đủ lớn để tránh noise, đủ nhỏ để bắt cơ hội

---

## 2. Nguyên tắc KHÔNG BAO GIỜ được vi phạm

Đây là những ranh giới tuyệt đối. Nếu AI hoặc bất kỳ ai đề xuất thay đổi vi phạm các điều này → **TỪ CHỐI**.

### 2.1 Risk Management

| Nguyên tắc | Giá trị hiện tại | Lý do |
|-----------|-----------------|-------|
| Risk mỗi lệnh ≤ 1% equity | 0.5% | Vượt 1% → risk of ruin tăng exponential |
| Daily loss limit ≤ 5% | 5% | FTMO quy định, vi phạm = mất account |
| Max drawdown ≤ 10% | 10% | FTMO quy định, vi phạm = mất account |
| Chỉ 1 lệnh mở cùng lúc per symbol | 1 | Tránh correlation risk, dễ quản lý |
| Partial TP luôn ≥ 40% | 50% | Khóa lợi nhuận sớm, không để tham |

**KHÔNG BAO GIỜ:**
- Tăng risk per trade lên trên 1% — dù backtest có đẹp đến đâu
- Bỏ daily loss limit hoặc max drawdown limit
- Mở nhiều lệnh cùng lúc trên cùng symbol
- Bỏ partial TP (all-in trailing = quá rủi ro)

### 2.2 Tín hiệu vào lệnh

| Nguyên tắc | Lý do |
|-----------|-------|
| Luôn phải có ≥ 2 xác nhận (MA + MACD + nến) | 1 indicator = quá nhiều false signal |
| R:R tối thiểu ≥ 1.0 | Lệnh R:R < 1.0 = kỳ vọng âm về lâu dài |
| Entry phải có breakout buffer (x) | Entry tại giá chính xác = dễ bị whipsaw |

**KHÔNG BAO GIỜ:**
- Vào lệnh chỉ dựa trên 1 indicator duy nhất
- Bỏ điều kiện R:R minimum
- Set x = 0 (entry không có buffer)

### 2.3 Cấu trúc hệ thống

| Nguyên tắc | Lý do |
|-----------|-------|
| Tham số chiến lược tập trung tại `strategy_config.py` | Single source of truth, dễ audit |
| Credentials luôn từ `.env` | Bảo mật, không hardcode |
| Mọi thay đổi DB phải có transaction | Tránh corrupt data |

**KHÔNG BAO GIỜ:**
- Hardcode tham số chiến lược trong file logic (execution.py, backtest_engine.py)
- Hardcode passwords/tokens trong code
- Xóa dữ liệu Fact_OHLCV mà không backup

---

## 3. Những gì ĐƯỢC PHÉP điều chỉnh (trong phạm vi an toàn)

### 3.1 Tham số có thể tinh chỉnh

| Tham số | Phạm vi an toàn | File | Ghi chú |
|---------|-----------------|------|---------|
| `ktp` (hệ số TP) | 1.5 – 3.5 | strategy_config.py | TP = kTP × ATR. Quá nhỏ → chốt lời sớm, quá lớn → không bao giờ chạm |
| `x` (breakout buffer) | 1 – 20 (tùy symbol) | strategy_config.py | Quá nhỏ → whipsaw, quá lớn → bỏ lỡ entry |
| `ma_period` (chu kỳ MA) | 10 – 50 | strategy_config.py | Quá nhỏ → nhiều tín hiệu giả, quá lớn → chậm phản ứng |
| `trailing_activation` | 0.5 – 2.0 | strategy_config.py | Ngưỡng kích hoạt trailing SL (× ATR) |
| `min_rr` | 1.0 – 2.0 | strategy_config.py | R:R tối thiểu để chấp nhận tín hiệu |
| `risk_per_trade` | 0.002 – 0.01 | strategy_config.py | 0.2% – 1.0% equity per trade |
| `pending_ttl_bars` | 1 – 5 | strategy_config.py | Thời gian chờ pending order |
| `session_hours_utc` | Danh sách giờ UTC | strategy_config.py | Lọc giờ giao dịch |

### 3.2 Symbols có thể thêm/bớt

- **Thêm symbol mới**: OK, nhưng phải có đủ data lịch sử (≥ 1 năm H4) và backtest riêng
- **Bỏ symbol kém**: OK, dựa trên Profit Factor < 1.0 qua ≥ 2 năm data
- **Thêm asset class mới** (forex, crypto): CẨN THẬN — cần điều chỉnh parameters riêng, không dùng chung params của indices

### 3.3 Thêm indicator bổ trợ

**ĐƯỢC PHÉP** thêm indicator nhưng phải tuân thủ:
1. Indicator mới chỉ được dùng làm **BỘ LỌC PHỤ** (filter), KHÔNG thay thế tín hiệu MA + MACD
2. Phải backtest so sánh trước/sau thêm (win rate, PF, total trades)
3. Nếu total trades giảm > 40% → indicator quá khắt khe, cần xem lại
4. Tối đa **4 indicators** (hiện tại 3: MA, MACD, ATR) — nhiều hơn = overfitting risk

**VÍ DỤ ĐƯỢC PHÉP:**
- Thêm RSI làm bộ lọc: chỉ BUY khi RSI < 70 (tránh overbought)
- Thêm session filter: chỉ trade giờ London+NY

**VÍ DỤ KHÔNG ĐƯỢC PHÉP:**
- Thay MA bằng Bollinger Bands làm tín hiệu chính → đổi triết lý strategy
- Thêm 5 indicators mới cùng lúc → overfitting

---

## 4. Quy trình thay đổi chiến lược

Mỗi khi muốn thay đổi strategy, **BẮT BUỘC** làm theo quy trình:

### Bước 1: Phân loại thay đổi

| Loại | Ví dụ | Yêu cầu |
|------|-------|---------|
| **Tinh chỉnh** | Đổi kTP từ 2.3 → 2.5 | Backtest so sánh 1 symbol |
| **Thêm filter** | Thêm RSI filter | Backtest so sánh tất cả symbols, trước/sau |
| **Đổi logic** | Đổi entry từ MA crossover → Bollinger breakout | ❌ Vi phạm triết lý → CẦN HỌP BÀN |
| **Đổi risk** | Tăng risk từ 0.5% → 1% | ❌ Phải có Monte Carlo chứng minh |

### Bước 2: Backtest bắt buộc

Trước khi áp dụng bất kỳ thay đổi nào:
1. Chạy backtest **TẤT CẢ symbols** với tham số CŨ → ghi lại KPIs
2. Chạy backtest **TẤT CẢ symbols** với tham số MỚI → ghi lại KPIs
3. So sánh: Sharpe, Win Rate, Profit Factor, Max DD, Total Trades
4. Thay đổi chỉ được áp dụng nếu:
   - PF không giảm > 20%
   - Max DD không tăng > 2%
   - Total trades không giảm > 40%

### Bước 3: Ghi nhật ký

Mỗi thay đổi phải ghi lại:
```
Ngày: YYYY-MM-DD
Thay đổi: [mô tả]
Lý do: [tại sao thay đổi]
Trước: [KPIs cũ]
Sau: [KPIs mới]
Kết luận: [giữ / rollback]
```

---

## 5. Các câu hỏi kiểm tra nhanh

Khi owner hoặc AI muốn thay đổi gì, hãy tự hỏi:

1. **"Thay đổi này có làm tăng risk per trade lên trên 1% không?"** → Nếu có → KHÔNG
2. **"Thay đổi này có thay thế tín hiệu chính (MA + MACD) không?"** → Nếu có → KHÔNG (trừ khi viết lại toàn bộ strategy)
3. **"Thay đổi này có bỏ partial TP không?"** → Nếu có → KHÔNG
4. **"Đã backtest so sánh trước/sau chưa?"** → Nếu chưa → CHƯA ĐƯỢC ÁP DỤNG
5. **"Thay đổi này có thêm complexity (indicator mới, logic mới) không?"** → Nếu có → Tổng indicators vẫn ≤ 4?

---

## 6. Tham số hiện tại (snapshot)

```
Strategy     : Combo v2
Timeframe    : H4
Symbols      : US30, HK50, J225
MA period    : 20 (US30: 25, J225: 25)
MACD         : (5, 25, 5)
ATR period   : 5
kTP          : 2.3 (US30: 2.8, HK50: 2.8, J225: 1.8)
Min R:R      : 1.25
Risk/trade   : 0.5%
Daily limit  : 5%
Max DD       : 10%
Partial TP   : 50% fixed + 50% trailing
Trailing act : 1.0× ATR (US30: 1.5, HK50: 0.75, J225: 1.5)
Pending TTL  : 3 bars
```

---

*Cập nhật lần cuối: 2026-04-12*
*Phiên bản tài liệu: 1.0*
