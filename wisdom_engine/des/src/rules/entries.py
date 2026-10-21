"""
Entry helpers cho từng chiến lược.

des chỉ tạo signal và điểm vào lệnh. SL/TP KHÔNG thuộc OG: từ
2026-10-04 OF (OF10/11/12) tự tính SL/TP từ bảng KSL/KTP đã optimize của họ,
nên OG không còn tính hay publish sl_price/tp_price/ksl/ktp nữa. Sizing
(volume) và quản trị lệnh cũng thuộc OF.

Đồng bộ với we_core 2026-10-16: bỏ cột entry_time (luôn bằng bartime của dòng tín hiệu, không ai đọc).
Cột duy nhất do file này tạo là entry_price (signal_publisher.build_signal đọc nó làm giá vào lệnh).
Bản sao nguyên văn của we_core/src/rules/entries.py (des là hệ con tách biệt, không import we_core); sửa một
bên phải sửa cả bên kia.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_combo_entries(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Thêm entry pending cho Combo.

    BUY dùng high + X, SELL dùng low - X. X vẫn thuộc core vì nó quyết định
    chính điểm vào lệnh của Combo, không phải TP/SL.
    """
    _ = symbol
    out = _with_empty_entry_column(df)
    if out.empty or not {"signal", "high", "low"}.issubset(out.columns):
        return out

    x = float(params["X"])
    signals = _signals(out)
    buy = signals.eq(1)
    sell = signals.eq(-1)

    out.loc[buy, "entry_price"] = out.loc[buy, "high"] + x
    out.loc[sell, "entry_price"] = out.loc[sell, "low"] - x
    return out


def add_ma_cross_entries(
    df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Thêm market entry cho MA Cross: entry = close của bar tín hiệu.
    """
    _ = symbol, params
    return _add_close_market_entries(df)


def add_macd_break_entries(
    df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Entry tham chiếu cho MACD Break = close của bar tín hiệu. Hệ thống đặt lệnh
    tự vào market ở nến kế tiếp, nên cột này chỉ để hiển thị/đối chiếu.
    """
    _ = symbol, params
    return _add_close_market_entries(df)


def _add_close_market_entries(df: pd.DataFrame) -> pd.DataFrame:
    """entry_price = close cho các bar có signal ±1."""
    out = _with_empty_entry_column(df)
    if out.empty or not {"signal", "close"}.issubset(out.columns):
        return out
    has_signal = _signals(out).isin({1, -1})
    out.loc[has_signal, "entry_price"] = out.loc[has_signal, "close"]
    return out


def _with_empty_entry_column(df: pd.DataFrame) -> pd.DataFrame:
    """Bản sao df có cột entry_price toàn NaN; các hàm add_*_entries điền cho dòng có tín hiệu."""
    out = df.copy()
    out["entry_price"] = np.nan
    return out


def _signals(df: pd.DataFrame) -> pd.Series:
    if "signal" not in df.columns:
        return pd.Series(0, index=df.index, dtype="int64")
    return pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)
