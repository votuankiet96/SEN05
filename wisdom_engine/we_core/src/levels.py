"""
Entry level helpers cho từng chiến lược.

wisdom_engine chỉ chịu trách nhiệm tạo signal + điểm vào lệnh (entry_price/
entry_time) — KHÔNG tính SL/TP theo KSL/KTP. Đây là hệ thống backtest/thử
nghiệm chiến lược để tìm signal, không phải hệ tối ưu hoá SL/TP: cBot chạy
trên cTrader (Combo.cs, MA Cross.cs) đã tự có công thức SL/TP riêng, tính
lại ở đây là dư thừa. Việc tính KSL/KTP thật cho luồng OF đặt lệnh sống ở
`order_gateway`, không phải ở đây (chốt 2026-09-21, xem CLAUDE.md mục 1).

sl_price/tp_price/risk_reward vẫn được tạo nhưng LUÔN NaN — giữ cột ổn
định cho dashboard/payload cũ (`signal_display/payload.py` đọc phòng thủ
qua `.get()`) không gãy, không phải vì còn tính gì cho chúng.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_combo_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Thêm entry pending cho Combo.

    BUY dùng high + X, SELL dùng low - X. X vẫn thuộc core vì nó quyết định
    chính điểm vào lệnh của Combo, không phải TP/SL.
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
    _ = symbol, params
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
    Entry tham chiếu cho MACD Break = close của bar tín hiệu. Hệ thống đặt lệnh
    tự vào market ở nến kế tiếp, nên cột này chỉ để hiển thị/đối chiếu. SL/TP
    để NaN — thuộc quản lý lệnh (KSL/KTP), không thuộc wisdom_engine.
    """
    _ = symbol, params
    return _add_close_market_levels(df)


def add_sma_trend_levels(
    df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Market entry tại close của bar tín hiệu cho SMA Trend. SL/TP để NaN —
    nguồn không có, chưa chốt.
    """
    _ = symbol, params
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
    Tạo các cột level ổn định cho dashboard/payload -- sl_price/tp_price/
    risk_reward luôn NaN (không tính, xem docstring module).
    """
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan
    return out


def _signals(df: pd.DataFrame) -> pd.Series:
    if "signal" not in df.columns:
        return pd.Series(0, index=df.index, dtype="int64")
    return pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)
