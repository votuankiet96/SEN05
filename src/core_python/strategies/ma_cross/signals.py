"""Indicator calculation and signal detection for MA Cross.

BUY is emitted on the first closed candle where SMA fast crosses above SMA
slow and MACD Histogram is positive. SELL is the symmetric condition with a
downward cross and negative MACD Histogram. There is no session filter.
"""

from __future__ import annotations

import pandas as pd

from core_python.indicators.core import atr, macd_hist, safe_ratio, sma
from core_python.strategies.ma_cross.config import get_indicator_params


def add_ma_cross_indicators(
    df: pd.DataFrame,
    params: dict | None = None,
) -> pd.DataFrame:
    """Add SMA 13/34, MACD Histogram, ATR and prior-SMA columns."""
    p = {**get_indicator_params(), **(params or {})}
    out = df.copy()
    out["fast_ma"] = sma(out["close"], int(p["FAST_MA"]))
    out["slow_ma"] = sma(out["close"], int(p["SLOW_MA"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(p["MACD_FAST"]),
        slow=int(p["MACD_SLOW"]),
        signal=int(p["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(p["ATR_PERIOD"]))
    out["prev_fast_ma"] = out["fast_ma"].shift(1)
    out["prev_slow_ma"] = out["slow_ma"].shift(1)
    return out


def detect_ma_cross_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """Emit MACD-confirmed SMA crossover signals on closed candles.

    BUY:
        prev_fast_ma <= prev_slow_ma
        fast_ma > slow_ma
        macd_h > 0

    SELL:
        prev_fast_ma >= prev_slow_ma
        fast_ma < slow_ma
        macd_h < 0
    """
    _ = symbol, params, sess_mask
    out = df.copy()

    valid = (
        out["fast_ma"].notna()
        & out["slow_ma"].notna()
        & out["prev_fast_ma"].notna()
        & out["prev_slow_ma"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )
    cross_up = (out["prev_fast_ma"] <= out["prev_slow_ma"]) & (
        out["fast_ma"] > out["slow_ma"]
    )
    cross_down = (out["prev_fast_ma"] >= out["prev_slow_ma"]) & (
        out["fast_ma"] < out["slow_ma"]
    )

    out["signal"] = 0
    out.loc[valid & cross_up & out["macd_h"].gt(0), "signal"] = 1
    out.loc[valid & cross_down & out["macd_h"].lt(0), "signal"] = -1

    out["ma_gap"] = out["fast_ma"] - out["slow_ma"]
    out["ma_gap_atr"] = safe_ratio(out["ma_gap"], out["atr"])
    out["signal_reason"] = ""
    out.loc[out["signal"].eq(1), "signal_reason"] = (
        "fast SMA crossed above slow SMA; MACD histogram > 0"
    )
    out.loc[out["signal"].eq(-1), "signal_reason"] = (
        "fast SMA crossed below slow SMA; MACD histogram < 0"
    )
    return out
