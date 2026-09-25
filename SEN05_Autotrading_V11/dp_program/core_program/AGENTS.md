# Hướng dẫn làm việc với `dp_program` V3

File này áp dụng cho toàn bộ repository. `dp_program` V3 là chương trình độc lập;
runtime không được phụ thuộc vào source, config, service hoặc đường dẫn của V2.

## Phạm vi và contract

V3 có hai workflow **độc lập hoàn toàn**, không còn giao nhau qua SQL:

- `backfill`: kiểm tra dữ liệu SQL, tải cửa sổ lịch sử và upsert phần cần thiết —
  nguồn ghi SQL duy nhất của V3;
- `live`: mỗi chu kỳ (`live.interval_minutes`) lấy nến mới nhất từ TradingView và
  đẩy thẳng lên Redis — không ghi SQL, không phụ thuộc Fact watermark. Live vẫn
  đọc universe symbol/timeframe từ SQL dimension table (`select_pairs()`, giống
  backfill) nhưng đó là READ cho contract, không phải ghi candle.

Contract dữ liệu không tự thay đổi:

- 37 symbols, 15 direct timeframes;
- live mặc định gồm 11 symbols thuộc `Indice`, `Metal`, `Crypto`;
- FOREX chỉ historical;
- live interval mặc định 5 phút và chỉ lấy candle đã đóng;
- SQL Server là durable source of truth cho dữ liệu lịch sử (qua backfill);
  Redis là bản real-time độc lập do live tự duy trì, không còn là bản chụp
  (mirror) của SQL;
- staging dùng `SEN.TF_*`;
- Fact dùng `DWH.Fact_OHLCV`;
- loader phải là `DWH.usp_LoadDirect` contract version 4.

Không mở rộng `DWH.Dim_Date`, đổi universe, timeframe, business key, schema,
stored-procedure contract hoặc credential nếu chưa có operator approval.

## Nguồn sự thật V3

Ưu tiên theo thứ tự:

1. Code và test V3 hiện tại.
2. Metadata/read-only state của SQL Server.
3. Private `config.yaml` đã che secret.
4. SQL scripts đã triển khai và Git history.

Repository này là hệ thống nội bộ, ưu tiên gọn và dễ vận hành. Không tự ý
khôi phục `README.md`, `config.example.yaml`, `pyproject.toml` hoặc package
`__init__.py`; wrappers Windows tự set `PYTHONPATH=src`,
`PYTHONDONTWRITEBYTECODE=1` và gọi Python với `-B` cho runtime.

Engine là package `dp_program` theo src layout. Các file chính:

- `src/dp_program/configuration.py`: owner duy nhất của `config.yaml` và fixed
  technical defaults; operator live/backfill parameters are accepted from YAML;
- `src/dp_program/engine/auth.py`: auth account và Chromium headless;
- `src/dp_program/engine/websocket.py`: WebSocket protocol và complete fetch;
- `src/dp_program/engine/live.py`: live real-time — mỗi cycle fetch đuôi nhỏ
  (`live.bars_per_request`) từ TradingView cho mọi pair (gộp theo symbol), rồi
  đẩy thẳng lên Redis. Owns luôn Redis client và Lua script duy nhất (xem mục
  Redis bên dưới). Không đụng SQL, không phụ thuộc Fact watermark — mỗi cycle
  tự so đuôi vừa fetch với nến cuối đang có trong Redis List; nếu List rỗng,
  thiếu, hoặc có gap thì tự fetch lại đủ `redis.bars_per_snapshot` nến rồi đẩy
  qua cùng cơ chế — tự phục hồi, không cần bước bootstrap tách riêng. Có 2
  circuit breaker độc lập: lỗi TradingView (bảo vệ account/provider) và lỗi
  Redis (cooldown, tránh hammering khi Redis down);
- `src/dp_program/engine/backfill.py`: exact bootstrap và rolling repair — nguồn
  ghi SQL duy nhất của V3;
- `src/dp_program/engine/pipeline.py`: `validate_candles()` là hàm thuần dùng
  chung bởi cả live và backfill; `fetch_and_store()` (durable delivery vào SQL)
  giờ chỉ backfill dùng;
- `src/dp_program/engine/spool.py`: durable outbox trước SQL commit — chỉ
  backfill dùng, live không còn spool;
- `src/dp_program/engine/sql_connector.py`: owner duy nhất của mọi SQL access,
  warehouse value contract, public pair contract và chọn symbol/timeframe pair
  từ SQL dimensions (live vẫn đọc universe qua đây — READ, không ghi);
- `src/dp_program/log.py`: owner duy nhất của format, risk, masking và
  log rotation;
