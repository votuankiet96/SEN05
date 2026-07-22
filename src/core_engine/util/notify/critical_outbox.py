"""Durable, multi-process-safe outbox for CRITICAL-level alerts.

A CRITICAL record is committed to SQLite before any network request.  Rows are
claimed with a renewable-by-retry lease, delivered synchronously, and deleted
only in the same transaction that records a successful delivery.  Delivery
metadata lives in SQLite as well, so health reporting remains meaningful after
a process restart.

Storage failures deliberately fail closed.  Mutating/query APIs raise
``CriticalOutboxStorageError`` and :meth:`status` reports ``healthy=False``;
an unreadable database must never be represented as an empty outbox.
"""

from __future__ import annotations

import calendar
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from core_engine.util.notify.transport import post_webhook_once

DEFAULT_TIMEOUT_CONNECT_SEC = 5.0
DEFAULT_TIMEOUT_READ_SEC = 10.0
DEFAULT_LEASE_SECONDS = 15 * 60
DEFAULT_DELIVERY_LEDGER_LIMIT = 1_000


class CriticalOutboxStorageError(RuntimeError):
    """The durable alert store could not complete an operation."""


class CriticalOutboxLeaseLostError(CriticalOutboxStorageError):
    """A stale sender attempted to mutate a row owned by another sender."""


