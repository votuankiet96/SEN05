# S007 — deepentropy/tvscreener (thư viện Python không chính thức truy vấn "screener" của TradingView) + Điều khoản sử dụng của TradingView

Trạng thái nạp: đã đọc README đầy đủ, toàn bộ lõi thư viện (`core/base.py`, `util.py`, `filter.py`, `core/stock.py`, `exceptions.py`, `__init__.py`, `ta/__init__.py`, `news.py`), phần đầu `field/__init__.py`, phần đầu `mcp/tools.py` + danh sách tool của `mcp/server.py`, vài trang docs, changelog, thư mục `gap_assessment/`, `.dev/codegen/generate.py`, các issue #54/#57/#51/#59, siêu dữ liệu PyPI, và **văn bản Điều khoản của TradingView** (nguồn gốc, đọc nguyên văn). Đã chạy unit test của thư viện và các phép đếm/tái lập offline (mạng bị chặn bằng proxy chết). **Không gửi bất kỳ yêu cầu nào tới endpoint dữ liệu của TradingView** (`scanner.tradingview.com`, `news-mediator.tradingview.com`) — lý do ở mục 5. Đối chiếu với S001–S006 và bộ nhớ ở mục 11.

Nhãn trạng thái: `nguồn-báo-cáo` · `tái-lập` · `lỗi-nguồn` · `suy-luận` (định nghĩa ở `INDEX.md`). Riêng mục 5 là **trích nguyên văn từ văn bản gốc của nhà cung cấp** (không phải báo cáo thứ cấp); việc nó áp dụng cho hệ nào là câu hỏi riêng.

## 1. Nguồn

- Loại: thư viện mã nguồn mở **không chính thức** ("unofficial, third-party library … not affiliated with, endorsed by, or connected to TradingView"), tác giả "DeepEntropy"; danh sách người đóng góp có tài khoản `claude` (14 commit) và `Copilot` (10 commit); commit mới nhất có dòng "Files changed during an Atelier chat response." (suy-luận: commit sinh trong phiên làm việc với trợ lý AI). Không bình duyệt, không có bài báo.
- Repo: https://github.com/deepentropy/tvscreener · mô tả "TradingView Screener API - Stock, Crypto, Forex, Bond, Futures, Coin" · tạo 2023-08-02 · pushed 2026-09-29T08:02:38Z · commit đọc `6faf496fa8b2af205eb485784412a48222986e96` (2026-09-29T08:02:35Z) · 1 576 sao / 219 fork / 18 người theo dõi · 2 mục mở (issue #53 "Add options data support (greeks, IV, volatility curves)" và PR #59) · 3 659 KB · chủ đề quantitative-finance, technical-analysis, tradingview. Người đóng góp: deepentropy 88, claude 14, Copilot 10, scherer33 1, robin-watcha 1 (số commit).
- Phát hành: GitHub 8 release (v0.1.0 2025-12-08 → v0.5.2 2026-09-23); **PyPI 18 phiên bản** (0.0.14 2025-10-20 → 0.5.2 2026-09-23 13:04 UTC; 0.4.0 lên PyPI 2026-07-13, 0.4.1 2026-09-08). Ngày "published" của release GitHub v0.4.0 (2026-09-08) muộn hơn ngày lên PyPI (2026-07-13) → ngày release GitHub không dùng được để suy ra thời điểm người dùng có phiên bản đó. Trạng thái khai báo: "Development Status :: 4 - Beta"; `requires-python >=3.10`; phụ thuộc `pandas>=1.3.0`, `requests>=2.27.1`; tuỳ chọn `mcp[cli]>=1.0.0`, `tabulate`.
- Giấy phép — **mâu thuẫn trong chính dự án** (`lỗi-nguồn`): tệp `LICENSE` là **Apache License 2.0** (201 dòng; GitHub API cũng trả `apache-2.0`), nhưng `pyproject.toml` khai `license = "MIT"` và PyPI hiển thị `license_expression: 'MIT'` (trường `license` rỗng, không có classifier License). Cả hai đều dễ dãi; không đánh giá pháp lý.
- Ngày tải: 2026-10-03 (ghi chú viết 2026-10-04). Tệp và sha256:

