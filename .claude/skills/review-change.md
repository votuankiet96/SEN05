# Skill: /review-change

Kiểm tra xem yêu cầu thay đổi có phù hợp với nguyên tắc chiến lược hay không. Bảo vệ owner khỏi những thay đổi vô tình phá hỏng strategy.

## Khi nào dùng

Trước khi thực hiện BẤT KỲ thay đổi nào liên quan đến:
- Tham số chiến lược (kTP, x, MA, risk, v.v.)
- Logic vào/ra lệnh (entry, exit, SL, TP)
- Thêm/bớt indicator
- Thêm/bớt symbol
- Thay đổi risk management

## Các bước thực hiện

### Bước 1: Đọc nguyên tắc

Đọc file `STRATEGY_PRINCIPLES.md` ở project root. Đây là "hiến pháp" của chiến lược — mọi thay đổi phải tuân thủ.

### Bước 2: Phân loại yêu cầu thay đổi

Hỏi user: "Bạn muốn thay đổi gì?" hoặc phân tích từ context conversation.

Phân loại vào 1 trong 4 nhóm:

**A. Tinh chỉnh tham số** (ví dụ: đổi kTP từ 2.3 → 2.5)
→ Kiểm tra: tham số mới có nằm trong phạm vi an toàn không?

Phạm vi an toàn (từ STRATEGY_PRINCIPLES.md):
- `ktp`: 1.5 – 3.5
- `x`: 1 – 20 (tùy symbol)
- `ma_period`: 10 – 50
- `trailing_activation`: 0.5 – 2.0
- `min_rr`: 1.0 – 2.0
- `risk_per_trade`: 0.002 – 0.01 (0.2% – 1.0%)
- `pending_ttl_bars`: 1 – 5

Nếu trong phạm vi → ✅ AN TOÀN, nhắc cần backtest so sánh
Nếu ngoài phạm vi → ⚠️ CẢNH BÁO, giải thích rủi ro

**B. Thêm/bớt filter hoặc indicator**
→ Kiểm tra:
- Tổng indicators sau thay đổi có ≤ 4 không? (hiện tại 3: MA, MACD, ATR)
- Indicator mới có THAY THẾ tín hiệu chính (MA crossover + MACD) không?
  - Nếu thay thế → ❌ VI PHẠM triết lý — từ chối hoặc cảnh báo mạnh
  - Nếu chỉ là bộ lọc phụ → ✅ OK, nhắc cần backtest so sánh

**C. Thay đổi logic vào/ra lệnh**
→ Kiểm tra:
- Có thay đổi cơ chế entry (MA crossover) không?
- Có bỏ partial TP không?
- Có bỏ breakeven protection không?
- Có bỏ reversal mechanism không?

Nếu bất kỳ câu nào = CÓ → ❌ VI PHẠM nguyên tắc cốt lõi
→ Cảnh báo mạnh, giải thích tại sao không nên

**D. Thay đổi risk management**
→ Kiểm tra:
- Risk per trade có vượt 1% không?
- Daily limit có vượt 5% không?
- Max DD có vượt 10% không?
- Có bỏ daily limit hoặc max DD limit không?

Nếu bất kỳ câu nào = CÓ → ❌ TUYỆT ĐỐI KHÔNG
→ Từ chối, giải thích FTMO rules và risk of ruin

### Bước 3: Đưa ra đánh giá

Trình bày kết quả bằng tiếng Việt:

