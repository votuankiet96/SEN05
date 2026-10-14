"""
Chiến lược SMA Trend — giữ vị thế khi giá nằm trên đường SMA dài.

Nguồn (research_notes S005, mẫu `asset-class-trend-following.py` trong
paperswithbacktest/awesome-systematic-trading, dẫn Quantpedia "Asset Class
Trend Following"), nguyên văn mô tả của nguồn:
    "Hold asset class ETF only when it is over its 10 month Simple Moving
    Average, otherwise stay in cash."
Mã QuantConnect của nguồn: SMA 210 nến ngày; mỗi đầu tháng, ETF có giá >
SMA thì mua (chia đều), ngược lại bán về tiền mặt. Không SL, không TP.
Chỉ chiều mua (long-only).

Chuyển sang hệ OG (khác nguồn — lựa chọn của tôi, ghi rõ để backtest):
    1. "210 nến ngày, xét mỗi tháng" → SMA_PERIOD bar, xét ở MỖI bar đóng của
       timeframe đang chạy (H1–H4, M10–M45). Nguồn không có căn cứ cho chu kỳ
       nào trên khung giờ này — để backtest chọn.
    2. Chiều SELL do tôi thêm (ALLOW_SHORT): close < SMA → bán. Khi tắt, dưới
       SMA chỉ đóng vị thế mua (đúng nguồn: "stay in cash").
    3. Nguồn: một danh mục 5 ETF chia đều; ở đây mỗi symbol xét độc lập.

Chỉ báo (trend_ma) tính ở indicator.add_sma_trend_indicators.

Tín hiệu (tại lúc bar ĐÓNG, chỉ dùng dữ liệu đến bar đó):
    Trạng thái mong muốn tại bar t:
        close > trend_ma            → +1 (giữ mua)
        close < trend_ma            → -1 nếu ALLOW_SHORT, ngược lại 0 (tiền mặt)
        close == trend_ma           → giữ nguyên trạng thái cũ
    BUY  (signal = +1): trạng thái chuyển SANG +1 (giá đóng cửa cắt lên SMA; ở
                        bar hợp lệ đầu tiên, nếu close > SMA thì BUY luôn — như
                        nguồn mua ngay ở lần tái cân bằng đầu tiên).
    SELL (signal = -1): trạng thái chuyển SANG -1 (chỉ khi ALLOW_SHORT).
    Thoát (exit_signal = 1): đang giữ vị thế và trạng thái đổi khác (về 0, hoặc
        đảo chiều — khi đảo chiều thì cùng bar có exit_signal và signal mới).

Loại lệnh: market tại giá đóng cửa của bar tín hiệu (entry_price/entry_time do
levels.add_sma_trend_levels gắn, không tính ở file này).
SL: không có (nguồn không có). TP: không có. Thoát = giá đóng cửa cắt ngược SMA.

Tham số: SMA_PERIOD (dùng ở indicator), ALLOW_SHORT.

Đầu ra: bản sao df + position, signal, signal_reason,
    exit_signal, exit_price, exit_reason.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def detect_sma_trend_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL và thoát theo vị trí giá so với SMA dài.

    Args:
        df: DataFrame đã qua indicator.add_sma_trend_indicators(), sắp tăng dần
            theo bartime. Cần close, trend_ma.
        symbol, sess_mask: Không dùng — giữ cùng chữ ký detect_signals của pipeline.
        params: SMA_PERIOD (chỉ để ghi lý do), ALLOW_SHORT.
    """
    _ = symbol, sess_mask
    p = params or {}
    period = int(p["SMA_PERIOD"])
    allow_short = bool(p["ALLOW_SHORT"])

    out = df.reset_index(drop=True).copy()
    c = out["close"].astype(float).to_numpy()
    ma = out["trend_ma"].astype(float).to_numpy()

    n = len(out)
    position = np.zeros(n, dtype=int)
    signal = np.zeros(n, dtype=int)
    exit_signal = np.zeros(n, dtype=int)
    exit_price = np.full(n, np.nan)
    signal_reason = np.full(n, "", dtype=object)
    exit_reason = np.full(n, "", dtype=object)

    pos = 0
    for i in range(n):
        if np.isnan(ma[i]):
            position[i] = pos
            continue

        # =====================================================================
        # QUY TẮC CHIẾN LƯỢC — chỉ sửa khối này khi đổi ý tưởng.
        # =====================================================================
        if c[i] > ma[i]:
            desired = 1
        elif c[i] < ma[i]:
            desired = -1 if allow_short else 0
        else:
            desired = pos
        # =====================================================================

        if desired != pos:
            if pos != 0:
                exit_signal[i] = 1
                exit_price[i] = c[i]
                exit_reason[i] = (
                    f"close moved below SMA{period}" if pos == 1 else f"close moved above SMA{period}"
                )
            if desired == 1:
                signal[i] = 1
                signal_reason[i] = f"close above SMA{period} (state turned long)"
            elif desired == -1:
                signal[i] = -1
                signal_reason[i] = f"close below SMA{period} (state turned short; mirrored, not in source)"
            pos = desired
        position[i] = pos

    out["position"] = position
    out["signal"] = signal
    out["signal_reason"] = signal_reason
    out["exit_signal"] = exit_signal
    out["exit_price"] = exit_price
    out["exit_reason"] = exit_reason
    return out
