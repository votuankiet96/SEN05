"""Tests for the High-9 gap-repair fixes in core_engine.warehouse.reader:
get_internal_gaps() now raises on SQL failure instead of returning {}
(indistinguishable from "scanned, found nothing"), and fact_covers_window()
lets a repair caller re-verify a window actually landed data before
caching it as verified-clean.
"""

from __future__ import annotations

import pytest

from core_engine.warehouse import reader


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


def test_get_internal_gaps_raises_instead_of_swallowing_sql_failure(monkeypatch):
    monkeypatch.setattr(reader, "get_connection", lambda: _FailingConnection())

    with pytest.raises(RuntimeError):
        reader.get_internal_gaps(["M5"], lookback_days=7)


def test_get_internal_gaps_empty_tf_codes_returns_empty_dict_without_querying():
    # No tf_codes -> nothing to scan; must not attempt a connection at all.
    assert reader.get_internal_gaps([], lookback_days=7) == {}


def test_fact_covers_window_true_when_a_matching_row_exists(monkeypatch):
    monkeypatch.setattr(reader, "get_connection", lambda: _OneRowConnection((1,)))
    assert reader.fact_covers_window(11, "M5", "2026-01-01", "2026-01-02") is True


def test_fact_covers_window_false_when_no_matching_row(monkeypatch):
    monkeypatch.setattr(reader, "get_connection", lambda: _OneRowConnection(None))
    assert reader.fact_covers_window(11, "M5", "2026-01-01", "2026-01-02") is False
