"""
Chỉ báo kỹ thuật thuần của DE: nhận Series/DataFrame giá, trả Series chỉ báo.

File này CHỈ tính chỉ báo. Logic tín hiệu nằm ở de_module/ (MA Cross, trend), logic quyết định ở decision_engine.py.
    Chỉ báo thuần: sma, ema, macd_hist, atr.
    
Đầu vào là Series/DataFrame giá hoặc OHLCV (cột lowercase) do data_engine.py đọc từ Redis. Các hàm không có side effect và không sửa input tại chỗ.
File này không import gì trong src/: mọi file khác import nó, không có chiều ngược lại.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Trung bình động đơn giản: rolling window cố định, cần đủ ``period`` điểm mới có giá trị đầu tiên (các vị trí đầu là NaN)."""
    return series.astype(float).rolling(int(period)).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Trung bình động hàm mũ: pandas ewm với adjust=False (alpha = 2 / (period + 1)), không hiệu chỉnh bias ở đầu chuỗi."""
    return series.astype(float).ewm(span=int(period), adjust=False).mean()


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """
    Làm mượt Wilder đúng seed của TradingView (`ta.rma`): bar hợp lệ đầu tiên = SMA(period) của input, các bar sau mới chạy đệ quy alpha*x + (1-alpha)*prev với alpha = 1/period.

    Khác với `series.ewm(alpha=1/period, adjust=False)` thuần: cách đó seed bằng đúng 1 điểm dữ liệu đầu tiên nên lệch tới vài % so với TradingView ở các bar đầu và hội tụ chậm
    (đo thật: period=5 lệch 2,5% tại bar đầu, cần +24 bar mới về dưới 0,01%; period=14 lệch 4,2%, cần +81 bar). `min_periods=period` của cách đó chỉ CHE NaN, không seed lại bằng SMA.
    """
    period = max(1, int(period))
    values = series.astype(float)
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) < period:
        return out
    tail = values.iloc[period - 1 :].copy()
    tail.iloc[0] = values.iloc[:period].mean()
    out.iloc[period - 1 :] = tail.ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    return out


def macd_hist(series: pd.Series, *, fast: int, slow: int, signal: int) -> pd.Series:
    """MACD Histogram = đường MACD - đường signal, với MACD = EMA(fast) - EMA(slow) và signal = EMA(MACD, signal). Dương khi động lực tăng, âm khi giảm."""
    close = series.astype(float)
    macd_line = ema(close, int(fast)) - ema(close, int(slow))
    return macd_line - ema(macd_line, int(signal))


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range theo làm mượt Wilder. ``df`` cần các cột high, low, close; các bar đầu (chưa đủ ``period``) là NaN."""
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return _wilder_smooth(true_range, int(period))

