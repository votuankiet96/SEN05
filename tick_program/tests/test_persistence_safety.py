import json

import pytest

from tick_engine.data_storage.spool import TickBatcher, TickSpool
from tick_engine.data_storage.store_sql import INSERT_COLUMNS, TickSqlStore
from tick_engine.data_storage.symbols import RemoteSymbol, TargetSymbol
from tick_engine.data_storage.ticks import TickRecord


TARGET = TargetSymbol(1, "TEST", "INDEX", "tick.TEST")
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
    def __init__(self, event_hash: bytes) -> None:
        self.event_hash = event_hash
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
        if "OUTPUT inserted.[EventHash]" in self.last_sql:
            return [(self.event_hash,)]
        return []


class FakeConnection:
    def __init__(self, event_hash: bytes) -> None:
        self.cursor_obj = FakeCursor(event_hash)
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
    conn = FakeConnection(item.event_hash)
    store = TickSqlStore("tick", [TARGET], connection_factory=lambda: conn)

    assert store.insert_ticks([item]) == 1
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert any("MERGE [tick].[IngestState]" in sql for sql in conn.cursor_obj.executed)
