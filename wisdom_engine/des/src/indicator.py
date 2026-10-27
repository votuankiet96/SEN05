"""
Xây dựng chỉ báo kỹ thuật cho des.

Mô tả:
    File này CHỈ tính chỉ báo (indicator) — không có logic phát hiện tín
    hiệu (signal) và không tính entry/level (entries). Hai phần đó là logic
    riêng của từng chiến lược và sống trong strategies/<tên>.py.

    Gồm 2 nhóm hàm:
    1. Chỉ báo thuần (sma/ema/wma/rma/macd_hist/atr) — nhận Series/
       DataFrame giá, trả về Series, không phụ thuộc chiến lược nào.
    2. add_*_indicators(df, params) — thêm cột chỉ báo (ma/macd_h/atr...)
       vào DataFrame OHLCV cho từng chiến lược, dùng lại nhóm (1). params
       luôn là dict đã resolve đầy đủ từ configuration.py.

    File này KHÔNG import bất kỳ gì từ strategies/ — chiều phụ thuộc chỉ
    một chiều: strategies/*.py không import ngược lại indicator.py.

    merge_trend_reference phục vụ đường 2 timeframe
    (configuration.run_strategy_with_trend_reference). live_worker đi đường đó
    cho bốn nguồn lọc trend (knn, breakout, zigzag, tstat) — xem
    configuration.trend_source_for(). Chỉ báo xác nhận xu hướng khung lớn
    (KNN, ...) KHÔNG nằm ở đây: mỗi phương pháp là một khối riêng trong
    trend_methods/ (xem rules/trend_tf.py).

Đầu vào:
    pd.Series/pd.DataFrame giá hoặc OHLCV từ candle_reader.py (Redis, db đầu vào).

Đầu ra:
    pd.Series (chỉ báo thuần) hoặc pd.DataFrame đã thêm cột chỉ báo.

Lưu ý:
    Các hàm ở đây không có side effect, không sửa input tại chỗ.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# =============================================================================
# Chỉ báo kỹ thuật thuần — dùng chung cho mọi chiến lược.
# =============================================================================


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


def wma(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Weighted Moving Average theo trọng số tăng dần.

    Hàm này phục vụ AI Trend Navigator KNN. Với window [x1, x2, ..., xn],
    điểm gần nhất có trọng số lớn nhất n.
    """
    period = max(1, int(period))
    weights = np.arange(1, period + 1, dtype=float)
    return series.astype(float).rolling(period, min_periods=period).apply(
        lambda values: float(np.dot(values, weights) / weights.sum()),
        raw=True,
    )


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """
    Làm mượt Wilder đúng seed của TradingView (`ta.rma`): bar hợp lệ đầu
    tiên = SMA(period) của input, các bar sau mới chạy đệ quy
    alpha*x + (1-alpha)*prev với alpha = 1/period.

    Khác với `series.ewm(alpha=1/period, adjust=False)` thuần — cách đó seed
    đệ quy bằng đúng 1 điểm dữ liệu đầu tiên thay vì SMA, nên lệch tới vài %
    so với TradingView ở các bar đầu và hội tụ chậm (đã đo thật: period=5
    lệch 2.5% tại bar đầu, cần +24 bar mới về dưới 0.01%; period=14 lệch
    4.2%, cần +81 bar). `min_periods=period` của cách cũ chỉ CHE NaN, không
    seed lại bằng SMA, nên không đủ để khớp Pine.
    """
    period = max(1, int(period))
    values = series.astype(float)
    n = len(values)
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if n < period:
        return out
    seed = values.iloc[:period].mean()
    tail = values.iloc[period - 1 :].copy()
    tail.iloc[0] = seed
    out.iloc[period - 1 :] = tail.ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    return out


