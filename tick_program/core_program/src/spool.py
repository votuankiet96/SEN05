"""Durable SQLite outbox, in-memory batcher, and file-based job locks.

Everything a job needs to (a) buffer ticks and flush them to SQL without
losing data on a SQL outage, and (b) make sure only one process at a time
touches the cTrader history API or a shared runtime resource.
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.pipeline import TickRecord

CANCEL_ENV = "TICK_ENGINE_CANCEL_FILE"


# ---------------------------------------------------------------------------
# Process liveness (cross-platform)
# ---------------------------------------------------------------------------


def is_pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        from ctypes import wintypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def terminate_pid(pid: int, timeout: float = 10.0) -> bool:
    if not is_pid_alive(pid):
        return True
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=max(1.0, timeout), check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.3)
    if is_pid_alive(pid) and os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.5)
    return not is_pid_alive(pid)


# ---------------------------------------------------------------------------
# File-based job locks and cancel signals
# ---------------------------------------------------------------------------


class CancelRequested(RuntimeError):
    """Raised by long-running jobs when their cancel sentinel is present."""


class JobLockConflict(RuntimeError):
    """Raised when another process owns a lightweight job resource lock."""


def _run_dir() -> Path:
    from src.configuration import RUN_DIR

    return RUN_DIR


def cancel_file_for(label: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "_.-" else "_" for c in label).strip("._") or "job"
    path = _run_dir() / "cancel" / f"{safe}.cancel"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clear_cancel_file(path: Path | str | None) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def write_cancel_file(path: Path | str | None, reason: str = "cancel requested") -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"reason": reason, "requested_at_utc": datetime.now(timezone.utc).isoformat(),
                    "requested_by_pid": os.getpid()}, sort_keys=True),
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
    safe = "".join(c if c.isalnum() or c in "_.-" else "_" for c in resource).strip("._") or "job"
    path = _run_dir() / "locks" / f"{safe}.lock"
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
    pid, lock_host = info.get("pid"), info.get("host")
    current_host = socket.gethostname()
    same_host = not lock_host or str(lock_host).lower() == current_host.lower()
    pid_alive = (isinstance(pid, int) and is_pid_alive(pid)) if same_host else None
    return {
        "resource": resource, "path": str(path), "exists": path.exists(),
        "active": bool(path.exists() and (pid_alive if same_host else True)),
        "owner": info.get("label"), "pid": pid, "host": lock_host,
    }


class exclusive_job_lock(AbstractContextManager["exclusive_job_lock"]):
    """File-existence lock with stale-PID cleanup for coarse job resources.

    ``wait_seconds`` (default 0, i.e. fail immediately) polls for up to that
    long when the lock is held by another live owner before raising
    ``JobLockConflict`` — a stale lock (dead PID, or same-host cleanup) is
    always reclaimed immediately regardless of ``wait_seconds``.
    """

    def __init__(self, resource: str, *, label: str | None = None, wait_seconds: float = 0.0) -> None:
        self.resource = resource
        self.label = label or resource
        self.path = _lock_path(resource)
        self.acquired = False
        self.wait_seconds = max(0.0, float(wait_seconds))

    def __enter__(self) -> "exclusive_job_lock":
        payload = {
            "resource": self.resource, "label": self.label, "pid": os.getpid(),
            "host": socket.gethostname(), "started_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = _read_lock(self.path)
                pid, lock_host = info.get("pid"), info.get("host")
                current_host = socket.gethostname()
                busy = (lock_host and str(lock_host).lower() != current_host.lower()) or (
                    isinstance(pid, int) and is_pid_alive(pid)
                )
                if not busy:
                    self.path.unlink(missing_ok=True)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JobLockConflict(
                        f"{self.resource} is busy; owner={info.get('label')} pid={pid} host={lock_host or current_host}"
                    )
                time.sleep(min(1.0, remaining))
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
            self.acquired = True
            return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False
        return False


# ---------------------------------------------------------------------------
# Durable SQLite spool + in-memory batcher
# ---------------------------------------------------------------------------


class TickSpool:
    """Durable local overflow queue used when SQL Server insert fails."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tick_spool ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, event_hash BLOB NOT NULL UNIQUE, "
                "payload_json TEXT NOT NULL, created_at_utc TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tick_spool_quarantine ("
                "seq INTEGER PRIMARY KEY, event_hash BLOB, payload_json TEXT NOT NULL, "
                "created_at_utc TEXT, quarantined_at_utc TEXT NOT NULL, error_text TEXT NOT NULL)"
            )
            conn.commit()

    def append_many(self, records: list[TickRecord]) -> int:
        if not records:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (sqlite3.Binary(r.event_hash or b""), json.dumps(r.to_json_dict(), separators=(",", ":"), sort_keys=True), now)
            for r in records
        ]
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "INSERT OR IGNORE INTO tick_spool (event_hash, payload_json, created_at_utc) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
            return cursor.rowcount

    def read_batch(self, limit: int) -> list[tuple[int, TickRecord]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT seq, event_hash, payload_json, created_at_utc FROM tick_spool ORDER BY seq LIMIT ?",
                (int(limit),),
            ).fetchall()
            valid: list[tuple[int, TickRecord]] = []
            quarantined_at = datetime.now(timezone.utc).isoformat()
            for seq, event_hash, payload, created_at in rows:
                try:
                    record = TickRecord.from_json_dict(json.loads(payload))
                except Exception as exc:
                    conn.execute(
                        "INSERT OR REPLACE INTO tick_spool_quarantine "
                        "(seq, event_hash, payload_json, created_at_utc, quarantined_at_utc, error_text) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (int(seq), event_hash, str(payload), created_at, quarantined_at,
                         f"{type(exc).__name__}: {exc}"[:1000]),
                    )
                    conn.execute("DELETE FROM tick_spool WHERE seq = ?", (int(seq),))
                    continue
                valid.append((int(seq), record))
            conn.commit()
            return valid

    def delete_through(self, seq: int) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM tick_spool WHERE seq <= ?", (int(seq),))
            conn.commit()
            return cursor.rowcount

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM tick_spool").fetchone()
        return int(row[0] if row else 0)

    def quarantine_count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM tick_spool_quarantine").fetchone()
        return int(row[0] if row else 0)

    def clear(self) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM tick_spool")
            deleted = cursor.rowcount
            conn.commit()
        return int(deleted if deleted is not None and deleted >= 0 else 0)


