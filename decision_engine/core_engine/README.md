# core_engine (DE – Decision Engine)

Nến từ Redis 6379 db0 (Core/AEN ghi; 7 symbol Core không có thì đọc từ Redis DF của OG8, xem "Nguồn nến thêm") → **runner** tính, cho MỌI symbol và MỌI khung, các cấu phần thô và ghi vào Redis 6380 (**db0**: SETUP; **db1**: TRIGGER, CONTEXT, TREND), mỗi danh sách chỉ giữ 500 nến gần nhất → **publisher** ghép theo từng ô (symbol, chiến lược, khung trigger), lọc trend/dời và ghi tín hiệu vào **db2** (+ kênh Pub/Sub cho OE).

```text
nến AEN (6379 db0)  ┐                                 6380 db0: {SYM}_SETUP_{CL}_{KHUNG}    (trạng thái từng nến)
nến OG8 (10.11.12.8) ┴─► runner ─► (không phụ thuộc          6380 db1: {SYM}_TRIGGER_{CL}_{KHUNG}  (chỉ nến có tín hiệu)   (keyspace event)
                          cấu hình)                          {SYM}_CONTEXT_{KHUNG}      ─────────────────► publisher ─► 6380 db2: L_DE_{CL}_{SYM}_{KHUNG}
                                     {SYM}_TREND_{KHỐI}_{KHUNG}                  (nến đóng ≤ T,                 L_DE_{KHỐI}_{CL}_{SYM}_{KHUNG}
                                                                                           trùng T thì chờ)               + kênh Pub/Sub L_CHANNEL_…
```

**Nguyên tắc: tính đủ, ghép theo cấu hình.** Runner (db0, db1) chỉ TÍNH và không biết cấu hình ô nào; với mọi symbol × khung nó luôn tính đủ chiến lược (TRIGGER, SETUP), CONTEXT và TREND của từng khối. Chỉ publisher (db2) QUYẾT ĐỊNH theo cấu hình: mỗi ô chọn khung nào của TRIGGER, khung trend nào, khung context nào. Đổi cấu hình ô không làm đổi hay phải xoá dữ liệu db0/db1.

## Hai loại tiến trình, mỗi loại chia thành LÀN, độc lập, chỉ trao đổi qua Redis

| Loại | Lệnh | Việc |
|---|---|---|
| **runner** `de-runner@<làn>.<nguồn>` | `python -m core_engine.src.main --runner --lane <làn>.<nguồn> [--symbols A,B]` | nghe nến (keyspace event) của mọi (symbol, khung) trong nguồn nến; mỗi nến đóng tính phần việc của làn rồi ghi db0/db1 theo List–Hash. **Làn chiến lược** (`combo`, `ma_cross`, `ema_reach`, `macd_break`, `ichimoku`): chỉ tính chiến lược đó, ghi **TRIGGER** (CHỈ khi nến có tín hiệu thô) và **SETUP** (trạng thái điều kiện của nến), đọc `snapshot_bars + warmup_bars` = 800 nến. **Làn `trend`**: ghi **CONTEXT** (macd_h, ma) rồi **TREND** của các khối mà có ô đọc (knn, zigzag, tstat, cloud chỉ ở khung H1–H4; breakout ở mọi khung), đọc 1200 nến cho zigzag và breakout. Khởi động lại thì xét lại 500 nến gần nhất xem nến nào có tín hiệu. |
| **publisher** `de-publisher@<chiến lược>` | `python -m core_engine.src.main --publisher --lane <chiến lược> [--symbols A,B]` | nghe db0 và db1 (keyspace event); với từng ô của chiến lược đó, đến lúc nến trigger đóng thì ghép SETUP + TRIGGER + CONTEXT + TREND theo luật nến đóng, lọc trend/dời, tính entry, kiểm hạn hiệu lực rồi ghi tín hiệu vào db2 (List–Hash + kênh Pub/Sub cho OE). |

