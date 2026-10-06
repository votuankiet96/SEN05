"""
Entry level helpers cho từng chiến lược.

order_gateway chỉ tạo signal và điểm vào lệnh. SL/TP KHÔNG thuộc OG: từ
2026-10-04 OF (OF10/11/12) tự tính SL/TP từ bảng KSL/KTP đã optimize của họ,
nên OG không còn tính hay publish sl_price/tp_price/ksl/ktp nữa. Sizing
(volume) và quản trị lệnh cũng thuộc OF.

Với các chiến lược có dữ liệu Dow từ indicator.py, levels.py copy tham
chiếu đó sang cột sl_dow (tham chiếu phụ, không phải SL chính thức).
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
    Thêm market entry cho MA Cross và copy stop reference Dow nếu có.
    """
    _ = params, symbol
    out = _with_empty_order_columns(df, include_sl_dow=True)
    if out.empty or not {"signal", "bartime", "close"}.issubset(out.columns):
        return out

    signals = _signals(out)
    has_signal = signals.isin({1, -1})
    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[has_signal, "entry_price"] = out.loc[has_signal, "close"]
    return _copy_sl_dow_reference(out, signals)


def _with_empty_order_columns(df: pd.DataFrame, *, include_sl_dow: bool = False) -> pd.DataFrame:
    """
    Tạo sẵn các cột level với NaN để mọi dòng có cùng schema, kể cả dòng
    không có tín hiệu.
    """
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    if include_sl_dow:
        out["sl_dow"] = np.nan
    return out


def _signals(df: pd.DataFrame) -> pd.Series:
    if "signal" not in df.columns:
        return pd.Series(0, index=df.index, dtype="int64")
    return pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)


def _copy_sl_dow_reference(df: pd.DataFrame, signals: pd.Series) -> pd.DataFrame:
    """
    Copy dữ liệu Dow từ indicator sang sl_dow theo hướng lệnh.
    """
    out = df.copy()
    if "sl_dow" not in out.columns:
        out["sl_dow"] = np.nan

    entries = pd.to_numeric(out["entry_price"], errors="coerce")
    if "sl_dow_buy" in out.columns:
        buy_sl = pd.to_numeric(out["sl_dow_buy"], errors="coerce")
        valid_buy = signals.eq(1) & entries.notna() & buy_sl.lt(entries)
        out.loc[valid_buy, "sl_dow"] = buy_sl.loc[valid_buy]
    if "sl_dow_sell" in out.columns:
        sell_sl = pd.to_numeric(out["sl_dow_sell"], errors="coerce")
        valid_sell = signals.eq(-1) & entries.notna() & sell_sl.gt(entries)
        out.loc[valid_sell, "sl_dow"] = sell_sl.loc[valid_sell]
    return out
