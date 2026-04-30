"""Shared core utilities used by every strategy package."""

import sys

from . import analytics as _analytics
from . import market as _market
from . import reporting as _reporting
from .contracts import (
    Direction,
    FillEvent,
    MarketOrderIntent,
    OrderIntent,
    OrderKind,
    PendingKind,
    PendingOrderIntent,
    Position,
    PortfolioModel,
    PortfolioBacktestResult,
    PortfolioWindowResult,
    SymbolBacktestResult,
    TradeEvent,
    intent_to_legacy_pending,
)
from .market import audit_symbol_specs, merge_broker_profile, round_lot_size
from .market import (
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
from .execution.primitives import calc_dynamic_slippage
from .analytics import _bars_per_year, calc_metrics, calc_trade_level_return_stats, in_bao_cao
from .analytics import plot_monte_carlo, run_monte_carlo
from .analytics import (
    build_combined_equity,
    calc_portfolio_metrics,
    check_portfolio_ftmo,
    combine_trade_logs,
    equity_frame_from_dict,
)
from .sessions import detect_time_gaps, normalize_datetime_index_utc, session_mask_utc
from .timeframes import (
    TIMEFRAME_MINUTES,
    bars_per_year,
    expected_timedelta_for_tf,
    normalize_timeframe_code,
)
from .reporting import (
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

sys.modules[__name__ + ".broker"] = _market
sys.modules[__name__ + ".instruments"] = _market
sys.modules[__name__ + ".metrics"] = _analytics
sys.modules[__name__ + ".portfolio"] = _analytics
sys.modules[__name__ + ".monte_carlo"] = _analytics
sys.modules[__name__ + ".theme"] = _reporting