Bố trí hiện tại: **17 tiến trình** = 2 nguồn nến (`default` = `redis.input`, `og8` = `redis.input_sources.og8`) × (làn `trend` + 5 làn chiến lược) = 12 runner, cộng 5 publisher (mỗi chiến lược một). Số tiến trình KHÔNG khai ở config: suy từ nguồn nến có symbol (`redis.input`, `redis.input_sources`) và các chiến lược ở mục `strategies:`. `--lane` bỏ đi thì một tiến trình làm trọn mọi vai trò (`--runner [--source og8]` / `--publisher`): chỉ để chạy tay hoặc thử, systemd chạy theo làn. `--symbols` chia việc thêm theo symbol. Tiến trình chết / restart không kéo theo tiến trình khác: các làn chỉ trao đổi qua Redis, và publisher **chờ** cấu phần chưa về (TREND hoặc CONTEXT do làn khác ghi) rồi tính lại khi nó về, nên không làn nào phải chờ làn nào. Hạ tầng bắt buộc: Redis 6380 bật `notify-keyspace-events` có lớp `K` và `h` (publisher nghe db0 và db1; thiếu thì báo lỗi rõ ràng lúc khởi động).

## Bố cục Redis cổng 6380

**Mọi danh sách ở db0 và db1 chỉ giữ cửa sổ 500 nến gần nhất** (`redis.input.snapshot_bars` = `redis.setup.window_bars` = `retention_max_entries` = 500): nến mới vào thì nến cũ nhất ra khỏi danh sách VÀ dòng dữ liệu của nó bị xoá luôn (số khoá của một chuỗi luôn bằng số nến đang giữ). Runner ghi, publisher đọc; LIST mới nhất đứng cuối.

**db0 – SETUP** (`redis.setup`):

| LIST | Số lượng | Có dòng khi nào | HASH `<LIST>:<giờ mở nến>` |
|---|---|---|---|
| `HK50_SETUP_COMBO_H1` (chiến lược viết liền: COMBO, MACROSS, EMAREACH, MACDBREAK, ICHIMOKU) | 600 (15 symbol × 8 khung × 5 chiến lược) | mọi nến | `bartime`, `close_time`, `setup`, `open`, `high`, `low`, `close`, `atr` |

**db1 – TRIGGER, CONTEXT và TREND** (`redis.signal_inputs`):

| LIST | Số lượng | Có dòng khi nào | HASH `<LIST>:<giờ mở nến>` |
|---|---|---|---|
| `HK50_TRIGGER_COMBO_H1` | tối đa 600; LIST chỉ xuất hiện khi chiến lược có tín hiệu | **chỉ nến mà chiến lược BẮN tín hiệu thô** | `bartime`, `close_time`, `signal` (1/-1), `setup`, `open`, `high`, `low`, `close`, `atr`, `reason` |
| `HK50_CONTEXT_H2` | 120 (15 × 8) | mọi nến | `bartime`, `close_time`, `macd_h`, `ma` |
| `HK50_TREND_KNN_H4` | 600 (15 symbol × 8 khung × 5 khối knn, breakout, zigzag, tstat, cloud) | mọi nến | `bartime`, `close_time`, `trend_bias` (+1/-1/0) |

- **TRIGGER = SỰ KIỆN.** Một dòng chỉ có khi chiến lược thật sự bắn tín hiệu thô ở nến đó (đúng `add_indicators` + `detect_signals` của chiến lược trên khung đó, trend tắt, SAU lọc trùng: nến đầu tiên của mỗi đợt); nến không có tín hiệu thì không ghi gì. `reason` là lý do tín hiệu. Cửa sổ trượt theo nến: sự kiện cũ hơn nến đầu của 500 nến gần nhất bị bỏ khỏi LIST (và xoá HASH). Lúc khởi động lại, 500 nến gần nhất được xét lại để xem nến nào có tín hiệu (mốc đã có thì bỏ qua).
- **SETUP = TRẠNG THÁI điều kiện ở TỪNG nến (không phải sự kiện).** `setup` (1/-1/0) là mặt nạ "nến được phép bắn" mà phần DỜI cần theo từng nến (chiến lược trạng thái như ma_cross: nến còn đủ điều kiện; combo: hướng đợt đang giữ), kèm giá nến và `atr` để nến dời lấy entry/atr của chính nó. Không có `signal`/`reason`: tín hiệu nằm ở TRIGGER. Runner ghi SETUP CUỐI CÙNG nên là "dấu chốt" của lượt ghi.
- **CONTEXT** = chỉ báo tính THÊM ở mọi khung để đối chiếu với trigger ở khung nhỏ hơn: MACD histogram (`macd_h`) và MA (`ma`), tham số chuẩn ở `context:` (5/25/5 và 20). Một list chứa mọi chỉ báo; thêm chỉ báo = thêm cột.
- **TREND**: mỗi khối tính trên nến của chính khung đó.

