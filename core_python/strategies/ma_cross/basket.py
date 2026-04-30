"""Basket state helpers for MA Cross controlled lot scaling."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from core_python.shared.execution.primitives import (
    adverse_exit_price,
    max_drawdown_breached,
    price_pnl,
    risk_sized_lots_from_distance,
    round_turn_commission,
    swap_cost,
)
from core_python.shared.market import round_lot_size
from .config import get_basket_settings


@dataclass(slots=True)
class BasketOrder:
    order_id: int
    symbol: str
    direction: int
    entry_time: pd.Timestamp
    entry_price: float
    lot_size: float
    order_index: int
    entry_spread_pts: float = 0.0
    entry_slippage_pts: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BasketSnapshot:
    timestamp: pd.Timestamp
    total_buy_lot: float
    total_sell_lot: float
    gross_exposure: float
    net_exposure: float
    floating_pnl: float
    order_count: int
    avg_buy_entry: float | None = None
    avg_sell_entry: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def next_linear_lot(
    order_index: int,
    strategy: dict[str, Any] | None,
    symbol_config: dict[str, Any],
    *,
    current_equity: float | None = None,
    atr_stop_pts: float | None = None,
) -> float:
    """Return the next capped linear lot. This is not martingale sizing."""
    strategy = strategy or {}
    settings = get_basket_settings(strategy)
    mode = str(settings.get("lot_sizing_mode", "fixed_linear")).lower()
    if mode == "risk_based":
        if current_equity is None or atr_stop_pts is None or float(atr_stop_pts) <= 0:
            return 0.0
        initial_lot = risk_sized_lots_from_distance(
            float(current_equity),
            float(strategy.get("risk_per_trade", 0.0)),
            float(atr_stop_pts),
            symbol_config,
            float(symbol_config.get("commission_per_lot", 0.0)),
        )
        lot_step = initial_lot * float(settings.get("lot_step_ratio", 1.0))
        max_single = initial_lot * float(settings.get("max_single_lot_mult", 3.0))
    elif mode == "fixed_linear":
        initial_lot = float(settings["initial_lot"])
        lot_step = float(settings["lot_step"])
        max_single = float(settings["max_single_lot"])
    else:
        raise ValueError(
            "Unsupported basket lot_sizing_mode "
            f"'{mode}'. Use 'fixed_linear' or 'risk_based'."
        )

    if initial_lot <= 0:
        return 0.0

    raw_lot = min(initial_lot + int(order_index) * lot_step, max_single)
    return round_lot_size(
        raw_lot,
        min_lot=float(symbol_config.get("min_lot_size", 0.01)),
        max_lot=min(
            float(max_single),
            float(symbol_config.get("max_lot_size", max_single)),
        ),
        lot_step=float(symbol_config.get("lot_step", settings["lot_step"])),
    )


def effective_basket_caps(
    strategy: dict[str, Any],
    *,
    initial_lot: float,
) -> dict[str, float]:
    """Return effective exposure caps for the configured basket sizing mode."""
    settings = get_basket_settings(strategy)
    mode = str(settings.get("lot_sizing_mode", "fixed_linear")).lower()
    if mode == "risk_based" and initial_lot > 0:
        return {
            "max_single_lot": float(initial_lot) * float(settings["max_single_lot_mult"]),
            "max_total_lot": float(initial_lot) * float(settings["max_total_lot_mult"]),
            "max_net_lot": float(initial_lot) * float(settings["max_net_lot_mult"]),
        }
    return {
        "max_single_lot": float(settings["max_single_lot"]),
        "max_total_lot": float(settings["max_total_lot"]),
        "max_net_lot": float(settings["max_net_lot"]),
    }


def _weighted_average(entries: list[tuple[float, float]]) -> float | None:
    total_lot = sum(lot for _, lot in entries)
    if total_lot <= 0:
        return None
    return sum(price * lot for price, lot in entries) / total_lot


def basket_snapshot(
    orders: list[BasketOrder],
    *,
    timestamp: pd.Timestamp,
    mark_price: float,
    cfg: dict[str, Any],
    spread_pts: float,
    slippage_pts: float,
    commission_per_lot: float,
) -> BasketSnapshot:
    """Mark the open basket to market using adverse exit assumptions."""
    total_buy = sum(order.lot_size for order in orders if order.direction == 1)
    total_sell = sum(order.lot_size for order in orders if order.direction == -1)
    gross_exposure = total_buy + total_sell
    net_exposure = total_buy - total_sell

    floating_pnl = 0.0
    buy_entries: list[tuple[float, float]] = []
    sell_entries: list[tuple[float, float]] = []
    for order in orders:
        exit_price = adverse_exit_price(
            mark_price,
            order.direction,
            spread_pts,
            slippage_pts,
        )
        gross = price_pnl(
            order.entry_price,
            exit_price,
            order.direction,
            order.lot_size,
            cfg,
        )
        commission = round_turn_commission(order.lot_size, commission_per_lot)
        holding_days = max((timestamp.date() - order.entry_time.date()).days, 0)
        swap = swap_cost(
            holding_days,
            order.lot_size,
            order.direction,
            float(cfg.get("swap_long_per_lot_per_day", 0.0)),
            float(cfg.get("swap_short_per_lot_per_day", 0.0)),
        )
        floating_pnl += gross - commission - swap
        if order.direction == 1:
            buy_entries.append((order.entry_price, order.lot_size))
        else:
            sell_entries.append((order.entry_price, order.lot_size))

    return BasketSnapshot(
        timestamp=timestamp,
        total_buy_lot=round(float(total_buy), 8),
        total_sell_lot=round(float(total_sell), 8),
        gross_exposure=round(float(gross_exposure), 8),
        net_exposure=round(float(net_exposure), 8),
        floating_pnl=round(float(floating_pnl), 8),
        order_count=len(orders),
        avg_buy_entry=_weighted_average(buy_entries),
        avg_sell_entry=_weighted_average(sell_entries),
    )


def can_open_new_order(
    snapshot: BasketSnapshot,
    *,
    direction: int,
    lot_size: float,
    day_start_equity: float,
    equity_mark: float,
    initial_equity: float,
    peak_equity: float,
    strategy: dict[str, Any],
    initial_lot: float | None = None,
) -> tuple[bool, str | None]:
    """Return whether a new basket leg can be opened and why it was blocked."""
    settings = get_basket_settings(strategy)
    daily_limit = float(strategy.get("daily_loss_limit", strategy.get("ftmo_daily_limit", 1.0)))
    max_dd = float(strategy.get("max_drawdown_limit", strategy.get("ftmo_max_dd", 1.0)))
    max_dd_mode = str(strategy.get("max_drawdown_mode", "fixed_initial"))
    caps = effective_basket_caps(
        strategy,
        initial_lot=float(initial_lot if initial_lot is not None else lot_size),
    )

    if snapshot.order_count >= int(settings["max_orders"]):
        return False, "max_orders"

    projected_gross = snapshot.gross_exposure + float(lot_size)
    if snapshot.gross_exposure >= caps["max_total_lot"] or (
        projected_gross > caps["max_total_lot"]
    ):
        return False, "max_total_lot"

    projected_net = snapshot.net_exposure + int(direction) * float(lot_size)
    if abs(snapshot.net_exposure) >= caps["max_net_lot"] or (
        abs(projected_net) > caps["max_net_lot"]
    ):
        return False, "max_net_lot"

    daily_loss = float(day_start_equity) - float(equity_mark)
    if daily_loss >= float(initial_equity) * daily_limit:
        return False, "daily_loss"

    if max_drawdown_breached(equity_mark, initial_equity, peak_equity, max_dd, max_dd_mode):
        return False, "max_drawdown"

    return True, None


__all__ = [
    "BasketOrder",
    "BasketSnapshot",
    "basket_snapshot",
    "can_open_new_order",
    "effective_basket_caps",
    "next_linear_lot",
]
