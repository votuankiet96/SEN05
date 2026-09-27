"""Characterization tests for core_python.indicator.

These lock in current behavior on a fixed synthetic fixture so future
refactors can be verified to not change any math.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core_python.indicator import (
    DowStructureParams,
    add_dow_structure_indicators,
    add_ema_cross_indicators,
    atr,
    calc_ai_trend_navigator,
    ema,
    macd_hist,
    merge_trend_reference,
    rma,
    sma,
)
from tests.fixtures import make_ohlcv


@pytest.fixture
def ohlcv():
    return make_ohlcv(300)


def test_sma_matches_golden(ohlcv):
    result = sma(ohlcv["close"], 20)
    assert result.isna().sum() == 19
    assert result.tail(3).round(6).tolist() == [90.573271, 90.363478, 90.110876]


def test_ema_matches_golden(ohlcv):
    result = ema(ohlcv["close"], 20)
    assert result.tail(3).round(6).tolist() == [90.033372, 89.785709, 89.52565]


def test_macd_hist_matches_golden(ohlcv):
    result = macd_hist(ohlcv["close"], fast=5, slow=25, signal=5)
    assert result.tail(3).round(6).tolist() == [-0.165339, -0.417086, -0.513022]


def test_atr_matches_golden(ohlcv):
    result = atr(ohlcv, 14)
    assert result.isna().sum() == 13
    assert result.tail(3).round(6).tolist() == [1.233416, 1.324877, 1.282539]


def test_atr_seed_matches_wilder_sma_not_single_point(ohlcv):
    """
    Bar hợp lệ đầu tiên của ATR phải bằng SMA(TR, period) — đúng cách seed
    của TradingView ta.rma/ta.atr. `tail(3)` ở test golden phía trên không
    bắt được điều này vì 2 cách seed hội tụ về cùng 1 giá trị sau vài chục
    bar; test này khoá đúng bar bị lệch (đo thật 2026-09-13: seed 1 điểm
    kiểu ewm(adjust=False) thuần làm ATR lệch ~4% tại đúng bar này).
    """
    period = 14
    result = atr(ohlcv, period)
    prev_close = ohlcv["close"].shift(1)
    true_range = pd.concat(
        [
            ohlcv["high"] - ohlcv["low"],
            (ohlcv["high"] - prev_close).abs(),
            (ohlcv["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    seed = true_range.iloc[:period].mean()
    assert result.iloc[period - 1] == pytest.approx(seed)


def test_rma_seed_matches_wilder_sma_not_single_point():
    """rma() dùng cùng công thức seed với atr() — khoá riêng vì đây là hàm
    public độc lập, dùng cho AI Trend Navigator (ai_avg, target "Price
    Action"), không đi qua atr()."""
    period = 10
    series = pd.Series([float(i) for i in range(1, 31)])  # 1..30, tăng dần đơn giản để dễ kiểm tay
    result = rma(series, period)
    assert result.iloc[: period - 1].isna().all()
    assert result.iloc[period - 1] == pytest.approx(series.iloc[:period].mean())


def test_ema_cross_indicator_wrapper_adds_expected_columns(ohlcv):
    params = {"FAST_EMA": 13, "SLOW_EMA": 34, "ATR_PERIOD": 5, "_RUN_TF": "M10"}

    result = add_ema_cross_indicators(ohlcv, params)

    for column in ["ema_fast", "ema_slow", "prev_ema_fast", "prev_ema_slow", "atr"]:
        assert column in result.columns
    assert result["entry_close_time"].iloc[0] - result["bartime"].iloc[0] == pd.Timedelta(minutes=10)
    assert str(result["entry_close_time"].iloc[0]) == "2026-01-05 00:10:00"


def test_dow_structure_indicators_add_confirmed_stop_references():
    frame = pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=5, freq="5min"),
            "open": [10.5, 9.0, 9.5, 11.5, 12.5],
            "high": [11.0, 10.0, 10.0, 12.0, 13.0],
            "low": [10.0, 8.0, 9.0, 11.0, 12.0],
            "close": [10.5, 9.0, 9.5, 11.5, 12.5],
            "volume": [1, 1, 1, 1, 1],
        }
    )

    result = add_dow_structure_indicators(
        frame,
        DowStructureParams(left=1, right=1, min_atr_mult=0, atr_period=1),
    )

    assert result["dow_swing_low"].iloc[:2].isna().all()
    assert result["dow_swing_low"].iloc[2:].tolist() == pytest.approx([8.0, 8.0, 8.0])
    assert result["sl_dow_buy"].iloc[3] == pytest.approx(8.0)


def test_ai_trend_navigator_returns_direction_columns(ohlcv):
    result = calc_ai_trend_navigator(
        ohlcv,
        price_value="hl2",
        ma_len=5,
        target_value="Price Action",
        target_len=5,
        number_of_closest_values=3,
        smoothing_period=50,
    )

    assert list(result.columns) == ["ai_knn", "ai_avg", "ai_direction"]
    assert len(result) == len(ohlcv)
    assert set(result["ai_direction"].dropna().unique()) <= {-1, 0, 1}


def test_merge_trend_reference_uses_only_closed_trend_bars():
    entry = make_ohlcv(3, freq_minutes=45, start="2026-01-01 02:15")
    trend = make_ohlcv(1, freq_minutes=180, start="2026-01-01 00:00")
    trend["trend_close_time"] = pd.to_datetime(["2026-01-01 03:00"])
    trend["trend_bias"] = 1

    result = merge_trend_reference(entry, trend)

    assert result["trend_bias"].isna().tolist() == [True, False, False]
    assert result["trend_bias"].iloc[1:].tolist() == [1.0, 1.0]
