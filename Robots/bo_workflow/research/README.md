# research/

Nơi chứa các Jupyter notebook phân tích dữ liệu backtest/optimize — thay dần
cho cách phân tích thủ công qua PowerShell (đã dùng trong
`reports/exit-mode-comparison-2026-08-28.md` và
`reports/missing-bar-followup-2026-09-01.md`).

## Môi trường (2026-09-01, đã cài xong)

Python 3.12.7 tại `%LOCALAPPDATA%\Programs\Python\Python312\` (User install,
đã thêm PATH — mở terminal MỚI để PATH có hiệu lực). Đã cài: `jupyter`,
`jupyterlab`, `pandas`, `matplotlib`, `numpy`.

Chạy notebook: `jupyter lab` rồi mở file, hoặc chạy/xuất lại toàn bộ không
cần mở UI: `python -m jupyter nbconvert --to notebook --execute --inplace <file>.ipynb`.

## Notebook hiện có

### `signal_fidelity_check.ipynb`

Kiểm định "signal CSV Python xuất ra" có được cBot (Combo/MA Cross) nhận
đúng và đặt lệnh hợp lệ hay không — đối chiếu ĐỘC LẬP giữa file CSV signal
gốc và `log.txt` của 1 lượt backtest đã archive (không tin vào bộ đếm nội bộ
`OnStop` của cBot). Đã tự-kiểm-chứng bằng 4 lượt archive thật (Combo H4 +
MA Cross M30, cả 2 chế độ Fallback ON/OFF) — số liệu ra khớp tuyệt đối với
phân tích thủ công trước đó trong `reports/missing-bar-followup-2026-09-01.md`.

**Đổi dùng cho lượt khác**: sửa list `RUNS` trong cell "Chạy trên dữ liệu
thật" — mỗi phần tử cần `strategy` ("combo"/"ma_cross"), `csv` (đường dẫn
file signal, thường ở `Z:\Desktop\og_program\runtime\exports\`), `run_dir`
(folder trong `ArchivedRuns\`).

**Giới hạn đã biết**: không truy được về đúng tín hiệu nào bị bỏ do dưới sàn
volume broker (`CalculateVolume` trong `Combo.cs`/`MA Cross.cs` không gắn
bartime vào dòng log đó) — chỉ đếm được tổng số qua cột
`untraceable_volume_floor_skips`, không map ngược lại được về hàng CSV cụ
thể. Cần sửa `Combo.cs`/`MA Cross.cs` (thêm bartime vào dòng Print đó) nếu
muốn truy vết đầy đủ 100%.

### `signal_chart_visualizer.ipynb`

Đối chứng TRỰC QUAN 3 nguồn dữ liệu độc lập trên cùng 1 biểu đồ nến — nến
(SQL DP6, cache CSV trong `data_cache/`), signal (CSV gốc), lệnh thật
(`events.json`/`log.txt` của 1 lượt archive, qua `fidelity_lib.py`). Mục tiêu
CHỈ để mắt thường kiểm tra "đặt lệnh có chuẩn không", chưa phải đánh giá hiệu
suất/tối ưu. Output: 1 file HTML tự chứa duy nhất
`output/signal_chart_viewer.html`, dùng `vendor/lightweight-charts.js` (vendor
từ chính `dp_program_v3`/DP6) — mở trực tiếp bằng trình duyệt, chạy offline,
có dropdown chuyển qua lại giữa các dataset đã cấu hình.

Mỗi tín hiệu hiện 1 marker theo đúng bartime, phân biệt 3 tầng kết quả:
- 🟢/🔴 đậm (mũi tên) = thực sự thành giao dịch.
- 🟡 nhạt (mũi tên, chỉ Combo) = pending được chấp nhận nhưng cuối cùng không
  khớp (hết hạn / còn treo cuối kỳ) — phân biệt qua `fill_status` trong
  `fidelity_lib.get_fill_outcomes()`.
- ⚪ tròn xám = không đặt được lệnh nào (rejected/thiếu bar/ngoài khung test).

**Đổi/thêm dataset khác**: sửa list `RUNS` ở cell CONFIG — thêm 1 dict
`{label, strategy, archived_run_dir, candle_cache}` là đủ, `signal_csv_path`
tự đọc từ `parameters.cbotset` của chính run đó (không hardcode). Cần có sẵn:
1 file cache nến trong `data_cache/<Symbol>_<TF>_candles.csv` (tự fetch qua
OG8's `db_connector.load_range()`, xem cách làm trong lịch sử hội thoại
2026-09-01) + 1 lượt backtest đã archive. Không cần sửa hàm render.

**fidelity_lib.py**: module dùng chung giữa notebook này và
`signal_fidelity_check.ipynb` (bản độc lập, KHÔNG import module này — xem
docstring đầu file) — chứa logic đối chiếu CSV↔log đã tự-kiểm-chứng, cộng
thêm `get_fill_outcomes()` (truy vết placed→filled/expired/still_pending qua
`orderId` trong `events.json`, chỉ áp dụng Combo) và `get_equity_curve()`
(đường số dư theo từng lệnh đóng, từ field `balance`/`equity` có sẵn trong
`events.json`).

**2026-09-01, sau phản hồi người dùng — sửa 2 vòng UX:**
- **Vòng 1**: tách marker thành 2 LỚP riêng biệt — chấm nhỏ tại `bartime`
  (nơi CÓ SIGNAL) và mũi tên tại nến chứa `executed` trong log (nơi lệnh
  THỰC SỰ vào thị trường) — trước đó bị gộp nhầm vào cùng 1 điểm. Nhân tiện
  phát hiện thêm 1 bug độc lập: máy chạy Pacific Time (UTC-8), `pandas
  Timestamp.timestamp()` trên giá trị tz-naive âm thầm quy đổi qua múi giờ
  hệ thống — mọi marker time ở bản đầu bị lệch ~8 tiếng so với candle series
  thật; đã sửa bằng `.value // 10**9` (đọc thẳng wall-clock, không qua tz).
