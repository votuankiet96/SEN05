"""Durable SQLite spool for live OHLCV batches."""

from __future__ import annotations

import logging
import pickle
import queue
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

PAYLOAD_VERSION = 1
PAYLOAD_MARKER = "__sen05_spool_payload__"


class LiveSpool:
    """Disk-backed fallback used when the DB queue and RAM overflow are full."""

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
                        created_at      TEXT    DEFAULT (datetime('now'))
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
                con.commit()
        self.logger.info("[SPOOL] Persistent spool ready: %s", self.path)

    def write(self, item: tuple) -> bool:
        batch_id, symbol_id, tf_code, staging_table, tv_symbol, df = self._normalize_item(item)
        blob = self._encode_payload(df)
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                count = con.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
                if count >= self.max_rows:
                    self.logger.error(
                        "[SPOOL] Offline spool is full (%d rows) - dropping bar %s %s.",
                        self.max_rows,
                        tv_symbol,
                        tf_code,
                    )
                    return False
                con.execute(
                    "INSERT INTO spool "
                    "(batch_id,payload_version,symbol_id,tf_code,staging_table,tv_symbol,bar_data) "
                    "VALUES (?,?,?,?,?,?,?)",
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
        return True

    def flush_to_queue(self, target_queue: queue.Queue, *, limit: int = 200) -> int:
        flushed = 0
        with self._lock:
            with closing(sqlite3.connect(self.path)) as con:
                rows = con.execute(
                    "SELECT id,batch_id,payload_version,symbol_id,tf_code,staging_table,tv_symbol,bar_data "
                    "FROM spool ORDER BY id LIMIT ?",
                    (limit,),
                ).fetchall()
                for row_id, batch_id, payload_version, sym_id, tf_code, stg_tbl, tv_sym, blob in rows:
                    try:
                        df = self._decode_payload(blob, payload_version)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.error(
                            "[SPOOL] Quarantining corrupt row id=%s %s %s: %s",
                            row_id,
                            tv_sym,
                            tf_code,
                            exc,
                        )
                        self._quarantine_row(
                            con,
                            row_id=row_id,
                            payload_version=int(payload_version or 0),
                            sym_id=int(sym_id),
                            tf_code=str(tf_code),
                            stg_tbl=str(stg_tbl),
                            tv_sym=str(tv_sym),
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        continue
                    try:
                        target_queue.put_nowait((batch_id, sym_id, tf_code, stg_tbl, tv_sym, df))
                    except queue.Full:
                        break
                    con.execute("DELETE FROM spool WHERE id=?", (row_id,))
                    flushed += 1
                con.commit()
        if flushed:
            self.logger.info("[SPOOL] Recovered %d bar(s) from persistent spool.", flushed)
        return flushed

    def count(self) -> int | None:
        try:
            with self._lock:
                with closing(sqlite3.connect(self.path)) as con:
                    row = con.execute("SELECT COUNT(*) FROM spool").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return None

    def cleanup_old(self, *, hours: int = 48) -> int:
        try:
            with self._lock:
                with closing(sqlite3.connect(self.path)) as con:
                    con.execute(
                        "DELETE FROM spool WHERE created_at < datetime('now', ?)",
                        (f"-{int(hours)} hours",),
                    )
                    deleted = con.total_changes
                    con.commit()
            if deleted:
                self.logger.info("[SPOOL] Cleaned up %d stale entries (>%dh).", deleted, hours)
            return deleted
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SPOOL] cleanup_old failed: %s", exc)
            return 0

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

    @staticmethod
    def _quarantine_row(
        con: sqlite3.Connection,
        *,
        row_id: int,
        payload_version: int,
        sym_id: int,
        tf_code: str,
        stg_tbl: str,
        tv_sym: str,
        error: str,
    ) -> None:
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