`close_time` là giờ ĐÓNG nến. Số thực ghi bằng `repr` và đọc lại bằng `float()` (đúng từng bit; riêng `ma` của CONTEXT có thể lệch chữ số cuối do cộng dồn vì bảng tính bắt đầu ở nến khác nhau, ≤ 3e-16 tương đối). HASH còn có hạn lưu dự phòng 2 × số mốc × độ dài khung. Runner ghi theo thứ tự TREND, CONTEXT, TRIGGER (cả ba ở db1) rồi SETUP (db0) cuối cùng ("dấu chốt": thấy nến mới ở SETUP thì phần còn lại của nến đó đã có). Quy mô: khoảng 0,135 triệu khoá, ~45 MB (Redis `maxmemory` 1 GB, noeviction).

**Warm-up (`redis.input.warmup_bars`, hiện 300).** Chỉ báo cần nến đứng trước để hội tụ (MA20 mất 19 nến, ichimoku/cloud 77, macd_break 100, trend breakout tới 124), nên hàng đầu của bảng tính từ cửa sổ cố định luôn NaN/chưa ấm. Mỗi lượt runner tính lại các bảng từ đầu trên `snapshot_bars + warmup_bars` = 800 nến cuối (không nhớ trạng thái giữa hai lượt, nên warm-up cần ở MỌI lượt chứ không chỉ lúc khởi động); riêng zigzag (đòi 3000 nến) và breakout (giữ hướng tới cú phá kế tiếp) dùng cả 1200 nến nguồn giữ. Bảng nến trong bộ nhớ vì thế dài 1200 nến, nhưng lượt thường chỉ đọc các mốc mới rồi nối vào. Runner chỉ tính và ghi bảng TREND mà có ô đọc: knn, zigzag, tstat, cloud chỉ ở khung H1–H4 (khung M không có ô nào đọc), breakout ở mọi khung. Sau khi tính, runner chỉ ghi `snapshot_bars` (500) hàng mới nhất và không bao giờ ghi hàng thuộc phần ấm. Cặp có không quá `warmup_bars` nến thì không ghi và log `WARNING event=warmup_incomplete`. `snapshot_bars + warmup_bars` phải ≤ `source_max_bars` (1200). Warm-up **không** cứu được zigzag (cần ≳ 1.705 nến để khớp bản tính từ đầu).

**db2 – tín hiệu đã publish** (HASH theo hợp đồng OE: `symbol`, `timeframe`, `strategy`, `bartime`, `tradeSide`, `orderType`, `stopPrice` (chỉ lệnh STOP), `expirationTimestamp`, `clientOrderId`, `comment`, `atr`, `valid_from`):
- bản không lọc trend: `L_DE_{CHIẾN LƯỢC}_{SYMBOL}_{KHUNG TRIGGER}` (vd `L_DE_COMBO_HK50_H1`), kênh `L_CHANNEL_COMBO_HK50_H1`;
- bản đã lọc trend của từng khối: `L_DE_KNN_COMBO_HK50_H1` (mỗi khối một tiền tố ở `redis.trend_outputs`). Tối đa 600 danh sách thô + 2.280 danh sách lọc (480 ô trend × 4 khối + macd_break không dùng breakout; ichimoku không trend). **Mỗi tín hiệu (HASH) hết hạn sau TỐI ĐA 7 ngày** (`redis.output.retention_seconds`, mặc định và trần 604800 giây; khai lớn hơn thì từ chối lúc nạp config); mỗi LIST giữ tối đa `redis.output.retention_max_entries` (500) mốc mới nhất.

Cổng 6379 db0 chỉ có nến của Core, DE chỉ đọc. Tín hiệu ghi đúng một lần (HASH đã có thì không ghi và không publish lại).

