"""Order lifecycle models shared by execution engines."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from math import isinf
from typing import Any

import pandas as pd

from backtest_optimize.contracts import (
    ClusterResult,
    ClusterStatus,
    AmbiguityPolicy,
    Direction,
    ExitReason,
    LegSizing,
    MarketSpec,
    OrderStatus,
    PositionLeg,
    SLResult,
    SignalRow,
    TPLevel,
)
from backtest_optimize.execution.cost_model import (
    cost_r,
    price_exit_r,
    round_turn_cost_currency,
)
from backtest_optimize.execution.entry import EntryResult, calculate_entry
from backtest_optimize.execution.exits import apply_sma_trailing_to_leg
from backtest_optimize.execution.fill_model import resolve_exit, stop_entry_touched
from backtest_optimize.execution.sizing import calculate_leg_sizing
from backtest_optimize.execution.sl_calculator import calculate_sl
from backtest_optimize.execution.tp_calculator import calculate_tp_levels
from backtest_optimize.io.market_data import ensure_normalized_ohlcv


@dataclass
class PendingOrderState:
    """Pending stop/limit order state in an OHLC simulation."""

    signal: SignalRow
    direction: Direction
    entry_price: float
    sl_price: float
    tp_levels: tuple[TPLevel, ...]
    created_bar_index: int
    created_time: pd.Timestamp
    expires_after_bars: int
    slot_index: int
    status: OrderStatus = OrderStatus.PENDING
    cancelled_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def age_bars(self, bar_index: int) -> int:
        return int(bar_index) - int(self.created_bar_index)


def pending_result(
    order: PendingOrderState,
    *,
    status: ClusterStatus,
    exit_time: pd.Timestamp | None,
    reason: ExitReason,
    skip_reason: str | None = None,
) -> ClusterResult:
    """Build a cluster result for a pending order that never became a position."""
    return ClusterResult(
        signal=order.signal,
        status=status,
        entry_time=None,
        entry_price=float(order.entry_price),
        sl_price=float(order.sl_price),
        tp_levels=order.tp_levels,
        exit_time=exit_time,
        skip_reason=skip_reason,
        metadata={
            **order.metadata,
            "order_status": status.value,
            "order_exit_reason": reason.value,
            "created_time": order.created_time,
            "expires_after_bars": order.expires_after_bars,
            "cancelled_reason": order.cancelled_reason,
        },
    )


@dataclass
class ActiveClusterState:
    """Filled cluster state managed bar by bar."""

    signal: SignalRow
    slot_index: int
    entry_time: pd.Timestamp
    entry_price: float
    sl_result: SLResult
    tp_levels: tuple[TPLevel, ...]
    legs: list[PositionLeg]
    fill_bar_index: int
    metadata: dict[str, Any] = field(default_factory=dict)
    ambiguous_bars: int = 0
    path_end_index: int | None = None
    skipped_reason: str | None = None


def simulate_stop_order_components(
    *,
    signal_rows: Sequence[SignalRow],
    bars: pd.DataFrame,
    market_spec: MarketSpec,
    account_size: float,
    risk_per_cluster: float,
    entry_model: str,
    entry_params: dict[str, Any],
    sl_method: str,
    sl_params: dict[str, Any],
    tp_method: str,
    tp_params: dict[str, Any],
    leg_weights: Sequence[float] | None,
    order_model: str,
    order_params: dict[str, Any],
    exit_model: str,
    exit_params: dict[str, Any],
    ambiguity_policy: AmbiguityPolicy,
    raise_on_error: bool = False,
) -> tuple[ClusterResult, ...]:
    """Run a bar-driven stop-order OHLC lifecycle.

    The function is strategy-neutral. Strategy names should choose signals or
    presets, while this function only sees reusable execution components.
    """
    del order_model
    normalized_bars = ensure_normalized_ohlcv(bars).copy()
    cancel_after_bars = int(order_params.get("cancel_after_bars", order_params.get("cancel_pending_after_bars", 3)))
    if cancel_after_bars < 1:
        raise ValueError("order_params.cancel_after_bars must be >= 1.")

    sma_period = int(exit_params.get("sma_period", 20))
    normalized_bars["sma20"] = (
        normalized_bars["close"].astype(float).rolling(sma_period, min_periods=sma_period).mean()
    )

    result_slots: list[ClusterResult | None] = []
    pending_orders: list[PendingOrderState] = []
    active_clusters: list[ActiveClusterState] = []
    signal_idx = 0
    max_signal_bar_clock_hours = float(order_params.get("max_signal_bar_clock_hours", 0.0))

    def add_result(result: ClusterResult, slot: int | None = None) -> None:
        if slot is None:
            result_slots.append(result)
        else:
            result_slots[slot] = result

    def next_open_for_bar(idx: int) -> tuple[pd.Timestamp, float]:
        if idx + 1 < len(normalized_bars):
            next_bar = normalized_bars.iloc[idx + 1]
            return pd.Timestamp(next_bar["bartime"]), float(next_bar["open"])
        row = normalized_bars.iloc[idx]
        return pd.Timestamp(row["bartime"]), float(row["close"])

    for bar_index, bar in normalized_bars.iterrows():
        bar_time = pd.Timestamp(bar["bartime"])

        for active in list(active_clusters):
            skipped = _apply_active_exits(
                active,
                bar=bar,
                bar_index=int(bar_index),
                market_spec=market_spec,
                ambiguity_policy=ambiguity_policy,
            )
            if skipped:
                _close_active_at_price(
                    active,
                    exit_time=bar_time,
                    exit_price=float(bar["close"]),
                    exit_reason=ExitReason.SKIPPED,
                    market_spec=market_spec,
                    path_end_index=int(bar_index),
                )
                add_result(
                    _cluster_to_result(
                        active,
                        normalized_bars=normalized_bars,
                        status=ClusterStatus.SKIPPED,
                        skip_reason=active.skipped_reason,
                    ),
                    active.slot_index,
                )
                active_clusters.remove(active)
                continue
            if all(leg.exit_time is not None for leg in active.legs):
                add_result(_cluster_to_result(active, normalized_bars=normalized_bars), active.slot_index)
                active_clusters.remove(active)
                continue
            if exit_model in {"two_legs_sma20", "sma20_trailing_leg2"}:
                _apply_sma20_trailing(active, bar)

        for order in list(pending_orders):
            if order.created_bar_index >= int(bar_index):
                continue
            if not stop_entry_touched(order.direction, bar, order.entry_price):
                continue

            sizing = calculate_leg_sizing(
                tp_levels=order.tp_levels,
                entry_price=order.entry_price,
                sl_price=order.sl_price,
                market_spec=market_spec,
                account_size=account_size,
                risk_per_cluster=risk_per_cluster,
                leg_weights=leg_weights,
            )
            active = _build_active_cluster(
                order=order,
                entry_time=bar_time,
                fill_bar_index=int(bar_index),
                sizing=sizing,
            )
            pending_orders.remove(order)
            if not active.legs:
                add_result(
                    ClusterResult(
                        signal=order.signal,
                        status=ClusterStatus.SKIPPED,
                        entry_time=bar_time,
                        entry_price=order.entry_price,
                        sl_price=order.sl_price,
                        tp_levels=order.tp_levels,
                        skip_reason="all_legs_below_min_lot",
                        metadata={**order.metadata, "sizing": [item.__dict__ for item in sizing]},
                    ),
                    order.slot_index,
                )
                continue

            skipped = _apply_active_exits(
                active,
                bar=bar,
                bar_index=int(bar_index),
                market_spec=market_spec,
                ambiguity_policy=ambiguity_policy,
            )
            if skipped:
                _close_active_at_price(
                    active,
                    exit_time=bar_time,
                    exit_price=float(bar["close"]),
                    exit_reason=ExitReason.SKIPPED,
                    market_spec=market_spec,
                    path_end_index=int(bar_index),
                )
                add_result(
                    _cluster_to_result(
                        active,
                        normalized_bars=normalized_bars,
                        status=ClusterStatus.SKIPPED,
                        skip_reason=active.skipped_reason,
                    ),
                    active.slot_index,
                )
            elif all(leg.exit_time is not None for leg in active.legs):
                add_result(_cluster_to_result(active, normalized_bars=normalized_bars), active.slot_index)
            else:
                if exit_model in {"two_legs_sma20", "sma20_trailing_leg2"}:
                    _apply_sma20_trailing(active, bar)
                active_clusters.append(active)

        for order in list(pending_orders):
            if order.age_bars(int(bar_index)) < cancel_after_bars:
                continue
            order.status = OrderStatus.EXPIRED
            pending_orders.remove(order)
            add_result(
                pending_result(
                    order,
                    status=ClusterStatus.PENDING_EXPIRED,
                    exit_time=bar_time,
                    reason=ExitReason.PENDING_EXPIRED,
                    skip_reason="pending_expired",
                ),
                order.slot_index,
            )

        if bar_index + 1 >= len(normalized_bars):
            continue

        next_time = pd.Timestamp(normalized_bars.iloc[int(bar_index) + 1]["bartime"])
        signals_in_bar: list[SignalRow] = []
        while signal_idx < len(signal_rows) and signal_rows[signal_idx].bartime < next_time:
            if signal_rows[signal_idx].bartime >= bar_time:
                signals_in_bar.append(signal_rows[signal_idx])
            signal_idx += 1
        if not signals_in_bar:
            continue

        signal = max(signals_in_bar, key=lambda item: item.bartime)
        slot = len(result_slots)
        result_slots.append(None)
        signal_bar_clock_hours = (next_time - bar_time).total_seconds() / 3600.0
        if max_signal_bar_clock_hours > 0 and signal_bar_clock_hours > max_signal_bar_clock_hours:
            add_result(
                ClusterResult(
                    signal=signal,
                    status=ClusterStatus.SKIPPED,
                    skip_reason="signal_bar_too_long",
                    metadata={"signals_in_bar": len(signals_in_bar), "bar_clock_hours": signal_bar_clock_hours},
                ),
                slot,
            )
            continue

        for order in list(pending_orders):
            if order.direction == signal.direction:
                continue
            order.status = OrderStatus.CANCELLED
            order.cancelled_reason = "opposite_signal"
            pending_orders.remove(order)
            add_result(
                pending_result(
                    order,
                    status=ClusterStatus.PENDING_CANCELLED,
                    exit_time=next_time,
                    reason=ExitReason.PENDING_CANCELLED,
                    skip_reason="opposite_signal_cancelled_pending",
                ),
                order.slot_index,
            )

        for active in list(active_clusters):
            if active.signal.direction == signal.direction:
                continue
            exit_time, exit_price = next_open_for_bar(int(bar_index))
            _close_active_at_price(
                active,
                exit_time=exit_time,
                exit_price=exit_price,
                exit_reason=ExitReason.REVERSE_SIGNAL,
                market_spec=market_spec,
                path_end_index=int(bar_index),
            )
            add_result(_cluster_to_result(active, normalized_bars=normalized_bars), active.slot_index)
            active_clusters.remove(active)

        same_direction_exposure = any(order.direction == signal.direction for order in pending_orders) or any(
            active.signal.direction == signal.direction for active in active_clusters
        )
        if same_direction_exposure:
            add_result(
                ClusterResult(
                    signal=signal,
                    status=ClusterStatus.SKIPPED,
                    skip_reason="same_direction_open",
                    metadata={"signals_in_bar": len(signals_in_bar)},
                ),
                slot,
            )
            continue

        try:
            calc_signal = replace(signal, bartime=bar_time)
            entry_result: EntryResult = calculate_entry(entry_model, calc_signal, normalized_bars, entry_params)
            sl_result = calculate_sl(sl_method, calc_signal, entry_result.price, normalized_bars, sl_params)
            tp_levels = calculate_tp_levels(
                tp_method,
                calc_signal,
                entry_result.price,
                sl_result.price,
                normalized_bars,
                tp_params,
            )
        except Exception:
            if raise_on_error:
                raise
            add_result(
                ClusterResult(
                    signal=signal,
                    status=ClusterStatus.SKIPPED,
                    skip_reason="execution_error",
                ),
                slot,
            )
            continue

        pending_orders.append(
            PendingOrderState(
                signal=signal,
                direction=signal.direction,
                entry_price=float(entry_result.price),
                sl_price=float(sl_result.price),
                tp_levels=tuple(tp_levels),
                created_bar_index=int(bar_index),
                created_time=next_time,
                expires_after_bars=cancel_after_bars,
                slot_index=slot,
                metadata={
                    "signals_in_bar": len(signals_in_bar),
                    "entry_model": entry_result.model,
                    "entry_params": entry_result.params,
                    "entry_source": entry_result.source,
                    "sl_method": sl_result.method,
                    "sl_params": sl_result.params,
                    "tp_method": tp_method,
                    "tp_params": tp_params,
                    "order_model": "stop_order",
                    "exit_model": exit_model,
                },
            )
        )

    if len(normalized_bars):
        last_bar = normalized_bars.iloc[-1]
        last_time = pd.Timestamp(last_bar["bartime"])
        for order in list(pending_orders):
            add_result(
                pending_result(
                    order,
                    status=ClusterStatus.PENDING_UNFILLED_AT_END,
                    exit_time=last_time,
                    reason=ExitReason.END_OF_DATA,
                    skip_reason="pending_unfilled_at_end",
                ),
                order.slot_index,
            )
        for active in list(active_clusters):
            _close_active_at_price(
                active,
                exit_time=last_time,
                exit_price=float(last_bar["close"]),
                exit_reason=ExitReason.END_OF_DATA,
                market_spec=market_spec,
                path_end_index=len(normalized_bars) - 1,
            )
            add_result(_cluster_to_result(active, normalized_bars=normalized_bars), active.slot_index)

    return tuple(item for item in result_slots if item is not None)


def _closed_cluster_status(legs: Sequence[PositionLeg]) -> ClusterStatus:
    if any(leg.exit_reason == ExitReason.END_OF_DATA for leg in legs):
        return ClusterStatus.OPEN_AT_END
    if any(leg.exit_reason == ExitReason.AMBIGUOUS for leg in legs):
        return ClusterStatus.AMBIGUOUS
    if any(leg.exit_reason == ExitReason.REVERSE_SIGNAL for leg in legs):
        return ClusterStatus.REVERSED
    return ClusterStatus.CLOSED


def _close_leg(
    leg: PositionLeg,
    *,
    exit_time: pd.Timestamp,
    exit_price: float,
    exit_reason: ExitReason,
    market_spec: MarketSpec,
    ambiguous: bool = False,
) -> None:
    leg.exit_time = exit_time
    leg.exit_price = float(exit_price)
    leg.exit_reason = exit_reason
    leg.ambiguous = ambiguous
    gross_r = price_exit_r(
        direction=leg.direction,
        entry_price=leg.entry_price,
        exit_price=float(exit_price),
        sl_price=leg.sl_price,
    )
    cost_currency = round_turn_cost_currency(leg.sizing.lot, market_spec)
    leg.cost_r = cost_r(cost_currency, leg.sizing.risk_amount)
    leg.r_result = gross_r - leg.cost_r


def _path_stats(
    direction: Direction,
    entry_price: float,
    sl_price: float,
    path: pd.DataFrame,
) -> tuple[float | None, float | None]:
    if path.empty:
        return None, None
    risk_distance = abs(float(entry_price) - float(sl_price))
    if risk_distance <= 0:
        return None, None
    if direction == Direction.BUY:
        adverse = max(0.0, float(entry_price) - float(path["low"].min()))
        favorable = max(0.0, float(path["high"].max()) - float(entry_price))
    else:
        adverse = max(0.0, float(path["high"].max()) - float(entry_price))
        favorable = max(0.0, float(entry_price) - float(path["low"].min()))
    return adverse / risk_distance, favorable / risk_distance


def _cluster_to_result(
    active: ActiveClusterState,
    *,
    normalized_bars: pd.DataFrame,
    status: ClusterStatus | None = None,
    skip_reason: str | None = None,
) -> ClusterResult:
    path_end = active.path_end_index if active.path_end_index is not None else active.fill_bar_index
    path = normalized_bars.iloc[active.fill_bar_index : path_end + 1]
    bar_mae_r, bar_mfe_r = _path_stats(
        active.signal.direction,
        active.entry_price,
        active.sl_result.price,
        path,
    )
    gross_r = sum(leg.sizing.risk_fraction * (leg.r_result + leg.cost_r) for leg in active.legs)
    total_cost_r = sum(leg.sizing.risk_fraction * leg.cost_r for leg in active.legs)
    total_r = gross_r - total_cost_r
    exit_times = [leg.exit_time for leg in active.legs if leg.exit_time is not None]
    exit_time = max(exit_times) if exit_times else active.entry_time
    return ClusterResult(
        signal=active.signal,
        status=status or _closed_cluster_status(active.legs),
        entry_time=active.entry_time,
        entry_price=float(active.entry_price),
        sl_price=float(active.sl_result.price),
        tp_levels=active.tp_levels,
        legs=tuple(replace(leg) for leg in active.legs),
        exit_time=exit_time,
        r_result=total_r,
        gross_r=gross_r,
        cost_r=total_cost_r,
        bar_mae_r=bar_mae_r,
        bar_mfe_r=bar_mfe_r,
        holding_bars=len(path),
        ambiguous_bars=active.ambiguous_bars,
        skip_reason=skip_reason,
        metadata=dict(active.metadata),
    )


def _tp_price_for_leg(leg: PositionLeg) -> float | None:
    price = float(leg.tp_level.price)
    if isinf(price) or str(leg.tp_level.label or "").upper().startswith("NO_TP"):
        return None
    return price


def _apply_active_exits(
    active: ActiveClusterState,
    *,
    bar: pd.Series,
    bar_index: int,
    market_spec: MarketSpec,
    ambiguity_policy: AmbiguityPolicy,
) -> bool:
    for leg in active.legs:
        if leg.exit_time is not None:
            continue
        decision = resolve_exit(
            direction=leg.direction,
            bar=bar,
            sl_price=float(leg.current_sl if leg.current_sl is not None else leg.sl_price),
            tp_price=_tp_price_for_leg(leg),
            ambiguity_policy=ambiguity_policy,
        )
        if not decision.hit:
            continue
        active.path_end_index = int(bar_index)
        if decision.ambiguous:
            active.ambiguous_bars += 1
        if decision.reason == ExitReason.SKIPPED:
            active.skipped_reason = "ambiguous_bar"
            return True
        _close_leg(
            leg,
            exit_time=pd.Timestamp(bar["bartime"]),
            exit_price=float(decision.price),
            exit_reason=decision.reason or ExitReason.SL,
            market_spec=market_spec,
            ambiguous=decision.ambiguous,
        )
    return False


def _apply_sma20_trailing(active: ActiveClusterState, bar: pd.Series) -> None:
    sma_value = bar.get("sma20")
    if sma_value is None or pd.isna(sma_value):
        return
    for leg in active.legs:
        if leg.exit_time is not None or leg.leg_id != 2:
            continue
        apply_sma_trailing_to_leg(leg, float(sma_value))


def _build_active_cluster(
    *,
    order: PendingOrderState,
    entry_time: pd.Timestamp,
    fill_bar_index: int,
    sizing: Sequence[LegSizing],
) -> ActiveClusterState:
    levels_by_id = {tp.level: tp for tp in order.tp_levels}
    legs = [
        PositionLeg(
            leg_id=size.leg_id,
            direction=order.direction,
            entry_time=entry_time,
            entry_price=float(order.entry_price),
            sl_price=float(order.sl_price),
            current_sl=float(order.sl_price),
            tp_level=levels_by_id[size.leg_id],
            sizing=size,
        )
        for size in sizing
        if not size.skipped and size.lot > 0 and size.leg_id in levels_by_id
    ]
    return ActiveClusterState(
        signal=order.signal,
        slot_index=order.slot_index,
        entry_time=entry_time,
        entry_price=float(order.entry_price),
        sl_result=SLResult(price=float(order.sl_price), method="order_sl", source="order"),
        tp_levels=order.tp_levels,
        legs=legs,
        fill_bar_index=fill_bar_index,
        metadata={**order.metadata, "order_status": "filled", "filled_time": entry_time},
    )


def _close_active_at_price(
    active: ActiveClusterState,
    *,
    exit_time: pd.Timestamp,
    exit_price: float,
    exit_reason: ExitReason,
    market_spec: MarketSpec,
    path_end_index: int,
) -> None:
    active.path_end_index = path_end_index
    for leg in active.legs:
        if leg.exit_time is not None:
            continue
        _close_leg(
            leg,
            exit_time=exit_time,
            exit_price=float(exit_price),
            exit_reason=exit_reason,
            market_spec=market_spec,
        )
