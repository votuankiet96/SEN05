"""Non-blocking, Windows-safe file sinks for the four canonical log streams."""

from __future__ import annotations

import atexit
import gzip
import itertools
import logging
import os
import queue
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from core_engine.settings import (
    ALERTS_LOG,
    HISTORICAL_LOG,
    LIVE_LOG,
    LOG_ARCHIVE_DIR,
    LOG_DIR,
    LOG_EMERGENCY_DIR,
    LOG_LOCK_DIR,
    LOGGING,
    RUN_DIR,
    SYSTEM_LOG,
)
from core_engine.util.logkit.formatter import OperatorFormatter
from core_engine.util.runtime_state import atomic_write_json

_STREAM_PATHS = {
    "live": LIVE_LOG,
    "historical": HISTORICAL_LOG,
    "system": SYSTEM_LOG,
    "alerts": ALERTS_LOG,
}
_LOCAL_LOCKS: dict[str, threading.RLock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()
_MANAGER: "SinkManager | None" = None
_MANAGER_GUARD = threading.Lock()
_EVENT_SEQUENCE = itertools.count(1)


def process_role() -> str:
    value = str(os.environ.get("DP_PROCESS_ROLE") or "").strip().lower()
    return value or "unknown"


def stream_path(stream: str) -> Path:
    return Path(_STREAM_PATHS.get(str(stream).lower(), SYSTEM_LOG))


def _local_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).lower()
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _cross_process_lock(name: str, *, timeout: float = 2.0) -> Iterator[None]:
    """Lock one byte in a runtime lock file; the OS releases it on process exit."""
    LOG_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    path = LOG_LOCK_DIR / f"{safe}.lock"
    handle = path.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + max(0.05, float(timeout))
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out waiting for log lock {safe}")
                    time.sleep(0.01)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out waiting for log lock {safe}")
                    time.sleep(0.01)
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        handle.close()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def _registry_path() -> Path:
    return RUN_DIR / "log_sinks" / f"{process_role()}.{os.getpid()}.json"


def _register_streams(streams: set[str], *, status: str = "running", error: str | None = None) -> None:
    paths = [str(stream_path(name).resolve()) for name in sorted(streams)]
    payload = {
        "schema": 2,
        "role": process_role(),
        "pid": os.getpid(),
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "streams": sorted(streams),
        "paths": paths,
        "error": error,
    }
    try:
        _atomic_json(_registry_path(), payload)
    except Exception:
        pass


def _archive_target(path: Path, modified: datetime) -> Path:
    day_dir = LOG_ARCHIVE_DIR / modified.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return day_dir / f"{path.stem}.{stamp}.{os.getpid()}.log"


def _compress(path: Path) -> Path:
    target = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=6) as dest:
        shutil.copyfileobj(source, dest, length=1024 * 1024)
    path.unlink(missing_ok=True)
    return target


def _rotate_if_needed(path: Path, incoming_bytes: int, *, max_bytes: int) -> Path | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    today = datetime.now(timezone.utc).date()
    if modified.date() == today and stat.st_size + incoming_bytes < max_bytes:
        return None
    target = _archive_target(path, modified)
    os.replace(path, target)
    try:
        return _compress(target)
    except Exception:
        return target


def _emergency_line(line: str, reason: BaseException | str) -> None:
    """Best-effort fallback that never raises back into the data engine."""
    try:
        LOG_EMERGENCY_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_EMERGENCY_DIR / f"{process_role()}.{os.getpid()}.log"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{stamp} UTC | LOGGING FALLBACK | {reason} | {line}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.write(2, (f"DP logging failure: {reason}\n").encode("ascii", errors="replace"))
        except Exception:
            pass


