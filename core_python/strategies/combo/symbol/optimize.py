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

from ..config import (
    OPTIMIZATION,
    TIMEFRAME,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_params,
    get_symbol_search_space,
)
from ..execution import backtest_fast
from ..signals import add_combo_indicators
from .backtest import load_backtest_full


PARAMETER_KEYS = ("ktp", "x", "min_rr")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return float(value)


def build_parameter_grid(
    symbol_key: str,
    search_space: dict[str, list[Any]] | None = None,
    broker_profile: str | None = None,
) -> list[dict[str, Any]]:
    """Expand a compact search space into a list of candidate parameter dicts."""
    space = search_space or get_symbol_search_space(symbol_key, broker_profile=broker_profile)
    missing = [key for key in PARAMETER_KEYS if key not in space]
    if missing:
        raise KeyError(f"Search space is missing required keys: {missing}")
    empty = [key for key in PARAMETER_KEYS if not space[key]]
    if empty:
        raise ValueError(f"Search space contains empty parameter lists: {empty}")
    return [
        dict(zip(PARAMETER_KEYS, combo))
        for combo in product(*(space[k] for k in PARAMETER_KEYS))
    ]


def run_symbol_grid_search(
    symbol_key: str,
    *,
    date_from: str | None,
    date_to: str | None,
    init_eq: float = 100_000.0,
    account_mode: str = "standard",
    tf: str = TIMEFRAME,
    max_bars: int | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    search_space: dict[str, list[Any]] | None = None,
    broker_profile: str | None = None,
    allow_full_history: bool = False,
) -> pd.DataFrame:
    """Run a compact grid search for one symbol using backtest_fast().

    Review note
    -----------
    We precompute the grid once and group by `ma_period` so indicator generation
    is reused across candidates that share the same MA length.
    """
    if date_from is None and date_to is None and not allow_full_history:
        raise ValueError(
            "run_symbol_grid_search() requires at least one explicit date bound. "
            "Pass date_from/date_to for an in-sample window, or set "
            "allow_full_history=True when a full-history sweep is intentional."
        )

    symbol_cfg = get_symbol_params(symbol_key, broker_profile=broker_profile)
    raw_df = load_backtest_full(
        symbol_cfg["symbol_id"],
        tf=tf,
        max_bars=max_bars or OPTIMIZATION["symbol"]["max_bars"],
    )
    if raw_df.empty:
        return pd.DataFrame()

    effective_date_from = date_from or str(raw_df.index.min())
    effective_date_to = date_to or str(raw_df.index.max())

    results: list[dict[str, Any]] = []
    candidate_grid = build_parameter_grid(
        symbol_key,
        search_space,
        broker_profile=broker_profile,
    )
    strategy_cfg = get_account_settings(account_mode, strategy_overrides)
    cost_cfg = get_cost_settings(symbol_key, costs, broker_profile=broker_profile)

    base_ind_params = {**get_indicator_params(), **(indicator_overrides or {})}

    # MA cố định 20 — chỉ tính indicators một lần cho toàn bộ grid
    df_ind = add_combo_indicators(raw_df.copy(), {**base_ind_params, "MA_PERIOD": 20})

    for candidate in candidate_grid:
        cfg = {
            **symbol_cfg,
            "ktp": float(candidate["ktp"]),
            "x": float(candidate["x"]),
        }
        local_strategy = {
            **strategy_cfg,
            "min_rr": _optional_float(candidate["min_rr"]),
        }
        metrics = backtest_fast(
            symbol_key,
            df_ind,
            cfg,
            ktp=cfg["ktp"],
            x_actual=cfg["x"],
            trailing_act=float(strategy_cfg.get("trailing_activation", 1.0)),
            date_from=effective_date_from,
            date_to=effective_date_to,
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
    df_results = pd.DataFrame(results)
    df_results["ranking_basis"] = "fast_approximation"
    df_results["grid_search_data_start"] = pd.Timestamp(effective_date_from)
    df_results["grid_search_data_end"] = pd.Timestamp(effective_date_to)
    sort_cols = [c for c in [score_col, "score", "pf", "ret"] if c in df_results.columns]
    return df_results.sort_values(by=sort_cols, ascending=False, ignore_index=True)
