"""Risk-based lot sizing."""

from __future__ import annotations

import math
from typing import Sequence

from backtest_optimize.contracts import LegSizing, MarketSpec, TPLevel
from backtest_optimize.execution.cost_model import risk_currency_per_lot


def round_down_to_step(value: float, step: float) -> float:
    """Round value down to the nearest positive step."""
    if step <= 0:
        raise ValueError("step must be positive.")
    rounded = math.floor((float(value) + 1e-12) / step) * step
    decimals = max(0, len(str(step).split(".")[-1]) if "." in str(step) else 0)
    return round(rounded, decimals)


def normalize_leg_weights(
    tp_levels: Sequence[TPLevel],
    leg_weights: Sequence[float] | None = None,
) -> list[float]:
    """Return normalized leg risk fractions."""
    if not tp_levels:
        raise ValueError("At least one TP level is required.")

    if leg_weights is not None:
        if len(leg_weights) != len(tp_levels):
            raise ValueError("leg_weights length must match tp_levels length.")
        raw = [float(w) for w in leg_weights]
    else:
        raw = [float(tp.weight) for tp in tp_levels]

    if any(w < 0 for w in raw):
        raise ValueError("Leg weights must be non-negative.")
    total = sum(raw)
    if total <= 0:
        raw = [1.0 for _ in tp_levels]
        total = float(len(tp_levels))
    return [w / total for w in raw]


def calculate_leg_sizing(
    *,
    tp_levels: Sequence[TPLevel],
    entry_price: float,
    sl_price: float,
    market_spec: MarketSpec,
    account_size: float,
    risk_per_cluster: float,
    leg_weights: Sequence[float] | None = None,
) -> list[LegSizing]:
    """Calculate lot sizes for each TP leg using round-down/skip policy."""
    if account_size <= 0:
        raise ValueError("account_size must be positive.")
    if not 0 < risk_per_cluster < 1:
        raise ValueError("risk_per_cluster must be between 0 and 1.")

    risk_per_lot = risk_currency_per_lot(entry_price, sl_price, market_spec)
    cluster_risk = float(account_size) * float(risk_per_cluster)
    weights = normalize_leg_weights(tp_levels, leg_weights)

    decisions: list[LegSizing] = []
    for idx, weight in enumerate(weights, start=1):
        risk_amount = cluster_risk * weight
        raw_lot = risk_amount / risk_per_lot
        lot = round_down_to_step(raw_lot, market_spec.lot_step)
        skipped = lot < market_spec.min_lot
        decisions.append(
            LegSizing(
                leg_id=idx,
                risk_fraction=weight,
                risk_amount=risk_amount,
                raw_lot=raw_lot,
                lot=0.0 if skipped else lot,
                skipped=skipped,
                skip_reason="below_min_lot" if skipped else None,
            )
        )
    return decisions