class CriticalAlertOutbox:
    def __init__(
        self,
        db_path: Path,
        status_log_path: Path,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        delivery_ledger_limit: int = DEFAULT_DELIVERY_LEDGER_LIMIT,
    ) -> None:
        self.db_path = db_path
        self.status_log_path = status_log_path
        self.lease_seconds = max(1, int(lease_seconds))
        self.delivery_ledger_limit = max(1, int(delivery_ledger_limit))
        # Serialises threads sharing this object. SQLite BEGIN IMMEDIATE is the
        # corresponding inter-process boundary.
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=5.0)
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    @staticmethod
    def _raise_storage(operation: str, exc: BaseException) -> None:
        raise CriticalOutboxStorageError(
            f"critical alert outbox {operation} failed: {type(exc).__name__}: {exc}"
        ) from exc

    def init(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with closing(self._connect()) as con:
                    con.execute("PRAGMA journal_mode = WAL")
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS critical_alerts (
                            id           INTEGER PRIMARY KEY AUTOINCREMENT,
                            message      TEXT NOT NULL,
                            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                            attempts     INTEGER NOT NULL DEFAULT 0,
                            last_error   TEXT,
                            claim_token  TEXT,
                            leased_until REAL
                        )
                        """
                    )
                    # In-place migration from the original production schema.
                    columns = {
                        str(row[1]) for row in con.execute("PRAGMA table_info(critical_alerts)")
                    }
                    if "claim_token" not in columns:
                        con.execute("ALTER TABLE critical_alerts ADD COLUMN claim_token TEXT")
                    if "leased_until" not in columns:
                        con.execute("ALTER TABLE critical_alerts ADD COLUMN leased_until REAL")
                    con.execute(
                        """
                        CREATE INDEX IF NOT EXISTS ix_critical_alerts_claim
                        ON critical_alerts (leased_until, id)
                        """
                    )
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS critical_outbox_metadata (
                            key        TEXT PRIMARY KEY,
                            value      TEXT,
                            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                        )
                        """
                    )
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS critical_delivery_ledger (
                            id           INTEGER PRIMARY KEY AUTOINCREMENT,
                            alert_id     INTEGER NOT NULL,
                            message      TEXT NOT NULL,
                            delivered_at TEXT NOT NULL
                        )
                        """
                    )
                    result = con.execute("PRAGMA quick_check").fetchone()
                    if not result or str(result[0]).lower() != "ok":
                        raise sqlite3.DatabaseError(f"PRAGMA quick_check returned {result!r}")
                    con.commit()
        except CriticalOutboxStorageError:
            raise
        except Exception as exc:
            self._raise_storage("initialisation", exc)

    def persist(self, message: str) -> int:
        try:
            with self._lock:
                with closing(self._connect()) as con:
                    cur = con.execute(
                        "INSERT INTO critical_alerts (message) VALUES (?)", (message,)
                    )
                    con.commit()
                    return int(cur.lastrowid)
        except Exception as exc:
            self._raise_storage("persist", exc)

    def ack(self, row_id: int, *, claim_token: str | None = None) -> bool:
        """Acknowledge a delivered row and append a bounded audit ledger entry.

        ``claim_token`` is optional for compatibility with administrative/tests
        callers. Delivery paths always supply it, preventing a stale worker from
        deleting a row after its lease has moved to another process.
        """
        try:
            with self._lock:
                with closing(self._connect()) as con:
                    con.execute("BEGIN IMMEDIATE")
                    if claim_token is None:
                        row = con.execute(
                            "SELECT message FROM critical_alerts WHERE id = ?", (row_id,)
                        ).fetchone()
                    else:
                        row = con.execute(
                            """SELECT message FROM critical_alerts
                               WHERE id = ? AND claim_token = ?""",
                            (row_id, claim_token),
                        ).fetchone()
                    if row is None:
                        exists = con.execute(
                            "SELECT 1 FROM critical_alerts WHERE id = ?", (row_id,)
                        ).fetchone()
                        con.rollback()
                        if exists and claim_token is not None:
                            raise CriticalOutboxLeaseLostError(
                                f"critical alert row {row_id} is no longer owned by this sender"
                            )
                        return False

                    delivered_at = _utc_now_iso()
                    con.execute(
                        """INSERT INTO critical_delivery_ledger
                           (alert_id, message, delivered_at) VALUES (?, ?, ?)""",
                        (row_id, str(row[0]), delivered_at),
                    )
                    if claim_token is None:
                        con.execute("DELETE FROM critical_alerts WHERE id = ?", (row_id,))
                    else:
                        con.execute(
                            "DELETE FROM critical_alerts WHERE id = ? AND claim_token = ?",
                            (row_id, claim_token),
                        )
                    self._set_metadata_in_transaction(con, "last_success_at", delivered_at)
                    self._trim_ledger_in_transaction(con)
                    con.commit()
                    return True
        except CriticalOutboxStorageError:
            raise
        except Exception as exc:
            self._raise_storage("ack", exc)

    def mark_failed(
        self, row_id: int, error: str, *, claim_token: str | None = None
    ) -> bool:
        try:
            with self._lock:
                with closing(self._connect()) as con:
                    con.execute("BEGIN IMMEDIATE")
                    if claim_token is None:
                        cur = con.execute(
                            """UPDATE critical_alerts
                               SET attempts = attempts + 1, last_error = ?,
                                   claim_token = NULL, leased_until = NULL
                               WHERE id = ?""",
                            (error[:500], row_id),
                        )
                    else:
                        cur = con.execute(
                            """UPDATE critical_alerts
                               SET attempts = attempts + 1, last_error = ?,
                                   claim_token = NULL, leased_until = NULL
                               WHERE id = ? AND claim_token = ?""",
                            (error[:500], row_id, claim_token),
                        )
                    if cur.rowcount == 0 and claim_token is not None:
                        exists = con.execute(
                            "SELECT 1 FROM critical_alerts WHERE id = ?", (row_id,)
                        ).fetchone()
                        if exists:
                            con.rollback()
                            raise CriticalOutboxLeaseLostError(
                                f"critical alert row {row_id} is no longer owned by this sender"
                            )
                    failed_at = _utc_now_iso()
                    self._set_metadata_in_transaction(con, "last_failure_at", failed_at)
                    self._set_metadata_in_transaction(con, "last_failure_error", error[:500])
                    con.commit()
                    return cur.rowcount > 0
        except CriticalOutboxStorageError:
            raise
        except Exception as exc:
            self._raise_storage("mark failed", exc)

    def pending(self) -> list[tuple[int, str, str, int]]:
        """Return every undelivered row, including rows currently leased."""
        try:
            with self._lock:
                with closing(self._connect()) as con:
                    return con.execute(
                        """SELECT id, message, created_at, attempts
                           FROM critical_alerts ORDER BY id"""
                    ).fetchall()
        except Exception as exc:
            self._raise_storage("pending query", exc)

    def _claim(self, *, limit: int, row_id: int | None = None) -> list[tuple[int, str, str, int, str]]:
        """Atomically claim available rows across all processes."""
        token = uuid.uuid4().hex
        now = time.time()
        leased_until = now + self.lease_seconds
        try:
            with self._lock:
                with closing(self._connect()) as con:
                    con.execute("BEGIN IMMEDIATE")
                    params: list[Any] = [now]
                    predicate = "(claim_token IS NULL OR leased_until IS NULL OR leased_until <= ?)"
                    if row_id is not None:
                        predicate += " AND id = ?"
                        params.append(row_id)
                    params.append(max(0, int(limit)))
                    ids = [
                        int(row[0])
                        for row in con.execute(
                            f"SELECT id FROM critical_alerts WHERE {predicate} ORDER BY id LIMIT ?",
                            params,
                        ).fetchall()
                    ]
                    if not ids:
                        con.commit()
                        return []
                    placeholders = ",".join("?" for _ in ids)
                    con.execute(
                        f"""UPDATE critical_alerts SET claim_token = ?, leased_until = ?
                            WHERE id IN ({placeholders})""",
                        [token, leased_until, *ids],
                    )
                    rows = con.execute(
                        f"""SELECT id, message, created_at, attempts, claim_token
                            FROM critical_alerts WHERE id IN ({placeholders}) ORDER BY id""",
                        ids,
                    ).fetchall()
                    con.commit()
                    return rows
        except Exception as exc:
            self._raise_storage("claim", exc)

    @staticmethod
    def _set_metadata_in_transaction(
        con: sqlite3.Connection, key: str, value: str
    ) -> None:
        con.execute(
            """INSERT INTO critical_outbox_metadata (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
            (key, value),
        )

    def _trim_ledger_in_transaction(self, con: sqlite3.Connection) -> None:
        con.execute(
            """DELETE FROM critical_delivery_ledger
               WHERE id NOT IN (
                   SELECT id FROM critical_delivery_ledger
                   ORDER BY id DESC LIMIT ?
               )""",
            (self.delivery_ledger_limit,),
        )

    def send_one(self, message: str) -> bool:
        """Synchronously POST one alert and return confirmed HTTP success."""
        try:
            from core_engine.settings import NOTIFICATION

            webhook_url = getattr(NOTIFICATION, "discord_webhook_url", "")
            if not webhook_url:
                return False
            result = post_webhook_once(
                webhook_url,
                {"content": message[:1900]},
                connect_timeout=DEFAULT_TIMEOUT_CONNECT_SEC,
                read_timeout=DEFAULT_TIMEOUT_READ_SEC,
            )
            return result.ok
        except Exception:
            return False

    def record_and_send(self, message: str) -> bool:
        row_id = self.persist(message)
        claimed = self._claim(limit=1, row_id=row_id)
        if not claimed:
            # Another process claimed the durable row between commit and this
            # call. It owns delivery now; do not send a duplicate.
            self._write_status()
            return False
        _row_id, claimed_message, _created_at, _attempts, token = claimed[0]
        try:
            ok = self.send_one(claimed_message)
        except Exception as exc:  # also covers fault-injected/custom senders
            self.mark_failed(row_id, f"delivery raised: {type(exc).__name__}: {exc}", claim_token=token)
            self._write_status()
            return False
        if ok:
            self.ack(row_id, claim_token=token)
        else:
            self.mark_failed(row_id, "delivery failed", claim_token=token)
        self._write_status()
        return ok

    def enqueue(self, message: str) -> int:
        """Durably enqueue without performing network I/O in the caller."""
        row_id = self.persist(message)
        self._write_status()
        return row_id

    def drain(self, *, limit: int = 20) -> int:
        """Claim and retry up to ``limit`` pending rows once."""
        sent = 0
        for row_id, message, _created_at, _attempts, token in self._claim(limit=limit):
            try:
                ok = self.send_one(message)
            except Exception as exc:
                self.mark_failed(
                    row_id,
                    f"retry raised: {type(exc).__name__}: {exc}",
                    claim_token=token,
                )
                continue
            if ok:
                self.ack(row_id, claim_token=token)
                sent += 1
            else:
                self.mark_failed(row_id, "retry failed", claim_token=token)
        self._write_status()
        return sent

    def status(self) -> dict[str, Any]:
        """Return health metadata without ever mapping a DB fault to zero rows."""
        try:
            with self._lock:
                with closing(self._connect()) as con:
                    check = con.execute("PRAGMA quick_check").fetchone()
                    if not check or str(check[0]).lower() != "ok":
                        raise sqlite3.DatabaseError(f"PRAGMA quick_check returned {check!r}")
                    pending_count = int(
                        con.execute("SELECT COUNT(*) FROM critical_alerts").fetchone()[0]
                    )
                    oldest = con.execute(
                        "SELECT created_at FROM critical_alerts ORDER BY id LIMIT 1"
                    ).fetchone()
                    metadata = dict(
                        con.execute(
                            "SELECT key, value FROM critical_outbox_metadata"
                        ).fetchall()
                    )
                    ledger_count = int(
                        con.execute("SELECT COUNT(*) FROM critical_delivery_ledger").fetchone()[0]
                    )
            return {
                "healthy": True,
                "storage_error": None,
                "pending_count": pending_count,
                "oldest_pending_age_seconds": _age_seconds(oldest[0]) if oldest else None,
                "last_success_at": metadata.get("last_success_at"),
                "last_failure_at": metadata.get("last_failure_at"),
                "last_failure_error": metadata.get("last_failure_error"),
                "delivery_ledger_count": ledger_count,
            }
        except Exception as exc:
            return {
                "healthy": False,
                "storage_error": f"{type(exc).__name__}: {exc}",
                "pending_count": None,
                "oldest_pending_age_seconds": None,
                "last_success_at": None,
                "last_failure_at": None,
                "last_failure_error": None,
                "delivery_ledger_count": None,
            }

    def _write_status(self) -> None:
        try:
            self.status_log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": _utc_now_iso(), **self.status()}
            temp = self.status_log_path.with_name(
                f".{self.status_log_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            os.replace(temp, self.status_log_path)
        except Exception as exc:
            raise CriticalOutboxStorageError(
                f"critical alert status log write failed: {type(exc).__name__}: {exc}"
            ) from exc


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _age_seconds(sqlite_datetime: str) -> float | None:
    try:
        parsed = time.strptime(sqlite_datetime, "%Y-%m-%d %H:%M:%S")
        return max(0.0, time.time() - calendar.timegm(parsed))
    except Exception:
        return None


_OUTBOX: CriticalAlertOutbox | None = None


class _CriticalDeliveryDispatcher:
    """One fixed best-effort worker; SQLite remains the actual durable queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._worker: threading.Thread | None = None

    def schedule(self) -> None:
        worker = self._worker
        if worker is None or not worker.is_alive():
            with self._lock:
                worker = self._worker
                if worker is None or not worker.is_alive():
                    self._worker = threading.Thread(
                        target=self._run,
                        name="critical-alert-sender",
                        daemon=True,
                    )
                    self._worker.start()
        self._event.set()

    def _run(self) -> None:
        while True:
            self._event.wait()
            self._event.clear()
            try:
                critical_alert_outbox().drain(limit=3)
            except Exception:
                # The row was committed before this worker was scheduled. The
                # supervisor owns periodic retry and health reports storage
                # failure fail-closed, so this worker must never recurse into
                # logging/Discord from its own error path.
                pass


_DISPATCHER = _CriticalDeliveryDispatcher()


def enqueue_critical_alert(message: str) -> int:
    outbox = critical_alert_outbox()
    row_id = outbox.enqueue(message)
    _DISPATCHER.schedule()
    return row_id


def critical_alert_outbox() -> CriticalAlertOutbox:
    global _OUTBOX
    if _OUTBOX is None:
        from core_engine.settings import CACHE_DIR, SYSTEM_LOG_DIR

        outbox = CriticalAlertOutbox(
            db_path=CACHE_DIR / "critical_alerts_outbox.db",
            status_log_path=SYSTEM_LOG_DIR / "critical_outbox_status.json",
        )
        outbox.init()
        _OUTBOX = outbox
    return _OUTBOX
