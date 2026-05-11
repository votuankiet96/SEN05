from __future__ import annotations

import pandas as pd

from core_python.strategies.combo.config import normalize_params
from core_python.strategies.combo.signals import detect_combo_signals
from core_python.strategies.combo.trend_filter import (
    merge_combo_trend_into_entry,
    prepare_combo_trend_frame,
)


def _probe_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-03 00:00:00", "2026-01-03 01:00:00"]),
            "open": [10.0, 10.0],
            "high": [12.0, 11.0],
            "low": [9.0, 8.0],
            "close": [11.0, 9.0],
            "ma": [10.0, 10.0],
            "prev_close": [9.0, 11.0],
            "prev_ma": [10.0, 10.0],
            "macd_h": [1.0, -1.0],
            "atr": [2.0, 2.0],
        }
    )


def test_combo_htf_filter_disabled_preserves_raw_signals() -> None:
    params = normalize_params({"HTF_TREND_ENABLED": False, "X": 0}, "US30")
    out = detect_combo_signals(_probe_frame(), symbol="US30", params=params)

    assert out["raw_signal"].tolist() == [1, -1]
    assert out["signal"].tolist() == [1, -1]
    assert out["filter_reason"].tolist() == ["", ""]


def test_combo_htf_filter_blocks_countertrend_entries() -> None:
    params = normalize_params({"HTF_TREND_ENABLED": True, "HTF_NEUTRAL_BLOCK": True, "X": 0}, "US30")
    frame = _probe_frame()
    frame["htf_trend"] = [1, 1]
    frame["htf_trend_label"] = ["Bullish", "Bullish"]

    out = detect_combo_signals(frame, symbol="US30", params=params)

    assert out["raw_signal"].tolist() == [1, -1]
    assert out["signal"].tolist() == [1, 0]
    assert out["filter_reason"].iloc[1] == "D1 bullish trend blocks SELL"
    assert "D1 trend: Bullish" in out["signal_reason"].iloc[0]


def test_combo_htf_filter_blocks_neutral_when_configured() -> None:
    params = normalize_params({"HTF_TREND_ENABLED": True, "HTF_NEUTRAL_BLOCK": True, "X": 0}, "US30")
    frame = _probe_frame()
    frame["htf_trend"] = [0, 0]
    frame["htf_trend_label"] = ["Neutral", "Neutral"]

    out = detect_combo_signals(frame, symbol="US30", params=params)

    assert out["raw_signal"].tolist() == [1, -1]
    assert out["signal"].tolist() == [0, 0]
    assert out["filter_reason"].tolist() == ["D1 trend neutral", "D1 trend neutral"]


def test_combo_htf_merge_uses_only_closed_trend_bars() -> None:
    params = normalize_params(
        {
            "HTF_TREND_ENABLED": True,
            "HTF_TF": "D1",
            "HTF_FAST_EMA": 2,
            "HTF_SLOW_EMA": 3,
            "HTF_SLOPE_LOOKBACK": 1,
        },
        "US30",
    )
    trend_raw = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
            "open": [10.0, 11.0, 12.0, 13.0],
            "high": [11.0, 12.0, 13.0, 14.0],
            "low": [9.0, 10.0, 11.0, 12.0],
            "close": [10.0, 11.0, 12.0, 13.0],
            "volume": [1.0, 1.0, 1.0, 1.0],
        }
    )
    entry = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-01 12:00:00", "2026-01-02 00:00:00", "2026-01-02 01:00:00"]),
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
        }
    )

    trend = prepare_combo_trend_frame(trend_raw, params)
    merged = merge_combo_trend_into_entry(entry, trend)

    assert pd.isna(merged.loc[0, "htf_bartime"])
    assert merged.loc[1, "htf_bartime"] == pd.Timestamp("2026-01-01")
    assert merged.loc[2, "htf_bartime"] == pd.Timestamp("2026-01-01")
