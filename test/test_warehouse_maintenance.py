"""Tests for core_engine.warehouse.maintenance.purge_staging - the P0-2
crash-recovery fix. Before this fix, purge deleted staging rows based only
on age + IsProcessed=1, with no check that the row had actually reached
Fact_OHLCV. Combined with the ETL-skip-on-staged==0 bug (see
test_historical_pipeline.py), a row stuck behind a broken/skipped ETL call
could be purged after days_to_keep and be gone forever. purge_staging must
now only delete a staging row when a matching Fact_OHLCV row is confirmed
to exist.
"""

from __future__ import annotations

from core_engine.warehouse import maintenance


class _FakeCursor:
    """Simulates DELETE TOP(n): the FIRST call for each distinct staging
    table "removes" first_call_rowcount rows, every following call for
    that same table sees rowcount=0 (so purge_staging's inner while-loop
    must stop after exactly 2 calls per table)."""

    def __init__(self, first_call_rowcount: int = 0):
        self.executed: list[tuple[str, tuple]] = []
        self.rowcount = 0
        self._first_call_rowcount = first_call_rowcount
        self._calls_per_table: dict[str, int] = {}

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))
        table = next((t for t in maintenance._ALL_STAGING_TABLES if t in sql), "?")
        seen = self._calls_per_table.get(table, 0) + 1
        self._calls_per_table[table] = seen
        self.rowcount = self._first_call_rowcount if seen == 1 else 0
        return self


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def test_purge_staging_sql_requires_matching_fact_row(monkeypatch):
    cursor = _FakeCursor(first_call_rowcount=0)
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(maintenance, "get_connection", lambda: conn)

    maintenance.purge_staging(days_to_keep=7)

    assert cursor.executed, "purge_staging must issue at least one DELETE per staging table"
    for sql, params in cursor.executed:
        assert "EXISTS" in sql
        assert "DWH.Fact_OHLCV" in sql
        assert "Dim_Timeframe" in sql
        # params: (-days_to_keep, tf_code)
        assert params[0] == -7
        assert params[1] in maintenance.TF_STAGING or params[1] in {
            v for v in maintenance._STAGING_TABLE_TF_CODE.values()
        }
    assert conn.closed is True


def test_purge_staging_uses_correct_tf_code_per_table(monkeypatch):
    cursor = _FakeCursor(first_call_rowcount=0)
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(maintenance, "get_connection", lambda: conn)

    maintenance.purge_staging(days_to_keep=1)

    seen_tables_to_tf: dict[str, str] = {}
    for sql, params in cursor.executed:
        for table, tf_code in maintenance._STAGING_TABLE_TF_CODE.items():
            if table in sql:
                seen_tables_to_tf[table] = params[1]
                break

    for table, expected_tf in maintenance._STAGING_TABLE_TF_CODE.items():
        assert seen_tables_to_tf.get(table) == expected_tf, (
            f"purge for {table} must scope the Fact-existence check to timeframe {expected_tf}"
        )


def test_purge_staging_stops_when_batch_returns_zero_rows(monkeypatch):
    # first DELETE for each table "removes" some rows, second call must see
    # rowcount 0 and move on rather than looping forever.
    cursor = _FakeCursor(first_call_rowcount=5)
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(maintenance, "get_connection", lambda: conn)

    result = maintenance.purge_staging(days_to_keep=7)

    assert "__error__" not in result
    # Exactly 2 DELETE calls per staging table: one that deletes 5 rows, one
    # that sees 0 and stops that table's loop (a CHECKPOINT call may also be
    # issued in between per HISTORICAL.staging_cleanup_checkpoint - that is
    # not part of what this test is checking).
    delete_calls = [sql for sql, _ in cursor.executed if sql.startswith("DELETE TOP")]
    table_count = len(maintenance._ALL_STAGING_TABLES)
    assert len(delete_calls) == table_count * 2
