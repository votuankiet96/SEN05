"""Distributed DB advisory locks and file-based job coordination.

Merged from:
  tick_program/engine/common/locks.py   — SEN.ActiveTask DB advisory lock
  tick_program/job_control.py           — file-based job locks and cancel signals
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyodbc

from tick_engine.utils_support.proc_utils import is_pid_alive

CANCEL_ENV = "TICK_ENGINE_CANCEL_FILE"

# ---------------------------------------------------------------------------
# DB advisory lock (SEN.ActiveTask)
# ---------------------------------------------------------------------------

_lock_cache: dict = {"task_name": None, "locked": False, "checked_at": 0.0}
_LOCK_CACHE_TTL = 30.0


def acquire(task_name: str, duration_min: int = 90, payload: str | None = None) -> bool:
    """Try to acquire a DB advisory lock via SEN.ActiveTask INSERT."""
    from tick_engine.data_storage.db_connector import get_connection

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM SEN.ActiveTask WHERE TaskName = ? AND ExpiresAt <= SYSUTCDATETIME()",
            (task_name,),
        )
        if payload is None:
            cursor.execute(
                "INSERT INTO SEN.ActiveTask (TaskName, ExpiresAt) VALUES (?, DATEADD(minute, ?, SYSUTCDATETIME()))",
                (task_name, duration_min),
            )
        else:
            cursor.execute(
                "INSERT INTO SEN.ActiveTask (TaskName, ExpiresAt, Payload) VALUES (?, DATEADD(minute, ?, SYSUTCDATETIME()), ?)",
                (task_name, duration_min, payload),
            )
        conn.commit()
        return True
    except pyodbc.IntegrityError:
        return False
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def release(task_name: str) -> None:
    """Release a DB advisory lock by deleting the row from SEN.ActiveTask."""
    import logging as _logging

    from tick_engine.data_storage.db_connector import get_connection

    _log = _logging.getLogger(__name__)
    for attempt in range(2):
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM SEN.ActiveTask WHERE TaskName = ?", (task_name,))
            conn.commit()
            if cursor.rowcount == 0:
                _log.warning("release(%s): DELETE matched 0 rows — lock may have already expired", task_name)
            if _lock_cache["task_name"] == task_name:
                _lock_cache["checked_at"] = 0.0
            return
        except Exception as exc:
            if attempt == 0:
                _log.warning("release(%s): attempt 1 failed (%s) — retrying", task_name, exc)
            else:
                _log.error("release(%s): attempt 2 failed (%s) — ghost lock may expire by TTL", task_name, exc)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    if _lock_cache["task_name"] == task_name:
        _lock_cache["checked_at"] = 0.0


def renew(task_name: str, duration_min: int = 90, payload: str | None = None) -> bool:
    """Extend a held DB advisory lock's expiry."""
    from tick_engine.data_storage.db_connector import get_connection

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if payload is None:
            cursor.execute(
                "UPDATE SEN.ActiveTask SET ExpiresAt = DATEADD(minute, ?, SYSUTCDATETIME()) WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()",
                (duration_min, task_name),
            )
        else:
            cursor.execute(
                "UPDATE SEN.ActiveTask SET ExpiresAt = DATEADD(minute, ?, SYSUTCDATETIME()), Payload = ? WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()",
                (duration_min, payload, task_name),
            )
        conn.commit()
        updated = cursor.rowcount > 0
    except Exception:
        updated = False
    finally:
        if conn is not None:
            conn.close()
    if _lock_cache["task_name"] == task_name:
        _lock_cache["checked_at"] = 0.0
    return updated


def update_payload(
    task_name: str,
    payload: str,
    *,
    expire_after_seconds: int | None = None,
) -> bool:
    """Write a small signal into SEN.ActiveTask.Payload without changing the holder."""
    from tick_engine.data_storage.db_connector import get_connection

    expire_seconds = None
    if expire_after_seconds is not None:
        try:
            expire_seconds = max(1, int(expire_after_seconds))
        except Exception:
            expire_seconds = None
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if expire_seconds is None:
            cursor.execute(
                "UPDATE SEN.ActiveTask SET Payload = ? WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()",
                (payload, task_name),
            )
        else:
            cursor.execute(
                """
                UPDATE SEN.ActiveTask
                SET Payload = ?,
                    ExpiresAt = CASE
                        WHEN ExpiresAt > DATEADD(second, ?, SYSUTCDATETIME())
                            THEN DATEADD(second, ?, SYSUTCDATETIME())
                        ELSE ExpiresAt
                    END
                WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
                """,
                (payload, expire_seconds, expire_seconds, task_name),
            )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def is_locked(task_name: str) -> bool:
    """Check if a DB advisory lock is held; fail-open on DB errors."""
    from tick_engine.data_storage.db_connector import get_connection

    now = time.monotonic()
    if _lock_cache["task_name"] == task_name and now - _lock_cache["checked_at"] < _LOCK_CACHE_TTL:
        return _lock_cache["locked"]

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM SEN.ActiveTask WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()",
            (task_name,),
        )
        result = cursor.fetchone() is not None
    except Exception:
        result = False
    finally:
        if conn is not None:
            conn.close()

    _lock_cache.update({"task_name": task_name, "locked": result, "checked_at": now})
    return result


