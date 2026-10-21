# Chủ đề — Chuẩn frontend và UI/UX để dashboard (`signal_display`) mượt

Ngày: 2026-10-15. Câu hỏi của người dùng: nghiên cứu các tiêu chuẩn để code frontend và UI/UX sao cho dashboard mượt; **không sửa backend, chỉ tập trung trong nhóm file `we_core/src/signal_display/`**. Ghi chú này CHỈ nghiên cứu và đề xuất — chưa sửa mã.

Cách làm: tìm rồi mở trực tiếp trang gốc để đọc nguyên văn (✔ = đã đọc văn bản gốc qua công cụ, trích ngắn); đối chiếu với số đo trên chính dashboard (T003). W3C chặn công cụ tải trang (HTTP 403) nên các tiêu chí WCAG đọc qua trang diễn giải của MDN (xem §6). Tìm "vừa đủ", dừng khi các trang chính thống đã trả lời.

Nhãn: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở INDEX.md).

## 1. Nguồn đã đọc

| ID | Nguồn | Loại | Ghi chú |
|---|---|---|---|
| S042 | web.dev, *Interaction to Next Paint (INP)* — web.dev/articles/inp | tài liệu chính thức (Google) | ✔ ngưỡng, 3 pha, loại tương tác |
| S043 | web.dev, *Optimize long tasks* — web.dev/articles/optimize-long-tasks | tài liệu chính thức | ✔ |
| S044 | web.dev, *Optimize Interaction to Next Paint* — web.dev/articles/optimize-inp | tài liệu chính thức | ✔ (bài không nói về debounce) |
| S045 | MDN, *Animation performance and frame rate* — developer.mozilla.org/en-US/docs/Web/Performance/Guides/Animation_performance_and_frame_rate | tài liệu chính thức | ✔ ngân sách khung hình |
| S046 | Nielsen, *Response Times: The 3 Important Limits* — nngroup.com/articles/response-times-3-important-limits/ | bài NN/g | ✔ (1993/2000, gốc từ Miller 1968) |
| S047 | NN/g, *Skeleton Screens 101* — nngroup.com/articles/skeleton-screens/ | bài NN/g | ✔ |
| S048 | NN/g, *10 Usability Heuristics* — nngroup.com/articles/ten-usability-heuristics/ | bài NN/g | ✔ chỉ đoạn đầu mỗi định nghĩa (công cụ cắt ngắn) |
| S049 | Laws of UX, *Doherty Threshold* — lawsofux.com/doherty-threshold/ | trang tổng hợp | ✔ trang này; gốc là Doherty & Thadani, IBM Systems Journal 1982 (chưa đọc bài gốc) |
| S050 | TradingView Lightweight Charts, demo *Infinite history* — tradingview.github.io/lightweight-charts/tutorials/demos/infinite-history | tài liệu chính thức | ✔ mã demo |
| S051 | TradingView Lightweight Charts docs: `ChartOptionsBase` (autoSize), `TimeScaleOptions` — tradingview.github.io/lightweight-charts/docs/api/interfaces/… | tài liệu chính thức | ✔ docs BẢN MỚI; vendor của hệ là v4.2.1 → đối chiếu bằng grep (F098–F099) |
| S052 | MDN, *AbortController* và *AbortController.abort()* | tài liệu chính thức | ✔ |
| S053 | MDN, *ARIA live regions* | tài liệu chính thức | ✔ |
| S054 | MDN, *Understanding WCAG — Color contrast* (1.4.3, 1.4.11) | tài liệu chính thức | ✔ thay cho trang W3C (403) |
| S055 | MDN, *Understanding WCAG — Operable* (2.1.1, 2.4.7, 2.5.8) | tài liệu chính thức | ✔ |
| S056 | MDN, *Understanding WCAG — Robust* (4.1.2, 4.1.3) | tài liệu chính thức | ✔ nhưng ngắn; không nêu kỹ thuật live region |
| S057 | Chartability (Elavsky) — chartability.fizz.studio; Elavsky, Bennett, Moritz (2022) Eurographics | khung đánh giá | trang chủ ✔ (7 nguyên tắc POUR + CAF, không liệt kê test); "50 test, 14 critical" chỉ từ tóm tắt tìm kiếm (thứ cấp) |
| S058 | Google HTML/CSS Style Guide — google.github.io/styleguide/htmlcssguide.html | style guide | ✔ |
| T003 | §4 dưới đây | số đo trên dashboard thật (SQL chỉ đọc, Chrome headless) | `tái-lập` |

## 2. Các chuẩn nói gì — theo chủ đề

