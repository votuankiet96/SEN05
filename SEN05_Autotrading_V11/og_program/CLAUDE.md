# core_python — Kiến trúc & Quy chuẩn hệ thống

Tài liệu này là bản kiến trúc chung và quy chuẩn duy nhất của `core_python`.
Đây là hệ thống nội bộ (SEN05, vận hành trên VM `vm-og`) — không có
CONTRACTS hay requirements.txt riêng lẻ theo kiểu dự án mã nguồn mở; mọi
thứ gom về một file này. Ngoại lệ duy nhất:
`research/README_REDIS_AUDIT.md` — tài liệu chi tiết riêng cho bộ công cụ
audit Redis (xem mục 8).

**Mọi khẳng định trong file này phải khớp code thật.** Bản này được viết
lại ngày 2026-09-13 bằng cách đọc lại từng dòng `core_python/` +
`og_signal/` và đối chiếu với `config.yaml` đang chạy.

## 1. Hệ thống này làm gì (và không làm gì)

`core_python` = **Order Generation (OG)**. Nhiệm vụ đúng 3 bước, không hơn:

```text
SQL Server (DP6) OHLCV -> tính chiến lược -> xuất tín hiệu BUY/SELL + entry
```

1. Đọc OHLCV lịch sử từ SQL Server trên DP6 — **chỉ đọc** (SELECT), không
   ghi.
2. Chạy chiến lược kỹ thuật: chỉ báo → tín hiệu BUY/SELL → điểm vào lệnh.
3. Xuất tín hiệu ra CSV (đưa đi backtest), hiển thị lên chart HTML tĩnh,
   hoặc xem qua dashboard sống trên trình duyệt.

**Không làm trong `core_python`**: Redis/PubSub, order execution, Order
Follower hay Discord. Luồng live nằm riêng ở `og_signal/`; tầng này gọi
`configuration.run_strategy()` theo phụ thuộc một chiều, còn `core_python`
không import ngược lại `og_signal`.

**SL/TP/risk-reward KHÔNG thuộc OG.** Đó là việc của OF (Order Follower).
`levels.py` chỉ điền `entry_price`/`entry_time`; các cột `sl_price`,
`tp_price`, `risk_reward` vẫn được tạo nhưng **luôn để rỗng (NaN)** cho
payload/dashboard cũ không gãy. Không có KTP, không có công thức TP nào
trong repo này nữa.

**3 chiến lược được đăng ký** (`configuration.STRATEGIES`):

| Key | Nhãn | Timeframe | Giới hạn cứng? |
|---|---|---|---|
| `combo` | Combo | khuyến nghị H1-H4 | Không — chạy được mọi TF hệ thống |
| `ma_cross` | MA Cross | M10/M20/M30/M45 | Có — TF khác bị `run_strategy` raise |
| `ema_cross` | EMA Cross | M10/M20/M30/M45 | Có |

`strategies/trend.py` **không phải** chiến lược thứ 4 — đó là module lọc
trend KNN dùng chung cho cả 3 (xem mục 6).

## 2. Nguyên tắc thiết kế — bắt buộc tuân thủ khi sửa/thêm code