def cleanup_expired() -> int:
    """Delete expired advisory lock rows; returns count deleted."""
    from tick_engine.data_storage.db_connector import get_connection

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM SEN.ActiveTask WHERE ExpiresAt <= SYSUTCDATETIME()")
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    except Exception:
        return 0
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# File-based job locks and cancel signals
# ---------------------------------------------------------------------------


class CancelRequested(RuntimeError):
    """Raised by long-running jobs when their cancel sentinel is present."""


class JobLockConflict(RuntimeError):
    """Raised when another process owns a lightweight job resource lock."""


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return text.strip("._") or "job"


def _run_dir() -> Path:
    from tick_engine.settings import RUN_DIR
    return RUN_DIR


def cancel_file_for(label: str) -> Path:
    path = _run_dir() / "cancel" / f"{_safe_name(label)}.cancel"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clear_cancel_file(path: Path | str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def write_cancel_file(path: Path | str | None, reason: str = "cancel requested") -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "reason": reason,
                "requested_at_utc": datetime.now(timezone.utc).isoformat(),
                "requested_by_pid": os.getpid(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def current_cancel_file() -> Path | None:
    raw = os.environ.get(CANCEL_ENV, "").strip()
    return Path(raw) if raw else None


def cancel_requested(path: Path | str | None = None) -> bool:
    target = Path(path) if path else current_cancel_file()
    return bool(target and target.exists())


def raise_if_cancelled(path: Path | str | None = None) -> None:
    target = Path(path) if path else current_cancel_file()
    if target and target.exists():
        raise CancelRequested(f"cancel requested via {target}")


def _lock_path(resource: str) -> Path:
    path = _run_dir() / "locks" / f"{_safe_name(resource)}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def job_lock_status(resource: str) -> dict[str, Any]:
    path = _lock_path(resource)
    info = _read_lock(path) if path.exists() else {}
    pid = info.get("pid")
    lock_host = info.get("host")
    current_host = socket.gethostname()
    same_host = not lock_host or str(lock_host).lower() == current_host.lower()
    pid_alive = (isinstance(pid, int) and is_pid_alive(pid)) if same_host else None
    active = bool(path.exists() and (pid_alive if same_host else True))
    return {
        "resource": resource,
        "path": str(path),
        "exists": path.exists(),
        "active": active,
        "stale": bool(path.exists() and same_host and not pid_alive),
        "pid_alive": pid_alive,
        "owner": info.get("label"),
        "pid": pid,
        "host": lock_host,
        "current_host": current_host,
        "remote": bool(path.exists() and not same_host),
        "started_at_utc": info.get("started_at_utc"),
        "payload": info,
    }


def lock_active(resource: str) -> bool:
    path = _lock_path(resource)
    if not path.exists():
        return False
    info = _read_lock(path)
    pid = info.get("pid")
    lock_host = info.get("host")
    current_host = socket.gethostname()
    if lock_host and str(lock_host).lower() != current_host.lower():
        return True
    if isinstance(pid, int) and is_pid_alive(pid):
        return True
    clear_cancel_file(path)
    return False


class exclusive_job_lock(AbstractContextManager["exclusive_job_lock"]):
    """File-existence lock with stale-PID cleanup for coarse job resources."""

    def __init__(self, resource: str, *, label: str | None = None) -> None:
        self.resource = resource
        self.label = label or resource
        self.path = _lock_path(resource)
        self.acquired = False

    def __enter__(self) -> "exclusive_job_lock":
        payload = {
            "resource": self.resource,
            "label": self.label,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        for _attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = _read_lock(self.path)
                pid = info.get("pid")
                lock_host = info.get("host")
                current_host = socket.gethostname()
                if lock_host and str(lock_host).lower() != current_host.lower():
                    owner = info.get("label") or self.resource
                    raise JobLockConflict(
                        f"{self.resource} is busy on host={lock_host}; owner={owner} pid={pid}"
                    )
                if isinstance(pid, int) and is_pid_alive(pid):
                    owner = info.get("label") or self.resource
                    raise JobLockConflict(f"{self.resource} is busy; owner={owner} pid={pid}")
                try:
                    self.path.unlink()
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
            self.acquired = True
            return self
        raise JobLockConflict(f"{self.resource} lock is busy")

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self.acquired = False
        return False