| Tệp | Dòng / byte | sha256 |
|---|---|---|
| tarball repo @6faf496 | 1 331 469 byte | `f274daa0cae6105279d6164f83070a9304ec030e8e455120e5f7610b28836da4` |
| README.md | 363 / 11 564 | `f42621d1b15df1032337e33024b1ed875033414926d88a4f0c7bfdc3c8762299` |
| pyproject.toml | 59 | `97544e76720e4c9498fd27d1c14e32025d73db9bb31b1aa22d10db1093d38c56` |
| LICENSE | 201 | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
| PyPI JSON `tvscreener` | – | `6c5dae4908bbd44d354f47097393935b313376a59892bc93322297af0790ee40` |
| Issue #54 (JSON) | – | `0afadcacbeae06fc565770676e344dd592f9fe1cc854e004bdbb6e5435b46da6` |
| Issue #57 (JSON) | – | `9eaec635985916fb3c9b484cb380d9325b777081b91ddb862ac7b1dd07252c92` |
| Trang điều khoản TradingView (HTML, 225 633 byte) https://www.tradingview.com/policies/ | – | `fae0be7b031caab894583a82a16fa724937b0064cc5db273dfe9453ee7136dc4` |
| Văn bản thuần trích từ trang đó (62 738 ký tự) | – | `f9baee4d1365e568ee7b2fba14e8f559919170a9d2a1429fcfbe069070919ca5` |

- **Chưa đọc**: ~25 trang docs còn lại, 5 notebook, `app/` (web "Code Generator"), `beauty.py` (253 dòng), `field/presets.py` (333), phần lớn `field/__init__.py` (1035 dòng) và `mcp/` ngoài đầu `tools.py`, nội dung 6 tệp enum trường (chỉ **đếm bằng chương trình**, không đọc từng dòng), `tests/functional/` (chỉ đếm hàm test; không chạy vì gọi API thật), các issue/PR cũ.
- Trang điều khoản TradingView không ghi ngày cập nhật (tìm "Last updated/Effective" không thấy). Đã thử thêm hai URL đoán (`/terms-of-service/`, `/policies/terms-of-use/`) → HTTP 404; chỉ dùng `/policies/` (HTTP 200).

## 2. Thư viện làm gì (đọc mã)