- **Chỉ code khi đã được chốt/đồng thuận triển khai rõ ràng — áp dụng cho
  MỌI session, không riêng session nào.** Không tự ý sửa/thêm/xoá code hay
  file ngoài đúng phạm vi vừa được yêu cầu, kể cả khi thấy hợp lý, tiện làm
  luôn, hay là bước tiếp theo "hiển nhiên". Bàn bạc, đề xuất, phân tích,
  liệt kê phương án — làm tự do. Chỉ bắt đầu thực thi (sửa file, chạy
  lệnh có thay đổi trạng thái) khi người dùng xác nhận rõ ràng đã chốt
  hoặc đồng ý triển khai (vd "triển khai đi", "làm đi", "chốt vậy", "ok
  làm luôn") — không tự suy diễn sự đồng ý từ ngữ cảnh hay từ việc câu hỏi
  trước đó "có vẻ" đã được trả lời. Chốt ngày 2026-09-21 giữa lúc tái cấu
  trúc `core_python` thành `order_gateway`/`strategy_lab` — xem memory
  `feedback_require_explicit_go_ahead`.
- **Mỗi file một trách nhiệm duy nhất.** Không gộp, không lấn sang việc của
  file khác (xem bảng trách nhiệm ở mục 3).
- **Phụ thuộc một chiều, không vòng lặp.** `db_connector.py`/`indicator.py`/
  `levels.py` là tầng đáy (không import gì trong core_python).
  `strategies/*.py` chỉ import `indicator.py` và `strategies/trend.py`.
  `configuration.py` nối `indicator.py` + `strategies/*.py` + `levels.py`
  lại thành pipeline. `export_cli.py`/`signal_display/*` là tầng wiring
  trên cùng. (Đã kiểm bằng đồ thị import AST: 0 vòng lặp.)
- **Không dư thừa, không phức tạp hoá.** Không tạo abstraction/setting cho
  trường hợp chưa xảy ra. Không thêm file/class/layer nếu vài dòng lặp lại
  đơn giản hơn (vd `_to_int`/`_to_float` được phép trùng giữa các nơi nếu
  tách file làm tăng coupling không cần thiết).
- **Không ghi cột DataFrame mà không ai đọc.** Đợt dọn 2026-09-13 đã xoá 10
  cột loại này (`prev_ma`, `ema_gap`, `ema_gap_pct`, `ma_gap`, `ma_gap_atr`,
  `dow_swing_high`, `trend_aligned`, cột `trend_filter_enabled`,
  `trend_bias_segment`, `trend_window_end`). Thêm cột mới thì phải có nơi
  đọc thật (strategy, levels, payload, export, publisher hoặc test).
- **Không có `__init__.py`.** Package kiểu namespace (PEP 420, Python 3.3+)
  — chạy bằng `python -m core_python.<module>` từ đúng thư mục gốc repo,
  không cần cài đặt package. Ngoại lệ DUY NHẤT: `tests/__init__.py` — bắt
  buộc phải giữ vì pytest cần nó để resolve `from tests.fixtures import ...`
  đúng cách.
- **Tham số vận hành → `config.yaml`. Cấu trúc/logic hệ thống → code.**
  Bất kỳ giá trị nào operator có thể muốn chỉnh (SQL connection, symbol/bars
  mặc định, tham số chiến lược: X buffer, chu kỳ MA/MACD/ATR, session hours,
  timeframe hỗ trợ, công tắc trend...) phải nằm ở `config.yaml`, KHÔNG
  hardcode trong `.py`. **`core_python` không có fallback nào** — thiếu key
  nào trong `config.yaml` thì `_require()` raise `KeyError` rõ ràng ngay lúc
  import. 1 giá trị chỉ tồn tại ở đúng 1 nơi.
  - **Ngoại lệ đã biết, có chủ đích: `og_signal` giữ lại vài default kỹ
    thuật thuần trong code.** `og_signal/redis_client.py` vẫn giữ
    `REDIS_DEFAULTS` (port, timeout, healthcheck, reconnect delay) +
    `reconcile_interval_seconds`/`retention_seconds`/`retention_max_entries`
    trong code, vì đó là tinh chỉnh kỹ thuật chưa từng thật sự đổi trên
    khía cạnh operator. **8 key vận hành thật** (đã đổi nhiều lần, sửa
    xong chỉ cần restart service) bắt buộc khai ở `redis.input`/
    `redis.output` trong `config.yaml`, enforced bởi `_require()` riêng
    của `redis_client.py` — xem mục 5 để biết danh sách đầy đủ.
  - **Ngoại lệ thứ hai: default kỹ thuật thuần của indicator.**
    `DowStructureParams` (left=3, right=5, min_atr_mult=0.5, atr_period=14)
    sống trong `indicator.py` vì đó là công thức, không phải tham số vận
    hành. Production luôn gọi `add_dow_structure_indicators(out)` không
    truyền params → **luôn dùng đúng 4 giá trị trên**, độc lập hoàn toàn với
    `atr_period` của chiến lược. Tương tự, tham số KNN (`PRICE_VALUE`,
    `AI_K`...) nằm ở `strategies/trend.py: KNN_TREND_DEFAULT_PARAMS`.
  - **Ngoại lệ thứ ba: 10 mức KSL/KTP (`KSL_LEVELS`/`KTP_LEVELS` trong
    `levels.py`).** Công thức phi^(n/2) cố định, không đổi theo thời gian
    — cùng lý do với `DowStructureParams`. Đừng nhầm với **bảng tra theo
    (strategy, symbol, timeframe)** (`core_python/ksl_ktp.csv`) — đó MỚI
    là giá trị vận hành thật (kết quả walk-forward optimize, đổi mỗi lần
    optimize lại), nhưng nằm ở CSV riêng chứ không phải `config.yaml`, vì
    2 hệ thống khác nhau cùng cần đọc/ghi nó (`core_python` đọc,
    `backtest_optimize/` ghi) — xem mục 6.
- **Dữ liệu khớp schema ngoài (DP6) → không hardcode, luôn query trực
  tiếp.** Danh sách symbol/timeframe hợp lệ lấy thẳng từ
  `DWH.Dim_Symbol`/`DWH.Dim_Timeframe` trên DP6 (nguồn sự thật duy nhất),
  cache lại 1 lần/tiến trình — không phải danh sách tay trong code.
- **`strategies/*.py` CHỈ có logic quyết định tín hiệu BUY/SELL** — khi
  nào, hướng nào. Không giữ tham số, không tính chỉ báo, không tính entry,
  không tự validate/merge gì — luôn nhận `params` là dict đã hoàn chỉnh từ
  `configuration.py`.
- **`levels.py` CHỈ có logic entry** — vào giá nào, tại thời điểm nào.
  Không giữ tham số, không tự validate/merge gì.
- **`indicator.py` chỉ tính chỉ báo kỹ thuật thuần.** Không có logic
  signal/entry.
- **Test phải hermetic.** Không test nào được đụng SQL Server thật — xem
  mục 8.
- **Dọn sạch artifact sau khi chạy/test thật**: `__pycache__/`,
  `.pytest_cache/`, `.ruff_cache/` — xem mục 9 (KHÔNG bao gồm
  `runtime/exports/`).
- **Sửa code xong luôn chạy `ruff check .` + `vulture core_python og_signal
  tests --min-confidence 80` + `pytest -q`** trước khi coi là hoàn tất. Cả
  3 lệnh phải sạch, kể cả notebook trong `research/`.

## 3. Kiến trúc — bảng trách nhiệm từng file

```text
core_python/
  db_connector.py      Kết nối + cung cấp MỌI dữ liệu từ DP6 (tầng đáy):
                        - Nạp config.yaml (mục `sql:`), get_connection() có retry.
                        - load(symbol, tf, n_bars, before=None): N bar mới
                          nhất, hoặc N bar ngay trước 1 mốc thời gian (dùng
                          cho chart/dashboard + cuộn ngược lịch sử).
                        - load_range(symbol, tf, date_from, date_to): theo
                          khoảng thời gian, không giới hạn số bar (dùng cho
                          export_cli.py) — để trống cả 2 mốc = toàn bộ lịch
                          sử có trong DP6.
                        - load_range_with_warmup(symbol, tf, date_from,
                          date_to, warmup_bars): như trên, kèm tải thêm
                          warmup_bars bar TRƯỚC date_from để chỉ báo kịp
                          "ấm" — xem mục 5 "Warm-up chỉ báo".
                        - symbols()/tf_minutes()/get_symbol(): query trực
                          tiếp DWH.Dim_Symbol/Dim_Timeframe (không hardcode),
                          cache 1 lần/tiến trình.
                        KHÔNG giữ tham số chiến lược nào.

  indicator.py          Tầng đáy — chỉ tính chỉ báo kỹ thuật thuần:
                        - sma/ema/wma/rma/macd_hist/atr.
                        - add_combo_indicators / add_ma_cross_indicators /
                          add_ema_cross_indicators / add_knn_trend_indicators
                          (df, params) — params luôn truyền từ ngoài vào.
                        - add_dow_structure_indicators: swing Dow đã xác
                          nhận, không repaint -> sl_dow_buy/sl_dow_sell.
                        - calc_ai_trend_navigator: KNN trend (indicator
                          deterministic, KHÔNG phải model ML train offline).
                        - merge_trend_reference: merge_asof trend bar ĐÃ ĐÓNG
                          vào entry bar (không lookahead).
                        - timeframe_minutes: parser mã TF -> phút.

  levels.py             Điểm vào lệnh + SL/TP tuyệt đối (combo/ma_cross):
                        - add_combo_levels:     entry = high + X (BUY) / low - X (SELL).
                        - add_ma_cross_levels:  entry = close.
                        - add_ema_cross_levels: entry = close, entry_time lấy
                          từ entry_close_time nếu có.
                        - sl_price/tp_price (combo/ma_cross): entry ∓/± K×atr,
                          K = KSL/KTP tra theo (strategy,symbol,tf) từ
                          ksl_ktp.csv (đọc bởi configuration.py, xem mục 6).
                          ema_cross CHƯA có — sl_price/tp_price luôn NaN.
                        risk_reward LUÔN NaN (không có công thức, KSL/KTP
                        độc lập nhau, không theo tỉ lệ cố định).
                        ma_cross/ema_cross copy thêm sl_dow từ tham chiếu Dow
                        — tham chiếu phụ cho OF, KHÔNG phải sl_price ở trên.
  ksl_ktp.csv           Bảng KSL/KTP theo (strategy,symbol,timeframe) — kết
                        quả walk-forward optimize, KHÔNG phải config.yaml vì
                        đây là dữ liệu thay đổi mỗi lần optimize lại, khác
                        bản chất tham số vận hành ổn định. Mỗi ô là 1 mã
                        kiểu "KSL0618" (giải mã + validate bởi
                        levels.decode_level, xem mục 2 "Ngoại lệ thứ ba").
                        Thiếu dòng cho 1 (strategy,symbol,tf) → raise KeyError
                        rõ ràng lúc chạy, không fallback 0 (0 nghĩa là SL/TP
                        trùng giá vào lệnh).

  strategies/
    combo.py             CHỈ detect_combo_signals.
    ma_cross.py          CHỈ detect_ma_cross_signals.
    ema_cross.py         CHỈ detect_ema_cross_signals.
    trend.py             Trend reference dùng chung, KHÔNG phải strategy:
                          - trend_filter_enabled(params) -> bool.
                          - apply_trend_filter(df, params): raw_signal ->
                            signal (lọc theo trend_bias nếu bật).
                          - knn_trend_indicator_params: tham số công thức KNN.
                         Cả 4 file không giữ tham số vận hành nào.

  configuration.py      Tham số CHO TỪNG chiến lược (đọc từ config.yaml,
                        mục `strategies:`) + sổ đăng ký pipeline:
                        - DEFAULT_SYMBOL/N_BARS (mục `defaults:`).
                        - COMBO_*/MA_CROSS_*/EMA_CROSS_* — bắt buộc đọc từ
                          config.yaml qua _require(), không fallback.
                        - PARAM_FIELDS (schema validate/UI: min/max/label) +
                          normalize_*_params (merge + validate + clamp).
                        - StrategySpec + STRATEGIES + get_strategy().
                        - run_strategy(): pipeline 1 timeframe.
                        - run_strategy_with_trend_reference(): pipeline 2
                          timeframe (entry TF + trend TF).

  export_cli.py         CLI xuất CSV: load_range -> run_strategy -> CSV tín
                        hiệu (chỉ các dòng có signal). Mặc định (không
                        truyền --from/--to) = toàn bộ lịch sử có trong DP6.
                        Có --from thì tải kèm warmup (load_range_with_warmup)
                        rồi cắt kết quả về lại đúng khoảng yêu cầu sau khi
                        chạy strategy — xem mục 5 "Warm-up chỉ báo".
                        Hỗ trợ --trend-mode no_trend|trend_filter.

  signal_display/       Hiển thị BUY/SELL — 2 chế độ dùng chung payload.py:
    payload.py             DataFrame -> candles/markers/overlays/panels/
                            table/stats (JSON-safe, thuần, không I/O).
    renderer.py             candles/markers -> 1 file HTML tĩnh tự chứa.
    cli.py                   wiring chế độ tĩnh: load -> run_strategy ->
                            payload -> renderer -> ghi file .html.
                            Giữ BARS_MIN/BARS_MAX + clamp_bars dùng chung.
    live_page.py             HTML dashboard sống (form strategy/symbol/tf/
                            bars + param override, chart giá/MACD + chart
                            trend khung lớn đồng bộ, nút Export CSV).
    server.py                wiring dashboard sống: Flask, load ->
                            run_strategy -> payload -> JSON (/api/scan),
                            /api/export (CSV), /health, /.
    vendor/                  Lightweight Charts vendor sẵn (không cần internet).

tests/                  Test characterization — hermetic, xem mục 8.
config.yaml             Setting THẬT (gitignored) — xem mục 5.

og_signal/              Live wiring: Redis DB0 -> run_strategy -> Redis DB1
                        + Discord. Không chứa logic chiến lược riêng.
  redis_client.py        Kết nối Redis, dựng key, đọc snapshot nến, default
                          runtime Redis.
  redis_listener.py      SUBSCRIBE kênh Pub/Sub + nhịp reconcile định kỳ.
  signal_publisher.py    Dựng payload DB1 + publish + gửi Discord.
  live_worker.py         Vòng đời worker, định tuyến TF -> strategy, cửa sổ
                          hiệu lực signal.

research/               Công cụ audit Redis/SQL — xem mục 8 và
                        research/README_REDIS_AUDIT.md.
deploy/                 Unit file systemd user service — xem mục 7.
run_og.sh               Launcher Linux duy nhất — xem mục 7.
```

**Luồng dữ liệu 1 lượt chạy** — có đúng 2 đường, cả hai đều ở
`configuration.py`:

```text
[A] 1 timeframe — run_strategy()
db_connector.load() | load_range()
  -> normalize_params -> add_indicators -> detect_signals -> add_levels
  -> (export_cli.to_csv | signal_display.payload.* -> renderer/live_page)

[B] 2 timeframe — run_strategy_with_trend_reference()
entry_bars (TF vào lệnh) + trend_bars (TF khung lớn)
  -> add_indicators(entry) + add_knn_trend_indicators(trend)
  -> merge_trend_reference (chỉ trend bar ĐÃ ĐÓNG)
  -> detect_signals (apply_trend_filter lọc raw_signal -> signal)
  -> add_levels
```

`StrategySpec` ghim chết thứ tự của [A] (docstring `StrategySpec.Invariant`)
— không được đảo, mỗi bước nhận output bước trước làm input.

`export_cli.py` dùng `load_range()`; `signal_display/cli.py` và `server.py`
dùng `load()` (N bar gần nhất). Dashboard ở chế độ range thì `server.py`
ghép `load()` warmup + `load_range()` để chỉ báo đủ warmup trước mốc `from`.

## 4. Hợp đồng với DP6 (SQL Server)

`db_connector.py` là file DUY NHẤT kết nối + đọc (SELECT) từ database
`SEN05_AutoTrading` trên DP6. Schema kỳ vọng:

```text
DWH.Fact_OHLCV
  SymbolID     — khớp DWH.Dim_Symbol.SymbolID
  TimeframeID  — khớp DWH.Dim_Timeframe.TimeframeID (query qua JOIN theo Code)
  BarTime      — UTC-naive datetime, thời điểm MỞ của bar (không phải đóng)
  [Open], High, Low, [Close], Volume

DWH.Dim_Timeframe
  TimeframeID, Code (mã TF dạng chuỗi: "M5", "H1"...), Minutes

DWH.Dim_Symbol
  SymbolID, Symbol (mã TradingView, vd "US30"), AssetType,
  IsActive (chỉ symbol IsActive=1 được nạp)
```

`db_connector.symbols()`/`tf_minutes()` query trực tiếp 2 bảng
`Dim_Symbol`/`Dim_Timeframe` (không hardcode danh sách trong code) — cache
lại 1 lần trong tiến trình. Đổi cột/tên bảng phía DP6 sẽ làm core_python
lỗi ngay khi cần symbol/TF, không âm thầm lệch dữ liệu.

**Giả định core_python dựa vào** (vi phạm sẽ tính sai chỉ báo/tín hiệu):

- BarTime tăng dần, không trùng lặp cho cùng (SymbolID, TimeframeID).
- Bar cuối cùng trả về CÓ THỂ là bar đang mở (chưa đóng) — không có cột
  "is_closed" riêng, caller tự lọc nếu cần chỉ dùng bar đã đóng.
- BarTime là UTC-naive nhất quán (không lẫn giờ địa phương).

**Đã kiểm chứng thật (2026-07-07)**: từ Linux (`vm-og`), phải dùng **SQL
Authentication** (`uid`/`pwd` thật) với `driver: freetds`. Để trống uid/pwd
(Windows Integrated Auth) KHÔNG hoạt động trên máy Linux không join domain
— FreeTDS rơi về GSSAPI/Kerberos và lỗi thẳng (`gss_init_sec_context:
GSS_S_FAILURE`).

`export_cli.py` và `signal_display.cli` đều in `ERROR: <message>` + exit
code 1 cho cả lỗi input dự đoán được (strategy/symbol/timeframe không hợp
lệ) lẫn lỗi hạ tầng (không kết nối được SQL Server).

**Lưu ý về `db_connector.py`**: khác `configuration.py`, file này KHÔNG dùng
`_require()` — 12 hằng `SQL_*` đều có fallback im lặng
(`str(_SQL.get("server") or "localhost")`...). Hiện không gây lỗi vì
`config.yaml` thật có đủ key, nhưng đây là điểm lệch chuẩn đã biết so với
nguyên tắc mục 2.

## 5. `config.yaml` — nơi operator chỉnh trực tiếp

`config.yaml` ở gốc repo, **gitignored** (không commit — chứa mật khẩu SQL
thật, password Redis và webhook Discord). Không có file `.example` mẫu —
cấu trúc thật ghi lại đây:

```yaml
sql:
  server: 10.11.12.6        # địa chỉ DP6
  database: SEN05_AutoTrading
  driver: freetds            # Linux: bắt buộc freetds
  port: 1433
  tds_version: "7.4"
  uid: <thật>                 # SQL Authentication — để trống KHÔNG chạy trên Linux
  pwd: <thật>
  encrypt: false
  trust_server_cert: true
  retry_count: 3
  retry_delay_seconds: 5
  timeout_seconds: 30

redis:
  enabled: true
  host: "127.0.0.1"          # Redis chạy cùng máy OG8
  password: <thật>
  input:                      # 5 key vận hành bắt buộc — xem mục 5 bên dưới
    db: 0
    key_prefix: "L_CANDLE"
    event_channel: "dp:events:candles"
    snapshot_bars: 500
    process_on_startup: true
  output:                     # 3 key vận hành bắt buộc
    db: 1
    key_prefix: "L_SIGNAL"
    event_channel: "og:events:signals"

live:
  enabled_strategies: [combo, ma_cross]   # ema_cross CHƯA bật live
  log_level: INFO
  discord:
    enabled: true
    webhook_url: <thật>
    username: "OG Signal"
    timeout_seconds: 10
    retry_count: 2
    retry_delay_seconds: 2
  signal_validity:            # cửa sổ hiệu lực, tính từ lúc bar ĐÓNG
    combo:     {mode: next_bar, valid_bars: 1}
    ma_cross:  {mode: seconds_after_bar_close, seconds: 180}
    ema_cross: {mode: seconds_after_bar_close, seconds: 180}

defaults:
  symbol: BTCUSD               # dùng khi CLI không truyền --symbol
  bars: 500                    # dùng cho chart/dashboard (export_cli KHÔNG
                                # dùng key này — export mặc định lấy toàn bộ
                                # lịch sử)
  warmup_bars: 300              # số bar tải thêm TRƯỚC mốc --from/from_time
                                # để chỉ báo kịp "ấm" (xem mục "Warm-up chỉ
                                # báo" bên dưới) — khác bản chất với
                                # redis.input.snapshot_bars (đó là "xin
                                # Redis bao nhiêu", cố ý không gộp)

strategies:
  combo:
    ma_period: 20
    macd_fast: 5
    macd_slow: 25
    macd_signal: 5
    atr_period: 5
    recommended_timeframes: [H1, H2, H3, H4]
    default_timeframe: H4
    trend_filter_enabled: false
    trend_type: knn            # hiện chỉ chấp nhận "knn"
    trend_timeframes: [H1, H2, H3, H4]
    default_trend_tf: H4
    symbol_x:                  # buffer X (điểm) cộng/trừ khi tính Entry
      US30: 10.0
      US500: 1.0
      US100: 5.0
      DE40: 5.0
      UK100: 5.0
      FR40: 5.0
      SP35: 5.0
      HK50: 15.0
      J225: 15.0
      GOLD: 0.5
      BTCUSD: 50.0
    session_hours_utc: {}      # symbol: [giờ UTC]; rỗng = giao dịch mọi giờ

  ma_cross:
    fast_ma: 13
    slow_ma: 34
    macd_fast: 5
    macd_slow: 25
    macd_signal: 5
    atr_period: 5
    supported_timeframes: [M10, M20, M30, M45]
    default_timeframe: M30
    trend_filter_enabled: false
    trend_type: knn
    trend_timeframes: [H1, H2, H3, H4]
    default_trend_tf: H4

  ema_cross:
    fast_ema: 13
    slow_ema: 34
    atr_period: 5
    supported_timeframes: [M10, M20, M30, M45]
    default_timeframe: M45
    trend_filter_enabled: false
    trend_type: knn
    trend_timeframes: [H1, H2, H3, H4]
    default_trend_tf: H4
```

Sửa bất kỳ giá trị nào trong mục `strategies:` sẽ có hiệu lực ngay ở lần
chạy tiếp theo — không cần sửa `configuration.py`.

**Chỉ `combo` có `symbol_x`** — đúng vì chỉ Combo dùng buffer X để tính
entry; MA Cross và EMA Cross vào lệnh tại `close`.

**`strategies:` là bắt buộc, không có fallback nào trong code.** Thiếu file
hoặc thiếu bất kỳ key nào ở trên, `configuration.py` raise `KeyError` rõ
ràng ngay khi import (chỉ đúng tên key còn thiếu, vd `"config.yaml thiếu
'strategies.combo.ma_period' — xem CLAUDE.md mục 5"`).

Ngoại lệ suy diễn duy nhất: `ma_cross`/`ema_cross` không có
`recommended_timeframes` — `configuration.py` tự suy ra bằng đúng
`supported_timeframes` thay vì bắt operator gõ lại cùng 1 danh sách 2 lần.

`PARAM_FIELDS` (giới hạn min/max/label cho form dashboard/CLI `--param`)
KHÔNG nằm trong `config.yaml` — đó là schema validate đầu vào (an toàn phần
mềm), không phải giá trị vận hành chiến lược.

### `redis.input.*` / `redis.output.*` trong config.yaml

Chốt 2026-09-17 (mở rộng 2026-09-18 thêm `output.event_channel`): 8 key vận
hành thật của Redis nằm ở `config.yaml`, bắt buộc khai đủ — thiếu key nào
thì `normalize_redis_config()` (trong `og_signal/redis_client.py`,
`_require()` riêng của module này, cùng kiểu với `configuration._require()`)
raise `KeyError` rõ ràng ngay lúc load config, không có fallback. Tiêu chí
chọn các key này: **đã thật sự bị đổi nhiều lần trên khía cạnh operator**
(sửa `config.yaml` + restart service là đủ áp dụng) — không phải thông số
kỹ thuật thuần chưa từng cần đổi:

| Key | Giá trị thật hiện tại (`config.yaml`) | Vì sao thuộc vận hành |
|---|---|---|
| `input.db` | 0 | Theo hợp đồng DB0 của DP — DP có thể đổi bất cứ lúc nào |
| `input.key_prefix` | `L_CANDLE` | Đã đổi 4 lần trong DP contract chỉ riêng tháng 9/2026 |
| `input.event_channel` | `dp:events:candles` | Cùng hợp đồng DP với key_prefix, cùng rủi ro đổi không báo trước |
| `input.snapshot_bars` | 500 (đổi từ 100 ngày 2026-09-20, DP nâng cửa sổ DB0) | Cửa sổ đọc phụ thuộc cấu hình cửa sổ phía DP |
| `input.process_on_startup` | `true` | Công tắc an toàn — chọn `false` lúc cẩn trọng rồi restart |
| `output.db` | 1 | Đối xứng `input.db`, OG tự quyết DB nào chứa DB1 |
| `output.key_prefix` | `L_SIGNAL` | Vừa đổi 2026-09-16 (`og:signals` → `L_SIGNAL` khi chuyển sang List+Hash) |
| `output.event_channel` | `og:events:signals` | Mới thêm 2026-09-18 khi triển khai Pub/Sub OG→OF — cùng lý do `input.event_channel`, tên kênh OF phụ thuộc trực tiếp |

Các giá trị Redis còn lại **vẫn là default trong code**
(`REDIS_DEFAULTS`/`REDIS_INPUT_DEFAULTS`/`REDIS_OUTPUT_DEFAULTS` trong
`og_signal/redis_client.py`) vì chưa từng thật sự cần operator đổi —
thuần tinh chỉnh kỹ thuật:

| Giá trị | Mặc định | Nguồn |
|---|---|---|
| `input.reconcile_interval_seconds` | 1800 | code |
| `output.retention_seconds` | 604800 (7 ngày) | code |
| `output.retention_max_entries` | 500 | code |
| `port`, các `*_timeout_seconds`, `reconnect_delay_seconds`, `healthcheck_interval_seconds` | 6379 / 5 / 5 / 3 / 30 | code |

Muốn đổi 1 trong 4 giá trị kỹ thuật này thì thêm đè lên trong
`redis.input:`/`redis.output:` — `normalize_redis_config()` vẫn merge đè
được, chỉ là operator không bắt buộc phải khai.

### Warm-up chỉ báo (`defaults.warmup_bars`)

Chốt 2026-09-18, nâng lên 300 ngày 2026-09-20 (quyết định vận hành của
operator, xem bên dưới): `defaults.warmup_bars` là số bar tải thêm NGAY
TRƯỚC mốc `--from`/`from_time` để SMA/MACD/ATR/KNN trend kịp "ấm" (hết
NaN, hết lệch do seed) trước khi vào đúng khoảng người dùng yêu cầu —
không có bước này, vài bar đầu của khoảng sẽ NaN hoặc lệch giá trị dù dữ
liệu thật sự tồn tại trước đó trong DP6.

**Đo thật** (không suy đoán — cắt ngắn dần độ dài dữ liệu trước 1 điểm cố
định, so với giá trị tính từ lịch sử đầy đủ, lặp nhiều seed lấy trung
bình): SMA không có vấn đề hội tụ (không đệ quy, chỉ cần đúng `period`
bar). MACD Histogram (5/25/5) hội tụ gần như tức thì. Điểm nghẽn thật là
**ATR period=14** (Dow structure, dùng cho `sl_dow`) — lệch ~1% ở warm-up
30 bar, còn ~0,007% ở warm-up 100 bar, về 0% ở 150 — khớp với phát hiện
thực nghiệm cũ ở mục 6 (Wilder seeding, period=14 cần +81 bar mới về dưới
0,01%). RMA period=50 (KNN trend `ai_avg`, chỉ tính khi bật trend filter)
hội tụ nhanh hơn, ~0,01% ở warm-up 100. **150 bar đã đủ để hội tụ hoàn
toàn** theo đo thật — 300 (2026-09-20) là biên an toàn operator chủ động
chọn thêm, không phải số đo mới; không sai, chỉ tải nhiều hơn mức cần
thiết một chút mỗi lần export/dashboard có `--from`.

Đây là **giá trị vận hành duy nhất**, dùng chung cho:
- `core_python/db_connector.py`: `load_range_with_warmup()` — tải
  `warmup_bars` bar trước `date_from` (qua `load(before=date_from)`) rồi
  ghép với `load_range(date_from, date_to)`, bỏ trùng theo `bartime`, sort
  tăng dần. Trùng bar thì phần `load_range` (window chính) thắng.
- `export_cli.py`: `_load_entry_range()` gọi hàm trên khi có `--from`, rồi
  `_trim_to_requested_range()` cắt kết quả về đúng `bartime >= date_from`
  sau khi chạy strategy — không làm vậy thì tín hiệu thật nằm trong đoạn
  warmup sẽ lọt vào CSV dù nằm ngoài khoảng người dùng yêu cầu. Không
  `--from` (full-history) thì không cần bước này — không có gì đứng
  "trước" bar đầu tiên của toàn bộ lịch sử.
- `signal_display/server.py`: chế độ dashboard xem theo khoảng ngày cũng
  gọi `load_range_with_warmup()` y hệt (`_filter_time_window()` làm việc
  trim). Trước đây file này tự giữ 2 hằng riêng
  (`TREND_DISPLAY_WARMUP_BARS=100`, `RANGE_ENTRY_WARMUP_BARS=300`) — đã bỏ
  hẳn, `RANGE_ENTRY_WARMUP_BARS=300` trước đó không có căn cứ đo thật nào.

**Cố ý KHÔNG gộp với `redis.input.snapshot_bars`** dù cùng là "bao nhiêu
bar" — 2 khái niệm khác bản chất: `warmup_bars` là "cần bao nhiêu để chỉ
báo đáng tin" (SQL, không giới hạn dung lượng), `snapshot_bars` là "xin
Redis bao nhiêu" (bị giới hạn bởi cửa sổ thật DP đang duy trì trên DB0,
hiện ~500 nến/pair kể từ 2026-09-20 — xem mục 7). Nếu sau này cần nâng
`warmup_bars` vượt quá cửa sổ Redis thật, `og_signal` (luồng live, chỉ đọc Redis không đọc
SQL) sẽ không tự đủ được — phải báo DP mở rộng cửa sổ của họ trước, không
tự sửa được ở phía OG.

