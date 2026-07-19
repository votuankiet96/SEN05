"""SQLite spool and batch flushing for cTrader/FTMO ticks."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from tick_engine.data_storage.ticks import TickRecord
from tick_engine.data_storage.store_sql import inserted_records_from_last_insert

logger = logging.getLogger(__name__)


class TickSpool:
    """Durable local overflow queue for backfill tick ingest."""

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
                """
                CREATE TABLE IF NOT EXISTS tick_spool (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_hash BLOB NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tick_spool_quarantine (
                    seq INTEGER PRIMARY KEY,
                    event_hash BLOB,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT,
                    quarantined_at_utc TEXT NOT NULL,
                    error_text TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def append_many(self, records: list[TickRecord]) -> int:
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
                "INSERT OR IGNORE INTO tick_spool (event_hash, payload_json, created_at_utc) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
            return cursor.rowcount

    def read_batch(self, limit: int) -> list[tuple[int, TickRecord]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT seq, event_hash, payload_json, created_at_utc "
                "FROM tick_spool ORDER BY seq LIMIT ?",
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
                        (
                            int(seq),
                            event_hash,
                            str(payload),
                            created_at,
                            quarantined_at,
                            f"{type(exc).__name__}: {exc}"[:1000],
                        ),
                    )
                    conn.execute("DELETE FROM tick_spool WHERE seq = ?", (int(seq),))
                    logger.error("quarantined malformed tick spool row seq=%s: %s", seq, exc)
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

    def oldest_age_seconds(self) -> float | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT MIN(created_at_utc) FROM tick_spool").fetchone()
        if not row or not row[0]:
            return None
        try:
            created = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
        except (TypeError, ValueError):
            return None

    def clear(self) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM tick_spool")
            deleted = cursor.rowcount
            conn.commit()
        return int(deleted if deleted is not None and deleted >= 0 else 0)


class TickBatcher:
    """Collect ticks, write them in batches, and spool on SQL failures."""

    def __init__(
        self,
        store: object,
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
        self._pending.append(record)
        self.last_tick_utc = record.tick_time_utc.astimezone(timezone.utc)
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
                logger.exception("SQL insert and durable spool both failed; retaining %d pending ticks", len(records))
                self._last_flush_monotonic = time.monotonic()
                raise RuntimeError(
                    f"SQL tick insert failed ({exc}); durable spool also failed ({spool_exc})"
                ) from spool_exc
            self._pending.clear()
            self.rows_spooled += spooled
            if self.on_spooled is not None:
                try:
                    self.on_spooled(records, spooled, exc)
                except Exception:
                    logger.exception("tick spool callback failed after successful durable spool write")
            logger.exception("SQL tick insert failed; spooled %d ticks: %s", spooled, exc)
        else:
            self._pending.clear()
            self.rows_inserted += inserted
            if self.on_inserted is not None:
                try:
                    self.on_inserted(
                        inserted_records_from_last_insert(self.store, records),
                        inserted,
                    )
                except Exception:
                    logger.exception("tick insert callback failed after successful SQL insert")
        finally:
            self._last_flush_monotonic = time.monotonic()

    def drain_spool(self, batch_limit: int | None = None) -> int:
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
                self.on_inserted(
                    inserted_records_from_last_insert(self.store, records),
                    inserted,
                )
            except Exception:
                logger.exception("tick insert callback failed after successful spool drain")
        return inserted
