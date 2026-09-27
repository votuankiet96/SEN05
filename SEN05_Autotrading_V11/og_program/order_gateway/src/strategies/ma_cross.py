"""
Chiến lược MA Cross — CHỈ quyết định khi nào có tín hiệu BUY/SELL.

Mô tả dễ hiểu:
    MA Cross dùng 2 đường trung bình động SMA:
    - Fast SMA: đường nhanh, phản ứng nhanh hơn với giá.
    - Slow SMA: đường chậm, phản ứng chậm hơn với giá.

    Ý tưởng chính:
    - BUY khi Fast SMA cắt lên Slow SMA và MACD Histogram đang dương.
    - SELL khi Fast SMA cắt xuống Slow SMA và MACD Histogram đang âm.

File này KHÔNG làm các việc sau:
    - Không đọc dữ liệu (nến đến từ candle_reader.py).
    - Không giữ tham số mặc định như FAST_MA, SLOW_MA.
    - Không tự tính SMA, MACD Histogram hoặc ATR.
    - Không tính Entry/Stop Loss/Take Profit cuối cùng.

Vị trí của file trong pipeline:
    1. candle_reader.py lấy nến OHLCV từ Redis DB0.
    2. configuration.py chuẩn hóa tham số từ og_config.yaml.
    3. indicator.py thêm fast_ma, slow_ma, macd_h, atr.
    4. File này đọc các cột đó để tạo signal BUY/SELL.
    5. levels.py tính market entry, SL/TP và copy tham chiếu Dow sau khi
       signal đã có.

Đầu ra:
    detect_ma_cross_signals(...) trả về DataFrame mới có thêm:
    - raw_signal: tín hiệu gốc của MA Cross trước khi lọc trend.
    - signal: 1 = BUY, -1 = SELL, 0 = không có tín hiệu.
    - signal_reason: mô tả ngắn vì sao có tín hiệu.
"""

from __future__ import annotations

import pandas as pd

from order_gateway.src.strategies.trend import apply_trend_filter, trend_filter_enabled


def detect_ma_cross_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu MA Cross trên DataFrame đã có sẵn indicator.

    Điều kiện BUY:
        1. prev_fast_ma <= prev_slow_ma  ← Trước đó fast SMA chưa nằm trên slow SMA.
        2. fast_ma > slow_ma             ← Hiện tại fast SMA đã cắt lên slow SMA.
        3. macd_h > 0                    ← MACD Histogram dương, xác nhận động lực tăng.

    Điều kiện SELL:
        1. prev_fast_ma >= prev_slow_ma  ← Trước đó fast SMA chưa nằm dưới slow SMA.
        2. fast_ma < slow_ma             ← Hiện tại fast SMA đã cắt xuống slow SMA.
        3. macd_h < 0                    ← MACD Histogram âm, xác nhận động lực giảm.

    Điều kiện hợp lệ trước khi xét BUY/SELL:
        - fast_ma, slow_ma, prev_fast_ma, prev_slow_ma không được NaN.
        - macd_h không được NaN.
        - atr không được NaN để output vẫn giữ cùng ngôn ngữ biến động như bản cũ.

    Args:
        df: DataFrame đã qua indicator.add_ma_cross_indicators().
        symbol: Không dùng trực tiếp — giữ để hàm có cùng chữ ký với các strategy khác.
        params: Có thể chứa TREND_FILTER_ENABLED để lọc raw_signal theo trend_bias.
        sess_mask: Chưa dùng cho MA Cross — giữ để cùng khuôn pipeline.

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, raw_signal_reason,
        signal, signal_reason.
    """
    _ = symbol, sess_mask
    out = df.copy()

    valid = (
        out["fast_ma"].notna()
        & out["slow_ma"].notna()
        & out["prev_fast_ma"].notna()
        & out["prev_slow_ma"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC MA CROSS
    #
    # Đây là vùng quan trọng nhất của file. Nếu sau này đổi ý tưởng vào lệnh
    # của MA Cross, thường chỉ cần sửa điều kiện cross_up/cross_down và điều
    # kiện MACD xác nhận bên dưới. Các phần khác chỉ là chuẩn bị dữ liệu phụ.
    # =========================================================================
    cross_up = (out["prev_fast_ma"] <= out["prev_slow_ma"]) & (
        out["fast_ma"] > out["slow_ma"]
    )
    cross_down = (out["prev_fast_ma"] >= out["prev_slow_ma"]) & (
        out["fast_ma"] < out["slow_ma"]
    )

    out["raw_signal"] = 0
    out.loc[valid & cross_up & out["macd_h"].gt(0), "raw_signal"] = 1
    out.loc[valid & cross_down & out["macd_h"].lt(0), "raw_signal"] = -1
    # =========================================================================

    # raw_signal_reason -> signal_reason -> field `comment` của HASH trên Redis
    # DB1, OF đọc nguyên văn. Đổi chuỗi ở đây là đổi dữ liệu OF nhận được.
    out["raw_signal_reason"] = ""
    out.loc[out["raw_signal"].eq(1), "raw_signal_reason"] = (
        "fast SMA crossed above slow SMA; MACD histogram > 0"
    )
    out.loc[out["raw_signal"].eq(-1), "raw_signal_reason"] = (
        "fast SMA crossed below slow SMA; MACD histogram < 0"
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
