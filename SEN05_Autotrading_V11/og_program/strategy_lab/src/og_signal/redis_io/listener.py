"""React to DP's backfill-completed trigger through Redis Pub/Sub.

DP publish 1 message JSON mỗi khi 1 đợt backfill ghi xong nến mới vào
SQL Server, lên kênh riêng ``redis.backfill_event.channel`` (mặc định
``dp:events:backfill`` -- KHÁC hẳn ``dp:events:candles`` mà order_gateway
dùng, 2 sự kiện khác nhau hoàn toàn: 1 bên là "giá trị Redis DB0 đổi", 1
bên là "SQL vừa backfill xong"). Đã xác nhận DP publish thật sự hoạt động
(log thật 2026-09-21/22, xem memory project_strategy_lab_redesign) -- và
publish cho 1 universe symbol/timeframe rộng hơn nhiều so với phạm vi
strategy_lab theo dõi, nên `worker.py` tự lọc lại trước khi tính (xem
worker._strategy_for_timeframe()).

Chốt 2026-09-23: theo yêu cầu strategy_lab gửi DP (xem memory
project_strategy_lab_redesign), payload nay có thêm 3 field TUỲ CHỌN --
``candle_count``/``from``/``to`` -- mô tả đúng phạm vi nến vừa mới/đổi
trong lần backfill này (``from``/``to`` dạng "YYYY-MM-DD HH:MM:SS"
UTC-naive, cùng quy ước ``live.py:_stamp()`` phía DP). Payload cũ (chỉ có
``symbol``/``timeframe``) VẪN hợp lệ -- DP fallback về dạng cũ khi
``delivered_candles`` rỗng phía họ, và bản thân module này không bắt buộc
2 field mới phải có mặt. `on_update` vẫn KHÔNG được tin nội dung message
theo nghĩa "không cần đọc lại SQL" -- strategy_lab luôn tính lại từ SQL
(nguồn thật), chỉ khác là giờ biết CHÍNH XÁC đoạn nào cần tính lại thay vì
phải tính lại toàn bộ lịch sử mỗi lần (xem worker.refresh_pair()).

Pub/Sub là fire-and-forget: mất kết nối ngắn là mất message không thể lấy
lại. ``on_reconcile``/``reconcile_interval_seconds`` từ 2026-09-22 được
``worker.py`` dùng làm nhịp heartbeat log định kỳ (KHÔNG phải quét lại dữ
liệu -- phạm vi "quét lại bao nhiêu cặp, bao lâu 1 lần" vẫn CHƯA chốt,
xem cảnh báo trong worker.py) -- chỉ chứng minh worker còn sống/đang lắng
nghe trong log, không tốn SQL/Redis gì thêm.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import redis

from strategy_lab.src.og_signal.redis_io.logging_setup import log_event

LOGGER = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def listen_for_backfill_events(
    client: redis.Redis,
    *,
    channel: str,
    on_update: Callable[[str, str, str | None, str | None], None],
    on_started: Callable[[], None] | None = None,
    reconcile_interval_seconds: int | None = None,
    on_reconcile: Callable[[], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Subscribe ``channel`` và gọi ``on_update(timeframe, symbol, date_from,
    date_to)`` mỗi lần có message hợp lệ. ``date_from``/``date_to`` là
    ``None`` khi message không mang field ``from``/``to`` (payload cũ, hoặc
    DP fallback) -- module này CHỈ parse, không tự quyết "vậy thì làm gì"
    (đó là việc của worker.py, xem docstring module + refresh_pair()).

    Sai định dạng thì log lại (WARNING, kèm payload thô để tra cứu) rồi bỏ
    qua message đó, không raise -- trước 2026-09-22, chỗ này nuốt im lặng
    mọi message lỗi, không ai biết đã có message hỏng (xem memory
    project_parse_audit_findings, phát hiện chưa vá từ 2026-09-21).

    ``should_stop``: kiểm tra mỗi vòng lặp (~1s/lần, nhờ timeout sẵn có của
    ``get_message()``) -- trả True thì thoát vòng lặp êm, không raise. Đây
    là cách graceful-shutdown ĐÚNG cho SIGTERM (worker.py đặt cờ trong
    signal handler rồi để vòng lặp chính tự kiểm tra) -- KHÔNG gọi log/I-O
    trực tiếp từ signal handler, vì đã xác nhận thật (2026-09-22) gọi
    logging từ trong handler bị nuốt im lặng khi handler chen ngang đúng
    lúc code chính đang giữ lock ghi log (rủi ro re-entrancy kinh điển của
    Python `logging`, không phải lỗi riêng của module này).
    """
    pubsub = client.pubsub()
    try:
        pubsub.subscribe(channel)

        # Xác nhận subscribe xong trước khi báo "đã sẵn sàng" để không lỡ
        # message tới ngay trong lúc đang subscribe.
        while True:
            message = pubsub.get_message(timeout=1)
            if message and message.get("type") == "subscribe":
                break

        log_event(LOGGER, logging.INFO, "subscribed", channel=channel)

        if on_started is not None:
            on_started()

        next_reconcile = (
            time.monotonic() + reconcile_interval_seconds
            if reconcile_interval_seconds
            else None
        )

        while True:
            if should_stop is not None and should_stop():
                return

            timeout = 1.0
            if next_reconcile is not None:
                timeout = max(0.0, min(next_reconcile - time.monotonic(), 1.0))

            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
            if message and message.get("type") == "message":
                raw = _text(message.get("data", ""))
                try:
                    event = json.loads(raw)
                    timeframe = str(event["timeframe"]).upper()
                    symbol = str(event["symbol"]).upper()
                except (TypeError, ValueError, KeyError) as exc:
                    event = None
                    log_event(
                        LOGGER, logging.WARNING, "trigger_message_invalid",
                        result="fail", channel=channel, error=str(exc), raw=raw[:200],
                    )
                if event is not None:
                    # from/to là tuỳ chọn -- rỗng/thiếu thì để None, worker.py
                    # tự rơi về chế độ tính lại toàn bộ (xem refresh_pair()).
                    # Không validate định dạng ở đây: db_connector.load_range()
                    # tự raise nếu chuỗi không parse được thành ngày, và lỗi đó
                    # đã được _safe_refresh_pair() phía worker bắt + log sẵn.
                    date_from = str(event["from"]) if event.get("from") else None
                    date_to = str(event["to"]) if event.get("to") else None
                    on_update(timeframe, symbol, date_from, date_to)

            if next_reconcile is not None and time.monotonic() >= next_reconcile:
                if on_reconcile is not None:
                    on_reconcile()
                next_reconcile = time.monotonic() + reconcile_interval_seconds
    finally:
        pubsub.close()
