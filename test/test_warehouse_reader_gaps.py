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
