# core_engine (DE – Decision Engine)

Nến từ Redis 6379 db0 (Core/AEN ghi; 7 symbol Core không có thì đọc từ Redis DF của OG8, xem "Nguồn nến thêm") → chiến lược → tín hiệu ra Redis 6380 theo hai nhánh: **thô** (không lọc trend, db0) và **đã lọc trend** (db2, riêng từng khối trend); xu hướng khung lớn do các tiến trình trend tính sẵn và ghi ở 6380 db1. Chỉ xét từ lúc nến vào db0 đến lúc ra tín hiệu.

## 10 tiến trình, độc lập, chỉ trao đổi qua Redis

| Loại | Lệnh | Việc |
|---|---|---|
| 5 chiến lược `de-signal@<combo\|ma_cross\|macd_break\|ichimoku\|ema_reach>` | `python -m core_engine.src.main --strategy X` | nghe nến vào lệnh của các ô (symbol × khung) của chiến lược, chạy chiến lược rồi ghi hai nhánh. Nhánh 1: tín hiệu **thô** (không lọc trend) vào Redis 6380 **db0** (List–Hash + kênh Pub/Sub cho OE). Nhánh 2: với từng khối trend áp cho ô, đọc xu hướng khối đó ở db1, ghép vào nến, chạy chiến lược có lọc trend và dời tín hiệu, ghi tín hiệu **đã lọc** vào **db2** (cùng cấu trúc, tiền tố khoá và kênh riêng của khối) |
| 5 trend `de-trend@<knn\|breakout\|zigzag\|tstat\|cloud>` | `python -m core_engine.src.main --trend Y` | nghe nến khung trend (db0 cổng 6379), tính hướng trend của từng cặp (symbol × khung H1–H4; breakout thêm M10–M45), ghi vào Redis 6380 **db1** theo List–Hash (`L_TREND_<KHỐI>_<SYMBOL>_<KHUNG>` + HASH `<LIST>:<giờ mở>`, không kênh) |

**Bố cục Redis cổng 6380:** db0 = tín hiệu thô (do tiến trình chiến lược; cùng db với sổ lệnh `L_OE_*` của OE, khác tiền tố), db1 = kết quả trend (do tiến trình trend, `trend_filtering.TrendOutput`), db2 = tín hiệu đã lọc trend (do tiến trình chiến lược, mỗi khối một tiền tố ở `redis.trend_outputs`, vd `L_DE_TSTAT_...` và kênh `L_CHANNEL_TSTAT_...`). Cổng 6379 db0 chỉ có nến của Core, DE chỉ đọc. Tiến trình chiến lược có 4 kết nối: 6379 db0 (đọc nến, nghe event), 6380 db0 (ghi thô), 6380 db1 (đọc xu hướng), 6380 db2 (ghi lọc).

**Quy tắc xu hướng:** nến vào lệnh chỉ dùng xu hướng của nến trend ĐÃ ĐÓNG trước khi nó mở (kể cả cùng khung), nên xu hướng nó cần luôn được ghi từ ít nhất một nến vào lệnh trước và tiến trình chiến lược không phải chờ tiến trình trend. Hệ quả: xu hướng do chính nến trigger tạo ra chỉ có tác dụng từ nến kế tiếp. Khối không dời (knn, zigzag) chỉ xét khi nến mới nhất có tín hiệu thô; khối dời (breakout, tstat, cloud) xét ở mọi nến mới, giữ tín hiệu bị loại đến hết đợt và phát ở nến đầu tiên mà điều kiện còn đúng và xu hướng đã cùng chiều (tối đa một lần mỗi đợt). Chưa có xu hướng của khối ở db1 thì bỏ nhánh 2 của khối đó lần đó và ghi cảnh báo `event=trend_missing`, nhánh 1 không ảnh hưởng.

Tiến trình chết / restart không kéo theo tiến trình khác. Cấu hình quyết định chạy gì: chiến lược = `live.enabled_strategies` (mỗi chiến lược chạy các khung của nó trong `strategies.<tên>` giao với `redis.input.timeframes`, trên mọi symbol của `redis.input.symbols`), khối trend = các khoá của `redis.trend_outputs`.

**Nguồn nến thêm (`redis.input_sources.<nguồn>`, hiện có `og8`):** ngoài Core AEN (`redis.input`, cổng 6379, 8 symbol), DE đọc nến của một số symbol từ một Redis khác cùng bố cục (LIST + HASH, 1200 nến): Redis DF trên OG8 (10.11.12.8:6380 db0) nuôi EURUSD, GBPUSD, USDCAD, USDJPY, FR40, SP35, UK100. Mỗi nguồn một NHÓM tiến trình riêng (5 chiến lược + 5 trend, cùng mã, khác nguồn): `--strategy combo.og8`, `--trend knn.og8`, unit `de-signal@combo.og8`, log `de_run_combo_og8.log`. Tách nhóm vì mất kết nối một Redis làm `keep_connected` nối lại CẢ tiến trình: OG8 chết chỉ dừng nhóm og8, 8 symbol của AEN chạy tiếp. Nguồn thêm chỉ quyết định ĐỌC nến ở đâu và symbol nào (host, port, password, db, key_prefix, symbols; mọi khoá khác như `timeframes`, `snapshot_bars` kế thừa `redis.input`); đầu ra (db0 thô, db1 xu hướng, db2 lọc) vẫn ở Redis cổng 6380 của DE, khoá có tên symbol nên các nhóm không đụng nhau. Mỗi symbol chỉ thuộc MỘT nguồn, và bảng `trend_tf_by_symbol` của chiến lược phải phủ symbol của mọi nguồn. Nến từ nguồn thêm đến chậm hơn Core AEN (Redis DF ghi sau 17 giây đến ~3,4 phút tuỳ ranh giới nến), nên tín hiệu của nhóm này cũng ra muộn hơn. `live.price_decimals` đặt số lẻ làm tròn entry/atr theo symbol (forex 5 hoặc 3; symbol không khai dùng 2).

