"""MA Cross strategy package."""

import sys

from . import config as _config
from . import signals as _signals

# --- Config ---
from .config import (
    BASKET,
    FILTERS,
    STRATEGY,
    TIMEFRAME,
    TIMEFRAMES,
    get_account_settings,
    get_basket_settings,
    get_cost_settings,
    get_filter_settings,
    get_indicator_params,
    get_symbol_params,
    summary,
)

# --- Signals & Orders ---
from .signals import add_ma_cross_indicators, detect_ma_cross_signals, session_mask
from .filters import add_entry_filter_columns
from .orders import (
    create_market_next_open_intent,
    create_order_intent_from_signal,
    resolve_exit_policy,
)
from .basket import BasketOrder, BasketSnapshot, basket_snapshot, next_linear_lot

# --- Universe ---
from .universe import MA_CROSS_SYMBOL_KEYS, get_ma_cross_universe_metadata

# --- Execution boundary ---
from .execution import backtest_basket_reversal_symbol, backtest_market_symbol

# --- Export ---
from .export import build_ctrader_signal_export, export_ctrader_csv, validate_ctrader_export

# --- Symbol workflows ---
from .symbol.backtest import (
    build_symbol_signal_frame,
    load_backtest_data,
    load_backtest_full,
    run_symbol_backtest,
    run_symbol_backtest_on_frame,
)
from .symbol.matrix import run_timeframe_matrix
from .symbol.optimize import metric_row, metrics_frame, run_symbol_grid_search
from .symbol.selection import rank_grid, select_top_candidates
from .symbol.walkforward import simple_walkforward

# --- Portfolio workflows ---
from .portfolio.backtest import run_portfolio_backtest
from .portfolio.optimize import run_portfolio_grid_search
from .portfolio.walkforward import portfolio_walkforward

# Legacy aliases — tests dùng ma_cross.params / ma_cross.model
sys.modules[__name__ + ".params"] = _config
sys.modules[__name__ + ".model"] = _signals

__all__ = [
    "MA_CROSS_SYMBOL_KEYS",
    "BASKET",
    "BasketOrder",
    "BasketSnapshot",
    "FILTERS",
    "STRATEGY",
    "TIMEFRAME",
    "TIMEFRAMES",
    "add_ma_cross_indicators",
    "add_entry_filter_columns",
    "backtest_basket_reversal_symbol",
    "backtest_market_symbol",
    "basket_snapshot",
    "build_ctrader_signal_export",
    "build_symbol_signal_frame",
    "create_market_next_open_intent",
    "create_order_intent_from_signal",
    "detect_ma_cross_signals",
    "export_ctrader_csv",
    "get_account_settings",
    "get_basket_settings",
    "get_cost_settings",
    "get_filter_settings",
    "get_indicator_params",
    "get_ma_cross_universe_metadata",
    "get_symbol_params",
    "load_backtest_data",
    "load_backtest_full",
    "metric_row",
    "metrics_frame",
    "next_linear_lot",
    "portfolio_walkforward",
    "rank_grid",
    "resolve_exit_policy",
    "run_portfolio_backtest",
    "run_portfolio_grid_search",
    "run_symbol_backtest",
    "run_symbol_backtest_on_frame",
    "run_symbol_grid_search",
    "run_timeframe_matrix",
    "select_top_candidates",
    "session_mask",
    "simple_walkforward",
    "summary",
    "validate_ctrader_export",
]
