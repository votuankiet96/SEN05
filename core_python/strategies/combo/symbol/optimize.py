"""Symbol-level optimization helpers for Combo.

Architecture role
-----------------
- This file explores parameter candidates for one symbol only.
- It deliberately uses `shared.execution.backtest_fast()` so optimization stays
  lightweight and leaves full trade-log validation to symbol backtests.
"""

from __future__ import annotations

from itertools import product
from typing import Any

import pandas as pd

from shared.execution import backtest_fast

from ..config import (
    OPTIMIZATION,
    TIMEFRAME,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_params,
    get_symbol_search_space,
)
from ..logic import add_combo_indicators
from .backtest import load_backtest_full


def build_parameter_grid(
    symbol_key: str,
    search_space: dict[str, list[Any]] | None = None,
    broker_profile: str | None = None,
) -> list[dict[str, Any]]:
    """Expand a compact search space into a list of candidate parameter dicts."""
    space = search_space or get_symbol_search_space(symbol_key, broker_profile=broker_profile)
    keys = ("ktp", "x", "trailing_activation", "ma_period")
    return [
        dict(zip(keys, combo))
        for combo in product(*(space[k] for k in keys))
    ]


def run_symbol_grid_search(
    symbol_key: str,
    *,
    date_from: str,
    date_to: str,
    init_eq: float = 100_000.0,
    account_mode: str = "standard",
    tf: str = TIMEFRAME,
    max_bars: int | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    search_space: dict[str, list[Any]] | None = None,
    broker_profile: str | None = None,
) -> pd.DataFrame:
    """Run a compact grid search for one symbol using backtest_fast().

    Review note
    -----------
    We precompute the grid once and group by `ma_period` so indicator generation
    is reused across candidates that share the same MA length.
    """
    symbol_cfg = get_symbol_params(symbol_key, broker_profile=broker_profile)
    raw_df = load_backtest_full(
        symbol_cfg["symbol_id"],
        tf=tf,
        max_bars=max_bars or OPTIMIZATION["symbol"]["max_bars"],
    )

    results: list[dict[str, Any]] = []
    candidate_grid = build_parameter_grid(
        symbol_key,
        search_space,
        broker_profile=broker_profile,
    )
    strategy_cfg = get_account_settings(account_mode, strategy_overrides)
    cost_cfg = get_cost_settings(symbol_key, costs, broker_profile=broker_profile)
    if indicator_overrides and "MIN_RR" in indicator_overrides:
        strategy_cfg["min_rr"] = float(indicator_overrides["MIN_RR"])

    base_ind_params = {**get_indicator_params(), **(indicator_overrides or {})}

    for ma_period in sorted({int(row["ma_period"]) for row in candidate_grid}):
        df_ind = add_combo_indicators(raw_df.copy(), {**base_ind_params, "MA_PERIOD": ma_period})
        ma_candidates = [row for row in candidate_grid if int(row["ma_period"]) == ma_period]
        for candidate in ma_candidates:
            cfg = {
                **symbol_cfg,
                "ktp": float(candidate["ktp"]),
                "x": float(candidate["x"]),
                "ma_period": int(candidate["ma_period"]),
                "trailing_activation": float(candidate["trailing_activation"]),
            }
            local_strategy = {
                **strategy_cfg,
                "trailing_activation": cfg["trailing_activation"],
            }
            metrics = backtest_fast(
                symbol_key,
                df_ind,
                cfg,
                ktp=cfg["ktp"],
                x_actual=cfg["x"],
                trailing_act=cfg["trailing_activation"],
                date_from=date_from,
                date_to=date_to,
                init_eq=init_eq,
                strategy=local_strategy,
                costs=cost_cfg,
                tf=tf,
            )
            results.append({
                **candidate,
                **metrics,
            })

    if not results:
        return pd.DataFrame()

    score_col = OPTIMIZATION["symbol"]["score_column"]
    return pd.DataFrame(results).sort_values(
        by=[score_col, "score", "pf", "ret"],
        ascending=False,
        ignore_index=True,
    )
