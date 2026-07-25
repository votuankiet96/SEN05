"""Dashboard layout payload for the MA Cross strategy."""

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
    """Build MA Cross layout: OHLC + SMAs, MACD Histogram and ATR."""
    signals = signal_rows(df)

    overlays: list[dict[str, Any]] = []
    if params.get("SHOW_MA", True):
        overlays.extend(
            [
                {
                    "key": "fast_ma",
                    "label": f"Fast {params['FAST_MA']}",
                    "color": "#38bdf8",
                    "data": series_points(df, "fast_ma"),
                },
                {
                    "key": "slow_ma",
                    "label": f"Slow {params['SLOW_MA']}",
                    "color": "#f59e0b",
                    "data": series_points(df, "slow_ma"),
                },
            ]
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
        signal_table_rows=signal_table(
            signals,
            (
                ("fast_ma", "fast_ma", 5),
                ("slow_ma", "slow_ma", 5),
                ("macd_h", "macd_h", 5),
                ("ma_gap_atr", "ma_gap_atr", 4),
            ),
        ),
        signals=signals,
    )