- Là **lớp bọc rất mỏng** quanh một endpoint không có tài liệu công khai của TradingView: `POST https://scanner.tradingview.com/{global|bond|coin|futures|<subtype crypto>|<subtype forex>}/scan` (`util.get_url`; `StockScreener` dùng `global`). Gói `tvscreener/` có 17 584 dòng `.py`, trong đó 14 121 dòng nằm ở 6 tệp enum trường **sinh tự động** (`field/stock|crypto|forex|coin|futures|bond.py`); phần còn lại ≈ 3 460 dòng (gồm `field/__init__.py` 1 035, MCP ≈ 717, `presets.py` 333, `core/base.py` 400, `beauty.py` 253, …).
- Payload (`Screener._build_payload`): `filter` (danh sách `{left, operation, right}`), `options` (`{"lang": "en"}`), `symbols` (mặc định `{"query": {"types": []}, "tickers": []}` hoặc `{"symbolset": [...]}` khi `set_index`), `sort` (`{sortBy, sortOrder}`), `range` (mặc định `[0, 150]`), `columns`, và `markets` (chỉ stock; mặc định `America`).
- Tiêu đề HTTP **giả trình duyệt** (`REQUEST_HEADERS`, `core/base.py` dòng 28–33): `Content-Type: application/json`, `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/120.0.0.0 Safari/537.36`, `Origin: https://www.tradingview.com`, `Referer: https://www.tradingview.com/`. `news.py` dùng cùng bộ tiêu đề cho `https://news-mediator.tradingview.com/public/news-flow/v2/news` (với `user_prostatus=non_pro`) và còn **tải HTML bài viết rồi tách thân bài bằng regex** (`get_article`, fragile).
- Phản hồi: `data = [[d["s"]] + d["d"] for d in response.json()['data']]` → `ScreenerDataFrame`; cột đầu là mã có tiền tố sàn (`"s"`); nhãn cột lấy từ nhãn trường; nhãn trùng được thêm hậu tố `.1`, `.2` (`_uniquify`).
- Mỗi lần gọi tự thêm cột `update_mode`; bỏ cột bắt đầu bằng `candlestick`; thêm cột `Rec.<trường>` cho trường có `format == 'recommendation'`; thêm cột `<trường>[1]|…` ("Prev.") cho trường `historical`.
- Lỗi: hết 30 giây → `MalformedRequestException(408, …)`; lỗi `requests` khác → mã 0; HTTP không 2xx → `MalformedRequestException(status, text, url, payload)` (thông điệp lỗi chứa nguyên payload). **Không có retry/backoff**, không xử lý riêng HTTP 429.
- `stream(interval≥1.0 giây)`: lặp `get()`; **nuốt mọi `Exception`**, in `"Error fetching data: …"` rồi `yield None` (docstring có nói; README thì không). Ví dụ README `for df in ss.stream(...): print(f"Got {len(df)} rows")` sẽ ném `TypeError` khi một lần lấy dữ liệu lỗi.
- Dữ liệu trả về là **ảnh chụp giá trị hiện tại** của từng mã (và giá trị của thanh liền trước cho trường có `[n]`), **không phải lịch sử OHLCV**. Chính `gap_assessment.md` của repo ghi dòng "History | Current values only".
- Quy ước đặt tên trường: `trường|khung` (ví dụ `RSI|60`); độ lùi `trường[n]|khung`. Changelog 0.4.1: dạng sai `RSI|1W[1]` bị **TradingView trả `null` chứ không báo lỗi**; dạng đúng là `RSI[1]|1W` → lỗi cú pháp tên trường biến thành dữ liệu rỗng im lặng.
- Máy chủ MCP (`tvscreener-mcp`): 9 tool (`discover_fields`, `list_field_types`, `custom_query`, `search_stocks`, `search_crypto`, `search_forex`, `get_top_movers`, `list_sectors`, `list_filter_operators`); README chỉ liệt kê 6 tên (lệch nhẹ tài liệu).

## 3. Danh mục trường — đếm bằng chương trình (`tái-lập`)

Nhập gói từ mã nguồn (không gọi mạng khi import) và đếm thành viên enum:

| Enum | Thành viên | `interval=True` | `historical=True` | `recommendation` |
|---|---:|---:|---:|---:|
| StockField | 3 526 | 2 518 | 9 | 4 |
| ForexField | 2 965 | 2 662 | 829 | 3 |
| CryptoField | 3 108 | 2 673 | 839 | 4 |
| BondField | 201 | 39 | 1 | 0 |
| FuturesField | 393 | 309 | 84 | 0 |
| CoinField | 3 026 | 2 674 | 841 | 0 |
| **Tổng** | **13 219** | | | |

