"""
Entry helpers cho từng chiến lược.

wisdom_engine chỉ chịu trách nhiệm tạo signal + giá vào lệnh (entry_price) —
KHÔNG tính SL/TP theo KSL/KTP. Đây là hệ thống backtest/thử nghiệm chiến lược
để tìm signal, không phải hệ tối ưu hoá SL/TP: cBot chạy trên cTrader
(Combo.cs, MA Cross.cs) đã tự có công thức SL/TP riêng, tính lại ở đây là dư
thừa. Việc tính KSL/KTP thật cho luồng OF đặt lệnh sống ở `order_gateway`,
không phải ở đây (chốt 2026-09-21, xem CLAUDE.md mục 1).

Đã bỏ 2026-10-16 vì không ai đọc: các cột sl_price/tp_price/risk_reward (từng được
giữ luôn NaN cho dashboard cũ) và entry_time (luôn bằng bartime của dòng tín hiệu).
Cột duy nhất do file này tạo là entry_price (CSV của combo và bảng tín hiệu dashboard dùng).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_combo_entries(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Thêm entry pending cho Combo (đổi 2026-10-21, người dùng quyết định): BUY = `high` của nến tín hiệu,
    SELL = `low` của nến tín hiệu, KHÔNG đệm (không X cố định theo symbol, không % ATR, không thêm tick).
    Lệnh chờ STOP đặt đúng mép nến tín hiệu: chỉ khớp khi giá phá qua mép đó ở nến sau.

    Khi nến đóng cửa đúng tại mép (BUY có close == high, SELL có close == low; đo 2026-10-20 khoảng 2,3% tín
    hiệu) entry trùng giá hiện tại — để engine/OF xử lý lệnh chờ đó, hệ này không xử lý đặc biệt.
    """
    _ = symbol, params
    out = _with_empty_entry_column(df)
    if out.empty or not {"signal", "high", "low"}.issubset(out.columns):
        return out

    signals = _signals(out)
    buy = signals.eq(1)
    sell = signals.eq(-1)

    out.loc[buy, "entry_price"] = out.loc[buy, "high"]
    out.loc[sell, "entry_price"] = out.loc[sell, "low"]
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
