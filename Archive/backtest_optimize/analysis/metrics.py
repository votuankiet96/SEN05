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

PENDING_STATUSES = {
    ClusterStatus.PENDING_EXPIRED,
    ClusterStatus.PENDING_CANCELLED,
    ClusterStatus.PENDING_UNFILLED_AT_END,
}


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
        reverse_hits = sum(1 for leg in cluster.legs if leg.exit_reason == ExitReason.REVERSE_SIGNAL)
        exit_reasons = sorted(
            {
                leg.exit_reason.value
                for leg in cluster.legs
                if leg.exit_reason is not None
            }
        )
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
                "reverse_leg_hits": reverse_hits,
                "exit_reasons": exit_reasons,
                "leg_count": len(cluster.legs),
            }
        )
    return pd.DataFrame(rows)


def tp_hit_rates(clusters: Iterable[ClusterResult]) -> dict[str, float]:
    """Return percentage of accepted clusters that hit each TP level."""
    accepted = [
        c
        for c in clusters
        if c.status != ClusterStatus.SKIPPED and c.status not in PENDING_STATUSES
    ]
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
    trade_clusters = [c for c in accepted if c.status not in PENDING_STATUSES]
    open_at_end = [c for c in clusters if c.status == ClusterStatus.OPEN_AT_END]
    pending_expired = [c for c in clusters if c.status == ClusterStatus.PENDING_EXPIRED]
    pending_cancelled = [c for c in clusters if c.status == ClusterStatus.PENDING_CANCELLED]
    pending_unfilled = [c for c in clusters if c.status == ClusterStatus.PENDING_UNFILLED_AT_END]
    reversed_clusters = [
        c
        for c in trade_clusters
        if c.status == ClusterStatus.REVERSED
        or any(leg.exit_reason == ExitReason.REVERSE_SIGNAL for leg in c.legs)
    ]
    same_direction_skips = [c for c in skipped_clusters if c.skip_reason == "same_direction_open"]
    opposite_direction_skips = [c for c in skipped_clusters if c.skip_reason == "opposite_direction_open"]
    legacy_cluster_open_skips = [c for c in skipped_clusters if c.skip_reason == "cluster_open"]
    execution_error_skips = [
        c
        for c in skipped_clusters
        if str(c.skip_reason or "").startswith("execution_error:")
    ]
    r_eligible = [
        c
        for c in trade_clusters
        if c.status != ClusterStatus.OPEN_AT_END
    ]
    r_values = np.array([c.r_result for c in r_eligible], dtype=float)
    reversed_r_values = np.array([c.r_result for c in reversed_clusters], dtype=float)

    expectancy = float(np.mean(r_values)) if len(r_values) else None
    std_r = float(np.std(r_values, ddof=1)) if len(r_values) > 1 else None
    reversed_avg_r = float(np.mean(reversed_r_values)) if len(reversed_r_values) else None
    reversed_profit_count = int(np.sum(reversed_r_values > 0)) if len(reversed_r_values) else 0
    reversed_loss_count = int(np.sum(reversed_r_values < 0)) if len(reversed_r_values) else 0
    reversed_flat_count = int(np.sum(reversed_r_values == 0)) if len(reversed_r_values) else 0
    sqn = None
    if len(r_values) >= 30 and std_r and std_r > 0:
        sqn = expectancy / std_r * sqrt(len(r_values))

    ambiguity_clusters = sum(1 for c in trade_clusters if c.ambiguous_bars > 0)
    sl_clusters = sum(
        1
        for c in trade_clusters
        if any(leg.exit_reason == ExitReason.SL for leg in c.legs)
    )

    summary: dict[str, Any] = {
        "signal_count": total,
        "accepted_count": len(accepted),
        "filled_count": len(trade_clusters),
        "pending_expired_count": len(pending_expired),
        "pending_cancelled_count": len(pending_cancelled),
        "pending_unfilled_count": len(pending_unfilled),
        "skipped_count": len(skipped_clusters),
        "reversed_count": len(reversed_clusters),
        "reversed_profit_count": reversed_profit_count,
        "reversed_loss_count": reversed_loss_count,
        "reversed_flat_count": reversed_flat_count,
        "open_at_end_count": len(open_at_end),
        "r_eligible_count": len(r_eligible),
        "open_at_end_excluded_from_r": True,
        "skip_rate": len(skipped_clusters) / total if total else None,
        "fill_rate": len(trade_clusters) / len(accepted) if accepted else None,
        "pending_expired_rate": len(pending_expired) / len(accepted) if accepted else None,
        "pending_cancelled_rate": len(pending_cancelled) / len(accepted) if accepted else None,
        "pending_unfilled_rate": len(pending_unfilled) / len(accepted) if accepted else None,
        "reversed_rate": len(reversed_clusters) / len(trade_clusters) if trade_clusters else None,
        "reversed_win_rate": reversed_profit_count / len(reversed_clusters) if reversed_clusters else None,
        "reversed_loss_rate": reversed_loss_count / len(reversed_clusters) if reversed_clusters else None,
        "reversed_avg_r": reversed_avg_r,
        "same_direction_skip_count": len(same_direction_skips),
        "opposite_direction_skip_count": len(opposite_direction_skips),
        "legacy_cluster_open_skip_count": len(legacy_cluster_open_skips),
        "execution_error_count": len(execution_error_skips),
        "expectancy_r": expectancy,
        "std_r": std_r,
        "sqn": sqn,
        "ambiguity_rate": ambiguity_clusters / len(trade_clusters) if trade_clusters else None,
        "sl_hit_rate": sl_clusters / len(trade_clusters) if trade_clusters else None,
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
