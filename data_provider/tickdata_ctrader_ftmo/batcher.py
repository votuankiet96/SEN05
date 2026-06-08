"""Batching and spool recovery for live tick ingestion."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .models import TickRecord
from .spool_sqlite import TickSpool
from .store_sql import TickSqlStore

logger = logging.getLogger(__name__)


class TickBatcher:
    """Collect ticks, write them in batches, and spool on SQL failures."""

    def __init__(
        self,
        store: TickSqlStore,
        spool: TickSpool,
        batch_size: int,
        flush_seconds: float,
    ) -> None:
        self.store = store
        self.spool = spool
        self.batch_size = int(batch_size)
        self.flush_seconds = float(flush_seconds)
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
        records = self._pending
        self._pending = []
        try:
            self.rows_inserted += self.store.insert_ticks(records)
        except Exception as exc:
            spooled = self.spool.append_many(records)
            self.rows_spooled += spooled
            logger.exception("SQL tick insert failed; spooled %d ticks: %s", spooled, exc)
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
        return inserted
