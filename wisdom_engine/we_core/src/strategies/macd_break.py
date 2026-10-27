"""
Chiến lược MACD Break — CHỈ logic quyết định tín hiệu BUY/SELL.

Mô tả:
    MACD Break = phá đỉnh/đáy giá đóng cửa của N nến gần nhất, xác nhận bằng
    MACD Histogram cùng chiều. File này có đúng 1 việc: phát hiện BUY/SELL
    (detect_macd_break_signals) — quyết định KHI NÀO và HƯỚNG NÀO.

    Chiến lược được biến tấu từ mẫu "Trend Following Effect in Stocks"
    (research_notes S005, xem lịch sử git của breakout_atr.py), nhưng đã bỏ
    toàn bộ phần quản lý lệnh của nguồn (ATR trailing stop, trạng thái đang
    giữ lệnh, tín hiệu thoát): thoát lệnh/SL/TP thuộc hệ thống đặt lệnh, không
    thuộc wisdom_engine.

    Không giữ tham số, không tính chỉ báo, KHÔNG tính entry:
    - Tham số (LOOKBACK_BARS, MACD_*, ATR_PERIOD...) sống ở
      configuration.normalize_macd_break_params — hàm ở đây luôn nhận `params`
      đã hoàn chỉnh.
    - Chỉ báo (prior_high_close/prior_low_close/macd_h/atr) tính ở
      indicator.add_macd_break_indicators, chạy trước file này.
    - Entry tham chiếu ở entries.add_macd_break_entries, chạy sau file này.

Đầu ra:
    detect_macd_break_signals(df, symbol, params): df + raw_signal/signal và
    reason tương ứng. Khi không bật trend filter, signal = raw_signal.
"""

from __future__ import annotations

import pandas as pd
from wisdom_engine.we_core.src.rules.trend_tf import apply_signal_filters

_BUY_REASON = "close broke above prior highest close, MACD histogram > 0"
_SELL_REASON = "close broke below prior lowest close, MACD histogram < 0"


def detect_macd_break_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL theo logic MACD Break.

    Điều kiện BUY (signal = +1):
        1. close >= prior_high_close  ← phá đỉnh close của N nến TRƯỚC (không
                                         gồm nến hiện tại), bằng đỉnh cũng tính.
        2. macd_h > 0                 ← MACD Histogram dương.

    Điều kiện SELL (signal = -1):
        1. close <= prior_low_close   ← phá đáy close của N nến trước.
        2. macd_h < 0                 ← MACD Histogram âm.

    macd_h đúng bằng 0 thì không có tín hiệu.

    Lọc lặp cùng chiều (alternating.py; thêm 2026-10-13 theo yêu cầu
    người dùng, cùng cơ chế với Combo; từ 2026-10-14 bật/tắt bằng cờ
    `strategies.macd_break.alternating_filter_enabled` trong config, mặc định
    bật): chỉ bắn tín hiệu ĐẦU TIÊN khi hướng đổi chiều. Khi giá chạy một pha,
    nến nào cũng phá đỉnh/đáy mới nên nến nào cũng thoả điều kiện (đo 2026-10-13
    trên 88 cặp từ 2022: 92,5% tín hiệu cùng chiều với tín hiệu liền trước); bộ
    lọc chỉ giữ nến đầu tiên, các nến cùng chiều sau đó bị giữ 0 cho tới khi có
    một setup ngược chiều. Cờ tắt thì mỗi nến thoả điều kiện đều bắn (hành vi
    trước 2026-10-13). Bộ lọc chỉ nhớ HƯỚNG CỦA SETUP GẦN NHẤT, không biết lệnh
    thật còn mở hay đã dính SL. Ngoài điều đó không có trạng thái nào khác:
    thoát lệnh, SL/TP, vào lại là việc của hệ thống đặt lệnh.

    Hai bộ lọc (lọc lặp cùng chiều rồi lọc trend) do rules/trend_tf.apply_signal_filters
    điều phối; chiến lược chỉ truyền setup và câu lý do của mình. Thứ tự lọc, chính
    sách theo khối trend (cơ chế dời tín hiệu) và hệ quả lên DB0/DB1 (raw_signal =
    DB0, signal = DB1; trend tắt thì signal == raw_signal) mô tả ở docstring của
    hàm đó. Setup của macd_break là TRẠNG THÁI (còn đúng ở nhiều nến liền nhau) nên
    nến được phép bắn là chính nến còn setup. Các điều cần biết khác nằm ở docstring
    của alternating.py. Riêng macd_break: trong 300 nến warm-up của cửa sổ làm mới
    worker chỉ 200 nến còn nhớ được setup (100 nến đầu dành cho LOOKBACK_BARS); đo
    2026-10-16 (88 cặp, từ 2022) có 34 khoảng giữa hai setup liên tiếp cùng chiều
    dài hơn 200 nến (lớn nhất 288).

    Điều kiện tiên quyết: prior_high_close, prior_low_close, macd_h, atr đều
    không NaN (qua warmup).

    Args:
        df: DataFrame đã qua indicator.add_macd_break_indicators().
        symbol, sess_mask: Không dùng — giữ cùng chữ ký detect_signals.
        params: Dict tham số đã validate (chỉ cần cho trend filter).

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, signal, signal_reason,
        trend_filter_status.
    """
    _ = symbol, sess_mask
    out = df.copy()

    valid = (
        out["prior_high_close"].notna()
        & out["prior_low_close"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC MACD BREAK — chỉ sửa 2 khối điều kiện dưới đây khi đổi
    # ý tưởng. Ngoài chúng chỉ còn lọc lặp cùng chiều (alternating.py,
    # cờ trong config). Tham số, chỉ báo và entry đều ở file khác (xem docstring
    # đầu file).
    # =========================================================================
    buy_cond = valid & (out["close"] >= out["prior_high_close"]) & (out["macd_h"] > 0)
    sell_cond = valid & (out["close"] <= out["prior_low_close"]) & (out["macd_h"] < 0)
    # =========================================================================

    return apply_signal_filters(
        out, params, buy_setup=buy_cond, sell_setup=sell_cond, buy_reason=_BUY_REASON, sell_reason=_SELL_REASON
    )
