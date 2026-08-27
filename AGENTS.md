# Hướng dẫn làm việc với `dp_program` V3

File này áp dụng cho toàn bộ repository. `dp_program` V3 là chương trình độc lập;
runtime không được phụ thuộc vào source, config, service hoặc đường dẫn của V2.

## Phạm vi và contract

V3 có hai workflow:

- `backfill`: kiểm tra dữ liệu SQL, tải cửa sổ lịch sử và upsert phần cần thiết;
- `live`: tải candle mới theo chu kỳ cấu hình.

Contract dữ liệu không tự thay đổi:

- 37 symbols, 15 direct timeframes;
- live mặc định gồm 11 symbols thuộc `Indice`, `Metal`, `Crypto`;
- FOREX chỉ historical;
- live interval mặc định 5 phút và chỉ lưu candle đã đóng;
- SQL Server là durable source of truth;
- staging dùng `SEN.TF_*`;
- Fact dùng `DWH.Fact_OHLCV`;
- loader phải là `DWH.usp_LoadDirect` contract version 4.

Không mở rộng `DWH.Dim_Date`, đổi universe, timeframe, business key, schema,
stored-procedure contract hoặc credential nếu chưa có operator approval.

## Nguồn sự thật V3

Ưu tiên theo thứ tự:

1. Code và test V3 hiện tại.
2. Metadata/read-only state của SQL Server.
3. Private `Config.yaml` đã che secret.
4. SQL scripts đã triển khai và Git history.

Repository này là hệ thống nội bộ, ưu tiên gọn và dễ vận hành. Không tự ý
khôi phục `README.md`, `Config.example.yaml`, `pyproject.toml` hoặc package
`__init__.py`; wrappers Windows tự set `PYTHONPATH=src`,
`PYTHONDONTWRITEBYTECODE=1` và gọi Python với `-B` cho runtime.

Engine là package `dp_program` theo src layout. Các file chính:

- `src/dp_program/configuration.py`: owner duy nhất của `Config.yaml` và fixed
  technical defaults; operator live/backfill parameters are accepted from YAML;
- `src/dp_program/engine/auth.py`: auth account và Chromium headless;
- `src/dp_program/engine/websocket.py`: WebSocket protocol và complete fetch;
- `src/dp_program/engine/live.py`: live planning, pending và recovery;
- `src/dp_program/engine/backfill.py`: exact bootstrap và rolling repair;
- `src/dp_program/engine/pipeline.py`: validation và shared durable delivery;
- `src/dp_program/engine/spool.py`: durable outbox trước SQL commit;
- `src/dp_program/engine/sql_connector.py`: owner duy nhất của mọi SQL access,
  warehouse value contract, public pair contract và chọn symbol/timeframe pair
  từ SQL dimensions;
- `src/dp_program/log.py`: owner duy nhất của format, risk, masking và
  log rotation;
- `src/dp_program/engine/runtime.py`: single-instance service, schedule và state;
- `src/dp_program/__main__.py`: CLI.
- `src/dp_program/util/discord_report.py`: reporter Discord tùy chọn, chỉ sống cùng
  lifecycle của `run`;
- `src/dp_program/util/redis_publisher.py`: publisher Redis tùy chọn cho live — sau mỗi
  lần một pair live ghi warehouse thành công, đọc lại nguyên cửa sổ nến mới nhất và
  ghi đè sang Redis cho OG đọc; không chặn đường ghi SQL chính;
- `src/dp_program/util/chart/server.py`: chart offline chạy thủ công, chỉ đọc Fact qua
  `sql_connector.py`.

Không import hoặc khôi phục `core_engine`/SEN05 architecture vào V3.

## Workflow kỹ thuật

Trước mọi thay đổi:

1. Chạy baseline Git và bảo toàn thay đổi có sẵn.
2. Đọc code/test/AGENTS liên quan.
3. Nếu liên quan SQL, đối chiếu schema và procedure metadata read-only.
4. Không đọc hoặc in nội dung private `Config.yaml`.

Khi sửa code:

1. Tái hiện lỗi hoặc thêm regression test.
2. Giữ một candle representation xuyên suốt pipeline.
3. Backfill và live phải tiếp tục dùng chung `fetch_and_store()`.
4. Mọi SQL, gồm SQL universe và truy vấn read-only cho chart, chỉ nằm trong
   `src/dp_program/engine/sql_connector.py`.
5. Mọi config access chỉ nằm trong `src/dp_program/configuration.py`.
6. Giữ `src/dp_program/engine/` đúng 8 file Python; mọi file tối đa 300 dòng
   code (dòng trống và dòng comment nguyên dòng không tính vào giới hạn này —
   xem `_code_line_count()` trong `test/test_v3.py` và `test/test_util.py`),
   riêng `sql_connector.py` tối đa 460 dòng code vì nó gộp SQL access và
   contract để không tạo thêm file. Utility đúng 3 file Python (`discord_report.py`,
   `redis_publisher.py`, `chart/server.py`); không tạo shim hoặc owner trùng.
   Không dùng package `__init__.py` làm owner logic.
