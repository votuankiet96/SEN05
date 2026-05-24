# Data Provider README

Tài liệu này mô tả lại toàn bộ thư mục `data_provider` theo góc nhìn vận hành hệ thống auto trading và kho dữ liệu. Mục tiêu là để người không code vẫn có thể hiểu:

- hệ thống này lấy dữ liệu từ đâu
- dữ liệu đi qua những tầng nào
- file nào làm việc gì
- từng file có các mode/chế độ nào
- các rủi ro vận hành nằm ở đâu và hệ thống đang giảm thiểu chúng như thế nào

Tài liệu bổ sung:

- `RISK_AND_INCIDENT_HANDLING.md`: tài liệu chuyên sâu chỉ tập trung vào các vấn đề, rủi ro và cách hệ thống phản ứng khi sự cố xảy ra.

## 1. Bức tranh tổng thể

`data_provider` là lớp thu thập, làm sạch, kiểm tra và phục vụ dữ liệu nến OHLCV cho toàn bộ hệ thống.

Nói ngắn gọn:

1. `01_data_pipeline.py` kéo lịch sử và bù khoảng trống dữ liệu.
2. `02_ws_live.py` cập nhật dữ liệu gần realtime theo batch WebSocket.
3. `04_checker.py` kiểm tra chất lượng dữ liệu, sửa dữ liệu khi cần và xác minh sau sửa.
4. `03_chart.py` + `03_chart.html` cho phép xem nhanh dữ liệu và indicator trên dashboard.
5. Toàn bộ dữ liệu đi vào SQL Server theo mô hình `SEN -> DWH -> MART`.
6. `SEN.ActiveTask` là bảng điều phối vận hành, dùng để khóa tạm và relay payload/tín hiệu vận hành.

## 2. Giải thích nhanh cho người không code

### Candle / nến là gì?

Một nến là dữ liệu giá trong một khoảng thời gian cố định, ví dụ 5 phút hoặc 1 giờ. Mỗi nến có:

- `Open`: giá mở đầu
- `High`: giá cao nhất
- `Low`: giá thấp nhất
- `Close`: giá đóng cuối kỳ
- `Volume`: khối lượng

### Timeframe là gì?

Là độ dài của một nến:

- `M5`: 5 phút
- `M15`: 15 phút
- `H1`: 1 giờ
- `D1`: 1 ngày
- `W`: 1 tuần

### Staging là gì?

Là khu vực chứa dữ liệu mới tải về nhưng chưa coi là “đã sạch hoàn toàn”. Đây là điểm đáp đầu tiên để tránh ghi thẳng vào bảng chính.

### Fact table là gì?

Là bảng dữ liệu trung tâm. Khi một nến đã đi vào `DWH.Fact_OHLCV`, hệ thống coi đó là dữ liệu chuẩn để đọc và phân tích.

### Timeframe phái sinh / computed fallback là gì?

Runtime hiện tại kéo trực tiếp cả 15 timeframe từ TradingView/Capital.com. Các khung dưới đây chỉ còn là fallback/rebuild khi bật `ENABLE_COMPUTED_TIMEFRAMES=1`:

- `M10` từ `M5`
- `M20` từ `M5`
- `M90` từ `M30`
- `H6` từ `H3`
- `H8` từ `H4`

## 3. Kiến trúc tổng thể

```text
TradingView / tvDatafeed / WebSocket
        |
        v
01_data_pipeline.py --------+
                            |
02_ws_live.py --------------+----> SEN.TF_* staging tables
                            |            |
04_checker.py --------------+            v
                                         DWH.usp_LoadDirect
                                         DWH.usp_AggregateFromStaging (fallback)
                                                  |
                                                  v
                                           DWH.Fact_OHLCV
                                           + dimensions
                                                  |
                                                  v
                                     MART.v_OHLCV / usp_GetLatestCandles
                                                  |
                                                  v
                                   dashboard / strategy / kiểm tra dữ liệu

SEN.ActiveTask
  - lock vận hành
  - relay payload / legacy token
  - điều phối giữa checker / ws_live / pipeline
```

## 4. Thiết kế cơ sở dữ liệu hiện tại

### 4.1. Tầng `SEN`

Đây là tầng tiếp nhận dữ liệu đầu vào.

Các đối tượng chính:

- `SEN.TF_M5`, `SEN.TF_M10`, `SEN.TF_M15`, `SEN.TF_M20`, `SEN.TF_M30`
- `SEN.TF_M45`, `SEN.TF_M90`
- `SEN.TF_H1`, `SEN.TF_H2`, `SEN.TF_H3`, `SEN.TF_H4`, `SEN.TF_H6`, `SEN.TF_H8`
- `SEN.TF_D1`, `SEN.TF_W`
- `SEN.ActiveTask`

