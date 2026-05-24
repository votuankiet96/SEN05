"""Parameter-grid and stability-map helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from itertools import product
from typing import Any

import numpy as np
import pandas as pd


def expand_grid(param_grid: Mapping[str, Iterable[Any] | Any]) -> list[dict[str, Any]]:
    """Expand a simple parameter grid into dictionaries."""
    keys = list(param_grid.keys())
    value_lists = [_as_values(param_grid[key]) for key in keys]
    return [dict(zip(keys, values)) for values in product(*value_lists)]


def _as_values(value: Iterable[Any] | Any) -> list[Any]:
    if isinstance(value, (str, bytes, dict)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def run_grid(
    evaluate: Callable[[dict[str, Any]], Mapping[str, Any]],
    param_grid: Mapping[str, Iterable[Any] | Any],
) -> pd.DataFrame:
    """Evaluate each parameter combination and return one row per combination."""
    rows = []
    for params in expand_grid(param_grid):
        metrics = dict(evaluate(params))
        rows.append({**params, **metrics})
    return pd.DataFrame(rows)


def add_stability_scores(
    results: pd.DataFrame,
    *,
    metric_col: str = "expectancy_r",
    param_cols: list[str] | None = None,
    neighborhood_pct: float = 0.10,
    epsilon: float = 1e-9,
) -> pd.DataFrame:
    """Add local stability score for each point in a parameter grid.

    Stability score is defined as local median(metric) divided by local
    std(metric) inside a parameter neighborhood. Numeric params use
    +/- `neighborhood_pct`; categorical params must match exactly.

    `param_cols` should be the original grid parameter columns. It is required
    because result frames also contain metric columns, and auto-detecting
    parameters from mixed param/metric output can make every point its own
    neighborhood. Scores are meaningful only when grid density is fine enough
    relative to `neighborhood_pct`; inspect `<metric>_local_count`.
    """
    if results.empty:
        return results.copy()
    if metric_col not in results.columns:
        raise ValueError(f"Missing metric column: {metric_col}")

    out = results.copy()
    if param_cols is None:
        raise ValueError("param_cols is required; pass the original parameter grid columns.")
    missing_params = [col for col in param_cols if col not in out.columns]
    if missing_params:
        raise ValueError(f"Missing param columns: {missing_params}")

    medians: list[float | None] = []
    stds: list[float | None] = []
    scores: list[float | None] = []
    counts: list[int] = []

    for _, row in out.iterrows():
        mask = pd.Series(True, index=out.index)
        for col in param_cols:
            if col not in out.columns:
                continue
            value = row[col]
            if pd.api.types.is_numeric_dtype(out[col]):
                radius = max(abs(float(value)) * neighborhood_pct, epsilon)
                mask &= (out[col].astype(float) - float(value)).abs() <= radius
            else:
                mask &= out[col] == value

        local = pd.to_numeric(out.loc[mask, metric_col], errors="coerce").dropna()
        counts.append(int(len(local)))
        if len(local) == 0:
            medians.append(None)
            stds.append(None)
            scores.append(None)
            continue
        median = float(np.median(local))
        std = float(np.std(local, ddof=1)) if len(local) > 1 else 0.0
        medians.append(median)
        stds.append(std)
        scores.append(median / (std + epsilon))

    out[f"{metric_col}_local_median"] = medians
    out[f"{metric_col}_local_std"] = stds
    out[f"{metric_col}_stability_score"] = scores
    out[f"{metric_col}_local_count"] = counts
    return out
