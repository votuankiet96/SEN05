"""Registry for dashboard payload builders.

Strategy math lives under ``core_python.strategies``. This registry is the
separate display layer: it maps a computed strategy result to the chart layout
that app.js should render.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from core_python.chart.payloads.layouts import combo, ma_cross

PayloadBuilder = Callable[..., dict[str, Any]]

PAYLOAD_BUILDERS: dict[str, PayloadBuilder] = {
    "combo": combo.build_payload,
    "ma_cross": ma_cross.build_payload,
}


def get_payload_builder(strategy: str) -> PayloadBuilder:
    """Return the dashboard payload builder for a strategy key."""
    key = str(strategy or "").strip().lower()
    if key not in PAYLOAD_BUILDERS:
        raise KeyError(f"No dashboard payload builder registered for strategy '{strategy}'.")
    return PAYLOAD_BUILDERS[key]


def build_chart_payload(
    df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch payload building to the strategy-specific dashboard layout."""
    builder = get_payload_builder(strategy)
    return builder(
        df,
        strategy=strategy,
        strategy_label=strategy_label,
        symbol=symbol,
        tf=tf,
        bars=bars,
        params=params,
    )
