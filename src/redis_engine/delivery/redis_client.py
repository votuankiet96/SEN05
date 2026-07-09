"""
Kết nối Redis dùng chung bởi candle_snapshot_consumer và safety_net_poller.

Nguyên tắc: publish_signal() không bao giờ raise ra ngoài — lỗi Redis được
nuốt, log warning, và trả None. Caller (watcher loop) tự quyết định ghi vào
outbox.py khi nhận None.
"""

from __future__ import annotations

import logging

import redis

from redis_engine import config

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    """Trả về 1 kết nối Redis dùng chung cho cả tiến trình (lazy singleton)."""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            username=config.REDIS_USERNAME,
            password=config.REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=5,
            # Phải LỚN HƠN rõ rệt BLOCK_MS (5000ms) của candle_snapshot_consumer
            # — nếu bằng hoặc nhỏ hơn, socket_timeout của client đua với chính
            # thời gian BLOCK phía server, gây "Timeout reading from socket"
            # giả (không phải mất kết nối thật) mỗi khi không có entry mới.
            socket_timeout=15,
        )
    return _client


def ensure_consumer_group() -> None:
    """
    Tạo consumer group cho stream candle_snapshot nếu chưa có (idempotent).

    id="$" nghĩa là consumer group chỉ thấy các entry XADD SAU thời điểm tạo
    group — đúng ý "chỉ quan tâm bar mới từ giờ trở đi", không đọc lại lịch
    sử stream cũ.
    """
    client = get_client()
    try:
        client.xgroup_create(config.CANDLE_SNAPSHOT_STREAM, config.CONSUMER_GROUP, id="$", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def publish_signal(strategy: str, payload: dict[str, object]) -> str | None:
    """
    XADD 1 tín hiệu vào signal_stream:{strategy}.

    Returns:
        Redis stream entry ID nếu thành công, None nếu lỗi (đã nuốt + log).
    """
    client = get_client()
    fields = {key: ("" if value is None else str(value)) for key, value in payload.items()}
    try:
        return client.xadd(
            config.signal_stream_key(strategy),
            fields,
            maxlen=config.SIGNAL_STREAM_MAXLEN,
            approximate=True,
        )
    except redis.RedisError as exc:
        logger.warning("redis_client: publish_signal failed for signal_id=%s: %s", payload.get("signal_id"), exc)
        return None
