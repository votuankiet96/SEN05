"""Public API for the Combo strategy package.

Architecture role
-----------------
- This file is the clean import surface for every consumer of the Combo strategy.
- External callers should prefer importing from `strategies.combo` rather than
  reaching into deep files unless they need a very specific workflow.

Package layout
--------------
- `config.py`     : strategy/account/symbol parameters
- `logic.py`      : indicator enrichment and signal rules
- `scanner.py`    : visual scan pipeline
- `symbol/*`      : single-symbol backtest / optimize / walk-forward
- `portfolio/*`   : portfolio-level orchestration
"""

from .config import (
    ACCOUNT_MODES,
    DEFAULT_COSTS,
    DEFAULT_N_BARS,
    INDICATOR_COLS,
    OPTIMIZATION,
    SCANNER_DEFAULTS,
    STRATEGY,
    SYMBOLS,
    TIMEFRAME,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_config,
    get_symbol_ktp,
    get_symbol_params,
    get_symbol_search_space,
    summary,
)
from .logic import (
    add_backtest_indicators,
    add_combo_indicators,
    build_raw_signal_masks,
    build_signal_record,
    detect_combo_signals,
    detect_signals,
    resolve_trade_hit,
    scan_signals_reversal,
    session_mask,
)
from .portfolio import (
    calc_portfolio_combo_metrics,
    compare_account_modes,
    grid_search_portfolio,
    run_portfolio_backtest,
    walk_forward_portfolio,
)
from .scanner import (
    build_reversal_figure,
    calc_reversal_stats,
    prepare_data,
    prepare_scan_data,
    run_multi_reversal_scan,
    run_reversal_scan,
)
from .symbol import (
    build_parameter_grid,
    build_symbol_signal_frame,
    check_plateau_stability,
    load_backtest_data,
    load_backtest_full,
    run_symbol_backtest,
    run_symbol_backtest_on_frame,
    run_symbol_grid_search,
    walk_forward_backtest,
)
