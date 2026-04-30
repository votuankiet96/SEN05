"""MA Cross workflow API."""

from __future__ import annotations

from .symbol.backtest import (
    build_symbol_signal_frame,
    load_backtest_data,
    load_backtest_full,
    run_symbol_backtest,
    run_symbol_backtest_on_frame,
)
from .symbol.matrix import run_timeframe_matrix
from .symbol.optimize import rank_grid, run_symbol_grid_search, select_top_candidates
from .symbol.walkforward import simple_walkforward
from .portfolio.backtest import run_portfolio_backtest

__all__ = [
    "build_symbol_signal_frame",
    "load_backtest_data",
    "load_backtest_full",
    "rank_grid",
    "run_portfolio_backtest",
    "run_symbol_backtest",
    "run_symbol_backtest_on_frame",
    "run_symbol_grid_search",
    "run_timeframe_matrix",
    "select_top_candidates",
    "simple_walkforward",
]
