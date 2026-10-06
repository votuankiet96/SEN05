"""Characterization tests for the Combo and MA Cross strategy pipelines.

These lock in current signal-detection behavior on fixed synthetic fixtures
so future refactors can be verified to not change any strategy logic.
"""

from __future__ import annotations

import pandas as pd
import pytest
from core_python.src import configuration

from tests.fixtures import make_ohlcv

TECHNICAL_TREND_KEYS = {
    "PRICE_VALUE",
    "TARGET_VALUE",
    "AI_MA_LEN",
    "AI_TARGET_LEN",
    "AI_K",
    "AI_SMOOTH",
}
ORDER_MANAGEMENT_KEYS = {"TP_RR"}
DOW_CONFIG_KEYS = {
    "DOW_PIVOT_LEFT",
    "DOW_PIVOT_RIGHT",
    "DOW_MIN_ATR_MULT",
    "DOW_ATR_PERIOD",
}


def test_combo_signals_match_golden():
    from core_python.src.indicator import add_combo_indicators
    from core_python.src.levels import add_combo_levels
    from core_python.src.strategies.combo import detect_combo_signals

    # US30 (X thật = 10.0) thay cho TESTSYM cũ (X = 0.0 do fallback) nên
    # entry_price lệch đúng 10 so với bản golden cũ.
    df = make_ohlcv(300)
    params = configuration.normalize_combo_params({}, "US30", "H1")
    indicators = add_combo_indicators(df, params)
    signals = detect_combo_signals(indicators, symbol="US30", params=params)
    enriched = add_combo_levels(signals, params, "US30")

    assert signals["signal"].value_counts().to_dict() == {0: 269, 1: 15, -1: 16}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 31

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 01:45:00"
    assert int(first["signal"]) == -1
    assert round(float(first["entry_price"]), 6) == 88.288889
    # core_python không tính SL/TP (KSL/KTP chuyển sang order_gateway,
    # chốt 2026-09-21) -- sl_price/tp_price luôn NaN, xem levels.py.
    assert pd.isna(first["sl_price"])
    assert pd.isna(first["tp_price"])

    last = rows.iloc[-1]
    assert str(last["bartime"]) == "2026-01-06 00:25:00"
    assert int(last["signal"]) == -1
    assert round(float(last["entry_price"]), 6) == 78.30068
    assert pd.isna(last["sl_price"])
    assert pd.isna(last["tp_price"])


def test_combo_trend_config_has_only_strategy_level_switches():
    params = configuration.normalize_combo_params({}, "US30", "H1")

    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_TYPE"] == "knn"
    assert params["TREND_TF"] == "H4"
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert configuration.COMBO_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")


def test_knn_trend_technical_defaults_live_in_trend_module():
    from core_python.src.strategies.trend import (
        KNN_TREND_DEFAULT_PARAMS,
        knn_trend_indicator_params,
    )

    params = knn_trend_indicator_params({"TREND_TF": "H4"})

    assert set(KNN_TREND_DEFAULT_PARAMS) == TECHNICAL_TREND_KEYS
    assert params["TREND_TF"] == "H4"
    assert params["PRICE_VALUE"] == "hl2"
    assert params["AI_K"] == 3


def test_ma_cross_signals_match_golden():
    from core_python.src.indicator import add_ma_cross_indicators
    from core_python.src.levels import add_ma_cross_levels
    from core_python.src.strategies.ma_cross import detect_ma_cross_signals

    # ma_cross không có tham số nào theo symbol (X, session...) nên
    # entry_price/signal giữ nguyên y hệt bản TESTSYM cũ.
    df = make_ohlcv(300)
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")
    indicators = add_ma_cross_indicators(df, params)
    signals = detect_ma_cross_signals(indicators, symbol="US30", params=params)
    enriched = add_ma_cross_levels(signals, params, "US30")

    assert signals["signal"].value_counts().to_dict() == {0: 291, -1: 5, 1: 4}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 9

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 05:40:00"
    assert int(first["signal"]) == 1
    assert round(float(first["entry_price"]), 6) == 104.270629
    # Không tính SL/TP ở core_python (xem test combo golden phía trên).
    assert pd.isna(first["sl_price"])
    assert pd.isna(first["tp_price"])


