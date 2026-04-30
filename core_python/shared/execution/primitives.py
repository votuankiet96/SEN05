"""Shared execution primitives for order simulation, costs, risk, and sizing."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.shared.contracts import (
    Direction,
    FillEvent,
    MarketOrderIntent,
    OrderIntent,
    OrderKind,
    PendingKind,
    PendingOrderIntent,
    Position,
    TradeEvent,
    intent_to_legacy_pending,
)
from core_python.shared.market import round_lot_size


def bar_time(row) -> pd.Timestamp:
    if "BarTime" in row:
        return pd.Timestamp(row["BarTime"])
    return pd.Timestamp(row.name)


def calc_dynamic_slippage(base_slippage: float, atr: float, close: float, k: float = 50) -> float:
    if close is None or close == 0:
        return float(base_slippage)
    vol_ratio = atr / close
    vol_ratio = min(max(vol_ratio, 0.0005), 0.01)
    return float(base_slippage) * (1 + k * vol_ratio)


def adverse_entry_price(entry_price: float, direction: int, spread: float, slippage: float) -> float:
    return float(entry_price) + int(direction) * (float(spread) + float(slippage))


def adverse_exit_price(exit_price: float, direction: int, spread: float, slippage: float) -> float:
    return float(exit_price) - int(direction) * (float(spread) + float(slippage))


def build_order_intents(
    strategy_module: Any,
    frame,
    symbol: str,
    cfg: dict[str, Any],
) -> list[OrderIntent]:
    create = getattr(strategy_module, "create_order_intent", None) or getattr(
        strategy_module,
        "create_order_intent_from_signal",
        None,
    )
    if create is None:
        raise AttributeError(
            "strategy_module must expose create_order_intent or create_order_intent_from_signal"
        )
    intents: list[OrderIntent] = []
    for _, row in frame.iterrows():
        intent = create(symbol, row, cfg)
        if intent is not None:
            intents.append(intent)
    return intents


def pending_touched(row, intent: PendingOrderIntent) -> bool:
    return (
        (intent.direction == 1 and float(row["high"]) >= float(intent.entry))
        or (intent.direction == -1 and float(row["low"]) <= float(intent.entry))
    )


def fill_pending_order(
    row,
    intent: PendingOrderIntent,
    spread: float = 0.0,
    slippage: float = 0.0,
) -> FillEvent | None:
    if not pending_touched(row, intent):
        return None
    return FillEvent(
        symbol=intent.symbol,
        direction=intent.direction,
        fill_time=bar_time(row),
        fill_price=adverse_entry_price(intent.entry, intent.direction, spread, slippage),
        order_type=intent.order_type,
        metadata={"intent": intent},
    )


def fill_market_order(
    row,
    intent: MarketOrderIntent,
    spread: float = 0.0,
    slippage: float = 0.0,
) -> FillEvent:
    base = float(row["open"])
    return FillEvent(
        symbol=intent.symbol,
        direction=intent.direction,
        fill_time=bar_time(row),
        fill_price=adverse_entry_price(base, intent.direction, spread, slippage),
        order_type=intent.order_type,
        metadata={"intent": intent},
    )


def round_turn_commission(
    lot_size: float,
    commission_per_lot: float,
    fraction: float = 1.0,
) -> float:
    return 2.0 * float(lot_size) * float(commission_per_lot) * float(fraction)


def swap_cost(
    holding_days: int,
    lot_size: float,
    direction: int,
    swap_long_per_lot_per_day: float = 0.0,
    swap_short_per_lot_per_day: float = 0.0,
) -> float:
    rate = swap_long_per_lot_per_day if int(direction) == 1 else swap_short_per_lot_per_day
    return max(int(holding_days), 0) * float(lot_size) * float(rate)


def price_pnl(
    entry: float,
    exit_price: float,
    direction: int,
    lot_size: float,
    cfg: dict[str, Any],
) -> float:
    point_size = float(cfg.get("point_size", 1.0))
    contract_value = float(cfg.get("contract_value", 0.0))
    return ((float(exit_price) - float(entry)) * int(direction) / point_size) * contract_value * float(lot_size)


def risk_sized_lots(
    equity: float,
    risk_pct: float | None = None,
    entry: float = 0.0,
    sl: float = 0.0,
    cfg: dict[str, Any] | None = None,
    commission_per_lot: float = 0.0,
    *,
    risk_per_trade: float | None = None,
    symbol_config: dict[str, Any] | None = None,
) -> tuple[float, float]:
    if risk_pct is None:
        risk_pct = risk_per_trade
    if cfg is None:
        cfg = symbol_config
    if risk_pct is None or cfg is None:
        raise TypeError("risk_sized_lots requires risk_pct/cfg or risk_per_trade/symbol_config")
    distance = abs(float(entry) - float(sl))
    risk_usd = float(equity) * float(risk_pct)
    lots = risk_sized_lots_from_distance(equity, risk_pct, distance, cfg, commission_per_lot)
    return lots, risk_usd


def risk_sized_lots_from_distance(
    equity: float,
    risk_pct: float,
    sl_distance: float,
    cfg: dict[str, Any],
    commission_per_lot: float = 0.0,
) -> float:
    point_size = float(cfg.get("point_size", 1.0))
    contract_value = float(cfg.get("contract_value", 0.0))
    if sl_distance <= 0 or point_size <= 0 or contract_value <= 0:
        return 0.0
    risk_usd = float(equity) * float(risk_pct)
    risk_per_lot = (float(sl_distance) / point_size) * contract_value + 2.0 * float(commission_per_lot)
    if risk_per_lot <= 0:
        return 0.0
    raw_lots = risk_usd / risk_per_lot
    min_lot = float(cfg.get("min_lot_size", 0.01))
    if raw_lots < min_lot:
        return 0.0
    return round_lot_size(
        raw_lots,
        min_lot=min_lot,
        max_lot=float(cfg.get("max_lot_size", 100.0)),
        lot_step=float(cfg.get("lot_step", cfg.get("min_lot_size", 0.01))),
    )


def max_drawdown_breached(
    equity: float,
    initial_equity: float,
    peak_equity: float,
    max_drawdown: float,
    mode: str = "fixed_initial",
) -> bool:
    if float(max_drawdown) >= 1:
        return False
    if mode in {"trailing_peak", "peak"}:
        return float(equity) <= float(peak_equity) * (1.0 - float(max_drawdown))
    return float(equity) <= float(initial_equity) * (1.0 - float(max_drawdown))


def daily_loss_breached(daily_pnl: float, init_eq: float, daily_loss_limit: float) -> bool:
    return float(daily_pnl) <= -(float(init_eq) * float(daily_loss_limit))


def apply_realized_pnl(equity: float, pnl: float) -> float:
    return float(equity) + float(pnl)


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
