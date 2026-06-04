"""Cluster state and bar-level position management."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

import pandas as pd

from backtest_optimize.contracts import (
    AmbiguityPolicy,
    ClusterResult,
    ClusterStatus,
    Direction,
    ExitReason,
    LegSizing,
    MarketSpec,
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
from backtest_optimize.io.market_data import normalize_ohlcv_frame


class PositionManager:
    """Track cluster state by `(symbol, timeframe)`."""

    def __init__(self) -> None:
        self._open_until: dict[tuple[str, str], tuple[pd.Timestamp, Direction]] = {}

    def is_cluster_open(self, symbol: str, timeframe: str, at_time: pd.Timestamp) -> bool:
        """Return True when a cluster is still open after `at_time`.

        If a cluster closes exactly at `at_time`, it is considered closed before
        the new signal is evaluated on that same bar.
        """
        key = (symbol.upper(), timeframe.upper())
        state = self._open_until.get(key)
        if state is None:
            return False
        exit_time, _ = state
        return exit_time is not None and exit_time > at_time

    def open_direction(self, symbol: str, timeframe: str, at_time: pd.Timestamp) -> Direction | None:
        """Return the active direction at `at_time`, if a cluster is open."""
        key = (symbol.upper(), timeframe.upper())
        state = self._open_until.get(key)
        if state is None:
            return None
        exit_time, direction = state
        if exit_time is not None and exit_time > at_time:
            return direction
        return None

    def record_cluster(self, result: ClusterResult) -> None:
        """Record the cluster exit time for conflict detection."""
        if result.status == ClusterStatus.SKIPPED or result.exit_time is None:
            return
        key = (result.signal.symbol.upper(), result.signal.timeframe.upper())
        self._open_until[key] = (result.exit_time, result.signal.direction)


def _hit_flags(direction: Direction, bar: pd.Series, sl_price: float, tp_price: float) -> tuple[bool, bool]:
    if direction == Direction.BUY:
        return bool(bar["low"] <= sl_price), bool(bar["high"] >= tp_price)
    return bool(bar["high"] >= sl_price), bool(bar["low"] <= tp_price)


def _conservative_exit(direction: Direction, sl_price: float, tp_price: float) -> tuple[float, ExitReason]:
    del direction, tp_price
    return sl_price, ExitReason.SL


def _optimistic_exit(direction: Direction, sl_price: float, tp_price: float) -> tuple[float, ExitReason]:
    del direction, sl_price
    return tp_price, ExitReason.TP


def _is_better_sl(direction: Direction, candidate: float, current: float) -> bool:
    if direction == Direction.BUY:
        return candidate > current
    return candidate < current


def _apply_sl_move_rule(
    legs: list[PositionLeg],
    *,
    entry_price: float,
    hit_tp_levels: set[int],
    tp_levels: Sequence[TPLevel],
    rule: str,
) -> None:
    if rule == "none" or not hit_tp_levels:
        return

    tp_by_level = {tp.level: tp for tp in tp_levels}
    highest_hit = max(hit_tp_levels)

    if rule == "breakeven_after_tp1" and highest_hit >= 1:
        candidate = entry_price
    elif rule == "previous_tp":
        candidate = entry_price if highest_hit <= 1 else tp_by_level[highest_hit - 1].price
    else:
        return

    for leg in legs:
        if leg.exit_time is not None:
            continue
        current = leg.current_sl if leg.current_sl is not None else leg.sl_price
        if _is_better_sl(leg.direction, candidate, current):
            leg.current_sl = float(candidate)


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


def _cluster_path_stats(
    direction: Direction,
    entry_price: float,
    sl_price: float,
    path: pd.DataFrame,
) -> tuple[float | None, float | None]:
    if path.empty:
        return None, None
    risk_distance = abs(entry_price - sl_price)
    if risk_distance <= 0:
        return None, None
    if direction == Direction.BUY:
        adverse = max(0.0, entry_price - float(path["low"].min()))
        favorable = max(0.0, float(path["high"].max()) - entry_price)
    else:
        adverse = max(0.0, float(path["high"].max()) - entry_price)
        favorable = max(0.0, entry_price - float(path["low"].min()))
    return adverse / risk_distance, favorable / risk_distance


def simulate_cluster(
    *,
    signal: SignalRow,
    entry_time: pd.Timestamp,
    entry_price: float,
    future_bars: pd.DataFrame,
    sl_result: SLResult,
    tp_levels: Sequence[TPLevel],
    sizing: Sequence[LegSizing],
    market_spec: MarketSpec,
    ambiguity_policy: AmbiguityPolicy = AmbiguityPolicy.CONSERVATIVE,
    management: dict | None = None,
    force_exit_at: pd.Timestamp | None = None,
    force_exit_price: float | None = None,
    force_exit_reason: ExitReason = ExitReason.REVERSE_SIGNAL,
) -> ClusterResult:
    """Simulate one signal cluster using OHLC bar ranges."""
    management = management or {}
    normalized = normalize_ohlcv_frame(future_bars)
    normalized = normalized[normalized["bartime"] >= entry_time].reset_index(drop=True)
    force_exit_at = None if force_exit_at is None else pd.Timestamp(force_exit_at)
    if normalized.empty:
        return ClusterResult(
            signal=signal,
            status=ClusterStatus.SKIPPED,
            entry_time=entry_time,
            entry_price=entry_price,
            sl_price=sl_result.price,
            tp_levels=tuple(tp_levels),
            skip_reason="no_future_bars",
        )

    usable_sizing = [item for item in sizing if not item.skipped and item.lot > 0]
    if not usable_sizing:
        return ClusterResult(
            signal=signal,
            status=ClusterStatus.SKIPPED,
            entry_time=entry_time,
            entry_price=entry_price,
            sl_price=sl_result.price,
            tp_levels=tuple(tp_levels),
            skip_reason="all_legs_below_min_lot",
            metadata={"sizing": [item.__dict__ for item in sizing]},
        )

    levels_by_id = {tp.level: tp for tp in tp_levels}
    legs = [
        PositionLeg(
            leg_id=size.leg_id,
            direction=signal.direction,
            entry_time=entry_time,
            entry_price=float(entry_price),
            sl_price=float(sl_result.price),
            current_sl=float(sl_result.price),
            tp_level=levels_by_id[size.leg_id],
            sizing=size,
        )
        for size in usable_sizing
        if size.leg_id in levels_by_id
    ]
    if not legs:
        return ClusterResult(
            signal=signal,
            status=ClusterStatus.SKIPPED,
            entry_time=entry_time,
            entry_price=entry_price,
            sl_price=sl_result.price,
            tp_levels=tuple(tp_levels),
            skip_reason="no_tp_level_for_sized_legs",
        )

    hit_tp_levels: set[int] = set()
    ambiguous_bars = 0
    path_end_idx = 0
    sl_move_rule = str(management.get("sl_move_rule", "none"))

    for idx, bar in normalized.iterrows():
        if force_exit_at is not None and pd.Timestamp(bar["bartime"]) >= force_exit_at:
            path_end_idx = max(0, int(idx) - 1)
            exit_price = float(bar["open"] if force_exit_price is None else force_exit_price)
            for leg in legs:
                if leg.exit_time is not None:
                    continue
                _close_leg(
                    leg,
                    exit_time=force_exit_at,
                    exit_price=exit_price,
                    exit_reason=force_exit_reason,
                    market_spec=market_spec,
                )
            break

        path_end_idx = int(idx)
        for leg in legs:
            if leg.exit_time is not None:
                continue
            current_sl = float(leg.current_sl if leg.current_sl is not None else leg.sl_price)
            sl_hit, tp_hit = _hit_flags(signal.direction, bar, current_sl, leg.tp_level.price)

            if sl_hit and tp_hit:
                ambiguous_bars += 1
                if ambiguity_policy == AmbiguityPolicy.SKIP:
                    return ClusterResult(
                        signal=signal,
                        status=ClusterStatus.SKIPPED,
                        entry_time=entry_time,
                        entry_price=entry_price,
                        sl_price=sl_result.price,
                        tp_levels=tuple(tp_levels),
                        legs=tuple(legs),
                        skip_reason="ambiguous_bar",
                        ambiguous_bars=ambiguous_bars,
                    )
                if ambiguity_policy == AmbiguityPolicy.OPTIMISTIC:
                    exit_price, reason = _optimistic_exit(signal.direction, current_sl, leg.tp_level.price)
                    hit_tp_levels.add(leg.tp_level.level)
                else:
                    exit_price, reason = _conservative_exit(signal.direction, current_sl, leg.tp_level.price)
                    if ambiguity_policy == AmbiguityPolicy.MARK_AMBIGUOUS:
                        reason = ExitReason.AMBIGUOUS
                _close_leg(
                    leg,
                    exit_time=bar["bartime"],
                    exit_price=exit_price,
                    exit_reason=reason,
                    market_spec=market_spec,
                    ambiguous=True,
                )
            elif tp_hit:
                hit_tp_levels.add(leg.tp_level.level)
                _close_leg(
                    leg,
                    exit_time=bar["bartime"],
                    exit_price=leg.tp_level.price,
                    exit_reason=ExitReason.TP,
                    market_spec=market_spec,
                )
            elif sl_hit:
                _close_leg(
                    leg,
                    exit_time=bar["bartime"],
                    exit_price=current_sl,
                    exit_reason=ExitReason.SL,
                    market_spec=market_spec,
                )

        _apply_sl_move_rule(
            legs,
            entry_price=entry_price,
            hit_tp_levels=hit_tp_levels,
            tp_levels=tp_levels,
            rule=sl_move_rule,
        )

        if all(leg.exit_time is not None for leg in legs):
            break

    last_bar = normalized.iloc[path_end_idx]
    for leg in legs:
        if leg.exit_time is None:
            _close_leg(
                leg,
                exit_time=last_bar["bartime"],
                exit_price=float(last_bar["close"]),
                exit_reason=ExitReason.END_OF_DATA,
                market_spec=market_spec,
            )

    path = normalized.iloc[: path_end_idx + 1]
    bar_mae_r, bar_mfe_r = _cluster_path_stats(
        signal.direction,
        float(entry_price),
        float(sl_result.price),
        path,
    )

    gross_r = sum(
        leg.sizing.risk_fraction * (leg.r_result + leg.cost_r)
        for leg in legs
    )
    total_cost_r = sum(leg.sizing.risk_fraction * leg.cost_r for leg in legs)
    total_r = gross_r - total_cost_r
    exit_time = max(leg.exit_time for leg in legs if leg.exit_time is not None)
    has_end = any(leg.exit_reason == ExitReason.END_OF_DATA for leg in legs)
    has_marked_ambiguous = any(leg.exit_reason == ExitReason.AMBIGUOUS for leg in legs)
    has_reverse = any(leg.exit_reason == ExitReason.REVERSE_SIGNAL for leg in legs)

    if has_end:
        status = ClusterStatus.OPEN_AT_END
    elif has_marked_ambiguous:
        status = ClusterStatus.AMBIGUOUS
    elif has_reverse:
        status = ClusterStatus.REVERSED
    else:
        status = ClusterStatus.CLOSED

    return ClusterResult(
        signal=signal,
        status=status,
        entry_time=entry_time,
        entry_price=float(entry_price),
        sl_price=float(sl_result.price),
        tp_levels=tuple(tp_levels),
        legs=tuple(replace(leg) for leg in legs),
        exit_time=exit_time,
        r_result=total_r,
        gross_r=gross_r,
        cost_r=total_cost_r,
        bar_mae_r=bar_mae_r,
        bar_mfe_r=bar_mfe_r,
        holding_bars=len(path),
        ambiguous_bars=ambiguous_bars,
        metadata={
            "sl_method": sl_result.method,
            "sl_params": sl_result.params,
            "management": dict(management),
            "force_exit_at": force_exit_at,
            "force_exit_reason": force_exit_reason.value if force_exit_at is not None else None,
        },
    )
