# cTrader FTMO Tick Data Provider

Tài liệu này mô tả project con lấy dữ liệu tick từ cTrader/FTMO cho SEN05.
Nhánh này độc lập với TradingView, `ws_live`, pipeline OHLCV, checker và toàn bộ
luồng nến hiện có. Dữ liệu được ghi vào schema SQL riêng tên `tick`.

## Mục tiêu

- Lấy live tick từ tài khoản FTMO thông qua cTrader Open API.
- Lấy historical BID/ASK tick theo cửa sổ thời gian khi cần backfill.
- Match symbol thật của tài khoản cTrader vào 11 symbol SEN05 mục tiêu.
- Lưu tick raw và tick đã scale vào các bảng `tick.<SYMBOL>`.
- Chống mất dữ liệu ngắn hạn bằng SQLite spool nếu SQL Server tạm lỗi.
- Không hard-code token, client secret hoặc tên symbol broker.

## Ranh giới hệ thống

Project này chỉ nằm trong:

- `data_provider/tick_data/`
- `data_provider/apps/ctrader_ftmo_tick.py`
- `data_provider/sql/07_ctrader_ftmo_tick.sql`
- `data_provider/runtime/cache/ctrader_ftmo_oauth.json`
- `data_provider/runtime/spool/ctrader_ftmo_tick_spool.db`
- `data_provider/runtime/logs/ctrader_ftmo_tick.log`

Không dùng `DWH.Fact_OHLCV` để lưu tick. Không sửa hoặc phụ thuộc vào token
TradingView.

## Luồng vận hành

1. `oauth-login` hoặc `exchange-code` tạo token cache local.
2. `account-list` xác nhận đúng `ctidTraderAccountId` của tài khoản FTMO.
3. `symbol-sync` fetch danh sách symbol từ account cTrader.
4. `symbol-sync --apply` ghi mapping đã audit vào `tick.SymbolMap`.
5. `live --smoke-seconds 300` chạy smoke live có giới hạn.
6. Nếu smoke ổn mới chạy `live` dài hạn hoặc `backfill`.

## Commands

```powershell
Set-Location "C:\Share\SEN05_Autotrading"

.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick show-config
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick token-status
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick auth-url
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick oauth-login
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick account-list
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick symbol-sync
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick symbol-sync --apply
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick live --smoke-seconds 300
```

Additional production ops commands:

```powershell
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick check
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick spool-status
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick history-depth --json --max-days 20000
```

Backfill dùng UTC ISO hoặc millisecond timestamp:

```powershell
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick backfill --from "2026-06-09T00:00:00Z" --to "2026-06-09T01:00:00Z" --symbols US30 GOLD BTCUSD
```

## Cấu hình cần có khi cTrader app Active

Các giá trị có thể nằm trong environment hoặc trong token cache sau OAuth:

- `CTRADER_CLIENT_ID`
- `CTRADER_CLIENT_SECRET`
- `CTRADER_ACCESS_TOKEN`
- `CTRADER_REFRESH_TOKEN`
- `CTRADER_ACCOUNT_ID`
- `CTRADER_TRADER_LOGIN`
- `CTRADER_FTMO_ENV`, mặc định `demo`
- `CTRADER_REDIRECT_URI`, mặc định `http://localhost:8765/callback`

Access token được refresh chủ động khi cache cho thấy token thiếu hoặc còn ít hơn
`CTRADER_FTMO_TOKEN_REFRESH_SAFETY_SECONDS`, mặc định 24 giờ. Nếu refresh tạm lỗi
nhưng token cũ vẫn còn hạn, service tiếp tục chạy với token hiện tại và log cảnh
báo. Nếu token đã hết hạn và refresh fail, startup/auth sẽ dừng rõ ràng.

## Symbol universe

Giai đoạn đầu lấy 11 symbol:

| SymbolID | SEN05 symbol | Nhóm |
|---:|---|---|
| 2 | FR40 | Indice |
| 3 | DE40 | Indice |
| 4 | HK50 | Indice |
| 5 | J225 | Indice |
| 6 | SP35 | Indice |
| 7 | UK100 | Indice |
| 8 | US500 | Indice |
| 9 | US100 | Indice |
| 10 | US30 | Indice |
| 56 | GOLD | Metal |
| 81 | BTCUSD | Crypto |

