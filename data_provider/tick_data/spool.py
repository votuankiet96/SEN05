"""SQLite spool and batch flushing for cTrader/FTMO ticks."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .ticks import TickRecord

logger = logging.getLogger(__name__)


class TickSpool:
    """Durable local overflow queue for live and backfill tick ingest."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tick_spool (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_hash BLOB NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def append_many(self, records: list[TickRecord]) -> int:
        """Persist records that failed SQL insertion, ignoring duplicates."""
        if not records:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                sqlite3.Binary(record.event_hash or b""),
                json.dumps(record.to_json_dict(), separators=(",", ":"), sort_keys=True),
                now,
            )
            for record in records
        ]
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR IGNORE INTO tick_spool
                    (event_hash, payload_json, created_at_utc)
                VALUES (?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            return cursor.rowcount

    def read_batch(self, limit: int) -> list[tuple[int, TickRecord]]:
        """Read the oldest spooled records without deleting them yet."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT seq, payload_json
                FROM tick_spool
                ORDER BY seq
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [(int(seq), TickRecord.from_json_dict(json.loads(payload))) for seq, payload in rows]

    def delete_through(self, seq: int) -> int:
        """Delete spooled rows only after SQL insertion has succeeded."""
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM tick_spool WHERE seq <= ?", (int(seq),))
            conn.commit()
            return cursor.rowcount

    def count(self) -> int:
        """Return current backlog size for operator/health checks."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM tick_spool").fetchone()
        return int(row[0] if row else 0)


class TickBatcher:
    """Collect ticks, write them in batches, and spool on SQL failures."""

    def __init__(
        self,
        store,
        spool: TickSpool,
        batch_size: int,
        flush_seconds: float,
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
        self.last_tick_utc: datetime | None = None

    def add(self, record: TickRecord) -> None:
        """Append one normalized tick and flush when size/time thresholds hit."""
        self._pending.append(record)
        self.last_tick_utc = record.tick_time_utc.astimezone(timezone.utc)
        if self.should_flush():
            self.flush()

    def should_flush(self) -> bool:
        if len(self._pending) >= self.batch_size:
            return True
        return (time.monotonic() - self._last_flush_monotonic) >= self.flush_seconds

    def flush(self) -> None:
        """Write pending ticks to SQL or persist them to SQLite spool on failure."""
        if not self._pending:
            self._last_flush_monotonic = time.monotonic()
            return
        records = self._pending
        self._pending = []
        try:
            inserted = self.store.insert_ticks(records)
        except Exception as exc:
            spooled = self.spool.append_many(records)
            self.rows_spooled += spooled
            if self.on_spooled is not None:
                self.on_spooled(records, spooled, exc)
            logger.exception("SQL tick insert failed; spooled %d ticks: %s", spooled, exc)
        else:
            self.rows_inserted += inserted
            if self.on_inserted is not None:
                try:
                    self.on_inserted(records, inserted)
                except Exception:
                    logger.exception("tick insert callback failed after successful SQL insert")
        finally:
            self._last_flush_monotonic = time.monotonic()

    def drain_spool(self, batch_limit: int | None = None) -> int:
        """Move one spool batch back into SQL after the database recovers."""
        limit = int(batch_limit or self.batch_size)
        batch = self.spool.read_batch(limit)
        if not batch:
            return 0
        max_seq = batch[-1][0]
        records = [record for _seq, record in batch]
        inserted = self.store.insert_ticks(records)
        self.spool.delete_through(max_seq)
        self.rows_inserted += inserted
        if self.on_inserted is not None:
            try:
                self.on_inserted(records, inserted)
            except Exception:
                logger.exception("tick insert callback failed after successful spool drain")
        return inserted
