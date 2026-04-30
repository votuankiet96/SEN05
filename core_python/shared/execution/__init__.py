"""Public execution API.

This package groups shared execution primitives and generic engines.
"""

from __future__ import annotations

from core_python.shared.execution.engines.market import backtest_market_symbol
from core_python.shared.execution.primitives import (
    Direction,
    FillEvent,
    MarketOrderIntent,
    OrderIntent,
    OrderKind,
    PendingKind,
    PendingOrderIntent,
    Position,
    TradeEvent,
    adverse_entry_price,
    adverse_exit_price,
    apply_realized_pnl,
    bar_time,
    build_order_intents,
    calc_dynamic_slippage,
    daily_loss_breached,
    fill_market_order,
    fill_pending_order,
    intent_to_legacy_pending,
    max_drawdown_breached,
    pending_touched,
    price_pnl,
    risk_sized_lots,
    risk_sized_lots_from_distance,
    round_turn_commission,
    swap_cost,
)

__all__ = [
    "Direction",
    "FillEvent",
    "MarketOrderIntent",
    "OrderIntent",
    "OrderKind",
    "PendingKind",
    "PendingOrderIntent",
    "Position",
    "TradeEvent",
    "adverse_entry_price",
    "adverse_exit_price",
    "apply_realized_pnl",
    "backtest_market_symbol",
    "bar_time",
    "build_order_intents",
    "calc_dynamic_slippage",
    "daily_loss_breached",
    "fill_market_order",
    "fill_pending_order",
    "intent_to_legacy_pending",
    "max_drawdown_breached",
    "pending_touched",
    "price_pnl",
    "risk_sized_lots",
    "risk_sized_lots_from_distance",
    "round_turn_commission",
    "swap_cost",
]
