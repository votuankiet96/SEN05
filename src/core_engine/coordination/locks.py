"""Database-backed lock and coordination primitives for the new DP engine.

This file replaces the old split between `common/locks.py` and `tv/coord.py`.
It intentionally keeps one responsibility: coordinate long-running data jobs
through the `SEN.ActiveTask` table.

Core concepts:
- generic advisory lock with TTL
- historical job singleton with heartbeat
- live runtime lock and graceful shutdown signal
- short live batch window so historical pulls can yield to live fetching

The module is written as refactor material for `core_engine`; it does not pull
dashboard, CLI, setup, or checker concerns into the new backend.
"""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

try:
    import pyodbc
except Exception:  # pragma: no cover - import is optional for static review
    pyodbc = None  # type: ignore[assignment]

from core_engine.logkit.formatters import operation_line


ConnectionFactory = Callable[[], Any]

HISTORICAL_JOB_LOCK = "tv_historical_job"
LIVE_BATCH_LOCK = "tv_live_batch"
LIVE_RUNTIME_LOCK = "ws_live_runtime"
DP_PROGRAM_LOCK = "dp_program_supervisor"
WAREHOUSE_MAINTENANCE_LOCK = "warehouse_maintenance"

DEFAULT_LOCK_CACHE_TTL_SEC = 30.0
DEFAULT_HISTORICAL_TTL_MIN = 240
DEFAULT_LIVE_RUNTIME_TTL_MIN = 10
DEFAULT_LIVE_BATCH_TTL_MIN = 10
DEFAULT_HEARTBEAT_SEC = 60.0

LIVE_BATCH_INTERVAL_MIN = 5.0
HISTORICAL_YIELD_BEFORE_BATCH_SEC = 75.0
LIVE_SHUTDOWN_GRACE_SEC = 60
WS_LIVE_SHUTDOWN_GRACE_SECONDS = LIVE_SHUTDOWN_GRACE_SEC


@dataclass(frozen=True)
class LockRecord:
    task_name: str
    started_at: Any | None
    expires_at: Any | None
    payload: str

    @property
    def meta(self) -> dict[str, str]:
        return parse_payload(self.payload)


@dataclass
class LockLease:
    task_name: str
    owner: str
    run_id: str
    owner_prefix: str
    stop_event: threading.Event
    released: bool = False


def default_connection_factory() -> Any:
    """Lazy default import so this module can be reviewed before wiring."""
    from core_engine.warehouse.connection import get_connection

    return get_connection()


def utc_stamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def parse_payload(payload: str | None) -> dict[str, str]:
    if not payload:
        return {}
    parsed: dict[str, str] = {}
    for part in str(payload).split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            parsed[part] = "1"
            continue
        key, value = part.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def format_payload(meta: dict[str, Any], *, order: list[str] | None = None) -> str:
    ordered = order or [
        "run",
        "kind",
        "owner",
        "host",
        "pid",
        "started",
        "heartbeat",
        "shutdown_requested",
        "shutdown_by_host",
        "shutdown_by_pid",
        "shutdown_at",
    ]
    parts: list[str] = []
    used: set[str] = set()
    for key in ordered:
        value = meta.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
            used.add(key)
    for key in sorted(meta):
        if key in used:
            continue
        value = meta.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return ";".join(parts)


def _local_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False

    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return bool(ok) and exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


