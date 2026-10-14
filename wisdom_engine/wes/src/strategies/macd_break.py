"""
Chiến lược MACD Break — CHỈ logic quyết định tín hiệu BUY/SELL.

Mô tả:
    MACD Break = phá đỉnh/đáy giá đóng cửa của N nến gần nhất, xác nhận bằng
    MACD Histogram cùng chiều. File này có đúng 1 việc: phát hiện BUY/SELL
    (detect_macd_break_signals) — quyết định KHI NÀO và HƯỚNG NÀO.

    Bản sao của we_core/src/strategies/macd_break.py (wes là hệ con tách biệt,
    không import we_core); sửa luật ở một bên phải sửa cả bên kia. Chiến lược
    được biến tấu từ mẫu "Trend Following Effect in Stocks" nhưng đã bỏ toàn bộ
    phần quản lý lệnh của nguồn (ATR trailing stop, trạng thái đang giữ lệnh,
    tín hiệu thoát): thoát lệnh/SL/TP thuộc OF, không thuộc wes.

    Không giữ tham số, không tính chỉ báo, KHÔNG tính entry:
    - Tham số (LOOKBACK_BARS, MACD_*, ATR_PERIOD...) sống ở
      configuration.normalize_macd_break_params — hàm ở đây luôn nhận `params`
      đã hoàn chỉnh.
    - Chỉ báo (prior_high_close/prior_low_close/macd_h/atr) tính ở
      indicator.add_macd_break_indicators, chạy trước file này.
    - Entry tham chiếu ở levels.add_macd_break_levels, chạy sau file này.

Đầu ra:
    detect_macd_break_signals(df, symbol, params): df + raw_signal/signal và
    reason tương ứng. Khi không bật trend filter, signal = raw_signal.
"""

from __future__ import annotations

import pandas as pd
from wisdom_engine.wes.src.alternating import apply_alternating_filter
from wisdom_engine.wes.src.trend import (
    apply_trend_filter,
    trend_filter_enabled,
)


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

    Lọc lặp cùng chiều (alternating.py, bật/tắt bằng cờ
    `strategies.macd_break.alternating_filter_enabled` trong wes_config.yaml,
    mặc định bật): chỉ bắn tín hiệu ĐẦU TIÊN khi hướng đổi chiều. Khi giá chạy
    một pha, nến nào cũng phá đỉnh/đáy mới nên nến nào cũng thoả điều kiện; bộ
    lọc chỉ giữ nến đầu tiên, các nến cùng chiều sau đó bị giữ 0 cho tới khi có
    một setup ngược chiều. Cờ tắt thì mỗi nến thoả điều kiện đều bắn. Bộ lọc chỉ
    nhớ HƯỚNG CỦA SETUP GẦN NHẤT, không biết lệnh thật còn mở hay đã dính SL.
    Ngoài điều đó không có trạng thái nào khác: thoát lệnh, SL/TP, vào lại là
    việc của OF.

    Bộ lọc đứng TRƯỚC trend filter: raw_signal đã được lọc xen kẽ, rồi mới lọc
    trend (signal vẫn ⊆ raw_signal). Các điều cần biết khác (một setup bị trend
    loại vẫn chiếm "lượt đầu"; trạng thái chỉ nhớ trong cửa sổ đang tính — ở wes
    là 500 nến mỗi sự kiện) nằm ở docstring của alternating.py.

    Điều kiện tiên quyết: prior_high_close, prior_low_close, macd_h, atr đều
    không NaN (qua warmup): nến cuối của cửa sổ chỉ có tín hiệu khi cửa sổ có ít
    nhất LOOKBACK_BARS + 1 nến.

    Args:
        df: DataFrame đã qua indicator.add_macd_break_indicators().
        symbol, sess_mask: Không dùng — giữ cùng chữ ký detect_signals.
        params: Dict tham số đã validate (chỉ cần cho trend filter và cờ lọc).

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, raw_signal_reason,
        signal, signal_reason, trend_filter_status.
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

    out["raw_signal"] = apply_alternating_filter(buy_cond, sell_cond, params)
    # raw_signal_reason -> signal_reason -> field `comment` của HASH trên Redis
    # db đầu ra, OF đọc nguyên văn. Đổi chuỗi ở đây là đổi dữ liệu OF nhận được.
    out["raw_signal_reason"] = ""
    out.loc[out["raw_signal"].eq(1), "raw_signal_reason"] = (
        "close broke above prior highest close, MACD histogram > 0"
    )
    out.loc[out["raw_signal"].eq(-1), "raw_signal_reason"] = (
        "close broke below prior lowest close, MACD histogram < 0"
    )

    out = apply_trend_filter(out, params, raw_signal_col="raw_signal", output_col="signal")
    out["signal_reason"] = ""
    has_signal = out["signal"].fillna(0).astype(int).ne(0)
    out.loc[has_signal, "signal_reason"] = out.loc[has_signal, "raw_signal_reason"]
    if trend_filter_enabled(params):
        out.loc[has_signal, "signal_reason"] = (
            out.loc[has_signal, "signal_reason"] + "; aligned with trend reference"
        )
    return out
