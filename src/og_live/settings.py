"""Runtime settings for OG live signal generation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass


REPO_ROOT = Path(__file__).resolve().parents[2]

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_USERNAME = os.environ.get("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")

CANDLE_SNAPSHOT_STREAM = os.environ.get("OG_LIVE_CANDLE_STREAM", "candle_snapshot")
CONSUMER_GROUP = os.environ.get("OG_LIVE_CONSUMER_GROUP", "og_live")
CONSUMER_NAME = os.environ.get("OG_LIVE_CONSUMER_NAME", "og-primary")

SIGNAL_STREAM_PREFIX = os.environ.get("OG_LIVE_SIGNAL_STREAM_PREFIX", "signal_stream")
SIGNAL_STREAM_MAXLEN = int(os.environ.get("OG_LIVE_SIGNAL_STREAM_MAXLEN", "10000"))

READ_COUNT = int(os.environ.get("OG_LIVE_READ_COUNT", "50"))
BLOCK_MS = int(os.environ.get("OG_LIVE_BLOCK_MS", "5000"))
REDIS_SOCKET_TIMEOUT_SECONDS = int(os.environ.get("OG_LIVE_REDIS_SOCKET_TIMEOUT_SECONDS", "15"))
OUTBOX_RETRY_INTERVAL_SECONDS = int(os.environ.get("OG_LIVE_OUTBOX_RETRY_INTERVAL_SECONDS", "30"))
RESTART_PAUSE_SECONDS = max(1.0, BLOCK_MS / 1000)


@dataclass(frozen=True)
class WatchedItem:
    """One live strategy subscription matched against DP6 candle snapshots."""

    strategy: str
    symbols: tuple[str, ...]
    tf: str
    bars: int = 500
    latest_only: bool = True


DEFAULT_WATCHED = [
    {"strategy": "combo", "symbols": ["US30", "DE40"], "tf": "H1", "bars": 500, "latest_only": True},
]


def signal_stream_key(strategy: str) -> str:
    """Return the Redis stream name for one strategy's published signals."""
    return f"{SIGNAL_STREAM_PREFIX}:{strategy}"


def runtime_dir() -> Path:
    """Return the local runtime state directory for og_live."""
    path = REPO_ROOT / "runtime" / "og_live"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_watched_items() -> list[WatchedItem]:
    """Load watched strategy subscriptions from env JSON or the built-in pilot default."""
    raw = os.environ.get("OG_LIVE_WATCHED_JSON")
    data: Any = DEFAULT_WATCHED
    if raw:
        data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("OG_LIVE_WATCHED_JSON must be a JSON list")
    return [_parse_watched_item(item) for item in data]


def _parse_watched_item(item: Any) -> WatchedItem:
    if not isinstance(item, dict):
        raise ValueError(f"Invalid watched item: {item!r}")
    symbols = item.get("symbols")
    if isinstance(symbols, str):
        symbols = [part.strip() for part in symbols.split(",") if part.strip()]
    if not symbols:
        raise ValueError(f"Watched item missing symbols: {item!r}")
    return WatchedItem(
        strategy=str(item.get("strategy", "combo")).strip().lower(),
        symbols=tuple(str(symbol).strip().upper() for symbol in symbols),
        tf=str(item.get("tf", "H1")).strip().upper(),
        bars=int(item.get("bars", 500)),
        latest_only=_to_bool(item.get("latest_only", True), True),
    )


def _to_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