class TickBatcher:
    """Collect ticks, write them in batches, and spool on SQL failures."""

    def __init__(
        self, store: object, spool: TickSpool, batch_size: int, flush_seconds: float,
        on_inserted: Callable[[list[TickRecord], int], None] | None = None,
        on_spooled: Callable[[list[TickRecord], int, Exception], None] | None = None,
    ) -> None:
        self.store = store
        self.spool = spool
        self.batch_size = int(batch_size)
        self.flush_seconds = float(flush_seconds)
        self.on_inserted = on_inserted
        self.on_spooled = on_spooled
        self._pending: list[TickRecord] = []
        self._last_flush_monotonic = time.monotonic()
        self.rows_inserted = 0
        self.rows_spooled = 0

    def add(self, record: TickRecord) -> None:
        self._pending.append(record)
        if self.should_flush():
            self.flush()

    def should_flush(self) -> bool:
        if len(self._pending) >= self.batch_size:
            return True
        return (time.monotonic() - self._last_flush_monotonic) >= self.flush_seconds

    def flush(self) -> None:
        if not self._pending:
            self._last_flush_monotonic = time.monotonic()
            return
        records = list(self._pending)
        try:
            inserted = self.store.insert_ticks(records)
        except Exception as exc:
            try:
                spooled = self.spool.append_many(records)
            except Exception as spool_exc:
                self._last_flush_monotonic = time.monotonic()
                raise RuntimeError(
                    f"SQL tick insert failed ({exc}); durable spool also failed ({spool_exc})"
                ) from spool_exc
            self._pending.clear()
            self.rows_spooled += spooled
            if self.on_spooled is not None:
                self.on_spooled(records, spooled, exc)
        else:
            self._pending.clear()
            self.rows_inserted += inserted
            if self.on_inserted is not None:
                inserted_keys = getattr(self.store, "last_inserted_keys", None)
                kept = records if inserted_keys is None else [
                    r for r in records if r.sql_dedup_key() in inserted_keys
                ]
                self.on_inserted(kept, inserted)
        finally:
            self._last_flush_monotonic = time.monotonic()