## 6. Chiến lược

Trong `strategies/<tên>.py`, phần "khi nào/hướng nào" (điều kiện BUY/SELL)
luôn được đóng khung rõ bằng banner comment — chỉ cần sửa đúng khối đó khi
đổi ý tưởng chiến lược.

**Không chiến lược nào có state machine giữa các bar** (bỏ từ 2026-09-01 —
trước đó Combo dùng `_alternating_signals`). Mỗi bar thoả điều kiện là bắn
tín hiệu, bất kể tín hiệu gần nhất trước đó là gì. Lý do: OG không theo dõi
lệnh thật (không biết SL đã dính hay chưa), nên giữ giả định "còn đang ở
trạng thái BUY tới khi có SELL" là sai — dễ bỏ lỡ tín hiệu thật sau khi
lệnh cũ đã dừng lỗ từ lâu. OF là nơi quyết định vào lệnh ở tín hiệu nào.

### Combo V0 = MA + MACD Histogram

- BUY: `close > open` (nến tăng) và `close > ma` và `prev_close < ma`
  (vừa cắt lên MA, không phải đã ở trên từ trước) và `macd_h > 0`.
- SELL: đối xứng.
- Có lọc session: `session_hours_utc` rỗng = giao dịch mọi giờ.
- Entry: `high + X` (BUY) / `low - X` (SELL), X theo symbol.

