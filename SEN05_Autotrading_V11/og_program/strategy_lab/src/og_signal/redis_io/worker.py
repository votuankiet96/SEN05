"""Vòng lặp chính của luồng "backtest cấp 2": nghe trigger backfill từ DP
(qua redis_io/listener.py), với mỗi (symbol, timeframe) báo thay đổi VÀ khớp
đúng phạm vi cấu hình (symbol trong strategies.combo.symbol_x, timeframe
khớp _STRATEGY_TIMEFRAMES của đúng 1 chiến lược -- xem docstring
_make_on_update(); trigger ngoài phạm vi bị bỏ qua, chốt 2026-09-21 vì DP
backfill 1 universe symbol rộng hơn nhiều so với symbol operator cấu hình),
tính lại lịch sử signal cho đúng chiến lược khớp timeframe đó, publish lên
**2 DB song song** (chốt 2026-09-26, qua redis_io/publisher.py): DB2
"past_signal" (không lọc trend, `run_strategy()`) và DB3 "past_signal_trend"
(có lọc trend khung lớn, `run_strategy_with_trend_reference()`) -- cùng 1
lần tải entry_bars, cùng 1 trigger, chỉ khác client/key_prefix đích và việc
có tải thêm trend_bars hay không. 2 kênh dùng để đối chứng khi ctrader cli
simulation (VM-BO20) backtest cùng chiến lược có/không trend.

Chốt 2026-09-23 -- CHỈ TÍNH ĐÚNG PHẦN THAY ĐỔI, không phải toàn bộ lịch sử
mỗi lần trigger: DP nay publish kèm `candle_count`/`from`/`to` mô tả đúng
phạm vi nến vừa mới/đổi (xem listener.py + memory
project_strategy_lab_redesign, yêu cầu strategy_lab tự gửi DP). Khi trigger
có đủ `from`/`to`, refresh_pair() chỉ load_range_with_warmup() + tính lại
đúng `[from, to]` rồi GHÉP vào phần DB2 hiện có (_merge_delta_frame() +
publisher.read_past_signals()) thay vì tính lại từ SIGNAL_START_DATE. Khi
thiếu (payload cũ, hoặc DP fallback vì delivered_candles rỗng phía họ) --
rơi về đúng hành vi full-recompute gốc, không đổi gì. 2 đường dùng chung 1
hàm refresh_pair(), khác nhau ở chỗ có date_from/date_to hay không -- xem
docstring hàm đó để biết chi tiết từng bước.

Bootstrap lúc start/reconnect (chốt 2026-09-21): mỗi lần vào lại vòng lặp
kết nối trong run_forever() -- chạy lần đầu HAY sau khi mất kết nối Redis
giữa chừng -- worker chạy lại cho toàn bộ pair trong _bootstrap_pairs()
TRƯỚC khi subscribe, không đợi trigger đầu tiên. Lý do:
Pub/Sub là fire-and-forget, bất kỳ khoảng thời gian nào worker không kết
nối (lần đầu chạy, hoặc rớt kết nối tạm thời) đều có thể làm mất trigger
thật, khiến DB2 "cũ" hơn SQL mà không ai biết. Bootstrap là lưới an toàn
bù đúng khoảng trống đó -- ghi đè idempotent (publish_past_signals() luôn
DELETE+RPUSH lại từ đầu), chạy lại nhiều lần không sao. Phạm vi
_bootstrap_pairs(): symbol lấy từ strategies.combo.symbol_x (sl_config.yaml)
-- đúng tập symbol operator đã chủ động cấu hình để trade -- ghép với đúng
timeframe riêng của từng chiến lược (_STRATEGY_TIMEFRAMES: combo chỉ
H1-H4, ma_cross chỉ M10/M20/M30/M45 -- KHÔNG dùng chung 1 danh sách hợp
nhất giữa 2 chiến lược, xem chi tiết lý do ở _STRATEGY_TIMEFRAMES). Cố ý
KHÔNG dùng toàn bộ symbol active trên DP6 (db_connector.symbols()) -- sẽ
đụng cả symbol lạ chưa chắc cần signal, nặng SQL không cần thiết.

Chốt 2026-09-24 -- CLEAR SẠCH REDIS ĐÚNG 1 LẦN, chỉ khi tiến trình thật
sự khởi động (không phải mỗi lần reconnect như bootstrap ở trên): sự cố
thật cùng ngày cho thấy đổi cách đặt tên (bartime format) khiến publish_
past_signals() để sót ~53% dữ liệu cũ làm rác vĩnh viễn, vì nó chỉ biết
xoá-rồi-ghi-đè đúng tên trùng, không biết dọn tên đã đổi. Thay vì sửa hàm
ghi để tự dò-diệt rác (phức tạp), clear_all_signals() (publisher.py) xoá
sạch toàn bộ trước khi bootstrap lần đầu của tiến trình -- rác chỉ có thể
sinh ra do đổi code/tham số, mà việc đó chỉ có hiệu lực sau khi restart,
nên chỉ cần đảm bảo mỗi lần restart đều xuất phát từ Redis sạch tinh.
Cờ `_CLEARED_THIS_PROCESS` phân biệt "process thật sự mới" khỏi "chỉ mất
kết nối Redis tạm thời rồi tự nối lại" (bootstrap ở trên vẫn chạy lại
bình thường trong trường hợp sau, KHÔNG kèm clear -- dữ liệu cũ vẫn đúng,
xoá thêm chỉ tạo khoảng trống Redis không cần thiết mỗi lần mạng chập
chờn). Chấp nhận DB2 trống hoàn toàn trong lúc bootstrap lại (~3-5 phút,
88 pair) mỗi khi có restart thật -- dữ liệu backtest, không cần luôn sẵn
sàng tức thời.

Sau bootstrap, quay lại thuần trigger-driven -- mất 1 message Pub/Sub giữa
2 lần bootstrap chỉ mất đúng lần cập nhật đó, chờ lần backfill kế tiếp mới
có trigger mới, hoặc chờ lần bootstrap kế (worker restart/reconnect) tự bù
lại. CHƯA có reconcile định kỳ tách riêng ngoài bootstrap này (khác bản
order_gateway/og_signal cũ vốn quét lại DB0 mỗi 1800s) -- nếu Redis chập
chờn liên tục, bootstrap có thể chạy lặp lại nhiều lần liên tiếp, mỗi lần
quét hết _bootstrap_pairs() -- chấp nhận đánh đổi này thay vì thêm throttle
phức tạp chưa ai yêu cầu.

Xem memory project_strategy_lab_redesign cho toàn bộ bối cảnh thiết kế.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable, Iterable

import pandas as pd
import redis

from strategy_lab.src.configuration import (
    COMBO_RECOMMENDED_TIMEFRAMES,
    COMBO_SYMBOL_X,
    MA_CROSS_SUPPORTED_TIMEFRAMES,
    SIGNAL_START_DATE,
    STRATEGIES,
    WARMUP_BARS,
    get_strategy,
    run_strategy,
    run_strategy_with_trend_reference,
    trim_to_requested_range,
)
from strategy_lab.src.db_connector import load_range_with_warmup
from strategy_lab.src.og_signal.export_cli import signal_frame
from strategy_lab.src.og_signal.redis_io.client import create_client, load_config
from strategy_lab.src.og_signal.redis_io.listener import listen_for_backfill_events
from strategy_lab.src.og_signal.redis_io.logging_setup import (
    configure_logging,
    log_event,
)
from strategy_lab.src.og_signal.redis_io.publisher import (
    clear_all_signals,
    publish_past_signals,
    read_past_signals,
)

# CỐ Ý dùng tên cố định, KHÔNG dùng logging.getLogger(__name__) -- đây là
# file entry point chạy qua `python -m strategy_lab.src.og_signal.redis_io.
# worker`, lúc đó Python tự gán __name__ = "__main__" cho chính file này,
# khiến logger bị đặt tên "__main__" thay vì "...redis_io.worker" -- không
# còn là hậu duệ của logger gốc "strategy_lab" (xem configure_logging()),
# không thừa kế handler nào, MỌI log của worker.py biến mất im lặng dù
# code chạy đúng (xác nhận THẬT 2026-09-22, sau nhiều giờ tưởng nhầm là
# lỗi signal/SIGTERM). client.py/listener.py/publisher.py không dính lỗi
# này vì chúng luôn được import bình thường (giữ đúng __name__ dạng
# module), chỉ file được gọi trực tiếp qua `-m` mới bị.
LOGGER = logging.getLogger("strategy_lab.src.og_signal.redis_io.worker")

# Mốc thời gian tiến trình bắt đầu -- dùng tính uptime cho log heartbeat/
# service_stopped, đặt ở mức module (chạy đúng 1 lần lúc import, gần như
# trùng lúc tiến trình thật sự khởi động) để khỏi phải truyền tay qua
# nhiều lớp hàm.
_PROCESS_START = time.monotonic()

# Cờ dừng hợp tác (cooperative shutdown) -- signal handler CHỈ set cờ này,
# không làm gì khác (không log, không I/O). Vòng lặp chính (run_forever/
# bootstrap_all/listen_for_backfill_events) tự kiểm tra cờ ở các điểm dừng
# tự nhiên rồi mới log/thoát. Dùng threading.Event (không phải bool thường)
# vì cần .is_set truyền thẳng làm callback cho
# listen_for_backfill_events(should_stop=...).
#
# ĐÃ KIỂM CHỨNG THẬT qua systemd (2026-09-22, marker file độc lập với
# logging): cơ chế dừng bằng cờ HOẠT ĐỘNG ĐÚNG -- SIGTERM giữa lúc
# bootstrap khiến bootstrap_all() dừng đúng tại pair đang xử lý dở, không
# chạy tiếp 90+ pair còn lại (khác hẳn cách raise SystemExit trong handler
# cũ, gần như không bao giờ propagate kịp qua các lời gọi pyodbc/redis
# đang chặn). HẠN CHẾ CÒN LẠI đã biết: 1-2 dòng log CUỐI CÙNG mô tả lý do
# dừng (bootstrap_interrupted/service_stopping/service_stopped) đôi khi
# KHÔNG kịp ghi vào file nếu SIGTERM tới ngay sát 1 lời gọi pyodbc/redis
# đang chặn -- nghi do tương tác giữa signal delivery và lock nội bộ của
# `logging` module (Python 3.14), CHƯA xác định được root cause chính
# xác. Hành vi dừng THẬT (không tính dòng log) vẫn đúng trong mọi trường
# hợp đã test. Chấp nhận hạn chế này -- không dư thời gian truy tới cùng
# gốc rễ, và tác động thực tế nhỏ (chỉ mất 1-2 dòng log cuối, không mất
# dữ liệu, không treo service).
_SHUTDOWN = threading.Event()

# True sau khi đã clear_all_signals() ĐÚNG 1 LẦN cho tiến trình này (chốt
# 2026-09-24). Phân biệt "tiến trình thật sự khởi động" (systemctl restart/
# start -- process mới, cờ này reset về False) khỏi "chỉ mất kết nối Redis
# tạm thời rồi run_forever() tự nối lại" (vẫn CÙNG 1 tiến trình, vòng lặp
# connect chạy lại nhưng KHÔNG xoá gì -- dữ liệu cũ trong TH đó vẫn đúng,
# xoá đi chỉ tạo ra khoảng trống Redis không cần thiết mỗi lần mạng chập
# chờn). Không dùng threading.Event vì chỉ set 1 chiều, không cần chờ đợi
# giống _SHUTDOWN.
_CLEARED_THIS_PROCESS = False

# Timeframe riêng của từng chiến lược -- combo: khuyến nghị (H1-H4, không
# phải giới hạn cứng nhưng đây là phạm vi operator thật sự muốn theo dõi ở
# luồng Redis DB2); ma_cross: giới hạn cứng (M10/M20/M30/M45). Hai tập này
# KHÔNG giao nhau. Chốt 2026-09-21: bootstrap/trigger phải dùng đúng tập
# riêng của từng chiến lược, không gộp thành 1 danh sách hợp nhất rồi để
# combo (vốn không bị chặn cứng) lấn sang chạy cả trên M10-M45 của ma_cross.
_STRATEGY_TIMEFRAMES: dict[str, tuple[str, ...]] = {
    "combo": COMBO_RECOMMENDED_TIMEFRAMES,
    "ma_cross": MA_CROSS_SUPPORTED_TIMEFRAMES,
}


def _strategy_for_timeframe(timeframe: str) -> str | None:
    """Chiến lược nào coi timeframe này là của mình, hoặc None nếu không
    chiến lược nào nhận. 2 tập timeframe của _STRATEGY_TIMEFRAMES rời nhau
    hoàn toàn nên tối đa khớp đúng 1 chiến lược, không bao giờ cả 2.
    """
    for strategy_key, timeframes in _STRATEGY_TIMEFRAMES.items():
        if timeframe in timeframes:
            return strategy_key
    return None


def _merge_delta_frame(
    client: redis.Redis,
    *,
    key_prefix: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    delta_frame: pd.DataFrame,
    window_from: pd.Timestamp,
    window_to: pd.Timestamp,
) -> pd.DataFrame:
    """Ghép `delta_frame` (signal vừa tính lại cho đúng [window_from,
    window_to]) vào phần DB2 hiện có của pair này -- giữ nguyên mọi dòng
    NGOÀI cửa sổ, THAY HẲN mọi dòng TRONG cửa sổ bằng delta_frame (kể cả
    khi delta_frame rỗng: nghĩa là sau khi DP sửa nến, các bar trong cửa sổ
    đó không còn thoả điều kiện signal nào nữa -- phải biến mất khỏi DB2,
    không được giữ lại signal cũ đã sai).

    So sánh biên bằng bartime thật (parse từ chuỗi "YYYY-MM-DD HH:MM" mà
    signal_frame()/publish_past_signals() đang dùng thống nhất), không phải
    so chuỗi. window_to dùng so sánh <= (không phải <) vì delta_frame chính
    là kết quả SQL đã lọc đúng đến window_to -- 1 dòng cũ trên DB2 đúng bằng
    window_to phải được coi là "trong cửa sổ", để delta_frame quyết định nó
    còn tồn tại hay không, không phải giữ nguyên bản cũ.

    Không sửa gì trên Redis ở đây -- chỉ trả về 1 DataFrame đã ghép xong,
    caller (refresh_pair()) vẫn gọi publish_past_signals() y như đường
    full-recompute, nên publisher.py không cần biết khái niệm "delta".
    """
    existing = read_past_signals(
        client, key_prefix=key_prefix, symbol=symbol, timeframe=timeframe, strategy=strategy,
    )
    if existing.empty:
        return delta_frame

    # Có giây (chốt 2026-09-24, đồng bộ với signal_frame()) -- existing đọc
    # lại từ Redis phải parse ĐÚNG format mà export_cli.signal_frame() ghi
    # ra, nếu không sẽ raise ValueError. Dữ liệu cũ trên Redis (ghi trước
    # 2026-09-24, thiếu giây) đã được làm sạch lại 1 lần qua bootstrap full
    # ngay sau khi đổi -- xem memory project_strategy_lab_redesign.
    existing_bartime = pd.to_datetime(existing["bartime"], format="%Y-%m-%d %H:%M:%S")
    outside_window = existing[(existing_bartime < window_from) | (existing_bartime > window_to)]
    if outside_window.empty:
        return delta_frame

    merged = pd.concat([outside_window, delta_frame], ignore_index=True)
    sort_key = pd.to_datetime(merged["bartime"], format="%Y-%m-%d %H:%M:%S")
    return merged.loc[sort_key.sort_values().index].reset_index(drop=True)


def _compute_and_publish(
    client: redis.Redis,
    *,
    key_prefix: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    enriched: pd.DataFrame,
    effective_from: pd.Timestamp,
    delta_mode: bool,
    window_to: pd.Timestamp | None,
) -> int:
    """Trim -> signal_frame -> (ghép delta nếu có) -> publish. Dùng chung
    cho cả nhánh DB2 (không trend, `enriched` từ run_strategy()) lẫn DB3
    (có trend, `enriched` từ run_strategy_with_trend_reference()) trong
    refresh_pair() -- 2 nhánh chỉ khác `client`/`key_prefix`/`enriched`
    truyền vào, phần còn lại (trim/frame/merge/publish) giống hệt nhau nên
    gom về đúng 1 chỗ thay vì lặp lại 2 lần trong refresh_pair().
    """
    trimmed = trim_to_requested_range(enriched, effective_from)
    frame = signal_frame(trimmed, strategy=strategy)
    if delta_mode:
        frame = _merge_delta_frame(
            client, key_prefix=key_prefix, symbol=symbol, timeframe=timeframe,
            strategy=strategy, delta_frame=frame, window_from=effective_from, window_to=window_to,
        )
    return publish_past_signals(
        client, key_prefix=key_prefix, symbol=symbol, timeframe=timeframe,
        strategy=strategy, frame=frame,
    )


def refresh_pair(
    client: redis.Redis,
    *,
    symbol: str,
    timeframe: str,
    key_prefix: str,
    trend_client: redis.Redis,
    trend_key_prefix: str,
    strategy_keys: Iterable[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    """Tính lại signal của (symbol, timeframe) cho các chiến lược trong
    `strategy_keys`, publish từng chiến lược lên **2 DB song song** (chốt
    2026-09-26): DB2 (`client`/`key_prefix`, không lọc trend) và DB3
    (`trend_client`/`trend_key_prefix`, có lọc trend khung lớn) -- cùng 1
    lần tải entry_bars (`raw` bên dưới), DB3 chỉ tải thêm đúng 1 nguồn phụ
    (trend_bars, khung `TREND_TF` của chiến lược, mặc định H4).

    2 chế độ, chọn theo việc có đủ `date_from`+`date_to` hay không:

    - ĐỦ CẢ HAI (chế độ "delta", chốt 2026-09-23 -- trigger từ DP kèm đúng
      phạm vi nến vừa mới/đổi, xem listener.py): chỉ
      load_range_with_warmup(date_from, date_to) + tính signal đúng đoạn
      đó, rồi GHÉP vào phần DB hiện có qua _merge_delta_frame() trước khi
      publish. `date_from` được kẹp không sớm hơn SIGNAL_START_DATE --
      SIGNAL_START_DATE là biên cứng của toàn bộ luồng DB2/DB3, DP có thể
      báo thay đổi ở nến còn cũ hơn cả biên đó (backfill full history),
      phần đó nằm ngoài phạm vi strategy_lab nên bỏ qua, không phải lỗi.
    - THIẾU 1 TRONG 2 (chế độ "full" -- payload cũ, DP fallback vì
      delivered_candles rỗng phía họ, hoặc bootstrap_all() không truyền gì
      cả): hành vi GỐC trước 2026-09-23 -- load_range_with_warmup từ
      SIGNAL_START_DATE tới hiện tại, tính lại toàn bộ, publish_past_signals()
      tự DELETE+RPUSH lại từ đầu, không ghép gì cả.

    Cả 2 chế độ dùng chung publish_past_signals() không đổi -- khác nhau
    duy nhất ở chỗ frame truyền vào đã được ghép trước hay chưa. Áp dụng
    như nhau cho cả đường gọi từ bootstrap_all() lẫn trigger thật
    (_make_on_update()).

    strategy_keys: chỉ thử đúng các chiến lược này -- None (mặc định) = thử
        toàn bộ STRATEGIES, chiến lược nào không hỗ trợ timeframe này (vd
        ma_cross ngoài M10/M20/M30/M45) bị bỏ qua ở bước kiểm
        supported_timeframes bên dưới (có log DEBUG), không phải lỗi thật. bootstrap_all()/_make_on_update() LUÔN truyền đúng 1
        chiến lược khớp _STRATEGY_TIMEFRAMES cho timeframe đó -- combo
        không có giới hạn cứng nên nếu không chặn ở đây, nó vẫn chạy được
        (và từng chạy nhầm) trên cả M10-M45 của ma_cross (chốt sửa
        2026-09-21, xem _STRATEGY_TIMEFRAMES).

    DB3 luôn lọc trend bất kể `strategies.*.trend_filter_enabled` trong
    sl_config.yaml đang true/false -- run_strategy_with_trend_reference() tự
    ép TREND_FILTER_ENABLED=True bên trong, cờ config đó chỉ ảnh hưởng
    DB2/dashboard/live, không ảnh hưởng DB3.

    Trả về tổng số dòng đã publish (cộng dồn cả DB2 lẫn DB3, mọi chiến lược
    áp dụng được).
    """
    delta_mode = bool(date_from) and bool(date_to)
    if delta_mode:
        effective_from = max(pd.Timestamp(date_from), pd.Timestamp(SIGNAL_START_DATE))
        window_to = pd.Timestamp(date_to)
        raw = load_range_with_warmup(symbol, timeframe, effective_from, window_to, WARMUP_BARS)
    else:
        effective_from = pd.Timestamp(SIGNAL_START_DATE)
        window_to = None
        raw = load_range_with_warmup(symbol, timeframe, SIGNAL_START_DATE, None, WARMUP_BARS)

    if raw.empty:
        log_event(
            LOGGER, logging.WARNING, "no_sql_data", result="skip",
            symbol=symbol, timeframe=timeframe, mode="delta" if delta_mode else "full",
        )
        return 0

    keys_to_try = list(strategy_keys) if strategy_keys is not None else list(STRATEGIES)
    total = 0
    for strategy_key in keys_to_try:
        # Kiểm timeframe TRƯỚC khi chạy, thay vì bắt ValueError của
        # run_strategy(). Bản cũ dùng `except ValueError: continue` và nuốt IM
        # LẶNG mọi ValueError khác — gồm cả lỗi cấu hình thật ("FAST_MA must be
        # smaller than SLOW_MA", "TREND_TYPE must be 'knn'", "SESSION_HOURS_UTC
        # must contain UTC hours in range 0..23") — khiến toàn bộ 44 pair của 1
        # chiến lược không được refresh mà KHÔNG một dòng log nào, trong khi
        # bootstrap_done vẫn báo result=ok (audit 2026-09-22). Giờ chỉ timeframe
        # ngoài phạm vi mới bị bỏ qua (có log), mọi lỗi khác nổi lên
        # _safe_refresh_pair() và được ghi result=fail kèm traceback.
        spec = get_strategy(strategy_key)
        if spec.supported_timeframes and timeframe not in spec.supported_timeframes:
            log_event(
                LOGGER, logging.DEBUG, "strategy_timeframe_skipped", result="skip",
                strategy=strategy_key, symbol=symbol, timeframe=timeframe,
            )
            continue

        # DB2 -- không lọc trend, hành vi không đổi so với trước 2026-09-26.
        started_at = time.monotonic()
        enriched = run_strategy(strategy_key, symbol=symbol, tf=timeframe, bars=raw)
        count = _compute_and_publish(
            client, key_prefix=key_prefix, symbol=symbol, timeframe=timeframe,
            strategy=strategy_key, enriched=enriched, effective_from=effective_from,
            delta_mode=delta_mode, window_to=window_to,
        )
        duration_ms = round((time.monotonic() - started_at) * 1000)
        log_event(
            LOGGER, logging.INFO, "pair_refreshed", db="past_signal",
            strategy=strategy_key, symbol=symbol, timeframe=timeframe,
            rows=count, duration_ms=duration_ms, mode="delta" if delta_mode else "full",
        )
        total += count

        # DB3 -- có lọc trend (chốt 2026-09-26). TREND_TF lấy từ
        # default_trend_tf khai trong sl_config.yaml (qua normalize_params),
        # không hardcode "H4" ở đây -- entry_bars (`raw`) dùng lại nguyên
        # vẹn, chỉ tải thêm trend_bars.
        started_at = time.monotonic()
        trend_tf = str(spec.normalize_params(None, symbol, timeframe)["TREND_TF"])
        trend_bars = load_range_with_warmup(symbol, trend_tf, effective_from, window_to, WARMUP_BARS)
        trend_enriched = run_strategy_with_trend_reference(
            strategy_key, symbol=symbol, entry_tf=timeframe, entry_bars=raw,
            trend_tf=trend_tf, trend_bars=trend_bars,
        )
        trend_count = _compute_and_publish(
            trend_client, key_prefix=trend_key_prefix, symbol=symbol, timeframe=timeframe,
            strategy=strategy_key, enriched=trend_enriched, effective_from=effective_from,
            delta_mode=delta_mode, window_to=window_to,
        )
        duration_ms = round((time.monotonic() - started_at) * 1000)
        log_event(
            LOGGER, logging.INFO, "pair_refreshed", db="past_signal_trend",
            strategy=strategy_key, symbol=symbol, timeframe=timeframe, trend_tf=trend_tf,
            rows=trend_count, duration_ms=duration_ms, mode="delta" if delta_mode else "full",
        )
        total += trend_count
    return total


def _bootstrap_pairs() -> list[tuple[str, str, str]]:
    """Danh sách (strategy, symbol, timeframe) cần full-history lúc bootstrap.

    symbol: các key của strategies.combo.symbol_x trong sl_config.yaml.
    timeframe: đúng timeframe riêng của từng chiến lược (_STRATEGY_TIMEFRAMES)
    -- combo chỉ ghép H1-H4, ma_cross chỉ ghép M10/M20/M30/M45, không còn
    dùng chung 1 danh sách hợp nhất giữa 2 chiến lược nữa.
    """
    symbols = sorted(COMBO_SYMBOL_X)
    return [
        (strategy_key, symbol, tf)
        for strategy_key, timeframes in _STRATEGY_TIMEFRAMES.items()
        for symbol in symbols
        for tf in timeframes
    ]


def _safe_refresh_pair(
    output_client: redis.Redis,
    *,
    symbol: str,
    timeframe: str,
    key_prefix: str,
    trend_client: redis.Redis,
    trend_key_prefix: str,
    strategy_keys: Iterable[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> None:
    """refresh_pair() với biên chịu lỗi dùng chung cho cả trigger lẫn
    bootstrap: RedisError propagate ra ngoài (run_forever() tự reconnect),
    lỗi khác chỉ log rồi bỏ qua đúng 1 pair -- không để 1 pair lỗi chặn cả
    vòng lặp subscribe hoặc cả lượt bootstrap còn lại. Lỗi ở nhánh DB3 (vd
    SQL trend_bars lỗi) cũng rơi vào biên chịu lỗi này giống DB2 -- không
    tách riêng, vì refresh_pair() tính cả 2 DB trong cùng 1 lời gọi.

    date_from/date_to: truyền thẳng xuống refresh_pair() -- bootstrap_all()
    không truyền gì (None, None -- luôn full-recompute, đúng ý "bootstrap
    là lưới an toàn phủ hết", xem docstring bootstrap_all()), chỉ
    _make_on_update() mới có thể truyền giá trị thật từ trigger DP.
    """
    try:
        refresh_pair(
            output_client,
            symbol=symbol,
            timeframe=timeframe,
            key_prefix=key_prefix,
            trend_client=trend_client,
            trend_key_prefix=trend_key_prefix,
            strategy_keys=strategy_keys,
            date_from=date_from,
            date_to=date_to,
        )
    except redis.RedisError:
        raise
    except Exception as exc:  # noqa: BLE001 -- biên chịu lỗi có chủ đích, không để 1 pair lỗi chặn cả vòng lặp.
        log_event(
            LOGGER, logging.ERROR, "pair_refresh_failed", result="fail", exc_info=True,
            symbol=symbol, timeframe=timeframe, error=str(exc),
        )


def bootstrap_all(
    output_client: redis.Redis,
    *,
    key_prefix: str,
    trend_client: redis.Redis,
    trend_key_prefix: str,
) -> None:
    """Chạy refresh_pair() (từ SIGNAL_START_DATE) cho toàn bộ pair trong
    _bootstrap_pairs() -- mỗi pair chỉ tính đúng 1 chiến lược khớp timeframe
    của nó (_STRATEGY_TIMEFRAMES), không thử chiến lược kia. Gọi lúc worker
    mới start hoặc vừa reconnect Redis, TRƯỚC khi subscribe, để DB2/DB3
    không bao giờ "cũ" hơn SQL vì lỡ mất trigger Pub/Sub trong khoảng không
    kết nối (xem docstring module).

    Kiểm tra `_SHUTDOWN` giữa mỗi pair -- bootstrap 88 pair có thể mất tới
    vài trăm giây (tăng so với bản chỉ DB2 vì mỗi pair giờ tính thêm DB3),
    nếu không dừng giữa chừng khi SIGTERM tới thì graceful shutdown sẽ bị
    trễ cả phút (xem docstring `_SHUTDOWN`).
    """
    pairs = _bootstrap_pairs()
    combo_count = sum(1 for strategy_key, _, _ in pairs if strategy_key == "combo")
    ma_cross_count = len(pairs) - combo_count
    log_event(
        LOGGER, logging.INFO, "bootstrap_start",
        pair_count=len(pairs), combo_pairs=combo_count, ma_cross_pairs=ma_cross_count,
    )
    started_at = time.monotonic()
    for index, (strategy_key, symbol, timeframe) in enumerate(pairs):
        if _SHUTDOWN.is_set():
            log_event(
                LOGGER, logging.INFO, "bootstrap_interrupted", result="skip",
                completed=index, remaining=len(pairs) - index,
            )
            return
        _safe_refresh_pair(
            output_client,
            symbol=symbol,
            timeframe=timeframe,
            key_prefix=key_prefix,
            trend_client=trend_client,
            trend_key_prefix=trend_key_prefix,
            strategy_keys=[strategy_key],
        )
    duration_ms = round((time.monotonic() - started_at) * 1000)
    log_event(LOGGER, logging.INFO, "bootstrap_done", pair_count=len(pairs), duration_ms=duration_ms)


def _make_on_update(
    output_client: redis.Redis,
    *,
    key_prefix: str,
    trend_client: redis.Redis,
    trend_key_prefix: str,
) -> Callable[[str, str, str | None, str | None], None]:
    """Trigger thật từ DP không tự giới hạn theo symbol/timeframe nào --
    DP backfill một universe rộng hơn nhiều so với 11 symbol operator cấu
    hình (strategies.combo.symbol_x), nên nếu xử lý mù quáng mọi trigger sẽ
    ghi vào DB2/DB3 cả những symbol ngoài phạm vi (combo còn tính sai
    entry_price cho symbol không có trong symbol_x -- X rơi về fallback 0.0,
    xem _combo_symbol_params()). Chốt 2026-09-21: chỉ xử lý trigger có symbol
    nằm trong symbol_x VÀ timeframe khớp _STRATEGY_TIMEFRAMES của đúng 1
    chiến lược -- chỉ tính/ghi cho đúng chiến lược đó (không thử chiến lược
    kia), trigger ngoài phạm vi bị bỏ qua có log.

    date_from/date_to (chốt 2026-09-23): truyền thẳng từ message DP xuống
    refresh_pair() qua _safe_refresh_pair() -- None cả hai thì tự rơi về
    full-recompute (payload cũ hoặc DP fallback), không cần biết trước ở
    đây là chế độ nào.
    """
    allowed_symbols = set(COMBO_SYMBOL_X)

    def on_update(timeframe: str, symbol: str, date_from: str | None, date_to: str | None) -> None:
        strategy_key = _strategy_for_timeframe(timeframe)
        if strategy_key is None or symbol not in allowed_symbols:
            log_event(
                LOGGER, logging.DEBUG, "trigger_skipped", result="skip",
                symbol=symbol, timeframe=timeframe, reason="out_of_scope",
            )
            return
        log_event(
            LOGGER, logging.INFO, "trigger_accepted",
            symbol=symbol, timeframe=timeframe, strategy=strategy_key,
            mode="delta" if (date_from and date_to) else "full",
        )
        _safe_refresh_pair(
            output_client,
            symbol=symbol,
            timeframe=timeframe,
            key_prefix=key_prefix,
            trend_client=trend_client,
            trend_key_prefix=trend_key_prefix,
            strategy_keys=[strategy_key],
            date_from=date_from,
            date_to=date_to,
        )

    return on_update


def _make_on_reconcile() -> Callable[[], None]:
    """Heartbeat -- chứng minh worker còn sống/đang lắng nghe trong log,
    KHÔNG chạy lại refresh/SQL/Redis gì cả (khác hẳn khái niệm "reconcile
    quét lại toàn bộ pair" từng để ngỏ, xem docstring module). Nhịp đặt qua
    `reconcile_interval_seconds` khi gọi `listen_for_backfill_events()`.
    """
    pair_count = len(_bootstrap_pairs())

    def on_reconcile() -> None:
        log_event(
            LOGGER, logging.INFO, "heartbeat",
            uptime_seconds=round(time.monotonic() - _PROCESS_START),
            pairs_tracked=pair_count,
        )

    return on_reconcile


def _handle_sigterm(signum: int, frame: object) -> None:
    """`systemctl stop`/`restart` gửi SIGTERM, không phải SIGINT -- Python
    mặc định KHÔNG raise KeyboardInterrupt khi nhận SIGTERM, nên nếu không
    tự bắt ở đây, service bị kill thẳng, không có dòng log nào đánh dấu
    "dừng ở đây, vì sao" (lỗ hổng phát hiện 2026-09-21).

    CHỈ set `_SHUTDOWN`, KHÔNG log/I-O gì khác -- xác nhận THẬT 2026-09-22
    (test qua đúng systemd, marker file độc lập chứng minh handler có chạy)
    rằng gọi logging từ TRONG signal handler bị nuốt im lặng, dù handler
    chắc chắn có chạy. Việc log "service_stopping" chuyển hẳn sang
    run_forever()/main() -- code bình thường, không phải signal handler,
    nên gọi logging ở đó an toàn.
    """
    _ = signum, frame  # chữ ký bắt buộc của signal.signal(), không dùng tới.
    _SHUTDOWN.set()


def run_forever(config: dict) -> None:
    """Kết nối, bootstrap full-history, subscribe kênh backfill-event, xử lý
    vô hạn. Mỗi lần vào lại vòng `while True` (lần đầu hoặc sau khi mất kết
    nối Redis) đều bootstrap lại trước khi subscribe -- xem docstring module.

    `clear_all_signals()` (chốt 2026-09-24) chạy ĐÚNG 1 LẦN, chỉ ở lượt
    đầu tiên vào vòng lặp này của tiến trình (canh bằng `_CLEARED_THIS_
    PROCESS`) -- KHÔNG lặp lại ở các lượt sau (khi chỉ là reconnect Redis
    tạm thời, không phải process restart thật). Đặt TRƯỚC `bootstrap_all()`
    để lần bootstrap luôn ghi vào Redis sạch tinh, không kế thừa rác từ thế
    hệ dữ liệu trước (xem docstring clear_all_signals()).

    Kiểm tra `_SHUTDOWN` ở đầu mỗi vòng lặp kết nối VÀ truyền
    `should_stop=_SHUTDOWN.is_set` cho listen_for_backfill_events() để nó
    tự thoát êm mỗi ~1s -- đây là nơi DUY NHẤT log "service_stopping" (từ
    code bình thường, không phải signal handler, xem _handle_sigterm()).
    """
    global _CLEARED_THIS_PROCESS
    redis_config = config["redis"]
    backfill_cfg = redis_config["backfill_event"]
    past_signal_cfg = redis_config["past_signal"]
    past_signal_trend_cfg = redis_config["past_signal_trend"]

    while not _SHUTDOWN.is_set():
        clients: list[redis.Redis] = []
        try:
            # Pub/Sub không phân biệt theo DB đã SELECT -- db truyền vào đây
            # chỉ để nhất quán, không ảnh hưởng việc nhận message.
            event_client = create_client(redis_config, past_signal_cfg["db"])
            output_client = create_client(redis_config, past_signal_cfg["db"])
            trend_client = create_client(redis_config, past_signal_trend_cfg["db"])
            clients = [event_client, output_client, trend_client]
            for client in clients:
                client.ping()

            key_prefix = str(past_signal_cfg["key_prefix"])
            trend_key_prefix = str(past_signal_trend_cfg["key_prefix"])

            if not _CLEARED_THIS_PROCESS:
                cleared = clear_all_signals(output_client, key_prefix=key_prefix)
                cleared_trend = clear_all_signals(trend_client, key_prefix=trend_key_prefix)
                log_event(
                    LOGGER, logging.INFO, "signals_cleared",
                    keys_deleted=cleared, keys_deleted_trend=cleared_trend,
                )
                _CLEARED_THIS_PROCESS = True

            bootstrap_all(
                output_client, key_prefix=key_prefix,
                trend_client=trend_client, trend_key_prefix=trend_key_prefix,
            )
            if _SHUTDOWN.is_set():
                break

            on_update = _make_on_update(
                output_client,
                key_prefix=key_prefix,
                trend_client=trend_client,
                trend_key_prefix=trend_key_prefix,
            )

            log_event(LOGGER, logging.INFO, "subscribing", channel=backfill_cfg["channel"])
            listen_for_backfill_events(
                event_client,
                channel=str(backfill_cfg["channel"]),
                on_update=on_update,
                reconcile_interval_seconds=int(backfill_cfg["reconcile_interval_seconds"]),
                on_reconcile=_make_on_reconcile(),
                should_stop=_SHUTDOWN.is_set,
            )
        except redis.RedisError as exc:
            if _SHUTDOWN.is_set():
                break
            delay = float(redis_config.get("reconnect_delay_seconds", 3))
            log_event(
                LOGGER, logging.ERROR, "redis_disconnected", result="fail",
                error=str(exc), retry_in_seconds=delay,
            )
            time.sleep(delay)
        finally:
            for client in clients:
                client.close()

    if _SHUTDOWN.is_set():
        log_event(LOGGER, logging.INFO, "service_stopping", signal="SIGTERM")


def main() -> int:
    log_dir = configure_logging()
    signal.signal(signal.SIGTERM, _handle_sigterm)

    exit_code = 0
    try:
        config = load_config()
        redis_cfg = config["redis"]
        log_event(
            LOGGER, logging.INFO, "service_started",
            redis_host=redis_cfg.get("host"),
            redis_db=redis_cfg["past_signal"]["db"],
            key_prefix=redis_cfg["past_signal"]["key_prefix"],
            log_dir=str(log_dir),
        )
        run_forever(config)
    except KeyboardInterrupt:
        log_event(LOGGER, logging.INFO, "service_stopping", signal="SIGINT")
    except (KeyError, TypeError, ValueError) as exc:
        log_event(LOGGER, logging.CRITICAL, "fatal_config_error", result="fail", error=str(exc))
        exit_code = 1
    finally:
        log_event(
            LOGGER, logging.INFO, "service_stopped",
            uptime_seconds=round(time.monotonic() - _PROCESS_START),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
