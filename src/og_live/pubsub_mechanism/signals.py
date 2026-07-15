"""Redis access for the OG Live Pub/Sub mechanism."""

from __future__ import annotations

import logging
from typing import Any

import redis

from og_live.common.redis_tools import build_client
from og_live.pubsub_mechanism import settings

logger = logging.getLogger(__name__)

_input_client: redis.Redis | None = None
_output_client: redis.Redis | None = None
_PUBLISH_SIGNAL_SCRIPT = """
local seen_key = KEYS[1]
local ttl = tonumber(ARGV[1])
local maxlen = tonumber(ARGV[2])
local fields = {}
for i = 3, #ARGV do
    fields[#fields + 1] = ARGV[i]
end

if redis.call("SET", seen_key, "1", "NX", "EX", ttl) then
    local ids = {}
    for i = 2, #KEYS do
        ids[#ids + 1] = redis.call("XADD", KEYS[i], "MAXLEN", "~", maxlen, "*", unpack(fields))
    end
    return ids
end

return "__duplicate__"
"""
DUPLICATE_SIGNAL = "__duplicate__"


def get_input_client() -> redis.Redis:
    """Return the Redis client used for DP Pub/Sub input and snapshot state reads."""
    global _input_client
    if _input_client is None:
        _input_client = _build_client(settings.INPUT_REDIS_DB)
    return _input_client


def get_output_client() -> redis.Redis:
    """Return the Redis client used for Pub/Sub mechanism signal output."""
    global _output_client
    if _output_client is None:
        _output_client = _build_client(settings.OUTPUT_REDIS_DB)
    return _output_client


def reset_input_client() -> None:
    """Drop the cached input Redis client after connection-level failures."""
    global _input_client
    _input_client = None


def reset_output_client() -> None:
    """Drop the cached output Redis client after connection-level failures."""
    global _output_client
    _output_client = None


def reset_clients() -> None:
    """Drop both Redis clients after broad connection failures."""
    reset_input_client()
    reset_output_client()


def _build_client(db: int) -> redis.Redis:
    return build_client(db=db, socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS)


def publish_signal(strategy: str, payload: dict[str, Any]) -> str | None:
    """Publish one Pub/Sub mechanism signal payload idempotently."""
    signal_id = str(payload.get("signal_id", "") or "")
    if not signal_id:
        logger.warning("pubsub_signals: missing signal_id, refusing publish")
        return None
    try:
        stream_keys = settings.signal_stream_keys(strategy, payload)
    except ValueError as exc:
        logger.warning("pubsub_signals: invalid output routing for signal_id=%s: %s", signal_id, exc)
        return None
    if not stream_keys:
        logger.warning("pubsub_signals: no output streams configured for signal_id=%s", signal_id)
        return None

    fields: list[str] = []
    for key, value in payload.items():
        fields.extend([str(key), "" if value is None else str(value)])
    try:
        result = get_output_client().eval(
            _PUBLISH_SIGNAL_SCRIPT,
            1 + len(stream_keys),
            settings.signal_dedup_key(signal_id),
            *stream_keys,
            str(settings.SIGNAL_DEDUP_TTL_SECONDS),
            str(settings.SIGNAL_STREAM_MAXLEN),
            *fields,
        )
        if isinstance(result, list):
            return str(result[0]) if result else ""
        return str(result)
    except redis.RedisError as exc:
        logger.warning("pubsub_signals: publish failed for signal_id=%s: %s", signal_id, exc)
        reset_output_client()
        return None

