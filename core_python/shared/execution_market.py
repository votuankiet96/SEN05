"""Generic market-order execution engine for close-confirmed signals.

This module is intentionally separate from ``shared.execution``. The existing
engine models pending breakout orders, while MA Cross and similar strategies
need market execution at the next bar open after a signal is known.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .broker import round_lot_size
from .execution import calc_dynamic_slippage


_DEFAULT_STRATEGY = {
    "risk_per_trade": 0.005,
    "daily_loss_limit": 0.05,
    "max_drawdown_limit": 0.10,
    "max_drawdown_mode": "fixed_initial",
    "atr_stop_mult": 2.0,
    "atr_tp_mult": 2.0,
    "partial_tp_fraction": 0.5,
    "trailing_activation": 1.0,
    "trailing_column": "slow_ma",
    "reverse_on_opposite": True,
    "allow_same_bar_exit": True,
}

_DEFAULT_COSTS = {
    "slippage_pts": 2.0,
    "commission_per_lot": 3.5,
    "slippage_k": 50,
}


def _bar_time(row: pd.Series) -> pd.Timestamp:
    if "BarTime" in row:
        return pd.Timestamp(row["BarTime"])
    return pd.Timestamp(row.name)


def _max_drawdown_breached(
    equity: float,
    init_eq: float,
    peak_eq: float,
    max_dd: float,
    mode: str,
) -> bool:
    if max_dd >= 1:
        return False
    if mode in {"trailing_peak", "peak"}:
        return equity <= peak_eq * (1 - max_dd)
    return equity <= init_eq * (1 - max_dd)


def _resolve_entry_price(bar: pd.Series, direction: int, spread_pts: float, slippage: float) -> float:
    return float(bar["open"]) + direction * (spread_pts + slippage)


def _resolve_market_exit_price(bar: pd.Series, direction: int, slippage: float) -> float:
    return float(bar["open"]) - direction * slippage


def _position_size(
    equity: float,
    risk_pct: float,
    sl_dist: float,
    cfg: dict[str, Any],
) -> tuple[float, float]:
    contract_value = float(cfg.get("contract_value", 0.0))
    point_size = float(cfg.get("point_size", 1.0))
    risk_usd = equity * risk_pct
    if sl_dist <= 0 or contract_value <= 0 or point_size <= 0:
        return 0.0, risk_usd

    raw_lots = risk_usd / ((sl_dist / point_size) * contract_value)
    lots = round_lot_size(
        raw_lots,
        min_lot=float(cfg.get("min_lot_size", 0.01)),
        max_lot=float(cfg.get("max_lot_size", 100.0)),
        lot_step=float(cfg.get("lot_step", cfg.get("min_lot_size", 0.01))),
    )
    return lots, risk_usd


def _price_pnl(
    entry: float,
    exit_price: float,
    direction: int,
    lots: float,
    cfg: dict[str, Any],
) -> float:
    point_size = float(cfg.get("point_size", 1.0))
    contract_value = float(cfg.get("contract_value", 0.0))
    return ((exit_price - entry) * direction / point_size) * contract_value * lots


def _swap_cost(
    position: dict[str, Any],
    exit_time: pd.Timestamp,
    cfg: dict[str, Any],
    lot_fraction: float,
) -> float:
    holding_days = max((exit_time.date() - position["entry_time"].date()).days, 0)
    if position["direction"] == 1:
        swap_per_day = float(cfg.get("swap_long_per_lot_per_day", 0.0))
    else:
        swap_per_day = float(cfg.get("swap_short_per_lot_per_day", 0.0))
    return holding_days * swap_per_day * float(position["lot_size"]) * lot_fraction


def _close_position(
    position: dict[str, Any],
    exit_time: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
    cfg: dict[str, Any],
    commission_per_lot: float,
) -> dict[str, Any]:
    lots = float(position["lot_size"])
    remaining_fraction = (
        1.0 - float(position["partial_tp_fraction"])
        if position.get("partial_tp_hit")
        else 1.0
    )
    gross_h2 = _price_pnl(
        position["entry"],
        exit_price,
        position["direction"],
        lots * remaining_fraction,
        cfg,
    )
    commission_h2 = 2 * lots * commission_per_lot * remaining_fraction
    swap = _swap_cost(position, exit_time, cfg, remaining_fraction)
    gross = float(position.get("half1_pnl_gross", 0.0)) + gross_h2
    commission = float(position.get("half1_commission", 0.0)) + commission_h2
    pnl = gross - commission - swap
    sl_dist = max(float(position["sl_dist"]), 1e-12)
    r_multiple = ((exit_price - position["entry"]) * position["direction"]) / sl_dist

    return {
        "symbol": position["symbol"],
        "direction": position["direction"],
        "entry_time": position["entry_time"],
        "exit_time": exit_time,
        "entry": round(float(position["entry"]), 8),
        "exit": round(float(exit_price), 8),
        "sl": round(float(position["sl"]), 8),
        "tp": round(float(position["tp"]), 8) if np.isfinite(position["tp"]) else None,
        "lot_size": lots,
        "pnl_usd": round(float(pnl), 2),
        "pnl_net": round(float(pnl), 2),
        "gross_pnl": round(float(gross), 2),
        "commission": round(float(commission), 2),
        "swap_cost": round(float(swap), 2),
        "r_multiple": round(float(r_multiple), 4),
        "exit_reason": exit_reason,
        "partial_tp_hit": bool(position.get("partial_tp_hit", False)),
        "half1_exit": (
            round(float(position["half1_exit"]), 8)
            if position.get("half1_exit") is not None
            else None
        ),
        "half1_exit_time": position.get("half1_exit_time"),
    }


def backtest_market_symbol(
    symbol: str,
    df: pd.DataFrame,
    cfg: dict[str, Any],
    init_eq: float,
    *,
    strategy: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], pd.Series]:
    """Backtest a market-order strategy using next-bar-open execution.

    Contract
    --------
    ``df`` must be time-sorted and contain ``open``, ``high``, ``low``, ``close``,
    ``atr`` and ``signal``. A signal at bar T is only actionable at bar T+1 open.
    """
    if df.empty:
        return [], pd.Series(dtype=float)

    s = {**_DEFAULT_STRATEGY, **(strategy or {})}
    c = {**_DEFAULT_COSTS, **(costs or {})}

    frame = df.sort_index().copy()
    if "BarTime" not in frame.columns:
        frame["BarTime"] = frame.index

    risk_pct = float(s["risk_per_trade"])
    daily_limit = float(s.get("daily_loss_limit", s.get("ftmo_daily_limit", 1.0)))
    max_dd = float(s.get("max_drawdown_limit", s.get("ftmo_max_dd", 1.0)))
    max_dd_mode = str(s.get("max_drawdown_mode", "fixed_initial"))
    atr_stop_mult = float(s.get("atr_stop_mult", 2.0))
    atr_tp_mult = float(s.get("atr_tp_mult", 0.0))
    partial_frac = max(0.0, min(float(s.get("partial_tp_fraction", 0.0)), 1.0))
    trailing_activation = float(s.get("trailing_activation", 1.0))
    trailing_column = str(s.get("trailing_column", "slow_ma"))
    reverse_on_opposite = bool(s.get("reverse_on_opposite", True))
    allow_same_bar_exit = bool(s.get("allow_same_bar_exit", True))

    base_slip = float(cfg.get("slippage_pts", c["slippage_pts"]))
    spread_pts = float(cfg.get("spread_pts", 0.0))
    commission_per_lot = float(c.get("commission_per_lot", cfg.get("commission_per_lot", 0.0)))

    trades: list[dict[str, Any]] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    equity = float(init_eq)
    peak_eq = float(init_eq)
    daily_pnl = 0.0
    cur_day = None
    daily_stop = False
    position: dict[str, Any] | None = None
    pending_signal = 0

    rows = list(frame.iterrows())
    for _, bar in rows:
        now = _bar_time(bar)
        bdate = now.date()
        if bdate != cur_day:
            cur_day = bdate
            daily_pnl = 0.0
            daily_stop = False

        if _max_drawdown_breached(equity, init_eq, peak_eq, max_dd, max_dd_mode):
            break

        slippage = calc_dynamic_slippage(
            base_slippage=base_slip,
            atr=float(bar.get("atr", 0.0)),
            close=float(bar.get("close", 0.0)),
            k=float(c.get("slippage_k", 50)),
        )

        if pending_signal and not daily_stop:
            if position and pending_signal != position["direction"] and reverse_on_opposite:
                exit_price = _resolve_market_exit_price(bar, position["direction"], slippage)
                trade = _close_position(
                    position,
                    now,
                    exit_price,
                    "REVERSED",
                    cfg,
                    commission_per_lot,
                )
                trades.append(trade)
                equity += trade["pnl_usd"]
                daily_pnl += trade["pnl_usd"]
                peak_eq = max(peak_eq, equity)
                position = None
                if daily_pnl <= -(init_eq * daily_limit):
                    daily_stop = True

            if position is None and not daily_stop:
                direction = int(pending_signal)
                atr = float(bar.get("atr", 0.0))
                entry = _resolve_entry_price(bar, direction, spread_pts, slippage)
                sl_dist = max(atr_stop_mult * atr, 0.0)
                if sl_dist > 0:
                    sl = entry - direction * sl_dist
                    tp = entry + direction * atr_tp_mult * atr if atr_tp_mult > 0 else np.inf * direction
                    lots, risk_usd = _position_size(equity, risk_pct, sl_dist, cfg)
                    if lots > 0:
                        position = {
                            "symbol": symbol,
                            "direction": direction,
                            "entry_time": now,
                            "entry": entry,
                            "sl": sl,
                            "tp": tp,
                            "atr": atr,
                            "sl_dist": sl_dist,
                            "lot_size": lots,
                            "risk_usd": risk_usd,
                            "partial_tp_fraction": partial_frac if atr_tp_mult > 0 else 0.0,
                            "partial_tp_hit": False,
                            "half1_exit": None,
                            "half1_exit_time": None,
                            "half1_pnl_gross": 0.0,
                            "half1_commission": 0.0,
                            "trail_active": False,
                        }
            pending_signal = 0

        if position and (allow_same_bar_exit or position["entry_time"] != now):
            direction = position["direction"]
            sl = position["sl"]
            tp = position["tp"]
            hit_sl = (direction == 1 and float(bar["low"]) <= sl) or (
                direction == -1 and float(bar["high"]) >= sl
            )
            hit_tp = np.isfinite(tp) and (
                (direction == 1 and float(bar["high"]) >= tp)
                or (direction == -1 and float(bar["low"]) <= tp)
            )
            if hit_sl:
                exit_price = sl - direction * slippage
                trade = _close_position(
                    position,
                    now,
                    exit_price,
                    "SL",
                    cfg,
                    commission_per_lot,
                )
                trades.append(trade)
                equity += trade["pnl_usd"]
                daily_pnl += trade["pnl_usd"]
                peak_eq = max(peak_eq, equity)
                position = None
                if daily_pnl <= -(init_eq * daily_limit):
                    daily_stop = True
            elif hit_tp and position is not None:
                if position["partial_tp_fraction"] > 0 and not position["partial_tp_hit"]:
                    half_frac = float(position["partial_tp_fraction"])
                    gross_h1 = _price_pnl(
                        position["entry"],
                        tp,
                        direction,
                        float(position["lot_size"]) * half_frac,
                        cfg,
                    )
                    comm_h1 = 2 * float(position["lot_size"]) * commission_per_lot * half_frac
                    net_h1 = gross_h1 - comm_h1
                    equity += net_h1
                    daily_pnl += net_h1
                    peak_eq = max(peak_eq, equity)
                    position.update(
                        partial_tp_hit=True,
                        half1_exit=tp,
                        half1_exit_time=now,
                        half1_pnl_gross=gross_h1,
                        half1_commission=comm_h1,
                        sl=position["entry"],
                        tp=np.inf * direction,
                        trail_active=True,
                    )
                    if daily_pnl <= -(init_eq * daily_limit):
                        daily_stop = True
                else:
                    exit_price = tp - direction * slippage
                    trade = _close_position(
                        position,
                        now,
                        exit_price,
                        "TP",
                        cfg,
                        commission_per_lot,
                    )
                    trades.append(trade)
                    equity += trade["pnl_usd"]
                    daily_pnl += trade["pnl_usd"]
                    peak_eq = max(peak_eq, equity)
                    position = None
                    if daily_pnl <= -(init_eq * daily_limit):
                        daily_stop = True

        if position:
            direction = position["direction"]
            unrealized = (float(bar["close"]) - float(position["entry"])) * direction
            if not position["trail_active"] and unrealized >= float(position["atr"]) * trailing_activation:
                position["trail_active"] = True

            trail_value = bar.get(trailing_column)
            if position["trail_active"] and trail_value is not None and pd.notna(trail_value):
                trail_value = float(trail_value)
                if direction == 1:
                    new_sl = max(float(position["sl"]), trail_value)
                    if position["partial_tp_hit"]:
                        new_sl = max(new_sl, float(position["entry"]))
                else:
                    new_sl = min(float(position["sl"]), trail_value)
                    if position["partial_tp_hit"]:
                        new_sl = min(new_sl, float(position["entry"]))
                position["sl"] = new_sl

        equity_points.append((now, equity))

        signal = int(bar.get("signal", 0) or 0)
        in_window = bool(bar.get("in_window", True))
        if signal and in_window and not daily_stop:
            if position is None or (reverse_on_opposite and signal != position["direction"]):
                pending_signal = signal

    if position is not None:
        last = rows[-1][1]
        now = _bar_time(last)
        exit_price = float(last["close"])
        trade = _close_position(
            position,
            now,
            exit_price,
            "END_OF_DATA",
            cfg,
            commission_per_lot,
        )
        trades.append(trade)
        equity += trade["pnl_usd"]
        equity_points.append((now, equity))

    equity_ts = pd.Series(
        [value for _, value in equity_points],
        index=pd.DatetimeIndex([ts for ts, _ in equity_points], name="BarTime"),
        dtype=float,
    )
    if equity_ts.empty:
        first_ts = _bar_time(rows[0][1])
        equity_ts = pd.Series([init_eq], index=pd.DatetimeIndex([first_ts], name="BarTime"))
    return trades, equity_ts


__all__ = ["backtest_market_symbol"]
