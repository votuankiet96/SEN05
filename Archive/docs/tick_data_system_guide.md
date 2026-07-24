# SEN05 — Hệ thống Tick Data (cTrader / FTMO)

**Cập nhật:** 2026-06-11
**Đối tượng:** Đội audit và implementer
**Phạm vi:** Mô tả toàn bộ kiến trúc, trạng thái triển khai, quy trình vận hành và checklist triển khai tiếp theo

---

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Vị trí trong hệ thống SEN05](#2-vị-trí-trong-hệ-thống-sen05)
3. [Trạng thái hiện tại](#3-trạng-thái-hiện-tại)
4. [Kiến trúc tổng thể](#4-kiến-trúc-tổng-thể)
5. [Cấu trúc file](#5-cấu-trúc-file)
6. [Mô tả các module Python](#6-mô-tả-các-module-python)
7. [Schema SQL](#7-schema-sql)
8. [Các mode vận hành và CLI](#8-các-mode-vận-hành-và-cli)
9. [Luồng live ingest chi tiết](#9-luồng-live-ingest-chi-tiết)
10. [Luồng historical backfill chi tiết](#10-luồng-historical-backfill-chi-tiết)
11. [Cơ chế Symbol Matching](#11-cơ-chế-symbol-matching)
12. [Cơ chế chống duplicate — EventHash](#12-cơ-chế-chống-duplicate--eventhash)
13. [Cơ chế an toàn dữ liệu — SQLite Spool](#13-cơ-chế-an-toàn-dữ-liệu--sqlite-spool)
14. [Cấu hình và biến môi trường](#14-cấu-hình-và-biến-môi-trường)
15. [OAuth và quản lý token](#15-oauth-và-quản-lý-token)
16. [Phụ thuộc và môi trường Python](#16-phụ-thuộc-và-môi-trường-python)
17. [Checklist triển khai — thứ tự ưu tiên](#17-checklist-triển-khai--thứ-tự-ưu-tiên)
18. [Câu hỏi thường gặp khi vận hành](#18-câu-hỏi-thường-gặp-khi-vận-hành)
19. [Những gì chưa có và hướng phát triển tiếp](#19-những-gì-chưa-có-và-hướng-phát-triển-tiếp)
20. [Tài liệu liên quan](#20-tài-liệu-liên-quan)

---

## 1. Tổng quan

Hệ thống **Tick Data** là một nhánh Data Provider mới trong SEN05, **hoàn toàn độc lập** với pipeline OHLCV (nến) đang chạy từ TradingView. Mục tiêu là thu thập dữ liệu tick (từng thay đổi giá nhỏ nhất theo thời gian thực) từ tài khoản **FTMO Demo** thông qua **cTrader Open API**, sau đó lưu vào SQL Server local.

### Tại sao cần tick data?

| Loại dữ liệu | OHLCV (nến) | Tick |
|---|---|---|
| Đơn vị nhỏ nhất | 1 nến M5 | Từng thay đổi giá (ms) |
| Dùng để | Chart, strategy signal, backtest bar-level | Kiểm định spread, slippage, execution |
| Nguồn | TradingView / Capital.com | cTrader / FTMO |
| Lưu tại | `DWH.Fact_OHLCV` | `tick.<SYMBOL>` |

### Nguyên tắc cô lập

Tick provider **không** chạm vào bất kỳ bảng OHLCV nào. Hai nhánh dùng chung database `SEN05_AutoTrading` và `SymbolID`, nhưng dữ liệu tick nằm hoàn toàn trong schema `tick` riêng.

---

## 2. Vị trí trong hệ thống SEN05

```
TradingView / Capital.com
    │
    ├── data_provider/apps/pipeline.py  ──► DWH.Fact_OHLCV  (nến lịch sử)
    └── data_provider/apps/ws_live.py   ──► DWH.Fact_OHLCV  (nến live 5 phút/lần)

cTrader / FTMO Demo
    │
    └── data_provider/tick_data/        ──► tick.<SYMBOL>    (tick live + backfill)
              │
              ├── OAuth  →  access token
              ├── symbol-sync  →  tick.SymbolMap
              ├── live ingest  →  tick.US30, tick.GOLD, ...
              └── backfill     →  tick.US30, tick.GOLD, ...
```

### Vị trí trong cụm Hyper-V

| VM | IP | Role | Tick Data? |
|---|---|---|---|
| SERVER-HOST | 10.11.12.5 | Dev / repo | Đây là nơi chạy tick provider |
| VM-DP | 10.11.12.6 | SQL Server | Database đích (`SEN05_AutoTrading`) |
| VM-OG | 10.11.12.8 | core_python | Không liên quan đến tick |
| VM-OF1–4 | 10.11.12.10–13 | cTrader cBots | Execution, không liên quan |

> **Lưu ý:** Tick provider kết nối đến SQL Server tại `10.11.12.6` (hoặc `localhost` nếu chạy trên VM-DP). Hiện tại đang được phát triển và test trên SERVER-HOST với SQL Server local.

---

## 3. Trạng thái hiện tại

> Kiểm tra ngày 2026-06-11. Đây là trạng thái **thực tế** của hệ thống, không phải lý thuyết.

### 3.1. cTrader Open API Application

| Mục | Trạng thái |
|---|---|
| Application status | ✅ **ACTIVE** (đã được Spotware duyệt) |
| OAuth đã chạy | ✅ Token cache tồn tại |
| `ctidTraderAccountId` | **47522998** |
| `traderLogin` | 7563609 |
| Token hết hạn | **2026-07-11** (~30 ngày còn lại tính từ hôm nay) |
| Token cache path | `data_provider/runtime/cache/ctrader_ftmo_oauth.json` |

### 3.2. Python Environment

| Mục | Trạng thái |
|---|---|
| `.venv` | ❌ **BROKEN** — được build từ Python 3.12 ở `C:\Users\Administrator\...` (đã xóa) |
| Python trên PATH | Python **3.13.12** tại `C:\Users\ADMIN\AppData\Local\Programs\Python\Python313` |
| Packages trong Python 3.13 | `pyodbc` có, `requests`/`ctrader-open-api`/`twisted` **không có** |
| Packages trên PyPI cho 3.13 | ✅ `ctrader-open-api 0.9.2` và `twisted-iocpsupport 25.10.1` đều có sẵn |
| **Hành động cần thiết** | Rebuild `.venv` với Python 3.13 + `pip install -r requirements.txt` |

### 3.3. SQL Schema `tick`

| Mục | Trạng thái |
|---|---|
| Schema `tick` trên SQL Server local | ❌ **0 tables** — script `07_ctrader_ftmo_tick.sql` **chưa được apply** |
| **Hành động cần thiết** | Chạy `sqlcmd ... -i data_provider\sql\07_ctrader_ftmo_tick.sql` |

### 3.4. Cấu hình

| Mục | Trạng thái |
|---|---|
| `DISCORD_WEBHOOK_URL` | ✅ Có trong `.env` |
| `CTRADER_CLIENT_ID` | ✅ Có trong `.env` |
| `CTRADER_CLIENT_SECRET` | ✅ Có trong `.env` |
| `CTRADER_FTMO_ENV` | ✅ `demo` |
| `CTRADER_ACCOUNT_ID` | ⚠️ Không có trong `.env` — được load từ token cache (OK) |
| `CTRADER_ACCESS_TOKEN` | ⚠️ Không có trong `.env` — được load từ token cache (OK) |

### 3.5. Tóm tắt — sẵn sàng chạy live chưa?

```
❌ Chưa sẵn sàng. Cần hoàn thành 4 bước:

  [1] Rebuild .venv với Python 3.13
  [2] Apply SQL schema 07_ctrader_ftmo_tick.sql
  [3] symbol-sync --apply
  [4] Smoke test live --smoke-seconds 300

  Token OAuth: ✅ đã có, không cần oauth-login lại.
```

---

## 4. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────┐
│                    cTrader Open API                          │
│                  demo.ctraderapi.com:5035                    │
└──────────────────────┬──────────────────────────────────────┘
                       │  Twisted TCP + Protobuf
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Python Tick Provider                            │
│              data_provider/tick_data/                        │
│                                                              │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │  auth.py     │  │  service_live   │  │ service_jobs  │  │
│  │  token_store │  │  (live ingest)  │  │ (sync/backfil)│  │
│  └──────────────┘  └────────┬────────┘  └───────┬───────┘  │
│                             │                    │           │
│                    ┌────────▼────────────────────▼───────┐  │
│                    │         TickBatcher + TickSpool      │  │
│                    │  (batch 500 ticks, flush mỗi 1 giây)│  │
│                    └────────────────┬────────────────────┘  │
└────────────────────────────────────┼────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────┐
                    │        SQL Server (local)            │
                    │        SEN05_AutoTrading             │
                    │                                      │
                    │  tick.IngestRun   tick.IngestState   │
                    │  tick.SymbolMap   tick.AccountProfile│
                    │  tick.US30        tick.GOLD           │
                    │  tick.BTCUSD      tick.US100  ...    │
                    └─────────────────────────────────────-┘
                            │ (nếu SQL fail)
                    ┌───────▼──────────────────────────────┐
                    │   SQLite Spool (local fallback)       │
                    │   data_provider/runtime/spool/        │
                    │   ctrader_ftmo_tick_spool.db          │
                    └──────────────────────────────────────┘
```

---

## 5. Cấu trúc file

```
data_provider/
├── tick_data/                          ← Package tick provider (isolate hoàn toàn)
│   ├── __init__.py
│   ├── runtime.py                      ← Settings, SDK wrapper, token refresh
│   ├── auth.py                         ← OAuth URL, exchange code, refresh token
│   ├── token_store.py                  ← Lưu/đọc token cache local
│   ├── symbols.py                      ← Model symbol, thuật toán match symbol
│   ├── ticks.py                        ← Model tick, scale giá, hash, delta decode
│   ├── spool.py                        ← SQLite spool, TickBatcher flush
│   ├── store_sql.py                    ← Ghi SQL Server schema tick
│   ├── service_live.py                 ← Live ingest 24/7 + reconnect
│   ├── service_jobs.py                 ← account-list, symbol-sync, backfill
│   ├── cli.py                          ← CLI entry point (10 commands)
│   └── README.md                       ← Hướng dẫn vận hành nhanh
│
├── apps/
│   └── ctrader_ftmo_tick.py            ← App wrapper (thin, gọi cli.main)
│
├── sql/
│   ├── 07_ctrader_ftmo_tick.sql        ← Tạo schema tick (idempotent)
│   └── 00_run_all.sql                  ← Fresh install (đã include file 07)
│
└── runtime/                            ← Gitignored, không commit
    ├── cache/
    │   └── ctrader_ftmo_oauth.json     ← Token cache (access + refresh token)
    └── spool/
        └── ctrader_ftmo_tick_spool.db  ← SQLite overflow khi SQL tạm lỗi

docs/
└── tick_data_system_guide.md           ← File này

tests/
└── test_ctrader_ftmo_tickdata.py       ← 23 targeted tests (offline)
```

---

## 6. Mô tả các module Python

### `runtime.py` — Settings và SDK

**Vai trò:** Trung tâm cấu hình runtime. Load settings từ `config.py`, `.env` và token cache.

**`TickRuntimeSettings`** (frozen dataclass) chứa tất cả thông tin cần thiết:

| Field | Nguồn | Ghi chú |
|---|---|---|
| `env` | `.env` → `CTRADER_FTMO_ENV` | `"demo"` hoặc `"live"` |
| `host` / `port` | Tính từ `env` | `demo.ctraderapi.com:5035` |
| `client_id` | `.env` → token cache | Client ID của application |
| `client_secret` | `.env` → token cache | Client Secret |
| `access_token` | `.env` → token cache | OAuth access token |
| `refresh_token` | `.env` → token cache | OAuth refresh token |
| `account_id` | `.env` → token cache | `ctidTraderAccountId` (không phải broker login) |
| `symbols` | `config.py` → `CTRADER_FTMO_TICK_SYMBOLS` | Tuple 11 `TargetSymbol` |
| `batch_size` | `.env` → default 500 | Số tick mỗi lần flush SQL |
| `flush_seconds` | `.env` → default 1.0 | Flush tối đa sau N giây dù chưa đủ batch |
| `reconnect_min_seconds` | `.env` → default 5 | Backoff min khi mất kết nối |
| `reconnect_max_seconds` | `.env` → default 300 | Backoff max |
| `spool_path` | Cố định | `runtime/spool/ctrader_ftmo_tick_spool.db` |

**`ensure_fresh_access_token()`:** Kiểm tra token expiry trước mỗi thao tác API. Nếu token còn `< CTRADER_FTMO_TOKEN_REFRESH_SAFETY_SECONDS` (mặc định 24 giờ), tự refresh và lưu lại cache.

**`load_ctrader_sdk()`:** Load lazy — chỉ import `ctrader_open_api` và `twisted.reactor` khi cần. Tests offline không cần SDK.

---

### `auth.py` — OAuth

**Vai trò:** Thực hiện OAuth 2.0 với endpoint cTrader.

| Hàm | Mô tả |
|---|---|
| `build_authorization_url()` | Tạo URL redirect người dùng đến trang đăng nhập cTrader |
| `exchange_code_for_token()` | POST → `https://connect.spotware.com/apps/token` với `authorization_code` |
| `refresh_access_token()` | POST với `refresh_token` để lấy access token mới |
| `run_local_oauth_login()` | Mở browser → start HTTP server local `:8765/callback` → nhận code tự động |

**Lưu ý quan trọng:** Endpoint token của cTrader dùng HTTP GET (theo spec Spotware), không phải POST. Đây là đặc thù của cTrader, không phải lỗi code.

---

### `token_store.py` — Token cache

**Vai trò:** Đọc/ghi file `ctrader_ftmo_oauth.json`. Không bao giờ in secret ra terminal.

Token cache lưu các field: `accessToken`, `refreshToken`, `expiresIn`, `expires_at_utc`, `client_id`, `client_secret`, `redirect_uri`, `scope`, `ctidTraderAccountId`, `traderLogin`, `created_at_utc`, `updated_at_utc`.

Hàm `token_status()` trả về trạng thái token **không lộ giá trị** — chỉ cho biết field có/không có, còn hạn hay hết hạn.

---

### `symbols.py` — Symbol matching

**Vai trò:** Đối chiếu 11 symbol SEN05 với symbol thật trên tài khoản cTrader.

**Vấn đề cần giải quyết:** Cùng một thị trường có thể có tên khác nhau trên từng broker. Ví dụ:

| SEN05 symbol | Tên có thể có trên FTMO/cTrader |
|---|---|
| `FR40` | FR40, FRA40, CAC40, FRANCE40 |
| `DE40` | DE40, GER40, DAX40, DAX, GERMANY40 |
| `GOLD` | GOLD, XAUUSD, XAU, XAU/USD |
| `US30` | US30, DJ30, DJI, DOW30, USWALLST30 |
| `BTCUSD` | BTCUSD, BTC/USD, BITCOIN |

**Thuật toán scoring:**

| Điều kiện | Score | Nhận xét |
|---|---|---|
| Tên symbol trùng chính xác | 100 | "exact local symbol" |
| Tên nằm trong alias list | 96 | "exact alias" |
| Tên bắt đầu bằng alias | 88 | "symbol starts with alias" |
| Tên chứa alias | 82 | "symbol contains alias" |
| Description chứa alias | 76 | "description contains alias" |
| Không khớp | 0 | "no reliable symbol signal" |

**Ngưỡng an toàn:** Score ≥ 80 mới được coi là khớp. Nếu có 2 candidate cách nhau ≤ 5 điểm → `AMBIGUOUS`. Symbol `AMBIGUOUS`, `NOT_FOUND`, `PENDING` **không được** đưa vào live ingest.

**`MappingStatus` trong `tick.SymbolMap`:**

| Status | Nghĩa |
|---|---|
| `PENDING` | Chưa sync, chỉ mới được seed từ config |
| `MATCHED` | Đã khớp rõ ràng, có thể live ingest |
| `AMBIGUOUS` | Khớp nhưng không chắc, operator phải xem xét |
| `NOT_FOUND` | Không tìm thấy trên account |
| `DISABLED` | Tạm tắt thủ công |

---

### `ticks.py` — Tick model

**Vai trò:** Model `TickRecord`, chuyển đổi giá, tính hash chống duplicate, decode tick lịch sử.

**Chuyển đổi giá cTrader:**

```
Giá cTrader lưu dạng integer = giá thực * 100000
Ví dụ: BidRaw = 194300000 → Bid = 1943.00000
```

Scale factor cố định `100000` (`CTRADER_PRICE_SCALE`). Trường `Digits` từ SymbolMap dùng để làm tròn đúng số thập phân.

**`TickRecord`** — các trường chính:

| Trường | Mô tả |
|---|---|
| `tick_time_utc` | Thời gian tick (từ source cTrader) |
| `source_timestamp_ms` | Millisecond timestamp gốc |
| `bid` / `ask` | Giá đã scale (Decimal) |
| `bid_raw` / `ask_raw` | Giá integer gốc từ cTrader |
| `bid_updated` / `ask_updated` | Flag: tick này update bid hay ask |
| `quote_type` | `BID`, `ASK`, hoặc `BOTH` |
| `source_mode` | `LIVE` hoặc `HISTORICAL` |
| `event_hash` | SHA-256 idempotency key (32 bytes) |
| `ingest_run_id` | UUID của IngestRun đang mở |

**Delta decode cho historical tick:**

Historical tick từ cTrader trả về dạng delta (chênh lệch so với tick trước, không phải giá tuyệt đối). `decode_delta_ticks()` giải mã lại thành giá tuyệt đối.

**`iter_tick_windows()`:** Chia khoảng thời gian backfill thành cửa sổ ≤ 7 ngày (giới hạn của cTrader API). Mỗi window request riêng một BID và một ASK.

---

### `spool.py` — SQLite spool và batch

**`TickSpool`** — SQLite durable buffer:

```sql
-- Schema SQLite spool
CREATE TABLE tick_spool (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_hash    BLOB NOT NULL UNIQUE,
    payload_json  TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
)
```

Tick được đẩy vào spool khi SQL Server insert fail. Spool tự cleanup `INSERT OR IGNORE` (không duplicate). Drain tự động trong `flush_loop` mỗi `flush_seconds`.

**`TickBatcher`** — gom tick và quyết định khi nào flush:

- Flush khi `len(pending) >= batch_size` (mặc định 500) **hoặc**
- Flush khi `time.monotonic() - last_flush >= flush_seconds` (mặc định 1.0 giây)
- Track `rows_inserted` và `rows_spooled` để báo cáo vào `IngestRun`

---

### `store_sql.py` — SQL Server persistence

**Vai trò:** Tất cả thao tác ghi vào SQL Server schema `tick`.

**SQL safety:** Table name được whitelist từ danh sách symbol cấu hình và bracket-quoted `[schema].[table]`. Không chấp nhận table name tự do từ input.

| Method | Tác dụng |
|---|---|
| `insert_ticks(records)` | `executemany` vào `tick.[SYMBOL]`, group theo symbol |
| `start_ingest_run(mode)` | INSERT vào `tick.IngestRun`, trả về UUID |
| `finish_ingest_run(id, status, rows_inserted, rows_spooled)` | UPDATE IngestRun |
| `upsert_symbol_matches(matches)` | MERGE vào `tick.SymbolMap` |
| `fetch_matched_symbols()` | SELECT `MATCHED + Enabled=1` từ SymbolMap |
| `update_ingest_state(target, remote, ...)` | MERGE vào `tick.IngestState` |

---

### `service_live.py` — Live ingest

**Vai trò:** Subscribe tick 24/7 từ cTrader. Dùng Twisted reactor (event-driven I/O).

**Luồng khởi động:**

```
run_live_ingest()
  ├── ensure_fresh_access_token()     # kiểm tra + refresh token nếu cần
  ├── fetch_matched_symbols()         # lấy SymbolMap từ SQL
  ├── load_ctrader_sdk()              # lazy import Twisted + cTrader SDK
  ├── start_ingest_run("LIVE")        # ghi IngestRun mở
  ├── start_client()                  # kết nối TCP đến cTrader
  │     └── on_connected()
  │           └── send_auth_chain()   # app auth → account auth
  │                 └── on_authed()
  │                       ├── subscribe spots (11 symbols)
  │                       └── flush_loop() mỗi 1 giây
  └── reactor.run()                   # blocking event loop
```

**Reconnect backoff:**

```python
delay = min(max_seconds, min_seconds * (2 ** (attempt - 1)))
# attempt=1: 5s, 2: 10s, 3: 20s, 4: 40s, ... max: 300s
```

Reconnect không tạo IngestRun mới — cùng 1 run ID được giữ suốt phiên live cho đến khi `request_stop()`.

**Smoke mode:** `live --smoke-seconds 300` dùng `reactor.callLater(duration, request_stop)` để tự dừng sau N giây. Label trong `IngestRun` là `SMOKE`, note là `non-production`.

---

### `service_jobs.py` — Jobs đơn lần

**`fetch_account_list()`:** Dùng app-level auth (client_id + client_secret) để list tất cả account được cấp quyền cho access token này. Trả về JSON list.

**`sync_symbols()`:** Fetch `ProtoOASymbolsListReq` → build_symbol_matches → (nếu `--apply`) upsert vào `tick.SymbolMap`.

**`run_history_backfill()`:** Tạo queue các `HistoryRequest` (target × window × quote_type), gửi lần lượt `ProtoOAGetTickDataReq`. Nếu response có `hasMore=True`, thêm window nhỏ hơn vào đầu queue. Tất cả tick đi qua `TickBatcher` → SQL.

---

### `cli.py` — Command line interface

Entry point: `python -m data_provider.apps.ctrader_ftmo_tick <command>` hoặc qua wrapper `data_provider/apps/ctrader_ftmo_tick.py`.

---

## 7. Schema SQL

### 7.1. Metadata tables

#### `tick.AccountProfile`

Lưu thông tin account cTrader/FTMO mỗi lần `account-list` được gọi.

| Cột | Kiểu | Mô tả |
|---|---|---|
| `AccountProfileID` | INT IDENTITY | PK |
| `Environment` | VARCHAR(10) | `demo` hoặc `live` |
| `CtidTraderAccountId` | BIGINT | ID nội bộ cTrader (không phải broker login) |
| `TraderLogin` | BIGINT | Login hiển thị trong cTrader UI |
| `IsLive` | BIT | Account live hay demo |
| `BrokerName` | NVARCHAR(120) | Tên broker |
| `IsActive` | BIT | DEFAULT 1 |
| `CreatedAtUtc` / `LastSeenAtUtc` | DATETIME2(3) | Audit trail |

**Index:** `UX_tick_AccountProfile_Env_Account` (unique trên `Environment + CtidTraderAccountId`).

---

#### `tick.SymbolMap`

Bảng mapping giữa SEN05 symbol và symbol thật trên cTrader.

| Cột | Kiểu | Mô tả |
|---|---|---|
| `SymbolID` | INT | PK, FK → `DWH.Dim_Symbol(SymbolID)` |
| `SenSymbol` | NVARCHAR(20) | Tên SEN05 (VD: `US30`) |
| `AssetType` | NVARCHAR(20) | `Indice`, `Metal`, `Crypto` |
| `CTraderSymbolId` | BIGINT | ID thật trên tài khoản cTrader |
| `CTraderSymbolName` | NVARCHAR(80) | Tên symbol trên broker |
| `CTraderDescription` | NVARCHAR(200) | Mô tả từ cTrader |
| `Digits` | INT | Số thập phân |
| `PipPosition` | INT | Vị trí pip (dùng tính spread/pip) |
| `MappingStatus` | VARCHAR(20) | `PENDING`/`MATCHED`/`AMBIGUOUS`/`NOT_FOUND`/`DISABLED` |
| `MappingScore` | INT | Score từ thuật toán matching (0–100) |
| `Enabled` | BIT | DEFAULT 1 — tắt để bỏ qua symbol |
| `LastSyncedAtUtc` | DATETIME2(3) | Lần sync cuối |
| `Notes` | NVARCHAR(400) | Ghi chú lý do mapping |

**Quan trọng:** Script `07_ctrader_ftmo_tick.sql` **seed sẵn 11 rows** với status `PENDING` và `Enabled=1`. Sau `symbol-sync --apply`, status sẽ được cập nhật thành `MATCHED` / `AMBIGUOUS` / `NOT_FOUND`.

---

#### `tick.IngestRun`

Mỗi lần chạy `live`, `live --smoke-seconds`, hay `backfill` tạo một row ở đây.

| Cột | Kiểu | Mô tả |
|---|---|---|
| `IngestRunID` | UNIQUEIDENTIFIER | PK |
| `AppName` | NVARCHAR(80) | VD: `SEN05 cTrader FTMO Tick LIVE` |
| `Environment` | VARCHAR(10) | `demo` hoặc `live` |
| `CtidTraderAccountId` | BIGINT | Account ID |
| `StartedAtUtc` / `StoppedAtUtc` | DATETIME2(3) | Thời gian chạy |
| `Status` | VARCHAR(20) | `RUNNING` → `STOPPED`/`DONE`/`FAILED` |
| `StopReason` | NVARCHAR(400) | Note hoặc thông báo lỗi |
| `RowsInserted` | BIGINT | Tick ghi thành công vào SQL |
| `RowsSpooled` | BIGINT | Tick phải đưa vào SQLite spool |
| `HostName` | NVARCHAR(128) | Tên máy chủ |
| `ProcessID` | INT | PID Python |

**Vòng đời Status:**

```
(start_ingest_run) RUNNING
       ├── live dừng bình thường      → STOPPED
       ├── backfill hoàn tất          → DONE
       └── lỗi nghiêm trọng           → FAILED
```

---

#### `tick.IngestState`

Trạng thái mới nhất theo từng symbol. Được MERGE mỗi khi có tick mới.

| Cột | Kiểu | Mô tả |
|---|---|---|
| `SymbolID` | INT | PK, FK → `DWH.Dim_Symbol` |
| `Status` | VARCHAR(20) | `INIT`/`SYNCED`/`LIVE`/`STALE`/`ERROR`/`DISABLED` |
| `LastLiveTickTimeUtc` | DATETIME2(3) | Thời gian tick live cuối cùng |
| `LastHistoricalTickTimeUtc` | DATETIME2(3) | Thời gian tick backfill cuối |
| `LastBid` / `LastAsk` | DECIMAL(19,8) | Giá bid/ask cuối cùng |
| `LastWriteAtUtc` | DATETIME2(3) | Lần cuối ghi SQL thành công |
| `LastHeartbeatAtUtc` | DATETIME2(3) | Lần cuối nhận tick (kể cả spool) |
| `TotalTicksInserted` | BIGINT | Tổng tick đã ghi từ trước đến nay |
| `ConsecutiveErrors` | INT | Số lỗi liên tiếp |
| `LastError` | NVARCHAR(1000) | Thông báo lỗi cuối |

---

### 7.2. Tick tables per-symbol

**11 bảng:** `tick.FR40`, `tick.DE40`, `tick.HK50`, `tick.J225`, `tick.SP35`, `tick.UK100`, `tick.US500`, `tick.US100`, `tick.US30`, `tick.GOLD`, `tick.BTCUSD`

Tất cả có cùng cấu trúc:

| Cột | Kiểu | Mô tả |
|---|---|---|
| `TickID` | BIGINT IDENTITY | PK clustered |
| `SymbolID` | INT | FK → `DWH.Dim_Symbol` |
| `CTraderSymbolId` | BIGINT | Symbol ID trên cTrader |
| `CTraderSymbolName` | NVARCHAR(80) | Tên symbol broker |
| `TickTimeUtc` | DATETIME2(3) | Thời gian tick (millisecond precision) |
| `SourceTimestampMs` | BIGINT | Timestamp ms gốc từ cTrader |
| `BidRaw` / `AskRaw` | BIGINT | Giá integer gốc (×100000) |
| `Bid` / `Ask` | DECIMAL(19,8) | Giá đã scale |
| `Mid` | DECIMAL(19,8) | **(Bid+Ask)/2** — computed, persisted |
| `Spread` | DECIMAL(19,8) | **Ask−Bid** — computed, persisted |
| `BidUpdated` / `AskUpdated` | BIT | Flag: tick này update bid/ask? |
| `QuoteType` | VARCHAR(10) | `BID`, `ASK`, `BOTH`, `TECHNICAL` |
| `SourceMode` | VARCHAR(16) | `LIVE` hoặc `HISTORICAL` |
| `SessionCloseRaw` / `SessionClose` | BIGINT / DECIMAL | Giá đóng phiên nếu có |
| `IsTechnicalEvent` | BIT | DEFAULT 0 |
| `ReceivedAtUtc` | DATETIME2(3) | Thời điểm Python nhận tick |
| `IngestRunID` | UNIQUEIDENTIFIER | FK → `tick.IngestRun` |
| `EventHash` | BINARY(32) | SHA-256 idempotency key |

**Indexes mỗi bảng:**

```sql
-- Unique: chống duplicate (IGNORE_DUP_KEY = ON → silently skip)
CREATE UNIQUE NONCLUSTERED INDEX UX_tick_<SYMBOL>_EventHash ON tick.<SYMBOL>(EventHash)
    WITH (IGNORE_DUP_KEY = ON);

-- Query nhanh theo thời gian + giá
CREATE NONCLUSTERED INDEX IX_tick_<SYMBOL>_Time ON tick.<SYMBOL>(TickTimeUtc DESC)
    INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
```

---

### 7.3. Views

#### `tick.v_SymbolMap`

View phẳng của `tick.SymbolMap` — dùng để kiểm tra mapping nhanh.

```sql
SELECT * FROM tick.v_SymbolMap;
-- Xem: SymbolID, SenSymbol, CTraderSymbolId, CTraderSymbolName, MappingStatus, Score
```

#### `tick.v_IngestHealth`

JOIN `tick.SymbolMap` với `tick.IngestState` — view sức khỏe hệ thống theo symbol.

```sql
SELECT * FROM tick.v_IngestHealth ORDER BY SenSymbol;
-- Xem: MappingStatus, Status, LastLiveTickTimeUtc, LastBid, LastAsk,
--      TotalTicksInserted, ConsecutiveErrors, LastError
```

---

### 7.4. Symbol universe ban đầu

| SymbolID | SEN05 | Asset Type |
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

---

## 8. Các mode vận hành và CLI

### 8.1. Nhóm 1 — Setup / Auth (chạy 1 lần)

```powershell
# Working directory
Set-Location "Z:\SEN05_Autotrading"
$py = ".\.venv\Scripts\python.exe"
$app = "data_provider.apps.ctrader_ftmo_tick"
```

| Command | Mục đích |
|---|---|
| `show-config` | In config runtime không lộ secret. Kiểm tra env, endpoint, symbols, missing fields. |
| `token-status` | In trạng thái token cache: có/không có, còn hạn hay hết hạn. **Không in giá trị token.** |
| `auth-url` | Tạo OAuth authorization URL nếu cần đăng nhập lại thủ công. |
| `oauth-login [--no-browser]` | Tự động OAuth: mở browser → server local `:8765/callback` → nhận code → lưu cache. |
| `exchange-code --code <CODE> [--save]` | Đổi code thủ công lấy token. Dùng khi `oauth-login` không tiện. |
| `refresh-token [--save]` | Refresh access token từ refresh token trong cache. |
| `account-list [--save-account-id N]` | List account FTMO được cấp quyền. Dùng `--save-account-id` để ghi account ID vào cache. |
| `symbol-sync [--apply]` | Dry run: show proposed mapping. `--apply`: ghi vào `tick.SymbolMap`. |

**Ví dụ:**

```powershell
& $py -m $app show-config
& $py -m $app token-status
& $py -m $app account-list --save-account-id 47522998
& $py -m $app symbol-sync
& $py -m $app symbol-sync --apply
```

---

### 8.2. Nhóm 2 — Live Ingest

#### Mode LIVE (production 24/7)

```powershell
& $py -m $app live
```

- Chạy không giới hạn thời gian.
- Label trong `tick.IngestRun`: `LIVE`
- Dừng bằng Ctrl+C → `on_shutdown()` flush batcher → `STOPPED`.

#### Mode SMOKE (smoke test ngắn hạn)

```powershell
& $py -m $app live --smoke-seconds 300
```

- Tự dừng sau 300 giây (hoặc bất kỳ số giây nào).
- Label: `SMOKE`, Status kết thúc: `STOPPED`
- **Không dùng dữ liệu smoke cho trading decision.**
- **Không chạy backfill trong cùng session smoke.**

---

### 8.3. Nhóm 3 — Historical Backfill

```powershell
& $py -m $app backfill `
    --from "2026-06-01T00:00:00Z" `
    --to   "2026-06-08T00:00:00Z"

# Lọc chỉ một số symbol
& $py -m $app backfill `
    --from "2026-06-01T00:00:00Z" `
    --to   "2026-06-02T00:00:00Z" `
    --symbols US30 GOLD BTCUSD
```

- Label: `BACKFILL`, Status kết thúc: `DONE` (hoặc `FAILED` nếu lỗi).
- Cửa sổ tối đa 7 ngày / request (giới hạn cTrader API). Code tự split nếu range dài hơn.
- Mỗi symbol × mỗi window → 2 request (BID + ASK).
- `--from` / `--to` chấp nhận ISO UTC hoặc millisecond timestamp.

---

## 9. Luồng live ingest chi tiết

```
1. ensure_fresh_access_token()
      └── Nếu token còn < 24h → gọi refresh_access_token() → lưu cache

2. fetch_matched_symbols() từ tick.SymbolMap
      └── Chỉ lấy WHERE MappingStatus='MATCHED' AND Enabled=1
      └── Nếu 0 row → raise RuntimeError (chưa sync symbol)

3. load_ctrader_sdk() → import Twisted reactor + cTrader SDK

4. start_ingest_run("LIVE") → INSERT tick.IngestRun, status=RUNNING

5. start_client()
      └── Client(host, port, TcpProtocol)
      └── setConnectedCallback → on_connected

6. on_connected
      └── ensure_fresh_access_token() lần nữa (reconnect case)
      └── send_auth_chain:
            [a] ProtoOAApplicationAuthReq (client_id + client_secret)
            [b] ProtoOAAccountAuthReq (account_id + access_token)
            [c] on_authed()

7. on_authed
      └── ProtoOASubscribeSpotsReq cho 11 symbol IDs
      └── Bắt đầu flush_loop() nếu chưa chạy

8. flush_loop (mỗi 1 giây qua reactor.callLater)
      └── batcher.flush() → insert_ticks() → SQL
      └── batcher.drain_spool() → nếu spool có tick tồn đọng

9. on_message → ProtoOASpotEvent
      └── Lookup symbol từ by_remote_id dict
      └── TickRecord.from_live_spot()
      └── batcher.add(record)
      └── store.update_ingest_state()

10. on_disconnected (không phải intentional stop)
       └── reconnect_delay_seconds(attempt, 5, 300) = exponential backoff
       └── reactor.callLater(delay, start_client)

11. Ctrl+C hoặc duration elapsed
       └── request_stop() → client.stopService() → reactor.stop()
       └── on_shutdown() → batcher.flush() → finish_ingest_run(STOPPED)
```

---

## 10. Luồng historical backfill chi tiết

```
1. ensure_fresh_access_token()

2. fetch_matched_symbols()

3. Tính danh sách HistoryRequest:
   - iter_tick_windows(from_ms, to_ms) → chia thành cửa sổ ≤ 7 ngày
   - Mỗi (symbol, window) → 2 request: BID + ASK
   - Queue: deque[HistoryRequest]

4. start_ingest_run("BACKFILL")

5. Kết nối TCP → auth chain → send_next()

6. send_next():
   a. Queue rỗng → flush → finish_ingest_run(DONE) → stop
   b. Lấy HistoryRequest đầu tiên
   c. ProtoOAGetTickDataReq(symbol_id, quote_type, from_ms, to_ms)
   d. on_ticks(response):
         - decode_delta_ticks(raw_ticks, quote_type)
         - Mỗi tick → batcher.add()
         - batcher.flush()
         - Nếu hasMore=True và có tick → thêm sub-window vào đầu queue
         - send_next() tiếp
```

---

## 11. Cơ chế Symbol Matching

```
symbol-sync (không --apply): dry run, chỉ in kết quả
symbol-sync --apply:
   1. Kết nối cTrader → ProtoOASymbolsListReq
   2. Nhận danh sách tất cả symbol của account
   3. build_symbol_matches(targets, remotes)
       └── Với mỗi target (11 symbol SEN05):
           └── Score tất cả remote → chọn best
           └── Nếu top ≥ 80 và không ambiguous → MATCHED
   4. store.upsert_symbol_matches(matches)
       └── MERGE vào tick.SymbolMap
```

**Sau sync, kiểm tra kết quả:**

```sql
SELECT SenSymbol, CTraderSymbolName, MappingStatus, MappingScore, Notes
FROM tick.v_SymbolMap
ORDER BY MappingStatus, SenSymbol;
```

**Chỉ symbol `MATCHED` mới được live ingest.** Nếu có `AMBIGUOUS` hoặc `NOT_FOUND`, operator phải xử lý thủ công (update `tick.SymbolMap` trực tiếp sau khi xác nhận).

---

## 12. Cơ chế chống duplicate — EventHash

**Công thức:**

```python
EventHash = SHA256(
    local_symbol.upper()     |
    str(ctrader_symbol_id)   |
    str(source_timestamp_ms) |
    str(bid_raw or "")       |
    str(ask_raw or "")       |
    quote_type.upper()
)
```

**Lưu ý quan trọng:** `source_mode` (`LIVE`/`HISTORICAL`) **không** tham gia vào hash. Điều này có nghĩa là:

- Cùng 1 market event (bid/ask/timestamp) xuất hiện cả trong live và historical backfill → **chỉ lưu 1 lần**.
- Trường `SourceMode` vẫn được lưu trong row để biết tick đó đến từ live hay backfill.
- Khi SQL insert duplicate, index `IGNORE_DUP_KEY = ON` silently skip — không raise error, không rollback.

---

## 13. Cơ chế an toàn dữ liệu — SQLite Spool

Khi SQL Server insert fail (network, restart, maintenance):

```
batcher.flush()
  └── insert_ticks() → FAIL
  └── spool.append_many(records)  ← tick vào SQLite local
  └── batcher.rows_spooled += N

flush_loop (mỗi 1 giây)
  └── batcher.drain_spool()
      └── spool.read_batch(500)
      └── insert_ticks(records)  ← thử lại vào SQL
      └── spool.delete_through(max_seq)  ← chỉ xóa khi insert thành công
```

**Spool path:** `data_provider/runtime/spool/ctrader_ftmo_tick_spool.db`

**Spool KHÔNG thay thế:**
- Production monitoring
- Alert khi SQL down kéo dài
- Backup đầy đủ

Spool giảm thiểu mất dữ liệu trong **lỗi ngắn hạn** (< vài phút). Nếu quá trình bị kill trong khi spool chưa drain, tick trong spool sẽ được drain trong lần chạy tiếp theo.

---

## 14. Cấu hình và biến môi trường

### `.env` (đọc bởi `python-dotenv`)

| Biến | Giá trị mặc định | Ghi chú |
|---|---|---|
| `CTRADER_CLIENT_ID` | *(bắt buộc)* | Client ID của Open API application |
| `CTRADER_CLIENT_SECRET` | *(bắt buộc)* | Client Secret |
| `CTRADER_FTMO_ENV` | `demo` | `demo` hoặc `live` |
| `CTRADER_REDIRECT_URI` | `http://localhost:8765/callback` | Phải khớp với application settings |
| `CTRADER_OAUTH_SCOPE` | `accounts` | Scope OAuth |
| `CTRADER_ACCESS_TOKEN` | *(từ token cache)* | Không cần set nếu có cache |
| `CTRADER_REFRESH_TOKEN` | *(từ token cache)* | Không cần set nếu có cache |
| `CTRADER_ACCOUNT_ID` | *(từ token cache)* | `ctidTraderAccountId` |
| `CTRADER_TRADER_LOGIN` | *(từ token cache)* | Login hiển thị trong UI |
| `DISCORD_WEBHOOK_URL` | *(optional)* | Webhook Discord để alert |
| `SQL_SERVER` | `localhost` | SQL Server instance |

### `config.py` (thông số vận hành)

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `CTRADER_FTMO_TICK_BATCH_SIZE` | 500 | Số tick mỗi batch SQL |
| `CTRADER_FTMO_TICK_FLUSH_SECONDS` | 1.0 | Flush tối đa sau N giây |
| `CTRADER_FTMO_TICK_QUEUE_MAXSIZE` | 50000 | Queue max (chưa dùng trong code hiện tại) |
| `CTRADER_FTMO_TICK_RECONNECT_MIN_SECONDS` | 5 | Backoff min |
| `CTRADER_FTMO_TICK_RECONNECT_MAX_SECONDS` | 300 | Backoff max |
| `CTRADER_FTMO_TICK_STALE_SECONDS_BTC` | 120 | Ngưỡng stale cho BTCUSD |
| `CTRADER_FTMO_TICK_STALE_SECONDS_MARKET` | 600 | Ngưỡng stale cho market giờ mở cửa |
| `CTRADER_FTMO_TICK_SCHEMA` | `tick` | Schema SQL |

---

## 15. OAuth và quản lý token

### Luồng OAuth (lần đầu hoặc khi cần login lại)

```
oauth-login
  └── build_authorization_url(client_id, redirect_uri, scope)
  └── Mở browser → user đăng nhập cTrader
  └── Spotware redirect về http://localhost:8765/callback?code=XXX
  └── Local HTTP server nhận code
  └── exchange_code_for_token(client_id, client_secret, code, redirect_uri)
  └── save_token_cache(payload) → ctrader_ftmo_oauth.json
  └── (optional) account-list --save-account-id → ghi account ID vào cache
```

### Token tự refresh

`ensure_fresh_access_token()` được gọi tại 2 thời điểm:
1. Khi `run_live_ingest()` khởi động
2. Khi `on_connected()` sau mỗi reconnect

Nếu token còn `< CTRADER_FTMO_TOKEN_REFRESH_SAFETY_SECONDS` (24h mặc định):
- Gọi `refresh_access_token()` tự động
- Lưu token mới vào cache
- Tiếp tục không gián đoạn

Nếu refresh fail nhưng access token cũ còn hạn: **cảnh báo + tiếp tục** với token cũ.
Nếu access token cũ đã hết hạn và refresh cũng fail: **dừng và báo lỗi rõ**.

### Trạng thái token hiện tại

```
ctidTraderAccountId : 47522998
traderLogin         : 7563609
accessToken         : có
refreshToken        : có
expires_at_utc      : 2026-07-11T00:28:58 UTC  (~30 ngày còn lại)
```

> Không cần chạy `oauth-login` lại. Token cache sẵn dùng ngay sau khi rebuild venv.

---

## 16. Phụ thuộc và môi trường Python

### Python version

- **Venv hiện tại:** BROKEN (Python 3.12 từ user profile Administrator, đã xóa)
- **Python trên PATH:** 3.13.12 (`C:\Users\ADMIN\AppData\Local\Programs\Python\Python313`)
- **Cần làm:** Rebuild `.venv` với Python 3.13

### Packages quan trọng

| Package | Version trong old venv | Có trên PyPI cho Py3.13? |
|---|---|---|
| `ctrader-open-api` | 0.9.2 | ✅ 0.9.2 |
| `twisted` | 24.3.0 | ✅ (phiên bản mới hơn) |
| `twisted-iocpsupport` | 1.0.4 | ✅ 25.10.1 |
| `requests` | 2.34.2 | ✅ |
| `pyodbc` | 5.3.0 | ✅ (có sẵn trên system Python 3.13) |
| `pandas` | - | ✅ |
| `flask` | - | ✅ |
| `redis` | - | ✅ |
| `python-dotenv` | - | ✅ |

### Rebuild venv

```powershell
Set-Location "Z:\SEN05_Autotrading"

# Xóa venv cũ
Remove-Item -Recurse -Force .venv

# Tạo venv mới với Python 3.13
python -m venv .venv

# Cài packages
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Kiểm tra
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick show-config
```

### Lưu ý `twisted_iocpsupport`

Trên Windows production, Twisted dùng `IocpReactor` (IOCP = I/O Completion Ports) để hiệu suất cao hơn `SelectReactor`. `twisted_iocpsupport` là C extension cần compile cho Python version cụ thể. Version 25.10.1 trên PyPI có wheel cho Python 3.13 Windows — không cần compile thủ công.

Để kiểm tra reactor đang dùng sau khi install:

```python
from twisted.internet import reactor
print(type(reactor).__name__)
# Mong muốn: IocpReactor (Windows) hoặc EPollReactor (Linux)
# Chấp nhận được: SelectReactor (hiệu suất thấp hơn nhưng vẫn hoạt động)
```

---

## 17. Checklist triển khai — thứ tự ưu tiên

### Bước 1 — Rebuild Python environment

```powershell
Set-Location "Z:\SEN05_Autotrading"
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Verify:**

```powershell
.\.venv\Scripts\python.exe -m data_provider.apps.ctrader_ftmo_tick show-config
# Kỳ vọng: in env, endpoint, 11 symbols, missing_api_fields=none
```

---

### Bước 2 — Apply SQL schema tick

```powershell
sqlcmd -S localhost -d SEN05_AutoTrading -E -C -b `
    -i "data_provider\sql\07_ctrader_ftmo_tick.sql"
```

**Verify:**

```sql
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'tick'
ORDER BY TABLE_NAME;
-- Phải có 15 rows: AccountProfile, BTCUSD, DE40, FR40, GOLD, HK50,
--   IngestRun, IngestState, J225, SP35, SymbolMap, UK100, US100, US30, US500
```

```sql
SELECT SymbolID, SenSymbol, MappingStatus FROM tick.SymbolMap ORDER BY SymbolID;
-- Phải có 11 rows, tất cả MappingStatus = 'PENDING'
```

---

### Bước 3 — Kiểm tra token

```powershell
.\.venv\Scripts\python.exe -m $app token-status
# Kỳ vọng: access_token=set, refresh_token=set, not_expired=true
```

---

### Bước 4 — Xác nhận account

```powershell
.\.venv\Scripts\python.exe -m $app account-list
# Xem output, xác nhận ctidTraderAccountId=47522998
# Nếu account ID đã trong cache (thường là có), không cần --save-account-id
```

---

### Bước 5 — Symbol sync

```powershell
# Dry run trước — xem proposed mapping
.\.venv\Scripts\python.exe -m $app symbol-sync

# Audit từng dòng output:
# US30: MATCHED score=100 remote=US30 ...     ← OK
# GOLD: MATCHED score=96  remote=XAUUSD ...   ← OK
# DE40: AMBIGUOUS ...                          ← cần xem xét

# Khi mapping hợp lý, apply
.\.venv\Scripts\python.exe -m $app symbol-sync --apply
```

**Verify sau apply:**

```sql
SELECT SenSymbol, CTraderSymbolName, MappingStatus, MappingScore
FROM tick.v_SymbolMap ORDER BY SenSymbol;
-- Phải có ít nhất các symbol quan trọng là MATCHED
-- AMBIGUOUS cần operator xem xét trước khi live
```

---

### Bước 6 — Smoke test

```powershell
.\.venv\Scripts\python.exe -m $app live --smoke-seconds 300
# Chạy 5 phút, tự dừng
```

**Trong khi chạy, quan sát log stdout:**

```
INFO - subscribed to X cTrader spot symbols
INFO - flushed Y ticks for Z symbols
```

**Sau khi dừng, verify SQL:**

```sql
-- Kiểm tra IngestRun
SELECT TOP 5 AppName, Status, RowsInserted, RowsSpooled, StartedAtUtc, StoppedAtUtc
FROM tick.IngestRun ORDER BY StartedAtUtc DESC;

-- Kiểm tra health
SELECT * FROM tick.v_IngestHealth ORDER BY SenSymbol;

-- Kiểm tra tick thực tế
SELECT TOP 20 TickTimeUtc, Bid, Ask, Mid, Spread, QuoteType, SourceMode
FROM tick.US30 ORDER BY TickTimeUtc DESC;

SELECT TOP 20 TickTimeUtc, Bid, Ask, Mid, Spread, QuoteType
FROM tick.GOLD ORDER BY TickTimeUtc DESC;

SELECT TOP 20 TickTimeUtc, Bid, Ask, Mid, Spread, QuoteType
FROM tick.BTCUSD ORDER BY TickTimeUtc DESC;
```

---

### Bước 7 — Production live (chỉ sau smoke thành công)

```powershell
.\.venv\Scripts\python.exe -m $app live
```

Để chạy ở background (không block terminal):

```powershell
Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m data_provider.apps.ctrader_ftmo_tick live" `
    -RedirectStandardOutput "data_provider\runtime\logs\tick_live.out.log" `
    -RedirectStandardError  "data_provider\runtime\logs\tick_live.err.log" `
    -NoNewWindow
```

---

## 18. Câu hỏi thường gặp khi vận hành

**Q: Token hết hạn trong khi live đang chạy?**
A: `ensure_fresh_access_token()` được gọi mỗi lần reconnect. Nếu còn refresh token, tự refresh. Nếu cả hai đều hết → dừng rõ ràng với lỗi. Chạy `oauth-login` để lấy token mới.

**Q: SQL Server restart trong khi live đang chạy?**
A: Tick tiếp tục nhận về, ghi vào SQLite spool. Khi SQL phục hồi, `drain_spool()` trong flush_loop tự drain lại. `RowsSpooled` trong `IngestRun` sẽ phản ánh số tick phải spool.

**Q: `symbol-sync` trả về AMBIGUOUS cho một symbol?**
A: Xem cột `Notes` trong `tick.SymbolMap` (hoặc `--apply` output) để biết tên nào đang cạnh tranh. Tìm tên đúng trên portal FTMO, sau đó UPDATE thủ công:
```sql
UPDATE tick.SymbolMap
SET CTraderSymbolId = <id đúng>,
    CTraderSymbolName = '<tên đúng>',
    MappingStatus = 'MATCHED',
    Notes = 'manual fix'
WHERE SymbolID = <id>;
```

**Q: Muốn tắt một symbol không lấy tick?**
A: Update `Enabled = 0` trong `tick.SymbolMap`, restart live service. Symbol đó sẽ không được include trong `fetch_matched_symbols()`.

**Q: Backfill bị gián đoạn giữa chừng?**
A: `IngestRun` sẽ ở trạng thái `FAILED`. Tick đã được ghi vẫn còn trong bảng (không bị xóa). Chạy lại `backfill` với cùng khoảng thời gian — `IGNORE_DUP_KEY` đảm bảo không duplicate.

**Q: Làm sao biết tick đang về live?**
A: Xem `tick.v_IngestHealth` → cột `LastHeartbeatAtUtc` phải ≤ vài giây so với hiện tại.

---

## 19. Những gì chưa có và hướng phát triển tiếp

### Đã có sẵn nền tảng, chưa code

| Feature | Mức độ | Mô tả |
|---|---|---|
| **Discord alert khi lỗi / reconnect** | Thấp | `service_live.py` chỉ dùng `logging`. Module `data_provider.common.notifications.tg_alert()` có sẵn, chỉ cần import và gọi tại `on_error` / `on_disconnected` |
| **Hourly report qua Discord** | Thấp | Giống cơ chế `_status_reporter()` trong `ws_live.py`. Dùng `reactor.callLater(3600, _hourly_report)` trong `service_live.py`. Report: tick/hour per symbol, spool count, token TTL |
| **Discord startup announce** | Rất thấp | Gọi `tg_alert("INFO", ...)` ngay sau `subscribe` trong `on_authed()` |

### Cần thiết kế thêm

| Feature | Mức độ | Mô tả |
|---|---|---|
| **Tick Health tab trong chart_server.py** | Trung bình | Thêm `GET /api/tick/health` query `tick.v_IngestHealth`. Thêm tab HTML auto-refresh. Không cần đụng OHLCV logic |
| **Token refresh daemon / watchdog** | Trung bình | Hiện tại token refresh chỉ trigger khi có reconnect hoặc startup. Cần scheduled check độc lập để alert trước khi token hết hạn |
| **Tick chart realtime trong browser** | Cao | Cần endpoint streaming (SSE hoặc WebSocket) + frontend JavaScript mới |
| **Historical backfill theo policy overlap** | Trung bình | Cần thống nhất chính sách: khi live đang chạy, backfill đến đâu là hợp lý |
| **Monitor process uptime** | Trung bình | `tick.IngestRun` có `RUNNING` nhưng nếu process crash, status không tự cập nhật. Cần watchdog kiểm tra PID |

### Không nên làm (rủi ro)

- **Không gộp tick vào `DWH.Fact_OHLCV`** — tick không phải nến, structure khác nhau.
- **Không hard-code tên symbol cTrader** — tên thay đổi theo broker configuration.
- **Không bỏ qua bước `symbol-sync` trước live** — nếu `AMBIGUOUS` mà ignore, tick có thể ghi nhầm symbol.

---

## 20. Tài liệu liên quan

| File | Nội dung |
|---|---|
| `data_provider/tick_data/README.md` | Quick reference cho operator |
| `data_provider/sql/07_ctrader_ftmo_tick.sql` | DDL đầy đủ schema `tick` |
| `tests/test_ctrader_ftmo_tickdata.py` | 23 targeted tests, chạy offline không cần SDK |
| `config.py` (phần CTRADER_*) | Tất cả biến cấu hình cTrader tick |
| `data_provider/common/notifications.py` | Discord webhook module (`tg_alert`, `tg_send`) |
| `data_provider/apps/ws_live.py` | Pattern tham khảo: hourly report + Discord alert |

---

*Document này được tạo từ code review trực tiếp tại `Z:\SEN05_Autotrading` ngày 2026-06-11.*
*Trạng thái hệ thống trong mục 3 phản ánh thực tế tại thời điểm kiểm tra.*