```
=== ĐÁNH GIÁ THAY ĐỔI ===

Yêu cầu: [mô tả yêu cầu]
Phân loại: [Tinh chỉnh / Thêm filter / Đổi logic / Đổi risk]

Kiểm tra nguyên tắc:
1. Risk management:     ✅ Không vi phạm / ❌ VI PHẠM (giải thích)
2. Tín hiệu chính:     ✅ Giữ nguyên / ❌ Bị thay đổi (giải thích)
3. Phạm vi tham số:     ✅ Trong phạm vi / ⚠️ Ngoài phạm vi (giải thích)
4. Số lượng indicators: ✅ ≤ 4 / ⚠️ > 4 (quá nhiều)
5. Backtest cần thiết:  ✅ Đã có / ⚠️ Chưa backtest

KẾT LUẬN: 
✅ AN TOÀN — có thể thực hiện (nhớ backtest so sánh)
HOẶC
⚠️ CẨN THẬN — nằm ngoài phạm vi khuyến nghị, cần cân nhắc kỹ
HOẶC
❌ KHÔNG NÊN — vi phạm nguyên tắc cốt lõi
```

### Bước 4: Nếu AN TOÀN, hướng dẫn thực hiện

1. Nhắc owner: "Cần chạy backtest TẤT CẢ symbols trước/sau để so sánh"
2. Chỉ rõ file cần sửa (thường là `strategy_config.py`)
3. Nhắc tiêu chuẩn chấp nhận:
   - Profit Factor không giảm > 20%
   - Max DD không tăng > 2%
   - Total trades không giảm > 40%
4. Nhắc ghi nhật ký thay đổi vào cuối `STRATEGY_PRINCIPLES.md`

### Bước 5: Nếu KHÔNG AN TOÀN, đề xuất thay thế

Không chỉ từ chối — đề xuất cách đạt mục tiêu tương tự mà KHÔNG vi phạm nguyên tắc.

Ví dụ:
- Owner muốn "tăng lợi nhuận" → Thay vì tăng risk, đề xuất tối ưu kTP hoặc thêm symbols mới
- Owner muốn "trade trên M15" → Giải thích noise risk, đề xuất thử H1 trước (gần hơn với H4)
- Owner muốn "thêm 5 indicators" → Đề xuất thêm 1 indicator làm filter, test trước

## Ví dụ cụ thể

### Ví dụ 1: "Tăng risk lên 2%"
```
❌ KHÔNG NÊN
- Risk 2% vi phạm nguyên tắc tối đa 1% (hiện tại 0.5%)
- Với 2% risk: 5 lệnh thua liên tiếp = -10% → vi phạm FTMO max DD
- Đề xuất thay thế: Giữ 0.5%, tối ưu kTP để tăng TP distance
```

### Ví dụ 2: "Thêm RSI filter"
```
✅ AN TOÀN
- RSI là bộ lọc phụ, không thay thế MA + MACD
- Tổng indicators: 4 (MA, MACD, ATR, RSI) = vẫn ≤ 4
- Cần backtest so sánh: RSI < 70 cho BUY, RSI > 30 cho SELL
- Lưu ý: nếu total trades giảm > 40% → RSI quá khắt khe
```

### Ví dụ 3: "Đổi từ MA sang Bollinger Bands"
```
❌ KHÔNG NÊN
- Thay thế tín hiệu chính (MA crossover) = đổi triết lý strategy
- Bollinger Bands là chiến lược khác (volatility breakout vs mean-reversion)
- Đề xuất: Nếu muốn thử BB → tạo strategy MỚI (Combo v3), giữ nguyên v2
```

### Ví dụ 4: "Đổi kTP từ 2.3 → 3.0"
```
✅ AN TOÀN
- kTP 3.0 nằm trong phạm vi 1.5–3.5
- Lưu ý: kTP cao → ít trades chạm TP, trailing chiếm tỷ trọng lớn hơn
- Cần backtest US30, HK50, J225 so sánh PF và total trades
```

## Lưu ý

- Luôn đọc `STRATEGY_PRINCIPLES.md` trước khi đánh giá
- Giải thích bằng tiếng Việt đơn giản, dùng ví dụ cụ thể
- Không chỉ nói "không được" — luôn đề xuất giải pháp thay thế
- Nếu owner kiên quyết muốn vi phạm → yêu cầu ghi lý do vào nhật ký, backtest chứng minh, và cập nhật STRATEGY_PRINCIPLES.md