## Ô (symbol, chiến lược, khung trigger) và bảng của we

`configuration.build_cells` dựng 600 ô theo `strategies.<tên>.trend_tf_by_symbol` (bảng của we trên OG8, tên symbol theo nguồn nến: GER40/JP225/XAUUSD thay cho DE40/J225/GOLD): symbol → khung trigger → khung trend. Khối `breakout` và `cloud` không tra bảng mà tính trên chính khung trigger (cloud không nhỏ hơn H1). **Luật khung: trigger ≤ context ≤ trend**, kiểm lúc nạp config. Khung context có bảng riêng `strategies.<tên>.context_tf_by_symbol` (symbol → khung trigger → khung context; bắt buộc đủ 15 symbol × 8 khung cho cả 5 chiến lược) và hiện LUÔN bằng khung trigger (chủ hệ chốt 10/11/2026): ví dụ combo US30 M10 có trigger M10, context M10, trend H4. CONTEXT không làm điều kiện lọc: nếu không có vấn đề gì thì tín hiệu chính là tín hiệu thô. Lưu ý: khối breakout/cloud tính ở khung trigger nên ô có breakout/cloud không thể có context lớn hơn khung đó.

## Luật quyết định của publisher (mỗi ô, mỗi khi một cấu phần của ô được ghi)

1. Đọc SETUP (500 nến), TRIGGER (sự kiện trong 500 nến đó; không có LIST nghĩa là không nến nào có tín hiệu), CONTEXT của khung context và TREND của từng khối. Nến mới nhất là nến mới nhất của SETUP; `signal` và `reason` của từng nến lấy từ TRIGGER (nến không có dòng: 0 và rỗng). CONTEXT được đọc và ghép vào bảng nhưng **hiện chưa làm điều kiện nào** (tín hiệu đã có đủ điều kiện của chính khung trigger); thiếu CONTEXT của nến mới nhất, hoặc nến context lớn hơn đóng đúng lúc mà chưa về, thì chưa quyết định gì.
2. **Bản thô:** nến đã đóng mới nhất có `signal ≠ 0` (có sự kiện TRIGGER ở nến đó).
3. **Bản lọc của khối m:** ghép `trend_bias` của nến trend ĐÃ ĐÓNG gần nhất có giờ đóng ≤ giờ đóng nến trigger (luật nến đóng, `indicator.merge_closed_reference`); nếu đúng lúc đó có nến trend phải đóng mà chưa về thì CHỜ, không dùng nến cũ thay. Khối không dời (knn, zigzag) chỉ giữ tín hiệu thô cùng chiều trend. Khối dời (breakout, tstat, cloud) dùng `first_aligned_signal(signal, trend_bias, setup)`: mỗi đợt bắn đúng MỘT tín hiệu, ở nến đầu tiên còn trong mặt nạ mà trend đã cùng chiều (số nến dời tối đa kể từ nến mở đợt do `live.max_shift_bars` quy định, áp chung mọi chiến lược: 0 = không dời, n = dời tối đa n nến, 999 = không giới hạn, mặc định 999; khi có giới hạn, nến thuộc đoạn đầu cửa sổ chưa thấy nến mở đợt không bao giờ bắn).
4. Hạn hiệu lực (`live.signal_validity`) tính từ lúc nến bắn đóng; quá hạn thì bỏ (`event=signal_expired`). Chờ quá hạn mà nến tham chiếu vẫn chưa về thì WARNING `reference_wait_expired`.
5. Entry/atr lấy từ hàng SETUP của đúng nến bắn (nến dời mượn lý do của nến thô mở đợt), ghi db2 một lần.

