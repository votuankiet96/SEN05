# core_python — Kiến trúc & Quy chuẩn hệ thống

Tài liệu kiến trúc và quy chuẩn duy nhất của `core_python`. Hệ thống nội bộ
(SEN05), không có `requirements.txt`/`pyproject.toml`.

**Mọi khẳng định trong file này phải khớp code thật.** Bản này viết lại ngày
2026-10-05 bằng cách đọc lại từng file trong `src/`, `tests/`, `run_cp.sh`,
`deploy/` và đối chiếu với trạng thái chạy thật (Redis, systemd). Bản cũ mô tả
hệ thống trước 3 lần tái cấu trúc (tách `order_gateway`/`strategy_lab`, xoá
live, flatten thư mục) nên không còn dùng được.

## 1. Hệ thống này làm gì (và không làm gì)

`core_python` = hệ **backtest / past-signal**. Việc làm:

```text
SQL Server (DP6) OHLCV -> chỉ báo -> tín hiệu BUY/SELL + entry -> CSV / Redis DB0+DB1 / dashboard
```

1. Đọc OHLCV lịch sử từ SQL Server DP6 — **chỉ đọc** (SELECT), không ghi.
2. Chạy chiến lược kỹ thuật: chỉ báo → tín hiệu → điểm vào lệnh.
3. Xuất tín hiệu ra 4 đường: CSV, Redis (worker 24/7), dashboard sống, chart
   HTML tĩnh (mục 7).

**Không làm trong `core_python`**: không đọc nến từ Redis, không đặt lệnh,
không Discord, không tính SL/TP/risk-reward. Luồng live (đọc Redis DB0 của
DP → tín hiệu → OF) đã chuyển sang hệ khác (OG18), repo này không còn code
đó. `levels.py` chỉ điền `entry_price`/`entry_time`; `sl_price`, `tp_price`,
`risk_reward` vẫn được tạo nhưng **luôn NaN** để payload/dashboard không gãy.

**4 chiến lược đăng ký** (`configuration.STRATEGIES`):

| Key | Nhãn | Timeframe | Giới hạn cứng? | Trend filter KNN | Worker Redis chạy? |
|---|---|---|---|---|---|
| `combo` | Combo | khuyến nghị H1–H4 | Không | Có | Có (H1–H4) |
| `ma_cross` | MA Cross | M10/M20/M30/M45 | Có (`run_strategy` raise) | Có (cơ chế "chờ", mục 6) | Có (M10–M45) |
| `breakout_atr` | Breakout ATR (research) | M10–M45, H1–H4 | Có | **Không** (bật sẽ raise) | Không |
| `sma_trend` | SMA Trend (research) | M10–M45, H1–H4 | Có | **Không** (bật sẽ raise) | Không |

`breakout_atr`/`sma_trend` là chiến lược nghiên cứu (research_notes S005),
tham số ban đầu do người dùng duyệt để xem chart/export, **chưa có căn cứ
backtest**. `strategies/trend.py` **không phải** chiến lược — là module lọc
trend KNN dùng chung.

## 2. Nguyên tắc thiết kế — bắt buộc tuân thủ khi sửa/thêm code

- **Chỉ code khi đã được chốt/đồng thuận triển khai rõ ràng — áp dụng cho MỌI
  session.** Không tự ý sửa/thêm/xoá code hay file ngoài đúng phạm vi vừa
  được yêu cầu, kể cả khi thấy hợp lý hay "hiển nhiên". Bàn bạc, đề xuất,
  phân tích — làm tự do. Chỉ thực thi (sửa file, chạy lệnh đổi trạng thái)
  khi người dùng xác nhận rõ ràng (vd "triển khai đi", "làm đi", "chốt vậy").
  Không tự suy diễn đồng ý từ ngữ cảnh.
- **Hành động phá huỷ trên Redis → kiểm tra đối tượng trước khi làm.** Từ
  2026-10-05 Redis trên OG8 chỉ dành cho `core_python` (mục 5), nhưng vẫn phải
  xem `keyspace` trước khi `FLUSH*`: nếu thấy key lạ (không phải `L_PastSignal*`)
  thì dừng và hỏi — có thể hệ khác đã dùng lại server này.
