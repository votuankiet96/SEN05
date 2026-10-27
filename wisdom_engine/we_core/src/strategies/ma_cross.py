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
    - Không đọc dữ liệu từ SQL Server.
    - Không giữ tham số mặc định như FAST_MA, SLOW_MA.
    - Không tự tính SMA, MACD Histogram hoặc ATR.
    - Không tính Entry/Stop Loss/Take Profit cuối cùng.

Vị trí của file trong pipeline:
    1. db_connector.py lấy dữ liệu nến OHLCV.
    2. configuration.py chuẩn hóa tham số từ we_config.yaml.
    3. indicator.py thêm fast_ma, slow_ma, macd_h, atr.
    4. File này đọc các cột đó để tạo signal BUY/SELL.
    5. rules/entries.py tính market entry sau khi signal đã có.

Đổi luật 2026-10-16 (người dùng chốt: "signal buy sell chỉ đơn giản dựa trên SMA 13 lớn hơn/nhỏ
hơn SMA 34 cùng MACD hist dương/âm"): trước đó MA Cross chỉ bắn ở nến SMA CẮT nhau (MACD phải cùng dấu ngay
nến cắt) và có một cơ chế trend riêng (_apply_trend_wait, máy trạng thái chờ) — tức hai luật khác nhau tuỳ
có trend hay không (DB1 không nằm trong DB0, ~65% cặp tín hiệu liền nhau của DB1 cùng chiều). Nay chỉ còn
MỘT luật, đúng khuôn của combo/macd_break:

    setup (mọi nến thỏa điều kiện)
      -> lọc lặp cùng chiều trên MỌI setup (rules/alternating.py, cờ trong config) = raw_signal
      -> trend filter lọc raw_signal (rules/trend_tf.apply_trend_filter) = signal.
    (hai bước sau do rules/trend_tf.apply_signal_filters điều phối.)

(Thứ tự "xen kẽ trước, trend sau" được chốt lại ngày 2026-10-16 theo yêu cầu người dùng, cùng thứ tự với
combo/macd_break; đổi thứ tự là đổi tập tín hiệu DB1, xem CLAUDE.md mục 6.)

Hệ quả cần biết (đo 2026-10-16, 44 cặp M10–M45 từ 2022-01-01, xem research_notes T007 và F143):
    - Setup thô chiếm ~42% số nến. Vì thế lọc lặp cùng chiều là BẮT BUỘC: tắt cờ alternating_filter_enabled thì
      gần một nửa số nến trở thành tín hiệu.
    - Tín hiệu thô (DB0) là nến ĐẦU TIÊN của mỗi đợt setup (fast/slow và MACD khớp sau một setup ngược chiều). Khi trend
      bật, khối có chính sách first_aligned_per_run (rules/trend_tf.POLICY, hiện breakout và tstat) bắn đúng một tín hiệu mỗi
      đợt ở nến setup đầu tiên mà trend đã cùng chiều; khối không có chính sách này (knn) thì đợt nào có nến đầu lệch
      trend mất cả đợt, không chờ trend đổi chiều trong lúc đợt còn kéo dài. Hai tín hiệu liền nhau có thể cùng chiều
      (giữa chúng có đợt ngược chiều bị trend loại).
    - Trạng thái chống trùng chỉ nhớ trong cửa sổ đang tính (xem docstring rules/alternating.py).

Đầu ra:
    detect_ma_cross_signals(...) trả về DataFrame mới có thêm:
    - raw_signal: tín hiệu SAU lọc lặp cùng chiều trên mọi setup, TRƯỚC trend filter (cũng là tín hiệu khi không có trend).
    - signal: 1 = BUY, -1 = SELL, 0 = không có tín hiệu (lọc lặp cùng chiều rồi trend filter; với chính sách first_aligned_per_run
      signal có thể nằm giữa đợt nên không còn ⊆ raw_signal, xem rules/trend_tf.apply_signal_filters).
    - signal_reason: mô tả ngắn vì sao có tín hiệu.
    - trend_filter_status: trạng thái của bước trend filter (rules/trend_tf.apply_trend_filter).
"""

from __future__ import annotations

import pandas as pd
from wisdom_engine.we_core.src.rules.trend_tf import apply_signal_filters

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

    Hai bộ lọc (giống combo/macd_break): lọc lặp cùng chiều (alternating.py; cờ
    `strategies.ma_cross.alternating_filter_enabled` trong config) rồi lọc trend do rules/trend_tf.apply_signal_filters
    điều phối; chiến lược chỉ truyền setup và câu lý do của mình. Thứ tự lọc, chính sách theo khối trend (cơ chế dời
    tín hiệu) và hệ quả lên DB0/DB1 (raw_signal = DB0, signal = DB1) mô tả ở docstring của hàm đó. Trend tắt: signal ==
    raw_signal từng bit. Cờ lọc lặp cùng chiều tắt: bar nào có setup cũng bắn (rất nhiều tín hiệu liền nhau, chỉ dùng để
    xem/so sánh). Setup của ma_cross là TRẠNG THÁI (còn đúng ở nhiều nến liền nhau) nên nến được phép bắn là chính nến còn setup.

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

    return apply_signal_filters(
        out, params, buy_setup=buy_cond, sell_setup=sell_cond, buy_reason=_BUY_REASON, sell_reason=_SELL_REASON
    )
