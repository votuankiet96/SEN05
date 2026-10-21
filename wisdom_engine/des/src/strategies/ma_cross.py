"""
Chiến lược MA Cross — CHỈ quyết định khi nào có tín hiệu BUY/SELL.

Mô tả dễ hiểu:
    MA Cross dùng 2 đường trung bình động SMA:
    - Fast SMA: đường nhanh, phản ứng nhanh hơn với giá.
    - Slow SMA: đường chậm, phản ứng chậm hơn với giá.

    Ý tưởng chính (luật TRẠNG THÁI, đổi 2026-10-16 — xem phần "Đổi luật" bên dưới):
    - Setup BUY ở mọi nến có Fast SMA nằm TRÊN Slow SMA và MACD Histogram dương.
    - Setup SELL ở mọi nến có Fast SMA nằm DƯỚI Slow SMA và MACD Histogram âm.
    - Tín hiệu thật (signal) là setup còn lại sau lọc lặp cùng chiều rồi trend filter.

File này KHÔNG làm các việc sau:
    - Không đọc dữ liệu (nến đến từ candle_reader.py).
    - Không giữ tham số mặc định như FAST_MA, SLOW_MA.
    - Không tự tính SMA, MACD Histogram hoặc ATR.
    - Không tính Entry/Stop Loss/Take Profit cuối cùng.

Vị trí của file trong pipeline:
    1. candle_reader.py lấy nến OHLCV từ Redis (db đầu vào).
    2. configuration.py chuẩn hóa tham số từ des_config.yaml.
    3. indicator.py thêm fast_ma, slow_ma, macd_h, atr.
    4. File này đọc các cột đó để tạo signal BUY/SELL.
    5. rules/entries.py tính market entry sau khi signal đã có.

Đồng bộ với we_core 2026-10-16 (luật trạng thái): trước đó des giữ luật cũ — chỉ bắn ở nến SMA CẮT nhau (MACD phải cùng
dấu ngay nến cắt), không có lọc lặp cùng chiều. Nay chỉ còn MỘT luật, đúng khuôn của combo/macd_break:

    setup (mọi nến thỏa điều kiện)
      -> lọc lặp cùng chiều trên MỌI setup (rules/alternating.py, cờ trong config) = raw_signal
      -> trend filter lọc raw_signal (rules/trend_tf.apply_trend_filter, khớp đúng 1 bar) = signal.

(Thứ tự "xen kẽ trước, trend sau" cùng thứ tự với combo/macd_break; đổi thứ tự là đổi tập tín hiệu khi bật trend.)

Hệ quả cần biết (đo trên we_core 2026-10-16, 44 cặp M10–M45 từ 2022-01-01, xem research_notes T007 và F143):
    - Setup thô chiếm ~42% số nến. Vì thế lọc lặp cùng chiều là BẮT BUỘC: tắt cờ alternating_filter_enabled thì
      gần một nửa số nến trở thành tín hiệu.
    - Tín hiệu là nến ĐẦU TIÊN của mỗi đợt setup (fast/slow và MACD khớp sau một setup ngược chiều) và, khi trend
      bật, phải cùng chiều trend đúng nến đó: đợt nào có nến đầu lệch trend thì mất cả đợt (không chờ trend đổi
      chiều trong lúc đợt còn kéo dài), và hai tín hiệu liền nhau có thể cùng chiều.
    - Trạng thái chống trùng chỉ nhớ trong cửa sổ đang tính (xem docstring rules/alternating.py): khoảng giữa hai
      setup thô cùng chiều lớn nhất đo được là 72 nến, nằm gọn trong cửa sổ 500 nến của des.
    - Chuỗi lý do (field `comment` của HASH, OF đọc nguyên văn) đổi theo luật mới: "fast SMA above/below slow SMA;
      MACD histogram > 0 / < 0" (trước đó "fast SMA crossed above/below slow SMA; ...").

Đầu ra:
    detect_ma_cross_signals(...) trả về DataFrame mới có thêm:
    - raw_signal: tín hiệu SAU lọc lặp cùng chiều trên mọi setup, TRƯỚC trend filter (cũng là tín hiệu khi không có trend).
    - signal: 1 = BUY, -1 = SELL, 0 = không có tín hiệu (lọc lặp cùng chiều rồi trend filter; signal ⊆ raw_signal).
    - signal_reason: mô tả ngắn vì sao có tín hiệu.
    - trend_filter_status: trạng thái của bước trend filter (rules/trend_tf.apply_trend_filter).
"""