### MA Cross = SMA Fast/Slow crossover, xác nhận bằng MACD Histogram

- BUY: `prev_fast_ma <= prev_slow_ma` và `fast_ma > slow_ma` và
  `macd_h > 0`. SELL đối xứng.
- Yêu cầu `atr` không NaN mới xét tín hiệu (giữ cùng warmup với bản cũ).
- Entry: `close`. Kèm `sl_dow` tham chiếu từ swing Dow.
- Giới hạn cứng: chỉ chạy M10/M20/M30/M45.

### EMA Cross = EMA 13/34 crossover

- BUY: `prev_ema_fast <= prev_ema_slow` và `ema_fast > ema_slow`.
  SELL đối xứng. **Không dùng MACD xác nhận.**
- Entry: `close`. Kèm `sl_dow`. `entry_time` lấy theo `entry_close_time`
  (thời điểm bar đóng) thay vì thời điểm bar mở.
- Giới hạn cứng: chỉ chạy M10/M20/M30/M45.

### Trend filter KNN (`strategies/trend.py`)

Dùng chung cho cả 3 chiến lược, tắt mặc định
(`trend_filter_enabled: false`).

- Bật: `signal` chỉ giữ khi `raw_signal` cùng chiều `trend_bias` khung lớn;
  trend trung tính/thiếu/ngược chiều đều bị lọc về 0.
