"""Durable SQLite write-ahead outbox for live OHLCV batches.

Every candle handed to the live DB writer is persisted here FIRST (via
persist_pending), before it is ever placed on the in-memory dispatch queue.
A row moves through three states:

    pending -> leased -> staged -> (deleted / acked)

- pending: durable on disk, not yet claimed by a worker.
- leased: claimed by lease_batch() for staging; reverts to pending
  (release_for_retry) if the staging write fails, so a failed attempt is
  retried rather than dropped.
- staged: insert_staging_batch() succeeded (the row is durably in
  SEN.TF_* staging) but run_etl_direct() (staging -> Fact_OHLCV) has not
  yet succeeded for it. A 'staged' row is excluded from lease_batch()'s
  pending/leased query, so it is never re-staged while waiting - but it
  is NOT acked yet either.
- The row is only finally deleted (ack / ack_staged_for_key) once
  run_etl_direct() actually succeeds for that symbol/timeframe. This is
  deliberate: acking right after the staging write (an earlier version of
  this design did that) would leave the exact gap this outbox exists to
  close still open on the live path - a crash between a successful
  staging commit and the Fact commit would have nothing durable left
  pointing at the fact that Fact_OHLCV is still missing that data,
  because the in-memory deferred-ETL retry state (live.state._deferred_etl)
  does not survive a crash. Keeping the row 'staged' until Fact commit
  succeeds means a crash at that exact point is recoverable: LiveSpool.init()
  resets any leftover 'staged' rows back to 'pending' on the next startup,
  so they re-enter the normal pending -> leased -> staged -> ack cycle
  (safe/idempotent: insert_staging_batch's MERGE is a no-op for unchanged
  rows, and run_etl_direct's NOT EXISTS check is what actually lands them
  in Fact_OHLCV).

This replaces an earlier design where the spool only existed as a last-
resort overflow buffer, and its own flush_to_queue() deleted a row the
moment it was handed to the in-memory queue - before that data was
actually durable in SQL Server. A crash between that delete and a
successful staging commit lost the candle permanently. See
test/test_spool.py for fault-injection coverage of the crash points this
design is meant to survive.
"""

from __future__ import annotations

import logging
import pickle
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

PAYLOAD_VERSION = 1
PAYLOAD_MARKER = "__sen05_spool_payload__"

STATUS_PENDING = "pending"
STATUS_LEASED = "leased"
STATUS_STAGED = "staged"

DEFAULT_LEASE_SECONDS = 120