- **Mỗi file một trách nhiệm duy nhất** (bảng mục 3).
- **Phụ thuộc một chiều, không vòng lặp.** `db_connector.py`/`indicator.py`/
  `levels.py` là tầng đáy (không import gì trong `src/`). `strategies/*.py`
  chỉ import `strategies/trend.py` (`combo.py`, `ma_cross.py`; hai chiến lược
  nghiên cứu không import gì). `configuration.py` nối indicator + strategies
  + levels thành pipeline. `og_signal/*`, `signal_display/*` là tầng wiring.
- **Không dư thừa, không phức tạp hoá.** Không tạo abstraction/setting cho
  trường hợp chưa xảy ra. Code chưa nối vào pipeline là orphan — xoá (đã có
  git history), không để "dự phòng".
- **Không ghi cột DataFrame mà không ai đọc.** Cột mới phải có nơi đọc thật
  (strategy, levels, payload, export, test).
- **Không có `__init__.py`** (namespace package, PEP 420). Ngoại lệ duy nhất:
  `tests/__init__.py` (pytest cần để resolve `from tests.fixtures import ...`).
- **Package import là `core_python.src.*`** nên tên thư mục repo PHẢI là
  `core_python` và mọi lệnh Python chạy từ **thư mục cha** (`~/Desktop`), không
  phải từ trong repo. `run_cp.sh` và unit systemd đã làm đúng (`WorkingDirectory=
  /home/administrator/Desktop`). Đổi tên thư mục repo là làm hỏng toàn bộ import
  và đường dẫn trong unit.
- **Tham số vận hành → `cp_config.yaml`. Cấu trúc/logic → code.** SQL, Redis
  (DB, prefix, kênh), symbol/bars mặc định, tham số chiến lược, timeframe hỗ
  trợ, công tắc trend... nằm ở `cp_config.yaml`. `configuration.py` và
  `redis_io/client.py` dùng `_require()` — thiếu key bắt buộc thì raise
  `KeyError` rõ ràng ngay lúc import/nạp config. 1 giá trị chỉ tồn tại ở 1 nơi.
  Ngoại lệ có chủ đích (default kỹ thuật thuần, nằm trong code):
  - `DowStructureParams` (left=3, right=5, min_atr_mult=0.5, atr_period=14) ở
    `indicator.py`, độc lập với `atr_period` của chiến lược.
  - Tham số KNN (`KNN_TREND_DEFAULT_PARAMS`) ở `strategies/trend.py`.
  - `REDIS_DEFAULTS` (port 6379, các timeout, healthcheck, reconnect) và
    `reconcile_interval_seconds` (1800) ở `redis_io/client.py` — operator vẫn
    đè được qua `redis:`/`redis.backfill_event:`.
  - **Lệch chuẩn đã biết:** `db_connector.py` KHÔNG dùng `_require()` — các hằng
    `SQL_*` có fallback im lặng (`str(_SQL.get("server") or "localhost")`...), và
    nếu thiếu file config thì `_load_config()` trả `{}` thay vì lỗi.
- **Dữ liệu khớp schema ngoài (DP6) → không hardcode.** Danh sách
  symbol/timeframe hợp lệ lấy từ `DWH.Dim_Symbol`/`DWH.Dim_Timeframe`, cache
  một lần mỗi tiến trình.
- **`strategies/*.py` CHỈ có logic quyết định BUY/SELL** — nhận `params` đã
  hoàn chỉnh từ `configuration.py`, không tính chỉ báo, không tính entry.
- **`levels.py` CHỈ có logic entry. `indicator.py` chỉ tính chỉ báo thuần.**
- **Test phải hermetic** — không test nào đụng SQL Server/Redis thật (mục 8).
- **Dọn artifact sau khi chạy/test thật**: `__pycache__/`, `.pytest_cache/`,
  `.ruff_cache/` (mục 9). KHÔNG tự xoá `runtime/exports/`.
- **Sửa code xong luôn chạy `ruff check .` + `vulture src tests
  --min-confidence 80` + `pytest -q`** (từ thư mục cha cho pytest, mục 8).
  Cả 3 phải sạch.

## 3. Kiến trúc — bảng trách nhiệm từng file

