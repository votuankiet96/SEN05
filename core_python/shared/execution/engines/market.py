"""Generic market-order execution engine for close-confirmed signals."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core_python.shared.execution.primitives import (
    adverse_entry_price,
    adverse_exit_price,
    bar_time,
    calc_dynamic_slippage,
    max_drawdown_breached,
    price_pnl,
    risk_sized_lots_from_distance,
    round_turn_commission,
    swap_cost,
)
from core_python.shared.replay_events import emit_replay_event


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


def _position_size(
    equity: float,
    risk_pct: float,
    sl_dist: float,
    cfg: dict[str, Any],
    commission_per_lot: float = 0.0,
) -> tuple[float, float]:
    risk_usd = float(equity) * float(risk_pct)
    lots = risk_sized_lots_from_distance(
        equity,
        risk_pct,
        sl_dist,
        cfg,
        commission_per_lot,
    )
    return lots, risk_usd


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
    gross_h2 = price_pnl(
        position["entry"],
        exit_price,
        position["direction"],
        lots * remaining_fraction,
        cfg,
    )
    commission_h2 = round_turn_commission(lots, commission_per_lot, remaining_fraction)
    holding_days = max((exit_time.date() - position["entry_time"].date()).days, 0)
    swap = swap_cost(
        holding_days,
        lots,
        position["direction"],
        float(cfg.get("swap_long_per_lot_per_day", 0.0)),
        float(cfg.get("swap_short_per_lot_per_day", 0.0)),
    ) * remaining_fraction
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


def _floating_pnl_at_mark(
    position: dict[str, Any] | None,
    *,
    mark_time: pd.Timestamp,
    mark_price: float,
    cfg: dict[str, Any],
    spread_pts: float,
    slippage_pts: float,
    commission_per_lot: float,
) -> float:
    if position is None:
        return 0.0

    lots = float(position["lot_size"])
    remaining_fraction = (
        1.0 - float(position["partial_tp_fraction"])
        if position.get("partial_tp_hit")
        else 1.0
    )
    exit_price = adverse_exit_price(
        mark_price,
        int(position["direction"]),
        spread_pts,
        slippage_pts,
    )
    gross = price_pnl(
        float(position["entry"]),
        exit_price,
        int(position["direction"]),
        lots * remaining_fraction,
        cfg,
    )
    commission = round_turn_commission(lots, commission_per_lot, remaining_fraction)
    holding_days = max((mark_time.date() - position["entry_time"].date()).days, 0)
    swap = swap_cost(
        holding_days,
        lots,
        int(position["direction"]),
        float(cfg.get("swap_long_per_lot_per_day", 0.0)),
        float(cfg.get("swap_short_per_lot_per_day", 0.0)),
    ) * remaining_fraction
    return float(gross - commission - swap)


def backtest_market_symbol(
    symbol: str,
    df: pd.DataFrame,
    cfg: dict[str, Any],
    init_eq: float,
    *,
    strategy: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    event_sink: list | None = None,
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
    day_start_equity = float(init_eq)
    last_mark_equity = float(init_eq)
    daily_pnl = 0.0
    cur_day = None
    daily_stop = False
    position: dict[str, Any] | None = None
    pending_signal = 0

    rows = list(frame.iterrows())
    for _, bar in rows:
        now = bar_time(bar)
        bdate = now.date()
        if bdate != cur_day:
            cur_day = bdate
            day_start_equity = last_mark_equity
            daily_pnl = 0.0
            daily_stop = False

        if max_drawdown_breached(last_mark_equity, init_eq, peak_eq, max_dd, max_dd_mode):
            emit_replay_event(
                event_sink,
                time=now,
                symbol=symbol,
                event_type="MAX_DRAWDOWN_STOP",
                equity=last_mark_equity,
                reason="max_drawdown_limit",
                metadata={"peak_equity": peak_eq, "max_drawdown_limit": max_dd},
            )
            break

        slippage = calc_dynamic_slippage(
            base_slippage=base_slip,
            atr=float(bar.get("atr", 0.0)),
            close=float(bar.get("close", 0.0)),
            k=float(c.get("slippage_k", 50)),
        )

        if pending_signal and not daily_stop:
            if position and pending_signal != position["direction"] and reverse_on_opposite:
                exit_price = adverse_exit_price(
                    float(bar["open"]),
                    position["direction"],
                    spread_pts,
                    slippage,
                )
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
                emit_replay_event(
                    event_sink,
                    time=now,
                    symbol=symbol,
                    event_type="REVERSAL_CLOSE",
                    direction=position["direction"],
                    price=exit_price,
                    entry=position["entry"],
                    sl=position["sl"],
                    tp=position["tp"],
                    lot_size=position["lot_size"],
                    equity=equity,
                    pnl_usd=trade["pnl_usd"],
                    reason="REVERSED",
                    metadata={"commission": trade.get("commission"), "swap_cost": trade.get("swap_cost")},
                )
                position = None
                if daily_pnl <= -(init_eq * daily_limit):
                    daily_stop = True
                    emit_replay_event(
                        event_sink,
                        time=now,
                        symbol=symbol,
                        event_type="DAILY_LOSS_STOP",
                        equity=equity,
                        pnl_usd=daily_pnl,
                        reason="daily_loss_limit",
                    )

            if position is None and not daily_stop:
                direction = int(pending_signal)
                atr = float(bar.get("atr", 0.0))
                entry = adverse_entry_price(float(bar["open"]), direction, spread_pts, slippage)
                sl_dist = max(atr_stop_mult * atr, 0.0)
                if sl_dist > 0:
                    sl = entry - direction * sl_dist
                    tp = entry + direction * atr_tp_mult * atr if atr_tp_mult > 0 else np.inf * direction
                    lots, risk_usd = _position_size(
                        equity,
                        risk_pct,
                        sl_dist,
                        cfg,
                        commission_per_lot,
                    )
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
                        emit_replay_event(
                            event_sink,
                            time=now,
                            symbol=symbol,
                            event_type="POSITION_OPENED",
                            direction=direction,
                            price=entry,
                            entry=entry,
                            sl=sl,
                            tp=tp if np.isfinite(tp) else None,
                            lot_size=lots,
                            equity=equity,
                            reason="market_next_open",
                            metadata={"risk_usd": risk_usd, "sl_dist": sl_dist, "atr": atr},
                        )
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
                exit_price = adverse_exit_price(sl, direction, spread_pts, slippage)
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
                emit_replay_event(
                    event_sink,
                    time=now,
                    symbol=symbol,
                    event_type="STOP_LOSS_HIT",
                    direction=direction,
                    price=exit_price,
                    entry=position["entry"],
                    sl=sl,
                    tp=position["tp"],
                    lot_size=position["lot_size"],
                    equity=equity,
                    pnl_usd=trade["pnl_usd"],
                    reason="SL",
                    metadata={"commission": trade.get("commission"), "swap_cost": trade.get("swap_cost")},
                )
                position = None
                if daily_pnl <= -(init_eq * daily_limit):
                    daily_stop = True
                    emit_replay_event(
                        event_sink,
                        time=now,
                        symbol=symbol,
                        event_type="DAILY_LOSS_STOP",
                        equity=equity,
                        pnl_usd=daily_pnl,
                        reason="daily_loss_limit",
                    )
            elif hit_tp and position is not None:
                if position["partial_tp_fraction"] > 0 and not position["partial_tp_hit"]:
                    half_frac = float(position["partial_tp_fraction"])
                    half_exit = adverse_exit_price(tp, direction, spread_pts, slippage)
                    gross_h1 = price_pnl(
                        position["entry"],
                        half_exit,
                        direction,
                        float(position["lot_size"]) * half_frac,
                        cfg,
                    )
                    comm_h1 = round_turn_commission(position["lot_size"], commission_per_lot, half_frac)
                    net_h1 = gross_h1 - comm_h1
                    equity += net_h1
                    daily_pnl += net_h1
                    peak_eq = max(peak_eq, equity)
                    position.update(
                        partial_tp_hit=True,
                        half1_exit=half_exit,
                        half1_exit_time=now,
                        half1_pnl_gross=gross_h1,
                        half1_commission=comm_h1,
                        sl=position["entry"],
                        tp=np.inf * direction,
                        trail_active=True,
                    )
                    emit_replay_event(
                        event_sink,
                        time=now,
                        symbol=symbol,
                        event_type="PARTIAL_TP_HIT",
                        direction=direction,
                        price=half_exit,
                        entry=position["entry"],
                        sl=position["sl"],
                        tp=half_exit,
                        lot_size=position["lot_size"],
                        equity=equity,
                        pnl_usd=net_h1,
                        reason="partial_tp",
                        metadata={"commission": comm_h1},
                    )
                    emit_replay_event(
                        event_sink,
                        time=now,
                        symbol=symbol,
                        event_type="TRAILING_ACTIVATED",
                        direction=direction,
                        price=float(bar["close"]),
                        entry=position["entry"],
                        sl=position["sl"],
                        tp=position["tp"],
                        lot_size=position["lot_size"],
                        equity=equity,
                        reason="partial_tp_breakeven",
                    )
                    if daily_pnl <= -(init_eq * daily_limit):
                        daily_stop = True
                        emit_replay_event(
                            event_sink,
                            time=now,
                            symbol=symbol,
                            event_type="DAILY_LOSS_STOP",
                            equity=equity,
                            pnl_usd=daily_pnl,
                            reason="daily_loss_limit",
                        )
                else:
                    exit_price = adverse_exit_price(tp, direction, spread_pts, slippage)
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
                    emit_replay_event(
                        event_sink,
                        time=now,
                        symbol=symbol,
                        event_type="TAKE_PROFIT_HIT",
                        direction=direction,
                        price=exit_price,
                        entry=position["entry"],
                        sl=position["sl"],
                        tp=tp,
                        lot_size=position["lot_size"],
                        equity=equity,
                        pnl_usd=trade["pnl_usd"],
                        reason="TP",
                        metadata={"commission": trade.get("commission"), "swap_cost": trade.get("swap_cost")},
                    )
                    position = None
                    if daily_pnl <= -(init_eq * daily_limit):
                        daily_stop = True
                        emit_replay_event(
                            event_sink,
                            time=now,
                            symbol=symbol,
                            event_type="DAILY_LOSS_STOP",
                            equity=equity,
                            pnl_usd=daily_pnl,
                            reason="daily_loss_limit",
                        )

        if position:
            direction = position["direction"]
            unrealized = (float(bar["close"]) - float(position["entry"])) * direction
            if not position["trail_active"] and unrealized >= float(position["atr"]) * trailing_activation:
                position["trail_active"] = True
                emit_replay_event(
                    event_sink,
                    time=now,
                    symbol=symbol,
                    event_type="TRAILING_ACTIVATED",
                    direction=direction,
                    price=float(bar["close"]),
                    entry=position["entry"],
                    sl=position["sl"],
                    tp=position["tp"],
                    lot_size=position["lot_size"],
                    equity=equity,
                    reason="activation_threshold",
                )

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
                old_sl = position["sl"]
                position["sl"] = new_sl
                if float(new_sl) != float(old_sl):
                    emit_replay_event(
                        event_sink,
                        time=now,
                        symbol=symbol,
                        event_type="TRAILING_SL_MOVED",
                        direction=direction,
                        price=float(bar["close"]),
                        entry=position["entry"],
                        sl=position["sl"],
                        tp=position["tp"],
                        lot_size=position["lot_size"],
                        equity=equity,
                        metadata={"old_sl": old_sl, "new_sl": new_sl},
                    )

        floating_pnl = _floating_pnl_at_mark(
            position,
            mark_time=now,
            mark_price=float(bar["close"]),
            cfg=cfg,
            spread_pts=spread_pts,
            slippage_pts=slippage,
            commission_per_lot=commission_per_lot,
        )
        mark_equity = float(equity) + floating_pnl
        last_mark_equity = mark_equity
        peak_eq = max(peak_eq, mark_equity)
        if not daily_stop and (day_start_equity - mark_equity) >= init_eq * daily_limit:
            daily_stop = True
            emit_replay_event(
                event_sink,
                time=now,
                symbol=symbol,
                event_type="DAILY_LOSS_STOP",
                equity=mark_equity,
                pnl_usd=mark_equity - day_start_equity,
                reason="daily_loss_limit_mtm",
            )

        equity_points.append((now, mark_equity))

        signal = int(bar.get("signal", 0) or 0)
        in_window = bool(bar.get("in_window", True))
        if signal and in_window and not daily_stop:
            if position is None or (reverse_on_opposite and signal != position["direction"]):
                pending_signal = signal
                emit_replay_event(
                    event_sink,
                    time=now,
                    symbol=symbol,
                    event_type="SIGNAL_DETECTED",
                    direction=signal,
                    price=float(bar["close"]),
                    equity=equity,
                    reason="entry_signal" if position is None else "reversal_signal",
                )

    if position is not None:
        last = rows[-1][1]
        now = bar_time(last)
        last_slippage = calc_dynamic_slippage(
            base_slippage=base_slip,
            atr=float(last.get("atr", 0.0)),
            close=float(last.get("close", 0.0)),
            k=float(c.get("slippage_k", 50)),
        )
        exit_price = adverse_exit_price(
            float(last["close"]),
            position["direction"],
            spread_pts,
            last_slippage,
        )
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
        last_mark_equity = equity
        emit_replay_event(
            event_sink,
            time=now,
            symbol=symbol,
            event_type="FORCE_CLOSE_END_OF_DATA",
            direction=position["direction"],
            price=exit_price,
            entry=position["entry"],
            sl=position["sl"],
            tp=position["tp"],
            lot_size=position["lot_size"],
            equity=equity,
            pnl_usd=trade["pnl_usd"],
            reason="END_OF_DATA",
            metadata={"commission": trade.get("commission"), "swap_cost": trade.get("swap_cost")},
        )
        if equity_points and equity_points[-1][0] == now:
            equity_points[-1] = (now, equity)
        else:
            equity_points.append((now, equity))

    equity_ts = pd.Series(
        [value for _, value in equity_points],
        index=pd.DatetimeIndex([ts for ts, _ in equity_points], name="BarTime"),
        dtype=float,
    )
    if equity_ts.empty:
        first_ts = bar_time(rows[0][1])
        equity_ts = pd.Series([init_eq], index=pd.DatetimeIndex([first_ts], name="BarTime"))
    equity_ts.attrs["equity_model"] = "mark_to_market"
    equity_ts.attrs["realized_equity_final"] = float(equity)
    return trades, equity_ts


__all__ = ["backtest_market_symbol"]
