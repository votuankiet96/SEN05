"""Redis access for OG live input and output streams."""

from __future__ import annotations

import logging
from typing import Any

import redis

from og_live import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None
_PUBLISH_SIGNAL_SCRIPT = """
local seen_key = KEYS[1]
local stream_key = KEYS[2]
local ttl = tonumber(ARGV[1])
local maxlen = tonumber(ARGV[2])
local fields = {}
for i = 3, #ARGV do
    fields[#fields + 1] = ARGV[i]
end

if redis.call("SET", seen_key, "1", "NX", "EX", ttl) then
    return redis.call("XADD", stream_key, "MAXLEN", "~", maxlen, "*", unpack(fields))
end

return "__duplicate__"
"""
DUPLICATE_SIGNAL = "__duplicate__"


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
    """Publish one signal payload idempotently. Never raises; returns None on Redis failure."""
    signal_id = str(payload.get("signal_id", "") or "")
    if not signal_id:
        logger.warning("redis_signals: missing signal_id, refusing publish")
        return None

    fields: list[str] = []
    for key, value in payload.items():
        fields.extend([str(key), "" if value is None else str(value)])
    try:
        return str(
            get_client().eval(
                _PUBLISH_SIGNAL_SCRIPT,
                2,
                settings.signal_dedup_key(signal_id),
                settings.signal_stream_key(strategy),
                str(settings.SIGNAL_DEDUP_TTL_SECONDS),
                str(settings.SIGNAL_STREAM_MAXLEN),
                *fields,
            )
        )
    except redis.RedisError as exc:
        logger.warning("redis_signals: publish failed for signal_id=%s: %s", signal_id, exc)
        reset_client()
        return None
