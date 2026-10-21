# Chủ đề — `ma_cross` theo luật trạng thái (SMA13 > SMA34 và MACD histogram > 0) + trend + chống trùng

Ngày: 2026-10-16. Người dùng muốn đơn giản hoá `ma_cross` ("viết khá lòng vòng"): BUY khi SMA13 > SMA34 và MACD hist > 0, SELL khi SMA13 < SMA34 và MACD hist < 0 — mỗi nến thoả điều kiện là một setup ("vô số tín hiệu liền mạch") — rồi dựa vào trend và chống trùng cùng chiều để gọn lại. Ghi chú này gồm phép đo (chỉ-đọc) làm căn cứ và kết quả kiểm sau khi **đã triển khai ở `we_core`** cùng ngày (người dùng chốt: đổi luật, khung trend mặc định H2, chưa đồng bộ `des`) và đã **áp dụng lên Redis** bằng restart `we-worker` 2026-10-16 02:32:46 UTC.

**Cập nhật 2026-10-16 (sau khi viết ghi chú này):** người dùng chọn đổi thứ tự lọc về **cách B (chống trùng TRƯỚC, trend SAU)** cho cả ba chiến lược, kể cả `ma_cross`. Mọi số DB1 ở §3 (F137 trở đi: 45.631 tín hiệu với trend H2, 72.421/31.603/23.863 với H1/H3/H4, "tín hiệu ≤ số lần trend đổi hướng", 0 cặp liền nhau cùng chiều) là của **cách A (trend trước, chống trùng sau)** và KHÔNG còn mô tả mã hiện tại. Cách B với trend H2 cho 42.931 tín hiệu DB1 (38,7% của DB0 110.813), 24.581 cặp liền nhau cùng chiều (57%), DB1 ⊆ DB0; số theo từng khung trend H1/H3/H4 của cách B chưa đo. Xem F143 (so A–B) và F144 (phân loại tín hiệu) trong `topic_2026_ms-fractal-trading-range-trend.md`.

Nhãn: `nguồn-báo-cáo` · `tái-lập` · `suy-luận` (định nghĩa ở `INDEX.md`).

## 1. Luật cũ (git HEAD: `git show HEAD:src/strategies/ma_cross.py`)

- DB0 (trend tắt): `raw_signal` = SỰ KIỆN cắt (SMA13 cắt lên/xuống SMA34) VÀ MACD hist cùng dấu ngay nến cắt; cắt mà MACD chưa xác nhận thì mất luôn.
- DB1 (trend bật): máy trạng thái chờ `_apply_trend_wait` (vòng lặp Python): mở "quy chế" ở nến cắt, nổ ở nến đầu tiên MACD và trend cùng khớp, huỷ nếu SMA đảo lại.
- Hệ quả đã đo trước đó (CLAUDE.md, 15/10): DB1 không nằm trong DB0 (30,4%); cặp tín hiệu liền nhau cùng chiều ~16% ở DB0 và ~65% ở DB1; `ma_cross` là chiến lược duy nhất chưa có chống trùng.

## 2. Cách đo

Thay `detect_signals` của `ma_cross` TRONG BỘ NHỚ của tiến trình đo (không sửa file) bằng luật đề xuất, cùng khuôn `detect_combo_signals`/`detect_macd_break_signals`: setup → trend lọc từng setup → chống trùng trên setup đã qua trend; luật cũ dựng lại nguyên văn từ mã cũ để so. Pipeline thật (`run_strategy`, `run_strategy_with_trend_reference`, KNN, ghép trend không nhìn trước, cắt từ 2022-01-01), SQL chỉ SELECT, 11 symbol × M10/M20/M30/M45 = 44 cặp, dữ liệu cắt 2026-10-14 (4,78 năm), warm-up 300.

**Đính chính:** lần đo đầu cache KNN theo `id()` của DataFrame; địa chỉ bộ nhớ được tái sử dụng giữa các symbol nên vài ô (UK100, mọi khung vào lệnh) lấy nhầm khung trend của symbol khác, làm số tín hiệu DB1 lệch tới 2,8% (ví dụ H2 44.628 thay vì 45.631). Đã đổi khoá cache sang nhãn (symbol, khung) và đo lại; mọi số dưới đây là bản đã sửa. Bài học: khoá cache theo nội dung/nhãn, không theo `id()`.

## 3. Kết quả đo

**F135 — bộ đo hợp lệ.** Luật cũ dựng lại: DB0 103.956 (Redis 15/10: 104.054, −0,09%), DB1 trend H4 61.342 (61.402, −0,10%), cặp liền nhau cùng chiều 16.806 / 39.575 (16.829 / 39.615). — `tái-lập`

**F136 — "vô số tín hiệu" là thật và chống trùng là bắt buộc.** Setup thô 1.581.660 trên 3.800.672 nến = 42% số nến. Chỉ lọc trend (chưa chống trùng) vẫn còn 1.224.415 / 1.025.375 / 922.116 / 868.910 (trend H1 / H2 / H3 / H4) = 23–32% số nến. — `tái-lập`

**F137 — số tín hiệu sau trend + chống trùng** (tổng 11 symbol, M10–M45; trong ngoặc: luật cũ):

