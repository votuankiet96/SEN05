"""Shared indicator calculations for visual strategy scanners."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Return a simple moving average with full-window warmup."""
    return series.astype(float).rolling(int(period)).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Return an exponential moving average."""
    return series.astype(float).ewm(span=int(period), adjust=False).mean()


def ma(series: pd.Series, period: int, ma_type: str = "sma") -> pd.Series:
    """Return SMA or EMA by name."""
    name = str(ma_type).lower().strip()
    if name == "sma":
        return sma(series, period)
    if name == "ema":
        return ema(series, period)
    raise ValueError(f"Unsupported MA_TYPE '{ma_type}'. Use 'sma' or 'ema'.")


def macd_hist(
    series: pd.Series,
    *,
    fast: int,
    slow: int,
    signal: int,
) -> pd.Series:
    """Return MACD histogram using EMA fast/slow/signal lines."""
    close = series.astype(float)
    macd_line = ema(close, int(fast)) - ema(close, int(slow))
    signal_line = ema(macd_line, int(signal))
    return macd_line - signal_line


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Return Wilder ATR from lowercase OHLC columns."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / int(period), min_periods=int(period), adjust=False).mean()


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Return numerator/denominator with zero and inf converted to NaN."""
    return (numerator / denominator.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