- **Vòng 2**: chú thích thu về 1 góc nhỏ (không chiếm hết header), thành
  **checkbox lọc** theo từng nhóm marker (Signal / Vào lệnh thành công / Đặt
  được không khớp) thay vì chỉ để đọc; marker Signal có màu theo hướng
  (xanh dương Buy/cam Sell) thay vì xám đều; mặc định chỉ hiện ~200 nến gần
  nhất (kéo trái/phải để xem thêm, dữ liệu đã nằm sẵn trong bộ nhớ, không
  phải lazy-load); text chi tiết (OHLC + entry/SL/TP/trạng thái) chuyển từ
  hiển thị thường trực (gây rối mắt) sang **tooltip chỉ hiện khi hover**
  (`chart.subscribeCrosshairMove`); thêm 1 chart phụ bên dưới vẽ đường số dư
  tài khoản, **2 chart kéo/zoom đồng bộ trục thời gian**
  (`subscribeVisibleTimeRangeChange` 2 chiều, có chặn lặp vô hạn).
- SL/TP giờ đọc trực tiếp từ chính dòng log "placed" (đã có sẵn, không cần
  join qua `events.json`) — mở rộng `PLACED_RE` trong `fidelity_lib.py`
  (nhóm capture SL/TP là optional, không phá vỡ số đếm "placed" đã validate
  trước đó — đã re-verify 140/343 không đổi sau khi sửa regex).
- **Bug thật đã gặp + đã sửa (2 chart đồng bộ)**: chart 1 bắn sự kiện "visible
  range đổi" ngay khi `setData()` của nó chạy — nếu chart 2 CHƯA kịp có dữ
  liệu tại thời điểm đó (2 series set tuần tự, không cùng lúc), lệnh đồng bộ
  chéo ném lỗi nội bộ thư viện ("Value is null") và DỪNG CẢ SCRIPT giữa
  chừng — mọi thứ set sau đó (marker, chart phụ...) biến mất, dù chart 1 vẫn
  hiện bình thường (đã set trước khi crash). Sửa bằng try/catch quanh lệnh
  đồng bộ chéo; lần đồng bộ tường minh cuối `showDataset()` (chạy sau khi cả
  2 đã có dữ liệu) tự chỉnh lại đúng. Bài học áp dụng cho MỌI cặp chart đồng
  bộ trong file này, không riêng equity (đã bỏ) hay MACD (mới thêm).
