"""Source-agnostic strategy runner for OG.

This module is the shared core between historical/backtest workflows and live
order-generation workflows. It runs a registered strategy on OHLCV data already
loaded by an adapter. It does not know SQL, Redis, Flask, CSV, or systemd.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from og_core.strategies.registry import StrategySpec, get_strategy

MULTI_TIMEFRAME_STRATEGIES = {"ai_trend", "knn_combo"}
OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class StrategyRunResult:
    """Computed strategy output from a source-agnostic run."""

    strategy: str
    strategy_label: str
    symbol: str
    tf: str
    bars: int
    params: dict[str, Any]
    enriched: pd.DataFrame
    layout: str = "single"
    trend_frame: pd.DataFrame | None = None
    trend_tf: str | None = None


def run_strategy_on_bars(
    strategy_key: str,
    *,
    symbol: str,
    tf: str,
    bars: pd.DataFrame,
    params: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> StrategyRunResult:
    """Run a single-timeframe strategy on an existing OHLCV DataFrame."""
    spec = get_strategy(strategy_key)
    if spec.key in MULTI_TIMEFRAME_STRATEGIES:
        raise ValueError(f"{spec.key}: requires multiple timeframes; use a multi-frame runner")

    selected_symbol = str(symbol).strip().upper()
    selected_tf = str(tf).strip().upper()
    resolved_params = params if params is not None else spec.normalize_params(overrides, selected_symbol)
    if spec.key == "combo" and resolved_params.get("HTF_TREND_ENABLED", False):
        raise ValueError("combo with HTF_TREND_ENABLED=True requires a multi-frame runner")

    bars = normalize_ohlcv_frame(bars)
    if bars.empty:
        enriched = empty_ohlcv_frame()
        enriched["signal"] = pd.Series(dtype="int64")
        return StrategyRunResult(
            strategy=spec.key,
            strategy_label=spec.label,
            symbol=selected_symbol,
            tf=selected_tf,
            bars=0,
            params=resolved_params,
            enriched=enriched,
        )

    enriched = run_single_frame_pipeline(spec, bars, resolved_params, selected_symbol)
    return StrategyRunResult(
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=selected_symbol,
        tf=selected_tf,
        bars=len(enriched),
        params=resolved_params,
        enriched=enriched,
    )


def run_single_frame_pipeline(
    spec: StrategySpec,
    bars: pd.DataFrame,
    params: dict[str, Any],
    symbol: str,
) -> pd.DataFrame:
    """Run normalize-independent indicator/signal/level stages on one frame."""
    with_indicators = spec.add_indicators(bars, params)
    with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
    return spec.add_levels(with_signals, params, symbol)


def normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted, numeric OHLCV frame accepted by all single-frame strategies."""
    missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    out = df.loc[:, OHLCV_COLUMNS].copy()
    out["bartime"] = pd.to_datetime(out["bartime"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["bartime", "open", "high", "low", "close"])
    out = out.drop_duplicates(subset=["bartime"], keep="last")
    return out.sort_values("bartime").reset_index(drop=True)


def empty_ohlcv_frame() -> pd.DataFrame:
    """Build an empty frame with the standard OHLCV columns."""
    return pd.DataFrame(
        {
            "bartime": pd.Series(dtype="datetime64[ns]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
        }
    )