7. Code, identifier, chuỗi thông báo lỗi/log trong code, và application
   log dùng tiếng Anh. Riêng comment giải thích (dòng bắt đầu bằng `#`,
   và docstring) dùng tiếng Việt — xem `src/dp_program/configuration.py`
   để tham khảo phong cách. Áp dụng cho comment mới lẫn comment cũ khi
   được chỉnh sửa; không bắt buộc đi dịch lại toàn bộ comment tiếng Anh
   còn sót lại ở những file chưa được rà soát.

Validation tối thiểu:

```powershell
python -m pytest test/
Get-ChildItem src/dp_program -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
python -m dp_program check-sql
python -m dp_program doctor
```

`check-sql` là read-only. Không dùng `backfill` hoặc `live` như integration
test trên database thật nếu chưa xác nhận write scope.

Sau khi hoàn tất một task (trước khi báo xong cho operator): dọn sạch mọi
artifact tạm sinh ra trong lúc làm — build cache (`build/`, `__pycache__/`),
file test/scratch tự tạo, Scheduled Task tạm dùng để verify, tiến trình
con lỡ khởi động ngoài ý muốn. Không để lại rác trong repo hay trên máy
vận hành chỉ vì task đã "xong việc chính".

## Toàn vẹn dữ liệu và SQL

- UTC được lưu dưới dạng SQL `DATETIME2(0)`.
- Input duplicate phải được loại trước write.
- Bootstrap phải scan đúng 60 ngày theo policy hiện tại và chỉ complete khi
  response `series_completed` phủ tới đầu cửa sổ.
- Rolling/live phải bắt đầu từ Fact watermark và overlap; vượt request cap phải
  fail closed, không cắt cửa sổ.
- Live khỏe dùng request cố định nhỏ; cycle đầu sau restart và pair pending mới
  mở rộng catch-up từ Fact watermark. Pair chưa có watermark phải chờ backfill.
- Live response lỗi/thiếu không được advance watermark; phần chưa chạy phải được
  giữ pending cho cycle sau, có circuit breaker để bảo vệ account/provider.
- WebSocket frame, JSON, data shape hoặc candle malformed phải fail toàn request
  và đi qua bounded retry; không được bỏ packet rồi chấp nhận `series_completed`.
- Gap chỉ là candle provider đã trả nhưng Fact thiếu; không dựng calendar grid
  cho weekend/holiday.
- Với loader v4, khi có delta phải stage toàn bộ provider-observed window đã lọc,
  không chỉ stage delta.
- Staging key là `(SymbolID, BarTime)`.
- Fact key là `(SymbolID, TimeframeID, BarTime)`.
- Bulk write phải ở trong transaction và rollback khi lỗi.
- Phải verify procedure contract trước write.
- Không tự động drop/truncate/delete table hoặc dữ liệu.
- Giữ chuỗi cài đặt canonical trong `scripts/sql/` đồng bộ với contract V3.
  Các migration đã bị thay thế chỉ thuộc Git history, không đưa lại vào
  installer vì có thể hạ contract của database.

Không reboot host, dừng SQL Server, rotate credential, kill hàng loạt process,
merge main, push hoặc tag khi chưa được yêu cầu.

## Runtime và deployment

Phase hiện tại chưa dùng Scheduled Task. Vận hành pilot dùng hai wrapper foreground:
`run_live.bat` -> `<production-python> -m dp_program run-live` và
`run_backfill.bat` -> `<production-python> -m dp_program run-backfill`. Mỗi mode có
lock riêng để chống chạy trùng chính nó; live/backfill không khóa chéo nhau.
Không tạo hoặc bật Scheduled Task khi chưa có operator approval mới.

Trước deploy phải có operator approval, rollback commit, controlled stop/start,
full validation, targeted write và runtime observation. V3 phải fail closed
khi không xác thực được tài khoản TradingView; tuyệt đối không chạy guest.
Chi tiết vận hành thuộc `docs/OPERATOR_RUNBOOK.md`.

Thay đổi tài liệu/test không được restart runtime. Mọi kết luận production phải
có timestamp và evidence trực tiếp.

## Bảo mật và báo cáo

Không print, paste hoặc commit token, cookie, password, connection string,
webhook, private `Config.yaml`, runtime spool hoặc backup.

Báo cáo operator bằng tiếng Việt, dẫn đầu bằng kết quả và tác động; phân biệt:

- đã kiểm chứng;
- suy luận;
- chưa kiểm chứng hoặc còn rủi ro.
