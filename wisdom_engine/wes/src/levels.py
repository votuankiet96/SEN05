"""
Entry level helpers cho từng chiến lược.

wes chỉ tạo signal và điểm vào lệnh. SL/TP KHÔNG thuộc OG: từ
2026-10-04 OF (OF10/11/12) tự tính SL/TP từ bảng KSL/KTP đã optimize của họ,
nên OG không còn tính hay publish sl_price/tp_price/ksl/ktp nữa. Sizing
(volume) và quản trị lệnh cũng thuộc OF.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_combo_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Thêm entry pending cho Combo.

    BUY dùng high + X, SELL dùng low - X. X vẫn thuộc core vì nó quyết định
    chính điểm vào lệnh của Combo.
    """
    _ = symbol
    out = _with_empty_order_columns(df)
    if out.empty or not {"signal", "bartime", "high", "low"}.issubset(out.columns):
        return out

    x = float(params["X"])
    signals = _signals(out)
    buy = signals.eq(1)
    sell = signals.eq(-1)
    has_signal = buy | sell

    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[buy, "entry_price"] = out.loc[buy, "high"] + x
    out.loc[sell, "entry_price"] = out.loc[sell, "low"] - x
    return out


def add_ma_cross_levels(
    df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Thêm market entry cho MA Cross.
    """
    _ = params, symbol
    out = _with_empty_order_columns(df)
    if out.empty or not {"signal", "bartime", "close"}.issubset(out.columns):
        return out

    signals = _signals(out)
    has_signal = signals.isin({1, -1})
    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[has_signal, "entry_price"] = out.loc[has_signal, "close"]
    return out


def add_macd_break_levels(
    df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Thêm market entry cho MACD Break: entry = close của bar tín hiệu. OF tự vào
    market ở nến kế tiếp, nên đây chỉ là giá tham chiếu đi kèm payload.
    """
    _ = params, symbol
    return _add_close_market_levels(df)


def _add_close_market_levels(df: pd.DataFrame) -> pd.DataFrame:
    """entry_price = close, entry_time = bartime cho các bar có signal ±1."""
    out = _with_empty_order_columns(df)
    if out.empty or not {"signal", "bartime", "close"}.issubset(out.columns):
        return out

    has_signal = _signals(out).isin({1, -1})
    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[has_signal, "entry_price"] = out.loc[has_signal, "close"]
    return out


def _with_empty_order_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tạo sẵn các cột level với NaN để mọi dòng có cùng schema, kể cả dòng
    không có tín hiệu.
    """
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    return out


def _signals(df: pd.DataFrame) -> pd.Series:
    if "signal" not in df.columns:
        return pd.Series(0, index=df.index, dtype="int64")
    return pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)