- **2026-09-01, vòng 3 — theo yêu cầu người dùng**: bỏ hẳn chart equity
  (không nhiều giá trị thực tế); thêm overlay MA/SMA + panel MACD Histogram —
  lấy ĐÚNG dữ liệu đã tính sẵn bởi `core_python.configuration.run_strategy()`
  bên OG8 (không tự tính lại chỉ báo độc lập, tránh lệch với logic tín hiệu
  thật — xem `og_program/core_python/signal_display/` để hiểu tại sao chọn
  cách này: module dashboard sống nội bộ của OG8 đã làm y hệt, dùng chung
  `lightweight-charts.js`). Cache CSV chỉ báo mới:
  `data_cache/US30_H4_combo_indicators.csv` (cột `ma`, `macd_h`),
  `data_cache/US30_M30_ma_cross_indicators.csv` (cột `fast_ma`, `slow_ma`,
  `macd_h`) — lấy 1 lần qua script tạm chạy trên OG8 (chỉ SELECT + gọi lại
  `run_strategy()` có sẵn), đã xoá sạch mọi file tạm trên OG8 sau khi fetch.
  Cũng đổi vị trí marker Signal từ `inBar` (giữa thân nến, hay bị đè) sang
  `belowBar`/`aboveBar` theo hướng lệnh (khớp quy ước marker Entry).
- **2026-09-01, đã kiểm chứng tham số chỉ báo khớp đúng file signal CSV đang
  dùng** — người dùng hỏi đúng: `export_cli.py` thực ra CÓ hỗ trợ
  `--param NAME=VALUE` override (dù `run_og.sh` không hỏi tới trong menu
  tương tác), nên chỉ đọc code chưa đủ chắc 100%. Đã kiểm chứng THỰC NGHIỆM:
  regenerate lại 2 file signal CSV bằng đúng tham số mặc định (không override
  gì), `diff` với file thật đang dùng trong `parameters.cbotset` của các lượt
  archive. Combo: khớp tuyệt đối (0 khác biệt). MA Cross: chỉ khác đúng 2
  dòng cuối (2 signal mới hơn ngày 2026-08-31, do DP6 đã ingest thêm bar mới
  từ lúc file gốc export tới giờ — KHÔNG phải do khác tham số). Kết luận:
  tham số mặc định (`config.yaml`) dùng khi tính chỉ báo (`ma`/`fast_ma`/
  `slow_ma`/`macd_h`) khớp đúng 100% với tham số đã tạo ra file signal CSV
  đang hiển thị trên chart — không có rủi ro lệch tham số.
- **2026-09-01, thêm HK50/H2 (Combo) + HK50/M45 (MA Cross) vào `RUNS`** — phát
  hiện + sửa **1 bug thật** trong `fidelity_lib.get_fill_outcomes()`: hàm này
  join `fill_status` vào `merged` qua **giá entry** (`on="entry"`) — nếu 2
  lệnh KHÁC NHAU trùng đúng giá entry (thực tế gặp trên HK50/H2:
  `entryPrice=25324.6` trùng giữa `orderId 252` và `431`), merge theo giá sẽ
  nhân đôi dòng kết quả (placed báo cáo 249 thay vì 247 thật). Không xảy ra
  với US30 (may mắn không có giá nào trùng) nên chưa lộ ra ở các lượt kiểm
  chứng trước. **Đã sửa**: gán `fill_status` theo VỊ TRÍ (thứ tự thời gian)
  thay vì merge theo giá — `merged["status"]=="placed"` (theo đúng thứ tự
  bartime CSV) và `fill_df` (theo đúng thứ tự serial trong `events.json`,
  dict Python giữ nguyên insertion order) cùng chronological order và cùng
  số lượng (1 signal placed = đúng 1 Create Stop Order), an toàn hơn nhiều so
  với join theo 1 giá trị float có thể trùng lặp. Có cảnh báo in ra nếu số
  lượng 2 phía lệch nhau (không đoán mò gán sai). Đã re-verify US30 không bị
  ảnh hưởng (140/110/29/1 giữ nguyên).
