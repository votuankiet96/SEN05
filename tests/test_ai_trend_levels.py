from __future__ import annotations

import pandas as pd

from core_python.strategies.ai_trend.levels import add_ai_trend_levels


def _frame(values: dict) -> pd.DataFrame:
    rows = len(next(iter(values.values())))
    bartime = pd.date_range("2026-05-11 00:00:00", periods=rows, freq="45min")
    frame = pd.DataFrame({"bartime": bartime, **values})
    frame["m45_close_time"] = frame["bartime"] + pd.Timedelta(minutes=45)
    return frame


def test_ai_trend_buy_uses_latest_confirmed_dow_low_without_next_bar() -> None:
    frame = _frame(
        {
            "close": [100.0, 103.0, 98.0, 105.0, 104.0, 110.0],
            "signal": [0, 0, 0, 0, 0, 1],
            "dow_pivot_type": [None, None, "low", None, "low", None],
            "dow_pivot_price": [None, None, 98.0, None, 104.0, None],
        }
    )

    out = add_ai_trend_levels(frame, {"DOW_PIVOT_RIGHT": 2, "TP_RR": 1.5})
    signal = out.iloc[-1]

    assert signal["entry_time"] == pd.Timestamp("2026-05-11 04:30:00")
    assert signal["entry_price"] == 110.0
    assert signal["sl_price"] == 98.0
    assert signal["tp_price"] == 128.0
    assert signal["risk_reward"] == 1.5


def test_ai_trend_sell_uses_latest_confirmed_dow_high_not_unconfirmed_high() -> None:
    frame = _frame(
        {
            "close": [125.0, 118.0, 100.0, 105.0, 102.0, 100.0],
            "signal": [0, 0, 0, 0, 0, -1],
            "dow_pivot_type": ["high", None, "high", None, "high", None],
            "dow_pivot_price": [130.0, None, 120.0, None, 110.0, None],
        }
    )

    out = add_ai_trend_levels(frame, {"DOW_PIVOT_RIGHT": 2, "TP_RR": 1.5})
    signal = out.iloc[-1]

    assert signal["entry_price"] == 100.0
    assert signal["sl_price"] == 120.0
    assert signal["tp_price"] == 70.0
    assert signal["risk_reward"] == 1.5


def test_ai_trend_leaves_levels_empty_without_confirmed_dow_structure() -> None:
    frame = _frame(
        {
            "close": [100.0, 101.0, 102.0, 103.0],
            "signal": [0, 0, 0, 1],
            "dow_pivot_type": [None, None, "low", None],
            "dow_pivot_price": [None, None, 99.0, None],
        }
    )

    out = add_ai_trend_levels(frame, {"DOW_PIVOT_RIGHT": 2, "TP_RR": 1.5})
    signal = out.iloc[-1]

    assert signal["entry_time"] == pd.Timestamp("2026-05-11 03:00:00")
    assert signal["entry_price"] == 103.0
    assert pd.isna(signal["sl_price"])
    assert pd.isna(signal["tp_price"])
    assert pd.isna(signal["risk_reward"])
