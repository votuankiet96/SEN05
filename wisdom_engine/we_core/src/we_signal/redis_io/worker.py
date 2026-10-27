"""Vòng lặp chính của luồng "backtest cấp 2": nghe trigger backfill từ DF
(qua redis_io/listener.py), với mỗi (symbol, timeframe) báo thay đổi VÀ khớp
đúng phạm vi cấu hình (symbol trong strategies.combo.symbols, timeframe
khớp _STRATEGY_TIMEFRAMES của ít nhất 1 chiến lược -- xem docstring
_make_on_update(); trigger ngoài phạm vi bị bỏ qua, chốt 2026-09-21 vì DF
backfill 1 universe symbol rộng hơn nhiều so với symbol operator cấu hình),
tính lại lịch sử signal cho đúng chiến lược khớp timeframe đó, publish lên
**2 DB song song** (chốt 2026-09-26, qua redis_io/publisher.py): DB0
"past_signal" (không lọc trend, `run_strategy()`) và DB1 "past_signal_trend"
(có lọc trend khung lớn, `run_strategy_with_trend_reference()`) -- cùng 1
lần tải entry_bars, cùng 1 trigger, chỉ khác client/key_prefix đích và việc
có tải thêm trend_bars hay không. 2 kênh dùng để đối chứng khi ctrader cli
simulation (VM-BO20) backtest cùng chiến lược có/không trend.

Chốt 2026-09-23 -- CHỈ TÍNH ĐÚNG PHẦN THAY ĐỔI, không phải toàn bộ lịch sử
mỗi lần trigger: DF nay publish kèm `candle_count`/`from`/`to` mô tả đúng
phạm vi nến vừa mới/đổi (xem listener.py; yêu cầu wisdom_engine tự gửi DF). Khi trigger
có đủ `from`/`to`, refresh_pair() chỉ load_range_with_warmup() + tính lại
đúng `[from, to]` rồi GHÉP vào phần DB0 hiện có (_merge_delta_frame() +
publisher.read_past_signals()) thay vì tính lại từ SIGNAL_START_DATE. Khi
thiếu (payload cũ, hoặc DF fallback vì delivered_candles rỗng phía họ) --
rơi về đúng hành vi full-recompute gốc, không đổi gì. 2 đường dùng chung 1
hàm refresh_pair(), khác nhau ở chỗ có date_from/date_to hay không -- xem
docstring hàm đó để biết chi tiết từng bước.

Bootstrap lúc start/reconnect (chốt 2026-09-21): mỗi lần vào lại vòng lặp
kết nối trong run_forever() -- chạy lần đầu HAY sau khi mất kết nối Redis
giữa chừng -- worker chạy lại cho toàn bộ pair trong _bootstrap_pairs()
TRƯỚC khi subscribe, không đợi trigger đầu tiên. Lý do:
Pub/Sub là fire-and-forget, bất kỳ khoảng thời gian nào worker không kết
nối (lần đầu chạy, hoặc rớt kết nối tạm thời) đều có thể làm mất trigger
thật, khiến DB0 "cũ" hơn SQL mà không ai biết. Bootstrap là lưới an toàn
bù đúng khoảng trống đó -- ghi đè idempotent (publish_past_signals() luôn
DELETE+RPUSH lại từ đầu), chạy lại nhiều lần không sao. Phạm vi
_bootstrap_pairs(): symbol lấy từ strategies.combo.symbols (we_config.yaml)
-- đúng tập symbol operator đã chủ động cấu hình để trade -- ghép với đúng
timeframe riêng của từng chiến lược (_STRATEGY_TIMEFRAMES: combo chỉ
H1-H4, ma_cross chỉ M10/M20/M30/M45 -- KHÔNG dùng chung 1 danh sách hợp
nhất giữa các chiến lược, xem chi tiết lý do ở _STRATEGY_TIMEFRAMES). Cố ý
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
chờn). Chấp nhận DB0 trống hoàn toàn trong lúc bootstrap lại (~3-5 phút,
88 pair) mỗi khi có restart thật -- dữ liệu backtest, không cần luôn sẵn
sàng tức thời.

Sau bootstrap, quay lại thuần trigger-driven -- mất 1 message Pub/Sub giữa
2 lần bootstrap chỉ mất đúng lần cập nhật đó, chờ lần backfill kế tiếp mới
có trigger mới, hoặc chờ lần bootstrap kế (worker restart/reconnect) tự bù
lại. CHƯA có reconcile định kỳ tách riêng ngoài bootstrap này (khác bản
order_gateway/og_signal cũ vốn quét lại DB0 của DF mỗi 1800s) -- nếu Redis chập
chờn liên tục, bootstrap có thể chạy lặp lại nhiều lần liên tiếp, mỗi lần
quét hết _bootstrap_pairs() -- chấp nhận đánh đổi này thay vì thêm throttle
phức tạp chưa ai yêu cầu.

Xem CLAUDE.md mục 5 và 7 cho toàn bộ bối cảnh thiết kế.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple

import pandas as pd
import redis
from wisdom_engine.we_core.src.configuration import (
    COMBO_RECOMMENDED_TIMEFRAMES,
    MA_CROSS_SUPPORTED_TIMEFRAMES,
    MACD_BREAK_SUPPORTED_TIMEFRAMES,
    OPERATING_SYMBOLS,
    SIGNAL_START_DATE,
    STRATEGIES,
    WARMUP_BARS,
    check_trend_timeframe,
    get_strategy,
    price_decimals,
    run_strategy,
    run_strategy_with_trend_reference,
    trim_to_requested_range,
)
from wisdom_engine.we_core.src.db_connector import load_range_with_warmup
from wisdom_engine.we_core.src.rules.trend_tf import max_trend_warmup_bars
from wisdom_engine.we_core.src.we_signal.export_cli import signal_frame
from wisdom_engine.we_core.src.we_signal.redis_io.client import (
    create_client,
    load_config,
)
from wisdom_engine.we_core.src.we_signal.redis_io.listener import (
    listen_for_backfill_events,
)
from wisdom_engine.we_core.src.we_signal.redis_io.logging_setup import (
    configure_logging,
    log_event,
)
from wisdom_engine.we_core.src.we_signal.redis_io.publisher import (
    clear_all_signals,
    clear_pair_signals,
    publish_past_signals,
    read_past_signals,
)

# CỐ Ý dùng tên cố định, KHÔNG dùng logging.getLogger(__name__) -- đây là
# file entry point chạy qua `python -m wisdom_engine.we_core.src.we_signal.redis_io.
# worker`, lúc đó Python tự gán __name__ = "__main__" cho chính file này,
# khiến logger bị đặt tên "__main__" thay vì "...redis_io.worker" -- không
# còn là hậu duệ của logger gốc "wisdom_engine" (xem configure_logging()),
# không thừa kế handler nào, MỌI log của worker.py biến mất im lặng dù
# code chạy đúng (xác nhận THẬT 2026-09-22, sau nhiều giờ tưởng nhầm là
# lỗi signal/SIGTERM). client.py/listener.py/publisher.py không dính lỗi
# này vì chúng luôn được import bình thường (giữ đúng __name__ dạng
# module), chỉ file được gọi trực tiếp qua `-m` mới bị.
LOGGER = logging.getLogger("wisdom_engine.we_core.src.we_signal.redis_io.worker")

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

# Timeframe riêng của từng chiến lược mà worker chạy -- combo: khuyến nghị (H1-H4,
# không phải giới hạn cứng nhưng đây là phạm vi operator thật sự muốn theo dõi ở
# luồng Redis DB0); ma_cross: giới hạn cứng (M10/M20/M30/M45); macd_break:
# giới hạn cứng (M10-M45 + H1-H4, chốt 2026-10-06). Các tập này CÓ THỂ giao nhau
# (macd_break chồng cả combo lẫn ma_cross) -- 1 timeframe có thể chạy nhiều chiến
# lược, mỗi chiến lược ghi LIST riêng (key có STRATEGY) nên không đè nhau. Chốt
# 2026-09-21 vẫn giữ: mỗi chiến lược chỉ chạy đúng tập timeframe của chính nó,
# không gộp thành 1 danh sách hợp nhất để combo (vốn không bị chặn cứng) lấn
# sang M10-M45.
_STRATEGY_TIMEFRAMES: dict[str, tuple[str, ...]] = {
    "combo": COMBO_RECOMMENDED_TIMEFRAMES,
    "ma_cross": MA_CROSS_SUPPORTED_TIMEFRAMES,
    "macd_break": MACD_BREAK_SUPPORTED_TIMEFRAMES,
}


def _strategies_for_timeframe(timeframe: str) -> list[str]:
    """Các chiến lược coi timeframe này là của mình (theo thứ tự khai báo ở
    _STRATEGY_TIMEFRAMES); rỗng nếu không chiến lược nào nhận.
    """
    return [key for key, timeframes in _STRATEGY_TIMEFRAMES.items() if timeframe in timeframes]


class TrendOutput(NamedTuple):
    """Một đầu ra có lọc trend THÊM vào DB1 (``redis.extra_trend_outputs``): một DB riêng + khối trend riêng.

    ``trend_methods`` đè ``strategies.<tên>.trend_methods`` chỉ cho đầu ra này (DB0 và DB1 không đổi); ``label`` là tên
    hiển thị trong log, vd ``db2:breakout``.
    """

    client: redis.Redis
    key_prefix: str
    trend_methods: tuple[str, ...]
    label: str


def _make_extra_outputs(redis_config: Mapping) -> list[TrendOutput]:
    """Tạo client + mô tả cho từng phần tử đã chuẩn hoá của ``redis.extra_trend_outputs`` (rỗng nếu không khai)."""
    return [
        TrendOutput(
            client=create_client(dict(redis_config), item["db"]),
            key_prefix=str(item["key_prefix"]),
            trend_methods=tuple(item["trend_methods"]),
            label=f"db{item['db']}:{'+'.join(item['trend_methods'])}",
        )
        for item in redis_config.get("extra_trend_outputs") or []
    ]


def check_extra_trend_outputs(redis_config: Mapping) -> None:
    """Kiểm tra TRƯỚC khi worker chạm Redis (cùng lý do với check_trend_timeframes): với MỌI (chiến lược, symbol, khung
    vào lệnh) worker chạy, khối trend khai cho từng đầu ra trong ``redis.extra_trend_outputs`` phải ra được khung trend
    hợp lệ (khung >= khung vào lệnh, quy tắc 2026-10-16; khối cần khung tham chiếu riêng như knn phải có ô trong
    ``trend_tf_by_symbol``). Sai thì dừng ngay với MỘT thông báo liệt kê mọi tổ hợp sai (ValueError -> fatal_config_error)
    khi Redis còn nguyên, thay vì xoá sạch rồi báo lỗi ở từng cặp.
    """
    problems: dict[tuple[str, str, str], list[str]] = {}
    for item in redis_config.get("extra_trend_outputs") or []:
        methods = ",".join(item["trend_methods"])
        output = f"db{item['db']}:{methods}"
        for strategy_key, timeframes in _STRATEGY_TIMEFRAMES.items():
            spec = get_strategy(strategy_key)
            for timeframe in timeframes:
                for symbol in sorted(OPERATING_SYMBOLS):
                    try:
                        trend_tf = str(spec.normalize_params({"TREND_METHODS": methods}, symbol, timeframe)["TREND_TF"])
                        if not trend_tf:
                            raise ValueError("no trend timeframe configured")
                        check_trend_timeframe(timeframe, trend_tf)
                    except ValueError as exc:
                        problems.setdefault((output, f"{strategy_key} {timeframe}", str(exc)), []).append(symbol)
    if problems:
        details = [f"{output} {where}: {message} [{', '.join(symbols)}]" for (output, where, message), symbols in problems.items()]
        raise ValueError("redis.extra_trend_outputs cannot run with the current strategies config — " + "; ".join(details))


def check_trend_timeframes() -> None:
    """Kiểm tra cấu hình TRƯỚC khi worker chạm vào Redis: khung trend của MỌI (chiến lược, symbol, khung vào lệnh)
    mà worker chạy phải >= khung vào lệnh (configuration.check_trend_timeframe, quy tắc chốt 2026-10-16).
    Khung trend là ô khai trong `trend_tf_by_symbol` (bắt buộc đủ ô, kiểm lúc nạp config; không có khung mặc định chung).

    DB1 luôn lọc trend nên một cấu hình sai sẽ làm refresh_pair() báo lỗi ở TỪNG cặp
    liên quan và DB1 của các cặp đó trống, trong khi clear_all_signals() đã xoá sạch dữ
    liệu cũ. Gọi hàm này ở đầu main(), trước clear_all_signals(): sai thì dừng ngay với
    một thông báo liệt kê mọi tổ hợp sai (ValueError -> fatal_config_error), Redis còn nguyên.
    """
    wrong: dict[tuple[str, str, str], list[str]] = {}
    for strategy_key, timeframes in _STRATEGY_TIMEFRAMES.items():
        spec = get_strategy(strategy_key)
        for timeframe in timeframes:
            for symbol in sorted(OPERATING_SYMBOLS):
                trend_tf = str(spec.normalize_params(None, symbol, timeframe)["TREND_TF"])
                try:
                    check_trend_timeframe(timeframe, trend_tf)
                except ValueError as exc:
                    wrong.setdefault((strategy_key, timeframe, str(exc)), []).append(symbol)
    if wrong:
        details = [f"{strategy_key} {timeframe}: {message} [{', '.join(symbols)}]" for (strategy_key, timeframe, message), symbols in wrong.items()]
        raise ValueError("trend timeframe in we_config.yaml (trend_tf_by_symbol) is smaller than the entry timeframe — " + "; ".join(details))


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
    window_to]) vào phần DB0 hiện có của pair này -- giữ nguyên mọi dòng
    NGOÀI cửa sổ, THAY HẲN mọi dòng TRONG cửa sổ bằng delta_frame (kể cả
    khi delta_frame rỗng: nghĩa là sau khi DF sửa nến, các bar trong cửa sổ
    đó không còn thoả điều kiện signal nào nữa -- phải biến mất khỏi DB0,
    không được giữ lại signal cũ đã sai).

    So sánh biên bằng bartime thật (parse từ chuỗi "YYYY-MM-DD HH:MM" mà
    signal_frame()/publish_past_signals() đang dùng thống nhất), không phải
    so chuỗi. window_to dùng so sánh <= (không phải <) vì delta_frame chính
    là kết quả SQL đã lọc đúng đến window_to -- 1 dòng cũ trên DB0 đúng bằng
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
    # ngay sau khi đổi.
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
    cho cả nhánh DB0 (không trend, `enriched` từ run_strategy()) lẫn DB1
    (có trend, `enriched` từ run_strategy_with_trend_reference()) trong
    refresh_pair() -- 2 nhánh chỉ khác `client`/`key_prefix`/`enriched`
    truyền vào, phần còn lại (trim/frame/merge/publish) giống hệt nhau nên
    gom về đúng 1 chỗ thay vì lặp lại 2 lần trong refresh_pair().
    """
    trimmed = trim_to_requested_range(enriched, effective_from)
    frame = signal_frame(trimmed, strategy=strategy, decimals=price_decimals(symbol))
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
    extra_outputs: Sequence[TrendOutput] = (),
) -> int:
    """Tính lại signal của (symbol, timeframe) cho các chiến lược trong
    `strategy_keys`, publish từng chiến lược lên **2 DB song song** (chốt
    2026-09-26): DB0 (`client`/`key_prefix`, không lọc trend) và DB1
    (`trend_client`/`trend_key_prefix`, có lọc trend khung lớn) -- cùng 1
    lần tải entry_bars (`raw` bên dưới), DB1 chỉ tải thêm đúng 1 nguồn phụ
    (trend_bars, khung `TREND_TF` tra theo (symbol, khung vào lệnh) trong `trend_tf_by_symbol`).

    `extra_outputs` (tuỳ chọn, ``redis.extra_trend_outputs``, 2026-10-23): mỗi phần tử là một đầu ra có lọc trend THÊM,
    cùng entry_bars và cùng trigger, ghi vào DB riêng với khối trend riêng của nó (đè `trend_methods` của chiến lược);
    khối cùng khung với tín hiệu (vd breakout) dùng lại chính `raw`, không tải nến trend. Rỗng (mặc định) = đúng như
    trước khi có tham số này: DB0/DB1 không đổi một byte.

    Nến trend (DB1 và đầu ra thêm) tải với warm-up `max(WARMUP_BARS, rules/trend_tf.max_trend_warmup_bars(khối))`: khối có
    máy trạng thái nhớ lâu (zigzag: 3.000 nến, POLICY.trend_warmup_bars) cần nhiều nến trước mốc tính lại hơn warm-up chung
    thì đường delta mới khớp bản tính từ đầu; khối cùng khung chỉ dùng lại `raw` khi warm-up của nó không dài hơn `raw` đã có.

    2 chế độ, chọn theo việc có đủ `date_from`+`date_to` hay không:

    - ĐỦ CẢ HAI (chế độ "delta", chốt 2026-09-23 -- trigger từ DF kèm đúng
      phạm vi nến vừa mới/đổi, xem listener.py): chỉ
      load_range_with_warmup(date_from, date_to) + tính signal đúng đoạn
      đó, rồi GHÉP vào phần DB hiện có qua _merge_delta_frame() trước khi
      publish. `date_from` được kẹp không sớm hơn SIGNAL_START_DATE --
      SIGNAL_START_DATE là biên cứng của toàn bộ luồng DB0/DB1, DF có thể
      báo thay đổi ở nến còn cũ hơn cả biên đó (backfill full history),
      phần đó nằm ngoài phạm vi wisdom_engine nên bỏ qua, không phải lỗi.
    - THIẾU 1 TRONG 2 (chế độ "full" -- payload cũ, DF fallback vì
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

    DB1 luôn lọc trend bất kể `strategies.*.trend_filter_enabled` trong
    we_config.yaml đang true/false -- run_strategy_with_trend_reference() tự
    ép TREND_FILTER_ENABLED=True bên trong, cờ config đó chỉ ảnh hưởng
    DB0/dashboard/live, không ảnh hưởng DB1.

    Trả về tổng số dòng đã publish (cộng dồn cả DB0 lẫn DB1, mọi chiến lược
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
        # smaller than SLOW_MA", "TREND_METHODS has unknown trend method(s)", "SESSION_HOURS_UTC
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

        # DB0 -- không lọc trend, hành vi không đổi so với trước 2026-09-26.
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

        # DB1 -- có lọc trend (chốt 2026-09-26). TREND_TF tra trong we_config.yaml
        # theo (symbol, khung vào lệnh) ở trend_tf_by_symbol (qua normalize_params),
        # không hardcode khung ở đây -- entry_bars (`raw`) dùng lại nguyên vẹn,
        # chỉ tải thêm trend_bars.
        started_at = time.monotonic()
        trend_params = spec.normalize_params(None, symbol, timeframe)
        trend_tf = str(trend_params["TREND_TF"])
        # Nến trend cần warm-up riêng của khối đang bật nếu dài hơn warm-up chung (rules/trend_tf POLICY.trend_warmup_bars).
        trend_warmup = max(WARMUP_BARS, max_trend_warmup_bars(trend_params["TREND_METHODS"]))
        trend_bars = load_range_with_warmup(symbol, trend_tf, effective_from, window_to, trend_warmup)
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

        # Các đầu ra trend THÊM: khối trend riêng của từng đầu ra, cùng entry_bars `raw`.
        for extra in extra_outputs:
            started_at = time.monotonic()
            extra_methods = ",".join(extra.trend_methods)
            extra_trend_tf = str(spec.normalize_params({"TREND_METHODS": extra_methods}, symbol, timeframe)["TREND_TF"])
            extra_warmup = max(WARMUP_BARS, max_trend_warmup_bars(extra.trend_methods))
            # Dùng lại `raw` chỉ khi cùng khung VÀ khối không đòi warm-up dài hơn cái `raw` đã có.
            extra_trend_bars = (
                raw if extra_trend_tf == timeframe and extra_warmup <= WARMUP_BARS
                else load_range_with_warmup(symbol, extra_trend_tf, effective_from, window_to, extra_warmup)
            )
            extra_enriched = run_strategy_with_trend_reference(
                strategy_key, symbol=symbol, entry_tf=timeframe, entry_bars=raw,
                trend_tf=extra_trend_tf, trend_bars=extra_trend_bars, overrides={"TREND_METHODS": extra_methods},
            )
            extra_count = _compute_and_publish(
                extra.client, key_prefix=extra.key_prefix, symbol=symbol, timeframe=timeframe,
                strategy=strategy_key, enriched=extra_enriched, effective_from=effective_from,
                delta_mode=delta_mode, window_to=window_to,
            )
            duration_ms = round((time.monotonic() - started_at) * 1000)
            log_event(
                LOGGER, logging.INFO, "pair_refreshed", db=extra.label,
                strategy=strategy_key, symbol=symbol, timeframe=timeframe, trend_tf=extra_trend_tf,
                rows=extra_count, duration_ms=duration_ms, mode="delta" if delta_mode else "full",
            )
            total += extra_count
    return total


def _bootstrap_pairs() -> list[tuple[str, str, str]]:
    """Danh sách (strategy, symbol, timeframe) cần full-history lúc bootstrap.

    symbol: OPERATING_SYMBOLS = danh sách strategies.combo.symbols trong we_config.yaml.
    timeframe: đúng timeframe riêng của từng chiến lược (_STRATEGY_TIMEFRAMES)
    -- combo chỉ ghép H1-H4, ma_cross chỉ ghép M10/M20/M30/M45, không còn
    dùng chung 1 danh sách hợp nhất giữa 2 chiến lược nữa.
    """
    symbols = sorted(OPERATING_SYMBOLS)
    return [
        (strategy_key, symbol, tf)
        for strategy_key, timeframes in _STRATEGY_TIMEFRAMES.items()
        for symbol in symbols
        for tf in timeframes
    ]


# Restart có chọn phạm vi (2026-10-20): chọn chiến lược / symbol / khung vào lệnh bằng tham số dòng lệnh hoặc biến môi trường
# (tham số thắng biến môi trường). Không chọn gì = restart FULL như cũ (xoá sạch DB0 + DB1 của ta, bootstrap mọi cặp).
SCOPE_ENV_VARS = {"strategy": "WE_SCOPE_STRATEGY", "symbol": "WE_SCOPE_SYMBOL", "timeframe": "WE_SCOPE_TF"}


def _split_values(raw: str | None) -> list[str]:
    """"a, b;c d" -> ["a", "b", "c", "d"] (phân cách bằng dấu phẩy, chấm phẩy hoặc khoảng trắng)."""
    return [part for part in str(raw or "").replace(";", ",").replace(" ", ",").split(",") if part]


def select_scope_pairs(
    strategies: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    timeframes: Iterable[str] | None = None,
) -> list[tuple[str, str, str]] | None:
    """Phạm vi restart: các cặp (chiến lược, symbol, khung) nằm trong tập đã chọn; None = không chọn gì (FULL).

    Mỗi tập bỏ trống nghĩa là "tất cả" ở chiều đó; không phân biệt hoa thường. Giá trị lạ (chiến lược/symbol/
    khung worker không chạy) hoặc tổ hợp không ra cặp nào là ValueError, báo trước khi chạm vào Redis.
    """
    wanted_strategies = {value.strip().lower() for value in strategies or () if value.strip()}
    wanted_symbols = {value.strip().upper() for value in symbols or () if value.strip()}
    wanted_timeframes = {value.strip().upper() for value in timeframes or () if value.strip()}
    if not (wanted_strategies or wanted_symbols or wanted_timeframes):
        return None
    valid_timeframes = {tf for timeframes_of in _STRATEGY_TIMEFRAMES.values() for tf in timeframes_of}
    problems = [
        f"unknown {kind} {value!r} (valid: {', '.join(sorted(valid))})"
        for kind, wanted, valid in (
            ("strategy", wanted_strategies, set(_STRATEGY_TIMEFRAMES)),
            ("symbol", wanted_symbols, set(OPERATING_SYMBOLS)),
            ("timeframe", wanted_timeframes, valid_timeframes),
        )
        for value in sorted(wanted - valid)
    ]
    if problems:
        raise ValueError("restart scope: " + "; ".join(problems))
    pairs = [
        (strategy_key, symbol, timeframe)
        for strategy_key, symbol, timeframe in _bootstrap_pairs()
        if (not wanted_strategies or strategy_key in wanted_strategies)
        and (not wanted_symbols or symbol in wanted_symbols)
        and (not wanted_timeframes or timeframe in wanted_timeframes)
    ]
    if not pairs:
        raise ValueError(
            "restart scope selects no pair (strategy="
            f"{','.join(sorted(wanted_strategies)) or '*'} symbol={','.join(sorted(wanted_symbols)) or '*'} "
            f"tf={','.join(sorted(wanted_timeframes)) or '*'}): that strategy does not run on that timeframe."
        )
    return pairs


def parse_scope(argv: Sequence[str], environ: Mapping[str, str]) -> list[tuple[str, str, str]] | None:
    """Đọc --strategy/--symbol/--tf (mặc định lấy từ WE_SCOPE_STRATEGY/WE_SCOPE_SYMBOL/WE_SCOPE_TF) -> select_scope_pairs."""
    parser = argparse.ArgumentParser(
        prog="worker",
        description="Past-signal worker. Không đặt phạm vi = restart FULL (xoá DB0+DB1 của ta, bootstrap mọi cặp); "
        "đặt phạm vi = chỉ xoá DB0+DB1 và tính lại các cặp được chọn, giữ nguyên cặp còn lại.",
    )
    parser.add_argument("--strategy", default=environ.get(SCOPE_ENV_VARS["strategy"]), help="combo,ma_cross,macd_break (cách nhau bằng dấu phẩy)")
    parser.add_argument("--symbol", default=environ.get(SCOPE_ENV_VARS["symbol"]), help="vd EURUSD,USDJPY")
    parser.add_argument("--tf", default=environ.get(SCOPE_ENV_VARS["timeframe"]), help="vd H1,H2")
    args = parser.parse_args(list(argv))
    return select_scope_pairs(_split_values(args.strategy), _split_values(args.symbol), _split_values(args.tf))


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
    extra_outputs: Sequence[TrendOutput] = (),
) -> None:
    """refresh_pair() với biên chịu lỗi dùng chung cho cả trigger lẫn
    bootstrap: RedisError propagate ra ngoài (run_forever() tự reconnect),
    lỗi khác chỉ log rồi bỏ qua đúng 1 pair -- không để 1 pair lỗi chặn cả
    vòng lặp subscribe hoặc cả lượt bootstrap còn lại. Lỗi ở nhánh DB1 (vd
    SQL trend_bars lỗi) cũng rơi vào biên chịu lỗi này giống DB0 -- không
    tách riêng, vì refresh_pair() tính cả 2 DB trong cùng 1 lời gọi.

    date_from/date_to: truyền thẳng xuống refresh_pair() -- bootstrap_all()
    không truyền gì (None, None -- luôn full-recompute, đúng ý "bootstrap
    là lưới an toàn phủ hết", xem docstring bootstrap_all()), chỉ
    _make_on_update() mới có thể truyền giá trị thật từ trigger DF.
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
            extra_outputs=extra_outputs,
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
    pairs: list[tuple[str, str, str]] | None = None,
    extra_outputs: Sequence[TrendOutput] = (),
) -> None:
    """Chạy refresh_pair() (từ SIGNAL_START_DATE) cho toàn bộ pair trong
    _bootstrap_pairs() (hoặc đúng `pairs` nếu truyền vào -- restart có chọn phạm vi) -- mỗi phần tử là 1 bộ (chiến lược, symbol, timeframe),
    chỉ tính đúng chiến lược đó (_STRATEGY_TIMEFRAMES). Gọi lúc worker
    mới start hoặc vừa reconnect Redis, TRƯỚC khi subscribe, để DB0/DB1
    không bao giờ "cũ" hơn SQL vì lỡ mất trigger Pub/Sub trong khoảng không
    kết nối (xem docstring module).

    Kiểm tra `_SHUTDOWN` giữa mỗi pair -- bootstrap hàng trăm pair có thể mất tới
    vài trăm giây (tăng so với bản chỉ DB0 vì mỗi pair giờ tính thêm DB1),
    nếu không dừng giữa chừng khi SIGTERM tới thì graceful shutdown sẽ bị
    trễ cả phút (xem docstring `_SHUTDOWN`).
    """
    scoped = pairs is not None
    pairs = _bootstrap_pairs() if pairs is None else pairs
    per_strategy = Counter(strategy_key for strategy_key, _, _ in pairs)
    # Cờ lọc lặp cùng chiều của từng chiến lược có cờ này: đổi cờ làm đổi nội dung
    # DB0/DB1 trong khi tên key không đổi, nên ghi vào log để tra được dữ liệu
    # hiện tại được tạo với cấu hình nào.
    alternating = {
        f"alternating_{key}": get_strategy(key).default_params["ALTERNATING_FILTER_ENABLED"]
        for key in per_strategy
        if "ALTERNATING_FILTER_ENABLED" in get_strategy(key).default_params
    }
    # Các khối xác nhận xu hướng (đường DB1) của từng chiến lược: đổi danh sách khối
    # cũng làm đổi nội dung DB1 mà tên key không đổi, nên ghi vào log như cờ ở trên.
    trend_methods = {
        f"trend_methods_{key}": "+".join(get_strategy(key).default_params["TREND_METHODS"])
        for key in per_strategy
        if "TREND_METHODS" in get_strategy(key).default_params
    }
    log_event(
        LOGGER, logging.INFO, "bootstrap_start",
        pair_count=len(pairs), scoped=scoped, **{f"{key}_pairs": count for key, count in per_strategy.items()},
        **alternating, **trend_methods,
        **({"extra_outputs": ",".join(extra.label for extra in extra_outputs)} if extra_outputs else {}),
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
            extra_outputs=extra_outputs,
        )
    duration_ms = round((time.monotonic() - started_at) * 1000)
    log_event(LOGGER, logging.INFO, "bootstrap_done", pair_count=len(pairs), duration_ms=duration_ms)


