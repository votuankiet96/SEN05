"""Pipeline helpers for single-timeframe and MTF Combo execution paths."""

from __future__ import annotations

import pandas as pd

from og_core.strategies.combo.levels import add_combo_levels
from og_core.strategies.combo.signals import add_combo_indicators, detect_combo_signals
from og_core.strategies.combo.trend_filter import (
    merge_combo_trend_into_entry,
    prepare_combo_trend_frame,
)


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