from __future__ import annotations

import pandas as pd
from wisdom_engine.des.src.rules.alternating import apply_alternating_filter
from wisdom_engine.des.src.rules.trend_tf import (
    apply_trend_filter,
    trend_filter_enabled,
)

_BUY_REASON = "fast SMA above slow SMA; MACD histogram > 0"
_SELL_REASON = "fast SMA below slow SMA; MACD histogram < 0"


def detect_ma_cross_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu MA Cross trên DataFrame đã có sẵn indicator.

    Điều kiện setup BUY (mỗi nến thỏa là một setup):
        1. fast_ma > slow_ma   ← Fast SMA đang nằm trên Slow SMA.
        2. macd_h > 0          ← MACD Histogram dương, xác nhận động lực tăng.

    Điều kiện setup SELL:
        1. fast_ma < slow_ma   ← Fast SMA đang nằm dưới Slow SMA.
        2. macd_h < 0          ← MACD Histogram âm, xác nhận động lực giảm.

    Bằng nhau (fast_ma == slow_ma hoặc macd_h == 0) thì không có setup.

    Điều kiện hợp lệ trước khi xét BUY/SELL:
        - fast_ma, slow_ma, macd_h không được NaN.
        - atr không được NaN để output vẫn giữ cùng ngôn ngữ biến động như bản cũ.

    Thứ tự lọc (giống combo/macd_break): lọc lặp cùng chiều (alternating.py; cờ
    `strategies.ma_cross.alternating_filter_enabled` trong config) chạy TRƯỚC trên mọi setup, trend filter lọc
    SAU trên raw_signal đã xen kẽ. raw_signal vì thế vừa là kết quả không có trend vừa là tín hiệu TRƯỚC khi lọc
    trend, và signal ⊆ raw_signal. Trend tắt: signal == raw_signal từng bit. Cờ lọc lặp cùng chiều
    tắt: bar nào có setup cũng bắn (rất nhiều tín hiệu liền nhau, chỉ dùng để xem/so sánh).

    Args:
        df: DataFrame đã qua indicator.add_ma_cross_indicators().
        symbol: Không dùng trực tiếp — giữ để hàm có cùng chữ ký với các strategy khác.
        params: Có thể chứa TREND_FILTER_ENABLED (lọc setup theo trend_bias) và
            ALTERNATING_FILTER_ENABLED (lọc lặp cùng chiều); thiếu key = tắt.
        sess_mask: Chưa dùng cho MA Cross — giữ để cùng khuôn pipeline.

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, signal, signal_reason,
        trend_filter_status.
    """
    _ = symbol, sess_mask
    out = df.copy()

    valid = (
        out["fast_ma"].notna()
        & out["slow_ma"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC MA CROSS (luật trạng thái, chốt 2026-10-16). Nếu sau này
    # đổi ý tưởng vào lệnh của MA Cross, thường chỉ cần sửa 2 điều kiện dưới đây;
    # trend và lọc lặp cùng chiều là lớp lọc gắn thêm (rules/), không nằm ở đây.
    # =========================================================================
    buy_cond = valid & (out["fast_ma"] > out["slow_ma"]) & (out["macd_h"] > 0)
    sell_cond = valid & (out["fast_ma"] < out["slow_ma"]) & (out["macd_h"] < 0)
    # =========================================================================

    # raw_signal = tín hiệu sau lọc lặp cùng chiều trên MỌI setup, TRƯỚC trend filter (xen kẽ trước, trend sau, như
    # combo/macd_break). Dashboard dùng nó để so "raw -> filtered"; khi trend tắt thì signal == raw_signal.
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
