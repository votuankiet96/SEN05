"""Runtime settings for the OG Live Pub/Sub mechanism."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from og_live.common import settings as common_settings
from og_live.common.audit import DEFAULT_AUDIT_BACKUP_COUNT, DEFAULT_AUDIT_MAX_BYTES, audit_log_path
from og_live.common.settings import WatchedItem

MECHANISM_NAME = "pubsub_mechanism"
DISPLAY_NAME = "OG Live Pub/Sub"

ENABLED = common_settings.to_bool(os.environ.get("OG_PUBSUB_ENABLED"), True)
INPUT_REDIS_DB = int(os.environ.get("OG_PUBSUB_INPUT_REDIS_DB", "0"))
OUTPUT_REDIS_DB = int(os.environ.get("OG_PUBSUB_OUTPUT_REDIS_DB", "2"))

PUBSUB_CHANNEL = os.environ.get("OG_PUBSUB_CHANNEL", "dp:pubsub:candle_snapshot:events")
CANDLE_STATE_PREFIX = os.environ.get("OG_PUBSUB_CANDLE_STATE_PREFIX", "dp:candle_snapshot:latest")

SIGNAL_OUTPUT_MODE = os.environ.get("OG_PUBSUB_SIGNAL_OUTPUT_MODE", "routed").strip().lower()
SIGNAL_STREAM_PREFIX = os.environ.get("OG_PUBSUB_SIGNAL_STREAM_PREFIX", "og:pubsub:signals")
ROUTED_SIGNAL_STREAM_TEMPLATE = os.environ.get(
    "OG_PUBSUB_ROUTED_SIGNAL_STREAM_TEMPLATE",
    "og:pubsub:signals:{strategy}:{symbol}:{timeframe}",
)
SIGNAL_DEDUP_PREFIX = os.environ.get("OG_PUBSUB_SIGNAL_DEDUP_PREFIX", "og:pubsub:dedup:signals")
SIGNAL_STREAM_MAXLEN = int(os.environ.get("OG_PUBSUB_SIGNAL_STREAM_MAXLEN", "10000"))
SIGNAL_DEDUP_TTL_SECONDS = int(os.environ.get("OG_PUBSUB_SIGNAL_DEDUP_TTL_SECONDS", str(14 * 24 * 3600)))

REDIS_SOCKET_TIMEOUT_SECONDS = int(os.environ.get("OG_PUBSUB_REDIS_SOCKET_TIMEOUT_SECONDS", "15"))
OUTBOX_RETRY_INTERVAL_SECONDS = int(os.environ.get("OG_PUBSUB_OUTBOX_RETRY_INTERVAL_SECONDS", "30"))
MESSAGE_POLL_TIMEOUT_SECONDS = float(os.environ.get("OG_PUBSUB_MESSAGE_POLL_TIMEOUT_SECONDS", "1"))
MAX_EVENT_AGE_SECONDS = int(os.environ.get("OG_PUBSUB_MAX_EVENT_AGE_SECONDS", str(15 * 60)))
RESTART_PAUSE_SECONDS = max(1.0, float(os.environ.get("OG_PUBSUB_RESTART_PAUSE_SECONDS", "5")))

AUDIT_LOG_ENABLED = common_settings.to_bool(os.environ.get("OG_PUBSUB_AUDIT_LOG_ENABLED"), True)
AUDIT_LOG_FILE = os.environ.get("OG_PUBSUB_AUDIT_LOG_FILE", "og_live_pubsub_audit.jsonl")
AUDIT_LOG_MAX_BYTES = int(os.environ.get("OG_PUBSUB_AUDIT_LOG_MAX_BYTES", str(DEFAULT_AUDIT_MAX_BYTES)))
AUDIT_LOG_BACKUP_COUNT = int(os.environ.get("OG_PUBSUB_AUDIT_LOG_BACKUP_COUNT", str(DEFAULT_AUDIT_BACKUP_COUNT)))

LIVE_FETCHING_SYMBOLS = common_settings.LIVE_FETCHING_SYMBOLS
LIVE_FETCHING_TIMEFRAMES = common_settings.LIVE_FETCHING_TIMEFRAMES


def signal_stream_key(strategy: str) -> str:
    """Return the Pub/Sub mechanism Redis stream name for one strategy."""
    return f"{SIGNAL_STREAM_PREFIX}:{str(strategy).strip().lower()}"


def routed_signal_stream_key(strategy: str, symbol: str, tf: str) -> str:
    """Return the routed Redis stream name for one Pub/Sub mechanism signal."""
    return common_settings.routed_signal_stream_key(ROUTED_SIGNAL_STREAM_TEMPLATE, strategy, symbol, tf)


def signal_stream_keys(strategy: str, payload: dict[str, Any]) -> tuple[str, ...]:
    """Return configured Redis output streams for one Pub/Sub mechanism signal payload."""
    mode = SIGNAL_OUTPUT_MODE or "routed"
    if mode not in {"legacy", "routed", "dual"}:
        raise ValueError("OG_PUBSUB_SIGNAL_OUTPUT_MODE must be one of: legacy, routed, dual")

    streams: list[str] = []
    if mode in {"legacy", "dual"}:
        streams.append(signal_stream_key(strategy))
    if mode in {"routed", "dual"}:
        symbol = str(payload.get("symbol", "") or "").strip()
        tf = str(payload.get("timeframe", "") or "").strip()
        if not symbol or not tf:
            raise ValueError("Pub/Sub routed signal output requires payload symbol and timeframe")
        streams.append(routed_signal_stream_key(strategy, symbol, tf))
    return tuple(dict.fromkeys(streams))


def signal_dedup_key(signal_id: str) -> str:
    """Return the Redis idempotency key for one Pub/Sub mechanism signal."""
    return f"{SIGNAL_DEDUP_PREFIX}:{signal_id}"


def candle_state_key(symbol: str, tf: str) -> str:
    """Return the DP state key that stores the latest candle snapshot."""
    return common_settings.candle_state_key(CANDLE_STATE_PREFIX, symbol, tf)


def snapshot_process_key(strategy: str, snapshot_version: str, symbol: str, tf: str) -> str:
    """Return the local idempotency key for one strategy run on one DP snapshot."""
    return common_settings.snapshot_process_key(strategy, snapshot_version, symbol, tf)


def runtime_dir() -> Path:
    """Return the local runtime state directory for the Pub/Sub mechanism."""
    return common_settings.runtime_dir(MECHANISM_NAME)


def audit_file_path() -> Path:
    """Return the Pub/Sub mechanism structured audit log path."""
    return audit_log_path(AUDIT_LOG_FILE)


def load_watched_items() -> list[WatchedItem]:
    """Load the Pub/Sub mechanism watchlist."""
    return common_settings.load_watched_items_from_env(
        prefix="OG_PUBSUB",
        default_strategy="combo",
        default_asset_types=("Indice",),
        default_timeframes=("H4",),
    )


def watched_summary() -> dict[str, Any]:
    """Return the Pub/Sub mechanism watchlist summary."""
    return common_settings.watched_summary(load_watched_items())