```text
core_python/                       (tên thư mục BẮT BUỘC, xem mục 2)
  cp_config.yaml                   Config THẬT (gitignored — chứa mật khẩu SQL/Redis).
  cp_config.example.yaml           Bản mẫu không secret (có trong git).
  run_cp.sh                        Launcher Linux: export CSV, mở dashboard, điều khiển worker.
  deploy/core-python-worker.service  Unit systemd user cho worker Redis.
  src/
    db_connector.py                Kết nối + MỌI dữ liệu từ DP6 (tầng đáy):
                                    get_connection() (retry), load(symbol, tf, n_bars,
                                    before=None), load_range(symbol, tf, from, to),
                                    load_range_with_warmup(symbol, tf, from, to,
                                    warmup_bars), symbols()/tf_minutes()/get_symbol().
    indicator.py                   Chỉ báo thuần (tầng đáy): sma/ema/wma/rma/
                                    macd_hist/atr (Wilder seed SMA, mục 6),
                                    add_dow_structure_indicators, calc_ai_trend_navigator
                                    (KNN), merge_trend_reference, timeframe_minutes,
                                    add_{combo,ma_cross,knn_trend,breakout_atr,sma_trend}_indicators.
    levels.py                      Entry: combo = high+X / low−X; ma_cross/breakout_atr/
                                    sma_trend = close. ma_cross copy thêm sl_dow (tham chiếu Dow).
    strategies/
      combo.py ma_cross.py         detect_<tên>_signals (CHỈ tín hiệu).
      breakout_atr.py sma_trend.py   Chiến lược nghiên cứu có TRẠNG THÁI (position, exit_*).
      trend.py                     Trend KNN dùng chung: trend_filter_enabled,
                                    apply_trend_filter (combo dùng), knn_trend_indicator_params.
    configuration.py               Tham số từng chiến lược (đọc cp_config.yaml) +
                                    PARAM_FIELDS + normalize_*_params + StrategySpec/
                                    STRATEGIES/get_strategy + run_strategy() +
                                    run_strategy_with_trend_reference() + trim_to_requested_range().
    og_signal/
      export_cli.py                CLI xuất CSV + signal_frame() (nguồn DUY NHẤT của
                                    schema CSV lẫn schema Redis — 2 đường không lệch nhau).
      redis_io/
        client.py                  Nạp cp_config.yaml cho worker, normalize_redis_config, create_client.
        listener.py                SUBSCRIBE kênh backfill, parse message, heartbeat.
        publisher.py               Ghi/đọc/xoá past-signal (LIST+HASH) trên Redis.
        worker.py                  Vòng đời worker: clear → bootstrap → subscribe → delta refresh.
        logging_setup.py           Log có cấu trúc (console INFO+, file DEBUG+, xoay vòng).
    signal_display/
      payload.py                   DataFrame -> candles/markers/overlays/panels/table/stats (thuần).
      renderer.py                  candles/markers -> 1 file HTML tĩnh tự chứa.
      cli.py                       Chart HTML tĩnh: load → run_strategy → payload → renderer.
                                    Giữ BARS_MIN/BARS_MAX (50/20000) + clamp_bars.
      live_page.py                 HTML dashboard sống.
      server.py                    Flask: /, /api/scan, /api/export, /health.
      vendor/lightweight-charts.js Vendor sẵn (không cần internet).
  tests/                           96 test, hermetic (mục 8).
  research_notes/                  Ghi chú nghiên cứu + scripts/t001_* (không phải code hệ thống).
  runtime/                         Output vận hành, gitignored: exports/ (CSV), logs/ (log worker) (mục 9).
```

**Luồng dữ liệu** — 2 đường, đều ở `configuration.py`:

```text
[A] 1 timeframe — run_strategy()
load() | load_range() | load_range_with_warmup()
  -> normalize_params -> add_indicators -> detect_signals -> add_levels

[B] 2 timeframe — run_strategy_with_trend_reference()
entry_bars + trend_bars (khung lớn, mặc định H4)
  -> add_indicators(entry) + add_knn_trend_indicators(trend)
  -> merge_trend_reference (chỉ trend bar ĐÃ ĐÓNG, không lookahead)
  -> detect_signals (lọc theo trend_bias) -> add_levels
```

