"""MA Cross-owned market order intent generation."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.shared.execution.types import MarketOrderIntent


def create_market_next_open_intent(
    symbol: str,
    bar: pd.Series,
    *,
    direction: int | None = None,
) -> MarketOrderIntent | None:
    signal = int(direction if direction is not None else bar.get("signal", 0))
    if signal not in {-1, 1}:
        return None
    created_time = pd.Timestamp(bar["BarTime"]) if "BarTime" in bar else pd.Timestamp(bar.name)
    return MarketOrderIntent(
        symbol=symbol,
        direction=signal,  # type: ignore[arg-type]
        created_time=created_time,
        strategy_name="ma_cross",
        execute_at="next_open",
        metadata={"atr": float(bar.get("atr", 0.0))},
    )


def create_order_intent_from_signal(
    symbol: str,
    bar: pd.Series,
    cfg: dict[str, Any],
    strategy: dict[str, Any],
) -> MarketOrderIntent | None:
    _ = cfg, strategy
    return create_market_next_open_intent(symbol, bar)


def resolve_exit_policy(strategy: dict[str, Any]) -> dict[str, Any]:
    return {
        "atr_stop_mult": float(strategy.get("atr_stop_mult", 2.0)),
        "atr_tp_mult": float(strategy.get("atr_tp_mult", 2.0)),
        "partial_tp_fraction": float(strategy.get("partial_tp_fraction", 0.5)),
        "trailing_activation": float(strategy.get("trailing_activation", 1.0)),
        "trailing_column": str(strategy.get("trailing_column", "slow_ma")),
        "reverse_on_opposite": bool(strategy.get("reverse_on_opposite", True)),
    }


__all__ = [
    "create_market_next_open_intent",
    "create_order_intent_from_signal",
    "resolve_exit_policy",
]
