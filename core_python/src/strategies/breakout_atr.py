"""
Chiến lược Breakout ATR — phá đỉnh đóng cửa + ATR trailing stop.

Nguồn (research_notes S005, mẫu `trend-following-effect-in-stocks.py` trong
paperswithbacktest/awesome-systematic-trading, dẫn Quantpedia "Trend
Following Effect in Stocks"), nguyên văn mô tả của nguồn:
    "The entry signal occurs if today's close is greater than or equal to the
    highest close during the stock's entire history. A 10-period average true
    range trailing stop is used as an exit signal."
Mã QuantConnect của nguồn:
    - Vào: MarketOrder khi close >= đỉnh đóng cửa lịch sử (cửa sổ 10 năm ngày).
    - Stop ban đầu: StopMarketOrder tại close − ATR(10).
    - Mỗi bar: stop mới = close − ATR(10); chỉ dời LÊN, không dời xuống.
    - Không có take profit. Chỉ chiều mua (long-only).

Chuyển sang hệ OG (khác nguồn — là lựa chọn của tôi, ghi rõ để backtest):
    1. "Toàn bộ lịch sử / 10 năm ngày" → cửa sổ LOOKBACK_BARS bar trên timeframe
       đang chạy (H1–H4, M10–M45). Không có giá trị nào được nguồn chứng minh
       cho khung giờ này — để backtest chọn.
    2. Chiều SELL là đối xứng do tôi thêm (ALLOW_SHORT): close <= đáy đóng cửa
       LOOKBACK_BARS bar trước → bán, stop = close + ATR, chỉ dời XUỐNG.
    3. Nguồn trên nến ngày; ở đây áp nguyên logic trên từng bar của timeframe.

Tín hiệu (tính tại lúc bar ĐÓNG, chỉ dùng dữ liệu đến bar đó — không look-ahead):
    BUY  (signal = +1): đang không giữ vị thế, close >= prior_high_close
                        (đỉnh đóng cửa của LOOKBACK_BARS bar TRƯỚC bar hiện tại).
    SELL (signal = -1): ALLOW_SHORT=true, đang không giữ vị thế,
                        close <= prior_low_close.
    Thoát (exit_signal = 1):
        - Long: low của bar <= stop đang hiệu lực (stop đặt ở cuối bar trước).
        - Short: high của bar >= stop đang hiệu lực.
    Đang giữ vị thế thì không phát thêm tín hiệu cùng chiều (giống nguồn:
    chỉ mua khi "not Invested").
    Thứ tự xử lý trong một bar: (1) kiểm tra stop → (2) dời stop → (3) xét vào
    lệnh. Vì vậy cùng một bar có thể vừa dính stop của lệnh cũ vừa vào lệnh mới
    (cùng hoặc ngược chiều) nếu giá đóng cửa thoả điều kiện phá đỉnh/đáy.

Loại lệnh:
    - Entry: market, tại giá đóng cửa của bar tín hiệu (entry_price/entry_time do
      levels.add_breakout_atr_levels gắn, không tính ở file này).
    - SL: stop-market ATR trailing (cột stop_price = mức stop hiệu lực cho bar KẾ
      TIẾP). Khi bị chạm, exit_price = stop (hoặc giá mở cửa nếu nhảy gap qua stop).
    - TP: không có (nguồn không có).

Chỉ báo (prior_high_close, prior_low_close, atr) tính ở
indicator.add_breakout_atr_indicators — file này chỉ đọc các cột đó.

Tham số (params, chưa đăng ký vào configuration.py — truyền trực tiếp):
    LOOKBACK_BARS (int)  — số bar để lấy đỉnh/đáy đóng cửa trước đó (dùng ở indicator).
    ATR_PERIOD (int)     — nguồn dùng 10 (dùng ở indicator).
    ALLOW_SHORT (bool)   — bật chiều SELL đối xứng (không có trong nguồn).

Đầu ra: bản sao df + các cột position (+1/0/-1 sau khi bar đóng),
    signal (+1/-1/0), signal_reason, stop_price,
    exit_signal (0/1), exit_price, exit_reason.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def detect_breakout_atr_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL và điểm thoát ATR trailing stop.

    Args:
        df: DataFrame đã qua indicator.add_breakout_atr_indicators(), sắp tăng
            dần theo bartime. Cần open, high, low, close, prior_high_close,
            prior_low_close, atr.
        symbol, sess_mask: Không dùng — giữ cùng chữ ký detect_signals của pipeline.
        params: LOOKBACK_BARS (chỉ để ghi lý do), ALLOW_SHORT.

    Returns:
        DataFrame (xem docstring module).
    """
    _ = symbol, sess_mask
    p = params or {}
    lookback = int(p["LOOKBACK_BARS"])
    allow_short = bool(p["ALLOW_SHORT"])

    out = df.reset_index(drop=True).copy()
    o = out["open"].astype(float).to_numpy()
    h = out["high"].astype(float).to_numpy()
    lo = out["low"].astype(float).to_numpy()
    c = out["close"].astype(float).to_numpy()
    hi_prev = out["prior_high_close"].to_numpy()
    lo_prev = out["prior_low_close"].to_numpy()
    a = out["atr"].to_numpy()

    n = len(out)
    position = np.zeros(n, dtype=int)
    signal = np.zeros(n, dtype=int)
    stop_price = np.full(n, np.nan)
    exit_signal = np.zeros(n, dtype=int)
    exit_price = np.full(n, np.nan)
    signal_reason = np.full(n, "", dtype=object)
    exit_reason = np.full(n, "", dtype=object)

    pos = 0
    stop = np.nan
    for i in range(n):
        # =====================================================================
        # (1) THOÁT — stop đang hiệu lực (đặt ở cuối bar trước) bị chạm trong bar.
        # =====================================================================
        if pos == 1 and lo[i] <= stop:
            exit_signal[i] = 1
            exit_price[i] = min(o[i], stop)  # gap xuống dưới stop thì khớp ở open
            exit_reason[i] = "long ATR trailing stop hit"
            pos, stop = 0, np.nan
        elif pos == -1 and h[i] >= stop:
            exit_signal[i] = 1
            exit_price[i] = max(o[i], stop)
            exit_reason[i] = "short ATR trailing stop hit"
            pos, stop = 0, np.nan

        valid = not (np.isnan(a[i]) or a[i] <= 0 or np.isnan(hi_prev[i]) or np.isnan(lo_prev[i]))

        # =====================================================================
        # (2) DỜI STOP — đang giữ vị thế: stop mới = close ∓ ATR, chỉ dời có lợi.
        # =====================================================================
        if pos == 1 and valid:
            stop = max(stop, c[i] - a[i])
        elif pos == -1 and valid:
            stop = min(stop, c[i] + a[i])

        # =====================================================================
        # (3) VÀO LỆNH — QUY TẮC CHIẾN LƯỢC (chỉ sửa khối này khi đổi ý tưởng).
        # =====================================================================
        if pos == 0 and valid:
            if c[i] >= hi_prev[i]:
                pos, stop = 1, c[i] - a[i]
                signal[i] = 1
                signal_reason[i] = f"close >= highest close of prior {lookback} bars"
            elif allow_short and c[i] <= lo_prev[i]:
                pos, stop = -1, c[i] + a[i]
                signal[i] = -1
                signal_reason[i] = f"close <= lowest close of prior {lookback} bars (mirrored, not in source)"

        position[i] = pos
        stop_price[i] = stop if pos != 0 else np.nan

    out["position"] = position
    out["signal"] = signal
    out["signal_reason"] = signal_reason
    out["stop_price"] = stop_price
    out["exit_signal"] = exit_signal
    out["exit_price"] = exit_price
    out["exit_reason"] = exit_reason
    return out
