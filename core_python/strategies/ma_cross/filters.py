"""Entry filters for MA Cross basket execution."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core_python.shared.sessions import session_mask_utc
from .config import get_filter_settings


def _optional_min(series: pd.Series, threshold: float | None) -> pd.Series:
    if threshold is None:
        return pd.Series(True, index=series.index)
    return series >= float(threshold)


def _optional_max(series: pd.Series, threshold: float | None) -> pd.Series:
    if threshold is None:
        return pd.Series(True, index=series.index)
    return series <= float(threshold)


def add_entry_filter_columns(
    df: pd.DataFrame,
    *,
    symbol_config: dict[str, Any] | None = None,
    filter_overrides: dict[str, Any] | None = None,
    signal_column: str = "signal",
) -> pd.DataFrame:
    """Add direction-aware entry filter columns without changing raw signals.

    ``signal`` remains the MA-cross decision marker. ``entry_signal`` is the
    version the basket engine should use for opening new orders.
    """
    filters = get_filter_settings(filter_overrides)
    cfg = dict(symbol_config or {})
    out = df.copy()

    if "raw_signal" not in out.columns:
        out["raw_signal"] = out.get(signal_column, pd.Series(0, index=out.index))

    lookback = max(int(filters.get("ma20_slope_lookback", 1)), 1)
    out["ma20_slope"] = out["slow_ma"] - out["slow_ma"].shift(lookback)

    ma_distance = (out["fast_ma"] - out["slow_ma"]).abs()
    out["ma_distance"] = ma_distance
    out["ma_distance_atr"] = (ma_distance / out["atr"].replace(0, np.nan)).replace(
        [np.inf, -np.inf],
        np.nan,
    )

    signal = out["raw_signal"].fillna(0).astype(int)
    min_slope = filters.get("min_ma20_slope")
    if min_slope is None:
        slope_ok = pd.Series(True, index=out.index)
    else:
        min_slope_f = abs(float(min_slope))
        slope = out["ma20_slope"]
        slope_ok = (
            (signal == 0)
            | ((signal > 0) & (slope >= min_slope_f))
            | ((signal < 0) & (slope <= -min_slope_f))
        )
    out["ma20_slope_ok"] = slope_ok.fillna(False)

    min_distance = filters.get("min_ma_distance_atr")
    if min_distance is None:
        out["ma_distance_ok"] = True
    else:
        out["ma_distance_ok"] = out["ma_distance_atr"] >= float(min_distance)

    out["atr_range_ok"] = (
        _optional_min(out["atr"], filters.get("min_atr"))
        & _optional_max(out["atr"], filters.get("max_atr"))
    )

    spread_series = out["spread_pts"] if "spread_pts" in out.columns else pd.Series(
        float(cfg.get("spread_pts", 0.0)),
        index=out.index,
    )
    out["spread_pts_effective"] = spread_series.astype(float)
    max_spread = filters.get("max_spread_pts")
    out["spread_ok"] = (
        pd.Series(True, index=out.index)
        if max_spread is None
        else out["spread_pts_effective"] <= float(max_spread)
    )

    if bool(filters.get("use_session_filter", True)):
        out["session_ok"] = session_mask_utc(out, cfg.get("session_hours_utc", []))
    else:
        out["session_ok"] = True

    out["entry_filter_ok"] = (
        out["ma20_slope_ok"]
        & out["ma_distance_ok"].fillna(False)
        & out["atr_range_ok"].fillna(False)
        & out["spread_ok"].fillna(False)
        & out["session_ok"].fillna(False)
    )
    out["entry_signal"] = out["raw_signal"].where(out["entry_filter_ok"], 0).astype(int)
    return out


__all__ = ["add_entry_filter_columns"]