`StrategySpec` ghim chết thứ tự [A]; mỗi bước nhận output bước trước.
`run_strategy_with_trend_reference` luôn ép `TREND_FILTER_ENABLED=True`
(cờ trong config chỉ ảnh hưởng đường [A]).

## 4. Hợp đồng với DP6 (SQL Server)

`db_connector.py` là file DUY NHẤT kết nối DB `SEN05_AutoTrading` trên DP6.
Schema kỳ vọng:

```text
DWH.Fact_OHLCV   SymbolID, TimeframeID, BarTime (UTC-naive, giờ MỞ bar),
                 [Open], High, Low, [Close], Volume
DWH.Dim_Timeframe  TimeframeID, Code ("M5","H1"...), Minutes
DWH.Dim_Symbol     SymbolID, Symbol, AssetType, IsActive (chỉ IsActive=1 được nạp)
```

Giả định (vi phạm sẽ tính sai): BarTime tăng dần/không trùng theo (Symbol,
TF); bar cuối có thể là bar đang mở (không có cột `is_closed`); BarTime nhất
quán UTC. `_validate()` loại dòng NaT/NaN OHLC, bỏ trùng (giữ dòng cuối), sort.

Từ Linux phải dùng **SQL Authentication** (`uid`/`pwd`) với `driver: freetds`
(Windows Integrated Auth lỗi `GSS_S_FAILURE` trên máy không join domain).

## 5. `cp_config.yaml` và Redis

`cp_config.yaml` ở gốc repo, **gitignored**. Cấu trúc (xem
`cp_config.example.yaml`):

```yaml
sql:   {server, database, uid, pwd, ...}   # driver/port/tds_version/retry... có default trong db_connector.py
redis:
  host: "127.0.0.1"
  password: ...
  backfill_event: {channel: "dp:events:backfill"}      # bắt buộc; reconcile_interval_seconds tuỳ chọn
  past_signal:        {db: 0, key_prefix: "L_PastSignal"}        # bắt buộc — không lọc trend
  past_signal_trend:  {db: 1, key_prefix: "L_PastSignal_Trend"}  # bắt buộc — có lọc trend
defaults:
  symbol: BTCUSD
  bars: 500                     # chart/dashboard (export_cli KHÔNG dùng — export mặc định full-history)
  warmup_bars: 300
  signal_start_date: "2024-01-01"   # mốc bắt đầu của worker Redis; KHÔNG áp dụng cho export_cli
strategies:
  combo / ma_cross / breakout_atr / sma_trend: {...}  # tham số, timeframe, trend, symbol_x (chỉ combo)
```

Thiếu `defaults`/`strategies.*` hoặc key con → `KeyError` lúc import. Thiếu
`redis.backfill_event.channel`, `redis.past_signal.{db,key_prefix}`,
`redis.past_signal_trend.{db,key_prefix}` → `KeyError` lúc nạp config worker.
Sửa `strategies:` có hiệu lực ở lần chạy kế (worker cần restart).
`PARAM_FIELDS` (min/max/label cho form dashboard/`--param`) là schema validate
đầu vào, không nằm trong config. `symbol_x`/`session_hours_utc` chỉ có ở
`combo` (X là buffer entry theo symbol; symbol thiếu → X=0.0).

### Redis trên OG8 — chỉ dành cho `core_python`

**Chốt 2026-10-05 (người dùng quyết định):** Redis server trên OG8
(`127.0.0.1:6379`) từ nay chỉ phục vụ phần chiến lược của `core_python`. Cùng
ngày đã `FLUSHALL` toàn bộ server (trước đó DB0 chứa `L_CANDLE_*` của DP, DB1 chứa
`L_SIGNAL_*`/`L_CHANNEL_*`/`L_OG_*` của OG18) rồi cho worker nạp lại.

| DB | Key (tiền tố) | Nội dung |
|---|---|---|
| **DB0** | `L_PastSignal_*` | past-signal không lọc trend (`past_signal`) |
| **DB1** | `L_PastSignal_Trend_*` | past-signal có lọc trend (`past_signal_trend`) |
| DB2, DB3 | — | không dùng (DB cũ của `core_python`, đã xoá) |

