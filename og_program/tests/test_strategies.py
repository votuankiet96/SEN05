"""Characterization tests for the Combo and MA Cross strategy pipelines.

These lock in current signal-detection behavior on fixed synthetic fixtures
so future refactors can be verified to not change any strategy logic.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core_python import configuration
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
    from core_python.indicator import add_combo_indicators
    from core_python.levels import add_combo_levels
    from core_python.strategies.combo import detect_combo_signals

    # US30 thay cho TESTSYM cũ: normalize_combo_params giờ cần tra KSL/KTP
    # theo (symbol, tf) từ core_python/ksl_ktp.csv -- symbol giả không có
    # dòng nào trong bảng đó (đúng ý, không fallback). US30/H1 là symbol/tf
    # thật có trong bảng. X thật của US30 (10.0) khác TESTSYM (0.0 do
    # fallback) nên entry_price lệch đúng 10 so với bản golden cũ.
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
    # KSL=1.618/KTP=2.618 (mặc định hiện tại trong ksl_ktp.csv) x atr, đúng
    # hướng SELL: sl_price = entry + KSL*atr, tp_price = entry - KTP*atr.
    assert round(float(first["sl_price"]), 6) == 90.076603
    assert round(float(first["tp_price"]), 6) == 85.396284
    assert first["ksl"] == pytest.approx(1.618)
    assert first["ktp"] == pytest.approx(2.618)

    last = rows.iloc[-1]
    assert str(last["bartime"]) == "2026-01-06 00:25:00"
    assert int(last["signal"]) == -1
    assert round(float(last["entry_price"]), 6) == 78.30068
    assert round(float(last["sl_price"]), 6) == 81.27726
    assert round(float(last["tp_price"]), 6) == 73.484432


def test_combo_trend_config_has_only_strategy_level_switches():
    params = configuration.normalize_combo_params({}, "US30", "H1")

    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_TYPE"] == "knn"
    assert params["TREND_TF"] == "H4"
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert configuration.COMBO_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")
    # KSL/KTP tra từ ksl_ktp.csv (mặc định hiện tại cho mọi cặp: KSL1618/KTP2618).
    assert params["KSL"] == pytest.approx(1.618)
    assert params["KTP"] == pytest.approx(2.618)


def test_knn_trend_technical_defaults_live_in_trend_module():
    from core_python.strategies.trend import (
        KNN_TREND_DEFAULT_PARAMS,
        knn_trend_indicator_params,
    )

    params = knn_trend_indicator_params({"TREND_TF": "H4"})

    assert set(KNN_TREND_DEFAULT_PARAMS) == TECHNICAL_TREND_KEYS
    assert params["TREND_TF"] == "H4"
    assert params["PRICE_VALUE"] == "hl2"
    assert params["AI_K"] == 3


def test_ma_cross_signals_match_golden():
    from core_python.indicator import add_ma_cross_indicators
    from core_python.levels import add_ma_cross_levels
    from core_python.strategies.ma_cross import detect_ma_cross_signals

    # US30/M30 thay cho TESTSYM cũ, cùng lý do đã nêu ở test combo golden
    # phía trên -- ma_cross không có tham số nào theo symbol (X, session...)
    # nên entry_price/signal giữ nguyên y hệt bản TESTSYM cũ, chỉ thêm
    # sl_price/tp_price (trước đây luôn NaN, giờ tính từ KSL/KTP*atr).
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
    # KSL=1.618/KTP=2.618 mặc định, hướng BUY: sl_price = entry - KSL*atr,
    # tp_price = entry + KTP*atr.
    assert round(float(first["sl_price"]), 6) == 102.372377
    assert round(float(first["tp_price"]), 6) == 107.342088
    assert first["ksl"] == pytest.approx(1.618)
    assert first["ktp"] == pytest.approx(2.618)


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
    assert params["KSL"] == pytest.approx(1.618)
    assert params["KTP"] == pytest.approx(2.618)


def test_ma_cross_requires_macd_histogram_confirmation():
    from core_python.strategies.ma_cross import detect_ma_cross_signals

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


def test_ma_cross_trend_filter_keeps_only_aligned_raw_signals():
    from core_python.strategies.ma_cross import detect_ma_cross_signals

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
    assert result.loc[0, "trend_filter_status"] == "aligned"
    assert result.loc[2, "trend_filter_status"] == "filtered"


def test_ma_cross_levels_add_market_entry_and_optional_sl_dow():
    from core_python.levels import add_ma_cross_levels

    # Không có cột "atr" trong frame tổng hợp này -- _apply_ksl_ktp() bỏ qua
    # khi thiếu atr (xem levels.py), nên sl_price/tp_price vẫn NaN dù KSL/KTP
    # đã resolve thật ở params. Test này khoá riêng hành vi sl_dow, không
    # phải hành vi KSL/KTP*atr (xem test_combo_signals_match_golden /
    # test_ma_cross_signals_match_golden cho công thức đó).
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
    assert pd.isna(buy["ksl"])
    assert pd.isna(buy["ktp"])

    assert sell["entry_price"] == pytest.approx(52252.8)
    assert sell["sl_dow"] == pytest.approx(52300.0)
    assert pd.isna(sell["sl_price"])
    assert pd.isna(sell["tp_price"])
    assert pd.isna(sell["risk_reward"])
    assert pd.isna(sell["ksl"])
    assert pd.isna(sell["ktp"])


def test_ma_cross_rejects_timeframe_outside_execution_set():
    with pytest.raises(ValueError, match="supports only these timeframes"):
        configuration.run_strategy(
            "ma_cross",
            symbol="US30",
            tf="M5",
            bars=make_ohlcv(100),
        )


def test_ema_cross_defaults_match_strategy_specification():
    params = configuration.normalize_ema_cross_params({}, "US30")

    assert (params["FAST_EMA"], params["SLOW_EMA"]) == (13, 34)
    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_TYPE"] == "knn"
    assert params["TREND_TF"] == "H4"
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert DOW_CONFIG_KEYS.isdisjoint(params)
    assert configuration.EMA_CROSS_DEFAULT_TIMEFRAME == "M45"
    assert configuration.EMA_CROSS_SUPPORTED_TIMEFRAMES == ("M10", "M20", "M30", "M45")
    assert configuration.EMA_CROSS_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")
    # EMA Cross chưa có SL/TP theo KSL/KTP (chỉ combo/ma_cross) -- khoá
    # riêng, không dựa vào ORDER_MANAGEMENT_KEYS (đã bỏ KSL/KTP khỏi set đó
    # vì 2 chiến lược kia giờ có 2 key này hợp lệ).
    assert "KSL" not in params
    assert "KTP" not in params


def test_ksl_ktp_table_and_decode_level_reject_bad_input():
    from core_python.levels import KSL_LEVELS, KTP_LEVELS, decode_level

    assert decode_level("KSL0618", "KSL", KSL_LEVELS) == pytest.approx(0.618)
    assert decode_level("ktp2618", "KTP", KTP_LEVELS) == pytest.approx(2.618)  # không phân biệt hoa/thường

    with pytest.raises(ValueError, match="thiếu tiền tố"):
        decode_level("KTP0618", "KSL", KSL_LEVELS)
    with pytest.raises(ValueError, match="4 chữ số"):
        decode_level("KSL618", "KSL", KSL_LEVELS)
    with pytest.raises(ValueError, match="không thuộc"):
        decode_level("KSL0619", "KSL", KSL_LEVELS)  # gần 0.618 nhưng không phải 1 trong 10 mức


def test_combo_and_ma_cross_raise_for_symbol_missing_from_ksl_ktp_csv():
    with pytest.raises(KeyError, match="ksl_ktp.csv"):
        configuration.normalize_combo_params({}, "NOSUCHSYMBOL", "H1")
    with pytest.raises(KeyError, match="ksl_ktp.csv"):
        configuration.normalize_ma_cross_params({}, "US30", "M5")  # M5 hợp lệ ở combo, không ở bảng ma_cross


def test_ema_cross_detects_raw_signal_without_trend_filter():
    from core_python.strategies.ema_cross import detect_ema_cross_signals

    frame = pd.DataFrame(
        {
            "ema_fast": [2.0, 2.0, 1.0, 1.0],
            "ema_slow": [1.0, 1.0, 2.0, 2.0],
            "prev_ema_fast": [0.0, 2.0, 3.0, 1.0],
            "prev_ema_slow": [1.0, 1.0, 2.0, 2.0],
        }
    )

    result = detect_ema_cross_signals(frame, params={"TREND_FILTER_ENABLED": False})

    assert result["raw_signal"].tolist() == [1, 0, -1, 0]
    assert result["signal"].tolist() == [1, 0, -1, 0]


def test_ema_cross_trend_filter_keeps_only_aligned_raw_signals():
    from core_python.strategies.ema_cross import detect_ema_cross_signals

    frame = pd.DataFrame(
        {
            "ema_fast": [2.0, 2.0, 1.0, 1.0],
            "ema_slow": [1.0, 1.0, 2.0, 2.0],
            "prev_ema_fast": [0.0, 2.0, 3.0, 1.0],
            "prev_ema_slow": [1.0, 1.0, 2.0, 2.0],
            "trend_bias": [1, 1, 1, 0],
        }
    )

    result = detect_ema_cross_signals(frame, params={"TREND_FILTER_ENABLED": True})

    assert result["raw_signal"].tolist() == [1, 0, -1, 0]
    assert result["signal"].tolist() == [1, 0, 0, 0]
    assert result.loc[0, "trend_filter_status"] == "aligned"
    assert result.loc[2, "trend_filter_status"] == "filtered"


def test_ema_cross_trend_filter_requires_trend_reference_columns():
    from core_python.strategies.ema_cross import detect_ema_cross_signals

    frame = pd.DataFrame(
        {
            "ema_fast": [2.0],
            "ema_slow": [1.0],
            "prev_ema_fast": [0.0],
            "prev_ema_slow": [1.0],
        }
    )

    with pytest.raises(ValueError, match="trend reference"):
        detect_ema_cross_signals(frame, params={"TREND_FILTER_ENABLED": True})


def test_ema_cross_levels_add_market_entry_and_optional_sl_dow():
    from core_python.levels import add_ema_cross_levels

    frame = pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=5, freq="5min"),
            "entry_close_time": pd.date_range("2026-01-01 00:05", periods=5, freq="5min"),
            "close": [10.5, 9.0, 9.5, 11.5, 12.5],
            "signal": [0, 0, 0, 1, 0],
            "sl_dow_buy": [8.0, 8.0, 8.0, 8.0, 8.0],
            "sl_dow_sell": [13.0, 13.0, 13.0, 13.0, 13.0],
        }
    )

    result = add_ema_cross_levels(frame, {})
    row = result.iloc[3]

    assert row["entry_time"] == pd.Timestamp("2026-01-01 00:20")
    assert row["entry_price"] == pytest.approx(11.5)
    assert row["sl_dow"] == pytest.approx(8.0)
    assert pd.isna(row["sl_price"])
    assert pd.isna(row["tp_price"])
    assert pd.isna(row["risk_reward"])
    assert pd.isna(row["ksl"])
    assert pd.isna(row["ktp"])


def test_run_ema_cross_strategy_adds_signal_and_level_columns():
    result = configuration.run_strategy(
        "ema_cross",
        symbol="US30",
        tf="M45",
        bars=make_ohlcv(120),
        overrides={"FAST_EMA": 1, "SLOW_EMA": 3},
    )

    for column in ["raw_signal", "signal", "entry_price", "sl_dow", "sl_dow_buy", "sl_dow_sell"]:
        assert column in result.columns
    assert set(result["signal"].dropna().unique()) <= {-1, 0, 1}


def test_run_ema_cross_with_trend_reference_filters_against_large_timeframe(monkeypatch):
    def fake_knn_trend_indicators(trend_bars, params):
        frame = trend_bars.copy()
        frame["trend_close_time"] = frame["bartime"] + pd.Timedelta(hours=1)
        frame["trend_bias"] = 1
        return frame

    monkeypatch.setattr(configuration, "add_knn_trend_indicators", fake_knn_trend_indicators)
    entry = make_ohlcv(6, freq_minutes=45)
    entry["close"] = [3.0, 2.0, 1.0, 2.0, 3.0, 4.0]
    entry["open"] = entry["close"]
    entry["high"] = entry["close"] + 0.5
    entry["low"] = entry["close"] - 0.5
    trend = make_ohlcv(1, freq_minutes=60, start="2026-01-04 23:00")

    result = configuration.run_strategy_with_trend_reference(
        "ema_cross",
        symbol="US30",
        entry_tf="M45",
        entry_bars=entry,
        trend_tf="H1",
        trend_bars=trend,
        overrides={"FAST_EMA": 1, "SLOW_EMA": 3},
    )

    assert result["raw_signal"].tolist() == [0, -1, 0, 1, 0, 0]
    assert result["signal"].tolist() == [0, 0, 0, 1, 0, 0]
    assert result["trend_bias"].iloc[0] == 1
