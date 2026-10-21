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
    Thêm entry pending cho Combo.

    BUY dùng high + đệm, SELL dùng low - đệm. Đệm vẫn thuộc core vì nó quyết định
    chính điểm vào lệnh của Combo, không phải TP/SL. Có hai kiểu đệm, mỗi symbol đúng một kiểu:
      - ENTRY_ATR_PCT là None (mặc định): đệm = X cố định theo symbol.
      - ENTRY_ATR_PCT khác None (nhóm forex, config strategies.combo.symbol_entry_atr_pct): đệm =
        ENTRY_ATR_PCT% × ATR tại nến tín hiệu (cột `atr`, chu kỳ ATR_PERIOD), X bị bỏ qua.
    """
    _ = symbol
    out = _with_empty_entry_column(df)
    if out.empty or not {"signal", "high", "low"}.issubset(out.columns):
        return out

    entry_atr_pct = params.get("ENTRY_ATR_PCT")
    if entry_atr_pct is None:
        buffer = float(params["X"])
    else:
        if "atr" not in out.columns:
            raise ValueError("ENTRY_ATR_PCT needs the 'atr' column (run add_combo_indicators first).")
        buffer = float(entry_atr_pct) / 100.0 * pd.to_numeric(out["atr"], errors="coerce")
    signals = _signals(out)
    buy = signals.eq(1)
    sell = signals.eq(-1)

    out.loc[buy, "entry_price"] = out.loc[buy, "high"] + (buffer[buy] if entry_atr_pct is not None else buffer)
    out.loc[sell, "entry_price"] = out.loc[sell, "low"] - (buffer[sell] if entry_atr_pct is not None else buffer)
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
