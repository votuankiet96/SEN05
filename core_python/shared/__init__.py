"""Shared core utilities used by every strategy package."""

from .contracts import (
    PortfolioBacktestResult,
    PortfolioWindowResult,
    SymbolBacktestResult,
)
from .broker import audit_symbol_specs, merge_broker_profile, round_lot_size
from .instruments import (
    BROKER_PROFILES,
    DEFAULT_BROKER_PROFILE,
    DEFAULT_COSTS,
    SYMBOLS,
    get_base_symbol_params,
    get_broker_profile,
    get_cost_settings,
    get_symbol_config,
)
from .data import (
    load_backtest_ohlcv,
    load_backtest_ohlcv_full,
    load_chart_candles,
    load_scan_ohlcv,
    load_symbols,
    load_timeframes,
)
from .execution import (
    backtest_fast,
    backtest_symbol,
    build_pending_order,
    calc_dynamic_slippage,
)
from .metrics import _bars_per_year, calc_metrics, in_bao_cao
from .monte_carlo import plot_monte_carlo, run_monte_carlo
from .portfolio import (
    build_combined_equity,
    calc_portfolio_metrics,
    check_portfolio_ftmo,
    combine_trade_logs,
    equity_frame_from_dict,
)
from .theme import (
    DARK,
    EQUITY_COLORS,
    METRIC_CSS,
    METRICS_FMT,
    NUM_FMT,
    SIGNAL,
    color_pf,
    dark_table_props,
    setup_dark_axes,
    setup_dark_figure,
    style_combined_row,
    style_reversal_row,
    style_signal_row,
)
