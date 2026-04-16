# =============================================================================
# strategies/combo/__init__.py  —  Public API của Combo v2 strategy
# =============================================================================
from .backtest_engine import (
    load_backtest_data,
    load_backtest_full,
    # Signal layer
    add_backtest_indicators,
    add_combo_indicators,
    detect_signals,
    detect_combo_signals,
    session_mask,
    build_raw_signal_masks,
    resolve_trade_hit,
    build_signal_record,
    scan_signals_reversal,
    # Execution layer (from shared)
    backtest_symbol,
    backtest_fast,
    # Walk-forward
    walk_forward_backtest,
    check_plateau_stability,
    # Metrics (from shared)
    calc_metrics,
    in_bao_cao,
)
from .strategy_config import (
    STRATEGY,
    SYMBOLS,
    TIMEFRAME,
    get_indicator_params,
    get_symbol_params,
    get_symbol_ktp,
)