Tên cTrader thật không được đoán cứng. `symbol-sync` dùng alias và score để đề
xuất mapping. Symbol `AMBIGUOUS`, `NOT_FOUND` hoặc `DISABLED` không được live
ingest cho đến khi operator xử lý.

## SQL contract

`07_ctrader_ftmo_tick.sql` tạo:

- `tick.AccountProfile`
- `tick.SymbolMap`
- `tick.IngestRun`
- `tick.IngestState`
- 11 bảng tick theo symbol
- `tick.v_SymbolMap`
- `tick.v_IngestHealth`

`tick.IngestRun.Status` có các trạng thái hợp lệ:

- `RUNNING`: run đang mở
- `STOPPED`: live/smoke dừng có kiểm soát
- `FAILED`: run lỗi
- `DONE`: historical backfill hoàn tất

Mỗi tick table có unique index trên `EventHash` với `IGNORE_DUP_KEY = ON` để
replay hoặc overlap live/history không nhân đôi dữ liệu.

## Runtime safety

- Token cache và spool nằm trong `data_provider/runtime`, đã bị gitignore.
- CLI `token-status` không in secret.
- Tên schema/table đi qua allow-list và bracket quoting.
- Live BID+ASK spot events are split into BID/ASK tick rows so live/history overlap keeps the same idempotency hash.
- `tick.IngestState` is updated only after SQL insert or spool drain succeeds.
- Production live owns `tick_live_runtime` and `tick_live_runtime.pid`; a newer live instance asks the older one to shutdown gracefully before taking over.
- Live ingest dùng reconnect backoff theo `CTRADER_FTMO_TICK_RECONNECT_*`.
- Nếu SQL insert fail, batch được đưa vào SQLite spool.
- Spool được drain lại trong flush loop khi SQL phục hồi.
- Smoke test phải dùng `--smoke-seconds` và không dùng dữ liệu đó cho trading.

## Ops folder

All Windows Scheduled Task and operator helpers for tick live are under:

```text
ops/run_tickdata/
```

Key entrypoints:

```powershell
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\install_tick_tasks.ps1 -Force
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_status.ps1
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_initial_backfill_max.ps1
powershell -ExecutionPolicy Bypass -File ops\run_tickdata\tick_initial_backfill_max.ps1 -Apply
```

Double-click helpers:

```text
ops\run_tickdata\tick_log_viewer.bat
ops\run_tickdata\tick_dashboard.bat
```

## Checklist khi có API ngày mai

1. Cài dependency mới nếu chưa có: `pip install -r requirements.txt`.
2. Chạy `show-config`; `missing_api_fields` phải chỉ ra đúng phần còn thiếu.
3. Chạy `oauth-login` hoặc `exchange-code --save`.
4. Chạy `token-status`; xác nhận access/refresh token đã set và chưa expired.
5. Chạy `account-list`; chọn đúng account FTMO demo bằng account id hoặc trader login.
6. Chạy `symbol-sync`; audit từng mapping.
7. Chạy `symbol-sync --apply` khi mapping hợp lý.
8. Chạy `live --smoke-seconds 300`.
9. Query `tick.v_IngestHealth`, `tick.IngestRun`, `tick.US30`, `tick.GOLD`, `tick.BTCUSD`.
10. Chỉ sau smoke tốt mới chạy live dài hạn hoặc historical backfill.

## File map

- `auth.py`: OAuth URL, local OAuth callback, exchange code, refresh token.
- `token_store.py`: cache token local và status redacted.
- `runtime.py`: runtime settings, endpoint demo/live, token TTL, cTrader SDK wrapper.
- `symbols.py`: symbol models và match symbol SEN05 với symbol account cTrader.
- `ticks.py`: tick model, scale giá, idempotency hash, historical windows, delta decode.
- `spool.py`: SQLite spool, gom tick, flush SQL, drain spool.
- `store_sql.py`: ghi SQL Server schema `tick` và cập nhật ingest state sau insert.
- `checker.py`: read-only tick health checks.
- `dashboard_server.py`: isolated tick dashboard API/server.
- `service_jobs.py`: account-list, symbol-sync, historical backfill.
- `service_live.py`: live subscription, reconnect, flush loop.
- `cli.py`: CLI cho operator.
