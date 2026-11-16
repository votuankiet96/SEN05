# decision_engine — Kiến trúc & Quy chuẩn hệ thống

Tài liệu này là bản kiến trúc chung và quy chuẩn duy nhất của repo `decision_engine`
(hệ thống nội bộ, vận hành trên máy `og18`). Nó chỉ mô tả hai package còn tồn tại:
`core_engine/` (DE chạy thật) và `des/` (DE chạy thử trên nến ảo).

**Mọi khẳng định trong file này phải khớp code thật.** Bản này được viết lại ngày
2026-11-16 bằng cách đọc lại từng file trong `core_engine/src/`, `des/src/`, hai bộ
`tests/`, hai file `run_*.sh`, các unit systemd, và đối chiếu với `de_config.yaml`
đang chạy.

> Bản trước của file này mô tả `core_python` / `og_signal` / `order_gateway` /
> `strategy_lab` (đọc nến từ SQL Server, xuất CSV, dashboard). Các thư mục đó **không
> còn trong working tree** (đã xoá, chưa commit). Muốn xem lại thì đọc git history,
> commit `60d08a9` là bản cuối còn đủ chúng. Đừng dùng bất cứ điều gì của kiến trúc đó
> để suy ra cách `core_engine` chạy.

## 1. Hệ thống này làm gì (và không làm gì)

**DE = Decision Engine.** Nhiệm vụ đúng 3 bước:

```text
nến ĐÃ ĐÓNG trong Redis  ->  chiến lược + yếu tố trend  ->  tín hiệu BUY/SELL cuối cùng trong Redis
```

1. Đọc nến (OHLC, mới nhất) mà hệ thống khác ghi vào Redis. DE **chỉ đọc** Redis nguồn.
2. Mỗi chiến lược tìm nến có tín hiệu thô; mỗi yếu tố trend cho biết khung lớn đang
   ủng hộ hướng nào; `decision_engine.py` áp luật chốt (không nhìn trước tương lai,
   dời, hủy, bỏ).
3. Ghi tín hiệu cuối vào Redis đầu ra và báo một kênh Pub/Sub để **OE** (Order Engine,
   hệ thống khác, ngoài repo này) đọc và đặt lệnh.

**Không làm trong repo này**: đọc SQL Server, đặt lệnh, tính khối lượng, đặt SL/TP,
Discord, xuất CSV, dashboard backtest. DE chỉ ghi `atr` để OE tự tính khối lượng.
Không có `sl_price`, `tp_price`, `ksl`, `ktp` ở bất kỳ đâu trong đầu ra.

**Hai thứ trong repo:**

| Thư mục | Là gì | Nến lấy từ | Ghi vào |
|---|---|---|---|
| `core_engine/` | DE chạy thật | Core AEN (`L_CANDLE_*` ở db0, Redis 6379) và Redis DF của OG8 (10.11.12.8:6380) | Redis `de` (127.0.0.1:6380) db0 |
| `des/` | DES: đúng engine của DE, chạy thử trên nến ảo | DFS (`L_DFS_*` ở db10, cùng Redis 6380) | Redis `de` db11 |

**Hai chiến lược, hai yếu tố trend đang đăng ký** (theo `de_config.yaml` thật, kiểm
2026-11-16; bản mẫu không bí mật nằm ở `core_engine/de_config.example.yaml`):

| Chiến lược | Khung trigger | Loại lệnh | Hiệu lực | Dời tối đa | Yếu tố trend tham chiếu |
|---|---|---|---|---|---|
| `ma_cross` | M10 | MARKET | `validity_seconds: 600` | `max_shift_bars: 3` | `macd` @M30, `sma20` @M30 |
| `combo` | M30 | STOP | `validity_bars: 3` (3 nến M30 = 90 phút) | `max_shift_bars: 0` (không dời) | `macd` @H1 |

Yếu tố trend: `macd` (dấu MACD Histogram 5/25/5; MA Cross dùng ở M30, Combo dùng ở H1,
nên runner `macd` chạy ở cả hai khung), `sma20` (close so với SMA 20). Loại yếu tố
`break` (chiều lần phá đỉnh/đáy của N nến trước) vẫn có trong code nhưng chưa chiến lược
nào tham chiếu; Combo từng dùng `break20` @M45 đến 2026-11-16. Hai chiến lược chạy trên cả 15 symbol: 8 symbol nguồn `default` (Core AEN: US30, US500,
US100, GER40, HK50, JP225, XAUUSD, BTCUSD) và 7 symbol nguồn `og8` (EURUSD, GBPUSD,
USDCAD, USDJPY, FR40, SP35, UK100). Đây là **cấu hình**, không phải mã: đổi bằng
`de_config.yaml`, xem mục 5 và 7.

## 2. Nguyên tắc làm việc — bắt buộc tuân thủ khi sửa/thêm code

- **Chỉ code khi đã được chốt rõ ràng — áp dụng cho MỌI session.** Không tự sửa,
  thêm hay xoá code/file ngoài đúng phạm vi vừa được yêu cầu, kể cả khi thấy hợp lý
  hoặc là bước "hiển nhiên" tiếp theo. Bàn bạc, đề xuất, phân tích, liệt kê phương
  án thì làm tự do. Chỉ thực thi (sửa file, chạy lệnh đổi trạng thái hệ thống) khi
  người dùng xác nhận rõ (vd "làm đi", "triển khai", "chốt vậy"); không tự suy diễn
  sự đồng ý từ ngữ cảnh.
- **Báo trước file mới và mọi chỗ lệch kế hoạch.** Kế hoạch phải liệt kê file sẽ
  tạo; tạo file không có trong kế hoạch, hoặc sửa file khác với file đã nói, thì dừng
  lại và hỏi.