Ý nghĩa:

- `SEN.TF_*` là staging theo từng timeframe.
- `SEN.ActiveTask` không chứa dữ liệu giá. Nó là bảng điều phối vận hành.

### 4.2. Tầng `DWH`

Đây là kho dữ liệu chuẩn hóa.

Các đối tượng chính:

- `DWH.Dim_Symbol`
- `DWH.Dim_Timeframe`
- `DWH.Dim_Date`
- `DWH.Fact_OHLCV`

Ý nghĩa:

- `Dim_*` là các bảng tra cứu để giải thích dữ liệu.
- `Fact_OHLCV` là bảng trung tâm, một dòng là một nến.
- Khóa logic quan trọng nhất là `(SymbolID, TimeframeID, BarTime)`.

### 4.3. Tầng `MART`

Đây là lớp đọc dữ liệu thân thiện cho truy vấn SQL và các consumer nhẹ. Runtime Python hiện tại vẫn đọc trực tiếp `DWH.Fact_OHLCV` thông qua các loader trong `modules` và `core_python`.

Các đối tượng chính:

- `MART.v_OHLCV`
- `MART.usp_GetLatestCandles`

Ý nghĩa:

- `v_OHLCV` nối dữ liệu fact với dimension để dễ đọc.
- `usp_GetLatestCandles` là API SQL đơn giản để lấy N nến gần nhất theo symbol/timeframe.

### 4.4. Điều phối vận hành bằng `SEN.ActiveTask`

`SEN.ActiveTask` có 3 vai trò thực tế:

1. Giữ lock khi một tiến trình đang làm việc nhạy cảm.
2. Có TTL qua `ExpiresAt` để tránh khóa treo vô hạn nếu process bị kill.
3. Dùng `Payload` cho tín hiệu vận hành hoặc legacy token relay. Runtime Discord hiện tại là một chiều nên không nhận lệnh `/confirm_TOKEN` hoặc `/skip_TOKEN` trong luồng mặc định.

Đây là lý do file `06_active_task.sql` rất quan trọng cho runtime, dù nó không liên quan trực tiếp đến dữ liệu nến.

## 5. Luồng dữ liệu chính

### 5.1. Historical / backfill

Luồng này do `01_data_pipeline.py` điều phối.

1. Kết nối TradingView.
2. Kéo dữ liệu lịch sử theo symbol và timeframe.
3. Chuẩn hóa DataFrame, lọc dữ liệu lỗi.
4. Ghi vào `SEN.TF_*`.
5. Chạy ETL sang `DWH.Fact_OHLCV`.
6. Tính lại timeframe phái sinh chỉ khi bật fallback computed timeframe.

### 5.2. Live / intra-day

Luồng này do `02_ws_live.py` điều phối.

1. Mỗi 5 phút mở WebSocket.
2. Đăng ký nhóm symbol live.
3. Nhận vài nến mới nhất.
4. Đưa vào hàng đợi ghi DB.
5. Nếu queue đầy thì đẩy sang buffer RAM, nặng hơn nữa thì lưu tạm SQLite spool.
6. Ghi staging, ETL sang fact nếu checker không giữ lock sửa chữa.

### 5.3. Checker / repair

Luồng này do `04_checker.py` điều phối.

1. Đọc nến gần nhất trong DB.
2. Kéo nến tương ứng từ TradingView.
3. So sánh thiếu/dư/sai OHLC/volume.
4. Nếu là lỗi lõi hoặc mismatch vượt ngưỡng thì đưa vào danh sách cần xử lý.
5. Mặc định có thể auto-repair theo lô an toàn.
6. Nếu bật `--manual-confirm`, checker chỉ gửi thông báo Discord; Discord webhook là một chiều nên runtime hiện tại không chờ phản hồi tương tác.
7. Sau sửa phải verify lại.

### 5.4. Dashboard

`03_chart.py` và `03_chart.html` chỉ đọc dữ liệu đã có sẵn. Chúng không tham gia ghi dữ liệu vào kho.

## 6. Các stored procedure và logic SQL quan trọng

### `DWH.usp_LoadDirect`

Dùng cho timeframe kéo trực tiếp từ TradingView.

Nó:

- đọc dữ liệu đã đánh dấu hoàn tất trong staging
- map timeframe code sang `TimeframeID`
- ghi sang `DWH.Fact_OHLCV`
- bỏ qua nến đã tồn tại để đảm bảo idempotent