def test_ma_cross_defaults_match_strategy_specification():
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")

    assert (params["FAST_MA"], params["SLOW_MA"]) == (13, 34)
    assert (
        params["MACD_FAST"],
        params["MACD_SLOW"],
        params["MACD_SIGNAL"],
    ) == (5, 25, 5)
    assert params["ATR_PERIOD"] == 5
    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_TYPE"] == "knn"
    assert params["TREND_TF"] == "H4"
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert DOW_CONFIG_KEYS.isdisjoint(params)
    assert configuration.MA_CROSS_DEFAULT_TIMEFRAME == "M30"
    assert configuration.MA_CROSS_SUPPORTED_TIMEFRAMES == ("M10", "M20", "M30", "M45")
    assert configuration.MA_CROSS_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")


def test_ma_cross_requires_macd_histogram_confirmation():
    from core_python.src.strategies.ma_cross import detect_ma_cross_signals

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


def test_ma_cross_trend_wait_fires_aligned_cross_and_holds_misaligned_one_pending():
    from core_python.src.strategies.ma_cross import detect_ma_cross_signals

    frame = pd.DataFrame(
        {
            "fast_ma": [2.0, 2.0, 1.0, 1.0],
            "slow_ma": [1.0, 1.0, 2.0, 2.0],
            "prev_fast_ma": [0.0, 0.0, 3.0, 3.0],
            "prev_slow_ma": [1.0, 1.0, 2.0, 2.0],
            "macd_h": [0.1, -0.1, -0.1, 0.1],
            "atr": [1.0, 1.0, 1.0, 1.0],
            "trend_bias": [1, 1, 1, -1],
        }
    )

    result = detect_ma_cross_signals(frame, params={"TREND_FILTER_ENABLED": True})

    assert result["raw_signal"].tolist() == [1, 0, -1, 0]
    assert result["signal"].tolist() == [1, 0, 0, 0]
    # Trend-wait (_apply_trend_wait, chốt 2026-09-25): bar 0 cắt lên khi trend
    # đã +1 -> nổ ngay; bar 2 cắt xuống nhưng trend +1 -> CHỜ (không bị loại
    # hẳn như trend filter cũ), bar 3 vẫn chờ vì MA chưa đảo và MACD > 0.
    assert result["trend_filter_status"].tolist() == ["fired", "no_regime", "pending", "pending"]


def test_ma_cross_levels_add_market_entry_and_optional_sl_dow():
    from core_python.src.levels import add_ma_cross_levels

    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30"]),
            "close": [52252.8, 52252.8],
            "signal": [1, -1],
            "sl_dow_buy": [52190.0, 52190.0],
            "sl_dow_sell": [52300.0, 52300.0],
        }
    )
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")

    result = add_ma_cross_levels(frame, params, "US30")
    buy, sell = result.iloc[0], result.iloc[1]

    assert buy["entry_price"] == pytest.approx(52252.8)
    assert buy["sl_dow"] == pytest.approx(52190.0)
    assert pd.isna(buy["sl_price"])
    assert pd.isna(buy["tp_price"])
    assert pd.isna(buy["risk_reward"])

    assert sell["entry_price"] == pytest.approx(52252.8)
    assert sell["sl_dow"] == pytest.approx(52300.0)
    assert pd.isna(sell["sl_price"])
    assert pd.isna(sell["tp_price"])
    assert pd.isna(sell["risk_reward"])


def test_ma_cross_rejects_timeframe_outside_execution_set():
    with pytest.raises(ValueError, match="supports only these timeframes"):
        configuration.run_strategy(
            "ma_cross",
            symbol="US30",
            tf="M5",
            bars=make_ohlcv(100),
        )
