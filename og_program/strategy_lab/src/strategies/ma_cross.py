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
    - Không đọc dữ liệu từ SQL Server.
    - Không giữ tham số mặc định như FAST_MA, SLOW_MA.
    - Không tự tính SMA, MACD Histogram hoặc ATR.
    - Không tính Entry/Stop Loss/Take Profit cuối cùng.

Vị trí của file trong pipeline:
    1. db_connector.py lấy dữ liệu nến OHLCV.
    2. configuration.py chuẩn hóa tham số từ config.yaml.
    3. indicator.py thêm fast_ma, slow_ma, macd_h, atr.
    4. File này đọc các cột đó để tạo signal BUY/SELL.
    5. levels.py tính market entry và copy tham chiếu Dow sau khi signal đã có.

Chốt 2026-09-25 -- MA Cross dùng cơ chế trend filter RIÊNG
(_apply_trend_wait bên dưới), KHÔNG dùng strategies.trend.apply_trend_
filter() nữa (Combo vẫn dùng hàm đó, không đổi gì). Lý do: apply_trend_
filter() đòi raw_signal và trend_bias phải khớp ĐÚNG 1 BAR mới giữ tín
hiệu -- hợp lý cho Combo (điều kiện tại 1 thời điểm), nhưng sai bản chất
cho MA Cross (bản chất là 1 sự kiện CẮT, không phải trạng thái). Đo thật
trên SQL (20.000 nến, 3 pair): cách khớp-đúng-1-bar chỉ giữ 30-47% tín
hiệu gốc, tức loại bỏ 53-70%. _apply_trend_wait() thay bằng: nếu trend
chưa align đúng lúc cắt thì TIẾP TỤC CHỜ (miễn xu hướng MA chưa đảo lại),
nổ tại bar ĐẦU TIÊN cả MACD lẫn trend cùng khớp -- đo thật cùng bộ dữ
liệu: giữ 55-75% tín hiệu gốc. Đã verify bằng walk-forward replay test
(không look-ahead, 7.130 cặp so sánh, 0 lệch, kèm 1 bản đối chứng cố ý
có look-ahead để chứng minh bài test đủ nhạy) và verify baseline khi tắt
trend filter khớp bit-for-bit với hành vi trước đó trên 100.000 nến thật
(5 pair, 0 lệch) -- xem memory project_ma_cross_trend_wait.

Đầu ra:
    detect_ma_cross_signals(...) trả về DataFrame mới có thêm:
    - raw_signal: tín hiệu gốc của MA Cross (cắt + MACD xác nhận) --
      KHÔNG đổi so với trước 2026-09-25.
    - signal: 1 = BUY, -1 = SELL, 0 = không có tín hiệu.
    - signal_reason: mô tả ngắn vì sao có tín hiệu.
    - trend_filter_status: "disabled" | "fired" | "pending" | "no_regime"
      -- không có consumer nào khác đọc cột này, chỉ để debug/audit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_lab.src.strategies.trend import trend_filter_enabled


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

    Đây là raw_signal -- công thức KHÔNG đổi so với trước 2026-09-25.
    Phần trend filter (nếu bật) xem docstring _apply_trend_wait() bên dưới.

    Args:
        df: DataFrame đã qua indicator.add_ma_cross_indicators().
        symbol: Không dùng trực tiếp — giữ để hàm có cùng chữ ký với các strategy khác.
        params: Có thể chứa TREND_FILTER_ENABLED để lọc raw_signal theo trend_bias.
        sess_mask: Chưa dùng cho MA Cross — giữ để cùng khuôn pipeline.

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, raw_signal_reason,
        signal, signal_reason, trend_filter_status.
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
    # QUY TẮC CHIẾN LƯỢC MA CROSS -- KHÔNG ĐỔI (chốt gốc). Chỉ phần lọc trend
    # bên dưới (_apply_trend_wait) mới thay đổi từ 2026-09-25. Nếu sau này
    # đổi ý tưởng vào lệnh của MA Cross, thường chỉ cần sửa điều kiện
    # cross_up/cross_down và điều kiện MACD xác nhận ngay tại đây.
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

    out["raw_signal_reason"] = ""
    out.loc[out["raw_signal"].eq(1), "raw_signal_reason"] = (
        "fast SMA crossed above slow SMA; MACD histogram > 0"
    )
    out.loc[out["raw_signal"].eq(-1), "raw_signal_reason"] = (
        "fast SMA crossed below slow SMA; MACD histogram < 0"
    )

    out = _apply_trend_wait(out, params)

    out["signal_reason"] = ""
    has_signal = out["signal"].fillna(0).astype(int).ne(0)
    out.loc[has_signal, "signal_reason"] = out.loc[has_signal, "raw_signal_reason"]
    if trend_filter_enabled(params):
        out.loc[has_signal, "signal_reason"] = (
            out.loc[has_signal, "signal_reason"]
            + "; trend aligned (có thể trễ hơn bar cắt gốc, xem trend_filter_status)"
        )
    return out