def rma(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Wilder RMA, tương thích với cách làm mượt dùng trong ATR/Pine.
    """
    return _wilder_smooth(series, period)


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
    return _wilder_smooth(tr, int(period))


def timeframe_minutes(tf: object) -> int:
    """
    Chuyển mã timeframe phổ biến (M10, M45, H3...) thành số phút.
    """
    code = str(tf or "").strip().upper()
    if not code:
        raise ValueError(f"Unsupported timeframe '{tf}'.")
    amount_text = code[1:] or "1"
    if not amount_text.isdigit():
        raise ValueError(f"Unsupported timeframe '{tf}'.")
    amount = int(amount_text)
    unit = code[0]
    if amount <= 0:
        raise ValueError(f"Unsupported timeframe '{tf}'.")
    if unit == "M":
        return amount
    if unit == "H":
        return amount * 60
    if unit == "D":
        return amount * 60 * 24
    if unit == "W":
        return amount * 60 * 24 * 7
    raise ValueError(f"Unsupported timeframe '{tf}'.")


# =============================================================================
# add_*_indicators — thêm cột chỉ báo vào DataFrame OHLCV cho từng chiến lược.
# params luôn là dict đã resolve đầy đủ từ StrategySpec.normalize_params()
# trong configuration.py — không có default ngầm ở đây.
# =============================================================================


def add_combo_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Thêm các cột chỉ báo cần thiết cho chiến lược Combo vào DataFrame.

    Các cột được thêm:
        ma:         Simple Moving Average của close (chu kỳ MA_PERIOD).
        macd_h:     MACD Histogram (fast EMA − slow EMA, rồi lấy EMA signal).
        atr:        Average True Range theo Wilder (chu kỳ ATR_PERIOD).
        prev_close: Giá đóng cửa bar trước (close.shift(1)).

    Combo so `prev_close` với `ma` HIỆN TẠI để xác nhận vừa cắt lên/xuống,
    nên không cần cột `prev_ma` — đã bỏ vì không nơi nào đọc.

    Args:
        df: DataFrame OHLCV với cột [bartime, open, high, low, close, volume].
        params: Dict tham số đã resolve đầy đủ (từ
            configuration.normalize_combo_params) — cần MA_PERIOD, MACD_FAST,
            MACD_SLOW, MACD_SIGNAL, ATR_PERIOD.

    Returns:
        Bản sao của df với 4 cột bổ sung. Input không bị thay đổi.
    """
    out = df.copy()
    out["ma"] = sma(out["close"], int(params["MA_PERIOD"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(params["MACD_FAST"]),
        slow=int(params["MACD_SLOW"]),
        signal=int(params["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(params["ATR_PERIOD"]))
    out["prev_close"] = out["close"].shift(1)
    return out


def add_ma_cross_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Thêm các cột chỉ báo cần thiết cho chiến lược MA Cross vào DataFrame.

    Các cột được thêm:
        fast_ma, slow_ma:            SMA nhanh/chậm của close.
        macd_h:                      MACD Histogram.
        atr:                         Average True Range theo Wilder.

    (Trước 2026-10-16 còn 2 cột prev_fast_ma/prev_slow_ma để phát hiện cắt; luật
    ma_cross nay chỉ so fast_ma với slow_ma tại cùng nến nên đã bỏ.)

    Args:
        df: DataFrame OHLCV với cột [bartime, open, high, low, close, volume].
        params: Dict tham số đã resolve đầy đủ (từ
            configuration.normalize_ma_cross_params) — cần FAST_MA, SLOW_MA,
            MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD.

    Returns:
        Bản sao của df với 4 cột bổ sung. Input không bị thay đổi.
    """
    out = df.copy()
    out["fast_ma"] = sma(out["close"], int(params["FAST_MA"]))
    out["slow_ma"] = sma(out["close"], int(params["SLOW_MA"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(params["MACD_FAST"]),
        slow=int(params["MACD_SLOW"]),
        signal=int(params["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(params["ATR_PERIOD"]))
    return out


def add_macd_break_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Thêm các cột chỉ báo cần thiết cho chiến lược MACD Break vào DataFrame.

    Các cột được thêm:
        prior_high_close: đỉnh close của LOOKBACK_BARS bar TRƯỚC bar hiện tại
                          (close.shift(1).rolling(LOOKBACK_BARS).max()) — không
                          gồm bar hiện tại nên không nhìn trước.
        prior_low_close:  đáy close tương ứng.
        macd_h:           MACD Histogram (MACD_FAST/MACD_SLOW/MACD_SIGNAL).
        atr:              ATR Wilder chu kỳ ATR_PERIOD (xuất cho OF tính SL/TP).

    Args:
        df: DataFrame OHLC với cột bartime/close/high/low, sắp tăng dần theo bartime.
        params: Dict tham số đã resolve đầy đủ (từ
            configuration.normalize_macd_break_params) — cần LOOKBACK_BARS,
            MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD.

    Returns:
        Bản sao của df với 4 cột bổ sung. Input không bị thay đổi.
    """
    out = df.copy()
    lookback = int(params["LOOKBACK_BARS"])
    close = out["close"].astype(float)
    out["prior_high_close"] = close.shift(1).rolling(lookback).max()
    out["prior_low_close"] = close.shift(1).rolling(lookback).min()
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(params["MACD_FAST"]),
        slow=int(params["MACD_SLOW"]),
        signal=int(params["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(params["ATR_PERIOD"]))
    return out


def merge_trend_reference(entry_df: pd.DataFrame, trend_df: pd.DataFrame) -> pd.DataFrame:
    """
    Gắn trend bar đã đóng gần nhất vào từng entry bar, không lookahead.

    Điều kiện merge thực tế:
        trend_close_time <= entry bartime

    Cột được mang sang khung vào lệnh: trend_close_time, các cột trend_* riêng của từng khối xác
    nhận xu hướng (xem trend_methods/<tên>.py), rồi trend_bias (hướng đã ghép) ở cuối. Cột hướng
    riêng từng khối (trend_bias_<tên>) chỉ dùng nội bộ để ghép nên không được mang sang.
    """
    if entry_df.empty:
        return entry_df.copy()
    if trend_df.empty or "trend_close_time" not in trend_df.columns:
        return entry_df.copy()

    block_cols = [
        col
        for col in trend_df.columns
        if col.startswith("trend_")
        and col not in ("trend_close_time", "trend_bias")
        and not col.startswith("trend_bias_")
    ]
    trend_cols = ["trend_close_time", *block_cols, "trend_bias"]
    available = [col for col in trend_cols if col in trend_df.columns]
    entry = entry_df.sort_values("bartime").copy()
    trend = trend_df[available].sort_values("trend_close_time").copy()
    merged = pd.merge_asof(
        entry,
        trend,
        left_on="bartime",
        right_on="trend_close_time",
        direction="backward",
    )
    return merged.sort_values("bartime").reset_index(drop=True)