- **2026-09-01**: người dùng hỏi "chart có hiện lệnh đặt được nhưng không
  khớp/tự huỷ không?" — CÓ, đã có sẵn từ đầu (mũi tên nhạt, checkbox "Đặt
  được, không khớp"), chỉ tooltip đang in thẳng tên biến tiếng Anh
  (`expired_unfilled`/`still_pending_at_end`) — đã dịch rõ nghĩa
  (`FILL_STATUS_TEXT_VI`), không đổi logic/số liệu.
- **2026-09-01, sau khi `Combo.cs` thêm `ReconcileExistingExposure`** (xem
  `AGENT.md` §8 "tiếp 9"/"tiếp 10") — 2 dataset Combo trong `RUNS` đã đổi
  sang trỏ tới lượt archive MỚI (`US30_H4_ReconcileExposure_...`,
  `HK50_H2_ReconcileExposure_...`), thay cho lượt cũ trước khi có logic này.
  Phát hiện: tín hiệu bị bỏ qua do "đã có exposure cùng hướng" KHÔNG có dòng
  `Print()` riêng trong 2 lượt archive này (Combo.cs lúc chạy chưa có dòng
  log đó — đã bổ sung NGAY SAU trong cùng lượt sửa, cho các lần chạy sau) —
  nếu không xử lý, `fidelity_lib` sẽ hiểu nhầm thành "⚠ IN_WINDOW_BUT_MISSING"
  (bất thường giả). Đã thêm cơ chế đối chiếu số lượng trong
  `build_fidelity_report()`: parse dòng tổng kết OnStop cuối log
  (`parse_summary_counters()`, regex mới `SUMMARY_RE`), nếu số dòng "⚠" khớp
  CHÍNH XÁC với counter `same-direction-skipped` thật trong log thì gán lại
  nhãn `same_direction_skipped_inferred` — nếu KHÔNG khớp, GIỮ NGUYÊN cảnh
  báo "⚠" (ưu tiên không che giấu bất thường thật hơn là làm gọn giao diện).
  Verify: cả 2 lượt khớp tuyệt đối (US30: 9=9, HK50: 19=19), 0 "⚠" thật còn
  sót lại. Cũng thêm regex `SAME_DIRECTION_SKIP_RE` nhận diện đúng dòng log
  mới (cho các lượt chạy SAU này, không cần suy luận qua đối chiếu số lượng
  nữa).

## Bối cảnh hệ thống (đã điều tra 2026-09-01)

- **DP6** (10.11.12.6, Windows, `dp_program_v3`) — TradingView (kênh
  **Capital.com**) → SQL Server `SEN05_AutoTrading.DWH.Fact_OHLCV` (BarTime
  UTC-naive). 37 symbol, 15 timeframe (M5→W). Có sẵn
  `core_program/research/warehouse_candle_integrity.ipynb` — tham khảo được
  cho hướng kiểm định tương tự ở tầng DP6 nếu cần mở rộng sau này.
- **OG8** (10.11.12.8, Ubuntu, `og_program/core_python`) —
  `db_connector.load_range()` đọc trực tiếp `Fact_OHLCV` qua SQL, tính
  indicator + signal (`combo.py`/`ma_cross.py`), tính entry/SL/TP
  (`levels.py`), xuất CSV tối giản qua `export_cli.py`.
- **BO20** (máy này) — cTrader đọc CSV, backtest/optimize.

**⚠ Lưu ý an toàn**: `Config.yaml` ở CẢ 2 máy DP6/OG8 chứa secret thật
(token/cookie TradingView, mật khẩu SQL/Redis...) — không đọc/paste các file
này trừ khi thật sự cần, và nếu cần thì không hiển thị nội dung ra
chat/không lưu bản copy lại cục bộ lâu hơn mức cần thiết.
