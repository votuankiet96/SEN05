from __future__ import annotations

import pandas as pd

from core_python.strategies.ai_trend.levels import add_ai_trend_levels


def _frame(values: dict) -> pd.DataFrame:
    rows = len(next(iter(values.values())))
    bartime = pd.date_range("2026-05-11 00:00:00", periods=rows, freq="45min")
    frame = pd.DataFrame({"bartime": bartime, **values})
    frame["m45_close_time"] = frame["bartime"] + pd.Timedelta(minutes=45)
    return frame


def test_ai_trend_buy_uses_latest_causal_dow_low_not_future_low() -> None:
    frame = _frame(
        {
            "high": [110, 108, 105, 109, 111, 106, 107, 108, 109],
            "low": [105, 103, 100, 104, 106, 99, 102, 103, 104],
            "close": [108, 106, 104, 108, 110, 101, 110, 107, 108],
            "signal": [0, 0, 0, 0, 0, 0, 1, 0, 0],
        }
    )

    out = add_ai_trend_levels(frame, _params())
    signal = out.iloc[6]

    assert signal["entry_time"] == pd.Timestamp("2026-05-11 05:15:00")
    assert signal["entry_price"] == 110.0
    assert signal["sl_dow"] == 100.0
    assert signal["sl_price"] == 100.0
    assert signal["tp_price"] == 125.0
    assert signal["risk_reward"] == 1.5


def test_ai_trend_sell_uses_latest_causal_dow_high_not_future_high() -> None:
    frame = _frame(
        {
            "high": [110, 115, 120, 116, 114, 125, 118, 117, 116],
            "low": [100, 105, 108, 106, 104, 109, 107, 106, 105],
            "close": [105, 112, 115, 112, 108, 115, 110, 108, 107],
            "signal": [0, 0, 0, 0, 0, 0, -1, 0, 0],
        }
    )

    out = add_ai_trend_levels(frame, _params())
    signal = out.iloc[6]

    assert signal["entry_price"] == 110.0
    assert signal["sl_dow"] == 120.0
    assert signal["sl_price"] == 120.0
    assert signal["tp_price"] == 95.0
    assert signal["risk_reward"] == 1.5


def test_ai_trend_keeps_signal_but_leaves_sl_dow_empty_without_confirmed_pivot() -> None:
    frame = _frame(
        {
            "high": [100.0, 101.0, 102.0],
            "low": [99.0, 98.0, 97.0],
            "close": [99.5, 100.5, 101.5],
            "signal": [0, 0, 1],
        }
    )

    out = add_ai_trend_levels(
        frame,
        {"DOW_PIVOT_LEFT": 5, "DOW_PIVOT_RIGHT": 3, "DOW_MIN_ATR_MULT": 0.5},
    )
    signal = out.iloc[-1]

    assert signal["signal"] == 1
    assert signal["entry_time"] == pd.Timestamp("2026-05-11 02:15:00")
    assert signal["entry_price"] == 101.5
    assert pd.isna(signal["sl_dow"])
    assert pd.isna(signal["sl_price"])
    assert pd.isna(signal["tp_price"])
    assert pd.isna(signal["risk_reward"])


def _params() -> dict[str, float | int]:
    return {"DOW_PIVOT_LEFT": 1, "DOW_PIVOT_RIGHT": 2, "DOW_MIN_ATR_MULT": 0, "TP_RR": 1.5}