- README "13,000+ fields across all screener types" → khớp (13 219). "Search 3500+ available fields" (MCP) → khớp riêng StockField (3 526), không mâu thuẫn. Chú thích "default 424 fields" trong `core/stock.py` → khớp (`len(DEFAULT_STOCK_FIELDS) == 424`). `Market` có 66 thành viên, `IndexSymbol` 50, `Country` 78, `Exchange` 4.
- **Hậu tố khung thời gian thật sự có trong tên trường** (quét mọi enum): `1`, `5`, `15`, `30`, `60`, `120`, `240`, `1W`, `1M` (9 mã; 1 123 trường mỗi mã, riêng `5` có 1 140); không hậu tố = khung ngày ("By default, technical indicators use daily data" — `docs/guide/time-intervals.md`). Không có 10/20/45 phút, không có 180 phút (H3).
- README viết "**Any** time interval (… 1D, 5m, 1h, etc.)" nhưng cùng README liệt kê đúng 10 giá trị `1, 5, 15, 30, 60, 120, 240, 1D, 1W, 1M` → "any" là nói quá (`lỗi-nguồn` nhẹ).
- Tham số chỉ báo của các trường liên quan tới chỉ báo mà OG tự tính (nhãn đọc từ enum): **ATR = "Average True Range (14)"** (+ `ATRP`); **MACD = "MACD Level (12, 26)" / "MACD Signal (12, 26)"** (+ `MACD.hist`); RSI(14); SMA và EMA có sẵn nhiều chu kỳ, gồm 2, 3, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 20, 21, 25, 26, 30, 34, 40, 50, 55, 60, 75, 89, 100, 120, 144, 150, 200, 250, 300. **Không có MACD(5,25,5) hay ATR(5)** — hai tham số mà OG dùng.
- Nguồn gốc danh mục: `.dev/codegen/generate.py` (410 dòng) dùng **Selenium (Firefox) + BeautifulSoup + XPath tuyệt đối** (ví dụ `"/html/body/div[4]/div/div[2]/div[11]"`) để cào giao diện web screener của TradingView, rồi sinh tệp enum từ mẫu Jinja; cờ `interval` lấy từ `data/time_intervals.json`, cờ `historical` từ danh sách viết tay `historical_fields`. Dùng cú pháp `find_element_by_xpath` (API cũ của Selenium; theo hiểu biết của tôi đã bị gỡ ở Selenium 4.3 → script khó chạy với Selenium hiện hành, `suy-luận`, chưa thử). Tức danh mục trường là **ảnh chụp tại thời điểm cào**; thư viện không có cơ chế phát hiện TradingView đổi tên/bỏ trường (kết quả là `null` im lặng).

## 4. Khẳng định trong README/docs/issue và trạng thái