- Hệ khác (DP live, OG18, OF) **không còn ghi vào Redis này**. Nếu thấy key lạ
  (`L_CANDLE_*`, `L_SIGNAL_*`...) xuất hiện lại thì có hệ khác đang dùng chung
  — kiểm tra trước khi `FLUSH*` hay `clear_all_signals`.
- `clear_all_signals` quét đúng `{key_prefix}_*`, an toàn khi worker restart.
- Hai prefix không đè nhau vì ở 2 DB khác nhau; nếu đặt cả hai vào cùng 1 DB thì
  `L_PastSignal_*` sẽ khớp cả `L_PastSignal_Trend_*` — phải đổi prefix trước.
- Worker chỉ SUBSCRIBE `dp:events:backfill` — kênh này do DP publish lên server
  Redis; xác nhận DP vẫn publish lên đúng server này sau khi dọn.

### Warm-up chỉ báo (`defaults.warmup_bars`)

`warmup_bars` = số bar tải thêm NGAY TRƯỚC mốc `from` để chỉ báo kịp ổn định.
Đo thật: SMA chỉ cần đúng `period` bar; MACD (5/25/5) hội tụ gần như tức thì;
điểm nghẽn là **ATR period=14 của Dow structure** (lệch ~1% ở 30 bar, ~0,007%
ở 100, 0% ở 150). 300 là biên an toàn do operator chọn. Dùng chung cho:
`load_range_with_warmup()`, `export_cli._load_entry_range()` (có `--from`),
`signal_display/server.py` (chế độ khoảng ngày), `redis_io/worker.refresh_pair()`.
Sau khi chạy strategy, **caller phải cắt về đúng `bartime >= date_from`**
(`configuration.trim_to_requested_range`) — không thì tín hiệu trong đoạn
warmup lọt ra output.

## 6. Chiến lược

Logic "khi nào/hướng nào" luôn nằm trong khối banner comment ở
`strategies/<tên>.py`. `combo`/`ma_cross` **không có state machine giữa các
bar** — mỗi bar thoả điều kiện là bắn tín hiệu (OG không theo dõi lệnh thật).
`breakout_atr`/`sma_trend` thì **có trạng thái** (`position`, `exit_signal`).

- **Combo**: BUY = `close>open` & `close>ma` & `prev_close<ma` & `macd_h>0`;
  SELL đối xứng. Lọc session `session_hours_utc` (rỗng = mọi giờ). Entry =
  `high+X` (BUY) / `low−X` (SELL). Trend filter dùng `apply_trend_filter`:
  `signal` chỉ giữ khi `raw_signal == trend_bias` đúng bar đó.
- **MA Cross**: BUY = `prev_fast<=prev_slow` & `fast>slow` & `macd_h>0`; cần
  `atr` không NaN; SELL đối xứng. Entry = `close` + `sl_dow`. Trend filter
  (từ 2026-09-25) dùng `_apply_trend_wait`, **riêng của ma_cross**: bản chất
  là sự kiện CẮT nên nếu trend chưa khớp đúng bar cắt thì tiếp tục chờ (miễn
  xu hướng MA chưa đảo), nổ ở bar đầu tiên MACD và trend cùng khớp; giữ
  55–75% tín hiệu gốc (cách khớp-đúng-1-bar chỉ giữ 30–47%). **Tắt trend
  filter ⇒ `signal = raw_signal` bit-for-bit.** Cột `trend_filter_status`:
  `disabled|fired|pending|no_regime`.
- **Breakout ATR** (nghiên cứu): vào khi `close >= đỉnh close LOOKBACK_BARS bar
  trước` (SELL đối xứng nếu `ALLOW_SHORT`); thoát bằng ATR trailing stop (`stop_price`,
  `exit_*`). Vào lệnh market tại close.
- **SMA Trend** (nghiên cứu): giữ mua khi close > SMA, `ALLOW_SHORT` thì giữ bán
  khi close < SMA; BUY/SELL khi trạng thái chuyển, `exit_signal` khi trạng thái
  đổi. `StrategySpec.state_warmup_start` (2025-01-01) làm dashboard nạp dữ liệu
  từ mốc đó rồi mới cắt cửa sổ hiển thị, để tín hiệu không đổi theo cửa sổ xem.
