from __future__ import annotations

import pytest


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _LegacyCursor:
    def __init__(self, rowcount: int = 7):
        self.rowcount = rowcount
        self.executed: list[tuple[str, tuple | None]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self


class _AggregateCursor:
    def __init__(self):
        self.executed: list[tuple[str, tuple | None]] = []
        self.rowcount = -1
        self._fetchall_rows = [("H3", 3), ("H6", 6)]
        self._fetchone_rows = iter([(3,)])
        self._write_rowcounts = iter([11, 29])

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT Code, TimeframeID" in sql or "/* continuity_check */" in sql:
            self.rowcount = -1
        else:
            self.rowcount = next(self._write_rowcounts)
        return self

    def fetchall(self):
        return self._fetchall_rows

    def fetchone(self):
        return next(self._fetchone_rows, None)


def test_run_etl_aggregate_uses_fact_rebuild_for_derived_tfs(load_module, monkeypatch):
    db = load_module("modules/db_connector.py", "db_connector")
    calls: list[tuple[int, str]] = []

    def _aggregate(symbol_id: int, target_tf: str) -> int:
        calls.append((symbol_id, target_tf))
        return 13

    monkeypatch.setattr(db, "aggregate_from_fact", _aggregate)
    monkeypatch.setattr(
        db,
        "get_connection",
        lambda: pytest.fail("legacy stored procedure path must not be used for derived TFs"),
    )

    rows = db.run_etl_aggregate(81, "H6", "SEN.Staging_H3")

    assert rows == 13
    assert calls == [(81, "H6")]


def test_run_etl_aggregate_keeps_legacy_fallback_for_unknown_tf(load_module, monkeypatch):
    db = load_module("modules/db_connector.py", "db_connector")
    cursor = _LegacyCursor(rowcount=5)
    conn = _FakeConnection(cursor)

    monkeypatch.setattr(db, "get_connection", lambda: conn)
    monkeypatch.setattr(
        db,
        "aggregate_from_fact",
        lambda *args, **kwargs: pytest.fail("aggregate_from_fact should not run for unknown TFs"),
    )

    rows = db.run_etl_aggregate(99, "W1", "SEN.Staging_H4")

    assert rows == 5
    assert conn.committed is True
    assert conn.closed is True
    assert cursor.executed == [
        ("EXEC DWH.usp_AggregateFromStaging ?, ?, ?", (99, "SEN.Staging_H4", "W1"))
    ]


def test_aggregate_from_fact_deletes_stale_rows_before_merge(load_module, monkeypatch):
    db = load_module("modules/db_connector.py", "db_connector")
    cursor = _AggregateCursor()
    conn = _FakeConnection(cursor)

    monkeypatch.setattr(db, "get_connection", lambda: conn)

    rows = db.aggregate_from_fact(81, "H6")

    assert rows == 29
    assert conn.committed is True
    assert conn.closed is True
    assert len(cursor.executed) == 4

    _, select_params = cursor.executed[0]
    delete_sql, delete_params = cursor.executed[1]
    merge_sql, merge_params = cursor.executed[2]
    continuity_sql, continuity_params = cursor.executed[3]

    assert select_params == ("H3", "H6")
    assert "DELETE tgt" in delete_sql
    assert "NOT EXISTS" in delete_sql
    assert delete_params == (360, 360, 81, 3, 81, 6, 2)

    assert "MERGE DWH.Fact_OHLCV AS tgt" in merge_sql
    assert "WHERE a.SrcCount = ?" in merge_sql
    assert merge_params == (360, 360, 81, 3, 81, 6, 2)

    assert "/* continuity_check */" in continuity_sql
    assert continuity_params == (81, 6, 360, 0.5)
