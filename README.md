# SEN05 - Auto Trading Research System

README này được viết lại theo code hiện tại của repo SEN05. Nguồn sự thật là
`config.py`, các package Python, SQL installer, ops scripts và test suite trong
repo, không phải các README cũ.

SEN05 hiện là một hệ thống nghiên cứu và vận hành dữ liệu giao dịch gồm 5 phần:

- `data_provider`: kéo OHLCV từ TradingView/Capital.com, nạp SQL Server, kiểm tra và sửa dữ liệu.
- `core_python`: dashboard chiến lược, export signal CSV và watcher gửi cảnh báo Telegram/Discord.
- `backtest_optimize`: lab trung lập chiến lược để thử entry, SL/TP, sizing, position management trên signal CSV.
- `modules`: thư viện DB/data/indicator legacy đang được data provider và dashboard sử dụng.
- `cbot_calgo`: mã cTrader/cAlgo robot và package `.algo` phục vụ kiểm chứng bên cTrader.

Hệ thống này không tự đặt lệnh live từ Python. Watcher hiện tại chỉ gửi cảnh báo
signal và export CSV; execution thật, nếu có, nằm ngoài runtime Python này.

## Kiến Trúc Nhanh

```text
TradingView / CAPITALCOM
        |
        v
data_provider/apps/
  pipeline.py        full load, gap fill, scoped reset/reload
  ws_live.py         updater gần realtime theo batch
  checker.py         scan, continuity check, repair/rebuild dữ liệu
  chart_server.py    data warehouse dashboard
        |
        v
SQL Server: SEN05_AutoTrading
  dbo.Symbol
  DWH.Dim_Symbol
  DWH.Dim_Timeframe
  DWH.Dim_Date
  DWH.Fact_OHLCV
  SEN.TF_* staging tables
  SEN.ActiveTask runtime locks
  MART.v_OHLCV, MART.usp_GetLatestCandles
        |
        v
core_python/
  data.loader        đọc OHLCV từ DWH.Fact_OHLCV
  engine             chạy strategy request cho chart/export
  chart.server       Flask dashboard chiến lược
  notify             watcher 24/7 và notifier
  strategies         combo, ma_cross, ai_trend, knn_combo
        |
        +--> Telegram/Discord alerts
        +--> CSV exports
        +--> raw_signals/ dùng cho backtest_optimize
```

## Yêu Cầu Môi Trường

- Windows là môi trường vận hành chính cho `ops/*.ps1`.
- Python `>=3.10`.
- SQL Server với ODBC Driver 18.
- TradingView auth nên dùng cookie/token; guest mode bị code pipeline chặn vì dễ thiếu dữ liệu.

Cài nhanh:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

`pyproject.toml` install các package `modules`, `data_provider`, `core_python`.
`backtest_optimize` có package local nhưng không nằm trong danh sách package
editable install hiện tại, nên khi dùng trực tiếp hãy chạy từ repo root.

## Biến Môi Trường

Repo đọc `.env` qua `python-dotenv` nếu có. Không hard-code credential vào code.

```env
# SQL Server
SQL_SERVER=localhost
SQL_UID=
SQL_PWD=
SQL_ENCRYPT=no
SQL_TRUST_SERVER_CERT=yes

# TradingView
TV_AUTH_TOKEN=
TV_COOKIE=
TV_USERNAME=
TV_PASSWORD=

# Data-provider notification
DISCORD_WEBHOOK_URL=

# Core signal watcher notification
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
COMBO_DISCORD_WEBHOOK_URL=
```

Một số env nâng cao được `config.py` dùng:

- `HISTORICAL_PROVIDER`, mặc định `websocket`.
- `ENABLE_COMPUTED_TIMEFRAMES`, mặc định `0`.
- `TV_WS_HISTORY_ENDPOINT`, mặc định `prodata`.
- `TV_WS_HISTORY_FALLBACK_ENDPOINTS`, mặc định `data`.
- `TV_WS_HISTORY_REQUEST_MORE_ROUNDS`, mặc định `5`.
- `TV_WS_HISTORY_REQUEST_MORE_BARS`, mặc định `50000`.
- `TV_WS_HISTORY_TIMEOUT_SEC`, mặc định `45`.
- `TV_WS_REPLAY_ENABLED`, mặc định `1`.
- `TV_WS_REPLAY_ENDPOINT`, mặc định `prodata`.
- `TV_WS_REPLAY_START_DATE`, mặc định `1970-01-01`.
- `TV_WS_REPLAY_TFS`, mặc định `M5,M10,M15,M20,M30,M45,H1`.
- `TV_WS_REPLAY_WINDOW_BARS`, mặc định `5000`.
- `TV_WS_REPLAY_STEP_BARS`, mặc định `5000`.
- `TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR`, mặc định `1000`.
- `TV_WS_REPLAY_ADVANCE_FACTOR`, mặc định `1.25`.
- `TV_WS_REPLAY_TIMEOUT_SEC`, mặc định `30`.
- `STAGING_INSERT_CHUNK_ROWS`, mặc định `50000`.

