"""Strategy registry for the simplified chart server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from simplified_core_python.strategies.combo import config as combo_config
from simplified_core_python.strategies.combo.levels import add_combo_levels
from simplified_core_python.strategies.combo.signals import (
    add_combo_indicators,
    detect_combo_signals,
)
from simplified_core_python.strategies.ma_cross import config as ma_cross_config
from simplified_core_python.strategies.ma_cross.levels import add_ma_cross_levels
from simplified_core_python.strategies.ma_cross.signals import (
    add_ma_cross_indicators,
    detect_ma_cross_signals,
)


@dataclass(frozen=True)
class StrategySpec:
    key: str
    label: str
    default_params: dict[str, Any]
    param_fields: list[dict[str, Any]]
    normalize_params: Callable[[dict[str, Any] | None, str | None], dict[str, Any]]
    add_indicators: Callable[[pd.DataFrame, dict[str, Any] | None], pd.DataFrame]
    detect_signals: Callable[..., pd.DataFrame]
    add_levels: Callable[[pd.DataFrame, dict[str, Any], str | None], pd.DataFrame]


STRATEGIES: dict[str, StrategySpec] = {
    "combo": StrategySpec(
        key="combo",
        label="Combo",
        default_params=combo_config.DEFAULT_PARAMS,
        param_fields=combo_config.PARAM_FIELDS,
        normalize_params=combo_config.normalize_params,
        add_indicators=add_combo_indicators,
        detect_signals=detect_combo_signals,
        add_levels=add_combo_levels,
    ),
    "ma_cross": StrategySpec(
        key="ma_cross",
        label="MA Cross",
        default_params=ma_cross_config.DEFAULT_PARAMS,
        param_fields=ma_cross_config.PARAM_FIELDS,
        normalize_params=ma_cross_config.normalize_params,
        add_indicators=add_ma_cross_indicators,
        detect_signals=detect_ma_cross_signals,
        add_levels=add_ma_cross_levels,
    ),
}


def get_strategy(key: str) -> StrategySpec:
    """Return a registered strategy by key."""
    normalized = str(key).strip().lower()
    if normalized not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{key}'. Available: {', '.join(STRATEGIES)}")
    return STRATEGIES[normalized]

