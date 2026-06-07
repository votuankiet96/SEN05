"""Monte Carlo helpers for R-series risk-shape analysis."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

MonteCarloMethod = Literal["shuffle", "bootstrap", "block_bootstrap"]
EXCLUDED_CLUSTER_STATUSES = {
    "skipped",
    "open_at_end",
    "pending_expired",
    "pending_cancelled",
    "pending_unfilled_at_end",
}


def eligible_r_values(clusters: pd.DataFrame) -> np.ndarray:
    """Return closed-trade R values suitable for Monte Carlo simulation."""
    required = {"status", "r_result"}
    missing = required - set(clusters.columns)
    if missing:
        raise ValueError(f"Cluster data is missing columns: {sorted(missing)}")
    statuses = clusters["status"].astype(str).str.lower()
    return (
        pd.to_numeric(
            clusters.loc[~statuses.isin(EXCLUDED_CLUSTER_STATUSES), "r_result"],
            errors="coerce",
        )
        .dropna()
        .to_numpy(dtype=float)
    )


def simulate_paths(
    r_values: list[float] | np.ndarray,
    *,
    n_runs: int = 1000,
    method: MonteCarloMethod = "shuffle",
    block_size: int = 5,
    seed: int | None = None,
) -> np.ndarray:
    """Simulate R paths using shuffle, bootstrap, or block bootstrap."""
    values = np.asarray(r_values, dtype=float)
    values = values[~np.isnan(values)]
    if len(values) == 0:
        raise ValueError("r_values is empty.")
    if n_runs <= 0:
        raise ValueError("n_runs must be positive.")

    rng = np.random.default_rng(seed)
    paths = np.empty((n_runs, len(values)), dtype=float)
    for idx in range(n_runs):
        if method == "shuffle":
            paths[idx] = rng.permutation(values)
        elif method == "bootstrap":
            paths[idx] = rng.choice(values, size=len(values), replace=True)
        elif method == "block_bootstrap":
            paths[idx] = _block_bootstrap(values, rng, block_size)
        else:
            raise ValueError(f"Unsupported Monte Carlo method: {method}")
    return paths


def _block_bootstrap(values: np.ndarray, rng: np.random.Generator, block_size: int) -> np.ndarray:
    if block_size <= 0:
        raise ValueError("block_size must be positive.")
    n = len(values)
    starts = np.arange(n)
    pieces = []
    while sum(len(piece) for piece in pieces) < n:
        start = int(rng.choice(starts))
        end = min(start + block_size, n)
        piece = values[start:end]
        if len(piece) < block_size:
            piece = np.concatenate([piece, values[: block_size - len(piece)]])
        pieces.append(piece)
    return np.concatenate(pieces)[:n]


def cumulative_paths(paths: np.ndarray, *, risk_per_cluster: float = 1.0) -> np.ndarray:
    """Convert R paths to cumulative R or cumulative account-percent paths."""
    return np.cumsum(paths * risk_per_cluster, axis=1)


def max_drawdowns(cumulative: np.ndarray) -> np.ndarray:
    """Return max drawdown for each cumulative path."""
    peaks = np.maximum.accumulate(cumulative, axis=1)
    drawdowns = peaks - cumulative
    return np.max(drawdowns, axis=1)


def summarize_paths(
    paths: np.ndarray,
    *,
    risk_per_cluster: float = 1.0,
    drawdown_threshold: float = 10.0,
) -> dict[str, float]:
    """Summarize Monte Carlo paths.

    If `risk_per_cluster` is 1.0, drawdown units are R. If it is 0.01, units are
    account fraction.
    """
    cumulative = cumulative_paths(paths, risk_per_cluster=risk_per_cluster)
    dds = max_drawdowns(cumulative)
    finals = cumulative[:, -1]
    return {
        "runs": float(paths.shape[0]),
        "trades_per_run": float(paths.shape[1]),
        "final_p05": float(np.percentile(finals, 5)),
        "final_p50": float(np.percentile(finals, 50)),
        "final_p95": float(np.percentile(finals, 95)),
        "max_dd_p50": float(np.percentile(dds, 50)),
        "max_dd_p95": float(np.percentile(dds, 95)),
        "max_dd_p99": float(np.percentile(dds, 99)),
        "risk_of_ruin": float(np.mean(dds > drawdown_threshold)),
        "drawdown_threshold": float(drawdown_threshold),
    }