class LockCoordinator:
    """Small facade over SEN.ActiveTask for engine-level coordination."""

    def __init__(
        self,
        connection_factory: ConnectionFactory | None = None,
        *,
        logger: logging.Logger | None = None,
        pid_alive: Callable[[int], bool] | None = None,
        host: str | None = None,
        pid: int | None = None,
    ) -> None:
        self.connection_factory = connection_factory or default_connection_factory
        self.logger = logger or logging.getLogger(__name__)
        self.pid_alive = pid_alive or _local_pid_alive
        self.host = host or socket.gethostname()
        self.pid = pid or os.getpid()
        # One identifier per LockCoordinator instance (effectively one per
        # process, since _DEFAULT below is the module-wide singleton every
        # public function goes through). Used to fence renew()/release()
        # against a stale owner - see scripts/sql/09_migration_lock_fencing.sql.
        self.owner_id = uuid.uuid4().hex
        self._cache: dict[str, dict[str, float | bool]] = {}
        self._local_historical_lease: LockLease | None = None
        self._owner_fencing_available: bool | None = None

    def _connect(self) -> Any:
        return self.connection_factory()

    def _supports_owner_fencing(self, cur: Any) -> bool:
        """Feature-detect the OwnerId column once per process, cached.

        Deliberately adaptive rather than assumed: this code can deploy
        before scripts/sql/09_migration_lock_fencing.sql has been run
        against a given database (that migration is intentionally NOT
        run automatically - see its header). Without this check, every
        acquire/renew/release call would start failing with a SQL error
        the moment this code runs against an unmigrated database. Once
        the column exists, fencing activates automatically on the next
        check with no code redeploy needed.
        """
        if self._owner_fencing_available is not None:
            return self._owner_fencing_available
        try:
            cur.execute(
                "SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('SEN.ActiveTask') AND name = 'OwnerId'"
            )
            self._owner_fencing_available = cur.fetchone() is not None
        except Exception:
            self._owner_fencing_available = False
        return self._owner_fencing_available

    def cleanup_expired(self) -> int:
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("DELETE FROM SEN.ActiveTask WHERE ExpiresAt <= SYSUTCDATETIME()")
            deleted = int(cur.rowcount or 0)
            conn.commit()
            self._cache.clear()
            return deleted
        except Exception as exc:
            self.logger.warning("lock cleanup_expired failed: %s", exc)
            return 0
        finally:
            if conn is not None:
                conn.close()

    def acquire(self, task_name: str, ttl_min: int, payload: str | None = None) -> bool:
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM SEN.ActiveTask
                WHERE TaskName = ? AND ExpiresAt <= SYSUTCDATETIME()
                """,
                (task_name,),
            )
            fencing = self._supports_owner_fencing(cur)
            owner_col = ", OwnerId" if fencing else ""
            owner_val_sql = ", ?" if fencing else ""
            if payload is None:
                cur.execute(
                    f"""
                    INSERT INTO SEN.ActiveTask (TaskName, ExpiresAt{owner_col})
                    VALUES (?, DATEADD(minute, ?, SYSUTCDATETIME()){owner_val_sql})
                    """,
                    (task_name, ttl_min, self.owner_id) if fencing else (task_name, ttl_min),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO SEN.ActiveTask (TaskName, ExpiresAt, Payload{owner_col})
                    VALUES (?, DATEADD(minute, ?, SYSUTCDATETIME()), ?{owner_val_sql})
                    """,
                    (task_name, ttl_min, payload, self.owner_id) if fencing else (task_name, ttl_min, payload),
                )
            conn.commit()
            self._cache.pop(task_name, None)
            return True
        except Exception as exc:
            if pyodbc is not None and isinstance(exc, pyodbc.IntegrityError):
                return False
            # SQL Server duplicate key can arrive wrapped by another DBAPI layer.
            if "duplicate" in str(exc).lower() or "primary key" in str(exc).lower():
                return False
            self.logger.warning("lock acquire(%s) failed: %s", task_name, exc)
            return False
        finally:
            if conn is not None:
                conn.close()

    def renew(
        self,
        task_name: str,
        ttl_min: int,
        *,
        payload: str | None = None,
        owner_prefix: str | None = None,
    ) -> bool:
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            fencing = self._supports_owner_fencing(cur)
            payload_sql = ", Payload = ?" if payload is not None else ""
            owner_sql = " AND Payload LIKE ?" if owner_prefix else ""
            # Renew is always "extend a lock I currently hold" - never an
            # administrative action on someone else's lock (unlike
            # release()'s owner_prefix path) - so OwnerId fencing applies
            # unconditionally whenever the column is available, on top of
            # any owner_prefix check.
            fence_sql = " AND OwnerId = ?" if fencing else ""
            params: list[Any] = [ttl_min]
            if payload is not None:
                params.append(payload)
            params.append(task_name)
            if owner_prefix:
                params.append(f"{owner_prefix}%")
            if fencing:
                params.append(self.owner_id)
            cur.execute(
                f"""
                UPDATE SEN.ActiveTask
                SET ExpiresAt = DATEADD(minute, ?, SYSUTCDATETIME())
                    {payload_sql}
                WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
                    {owner_sql}{fence_sql}
                """,
                tuple(params),
            )
            conn.commit()
            ok = int(cur.rowcount or 0) > 0
            self._cache.pop(task_name, None)
            return ok
        except Exception as exc:
            self.logger.warning("lock renew(%s) failed: %s", task_name, exc)
            return False
        finally:
            if conn is not None:
                conn.close()

    def release(self, task_name: str, *, owner_prefix: str | None = None) -> bool:
        for attempt in range(1, 3):
            conn = None
            try:
                conn = self._connect()
                cur = conn.cursor()
                if owner_prefix:
                    # Administrative/targeted delete of a SPECIFIC known
                    # row (e.g. cleanup_stale_lock releasing a lock that
                    # belongs to a different, already-dead process) - must
                    # NOT also filter by self.owner_id, since the row being
                    # deleted deliberately does not belong to this process.
                    cur.execute(
                        "DELETE FROM SEN.ActiveTask WHERE TaskName = ? AND Payload LIKE ?",
                        (task_name, f"{owner_prefix}%"),
                    )
                elif self._supports_owner_fencing(cur):
                    # Plain release() with no explicit target: this is
                    # always "release the lock I currently hold" (e.g.
                    # release_live_runtime(), release_supervisor_lock()) -
                    # fence it so a stale/reconnected owner cannot delete a
                    # lock a different process has since legitimately
                    # acquired for the same TaskName.
                    cur.execute(
                        "DELETE FROM SEN.ActiveTask WHERE TaskName = ? AND OwnerId = ?",
                        (task_name, self.owner_id),
                    )
                else:
                    cur.execute("DELETE FROM SEN.ActiveTask WHERE TaskName = ?", (task_name,))
                conn.commit()
                deleted = int(cur.rowcount or 0)
                if deleted == 0:
                    self.logger.warning(
                        "lock release(%s): no active row matched", task_name
                    )
                self._cache.pop(task_name, None)
                return deleted > 0
            except Exception as exc:
                level = logging.WARNING if attempt == 1 else logging.ERROR
                self.logger.log(
                    level,
                    "lock release(%s) attempt %d failed: %s",
                    task_name,
                    attempt,
                    exc,
                )
            finally:
                if conn is not None:
                    conn.close()
        self._cache.pop(task_name, None)
        return False

    def update_payload(
        self,
        task_name: str,
        payload: str,
        *,
        expire_after_seconds: int | None = None,
    ) -> bool:
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            if expire_after_seconds is None:
                cur.execute(
                    """
                    UPDATE SEN.ActiveTask
                    SET Payload = ?
                    WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
                    """,
                    (payload, task_name),
                )
            else:
                grace = max(1, int(expire_after_seconds))
                cur.execute(
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
                    (payload, grace, grace, task_name),
                )
            conn.commit()
            ok = int(cur.rowcount or 0) > 0
            self._cache.pop(task_name, None)
            return ok
        except Exception as exc:
            self.logger.warning("lock update_payload(%s) failed: %s", task_name, exc)
            return False
        finally:
            if conn is not None:
                conn.close()

    def fetch(self, task_name: str, *, active_only: bool = True) -> LockRecord | None:
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            active_sql = "AND ExpiresAt > SYSUTCDATETIME()" if active_only else ""
            cur.execute(
                f"""
                SELECT StartedAt, ExpiresAt, Payload
                FROM SEN.ActiveTask
                WHERE TaskName = ? {active_sql}
                """,
                (task_name,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return LockRecord(
                task_name=task_name,
                started_at=row[0],
                expires_at=row[1],
                payload=str(row[2] or ""),
            )
        except Exception as exc:
            self.logger.warning("lock fetch(%s) failed: %s", task_name, exc)
            return None
        finally:
            if conn is not None:
                conn.close()

    def is_locked(
        self,
        task_name: str,
        *,
        cache_sec: float = DEFAULT_LOCK_CACHE_TTL_SEC,
    ) -> bool:
        now = time.monotonic()
        cached = self._cache.get(task_name)
        if cached and now - float(cached.get("checked_at") or 0.0) < cache_sec:
            return bool(cached.get("locked"))
        locked = self.fetch(task_name, active_only=True) is not None
        self._cache[task_name] = {"locked": locked, "checked_at": now}
        return locked

    def _owned_payload(self, kind: str, owner: str, run_id: str, started: str) -> str:
        return format_payload(
            {
                "run": run_id,
                "kind": kind,
                "owner": owner,
                "host": self.host,
                "pid": str(self.pid),
                "started": started,
                "heartbeat": utc_stamp(),
            }
        )

    def _same_host_pid_dead(self, meta: dict[str, str]) -> bool:
        host = meta.get("host")
        if host and host.lower() != self.host.lower():
            return False
        pid_text = meta.get("pid")
        if not pid_text:
            return False
        try:
            return not self.pid_alive(int(pid_text))
        except Exception:
            return False

    def _heartbeat_stale(self, meta: dict[str, str], *, stale_after_sec: float) -> bool:
        heartbeat = meta.get("heartbeat") or meta.get("started")
        if not heartbeat:
            return False
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(heartbeat)).total_seconds()
            return age > stale_after_sec
        except Exception:
            return False

    def cleanup_stale_lock(
        self,
        task_name: str,
        *,
        stale_after_sec: float = DEFAULT_HEARTBEAT_SEC * 3,
        force: bool = False,
    ) -> bool:
        record = self.fetch(task_name, active_only=True)
        if record is None:
            return False
        meta = record.meta
        reason = ""
        if force:
            reason = "forced cleanup"
        elif self._same_host_pid_dead(meta):
            reason = f"dead pid {meta.get('pid')}"
        elif self._heartbeat_stale(meta, stale_after_sec=stale_after_sec):
            reason = "stale heartbeat"

        if not reason:
            return False

        owner_prefix = f"run={meta['run']};" if meta.get("run") else None
        released = self.release(task_name, owner_prefix=owner_prefix)
        if released:
            self.logger.warning(
                "released stale lock %s owner=%s pid=%s reason=%s",
                task_name,
                meta.get("owner", "unknown"),
                meta.get("pid", "?"),
                reason,
            )
        return released

    def acquire_historical_job(
        self,
        owner: str,
        *,
        ttl_min: int = DEFAULT_HISTORICAL_TTL_MIN,
        wait_timeout_sec: float = 30 * 60.0,
        poll_sec: float = 15.0,
    ) -> LockLease | None:
        start = time.monotonic()
        run_id = uuid.uuid4().hex[:12]
        owner_prefix = f"run={run_id};"
        started = utc_stamp()
        last_log_at = -60.0

        while True:
            payload = self._owned_payload("historical_job", owner, run_id, started)
            if self.acquire(HISTORICAL_JOB_LOCK, ttl_min, payload):
                lease = LockLease(
                    task_name=HISTORICAL_JOB_LOCK,
                    owner=owner,
                    run_id=run_id,
                    owner_prefix=owner_prefix,
                    stop_event=threading.Event(),
                )
                self._local_historical_lease = lease
                self._start_heartbeat(lease, ttl_min=ttl_min, started=started)
                self.logger.info(
                    "%s",
                    operation_line(
                        "HISTORICAL",
                        "Historical job lock acquired",
                        owner=owner,
                        run_id=run_id,
                        result="locked",
                    ),
                )
                return lease

            if self.cleanup_stale_lock(HISTORICAL_JOB_LOCK):
                continue
            waited = time.monotonic() - start
            if waited >= wait_timeout_sec:
                self.logger.error(
                    "%s",
                    operation_line(
                        "HISTORICAL",
                        "Historical job lock wait timed out",
                        owner=owner,
                        waited_seconds=round(waited),
                        result="failed",
                    ),
                )
                return None
            if waited - last_log_at >= 60.0:
                self.logger.info(
                    "%s",
                    operation_line(
                        "HISTORICAL",
                        "Historical job lock busy",
                        owner=owner,
                        waited_seconds=round(waited),
                        result="waiting",
                    ),
                )
                last_log_at = waited
            time.sleep(poll_sec)

    def _start_heartbeat(self, lease: LockLease, *, ttl_min: int, started: str) -> None:
        def loop() -> None:
            while not lease.stop_event.wait(DEFAULT_HEARTBEAT_SEC):
                payload = self._owned_payload(
                    "historical_job", lease.owner, lease.run_id, started
                )
                ok = self.renew(
                    lease.task_name,
                    ttl_min,
                    payload=payload,
                    owner_prefix=lease.owner_prefix,
                )
                if not ok:
                    self.logger.warning(
                        "historical lock heartbeat failed owner=%s run=%s",
                        lease.owner,
                        lease.run_id,
                    )

        thread = threading.Thread(
            target=loop,
            name=f"{lease.owner}-historical-lock-heartbeat",
            daemon=True,
        )
        thread.start()

    def release_lease(self, lease: LockLease | None) -> bool:
        if lease is None or lease.released:
            return False
        lease.stop_event.set()
        released = self.release(lease.task_name, owner_prefix=lease.owner_prefix)
        lease.released = True
        if self._local_historical_lease is lease:
            self._local_historical_lease = None
        return released

    def acquire_live_runtime(
        self,
        *,
        ttl_min: int = DEFAULT_LIVE_RUNTIME_TTL_MIN,
        started: str | None = None,
    ) -> bool:
        started = started or utc_stamp()
        payload = format_payload(
            {
                "kind": "live_runtime",
                "host": self.host,
                "pid": str(self.pid),
                "started": started,
                "heartbeat": utc_stamp(),
            }
        )
        return self.acquire(LIVE_RUNTIME_LOCK, ttl_min, payload)

    def renew_live_runtime(
        self,
        *,
        ttl_min: int = DEFAULT_LIVE_RUNTIME_TTL_MIN,
        started: str | None = None,
    ) -> bool:
        started = started or utc_stamp()
        payload = format_payload(
            {
                "kind": "live_runtime",
                "host": self.host,
                "pid": str(self.pid),
                "started": started,
                "heartbeat": utc_stamp(),
            }
        )
        return self.renew(LIVE_RUNTIME_LOCK, ttl_min, payload=payload)

    def release_live_runtime(self) -> bool:
        return self.release(LIVE_RUNTIME_LOCK)

    def request_live_shutdown(self, *, grace_sec: int = LIVE_SHUTDOWN_GRACE_SEC) -> bool:
        record = self.fetch(LIVE_RUNTIME_LOCK, active_only=True)
        if record is None:
            return False
        meta = dict(record.meta)
        host = meta.get("host")
        if host and host.lower() != self.host.lower():
            self.logger.warning(
                "refusing live shutdown signal for another host owner=%s", host
            )
            return False
        meta["shutdown_requested"] = "1"
        meta["shutdown_by_host"] = self.host
        meta["shutdown_by_pid"] = str(self.pid)
        meta["shutdown_at"] = utc_stamp()
        return self.update_payload(
            LIVE_RUNTIME_LOCK,
            format_payload(meta),
            expire_after_seconds=grace_sec,
        )

    def is_live_shutdown_requested(self) -> bool:
        record = self.fetch(LIVE_RUNTIME_LOCK, active_only=True)
        return bool(record and record.meta.get("shutdown_requested") == "1")

    def acquire_live_batch(self, *, ttl_min: int = DEFAULT_LIVE_BATCH_TTL_MIN) -> bool:
        payload = format_payload(
            {
                "kind": "live_batch",
                "host": self.host,
                "pid": str(self.pid),
                "started": utc_stamp(),
            }
        )
        return self.acquire(LIVE_BATCH_LOCK, ttl_min, payload)

    def release_live_batch(self) -> bool:
        return self.release(LIVE_BATCH_LOCK)

    def cleanup_orphan_live_batch(self) -> bool:
        if not self.is_locked(LIVE_BATCH_LOCK, cache_sec=0):
            return False
        if self.is_locked(LIVE_RUNTIME_LOCK, cache_sec=0):
            return False
        return self.release(LIVE_BATCH_LOCK)

    def wait_for_live_batch_clear(
        self,
        *,
        max_wait_sec: float = 3 * 60.0,
        poll_sec: float = 5.0,
    ) -> bool:
        self.cleanup_orphan_live_batch()
        start = time.monotonic()
        while self.is_locked(LIVE_BATCH_LOCK, cache_sec=0):
            self.cleanup_orphan_live_batch()
            waited = time.monotonic() - start
            if waited >= max_wait_sec:
                self.logger.warning(
                    "live batch still active after %.0fs; continuing cautiously",
                    waited,
                )
                return False
            time.sleep(poll_sec)
        return True

    def wait_for_historical_slot(self) -> bool:
        if not self.wait_for_live_batch_clear():
            return False
        if not self.is_locked(LIVE_RUNTIME_LOCK, cache_sec=0):
            return True
        seconds = seconds_until_next_live_batch()
        if seconds > HISTORICAL_YIELD_BEFORE_BATCH_SEC:
            return True
        sleep_sec = seconds + 5.0
        self.logger.info(
            "yielding %.0fs for upcoming live batch window", sleep_sec
        )
        time.sleep(sleep_sec)
        return self.wait_for_live_batch_clear()

def seconds_until_next_live_batch(
    interval_min: float = LIVE_BATCH_INTERVAL_MIN,
) -> float:
    now = datetime.now()
    interval_sec = interval_min * 60.0
    elapsed = (now.minute * 60 + now.second + now.microsecond / 1_000_000) % interval_sec
    wait = interval_sec - elapsed
    return wait if wait > 5.0 else interval_sec


_DEFAULT = LockCoordinator()


def acquire(task_name: str, duration_min: int = 90, payload: str | None = None) -> bool:
    return _DEFAULT.acquire(task_name, duration_min, payload)


def renew(task_name: str, duration_min: int = 90, payload: str | None = None) -> bool:
    return _DEFAULT.renew(task_name, duration_min, payload=payload)


def update_payload(
    task_name: str,
    payload: str,
    *,
    expire_after_seconds: int | None = None,
) -> bool:
    return _DEFAULT.update_payload(
        task_name,
        payload,
        expire_after_seconds=expire_after_seconds,
    )


def release(task_name: str) -> bool:
    return _DEFAULT.release(task_name)


def is_locked(task_name: str) -> bool:
    return _DEFAULT.is_locked(task_name)


def fetch_lock(task_name: str, *, active_only: bool = True) -> LockRecord | None:
    return _DEFAULT.fetch(task_name, active_only=active_only)


def cleanup_expired() -> int:
    return _DEFAULT.cleanup_expired()


def local_pid_alive(pid: int) -> bool:
    return _local_pid_alive(pid)


def cleanup_stale_lock(
    task_name: str,
    *,
    stale_after_sec: float = DEFAULT_HEARTBEAT_SEC * 3,
    force: bool = False,
) -> bool:
    return _DEFAULT.cleanup_stale_lock(
        task_name,
        stale_after_sec=stale_after_sec,
        force=force,
    )


def acquire_historical_job(
    owner: str,
    logger: logging.Logger | None = None,
    *,
    duration_min: int = DEFAULT_HISTORICAL_TTL_MIN,
    wait_timeout_sec: float = 30 * 60.0,
    poll_sec: float = 15.0,
) -> LockLease | None:
    if logger is not None:
        _DEFAULT.logger = logger
    return _DEFAULT.acquire_historical_job(
        owner,
        ttl_min=duration_min,
        wait_timeout_sec=wait_timeout_sec,
        poll_sec=poll_sec,
    )


def release_historical_job(
    lease: LockLease | None,
    owner: str | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    if logger is not None:
        _DEFAULT.logger = logger
    return _DEFAULT.release_lease(lease)


def wait_for_historical_slot(
    owner: str | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    if logger is not None:
        _DEFAULT.logger = logger
    return _DEFAULT.wait_for_historical_slot()


def request_ws_live_shutdown(logger: logging.Logger | None = None) -> bool:
    if logger is not None:
        _DEFAULT.logger = logger
    return _DEFAULT.request_live_shutdown()


def is_ws_live_shutdown_requested() -> bool:
    return _DEFAULT.is_live_shutdown_requested()


def acquire_live_batch_window(logger: logging.Logger | None = None) -> bool:
    if logger is not None:
        _DEFAULT.logger = logger
    return _DEFAULT.acquire_live_batch()


def release_live_batch_window(active: bool = True) -> bool:
    if not active:
        return False
    return _DEFAULT.release_live_batch()


def cleanup_orphan_live_batch_window(logger: logging.Logger | None = None) -> bool:
    if logger is not None:
        _DEFAULT.logger = logger
    return _DEFAULT.cleanup_orphan_live_batch()
