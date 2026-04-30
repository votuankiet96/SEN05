from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..config import TIMEFRAMES
from .backtest import run_symbol_backtest
from .optimize import metric_row, metrics_frame


def run_timeframe_matrix(
    symbol: str,
    *,
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    timeframes: Iterable[str] = TIMEFRAMES,
    max_bars: int = 50_000,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: Mapping[str, Any] | None = None,
    strategy_overrides: Mapping[str, Any] | None = None,
    costs: Mapping[str, Any] | None = None,
    broker_profile: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    for tf in timeframes:
        result = run_symbol_backtest(
            symbol,
            init_eq=initial_balance,
            account_mode=account_mode,
            tf=str(tf).upper(),
            date_from=date_from,
            date_to=date_to,
            max_bars=max_bars,
            indicator_overrides=dict(indicator_overrides or {}),
            strategy_overrides=dict(strategy_overrides or {}),
            costs=dict(costs or {}),
            broker_profile=broker_profile,
        )
        results[str(tf).upper()] = result
        rows.append(
            metric_row(
                result,
                symbol=symbol,
                timeframe=str(tf).upper(),
                params=indicator_overrides,
                strategy_overrides=strategy_overrides,
            )
        )
    return metrics_frame(rows), results


__all__ = ["run_timeframe_matrix"]