## Cấu Hình Trung Tâm

`config.py` là bảng điều khiển runtime chính cho data provider:

- SQL: server mặc định `localhost` qua `SQL_SERVER`, database `SEN05_AutoTrading`, driver `ODBC Driver 18 for SQL Server`.
- Symbols: 37 instrument từ Capital.com gồm Indice, FOREX, Metal, Crypto.
- Timeframes trực tiếp: 15 TF `M5, M10, M15, M20, M30, M45, H1, M90, H2, H3, H4, H6, H8, D1, W`.
- Historical bar counts: cấu hình riêng từng TF, ví dụ `H1=20000`, `M5=20000`, `D1=5000`.
- Live bar count: `N_BARS_LIVE = 5`.
- Computed TF fallback: `M10, M20, M90, H6, H8` chỉ bật khi `ENABLE_COMPUTED_TIMEFRAMES=1`.

Điểm quan trọng: theo code hiện tại, M10/M20/M90/H6/H8 cũng được kéo trực tiếp
từ TradingView WebSocket. Cơ chế aggregate từ TF nhỏ hơn vẫn tồn tại như fallback
và công cụ rebuild, nhưng không phải mặc định.

## Database

Khởi tạo database bằng các script trong `data_provider/sql/`:

```text
00_run_all.sql
01_setup_database.sql
02_core_tables.sql
03_staging_tables.sql
04_business_objects.sql
05_verify.sql
06_active_task.sql
07_ctrader_ftmo_tick.sql
```

Các object chính:

- `dbo.Symbol`: master list 37 symbols, ID ổn định.
- `DWH.Dim_Symbol`: warehouse copy của symbol metadata.
- `DWH.Dim_Timeframe`: 15 timeframe cố định.
- `DWH.Dim_Date`: date dimension.
- `DWH.Fact_OHLCV`: bảng nến trung tâm, unique theo `SymbolID, TimeframeID, BarTime`.
- `SEN.TF_*`: 15 staging table, mỗi TF một bảng, unique theo `SymbolID, BarTime`.
- `SEN.ActiveTask`: lock runtime cho pipeline, checker, ws live và watcher.
- `tick.*`: schema riêng cho cTrader/FTMO tick data, gồm `tick.SymbolMap`, `tick.IngestRun`, `tick.IngestState` và 11 bảng tick theo symbol.
- `DWH.usp_LoadDirect`: nạp staging vào `Fact_OHLCV`.
- `DWH.usp_AggregateFromStaging`: aggregate fallback/rebuild.
- `MART.v_OHLCV`, `MART.usp_GetLatestCandles`: read API thân thiện cho SQL/manual usage.

## Data Provider

### Pipeline

`data_provider/apps/pipeline.py` điều phối full load, gap fill, filter scope,
safe reset và replay bootstrap.

```powershell
python data_provider/apps/pipeline.py
python data_provider/apps/pipeline.py --mode auto --dry-run
python data_provider/apps/pipeline.py --mode gap
python data_provider/apps/pipeline.py --mode full --replay off
python data_provider/apps/pipeline.py --mode full --replay on --replay-tfs H1,M45
python data_provider/apps/pipeline.py --symbols GOLD,BTCUSD --timeframes M45 --reset
python data_provider/apps/pipeline.py --asset-type Indice --timeframes H1 --reset --yes
```

CLI chính theo code:

- `--mode auto|gap|full`
- `--dry-run`
- `--symbols US30,GOLD`
- `--timeframes M45,H1`
- `--asset-type Indice,FOREX,Metal,Crypto`
- `--reset`
- `--yes`
- `--force-unlock`
- `--replay config|on|off`
- `--replay-tfs`, `--replay-endpoint`, `--replay-start-date`
- `--replay-max-windows`, `--replay-window-bars`, `--replay-step-bars`, `--replay-timeout-sec`