**Luật epoch (chủ hệ gọi là "reality epoch") đã kiểm trên dữ liệu thật** (11/11/2026 02:04 UTC, đọc db1 và db2, độc lập với hàm ghép của DE): 1.050 tín hiệu đã lọc đang có ở db2 (knn 272, cloud 226, zigzag 217, breakout 209, tstat 126) đều có `trend_bias` của nến trend đã đóng gần nhất (giờ đóng ≤ giờ đóng nến vào lệnh) cùng chiều tín hiệu: 0 lệch; ngược lại, mọi tín hiệu thô đã khớp trend theo luật đó đều có bản lọc cùng mốc: 0 thiếu (knn 272/272, zigzag 217/217; breakout, cloud, tstat có thêm 18, 29 và 1 tín hiệu dời, chưa kiểm riêng số dời). Nếu dùng luật giờ MỞ của nến vào lệnh (như we trước đây) thì 92 trong 1.050 tín hiệu (8,8%) có bias khác chiều hoặc chưa có nến trend (cloud 47, breakout 22, knn 20, zigzag 2, tstat 1). Phạm vi: chỉ các tín hiệu còn trong db2 (tối đa 7 ngày); không kiểm giá trị bias của từng khối trend; nhánh "chờ" mới có test (`tests/test_signal_publisher.py`) và 0 cảnh báo `reference_wait_expired` làm bằng chứng.

Đã kiểm: bản thô và bản lọc (kể cả tín hiệu dời) giống HỆT pipeline một-tiến-trình cũ trên dữ liệu thử (`tests/test_lane.py`); tín hiệu thô của TRIGGER khớp 100% tín hiệu thô của we (OG8) trên cửa sổ 06–09/11/2026 (648/648, 0 ngược chiều).

## Nguồn nến thêm (`redis.input_sources.<nguồn>`, hiện có `og8`)

Ngoài Core AEN (`redis.input`, cổng 6379, 8 symbol), DE đọc nến của một số symbol từ một Redis khác cùng bố cục (LIST + HASH, 1200 nến): Redis DF trên OG8 (10.11.12.8:6380 db0) nuôi EURUSD, GBPUSD, USDCAD, USDJPY, FR40, SP35, UK100. Mỗi nguồn một nhóm runner riêng (`de-runner@og8`) nên nguồn mất kết nối chỉ làm nhóm đó dừng. Các khoá khác (`timeframes`, `snapshot_bars`, `warmup_bars`, `process_on_startup`) kế thừa `redis.input`. Mỗi symbol chỉ thuộc MỘT nguồn.

## Mỗi file một việc (`src/`)

- `data_engine.py` – **duy nhất** đọc dữ liệu vào tiến trình: nến của nguồn (LIST mới nhất đứng đầu, `HMGET`) và cấu phần của DE (LIST mới nhất đứng cuối, `HGETALL`, SETUP ở db0 và TRIGGER/CONTEXT/TREND ở db1 qua `role_db`); nghe keyspace (nến: mỗi cặp một mẫu; cấu phần: một mẫu cho mỗi (db, vai trò)); bộ đệm gia tăng. Không tự nối Redis.
- `strategy_runner.py` – phía **runner**: `trigger_table` / `context_table` / `trend_table` (bảng thuần, không Redis; `trigger_events` và `setup_view` cắt bảng từng nến thành phần sự kiện và phần trạng thái), `ComponentWriter` (ghi List–Hash, chỉ gửi mốc mới; `drop_before` bỏ sự kiện trôi ra ngoài cửa sổ) và nhớ đệm chỉ báo. Không biết cấu hình ô.
- `signal_publisher.py` – phía **publisher**: `assemble_cell_table` / `entry_complete` / `reference_ready` (ghép SETUP + TRIGGER + CONTEXT thành bảng của ô, kiểm đủ chưa), tên key/kênh, nội dung HASH theo hợp đồng OE, hạn hiệu lực, và `RawSignalWriter` (ghi tín hiệu db2). Không tự gọi lệnh Redis.
- `redis_connector.py` – **duy nhất** nối Redis và **ghi List–Hash nguyên tử dùng chung** (`write_list_hash`) và bỏ mốc cũ khỏi LIST (`trim_lists_before`): tạo kết nối, ping/đóng, nghe keyspace và kiểm cờ, nối lại khi mất kết nối (`keep_connected`).
- `configuration.py` – nạp thông số (đọc `de_config.yaml`, **chỉ file này đọc config**), `StrategySpec` và sổ đăng ký chiến lược, `build_cells` (lưới ô theo bảng của we), và **điều phối**: `build_runner_job` / `build_publisher_job` (dựng + kiểm việc trước khi chạm Redis), `process_frame` (runner) / `process_cell` (publisher), vòng lặp sự kiện, `run_runner` / `run_publisher`.
- `main.py` – lối vào mỏng (`--runner` hoặc `--publisher`, `--lane`, `--source`, `--symbols`; bắt SIGTERM).
- `indicator.py`, `strategies/`, `rules/`, `trend_methods/`, `log.py` – chỉ báo (+ `merge_closed_reference`), logic từng chiến lược, luật lọc/dời tín hiệu (`apply_signal_filters` trả thêm hai mặt nạ `buy_mask`/`sell_mask`), khối trend, ghi log.