- Tắt: `signal = raw_signal`.
- `trend_bias` đến từ AI Trend Navigator KNN chạy trên trend timeframe, rồi
  `merge_trend_reference` gắn **trend bar đã đóng** gần nhất vào từng entry
  bar (`trend_close_time <= entry bartime`) — không lookahead.
- Cột `trend_filter_status` (`disabled`/`aligned`/`filtered`/
  `no_raw_signal`/`neutral_trend`) được test characterization khoá giá trị.
- KNN là **indicator deterministic**, không phải model ML train offline.

### Công thức ATR/RMA — khớp TradingView

`atr()` và `rma()` dùng chung `_wilder_smooth()`: bar hợp lệ đầu tiên seed
bằng **SMA(period)**, các bar sau mới chạy đệ quy
`alpha*x + (1-alpha)*prev` với `alpha = 1/period` — đúng cách `ta.atr`/
`ta.rma` của TradingView làm.

**KHÔNG được thay bằng `series.ewm(alpha=1/period, adjust=False)` thuần.**
Cách đó seed đệ quy bằng đúng 1 điểm dữ liệu đầu tiên, và `min_periods` chỉ
che NaN chứ không seed lại. Đo thật ngày 2026-09-13: period=5 lệch 2,52% ở
bar hiện ra đầu tiên và cần thêm 24 bar mới về dưới 0,01%; period=14 lệch
4,17% và cần thêm 81 bar. Sai lệch này vô hình ở bar cuối của cửa sổ 500
bar nhưng lộ rõ khi export khoảng ngày ngắn. `tests/test_indicators.py` có
2 test khoá riêng hành vi seed này.

### Thêm chiến lược mới — checklist đầy đủ

Phải sửa **10 chỗ** (bảng này là danh sách thật, đã đếm lại trong code):

1. `indicator.py`: `add_<tên>_indicators`.
2. `strategies/<tên>.py`: `detect_<tên>_signals` (chỉ tín hiệu).
3. `levels.py`: `add_<tên>_levels` (chỉ entry).
4. `configuration.py`: `_<TÊN>_CFG` + hằng timeframe
   (`*_SUPPORTED/RECOMMENDED_TIMEFRAMES`, `*_DEFAULT_TIMEFRAME`,
   `*_TREND_TIMEFRAMES`).
