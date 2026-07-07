"""Characterization tests for the four strategy pipelines.

These lock in current signal-detection behavior on fixed synthetic fixtures
so the coming file-move/rename refactor stages can be verified to not change
any strategy logic — only where the code lives and how it's imported.
"""

from __future__ import annotations

from tests.fixtures import make_ohlcv


def test_combo_signals_match_golden():
    from og_core.strategies.combo import config as combo_config
    from og_core.strategies.combo.levels import add_combo_levels
    from og_core.strategies.combo.signals import add_combo_indicators, detect_combo_signals

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
    from og_core.strategies.ma_cross import config as ma_config
    from og_core.strategies.ma_cross.levels import add_ma_cross_levels
    from og_core.strategies.ma_cross.signals import add_ma_cross_indicators, detect_ma_cross_signals

    df = make_ohlcv(300)
    params = ma_config.normalize_params({}, "TESTSYM")
    indicators = add_ma_cross_indicators(df, params)
    signals = detect_ma_cross_signals(indicators, symbol="TESTSYM", params=params)
    enriched = add_ma_cross_levels(signals, params, "TESTSYM")

    assert signals["signal"].value_counts().to_dict() == {0: 283, -1: 9, 1: 8}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 17

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 05:05:00"
    assert int(first["signal"]) == -1
    assert round(float(first["entry_price"]), 6) == 102.062126
    assert round(float(first["risk_reward"]), 2) == 1.0


def test_ai_trend_signals_match_golden():
    from og_core.strategies.ai_trend import config as ai_config
    from og_core.strategies.ai_trend.levels import add_ai_trend_levels
    from og_core.strategies.ai_trend.signals import build_ai_trend_frames

    trend_df = make_ohlcv(150, freq_minutes=180, seed=7, start="2026-01-01")
    entry_df = make_ohlcv(300, freq_minutes=45, seed=11, start="2026-01-01")
    params = ai_config.normalize_params({"TREND_TF": "H3", "ENTRY_TF": "M45"}, "TESTSYM")

    trend_full, entry_full = build_ai_trend_frames(trend_df, entry_df, params)
    enriched = add_ai_trend_levels(entry_full, params, "TESTSYM")

    assert trend_full["h3_bias"].value_counts().to_dict() == {-1: 98, 1: 40, 0: 12}
    assert entry_full["signal"].value_counts().to_dict() == {0: 298, -1: 1, 1: 1}

    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 2
    first, last = rows.iloc[0], rows.iloc[-1]
    assert str(first["bartime"]) == "2026-01-02 21:00:00"
    assert int(first["signal"]) == -1
    assert round(float(first["entry_price"]), 6) == 95.455382
    assert str(last["bartime"]) == "2026-01-07 15:45:00"
    assert int(last["signal"]) == 1
    assert round(float(last["entry_price"]), 6) == 105.853832


def test_knn_combo_signals_match_golden():
    from og_core.strategies.knn_combo import config as knn_config
    from og_core.strategies.knn_combo.pipeline import build_knn_combo_strategy_frames

    trend_df = make_ohlcv(150, freq_minutes=180, seed=7, start="2026-01-01")
    entry_df = make_ohlcv(300, freq_minutes=60, seed=13, start="2026-01-01")
    params = knn_config.normalize_params({"TREND_TF": "H3", "ENTRY_TF": "H1"}, "TESTSYM")

    _trend_full, entry_full = build_knn_combo_strategy_frames(trend_df, entry_df, params, "TESTSYM")

    assert entry_full["raw_signal"].value_counts().to_dict() == {0: 280, -1: 12, 1: 8}
    assert entry_full["signal"].value_counts().to_dict() == {0: 286, -1: 10, 1: 4}

    rows = entry_full[entry_full["signal"] != 0]
    assert len(rows) == 14
    assert str(rows.iloc[0]["bartime"]) == "2026-01-02 16:00:00"
    assert int(rows.iloc[0]["signal"]) == -1
    assert str(rows.iloc[-1]["bartime"]) == "2026-01-13 06:00:00"
    assert int(rows.iloc[-1]["signal"]) == 1
