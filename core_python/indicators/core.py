"""
Các hàm tính toán chỉ báo kỹ thuật dùng chung cho toàn bộ chiến lược.

Mô tả:
    Tất cả hàm trong module này là thuần (pure functions): nhận pd.Series hoặc
    pd.DataFrame, trả về pd.Series mới, không có side effect, không sửa input.

Đầu vào:
    Các pd.Series giá (close, high, low) hoặc pd.DataFrame OHLCV.

Đầu ra:
    pd.Series kết quả tính toán, cùng index với input.

Lưu ý:
    - NaN được lan truyền tự nhiên theo hành vi mặc định của pandas.
    - Không có validation kiểu dữ liệu — caller chịu trách nhiệm truyền đúng kiểu số.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Simple Moving Average (trung bình động đơn giản).

    Dùng rolling window kích thước cố định — cần đủ `period` điểm dữ liệu
    mới bắt đầu cho giá trị đầu tiên (min_periods = period mặc định).

    Args:
        series: Chuỗi giá (thường là close).
        period: Số bar tính trung bình.

    Returns:
        pd.Series SMA cùng index với input. Các vị trí đầu (< period bar)
        sẽ là NaN cho đến khi đủ dữ liệu.
    """
    return series.astype(float).rolling(int(period)).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Exponential Moving Average (trung bình động hàm mũ).

    Dùng pandas ewm với adjust=False — trọng số theo công thức EMA chuẩn,
    không có hiệu chỉnh bias ở đầu chuỗi.

    Args:
        series: Chuỗi giá.
        period: Số bar (span). Alpha = 2 / (period + 1).

    Returns:
        pd.Series EMA cùng index với input.
    """
    return series.astype(float).ewm(span=int(period), adjust=False).mean()


def ma(series: pd.Series, period: int, ma_type: str = "sma") -> pd.Series:
    """
    Dispatcher: trả về SMA hoặc EMA theo tên.

    Args:
        series: Chuỗi giá.
        period: Số bar.
        ma_type: "sma" hoặc "ema" (không phân biệt hoa/thường).

    Returns:
        pd.Series kết quả của SMA hoặc EMA.

    Raises:
        ValueError: Nếu ma_type không phải "sma" hoặc "ema".
    """
    name = str(ma_type).lower().strip()
    if name == "sma":
        return sma(series, period)
    if name == "ema":
        return ema(series, period)
    raise ValueError(f"Unsupported MA_TYPE '{ma_type}'. Use 'sma' or 'ema'.")


def macd_hist(
    series: pd.Series,
    *,
    fast: int,
    slow: int,
    signal: int,
) -> pd.Series:
    """
    Tính MACD Histogram = MACD Line − Signal Line.

    Công thức:
        MACD Line   = EMA(fast) − EMA(slow)
        Signal Line = EMA(MACD Line, signal)
        Histogram   = MACD Line − Signal Line

    Args:
        series: Chuỗi giá close.
        fast: Chu kỳ EMA nhanh (ví dụ: 5 hoặc 12).
        slow: Chu kỳ EMA chậm (ví dụ: 25 hoặc 26). Phải > fast.
        signal: Chu kỳ EMA tính đường signal (ví dụ: 5 hoặc 9).

    Returns:
        pd.Series histogram — dương khi momentum tăng, âm khi giảm.

    Giả định giao dịch:
        Giá trị histogram dương đồng nghĩa với momentum tăng.
        Chiến lược Combo dùng histogram > 0 làm điều kiện lọc BUY.
    """
    close = series.astype(float)
    macd_line = ema(close, int(fast)) - ema(close, int(slow))
    signal_line = ema(macd_line, int(signal))
    return macd_line - signal_line


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """
    Tính Average True Range (ATR) theo phương pháp làm mượt Wilder.

    True Range (TR) = max(high−low, |high−prev_close|, |low−prev_close|)
    ATR = EWM của TR với alpha = 1/period (tương đương Wilder's smoothing).

    Args:
        df: DataFrame chứa ít nhất các cột lowercase: "high", "low", "close".
        period: Số bar cho Wilder's smoothing. min_periods = period.

    Returns:
        pd.Series ATR cùng index với df. Các bar đầu tiên là NaN.

    Giả định giao dịch:
        ATR phản ánh biến động trung bình của thị trường.
        Dùng để tính TP (KTP × ATR trong Combo) và SL/TP (ATR_MULT trong MA Cross).
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / int(period), min_periods=int(period), adjust=False).mean()


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """
    Tính tỷ lệ numerator/denominator, xử lý chia cho 0 và giá trị vô cực.

    Quy trình:
        1. Thay denominator = 0 bằng NaN (tránh ZeroDivisionError).
        2. Chia. Kết quả inf/-inf được thay bằng NaN.

    Args:
        numerator: Tử số (pd.Series).
        denominator: Mẫu số (pd.Series, cùng index).

    Returns:
        pd.Series kết quả chia; các vị trí không hợp lệ là NaN.

    Giả định giao dịch:
        Dùng để tính Risk/Reward ratio. Nếu SL_distance = 0 (bar doji),
        R:R sẽ là NaN thay vì crash.
    """
    return (numerator / denominator.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
