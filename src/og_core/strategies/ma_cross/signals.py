"""
Tính toán chỉ báo và phát hiện tín hiệu cho chiến lược MA Cross.

Mô tả:
    Phát hiện điểm cắt (crossover/crossunder) giữa hai đường MA nhanh và MA chậm.
    Tín hiệu xuất hiện tại bar mà crossover xảy ra (dùng shift(1) để so sánh bar trước).

Đầu vào:
    DataFrame OHLCV đã load.

Đầu ra:
    DataFrame với các cột bổ sung:
    - Sau add_ma_cross_indicators: fast_ma, slow_ma, atr, prev_fast_ma, prev_slow_ma
    - Sau detect_ma_cross_signals: signal, ma_gap, ma_gap_atr, signal_reason

Giả định giao dịch:
    Tín hiệu dựa trên giá đóng cửa bar đã hoàn thành — không có lookahead.
    Entry KHÔNG phải tại bar tín hiệu mà tại bar tiếp theo (add_ma_cross_levels dùng shift(-1)).
"""

from __future__ import annotations

import pandas as pd

from og_core.indicators.core import atr, ma, safe_ratio
from og_core.strategies.ma_cross.config import (
    SESSION_HOURS_UTC,
    get_indicator_params,
)


def _session_mask(df: pd.DataFrame, hours_utc: list[int] | None) -> pd.Series:
    """
    Tạo boolean mask — True cho các bar nằm trong giờ giao dịch cho phép.

    Args:
        df: DataFrame chứa cột "bartime" (UTC-naive datetime).
        hours_utc: List giờ UTC được phép (0-23). Rỗng = cho phép tất cả.

    Returns:
        pd.Series[bool] cùng index với df.
    """
    if not hours_utc:
        return pd.Series(True, index=df.index)
    if "bartime" not in df.columns:
        return pd.Series(True, index=df.index)
    bar_hours = pd.to_datetime(df["bartime"], errors="coerce").dt.hour
    return bar_hours.isin({int(hour) for hour in hours_utc})


def add_ma_cross_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Thêm các cột chỉ báo cho chiến lược MA Cross.

    Các cột được thêm:
        fast_ma:      MA nhanh của close (chu kỳ FAST_MA, loại MA_TYPE).
        slow_ma:      MA chậm của close (chu kỳ SLOW_MA, loại MA_TYPE).
        atr:          ATR theo Wilder (chu kỳ ATR_PERIOD).
        prev_fast_ma: fast_ma bar trước (shift(1)) — dùng để phát hiện crossover.
        prev_slow_ma: slow_ma bar trước (shift(1)) — dùng để phát hiện crossover.

    Args:
        df: DataFrame OHLCV.
        params: Dict tham số (None = dùng defaults).

    Returns:
        Bản sao df với 5 cột bổ sung.
    """
    p = {**get_indicator_params(), **(params or {})}
    out = df.copy()
    out["fast_ma"] = ma(out["close"], int(p["FAST_MA"]), str(p["MA_TYPE"]))
    out["slow_ma"] = ma(out["close"], int(p["SLOW_MA"]), str(p["MA_TYPE"]))
    out["atr"] = atr(out, int(p["ATR_PERIOD"]))
    out["prev_fast_ma"] = out["fast_ma"].shift(1)
    out["prev_slow_ma"] = out["slow_ma"].shift(1)
    return out


def detect_ma_cross_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL tại điểm giao cắt của hai đường MA.

    Điều kiện BUY (signal = +1) — crossover lên:
        prev_fast_ma <= prev_slow_ma AND fast_ma > slow_ma
        (Bar trước: fast dưới slow; bar này: fast vượt lên trên slow)

    Điều kiện SELL (signal = -1) — crossunder xuống:
        prev_fast_ma >= prev_slow_ma AND fast_ma < slow_ma
        (Bar trước: fast trên slow; bar này: fast xuống dưới slow)

    Điều kiện tiên quyết (valid mask):
        - Tất cả MA và ATR không phải NaN.
        - Bar nằm trong session_hours_utc cho phép.

    Args:
        df: DataFrame đã qua add_ma_cross_indicators().
        symbol: Không sử dụng — placeholder để thống nhất interface.
        params: Không sử dụng trực tiếp (params đã ở trong indicator columns).
                Tham số session lấy từ SESSION_HOURS_UTC module-level default.
        sess_mask: Boolean mask session tùy chỉnh (None = dùng SESSION_HOURS_UTC).

    Returns:
        Bản sao df với các cột bổ sung:
            signal:        +1 (BUY), -1 (SELL), 0 (không có tín hiệu).
            ma_gap:        fast_ma - slow_ma (dương = fast trên slow).
            ma_gap_atr:    ma_gap / ATR (gap chuẩn hóa theo volatility).
            signal_reason: Mô tả ngắn gọn lý do tín hiệu.

    Giả định giao dịch:
        Tín hiệu phát sinh tại bar đóng cửa — không có lookahead.
        Entry sẽ ở bar TIẾP THEO (xem add_ma_cross_levels).
        Potential issue: session_hours_utc ở module level (không từ params) —
        nếu params override SESSION_HOURS_UTC, override đó sẽ không có hiệu lực ở đây.
    """
    _ = symbol
    _ = params
    out = df.copy()
    if sess_mask is None:
        # Lấy SESSION_HOURS_UTC từ module-level default, không từ params
        sess_mask = _session_mask(out, SESSION_HOURS_UTC)
    sess_mask = pd.Series(sess_mask, index=out.index).fillna(False).astype(bool)

    # Mask "valid": tất cả chỉ báo sẵn sàng và trong session
    valid = (
        sess_mask
        & out["fast_ma"].notna()
        & out["slow_ma"].notna()
        & out["prev_fast_ma"].notna()
        & out["prev_slow_ma"].notna()
        & out["atr"].notna()
    )

    # Crossover: bar trước fast <= slow, bar này fast > slow
    cross_up = (out["prev_fast_ma"] <= out["prev_slow_ma"]) & (
        out["fast_ma"] > out["slow_ma"]
    )
    # Crossunder: bar trước fast >= slow, bar này fast < slow
    cross_down = (out["prev_fast_ma"] >= out["prev_slow_ma"]) & (
        out["fast_ma"] < out["slow_ma"]
    )

    out["signal"] = 0
    out.loc[valid & cross_up, "signal"] = 1
    out.loc[valid & cross_down, "signal"] = -1

    # ma_gap_atr chuẩn hóa khoảng cách MA theo ATR — cho biết crossover mạnh hay yếu
    out["ma_gap"] = out["fast_ma"] - out["slow_ma"]
    out["ma_gap_atr"] = safe_ratio(out["ma_gap"], out["atr"])
    out["signal_reason"] = ""
    out.loc[out["signal"].eq(1), "signal_reason"] = "fast MA crossed above slow MA"
    out.loc[out["signal"].eq(-1), "signal_reason"] = "fast MA crossed below slow MA"
    return out
