"""Durable file outbox for validated candles awaiting SQL commit."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..log import log_event
from .sql_connector import bulk_upsert_candles


LOGGER = logging.getLogger(__name__)
_VALID_NAME = re.compile(r"^\d+_[A-Z0-9]+_\d{8}T\d{6}Z\.json$")


def _directory(config: dict[str, Any]) -> Path:
    return Path(config["app"]["runtime_dir"]) / "spool" / "pending"


def _key(candle: dict[str, Any]) -> str:
    timestamp = candle["timestamp"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{int(candle['symbol_id'])}_{candle['timeframe']}_{timestamp}.json"


def _payload(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol_id": int(candle["symbol_id"]),
        "symbol": str(candle["symbol"]),
        "exchange": str(candle["exchange"]),
        "timeframe": str(candle["timeframe"]),
        "timestamp": candle["timestamp"].astimezone(timezone.utc).isoformat(),
        "open": str(candle["open"]),
        "high": str(candle["high"]),
        "low": str(candle["low"]),
        "close": str(candle["close"]),
        "volume": None if candle.get("volume") is None else str(candle["volume"]),
    }


def enqueue(config: dict[str, Any], candles: list[dict[str, Any]]) -> int:
    """Atomically persist candles by business key before warehouse delivery."""
    directory = _directory(config)
    directory.mkdir(parents=True, exist_ok=True)
    for candle in candles:
        target = directory / _key(candle)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(_payload(candle), separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
    return len(candles)


def ack(config: dict[str, Any], candles: list[dict[str, Any]]) -> int:
    """Remove only candles whose staging and Fact transaction committed."""
    directory = _directory(config)
    removed = 0
    for candle in candles:
        path = directory / _key(candle)
        for attempt in range(5):
            try:
                path.unlink()
                removed += 1
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    return removed


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["timestamp"] = datetime.fromisoformat(value["timestamp"]).astimezone(timezone.utc)
    for key in ("open", "high", "low", "close"):
        value[key] = Decimal(value[key])
    value["volume"] = None if value.get("volume") is None else Decimal(value["volume"])
    return value


def pending_status(config: dict[str, Any]) -> dict[str, Any]:
    """Return count, corrupt count, bytes, and oldest age without candle values."""
    directory = _directory(config)
    files = list(directory.glob("*.json")) if directory.is_dir() else []
    now = datetime.now(timezone.utc).timestamp()
    stats = []
    for path in files:
        try:
            stats.append((path, path.stat()))
        except FileNotFoundError:
            continue
    oldest = min((item.st_mtime for _, item in stats), default=None)
    return {
        "pending": len(stats),
        "corrupt": sum(not _VALID_NAME.fullmatch(path.name) for path, _ in stats),
        "bytes": sum(item.st_size for _, item in stats),
        "oldest_age_seconds": None if oldest is None else round(max(0, now - oldest), 1),
    }


def drain(config: dict[str, Any], *, limit: int = 5000) -> dict[str, int]:
    """Replay pending candles idempotently and acknowledge only after SQL commit."""
    directory = _directory(config)
    paths = sorted(directory.glob("*.json"))[:limit] if directory.is_dir() else []
    grouped: dict[tuple[int, str], list[tuple[Path, dict[str, Any]]]] = {}
    failed = 0
    for path in paths:
        try:
            candle = _load(path)
            key = (int(candle["symbol_id"]), candle["timeframe"])
            grouped.setdefault(key, []).append((path, candle))
        except (OSError, ValueError, KeyError) as exc:
            failed += 1
            log_event(
                LOGGER,
                logging.ERROR,
                "SPOOL_FILE_CORRUPT",
                "HIGH",
                component="spool",
                file=path.name,
                error_type=type(exc).__name__,
                error=exc,
                action="file retained for operator review",
            )
    timeframes = {item["code"]: item for item in config["data"]["timeframes"]}
    delivered = 0
    for (symbol_id, code), items in grouped.items():
        try:
            candles = [item[1] for item in items]
            bulk_upsert_candles(config, timeframes[code], candles)
            for path, _ in items:
                path.unlink(missing_ok=True)
            delivered += len(items)
        except Exception as exc:
            failed += len(items)
            log_event(
                LOGGER,
                logging.ERROR,
                "SPOOL_DELIVERY_FAILED",
                "HIGH",
                component="spool",
                symbol_id=symbol_id,
                timeframe=code,
                candles=len(items),
                error_type=type(exc).__name__,
                error=exc,
                action="candles retained in spool for retry",
            )
    result = {"examined": len(paths), "delivered": delivered, "failed": failed}
    if paths:
        log_event(
            LOGGER,
            logging.ERROR if failed else logging.INFO,
            "SPOOL_REPLAY_COMPLETED",
            "HIGH" if failed else "NONE",
            component="spool",
            **result,
        )
    return result