def _apply_trend_wait(
    df: pd.DataFrame,
    params: dict | None,
    *,
    raw_signal_col: str = "raw_signal",
    output_col: str = "signal",
    trend_bias_col: str = "trend_bias",
) -> pd.DataFrame:
    """
    Trend filter RIÊNG của MA Cross (chốt 2026-09-25) -- khác
    strategies.trend.apply_trend_filter() (Combo vẫn dùng, không đổi) ở
    chỗ KHÔNG đòi trend phải khớp đúng bar cắt: nếu chưa khớp, TIẾP TỤC
    CHỜ miễn xu hướng MA chưa đảo, nổ tại bar đầu tiên đủ điều kiện. Lý do
    + bằng chứng đầy đủ xem docstring module.

    TẮT trend filter: signal = raw_signal HOÀN TOÀN, không có gì khác --
    đã verify bit-for-bit trên 100.000 nến thật (5 pair) khớp 100% với
    hành vi trước 2026-09-25. Đây là chủ đích, không phải tình cờ: trend
    filter là lớp lọc gắn THÊM VÀO, không phải bản thân chiến lược -- tắt
    phải luôn là đường quay lại an toàn, không lệch dù 1 bar.

    Thuật toán khi BẬT (máy trạng thái tuần tự, xử lý từ bar cũ nhất tới
    mới nhất, mỗi bước chỉ đọc dữ liệu tại-hoặc-trước bar đang xét -- đã
    verify không look-ahead bằng walk-forward replay test, xem docstring
    module):

        pending = 0   # 0=không chờ | +1=đang chờ BUY | -1=đang chờ SELL

        Tại mỗi bar i, theo đúng thứ tự thời gian:
          (1) HUỶ nếu quy chế đã đảo tại đúng bar này:
                pending=+1 và fast_ma[i] <= slow_ma[i]  -> pending = 0
                pending=-1 và fast_ma[i] >= slow_ma[i]  -> pending = 0
          (2) MỞ quy chế mới nếu bar này có raw_signal (cắt + MACD xác
              nhận, công thức không đổi ở trên):
                raw_signal[i] = +1 -> pending = +1
                raw_signal[i] = -1 -> pending = -1
          (3) NỔ nếu đang chờ và đủ điều kiện TẠI ĐÚNG BAR NÀY (macd_h
              tính LẠI tại i, không dùng giá trị cũ lúc mở quy chế):
                pending=+1, macd_h[i]>0, trend_bias[i]=+1 -> signal[i]=+1, pending=0
                pending=-1, macd_h[i]<0, trend_bias[i]=-1 -> signal[i]=-1, pending=0

    Khi trend ĐÃ align sẵn tại đúng bar cắt, bước (2) và (3) xảy ra cùng
    1 vòng lặp -> nổ ngay tại bar cắt, giống hệt trước 2026-09-25 cho
    những tín hiệu vốn đã trùng khớp.

    Dùng vòng lặp Python thuần (không vector hoá bằng pandas) CÓ CHỦ Ý --
    đây là thuật toán có trạng thái xuyên nhiều bar với độ dài không cố
    định, không thể vector hoá an toàn bằng rolling/shift mà không có rủi
    ro look-ahead (vd lỡ tay dùng shift(-n), rolling(center=True)). Quy
    mô thực tế (vài nghìn bar/pair) đủ nhanh cho cách này.
    """
    out = df.copy()
    raw_signal = pd.to_numeric(out.get(raw_signal_col, 0), errors="coerce").fillna(0).astype(int)

    if not trend_filter_enabled(params):
        out[output_col] = raw_signal
        out["trend_filter_status"] = "disabled"
        return out

    if trend_bias_col not in out.columns:
        raise ValueError(
            "TREND_FILTER_ENABLED=true requires a trend reference frame with "
            f"'{trend_bias_col}' column."
        )

    fast_ma = pd.to_numeric(out["fast_ma"], errors="coerce").to_numpy()
    slow_ma = pd.to_numeric(out["slow_ma"], errors="coerce").to_numpy()
    macd_h = pd.to_numeric(out["macd_h"], errors="coerce").to_numpy()
    trend_bias = pd.to_numeric(out[trend_bias_col], errors="coerce").fillna(0).astype(int).to_numpy()
    raw_arr = raw_signal.to_numpy()

    n = len(out)
    signal = np.zeros(n, dtype=int)
    status = np.full(n, "no_regime", dtype=object)
    pending = 0
    for i in range(n):
        # (1) Huỷ nếu quy chế đã đảo TẠI ĐÚNG BAR NÀY.
        if (pending == 1 and fast_ma[i] <= slow_ma[i]) or (
            pending == -1 and fast_ma[i] >= slow_ma[i]
        ):
            pending = 0

        # (2) Mở quy chế mới nếu bar này có raw_signal.
        if raw_arr[i] == 1:
            pending = 1
        elif raw_arr[i] == -1:
            pending = -1

        # (3) Nổ nếu đang chờ và đủ điều kiện tại đúng bar này.
        if pending == 1 and macd_h[i] > 0 and trend_bias[i] == 1:
            signal[i] = 1
            status[i] = "fired"
            pending = 0
        elif pending == -1 and macd_h[i] < 0 and trend_bias[i] == -1:
            signal[i] = -1
            status[i] = "fired"
            pending = 0
        elif pending != 0:
            status[i] = "pending"

    out[output_col] = signal
    out["trend_filter_status"] = status
    return out