### `DWH.usp_AggregateFromStaging`

Dùng cho fallback/rebuild timeframe phái sinh, không phải luồng mặc định khi hệ thống đang kéo trực tiếp đủ 15 timeframe.

Lưu ý quan trọng:

- file `04_business_objects.sql` hiện chứa bản định nghĩa patched an toàn ở cuối file
- trong SQL Server, định nghĩa cuối cùng là định nghĩa có hiệu lực
- file `patch_usp_AggregateFromStaging_safe.sql` là bản vá độc lập để bảo trì hoặc backport

### `MART.usp_GetLatestCandles`

Dùng để lấy nhanh N nến gần nhất theo symbol/timeframe.

## 7. File nào làm gì?

### 7.1. SQL setup

- `00_sql/00_run_all.sql`: script chạy tuần tự toàn bộ setup SQL.
- `00_sql/01_setup_database.sql`: tạo database và schema.
- `00_sql/02_core_tables.sql`: tạo dimension, fact, bảng symbol master.
- `00_sql/03_staging_tables.sql`: tạo toàn bộ bảng staging theo timeframe.
- `00_sql/04_business_objects.sql`: tạo MART view/proc và ETL proc.
- `00_sql/05_verify.sql`: truy vấn kiểm tra setup.
- `00_sql/06_active_task.sql`: tạo bảng khóa/relay vận hành.
- `00_sql/patch_usp_AggregateFromStaging_safe.sql`: bản vá độc lập cho proc aggregate an toàn.

### 7.2. Module điều phối và helper

- `_helpers.py`: helper chung cho logging, validate, retry, gap detection, repull an toàn, recompute TF phái sinh.
- `_task_lock.py`: lock phân tán và token relay qua DB.
- `_discord.py`: gửi cảnh báo qua webhook/notification channel đang dùng bởi runtime.
- `_tv_auth.py`: quản lý auth TradingView, refresh token, guest fallback.
- `_tv_coord.py`: điều phối để historical job và live batch không đè nhau ở thời điểm nhạy cảm.
- `__init__.py`: package marker.

### 7.3. Luồng dữ liệu chính

- `01_data_pipeline.py`: historical load và daily backfill.
- `02_ws_live.py`: live updater dạng batch WebSocket.
- `04_checker.py`: checker, repair, continuity check, rebuild computed TF khi fallback được bật.

### 7.4. Quan sát dữ liệu

- `03_chart.py`: REST backend cho dashboard.
- `03_chart.html`: giao diện chart.
- `lightweight-charts.js`: thư viện vendor bên thứ ba.

## 8. Các mode/chế độ theo file

### `01_data_pipeline.py`

Mode chính:

- `auto`: tự quyết định full hay gap.
- `full`: kéo lịch sử đầy đủ.
- `gap`: chỉ bù phần còn thiếu.

Tuỳ chọn quan trọng:

- `--symbols`
- `--timeframes`
- `--asset-type`
- `--dry-run`
- `--reset`

Ý nghĩa vận hành:

- `--dry-run` để xem kế hoạch mà không ghi DB.
- `--reset` chỉ nên dùng khi có bộ lọc rõ ràng vì nó thay thế dữ liệu cũ.

### `02_ws_live.py`

Mode thực tế:

- batch scheduler mặc định, chạy theo chu kỳ 5 phút
- không phải persistent WebSocket 24/7

Chế độ xử lý nội bộ:

- queue bình thường
- overflow RAM buffer
- SQLite spool khi áp lực cao
- defer ETL khi checker đang repair
- guest mode warning khi auth suy giảm

### `04_checker.py`

Chế độ chính:

- `--dry-run`: chỉ quét
- mặc định: auto-repair an toàn
- `--manual-confirm`: gửi cảnh báo Discord trước khi sửa; webhook một chiều nên không nhận phản hồi tương tác

Khả năng mở rộng sẵn trong file:

- continuity check
- interval gap check
- auto repair interval gaps
- rebuild computed TF

### `03_chart.py`

Đây là mode read-only phục vụ kiểm tra dữ liệu bằng mắt:

- liệt kê symbol
- liệt kê timeframe theo nhóm direct/fallback computed
- trả OHLCV
- tính indicator server-side

## 9. Artefact runtime và file không nên nhầm là logic lõi

- `.tv_token_cache`: cache token TradingView runtime
- `.overflow_spool.db`: spool local khi live queue bị nghẽn
- `logs/`: log runtime
- `__pycache__/`: file Python sinh tự động
- `lightweight-charts.js`: thư viện vendor

