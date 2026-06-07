"""Take-profit calculation registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from backtest_optimize.contracts import Direction, SignalRow, TPLevel

TPFunction = Callable[[SignalRow, float, float, pd.DataFrame, dict[str, Any]], tuple[TPLevel, ...]]
TP_REGISTRY: dict[str, TPFunction] = {}
KTP_FIB_LEVELS: dict[int, float] = {
    1: 1.272,
    2: 1.618,
    3: 2.000,
    4: 2.272,
    5: 2.618,
    6: 3.618,
}
DEFAULT_KTP_LEVEL = 4
DEFAULT_KTP = KTP_FIB_LEVELS[DEFAULT_KTP_LEVEL]
CBOT_V0_KTP_LEVELS: dict[int, float] = {
    1: 1.272,
    2: 1.541,
    3: 1.811,
    4: 2.080,
    5: 2.349,
    6: 2.618,
    7: 2.888,
    8: 3.158,
    9: 3.427,
    10: 3.696,
    11: 3.966,
    12: 4.236,
}
DEFAULT_CBOT_V0_KTP_LEVEL = 6
DEFAULT_CBOT_V0_KTP = CBOT_V0_KTP_LEVELS[DEFAULT_CBOT_V0_KTP_LEVEL]


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


def _combo_ktp(params: dict[str, Any]) -> tuple[int, float]:
    level_value = params.get("ktp_level", params.get("fib_level", params.get("level")))
    if level_value is not None:
        level = int(level_value)
        if level not in KTP_FIB_LEVELS:
            raise ValueError("combo_fib_atr ktp_level must be between 1 and 6.")
        return level, KTP_FIB_LEVELS[level]

    if params.get("ktp") is not None:
        ktp = float(params["ktp"])
        for level, multiplier in KTP_FIB_LEVELS.items():
            if abs(ktp - multiplier) <= 1e-9:
                return level, multiplier
        allowed = ", ".join(str(value) for value in KTP_FIB_LEVELS.values())
        raise ValueError(f"combo_fib_atr ktp must be one of: {allowed}.")

    return DEFAULT_KTP_LEVEL, DEFAULT_KTP


def _ctrader_v0_ktp(params: dict[str, Any]) -> tuple[int, float]:
    level_value = params.get("ktp_level", params.get("fib_level", params.get("level")))
    if level_value is not None:
        level = int(level_value)
        if level not in CBOT_V0_KTP_LEVELS:
            raise ValueError("fib_atr_ctrader_v0 ktp_level must be between 1 and 12.")
        return level, CBOT_V0_KTP_LEVELS[level]

    if params.get("ktp") is not None:
        ktp = float(params["ktp"])
        for level, multiplier in CBOT_V0_KTP_LEVELS.items():
            if abs(ktp - multiplier) <= 1e-9:
                return level, multiplier
        allowed = ", ".join(str(value) for value in CBOT_V0_KTP_LEVELS.values())
        raise ValueError(f"fib_atr_ctrader_v0 ktp must be one of: {allowed}.")

    return DEFAULT_CBOT_V0_KTP_LEVEL, DEFAULT_CBOT_V0_KTP


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


@register_tp_method("combo_fib_atr")
def combo_fib_atr(
    signal: SignalRow,
    entry_price: float,
    sl_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[TPLevel, ...]:
    """Create one Combo-style TP at Entry +/- Fibonacci KTP level * ATR."""
    del sl_price, bars
    if signal.atr is None or signal.atr <= 0:
        raise ValueError("combo_fib_atr requires signal.atr > 0.")

    ktp_level, ktp = _combo_ktp(params)
    distance = float(signal.atr) * ktp
    price = entry_price + distance if signal.direction == Direction.BUY else entry_price - distance
    weight = float(params.get("weight", 1.0))
    return (
        TPLevel(
            level=1,
            price=float(price),
            weight=weight,
            label=f"KTP_L{ktp_level}_{ktp:g}",
        ),
    )


@register_tp_method("fib_atr_ctrader_v0")
def fib_atr_ctrader_v0(
    signal: SignalRow,
    entry_price: float,
    sl_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[TPLevel, ...]:
    """Create cBot Combo V0 TP levels at Entry +/- KTP * ATR.

    `exit_mode=fixed_only` returns one TP level. `exit_mode=two_legs` returns
    leg 1 with the fixed TP and leg 2 as a no-fixed-TP trailing leg.
    """
    del sl_price, bars
    if signal.atr is None or signal.atr <= 0:
        raise ValueError("fib_atr_ctrader_v0 requires signal.atr > 0.")

    ktp_level, ktp = _ctrader_v0_ktp(params)
    distance = float(signal.atr) * ktp
    price = entry_price + distance if signal.direction == Direction.BUY else entry_price - distance
    exit_mode = str(params.get("exit_mode", "fixed_only")).strip().lower()
    fixed = TPLevel(
        level=1,
        price=float(price),
        weight=1.0,
        label=f"CBOT_V0_L{ktp_level}_{ktp:g}",
    )
    if exit_mode in {"fixed_only", "fixedonly", "1"}:
        return (fixed,)
    if exit_mode in {"two_legs", "twolegs", "2"}:
        trailing_weight = float(params.get("trailing_weight", fixed.weight))
        return (
            TPLevel(level=1, price=float(price), weight=float(params.get("fixed_weight", 1.0)), label=fixed.label),
            TPLevel(level=2, price=float("inf"), weight=trailing_weight, label="NO_TP_SMA20_TRAIL"),
        )
    raise ValueError("fib_atr_ctrader_v0 exit_mode must be fixed_only or two_legs.")


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
