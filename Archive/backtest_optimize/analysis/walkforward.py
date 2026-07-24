"""Walk-forward window helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class WalkForwardWindow:
    """One walk-forward train/test split."""

    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    index: int


def make_walkforward_windows(
    *,
    start: object,
    end: object,
    train_months: int = 6,
    test_months: int = 1,
    step_months: int = 1,
) -> list[WalkForwardWindow]:
    """Create rolling train/test windows."""
    if train_months <= 0 or test_months <= 0 or step_months <= 0:
        raise ValueError("Window month counts must be positive.")

    current = pd.Timestamp(start)
    final = pd.Timestamp(end)
    windows: list[WalkForwardWindow] = []
    idx = 0
    while True:
        train_start = current
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)
        if test_end > final:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                index=idx,
            )
        )
        idx += 1
        current = current + pd.DateOffset(months=step_months)
    return windows


def split_by_window(
    df: pd.DataFrame,
    window: WalkForwardWindow,
    *,
    time_col: str = "bartime",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a DataFrame into train and test rows for a window."""
    times = pd.to_datetime(df[time_col])
    train = df[(times >= window.train_start) & (times < window.train_end)].copy()
    test = df[(times >= window.test_start) & (times < window.test_end)].copy()
    return train, test


def run_walkforward(
    evaluate: Callable[[WalkForwardWindow], Mapping[str, Any]],
    windows: list[WalkForwardWindow],
) -> pd.DataFrame:
    """Evaluate a callable once per window."""
    rows = []
    for window in windows:
        metrics = dict(evaluate(window))
        rows.append(
            {
                "window": window.index,
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                **metrics,
            }
        )
    return pd.DataFrame(rows)


def walkforward_stability_score(
    results: pd.DataFrame,
    *,
    metric_col: str = "oos_expectancy_r",
    min_windows: int = 5,
) -> float | None:
    """Return mean(metric) / std(metric) across OOS windows.

    The score is unreliable with fewer than `min_windows` non-null windows and
    returns None in that case. Interpret 5-10 windows with caution.
    """
    if results.empty or metric_col not in results.columns:
        return None
    values = pd.to_numeric(results[metric_col], errors="coerce").dropna().to_numpy()
    if len(values) < min_windows:
        return None
    std = float(np.std(values, ddof=1))
    if std == 0:
        return None
    return float(np.mean(values) / std)
