# =============================================================================
# strategies/ma_cross/backtest_engine.py  —  Data loading + re-exports
# =============================================================================
"""Entry point cho notebook backtest của MA Cross strategy.

Cách dùng trong notebook:
    from strategies.ma_cross.backtest_engine import (
        load_backtest_data,
        add_ma_cross_indicators,
        detect_ma_cross_signals,
        backtest_symbol,
        calc_metrics,
    )
"""
# Shared data loading (dùng chung DB adapter với combo)
from shared.data import load_backtest_ohlcv, load_backtest_ohlcv_full

# Shared execution + metrics
from shared.execution import backtest_fast, backtest_symbol
from shared.metrics import calc_metrics, in_bao_cao
from shared.monte_carlo import plot_monte_carlo, run_monte_carlo

# Strategy-specific signal logic
from .signal_logic import (
    add_ma_cross_indicators,
    detect_ma_cross_signals,
    session_mask,
)
from .strategy_config import STRATEGY, SYMBOLS, TIMEFRAME, get_indicator_params


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_backtest_data(symbol_id: int, date_to: str | None = None,
                       tf: str | None = None, max_bars: int = 60000,
                       warmup: int | None = None) -> 'pd.DataFrame':
    """Tải dữ liệu OHLCV từ DWH.Fact_OHLCV cho backtest."""
    tf = tf or TIMEFRAME
    if warmup is None:
        p = get_indicator_params()
        warmup = max(p.get('FAST_MA', 10), p.get('SLOW_MA', 50),
                     p.get('ATR_PERIOD', 14)) * 4
    return load_backtest_ohlcv(
        symbol_id=symbol_id,
        date_to=date_to,
        tf=tf,
        max_bars=max_bars,
        warmup=warmup,
    )


def load_backtest_full(symbol_id: int, tf: str | None = None,
                       max_bars: int = 80000) -> 'pd.DataFrame':
    """Tải toàn bộ dữ liệu cho 1 mã (dùng pre-cache cho optimizer)."""
    return load_backtest_ohlcv_full(symbol_id=symbol_id, tf=tf or TIMEFRAME, max_bars=max_bars)