- **Trend KNN** (`trend.py`): indicator deterministic (không phải model ML),
  chạy trên trend timeframe; `merge_trend_reference` gắn trend bar đã đóng
  (`trend_close_time <= entry bartime`). Mặc định tắt trong config.

### Công thức ATR/RMA — khớp TradingView

`atr()`/`rma()` dùng `_wilder_smooth()`: bar hợp lệ đầu seed bằng **SMA(period)**
rồi mới đệ quy `alpha=1/period`. **KHÔNG thay bằng `ewm(alpha=1/period,
adjust=False)` thuần** (seed bằng 1 điểm, lệch 2,5–4% ở bar đầu, cần +24..+81
bar mới hội tụ). `tests/test_indicators.py` khoá hành vi này.

### Thêm chiến lược mới — checklist

1. `indicator.py`: `add_<tên>_indicators`.
2. `strategies/<tên>.py`: `detect_<tên>_signals`.
3. `levels.py`: `add_<tên>_levels`.
4. `configuration.py`: `_<TÊN>_CFG` + hằng timeframe + `<TÊN>_DEFAULT_PARAMS` +
   `<TÊN>_PARAM_FIELDS` + `normalize_<tên>_params` + đăng ký `StrategySpec`.
5. `cp_config.yaml` **và** `cp_config.example.yaml`: mục `strategies.<tên>`.
6. `og_signal/export_cli.py`: `csv_columns()`/`signal_frame()` nếu schema CSV
   khác mặc định. **Cảnh báo:** `csv_columns()` trả schema mặc định
   (`bartime, atr, signal`) cho key lạ thay vì raise; chiến lược không có cột
   `atr` (vd `sma_trend`) vẫn ra CSV nhưng cột `atr` toàn rỗng.
7. `signal_display/server.py`: `_STRATEGY_OVERLAYS` (đường overlay trên chart).
8. `run_cp.sh`: dòng nhắc `Strategy (...)`.
9. Muốn worker Redis chạy chiến lược này: thêm vào `_STRATEGY_TIMEFRAMES`
   (`redis_io/worker.py`) — danh sách timeframe của các chiến lược **không được
   giao nhau**, vì `_strategy_for_timeframe()` map timeframe → đúng 1 chiến lược.

## 7. Input → Processing → Output

**Input**: (1) SQL Server DP6 — OHLCV; (2) `cp_config.yaml`; (3) trigger Pub/Sub
`dp:events:backfill` — JSON `{symbol, timeframe, from?, to?}` (`from`/`to` dạng
`YYYY-MM-DD HH:MM:SS` UTC-naive, tuỳ chọn); (4) tham số CLI/HTTP.

**Processing**: mục 3 (luồng [A]/[B]).

**Output** (4 đường):

1. **CSV** (`export_cli`, mặc định `runtime/exports/`):

   ```text
   {strategy}_{symbol}_{tf}_{label}_{mode}.csv      (bản trend nằm ở trend_filter/)
   label: full_history | from_YYYYMMDD | to_YYYYMMDD | YYYYMMDD_YYYYMMDD
   mode : nt (no_trend) | tf_<trend_type>_<TREND_TF>   vd tf_knn_H4
   ```

   Cột: `combo` → `bartime, atr, entry, signal`; còn lại → `bartime, atr, signal`.
   `signal` dùng mã **`ProtoOATradeSide` của cTrader: BUY=1, SELL=2** (không phải
   1/−1 nội bộ; quy đổi đúng 1 chỗ ở `signal_frame()`). `atr`/`entry` làm tròn 2
   chữ số; `bartime` dạng `YYYY-MM-DD HH:MM:SS`. Mặc định (không `--from/--to`)
   = toàn bộ lịch sử. Có `--from` thì tải kèm warmup rồi cắt lại.
   Dashboard `/api/export` dùng cùng `signal_frame()` nhưng tên file khác
   (`..._signals_{nt|tf_*}.csv`, theo `latest_{N}bars` hoặc khoảng giờ).
