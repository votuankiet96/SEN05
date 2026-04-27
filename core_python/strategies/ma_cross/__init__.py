"""MA Cross strategy package."""

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
from .logic import add_ma_cross_indicators, detect_ma_cross_signals, session_mask
from .symbol.backtest import run_symbol_backtest, run_symbol_backtest_on_frame

__all__ = [
    "STRATEGY",
    "TIMEFRAME",
    "TIMEFRAMES",
    "add_ma_cross_indicators",
    "detect_ma_cross_signals",
    "get_account_settings",
    "get_cost_settings",
    "get_indicator_params",
    "get_symbol_params",
    "run_symbol_backtest",
    "run_symbol_backtest_on_frame",
    "session_mask",
    "summary",
]
