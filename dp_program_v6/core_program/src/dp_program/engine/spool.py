"""Durable file outbox for validated candles awaiting SQL commit."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from ..log import log_event
from .sql_connector import bulk_upsert_candles, fetch_universe


LOGGER = logging.getLogger(__name__)
_VALID_NAME = re.compile(r"^\d+_[A-Z0-9]+_\d{8}T\d{6}Z\.json$")

# Spool là chỗ lưu tạm nến ra file trước khi ghi SQL.
# Nếu chương trình tắt giữa chừng, lần sau còn file để ghi lại.
# File này cũng chứa khóa dùng chung giữa live và backfill.

# Tên file gồm SymbolID, timeframe và thời gian nến.
# Nhìn tên file là biết nến đó thuộc cặp nào.


class InterprocessLockTimeout(TimeoutError):
    """Raised when a bounded shared-runtime lock cannot be acquired."""


@contextmanager
def interprocess_lock(
    config: dict[str, Any], name: str, *, timeout_seconds: float
) -> Iterator[None]:
    """Hold one named OS file lock for a bounded interval."""
    # Khóa file để hai process không sửa cùng một tài nguyên cùng lúc.
    # Có timeout để không chờ vô hạn.
    path = Path(config["app"]["runtime_dir"]) / "run" / f"{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if time.monotonic() >= deadline:
                handle.close()
                raise InterprocessLockTimeout(
                    f"timed out acquiring {name} interprocess lock"
                ) from exc
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace one shared-runtime text file via a unique temp file."""
    # Ghi vào file tạm trước, rồi thay thế file thật.
    # Cách này tránh file bị ghi nửa chừng.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        for attempt in range(100):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


def _directory(config: dict[str, Any]) -> Path:
    # File còn nằm ở đây nghĩa là chưa chắc đã ghi xong SQL.
    return Path(config["app"]["runtime_dir"]) / "spool" / "pending"


def _key(candle: dict[str, Any]) -> str:
    # Cùng một nến luôn ra cùng một tên file.
    # Vì vậy ghi lại nhiều lần vẫn không tạo trùng.
    timestamp = candle["timestamp"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{int(candle['symbol_id'])}_{candle['timeframe']}_{timestamp}.json"


def _payload(candle: dict[str, Any]) -> dict[str, Any]:
    # Payload chỉ giữ dữ liệu cần để ghi SQL lại.
    # Decimal và thời gian được chuyển thành text để lưu JSON an toàn.
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
    # Lưu nến ra file ngay trước khi ghi SQL.
    directory = _directory(config)
    directory.mkdir(parents=True, exist_ok=True)
    for candle in candles:
        target = directory / _key(candle)
        atomic_write_text(target, json.dumps(_payload(candle), separators=(",", ":")))
    return len(candles)


def ack(config: dict[str, Any], candles: list[dict[str, Any]]) -> int:
    """Remove only candles whose staging and Fact transaction committed."""
    # Chỉ xóa file sau khi SQL đã ghi thành công.
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
    # Đọc file tạm và đổi dữ liệu về đúng kiểu để ghi lại SQL.
    value = json.loads(path.read_text(encoding="utf-8"))
    value["timestamp"] = datetime.fromisoformat(value["timestamp"]).astimezone(timezone.utc)
    for key in ("open", "high", "low", "close"):
        value[key] = Decimal(value[key])
    value["volume"] = None if value.get("volume") is None else Decimal(value["volume"])
    return value


def pending_status(config: dict[str, Any]) -> dict[str, Any]:
    """Return count, corrupt count, bytes, and oldest age without candle values."""
    # Status chỉ trả số lượng, dung lượng và tuổi file.
    # Không log giá trị nến.
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
    # Runtime gọi hàm này để ghi lại các file tạm còn sót.
    # Ghi theo từng symbol/timeframe để đúng quy ước SQL.
    directory = _directory(config)
    paths = sorted(directory.glob("*.json"))[:limit] if directory.is_dir() else []
    grouped: dict[tuple[int, str], list[Path]] = {}
    failed = 0
    for path in paths:
        if not _VALID_NAME.fullmatch(path.name):
            # File sai tên được giữ lại để người vận hành kiểm tra.
            failed += 1
            log_event(
                LOGGER,
                logging.ERROR,
                "SPOOL_FILE_CORRUPT",
                "HIGH",
                component="spool",
                file=path.name,
                error_type="InvalidFilename",
                action="file retained for operator review",
            )
            continue
        symbol_id, code, _timestamp = path.stem.split("_", 2)
        grouped.setdefault((int(symbol_id), code), []).append(path)
    if not grouped:
        return {"examined": len(paths), "delivered": 0, "failed": failed}
    timeframes = {item["code"]: item for item in fetch_universe(config)[1]}
    delivered = 0
    timeout = float(config["sql_server"]["command_timeout_seconds"]) + 5.0
    for (symbol_id, code), group_paths in grouped.items():
        pending_count = len(group_paths)
        try:
            with interprocess_lock(config, "delivery", timeout_seconds=timeout):
                # Replay cũng dùng khóa ghi SQL như pipeline.
                items = []
                for path in group_paths:
                    try:
                        candle = _load(path)
                        if int(candle["symbol_id"]) != symbol_id or candle["timeframe"] != code:
                            raise ValueError("spool payload does not match its business-key filename")
                        items.append((path, candle))
                    except FileNotFoundError:
                        continue
                    except (OSError, ValueError, KeyError) as exc:
                        failed += 1
                        log_event(
                            LOGGER, logging.ERROR, "SPOOL_FILE_CORRUPT", "HIGH",
                            component="spool", file=path.name,
                            error_type=type(exc).__name__, error=exc,
                            action="file retained for operator review",
                        )
                if not items:
                    continue
                pending_count = len(items)
                # Ghi lại nến cũ vẫn an toàn vì SQL tự xử lý trùng.
                bulk_upsert_candles(config, timeframes[code], [item[1] for item in items])
                for path, _ in items:
                    path.unlink(missing_ok=True)
                delivered += len(items)
        except Exception as exc:
            failed += pending_count
            log_event(
                LOGGER,
                logging.ERROR,
                "SPOOL_DELIVERY_FAILED",
                "HIGH",
                component="spool",
                symbol_id=symbol_id,
                timeframe=code,
                candles=pending_count,
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
