"""Dashboard layout payload for the Combo strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.chart.payloads.common import (
    build_strategy_payload,
    signal_rows,
    signal_table,
)
from core_python.chart.payloads.helpers import histogram_points, series_points


def build_payload(
    df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Build Combo's dashboard layout: OHLC + MA, MACD histogram, ATR."""
    signals = signal_rows(df)

    overlays: list[dict[str, Any]] = []
    if params.get("SHOW_MA", True) and "ma" in df:
        overlays.append(
            {
                "key": "ma",
                "label": f"MA {params['MA_PERIOD']}",
                "color": "#f59e0b",
                "data": series_points(df, "ma"),
            }
        )

    panels: list[dict[str, Any]] = []
    if params.get("SHOW_MACD", True) and "macd_h" in df:
        panels.append(
            {
                "key": "macd",
                "label": "MACD Histogram",
                "type": "histogram",
                "data": histogram_points(df, "macd_h"),
            }
        )
    if params.get("SHOW_ATR", True) and "atr" in df:
        panels.append(
            {
                "key": "atr",
                "label": f"ATR {params['ATR_PERIOD']}",
                "type": "line",
                "color": "#a855f7",
                "data": series_points(df, "atr"),
            }
        )

    return build_strategy_payload(
        df,
        strategy=strategy,
        strategy_label=strategy_label,
        symbol=symbol,
        tf=tf,
        bars=bars,
        params=params,
        overlays=overlays,
        panels=panels,
        signal_table_rows=signal_table(signals, (("ma", "ma", 5), ("macd_h", "macd_h", 5))),
        signals=signals,
    )
