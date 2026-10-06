"""
Chiến lược Combo — CHỈ logic quyết định tín hiệu BUY/SELL.

Mô tả:
    Combo V0 = MA + MACD Histogram + ATR breakout. File này có đúng 1 việc:
    phát hiện tín hiệu BUY/SELL (detect_combo_signals) — quyết định KHI NÀO
    và HƯỚNG NÀO, không quyết định vào giá bao nhiêu.

    Không giữ tham số mặc định, không tự merge/validate tham số, không tính
    chỉ báo, và KHÔNG tính entry/level:
    - Tham số (MA_PERIOD, X theo symbol, session hours...) sống ở
      core_python/configuration.py (normalize_combo_params) — hàm ở đây
      luôn nhận `params` là dict ĐÃ HOÀN CHỈNH, không tự suy ra default nào.
    - Chỉ báo (ma/macd_h/atr) tính ở core_python/indicator.py
      (add_combo_indicators), chạy trước detect_combo_signals() trong
      pipeline (xem core_python/configuration.py: run_strategy()).
    - Entry/tham chiếu level (add_combo_levels) — câu hỏi "vào giá nào" tách
      riêng ở core_python/levels.py, chạy SAU detect_combo_signals() trong
      cùng pipeline. Không phải việc của file này.

Đầu ra:
    detect_combo_signals(df, symbol, params): df + cột raw_signal/signal
    và reason tương ứng. Khi không bật trend filter, signal = raw_signal.
"""

from __future__ import annotations

import pandas as pd
from core_python.src.strategies.trend import apply_trend_filter, trend_filter_enabled


def detect_combo_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL theo logic chiến lược Combo.

    Điều kiện BUY (signal = +1):
        1. close > open                           ← Nến tăng (bullish candle)
        2. close > ma                             ← Close nằm trên MA20
        3. prev_close < ma                        ← Bar trước đóng cửa dưới MA (xác nhận vừa cắt lên)
        4. macd_h > 0                             ← MACD histogram dương

    Điều kiện SELL (signal = -1): ngược lại với BUY.

    Không có state machine giữ trạng thái giữa các bar (đã bỏ từ bản trước
    — xem lịch sử git nếu cần tham khảo `_alternating_signals`). Mỗi bar
    thỏa đúng 4 điều kiện trên là bắn tín hiệu, bất kể tín hiệu gần nhất
    trước đó là gì. Lý do: OG không theo dõi lệnh thật (không biết SL đã bị
    dính hay chưa), nên giữ giả định "còn đang ở trạng thái BUY tới khi có
    SELL" là sai — có thể bỏ lỡ tín hiệu thật sau khi lệnh cũ đã bị dừng lỗ
    từ lâu. OF (hệ thống đặt lệnh) là nơi quyết định vào lệnh ở tín hiệu
    nào, không phải OG.

    Điều kiện tiên quyết (valid mask):
        - Tất cả chỉ báo không phải NaN (qua warmup period, kể cả prev_close).
        - Bar nằm trong session_hours_utc cho phép.

    Args:
        df: DataFrame đã qua indicator.add_combo_indicators().
        symbol: Không dùng trực tiếp — symbol params đã merged vào params.
        params: Dict tham số đã validate đầy đủ (từ
            configuration.normalize_combo_params) — cần SESSION_HOURS_UTC.
        sess_mask: Boolean mask session tùy chỉnh (None = tính lại từ params).

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, raw_signal_reason,
        signal, signal_reason.
    """
    _ = symbol
    out = df.copy()
    p = params or {}

    if sess_mask is None:
        sess_mask = _session_mask(out, p.get("SESSION_HOURS_UTC", []))
    sess_mask = pd.Series(sess_mask, index=out.index).fillna(False).astype(bool)

    # Mask "valid" đảm bảo tất cả chỉ báo đã qua warmup và session hợp lệ
    valid = (
        sess_mask
        & out["ma"].notna()
        & out["macd_h"].notna()
        & out["open"].notna()
        & out["close"].notna()
        & out["prev_close"].notna()
    )

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC COMBO — chỉ sửa 2 khối điều kiện dưới đây khi đổi ý
    # tưởng chiến lược. Không có gì khác trong file này quyết định "khi nào
    # mua/bán" — tham số và entry/level đều ở file khác (xem docstring đầu file).
    # =========================================================================
    # Combo V0 BUY setup: bullish candle vừa cắt lên MA20 (prev_close dưới MA,
    # close hiện tại trên MA — xác nhận động lực tăng, không phải đã ở trên
    # MA từ trước), MACD histogram dương.
    buy_cond = (
        valid
        & (out["close"] > out["open"])
        & (out["close"] > out["ma"])
        & (out["prev_close"] < out["ma"])
        & (out["macd_h"] > 0)
    )
    # Combo V0 SELL setup: đối xứng — bearish candle vừa cắt xuống MA20.
    sell_cond = (
        valid
        & (out["close"] < out["open"])
        & (out["close"] < out["ma"])
        & (out["prev_close"] > out["ma"])
        & (out["macd_h"] < 0)
    )
    # =========================================================================

    out["raw_signal"] = 0
    out.loc[buy_cond, "raw_signal"] = 1
    out.loc[sell_cond, "raw_signal"] = -1
    out["raw_signal_reason"] = ""
    out.loc[out["raw_signal"].eq(1), "raw_signal_reason"] = (
        "bullish candle crossing above MA, MACD histogram > 0"
    )
    out.loc[out["raw_signal"].eq(-1), "raw_signal_reason"] = (
        "bearish candle crossing below MA, MACD histogram < 0"
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


# =============================================================================
# Cơ chế kỹ thuật hỗ trợ detect_combo_signals() — không phải luật chiến lược,
# không cần đụng tới khi chỉnh ý tưởng BUY/SELL ở trên.
# =============================================================================


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
