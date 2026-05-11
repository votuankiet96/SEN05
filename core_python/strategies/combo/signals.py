"""
Tính toán chỉ báo và phát hiện tín hiệu cho chiến lược Combo.

Mô tả:
    Chiến lược Combo kết hợp 3 điều kiện để xác nhận tín hiệu:
    1. MA crossover (giá đóng cửa vượt qua đường MA)
    2. Nến xác nhận hướng (bullish hoặc bearish candle)
    3. MACD Histogram xác nhận momentum

    Tùy chọn: lọc theo R:R tối thiểu (MIN_RR) và giờ giao dịch (SESSION_HOURS_UTC).

Đầu vào:
    DataFrame OHLCV đã load (từ data/loader.py).

Đầu ra:
    DataFrame với các cột bổ sung:
    - Sau add_combo_indicators: ma, macd_h, atr, prev_close, prev_ma
    - Sau detect_combo_signals: signal (+1/-1/0), rr, signal_reason

Giả định giao dịch:
    Tín hiệu được tính từ bar đã đóng hoàn toàn.
    Không có lookahead — chỉ dùng dữ liệu của bar hiện tại và bar trước (shift(1)).
"""

from __future__ import annotations

import pandas as pd

from core_python.indicators.core import atr, macd_hist, sma, safe_ratio
from core_python.strategies.combo.config import (
    get_indicator_params,
)
from core_python.strategies.combo.trend_filter import apply_combo_htf_filter


def _rr_filter_enabled(min_rr: object) -> bool:
    """Trả về True nếu filter R:R đang bật (MIN_RR không phải None hoặc NaN)."""
    if min_rr is None:
        return False
    try:
        return not bool(pd.isna(min_rr))
    except Exception:
        return True


def _session_mask(df: pd.DataFrame, hours_utc: list[int] | None) -> pd.Series:
    """
    Tạo boolean mask — True cho các bar nằm trong giờ giao dịch cho phép.

    Args:
        df: DataFrame chứa cột "bartime" (UTC-naive datetime).
        hours_utc: List giờ UTC được phép (0-23). Rỗng = cho phép tất cả.

    Returns:
        pd.Series[bool] cùng index với df.
        True nếu hours_utc rỗng (không giới hạn giờ) hoặc giờ bar nằm trong danh sách.
    """
    if not hours_utc:
        return pd.Series(True, index=df.index)
    if "bartime" not in df.columns:
        return pd.Series(True, index=df.index)
    bar_hours = pd.to_datetime(df["bartime"], errors="coerce").dt.hour
    return bar_hours.isin({int(hour) for hour in hours_utc})