Safety hiện có trong code:

- `--reset` bắt buộc có scope: symbol, timeframe hoặc asset type.
- Full/reset bị block nếu `ws_live_runtime` đang active.
- Pipeline dùng historical job lock và warehouse maintenance lock.
- Guest TradingView mode bị coi là lỗi để tránh nạp thiếu history.

### WS Live

`data_provider/apps/ws_live.py` là updater gần realtime theo batch. Nó dùng
TradingView WebSocket, staging tables, ETL direct, watermark, spool/cache runtime
và lock để phối hợp với checker/pipeline.

```powershell
python data_provider/apps/ws_live.py
```

Runtime local nằm trong:

```text
data_provider/runtime/logs/
data_provider/runtime/cache/
data_provider/runtime/run/
data_provider/runtime/spool/
```

### Checker / Repair

`data_provider/apps/checker.py` kiểm tra chất lượng dữ liệu và có các mode sửa
hoặc rebuild.

```powershell
python data_provider/apps/checker.py --dry-run
python data_provider/apps/checker.py --dry-run --sym GOLD
python data_provider/apps/checker.py --dry-run --tf H1
python data_provider/apps/checker.py --co-check --co-days 7
python data_provider/apps/checker.py --dry-run --tf-check
python data_provider/apps/checker.py --dry-run --rebuild-computed
```

Các option trực tiếp khác của `checker.py`:

- `--threshold`
- `--tf-check-full`
- `--manual-confirm` deprecated: chỉ gửi notice qua Discord rồi vẫn auto-repair vì webhook là một chiều.

Checker chỉ sửa trong phạm vi dữ liệu nó vừa kiểm tra. Nếu phát hiện drift mang
tính hệ thống, checker sẽ repair checked window và log rõ rằng deep history/full
reload thuộc trách nhiệm của `pipeline.py`; nó không tự kích hoạt Replay lịch sử sâu.

### Data Chart

`data_provider/apps/chart_server.py` là Flask dashboard để xem warehouse data,
health summary, staging backlog và lock status.

```powershell
python data_provider/apps/chart_server.py
```

Mặc định mở tại:

```text
http://127.0.0.1:8050/
```

API chính:

- `/api/symbols`
- `/api/timeframes`
- `/api/candles`
- `/api/health/summary`
- `/api/health/matrix`
- `/api/health/staging`
- `/api/health/locks`

## Ops Layer

Nên ưu tiên chạy qua `ops/data_provider_app.ps1` vì script này gọi entrypoint
ổn định qua `ops/lib/Sen05Ops.psm1`, tự tìm `.venv`, mở log, kiểm tra process
và thêm guard cho thao tác ghi dữ liệu.

Interactive:

```powershell
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1
```

Non-interactive:

```powershell
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command pipeline -Mode auto -DryRun
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command pipeline -Mode gap -FollowLog
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command pipeline -Mode full -Replay off
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command ws-live -Forever -FollowLog
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command checker -DryRun
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command checker -TfCheck -DryRun
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command chart
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command status
```

Các wrapper nhỏ còn tồn tại:

- `ops/run_data_chart.ps1`
- `ops/open_chart.ps1`
- `ops/open_chart.bat`
- `ops/run_app.bat`

## Core Python

`core_python` là tầng strategy dashboard, export và signal watcher.

```text
core_python/
  main.py                  CLI khởi động chart server
  config.py                metadata symbol/timeframe/default UI
  data/loader.py           đọc OHLCV từ SQL Server
  indicators/              SMA, EMA, MACD histogram, ATR, AI trend, Dow wave
  strategies/registry.py   registry StrategySpec
  strategies/combo/
  strategies/ma_cross/
  strategies/ai_trend/
  strategies/knn_combo/
  engine.py                orchestration cho dashboard/export
  chart/server.py          Flask API và static dashboard
  notify/                  watcher, state, formatter, notifier
  export/                  CSV export service
```

Chạy dashboard strategy:

```powershell
python -m core_python.main
python -m core_python.main --host 127.0.0.1 --port 8516
```

Mặc định:

```text
http://127.0.0.1:8516/
```

API chart strategy:

- `/api/config`
- `/api/scan`
- `/api/export`
- `/api/export/bulk`
- `/api/data-range`

## Strategy Registry

`core_python/strategies/registry.py` đăng ký 4 strategy:

- `combo`: MA/MACD/ATR signal, có level entry/SL/TP; có biến thể HTF trend filter.
- `ma_cross`: MA crossover single-timeframe, có levels theo ATR.
- `ai_trend`: multi-timeframe, H3 trend + M45 entry path, dùng AI/KNN trend và EMA entry.
- `knn_combo`: multi-timeframe visual strategy, KNN trend gate + Combo-style entry signal.

Pipeline chung:

```text
normalize_params
  -> add_indicators
  -> detect_signals
  -> add_levels
```

`core_python/engine.py` có route riêng cho các strategy multi-timeframe:

- `ai_trend`: load trend TF và entry TF, merge trend đã đóng vào entry.
- `knn_combo`: load trend TF và entry TF, filter entry theo KNN bias.
- `combo` nếu bật `HTF_TREND_ENABLED`: load entry TF và HTF trend frame.

## Signal Watcher

Watcher nằm ở `core_python/notify/signal_watcher.py`. Nó chạy theo bar close,
lọc bar đang mở theo mặc định, dedup bằng state file và gửi thông báo sau khi
send thành công.

Chạy thử:

```powershell
python -m core_python.notify.signal_watcher --dry-run --once
python -m core_python.notify.signal_watcher --warm-up
python -m core_python.notify.signal_watcher --log-file logs\watcher.log
```

Option đáng chú ý:

- `--backend auto|telegram|discord|none`; `none` là kill switch gửi thông báo.
- `--dry-run`
- `--once`
- `--warm-up`
- `--state-path`
- `--output-dir`
- `--bars`
- `--include-open-bar`
- `--no-export`
- `--strategy combo|ma_cross|ai_trend`
- `--symbols`
- `--tf`
- `--bar-close-buffer-seconds`, mặc định `5`.
- `--post-close-retry-seconds`, mặc định `5`.
- `--post-close-watch-seconds`, mặc định `10`.
- `--max-alert-age-minutes`
- `--health-interval-minutes`, còn tồn tại nhưng đã deprecated.
- `--quiet`

Production groups được định nghĩa trong `core_python/notify/scan_config.py`:

```text
AI Trend:
  symbols: GOLD, BTCUSD, US30, UK100, J225, HK50, DE40, EURUSD, USDJPY, GBPUSD, AUDUSD
  H3  h3_trend_change  400 bars
  M45 m45_entry_signal 1000 bars, TREND_BARS=400

Combo:
  symbols: FR40, DE40, HK50, J225, SP35, UK100, US500, US100, US30
  H1 500 bars
  H2 500 bars
  H3 400 bars
  H4 300 bars
```

`knn_combo` có code dashboard/export và watcher path generic, nhưng không nằm
trong `SCAN_GROUPS` mặc định.

State runtime:

```text
core_python/runtime/state.json
```

Khi thêm symbol, TF hoặc strategy group production mới, nên dừng watcher, chạy
`--warm-up`, rồi khởi động lại để tránh gửi lại signal lịch sử.

## Backtest Optimize

`backtest_optimize` là lab execution trung lập chiến lược. Nó không tạo signal
mới và không thay thế backtest cTrader. Input chính là signal CSV có sẵn trong
`raw_signals/` và OHLCV đọc qua adapter nếu cần.

```text
backtest_optimize/
  contracts.py
  io/signal_loader.py
  io/market_data.py
  execution/
    sl_calculator.py
    tp_calculator.py
    cost_model.py
    sizing.py
    position_manager.py
    engine.py
  analysis/
    metrics.py
    optimize.py
    walkforward.py
    monte_carlo.py
    versioning.py
  configs/
    default_backtest.yaml
    param_grids.yaml
  notebooks/
```

Giả định cốt lõi được encode trong `contracts.py`:

- Bar timestamp là UTC-naive bar-open timestamp.
- Signal ở bar `i` vào lệnh tại open của bar `i+1`.
- Nếu một bar chạm cả SL và TP, mặc định dùng ambiguity policy `conservative`.
- Với mỗi bar, update/close cluster hiện có trước khi xét signal mới cùng bar.

Metrics ưu tiên là R-based và cluster-level, ví dụ expectancy theo R, hit
behavior, MAE/MFE xấp xỉ theo bar, ambiguity rate và stability.

