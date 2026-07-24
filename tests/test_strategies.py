"""Characterization tests for the Combo and MA Cross strategy pipelines.

These lock in current signal-detection behavior on fixed synthetic fixtures
so the coming file-move/rename refactor stages can be verified to not change
any strategy logic — only where the code lives and how it's imported.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests.fixtures import make_ohlcv


def test_combo_signals_match_golden():
    from core_python.strategies.combo import config as combo_config
    from core_python.strategies.combo.levels import add_combo_levels
    from core_python.strategies.combo.signals import add_combo_indicators, detect_combo_signals

    df = make_ohlcv(300)
    params = combo_config.normalize_params({}, "TESTSYM")
    indicators = add_combo_indicators(df, params)
    signals = detect_combo_signals(indicators, symbol="TESTSYM", params=params)
    enriched = add_combo_levels(signals, params, "TESTSYM")

    assert signals["signal"].value_counts().to_dict() == {0: 266, 1: 17, -1: 17}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 34

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 01:40:00"
    assert int(first["signal"]) == 1
    assert round(float(first["entry_price"]), 6) == 99.614221
    assert round(float(first["sl_price"]), 6) == 98.954182
    assert round(float(first["risk_reward"]), 2) == 3.76

    last = rows.iloc[-1]
    assert str(last["bartime"]) == "2026-01-06 00:25:00"
    assert int(last["signal"]) == -1
    assert round(float(last["entry_price"]), 6) == 88.30068


def test_ma_cross_signals_match_golden():
    from core_python.strategies.ma_cross import config as ma_config
    from core_python.strategies.ma_cross.levels import add_ma_cross_levels
    from core_python.strategies.ma_cross.signals import add_ma_cross_indicators, detect_ma_cross_signals

    df = make_ohlcv(300)
    params = ma_config.normalize_params({}, "TESTSYM")
    indicators = add_ma_cross_indicators(df, params)
    signals = detect_ma_cross_signals(indicators, symbol="TESTSYM", params=params)
    enriched = add_ma_cross_levels(signals, params, "TESTSYM")

    assert signals["signal"].value_counts().to_dict() == {0: 291, -1: 5, 1: 4}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 9

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 05:40:00"
    assert int(first["signal"]) == 1
    assert round(float(first["entry_price"]), 6) == 104.270629
    assert round(float(first["sl_price"]), 6) == 103.811168
    assert round(float(first["tp_price"]), 6) == 106.617045


def test_ma_cross_defaults_match_strategy_specification():
    from core_python.strategies.ma_cross import config

    params = config.normalize_params({}, "US30")

    assert (params["FAST_MA"], params["SLOW_MA"]) == (13, 34)
    assert (
        params["MACD_FAST"],
        params["MACD_SLOW"],
        params["MACD_SIGNAL"],
    ) == (5, 25, 5)
    assert params["ATR_PERIOD"] == 5
    assert params["KTP"] == 2.0
    assert params["X"] == 15.0
    assert config.DEFAULT_TIMEFRAME == "M30"
    assert config.SUPPORTED_TIMEFRAMES == ("M10", "M20", "M30", "M45")


@pytest.mark.parametrize(
    ("symbol", "expected_x"),
    [
        ("US30", 15.0),
        ("US500", 2.0),
        ("US100", 5.0),
        ("DE40", 8.0),
        ("HK50", 20.0),
        ("J225", 20.0),
        ("UK100", 5.0),
    ],
)
def test_ma_cross_uses_reference_x_for_each_index(symbol, expected_x):
    from core_python.strategies.ma_cross import config

    assert config.normalize_params({}, symbol)["X"] == expected_x


def test_ma_cross_requires_macd_histogram_confirmation():
    from core_python.strategies.ma_cross.signals import detect_ma_cross_signals

    frame = pd.DataFrame(
        {
            "fast_ma": [2.0, 2.0, 1.0, 1.0],
            "slow_ma": [1.0, 1.0, 2.0, 2.0],
            "prev_fast_ma": [0.0, 0.0, 3.0, 3.0],
            "prev_slow_ma": [1.0, 1.0, 2.0, 2.0],
            "macd_h": [0.1, -0.1, -0.1, 0.1],
            "atr": [1.0, 1.0, 1.0, 1.0],
        }
    )

    result = detect_ma_cross_signals(frame)

    assert result["signal"].tolist() == [1, 0, -1, 0]


def test_ma_cross_levels_match_us30_reference_sheet():
    from core_python.strategies.ma_cross import config
    from core_python.strategies.ma_cross.levels import add_ma_cross_levels

    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30"]),
            "high": [52277.1, 52277.1],
            "low": [52198.5, 52198.5],
            "close": [52252.8, 52252.8],
            "atr": [55.7, 55.7],
            "signal": [1, -1],
        }
    )
    params = config.normalize_params({"X": 15, "KTP": 2}, "US30")

    result = add_ma_cross_levels(frame, params, "US30")
    buy, sell = result.iloc[0], result.iloc[1]

    assert buy["entry_price"] == pytest.approx(52252.8)
    assert buy["sl_price"] == pytest.approx(52183.5)
    assert buy["tp_price"] == pytest.approx(52364.2)
    assert buy["risk_reward"] == pytest.approx(111.4 / 69.3)

    assert sell["entry_price"] == pytest.approx(52252.8)
    assert sell["sl_price"] == pytest.approx(52292.1)
    assert sell["tp_price"] == pytest.approx(52141.4)
    assert sell["risk_reward"] == pytest.approx(111.4 / 39.3)


def test_ma_cross_rejects_timeframe_outside_execution_set():
    from core_python.engine import run_strategy_on_bars

    with pytest.raises(ValueError, match="supports only these timeframes"):
        run_strategy_on_bars(
            "ma_cross",
            symbol="US30",
            tf="M5",
            bars=make_ohlcv(100),
        )
