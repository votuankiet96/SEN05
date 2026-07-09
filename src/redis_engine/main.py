"""
Điểm khởi động cho redis_engine: python -m redis_engine.main

Chạy song song 2 vòng (threading), dùng chung 1 SignalState + 1
DeliveryOutbox để dedup nhất quán:
    - candle_snapshot_consumer: trigger nhanh, block chờ Redis Streams.
    - safety_net_poller:        trigger an toàn, quét WATCHED định kỳ.

Cách chạy:
    python -m redis_engine.main            (chạy 24/7, Ctrl+C để dừng)
    python -m redis_engine.main --once     (1 vòng safety-net rồi thoát —
                                             dùng để test thủ công, không
                                             cần chờ candle_snapshot thật từ DP6)
"""

from __future__ import annotations

import argparse
import logging
import threading

from og_core.logging_setup import setup_logger
from redis_engine.delivery.outbox import DeliveryOutbox
from redis_engine.delivery.state import SignalState
from redis_engine.triggers import candle_snapshot_consumer, safety_net_poller

setup_logger("redis_engine.log")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the redis_engine realtime signal pipeline.")
    parser.add_argument("--once", action="store_true", help="Run a single safety-net-style pass and exit.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    state = SignalState()
    outbox = DeliveryOutbox()

    if args.once:
        published = safety_net_poller.run_once(state=state, outbox=outbox)
        logger.info("main: --once complete, published %d signal(s)", published)
        return 0

    stop_event = threading.Event()
    consumer_thread = threading.Thread(
        target=candle_snapshot_consumer.run,
        kwargs={"state": state, "outbox": outbox, "stop_event": stop_event},
        name="candle_snapshot_consumer",
        daemon=True,
    )
    poller_thread = threading.Thread(
        target=safety_net_poller.run,
        kwargs={"state": state, "outbox": outbox, "stop_event": stop_event},
        name="safety_net_poller",
        daemon=True,
    )
    consumer_thread.start()
    poller_thread.start()

    exit_code = 0
    try:
        while not stop_event.is_set():
            consumer_thread.join(timeout=1.0)
            poller_thread.join(timeout=1.0)
            if not consumer_thread.is_alive() or not poller_thread.is_alive():
                # Cả 2 thread đều tự retry lỗi tạm thời (xem
                # candle_snapshot_consumer.run()/safety_net_poller.run()) — nếu
                # 1 thread vẫn chết tới đây nghĩa là lỗi thật không tự phục
                # hồi được. Thoát khác 0 để supervisor (systemd) khởi động
                # lại toàn bộ tiến trình, thay vì âm thầm chạy thiếu 1 nhánh.
                logger.critical("main: a worker thread died unexpectedly, exiting for restart")
                exit_code = 1
                break
    except KeyboardInterrupt:
        logger.info("main: shutting down (Ctrl+C)")
    finally:
        stop_event.set()
        consumer_thread.join(timeout=5)
        poller_thread.join(timeout=5)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
