"""MA Cross strategy package."""

import sys

from . import config as _config
from . import research as _research
from . import signals as _signals
from .config import (
    STRATEGY,
    TIMEFRAME,
    TIMEFRAMES,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_params,
    summary,
)
from .signals import add_ma_cross_indicators
from .orders import create_market_next_open_intent, create_order_intent_from_signal, resolve_exit_policy
from .export import build_ctrader_signal_export, export_ctrader_csv, validate_ctrader_export
from .signals import detect_ma_cross_signals, session_mask
from .universe import MA_CROSS_SYMBOL_KEYS, get_ma_cross_universe_metadata
from .workflow import (
    rank_grid,
    run_portfolio_backtest,
    run_symbol_backtest,
    run_symbol_backtest_on_frame,
    run_symbol_grid_search,
    run_timeframe_matrix,
    select_top_candidates,
    simple_walkforward,
)

sys.modules[__name__ + ".params"] = _config
sys.modules[__name__ + ".model"] = _signals
sys.modules[__name__ + ".research_utils"] = _research

__all__ = [
    "MA_CROSS_SYMBOL_KEYS",
    "STRATEGY",
    "TIMEFRAME",
    "TIMEFRAMES",
    "add_ma_cross_indicators",
    "create_market_next_open_intent",
    "create_order_intent_from_signal",
    "build_ctrader_signal_export",
    "detect_ma_cross_signals",
    "get_account_settings",
    "get_cost_settings",
    "get_indicator_params",
    "get_ma_cross_universe_metadata",
    "get_symbol_params",
    "rank_grid",
    "resolve_exit_policy",
    "export_ctrader_csv",
    "run_portfolio_backtest",
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