Các file trên không phải là nơi nên chỉnh nghiệp vụ chính.

## 10. Rủi ro chính và cách hệ thống đang xử lý

### 10.1. Token TradingView hết hạn hoặc rơi xuống guest mode

Rủi ro:

- dữ liệu lịch sử bị thiếu
- batch live không lấy đủ nến
- checker so sánh sai vì nguồn pull không đầy đủ

Giảm thiểu hiện có:

- `_tv_auth.py` có nhiều tầng fallback
- refresh token trước batch
- refresh mid-run khi auth lỗi
- cảnh báo Discord nếu guest mode kéo dài

### 10.2. Race condition giữa ws_live và checker

Rủi ro:

- checker đang xóa/sửa nến thì ws_live lại ETL vào fact
- gây mất dữ liệu hoặc trạng thái khó đoán

Giảm thiểu hiện có:

- `SEN.ActiveTask`
- `_task_lock.py`
- `_tv_coord.py`
- ws_live defer ETL khi checker đang repair

### 10.3. Dữ liệu vào không đầy đủ hoặc queue ghi DB bị nghẽn

Rủi ro:

- mất bar trong giờ giao dịch
- live watermark sai
- backlog tích lũy

Giảm thiểu hiện có:

- queue riêng cho DB worker
- overflow buffer trong RAM
- SQLite spool bền hơn RAM
- backlog tracking và Discord alert

### 10.4. Sửa nhầm do false positive

Rủi ro:

- volume TradingView thay đổi nhẹ giữa 2 lần pull
- gap qua đêm hoặc cuối tuần bị hiểu nhầm là thiếu dữ liệu
- DST/anchor drift làm lệch timestamp

Giảm thiểu hiện có:

- `_validate_ohlcv_df`
- verified market gaps cache
- tolerance riêng cho giá và volume
- logic phát hiện DST / anchor drift / overnight gap

### 10.5. Reset hoặc repull làm hỏng dữ liệu

Rủi ro:

- xóa quá rộng
- xóa trước khi dữ liệu thay thế sẵn sàng

Giảm thiểu hiện có:

- `--reset` yêu cầu có bộ lọc
- repull an toàn theo cặp symbol/timeframe
- staging trước, thay thế sau

### 10.6. Drift giữa tài liệu SQL và runtime thực tế

Rủi ro:

- `run_all` không tạo đủ object vận hành
- verify đếm sai số object
- hiểu nhầm proc aggregate nào đang có hiệu lực

Giảm thiểu hiện có:

- tài liệu này
- note mới trong `00_run_all.sql`, `05_verify.sql`, `04_business_objects.sql`
- `06_active_task.sql` được đưa vào flow setup đầy đủ

## 11. Thứ tự nên đọc thư mục này

Nếu muốn hiểu hệ thống từ gốc tới ngọn, nên đọc theo thứ tự:

1. `00_sql/00_run_all.sql`
2. `00_sql/01_setup_database.sql`
3. `00_sql/02_core_tables.sql`
4. `00_sql/03_staging_tables.sql`
5. `00_sql/04_business_objects.sql`
6. `00_sql/06_active_task.sql`
7. `_tv_auth.py`
8. `_task_lock.py`
9. `_tv_coord.py`
10. `_helpers.py`
11. `01_data_pipeline.py`
12. `02_ws_live.py`
13. `04_checker.py`
14. `03_chart.py`
15. `03_chart.html`

## 12. Khuyến nghị vận hành

Trình tự chạy điển hình:

1. Chạy setup SQL đầy đủ.
2. Chạy `01_data_pipeline.py` để nạp lịch sử ban đầu.
3. Chạy `02_ws_live.py` để giữ dữ liệu cập nhật trong ngày.
4. Chạy `04_checker.py` định kỳ để kiểm tra và sửa sai lệch.
5. Dùng `03_chart.py` khi cần kiểm tra bằng mắt.

## 13. Kết luận

`data_provider` không chỉ là nơi “kéo dữ liệu”. Nó là một hệ thống con hoàn chỉnh gồm:

- ingestion
- ETL
- lock vận hành
- auth management
- quality control
- repair
- quan sát dữ liệu

Điểm quan trọng nhất để vận hành an toàn là luôn nhớ:

- staging và fact là hai tầng khác nhau
- live và repair phải được điều phối
- auth TradingView là một rủi ro vận hành thực sự
- `SEN.ActiveTask` là hạ tầng điều phối, không phải bảng dữ liệu nến
- checker mặc định hiện có thể auto-repair, không còn là luồng hỏi xác nhận tương tác như mô tả cũ
