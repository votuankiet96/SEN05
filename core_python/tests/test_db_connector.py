"""Tests for db_connector helpers that don't require a real SQL Server.

`load()`/`load_range()` themselves open a real pyodbc connection and are not
unit-tested here (no test in this repo touches SQL Server, see CLAUDE.md
mục 8) -- `load_range_with_warmup()` is pure orchestration on top of those
two, so it's tested by monkeypatching them.
"""

from __future__ import annotations

import pandas as pd
from core_python.src import db_connector

OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]


def _frame(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """One row per (bartime, close); other OHLCV columns filled arbitrarily."""
    return pd.DataFrame(
        {
            "bartime": pd.to_datetime([r[0] for r in rows]),
            "open": [c for _, c in rows],
            "high": [c for _, c in rows],
            "low": [c for _, c in rows],
            "close": [c for _, c in rows],
            "volume": [1.0] * len(rows),
        }
    )


def test_load_range_with_warmup_merges_dedupes_and_sorts(monkeypatch):
    calls = {}

    def fake_load(symbol, tf, n_bars, *, before=None):
        calls["load"] = (symbol, tf, n_bars, before)
        # Overlaps window's first bar on purpose, with a DIFFERENT close, to
        # prove the window's own value wins on the overlap (keep="last").
        return _frame([("2025-12-30", 1.0), ("2025-12-31", 2.0), ("2026-01-01", 999.0)])

    def fake_load_range(symbol, tf, date_from, date_to):
        calls["load_range"] = (symbol, tf, date_from, date_to)
        return _frame([("2026-01-01", 3.0), ("2026-01-02", 4.0)])

    monkeypatch.setattr(db_connector, "load", fake_load)
    monkeypatch.setattr(db_connector, "load_range", fake_load_range)

    result = db_connector.load_range_with_warmup("US30", "H1", "2026-01-01", "2026-01-02", 3)

    assert calls["load"] == ("US30", "H1", 3, "2026-01-01")
    assert calls["load_range"][2] == pd.Timestamp("2026-01-01").to_pydatetime()
    assert calls["load_range"][3] == pd.Timestamp("2026-01-02").to_pydatetime()

    assert list(result["bartime"]) == [
        pd.Timestamp("2025-12-30"),
        pd.Timestamp("2025-12-31"),
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-01-02"),
    ]
    # The overlapping 2026-01-01 bar keeps the WINDOW's own value (3.0), not
    # the warmup read's stale one (999.0) -- window is the authoritative side.
    overlap_row = result[result["bartime"] == pd.Timestamp("2026-01-01")].iloc[0]
    assert overlap_row["close"] == 3.0


def test_load_range_with_warmup_accepts_timestamp_inputs(monkeypatch):
    """signal_display/server.py passes pd.Timestamp, not strings."""
    calls = {}

    def fake_load(symbol, tf, n_bars, *, before=None):
        calls["before"] = before
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    def fake_load_range(symbol, tf, date_from, date_to):
        calls["date_from"] = date_from
        calls["date_to"] = date_to
        return _frame([("2026-01-01", 1.0)])

    monkeypatch.setattr(db_connector, "load", fake_load)
    monkeypatch.setattr(db_connector, "load_range", fake_load_range)

    from_time = pd.Timestamp("2026-01-01 00:00")
    to_time = pd.Timestamp("2026-01-02 00:00")
    db_connector.load_range_with_warmup("US30", "H1", from_time, to_time, 100)

    assert calls["before"] is from_time
    assert calls["date_from"] == from_time.to_pydatetime()
    assert calls["date_to"] == to_time.to_pydatetime()


def test_load_range_with_warmup_returns_empty_schema_when_both_sides_empty(monkeypatch):
    monkeypatch.setattr(db_connector, "load", lambda *a, **k: pd.DataFrame(columns=OHLCV_COLUMNS))
    monkeypatch.setattr(db_connector, "load_range", lambda *a, **k: pd.DataFrame(columns=OHLCV_COLUMNS))

    result = db_connector.load_range_with_warmup("US30", "H1", "2026-01-01", "2026-01-02", 100)

    assert list(result.columns) == OHLCV_COLUMNS
    assert result.empty