### 2.1. Hiệu năng tương tác (cảm giác "mượt")
- **INP** (S042 ✔): "An INP below or at 200 milliseconds means a page has good responsiveness"; trên 200 đến 500 ms là cần cải thiện; trên 500 ms là kém — tính ở phân vị 75 của lượt tải thực tế. Gồm 3 pha: input delay, processing duration, presentation delay. INP chỉ quan sát **click, tap, phím**; hover, zoom, scroll **không** tính. (F089)
- **Tác vụ dài** (S043 ✔): "Any task that takes longer than 50 milliseconds is a long task." Cách xử lý: nhường luồng chính (`await scheduler.yield()`), dự phòng `setTimeout` bọc Promise; `isInputPending` không còn được khuyến nghị; việc nặng có thể đưa sang Web Worker. (F090)
- **Tối ưu INP** (S044 ✔): "do as little work as possible" trong trình xử lý sự kiện; nhường luồng; tránh layout thrashing (ghi style rồi đọc layout trong cùng tác vụ); DOM nhỏ; chỉ làm phần cần cho khung kế, hoãn phần còn lại bằng `requestAnimationFrame`; `content-visibility` cho vùng ngoài màn hình. (F091)
- **Ngân sách khung hình** (S045 ✔): 60 fps = 16,7 ms để chạy script + tính lại style + layout + vẽ lại. Thuộc tính chỉ cần composite (`transform`, `opacity`) rẻ hơn thuộc tính đổi hình học (layout + paint). (F092)

### 2.2. Độ trễ cảm nhận và phản hồi
- **Nielsen** (S046 ✔): 0,1 s ≈ giới hạn để thấy hệ thống "phản ứng tức thì"; 1 s ≈ giới hạn để mạch suy nghĩ không bị đứt; 10 s ≈ giới hạn giữ chú ý. "For delays of more than 1 second, indicate to the user that the computer is working on the problem." Trên 10 s cần chỉ báo phần trăm hoàn thành và cách ngắt thao tác. (F093)
- **Doherty** (S049 ✔): phản hồi trong 400 ms; dùng "perceived performance" (hoạt ảnh, thanh tiến trình) để chờ dễ chịu hơn dù tốc độ thật không đổi. (F094)
- **Skeleton/spinner** (S047 ✔): spinner "best used when the page takes 2–10 seconds"; skeleton cho tải cả màn hình, dưới 10 s; dưới 1 s không cần; trên 10 s dùng thanh tiến trình; "do not replace performance-optimization efforts"; skeleton hoạt ảnh có thể gây khó chịu/vấn đề truy cập. (F095)
- **Heuristic NN/g** (S048 ✔, trích ngắn): #1 Visibility of system status ("always keep users informed about what is going on"), #3 User control and freedom, #7 Flexibility and efficiency of use, #9 thông báo lỗi bằng ngôn ngữ thường. (F096)

### 2.3. Thư viện chart (Lightweight Charts) — chỉ những gì áp dụng được cho vendor v4.2.1
- **Mẫu tải lịch sử chính thức** (S050 ✔): `chart.timeScale().subscribeVisibleLogicalRangeChange(...)`, ngưỡng `if (logicalRange.from < 10)`, nạp `50 - logicalRange.from` nến, gộp bằng `series.setData(data)` (không phải `update`). Demo **không có** cờ chống tải chồng (chỉ trễ giả 250 ms) — người dùng phải tự thêm. (F097)
- **`autoSize: true`** (S051 ✔): chart "watch the chart container's size" bằng ResizeObserver (theo container, không theo cửa sổ); khi bật thì `width`/`height` bị bỏ qua. Vendor v4.2.1 có chuỗi `autoSize` (9 lần) và `ResizeObserver` (15 lần). (F098)
- **`TimeScaleOptions`** (S051 ✔): `fixLeftEdge`/`fixRightEdge` (mặc định false), `rightOffset` (0), `shiftVisibleRangeOnNewBar` (true; "only applies when the last bar is visible"), `lockVisibleTimeRangeOnResize` (false), `rightBarStaysOnScroll` (false), `minBarSpacing` (0,5). Tuỳ chọn `enableConflation`/`precomputeConflationOnInit` có trong docs mới nhưng **không có** trong vendor v4.2.1 (0 lần) → đừng dựa vào. (F099)
- **Huỷ request** (S052 ✔): `abort()` "aborts an asynchronous operation before it has completed… fetch requests, the consumption of any response bodies, or streams"; lý do mặc định là `DOMException` tên "AbortError". MDN không viết rõ rằng promise của `fetch` bị reject bằng lỗi đó — phần này là kiến thức chung. (F100)

### 2.4. Truy cập được (WCAG 2.2, ARIA, Chartability)
- **Live region** (S053 ✔): `aria-live="polite"` cho cập nhật quan trọng nhưng không dồn dập; `assertive` chỉ cho khẩn cấp; `role="status"` (nên thêm `aria-live="polite"` cho tương thích) và `role="alert"`; vùng live phải **có sẵn trong DOM trước khi đổi nội dung** — "most reliable… include them in the initial markup". (F101)
- **Tương phản** (S054 ✔): 1.4.3 — chữ thường ≥ 4,5:1, chữ lớn ≥ 3:1; 1.4.11 — thành phần giao diện và đồ hoạ (biểu tượng, đồ thị) ≥ 3:1. (F102)
- **Vận hành** (S055 ✔): 2.5.8 mục tiêu con trỏ ≥ 24×24 px (AA, có ngoại lệ); 2.1.1 "All functionality should be accessible using keyboard controls"; 2.4.7 vòng focus nhìn thấy. (F103)
- **Tên/vai trò/giá trị và trạng thái** (S056 ✔): 4.1.2 tên và vai trò điều khiển phải xác định được bằng chương trình; 4.1.3 "Assistive technology users are made aware of new status messages added to the page". Cách hiện thực (live region) lấy từ S053; chi tiết kỹ thuật của W3C không đọc được. (F104)
- **Không chỉ dựa vào màu** (1.4.1) và **nội dung 4.1.3 chi tiết**: chỉ có từ tóm tắt tìm kiếm (thứ cấp), không đọc được trang W3C — dùng thận trọng.
- **Chartability** (S057): khung đánh giá dựng trên POUR (Perceivable, Operable, Understandable, Robust) cộng CAF (Compromising, Assistive, Flexible); số liệu "50 test, 14 critical" từ nguồn thứ cấp; danh sách test chi tiết ở workbook (chartability.github.io/POUR-CAF) — chưa đọc. (F105)

