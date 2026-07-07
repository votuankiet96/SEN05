"""
Tính toán mức Entry, Stop Loss và Take Profit cho chiến lược Combo.

Mô tả:
    Sau khi detect_combo_signals() xác định tín hiệu, module này tính
    các mức giá cụ thể để hiển thị trên biểu đồ và gửi trong thông báo Telegram.

    Mô hình entry là breakout: chờ giá phá vỡ đỉnh/đáy bar tín hiệu + buffer X.

Đầu vào:
    DataFrame đã qua add_combo_indicators() và detect_combo_signals().

Đầu ra:
    DataFrame với các cột bổ sung: entry_time, entry_price, sl_price, tp_price, risk_reward.

Giả định giao dịch:
    Entry là pending breakout order (không phải market order ở bar tiếp theo).
    SL đặt bên kia bar tín hiệu + buffer để tránh noise.
    TP = Entry ± KTP × ATR.
    risk_reward được sao chép từ cột "rr" của detect_combo_signals().
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_combo_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Thêm mức Entry, SL và TP vào DataFrame theo mô hình breakout.

    Công thức:
        BUY:
            entry_price = high + X   (đặt lệnh buy stop phía trên đỉnh bar + buffer)
            sl_price    = low  - X   (SL dưới đáy bar - buffer)
            tp_price    = entry_price + KTP × ATR

        SELL:
            entry_price = low  - X   (đặt lệnh sell stop phía dưới đáy bar - buffer)
            sl_price    = high + X   (SL trên đỉnh bar + buffer)
            tp_price    = entry_price - KTP × ATR

    Args:
        df: DataFrame đã qua detect_combo_signals() — phải có cột signal, high, low, atr, rr.
        params: Dict tham số đã validate — cần "X" (buffer) và "KTP" (TP multiplier).
        symbol: Không sử dụng trực tiếp (X đã được merge vào params trước đó).

    Returns:
        Bản sao df với 5 cột bổ sung:
            entry_time:   Thời gian bar tín hiệu (bằng bartime của bar đó).
            entry_price:  Giá Entry dạng pending breakout.
            sl_price:     Giá Stop Loss.
            tp_price:     Giá Take Profit.
            risk_reward:  R:R ratio (copy từ cột "rr" của signals.py).

    Giả định giao dịch:
        entry_time = bartime của bar tín hiệu (không phải bar tiếp theo).
        Đây là mô hình pending order — lệnh chỉ khớp khi giá phá vỡ entry_price.
        Không tính đến spread hay slippage.
    """
    _ = symbol  # symbol không dùng trực tiếp; X đã resolved trong normalize_params()
    out = df.copy()
    x = float(params["X"])
    ktp = float(params["KTP"])

    # Khởi tạo cột với giá trị NaN/NaT cho tất cả dòng
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = out.get("rr", np.nan)

    buy = out["signal"].eq(1)
    sell = out["signal"].eq(-1)
    has_signal = buy | sell

    # entry_time là chính bartime của bar phát tín hiệu
    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]

    # Entry: breakout phía trên đỉnh (BUY) hoặc dưới đáy (SELL)
    out.loc[buy, "entry_price"] = out.loc[buy, "high"] + x
    out.loc[sell, "entry_price"] = out.loc[sell, "low"] - x

    # SL: ngược hướng, bên kia bar + buffer
    out.loc[buy, "sl_price"] = out.loc[buy, "low"] - x
    out.loc[sell, "sl_price"] = out.loc[sell, "high"] + x

    # TP: Entry ± KTP × ATR; signal (+1/-1) xác định hướng cộng/trừ
    out.loc[has_signal, "tp_price"] = (
        out.loc[has_signal, "entry_price"]
        + out.loc[has_signal, "signal"] * ktp * out.loc[has_signal, "atr"]
    )
    return out