def _append_line(path: Path, line: str, *, durable: bool) -> None:
    encoded = (line.rstrip("\r\n") + "\n").encode("utf-8", errors="replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _local_lock(path)
    with lock:
        with _cross_process_lock(path.name):
            _rotate_if_needed(
                path,
                len(encoded),
                max_bytes=max(1, int(LOGGING.max_file_mb)) * 1024 * 1024,
            )
            with path.open("ab", buffering=0) as handle:
                handle.write(encoded)
                if durable:
                    os.fsync(handle.fileno())


def _ensure_stream_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _local_lock(path):
        with _cross_process_lock(path.name):
            if not path.exists():
                with path.open("xb"):
                    pass


def cleanup_archives(*, now: datetime | None = None) -> dict[str, Any]:
    """Apply age retention and a hard total-size budget without touching active logs."""
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max(1, int(LOGGING.retention_days)))
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    kept: list[Path] = []
    try:
        with _cross_process_lock("retention", timeout=5.0):
            for path in LOG_ARCHIVE_DIR.rglob("*") if LOG_ARCHIVE_DIR.exists() else []:
                if not path.is_file():
                    continue
                try:
                    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                    if modified < cutoff:
                        path.unlink()
                        deleted.append(str(path))
                    else:
                        kept.append(path)
                except Exception as exc:
                    failed.append({"path": str(path), "error": str(exc)})

            active = [path for path in _STREAM_PATHS.values() if Path(path).exists()]
            all_files = [*active, *kept]
            total_bytes = sum(path.stat().st_size for path in all_files if path.exists())
            budget = max(100, int(LOGGING.disk_budget_mb)) * 1024 * 1024
            if total_bytes > budget:
                candidates = sorted(
                    (path for path in kept if path.exists()),
                    key=lambda item: item.stat().st_mtime,
                )
                for path in candidates:
                    if total_bytes <= int(budget * 0.9):
                        break
                    try:
                        size = path.stat().st_size
                        path.unlink()
                        total_bytes -= size
                        deleted.append(str(path))
                    except Exception as exc:
                        failed.append({"path": str(path), "error": str(exc)})
    except Exception as exc:
        failed.append({"path": str(LOG_ARCHIVE_DIR), "error": str(exc)})
        total_bytes = 0

    payload = {
        "generated_at": current.isoformat(),
        "retention_days": int(LOGGING.retention_days),
        "disk_budget_mb": int(LOGGING.disk_budget_mb),
        "deleted": deleted,
        "failed": failed,
        "total_bytes": int(total_bytes),
    }
    try:
        _atomic_json(RUN_DIR / "log_retention_state.json", payload)
    except Exception:
        pass
    return payload