### 2.5. Cấu trúc mã frontend
- **Google HTML/CSS Style Guide** (S058 ✔): "Strictly keep structure (markup), presentation (styling), and behavior (scripting) apart"; "Use HTML according to its purpose"; luôn `<!doctype html>`; `<meta charset="utf-8">`. Guide không có quy tắc về viewport/lang. (F106)

## 3. Phát hiện (ID tiếp từ F088)

Các phát hiện F089–F106 được trình bày kèm trích ở §2 (mỗi mục §2 ghi sẵn ID). Phát hiện từ số đo của hệ: F107–F113 (§4); sau khi làm Gói C: F114–F119 (§4b).

## 4. T003 — đo dashboard hiện tại (`tái-lập`)

Điều kiện: code `we_core` ngày 2026-10-15 (sau tách `trend_methods`), SQL DP6 chỉ SELECT, Flask `threaded=True` (như bản chạy thật), cặp Combo US30 H1; Chrome headless với render phần mềm (số tuyệt đối về độ mượt thường PESSIMISTIC so với máy có GPU); vendor Lightweight Charts v4.2.1. Script đo nằm ở scratchpad phiên làm việc (tạm, **không lưu trong repo**) — cách dựng lại: bọc từng bước của `/api/scan` bằng bộ đếm thời gian, mở Chrome qua giao thức CDP (websocket tự viết bằng thư viện chuẩn), đọc Resource Timing / MutationObserver / `Performance.getMetrics`, và kiểm a11y bằng công thức tương phản WCAG + cây truy cập (`Accessibility.getFullAXTree`).

