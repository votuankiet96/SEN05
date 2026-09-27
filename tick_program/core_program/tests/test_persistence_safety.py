import json

import pytest

from src.configuration import RemoteSymbol, TargetSymbol
from src.pipeline import TickRecord
from src.spool import TickBatcher, TickSpool
from src.sql_store import INSERT_COLUMNS, TickSqlStore


TARGET = TargetSymbol(1, "TEST", "INDEX")
REMOTE = RemoteSymbol(101, "TEST", digits=2)


def record(timestamp_ms: int = 1_000) -> TickRecord:
    return TickRecord.from_historical_quote(
        TARGET,
        REMOTE,
        timestamp_ms,
        10_000_000,
        10_010_000,
        bid_updated=True,
        ask_updated=True,
        ingest_run_id="00000000-0000-0000-0000-000000000001",
    )


class FailingStore:
    def insert_ticks(self, _records):
        raise RuntimeError("sql down")


class FailingSpool:
    def append_many(self, _records):
        raise RuntimeError("disk down")


def test_batch_is_retained_when_sql_and_spool_both_fail() -> None:
    batcher = TickBatcher(FailingStore(), FailingSpool(), 500, 60)
    batcher.add(record())

    with pytest.raises(RuntimeError, match="durable spool also failed"):
        batcher.flush()

    assert len(batcher._pending) == 1


def test_poison_spool_row_is_quarantined_without_blocking_valid_rows(tmp_path) -> None:
    spool = TickSpool(tmp_path / "spool.db")
    valid = record()
    with spool._connect() as conn:
        conn.execute(
            "INSERT INTO tick_spool (event_hash, payload_json, created_at_utc) VALUES (?, ?, ?)",
            (b"x" * 32, "{bad json", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO tick_spool (event_hash, payload_json, created_at_utc) VALUES (?, ?, ?)",
            (valid.event_hash, json.dumps(valid.to_json_dict()), "2026-01-01T00:00:01+00:00"),
        )
        conn.commit()

    batch = spool.read_batch(10)

    assert [item.event_hash for _seq, item in batch] == [valid.event_hash]
    assert spool.count() == 1
    assert spool.quarantine_count() == 1


class FakeCursor:
    def __init__(self, dedup_key: tuple) -> None:
        self.dedup_key = dedup_key
        self.last_sql = ""
        self.executed: list[str] = []

    def execute(self, sql, *params):
        self.last_sql = str(sql)
        self.executed.append(self.last_sql)
        return self

    def executemany(self, sql, params):
        self.last_sql = str(sql)
        self.executed.append(self.last_sql)
        return self

    def fetchall(self):
        if "sys.columns" in self.last_sql:
            return [(column,) for column in INSERT_COLUMNS]
        if "BETWEEN" in self.last_sql:
            return []  # pre-check: nothing already in the DB for this batch's time range
        if "OUTPUT inserted.[TickTimeUtc]" in self.last_sql:
            return [self.dedup_key]
        return []


class FakeConnection:
    def __init__(self, dedup_key: tuple) -> None:
        self.cursor_obj = FakeCursor(dedup_key)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_tick_insert_and_ingest_state_share_one_commit() -> None:
    item = record()
    conn = FakeConnection(item.sql_dedup_key())
    store = TickSqlStore("tick", [TARGET], connection_factory=lambda: conn)

    assert store.insert_ticks([item]) == 1
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert any("MERGE [tick].[IngestState]" in sql for sql in conn.cursor_obj.executed)


class _AlreadyExistsCursor(FakeCursor):
    """Reports the incoming tick as already present for its own time range,
    same as a real re-fetch of an already-inserted window would."""

    def fetchall(self):
        if "BETWEEN" in self.last_sql:
            return [self.dedup_key]
        return super().fetchall()


def test_insert_ticks_skips_a_row_already_in_its_own_time_range() -> None:
    """insert_ticks()'s pre-check (query the batch's own [min, max]
    TickTimeUtc range, skip anything already found there) must reject a
    duplicate without ever reaching the INSERT -- this is the primary
    dedup mechanism now that no full-history index backs every insert."""
    item = record()
    conn = FakeConnection(item.sql_dedup_key())
    conn.cursor_obj = _AlreadyExistsCursor(item.sql_dedup_key())
    store = TickSqlStore("tick", [TARGET], connection_factory=lambda: conn)

    assert store.insert_ticks([item]) == 0
    assert not any("INSERT INTO #TickInsert" in sql for sql in conn.cursor_obj.executed)
    assert conn.commits == 1
