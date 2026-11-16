"""
Chiến lược MA Cross: CHỈ quyết định khi nào có tín hiệu BUY/SELL.

Tín hiệu là SỰ KIỆN cắt nhau của hai SMA, xét trên hai nến liền nhau, xác nhận bằng MACD Histogram của chính nến cắt:
    BUY:   nến trước fast_ma <= slow_ma và nến này fast_ma > slow_ma (SMA nhanh vừa cắt lên trên SMA chậm) và macd_h > 0
    SELL:  nến trước fast_ma >= slow_ma và nến này fast_ma < slow_ma (SMA nhanh vừa cắt xuống dưới SMA chậm) và macd_h < 0
Nến đầu của bảng (không có nến trước) và nến còn NaN thì không có tín hiệu; macd_h bằng 0 hoặc NaN không xác nhận. Không có lọc lặp cùng chiều: lần cắt không được MACD xác nhận thì không có tín hiệu.

File này KHÔNG làm các việc sau:
    - Không đọc dữ liệu nến và không giữ tham số hay giá trị nào: chu kỳ SMA và MACD do configuration.py truyền vào ``detect`` (khai ở ``strategies.<tên>.params`` của des_config.yaml). Công thức chỉ báo ở indicator.py.
    - Không lọc theo trend, không dời tín hiệu, không tính giá vào lệnh hay atr: đó là việc của decision_engine.py.

Hợp đồng với configuration.py: file chiến lược có hàm ``detect(frame, *, <tham số>)`` nhận nến khung trigger và trả bản sao frame có thêm hai cột signal, signal_reason; chữ ký là chỗ khai tham số.
Đầu ra: detect (và luật thuần detect_ma_cross_signals trên bảng đã có cột chỉ báo) trả về bản sao có thêm hai cột
    signal:         +1 BUY, -1 SELL, 0 không có;
    signal_reason:  lý do ở nến có tín hiệu (field ``comment`` của tín hiệu cuối, OF đọc nguyên văn: đổi chuỗi là đổi dữ liệu OF nhận), rỗng ở nến khác.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from des.src.indicator import macd_hist, sma

_BUY_REASON = "fast SMA crossed above slow SMA"
_SELL_REASON = "fast SMA crossed below slow SMA"


def detect_ma_cross_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phát hiện các nến có tín hiệu MA Cross trên DataFrame đã có sẵn chỉ báo.

    Args:
        df: nến tăng dần đã có cột chỉ báo fast_ma, slow_ma, macd_h.

    Returns:
        Bản sao df với các cột bổ sung: signal, signal_reason.
    """
    out = df.copy()
    fast, slow, hist = out["fast_ma"], out["slow_ma"], out["macd_h"]
    prev_fast, prev_slow = fast.shift(1), slow.shift(1)
    valid = fast.notna() & slow.notna() & prev_fast.notna() & prev_slow.notna()

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC MA CROSS: đổi ý tưởng vào lệnh thì chỉ sửa 2 điều kiện dưới đây.
    # =========================================================================
    cross_up = valid & (prev_fast <= prev_slow) & (fast > slow) & (hist > 0)
    cross_down = valid & (prev_fast >= prev_slow) & (fast < slow) & (hist < 0)
    # =========================================================================

    out["signal"] = np.where(cross_up, 1, np.where(cross_down, -1, 0)).astype("int64")
    out["signal_reason"] = np.where(cross_up, _BUY_REASON, np.where(cross_down, _SELL_REASON, ""))
    return out


def detect(frame: pd.DataFrame, *, fast_ma: int, slow_ma: int, macd_fast: int, macd_slow: int, macd_signal: int) -> pd.DataFrame:
    """
    Tín hiệu MA Cross trên nến của khung trigger (cột close, bartime tăng dần): tính SMA nhanh/chậm và MACD Histogram rồi áp luật ``detect_ma_cross_signals``.

    Returns:
        Bản sao frame với hai cột bổ sung: signal, signal_reason (không mang cột chỉ báo theo).
    """
    if fast_ma >= slow_ma:
        raise ValueError(f"ma_cross: fast_ma ({fast_ma}) must be smaller than slow_ma ({slow_ma})")
    if macd_fast >= macd_slow:
        raise ValueError(f"ma_cross: macd_fast ({macd_fast}) must be smaller than macd_slow ({macd_slow})")
    close = pd.to_numeric(frame["close"], errors="coerce")
    indicators = frame.assign(fast_ma=sma(close, fast_ma), slow_ma=sma(close, slow_ma), macd_h=macd_hist(close, fast=macd_fast, slow=macd_slow, signal=macd_signal))
    decided = detect_ma_cross_signals(indicators)
    return frame.assign(signal=decided["signal"], signal_reason=decided["signal_reason"])
