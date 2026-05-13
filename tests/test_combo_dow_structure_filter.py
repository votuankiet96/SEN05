from __future__ import annotations

import pandas as pd

from core_python.strategies.combo.payload import attach_dow_trend_filter


def _dow_structure_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {"bartime": "2024-01-01", "dow_pivot_type": "low", "dow_label": "LL", "dow_pivot_price": 8.0},
            {"bartime": "2024-01-02", "dow_pivot_type": "high", "dow_label": "LH", "dow_pivot_price": 13.0},
            {"bartime": "2024-01-03", "dow_pivot_type": "low", "dow_label": "HL", "dow_pivot_price": 10.0},
            {"bartime": "2024-01-04", "dow_pivot_type": "high", "dow_label": "HH", "dow_pivot_price": 14.0},
        ]
    )
    frame["bartime"] = pd.to_datetime(frame["bartime"])
    return frame


def _params(mode: str) -> dict[str, object]:
    return {"DOW_TF": "D1", "DOW_PIVOT_RIGHT": 1, "DOW_TREND_FILTER_MODE": mode}


def test_dow_structure_filter_tags_aligned_and_counter_signals() -> None:
    entry = pd.DataFrame(
        [
            {"bartime": pd.Timestamp("2024-01-05"), "signal": 1, "entry_price": 13.0, "signal_reason": "buy"},
            {"bartime": pd.Timestamp("2024-01-05"), "signal": -1, "entry_price": 13.0, "signal_reason": "sell"},
        ]
    )

    out = attach_dow_trend_filter(entry, _dow_structure_frame(), _params("warn_conflicts"))

    assert out["raw_signal"].tolist() == [1, -1]
    assert out["signal"].tolist() == [1, -1]
    assert out["d_structure_label"].tolist() == ["HH+HL", "HH+HL"]
    assert out["d_high_label"].tolist() == ["HH", "HH"]
    assert out["d_low_label"].tolist() == ["HL", "HL"]
    assert out["d_filter_status"].tolist() == ["aligned", "counter_trend"]


def test_dow_structure_filter_blocks_counter_signals_when_required() -> None:
    entry = pd.DataFrame(
        [
            {"bartime": pd.Timestamp("2024-01-05"), "signal": 1, "entry_price": 13.0, "signal_reason": "buy"},
            {"bartime": pd.Timestamp("2024-01-05"), "signal": -1, "entry_price": 13.0, "signal_reason": "sell"},
        ]
    )

    out = attach_dow_trend_filter(entry, _dow_structure_frame(), _params("require_structure"))

    assert out["raw_signal"].tolist() == [1, -1]
    assert out["signal"].tolist() == [1, 0]
    assert out["d_filter_status"].tolist() == ["aligned", "counter_trend"]
    assert "blocked by D structure filter" in out.loc[1, "signal_reason"]


def test_dow_structure_level_mode_blocks_broken_invalidation_level() -> None:
    entry = pd.DataFrame(
        [
            {"bartime": pd.Timestamp("2024-01-05"), "signal": 1, "entry_price": 9.5, "signal_reason": "buy"},
        ]
    )

    out = attach_dow_trend_filter(entry, _dow_structure_frame(), _params("require_structure_level"))

    assert out.loc[0, "raw_signal"] == 1
    assert out.loc[0, "signal"] == 0
    assert out.loc[0, "d_filter_status"] == "level_break"
