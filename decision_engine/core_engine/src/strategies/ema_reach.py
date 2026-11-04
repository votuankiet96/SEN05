"""
Chiến lược EMA Reach — CHỈ logic quyết định tín hiệu BUY/SELL.

Mô tả:
    EMA Reach kết hợp ba yếu tố (người dùng đặt tên và chốt ba yếu tố 2026-10-30, để trade khung ngắn M10–M45):
    xu hướng ngắn bằng EMA nhanh/chậm (13/34), cú phá đỉnh/đáy giá đóng cửa của 20 nến gần nhất làm điểm kích hoạt, và
    MACD Histogram cùng chiều xác nhận động lượng. File này có đúng 1 việc: phát hiện BUY/SELL (detect_ema_reach_signals).

    Không giữ tham số, không tính chỉ báo, KHÔNG tính entry:
    - Tham số (FAST_EMA, SLOW_EMA, LOOKBACK_BARS, MACD_*, ATR_PERIOD...) sống ở configuration.normalize_ema_reach_params —
      hàm ở đây luôn nhận `params` đã hoàn chỉnh.
    - Chỉ báo (ema_fast/ema_slow/prior_high_close/prior_low_close/macd_h/atr) tính ở indicator.add_ema_reach_indicators.
    - Entry tham chiếu ở rules/entries.add_ema_reach_entries (= close của nến tín hiệu).

Đầu ra:
    detect_ema_reach_signals(df, symbol, params): df + raw_signal/signal/signal_reason/trend_filter_status. Khi không bật
    trend filter, signal = raw_signal.
"""

from __future__ import annotations

import pandas as pd
from core_engine.src.rules.trend_tf import apply_signal_filters

_BUY_REASON = "EMA fast above EMA slow, close broke above prior highest close, MACD histogram > 0"
_SELL_REASON = "EMA fast below EMA slow, close broke below prior lowest close, MACD histogram < 0"


def detect_ema_reach_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL theo logic EMA Reach.

    Điều kiện BUY (signal = +1), cả ba cùng đúng ở một nến:
        1. ema_fast > ema_slow         ← xu hướng ngắn đang lên.
        2. close >= prior_high_close   ← phá đỉnh close của N nến TRƯỚC (không gồm nến hiện tại), bằng đỉnh cũng tính.
        3. macd_h > 0                  ← MACD Histogram dương.

    Điều kiện SELL (signal = -1):
        1. ema_fast < ema_slow.
        2. close <= prior_low_close    ← phá đáy close của N nến trước.
        3. macd_h < 0.

    EMA nhanh bằng EMA chậm (hoặc NaN) và macd_h đúng bằng 0 thì không có tín hiệu.

    Setup là TRẠNG THÁI (còn đúng ở nhiều nến liền nhau khi giá chạy một pha: nến nào cũng phá đỉnh mới) nên cùng khuôn với
    macd_break/ma_cross: lọc lặp cùng chiều (rules/alternating.py, cờ `strategies.ema_reach.alternating_filter_enabled`, mặc định
    bật) chỉ giữ nến đầu tiên của mỗi đợt, rồi bộ lọc trend (rules/trend_tf.py) giữ tín hiệu cùng chiều xu hướng khung lớn.
    Hai bộ lọc do rules/trend_tf.apply_signal_filters điều phối (thứ tự, chính sách theo khối trend và hệ quả lên DB0/DB1... xem
    docstring của hàm đó; raw_signal = DB0, signal = DB1..DB5; trend tắt thì signal == raw_signal). Chiến lược chỉ truyền setup
    và câu lý do của mình; nến được phép bắn là chính nến còn setup (hold_valid=None).

    Không có trạng thái nào khác: thoát lệnh, SL/TP, vào lại là việc của hệ thống đặt lệnh.

    Điều kiện tiên quyết: ema_fast, ema_slow, prior_high_close, prior_low_close, macd_h, atr đều không NaN (qua warm-up).

    Args:
        df: DataFrame đã qua indicator.add_ema_reach_indicators().
        symbol, sess_mask: Không dùng — giữ cùng chữ ký detect_signals.
        params: Dict tham số đã validate (chỉ cần cho các bộ lọc).

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, signal, signal_reason, trend_filter_status.
    """
    _ = symbol, sess_mask
    out = df.copy()

    valid = (
        out["ema_fast"].notna()
        & out["ema_slow"].notna()
        & out["prior_high_close"].notna()
        & out["prior_low_close"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC EMA REACH — chỉ sửa 2 khối điều kiện dưới đây khi đổi
    # ý tưởng. Ngoài chúng chỉ còn hai bộ lọc dùng chung. Tham số, chỉ báo và
    # entry đều ở file khác (xem docstring đầu file).
    # =========================================================================
    buy_cond = (
        valid
        & (out["ema_fast"] > out["ema_slow"])
        & (out["close"] >= out["prior_high_close"])
        & (out["macd_h"] > 0)
    )
    sell_cond = (
        valid
        & (out["ema_fast"] < out["ema_slow"])
        & (out["close"] <= out["prior_low_close"])
        & (out["macd_h"] < 0)
    )
    # =========================================================================

    return apply_signal_filters(
        out, params, buy_setup=buy_cond, sell_setup=sell_cond, buy_reason=_BUY_REASON, sell_reason=_SELL_REASON
    )
