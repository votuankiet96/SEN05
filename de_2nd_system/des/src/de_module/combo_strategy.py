"""
Chiến lược Combo: CHỈ quyết định khi nào có tín hiệu BUY/SELL (khi nào, hướng nào).

Tín hiệu là SỰ KIỆN nến vừa cắt MA, xác nhận bằng MACD Histogram, tất cả trên cùng một nến:
    BUY:   nến tăng (close > open) và close > ma và macd_h > 0 và VỪA CẮT LÊN, tức MỘT trong hai: nến này mở dưới ma (open < ma: nến tự cắt lên ngay trong thân nến) hoặc nến trước đóng dưới ma CỦA NẾN TRƯỚC (prev_close < prev_ma: cắt thật giữa hai lần đóng cửa)
    SELL:  đối xứng: nến giảm (close < open) và close < ma và macd_h < 0 và vừa cắt xuống: open > ma hoặc prev_close > prev_ma
Chủ hệ chốt 2026-11-16: trước đó điều kiện cắt là prev_close < ma của nến HIỆN TẠI (hệ cũ cũng thế). Khi ma dốc vài điểm trong một nến thì nến cắt thật bị bỏ lỡ (HK50 10/08/2022 10:30, US30 15/08/2022 02:30), và vì luật xen kẽ nhớ setup gần nhất nên một lần bỏ lỡ kéo theo mất cả tín hiệu ngược chiều kế tiếp.
Nến đầu của bảng (không có nến trước) và nến còn NaN ở open, close, ma hoặc macd_h thì không có setup; nến đầu tiên có ma chưa có ma của nến trước nên chỉ xét nhánh open. Mọi so sánh đều nghiêm ngặt (bằng nhau không tính).
Điều kiện XEN KẼ (một điều kiện của chính chiến lược Combo): một setup chỉ thành tín hiệu khi setup gần nhất TRƯỚC ĐÓ không cùng chiều (hoặc chưa có setup nào trong bảng). Nhiều setup cùng chiều liên tiếp (không có setup ngược
xen giữa) là cùng một pha thị trường nên chỉ setup ĐẦU TIÊN thành tín hiệu; setup ngược chiều mở pha mới. Vì vậy tín hiệu của Combo luôn xen kẽ BUY, SELL, BUY, SELL. Luật này chạy trên MỌI setup, độc lập với trend: việc
chọn tín hiệu nào được phát (trend, dời, hủy) là của decision_engine.py. Trạng thái chỉ nhớ trong bảng được truyền vào (runner tính trên 800 nến), nên setup đầu bảng luôn thành tín hiệu.

File này KHÔNG làm các việc sau:
    - Không đọc dữ liệu nến và không giữ tham số hay giá trị nào: chu kỳ MA và MACD do configuration.py truyền vào ``detect`` (khai ở ``strategies.<tên>.params`` của des_config.yaml). Công thức chỉ báo ở indicator.py.
    - Không lọc theo trend, không dời tín hiệu, không tính giá vào lệnh hay atr: đó là việc của decision_engine.py.

Hợp đồng với configuration.py: file chiến lược có hàm ``detect(frame, *, <tham số>)`` nhận nến khung trigger và trả bản sao frame có thêm hai cột signal, signal_reason; chữ ký là chỗ khai tham số.
Luật thuần ``detect_combo_signals`` chạy trên bảng nến tăng dần đã có cột ma (SMA của close) và macd_h (MACD Histogram của close).
Đầu ra: detect (và detect_combo_signals) trả về bản sao có thêm hai cột
    signal:         +1 BUY, -1 SELL, 0 không có;
    signal_reason:  lý do ở nến có tín hiệu (field ``comment`` của tín hiệu cuối, OE đọc nguyên văn: đổi chuỗi là đổi dữ liệu OE nhận), rỗng ở nến khác.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from des.src.indicator import macd_hist, sma

_BUY_REASON = "bullish candle crossing above MA, MACD histogram > 0"
_SELL_REASON = "bearish candle crossing below MA, MACD histogram < 0"


def detect_combo_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phát hiện các nến có tín hiệu Combo trên DataFrame đã có sẵn cột ma và macd_h.

    Args:
        df: nến tăng dần, có cột open, close, ma, macd_h.

    Returns:
        Bản sao df với các cột bổ sung: signal, signal_reason.
    """
    out = df.copy()
    prev_close = out["close"].shift(1)
    prev_ma = out["ma"].shift(1)
    valid = out["open"].notna() & out["close"].notna() & out["ma"].notna() & out["macd_h"].notna() & prev_close.notna()

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC COMBO: đổi ý tưởng vào lệnh thì chỉ sửa 2 điều kiện setup dưới đây (và điều kiện xen kẽ ngay sau chúng).
    # =========================================================================
    crossed_up = (out["open"] < out["ma"]) | (prev_close < prev_ma)  # nến tự cắt lên trong thân nến, hoặc cắt thật giữa hai lần đóng cửa (ma của nến trước; NaN thì nhánh này sai)
    crossed_down = (out["open"] > out["ma"]) | (prev_close > prev_ma)
    buy = valid & (out["close"] > out["open"]) & (out["close"] > out["ma"]) & (out["macd_h"] > 0) & crossed_up
    sell = valid & (out["close"] < out["open"]) & (out["close"] < out["ma"]) & (out["macd_h"] < 0) & crossed_down
    # =========================================================================

    # Điều kiện xen kẽ: setup chỉ thành tín hiệu khi chiều của setup gần nhất TRƯỚC nến này (kể cả setup đã không thành tín hiệu vì cùng chiều) khác chiều của nó; nến không có setup không đổi trạng thái.
    setup = np.where(buy, 1, np.where(sell, -1, 0))
    previous_setup = pd.Series(setup).replace(0, np.nan).ffill().shift(1).to_numpy()  # NaN khi chưa có setup nào trước đó
    signal = np.where(setup == previous_setup, 0, setup)

    out["signal"] = signal.astype("int64")
    out["signal_reason"] = np.where(signal == 1, _BUY_REASON, np.where(signal == -1, _SELL_REASON, ""))
    return out


def detect(frame: pd.DataFrame, *, ma_period: int, macd_fast: int, macd_slow: int, macd_signal: int) -> pd.DataFrame:
    """
    Tín hiệu Combo trên nến của khung trigger (cột open, close, bartime tăng dần): tính SMA và MACD Histogram rồi áp luật ``detect_combo_signals``.

    Returns:
        Bản sao frame với hai cột bổ sung: signal, signal_reason (không mang cột chỉ báo theo).
    """
    if macd_fast >= macd_slow:
        raise ValueError(f"combo: macd_fast ({macd_fast}) must be smaller than macd_slow ({macd_slow})")
    close = pd.to_numeric(frame["close"], errors="coerce")
    indicators = frame.assign(ma=sma(close, ma_period), macd_h=macd_hist(close, fast=macd_fast, slow=macd_slow, signal=macd_signal))
    decided = detect_combo_signals(indicators)
    return frame.assign(signal=decided["signal"], signal_reason=decided["signal_reason"])
