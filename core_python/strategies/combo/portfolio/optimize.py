"""Portfolio-level optimization helpers for Combo.

Architecture role
-----------------
- This layer searches over combinations of per-symbol winners.
- It should stay separate from symbol optimization so we do not mix up
  "does this symbol have edge?" with "how do several edges behave together?"
"""

from __future__ import annotations

from itertools import product
from typing import Any

import pandas as pd

from .backtest import run_portfolio_backtest


def calc_portfolio_combo_metrics(
    combo: dict[str, dict[str, Any]],
    *,
    initial_balance: float = 100_000.0,
    account_mode: str = "standard",
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    max_bars: int = 60000,
) -> dict[str, Any]:
    """Evaluate one portfolio candidate map {symbol -> params}."""
    result = run_portfolio_backtest(
        symbol_keys=list(combo.keys()),
        initial_balance=initial_balance,
        account_mode=account_mode,
        date_from=date_from,
        date_to=date_to,
        indicator_overrides=indicator_overrides,
        symbol_overrides=combo,
        strategy_overrides=strategy_overrides,
        costs=costs,
        broker_profile=broker_profile,
        max_bars=max_bars,
    )
    return {
        "symbols": list(combo.keys()),
        "params": combo,
        **result.metrics,
    }


def grid_search_portfolio(
    candidate_map: dict[str, list[dict[str, Any]]],
    *,
    initial_balance: float = 100_000.0,
    account_mode: str = "standard",
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    max_bars: int = 60000,
) -> pd.DataFrame:
    """Grid search portfolio candidate combinations assembled from per-symbol winners.

    Review warning
    --------------
    This search grows multiplicatively with the number of candidates per symbol.
    Keep `candidate_map` compact, or add pruning before using this on a large
    universe.
    """
    if not candidate_map:
        return pd.DataFrame()

    symbols = list(candidate_map.keys())
    rows = []
    for combo_values in product(*(candidate_map[sym] for sym in symbols)):
        combo = dict(zip(symbols, combo_values))
        metrics = calc_portfolio_combo_metrics(
            combo,
            initial_balance=initial_balance,
            account_mode=account_mode,
            date_from=date_from,
            date_to=date_to,
            indicator_overrides=indicator_overrides,
            strategy_overrides=strategy_overrides,
            costs=costs,
            broker_profile=broker_profile,
            max_bars=max_bars,
        )
        rows.append(metrics)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        by=["sharpe", "profit_factor", "total_return"],
        ascending=False,
        ignore_index=True,
    )
