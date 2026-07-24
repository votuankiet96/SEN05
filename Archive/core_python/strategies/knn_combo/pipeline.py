"""Pipeline helpers for KNN Combo."""

from __future__ import annotations

import pandas as pd

from core_python.strategies.knn_combo.levels import add_knn_combo_levels
from core_python.strategies.knn_combo.signals import build_knn_combo_frames


def build_knn_combo_strategy_frames(
    trend_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build trend and entry frames for the dashboard/export engine."""
    trend, entry = build_knn_combo_frames(trend_df, entry_df, params)
    return trend, add_knn_combo_levels(entry, params, symbol)

