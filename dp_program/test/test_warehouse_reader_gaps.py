"""Tests for the High-9 gap-repair fixes in core_engine.shared.warehouse.reader:
get_internal_gaps() now raises on SQL failure instead of returning {}
(indistinguishable from "scanned, found nothing"), and fact_covers_window()
lets a repair caller re-verify a window actually landed data before
caching it as verified-clean.
"""

from __future__ import annotations

import pytest

from core_engine.shared.warehouse import reader


class _FailingCursor:
    def execute(self, *a, **k):
        raise RuntimeError("SQL Server unreachable")


class _FailingConnection:
    def cursor(self):
        return _FailingCursor()

    def close(self):
        pass


class _OneRowCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return self._row


class _OneRowConnection:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _OneRowCursor(self._row)

    def close(self):
        pass


class _RecordingCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, sql, *args):
        self.sql = sql
        return self

    def fetchall(self):
        return self.rows


class _RecordingConnection:
    def __init__(self, rows):
        self.cursor_instance = _RecordingCursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def test_get_latest_bars_uses_bounded_index_seeks_not_full_fact_aggregate(monkeypatch):
    conn = _RecordingConnection([(81, "M5", "2026-07-22 02:30:00")])
    monkeypatch.setattr(reader, "get_connection", lambda: conn)

    result = reader.get_latest_bars()

    sql = " ".join(conn.cursor_instance.sql.split()).upper()
    assert result == {(81, "M5"): "2026-07-22 02:30:00"}
    assert "CROSS APPLY" in sql
    assert "TOP (1)" in sql
    assert "INDEX(IX_FACT_SYM_TF_TIME)" in sql
    assert "ORDER BY F.BARTIME DESC" in sql
    assert "OPTION (MAXDOP 1, RECOMPILE)" in sql
    assert "MAX(F.BARTIME)" not in sql
    assert conn.closed is True


def test_get_internal_gaps_raises_instead_of_swallowing_sql_failure(monkeypatch):
    monkeypatch.setattr(reader, "get_connection", lambda: _FailingConnection())

    with pytest.raises(RuntimeError):
        reader.get_internal_gaps(["M5"], lookback_days=7)


def test_get_internal_gaps_empty_tf_codes_returns_empty_dict_without_querying():
    # No tf_codes -> nothing to scan; must not attempt a connection at all.
    assert reader.get_internal_gaps([], lookback_days=7) == {}


def test_get_internal_gaps_bounds_scan_by_date_index_and_serial_plan(monkeypatch):
    conn = _RecordingConnection([(81, "M5", "2026-07-20", "2026-07-21", 1440)])
    monkeypatch.setattr(reader, "get_connection", lambda: conn)

    result = reader.get_internal_gaps(["M5"], lookback_days=2)

    sql = " ".join(conn.cursor_instance.sql.split()).upper()
    assert result == {(81, "M5"): [("2026-07-20", "2026-07-21", 1440)]}
    assert "F.DATEKEY >= @MINDATEKEY" in sql
    assert "F.BARTIME >= @CUTOFF" in sql
    assert "INDEX(IX_FACT_DATEKEY)" in sql
    assert "OPTION (MAXDOP 1, RECOMPILE)" in sql
    assert conn.closed is True


def test_fact_covers_window_true_only_when_largest_remaining_gap_is_within_limit(monkeypatch):
    monkeypatch.setattr(reader, "get_connection", lambda: _OneRowConnection((13, 5)))
    assert reader.fact_covers_window(
        11,
        "M5",
        "2026-01-01",
        "2026-01-02",
        max_gap_minutes=15,
    ) is True


@pytest.mark.parametrize("summary_row", [None, (0, None), (1, None), (3, 55)])
def test_fact_covers_window_rejects_empty_singleton_and_partial_repairs(monkeypatch, summary_row):
    monkeypatch.setattr(reader, "get_connection", lambda: _OneRowConnection(summary_row))
    assert reader.fact_covers_window(
        11,
        "M5",
        "2026-01-01",
        "2026-01-02",
        max_gap_minutes=15,
    ) is False


def test_fact_covers_window_rejects_non_positive_threshold_without_querying():
    with pytest.raises(ValueError, match="positive"):
        reader.fact_covers_window(11, "M5", "2026-01-01", "2026-01-02", max_gap_minutes=0)
