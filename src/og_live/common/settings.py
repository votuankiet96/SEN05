"""Shared OG Live runtime settings and watchlist helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from og_core import config as core_config

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
except ImportError:
    pass


REPO_ROOT = Path(__file__).resolve().parents[3]

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_USERNAME = os.environ.get("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_DB = int(os.environ.get("REDIS_DB", os.environ.get("OG_REDIS_DB", "0")))

LIVE_FETCHING_SYMBOLS = (
    "BTCUSD",
    "DE40",
    "FR40",
    "GOLD",
    "HK50",
    "J225",
    "SP35",
    "UK100",
    "US100",
    "US30",
    "US500",
)

LIVE_FETCHING_TIMEFRAMES = (
    "M5",
    "M10",
    "M15",
    "M20",
    "M30",
    "M45",
    "M90",
    "H1",
    "H2",
    "H3",
    "H4",
    "H6",
    "H8",
    "D1",
    "W",
)

DEFAULT_LIVE_STRATEGY = "combo"
DEFAULT_LIVE_BARS = 500
DEFAULT_LIVE_LATEST_ONLY = True

ASSET_TYPE_ALIASES = {
    "indice": "Indice",
    "indices": "Indice",
    "index": "Indice",
    "indexes": "Indice",
    "metal": "Metal",
    "metals": "Metal",
    "crypto": "Crypto",
    "cryptocurrency": "Crypto",
    "forex": "FOREX",
    "fx": "FOREX",
}


@dataclass(frozen=True)
class WatchedItem:
    """One live strategy subscription matched against DP candle snapshots."""

    strategy: str
    symbols: tuple[str, ...]
    tf: str
    bars: int = DEFAULT_LIVE_BARS
    latest_only: bool = DEFAULT_LIVE_LATEST_ONLY


def runtime_dir(mechanism: str) -> Path:
    """Return the local runtime state directory for one live mechanism."""
    path = REPO_ROOT / "runtime" / "og_live" / mechanism
    path.mkdir(parents=True, exist_ok=True)
    return path


def candle_state_key(prefix: str, symbol: str, tf: str) -> str:
    """Return the DP state key that stores the latest candle snapshot."""
    return f"{prefix}:{str(symbol).strip().upper()}:{str(tf).strip().upper()}"


def snapshot_process_key(strategy: str, snapshot_version: str, symbol: str, tf: str) -> str:
    """Return the local idempotency key for one strategy run on one DP snapshot."""
    return "|".join(
        [
            str(strategy).strip().lower(),
            str(symbol).strip().upper(),
            str(tf).strip().upper(),
            str(snapshot_version).strip(),
        ]
    )


def routed_signal_stream_key(template: str, strategy: str, symbol: str, tf: str) -> str:
    """Return the Redis stream name for one strategy/symbol/timeframe signal route."""
    return template.format(
        strategy=str(strategy).strip().lower(),
        symbol=str(symbol).strip().upper(),
        timeframe=str(tf).strip().upper(),
        tf=str(tf).strip().upper(),
    )


def load_watched_items_from_env(
    *,
    prefix: str,
    legacy_prefix: str | None = None,
    default_strategy: str = DEFAULT_LIVE_STRATEGY,
    default_asset_types: tuple[str, ...] = (),
    default_timeframes: tuple[str, ...] | None = None,
) -> list[WatchedItem]:
    """Load watched strategy subscriptions from mechanism-specific env vars."""
    raw = _env(prefix, "WATCHED_JSON", legacy_prefix=legacy_prefix)
    if raw:
        data: Any = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"{prefix}_WATCHED_JSON must be a JSON list")
        return [_parse_watched_item(item) for item in data]

    strategy = (_env(prefix, "STRATEGY", legacy_prefix=legacy_prefix) or default_strategy).strip().lower()
    symbols = _resolve_symbols(prefix, legacy_prefix=legacy_prefix, default_asset_types=default_asset_types)
    timeframes = _resolve_timeframes(prefix, legacy_prefix=legacy_prefix, default_timeframes=default_timeframes)
    bars = max(1, int(_env(prefix, "BARS", legacy_prefix=legacy_prefix) or str(DEFAULT_LIVE_BARS)))
    latest_only = to_bool(
        _env(prefix, "LATEST_ONLY", legacy_prefix=legacy_prefix),
        DEFAULT_LIVE_LATEST_ONLY,
    )
    return [
        WatchedItem(strategy=strategy, symbols=tuple(symbols), tf=tf, bars=bars, latest_only=latest_only)
        for tf in timeframes
    ]


def watched_summary(items: list[WatchedItem]) -> dict[str, Any]:
    """Return one mechanism watchlist in a compact, operator-readable shape."""
    symbols: set[str] = set()
    timeframes: set[str] = set()
    strategies: set[str] = set()
    bars = sorted({item.bars for item in items})
    latest_only = sorted({item.latest_only for item in items})
    for item in items:
        strategies.add(item.strategy)
        timeframes.add(item.tf)
        symbols.update(item.symbols)
    return {
        "strategies": sorted(strategies),
        "symbols": sorted(symbols),
        "timeframes": sorted(timeframes, key=timeframe_sort_key),
        "pairs": sum(len(item.symbols) for item in items),
        "bars": bars,
        "latest_only": latest_only,
    }


def csv(value: str | None) -> list[str]:
    """Parse a simple comma-separated env value."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def to_bool(value: object, default: bool) -> bool:
    """Parse env-like truthy values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def timeframe_sort_key(tf: str) -> int:
    """Sort by the live fetching timeframe order."""
    try:
        return LIVE_FETCHING_TIMEFRAMES.index(tf)
    except ValueError:
        return len(LIVE_FETCHING_TIMEFRAMES)


def _env(prefix: str, name: str, *, legacy_prefix: str | None = None) -> str | None:
    value = os.environ.get(f"{prefix}_{name}")
    if value is not None:
        return value
    if legacy_prefix:
        return os.environ.get(f"{legacy_prefix}_{name}")
    return None


def _resolve_symbols(
    prefix: str,
    *,
    legacy_prefix: str | None,
    default_asset_types: tuple[str, ...],
) -> list[str]:
    selected = csv(_env(prefix, "SYMBOLS", legacy_prefix=legacy_prefix))
    symbols = [symbol.upper() for symbol in selected] if selected else list(LIVE_FETCHING_SYMBOLS)
    unknown = [symbol for symbol in symbols if symbol not in LIVE_FETCHING_SYMBOLS]
    if unknown:
        raise ValueError(f"{prefix}_SYMBOLS contains unsupported live symbol(s): {', '.join(unknown)}")

    asset_types = _asset_types_from_env(prefix, legacy_prefix=legacy_prefix, default_asset_types=default_asset_types)
    if asset_types:
        symbols = [
            symbol
            for symbol in symbols
            if str(core_config.SYMBOLS.get(symbol, {}).get("asset_type", "")) in asset_types
        ]
    if not symbols:
        raise ValueError(f"{prefix} watchlist has no symbols after applying filters")
    return symbols


def _resolve_timeframes(
    prefix: str,
    *,
    legacy_prefix: str | None,
    default_timeframes: tuple[str, ...] | None,
) -> list[str]:
    selected = csv(_env(prefix, "TIMEFRAMES", legacy_prefix=legacy_prefix))
    if selected:
        timeframes = [tf.upper() for tf in selected]
    elif default_timeframes is not None:
        timeframes = [tf.upper() for tf in default_timeframes]
    else:
        timeframes = list(LIVE_FETCHING_TIMEFRAMES)
    unknown = [tf for tf in timeframes if tf not in LIVE_FETCHING_TIMEFRAMES]
    if unknown:
        raise ValueError(f"{prefix}_TIMEFRAMES contains unsupported timeframe(s): {', '.join(unknown)}")
    if not timeframes:
        raise ValueError(f"{prefix} watchlist has no timeframes")
    return timeframes


def _asset_types_from_env(
    prefix: str,
    *,
    legacy_prefix: str | None,
    default_asset_types: tuple[str, ...],
) -> set[str]:
    raw_values = csv(_env(prefix, "ASSET_TYPES", legacy_prefix=legacy_prefix))
    if not raw_values:
        raw_values = list(default_asset_types)
    asset_types: set[str] = set()
    for value in raw_values:
        normalized = ASSET_TYPE_ALIASES.get(value.strip().lower(), value.strip())
        if normalized:
            asset_types.add(normalized)
    return asset_types


def _parse_watched_item(item: Any) -> WatchedItem:
    if not isinstance(item, dict):
        raise ValueError(f"Invalid watched item: {item!r}")
    symbols = item.get("symbols")
    if isinstance(symbols, str):
        symbols = [part.strip() for part in symbols.split(",") if part.strip()]
    if not symbols:
        raise ValueError(f"Watched item missing symbols: {item!r}")
    return WatchedItem(
        strategy=str(item.get("strategy", DEFAULT_LIVE_STRATEGY)).strip().lower(),
        symbols=tuple(str(symbol).strip().upper() for symbol in symbols),
        tf=str(item.get("tf", "H1")).strip().upper(),
        bars=max(1, int(item.get("bars", DEFAULT_LIVE_BARS))),
        latest_only=to_bool(item.get("latest_only", DEFAULT_LIVE_LATEST_ONLY), DEFAULT_LIVE_LATEST_ONLY),
    )

