"""Dow-style swing wave detection for dashboard visualization."""

from __future__ import annotations

import numpy as np
import pandas as pd

from og_core.indicators.core import atr


def _candidate_pivots(df: pd.DataFrame, left: int, right: int) -> list[dict[str, object]]:
    highs = df["high"].astype(float).to_numpy()
    lows = df["low"].astype(float).to_numpy()
    pivots: list[dict[str, object]] = []
    for idx in range(left, len(df) - right):
        left_high = highs[idx - left : idx]
        right_high = highs[idx + 1 : idx + right + 1]
        left_low = lows[idx - left : idx]
        right_low = lows[idx + 1 : idx + right + 1]
        is_high = highs[idx] > np.nanmax(left_high) and highs[idx] >= np.nanmax(right_high)
        is_low = lows[idx] < np.nanmin(left_low) and lows[idx] <= np.nanmin(right_low)
        if is_high:
            pivots.append({"index": idx, "type": "high", "price": float(highs[idx])})
        if is_low:
            pivots.append({"index": idx, "type": "low", "price": float(lows[idx])})
    return sorted(pivots, key=lambda item: int(item["index"]))


def _alternate_pivots(pivots: list[dict[str, object]]) -> list[dict[str, object]]:
    swings: list[dict[str, object]] = []
    for pivot in pivots:
        if not swings:
            swings.append(pivot)
            continue
        last = swings[-1]
        if pivot["type"] != last["type"]:
            swings.append(pivot)
            continue
        if pivot["type"] == "high" and float(pivot["price"]) > float(last["price"]):
            swings[-1] = pivot
        elif pivot["type"] == "low" and float(pivot["price"]) < float(last["price"]):
            swings[-1] = pivot
    return swings


def _filter_small_swings(
    swings: list[dict[str, object]],
    atr_values: pd.Series,
    min_atr_mult: float,
) -> list[dict[str, object]]:
    if min_atr_mult <= 0 or len(swings) < 2:
        return swings
    filtered = [swings[0]]
    for pivot in swings[1:]:
        last = filtered[-1]
        atr_value = atr_values.iloc[int(pivot["index"])]
        min_move = 0.0 if pd.isna(atr_value) else float(atr_value) * min_atr_mult
        if abs(float(pivot["price"]) - float(last["price"])) >= min_move:
            filtered.append(pivot)
        elif pivot["type"] == last["type"]:
            if pivot["type"] == "high" and float(pivot["price"]) > float(last["price"]):
                filtered[-1] = pivot
            elif pivot["type"] == "low" and float(pivot["price"]) < float(last["price"]):
                filtered[-1] = pivot
    return _alternate_pivots(filtered)


def calc_dow_wave(
    df: pd.DataFrame,
    *,
    left: int = 3,
    right: int = 3,
    min_atr_mult: float = 0.0,
) -> pd.DataFrame:
    """
    Detect alternating swing highs/lows and label market structure.

    The swing is drawn at the pivot candle, but a pivot that uses ``right`` bars
    can only be confirmed after those bars are available.
    """
    out = pd.DataFrame(index=df.index)
    out["dow_pivot_type"] = pd.Series(pd.NA, index=df.index, dtype="object")
    out["dow_pivot_price"] = np.nan
    out["dow_label"] = pd.Series(pd.NA, index=df.index, dtype="object")

    if df.empty or len(df) < left + right + 1:
        return out

    left = max(1, int(left))
    right = max(1, int(right))
    min_atr_mult = max(0.0, float(min_atr_mult))
    pivots = _candidate_pivots(df, left, right)
    swings = _alternate_pivots(pivots)
    swings = _filter_small_swings(swings, atr(df, 14), min_atr_mult)

    last_high: float | None = None
    last_low: float | None = None
    for pivot in swings:
        idx = int(pivot["index"])
        price = float(pivot["price"])
        pivot_type = str(pivot["type"])
        if pivot_type == "high":
            label = "HH" if last_high is not None and price > last_high else "LH"
            last_high = price
        else:
            label = "HL" if last_low is not None and price > last_low else "LL"
            last_low = price
        out.iloc[idx, out.columns.get_loc("dow_pivot_type")] = pivot_type
        out.iloc[idx, out.columns.get_loc("dow_pivot_price")] = price
        out.iloc[idx, out.columns.get_loc("dow_label")] = label

    return out