class LiveSpool:
    """Disk-backed durable outbox: every live OHLCV batch passes through here."""

    def __init__(
        self,
        path: Path,
        *,
        max_rows: int,
        logger: logging.Logger,
    ) -> None:
        self.path = path
        self.max_rows = max_rows
        self.logger = logger
        self._lock = None  # set in init() via threading, kept None-safe for import order
        import threading

        self._lock = threading.Lock()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS spool (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        batch_id        INTEGER NOT NULL DEFAULT 0,
                        payload_version INTEGER NOT NULL DEFAULT 0,
                        symbol_id       INTEGER NOT NULL,
                        tf_code         TEXT    NOT NULL,
                        staging_table   TEXT    NOT NULL,
                        tv_symbol       TEXT    NOT NULL,
                        bar_data        BLOB    NOT NULL,
                        created_at      TEXT    DEFAULT (datetime('now')),
                        status          TEXT    NOT NULL DEFAULT 'pending',
                        lease_id        TEXT,
                        lease_until     TEXT,
                        attempts        INTEGER NOT NULL DEFAULT 0,
                        last_error      TEXT
                    )
                    """
                )
                cols = {row[1] for row in con.execute("PRAGMA table_info(spool)").fetchall()}
                if "batch_id" not in cols:
                    con.execute("ALTER TABLE spool ADD COLUMN batch_id INTEGER NOT NULL DEFAULT 0")
                if "payload_version" not in cols:
                    con.execute(
                        "ALTER TABLE spool ADD COLUMN payload_version INTEGER NOT NULL DEFAULT 0"
                    )
                if "status" not in cols:
                    con.execute("ALTER TABLE spool ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
                if "lease_id" not in cols:
                    con.execute("ALTER TABLE spool ADD COLUMN lease_id TEXT")
                if "lease_until" not in cols:
                    con.execute("ALTER TABLE spool ADD COLUMN lease_until TEXT")
                if "attempts" not in cols:
                    con.execute("ALTER TABLE spool ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
                if "last_error" not in cols:
                    con.execute("ALTER TABLE spool ADD COLUMN last_error TEXT")
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS spool_quarantine (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        original_id     INTEGER,
                        payload_version INTEGER,
                        symbol_id       INTEGER,
                        tf_code         TEXT,
                        staging_table   TEXT,
                        tv_symbol       TEXT,
                        error           TEXT,
                        created_at      TEXT,
                        quarantined_at  TEXT DEFAULT (datetime('now'))
                    )
                    """
                )
                # Crash/restart recovery: a 'staged' row means a previous
                # process successfully wrote it to SQL Server
                # staging but had not yet confirmed the Fact_OHLCV commit
                # when it stopped (graceful shutdown that ran out of time,
                # or a hard crash). The in-memory deferred-ETL retry state
                # that would normally track "this still needs Fact commit"
                # does not survive a restart, so without this reset these
                # rows would sit in 'staged' forever - excluded from
                # lease_batch() by design, but with nothing left to ever
                # retry their ETL. Resetting to 'pending' re-enters the
                # normal pending -> leased -> staged -> ack cycle; both
                # legs of that cycle are idempotent (MERGE / NOT EXISTS),
                # so redoing the staging write for an already-staged,
                # unchanged row is a safe no-op.
                reset = con.execute(
                    "UPDATE spool SET status='pending', lease_id=NULL, lease_until=NULL "
                    "WHERE status='staged'"
                ).rowcount
                con.commit()
        if reset:
            self.logger.warning(
                "[SPOOL] Recovered %d row(s) left 'staged' by a previous run - re-queued for staging+ETL.",
                reset,
            )
        self.logger.info("[SPOOL] Persistent write-ahead outbox ready: %s", self.path)

    # -- write path ----------------------------------------------------

    def persist_pending(self, item: tuple) -> int | None:
        """Durably store one item BEFORE it is handed to the RAM dispatch
        queue. Returns the row id (used later for ack/release_for_retry),
        or None if the outbox itself is full - callers must treat None as
        "cannot safely accept this item right now" (pause + alert), not as
        "silently drop"."""
        batch_id, symbol_id, tf_code, staging_table, tv_symbol, df = self._normalize_item(item)
        blob = self._encode_payload(df)
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                count = con.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
                if count >= self.max_rows:
                    self.logger.error(
                        "[SPOOL] Outbox is full (%d rows) - cannot durably accept bar %s %s.",
                        self.max_rows,
                        tv_symbol,
                        tf_code,
                    )
                    return None
                cur = con.execute(
                    "INSERT INTO spool "
                    "(batch_id,payload_version,symbol_id,tf_code,staging_table,tv_symbol,bar_data,status) "
                    "VALUES (?,?,?,?,?,?,?,'pending')",
                    (
                        batch_id,
                        PAYLOAD_VERSION,
                        symbol_id,
                        tf_code,
                        staging_table,
                        tv_symbol,
                        blob,
                    ),
                )
                con.commit()
                return int(cur.lastrowid)

    # -- lease / ack / retry path ---------------------------------------

    def lease_batch(self, *, limit: int = 200, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> list[tuple[int, tuple]]:
        """Atomically claim up to `limit` rows that are pending, or whose
        previous lease expired (crash/timeout recovery), for processing.
        Rows stay in the table (not deleted) until ack() confirms success -
        so a crash between lease_batch() and ack() simply leaves the row
        eligible to be leased again once its lease_until passes."""
        lease_id = uuid.uuid4().hex[:12]
        leased: list[tuple[int, tuple]] = []
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                now = time.time()
                lease_until = _iso(now + lease_seconds)
                rows = con.execute(
                    """
                    SELECT id, batch_id, payload_version, symbol_id, tf_code, staging_table, tv_symbol, bar_data
                    FROM spool
                    WHERE status = 'pending'
                       OR (status = 'leased' AND (lease_until IS NULL OR lease_until < ?))
                    ORDER BY id
                    LIMIT ?
                    """,
                    (_iso(now), limit),
                ).fetchall()
                ids = [row[0] for row in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    con.execute(
                        f"UPDATE spool SET status='leased', lease_id=?, lease_until=?, "
                        f"attempts = attempts + 1 WHERE id IN ({placeholders})",
                        (lease_id, lease_until, *ids),
                    )
                    con.commit()
                for row_id, batch_id, payload_version, sym_id, tf_code, stg_tbl, tv_sym, blob in rows:
                    try:
                        df = self._decode_payload(blob, payload_version)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.error(
                            "[SPOOL] Quarantining corrupt row id=%s %s %s: %s",
                            row_id, tv_sym, tf_code, exc,
                        )
                        self._quarantine_and_delete(con, row_id=row_id, payload_version=int(payload_version or 0),
                                                     sym_id=int(sym_id), tf_code=str(tf_code), stg_tbl=str(stg_tbl),
                                                     tv_sym=str(tv_sym), error=f"{type(exc).__name__}: {exc}")
                        continue
                    leased.append((int(row_id), (int(batch_id), int(sym_id), str(tf_code), str(stg_tbl), str(tv_sym), df)))
        return leased

    def ack(self, row_id: int) -> None:
        """Confirm row_id is fully durable end-to-end (Fact-committed, or
        - for the "nothing to write" case - confirmed to need no write at
        all) and delete it."""
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                con.execute("DELETE FROM spool WHERE id=?", (row_id,))
                con.commit()

    def mark_staged(self, row_id: int) -> None:
        """insert_staging_batch() succeeded for this row: it is durably in
        SQL Server staging, but run_etl_direct() has not (yet) succeeded.
        Transitions the row out of lease_batch()'s pending/leased pool so
        it is not re-staged while awaiting the Fact commit - it stays on
        disk, unacked, until ack_staged_for_key() (Fact commit succeeded)
        or a restart's pending-reset (Fact commit never happened before
        the process stopped) resolves it."""
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                con.execute(
                    "UPDATE spool SET status='staged', lease_id=NULL, lease_until=NULL WHERE id=?",
                    (row_id,),
                )
                con.commit()

    def ack_staged_for_key(self, symbol_id: int, tf_code: str, staging_table: str, tv_symbol: str) -> int:
        """Ack (delete) every 'staged' row matching this exact symbol/
        timeframe/table/symbol-name key - called once run_etl_direct()
        succeeds for that key. A single successful ETL call migrates every
        not-yet-migrated staging row for that key (see warehouse.writer.
        run_etl_direct's NOT EXISTS scan), not just the most recent one -
        so any other rows this key accumulated while deferred (warehouse
        maintenance lock held, or prior ETL attempts failing) are equally
        covered by that same success and must be acked together, not just
        the row that happened to trigger this particular attempt."""
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                cur = con.execute(
                    "DELETE FROM spool WHERE status='staged' AND symbol_id=? AND tf_code=? "
                    "AND staging_table=? AND tv_symbol=?",
                    (symbol_id, tf_code, staging_table, tv_symbol),
                )
                con.commit()
                return int(cur.rowcount or 0)

    def release_for_retry(self, row_id: int, *, error: str | None = None) -> None:
        """Give up on the current attempt WITHOUT deleting the row - it
        becomes eligible for lease_batch() again immediately. Never drops
        data: this is the fix for the previous behavior where a write that
        failed after all retries was simply task_done()'d and discarded."""
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                con.execute(
                    "UPDATE spool SET status='pending', lease_id=NULL, lease_until=NULL, "
                    "last_error=? WHERE id=?",
                    ((error or "")[:500], row_id),
                )
                con.commit()

    # -- introspection / maintenance ------------------------------------

    def count(self) -> int | None:
        try:
            with self._lock:
                with closing(sqlite3.connect(self.path)) as con:
                    row = con.execute("SELECT COUNT(*) FROM spool").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return None

    def count_unstaged(self) -> int | None:
        """Rows still in pending/leased (i.e. not yet even staged).

        Used for the live db_worker's shutdown-loop exit condition instead
        of the raw total count(): 'staged' rows awaiting a Fact commit are
        drained by the separate _deferred_etl shutdown-flush pass in
        live/engine.py (which runs once, after this loop exits), not by
        this loop looping until the outbox is fully empty - if it waited
        for that too, a deferred ETL blocked on a long-running warehouse
        maintenance job could keep the live process from ever shutting
        down.
        """
        try:
            with self._lock:
                with closing(sqlite3.connect(self.path)) as con:
                    row = con.execute(
                        "SELECT COUNT(*) FROM spool WHERE status IN ('pending','leased')"
                    ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return None

    def count_staged_pending_fact(self) -> int | None:
        """Rows durably staged but still awaiting a successful Fact
        commit - operator-facing visibility into the exact gap this
        design closes (see module docstring)."""
        try:
            with self._lock:
                with closing(sqlite3.connect(self.path)) as con:
                    row = con.execute(
                        "SELECT COUNT(*) FROM spool WHERE status='staged'"
                    ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return None

    def count_pending_retry(self) -> int | None:
        """Rows currently sitting in `pending` with attempts > 0, i.e.
        genuinely stuck/retrying rows rather than the normal in-flight
        pending->leased->ack turnover."""
        try:
            with self._lock:
                with closing(sqlite3.connect(self.path)) as con:
                    row = con.execute(
                        "SELECT COUNT(*) FROM spool WHERE status='pending' AND attempts > 0"
                    ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return None

    def cleanup_old(self, *, hours: int = 48) -> int:
        """Prune only the quarantine table (corrupt/unrecoverable rows
        already pulled out of the retry path). Never deletes a pending or
        leased row by age - an un-acked row means the candle has not been
        confirmed durable in SQL Server yet, and age alone is not a reason
        to give up on it; the operator-facing spool_pending metric and
        CRITICAL alert (when the outbox nears capacity) are the intended
        signal for a stuck backlog, not silent expiry."""
        try:
            with self._lock:
                with closing(sqlite3.connect(self.path)) as con:
                    con.execute(
                        "DELETE FROM spool_quarantine WHERE quarantined_at < datetime('now', ?)",
                        (f"-{int(hours)} hours",),
                    )
                    deleted = con.total_changes
                    con.commit()
            if deleted:
                self.logger.info("[SPOOL] Cleaned up %d stale quarantine entr(y/ies) (>%dh).", deleted, hours)
            return deleted
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SPOOL] cleanup_old failed: %s", exc)
            return 0

    # -- legacy-shaped helpers kept for compatibility --------------------

    def write(self, item: tuple) -> bool:
        """Back-compat alias for the old overflow-only write() - now just
        persist_pending() with a bool result."""
        return self.persist_pending(item) is not None

    # -- internals --------------------------------------------------------

    def _quarantine_and_delete(self, con, *, row_id, payload_version, sym_id, tf_code, stg_tbl, tv_sym, error) -> None:
        con.execute(
            """
            INSERT INTO spool_quarantine
                (original_id,payload_version,symbol_id,tf_code,staging_table,tv_symbol,error,created_at)
            SELECT id,?,?,?,?,?,?,created_at
            FROM spool
            WHERE id=?
            """,
            (payload_version, sym_id, tf_code, stg_tbl, tv_sym, error[:500], row_id),
        )
        con.execute("DELETE FROM spool WHERE id=?", (row_id,))
        con.commit()

    @staticmethod
    def _normalize_item(item: tuple) -> tuple[int, int, str, str, str, Any]:
        if len(item) == 6:
            batch_id, symbol_id, tf_code, staging_table, tv_symbol, df = item
        else:
            batch_id = 0
            symbol_id, tf_code, staging_table, tv_symbol, df = item
        return int(batch_id), int(symbol_id), str(tf_code), str(staging_table), str(tv_symbol), df

    @staticmethod
    def _encode_payload(df: Any) -> bytes:
        return pickle.dumps(
            {
                PAYLOAD_MARKER: True,
                "version": PAYLOAD_VERSION,
                "kind": "ohlcv_frame",
                "data": df,
            },
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    @staticmethod
    def _decode_payload(blob: bytes, payload_version: int = 0) -> Any:
        obj = pickle.loads(blob)
        if isinstance(obj, dict) and obj.get(PAYLOAD_MARKER):
            version = int(obj.get("version") or 0)
            if version != PAYLOAD_VERSION:
                raise ValueError(f"unsupported spool payload version {version}")
            if obj.get("kind") != "ohlcv_frame":
                raise ValueError(f"unsupported spool payload kind {obj.get('kind')!r}")
            return obj.get("data")
        if int(payload_version or 0) == 0:
            return obj
        raise ValueError(f"missing spool payload envelope for version {payload_version}")


def _iso(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch_seconds))