- **Mỗi file một trách nhiệm duy nhất** (bảng ở mục 3). Vai trò được khoá bằng mã
  trong `tests/test_architecture.py`, không chỉ bằng lời.
- **Phụ thuộc một chiều, không vòng lặp**, và **không file nào ngoài `archived/`
  import `archived/`**. Sơ đồ cho phép nằm ở `ALLOWED_IMPORTS` của
  `test_architecture.py`; thêm file vào `src/` mà không khai ở đó thì test fail.
- **Chỉ `redis_connector.py` được nối Redis** (`redis.Redis(...)`, nghe keyspace,
  kiểm cờ, ghi nguyên tử bằng `eval`, đọc/ghi config có phiên bản). `data_engine.py`
  chỉ đọc dữ liệu (`lrange`, `hmget`, `hgetall`) trên kết nối đã có. Mọi file khác
  không gọi lệnh Redis nào: quyết định ghi gì rồi giao cho `redis_connector`.
- **Tham số vận hành → `de_config.yaml`. Cấu trúc/logic → code.** Không có giá trị
  ngầm cho tham số vận hành: thiếu key thì `KeyError` nêu đúng đường dẫn, config sai
  thì không được nạp lên Redis. Tên chiến lược, tên yếu tố, khung, symbol, tham số, số
  nến dời, hiệu lực **không được viết cứng trong `.py`**.
- **Không có `__init__.py`** (package kiểu namespace). Chạy bằng
  `python -m core_engine.src.<module>` từ đúng thư mục gốc repo. `tests/conftest.py`
  chèn gốc repo vào `sys.path`.
- **`strategies` (file trong `de_module/`) CHỈ quyết định khi nào có tín hiệu thô và
  hướng nào.** Không biết yếu tố trend, không dời, không tính giá vào lệnh/atr, không
  giữ tham số (nhận từ `configuration.py`).
- **`decision_engine.py` là thuần pandas**: không đọc/ghi Redis, không giữ tên chiến
  lược hay yếu tố nào. `decide` là hàm **xác định**: gọi lại với cùng dữ liệu cho cùng
  kết quả; chống ghi trùng là việc của `util.SignalWriter` + Lua script.
- **`indicator.py` chỉ tính chỉ báo thuần** (sma, ema, macd_hist, atr), không import
  gì trong `src/`.
- **Test phải hermetic**: không Redis thật, không file log thật.
- **Không in nội dung `de_config.yaml` / `des_config.yaml` thật** (có mật khẩu Redis)
  ra terminal hay log; khi chạy test thì tránh traceback in repr config (dùng
  `--tb=no` nếu cần chạy thử).
- **Viết tài liệu bằng lời thường, đối chiếu code trước khi khẳng định.**
- **Sửa code xong luôn chạy** `ruff`, `vulture`, `pytest` (mục 8) rồi **dọn artifact**
  (mục 9) trước khi coi là hoàn tất.

## 3. Kiến trúc — bảng trách nhiệm từng file

```text
core_engine/
  de_config.example.yaml   Bản mẫu config, không bí mật. Copy thành src/de_config.yaml
                           (gitignored) rồi điền mật khẩu thật.
  deploy/
    de-runner@.service     Unit systemd user: --runner --lane %i
    de-publisher@.service  Unit systemd user: --publisher --lane %i
  src/
    main.py                Lối vào mỏng: đọc tham số dòng lệnh, bắt SIGTERM, giao cho
                           configuration.run_*. Ba vai trò: --runner, --publisher,
                           --load-config.
    configuration.py       Đọc + kiểm config, đăng ký chiến lược/yếu tố, dựng việc cho
                           từng tiến trình, chạy vòng lặp sự kiện. Không chứa logic
                           tín hiệu.
    data_engine.py         Cửa vào DUY NHẤT của dữ liệu đọc vào: nến của nguồn và kết
                           quả module ở Redis đầu ra. Nghe keyspace theo đúng các cặp
                           cần, đọc cửa sổ nến có bộ đệm gia tăng.
    redis_connector.py     Cửa DUY NHẤT nối và ghi Redis: make_client, Link,
                           KeyspaceListener, keep_connected, write_list_hash(_many),
                           trim_lists_before, write_config/read_config.
    util.py                Phía XUẤT BẢN: dựng tên key/kênh, nội dung HASH, hạn hiệu
                           lực; ModuleWriter (kết quả module), SignalWriter (tín hiệu
                           cuối + báo kênh). Không tự gọi lệnh Redis.
    decision_engine.py     Sổ đăng ký ô quyết định + luật chốt tín hiệu (mục 4.3).
                           Thuần pandas.
    indicator.py           sma, ema, macd_hist, atr (Wilder seed bằng SMA, khớp
                           TradingView). Không import gì trong src/.
    log.py                 Logging tập trung: MỘT RotatingFileHandler mỗi tiến trình.
    de_module/
      mac_strategy.py      detect() của MA Cross.
      combo_strategy.py    detect() của Combo (kèm điều kiện xen kẽ).
      trend.py             Các yếu tố trend: bias_macd, bias_sma, bias_break.
    archived/              Logic cũ (strategy_runner, rules/, strategies/, trend_methods/
                           gồm knn, zigzag, cloud, tstat, breakout). Giữ để tham khảo;
                           KHÔNG file nào ngoài archived/ được import nó.
  tests/                   Hermetic (mục 8).
  logs/                    Log thật (gitignored).

des/                       Bản sao của core_engine/ + chart.py, xem mục 6.
docs/DE_Workflow.html      Trang giải thích DE theo Input / Processing / Output, kèm
                           ví dụ tín hiệu thật.
run_de.sh                  Launcher Linux cho DE (mục 7).
.claude/settings.local.json  Cấu hình Claude Code cục bộ (không phải mã hệ thống).
graphify-out/              Đồ thị tri thức do /graphify sinh ra; không sửa tay.
```

