"""
Lưới an toàn: quét lại toàn bộ config.WATCHED theo chu kỳ cố định, không
phụ thuộc Streams còn sống. Bắt lại bar bị lọt khi candle_snapshot không
tới được OG (Redis chết hẳn, DP6 restart đúng lúc, network blip).

Dùng CHUNG delivery/state.py với candle_snapshot_consumer nên không publish
trùng: tín hiệu nào đã publish qua đường nhanh sẽ tự bị
state.check_and_mark() chặn lại ở đây. Mỗi vòng cũng thử retry outbox (tín
hiệu publish lỗi ở lần trước).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from redis_engine import compute, config
from redis_engine.delivery.outbox import DeliveryOutbox
from redis_engine.delivery.redis_client import publish_signal
from redis_engine.delivery.state import SignalState

logger = logging.getLogger(__name__)


def run_once(*, state: SignalState, outbox: DeliveryOutbox) -> int:
    """Quét toàn bộ WATCHED 1 lần, publish tín hiệu mới. Trả về số tín hiệu publish thành công."""
    published = 0
    for item in config.WATCHED:
        try:
            rows: list[dict[str, Any]] = compute.run_watched_item(item)
        except Exception:
            logger.exception("safety_net_poller: compute failed for %s", item)
            continue

        for row in rows:
            signal_id = row["signal_id"]
            if not state.check_and_mark(signal_id):
                continue
            result = publish_signal(row["strategy"], row)
            if result is None:
                outbox.add_pending(row["strategy"], row)
            else:
                published += 1

    retried = outbox.retry_all(publish_signal)
    if retried:
        logger.info("safety_net_poller: outbox delivered %d previously-failed signal(s)", retried)
    return published


def run(*, state: SignalState, outbox: DeliveryOutbox, stop_event: threading.Event | None = None) -> None:
    logger.info(
        "safety_net_poller: interval=%ss watched=%d item(s)",
        config.SAFETY_NET_INTERVAL_SECONDS,
        len(config.WATCHED),
    )
    while stop_event is None or not stop_event.is_set():
        try:
            n = run_once(state=state, outbox=outbox)
            if n:
                logger.info("safety_net_poller: published %d new signal(s)", n)
        except Exception:
            logger.exception("safety_net_poller: cycle failed")

        if stop_event is not None:
            if stop_event.wait(config.SAFETY_NET_INTERVAL_SECONDS):
                break
        else:
            time.sleep(config.SAFETY_NET_INTERVAL_SECONDS)
