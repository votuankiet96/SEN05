"""
Chiến lược Ichimoku (Ichimoku gốc) — CHỈ logic quyết định tín hiệu BUY/SELL.

Mô tả:
    Ichimoku Kinko Hyo gốc, ba điều kiện cùng đúng ở nến t (Sanyaku Kouten cho BUY, Gyakuten cho SELL). File này có đúng 1 việc: phát
    hiện BUY/SELL (detect_ichimoku_signals) — quyết định KHI NÀO và HƯỚNG NÀO.

    Bản sao của chiến lược ichimoku bên we_core (DE là hệ con tách biệt, không import we_core); sửa luật ở một bên phải sửa cả bên kia.
    Thêm vào DE 2026-10-28. CHƯA có backtest/P&L; bằng chứng bên ngoài cho bộ 9/26/52 trên nến M10–H4 rất yếu (ghi nhận từ we_core).

    Không giữ tham số, không tính chỉ báo, KHÔNG tính entry:
    - Tham số (TENKAN_PERIOD, KIJUN_PERIOD, SENKOU_B_PERIOD, DISPLACEMENT, ATR_PERIOD...) sống ở
      configuration.normalize_ichimoku_params — hàm ở đây luôn nhận `params` đã hoàn chỉnh.
    - Chỉ báo (tenkan, kijun, senkou_a, senkou_b, close_lag, atr) tính ở indicator.add_ichimoku_indicators, chạy trước file này.
    - Entry tham chiếu (= close) ở entries.add_ichimoku_entries, chạy sau file này.
    - Thoát lệnh, SL/TP: việc của hệ đặt lệnh (OF/OE), không thuộc DE.

Đầu ra:
    detect_ichimoku_signals(df, symbol, params): df + raw_signal/signal và signal_reason, trend_filter_status. Ichimoku KHÔNG có trend
    (configuration: trend_off_timeframes = mọi khung) nên signal == raw_signal, trend_filter_status = "disabled".
"""

from __future__ import annotations

import pandas as pd
from core_engine.src.rules.trend_tf import apply_signal_filters

# signal_reason -> field `comment` của HASH trên Redis db đầu ra, OF đọc nguyên văn. Đổi chuỗi ở đây là đổi dữ liệu OF nhận được.
# Không ghi số (9/26/52) vào chuỗi: các chu kỳ đọc từ de_config.yaml nên có thể đổi.
_BUY_REASON = "Tenkan > Kijun, close above the lagging close (Chikou), close above the cloud"
_SELL_REASON = "Tenkan < Kijun, close below the lagging close (Chikou), close below the cloud"


def detect_ichimoku_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL theo Ichimoku gốc.

    Trạng thái BUY ở nến t (cả ba điều kiện cùng đúng, so sánh nghiêm ngặt, NaN không thoả):
        1. tenkan > kijun                      ← Tenkan-sen nằm trên Kijun-sen.
        2. close_t > close_{t - DISPLACEMENT}  ← giá đóng cửa cao hơn chính nó DISPLACEMENT nến trước (Chikou Span trên giá).
        3. close_t > max(senkou_a, senkou_b)   ← giá đóng cửa nằm trên mây tại nến t (mây = Senkou đã tính ở nến t - DISPLACEMENT).

    Trạng thái SELL đối xứng (tenkan < kijun, close < close_lag, close < min(senkou_a, senkou_b)).

    Tín hiệu là SỰ KIỆN "vừa chuyển sang đủ ba điều kiện": nến t đủ ba điều kiện còn nến t - 1 thì chưa (kể cả chưa đủ dữ liệu). Nhờ đó
    một đợt dài thoả điều kiện chỉ cho MỘT tín hiệu ở nến đầu đợt, không bắn lại ở mọi nến của đợt. Sự kiện cần thêm atr hợp lệ ở nến t
    (build_signal cần atr để publish); atr KHÔNG tham gia định nghĩa trạng thái nên không làm lệch nến t - 1.

    Lọc lặp cùng chiều (alternating.py, bật/tắt bằng cờ `strategies.ichimoku.alternating_filter_enabled` trong de_config.yaml, mặc
    định bật): sau một sự kiện BUY, các sự kiện BUY kế tiếp bị giữ 0 cho tới khi có sự kiện SELL (và ngược lại) nên BUY/SELL xen kẽ tuyệt
    đối. Bộ lọc chỉ nhớ HƯỚNG CỦA SỰ KIỆN GẦN NHẤT trong cửa sổ đang tính (DE đọc 500 nến mỗi sự kiện) và không biết lệnh thật còn mở hay
    đã dính SL. Chỉ nến CUỐI của cửa sổ được publish nên chỉ cần nó đúng: sai lệch so với bản tính từ đầu lịch sử chỉ xảy ra khi KHÔNG có
    sự kiện nào trong phần cửa sổ đủ dữ liệu trước nến cuối (500 nến trừ 78 nến chưa đủ mây ≈ 422 nến) mà sự kiện gần nhất trước đó
    lại cùng chiều với sự kiện hiện tại: bản đầy đủ chặn nó, cửa sổ không thấy sự kiện nào nên cho bắn (xem docstring rules/alternating.py).

    Hai bộ lọc (lọc lặp cùng chiều rồi lọc trend) do rules/trend_tf.apply_signal_filters điều phối như mọi chiến lược; ichimoku không có
    trend nên chỉ còn bộ lọc đầu, signal == raw_signal.

    Args:
        df: DataFrame đã qua indicator.add_ichimoku_indicators().
        symbol, sess_mask: Không dùng — giữ cùng chữ ký detect_signals.
        params: Dict tham số đã validate (chỉ cần cho cờ lọc lặp cùng chiều).

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, signal, signal_reason, trend_filter_status.
    """
    _ = symbol, sess_mask
    out = df.copy()

    # Mây tại nến t: biên trên/dưới của Senkou A và B. NaN nếu một trong hai chưa có (chưa đủ nến) -> so sánh bên dưới không thoả.
    cloud = out[["senkou_a", "senkou_b"]]
    cloud_top = cloud.max(axis=1, skipna=False)
    cloud_bottom = cloud.min(axis=1, skipna=False)

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC ICHIMOKU — chỉ sửa 2 khối điều kiện dưới đây khi đổi ý tưởng. Ngoài chúng chỉ còn
    # "sự kiện vừa đủ ba điều kiện" và lọc lặp cùng chiều (alternating.py, cờ trong config). Tham số, chỉ báo
    # và entry đều ở file khác (xem docstring đầu file).
    # =========================================================================
    buy_state = (out["tenkan"] > out["kijun"]) & (out["close"] > out["close_lag"]) & (out["close"] > cloud_top)
    sell_state = (out["tenkan"] < out["kijun"]) & (out["close"] < out["close_lag"]) & (out["close"] < cloud_bottom)
    # =========================================================================

    valid = out["atr"].notna()
    buy_event = buy_state & ~buy_state.shift(1, fill_value=False) & valid
    sell_event = sell_state & ~sell_state.shift(1, fill_value=False) & valid

    return apply_signal_filters(
        out, params, buy_setup=buy_event, sell_setup=sell_event, buy_reason=_BUY_REASON, sell_reason=_SELL_REASON
    )