**Chiều phụ thuộc cho phép** (đúng bằng `ALLOWED_IMPORTS`, đã được test khoá):

| File | Chỉ được import |
|---|---|
| `indicator`, `log`, `redis_connector` | không file nào |
| `data_engine` | `redis_connector` |
| `util` | `data_engine`, `redis_connector` |
| `decision_engine`, `de_module/*` | `indicator` |
| `configuration` | `data_engine`, `de_module`, `decision_engine`, `indicator`, `redis_connector`, `util`, `log` |
| `main` | `configuration` |

## 4. Luồng dữ liệu và quy tắc quyết định

### 4.1 Hai loại tiến trình, chỉ nói chuyện qua Redis

```text
Redis nguồn (nến đã đóng)
   |  keyspace notification
   v
runner   (mỗi (chiến lược HOẶC yếu tố trend) x (nguồn nến) một tiến trình)
   |  ghi kết quả module vào Redis đầu ra
   v
L_MODSTR_*  (kết quả chiến lược)    L_MODTRE_*  (kết quả yếu tố trend)
   |  keyspace notification (cả hai) + nến khung trigger của nguồn
   v
publisher  (mỗi nguồn nến một tiến trình)
   |  epoch / dời / hủy -> ghi tín hiệu cuối + PUBLISH một kênh
   v
L_DE_{SYMBOL}_{KHUNG}  +  kênh L_CHANNEL_*   ->   OE
```

- **runner** (`--runner --lane <tên>.<nguồn>`): `<tên>` là khoá ở `strategies:` hoặc
  `trends:`, `<nguồn>` là khoá ở `redis.candles.sources` (`default` là nguồn mặc
  định). Mỗi nến mới của một cặp: đọc cửa sổ `snapshot_bars + warmup_bars` nến,
  chạy hàm đã đăng ký (`detect` của file chiến lược, hoặc `bias_<type>`), ghi các
  dòng của cửa sổ ghi (sau `warmup_bars` nến đầu). Cặp có không quá `warmup_bars`
  nến thì không ghi (log `warmup_incomplete`).
- **publisher** (`--publisher --lane <nguồn>`): với mỗi ô quyết định (chiến lược,
  symbol) của nguồn đó, đọc kết quả chiến lược, kết quả từng yếu tố trend và nến khung
  trigger rồi gọi `decision_engine.decide`; sự kiện của bất kỳ ba loại dữ liệu đó
  đều đánh dấu ô cần đánh giá lại.
- Chết hay restart một tiến trình không kéo theo tiến trình khác; một nguồn mất kết
  nối chỉ làm các tiến trình của nguồn đó chờ nối lại.
- Tên tiến trình systemd: `de-runner@<tên>.<nguồn>.service`,
  `de-publisher@<nguồn>.service`.

### 4.2 Vòng lặp sự kiện (`configuration._run_loop`)

- Redis báo keyspace (HSET trên HASH của một mốc) → cặp bị đánh dấu "bẩn" → vòng lặp
  lấy từng việc bẩn và xử lý trên dữ liệu **mới nhất**. Nhiều sự kiện của cùng một
  cặp (ghi bù) gộp thành một lần xử lý.
- Lúc khởi động (`redis.candles.process_on_startup: true`) quét toàn bộ việc **một
  lần** để bù những gì đã tới khi tiến trình tắt. **Không có nhịp quét định kỳ**:
  thông báo rơi mất thì cặp đó chỉ được xử lý lại ở sự kiện kế tiếp hoặc lần khởi
  động lại.
- Cần `notify-keyspace-events` có lớp `K` + `h` trên Redis nguồn, Redis đầu ra và
  Redis cất config. Thiếu thì `RuntimeError` nêu đúng lệnh `CONFIG SET` ngay lúc
  khởi động.
- Mất Redis (`redis.RedisError`): ghi `event=redis_connection_lost`, đợi rồi nối lại
  (`redis_connector.keep_connected`). Lỗi của riêng một việc được cô lập
  (`event=process_failed`), không làm chết tiến trình.
- Có phiên bản config mới trên Redis → vòng lặp raise `_Reload`, tiến trình dựng lại
  việc từ phiên bản đó **không restart**. Chạy mà cấu hình sai thì thoát mã 1
  (`event=config_error`); `redis.enabled: false` thì thoát êm mã 0.

### 4.3 Luật chốt tín hiệu (`decision_engine.py`)

1. **Reality epoch (không nhìn trước tương lai).** Nến trigger đóng lúc `Ce` chỉ được
   nhìn nến của một yếu tố trend **đã đóng**, giờ đóng `<= Ce` (bằng nhau vẫn tính).
   `merge_closed_reference` chọn và ghép nến theo luật đó cho từng yếu tố.
   `reference_ready` trả lời "đủ chưa": nếu có một nến yếu tố phải đóng **đúng**
   lúc `Ce` mà chưa về thì **phải chờ**, không dùng nến cũ thay.
2. **Mọi yếu tố cùng chiều thì phát; lệch thì dời.** Tín hiệu phát ở nến mà mọi yếu
   tố tham chiếu cùng chiều với nó. Có yếu tố ngược chiều thì xét tiếp nến kế, tối đa
   `max_shift_bars` nến sau nến tín hiệu, phát ở nến đầu tiên mọi yếu tố cùng chiều.
   `max_shift_bars` khai riêng từng chiến lược: **chỉ MA Cross dời (3)**, **Combo
   KHÔNG dời (0)**. Tín hiệu thô của Combo rất nhiều nên nến tín hiệu lệch trend thì bỏ
   luôn; MA Cross hiếm và theo trend thuần (mất tín hiệu đầu xu hướng là mất cả chuỗi)
   nên được chờ trend.
   Giá vào lệnh và `atr` lấy từ **nến phát**, không phải nến tín hiệu thô.
