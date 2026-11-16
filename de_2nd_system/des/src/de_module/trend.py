"""
Trend của DE: các YẾU TỐ trend TÁCH BIỆT. Mỗi yếu tố là một hàm ``bias_<tên>`` nhận nến của khung của nó và trả hướng của TỪNG nến: +1 (chỉ BUY), -1 (chỉ SELL), 0 (trung tính):

    bias_macd(frame, *, fast, slow, signal)   +1 nếu MACD Histogram > 0, -1 nếu < 0, 0 nếu bằng 0.
    bias_sma(frame, *, period)                +1 nếu close > SMA(period), -1 nếu close < SMA, 0 nếu bằng hoặc chưa đủ nến cho SMA.
    bias_break(frame, *, lookback)            chiều của lần PHÁ gần nhất, giữ nguyên cho tới khi có lần phá ngược; trước lần phá đầu tiên là 0.
                                              Đỉnh = High cao nhất, đáy = Low thấp nhất của ``lookback`` nến TRƯỚC (không gồm nến hiện tại); nến phá lên khi High của nó vượt đỉnh,
                                              phá xuống khi Low của nó thủng đáy. Nến phá cả hai phía cùng lúc thì giữ nguyên chiều cũ.

Thêm một yếu tố mới = thêm một hàm ``bias_<tên>`` ở đây rồi khai nó ở ``trends:`` của des_config.yaml (``type: <tên>`` và tham số); không phải sửa file nào khác. Chữ ký hàm (tham số keyword-only, không có giá trị
mặc định) là chỗ khai tham số: config khai thừa hoặc thiếu tham số bị từ chối lúc nạp. Hàm tự raise ValueError với tham số vô lý (vd MACD nhanh >= chậm) để config sai không được nạp.

File này chỉ làm MỘT việc: nhận nến của khung trend rồi trả hướng của từng nến. Không biết chiến lược nào đang dùng nó; không lọc tín hiệu và không ghép với nến vào lệnh (việc của decision_engine.py); không đọc
config và không giữ tham số mặc định. Công thức chỉ báo ở indicator.py (sma, macd_hist: phụ thuộc một chiều).

Ghi chú: SMA là NaN cho tới khi đủ ``period`` nến nên các nến đó luôn trung tính. MACD Histogram (EMA) không có đoạn NaN nhưng những nến đầu chưa "ấm"; việc cấp đủ nến warm-up trước khi gọi là của người gọi.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from des.src.indicator import macd_hist, sma


def _direction(positive: pd.Series, negative: pd.Series) -> np.ndarray:
    """+1 ở nến ``positive`` đúng, -1 ở nến ``negative`` đúng, 0 ở nến còn lại (so sánh với NaN luôn False nên chưa đủ dữ liệu thì trung tính)."""
    return np.where(positive, 1, np.where(negative, -1, 0)).astype(np.int64)


def bias_macd(frame: pd.DataFrame, *, fast: int, slow: int, signal: int) -> np.ndarray:
    """Hướng theo dấu của MACD Histogram (chu kỳ ``fast``/``slow``/``signal``) của close."""
    if fast >= slow:
        raise ValueError(f"macd: fast ({fast}) must be smaller than slow ({slow})")
    hist = macd_hist(pd.to_numeric(frame["close"], errors="coerce"), fast=fast, slow=slow, signal=signal)
    return _direction(hist > 0, hist < 0)


def bias_sma(frame: pd.DataFrame, *, period: int) -> np.ndarray:
    """Hướng theo close so với SMA(``period``) của close."""
    close = pd.to_numeric(frame["close"], errors="coerce")
    average = sma(close, period)
    return _direction(close > average, close < average)


def bias_break(frame: pd.DataFrame, *, lookback: int) -> np.ndarray:
    """Hướng theo lần phá đỉnh/đáy của ``lookback`` nến trước gần nhất (xem docstring đầu file)."""
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    top = high.shift(1).rolling(lookback).max()
    bottom = low.shift(1).rolling(lookback).min()
    broke_up, broke_down = (high > top).to_numpy(), (low < bottom).to_numpy()
    changes = np.where(broke_up & ~broke_down, 1.0, np.where(broke_down & ~broke_up, -1.0, np.nan))
    return pd.Series(changes).ffill().fillna(0).to_numpy().astype(np.int64)
