"""MA Cross indicators and signal detection for DataFrame inputs."""

from __future__ import annotations

import pandas as pd

from core_python.indicators.core import atr, ma, safe_ratio
from core_python.strategies.ma_cross.config import (
    SESSION_HOURS_UTC,
    get_indicator_params,
)


def _session_mask(df: pd.DataFrame, hours_utc: list[int] | None) -> pd.Series:
    if not hours_utc:
        return pd.Series(True, index=df.index)
    if "bartime" not in df.columns:
        return pd.Series(True, index=df.index)
    bar_hours = pd.to_datetime(df["bartime"], errors="coerce").dt.hour
    return bar_hours.isin({int(hour) for hour in hours_utc})


def add_ma_cross_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Add fast MA, slow MA and ATR columns required by MA Cross."""
    p = {**get_indicator_params(), **(params or {})}
    out = df.copy()
    out["fast_ma"] = ma(out["close"], int(p["FAST_MA"]), str(p["MA_TYPE"]))
    out["slow_ma"] = ma(out["close"], int(p["SLOW_MA"]), str(p["MA_TYPE"]))
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
    """Return a DataFrame with close-confirmed MA cross signals."""
    _ = symbol
    _ = params
    out = df.copy()
    if sess_mask is None:
        sess_mask = _session_mask(out, SESSION_HOURS_UTC)
    sess_mask = pd.Series(sess_mask, index=out.index).fillna(False).astype(bool)

    valid = (
        sess_mask
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
    out["ma_gap_atr"] = safe_ratio(out["ma_gap"], out["atr"])
    out["signal_reason"] = ""
    out.loc[out["signal"].eq(1), "signal_reason"] = "fast MA crossed above slow MA"
    out.loc[out["signal"].eq(-1), "signal_reason"] = "fast MA crossed below slow MA"
    return out