5. `configuration.py`: `<TÊN>_DEFAULT_PARAMS` + `<TÊN>_PARAM_FIELDS` +
   `normalize_<tên>_params`.
6. `configuration.py`: đăng ký `StrategySpec` vào `STRATEGIES`.
7. `config.yaml`: mục `strategies.<tên>` (giá trị thật).
8. `config.yaml`: `live.signal_validity.<tên>` nếu định chạy live, và thêm
   vào `live.enabled_strategies`.
9. `export_cli.py`: `csv_columns()` + `to_csv()` nếu schema CSV khác mặc
   định. **Cảnh báo**: `csv_columns()` hiện trả `CSV_COLUMNS` cho key lạ
   thay vì raise — quên bước này sẽ ra CSV sai schema mà không báo lỗi.
10. `signal_display/server.py`: `_STRATEGY_OVERLAYS` (đường overlay trên
    chart) và `run_og.sh` (dòng nhắc `Strategy (combo/ma_cross)`).

## 7. Vận hành

**Cài đặt** (không có `requirements.txt`/`pyproject.toml` — cài trực
tiếp):

```bash
python3 -m venv .venv
./.venv/bin/pip install pandas numpy pyyaml pyodbc flask redis        # chạy hệ thống
./.venv/bin/pip install pytest ruff vulture nbconvert ipykernel       # phát triển/test
```

`nbconvert`/`ipykernel` chỉ cần khi chạy `research/redis_audit_dashboard.ipynb`
bằng dòng lệnh.

Không cần cài `core_python` như 1 package — chạy trực tiếp bằng
`python -m core_python.<module>` từ đúng thư mục gốc repo.

**Chạy**:

```bash
# Xuất CSV tín hiệu — không truyền --from/--to = toàn bộ lịch sử có trong DP6
./.venv/bin/python -m core_python.export_cli --strategy combo --symbol US30 --tf H1

# Xuất theo khoảng thời gian cụ thể
./.venv/bin/python -m core_python.export_cli --strategy combo --symbol US30 --tf H1 --from 2024-01-01 --to 2024-06-01

# Xuất bản đã lọc theo trend khung lớn
./.venv/bin/python -m core_python.export_cli --strategy ema_cross --symbol US30 --tf M45 --trend-mode trend_filter --trend-tf H4

# Chart HTML tĩnh (mở trực tiếp bằng trình duyệt) — theo N bar gần nhất
./.venv/bin/python -m core_python.signal_display.cli --strategy combo --symbol US30 --tf H1

# Dashboard sống
./.venv/bin/python -m core_python.signal_display.server --port 8516

# Override tham số chiến lược (lặp lại --param cho nhiều tham số)
./.venv/bin/python -m core_python.export_cli --strategy combo --symbol US30 --param MA_PERIOD=25 --param X=12

# Menu tương tác trên Linux
./run_og.sh
```

### Tên file CSV export

```text
{strategy}_{symbol}_{tf}_{label}_{mode}.csv
```

- `label`: `full_history` (không truyền `--from/--to`) | `from_YYYYMMDD` |
  `to_YYYYMMDD` | `YYYYMMDD_YYYYMMDD`.
- `mode`: `nt` (no_trend — mặc định) | `tf_<trend_type>_<TREND_TF>`
  (vd `tf_knn_H4`).

Ví dụ: `combo_US30_H1_full_history_nt.csv`.

**Đây là format mới** (từ 2026-09-13). Bản cũ dùng hậu tố `_signals.csv`,
không có phần mode. VM-BO20 mount `runtime/exports/` qua SSHFS cho
`ctrader-cli` — đổi format phải báo bên đó.

Schema cột CSV khác nhau theo chiến lược:

| Chiến lược | Cột |
|---|---|
| `combo` | `bartime, atr, entry, signal` |
| `ma_cross` | `bartime, atr, signal` |
| `ema_cross` | `bartime, signal, entry` |

**Cột `signal` trong CSV dùng mã `ProtoOATradeSide` của cTrader — `BUY=1`,
`SELL=2`** (chốt 2026-09-19, xác nhận qua proto chính thức
`spotware/openapi-proto-messages`) — **KHÔNG phải quy ước nội bộ `1`/`-1`**
mà `core_python` dùng xuyên suốt indicator/strategy/levels/dashboard. Quy
đổi diễn ra đúng 1 chỗ, lúc ghi CSV (`export_cli.py:to_csv()`), không đụng
gì tới cột `signal` nội bộ ở bất kỳ nơi nào khác trong pipeline. Đây là
đổi Ý NGHĨA giá trị (không đổi tên cột) — cùng mức độ cần báo VM-BO20 như
đổi format ở trên.

`run_og.sh` menu chính: **1. Export Signal CSV**, **2. Open Dashboard**,
**3. Manage Live Signal Worker**. Tự dò display xrdp thật (không nhầm
display `:0` cục bộ), mở Chrome với profile riêng
(`~/.cache/core-python-dashboard-chrome`) để tránh bị single-instance Chrome
chuyển hướng sang cửa sổ đang chạy trên display khác.

### `og_signal` — luồng live 24/7

```text
Redis DB0 (DP ghi)  --Pub/Sub-->  og_signal  --Pub/Sub-->  Redis DB1  -->  OF
                                      |
                                      +--> Discord
```

Layout DB0 của DP (chốt lần thứ 4 từ 2026-09-15 05:37 UTC trong vòng chưa
tới 1 tuần — **luôn kiểm lại thực tế trên Redis trước khi tin mô tả này**;
**không còn đánh số phiên bản schema**, key `dp:candles:schema` đã bỏ hẳn):

- `LIST {key_prefix}_{SYMBOL}_{TIMEFRAME}` — danh sách mốc thời gian, tăng
  dần. Đây là chỉ mục duy nhất của pair. Toàn bộ là gạch dưới, **không có
  dấu `:` nào** ở key LIST. **SYMBOL trước, TIMEFRAME sau**:
  `L_CANDLE_US30_M5`, không phải `L_CANDLE_M5_US30`.
- `HASH <key LIST>:{stamp}` — đúng **một phép nối**: tên LIST + `":"` +
  phần tử lấy từ LIST. Mốc hiện có `:` bên trong nó (xem định dạng bên
  dưới) nên key HASH có thể mang vài dấu `:`, không phải đúng 1. **6 field
  phẳng** (chuỗi, **không phải JSON**): `timestamp`, `open`, `high`, `low`,
  `close`, `time_update`. Giá giữ nguyên scale DECIMAL của warehouse.
  **Không có field `bartime`** — mốc thời gian nằm trong chính tên key.
  **Không còn field `volume`/`datetime`/`source`.** `timestamp` (nay là
  chuỗi datetime, không phải epoch) / mốc trong LIST / đuôi key HASH là 3
  cách viết của cùng một BarTime SQL, sinh từ đúng 1 hàm nên không thể lệch
  nhau — `redis_snapshot_audit.py` kiểm trực tiếp điều này (rule 3 của DP).
  Chỉ `time_update` là khác (= `Fact_OHLCV.CreatedAt`, trước gọi
  `inserttime`).

**`volume` không còn tồn tại trên Redis từ contract này** —
`read_candles_from_redis()` vẫn HMGET xin field `volume` (để giữ đúng
schema `OHLCV_COLUMNS` khớp với `db_connector.load()` phía SQL), nhưng
HMGET trên field không tồn tại trả về `None` → luôn thành NaN. Không lỗi,
không cảnh báo — chỉ là cột `volume` từ Redis giờ **luôn NaN**. Không
strategy nào đọc `volume` nên không ảnh hưởng tín hiệu. `redis_sql_audit.py`
đã loại `volume` khỏi phần so OHLCV Redis-vs-SQL (`_COMPARABLE_OHLCV_COLUMNS`
chỉ còn 4 giá) — nếu không loại, mọi bar sẽ báo lệch volume giả (SQL có số
thật, Redis luôn NaN).

