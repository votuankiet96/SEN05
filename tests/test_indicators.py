"""Characterization tests for og_core.indicators.

These lock in current behavior on a fixed synthetic fixture so the coming
file-move/rename refactor stages can be verified to not change any math.
"""

from __future__ import annotations

import pytest

from og_core.indicators.ai_trend import calc_ai_trend_navigator
from og_core.indicators.core import atr, ema, macd_hist, sma
from og_core.indicators.dow_wave import calc_dow_wave
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


def test_ai_trend_navigator_matches_golden(ohlcv):
    result = calc_ai_trend_navigator(
        ohlcv,
        price_value="hl2",
        ma_len=5,
        target_value="Price Action",
        target_len=5,
        number_of_closest_values=3,
        smoothing_period=50,
    )
    assert result["ai_knn"].isna().sum() == 11
    assert result["ai_knn"].tail(3).round(6).tolist() == [89.971418, 89.721459, 89.337794]
    assert result["ai_avg"].tail(3).round(6).tolist() == [90.537611, 90.514245, 90.478449]
    assert result["ai_direction"].value_counts().to_dict() == {-1: 154, 1: 134, 0: 12}


def test_dow_wave_matches_golden(ohlcv):
    result = calc_dow_wave(ohlcv, left=3, right=3, min_atr_mult=0.0)
    assert int(result["dow_pivot_type"].notna().sum()) == 60
    assert result["dow_label"].value_counts().to_dict() == {
        "LH": 18,
        "LL": 15,
        "HL": 15,
        "HH": 12,
    }
    first_pivot = result.dropna(subset=["dow_pivot_type"]).iloc[0]
    assert first_pivot["dow_pivot_type"] == "low"
    assert first_pivot["dow_label"] == "LL"
    assert round(float(first_pivot["dow_pivot_price"]), 6) == 96.555724
