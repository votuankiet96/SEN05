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
import pandas as pd

from modules.db_connector import get_connection

# Shared execution + metrics
from ..shared.execution_engine import backtest_symbol, backtest_fast
from ..shared.metrics import calc_metrics, in_bao_cao
from ..shared.monte_carlo import run_monte_carlo, plot_monte_carlo

# Strategy-specific signal logic
from .signal_logic import (
    add_ma_cross_indicators,
    detect_ma_cross_signals,
    session_mask,
)
from .strategy_config import STRATEGY, SYMBOLS, TIMEFRAME, get_indicator_params


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING  (dùng chung cùng DB schema với combo)
# ─────────────────────────────────────────────────────────────────────────────

def load_backtest_data(symbol_id: int, date_to: str | None = None,
                       tf: str | None = None, max_bars: int = 60000,
                       warmup: int | None = None) -> pd.DataFrame:
    """
    Tải dữ liệu OHLCV từ DWH.Fact_OHLCV cho backtest.

    Trả về DataFrame với index là BarTime và cột:
    open, high, low, close, volume.
    """
    tf = tf or TIMEFRAME
    if warmup is None:
        p = get_indicator_params()
        warmup = max(p.get('FAST_MA', 10), p.get('SLOW_MA', 50),
                     p.get('ATR_PERIOD', 14)) * 4
    total = max_bars + warmup

    conn = get_connection()
    try:
        date_clause = "AND f.BarTime <= ?" if date_to else ""
        query = f'''
            SELECT TOP ({total})
                   f.BarTime,
                   f.OpenPrice  AS [open],
                   f.HighPrice  AS [high],
                   f.LowPrice   AS [low],
                   f.ClosePrice AS [close],
                   f.Volume     AS [volume]
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
              {date_clause}
            ORDER  BY f.BarTime DESC
        '''
        params = (symbol_id, tf, date_to) if date_to else (symbol_id, tf)
        df = pd.read_sql(query, conn, params=params,
                         parse_dates=['BarTime'], index_col='BarTime')
    finally:
        conn.close()
    return df.sort_index()


def load_backtest_full(symbol_id: int, tf: str | None = None,
                       max_bars: int = 80000) -> pd.DataFrame:
    """Tải toàn bộ dữ liệu cho 1 mã (dùng pre-cache cho optimizer)."""
    return load_backtest_data(symbol_id, date_to=None, tf=tf,
                              max_bars=max_bars, warmup=0)
