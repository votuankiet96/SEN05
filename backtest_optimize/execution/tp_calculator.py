"""Take-profit calculation registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from backtest_optimize.contracts import Direction, SignalRow, TPLevel

TPFunction = Callable[[SignalRow, float, float, pd.DataFrame, dict[str, Any]], tuple[TPLevel, ...]]
TP_REGISTRY: dict[str, TPFunction] = {}


def register_tp_method(name: str, fn: TPFunction | None = None):
    """Register a TP method by name."""

    def decorator(inner: TPFunction) -> TPFunction:
        TP_REGISTRY[name] = inner
        return inner

    if fn is not None:
        return decorator(fn)
    return decorator


def get_tp_method(name: str) -> TPFunction:
    try:
        return TP_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unknown TP method: {name!r}") from exc


def calculate_tp_levels(
    method: str,
    signal: SignalRow,
    entry_price: float,
    sl_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> tuple[TPLevel, ...]:
    """Dispatch TP level calculation."""
    levels = get_tp_method(method)(signal, float(entry_price), float(sl_price), bars, params or {})
    if not levels:
        raise ValueError("TP calculation returned no levels.")
    return levels


def _weights(params: dict[str, Any], count: int) -> list[float]:
    raw = params.get("weights")
    if raw is None:
        return [1.0 for _ in range(count)]
    if len(raw) != count:
        raise ValueError("weights length must match TP level count.")
    return [float(value) for value in raw]


@register_tp_method("risk_multiple")
def risk_multiple(
    signal: SignalRow,
    entry_price: float,
    sl_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[TPLevel, ...]:
    """Create TP levels at configured R multiples."""
    del bars
    multiples = params.get("r_multiples")
    if multiples is None:
        count = int(params.get("n_levels", 3))
        step = float(params.get("step_r", 1.0))
        multiples = [step * idx for idx in range(1, count + 1)]

    risk_distance = abs(float(entry_price) - float(sl_price))
    if risk_distance <= 0:
        raise ValueError("SL distance must be positive.")

    weights = _weights(params, len(multiples))
    levels = []
    for idx, (multiple, weight) in enumerate(zip(multiples, weights), start=1):
        distance = risk_distance * float(multiple)
        if signal.direction == Direction.BUY:
            price = entry_price + distance
        else:
            price = entry_price - distance
        levels.append(TPLevel(level=idx, price=float(price), weight=weight, label=f"TP{idx}"))
    return tuple(levels)


@register_tp_method("atr_multiple")
def atr_multiple(
    signal: SignalRow,
    entry_price: float,
    sl_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[TPLevel, ...]:
    """Create TP levels at ATR multiples from entry."""
    del sl_price, bars
    if signal.atr is None or signal.atr <= 0:
        raise ValueError("atr_multiple TP requires signal.atr > 0.")
    multiples = params.get("atr_multiples", [1.0, 2.0, 3.0])
    weights = _weights(params, len(multiples))
    levels = []
    for idx, (multiple, weight) in enumerate(zip(multiples, weights), start=1):
        distance = signal.atr * float(multiple)
        price = entry_price + distance if signal.direction == Direction.BUY else entry_price - distance
        levels.append(TPLevel(level=idx, price=float(price), weight=weight, label=f"TP{idx}"))
    return tuple(levels)


@register_tp_method("fixed_distance")
def fixed_distance(
    signal: SignalRow,
    entry_price: float,
    sl_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[TPLevel, ...]:
    """Create one or more fixed-distance TP levels."""
    del sl_price, bars
    distances_raw = params.get("distances")
    if distances_raw is None:
        distances_raw = params.get("distance")
    if distances_raw is None:
        raise ValueError("fixed_distance TP requires 'distances' or 'distance' in params.")
    if isinstance(distances_raw, (int, float)):
        distances = [distances_raw]
    else:
        distances = list(distances_raw)
    weights = _weights(params, len(distances))
    levels = []
    for idx, (distance, weight) in enumerate(zip(distances, weights), start=1):
        distance = float(distance)
        if distance <= 0:
            raise ValueError("TP distance must be positive.")
        price = entry_price + distance if signal.direction == Direction.BUY else entry_price - distance
        levels.append(TPLevel(level=idx, price=float(price), weight=weight, label=f"TP{idx}"))
    return tuple(levels)
