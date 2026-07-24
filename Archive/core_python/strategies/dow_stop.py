"""Causal Dow-based stop reference helpers for strategy exports."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core_python.indicators.core import atr


@dataclass(frozen=True)
class DowStopParams:
    left: int = 3
    right: int = 5
    min_atr_mult: float = 0.5
    atr_period: int = 14


@dataclass(frozen=True)
class _Pivot:
    index: int
    kind: str
    price: float


def causal_dow_stop(
    df: pd.DataFrame,
    *,
    params: DowStopParams | None = None,
    signal_col: str = "signal",
    output_col: str = "sl_dow",
) -> pd.Series:
    """
    Return a non-repainting Dow stop reference for each signal row.

    The function processes the provided entry-timeframe dataframe from left to
    right. A pivot is only available after its right-side confirmation bars have
    closed, and future pivots never rewrite a stop already emitted for an older
    signal.
    """
    p = params or DowStopParams()
    left = max(1, int(p.left))
    right = max(1, int(p.right))
    min_atr_mult = max(0.0, float(p.min_atr_mult))
    atr_period = max(1, int(p.atr_period))

    stops = pd.Series(np.nan, index=df.index, name=output_col, dtype="float64")
    required = {"high", "low", "close", signal_col}
    if df.empty or not required.issubset(df.columns):
        return stops

    highs = pd.to_numeric(df["high"], errors="coerce").astype(float).to_numpy()
    lows = pd.to_numeric(df["low"], errors="coerce").astype(float).to_numpy()
    closes = pd.to_numeric(df["close"], errors="coerce").astype(float).to_numpy()
    signals = pd.to_numeric(df[signal_col], errors="coerce").fillna(0).astype(int).to_numpy()
    atr_source = df["atr14"] if "atr14" in df.columns else atr(df, atr_period)
    atr_values = pd.to_numeric(atr_source, errors="coerce")

    swings: list[_Pivot] = []
    last_high: _Pivot | None = None
    last_low: _Pivot | None = None

    for current_pos in range(len(df)):
        pivot_pos = current_pos - right
        if pivot_pos >= left:
            for pivot in _confirmed_pivots(highs, lows, pivot_pos, left, right):
                accepted = _accept_pivot(swings, pivot, atr_values.iloc[pivot.index], min_atr_mult)
                if accepted is None:
                    continue
                if accepted.kind == "high":
                    last_high = accepted
                else:
                    last_low = accepted

        signal = signals[current_pos]
        close = closes[current_pos]
        if signal == 1 and last_low is not None and not np.isnan(close) and last_low.price < close:
            stops.iloc[current_pos] = last_low.price
        elif signal == -1 and last_high is not None and not np.isnan(close) and last_high.price > close:
            stops.iloc[current_pos] = last_high.price

    return stops


def _confirmed_pivots(
    highs: np.ndarray,
    lows: np.ndarray,
    pivot_pos: int,
    left: int,
    right: int,
) -> list[_Pivot]:
    high = highs[pivot_pos]
    low = lows[pivot_pos]
    if np.isnan(high) or np.isnan(low):
        return []

    left_high = highs[pivot_pos - left : pivot_pos]
    right_high = highs[pivot_pos + 1 : pivot_pos + right + 1]
    left_low = lows[pivot_pos - left : pivot_pos]
    right_low = lows[pivot_pos + 1 : pivot_pos + right + 1]

    pivots: list[_Pivot] = []
    if high > np.nanmax(left_high) and high >= np.nanmax(right_high):
        pivots.append(_Pivot(index=pivot_pos, kind="high", price=float(high)))
    if low < np.nanmin(left_low) and low <= np.nanmin(right_low):
        pivots.append(_Pivot(index=pivot_pos, kind="low", price=float(low)))
    return pivots


def _accept_pivot(
    swings: list[_Pivot],
    pivot: _Pivot,
    atr_value: float,
    min_atr_mult: float,
) -> _Pivot | None:
    if not swings:
        swings.append(pivot)
        return pivot

    last = swings[-1]
    if pivot.kind == last.kind:
        if _is_more_extreme(pivot, last):
            swings[-1] = pivot
            return pivot
        return None

    min_move = 0.0 if pd.isna(atr_value) else float(atr_value) * min_atr_mult
    if abs(pivot.price - last.price) < min_move:
        return None

    swings.append(pivot)
    return pivot


def _is_more_extreme(pivot: _Pivot, previous: _Pivot) -> bool:
    if pivot.kind == "high":
        return pivot.price > previous.price
    return pivot.price < previous.price