## Mỗi file một việc (`src/`)

- `data_engine.py` – **duy nhất** làm việc với dữ liệu nến db0: hiểu event nào là cặp nào có nến mới, đọc nến (LIST/HASH), bộ đệm gia tăng. Không tự nối Redis.
- `signal_publisher.py` – phần dùng chung về tín hiệu (cho cả tín hiệu thô và đã lọc): tên key/kênh, nội dung HASH theo hợp đồng OE, cửa sổ hiệu lực. Không nối Redis; không còn là một tiến trình.
- `redis_connector.py` – **duy nhất** nối Redis và **ghi List–Hash nguyên tử dùng chung** (`write_list_hash`: HASH + LIST + kênh tuỳ chọn, ghi đúng một lần): tạo kết nối, ping/đóng, nghe keyspace và kiểm cờ, nối lại khi mất kết nối (`keep_connected`).
- `trend_filtering.py` – `compute_timeline`: bảng nến khung trend vào, bảng hướng trend của từng nến ra; `TrendOutput` ghi kết quả vào db1 theo List–Hash (chỉ gửi nến mới); `TrendReader` đọc lại kết quả đó cho tiến trình chiến lược (nơi duy nhất ngoài `data_engine` được đọc dữ liệu, vì file này giữ định dạng db1; `trend_list_key` đặt tên khoá cho cả ghi lẫn đọc). Không config.
- `strategy_runner.py` – chạy MỘT chiến lược cho một cặp (bảng nến vào -> chỉ báo -> [ghép xu hướng nếu được đưa bảng xu hướng] -> quy tắc BUY/SELL -> rules -> bảng có `signal`, kèm bộ nhớ đệm chỉ báo) và `RawSignalWriter` (một bộ ghi cho mỗi nguồn: thô hoặc một khối trend) ghi tín hiệu sau khi kiểm hạn hiệu lực (List–Hash + kênh). Không tự tính xu hướng, không gọi Redis.
- `configuration.py` – nạp thông số (đọc `de_config.yaml`, **chỉ file này đọc config**), `StrategySpec` và sổ đăng ký chiến lược, và **điều phối**: `build_lane_job` (dựng + kiểm việc trước khi chạm Redis, gồm bảng "ô → khối trend") / `build_trend_job`, `process_pair` (hai nhánh) / `process_trend_pair`, vòng lặp sự kiện, `run` / `run_trend`.
- `main.py` – lối vào mỏng (`--strategy` hoặc `--trend`, thêm `.<nguồn>` để chọn nguồn nến thêm; bắt SIGTERM).
- `indicator.py`, `strategies/`, `rules/`, `trend_methods/`, `log.py` – chỉ báo, logic từng chiến lược, luật lọc/dời tín hiệu, khối trend, ghi log (không đổi trong đợt tách này).

Luật dời tín hiệu (breakout/tstat/cloud) và luật chỉ giữ nến đầu (alternating) nằm ở `rules/` + `rules/trend_tf.py` (bảng POLICY); luật chỉ giữ nến đầu chạy ở cả hai nhánh, luật dời tín hiệu chạy ở nhánh có lọc trend.

## Vận hành

- Menu: `./run_de.sh` (Start = trend trước rồi chiến lược, nhóm mặc định rồi từng nhóm nguồn thêm; Stop ngược lại; restart một tiến trình hoặc tất cả; xem log). Hoặc `systemctl --user {status,restart} 'de-signal@*' 'de-trend@*'`.
- Log: `core_engine/logs/de_run_<chiến lược>.log`, `de_run_trend_<khối>.log`; nhóm nguồn thêm thêm `_<nguồn>` (`de_run_combo_og8.log`, `de_run_trend_knn_og8.log`). Mỗi tiến trình một file; journald gần như trống. Mỗi dòng: `thời gian MỨC tiến-trình[pid] module.hàm:dòng: nội dung`, vd `... INFO    combo_og8[53166] strategy_runner.publish_latest:262: event=signal_published ...` (tiến-trình = tên file log; `module.hàm:dòng` = file `src/<module>.py` đã phát dòng đó; lỗi bất ngờ kèm traceback đầy đủ).
- Mỗi tiến trình quét toàn bộ cặp MỘT lần lúc khởi động (`redis.input.process_on_startup`), không quét định kỳ; sau restart có thể phát ngay tín hiệu còn hiệu lực.
- Đổi config: sửa `core_engine/de_config.yaml` (thật, có mật khẩu) rồi restart tiến trình liên quan; mẫu đầy đủ ở `de_config.example.yaml`.

## Kiểm

```bash
./.venv/bin/python ~/de_verify_tools/run_tests_guarded.py core_engine/tests -q -p no:cacheprovider --tb=no   # chặn mạng; KHÔNG bỏ --tb=no (assertion có thể in mật khẩu cấu hình)
./.venv/bin/python -m ruff check --no-cache .        # nền 10 lỗi I001 cũ (thứ tự import) ở chiến lược, khối trend, rules/trend_tf.py và configuration.py
./.venv/bin/python -B -m vulture core_engine --min-confidence 80
```

Công cụ phát lại / e2e / chuyển – quay lui nằm ngoài repo ở `~/de_verify_tools/` (xem README.txt ở đó).
