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


def grid_size(param_grid: Mapping[str, Iterable[Any] | Any]) -> int:
    """Return the number of parameter combinations without expanding the grid."""
    size = 1
    for value in param_grid.values():
        size *= len(_as_values(value))
    return size


def build_refined_grid(
    candidates: pd.DataFrame,
    refinement_values: Mapping[str, Iterable[Any] | Any],
    *,
    param_cols: list[str] | None = None,
    top_n: int = 3,
    neighbor_steps: int = 1,
    max_combinations: int = 250,
) -> dict[str, list[Any]]:
    """Build a bounded fine grid around the best coarse-grid candidates.

    `refinement_values` defines the complete fine-resolution value universe for
    each parameter. Values up to `neighbor_steps` positions from each selected
    candidate are included. Additional candidates are accepted only while the
    Cartesian product stays within `max_combinations`.
    """
    if candidates.empty:
        raise ValueError("Cannot build a refined grid from empty candidates.")
    if top_n <= 0:
        raise ValueError("top_n must be positive.")
    if neighbor_steps < 0:
        raise ValueError("neighbor_steps must be >= 0.")
    if max_combinations <= 0:
        raise ValueError("max_combinations must be positive.")

    columns = list(param_cols or refinement_values.keys())
    missing = [col for col in columns if col not in candidates.columns]
    if missing:
        raise ValueError(f"Missing candidate columns: {missing}")
    missing_values = [col for col in columns if col not in refinement_values]
    if missing_values:
        raise ValueError(f"Missing refinement values: {missing_values}")

    ranked = candidates.copy()
    if "candidate_eligible" in ranked.columns and ranked["candidate_eligible"].any():
        ranked = ranked[ranked["candidate_eligible"]].copy()
    ranked = ranked.head(top_n)

    universes = {col: _as_values(refinement_values[col]) for col in columns}
    selected: dict[str, set[Any]] = {col: set() for col in columns}
    accepted_candidates = 0
    for _, row in ranked.iterrows():
        tentative = {col: set(values) for col, values in selected.items()}
        for col in columns:
            universe = universes[col]
            position = _nearest_value_index(universe, row[col])
            start = max(0, position - neighbor_steps)
            end = min(len(universe), position + neighbor_steps + 1)
            tentative[col].update(universe[start:end])
        tentative_size = 1
        for values in tentative.values():
            tentative_size *= len(values)
        if tentative_size > max_combinations and accepted_candidates > 0:
            continue
        if tentative_size > max_combinations:
            raise ValueError(
                f"One candidate neighborhood creates {tentative_size} combinations, "
                f"above max_combinations={max_combinations}."
            )
        selected = tentative
        accepted_candidates += 1

    if accepted_candidates == 0:
        raise ValueError("No candidate could be used to build the refined grid.")
    return {
        col: [value for value in universes[col] if value in selected[col]]
        for col in columns
    }


def _nearest_value_index(values: list[Any], target: Any) -> int:
    if not values:
        raise ValueError("Refinement value lists cannot be empty.")
    try:
        numeric_target = float(target)
        numeric_values = [float(value) for value in values]
    except (TypeError, ValueError):
        try:
            return values.index(target)
        except ValueError as exc:
            raise ValueError(f"Candidate value {target!r} is absent from refinement values.") from exc
    return min(range(len(values)), key=lambda idx: abs(numeric_values[idx] - numeric_target))


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
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> pd.DataFrame:
    """Evaluate each parameter combination and return one row per combination."""
    combinations = expand_grid(param_grid)
    total = len(combinations)
    rows = []
    for index, params in enumerate(combinations, start=1):
        metrics = dict(evaluate(params))
        rows.append({**params, **metrics})
        if progress_callback is not None:
            progress_callback(index, total, params)
    return pd.DataFrame(rows)


def add_stability_scores(
    results: pd.DataFrame,
    *,
    metric_col: str = "expectancy_r",
    param_cols: list[str] | None = None,
    neighborhood_pct: float = 0.10,
    neighborhood_steps: int | None = None,
    epsilon: float = 1e-9,
) -> pd.DataFrame:
    """Add local stability score for each point in a parameter grid.

    Stability score is defined as local median(metric) divided by local
    std(metric) inside a parameter neighborhood. Numeric params use either
    +/- `neighborhood_pct` or adjacent unique grid values selected by
    `neighborhood_steps`; categorical params must match exactly.

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
    if neighborhood_steps is not None and neighborhood_steps < 0:
        raise ValueError("neighborhood_steps must be >= 0.")

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
                numeric = pd.to_numeric(out[col], errors="coerce")
                if neighborhood_steps is not None:
                    unique = sorted(numeric.dropna().unique())
                    position = min(
                        range(len(unique)),
                        key=lambda idx: abs(float(unique[idx]) - float(value)),
                    )
                    start = max(0, position - neighborhood_steps)
                    end = min(len(unique), position + neighborhood_steps + 1)
                    allowed = unique[start:end]
                    mask &= numeric.isin(allowed)
                else:
                    radius = max(abs(float(value)) * neighborhood_pct, epsilon)
                    mask &= (numeric - float(value)).abs() <= radius
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