class SinkManager:
    """One bounded queue and one writer thread for each process."""

    def __init__(self) -> None:
        self.formatter = OperatorFormatter()
        self.queue: queue.Queue[logging.LogRecord | None] = queue.Queue(
            maxsize=max(100, int(LOGGING.queue_size))
        )
        self._streams: set[str] = set()
        self._stream_guard = threading.Lock()
        self._started = threading.Event()
        self._closing = threading.Event()
        self._last_cleanup = 0.0
        self._submitted = 0
        self._written = 0
        self._fallback_writes = 0
        self._queue_full = 0
        self._last_error: str | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="dp-log-writer",
            daemon=True,
        )
        self._thread.start()
        self._started.wait(timeout=2.0)

    def register(self, stream: str) -> None:
        normalized = stream if stream in _STREAM_PATHS else "system"
        with self._stream_guard:
            self._streams.add(normalized)
            if normalized != "alerts":
                self._streams.add("alerts")
            for name in self._streams:
                try:
                    _ensure_stream_file(stream_path(name))
                except Exception as exc:
                    self._last_error = str(exc)
            _register_streams(self._streams, error=self._last_error)

    def submit(self, record: logging.LogRecord) -> None:
        # A CRITICAL event is an operational page, not ordinary telemetry.
        # Persist it to the canonical log and SQLite outbox before returning;
        # only the HTTP delivery remains asynchronous.
        if record.levelno >= logging.CRITICAL:
            self._write_direct(record)
            return
        if self._closing.is_set():
            self._write_direct(record)
            return
        self._submitted += 1
        wait_seconds = max(0, int(LOGGING.queue_wait_ms)) / 1000.0
        try:
            self.queue.put(record, timeout=wait_seconds)
        except queue.Full:
            self._queue_full += 1
            self._write_direct(record)
        except Exception as exc:
            self._last_error = str(exc)
            self._write_direct(record)

    def _write_direct(self, record: logging.LogRecord) -> None:
        try:
            self._write_record(record)
        except Exception as exc:
            self._fallback_writes += 1
            self._last_error = str(exc)
            try:
                line = self.formatter.format(record)
            except Exception:
                line = str(record.getMessage())
            _emergency_line(line, exc)

    def _write_record(self, record: logging.LogRecord) -> None:
        line = self.formatter.format(record)
        stream = str(getattr(record, "dp_stream", "system")).lower()
        if stream not in _STREAM_PATHS:
            stream = "system"
        durable = record.levelno >= logging.ERROR
        _append_line(stream_path(stream), line, durable=durable)
        if record.levelno >= logging.WARNING and stream != "alerts":
            _append_line(ALERTS_LOG, line, durable=durable)
        self._written += 1
        if record.levelno >= logging.CRITICAL and not getattr(record, "dp_skip_notify", False):
            try:
                from core_engine.util.notify.critical_outbox import enqueue_critical_alert

                enqueue_critical_alert(line)
            except Exception as exc:
                _emergency_line(line, f"critical outbox unavailable: {exc}")

    def _run(self) -> None:
        self._started.set()
        try:
            cleanup_archives()
        except Exception:
            pass
        self._last_cleanup = time.monotonic()
        while True:
            record = self.queue.get()
            try:
                if record is None:
                    return
                self._write_direct(record)
                if time.monotonic() - self._last_cleanup >= 3600:
                    cleanup_archives()
                    self._last_cleanup = time.monotonic()
            finally:
                self.queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.queue.unfinished_tasks == 0

    def close(self, timeout: float = 5.0) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        self.flush(timeout=max(0.1, timeout))
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            try:
                self.queue.put(None, timeout=0.5)
            except Exception:
                return
        self._thread.join(timeout=max(0.1, timeout))
        _register_streams(self._streams, status="stopped", error=self._last_error)

    def status(self) -> dict[str, Any]:
        return {
            "role": process_role(),
            "pid": os.getpid(),
            "writer_alive": self._thread.is_alive(),
            "queue_size": self.queue.qsize(),
            "queue_capacity": self.queue.maxsize,
            "queue_full_count": self._queue_full,
            "submitted": self._submitted,
            "written": self._written,
            "fallback_writes": self._fallback_writes,
            "last_error": self._last_error,
            "streams": sorted(self._streams),
        }


class SinkQueueHandler(logging.Handler):
    """A logger-facing handler that cannot propagate failures to callers."""

    def __init__(self, manager: SinkManager, *, stream: str, component: str) -> None:
        super().__init__(level=logging.DEBUG)
        self.manager = manager
        self.stream = stream
        self.component = component
        manager.register(stream)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not getattr(record, "dp_event_id", None):
                record.dp_event_id = (
                    f"{record.process:x}-{time.time_ns():x}-{next(_EVENT_SEQUENCE):x}"
                )
            record.dp_stream = self.stream
            record.dp_component = self.component
            record.dp_role = process_role()
            self.manager.submit(record)
        except Exception as exc:
            _emergency_line(str(record.getMessage()), exc)


def sink_manager() -> SinkManager:
    global _MANAGER
    manager = _MANAGER
    if manager is None:
        with _MANAGER_GUARD:
            manager = _MANAGER
            if manager is None:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                manager = SinkManager()
                _MANAGER = manager
    return manager


def flush_logs(timeout: float = 5.0) -> bool:
    manager = _MANAGER
    return True if manager is None else manager.flush(timeout)


def shutdown_logging(timeout: float = 5.0) -> None:
    manager = _MANAGER
    if manager is not None:
        manager.close(timeout)


def logging_status() -> dict[str, Any]:
    manager = _MANAGER
    return {
        "manager_started": manager is not None,
        "active_files": {name: str(path) for name, path in _STREAM_PATHS.items()},
        "manager": manager.status() if manager is not None else None,
    }


atexit.register(shutdown_logging)


__all__ = [
    "SinkManager",
    "SinkQueueHandler",
    "cleanup_archives",
    "flush_logs",
    "logging_status",
    "process_role",
    "shutdown_logging",
    "sink_manager",
    "stream_path",
]