Định dạng mốc: `YYYY-MM-DD HH:MM:SS` (vd `2026-09-15 05:37:00`, có dấu cách
và dấu `:`), luôn UTC, không hậu tố offset. Rộng cố định nên **so sánh
chuỗi đã cho đúng thứ tự thời gian**, code chỉ parse ra datetime khi thật
sự cần đối tượng datetime.

**Phân biệt LIST với HASH bằng SỰ HIỆN DIỆN của dấu `:`** (không phải số
lượng) — key LIST không có dấu nào, key HASH luôn có ít nhất 1 (nhiều hơn
1 vì mốc giờ cũng chứa `:`). `parse_pair_key()` dùng `":" in key` để loại
— **logic này không đổi so với contract liền trước**, vẫn đúng vì nó kiểm
sự hiện diện chứ không đếm số lượng. `scan_candle_pairs()` lọc `TYPE=list`
phía server nên đúng bất kể cách đặt tên.

Đọc bằng `LRANGE` + 1 pipeline `HMGET` (2 round-trip bất kể bao nhiêu bar).
`LRANGE` dùng chỉ số âm nên list ngắn hơn chỉ trả ít bar hơn — cửa sổ
500 nến/pair hiện tại (nâng từ 100 ngày 2026-09-20, `redis.input.snapshot_bars`
trong config.yaml) không bị hardcode ở phía OG.

`symbol`/`timeframe` của `read_candles_from_redis()` là **keyword-only**:
truyền nhầm thứ tự sẽ đọc ra list rỗng chứ không báo lỗi, nên chữ ký hàm
chặn hẳn khả năng đó.

Cập nhật đến qua `SUBSCRIBE dp:events:candles` (kênh không đổi). Message
chỉ là **trigger** — OG luôn đọc lại toàn bộ cửa sổ từ Redis, không dùng
`candles` trong payload, không giữ state nến nội bộ. Lưu ý 2 kiểu dữ liệu
tách bạch: trong payload event OHLCV là **số JSON**, còn trong HASH là
**chuỗi**. DP chỉ publish khi giá trị thực sự đổi. Pub/Sub là fire-and-forget
nên có thêm nhịp `reconcile_interval_seconds` (1800s) quét lại toàn bộ pair,
đúng cách DP tự làm cho chính họ.

**Redis KHÔNG luôn khớp SQL tức thì — đây là thiết kế, không phải lỗi.**
Backfill và spool replay của DP không publish, nến của chúng chỉ tới Redis ở
lần reconcile kế tiếp (trễ tối đa 30 phút). Ngay sau mỗi lần DP restart, vài
pair có thể thiếu nến mới nhất trong tối đa 30 phút. Quy tắc phán đoán:
thiếu vài nến ngay sau restart hoặc trong vòng 30 phút = **bình thường**;
lệch kéo dài qua **hai** chu kỳ reconcile mới là bất thường và cần báo DP.

**Layout DB1 (OG ghi, chốt 2026-09-16)** — List+Hash, mirror đúng thiết kế
DB0 của DP, thiết kế cùng anh Kiệt qua nhiều vòng để chuẩn bị cho OF
(VM-OF11, đọc DB1 để đặt lệnh qua cTrader Open API — xem memory
`project_of_ctrader_integration`):

```text
LIST L_SIGNAL_{SYMBOL}_{TIMEFRAME}_{STRATEGY}   -- vd L_SIGNAL_US30_H1_COMBO
HASH <LIST key>:{stamp}                          -- 1 phép nối, giống DB0
```

Khác DB0 (2 chiều: symbol+timeframe) ở chỗ DB1 có **3 chiều** — STRATEGY
phải nằm trong key vì tín hiệu (không như nến) luôn gắn với đúng 1 chiến
lược, và `combo` không bị giới hạn cứng theo timeframe (chỉ `ma_cross`/
`ema_cross` mới bị chặn cứng) nên 1 key chỉ 2 chiều có thể bị 2 chiến lược
cùng ghi đè nếu sau này cấu hình timeframe của chúng chồng nhau.
`ma_cross`/`ema_cross` chứa sẵn `_` trong tên (`MA_CROSS`) nên
`parse_signal_list_key()` (trong `research/redis_signal_audit.py`, KHÔNG
phải `og_signal/signal_publisher.py` — xem lý do bên dưới) khớp theo hậu tố
chiến lược đã biết trước, không split `_` ngây thơ.

**HASH — 3 nhóm field** (chi tiết đối chiếu từng field với
`ProtoOANewOrderReq` của cTrader, xem memory `project_of_ctrader_integration`):

1. **Định danh, chữ thường, dư thừa có chủ đích** (đọc thẳng, khỏi parse
   tên key — giống cách DB0 lặp lại `timestamp`): `symbol`, `timeframe`,
   `strategy`, `bartime`.
2. **Dữ liệu đặt lệnh, camelCase, khớp tên field cTrader nơi khớp được**:
   `tradeSide`, `orderType` (`STOP`=combo, `MARKET`=ma_cross/ema_cross,
   cố định theo chiến lược — không phải config), `stopPrice` (chỉ có khi
   `orderType=STOP`), `stopLoss`/`takeProfit` (chỉ có khi combo/ma_cross đã
   tính được, xem mục 6 phần KSL/KTP — ema_cross không có), `expirationTimestamp`
   (**epoch giây**, không phải ISO — cTrader cần int64; OF đặt
   `timeInForce=GOOD_TILL_DATE` thì cTrader tự huỷ lệnh STOP chờ đúng lúc
   quá hạn, không cần OF tự canh giờ huỷ), `clientOrderId` (= signal_id,
   cTrader trả nguyên lại trong execution report), `comment` (= lý do tín
   hiệu; **không có `label`** — cTrader có 2 field tách biệt nhưng OG chỉ
   có 1 nguồn text, chọn giữ đúng 1).
3. **Hỗ trợ OF, không thuộc cTrader**: `atr` (OF tự tính volume), `valid_from`
   (OF tự kiểm hiệu lực trước khi gửi request — mốc bar tín hiệu ĐÓNG, sớm
   nhất được phép vào lệnh, khác `bartime` là mốc bar MỞ), `ksl`/`ktp` (từ
   2026-09-18: chính ratio K dùng để tính `stopLoss`/`takeProfit`, vd
   `1.618`/`2.618` — KHÔNG phải khoảng cách đã nhân atr; OF tự nhân với
   `atr` đã có sẵn trong hash để ra khoảng cách. Có mặt cùng điều kiện với
   `stopLoss`/`takeProfit` — combo/ma_cross khi tính được, luôn vắng mặt ở
   ema_cross. Mục đích: ma_cross là MARKET order, giá khớp thật lúc OF gửi
   lệnh khác giá đóng nến lúc OG sinh tín hiệu, nên `stopLoss`/`takeProfit`
   tuyệt đối OG tính sẵn chỉ là tham chiếu — OF cần `ksl`/`ktp` để tự tính
   `relativeStopLoss`/`relativeTakeProfit` áp vào giá khớp thật).

**Biết chắc `symbol` KHÔNG khớp `symbolId` của cTrader** (số nội bộ theo
account/server, OF tự map qua `ProtoOASymbolsListReq`) — và `orderType` dù
khớp tên field vẫn là chuỗi, OF vẫn phải tự map sang protobuf enum. Đây là
giới hạn chấp nhận được, không phải thiếu sót — ghi rõ để không ai tưởng
nhầm Hash này đã "sẵn sàng cắm thẳng" vào `NewOrderReq`.