def _make_on_update(
    output_client: redis.Redis,
    *,
    key_prefix: str,
    trend_client: redis.Redis,
    trend_key_prefix: str,
    extra_outputs: Sequence[TrendOutput] = (),
) -> Callable[[str, str, str | None, str | None], None]:
    """Trigger thật từ DF không tự giới hạn theo symbol/timeframe nào --
    DF backfill một universe rộng hơn nhiều so với tập symbol operator cấu
    hình (OPERATING_SYMBOLS = strategies.combo.symbols),
    nên nếu xử lý mù quáng mọi trigger sẽ ghi vào DB0/DB1 cả những symbol
    ngoài phạm vi (combo còn tính sai entry_price cho symbol không có cách
    tính entry nào -- X rơi về fallback 0.0, xem _combo_symbol_params()).
    Chốt 2026-09-21: chỉ xử lý trigger có symbol nằm trong tập vận hành
    (mọi chiến lược dùng chung tập symbol này, kể cả macd_break dù nó không
    có X riêng) VÀ timeframe khớp _STRATEGY_TIMEFRAMES --
    chỉ tính/ghi cho đúng các chiến lược nhận timeframe đó (có thể nhiều hơn
    1, SQL chỉ tải 1 lần dùng chung), trigger ngoài phạm vi bị bỏ qua có log.

    date_from/date_to (chốt 2026-09-23): truyền thẳng từ message DF xuống
    refresh_pair() qua _safe_refresh_pair() -- None cả hai thì tự rơi về
    full-recompute (payload cũ hoặc DF fallback), không cần biết trước ở
    đây là chế độ nào.
    """
    allowed_symbols = set(OPERATING_SYMBOLS)

    def on_update(timeframe: str, symbol: str, date_from: str | None, date_to: str | None) -> None:
        strategy_keys = _strategies_for_timeframe(timeframe)
        if not strategy_keys or symbol not in allowed_symbols:
            log_event(
                LOGGER, logging.DEBUG, "trigger_skipped", result="skip",
                symbol=symbol, timeframe=timeframe, reason="out_of_scope",
            )
            return
        log_event(
            LOGGER, logging.INFO, "trigger_accepted",
            symbol=symbol, timeframe=timeframe, strategy=",".join(strategy_keys),
            mode="delta" if (date_from and date_to) else "full",
        )
        _safe_refresh_pair(
            output_client,
            symbol=symbol,
            timeframe=timeframe,
            key_prefix=key_prefix,
            trend_client=trend_client,
            trend_key_prefix=trend_key_prefix,
            strategy_keys=strategy_keys,
            date_from=date_from,
            date_to=date_to,
            extra_outputs=extra_outputs,
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


def run_forever(config: dict, scope_pairs: list[tuple[str, str, str]] | None = None) -> None:
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

    `scope_pairs` (2026-10-20, restart có chọn phạm vi; từ select_scope_pairs()): None = restart FULL như
    cũ. Khác None = lượt khởi động đầu chỉ xoá (DB0 + DB1) và bootstrap đúng các cặp này, cặp còn lại giữ
    nguyên. Phạm vi chỉ áp dụng cho lượt đầu: khi Redis rớt rồi nối lại, bootstrap tính lại MỌI cặp như cũ
    (nếu Redis đã mất dữ liệu thì cặp ngoài phạm vi cũng được nạp lại).
    """
    global _CLEARED_THIS_PROCESS
    pending_scope = scope_pairs
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
            extra_outputs = _make_extra_outputs(redis_config)
            clients = [event_client, output_client, trend_client, *(extra.client for extra in extra_outputs)]
            for client in clients:
                client.ping()

            key_prefix = str(past_signal_cfg["key_prefix"])
            trend_key_prefix = str(past_signal_trend_cfg["key_prefix"])

            if not _CLEARED_THIS_PROCESS:
                if pending_scope is None:
                    cleared = clear_all_signals(output_client, key_prefix=key_prefix)
                    cleared_trend = clear_all_signals(trend_client, key_prefix=trend_key_prefix)
                    log_event(
                        LOGGER, logging.INFO, "signals_cleared",
                        keys_deleted=cleared, keys_deleted_trend=cleared_trend,
                    )
                    for extra in extra_outputs:
                        log_event(
                            LOGGER, logging.INFO, "signals_cleared_extra", output=extra.label,
                            keys_deleted=clear_all_signals(extra.client, key_prefix=extra.key_prefix),
                        )
                else:
                    cleared = cleared_trend = 0
                    for strategy_key, symbol, timeframe in pending_scope:
                        cleared += clear_pair_signals(
                            output_client, key_prefix=key_prefix, symbol=symbol, timeframe=timeframe, strategy=strategy_key,
                        )
                        cleared_trend += clear_pair_signals(
                            trend_client, key_prefix=trend_key_prefix, symbol=symbol, timeframe=timeframe, strategy=strategy_key,
                        )
                    log_event(
                        LOGGER, logging.INFO, "signals_cleared_scoped",
                        pair_count=len(pending_scope), keys_deleted=cleared, keys_deleted_trend=cleared_trend,
                    )
                    for extra in extra_outputs:
                        log_event(
                            LOGGER, logging.INFO, "signals_cleared_extra", output=extra.label, scoped=True,
                            pair_count=len(pending_scope),
                            keys_deleted=sum(
                                clear_pair_signals(
                                    extra.client, key_prefix=extra.key_prefix, symbol=symbol, timeframe=timeframe, strategy=strategy_key,
                                )
                                for strategy_key, symbol, timeframe in pending_scope
                            ),
                        )
                _CLEARED_THIS_PROCESS = True

            bootstrap_all(
                output_client, key_prefix=key_prefix,
                trend_client=trend_client, trend_key_prefix=trend_key_prefix,
                pairs=pending_scope, extra_outputs=extra_outputs,
            )
            pending_scope = None   # từ lần nối lại Redis sau đó: tính lại mọi cặp như cũ
            if _SHUTDOWN.is_set():
                break

            on_update = _make_on_update(
                output_client,
                key_prefix=key_prefix,
                trend_client=trend_client,
                trend_key_prefix=trend_key_prefix,
                extra_outputs=extra_outputs,
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


def main(argv: Sequence[str] = ()) -> int:
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
        check_trend_timeframes()  # trước run_forever(): cấu hình sai thì dừng khi Redis còn nguyên
        check_extra_trend_outputs(redis_cfg)  # cùng lý do: đầu ra trend thêm khai sai thì dừng trước khi xoá gì
        scope_pairs = parse_scope(argv, os.environ)   # phạm vi sai cũng dừng ở đây, trước khi chạm Redis
        if scope_pairs is None:
            run_forever(config)
        else:
            log_event(
                LOGGER, logging.INFO, "restart_scope", pair_count=len(scope_pairs),
                strategies=",".join(sorted({strategy for strategy, _, _ in scope_pairs})),
                symbols=",".join(sorted({symbol for _, symbol, _ in scope_pairs})),
                timeframes=",".join(sorted({timeframe for _, _, timeframe in scope_pairs})),
            )
            run_forever(config, scope_pairs)
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
    raise SystemExit(main(sys.argv[1:]))