- `src/dp_program/engine/runtime.py`: single-instance service, schedule và state;
- `src/dp_program/__main__.py`: CLI.
- `src/dp_program/util/discord_report.py`: reporter Discord tùy chọn, chỉ sống cùng
  lifecycle của `run`;
- `src/dp_program/util/chart/server.py`: chart offline chạy thủ công, chỉ đọc Fact qua
  `sql_connector.py`.

### Redis (live → OG)

Mỗi nến là **một Hash riêng** `L_CANDLE_{SYMBOL}_{TIMEFRAME}:{stamp}` (6 field:
`timestamp`, `open`, `high`, `low`, `close`, `time_update`; bốn field giá làm
tròn 2 chữ số thập phân theo ROUND_HALF_UP -- chốt 2026-09-24, KHÔNG còn giữ
scale DECIMAL(18,8) của warehouse nữa, đó là quy ước riêng của SQL/backfill
qua `sql_connector.py`, live có hàm làm tròn riêng độc lập), và một List
`L_CANDLE_{SYMBOL}_{TIMEFRAME}`
chứa các `{stamp}` tăng dần làm chỉ mục duy nhất, luôn giữ đúng
`redis.bars_per_snapshot` nến gần nhất. Key Hash = key List nối thêm `":" +
stamp`, đúng một phép nối — tên List không bao giờ chứa `:`. `{stamp}` là
`YYYY-MM-DD HH:MM:SS`, luôn UTC, không offset, rộng cố định nên so sánh chuỗi
cho đúng thứ tự thời gian (Lua dựa vào đó, không dùng `tonumber`). Phần tử
List, đuôi key Hash và field `timestamp` luôn là cùng một chuỗi — open time.
`time_update` là thời điểm live fetch/đẩy nến đó lên Redis (không còn SQL
`CreatedAt`, vì live không ghi SQL nữa). Lua script chỉ HSET đúng nến nào thật
sự đổi giá và chỉ RPUSH mốc nào chưa có trong List; evict đúng phần dư ra khỏi
`bars_per_snapshot` bằng LPOP + DEL. `live.enabled=true` bắt buộc
`redis.enabled=true` (kiểm ở `configuration.py`) — live không còn việc gì để
làm nếu Redis tắt. Pair có lịch sử TradingView thật ngắn hơn
`bars_per_snapshot` (vd `HK50/W`) không bị ép full-reload mỗi cycle mãi mãi —
`_needs_full_reload()` ghi nhận trần thật qua marker Redis nội bộ
`dp:live:ceiling:{list_key}` sau khi TradingView xác nhận (namespace ngoài
hẳn `L_CANDLE_*` nên không lọt vào cơ chế phía OG bên dưới).

Live **không tự PUBLISH** message nào cho OG nữa (bỏ kênh
`redis.event_channel`/`dp:events:candles` 2026-09-23) — OG tự lắng nghe
**Redis keyspace notification** thật của chính HSET/RPUSH ở trên
(`PSUBSCRIBE __keyspace@{db}__:{key_prefix}_*`, lọc payload `hset`), server
Redis production đã bật đúng lớp `K`+`h` cần thiết (`notify-keyspace-events`,
xác nhận trực tiếp 2026-09-23, không phải DP bật). Xem `og-redis-contract`
memory cho lịch sử/lý do đổi.

### Redis (backfill → strategy_lab)

Kênh riêng `redis.backfill_event_channel` (mặc định `dp:events:backfill`) —
đây là kênh PUBLISH/SUBSCRIBE ứng dụng tự gọi DUY NHẤT DP còn dùng (khác hẳn
keyspace notification ở trên). Sau mỗi cặp symbol/timeframe ghi SQL xong
(`_run_group()` trong `backfill.py`), nếu `result["affected"] > 0` thì
PUBLISH một JSON `{"symbol", "timeframe"}` — kèm thêm `candle_count`/`from`/
`to` (tuỳ chọn, format giống `live.py:_stamp()`) khi `delivered_candles`
không rỗng, theo yêu cầu của strategy_lab (2026-09-23) để họ chỉ tính lại
đúng đoạn thay đổi thay vì toàn bộ lịch sử mỗi lần trigger — bên nhận tự đọc
lại SQL, message không mang giá trị nến thật. `backfill.py` có client Redis
riêng, độc lập hoàn toàn với client của `live.py` (không dùng chung, không
import lẫn nhau) — publish là best-effort, im lặng khi `redis.enabled=false`,
lỗi Redis không bao giờ làm rớt hoặc rollback việc ghi SQL đã commit.

Không import hoặc khôi phục `core_engine`/SEN05 architecture vào V3.

## Workflow kỹ thuật

Trước mọi thay đổi:

1. Chạy baseline Git và bảo toàn thay đổi có sẵn.
2. Đọc code/test/AGENTS liên quan.
3. Nếu liên quan SQL, đối chiếu schema và procedure metadata read-only.
4. Không đọc hoặc in nội dung private `config.yaml`.

Khi sửa code:

1. Tái hiện lỗi hoặc thêm regression test.
2. Giữ một candle representation xuyên suốt pipeline.
3. `validate_candles()` phải tiếp tục dùng chung giữa live và backfill.
   `fetch_and_store()` (ghi SQL) giờ chỉ backfill dùng — live không được gọi.
4. Mọi SQL, gồm SQL universe và truy vấn read-only cho chart, chỉ nằm trong
   `src/dp_program/engine/sql_connector.py`.
5. Mọi config access chỉ nằm trong `src/dp_program/configuration.py`.
6. Giữ `src/dp_program/engine/` đúng 8 file Python; mọi file tối đa 300 dòng
   code (dòng trống và dòng comment nguyên dòng không tính vào giới hạn này —
   xem `_code_line_count()` trong `test/test_util.py`),
   riêng `sql_connector.py` tối đa 460 dòng code vì nó gộp SQL access và
   contract để không tạo thêm file. Utility đúng 2 file Python
   (`discord_report.py`, `chart/server.py`) — Redis không còn ở `util/`, đã gộp
   vào `engine/live.py` vì live giờ đẩy thẳng lên Redis, không còn là
   side-effect tách rời. Không tạo shim hoặc owner trùng. Không dùng package
   `__init__.py` làm owner logic.
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
- Rolling backfill phải bắt đầu từ Fact watermark và overlap; vượt request cap
  phải fail closed, không cắt cửa sổ. (Live không còn khái niệm Fact watermark —
  xem mục Redis.)
- Live dùng request cố định nhỏ mỗi cycle; không có "pending" phải nhớ qua
  cycle — mỗi cycle tự kiểm lại trạng thái Redis và tự phục hồi (xem mục
  Redis). Có circuit breaker bảo vệ account/provider khi TradingView lỗi liên
  tục, và circuit breaker riêng khi Redis lỗi.
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

Production chạy gói đóng băng trong `run_dp/` (sibling của repo, không track):
`run_dp/dp_program.exe` build từ `scripts/windows/dp_program_entry.py`, cài qua
`run_dp/install.ps1` (đăng ký Scheduled Task `SEN05 DP Program Engine`). Dev/test
gọi thẳng `python -m dp_program run-live` / `run-backfill` từ `core_program/` với
`src/` trên `PYTHONPATH` — không còn wrapper `.bat`. Mỗi mode có lock riêng chống
chạy trùng chính nó; live/backfill không khóa chéo nhau. Sửa `core_engine` xong
phải build lại `.exe` rồi copy `run_dp/` mới sang máy đích — `.exe` cũ không tự
cập nhật (xem `run_dp/DEPLOY.md`).

Trước deploy phải có operator approval, rollback commit, controlled stop/start,
full validation, targeted write và runtime observation. V3 phải fail closed
khi không xác thực được tài khoản TradingView; tuyệt đối không chạy guest.
Chi tiết vận hành thuộc `docs/OPERATOR_RUNBOOK.md`.

Hai lớp tách bạch, không trộn khi sửa: `src/dp_program/**` là core_engine (chịu
lỗi dữ liệu: spool/replay, pending retry, circuit breaker, auth fail-closed).
`scripts/windows/**` là lớp vận hành đóng vào `.exe` (mode `--run`/`--restart`/
`--restart-live`/`--restart-backfill`/`--watchdog`/`--doctor-ops`/`--setup`,
giữ engine ở đúng trạng thái mong muốn). `--restart-live`/`--restart-backfill`
chỉ dừng/bật lại đúng 1 role — hợp lý vì live/backfill giờ độc lập hoàn toàn
(live đẩy thẳng Redis, backfill là nguồn ghi SQL duy nhất); `--restart` (không
tham số) vẫn làm cả hai để không phá thói quen cũ.
Lớp vận hành chỉ được GỌI core_engine qua API công khai/chỉ-đọc, không sửa nó.

Thay đổi tài liệu/test không được restart runtime. Mọi kết luận production phải
có timestamp và evidence trực tiếp.

## Bảo mật và báo cáo

Không print, paste hoặc commit token, cookie, password, connection string,
webhook, private `config.yaml`, runtime spool hoặc backup.

Báo cáo operator bằng tiếng Việt, dẫn đầu bằng kết quả và tác động; phân biệt:

- đã kiểm chứng;
- suy luận;
- chưa kiểm chứng hoặc còn rủi ro.