- **F107** Tương tác trơn (500 nến; rê chuột 90 sự kiện, lăn chuột zoom 30, kéo 60 bước): khoảng giữa khung hình p50/p95/max = 16,7/16,7–16,8/16,8 ms, 0 khung > 50 ms, 0 tác vụ dài; luồng chính bận 19–38% (No Trend) và 24–53% (Trend). — `tái-lập`
- **F108** Server `/api/scan`: 500 nến 0,21–0,39 s (tắt trend) / 0,34–0,45 s (bật); 2000 nến 0,64–0,96 s / 0,85–1,09 s; 5000 nến 1,5–2,1 s / 2,0–2,5 s; 20000 nến (tối đa) 5,8–7,5 s / 7,5–9,5 s; dung lượng JSON 88–117 KB ở 500 nến, 355–486 KB ở 2000, 3.590–4.920 KB ở 20000. Dựng payload bằng vòng lặp Python (`payload.py`) chiếm ~64% / 82% / 90% thời gian server ở 500 / 2000 / 20000 nến; SQL 54–215 ms; chiến lược 13–27 ms; JS trình duyệt sau khi nhận dữ liệu 4–21 ms. Bật trend thêm ~47–130 ms mỗi request (đọc SQL khung lớn, tính KNN **hai lần** — một trong pipeline, một lần chỉ để vẽ chart — và dựng dữ liệu chart trend). — `tái-lập`
- **F109** Số request `/api/scan` mỗi lần bấm Refresh (trang tự tải thêm lịch sử ngay sau khi vẽ): No Trend 300/500/1000/2000 nến = 2/1/1/1; Trend Filter = 3/3/2/1 (lần đo khác ra 2 ở 500 nến); với trend, dữ liệu đầu hiện sau ~0,35 s nhưng chỉ ổn định sau ~1,0–1,2 s và số nến hiển thị gấp 2–3 lần số yêu cầu. Cơ chế gây ra CHƯA kiểm chứng (nghi do đồng bộ vùng nhìn giữa chart trend và chart giá làm lộ vùng trước nến đầu → điều kiện `barsBefore < 0`). — `tái-lập` + `suy-luận`
- **F110** Dòng "raw X → filtered Y (Z%)" sai sau khi gộp lịch sử: `mergeHistory` gộp danh sách tín hiệu nhưng giữ `stats` của lần quét đầu. Ví dụ cùng 2000 nến: "raw 44 → filtered 31 (70,5%)" (sai) so với "raw 98 → filtered 31 (31,6%)" (đúng); có lần ra "raw 22 → filtered 30 (136,4%)" — không thể xảy ra. Lỗi có sẵn từ trước. — `tái-lập`
- **F111** Kiểm a11y tự động (29 tổ hợp màu, 19 điều khiển, cây truy cập): 11/29 tổ hợp dưới ngưỡng — 10 chữ gợi ý (placeholder mặc định Chrome #757575 trên #1e293b) 3,17:1 và nút Export (chữ trắng trên #16a34a) 3,30:1; các tổ hợp còn lại đạt (vd #94a3b8 trên #0f172a 6,96:1; Refresh 5,17:1; nến #dc2626 trên nền 3,70:1 ≥ 3). 8 ô tham số cao 22 px (< 24; có khả năng được ngoại lệ khoảng cách, cần xét). Không có `<meta name="viewport">`, `<main>`, heading, vùng live (`#stats`, `#error` không được thông báo), mô tả văn bản cho 14 canvas; 2 nút thiếu `type`. Đạt: `lang`, `<title>`, mọi điều khiển có tên truy cập được (0/18 thiếu), thứ tự Tab hợp lý (21 lần Tab: đi qua 18 điều khiển rồi tới liên kết logo của thư viện chart), vòng focus mặc định (`outline: auto 1px`). Chart **không** vào được bằng bàn phím → xem chi tiết tín hiệu chỉ bằng chuột. — `tái-lập`
- **F112** Chrome headless ở máy này luôn trễ ~25 s ở lần điều hướng đầu tiên (cả với trang tối giản `/health`, cả khi tắt kết nối nền) — KHÔNG phải lỗi dashboard; dashboard thật: HTML 194 KB về trong vài chục ms, quét đầu ~0,26–0,28 s (mở trang → có số liệu ≈ 0,4 s). — `tái-lập`
- **F113** INP chỉ đo đến lần vẽ kế tiếp sau thao tác, không chờ dữ liệu bất đồng bộ → với dashboard này INP nhiều khả năng ổn; vấn đề thật là **độ trễ cảm nhận** (0,25–1,2 s không có chỉ báo "đang tải", vượt mốc 1 s của Nielsen khi bật trend) — `suy-luận` — S042, S046, S049, F108–F109.

## 4b. T004 — Gói C (vector hoá `payload.py` + nén gzip), đo sau khi làm (`tái-lập`)

Điều kiện: 2026-10-15, cùng máy với T003; SQL DP6 chỉ SELECT; bản "trước" = `payload.py` cũ chép nguyên từng byte (vòng `iterrows`), bản "sau" = mã đã sửa, hai bản chạy **xen kẽ từng lượt trong cùng một tiến trình** (thay 8 tên hàm payload trong `server` rồi gọi `run_scan` / test client) để cùng điều kiện máy; mỗi ô là trung vị của 3 chiến lược (combo H1, ma_cross M30, macd_break H1, US30) × trend tắt/bật. Script ở scratchpad phiên làm việc (tạm, **không lưu trong repo**): so hàm bằng khung ngẫu nhiên và so JSON chuẩn; so phản hồi HTTP từng byte; đo nén bằng `gzip.compress` riêng; kiểm Chrome qua CDP đọc Resource Timing (`encodedBodySize` / `decodedBodySize`). Bản lặp cũ để test nằm trong repo: `we_core/tests/payload_reference.py`.

- **F114** Đầu ra không đổi: fuzz 16.500 phép gọi (11 hàm × 1.500 khung ngẫu nhiên: nhiều kiểu `bartime` và kiểu số, NaN/None/inf, giá trị sát nửa, index lạ/trùng, thiếu cột, rỗng) → 0 khác; 292 ca `run_scan` trên dữ liệu SQL đã lưu (300–4.000 nến, có/không trend, tải lịch sử, khoảng thời gian) → 0 khác; 24 ca trên SQL sống → phản hồi HTTP giống từng byte. Chỗ khác duy nhất: cột OHLC kiểu object chứa `None` — bản cũ ném `TypeError`, bản mới trả NaN (231/16.500 lần, chỉ `candlestick_points`; SQL thật không có). — `tái-lập`
- **F115** Thời gian `/api/scan` cả request, trước → sau: 500 nến 334 → 167 ms; 2.000 nến 949 → 263 ms; 5.000 nến 2.092 → 428 ms; 20.000 nến (tối đa) 7.867 → 1.066 ms. Riêng khâu dựng payload: 180 → 14 ms; 716 → 30 ms; 1.829 → 64 ms; 7.012 → 200 ms. Tính riêng phần xử lý (dữ liệu đã lưu, không SQL): 300 nến gấp 4,7×; 500 nến 6,7×; 2.000 nến 14×; 4.000 nến 18×. Ở 500–2.000 nến phần còn lại chủ yếu là SQL (54–215 ms, F108). — `tái-lập`
- **F116** Nén gzip mức 5: JSON 95 KB → 16 KB (500 nến), 486 KB → 81 KB (2.000 nến, bật trend), 4,9 MB → 0,78 MB (20.000 nến), trang chủ 194 KB → 59 KB; CPU nén ~1,3 ms / 7,4 ms / 74 ms (500 / 2.000 / 20.000 nến). Mức 9 chậm gấp 4–5 lần mà chỉ nhỉnh ~3%. Chrome thật nhận bản nén và giải nén đúng (trang chủ 59/194 KB, `/api/scan` 19/91 KB). Cùng máy (127.0.0.1) không thấy khác biệt thời gian — chỉ có lợi khi mở qua mạng/đường hầm. — `tái-lập`
- **F117** `round()` của Python khác `np.round` ở ca sát nửa (2.675 → 2.67 so với 2.68): vector hoá phải giữ `round()` trên từng giá trị (`ndarray.tolist()` rồi `round`). Bộ fuzz bắt được lỗi này khi cố ý bẻ thử. — `tái-lập`
- **F118** Màu histogram và màu đường trend phải xét trên giá trị ĐÃ làm tròn 2 chữ số (−0,004 → −0,0 vẫn ≥ 0 nên xanh; hướng 0,999 → 1,0 tính là hướng 1; NaN ra xám). Bộ fuzz bắt được lỗi khi tô theo giá trị thô. — `tái-lập`
- **F119** Chưa làm trong Gói C (vì phải sửa backend hoặc là lỗi phía JS): KNN còn tính hai lần mỗi request có trend (≤ 13 ms); số raw → filtered vẫn sai sau khi gộp lịch sử (F110); các mục frontend ở §5 mục 1–9. — `tái-lập` + `suy-luận`

## 5. Đề xuất (`suy-luận`, chưa làm) — chỉ trong `signal_display/`, không động tới backend

Mục tiêu đề xuất (suy từ các chuẩn): phản hồi thị giác ≤ 100 ms sau thao tác; có dữ liệu ≤ 1 s cho ≤ 2000 nến (hiện 0,64–1,09 s) và có chỉ báo nếu > 1 s; không tác vụ > 50 ms khi tương tác; ≥ 55 fps khi kéo/zoom (hiện 60); 1 request mỗi thao tác của người dùng; tự làm mới không đổi vùng nhìn của người dùng.

Phần **frontend thuần** (`live_page.py`: HTML/CSS/JS):
1. **Chỉ báo "đang tải" ngay khi bấm** (dòng trạng thái, `aria-busy`, khoá nút Refresh, con trỏ progress) — S046, S049, S048#1; bằng chứng F109. Nhỏ.
2. **Huỷ request cũ + gộp thay đổi liên tiếp** (`AbortController`, debounce ngắn cho đổi lựa chọn, không debounce nút Refresh) — S052, S044; hiện server vẫn tính request cũ và `_DB_LOCK` xếp hàng chúng. Nhỏ.
3. **Chặn chuỗi tải lịch sử không do người dùng kéo** (cờ khi đang đồng bộ/ vẽ lại; chỉ tải khi người dùng kéo; giữ cờ chống chồng) — S050; bằng chứng F109. Nhỏ–vừa; rủi ro: đổi hành vi tải lịch sử.
4. **Sửa số "raw → filtered"**: cộng dồn `rawTotal/rawBuy/rawSell` khi gộp các đợt lịch sử (các đợt không chồng lên nhau; `rows` đã gộp nên số "filtered" đúng) — F110. Nhỏ, nên làm đầu tiên vì đang hiển thị sai.
5. **Giữ zoom/pan khi Auto refresh** (truyền vùng nhìn hiện tại vào `renderChart`, hàm đã hỗ trợ tham số này) — S048#3; đọc từ mã: `scan()` luôn `renderChart(…, null)` → `fitContent()`. Nhỏ; chưa đo.
6. **Bộ nhớ đệm phía client theo khoá tham số** (vài mục, ngắn hạn; hiện bản cũ rồi cập nhật nền — "stale-while-revalidate") để bật/tắt trend hoặc quay lại cấu hình trước thành tức thì — S049, S047. Vừa; phải ghi nhãn dữ liệu đang cũ.
7. **Truy cập được (WCAG 2.2 AA + Chartability)**: chữ gợi ý `#94a3b8` (5,71:1 trên #1e293b); nút Export nền `#15803d` (5,02:1) hoặc `#166534` (7,13:1); `aria-live="polite"` cho `#stats` và `role="alert"` cho `#error` (vùng có sẵn trong HTML); `<meta name="viewport">`; `<main>`; `type="button"`; `role="img"` + `aria-label` tóm tắt cho vùng chart; **bảng tín hiệu có thể focus bằng bàn phím** làm bản thay thế (dữ liệu `signals` đã có sẵn trong payload); `min-height: 24px` cho ô tham số. — S053–S057. Nhỏ–vừa.
8. **Cấu hình chart**: `autoSize: true` thay cho `window.resize` + chiều cao cố định (container đặt chiều cao bằng CSS); bớt nhãn marker chen chúc (giữ mũi tên, bỏ chữ khi dày); không chỉ dựa vào đỏ/xanh cho nến (vd nến tăng rỗng hoặc bảng màu thay thế) — S051, S057; BUY/SELL đã có mũi tên + chữ nên đạt. Nhỏ. Không dùng conflation (không có ở v4.2.1, F099).
9. **Cấu trúc mã**: tách CSS/JS/HTML thành file thật trong `signal_display/` và để `render_dashboard_html` nhúng như đang làm với vendor JS (trang vẫn tự chứa, không đổi server); gom trạng thái; bỏ hàm trùng (`trendRangeMarker` và `entryRangeMarker` giống hệt) — S058. Vừa; phải cập nhật test kiểm HTML.

Phần **nằm trong nhóm file nhưng là Python phía server** (`payload.py`, `server.py`) — chỉ làm nếu người dùng coi chúng không thuộc "backend":
10. Vector hoá dựng payload (ước tính cắt 60–90% thời gian server — CHƯA đo); tính KNN một lần thay vì hai; gom `stats` theo hàng đã gộp ở phía server; nén gzip và/hoặc định dạng cột. Đây là chỗ cải thiện lớn nhất về tốc độ nạp (F108); frontend thuần không thay được. **→ ĐÃ LÀM 2026-10-15 ("Gói C", §4b): vector hoá payload (ước tính được xác nhận: khâu dựng payload cắt 92–97%, cả request nhanh 2–7×) và nén gzip; CHƯA làm: KNN một lần (phải sửa `configuration.py`, ≤ 13 ms) và gộp `stats` phía server (xem F110/mục 4).**

## 6. Hạn chế của ghi chú này
- W3C (`w3.org`) trả HTTP 403 cho công cụ tải trang: WCAG 2.2 đọc qua MDN (S054–S056); chi tiết kỹ thuật 4.1.3 và nội dung tiêu chí 1.4.1 chỉ có từ tóm tắt tìm kiếm (thứ cấp); MDN có thể chậm hơn W3C.
- Chartability: chỉ đọc trang chủ (nguyên tắc), chưa đọc workbook; "50 test / 14 critical" là nguồn thứ cấp.
- Docs Lightweight Charts đọc bản mới; chỉ các tuỳ chọn đã grep thấy trong vendor v4.2.1 (`autoSize`, `ResizeObserver`, `fixLeftEdge`, `shiftVisibleRangeOnNewBar`, `lockVisibleTimeRangeOnResize`, `kineticScroll`, `barsInLogicalRange`) được coi là dùng được; chưa chạy thử từng tuỳ chọn.
- Doherty qua trang tổng hợp (lawsofux), chưa đọc bài gốc 1982. "AbortError khi fetch bị huỷ" là kiến thức chung, MDN không viết rõ.
- INP là chỉ số thực địa (phân vị 75 lượt tải thật); ở đây chỉ dùng số đo phòng thí nghiệm cục bộ làm thước đo thay thế. Chrome headless dùng render phần mềm; chưa thử trên máy có GPU, màn hình/độ phân giải khác, hay công cụ đọc màn hình thật.
- Số đo T003 là một máy, một cặp (US30 H1 Combo) cho phần trình duyệt; phần server đo 3 chiến lược nhưng cũng chỉ US30. Dữ liệu SQL đổi theo thời gian (ví dụ ở cùng "600 nến" tải một lần ra 26 tín hiệu còn gộp hai đợt ra 29; nghi do bộ lọc lặp cùng chiều chỉ nhớ trong cửa sổ từng đợt, chưa kiểm chứng).
- Các đề xuất ở §5 là suy luận, chưa thử nghiệm; mức "nhỏ/vừa" là ước lượng công sức.

## 7. Vòng 2 (2026-10-17) — tối ưu cho CẢ dashboard `we_core` (:8517) và chart `des` (:8518)

Câu hỏi của người dùng: tìm tài liệu kỹ thuật/nghiên cứu về tối ưu dashboard dạng đang dùng, rồi đối chiếu để dashboard của cả WE và DES linh hoạt, tương tác nhanh và tối ưu hơn. **Chỉ tìm hiểu, chưa triển khai.** Phạm vi vòng 2 rộng hơn vòng 1: có tính cả chuyện truyền dữ liệu và chart `des`, không chỉ `signal_display/`. Nhãn như đầu file. Tìm "vừa đủ": 4 truy vấn, mở trực tiếp 6 trang gốc.

### 7.1. Nguồn mới (ID tiếp từ S062)

| ID | Nguồn | Loại | Ghi chú |
|---|---|---|---|
| S063 | Lightweight Charts docs `ISeriesApi` (`update`, `setData`) — tradingview.github.io/lightweight-charts/docs/api/interfaces/ISeriesApi | tài liệu chính thức (bản mới) | ✔ |
| S064 | Lightweight Charts demo *Realtime updates* — tradingview.github.io/lightweight-charts/tutorials/demos/realtime-updates | tài liệu chính thức | ✔ mã demo |
| S065 | Van Der Donckt và cộng sự, *Data Point Selection for Line Chart Visualization* (arXiv:2304.00900, 2023) | bài báo | ✔ bản ar5iv, qua tóm tắt của công cụ (có trích ngắn) |
| S066 | MDN *Using server-sent events* | tài liệu chính thức | ✔ |
| S067 | MDN *Page Visibility API* | tài liệu chính thức | ✔ |
| S068 | Lightweight Charts docs `TimeScaleOptions` bản 5.2 (conflation) | tài liệu chính thức | ✔ (vendor của hệ là v4.2.1) |
| S069 | Blog tổng hợp SSE vs WebSocket vs polling (oneuptime, back4app, codemia, openreplay) | blog, thứ cấp | CHỈ tóm tắt tìm kiếm, không mở; không có số đo Flask |
| S070 | apidog *API Caching with ETag and Cache-Control* và các trang về yêu cầu có điều kiện | blog, thứ cấp | CHỈ tóm tắt tìm kiếm |

Tìm "Lightweight Charts `update` vs `setData` hiệu năng" **không ra** benchmark hay hướng dẫn chính thức nào (kết quả toàn là Highcharts/ApexCharts), nên không có số so sánh từ nguồn.

### 7.2. Các chuẩn/tài liệu nói gì

- **F161 (S063, S064 ✔)** `update()`: "Adds new data item to the existing set (or updates the latest item if times of the passed/latest items are equal)"; thời gian mục mới phải ≥ mục cuối. `historicalUpdate: true` mới cho sửa nến không phải nến cuối, và "will be slower than updating the most recent data point". `setData()`: "Sets or replaces series data… Old data is fully replaced". Demo chính thức: nạp lịch sử MỘT lần bằng `setData`, rồi mỗi tick gọi `series.update(...)` (nến mới hoặc sửa nến cuối); nút "Go to realtime" gọi `timeScale().scrollToRealTime()`. — `nguồn-báo-cáo`
- **F162 (S065 ✔)** Nghiên cứu về chọn điểm khi giảm mẫu cho **biểu đồ đường** (EveryNth, MinMax, LTTB, M4; 3 thư viện Python): MinMax và LTTB tiết kiệm dữ liệu nhất cho độ giống hình; MinMax ổn định thị giác nhất; "M4 can never be pixel-perfect in the presence of anti-aliasing". Khái niệm *visual stability*: hình thay đổi bao nhiêu khi cập nhật liên tục hoặc khi kéo/zoom. Bài **không** nói về nến (OHLC) hay Lightweight Charts. Tìm kiếm thứ cấp (S069-nhóm) cho biết một thư viện chỉ giảm mẫu chuỗi đường > 2.000 điểm và bỏ qua biểu đồ cột vì mỗi cột có nghĩa riêng. — `nguồn-báo-cáo`
- **F163 (S066 ✔)** SSE: trên HTTP/1.1 giới hạn "very low number (6)" kết nối mỗi trình duyệt + miền (dùng chung giữa các tab); HTTP/2 mặc định 100 luồng; trình duyệt tự nối lại và có `retry`; định dạng văn bản UTF-8, dòng `data:` / `id:` / `event:`. MDN **không** nhắc `Last-Event-ID` ở trang này. Nguồn thứ cấp (S069): polling trễ trung bình ≈ nửa chu kỳ + một vòng gọi; SSE hợp với dashboard chỉ đẩy một chiều; Redis Pub/Sub là "bắn rồi quên" nên phải bù khi nối lại; cấu hình proxy/luồng của Flask ảnh hưởng độ trễ nhiều hơn chọn giao thức. — `nguồn-báo-cáo` (MDN) + thứ cấp
- **F164 (S067 ✔)** Page Visibility: dashboard "doesn't want to poll the server for updates when the page isn't visible"; `requestAnimationFrame` bị trình duyệt dừng ở tab ẩn; `setTimeout/setInterval` bị giảm tần suất (Chrome: sau 10 s ẩn, ngân sách hồi 10 ms/giây). — `nguồn-báo-cáo`
- **F165 (S070, thứ cấp)** Yêu cầu có điều kiện (`ETag` + `If-None-Match` → 304): tiết kiệm phần thân phản hồi và việc phân tích lại ở trình duyệt, **không** tiết kiệm công tính ETag ở server. — thứ cấp
- **F166 (S068 ✔, S051)** Docs bản 5.2 liệt kê `enableConflation`, `precomputeConflationOnInit`, `conflationThresholdFactor` (gộp nến khi khoảng cách nến rất nhỏ); vendor v4.2.1 không có (F099 đã grep). Docs không nói thêm từ phiên bản nào, và mức công sức/rủi ro nâng vendor lên v5 **chưa đọc, chưa kiểm**. — `nguồn-báo-cáo` + `lỗi-nguồn` (thiếu thông tin phiên bản)

### 7.3. Đối chiếu với hai trang thật (mã đọc 2026-10-17; số đo từ T003/T004 và lần đo `des` 17/10)

Đã biết từ số đo: kéo/zoom 60 fps, 0 khung > 50 ms, luồng chính nhàn rỗi 0–2% (`des`, Chrome headless); `/api/scan` 500 nến ≈ 167 ms (we_core, sau Gói C); nhịp nhẹ của `des` 4–6 ms / 159 byte; `/api/view` 55–125 ms, `/api/trend` 150–200 ms, trang `/trend` làm mới 195–244 ms so với 77 ms của trang chính. Phần "chậm" người dùng cảm thấy trên chart `des` **chưa tái hiện** được — nên khoảng cách dưới đây là so với tài liệu, chưa phải nút thắt đã đo.

- **F167 Cập nhật toàn bộ thay vì tăng dần.** `des`: `applyView` gọi `setData` cho nến + overlay + MACD mỗi lần có thay đổi (`live_page.py` 442–445, `trend_page.py` 523–544); we_core: mỗi lần quét gọi `renderChart` đầy đủ. Demo chính thức thì `setData` một lần rồi `update` từng nến. Lợi ích đo được **chưa biết** (không benchmark từ nguồn, chưa đo cục bộ; đo hiện tại cho thấy không có khung > 50 ms) — chỉ là khoảng cách so với mẫu chuẩn. `suy-luận`
- **F168 Cơ chế "chỉ gửi khi đổi" đã có ở `des`, chưa có ở we_core.** `des` đã gửi dấu vân tay (`head/nsig/pending/run`) và nhận `changed:false` — tương đương 304 ở tầng ứng dụng và đúng tinh thần F165. we_core quét lại từ đầu mỗi lần; nhưng Auto của we_core mặc định **Tắt** (tuỳ chọn 10/30/60 s) và dữ liệu SQL chỉ đổi theo đợt DF (6 lần/ngày, F160), nên với we_core Auto refresh vốn ít cần. `tái-lập` (mã) + `suy-luận`
- **F169 Không có `visibilitychange` ở bất kỳ trang nào** (grep 0 kết quả trên `live_page.py` của we_core và mọi file `chart_simul/`): tab ẩn vẫn gọi `/api/view` (hoặc `/api/trend`, `/api/mtf`) mỗi 0,5–5 s; Chrome tự giảm tần suất nhưng server vẫn nhận request. Khoảng cách so với F164 là có thật; tác hại thực tế nhỏ (nhịp nhẹ 4–6 ms) nhưng tăng theo số tab mở. `tái-lập` (mã)
- **F170 Không huỷ request cũ.** Cả hai chỉ bỏ kết quả cũ (`scanVersion` / `requestId`), không có `AbortController` (grep 0); `des` còn có cờ `loading` chặn chồng nhịp. Đúng như đề xuất §5 mục 2 của vòng 1, vẫn chưa làm. `tái-lập` (mã)
- **F171 Giảm mẫu (F162) ít áp dụng được ở đây:** dữ liệu là nến (mất OHLC nếu giảm), `des` tối đa 1.200 nến, we_core mặc định 500 (tối đa 20.000 → JSON 756 KB sau gzip). Đáng cân nhắc nhất là **bỏ qua** và dùng cách sẵn có: tải ít nến theo mặc định + tải thêm lịch sử khi kéo (đã có). `suy-luận`
- **F172 Đẩy thay vì hỏi (SSE) có lợi nhỏ, tốn nhiều:** DFS phát một mốc mỗi 2 giây thật; polling 1 s thêm trễ trung bình ~0,5 s (công thức F163, thứ cấp). SSE cần server giữ luồng/hoặc nghe keyspace của Redis (Flask `threaded=True` giữ một luồng mỗi kết nối), giới hạn 6 kết nối/miền ở HTTP/1.1 khi mở nhiều tab (các trang `des` mở 3 loại). Hiện nhịp nhẹ đã rất rẻ. `suy-luận`
- **F173 `/trend` chậm hơn trang chính (195–244 ms so với 77 ms)** do đọc thêm chuỗi nến trend và tính KNN hai lần (đã ghi 17/10); cách giảm (KNN một lần, ước 50–80 ms) vẫn chưa làm. `tái-lập` + `suy-luận`

### 7.4. Khuyến nghị xếp theo giá trị/công sức (`suy-luận`, CHƯA làm, chờ người dùng quyết)

1. **Hỏi người dùng đang thấy "chậm" ở đâu** (thao tác nào, trang nào, xem từ xa hay tại máy): số đo không tái hiện, nên mọi mục dưới đây chưa chắc là thứ họ cảm nhận. Không tối ưu mò.
2. Nhỏ, rủi ro thấp, cả hai hệ: tạm dừng nhịp hỏi khi tab ẩn + hỏi ngay một lần khi quay lại (F169); `AbortController` cho request cũ (F170); chỉ báo "đang tải" và sửa số raw → filtered đã nêu ở vòng 1 (§5 mục 1, 4 — số raw → filtered vẫn sai).
3. `des`: tính KNN một lần trong `/api/trend` (F173); cập nhật tăng dần bằng `series.update` khi chỉ có nến mới (F167) — **làm sau khi đo** `setData` thực sự tốn bao nhiêu ms trên máy người dùng, vì đo hiện tại chưa cho thấy vấn đề.
4. we_core: giữ zoom khi Auto refresh (§5 mục 5), bộ nhớ đệm phía client (§5 mục 6); Auto mặc định Tắt là hợp lý.
5. Để sau/không nên: SSE/WebSocket (F172), giảm mẫu nến (F171), nâng vendor lên v5 để có conflation (F166 — chưa kiểm công sức, và với 500–1.200 nến không có gì để gộp).

### 7.5. Hạn chế của vòng 2
- Không tìm được benchmark/hướng dẫn chính thức nào so `update` với `setData` cho Lightweight Charts; F167 là so sánh với mẫu demo, không phải bằng chứng hiệu năng.
- Nghiên cứu giảm mẫu (S065) đọc qua bản tóm tắt của công cụ (có trích ngắn), chỉ cho biểu đồ đường.
- S069/S070 là blog thứ cấp, chỉ đọc tóm tắt tìm kiếm; không có số đo Flask/SSE trên máy này.
- Docs Lightweight Charts đọc bản 5.2; vendor là v4.2.1: chưa chạy thử `update` trên vendor (mã `update` có từ lâu nhưng chưa grep lại trong lần này).
- Khoảng cách F167–F170 dựa trên đọc mã + grep, không đo; trang `des` chỉ được đo bằng Chrome headless render phần mềm.
