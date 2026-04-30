"""Public API for the Combo strategy package.

Architecture role
-----------------
- This file is the clean import surface for every consumer of the Combo strategy.
- External callers should prefer importing from `strategies.combo` rather than
  reaching into deep files unless they need a very specific workflow.

Package layout
--------------
- `config.py`     : canonical Combo parameter source-of-truth
- `universe.py`   : Combo-owned symbol universe backed by shared metadata
- `signals.py`    : Combo indicators, signals, scanner rules
- `orders.py`     : Combo order intent generation
- `symbol/`       : symbol backtest, optimization, and walk-forward workflows
- `portfolio/`    : portfolio backtest, optimization, and walk-forward workflows
- `chart.py`      : visual scan pipeline
- `export.py`     : cTrader signal/order-plan exports
"""

import sys

from . import config as _config
from . import research_utils as _research_utils
from . import signals as _signals
from .config import (
    ACCOUNT_MODES,
    BROKER_PROFILES,
    COMBO_SYMBOL_PARAMS,
    DEFAULT_BROKER_PROFILE,
    DEFAULT_COSTS,
    DEFAULT_N_BARS,
    INDICATOR_COLS,
    OPTIMIZATION,
    SCANNER_DEFAULTS,
    STRATEGY,
    SYMBOLS,
    TIMEFRAME,
    TIMEFRAMES,
    get_account_settings,
    get_broker_profile,
    get_combo_symbol_params,
    get_cost_settings,
    get_indicator_params,
    get_symbol_config,
    get_symbol_ktp,
    get_symbol_params,
    get_symbol_search_space,
    summary,
    validate_config,
)
from .signals import add_backtest_indicators, add_combo_indicators
from .signals import (
    build_fast_backtest_signal_masks,
    build_raw_signal_masks,
    build_signal_record,
    resolve_trade_hit,
    scan_signals_reversal,
)
from .orders import create_order_intent_from_signal, create_pending_breakout_intent, resolve_exit_policy
from .export import build_ctrader_signal_export, export_ctrader_csv, validate_ctrader_export
from .signals import detect_combo_signals, detect_signals, session_mask
from .portfolio.backtest import compare_account_modes, run_portfolio_backtest
from .portfolio.optimize import calc_portfolio_combo_metrics, grid_search_portfolio
from .portfolio.walkforward import walk_forward_portfolio
from .symbol.backtest import (
    build_symbol_signal_frame,
    load_backtest_data,
    load_backtest_full,
    run_symbol_backtest,
    run_symbol_backtest_on_frame,
)
from .symbol.optimize import build_parameter_grid, run_symbol_grid_search
from .symbol.walkforward import check_plateau_stability, walk_forward_backtest
from .scanner import (
    build_reversal_figure,
    calc_reversal_stats,
    prepare_data,
    prepare_scan_data,
    run_multi_reversal_scan,
    run_reversal_scan,
)

sys.modules[__name__ + ".params"] = _config
sys.modules[__name__ + ".model"] = _signals
sys.modules[__name__ + ".research_utils"] = _research_utils

__all__ = [
    "ACCOUNT_MODES",
    "BROKER_PROFILES",
    "COMBO_SYMBOL_PARAMS",
    "DEFAULT_BROKER_PROFILE",
    "DEFAULT_COSTS",
    "DEFAULT_N_BARS",
    "INDICATOR_COLS",
    "OPTIMIZATION",
    "SCANNER_DEFAULTS",
    "STRATEGY",
    "SYMBOLS",
    "TIMEFRAME",
    "TIMEFRAMES",
    "get_account_settings",
    "get_broker_profile",
    "get_combo_symbol_params",
    "get_cost_settings",
    "get_indicator_params",
    "get_symbol_config",
    "get_symbol_ktp",
    "get_symbol_params",
    "get_symbol_search_space",
    "summary",
    "validate_config",
    "add_backtest_indicators",
    "add_combo_indicators",
    "build_raw_signal_masks",
    "build_fast_backtest_signal_masks",
    "build_signal_record",
    "create_order_intent_from_signal",
    "create_pending_breakout_intent",
    "detect_combo_signals",
    "detect_signals",
    "resolve_trade_hit",
    "resolve_exit_policy",
    "scan_signals_reversal",
    "session_mask",
    "calc_portfolio_combo_metrics",
    "compare_account_modes",
    "grid_search_portfolio",
    "run_portfolio_backtest",
    "walk_forward_portfolio",
    "build_reversal_figure",
    "calc_reversal_stats",
    "prepare_data",
    "prepare_scan_data",
    "run_multi_reversal_scan",
    "run_reversal_scan",
    "build_parameter_grid",
    "build_ctrader_signal_export",
    "build_symbol_signal_frame",
    "check_plateau_stability",
    "load_backtest_data",
    "load_backtest_full",
    "run_symbol_backtest",
    "run_symbol_backtest_on_frame",
    "run_symbol_grid_search",
    "export_ctrader_csv",
    "validate_ctrader_export",
    "walk_forward_backtest",
]
