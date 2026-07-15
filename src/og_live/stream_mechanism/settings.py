"""Runtime settings for the OG Live Stream mechanism."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from og_live.common import settings as common_settings
from og_live.common.audit import DEFAULT_AUDIT_BACKUP_COUNT, DEFAULT_AUDIT_MAX_BYTES, audit_log_path
from og_live.common.settings import WatchedItem

MECHANISM_NAME = "stream_mechanism"
DISPLAY_NAME = "OG Live Stream"

INPUT_REDIS_DB = int(os.environ.get("OG_STREAM_INPUT_REDIS_DB", "0"))
OUTPUT_REDIS_DB = int(os.environ.get("OG_STREAM_OUTPUT_REDIS_DB", "1"))

CANDLE_EVENT_STREAM = os.environ.get("OG_STREAM_CANDLE_EVENT_STREAM", "dp:candle_snapshot:events")
CANDLE_STATE_PREFIX = os.environ.get("OG_STREAM_CANDLE_STATE_PREFIX", "dp:candle_snapshot:latest")
CONSUMER_GROUP = os.environ.get("OG_STREAM_CONSUMER_GROUP", "og_live_stream")
CONSUMER_NAME = os.environ.get("OG_STREAM_CONSUMER_NAME", "og-stream-primary")

SIGNAL_OUTPUT_MODE = os.environ.get("OG_STREAM_SIGNAL_OUTPUT_MODE", "routed").strip().lower()
SIGNAL_STREAM_PREFIX = os.environ.get("OG_STREAM_SIGNAL_STREAM_PREFIX", "og:stream:signals")
ROUTED_SIGNAL_STREAM_TEMPLATE = os.environ.get(
    "OG_STREAM_ROUTED_SIGNAL_STREAM_TEMPLATE",
    "og:stream:signals:{strategy}:{symbol}:{timeframe}",
)
SIGNAL_DEDUP_PREFIX = os.environ.get("OG_STREAM_SIGNAL_DEDUP_PREFIX", "og:stream:dedup:signals")
SIGNAL_STREAM_MAXLEN = int(os.environ.get("OG_STREAM_SIGNAL_STREAM_MAXLEN", "10000"))
SIGNAL_DEDUP_TTL_SECONDS = int(os.environ.get("OG_STREAM_SIGNAL_DEDUP_TTL_SECONDS", str(14 * 24 * 3600)))

READ_COUNT = int(os.environ.get("OG_STREAM_READ_COUNT", "50"))
BLOCK_MS = int(os.environ.get("OG_STREAM_BLOCK_MS", "5000"))
REDIS_SOCKET_TIMEOUT_SECONDS = int(os.environ.get("OG_STREAM_REDIS_SOCKET_TIMEOUT_SECONDS", "15"))
OUTBOX_RETRY_INTERVAL_SECONDS = int(os.environ.get("OG_STREAM_OUTBOX_RETRY_INTERVAL_SECONDS", "30"))
MAX_EVENT_AGE_SECONDS = int(os.environ.get("OG_STREAM_MAX_EVENT_AGE_SECONDS", str(15 * 60)))
RESTART_PAUSE_SECONDS = max(1.0, BLOCK_MS / 1000)

AUDIT_LOG_ENABLED = common_settings.to_bool(os.environ.get("OG_STREAM_AUDIT_LOG_ENABLED"), True)
AUDIT_LOG_FILE = os.environ.get("OG_STREAM_AUDIT_LOG_FILE", "og_live_stream_audit.jsonl")
AUDIT_LOG_MAX_BYTES = int(os.environ.get("OG_STREAM_AUDIT_LOG_MAX_BYTES", str(DEFAULT_AUDIT_MAX_BYTES)))
AUDIT_LOG_BACKUP_COUNT = int(os.environ.get("OG_STREAM_AUDIT_LOG_BACKUP_COUNT", str(DEFAULT_AUDIT_BACKUP_COUNT)))

LIVE_FETCHING_SYMBOLS = common_settings.LIVE_FETCHING_SYMBOLS
LIVE_FETCHING_TIMEFRAMES = common_settings.LIVE_FETCHING_TIMEFRAMES


def signal_stream_key(strategy: str) -> str:
    """Return the legacy Stream mechanism Redis stream name for one strategy."""
    return f"{SIGNAL_STREAM_PREFIX}:{str(strategy).strip().lower()}"


def routed_signal_stream_key(strategy: str, symbol: str, tf: str) -> str:
    """Return the routed Redis stream name for one Stream mechanism signal."""
    return common_settings.routed_signal_stream_key(ROUTED_SIGNAL_STREAM_TEMPLATE, strategy, symbol, tf)


def signal_stream_keys(strategy: str, payload: dict[str, Any]) -> tuple[str, ...]:
    """Return configured Redis output streams for one Stream mechanism signal payload."""
    mode = SIGNAL_OUTPUT_MODE or "routed"
    if mode not in {"legacy", "routed", "dual"}:
        raise ValueError("OG_STREAM_SIGNAL_OUTPUT_MODE must be one of: legacy, routed, dual")

    streams: list[str] = []
    if mode in {"legacy", "dual"}:
        streams.append(signal_stream_key(strategy))
    if mode in {"routed", "dual"}:
        symbol = str(payload.get("symbol", "") or "").strip()
        tf = str(payload.get("timeframe", "") or "").strip()
        if not symbol or not tf:
            raise ValueError("Stream routed signal output requires payload symbol and timeframe")
        streams.append(routed_signal_stream_key(strategy, symbol, tf))
    return tuple(dict.fromkeys(streams))


def signal_dedup_key(signal_id: str) -> str:
    """Return the Redis idempotency key for one Stream mechanism signal."""
    return f"{SIGNAL_DEDUP_PREFIX}:{signal_id}"


def candle_state_key(symbol: str, tf: str) -> str:
    """Return the DP state key that stores the latest candle snapshot."""
    return common_settings.candle_state_key(CANDLE_STATE_PREFIX, symbol, tf)


def snapshot_process_key(strategy: str, snapshot_version: str, symbol: str, tf: str) -> str:
    """Return the local idempotency key for one strategy run on one DP snapshot."""
    return common_settings.snapshot_process_key(strategy, snapshot_version, symbol, tf)


def runtime_dir() -> Path:
    """Return the local runtime state directory for the Stream mechanism."""
    return common_settings.runtime_dir(MECHANISM_NAME)


def audit_file_path() -> Path:
    """Return the Stream mechanism structured audit log path."""
    return audit_log_path(AUDIT_LOG_FILE)


def load_watched_items() -> list[WatchedItem]:
    """Load the Stream mechanism watchlist."""
    return common_settings.load_watched_items_from_env(prefix="OG_STREAM")


def watched_summary() -> dict[str, Any]:
    """Return the Stream mechanism watchlist summary."""
    return common_settings.watched_summary(load_watched_items())