def add_combo_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Thêm các cột chỉ báo cần thiết cho chiến lược Combo vào DataFrame.

    Các cột được thêm:
        ma:         Simple Moving Average của close (chu kỳ MA_PERIOD).
        macd_h:     MACD Histogram (fast EMA − slow EMA, rồi lấy EMA signal).
        atr:        Average True Range theo Wilder (chu kỳ ATR_PERIOD).
        prev_close: Giá đóng cửa bar trước (close.shift(1)) — dùng để phát hiện crossover.
        prev_ma:    MA bar trước (ma.shift(1)) — dùng để phát hiện crossover.

    Args:
        df: DataFrame OHLCV với cột [bartime, open, high, low, close, volume].
        params: Dict tham số (nếu None, dùng defaults từ get_indicator_params()).

    Returns:
        Bản sao của df với 5 cột bổ sung. Input không bị thay đổi.

    Giả định giao dịch:
        Các cột NaN ở đầu chuỗi (do warmup period) được xử lý bởi detect_combo_signals().
    """
    p = {**get_indicator_params(), **(params or {})}
    out = df.copy()
    out["ma"] = sma(out["close"], int(p["MA_PERIOD"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(p["MACD_FAST"]),
        slow=int(p["MACD_SLOW"]),
        signal=int(p["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(p["ATR_PERIOD"]))
    out["prev_close"] = out["close"].shift(1)
    out["prev_ma"] = out["ma"].shift(1)
    return out


def detect_combo_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL theo logic chiến lược Combo.

    Điều kiện BUY (signal = +1):
        1. prev_close <= prev_ma AND close > ma   ← MA crossover (vượt lên)
        2. close > open                           ← Nến tăng (bullish candle)
        3. macd_h > 0                             ← MACD histogram dương
        4. rr >= MIN_RR (nếu filter bật)         ← R:R đủ tốt

    Điều kiện SELL (signal = -1): ngược lại với BUY.
        1. prev_close >= prev_ma AND close <= ma  ← MA crossover (xuống)
        2. close < open                           ← Nến giảm (bearish candle)
        3. macd_h < 0                             ← MACD histogram âm
        4. rr >= MIN_RR (nếu filter bật)

    Điều kiện tiên quyết (valid mask):
        - Tất cả chỉ báo không phải NaN (qua warmup period).
        - Bar nằm trong session_hours_utc cho phép.

    Args:
        df: DataFrame đã qua add_combo_indicators().
        symbol: Không dùng trực tiếp — symbol params đã merged vào params từ normalize_params().
        params: Dict tham số đã validate (cần X, KTP, MIN_RR, SESSION_HOURS_UTC).
        sess_mask: Boolean mask session tùy chỉnh (None = tính lại từ params).

    Returns:
        Bản sao df với các cột bổ sung:
            signal:        +1 (BUY), -1 (SELL), 0 (không có tín hiệu).
            rr:            Risk/Reward ratio làm tròn 2 chữ số thập phân.
            signal_reason: Chuỗi mô tả lý do tín hiệu (chỉ set khi signal != 0).

    Giả định giao dịch:
        sl_dist = high - low + 2*X (khoảng cách SL dựa trên range bar + buffer).
        tp_dist = KTP * ATR.
        rr = tp_dist / sl_dist (dùng safe_ratio để tránh chia 0).
    """
    out = df.copy()
    p = {**get_indicator_params(), **(params or {})}
    x = float(p.get("X", 0.0) or 0.0)
    ktp = float(p["KTP"])
    min_rr = p.get("MIN_RR")

    if sess_mask is None:
        sess_mask = _session_mask(out, p.get("SESSION_HOURS_UTC", []))
    sess_mask = pd.Series(sess_mask, index=out.index).fillna(False).astype(bool)

    # Mask "valid" đảm bảo tất cả chỉ báo đã qua warmup và session hợp lệ
    valid = (
        sess_mask
        & out["ma"].notna()
        & out["prev_ma"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )

    # Crossover lên: close vượt MA từ dưới (bar trước close <= MA, bar này close > MA)
    cross_up = (out["prev_close"] <= out["prev_ma"]) & (out["close"] > out["ma"])
    # Crossover xuống: close xuống dưới MA (bar trước close >= MA, bar này close <= MA)
    cross_down = (out["prev_close"] >= out["prev_ma"]) & ~(out["close"] > out["ma"])

    buy_cond = valid & cross_up & (out["close"] > out["open"]) & (out["macd_h"] > 0)
    sell_cond = valid & cross_down & (out["close"] < out["open"]) & (out["macd_h"] < 0)

    # sl_dist: khoảng cách từ entry đến SL = range bar + 2*buffer (cả hai phía)
    sl_dist = out["high"] - out["low"] + 2 * x
    tp_dist = ktp * out["atr"]
    rr = safe_ratio(tp_dist, sl_dist)

    if _rr_filter_enabled(min_rr):
        rr_ok = rr >= float(min_rr)
    else:
        # Không lọc R:R — tất cả tín hiệu đều hợp lệ
        rr_ok = pd.Series(True, index=out.index)

    out["raw_signal"] = 0
    out.loc[buy_cond & rr_ok, "raw_signal"] = 1
    out.loc[sell_cond & rr_ok, "raw_signal"] = -1
    out["rr"] = rr.round(2)
    out["raw_signal_reason"] = ""
    out.loc[out["raw_signal"].eq(1), "raw_signal_reason"] = "close crossed above MA, bullish candle, MACD histogram > 0"
    out.loc[out["raw_signal"].eq(-1), "raw_signal_reason"] = "close crossed below MA, bearish candle, MACD histogram < 0"
    out["signal"] = out["raw_signal"]
    out["signal_reason"] = out["raw_signal_reason"]
    out["filter_reason"] = ""
    if bool(p.get("HTF_TREND_ENABLED", False)):
        out = apply_combo_htf_filter(out, p)
    return out
