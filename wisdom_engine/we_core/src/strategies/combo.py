"""
Chiến lược Combo — CHỈ logic quyết định tín hiệu BUY/SELL.

Mô tả:
    Combo V0 = MA + MACD Histogram + ATR breakout. File này có đúng 1 việc:
    phát hiện tín hiệu BUY/SELL (detect_combo_signals) — quyết định KHI NÀO
    và HƯỚNG NÀO, không quyết định vào giá bao nhiêu.

    Không giữ tham số mặc định, không tự merge/validate tham số, không tính
    chỉ báo, và KHÔNG tính entry/level:
    - Tham số (MA_PERIOD, X theo symbol, session hours...) sống ở
      wisdom_engine/configuration.py (normalize_combo_params) — hàm ở đây
      luôn nhận `params` là dict ĐÃ HOÀN CHỈNH, không tự suy ra default nào.
    - Chỉ báo (ma/macd_h/atr) tính ở wisdom_engine/indicator.py
      (add_combo_indicators), chạy trước detect_combo_signals() trong
      pipeline (xem wisdom_engine/configuration.py: run_strategy()).
    - Entry/tham chiếu level (add_combo_entries) — câu hỏi "vào giá nào" tách
      riêng ở entries.py, chạy SAU detect_combo_signals() trong
      cùng pipeline. Không phải việc của file này.

Đầu ra:
    detect_combo_signals(df, symbol, params): df + cột raw_signal/signal
    và reason tương ứng. Khi không bật trend filter, signal = raw_signal.
"""

from __future__ import annotations

import pandas as pd
from wisdom_engine.we_core.src.rules.alternating import apply_alternating_filter
from wisdom_engine.we_core.src.rules.trend_tf import (
    apply_trend_filter,
    trend_filter_enabled,
)

_BUY_REASON = "bullish candle crossing above MA, MACD histogram > 0"
_SELL_REASON = "bearish candle crossing below MA, MACD histogram < 0"


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
        5. tín hiệu gần nhất trước đó (nếu có) không phải BUY  ← lọc lặp cùng chiều (khi cờ bật)

    Điều kiện SELL (signal = -1): ngược lại với BUY (điều kiện 5: tín hiệu gần
    nhất trước đó không phải SELL).

    Lọc lặp cùng chiều (alternating.py; khôi phục 2026-10-11 theo yêu
    cầu người dùng, cho cả WE lẫn des; từ 2026-10-14 bật/tắt bằng cờ
    `strategies.combo.alternating_filter_enabled` trong config, mặc định bật):
    chỉ bắn tín hiệu ĐẦU TIÊN khi hướng đổi chiều. Nhiều bar liên tiếp cùng thỏa
    BUY thì chỉ bar BUY đầu tiên bắn signal = 1; các BUY sau bị giữ 0 cho tới khi
    xuất hiện một setup SELL (và ngược lại). Cơ chế này có từ 2026-08-26 (commit
    50a2643), bị bỏ 2026-09-11 (commit c74ffa9) vì hệ này không theo dõi lệnh
    thật nên giả định "còn đang ở trạng thái BUY tới khi có SELL" có thể bỏ lỡ
    tín hiệu thật sau khi lệnh cũ đã bị dừng lỗ; nay dựng lại để không còn nhiều
    tín hiệu cùng chiều. OF vẫn là nơi quyết định vào lệnh ở tín hiệu nào. Cờ tắt
    thì bar nào có setup cũng bắn.

    Thứ tự lọc (đổi lại 2026-10-16 theo yêu cầu người dùng, về cách "xen kẽ
    trước, trend sau" như trước 2026-10-15): lọc xen kẽ chạy TRƯỚC trên mọi
    setup, trend filter lọc SAU trên raw_signal đã xen kẽ (signal ⊆ raw_signal,
    DB1 ⊆ DB0). raw_signal vì thế vừa là kết quả không có trend vừa là tín hiệu
    TRƯỚC khi lọc trend; khi trend tắt thì signal == raw_signal. Hệ quả: một
    setup bị trend loại vẫn chiếm "lượt đầu" của hướng đó, nên DB1 có thể có hai
    tín hiệu liền nhau cùng chiều (giữa chúng có setup ngược chiều bị trend
    loại) và một đợt setup mà nến đầu lệch trend thì mất cả đợt. Các điều cần
    biết khác (trạng thái chỉ nhớ trong cửa sổ đang tính) nằm ở docstring của
    alternating.py.

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
        Bản sao df với các cột bổ sung: raw_signal, signal, signal_reason,
        trend_filter_status.
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
    # tưởng chiến lược. Ngoài chúng chỉ còn lọc lặp cùng chiều
    # (alternating.py, cờ trong config) quyết định "khi nào mua/bán" —
    # tham số và entry/level đều ở file khác (xem docstring đầu file).
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

    # raw_signal = tín hiệu sau lọc xen kẽ trên MỌI setup, TRƯỚC trend filter (thứ tự đổi lại 2026-10-16: xen kẽ
    # trước, trend sau). Dashboard dùng nó để so "raw -> filtered"; khi trend tắt thì signal == raw_signal.
    out["raw_signal"] = apply_alternating_filter(buy_cond, sell_cond, params)

    # signal: trend filter lọc raw_signal (giữ tín hiệu cùng chiều trend_bias đúng bar đó) -> signal ⊆ raw_signal.
    out = apply_trend_filter(out, params, raw_signal_col="raw_signal", output_col="signal")
    out["signal_reason"] = ""
    out.loc[out["signal"].eq(1), "signal_reason"] = _BUY_REASON
    out.loc[out["signal"].eq(-1), "signal_reason"] = _SELL_REASON
    if trend_filter_enabled(params):
        has_signal = out["signal"].fillna(0).astype(int).ne(0)
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
