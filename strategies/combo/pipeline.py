"""Pipeline helpers for single-timeframe and MTF Combo execution paths."""

from __future__ import annotations

import pandas as pd

from core_python.strategies.combo.levels import add_combo_levels
from core_python.strategies.combo.signals import add_combo_indicators, detect_combo_signals
from core_python.strategies.combo.trend_filter import (
    merge_combo_trend_into_entry,
    prepare_combo_trend_frame,
)


def build_combo_frame(entry_df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """Run the current single-timeframe Combo pipeline."""
    with_indicators = add_combo_indicators(entry_df, params)
    with_signals = detect_combo_signals(with_indicators, symbol=symbol, params=params)
    return add_combo_levels(with_signals, params, symbol)


def build_combo_mtf_frames(
    trend_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build HTF trend frame and entry frame filtered by that trend."""
    trend = prepare_combo_trend_frame(trend_df, params)
    entry = add_combo_indicators(entry_df, params)
    merged = merge_combo_trend_into_entry(entry, trend)
    signals = detect_combo_signals(merged, symbol=symbol, params=params)
    enriched = add_combo_levels(signals, params, symbol)
    return trend, enriched
