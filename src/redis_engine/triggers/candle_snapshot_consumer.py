"""
Trigger nhanh: đọc stream "candle_snapshot" (DP6 XADD 1 entry mang sẵn 500
bar OHLCV mới nhất ngay khi ghi/sửa xong 1 bar trong DWH.Fact_OHLCV) qua
consumer group, parse bars trực tiếp thành DataFrame (candle_store.py),
tính tín hiệu cho đúng symbol/tf vừa nhận, publish tín hiệu mới. Không cần
query SQL/Redis thêm lần nào cho bước lấy dữ liệu — entry đã mang đủ.
Không poll — XREADGROUP BLOCK thức dậy gần tức thời khi có entry mới.

XACK ngay sau khi xử lý xong (kể cả khi publish lỗi) — lỗi publish đã được
delivery/outbox.py xử lý riêng bằng retry ở safety_net_poller, không cần
giữ lại entry candle_snapshot để retry qua Streams (quét lại DB qua safety
net rẻ hơn nhiều so với retry đọc lại 1 stream entry).

run() không bao giờ để thread chết vì lỗi tạm thời (Redis down lúc khởi
động, mất kết nối giữa chừng ở XACK, bug bất ngờ khi xử lý 1 entry) — mọi
lỗi đều được log rồi retry toàn bộ vòng đọc, giữ nhánh trigger nhanh luôn
sống thay vì âm thầm chỉ còn lưới an toàn (5 phút) hoạt động.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import redis

from redis_engine import compute, config
from redis_engine.delivery.outbox import DeliveryOutbox
from redis_engine.delivery.redis_client import ensure_consumer_group, get_client, publish_signal
from redis_engine.delivery.state import SignalState
from redis_engine.triggers.candle_store import MalformedSnapshotError, parse_snapshot_entry, snapshot_symbol_tf

logger = logging.getLogger(__name__)

BLOCK_MS = 5000
READ_COUNT = 50
RESTART_PAUSE_SEC = BLOCK_MS / 1000


def run(*, state: SignalState, outbox: DeliveryOutbox, stop_event: threading.Event | None = None) -> None:
    while stop_event is None or not stop_event.is_set():
        try:
            _run_loop(state=state, outbox=outbox, stop_event=stop_event)
        except Exception:
            logger.exception("candle_snapshot_consumer: fatal error, restarting read loop")
            if stop_event is not None:
                if stop_event.wait(RESTART_PAUSE_SEC):
                    break
            else:
                time.sleep(RESTART_PAUSE_SEC)


def _run_loop(*, state: SignalState, outbox: DeliveryOutbox, stop_event: threading.Event | None) -> None:
    ensure_consumer_group()
    client = get_client()
    logger.info(
        "candle_snapshot_consumer: listening stream=%s group=%s consumer=%s",
        config.CANDLE_SNAPSHOT_STREAM,
        config.CONSUMER_GROUP,
        config.CONSUMER_NAME,
    )
    while stop_event is None or not stop_event.is_set():
        try:
            entries = client.xreadgroup(
                config.CONSUMER_GROUP,
                config.CONSUMER_NAME,
                {config.CANDLE_SNAPSHOT_STREAM: ">"},
                count=READ_COUNT,
                block=BLOCK_MS,
            )
        except redis.RedisError as exc:
            logger.warning("candle_snapshot_consumer: XREADGROUP failed, retrying: %s", exc)
            if stop_event is not None:
                stop_event.wait(BLOCK_MS / 1000)
            continue

        if not entries:
            continue

        for _stream_name, messages in entries:
            for message_id, fields in messages:
                try:
                    _handle_snapshot(fields, state=state, outbox=outbox)
                except Exception:
                    logger.exception("candle_snapshot_consumer: unexpected error handling entry %s", message_id)
                try:
                    client.xack(config.CANDLE_SNAPSHOT_STREAM, config.CONSUMER_GROUP, message_id)
                except redis.RedisError as exc:
                    logger.warning(
                        "candle_snapshot_consumer: XACK failed for %s (entry stays pending): %s", message_id, exc
                    )


def _matching_items(symbol: str, tf: str) -> list[dict[str, Any]]:
    return [item for item in config.WATCHED if tf == item["tf"] and symbol in item["symbols"]]


def _handle_snapshot(fields: dict[str, str], *, state: SignalState, outbox: DeliveryOutbox) -> None:
    try:
        symbol, tf = snapshot_symbol_tf(fields)
    except MalformedSnapshotError:
        logger.warning("candle_snapshot_consumer: malformed entry, skipping: %s", fields)
        return

    items = _matching_items(symbol, tf)
    if not items:
        return

    try:
        raw = parse_snapshot_entry(fields)
    except MalformedSnapshotError:
        logger.warning("candle_snapshot_consumer: malformed bars for %s %s, skipping", symbol, tf)
        return

    for item in items:
        strategy = str(item["strategy"])
        try:
            rows = compute.run_from_bars(strategy, symbol, tf, raw)
        except Exception:
            logger.exception("candle_snapshot_consumer: compute failed for %s %s %s", strategy, symbol, tf)
            continue
        _publish_new(rows, state=state, outbox=outbox)


def _publish_new(rows: list[dict[str, Any]], *, state: SignalState, outbox: DeliveryOutbox) -> None:
    for row in rows:
        signal_id = row["signal_id"]
        if not state.check_and_mark(signal_id):
            continue
        result = publish_signal(row["strategy"], row)
        if result is None:
            outbox.add_pending(row["strategy"], row)
