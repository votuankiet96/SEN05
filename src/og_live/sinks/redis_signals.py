"""Redis access for OG live input and output streams."""

from __future__ import annotations

import logging
from typing import Any

import redis

from og_live import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    """Return a lazy Redis client shared by this process."""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            username=settings.REDIS_USERNAME,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        )
    return _client


def reset_client() -> None:
    """Drop the cached Redis client after connection-level failures."""
    global _client
    _client = None


def ensure_consumer_group() -> None:
    """Create the candle snapshot consumer group if it does not already exist."""
    try:
        get_client().xgroup_create(
            settings.CANDLE_SNAPSHOT_STREAM,
            settings.CONSUMER_GROUP,
            id="$",
            mkstream=True,
        )
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def publish_signal(strategy: str, payload: dict[str, Any]) -> str | None:
    """Publish one signal payload. Never raises; returns None on Redis failure."""
    fields = {key: "" if value is None else str(value) for key, value in payload.items()}
    try:
        return get_client().xadd(
            settings.signal_stream_key(strategy),
            fields,
            maxlen=settings.SIGNAL_STREAM_MAXLEN,
            approximate=True,
        )
    except redis.RedisError as exc:
        logger.warning("redis_signals: publish failed for signal_id=%s: %s", payload.get("signal_id"), exc)
        reset_client()
        return None