| # | Khẳng định | Trạng thái | Ghi chú |
|---|---|---|---|
| 1 | 13 000+ trường; 3 500+ trường cho stock; 424 trường mặc định | `tái-lập` | mục 3 |
| 2 | "Any time interval (no need to be a registered user)" | một phần: số khung = 9 + ngày; phần "không cần đăng ký" `nguồn-báo-cáo` (thư viện không gửi thông tin đăng nhập; tôi **không** gọi endpoint để kiểm) | mục 3 |
| 3 | "Streaming / Auto-update … real-time market data" | `lỗi-nguồn` nhẹ: là thăm dò lặp ≥1 giây, không phải luồng; maintainer tự nói endpoint REST "isn't the right surface" cho luồng tick đảm bảo thời gian thực (issue #54) | mục 2, 7 |
| 4 | "Type-safe validation — catches field/screener mismatches" | đúng theo mã (`_validate_field_type` ném `TypeError`) | `core/base.py` 151–177 |
| 5 | Disclaimer: "publicly available data from TradingView's screener … subject to TradingView's terms of service" | tác giả tự mô tả; điều khoản gốc xem mục 5 | |
| 6 | "Full suite: 151 tests and 2 subtests passed" (maintainer, issue #57, 2026-09-15) | không tái lập y nguyên; **unit offline: 135 passed, 1 skipped** (thiếu extra `mcp`); 17 hàm test functional gọi API thật, không chạy | mục 8 |

## 5. Điều khoản sử dụng của TradingView — văn bản gốc (đọc nguyên văn ngày 2026-10-03)

Nguồn: https://www.tradingview.com/policies/ ("Terms of Use, Policies, and Disclaimers"; tiêu đề trang "Terms of Service and Company Policy — TradingView"). Các mục: 1 "Changes to the terms of use"; 2 "Changes to TradingView"; **3 "Ownership of information; license to use TradingView; redistribution of data; non-display usage"**. Trích nguyên văn những câu then chốt:

- Mục 2: "We may discontinue or change any service or feature on TradingView at any time without notice. We do not guarantee backward compatibility of our services and Application Programming Interface (API) in this case."
- Mục 3: "The content and market data provided on the TradingView platform, including but not limited to charts, alerts, webhooks, and any other forms of information, are licensed for exclusive display-only use. This license is strictly limited to personal or internal business purposes and explicitly prohibits any form of non-display usage."
- Mục 3: "Such prohibited uses include, but are not limited to, any form of automated trading, automated order generation, price referencing, order verification, algorithmic decision-making, algorithmic trading, smart order routing, using data in operations control or risk management programs, or any machine-driven processes that do not involve the direct, human-readable display of such data."
- Mục 3: "Such prohibited cases also include creating products or services based on TradingView content, any processing of TradingView's content, or any other use cases that undermine the restrictions in place by the Data Providers."
- Mục 3: "… we expressly forbid direct non-display usage by our users, as well as the development, offering, or utilization of any third-party products, tools, or services designed to facilitate or enable such non-display usage of TradingView's content and market data."
- Mục 3: "Except as otherwise expressly permitted by separate agreement, we do not permit commercial usage of any of our services or APIs."
- Mục 3 (thực thi): "Consequences of such breaches include, but are not limited to, the blocking of the user or visitor, termination of their account, and potential legal penalties."

Điều cần ghi rõ:
- Đây là **văn bản của nhà cung cấp, đọc trực tiếp**; không phải diễn giải của tôi. Tôi **không đánh giá pháp lý** và **không biết** hệ thống DP→OG→OF được cấp phép theo cách nào: tôi chưa thấy đường lấy dữ liệu của DP (bộ nhớ ghi "DP lấy dữ liệu từ TradingView"; `DWH.Dim_Symbol.Symbol` là "mã TradingView"), cũng chưa thấy thỏa thuận riêng nào ("separate agreement") hay nguồn dữ liệu có phép khác. Câu hỏi "đường dữ liệu đó có nằm trong điều khoản mục 3 không" cần người nắm cấp phép của DP trả lời.
- Văn bản mục 3 liệt kê đích danh "algorithmic decision-making", "algorithmic trading", "automated order generation", "any processing of TradingView's content" và "commercial usage … of APIs" — tức các hoạt động nằm đúng chức năng của chuỗi dữ liệu → tín hiệu → lệnh. Vì thế tôi **không** dùng thư viện này để gọi `scanner.tradingview.com` (kể cả để kiểm chéo chỉ báo của OG): chỉ tải trang chính sách (HTTP GET công khai) và không đụng endpoint dữ liệu.

## 6. Chất lượng và độ tin cậy của thư viện

- **Lịch sử lỗi (changelog + issue), `nguồn-báo-cáo` đã đối chiếu mã**: 0.4.1 — `FieldWithInterval`/`FieldWithHistory` thiếu `has_recommendation()` nên mọi `select()` có bọc khung thời gian hoặc độ lùi đều ném `AttributeError` khi `get()` (báo 2026-08-09 trên 0.4.0 lên PyPI từ 2026-07-13; sửa ở 0.4.1, 2026-09-08 → ít nhất ~8 tuần API đa khung thời gian "được tài liệu hoá" không dùng được ở bản PyPI; các bản trước 0.4.0 chưa biết); độ lùi đặt sai thứ tự (`RSI|1W[1]` → TradingView trả `null`); `select_all()` lỗi `ValueError: N columns passed, passed data had N+1 columns` trên 5/6 screener; 0.5.0 — `Rating.find()` phân loại sai biên (0,5 ra Buy thay vì Strong Buy; 0,1 ra Neutral thay vì Buy; dải chồng lấn); 0.5.2 — `with_interval()` nhận cả trường đã có khung (`EMA12|5`) tạo cột sai `EMA12|5|60`.
- **Lỗi còn nguyên ở HEAD (tái lập)**: `StockField.with_history()` — được docstring mô tả là classmethod ("Get all fields that support historical lookback", ví dụ `StockField.with_history()`) — bị **che khuất** bởi phương thức thể hiện `with_history(self, periods=1)` định nghĩa sau trong cùng lớp, nên gọi như docstring ghi ném `TypeError: Field.with_history() missing 1 required positional argument: 'self'`. Test chỉ phủ dạng thể hiện (`.with_history(1)`).
- Hành vi nguy hiểm không nêu trong README: `stream()` trả `None` khi lỗi (tái lập: với mạng bị chặn trả `None`, in `Error fetching data: …`, và `len(None)` ném `TypeError`).
- Không retry/backoff; không xử lý 429; đặt thời gian chờ 30 giây.
- Chẩn đoán im lặng: tên trường sai cú pháp → `null`; trường bị TradingView đổi tên → `null` (suy luận từ cơ chế nêu ở changelog).
- PR #59 (đang mở; tài khoản `anupamme`; nội dung tự nhận "Automated security fix by OrbisAI Security"): coi việc `get()` không có giới hạn tốc độ phía client là lỗ hổng "HIGH". `suy-luận`: không phải lỗ hổng bảo mật của thư viện khách; PR chưa được xử lý.
- Test: `tests/unit` — **135 passed, 1 skipped** (tôi chạy offline với `HTTP(S)_PROXY=http://127.0.0.1:9` để không thể chạm TradingView; skipped vì thiếu extra `mcp`). `tests/functional` (17 hàm) gọi API thật (ví dụ `ss.get()` rồi `assertEqual(150, len(df))`). Workflow `codecov.yml` chạy `pytest --cov=./` trên PR → kết quả CI phụ thuộc vào việc endpoint của TradingView còn sống (`suy-luận` từ tệp workflow + cấu trúc test).
- Giấy phép mâu thuẫn Apache-2.0 (LICENSE) / MIT (pyproject, PyPI) — mục 1.

## 7. Độ mới của dữ liệu (issue #54, đóng 2026-07-13)

- Người dùng báo (2026-05-27): cột `Update Mode` ra `delayed_streaming_900` cho cổ phiếu Mỹ, tưởng dữ liệu trễ 15 phút.
- Maintainer trả lời (cùng ngày): `update_mode` là "a static metadata/entitlement tag that TradingView returns for US equities to all anonymous scanner clients. It is **not** the actual delay". Họ đo trực tiếp lúc giờ giao dịch chính (≈19:32 UTC) bằng cách yêu cầu thêm `last_bar_update_time` rồi so với đồng hồ: AAOI trễ **40 giây**; thăm dò NVDA mỗi 30 giây cho độ trễ trong khoảng **9–40 giây**, trong khi `update_mode` vẫn báo `delayed_streaming_900`; mã gắn nhãn CBOE cũng báo `delayed_streaming_900` nhưng dữ liệu vẫn mới. Kết luận của họ: "The scanner is delivering near-real-time prices, not 15-minute-old ones." Và: "if you ever do need a guaranteed-real-time tick stream (not 1-minute snapshots), the scanner REST endpoint isn't the right surface anyway, you'd want TV's data websocket, or a different provider entirely."
- Trạng thái: `nguồn-báo-cáo` — **một ngày, hai mã, đo bởi maintainer**; tôi không kiểm (không gọi endpoint). README/docs không nói gì về độ trễ hay độ chính xác dữ liệu (tìm "delay/real-time" trong docs: chỉ có dòng "monitoring real-time market data" ở phần stream).
- Ý nghĩa thực dụng: nhãn `update_mode` không dùng để suy ra độ trễ; muốn biết phải so `last_bar_update_time` với đồng hồ.

## 8. Đã làm và kết quả

1. Đọc mã/tài liệu như mục 1 (phạm vi). 2. Nhập gói từ mã nguồn trong venv cô lập (scratch, **không** đụng `.venv` sản xuất), đếm trường (mục 3), tái lập lỗi `with_history()` và hành vi `stream()` → `None`. 3. Chạy `tests/unit` offline: 135 passed, 1 skipped. 4. Tải và đọc PyPI JSON, danh sách issue, 4 issue chi tiết. 5. Tải trang điều khoản TradingView (HTTP GET công khai) và trích nguyên văn (mục 5). 6. Lọc từ khoá trong docs/README tìm khẳng định về hiệu quả/backtest: **không có** (docs chỉ là ví dụ lọc; không nêu hiệu suất giao dịch).

## 9. Không kiểm được / cố ý không làm

- Không gọi `scanner.tradingview.com`/`news-mediator.tradingview.com` → không kiểm: độ trễ thật (mục 7), "không cần đăng ký", hành vi khi sai tên trường, giá trị chỉ báo so với OG.
- Chưa kiểm xem `generate.py` còn chạy được hay không; chưa đọc `beauty.py`, `presets.py`, phần còn lại của MCP; chưa chạy test functional.
- Không biết đường lấy dữ liệu và cấp phép của DP.

## 10. Đánh giá và mức dùng (đánh giá của tôi)

- **Không dùng làm nguồn dữ liệu cho OG**: chỉ có ảnh chụp giá trị hiện tại, không có lịch sử OHLCV; endpoint không có tài liệu; chính điều khoản của TradingView cho phép thay đổi API không báo trước và không bảo đảm tương thích ngược (mục 5); thư viện không retry và có lỗi lõi mới sửa gần đây.
- **Không dùng để kiểm chéo chỉ báo của OG**, kể cả về kỹ thuật: (1) điều khoản (mục 5); (2) khung thời gian: chỉ H1, H2, H4, M30 của OG nằm trong tập {1,5,15,30,60,120,240,1D,1W,1M}; M10, M20, M45 (ma_cross) và H3 (combo) không có; (3) tham số: không có MACD(5,25,5), ATR(5) — chỉ SMA13/20/34 và EMA13/34 là tương ứng được.
- Giá trị chính của nguồn này với og_program là **mục 5**: văn bản gốc về giới hạn sử dụng dữ liệu TradingView cho mục đích tự động hóa. Tôi ghi lại để người nắm cấp phép dữ liệu của DP đối chiếu; tôi không kết luận gì về hệ hiện hữu.
- Bài học kỹ thuật có thể dùng (tham khảo, không phải khuyến nghị): lỗi tên trường → dữ liệu rỗng im lặng (cùng họ với các "silent failure mode" đã ghi trong bộ nhớ `project_parse_audit_findings`); một thư viện bọc endpoint không tài liệu vẫn có thể sai ở tính năng lõi hàng tuần liền mà bản PyPI vẫn phát hành.

## 11. Đối chiếu với cái cũ

- Không trùng với S001–S006 (chưa có nguồn nào về TradingView). Không mâu thuẫn.
- Liên hệ bộ nhớ (ghi theo bộ nhớ, **chưa kiểm lại trong phiên này**): `project_atr_wilder_seeding` — OG đã đo và chỉnh `atr()/rma()` cho khớp `ta.atr`/`ta.rma` của TradingView (phép đo đó dựa trên Pine/biểu đồ, không dùng thư viện này); `project_og_signal_redis_contract` — DB0 là dữ liệu DP đẩy lên (nguồn TradingView theo lời người dùng).
- Cùng họ với F035 (S006): README khẳng định không kèm bằng chứng; ở đây các khẳng định về số trường đều kiểm được (13 219; 3 526; 424), còn "Any time interval" nói quá.

## 12. Bước kiểm chứng tiếp theo (đề xuất, chưa làm; cần quyết định của bạn)

1. Người phụ trách cấp phép dữ liệu của DP xác nhận cơ sở pháp lý của đường lấy dữ liệu so với mục 3 điều khoản TradingView (tôi không làm được).
2. Nếu vẫn muốn đối chiếu chỉ báo của OG với TradingView: so với **giá trị hiển thị trên biểu đồ** của chính TradingView do người xem đọc (mục đích hiển thị) hoặc với một nguồn dữ liệu có giấy phép phù hợp; không tự động hoá qua endpoint screener.
3. Không đưa `tvscreener` vào `requirements` của OG.
