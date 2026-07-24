from __future__ import annotations

from datetime import datetime

import pandas as pd

from core_python.chart import server
from core_python.data import loader
from core_python.export.service import export_range_suffix


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


class _BoundsCursor:
    description = [("start_time",), ("end_time",)]

    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, _query, params):
        self.params = params

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _BoundsConnection:
    def __init__(self, rows):
        self.cursor_obj = _BoundsCursor(rows)
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


def test_load_bounds_returns_first_and_latest_bar(monkeypatch) -> None:
    conn = _BoundsConnection(
        rows=[(datetime(2025, 3, 14, 0, 0), datetime(2026, 5, 13, 12, 0))]
    )
    monkeypatch.setattr(loader, "get_connection", lambda: conn)
    monkeypatch.setattr(loader, "get_symbol", lambda _symbol: {"symbol_id": 42})

    bounds = loader.load_bounds("BTCUSD", "M30")

    assert bounds == (
        pd.Timestamp("2025-03-14 00:00:00"),
        pd.Timestamp("2026-05-13 12:00:00"),
    )
    assert conn.closed
    assert conn.cursor_obj.params == (42, "M30")


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


def test_signal_export_rows_excludes_non_signal_bars() -> None:
    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                [
                    "2026-01-01 00:00:00",
                    "2026-01-01 00:45:00",
                    "2026-01-01 01:30:00",
                    "2026-01-01 02:15:00",
                ]
            ),
            "signal": [0, 1, -1, None],
            "close": [100.0, 101.0, 99.0, 100.5],
        }
    )

    out = server._signal_export_rows(frame)

    assert out["signal"].tolist() == [1, -1]
    assert out["bartime"].tolist() == [
        pd.Timestamp("2026-01-01 00:45:00"),
        pd.Timestamp("2026-01-01 01:30:00"),
    ]


def test_ai_trend_scan_preview_clips_heavy_date_range() -> None:
    window = server._date_window_from_args(
        {"start_date": "01/03/2026", "end_date": "30/04/2026"}
    )

    preview = server._scan_preview(
        "ai_trend",
        {"TREND_TF": "H1", "ENTRY_TF": "M5"},
        6000,
        window,
    )

    assert preview.date_window is not None
    assert preview.date_window.end == window.end
    assert preview.date_window.start > window.start
    assert preview.warning is not None
    assert "CSV export still uses the full" in preview.warning


def test_ai_trend_scan_preview_caps_bars_mode_without_touching_export() -> None:
    preview = server._scan_preview(
        "ai_trend",
        {
            "TREND_TF": "H4",
            "ENTRY_TF": "M15",
            "TREND_BARS": "500",
            "AUTO_ENTRY_BARS": "true",
        },
        8000,
        None,
    )

    assert preview.date_window is None
    assert int(preview.args["ENTRY_BARS"]) <= server.AI_TREND_PREVIEW_ENTRY_BARS
    assert int(preview.args["TREND_BARS"]) < 500
    assert preview.warning is not None


def test_export_range_suffix_includes_selected_date_window() -> None:
    window = server._date_window_from_args(
        {"start_date": "14/03/2026", "end_date": "13/05/2026"}
    )

    assert export_range_suffix(window, 2000) == "20260314_20260513"


def test_export_range_suffix_identifies_bars_mode() -> None:
    assert export_range_suffix(None, 1500) == "latest_1500bars"


def test_ai_trend_data_range_requires_both_trend_and_entry_timeframes() -> None:
    requirements = server._strategy_data_requirements(
        "ai_trend",
        {"symbols": "BTCUSD,J225", "TREND_TF": "H2", "ENTRY_TF": "M30"},
        "BTCUSD",
        "M30",
    )

    assert requirements == [
        ("BTCUSD", "H2"),
        ("J225", "H2"),
        ("BTCUSD", "M30"),
        ("J225", "M30"),
    ]


def test_combo_dashboard_defaults_to_us30_h4() -> None:
    assert server._strategy_defaults("combo") == {"symbol": "US30", "tf": "H4"}


def test_other_dashboard_defaults_keep_global_config() -> None:
    assert server._strategy_defaults("ma_cross") == {"symbol": "BTCUSD", "tf": "M5"}
