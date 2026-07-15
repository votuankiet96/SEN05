"""Shared Redis helpers for OG Live mechanisms."""

from __future__ import annotations

import redis

from og_live.common import settings as common_settings


def build_client(*, db: int, socket_timeout: int) -> redis.Redis:
    """Build a Redis client for one logical Redis database."""
    return redis.Redis(
        host=common_settings.REDIS_HOST,
        port=common_settings.REDIS_PORT,
        username=common_settings.REDIS_USERNAME,
        password=common_settings.REDIS_PASSWORD,
        db=db,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=socket_timeout,
    )