3. **Tín hiệu thô ngược chiều xuất hiện giữa chừng thì hủy** tín hiệu đang chờ.
4. **Hết số nến dời mà vẫn lệch thì bỏ.** Nến không có `atr` thì không phát.

Giá: MARKET → HASH không có field giá; STOP → `stopPrice` = High của nến phát (BUY)
hoặc Low của nến phát (SELL). `atr` là ATR (Wilder, chu kỳ `atr_period` của chiến
lược) tính từ chính nến phát.

Chờ không có hàng đợi riêng: khi nến hoặc kết quả còn thiếu về tới Redis thì sự kiện
đánh thức publisher đánh giá lại ô. Quá hạn hiệu lực thì `SignalWriter.publish` bỏ
(log `signal_expired`).

**Giới hạn đã biết (chưa sửa):** `reference_ready` suy lưới giờ đóng của khung yếu tố
từ chính các nến yếu tố đang có; sau nghỉ phiên lưới suy ra có thể lệch lưới thật nên
chờ nhầm.

### 4.4 Ba chiến lược/yếu tố trong code

- **MA Cross** (`de_module/mac_strategy.py`): BUY khi nến trước `fast_ma <= slow_ma`,
  nến này `fast_ma > slow_ma` và `macd_h > 0`; SELL đối xứng (`>=`, `<`, `macd_h < 0`).
  Không lọc lặp cùng chiều: lần cắt không được MACD xác nhận thì không có tín hiệu.
  Là chiến lược theo trend thuần nên được dời tối đa 3 nến chờ trend (`max_shift_bars: 3`).
  `detect` raise nếu `fast_ma >= slow_ma` hoặc `macd_fast >= macd_slow`.
- **Combo** (`de_module/combo_strategy.py`): BUY khi nến tăng (`close > open`),
  `close > ma`, `macd_h > 0` và **vừa cắt lên** = MỘT trong hai: `open < ma` (nến tự cắt
  lên ngay trong thân nến) hoặc `prev_close < prev_ma` (close nến trước dưới ma CỦA NẾN
  TRƯỚC, cắt thật giữa hai lần đóng cửa); SELL đối xứng (`open > ma` hoặc
  `prev_close > prev_ma`). Chủ hệ chốt 2026-11-16: trước đó là `prev_close < ma` của nến
  HIỆN TẠI (hệ cũ cũng thế) nên khi ma dốc vài điểm trong một nến thì nến cắt thật bị bỏ
  lỡ, và luật xen kẽ biến một lần bỏ lỡ thành mất cả tín hiệu ngược chiều kế tiếp (HK50
  10/08/2022 10:30, US30 15/08/2022 02:30 và 13:30). Có thêm **điều kiện xen kẽ**: một setup chỉ thành tín hiệu khi setup gần nhất trước
  đó không cùng chiều (hoặc chưa có setup nào trong bảng), nên tín hiệu luôn xen kẽ
  BUY, SELL. Trạng thái chỉ nhớ trong bảng nến được truyền vào, nên setup đầu bảng
  luôn thành tín hiệu. Không dời tín hiệu (`max_shift_bars: 0`): tín hiệu thô đã nhiều,
  nến tín hiệu lệch trend thì bỏ luôn.
- **Yếu tố trend** (`de_module/trend.py`): hàm `bias_<type>(frame, *, ...)` trả mảng
  `+1/-1/0` theo từng nến. Tham số keyword-only, không có giá trị mặc định (config
  khai thừa hoặc thiếu thì bị từ chối).

`reason` của chiến lược (`signal_reason`) thành field `comment` của tín hiệu cuối mà
OE đọc **nguyên văn**: đổi chuỗi lý do là đổi dữ liệu OE nhận.

### 4.5 Kích thước cửa sổ

`snapshot_bars` (500) = số nến gần nhất mà module ghi kết quả; `warmup_bars` (300) =
số nến tính thêm phía trước để chỉ báo kịp "ấm", không ghi ra. Ràng buộc:
`snapshot_bars + warmup_bars <= 1200` (số nến nguồn giữ), kiểm lúc nạp config;
`max_shift_bars < snapshot_bars`. Mỗi `L_MODSTR_*`/`L_MODTRE_*` LIST giữ `snapshot_bars`
mốc (mốc cũ hơn đầu cửa sổ bị bỏ, xoá luôn HASH); mỗi HASH của module hết hạn sau
`2 x snapshot_bars x độ dài khung` giây.

## 5. Hợp đồng Redis

**Nến (đầu vào, do Core AEN / OG8 ghi, DE chỉ đọc):** LIST `{key_prefix}_{SYMBOL}_{KHUNG}`
(vd `L_CANDLE_US30_M10`; SYMBOL trước, KHUNG sau), mới nhất đứng **đầu**; HASH
`<key LIST>:{mốc}` giữ `open/high/low/close/volume` (chuỗi thập phân). Mốc là
`YYYY-MM-DD HH:MM:SS` (UTC, giờ **mở** nến, rộng cố định nên so chuỗi đúng thứ tự thời
gian). Phân biệt LIST và HASH bằng **sự hiện diện** của `:`. `data_engine` vẫn xin
`volume` để bảng nến cùng hình dạng OHLCV; không module nào đọc nó.

**Đầu ra của DE: một Redis (`de`), MỘT db (db0), ba loại key** (khai ở
`redis.output`, prefix phải khác nhau):

