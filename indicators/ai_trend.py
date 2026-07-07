"""
AI Trend Navigator indicator calculations for core_python.

This ports the public TradingView Pine indicator "AI Trend Navigator"
by Zeiierman to the lowercase OHLCV convention used in core_python.

Original indicator:
    AI Trend Navigator [K-Neighbor] by Zeiierman
    https://www.tradingview.com/script/30aLnPNh-AI-Trend-Navigator-K-Neighbor/

License of the original Pine script:
    CC BY-NC-SA 4.0

The implementation is deterministic and uses recent OHLCV bars only. It is
not an offline-trained machine-learning model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_python.indicators.core import atr, ema, sma


def wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted moving average compatible with Pine ta.wma()."""
    period = max(1, int(period))
    weights = np.arange(1, period + 1, dtype=float)
    denom = weights.sum()
    return series.astype(float).rolling(period, min_periods=period).apply(
        lambda values: float(np.dot(values, weights) / denom),
        raw=True,
    )


def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder RMA compatible with Pine ta.rma()."""
    period = max(1, int(period))
    return series.astype(float).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def hma(series: pd.Series, period: int) -> pd.Series:
    """Hull moving average built from WMA."""
    period = max(1, int(period))
    half = max(1, period // 2)
    root = max(1, int(np.sqrt(period)))
    return wma(2 * wma(series, half) - wma(series, period), root)


def _vwap_source(df: pd.DataFrame, source: pd.Series) -> pd.Series:
    # Cumulative over the loaded frame; changing history length changes VWAP values.
    volume = df["volume"].replace(0, np.nan).fillna(1.0).astype(float)
    src = source.astype(float)
    return (src * volume).cumsum() / volume.cumsum()


def _price_source(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    kind = (kind or "hl2").strip().lower()
    close = df["close"].astype(float)
    hl2 = (df["high"].astype(float) + df["low"].astype(float)) / 2.0

    if kind == "hl2":
        return sma(hl2, period)
    if kind == "vwap":
        return _vwap_source(df, close.shift(period))
    if kind == "sma":
        return sma(close, period)
    if kind == "wma":
        return wma(close, period)
    if kind == "ema":
        return ema(close, period)
    if kind == "hma":
        return hma(close, period)
    return sma(hl2, period)


def _target_source(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    kind = (kind or "price action").strip().lower()
    close = df["close"].astype(float)

    if kind == "price action":
        return rma(close, period)
    if kind == "vwap":
        return _vwap_source(df, close.shift(period))
    if kind == "volatility":
        return atr(df, 14)
    if kind == "sma":
        return sma(close, period)
    if kind == "wma":
        return wma(close, period)
    if kind == "ema":
        return ema(close, period)
    if kind == "hma":
        return hma(close, period)
    return rma(close, period)


def calc_ai_trend_navigator(
    df: pd.DataFrame,
    *,
    price_value: str = "hl2",
    ma_len: int = 5,
    target_value: str = "Price Action",
    target_len: int = 5,
    number_of_closest_values: int = 3,
    smoothing_period: int = 50,
) -> pd.DataFrame:
    """
    Calculate the AI Trend Navigator KNN line.

    Returns a DataFrame aligned to ``df.index`` with:
        ai_knn: WMA(knnMA, 5), the colored classifier line.
        ai_avg: RMA(knnMA, smoothing_period), the average KNN line.
        ai_direction: +1 when ai_knn rises, -1 when it falls, 0 otherwise.
    """
    if df.empty:
        return pd.DataFrame(index=df.index)

    work = df.reset_index(drop=True).copy()
    k = max(2, min(int(number_of_closest_values), 200))
    ma_len = max(2, min(int(ma_len), 200))
    target_len = max(2, min(int(target_len), 200))
    smoothing_period = max(2, min(int(smoothing_period), 500))
    # Mirrors the Pine script's recent KNN search window. TREND_BARS warms
    # smoothing/history but does not expand this neighbor lookback.
    window_size = max(k, 30)

    values = _price_source(work, price_value, ma_len).to_numpy(dtype=float)
    targets = _target_source(work, target_value, target_len).to_numpy(dtype=float)

    n = len(work)
    knn_ma = np.full(n, np.nan, dtype=float)
    for idx in range(n):
        target = targets[idx]
        if np.isnan(target) or idx == 0:
            continue
        start = max(0, idx - window_size)
        hist = values[start:idx]
        valid = hist[~np.isnan(hist)]
        if len(valid) < k:
            continue
        nearest = np.argsort(np.abs(valid - target))[:k]
        knn_ma[idx] = float(valid[nearest].mean())

    knn_ma_s = pd.Series(knn_ma)
    ai_knn = wma(knn_ma_s, 5)
    ai_avg = rma(knn_ma_s, smoothing_period)
    ai_direction = pd.Series(
        np.where(ai_knn > ai_knn.shift(1), 1, np.where(ai_knn < ai_knn.shift(1), -1, 0))
    )

    out = pd.DataFrame(
        {
            "ai_knn": ai_knn,
            "ai_avg": ai_avg,
            "ai_direction": ai_direction,
        },
        index=df.index,
    )
    return out
