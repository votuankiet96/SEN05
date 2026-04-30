"""Combo-owned order intent generation."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.shared.execution.types import PendingOrderIntent


def create_pending_breakout_intent(
    symbol: str,
    bar: pd.Series,
    *,
    direction: int | None = None,
    x: float,
    ktp: float,
    atr: float | None = None,
    ttl_bars: int = 3,
) -> PendingOrderIntent | None:
    """Create Combo's pending breakout order intent from a signal bar.

    Combo owns the formula:
    - BUY entry = signal-bar high + x, SL = signal-bar low - x
    - SELL entry = signal-bar low - x, SL = signal-bar high + x
    - TP = entry +/- ktp * ATR
    """
    signal = int(direction if direction is not None else bar.get("signal", 0))
    if signal not in {-1, 1}:
        return None

    atr_value = float(atr if atr is not None else bar.get("atr", 0.0))
    entry = float(bar["high"]) + float(x) if signal == 1 else float(bar["low"]) - float(x)
    sl = float(bar["low"]) - float(x) if signal == 1 else float(bar["high"]) + float(x)
    tp = entry + signal * float(ktp) * atr_value
    created_time = pd.Timestamp(bar["BarTime"]) if "BarTime" in bar else pd.Timestamp(bar.name)

    return PendingOrderIntent(
        symbol=symbol,
        direction=signal,  # type: ignore[arg-type]
        created_time=created_time,
        entry=entry,
        sl=sl,
        tp=tp,
        ttl_bars=int(ttl_bars),
        strategy_name="combo",
        metadata={"atr": atr_value, "x": float(x), "ktp": float(ktp)},
    )


def create_order_intent_from_signal(
    symbol: str,
    bar: pd.Series,
    cfg: dict[str, Any],
    strategy: dict[str, Any],
) -> PendingOrderIntent | None:
    return create_pending_breakout_intent(
        symbol,
        bar,
        x=float(cfg["x"]),
        ktp=float(strategy.get("ktp", cfg.get("ktp", 0.0))),
        ttl_bars=int(strategy.get("pending_ttl_bars", 3)),
    )


def resolve_exit_policy(strategy: dict[str, Any]) -> dict[str, Any]:
    """Return the Combo exit policy consumed by execution."""
    return {
        "partial_tp_fraction": float(strategy.get("partial_tp_fraction", 0.5)),
        "trailing_activation": float(strategy.get("trailing_activation", 1.0)),
        "reverse_on_opposite": bool(strategy.get("reverse_on_opposite", True)),
        "pending_ttl_bars": int(strategy.get("pending_ttl_bars", 3)),
    }


__all__ = [
    "create_order_intent_from_signal",
    "create_pending_breakout_intent",
    "resolve_exit_policy",
]
