"""Characterization tests for core_python.indicators.

These lock in current behavior on a fixed synthetic fixture so the coming
file-move/rename refactor stages can be verified to not change any math.
"""

from __future__ import annotations

import pytest

from core_python.indicators.core import atr, ema, macd_hist, sma
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