| Key | Nội dung |
|---|---|
| LIST `L_MODSTR_{CHIẾN LƯỢC}_{SYMBOL}_{KHUNG}` + HASH `<LIST>:{mốc}` | Kết quả chiến lược, chỉ có dòng ở nến **có tín hiệu**: `close_time`, `signal`, `reason`. Mới nhất đứng **cuối**. |
| LIST `L_MODTRE_{YẾU TỐ}_{SYMBOL}_{KHUNG}` + HASH | Kết quả yếu tố trend, có dòng ở **mọi nến** của khung của nó: `close_time`, `trend_bias`. |
| LIST `L_DE_{SYMBOL}_{KHUNG}` + HASH `<LIST>:{mốc}` | **Tín hiệu cuối cho OE.** Key không có tên chiến lược vì mỗi (symbol, khung trigger) chỉ có đúng một chiến lược (config kiểm). |
| Kênh Pub/Sub `L_CHANNEL_{CHIẾN LƯỢC}_{SYMBOL}_{KHUNG}` | Message chỉ là **mốc** (`YYYY-MM-DD HH:MM:SS`) của tín hiệu vừa ghi. Không lưu key. |

Trong tên key/kênh, tên chiến lược hay yếu tố viết hoa và **bỏ gạch dưới**
(`ma_cross` → `MACROSS`), nên không hai tên nào được trùng sau phép đổi đó (config
kiểm). Field `strategy` và `clientOrderId` trong HASH vẫn giữ tên thật.

**HASH tín hiệu cuối** (`util.to_hash_fields`), tên field theo `ProtoOANewOrderReq` của
cTrader nơi khớp được:

| Nhóm | Field |
|---|---|
| Định danh | `symbol`, `timeframe`, `strategy` (chữ thường), `bartime` (ISO, giờ **mở** nến) |
| Đặt lệnh | `tradeSide` (`BUY`/`SELL`), `orderType` (`MARKET`/`STOP`), `stopPrice` (chỉ lệnh STOP), `expirationTimestamp` (epoch **giây**), `clientOrderId` (`{chiến lược}:{KHUNG}:{SYMBOL}:{YYYYMMDDTHHMMSSZ}`), `comment` |
| Hỗ trợ OE | `atr`, `valid_from` (ISO, giờ **đóng** nến = sớm nhất được vào lệnh) |

`entry` và `atr` làm tròn theo `live.price_decimals` của symbol (không khai thì 2 số
lẻ; forex phải khai 5 hoặc 3, nếu không giá bị hỏng). `symbol` của DE **không** khớp
`symbolId` của cTrader; `orderType` vẫn là chuỗi, OE tự map.

**Hiệu lực:** tín hiệu chỉ được ghi khi `now` còn trong `[giờ nến đóng, hạn chót)`,
hạn chót = giờ nến đóng + `validity_seconds` (hoặc `validity_bars` x độ dài khung
trigger). Quá hạn → bỏ, log `signal_expired`. DE chỉ **công bố** khoảng hiệu lực;
OE quyết định làm gì trong đó.

**Ghi một lần, nguyên tử** (`redis_connector._WRITE_LIST_HASH_SCRIPT`, Lua): nếu HASH
của mốc đã tồn tại thì không làm gì và **không PUBLISH** lại (nên bên nhận không bao giờ
bị báo hai lần, log `signal_duplicate`); nếu chưa thì HSET + EXPIRE + RPUSH, bỏ mốc
cũ nhất (và xoá HASH của nó) khi LIST dài quá `retention_max_entries`, rồi PUBLISH.
Hạn lưu mỗi tín hiệu `redis.output.retention_seconds` **tối đa 7 ngày** (khai lớn hơn
thì từ chối lúc nạp config); LIST `L_DE_*` giữ tối đa `retention_max_entries` mốc
(500).

**Pub/Sub là at-most-once**: OE phải vừa nghe kênh vừa tự quét lại `L_DE_*` định kỳ
để không bỏ sót khi mất kết nối.

## 6. DES — DE chạy thử trên nến ảo

`des/` là **bản sao** của `core_engine/` để chạy cùng một engine trên nến quá khứ do
DFS (Datafeed Simulator) phát, không đụng vào DE thật. Chỉ có đúng các điểm sau được
phép khác `core_engine/`:

| Điểm | DE (`core_engine`) | DES (`des`) |
|---|---|---|
| Nguồn nến | `default` (Core AEN db0) + `og8` | một nguồn `default`: DFS Redis 6380 **db10**, prefix `L_DFS` |
| Đầu ra | db0, kênh `L_CHANNEL_*` | **db11**, kênh `S_CHANNEL_*` (Pub/Sub dùng chung cả server nên phải khác tiền tố) |
| "Bây giờ" của publisher | giờ máy | giờ ảo `dfs:clock` đọc ở db nguồn (`configuration._virtual_now`, qua `DataEngine.read_key`); chưa có thì `RuntimeError` |
| Config | `core_engine/src/de_config.yaml` | `des/src/des_config.yaml`; mỗi nguồn khai thêm `clock_key` |
| Tên symbol | `XAUUSD`, `GER40`, `JP225` | theo DFS: `GOLD`, `DE40`, `J225`... |
| Log, unit, launcher | `de_run_*.log`, `de-*`, `run_de.sh` (menu) | `des_run_*.log`, `des-*`, `des/run_des.sh` (mở dashboard) |
| Thêm | | `des/src/chart.py` (module hiển thị) |

