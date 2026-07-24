"""Runtime singleton lock and graceful handoff for cTrader tick live ingest."""

from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from data_provider.common import locks
from data_provider.paths import RUN_DIR, ensure_runtime_dirs

TICK_LIVE_RUNTIME_LOCK = "tick_live_runtime"
TICK_LIVE_SHUTDOWN_SIGNAL = "shutdown_requested=1"
TICK_LIVE_PID = RUN_DIR / "tick_live_runtime.pid"


def _get_connection():
    from modules.db_connector import get_connection

    return get_connection()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_lock_active() -> bool:
    conn = None
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM SEN.ActiveTask
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
            """,
            (TICK_LIVE_RUNTIME_LOCK,),
        )
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def request_tick_live_shutdown(logger: logging.Logger) -> bool:
    ok = locks.update_payload(TICK_LIVE_RUNTIME_LOCK, TICK_LIVE_SHUTDOWN_SIGNAL)
    if ok:
        logger.info("[TICK_LOCK] shutdown signal written to existing tick live runtime lock")
    else:
        logger.warning("[TICK_LOCK] could not write shutdown signal; lock may already be gone")
    return ok


def is_tick_live_shutdown_requested() -> bool:
    conn = None
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT Payload FROM SEN.ActiveTask
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
            """,
            (TICK_LIVE_RUNTIME_LOCK,),
        )
        row = cursor.fetchone()
        return bool(row and row[0] and TICK_LIVE_SHUTDOWN_SIGNAL in str(row[0]))
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _acquire_local_pid_file(logger: logging.Logger) -> bool:
    ensure_runtime_dirs()
    while True:
        try:
            fd = os.open(str(TICK_LIVE_PID), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
            return True
        except FileExistsError:
            try:
                existing_pid = int(TICK_LIVE_PID.read_text(encoding="utf-8").strip())
            except Exception:
                existing_pid = 0
            if existing_pid and _pid_is_alive(existing_pid):
                logger.error("[TICK_LOCK] local tick live process already running pid=%d", existing_pid)
                return False
            try:
                TICK_LIVE_PID.unlink()
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.error("[TICK_LOCK] could not remove stale pid file: %s", exc)
                return False


def _release_local_pid_file() -> None:
    try:
        existing_pid = int(TICK_LIVE_PID.read_text(encoding="utf-8").strip())
    except Exception:
        existing_pid = 0
    if existing_pid == os.getpid():
        try:
            TICK_LIVE_PID.unlink()
        except FileNotFoundError:
            pass


@dataclass
class TickLiveRuntimeGuard:
    logger: logging.Logger
    lock_duration_min: int = 60
    heartbeat_seconds: int = 900
    handoff_wait_seconds: int = 60
    allow_handoff: bool = True

    def __post_init__(self) -> None:
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self.acquired = False

    def acquire(self) -> bool:
        locks.cleanup_expired()
        if not locks.acquire(TICK_LIVE_RUNTIME_LOCK, duration_min=self.lock_duration_min):
            if not self.allow_handoff:
                self.logger.error("[TICK_LOCK] tick live runtime is already active; startup aborted")
                return False
            self.logger.info("[TICK_LOCK] old tick live instance active - requesting graceful shutdown")
            request_tick_live_shutdown(self.logger)
            deadline = time.monotonic() + self.handoff_wait_seconds
            while time.monotonic() < deadline:
                if not _is_lock_active():
                    break
                time.sleep(1)
            locks.cleanup_expired()
            if not locks.acquire(TICK_LIVE_RUNTIME_LOCK, duration_min=self.lock_duration_min):
                self.logger.error("[TICK_LOCK] existing tick live lock still active after handoff wait")
                return False
            self.logger.info("[TICK_LOCK] tick live runtime lock acquired after handoff")

        if not _acquire_local_pid_file(self.logger):
            locks.release(TICK_LIVE_RUNTIME_LOCK)
            return False

        self.acquired = True
        atexit.register(self.release)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="tick-live-lock-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        return True

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_seconds):
            if not locks.renew(TICK_LIVE_RUNTIME_LOCK, duration_min=self.lock_duration_min):
                self.logger.warning("[TICK_LOCK] could not renew tick live runtime lock")

    def release(self) -> None:
        if not self.acquired:
            return
        self.acquired = False
        self._heartbeat_stop.set()
        locks.release(TICK_LIVE_RUNTIME_LOCK)
        _release_local_pid_file()


def stale_pid_status(pid_path: Path = TICK_LIVE_PID) -> dict[str, object]:
    if not pid_path.exists():
        return {"exists": False, "pid": None, "alive": False, "path": str(pid_path)}
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:
        pid = None
    return {
        "exists": True,
        "pid": pid,
        "alive": _pid_is_alive(pid or 0),
        "path": str(pid_path),
    }
