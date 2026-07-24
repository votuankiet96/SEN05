from __future__ import annotations

import pandas as pd

from core_python.strategies.dow_stop import DowStopParams, causal_dow_stop


def _entry_frame(
    *,
    high: list[float],
    low: list[float],
    close: list[float],
    signal: list[int],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-05-11 00:00:00", periods=len(close), freq="45min"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "signal": signal,
        }
    )


def test_causal_dow_stop_does_not_rewrite_old_signal_when_future_pivot_appears() -> None:
    frame = _entry_frame(
        high=[110, 108, 105, 109, 111, 106, 107, 108, 109],
        low=[105, 103, 100, 104, 106, 99, 102, 103, 104],
        close=[108, 106, 104, 108, 110, 101, 110, 107, 108],
        signal=[0, 0, 0, 0, 0, 0, 1, 0, 0],
    )
    params = DowStopParams(left=1, right=2, min_atr_mult=0)

    prefix_stop = causal_dow_stop(frame.iloc[:7], params=params)
    full_stop = causal_dow_stop(frame, params=params)

    assert prefix_stop.iloc[6] == 100.0
    assert full_stop.iloc[6] == 100.0


def test_causal_dow_stop_uses_latest_confirmed_high_for_sell() -> None:
    frame = _entry_frame(
        high=[110, 115, 120, 116, 114, 125, 118, 117, 116],
        low=[100, 105, 108, 106, 104, 109, 107, 106, 105],
        close=[105, 112, 115, 112, 108, 115, 110, 108, 107],
        signal=[0, 0, 0, 0, 0, 0, -1, 0, 0],
    )

    out = causal_dow_stop(frame, params=DowStopParams(left=1, right=2, min_atr_mult=0))

    assert out.iloc[6] == 120.0


def test_causal_dow_stop_leaves_signal_empty_before_any_confirmed_pivot() -> None:
    frame = _entry_frame(
        high=[100, 101, 102],
        low=[99, 98, 97],
        close=[99.5, 100.5, 101.5],
        signal=[0, 0, 1],
    )

    out = causal_dow_stop(frame, params=DowStopParams(left=5, right=3, min_atr_mult=0.5))

    assert pd.isna(out.iloc[2])
