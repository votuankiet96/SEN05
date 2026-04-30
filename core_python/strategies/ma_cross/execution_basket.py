"""Basket reversal execution engine for MA Cross."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.shared.execution.primitives import (
    adverse_entry_price,
    adverse_exit_price,
    bar_time,
    calc_dynamic_slippage,
    price_pnl,
    round_turn_commission,
    swap_cost,
)
from core_python.shared.market import DEFAULT_COSTS
from .basket import BasketOrder, basket_snapshot, can_open_new_order, next_linear_lot
from .config import get_basket_settings


def _weighted_average(entries: list[tuple[float, float]]) -> float | None:
    total_lot = sum(lot for _, lot in entries)
    if total_lot <= 0:
        return None
    return sum(price * lot for price, lot in entries) / total_lot


def _close_basket(
    orders: list[BasketOrder],
    *,
    exit_time: pd.Timestamp,
    base_exit_price: float,
    exit_reason: str,
    cfg: dict[str, Any],
    spread_pts: float,
    slippage_pts: float,
    commission_per_lot: float,
) -> dict[str, Any]:
    child_orders: list[dict[str, Any]] = []
    gross_pnl = 0.0
    commission = 0.0
    swap_total = 0.0
    buy_lot = 0.0
    sell_lot = 0.0
    buy_entries: list[tuple[float, float]] = []
    sell_entries: list[tuple[float, float]] = []
    all_entries: list[tuple[float, float]] = []

    for order in orders:
        exit_price = adverse_exit_price(
            base_exit_price,
            order.direction,
            spread_pts,
            slippage_pts,
        )
        order_gross = price_pnl(
            order.entry_price,
            exit_price,
            order.direction,
            order.lot_size,
            cfg,
        )
        order_commission = round_turn_commission(order.lot_size, commission_per_lot)
        holding_days = max((exit_time.date() - order.entry_time.date()).days, 0)
        order_swap = swap_cost(
            holding_days,
            order.lot_size,
            order.direction,
            float(cfg.get("swap_long_per_lot_per_day", 0.0)),
            float(cfg.get("swap_short_per_lot_per_day", 0.0)),
        )
        order_pnl = order_gross - order_commission - order_swap
        gross_pnl += order_gross
        commission += order_commission
        swap_total += order_swap
        all_entries.append((order.entry_price, order.lot_size))
        if order.direction == 1:
            buy_lot += order.lot_size
            buy_entries.append((order.entry_price, order.lot_size))
        else:
            sell_lot += order.lot_size
            sell_entries.append((order.entry_price, order.lot_size))

        child_orders.append(
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "direction": order.direction,
                "entry_time": order.entry_time,
                "exit_time": exit_time,
                "entry": round(float(order.entry_price), 8),
                "exit": round(float(exit_price), 8),
                "lot_size": round(float(order.lot_size), 8),
                "pnl_usd": round(float(order_pnl), 2),
                "gross_pnl": round(float(order_gross), 2),
                "commission": round(float(order_commission), 2),
                "swap_cost": round(float(order_swap), 2),
            }
        )

    pnl = gross_pnl - commission - swap_total
    total_lot = buy_lot + sell_lot
    net_lot = buy_lot - sell_lot
    first_entry_time = min(order.entry_time for order in orders)
    basket_direction = 1 if net_lot >= 0 else -1

    return {
        "symbol": orders[0].symbol,
        "direction": basket_direction,
        "entry_time": first_entry_time,
        "exit_time": exit_time,
        "entry": (
            round(float(_weighted_average(all_entries)), 8)
            if _weighted_average(all_entries) is not None
            else None
        ),
        "exit": round(float(base_exit_price), 8),
        "sl": None,
        "tp": None,
        "lot_size": round(float(total_lot), 8),
        "pnl_usd": round(float(pnl), 2),
        "pnl_net": round(float(pnl), 2),
        "gross_pnl": round(float(gross_pnl), 2),
        "commission": round(float(commission), 2),
        "swap_cost": round(float(swap_total), 2),
        "r_multiple": 0.0,
        "exit_reason": exit_reason,
        "partial_tp_hit": False,
        "half1_exit": None,
        "half1_exit_time": None,
        "basket_id": child_orders[0]["order_id"] if child_orders else None,
        "orders_closed": len(child_orders),
        "total_buy_lot": round(float(buy_lot), 8),
        "total_sell_lot": round(float(sell_lot), 8),
        "gross_exposure": round(float(total_lot), 8),
        "net_exposure": round(float(net_lot), 8),
        "avg_buy_entry": (
            round(float(_weighted_average(buy_entries)), 8)
            if _weighted_average(buy_entries) is not None
            else None
        ),
        "avg_sell_entry": (
            round(float(_weighted_average(sell_entries)), 8)
            if _weighted_average(sell_entries) is not None
            else None
        ),
        "child_orders": child_orders,
    }


def backtest_basket_reversal_symbol(
    symbol: str,
    df: pd.DataFrame,
    cfg: dict[str, Any],
    init_eq: float,
    *,
    strategy: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], pd.Series]:
    """Backtest MA Cross basket reversal with capped linear lot scaling.

    A signal at bar T is acted on at bar T+1 open. Opposite signals add a new
    basket leg instead of closing the old leg.
    """
    if df.empty:
        return [], pd.Series(dtype=float)

    s = dict(strategy or {})
    if not bool(s.get("hedge_allowed", True)):
        raise ValueError(
            "basket_reversal execution model requires hedge_allowed=True. "
            "Set execution_model='market_single' for non-hedging accounts."
        )
    basket_cfg = get_basket_settings(s)
    c = {**DEFAULT_COSTS, **(costs or {})}

    frame = df.sort_index().copy()
    if "BarTime" not in frame.columns:
        frame["BarTime"] = frame.index

    base_slip = float(cfg.get("slippage_pts", c["slippage_pts"]))
    spread_pts = float(cfg.get("spread_pts", 0.0))
    commission_per_lot = float(c.get("commission_per_lot", cfg.get("commission_per_lot", 0.0)))
    allow_same_bar_close = bool(basket_cfg.get("allow_same_bar_basket_close", False))
    reversal_only = bool(basket_cfg.get("reversal_only", True))

    trades: list[dict[str, Any]] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    open_orders: list[BasketOrder] = []
    equity = float(init_eq)
    peak_eq = float(init_eq)
    day_start_equity = float(init_eq)
    last_bar_mtm = float(init_eq)
    cur_day = None
    pending_signal = 0
    next_order_id = 1
    sizing_cfg = {**cfg, "commission_per_lot": commission_per_lot}

    rows = list(frame.iterrows())
    for _, bar in rows:
        now = bar_time(bar)
        bdate = now.date()
        if bdate != cur_day:
            cur_day = bdate
            day_start_equity = last_bar_mtm

        slippage = calc_dynamic_slippage(
            base_slippage=base_slip,
            atr=float(bar.get("atr", 0.0)),
            close=float(bar.get("close", 0.0)),
            k=float(c.get("slippage_k", 50)),
        )

        open_snapshot = basket_snapshot(
            open_orders,
            timestamp=now,
            mark_price=float(bar["open"]),
            cfg=cfg,
            spread_pts=spread_pts,
            slippage_pts=slippage,
            commission_per_lot=commission_per_lot,
        )
        mark_equity_open = equity + open_snapshot.floating_pnl
        peak_eq = max(peak_eq, mark_equity_open)

        if pending_signal:
            atr_stop_pts = float(bar.get("atr", 0.0)) * float(s.get("atr_stop_mult", 2.0))
            initial_lot = next_linear_lot(
                0,
                s,
                sizing_cfg,
                current_equity=mark_equity_open,
                atr_stop_pts=atr_stop_pts,
            )
            lot = next_linear_lot(
                open_snapshot.order_count,
                s,
                sizing_cfg,
                current_equity=mark_equity_open,
                atr_stop_pts=atr_stop_pts,
            )
            can_open, block_reason = can_open_new_order(
                open_snapshot,
                direction=int(pending_signal),
                lot_size=lot,
                day_start_equity=day_start_equity,
                equity_mark=mark_equity_open,
                initial_equity=init_eq,
                peak_equity=peak_eq,
                strategy=s,
                initial_lot=initial_lot,
            )
            if can_open and lot > 0:
                entry = adverse_entry_price(
                    float(bar["open"]),
                    int(pending_signal),
                    spread_pts,
                    slippage,
                )
                open_orders.append(
                    BasketOrder(
                        order_id=next_order_id,
                        symbol=symbol,
                        direction=int(pending_signal),
                        entry_time=now,
                        entry_price=entry,
                        lot_size=lot,
                        order_index=open_snapshot.order_count,
                        entry_spread_pts=spread_pts,
                        entry_slippage_pts=slippage,
                    )
                )
                next_order_id += 1
            elif open_orders:
                open_orders[-1].metadata["last_block_reason"] = block_reason
            pending_signal = 0

        close_snapshot = basket_snapshot(
            open_orders,
            timestamp=now,
            mark_price=float(bar["close"]),
            cfg=cfg,
            spread_pts=spread_pts,
            slippage_pts=slippage,
            commission_per_lot=commission_per_lot,
        )
        mark_equity_close = equity + close_snapshot.floating_pnl
        peak_eq = max(peak_eq, mark_equity_close)

        can_close_same_bar = allow_same_bar_close or all(
            order.entry_time != now for order in open_orders
        )
        close_reason: str | None = None
        if open_orders and can_close_same_bar:
            if close_snapshot.floating_pnl >= float(basket_cfg["basket_take_profit"]):
                close_reason = "BASKET_TP"
            elif close_snapshot.floating_pnl <= -float(basket_cfg["basket_stop_loss"]):
                close_reason = "BASKET_SL"

        if close_reason is not None:
            trade = _close_basket(
                open_orders,
                exit_time=now,
                base_exit_price=float(bar["close"]),
                exit_reason=close_reason,
                cfg=cfg,
                spread_pts=spread_pts,
                slippage_pts=slippage,
                commission_per_lot=commission_per_lot,
            )
            trades.append(trade)
            equity += float(trade["pnl_usd"])
            open_orders = []
            mark_equity_close = equity
            peak_eq = max(peak_eq, equity)

        equity_points.append((now, mark_equity_close))
        last_bar_mtm = mark_equity_close

        signal = int(bar.get("entry_signal", bar.get("signal", 0)) or 0)
        in_window = bool(bar.get("in_window", True))
        if signal and in_window:
            net_exposure = sum(order.direction * order.lot_size for order in open_orders)
            basket_empty = not open_orders
            net_neutral = abs(net_exposure) <= 1e-12
            reversal = (signal > 0 and net_exposure < 0) or (
                signal < 0 and net_exposure > 0
            )
            if not reversal_only or basket_empty or net_neutral or reversal:
                pending_signal = signal

    if open_orders:
        last = rows[-1][1]
        now = bar_time(last)
        last_slippage = calc_dynamic_slippage(
            base_slippage=base_slip,
            atr=float(last.get("atr", 0.0)),
            close=float(last.get("close", 0.0)),
            k=float(c.get("slippage_k", 50)),
        )
        trade = _close_basket(
            open_orders,
            exit_time=now,
            base_exit_price=float(last["close"]),
            exit_reason="END_OF_DATA",
            cfg=cfg,
            spread_pts=spread_pts,
            slippage_pts=last_slippage,
            commission_per_lot=commission_per_lot,
        )
        trades.append(trade)
        equity += float(trade["pnl_usd"])
        equity_points.append((now, equity))

    equity_ts = pd.Series(
        [value for _, value in equity_points],
        index=pd.DatetimeIndex([ts for ts, _ in equity_points], name="BarTime"),
        dtype=float,
    )
    if equity_ts.empty:
        first_ts = bar_time(rows[0][1])
        equity_ts = pd.Series([init_eq], index=pd.DatetimeIndex([first_ts], name="BarTime"))
    return trades, equity_ts


__all__ = ["backtest_basket_reversal_symbol"]
