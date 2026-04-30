from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

from .backtest import run_symbol_backtest
from .optimize import metric_row, metrics_frame


def simple_walkforward(
    symbol: str,
    *,
    tf: str = "M30",
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    windows: Iterable[tuple[str, str, str, str]],
    indicator_overrides: Mapping[str, Any] | None = None,
    strategy_overrides: Mapping[str, Any] | None = None,
    max_bars: int = 80_000,
    costs: Mapping[str, Any] | None = None,
    broker_profile: str | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for idx, (is_from, is_to, oos_from, oos_to) in enumerate(windows, start=1):
        for phase, start, end in [("IS", is_from, is_to), ("OOS", oos_from, oos_to)]:
            result = run_symbol_backtest(
                symbol,
                init_eq=initial_balance,
                account_mode=account_mode,
                tf=tf,
                date_from=start,
                date_to=end,
                max_bars=max_bars,
                indicator_overrides=dict(indicator_overrides or {}),
                strategy_overrides=dict(strategy_overrides or {}),
                costs=dict(costs or {}),
                broker_profile=broker_profile,
            )
            row = metric_row(
                result,
                symbol=symbol,
                timeframe=tf,
                params=indicator_overrides,
                strategy_overrides=strategy_overrides,
            )
            row.update({"window": idx, "phase": phase, "date_from": start, "date_to": end})
            rows.append(row)
    return metrics_frame(rows)


__all__ = ["simple_walkforward"]