| | DB0 (không trend) | trend H1 | trend H2 | trend H3 | trend H4 |
|---|---|---|---|---|---|
| Luật mới | 110.813 (103.956) | 72.421 (86.384) | 45.631 (72.607) | 31.603 (65.315) | 23.863 (61.342) |
| So với luật cũ cùng khung | +7% | −16% | −37% | −52% | −61% |
| Mỗi năm mỗi cặp (luật mới) | 527 | 344 | 217 | 150 | 113 |

Luật cũ + trend H4 (cấu hình sản xuất trước 16/10) là 291 mỗi năm mỗi cặp; luật mới + H2 (cấu hình mới) là 217 (−26%). Theo khung vào lệnh (trend H1 → H4, luật mới; trong ngoặc luật cũ): M10 22.816 → 6.389 (35.893 → 27.167); M20 21.777 → 5.894 (23.156 → 14.908); M30 16.706 → 5.798 (16.387 → 10.961); M45 11.122 → 5.782 (10.948 → 8.306). Riêng trend H2: M10 11.812, M20 11.613, M30 11.782, M45 10.424. — `tái-lập`

**F138 — trần bởi số lần trend đổi hướng.** Tín hiệu ≤ số lần trend đổi hướng + 1 ở 176/176 (cặp × khung trend); tín hiệu = 60% / 75% / 78% / 78% số lần đổi hướng (trend H1 / H2 / H3 / H4; 572 / 291 / 193 / 145 lần mỗi năm mỗi cặp); 0 cặp liền nhau cùng chiều; BUY = 50%. Với trend lớn số tín hiệu gần như không phụ thuộc khung vào lệnh (trend H4: M10 121, M45 110 mỗi năm mỗi cặp). — `tái-lập`

**F139 — "trí nhớ" của chống trùng.** Khoảng giữa hai setup đã qua bộ lọc liền nhau cùng chiều: 0 khoảng dài hơn 300 nến (warm-up của worker); lớn nhất 244 (M10, trend H4), 177 (M10, trend H2 mặc định), 113 (M10, H1), 72 (DB0 M10). Cửa sổ 500 nến của `des` đủ. — `tái-lập`

**F140 — kiểm mã thật sau khi triển khai.** Mã sản phẩm (không thay luật trong bộ nhớ) khớp số đo 220/220 phép (44 cặp × [DB0 + 4 khung trend]) cùng các bất biến: trend tắt ⇒ `signal == raw_signal`, `raw_signal` không phụ thuộc trend, 0 cặp liền nhau cùng chiều, DB1 ≤ DB0, entry = close, mọi tín hiệu DB1 cùng chiều `trend_bias`. Dashboard (`run_scan` ma_cross US30 M30, 1.500 nến: 39 tín hiệu không trend, 25 có trend H2) và export CSV (thư mục tạm; 3 cột; hậu tố `tf_knn_H2`) chạy đúng; trang có ô "Alternating Filter" cho ma_cross. — `tái-lập`

**F141 — sau restart `we-worker` (2026-10-16 02:32:46 UTC; bootstrap 349,8 s, 352/352 lần ghi, 0 lỗi).** Redis (chụp 02:39:36) so với chụp trước (02:32:33) và với tham chiếu tính lại từ SQL bằng mã sản phẩm, các dòng bartime < 2026-10-16 00:00: DB0 `ma_cross` 44/44, DB1 `ma_cross` 44/44 (trend H2), DB1 `combo` 44/44 (trend H1) LIST khớp từng dòng/field; DB0 `combo` 44/44, DB0 `macd_break` 88/88, DB1 `macd_break` 88/88 LIST giống hệt trước restart. Số dòng: DB0 `ma_cross` 104.105 → 110.971 (+6,6%), DB1 `ma_cross` 61.432 → 45.715 (−25,6%), DB1 `combo` 4.842 → 12.017 (+148%). Cặp liền nhau cùng chiều = 0 ở cả 6 nhóm; DB1 ≤ DB0 ở 0/176 LIST vi phạm; tín hiệu DB1 không có ở DB0: `combo` 23,9%, `ma_cross` 69,6%, `macd_break` 45,2%. — `tái-lập`

## 4. Hạn chế / chưa đo

- Chỉ số lượng; CHƯA đánh giá chất lượng (lãi/lỗ). CHƯA đo mức trùng nến với tín hiệu của luật cũ (luật mới bắn ở nến đầu tiên SMA + MACD + trend cùng khớp, không gắn với nến cắt).
- Mẫu chỉ tới 2026-10-14.
- `des` CHƯA đồng bộ (cắt + khớp đúng 1 nến, chưa chống trùng) và engine live DE (chưa biết) — luật khác nhau giữa các hệ cho tới khi đồng bộ.
- Dashboard cổng 8517 (người dùng bật 15/10 10:13) chưa restart nên còn luật `ma_cross` cũ + trend mặc định H4 trong bộ nhớ.

## 5. Còn chờ người dùng

Đồng bộ `des` (và DE live); có restart dashboard 8517 không; có đánh giá chất lượng tín hiệu không.
