"""Signal logic for the MA Cross 10/20 strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_python.shared.sessions import session_mask_utc
from .config import get_indicator_params


def _series_ma(close: pd.Series, period: int, ma_type: str) -> pd.Series:
    ma_type = ma_type.lower().strip()
    if ma_type == "ema":
        return close.ewm(span=period, adjust=False).mean()
    if ma_type == "sma":
        return close.rolling(period).mean()
    raise ValueError(f"Unsupported MA_TYPE '{ma_type}'. Use 'sma' or 'ema'.")


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def add_ma_cross_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Add fast/slow MA and ATR columns required by MA Cross."""
    p = {**get_indicator_params(), **(params or {})}
    out = df.copy()
    had_dt_index = isinstance(out.index, pd.DatetimeIndex)
    if not had_dt_index and "BarTime" in out.columns:
        out = out.set_index("BarTime")

    out["fast_ma"] = _series_ma(out["close"], int(p["FAST_MA"]), str(p["MA_TYPE"]))
    out["slow_ma"] = _series_ma(out["close"], int(p["SLOW_MA"]), str(p["MA_TYPE"]))
    out["atr"] = _atr(out, int(p["ATR_PERIOD"]))
    out["prev_fast_ma"] = out["fast_ma"].shift(1)
    out["prev_slow_ma"] = out["slow_ma"].shift(1)
    return out


def session_mask(df: pd.DataFrame, hours_utc: list[int] | None) -> pd.Series:
    """Return a UTC-hour session mask. Empty hours means all bars are valid."""
    return session_mask_utc(df, hours_utc)


def detect_ma_cross_signals(
    df: pd.DataFrame,
    sess_mask: pd.Series,
    sym_key: str | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    """Detect close-confirmed MA cross signals.

    BUY is emitted when fast MA crosses above slow MA. SELL is symmetric. The
    signal is only a decision marker; market execution happens at next bar open.
    """
    _ = sym_key
    _ = params
    out = df.copy()
    valid = (
        sess_mask
        & out.get("in_window", pd.Series(True, index=out.index))
        & out["fast_ma"].notna()
        & out["slow_ma"].notna()
        & out["prev_fast_ma"].notna()
        & out["prev_slow_ma"].notna()
        & out["atr"].notna()
    )
    cross_up = (out["prev_fast_ma"] <= out["prev_slow_ma"]) & (
        out["fast_ma"] > out["slow_ma"]
    )
    cross_down = (out["prev_fast_ma"] >= out["prev_slow_ma"]) & (
        out["fast_ma"] < out["slow_ma"]
    )

    out["signal"] = 0
    out.loc[valid & cross_up, "signal"] = 1
    out.loc[valid & cross_down, "signal"] = -1
    out["ma_gap"] = out["fast_ma"] - out["slow_ma"]
    out["ma_gap_atr"] = (out["ma_gap"] / out["atr"].replace(0, np.nan)).replace(
        [np.inf, -np.inf],
        np.nan,
    )
    return out


def build_raw_signal_masks(
    df: pd.DataFrame,
    hours_utc: list[int] | None = None,
) -> tuple[pd.Series, pd.Series]:
    """Return raw BUY/SELL masks for scanners."""
    sess = session_mask(df, hours_utc)
    valid = (
        sess
        & df["fast_ma"].notna()
        & df["slow_ma"].notna()
        & df["prev_fast_ma"].notna()
        & df["prev_slow_ma"].notna()
    )
    buy = valid & (df["prev_fast_ma"] <= df["prev_slow_ma"]) & (
        df["fast_ma"] > df["slow_ma"]
    )
    sell = valid & (df["prev_fast_ma"] >= df["prev_slow_ma"]) & (
        df["fast_ma"] < df["slow_ma"]
    )
    return buy, sell


add_backtest_indicators = add_ma_cross_indicators
detect_signals = detect_ma_cross_signals
