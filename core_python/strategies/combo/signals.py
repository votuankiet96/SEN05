"""Combo indicators and signal detection for DataFrame inputs."""

from __future__ import annotations

import pandas as pd

from simplified_core_python.indicators.core import atr, macd_hist, sma, safe_ratio
from simplified_core_python.strategies.combo.config import (
    get_indicator_params,
)


def _rr_filter_enabled(min_rr: object) -> bool:
    if min_rr is None:
        return False
    try:
        return not bool(pd.isna(min_rr))
    except Exception:
        return True


def _session_mask(df: pd.DataFrame, hours_utc: list[int] | None) -> pd.Series:
    if not hours_utc:
        return pd.Series(True, index=df.index)
    if "bartime" not in df.columns:
        return pd.Series(True, index=df.index)
    bar_hours = pd.to_datetime(df["bartime"], errors="coerce").dt.hour
    return bar_hours.isin({int(hour) for hour in hours_utc})


def add_combo_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Add MA, MACD histogram, ATR and previous-bar helper columns."""
    p = {**get_indicator_params(), **(params or {})}
    out = df.copy()
    out["ma"] = sma(out["close"], int(p["MA_PERIOD"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(p["MACD_FAST"]),
        slow=int(p["MACD_SLOW"]),
        signal=int(p["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(p["ATR_PERIOD"]))
    out["prev_close"] = out["close"].shift(1)
    out["prev_ma"] = out["ma"].shift(1)
    return out


def detect_combo_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """Return a DataFrame with Combo signal, ATR and pending-entry columns."""
    out = df.copy()
    p = {**get_indicator_params(), **(params or {})}
    x = float(p.get("X", 0.0) or 0.0)
    ktp = float(p["KTP"])
    min_rr = p.get("MIN_RR")

    if sess_mask is None:
        sess_mask = _session_mask(out, p.get("SESSION_HOURS_UTC", []))
    sess_mask = pd.Series(sess_mask, index=out.index).fillna(False).astype(bool)

    valid = (
        sess_mask
        & out["ma"].notna()
        & out["prev_ma"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )
    cross_up = (out["prev_close"] <= out["prev_ma"]) & (out["close"] > out["ma"])
    cross_down = (out["prev_close"] >= out["prev_ma"]) & ~(out["close"] > out["ma"])
    buy_cond = valid & cross_up & (out["close"] > out["open"]) & (out["macd_h"] > 0)
    sell_cond = valid & cross_down & (out["close"] < out["open"]) & (out["macd_h"] < 0)

    sl_dist = out["high"] - out["low"] + 2 * x
    tp_dist = ktp * out["atr"]
    rr = safe_ratio(tp_dist, sl_dist)
    if _rr_filter_enabled(min_rr):
        rr_ok = rr >= float(min_rr)
    else:
        rr_ok = pd.Series(True, index=out.index)

    out["signal"] = 0
    out.loc[buy_cond & rr_ok, "signal"] = 1
    out.loc[sell_cond & rr_ok, "signal"] = -1
    out["rr"] = rr.round(2)
    out["signal_reason"] = ""
    out.loc[out["signal"].eq(1), "signal_reason"] = "close crossed above MA, bullish candle, MACD histogram > 0"
    out.loc[out["signal"].eq(-1), "signal_reason"] = "close crossed below MA, bearish candle, MACD histogram < 0"
    return out
