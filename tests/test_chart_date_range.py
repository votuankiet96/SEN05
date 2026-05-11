from __future__ import annotations

from datetime import datetime

import pandas as pd

from core_python.chart import server
from core_python.data import loader


class _FakeCursor:
    description = [
        ("bartime",),
        ("open",),
        ("high",),
        ("low",),
        ("close",),
        ("volume",),
    ]

    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, _query, params):
        self.params = params

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_load_range_extends_sql_bounds_for_warmup_and_tail(monkeypatch) -> None:
    conn = _FakeConnection(
        rows=[
            (datetime(2026, 1, 10, 0, 0), 1.0, 2.0, 0.5, 1.5, 100.0),
        ]
    )
    monkeypatch.setattr(loader, "get_connection", lambda: conn)
    monkeypatch.setattr(loader, "get_symbol", lambda _symbol: {"symbol_id": 42})

    out = loader.load_range(
        "BTCUSD",
        "M45",
        "2026-01-10",
        "2026-01-11",
        warmup_bars=2,
        tail_bars=1,
    )

    assert len(out) == 1
    assert conn.closed
    assert conn.cursor_obj.params == (
        42,
        "M45",
        datetime(2026, 1, 9, 22, 30),
        datetime(2026, 1, 11, 0, 45),
    )


def test_date_window_treats_end_date_as_inclusive_day() -> None:
    window = server._date_window_from_args(
        {"start_date": "01/01/2026", "end_date": "01/01/2026"}
    )

    assert window is not None
    assert window.start == pd.Timestamp("2026-01-01 00:00:00")
    assert window.end == pd.Timestamp("2026-01-02 00:00:00")
    assert window.start_label == "01/01/2026"
    assert window.end_label == "01/01/2026"


def test_date_window_keeps_dd_mm_yyyy_unambiguous() -> None:
    window = server._date_window_from_args(
        {"start_date": "05/11/2026", "end_date": "05/11/2026"}
    )

    assert window is not None
    assert window.start == pd.Timestamp("2026-11-05 00:00:00")
    assert window.end == pd.Timestamp("2026-11-06 00:00:00")


def test_trim_to_window_removes_warmup_and_tail_rows() -> None:
    window = server._date_window_from_args(
        {"start_date": "2026-01-01", "end_date": "2026-01-01"}
    )
    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                [
                    "2025-12-31 23:15:00",
                    "2026-01-01 00:00:00",
                    "2026-01-01 23:15:00",
                    "2026-01-02 00:00:00",
                ]
            ),
            "signal": [0, 1, -1, 0],
        }
    )

    out = server._trim_to_window(frame, window)

    assert out["bartime"].tolist() == [
        pd.Timestamp("2026-01-01 00:00:00"),
        pd.Timestamp("2026-01-01 23:15:00"),
    ]