**Ghi 1 lần duy nhất, atomic, qua Lua script** (`_PUBLISH_SIGNAL_SCRIPT`
trong `signal_publisher.py`) — EXISTS HASH trước, có rồi thì bỏ qua (đúng
tính chất "chỉ publish 1 lần" mà `SET NX` cũ có, giờ trải trên 2 key thay
vì 1). `EXPIRE` áp lên từng HASH riêng (`retention_seconds`, vẫn 7 ngày —
per-signal, y hệt `SET EX` cũ). LIST không có TTL theo từng phần tử được
nên chặn bằng `LTRIM` giữ `retention_max_entries` (500) dòng mới nhất thay
vì theo thời gian.

**OG → OF Pub/Sub — triển khai 2026-09-18**, đối xứng với chiều DP → OG
(`dp:events:candles`). Cùng script Lua trên, ngay sau bước ghi (không chạy
ở nhánh "đã tồn tại, bỏ qua" — nên trùng lặp không bao giờ bắn lại thông
báo), thêm đúng 1 `PUBLISH` lên kênh `redis.output.event_channel`
(`og:events:signals`, khai ở `config.yaml`, bắt buộc — xem mục 5). Message
chỉ là **trigger**, cùng triết lý DP đã áp cho OG: `{"symbol", "timeframe",
"strategy", "stamp"}` — đủ để OF tự dựng lại đúng key LIST/HASH
(`signal_list_key`/`signal_hash_key`) và tự `HGETALL`, KHÔNG được tin bất
kỳ field nào nhúng sẵn trong message. OF bắt buộc có thêm 1 vòng reconcile
định kỳ tự quét `L_SIGNAL_*` — Pub/Sub tự thân là fire-and-forget (Redis
xác nhận at-most-once), 1 lần OF mất kết nối là mất vĩnh viễn thông báo đó
nếu không có bước quét lại này (checklist đầy đủ cho OF: memory
`project_of_ctrader_integration`).

`parse_signal_list_key()` chỉ nằm trong `research/redis_signal_audit.py`,
KHÔNG phải `og_signal/signal_publisher.py` — không có chỗ nào trong luồng
ghi thật cần parse ngược key (OG chỉ ghi DB1, không bao giờ tự SCAN lại
output của chính mình như cách nó reconcile DB0), nên hàm parse chỉ phục
vụ audit, không phải logic sản xuất.

**Chạy qua systemd user service (không cần sudo)**:

`deploy/og-signal-user.service` chạy `python -m og_signal.live_worker`,
`Restart=on-failure` + `RestartSec=5`, `StartLimitBurst=20` trong 300s.
Submenu **3** của `run_og.sh` bọc sẵn các lệnh dưới đây:

```bash
install -D -m 0644 deploy/og-signal-user.service ~/.config/systemd/user/og-signal.service
systemctl --user daemon-reload
systemctl --user enable --now og-signal.service   # Start Live Worker
systemctl --user disable --now og-signal.service  # Stop Live Worker
systemctl --user restart og-signal.service        # Restart Live Worker
systemctl --user status og-signal.service         # Show Worker Status
journalctl --user -u og-signal.service -f         # Tail Live Logs
```

Muốn service sống cả khi chưa đăng nhập: `loginctl enable-linger <user>`.

Log CHỈ ra qua `journald` (`Environment=PYTHONUNBUFFERED=1`) — không có file
log riêng, không rotation tự viết trong code. Submenu **3** cũng có lựa chọn
chạy foreground thủ công (`python -m og_signal.live_worker`).

**Cảnh báo trước lần Start đầu tiên**: `process_on_startup` mặc định `true`
(**default trong `og_signal/redis_client.py`**, không phải trong
`config.yaml` — xem mục 5) khiến worker quét lại toàn bộ snapshot DB0 ngay
khi khởi động và có thể publish + gửi Discord ngay lập tức cho bất kỳ signal
nào còn trong cửa sổ hiệu lực. `run_og.sh` bắt xác nhận `y` trước khi chạy
đúng vì lý do này. Giữ OF dừng cho tới khi đã kiểm tra payload DB1/Discord
hợp lý ở lần start đầu tiên.

## 8. Test và công cụ audit

```bash
./.venv/bin/python -m pytest -q                  # 133 test
./.venv/bin/python -m ruff check .               # phải sạch, kể cả notebook
./.venv/bin/python -m vulture core_python og_signal tests --min-confidence 80
```

Test trong `tests/` **không được đụng SQL Server thật** — không test nào
được mở kết nối DB thật hay query dữ liệu OHLCV/symbol/timeframe thật.
`tests/conftest.py` có 1 fixture `autouse` nạp sẵn cache của
`db_connector.symbols()`/`tf_minutes()` bằng dữ liệu tổng hợp trước mỗi
test.

Test **cần `config.yaml` tồn tại và đủ mục `strategies:`** để import được
`core_python.configuration` — hệ thống này chỉ chạy trên 1 máy (`vm-og`),
luôn có sẵn `config.yaml` thật.

Phần lớn test là **characterization/golden test**: giá trị mong đợi được
tính từ chính pipeline thật (không phải số bịa), khoá lại hành vi hiện tại
để phát hiện thay đổi logic ngoài ý muốn. Khi sửa logic chiến lược có chủ
đích, phải chạy lại pipeline thật để tính giá trị mới rồi cập nhật test —
không tự đoán số.

**Hệ quả cần biết**: một số cột không có reader production vẫn phải giữ vì
test đang khoá giá trị của chúng — `entry_time`, `trend_filter_status`,
`dow_swing_low`. Muốn xoá thì phải sửa test trước, và đó là quyết định có
chủ đích chứ không phải dọn rác.

### `research/` — không hermetic, có chủ đích

4 script + 1 notebook cần Redis/SQL Server thật. Chi tiết đầy đủ ở
`research/README_REDIS_AUDIT.md`; tóm tắt:

| Công cụ | Trả lời câu hỏi |
|---|---|
| `redis_snapshot_audit.py` | Cấu trúc DB0 hiện tại có hợp lệ không? |
| `redis_event_observer.py` | Redis có thật sự phát event không? |
| `redis_signal_audit.py` | Signal DB1 có đúng schema/TTL không? |
| `redis_sql_audit.py` | N nến SQL có đủ và khớp OHLCV/MA20/MACD Hist/ATR trên DB0 không? |
| `redis_audit_dashboard.ipynb` | Gọi 4 audit, mặc định full scope; hiển thị bảng trạng thái và chi tiết SQL -> Redis |

Các script chỉ dùng lệnh Redis đọc và đường `db_connector.load()` SELECT có
sẵn; không publish DB1, không gọi Discord, không tạo lệnh, **không được tự
thêm credential hoặc sao chép credential từ DP6**. Report ghi vào
`research/reports/` (gitignored), không chứa host/password/webhook. Report
SQL-Redis chỉ giữ giá tại các điểm bị lệch để debug, không sao chép toàn bộ
các nến đã khớp.

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python research/redis_sql_audit.py --all --report-dir research/reports
```

## 9. Dọn dẹp sau khi chạy thật

Sau mỗi lần chạy thật/verify bằng dữ liệu thật, xoá sạch cache/build
artifact không thuộc hệ thống:

```bash
rm -rf .ruff_cache .pytest_cache
find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
```

**Không tự động xoá `runtime/exports/`.** Trước đây mục này gộp chung cả
`runtime/` vào lệnh `rm -rf` — đã gây sự cố thật (2026-09-04): xoá mất
88 file CSV mà VM-BO20 đang mount qua SSHFS và phụ thuộc trực tiếp cho
`ctrader-cli` backtest, làm hỏng 1 lượt chạy thật đang dở của bên đó.
`runtime/exports/` là **sản phẩm đầu ra**, không phải artifact tạm —
không coi nó như `.pytest_cache`/`.ruff_cache`/`__pycache__`. Muốn dọn
`runtime/exports/` phải hỏi trước, không tự quyết.