`des/src/chart.py` là Flask app **chỉ đọc**: vẽ nến DFS (db10) cùng thứ DES đã ghi
(db11) theo đúng config DES đang chạy (`/api/catalog`, `/api/view`). Chạy:
`./.venv/bin/python -m des.src.chart --port 8518`. Thư viện Lightweight Charts nhúng sẵn
ở `des/src/vendor/` (không cần internet). Nó không ghi/xoá/đặt hạn gì, không nghe
keyspace, không tự nối (dùng `redis_connector.make_client`).

**Quy tắc đồng bộ:** `indicator.py`, `decision_engine.py`, `redis_connector.py`,
`de_module/*` ở `des/src/` phải **giống hệt** `core_engine/src/` (chỉ khác chuỗi mô tả
như `de_config` → `des_config`, `db0` → `db11`); `util.py`, `log.py`, `main.py` chỉ khác
tên/chuỗi mô tả; `data_engine.py` chỉ thêm `read_key`; `configuration.py` chỉ thêm
`clock_key` và `_virtual_now`. Sửa logic ở một bên thì phải áp sang bên kia, rồi đối
chiếu (vd `diff <(sed 's/core_engine/X/g' core_engine/src/F.py) <(sed 's/\bdes\b/X/g'
des/src/F.py)` cho từng file). Mục đích của DES là so kết quả mô phỏng với DE thật.

## 7. `de_config.yaml`, vận hành và launcher

### 7.1 Config có phiên bản trên Redis

`core_engine/src/de_config.yaml` (**gitignored**, có mật khẩu Redis) là **nguồn**. Mỗi
tiến trình khi khởi động nạp nó lên Redis (`upload_config`: chỉ tạo phiên bản mới khi
nội dung khác phiên bản mới nhất), rồi **đọc config từ Redis** (`fetch_plan`). Hệ quả:

- Sửa `de_config.yaml` rồi chạy
  `./.venv/bin/python -m core_engine.src.main --load-config` là mọi tiến trình đang
  chạy dùng ngay, **không cần restart**. Config sai thì không có gì lên Redis.
- Hai mục chỉ ở file, không nạp lên Redis: `redis.servers` (địa chỉ và mật khẩu mọi
  Redis) và `redis.store` (nơi cất config: `server`, `db`, `key_prefix: L_CONFIG`,
  `keep_versions`). Config trên Redis = LIST `L_CONFIG` giữ số phiên bản, HASH
  `L_CONFIG:{phiên bản}` giữ văn bản trong field `config`, không hết hạn.
- Menu 8 của `run_de.sh` bọc đúng lệnh nạp này.

### 7.2 Cấu trúc config

```yaml
redis:
  enabled: true
  servers: {aen: {host, port, password}, og8: {...}, de: {...}}   # mật khẩu "" = không AUTH
  store:   {server: de, db: 0, key_prefix: L_CONFIG, keep_versions: 20}
  candles:
    timeframes: [M10, M20, M30, M45, H1, H2, H3, H4]   # mọi khung của module phải nằm trong đây
    snapshot_bars: 500
    warmup_bars: 300
    process_on_startup: true
    sources:                     # mỗi symbol đúng MỘT nguồn; nguồn `default` bắt buộc
      default: {server: aen, db: 0, key_prefix: L_CANDLE, symbols: [...]}
      og8:     {server: og8, db: 0, key_prefix: L_CANDLE, symbols: [...]}
  output:                        # MỘT db cho mọi đầu ra
    server: de
    db: 0
    strategy_prefix: L_MODSTR
    trend_prefix: L_MODTRE
    signal_prefix: L_DE
    channel_prefix: L_CHANNEL
    retention_seconds: 604800    # tối đa 7 ngày
    retention_max_entries: 500
trends:                          # tên -> {type: <bias_type>, tham số}
  macd:    {type: macd, fast: 5, slow: 25, signal: 5}
  sma20:   {type: sma, period: 20}
strategies:                      # tên -> file trong de_module/ có hàm detect
  ma_cross:
    file: mac_strategy
    trigger: M10
    symbols: all                 # hoặc danh sách symbol
    params: {fast_ma: 13, slow_ma: 34, macd_fast: 5, macd_slow: 25, macd_signal: 5}
    atr_period: 5
    max_shift_bars: 3
    validity_seconds: 600        # khai ĐÚNG MỘT trong validity_seconds / validity_bars
    order_type: MARKET
    trend: {macd: M30, sma20: M30}     # yếu tố -> khung của yếu tố
live:
  log_level: INFO
  price_decimals: {EURUSD: 5, GBPUSD: 5, USDCAD: 5, USDJPY: 3}
```

`parse_plan` từ chối config khi: thiếu key bất kỳ; tham số của `detect`/`bias_*` không
phải số nguyên `>= 1` hoặc không khớp chữ ký hàm hoặc làm hàm raise khi chạy thử trên nến
giả; yếu tố khung **nhỏ hơn** khung trigger; chiến lược không tham chiếu yếu tố nào;
yếu tố khai ở `trends:` mà không chiến lược nào dùng; hai chiến lược cùng chạy một
(symbol, khung trigger); hai nguồn cùng nuôi một symbol; tên trùng sau khi bỏ gạch
dưới; `snapshot_bars + warmup_bars > 1200`; `max_shift_bars >= snapshot_bars`;
`retention_seconds > 604800`; `price_decimals` cho symbol không thuộc DE hoặc ngoài 0..8;
`strategy_prefix`/`trend_prefix`/`signal_prefix` trùng nhau.

### 7.3 Cài đặt

Không có `requirements.txt`/`pyproject.toml`; cài trực tiếp vào `.venv` (Python 3.14):

```bash
python3 -m venv .venv
./.venv/bin/pip install pandas numpy pyyaml redis flask        # chạy hệ thống (flask chỉ cho des chart)
./.venv/bin/pip install pytest ruff vulture                    # phát triển/test
```

