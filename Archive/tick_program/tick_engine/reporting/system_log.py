"""Small direct writer for operator-facing system diagnostics.

Scheduled child jobs disable their own file logging so their normal progress can
be mirrored into operation.log by the supervisor. Connection/auth diagnostics
still need one stable destination, so this module appends directly to system.log.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_AREA_WIDTH = 16
_ITEM_WIDTH = 28
_MAX_BYTES = 10_000_000
_BACKUP_COUNT = 5


def _clean(value: Any) -> str:
    text = str(value).replace("\r\n", " ").replace("\n", " ").strip()
    return text


def system_line(area: str, item: str, detail: str = "") -> str:
    return f"{_clean(area):<{_AREA_WIDTH}} | {_clean(item):<{_ITEM_WIDTH}} | {_clean(detail)}"


@contextmanager
def _process_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
    except FileNotFoundError:
        return
    oldest = path.with_name(f"{path.name}.{_BACKUP_COUNT}")
    oldest.unlink(missing_ok=True)
    for index in range(_BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))


def write_system_event(area: str, item: str, detail: str = "", *, level: str = "INFO") -> None:
    """Append one aligned system diagnostic line without raising into ingest code."""
    try:
        from tick_engine.settings import SYSTEM_LOG, ensure_runtime_dirs

        ensure_runtime_dirs()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{stamp} | {str(level or 'INFO').upper():<7} | {system_line(area, item, detail)}"
        with _LOCK:
            lock_path = SYSTEM_LOG.with_name(f".{SYSTEM_LOG.name}.lock")
            with _process_file_lock(lock_path):
                _rotate_if_needed(SYSTEM_LOG)
                with SYSTEM_LOG.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
    except Exception:
        return