## Vận hành

- Menu: `./run_de.sh` (Start = các làn runner rồi publisher; Stop ngược lại; restart / xem log theo tên: để trống = tất cả, `runner`, `publisher`, `trend`, tên một chiến lược như `combo` = các làn runner của nó VÀ publisher của nó, hoặc đúng một làn như `combo.og8`, `trend.default`, `publisher.combo`). Hoặc `systemctl --user {status,restart} 'de-runner@*' 'de-publisher@*'`. Bản cũ (`de-signal@*`, `de-trend@*`, `de-publisher.service` một publisher chung, `de-runner@<nguồn>.service` một runner trọn vai trò) ghi cùng các danh sách nên KHÔNG chạy cùng lúc.
- Log: `core_engine/logs/de_run_runner_<làn>[_<nguồn>].log` (nguồn mặc định bị bỏ: `de_run_runner_combo.log`, `de_run_runner_combo_og8.log`, `de_run_runner_trend.log`), `de_run_publisher_<chiến lược>.log`; thêm `_<symbol>` khi dùng `--symbols`. Mỗi tiến trình một file; journald gần như trống. Mỗi dòng: `thời gian MỨC tiến-trình[pid] module.hàm:dòng: nội dung`.
- Mỗi tiến trình quét toàn bộ việc MỘT lần lúc khởi động (`redis.input.process_on_startup`), không quét định kỳ. Khởi động từ db0/db1 trống: các làn runner chạy song song và ghi ~0,55 triệu hàng (~20 giây), sau đó mới bật publisher (mỗi chiến lược quét 120 ô, có thể phát ngay tín hiệu của nến mới nhất còn hiệu lực).
- Sau khi Redis 6379 nạp lại bản sao (resync) hoặc DE ngừng lâu, restart runner để nó ghi bù cấu phần còn thiếu.
- **Đổi tham số chiến lược** (vd `ma_period`) hoặc tham số `context:`: runner chỉ ghi mốc CHƯA có (HASH đã có thì bỏ qua) nên hàng cũ tính bằng tham số cũ vẫn ở lại tới 500 nến. Chỉ cần restart đúng chiến lược đó (menu: gõ tên chiến lược, hoặc `systemctl --user restart 'de-runner@combo.*' de-publisher@combo`); các chiến lược khác không bị tính lại. Muốn áp ngay cả cho hàng cũ: dừng các làn của chiến lược đó, xoá các list TRIGGER và SETUP của nó (hoặc CONTEXT cho `context:`, do làn `trend` giữ) rồi start lại.
- Đổi config: sửa `core_engine/de_config.yaml` (thật, có mật khẩu) rồi restart tiến trình liên quan; mẫu đầy đủ ở `de_config.example.yaml`.

## Kiểm

```bash
./.venv/bin/python ~/de_verify_tools/run_tests_guarded.py core_engine/tests -q -p no:cacheprovider --tb=no   # chặn mạng; KHÔNG bỏ --tb=no (assertion có thể in mật khẩu cấu hình)
./.venv/bin/python -m ruff check --no-cache .        # nền 10 lỗi I001 cũ (thứ tự import) ở chiến lược, khối trend, rules/trend_tf.py và configuration.py
./.venv/bin/python -B -m vulture core_engine --min-confidence 80
```

Công cụ phát lại / e2e / chuyển – quay lui nằm ngoài repo ở `~/de_verify_tools/` (xem README.txt ở đó; các công cụ viết cho bản 20 tiến trình hoặc bản kế hoạch theo symbol cũ nay đã lỗi thời).