### 7.4 Chạy

```bash
./run_de.sh                       # menu DE live (1 Start, 2 Stop, 3 Restart, 4 Status,
                                  # 5 Tail app log, 6 Tail journal, 7 Foreground một tiến trình,
                                  # 8 Load config, 0 Exit)
./des/run_des.sh                  # DES: KHÔNG có menu; bấm là bật server chart (127.0.0.1:8518) nếu chưa chạy rồi mở
                                  # dashboard bằng Chrome trên màn hình xrdp; không cần terminal; toàn tiếng Anh

# Chạy tay một tiến trình (chạy từ gốc repo):
./.venv/bin/python -m core_engine.src.main --runner --lane ma_cross.default
./.venv/bin/python -m core_engine.src.main --runner --lane macd.og8 --symbols BTCUSD,US30
./.venv/bin/python -m core_engine.src.main --publisher --lane og8
./.venv/bin/python -m core_engine.src.main --load-config
```

`--symbols` chia việc theo symbol cho nhiều tiến trình cùng vai trò (bỏ đi = mọi
symbol đã đăng ký); `--lane` của publisher bỏ đi = nguồn `default`.

DES không còn menu quản lý tiến trình: dùng `systemctl --user` trực tiếp. Dừng: publisher
trước (`des-publisher@default.service`) rồi bốn runner (`des-runner@{ma_cross,combo,macd,sma20}.default.service`);
bật: runner trước, 5 giây sau publisher (mỗi unit `enable --now`, hoặc `start`). Nạp config:
`./.venv/bin/python -m des.src.main --load-config`. Giờ của DES là `dfs:clock`: DFS PHẢI đang chạy.
Nếu bỏ một yếu tố khỏi `trends:` thì dừng và tắt (`disable --now`) runner của nó TRƯỚC khi nạp config.

`run_de.sh` tự suy danh sách tiến trình từ config thật (`de_sources`, `de_modules` đọc
`redis.candles.sources`, `strategies:`, `trends:`): mỗi (module x nguồn) một runner và mỗi
nguồn một publisher, **runner khởi động trước, publisher sau** (cách nhau 5 giây để
runner kịp ghi kết quả module đầu tiên); Stop theo thứ tự ngược lại. Start in cảnh
báo `process_on_startup` và bắt xác nhận `y` rồi mới chạy; nó cài lại 2 unit mẫu vào
`~/.config/systemd/user/` + `daemon-reload`, và **từ chối Start nếu còn tiến trình
BẢN CŨ** (`de-signal@*`, `de-trend@*`, `de-publisher.service`...) vì chúng ghi cùng db
và trùng tên kênh `L_CHANNEL_MACROSS_*` với bản mới.

Hiện trạng kiểm 2026-11-16: DE chạy **10 tiến trình** (4 module `ma_cross`, `combo`,
`macd`, `sma20` x 2 nguồn `default`/`og8` = 8 runner + 2 publisher); DES chạy
5 (4 runner + 1 publisher, nguồn `default`). `loginctl` báo `Linger=no`: service user
chỉ sống khi còn phiên đăng nhập; muốn sống khi chưa đăng nhập thì
`loginctl enable-linger <user>`.

### 7.5 Log

Log CHỈ ra **file**, không ra stdout/stderr (journald gần như trống): thư mục
`core_engine/logs/` (DES: `des/logs/`), mỗi tiến trình một file vì `RotatingFileHandler`
không an toàn khi nhiều tiến trình cùng xoay vòng một file (5 MB x 3 bản).
`de_run_runner_<module>[_<nguồn>].log`, `de_run_publisher[_<nguồn>].log` (nguồn
`default` bị bỏ khỏi tên), `de_run_load_config.log`. Dòng log:
`thời gian MỨC tiến-trình[pid] module.hàm:dòng: event=...`. Các event chính:
`startup`, `config_uploaded`, `config_reload`, `redis_connected`, `subscribed`,
`reconcile_start`/`reconcile_end`, `signal_published`, `signal_expired`,
`signal_duplicate`, `warmup_incomplete`, `process_failed`, `redis_connection_lost`,
`config_error`, `stopped`, `disabled`.

**Cảnh báo trước lần Start đầu tiên hoặc sau khi tắt lâu:** `process_on_startup: true` quét
lại mọi cặp nến ngay lúc khởi động và có thể ghi + báo kênh tín hiệu còn trong cửa sổ
hiệu lực. Nếu OE đang nghe thì nó nhận ngay. Kiểm payload hợp lý trước khi để OE nghe.

## 8. Test và công cụ kiểm

```bash
./.venv/bin/python -m pytest core_engine/tests -q        # 288 test (kiểm 2026-11-16)
./.venv/bin/python -m pytest des/tests -q                 # 326 test (gồm test_chart)
./.venv/bin/python -m ruff check core_engine des
./.venv/bin/python -m vulture core_engine des --min-confidence 80
```

- **Chạy `core_engine/tests` và `des/tests` RIÊNG.** Chạy chung trong một lần `pytest`
  sẽ lỗi lúc thu thập (11 lỗi) vì hai bên có cùng tên file test và repo không có
  `__init__.py`.
- Hiện trạng kiểm 2026-11-16: `pytest` qua hết ở cả hai; `vulture` sạch; `ruff` còn
  **5 lỗi `I001` (sắp xếp import), tất cả trong `core_engine/src/archived/`** (chưa sửa,
  không ảnh hưởng chạy). Ngoài `archived/` thì `ruff` sạch. Mục tiêu là cả 3 lệnh sạch.
- Test **hermetic**: Redis giả (`tests/engine_support.py`: `FakeCore`, `RecordingRedis`,
  `FakeSnapshots`, `FakeClock`, `LoopEngine`...), không Redis thật, không file log thật.