## Raw Signals

`raw_signals/` chứa CSV signal đã export hoặc sinh từ hệ thống hiện tại:

```text
raw_signals/ai_trend/
raw_signals/combo/
raw_signals/knn_combo/
```

Các file này là input thực tế cho `backtest_optimize`, đồng thời là artifact
để đối chiếu logic signal giữa dashboard, watcher và cTrader.

## cTrader / cAlgo

`cbot_calgo/` chứa robot cTrader, template `.tp`, package `.algo`, solution
và project C#.

Các robot chính hiện thấy trong repo:

- `SEN_Combo_V0`, `SEN_Combo_V1`, `SEN_Combo_V2`
- `SEN_KNN_Combo_V0`, `SEN_KNN_Combo_V1`, `SEN_KNN_Combo_V2`
- `SEN_SignalExecutor_V0`
- `SEN_EMA_One`

Thư mục `cbot_calgo/cAlgo/Sources/Robots/AB_Test_Combo/` có script và artifact
phục vụ A/B backtest bên cTrader.

## Modules Legacy

`modules/` vẫn là layer dùng chung quan trọng:

- `db_connector.py`: connection pyodbc, merge staging, ETL direct, aggregate fallback, delete/upsert fact.
- `data_loader.py`: load symbol/timeframe/candle từ warehouse.
- `data_health_loader.py`: health summary, matrix, staging backlog, active locks cho data dashboard.
- `indicators.py`: indicator legacy dùng bởi chart/data workflows.

Không nên sửa công thức indicator hoặc DB helper ở đây nếu chưa kiểm tra tác động
đến data provider, dashboard và tests.

## Test Suite

Pytest config nằm trong `pyproject.toml`:

```powershell
pytest
pytest tests/test_notify_runtime_safety.py
pytest tests/test_data_provider_resilience.py
pytest -k "knn_combo or ai_trend"
```

Phạm vi test hiện tại gồm:

- SQL installer contract và drift guards.
- Data provider resilience: locks, merge/upsert, retry, spool, watermark, checker safety.
- Core strategy architecture và import boundaries.
- Combo, MA Cross, AI Trend, KNN Combo signal/level/payload behavior.
- Dashboard date range, export, scan defaults.
- Watcher runtime safety, dedup state, alert routing, summary.
- Execution/backtest legacy equivalence và portfolio/replay behavior.
- `backtest_optimize` auditor fixes và ambiguity policy.

Lưu ý: có thể gặp permission issue trong `tests/pytest/*` trên Windows nếu thư
mục temp/cache cũ bị lock. Pytest config đã ignore `tests/pytest`, nhưng nếu
tool search thủ công quét vào đó vẫn có thể bị Access denied.

## Cấu Trúc Repo

```text
SEN05/
  config.py
  pyproject.toml
  requirements.txt
  data_provider/
    apps/
    common/
    dashboard/
    runtime/
    sql/
    tools/
    tv/
  core_python/
    chart/
    data/
    export/
    indicators/
    notify/
    strategies/
  backtest_optimize/
    analysis/
    configs/
    execution/
    io/
    notebooks/
    tests/
  modules/
  ops/
    lib/Sen05Ops.psm1
    data_provider_app.ps1
  raw_signals/
  cbot_calgo/
  tests/
```

## Quick Reference

| Mục tiêu | Lệnh |
|---|---|
| Cài môi trường | `pip install -r requirements.txt; pip install -e .` |
| Khởi tạo DB | chạy `data_provider/sql/00_run_all.sql` |
| Data provider menu | `powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1` |
| Dry-run pipeline | `python data_provider/apps/pipeline.py --dry-run` |
| Gap fill | `python data_provider/apps/pipeline.py --mode gap` |
| Full load không replay | `python data_provider/apps/pipeline.py --mode full --replay off` |
| WS live | `python data_provider/apps/ws_live.py` |
| Checker dry-run | `python data_provider/apps/checker.py --dry-run` |
| Data dashboard | `python data_provider/apps/chart_server.py` |
| Strategy dashboard | `python -m core_python.main` |
| Watcher dry-run | `python -m core_python.notify.signal_watcher --dry-run --once` |
| Watcher warm-up | `python -m core_python.notify.signal_watcher --warm-up` |
| Tests | `pytest` |
