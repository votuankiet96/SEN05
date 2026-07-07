"""
Tính toán mức Entry, Stop Loss và Take Profit cho chiến lược MA Cross.

Mô tả:
    MA Cross dùng mô hình market order tại mở cửa bar tiếp theo (shift(-1)).
    Khác với Combo (pending breakout), entry được giả định khớp ngay tại open
    của bar sau bar tín hiệu, cộng/trừ chi phí spread và slippage.

Đầu vào:
    DataFrame đã qua detect_ma_cross_signals().

Đầu ra:
    DataFrame với cột: entry_time, entry_price, sl_price, tp_price, risk_reward.

Giả định giao dịch:
    entry_time là bartime của bar TIẾP THEO (không phải bar tín hiệu).
    Bar cuối cùng trong DataFrame sẽ có entry_time = NaN (không có bar tiếp theo).
    ATR_TP_MULT = 0 đồng nghĩa không đặt TP (TP và R:R sẽ là NaN).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_ma_cross_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Thêm mức Entry, SL và TP dựa trên giá mở cửa bar tiếp theo.

    Công thức:
        cost = SPREAD_PTS + SLIPPAGE_PTS

        BUY:
            entry_price = next_open + cost   (mua ở open bar tiếp theo + chi phí)
            sl_price    = entry_price - ATR_STOP_MULT × ATR
            tp_price    = entry_price + ATR_TP_MULT × ATR  (nếu ATR_TP_MULT > 0)

        SELL:
            entry_price = next_open - cost   (bán ở open bar tiếp theo - chi phí)
            sl_price    = entry_price + ATR_STOP_MULT × ATR
            tp_price    = entry_price - ATR_TP_MULT × ATR  (nếu ATR_TP_MULT > 0)

        risk_reward = ATR_TP_MULT / ATR_STOP_MULT  (hằng số, không phụ thuộc vào ATR)

    Args:
        df: DataFrame đã qua detect_ma_cross_signals() — phải có cột signal, open, atr, bartime.
        params: Dict tham số đã validate — cần SPREAD_PTS, SLIPPAGE_PTS, ATR_STOP_MULT, ATR_TP_MULT.
        symbol: Không sử dụng — placeholder để thống nhất interface.

    Returns:
        Bản sao df với 5 cột bổ sung:
            entry_time:   Thời gian bar tiếp theo (bartime.shift(-1)).
            entry_price:  Giá Entry ước tính.
            sl_price:     Giá Stop Loss.
            tp_price:     Giá Take Profit (NaN nếu ATR_TP_MULT = 0).
            risk_reward:  ATR_TP_MULT / ATR_STOP_MULT (NaN nếu ATR_TP_MULT = 0).

    Giả định giao dịch:
        Dùng shift(-1) nên bar cuối DataFrame sẽ không có entry — NaN.
        Spread và slippage được xem như chi phí cố định mỗi lệnh.
        R:R là hằng số (không phụ thuộc ATR từng bar) vì tính theo bội số ATR.
    """
    _ = symbol
    out = df.copy()
    spread = float(params["SPREAD_PTS"])
    slippage = float(params["SLIPPAGE_PTS"])
    stop_mult = float(params["ATR_STOP_MULT"])
    tp_mult = float(params["ATR_TP_MULT"])

    # Lấy giá open và bartime của bar TIẾP THEO để xác định entry
    next_open = out["open"].shift(-1)
    next_time = out["bartime"].shift(-1)

    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan

    # Chỉ tính level cho bar có tín hiệu VÀ có bar tiếp theo (bar cuối sẽ bị bỏ qua)
    has_next = next_open.notna() & next_time.notna()
    buy = out["signal"].eq(1) & has_next
    sell = out["signal"].eq(-1) & has_next
    has_signal = buy | sell

    cost = spread + slippage
    out.loc[has_signal, "entry_time"] = next_time.loc[has_signal]

    # BUY: mua cao hơn next_open một chút (spread + slippage)
    out.loc[buy, "entry_price"] = next_open.loc[buy] + cost
    # SELL: bán thấp hơn next_open một chút
    out.loc[sell, "entry_price"] = next_open.loc[sell] - cost

    sl_dist = stop_mult * out["atr"]
    tp_dist = tp_mult * out["atr"]
    out.loc[buy, "sl_price"] = out.loc[buy, "entry_price"] - sl_dist.loc[buy]
    out.loc[sell, "sl_price"] = out.loc[sell, "entry_price"] + sl_dist.loc[sell]

    if tp_mult > 0:
        out.loc[buy, "tp_price"] = out.loc[buy, "entry_price"] + tp_dist.loc[buy]
        out.loc[sell, "tp_price"] = out.loc[sell, "entry_price"] - tp_dist.loc[sell]
        # R:R là hằng số vì cả TP và SL đều là bội số của ATR cùng giá trị
        out.loc[has_signal, "risk_reward"] = tp_mult / stop_mult
    # ATR_TP_MULT = 0: không đặt TP, R:R = NaN (đã khởi tạo NaN ở trên)
    return out