- Phần lớn test là **characterization**: giá trị mong đợi tính từ đúng logic thật.
  Khi đổi logic có chủ đích thì tính lại giá trị mới bằng pipeline thật rồi cập nhật
  test, không tự đoán số.

| File test | Khoá cái gì |
|---|---|
| `test_architecture.py` | Vai trò file: ai được nối/đọc/ghi Redis, chiều import, mọi file trong `src/` ngoài `archived/` phải khai ở `ALLOWED_IMPORTS` (DES thêm kiểm `chart.py` chỉ đọc) |
| `test_config_guards.py` | `parse_plan`, nạp/đọc config có phiên bản, dựng việc từng tiến trình |
| `test_sources.py` | `redis.candles.sources` (mỗi symbol một nguồn, `default` bắt buộc) |
| `test_core_engine.py` | `indicator` (ATR seed SMA khớp TradingView), MA Cross, Combo |
| `test_trend_methods.py` | `bias_macd`, `bias_sma`, `bias_break` |
| `test_epoch_rule.py` | `merge_closed_reference`, `reference_ready` |
| `test_signal_publisher.py` | `decision_engine` (đăng ký, dời, hủy) và `util` (tên key, nội dung, hạn, ghi db) |
| `test_data_engine.py` | Đọc nến/kết quả module, bộ đệm gia tăng phải giống hệt đọc đầy đủ, nghe keyspace |
| `test_redis_connector.py` | Nối, nghe, nối lại, ghi List–Hash nguyên tử |
| `test_lane.py` | Xử lý một việc, vòng lặp sự kiện, nạp lại config không restart, `main.py` |
| `test_log.py` | File log riêng mỗi tiến trình, quy ước logger |

## 9. Thêm chiến lược / yếu tố trend

**Thêm một chiến lược** (đã được khoá bởi test: tên không viết cứng trong mã):

1. `de_module/<tên>.py`: hàm `detect(frame, *, <tham số>)` nhận nến khung trigger
   (cột `bartime, open, high, low, close, volume`, tăng dần) và trả bản sao có thêm
   `signal` (+1/-1/0) và `signal_reason`. Tham số keyword-only, không giá trị mặc
   định. Chỉ chứa luật "khi nào, hướng nào", công thức chỉ báo mới thì thêm vào
   `indicator.py`.
2. `tests/test_architecture.py`: thêm `"de_module/<tên>": {"indicator"}` vào
   `ALLOWED_IMPORTS` (không thì test fail vì file ngoài sơ đồ).
3. `de_config.yaml`: thêm `strategies.<tên>` (đủ `file`, `trigger`, `symbols`, `params`,
   `atr_period`, `max_shift_bars`, `validity_seconds` hoặc `validity_bars`, `order_type`,
   `trend`) rồi `--load-config`. Mỗi (symbol, khung trigger) chỉ một chiến lược.
4. Bật runner cho module mới: Start trong `run_de.sh` (tự thêm
   `de-runner@<tên>.<nguồn>` theo config). Publisher đang chạy tự dựng lại việc khi
   config đổi.
5. Thêm test hermetic cho luật mới; nếu DES cần so sánh thì áp y hệt sang `des/`
   (mục 6).
6. Loại lệnh mới ngoài `MARKET`/`STOP` thì phải sửa `util.ORDER_TYPES` và
   `util.to_hash_fields` (và báo OE).

**Thêm một yếu tố trend:** thêm hàm `bias_<type>(frame, *, ...)` vào `de_module/trend.py`
(trả mảng int64 `+1/-1/0` đúng độ dài frame), khai ở `trends:` (`type: <type>` và tham
số), rồi cho chiến lược tham chiếu nó ở `strategies.<tên>.trend` với khung **không nhỏ
hơn** khung trigger và thuộc `redis.candles.timeframes`. Bật runner của yếu tố mới như
trên. Không phải sửa file nào khác.

## 10. Hạn chế đã biết

- `reference_ready` có thể chờ nhầm sau nghỉ phiên (mục 4.3).
- Đổi tham số của một chiến lược đang chạy **chưa** tự xoá kết quả cũ trong `L_MODSTR_*`:
  cần chọn giữa xoá rồi tính lại, hoặc đặt tên mới cho chiến lược.
- Tham số của chiến lược/yếu tố hiện chỉ nhận **số nguyên >= 1** (`_bind_params`).
- Nến về Redis nguồn **sau** giờ đóng (không tức thì), và độ trễ khác nhau theo nguồn
  và khung: ghi chú trong `de_config.example.yaml` nêu Core AEN ghi nến M45 chậm đều
  khoảng 310-373 giây nên `ma_cross` dùng `validity_seconds: 600` thay vì 180. Hạn hiệu
  lực quá hẹp thì tín hiệu bị bỏ (`signal_expired`).
- `docs/DE_Workflow.html` là tài liệu viết tay: khi đổi hành vi (luật chốt, tên key, hạn
  hiệu lực) phải cập nhật nó cùng lúc.

## 11. Dọn dẹp sau khi chạy thật/test

Sau mỗi lần chạy thật hoặc kiểm bằng `pytest`/`ruff`/`vulture`:

```bash
rm -rf .ruff_cache .pytest_cache
find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
```

Chạy kiểm với `PYTHONDONTWRITEBYTECODE=1`, `pytest -p no:cacheprovider` và
`ruff --no-cache` thì không sinh artifact. **Không xoá `core_engine/logs/` và
`des/logs/`** (đó là log vận hành thật, không phải cache). Service tự sinh lại
`__pycache__` mỗi lần restart; dọn rồi kiểm `find` rỗng.
