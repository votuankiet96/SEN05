"""
Khối xác nhận xu hướng BREAKOUT — một phương pháp trong trend_methods/, thay chỗ được cho KNN (knn.py).

Khối này chỉ làm MỘT việc: nhận nến của khung lớn (khung tham chiếu) rồi trả về hướng của từng nến đó: +1 đang
đi lên (chỉ BUY), -1 đang đi xuống (chỉ SELL), 0 chưa có break nào. Nó không biết chiến lược nào đang dùng nó; không
lọc tín hiệu (việc của rules/trend_tf.py); không ghép với nến vào lệnh (việc của indicator.merge_trend_reference);
không đọc config — tham số kỹ thuật cố định ngay trong file này (ngoại lệ có chủ đích, CLAUDE.md mục 2).
Hợp đồng khối giống hệt knn.py (NAME, OUTPUT_COLUMNS, OVERLAYS, build) nên chọn khối bằng config
`trend_methods: [breakout]` thay cho `[knn]` mà không phải sửa nơi khác.

Ý tưởng: lấy đúng cú phá đỉnh/đáy của chiến lược macd_break (strategies/macd_break.py) làm xu hướng.
    break lên:   close >= đỉnh close của 100 nến TRƯỚC (không gồm nến hiện tại) và MACD histogram (5/25/5) > 0
    break xuống: close <= đáy close của 100 nến TRƯỚC và MACD histogram < 0
Hai đường đỉnh/đáy là hai đường "High close"/"Low close" của macd_break trên chart.

Khung của khối = CHÍNH khung của tín hiệu (chính sách same_timeframe ở rules/trend_tf.POLICY, người dùng chốt 2026-10-22): trigger M30 thì trend
tính trên nến M30, trigger H1 thì trên nến H1 — không có khung tham chiếu riêng, không tra bảng trend_tf_by_symbol và
không bị giới hạn trong trend_timeframes (H1–H4 chỉ áp cho khối như KNN cần khung lớn hơn). Nến vào lệnh vẫn chỉ nhìn
trend của nến đã đóng TRƯỚC nó (indicator.merge_trend_reference, như mọi trường hợp khung trend bằng khung tín hiệu).

Hướng của nến = chiều của break GẦN NHẤT, giữ nguyên cho tới khi có break ngược; trước break đầu tiên là 0 (không
vào lệnh). Hướng đổi đúng tại nến có tín hiệu BUY/SELL của macd_break (mũi tên trên chart macd_break). Khối xét MỌI
nến thoả điều kiện thay vì chỉ nến đầu đợt của bộ lọc xen kẽ, nhưng hướng giống hệt: bộ lọc xen kẽ chỉ bỏ các break
cùng chiều lặp lại, không đổi chiều của break gần nhất.

Cách ghép với bộ lọc xen kẽ (chính sách first_aligned_per_run ở rules/trend_tf.POLICY, xem apply_trend_filter): mỗi đợt setup
bắn tối đa MỘT tín hiệu, ở nến setup đầu tiên mà trend đã cùng chiều. Ví dụ US30 M10 04/04/2024: SELL thô lúc 17:40 (trend còn
UP nên không đi qua), giá thủng đáy lúc 18:00, nến 18:10 là nến đầu tiên thấy trend DOWN và điều kiện SELL vẫn đúng => bắn SELL
ở 18:10; trước khi có chính sách này bộ lọc xen kẽ coi đợt SELL đã "dùng lượt" từ 17:40 nên cả đoạn giảm 118 nến không có SELL
nào. Mọi tín hiệu của cách cũ được giữ nguyên, chỉ thêm tín hiệu bị bỏ sót. Riêng combo (setup là sự kiện nến cắt MA, một nến)
tín hiệu thô bị lọc được giữ tới hết đợt và bắn ngay nến trend đổi đúng chiều, không giới hạn tuổi (strategies/combo.py).

Lịch sử: ngày 2026-10-22 từng có thêm "vùng xám" (nến đóng cửa quay lại phía trong mức vừa phá = break thất bại thì chỉ
cho chiều NGƯỢC với break gần nhất) rồi bị BỎ theo yêu cầu người dùng. Đo trên dữ liệu thật: vùng xám chiếm 79–86% số
nến nên đảo dấu bộ lọc ở phần lớn thời gian, làm hướng đổi gấp ~10 lần (386 so với 40 lần/năm ở H1) và tập tín hiệu
giữ lại gần như rời hẳn bản giữ chiều (xem CLAUDE.md). CHƯA có căn cứ backtest: là khối mặc định của combo và ma_cross
trong config từ 2026-10-22, còn macd_break dùng knn.

Các cột thêm vào (xem OUTPUT_COLUMNS):
    trend_breakout_high:   đỉnh close của 100 nến trước (đường xanh).
    trend_breakout_low:    đáy close của 100 nến trước (đường đỏ).
    trend_bias_breakout:   hướng của khối này (+1/-1/0), kiểu int.

Phụ thuộc: chỉ import hàm toán macd_hist của indicator.py — một chiều.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from wisdom_engine.des.src.indicator import macd_hist

NAME = "breakout"
BIAS_COLUMN = f"trend_bias_{NAME}"
OUTPUT_COLUMNS: tuple[str, ...] = ("trend_breakout_high", "trend_breakout_low")
# Đường vẽ trên chart khung lớn: hai đường đỉnh/đáy 100 nến, màu cố định như chart macd_break (hướng UP/DOWN đã có
# marker đổi hướng chung của chart trend).
OVERLAYS: tuple[dict[str, Any], ...] = (
    {"column": "trend_breakout_high", "label": "Break High", "color": "#22c55e", "colored_by_bias": False},
    {"column": "trend_breakout_low", "label": "Break Low", "color": "#ef4444", "colored_by_bias": False},
)

# Tham số kỹ thuật cố định: cùng giá trị mặc định hiện tại với chiến lược macd_break (lookback 100, MACD 5/25/5).
# Khối KHÔNG đọc config nên đổi tham số của macd_break không làm đổi khối này.
LOOKBACK_BARS = 100
MACD_FAST = 5
MACD_SLOW = 25
MACD_SIGNAL = 5


def _detect_breaks(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.Series, pd.Series]:
    """
    Cú break lên/xuống của từng nến và hai đường đỉnh/đáy close của LOOKBACK_BARS nến trước.

    Điều kiện y hệt strategies/macd_break.py nhưng KHÔNG lọc xen kẽ: mọi nến thoả điều kiện đều là một break.
    """
    close = bars["close"].astype(float)
    high = close.shift(1).rolling(LOOKBACK_BARS).max()
    low = close.shift(1).rolling(LOOKBACK_BARS).min()
    hist = macd_hist(close, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    valid = high.notna() & low.notna() & hist.notna()
    up = (valid & (close >= high) & (hist > 0)).to_numpy()
    down = (valid & (close <= low) & (hist < 0)).to_numpy()
    return up, down, high, low


def build(trend_bars: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Dựng các cột BREAKOUT cho frame nến khung lớn (bartime tăng dần).

    Các cột được thêm: xem OUTPUT_COLUMNS và BIAS_COLUMN ở docstring đầu file. Không sửa input, không xoá
    hay đổi cột có sẵn.
    """
    _ = params  # tham số kỹ thuật cố định trong file này, không lấy từ params của chiến lược
    out = trend_bars.copy()
    up, down, high, low = _detect_breaks(out)
    direction = pd.Series(np.where(up, 1, np.where(down, -1, np.nan))).ffill().fillna(0).astype(np.int64)
    out["trend_breakout_high"] = high.to_numpy()
    out["trend_breakout_low"] = low.to_numpy()
    out[BIAS_COLUMN] = direction.to_numpy()
    return out