2. **Redis past-signal** (worker 24/7, mục 5):
   - `LIST {key_prefix}_{SYMBOL}_{TF}_{STRATEGY}` + `HASH <list>:{bartime}`
     (1 phép nối, bartime `YYYY-MM-DD HH:MM:SS`), field = đúng cột CSV
     (giá trị chuỗi). Vd `L_PastSignal_US30_H1_COMBO`,
     `L_PastSignal_Trend_US30_H1_COMBO`.
   - DB0 = không lọc trend (`past_signal`); DB1 = có lọc trend khung H4
     (`past_signal_trend`). Cùng schema, trend chỉ đổi dòng nào có signal.
   - **Không TTL, không LTRIM** — giữ toàn bộ lịch sử từ `signal_start_date`.
     Ghi bằng pipeline transaction `DELETE + HSET + RPUSH` (ghi đè idempotent).
   - Không còn kênh Pub/Sub báo ngược (bỏ 2026-09-26: 0 subscriber).
   - VM-BO20 (ctrader-cli) đọc CSV + Redis này — **đổi schema, tên key, mã
     `signal` hay DB đều phải báo bên đó**.
3. **Dashboard sống** (`signal_display.server`, cổng **8517**, mặc định
   `127.0.0.1`): form strategy/symbol/tf/bars/override, chart giá + MACD +
   chart trend khung lớn đồng bộ, bảng tín hiệu, nút Export CSV. Chế độ `latest`
   (N bar) hoặc `range` (`from_time`/`to_time`). Một `_DB_LOCK` tuần tự hoá truy
   vấn SQL.
4. **Chart HTML tĩnh** (`signal_display.cli`, mặc định `runtime/charts/`).

**Phạm vi worker Redis**: symbol = key của `strategies.combo.symbol_x` (11
symbol) × timeframe riêng từng chiến lược (combo: H1–H4; ma_cross: M10/M20/M30/
M45) = **88 cặp**, từ `signal_start_date`. Trigger ngoài phạm vi bị bỏ qua (DP
backfill cả universe rộng hơn).

**Vòng đời worker** (`redis_io/worker.py`):

1. Khởi động tiến trình → `clear_all_signals()` **đúng 1 lần** cho DB0 và DB1
   (chỉ tiền tố của ta; cờ `_CLEARED_THIS_PROCESS` phân biệt process restart
   với reconnect) → Redis sạch trước khi ghi.
2. `bootstrap_all()` full-recompute 88 cặp (~3–5 phút; lúc này DB0/DB1 chưa đủ
   dữ liệu của ta) — chạy lại ở mỗi lần reconnect Redis, không clear.
3. Subscribe `dp:events:backfill`. Mỗi trigger hợp lệ → `refresh_pair`:
   - có `from`+`to` → chế độ **delta**: tải `[from,to]`+warmup, tính lại, ghép vào
     dữ liệu hiện có (`_merge_delta_frame`: giữ dòng ngoài cửa sổ, thay hẳn dòng
     trong cửa sổ — kể cả khi frame rỗng, tín hiệu cũ phải biến mất);
     `from` bị kẹp không sớm hơn `signal_start_date`.
   - thiếu một trong hai → **full**: tính lại từ `signal_start_date`.
   - Mỗi pair publish 2 nơi (DB0 + DB1); DB1 tải thêm `trend_bars`.
   - Lỗi 1 pair (không phải `RedisError`) chỉ log + bỏ qua pair đó;
     `RedisError` propagate → tự reconnect sau `reconnect_delay_seconds`.
4. Heartbeat log mỗi `reconcile_interval_seconds` (1800s) — chỉ log "còn sống",
   **không quét lại dữ liệu**. Chưa có reconcile định kỳ: mất 1 message Pub/Sub
   thì chờ trigger kế hoặc lần bootstrap kế (restart/reconnect) mới bù.
5. SIGTERM chỉ set cờ `_SHUTDOWN` (không log/IO trong handler); vòng lặp tự
   thoát. Hạn chế đã biết: 1–2 dòng log cuối đôi khi không kịp ghi.

**Log**: `runtime/logs/cp_run.log` (DEBUG+, xoay vòng 700KB × 30 file ≈ 20MB, dọn file
>30 ngày) + journald (INFO+). Mỗi dòng: `<ts UTC> <level> component=<module.hàm>
event=<tên> pid=<pid> result=<ok|fail|skip> key=value...`.

## 8. Vận hành, test, kiểm tra

**Cài đặt** (không cài `core_python` như package):

