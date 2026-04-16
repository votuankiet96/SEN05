# =============================================================================
# strategies/ma_cross/signal_logic.py  —  Signal logic cho MA Cross strategy
# =============================================================================
"""Phát hiện tín hiệu giao cắt MA cho chiến lược MA Cross.

TODO: Điền logic tín hiệu cụ thể.

Tham khảo strategies/combo/signal_logic.py để biết cấu trúc cần implement:
- add_ma_cross_indicators(df, params) → DataFrame
- detect_ma_cross_signals(df, sess_mask, sym_key, params) → DataFrame với cột signal
- session_mask(df, hours_utc) → Series[bool]
"""
import numpy as np
import pandas as pd

from modules.indicators import add_indicators as _add_base_indicators

from .strategy_config import STRATEGY, SYMBOLS, get_indicator_params


# ─────────────────────────────────────────────────────────────────────────────
# 1. INDICATOR SETUP
# ─────────────────────────────────────────────────────────────────────────────

def add_ma_cross_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Thêm chỉ báo cho MA Cross strategy.

    TODO: Implement logic tính fast_ma, slow_ma, atr.
    Tham khảo: modules/indicators.py để dùng các indicator có sẵn.

    Parameters
    ----------
    df     : DataFrame OHLCV với cột open, high, low, close, volume.
    params : dict tham số — xem get_indicator_params().
    """
    # TODO: implement
    raise NotImplementedError("add_ma_cross_indicators() chưa được implement.")


# ─────────────────────────────────────────────────────────────────────────────
# 2. SESSION MASK
# ─────────────────────────────────────────────────────────────────────────────

def session_mask(df: pd.DataFrame, hours_utc: list) -> pd.Series:
    """Tạo mask theo phiên giao dịch (UTC). hours_utc=[] → scan all."""
    if not hours_utc:
        return pd.Series(True, index=df.index)
    return pd.Series(df.index.hour.isin(hours_utc), index=df.index)


# ─────────────────────────────────────────────────────────────────────────────
# 3. SIGNAL DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_ma_cross_signals(df: pd.DataFrame, sess_mask: pd.Series,
                             sym_key: str | None = None,
                             params: dict | None = None) -> pd.DataFrame:
    """
    Phát hiện tín hiệu giao cắt MA.

    Luật tín hiệu (TODO — ví dụ cơ bản):
    - BUY : fast_ma cắt lên slow_ma + các điều kiện lọc.
    - SELL: fast_ma cắt xuống slow_ma + các điều kiện lọc.

    Cần thêm cột 'signal' (1/-1/0) và 'rr' vào df trả về.
    shared/execution_engine.py sẽ dùng cột 'signal' để thực thi lệnh.

    TODO: Implement signal detection logic.
    """
    raise NotImplementedError("detect_ma_cross_signals() chưa được implement.")
