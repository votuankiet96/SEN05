"""Cluster-level metrics for execution logic validation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from math import sqrt
from typing import Any

import numpy as np
import pandas as pd

from backtest_optimize.contracts import (
    BacktestResult,
    ClusterResult,
    ClusterStatus,
    ExitReason,
)


def _clusters(value: BacktestResult | Iterable[ClusterResult]) -> list[ClusterResult]:
    if isinstance(value, BacktestResult):
        return list(value.clusters)
    return list(value)


def clusters_to_frame(value: BacktestResult | Iterable[ClusterResult]) -> pd.DataFrame:
    """Flatten cluster results into an analysis DataFrame."""
    rows: list[dict[str, Any]] = []
    for cluster in _clusters(value):
        tp_hits = sorted(
            leg.tp_level.level
            for leg in cluster.legs
            if leg.exit_reason == ExitReason.TP
        )
        sl_hits = sum(1 for leg in cluster.legs if leg.exit_reason == ExitReason.SL)
        rows.append(
            {
                "symbol": cluster.signal.symbol,
                "timeframe": cluster.signal.timeframe,
                "signal_bartime": cluster.signal.bartime,
                "direction": cluster.signal.direction.value,
                "status": cluster.status.value,
                "skip_reason": cluster.skip_reason,
                "entry_time": cluster.entry_time,
                "exit_time": cluster.exit_time,
                "entry_price": cluster.entry_price,
                "sl_price": cluster.sl_price,
                "r_result": cluster.r_result,
                "gross_r": cluster.gross_r,
                "cost_r": cluster.cost_r,
                "bar_mae_r": cluster.bar_mae_r,
                "bar_mfe_r": cluster.bar_mfe_r,
                "holding_bars": cluster.holding_bars,
                "ambiguous_bars": cluster.ambiguous_bars,
                "tp_hits": tp_hits,
                "sl_leg_hits": sl_hits,
                "leg_count": len(cluster.legs),
            }
        )
    return pd.DataFrame(rows)


def tp_hit_rates(clusters: Iterable[ClusterResult]) -> dict[str, float]:
    """Return percentage of accepted clusters that hit each TP level."""
    accepted = [c for c in clusters if c.status != ClusterStatus.SKIPPED]
    denominator = len(accepted)
    if denominator == 0:
        return {}

    hit_by_level: dict[int, int] = defaultdict(int)
    for cluster in accepted:
        levels = {
            leg.tp_level.level
            for leg in cluster.legs
            if leg.exit_reason == ExitReason.TP
        }
        for level in levels:
            hit_by_level[level] += 1

    return {
        f"tp{level}_hit_rate": count / denominator
        for level, count in sorted(hit_by_level.items())
    }


def summarize(value: BacktestResult | Iterable[ClusterResult]) -> dict[str, Any]:
    """Summarize cluster-level logic quality."""
    clusters = _clusters(value)
    total = len(clusters)
    skipped_clusters = [c for c in clusters if c.status == ClusterStatus.SKIPPED]
    accepted = [c for c in clusters if c.status != ClusterStatus.SKIPPED]
    open_at_end = [c for c in clusters if c.status == ClusterStatus.OPEN_AT_END]
    r_eligible = [
        c
        for c in accepted
        if c.status != ClusterStatus.OPEN_AT_END
    ]
    r_values = np.array([c.r_result for c in r_eligible], dtype=float)

    expectancy = float(np.mean(r_values)) if len(r_values) else None
    std_r = float(np.std(r_values, ddof=1)) if len(r_values) > 1 else None
    sqn = None
    if len(r_values) >= 30 and std_r and std_r > 0:
        sqn = expectancy / std_r * sqrt(len(r_values))

    ambiguity_clusters = sum(1 for c in accepted if c.ambiguous_bars > 0)
    sl_clusters = sum(
        1
        for c in accepted
        if any(leg.exit_reason == ExitReason.SL for leg in c.legs)
    )

    summary: dict[str, Any] = {
        "signal_count": total,
        "accepted_count": len(accepted),
        "skipped_count": len(skipped_clusters),
        "open_at_end_count": len(open_at_end),
        "r_eligible_count": len(r_eligible),
        "open_at_end_excluded_from_r": True,
        "skip_rate": len(skipped_clusters) / total if total else None,
        "expectancy_r": expectancy,
        "std_r": std_r,
        "sqn": sqn,
        "ambiguity_rate": ambiguity_clusters / len(accepted) if accepted else None,
        "sl_hit_rate": sl_clusters / len(accepted) if accepted else None,
        "mean_bar_mae_r": _nanmean([c.bar_mae_r for c in r_eligible]),
        "mean_bar_mfe_r": _nanmean([c.bar_mfe_r for c in r_eligible]),
        "mean_holding_bars": _nanmean([c.holding_bars for c in r_eligible]),
        "total_cost_r": float(np.sum([c.cost_r for c in r_eligible])) if r_eligible else 0.0,
    }
    summary.update(tp_hit_rates(r_eligible))
    return summary


def _nanmean(values: Iterable[float | int | None]) -> float | None:
    array = np.array([value for value in values if value is not None], dtype=float)
    if len(array) == 0:
        return None
    return float(np.nanmean(array))