```bash
python3 -m venv .venv
./.venv/bin/pip install pandas numpy pyyaml pyodbc flask redis        # chạy hệ thống
./.venv/bin/pip install pytest ruff vulture                           # phát triển/test
```

**Chạy** — tất cả từ **thư mục cha** của repo (`cd ~/Desktop`), hoặc dùng
`./core_python/run_cp.sh` (menu: 1 Export CSV, 2 Open Dashboard, 3 Manage
Past-Signal Worker):

```bash
core_python/.venv/bin/python -m core_python.src.og_signal.export_cli --strategy combo --symbol US30 --tf H1
core_python/.venv/bin/python -m core_python.src.og_signal.export_cli --strategy ma_cross --symbol US30 --tf M30 --from 2024-01-01 --to 2024-06-01 --trend-mode trend_filter --trend-tf H4
core_python/.venv/bin/python -m core_python.src.signal_display.cli --strategy combo --symbol US30 --tf H1
core_python/.venv/bin/python -m core_python.src.signal_display.server --port 8517
core_python/.venv/bin/python -m core_python.src.og_signal.redis_io.worker      # foreground
```

**Worker qua systemd user service** (không cần sudo; submenu 3 của `run_cp.sh`
bọc sẵn, và luôn `install` lại unit + `daemon-reload` trước khi start):

```bash
install -D -m 0644 core_python/deploy/core-python-worker.service ~/.config/systemd/user/core-python-worker.service
systemctl --user daemon-reload
systemctl --user enable --now core-python-worker.service      # start
systemctl --user restart core-python-worker.service
systemctl --user status core-python-worker.service
journalctl --user -u core-python-worker.service -f
```

`Restart=on-failure`, `RestartSec=5`, `StartLimitBurst=20`/300s. Bản unit đã
cài trong `~/.config/systemd/user/` có thể lệch bản trong `deploy/` (vd sau đổi
đường dẫn repo) — luôn cài lại trước khi start. **Mỗi lần worker start là clear
+ bootstrap lại DB0/DB1 phần của ta** (mục 7) — chấp nhận được có chủ đích vì
đây là dữ liệu backtest.

**Test/kiểm tra** (pytest chạy từ thư mục cha vì import là `core_python.src.*`):

```bash
cd ~/Desktop
PYTHONDONTWRITEBYTECODE=1 core_python/.venv/bin/python -m pytest core_python/tests -q -p no:cacheprovider   # 96 test
cd core_python
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m ruff check . --no-cache
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m vulture src tests --min-confidence 80
```

Test **không được đụng SQL Server/Redis thật**: `tests/conftest.py` có fixture
`autouse` nạp sẵn cache `db_connector.symbols()`/`tf_minutes()` bằng dữ liệu
tổng hợp. Cần `cp_config.yaml` tồn tại (import `configuration` đòi). File test:
`test_dashboard_server`, `test_db_connector`, `test_export_cli`,
`test_indicators`, `test_signal_display`, `test_strategies`. Phần lớn là
characterization/golden test — sửa logic có chủ đích thì chạy lại pipeline để
tính giá trị mới rồi cập nhật test, không đoán số. Cột không có reader
production nhưng test đang khoá giá trị (vd `entry_time`, `trend_filter_status`,
`dow_swing_low`) thì muốn xoá phải sửa test trước.

Vulture ở confidence 60 báo false positive: route Flask (`api_scan`,
`api_export`, `health`) và fixture autouse `_no_live_reference_data` — không
phải orphan.

## 9. Dọn dẹp

Sau mỗi lần chạy/verify bằng dữ liệu thật:

```bash
cd ~/Desktop/core_python
rm -rf .ruff_cache .pytest_cache
find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
```

**Không tự xoá `runtime/exports/`.** Đó là sản phẩm đầu ra, VM-BO20 mount qua
SSHFS và phụ thuộc trực tiếp (sự cố thật 2026-09-04: xoá nhầm 88 file CSV làm hỏng
1 lượt chạy backtest đang dở). Muốn dọn phải hỏi trước. `runtime/logs/` đã tự xoay vòng
(không dọn tay, trừ khi được yêu cầu). Redis: chỉ xoá tiền tố `L_PastSignal*`
(mục 5); chỉ `FLUSH*` khi người dùng yêu cầu rõ và đã kiểm tra `keyspace`.
