# =============================================================================
# strategies/combo/backtest_engine.py  —  Data loading + re-exports cho Combo v2
# =============================================================================
# NOTE: File này là COMPATIBILITY LAYER — không chứa logic thực.
#
# Mục đích duy nhất:
#   1. Cung cấp load_backtest_data() / load_backtest_full() để load OHLCV từ DB.
#   2. Re-export toàn bộ public API về một chỗ để notebooks không cần đổi import
#      khi logic được tái cấu trúc sang các module riêng.
#
# Nếu cần chỉnh sửa logic, hãy đến đúng module nguồn:
#   Execution logic → strategies/shared/execution_engine.py
#   Signal logic    → strategies/combo/signal_logic.py
#   Walk-forward    → strategies/combo/walk_forward.py
#   Metrics         → strategies/shared/metrics.py
# =============================================================================
import pandas as pd

from modules.db_connector import get_connection

from .strategy_config import TIMEFRAME, get_indicator_params

# ─────────────────────────────────────────────────────────────────────────────
# Re-exports — để các notebook dùng import từ backtest_engine như cũ
# ─────────────────────────────────────────────────────────────────────────────
from .signal_logic import (
    add_combo_indicators,
    add_combo_indicators as add_backtest_indicators,   # alias cũ
    detect_combo_signals,
    detect_combo_signals as detect_signals,            # alias cũ
    session_mask,
    build_raw_signal_masks,
    resolve_trade_hit,
    build_signal_record,
    scan_signals_reversal,
)
from ..shared.execution_engine import backtest_symbol, backtest_fast
from .walk_forward import walk_forward_backtest, check_plateau_stability
from ..shared.metrics import calc_metrics, in_bao_cao


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_backtest_data(symbol_id: int, date_to: str | None = None,
                       tf: str | None = None, max_bars: int = 60000,
                       warmup: int | None = None) -> pd.DataFrame:
    """
    Tải dữ liệu OHLCV phục vụ backtest.

    Trả về DataFrame với index là BarTime và cột chuẩn hoá chữ thường:
    open, high, low, close, volume.

    Parameters
    ----------
    date_to  : mốc thời gian trên cùng để cắt dữ liệu.
    tf       : timeframe, mặc định lấy từ strategy_config.TIMEFRAME.
    max_bars : số bar tối đa cho vùng test (chưa tính warmup).
    warmup   : số bar đệm cho chỉ báo; None = tự tính theo MA/MACD/ATR.
    """
    tf = tf or TIMEFRAME
    if warmup is None:
        p = get_indicator_params()
        warmup = max(p['MA_PERIOD'], p['MACD_SLOW'], p['ATR_PERIOD']) * 4
    total = max_bars + warmup

    conn = get_connection()
    try:
        date_clause = "AND f.BarTime <= ?" if date_to else ""
        query = f'''
            SELECT TOP ({total})
                   f.BarTime,
                   f.[Open]  AS [open],
                   f.High  AS [high],
                   f.Low   AS [low],
                   f.[Close] AS [close],
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
    """Tải gần như toàn bộ dữ liệu cho 1 mã (dùng pre-cache cho optimizer)."""
    return load_backtest_data(symbol_id, date_to=None, tf=tf,
                              max_bars=max_bars, warmup=0)
