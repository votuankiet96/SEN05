"""OHLC fill and exit helpers.

These helpers deliberately work with bar ranges instead of tick ordering. When
one bar can imply multiple event orders, callers must use an ambiguity policy.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest_optimize.contracts import AmbiguityPolicy, Direction, ExitReason


@dataclass(frozen=True)
class ExitDecision:
    """Decision for one leg on one OHLC bar."""

    hit: bool
    price: float | None = None
    reason: ExitReason | None = None
    ambiguous: bool = False
    tp_hit: bool = False
    sl_hit: bool = False


def stop_entry_touched(direction: Direction, bar: pd.Series, entry_price: float) -> bool:
    """Return True when a stop entry target is touched by the OHLC range."""
    if direction == Direction.BUY:
        return bool(float(bar["high"]) >= float(entry_price))
    return bool(float(bar["low"]) <= float(entry_price))


def exit_touches(
    direction: Direction,
    bar: pd.Series,
    sl_price: float,
    tp_price: float | None,
) -> tuple[bool, bool]:
    """Return `(sl_hit, tp_hit)` for a leg on one bar."""
    if direction == Direction.BUY:
        sl_hit = bool(float(bar["low"]) <= float(sl_price))
        tp_hit = bool(tp_price is not None and float(bar["high"]) >= float(tp_price))
    else:
        sl_hit = bool(float(bar["high"]) >= float(sl_price))
        tp_hit = bool(tp_price is not None and float(bar["low"]) <= float(tp_price))
    return sl_hit, tp_hit


def resolve_exit(
    *,
    direction: Direction,
    bar: pd.Series,
    sl_price: float,
    tp_price: float | None,
    ambiguity_policy: AmbiguityPolicy,
) -> ExitDecision:
    """Resolve one leg exit on one OHLC bar."""
    sl_hit, tp_hit = exit_touches(direction, bar, sl_price, tp_price)
    if not sl_hit and not tp_hit:
        return ExitDecision(hit=False)

    if sl_hit and tp_hit:
        if ambiguity_policy == AmbiguityPolicy.SKIP:
            return ExitDecision(
                hit=True,
                price=None,
                reason=ExitReason.SKIPPED,
                ambiguous=True,
                tp_hit=True,
                sl_hit=True,
            )
        if ambiguity_policy == AmbiguityPolicy.OPTIMISTIC and tp_price is not None:
            return ExitDecision(
                hit=True,
                price=float(tp_price),
                reason=ExitReason.TP,
                ambiguous=True,
                tp_hit=True,
                sl_hit=True,
            )
        reason = (
            ExitReason.AMBIGUOUS
            if ambiguity_policy == AmbiguityPolicy.MARK_AMBIGUOUS
            else ExitReason.SL
        )
        return ExitDecision(
            hit=True,
            price=float(sl_price),
            reason=reason,
            ambiguous=True,
            tp_hit=True,
            sl_hit=True,
        )

    if tp_hit and tp_price is not None:
        return ExitDecision(
            hit=True,
            price=float(tp_price),
            reason=ExitReason.TP,
            tp_hit=True,
        )
    return ExitDecision(
        hit=True,
        price=float(sl_price),
        reason=ExitReason.SL,
        sl_hit=True,
    )
