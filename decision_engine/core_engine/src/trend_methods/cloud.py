"""
Khối xác nhận xu hướng CLOUD (mây Ichimoku) — một phương pháp trong trend_methods/, cùng hợp đồng với knn.py / breakout.py / zigzag.py / tstat.py.

Khối này chỉ làm MỘT việc: nhận nến của khung tham chiếu rồi trả về hướng của từng nến đó theo VỊ TRÍ GIÁ ĐÓNG CỬA SO VỚI MÂY ICHIMOKU:
+1 giá đóng cửa nằm TRÊN mây (chỉ BUY), -1 nằm DƯỚI mây (chỉ SELL), 0 nằm trong mây (kể cả bằng đúng biên) hoặc chưa đủ dữ liệu. Nó không
biết chiến lược nào đang dùng nó; không lọc tín hiệu (việc của rules/trend_tf.py); không ghép với nến vào lệnh (việc của
indicator.merge_trend_reference); không đọc config — tham số kỹ thuật cố định ngay trong file này (ngoại lệ có chủ đích, CLAUDE.md mục 2).
Khung dùng và cách lọc (khung trend = CHÍNH khung vào lệnh nhưng không nhỏ hơn H1, CÓ dời tín hiệu) là chính sách ở rules/trend_tf.POLICY,
KHÔNG khai báo trong khối. Tên cũ của khối bên we_core là "ichimoku"; DE dùng tên "cloud" để không trùng chiến lược ichimoku
(strategies/ichimoku.py, một chiến lược vào lệnh riêng, không liên quan tới khối lọc này).

Công thức (bộ tham số Ichimoku gốc 9 / 26 / 52 / 26, cố định):
    Tenkan   = (đỉnh cao nhất + đáy thấp nhất) / 2 của 9 nến gần nhất, GỒM nến đang xét
    Kijun    = như vậy với 26 nến
    Senkou A = (Tenkan + Kijun) / 2
    Senkou B = (đỉnh cao nhất + đáy thấp nhất) / 2 của 52 nến
    Mây tại nến t lấy Senkou A / B đã tính ở nến t - 26 (nhân quả: chỉ dùng dữ liệu đã có tại t, không nhìn trước).
    Biên trên = max(A, B), biên dưới = min(A, B) của mây tại t.
    hướng(t) = +1 nếu close_t > biên trên; -1 nếu close_t < biên dưới; 0 nếu nằm trong mây (kể cả bằng biên) hoặc chưa đủ 78 nến (52 + 26).
Chỉ dùng "giá so với mây": KHÔNG dùng giao cắt Tenkan/Kijun, màu mây hay Chikou (Tenkan và Kijun chỉ là bước trung gian của Senkou A).
So sánh nghiêm ngặt; NaN (chưa đủ nến) không thoả cả hai chiều nên hướng là 0.

Nến vào lệnh chỉ nhìn nến trend ĐÃ ĐÓNG hẳn trước khi nó mở (indicator.merge_trend_reference), như mọi khối. Chưa có backtest/P&L cho khối này;
bằng chứng bên ngoài cho bộ 9/26/52 trên nến M10–H4 rất yếu (ghi nhận từ we_core 2026-10-28).

Các cột thêm vào (xem OUTPUT_COLUMNS):
    trend_cloud_top:     biên trên của mây tại nến đó, max(Senkou A, Senkou B) đã lùi 26 nến (NaN khi chưa đủ 78 nến).
    trend_cloud_bottom:  biên dưới của mây, min(Senkou A, Senkou B) đã lùi 26 nến.
    trend_bias_cloud:    hướng của khối này (+1/-1/0), kiểu int.

Phụ thuộc: chỉ numpy/pandas (không import gì trong src/).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

NAME = "cloud"
BIAS_COLUMN = f"trend_bias_{NAME}"
OUTPUT_COLUMNS: tuple[str, ...] = ("trend_cloud_top", "trend_cloud_bottom")
# Đường vẽ trên chart khung lớn: hai biên của mây, màu cố định (hướng UP/DOWN đã có marker đổi hướng chung của chart trend).
OVERLAYS: tuple[dict[str, Any], ...] = (
    {"column": "trend_cloud_top", "label": "Cloud Top", "color": "#22c55e", "colored_by_bias": False},
    {"column": "trend_cloud_bottom", "label": "Cloud Bottom", "color": "#ef4444", "colored_by_bias": False},
)

# Tham số kỹ thuật cố định (Ichimoku gốc). Khối KHÔNG đọc config nên đổi tham số của chiến lược ichimoku (strategies.ichimoku trong
# de_config.yaml) không làm đổi khối này.
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26


def _midpoint(high: pd.Series, low: pd.Series, period: int) -> pd.Series:
    """(đỉnh cao nhất + đáy thấp nhất) / 2 của `period` nến gần nhất, gồm nến đang xét; NaN cho tới khi đủ `period` nến."""
    return (high.rolling(period).max() + low.rolling(period).min()) / 2.0


def build(trend_bars: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Dựng các cột CLOUD cho frame nến của khung tham chiếu (bartime tăng dần).

    Các cột được thêm (xem OUTPUT_COLUMNS và BIAS_COLUMN ở docstring đầu file). Không sửa input, không xoá hay đổi cột có sẵn.
    """
    _ = params  # tham số kỹ thuật cố định trong file này, không lấy từ params của chiến lược
    out = trend_bars.copy()
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")

    tenkan = _midpoint(high, low, TENKAN_PERIOD)
    kijun = _midpoint(high, low, KIJUN_PERIOD)
    senkou_a = ((tenkan + kijun) / 2.0).shift(DISPLACEMENT)  # mây tại t = giá trị đã tính ở nến t - 26
    senkou_b = _midpoint(high, low, SENKOU_B_PERIOD).shift(DISPLACEMENT)
    edges = pd.concat([senkou_a, senkou_b], axis=1)
    top = edges.max(axis=1, skipna=False)  # chỉ một trong hai là NaN thì biên cũng NaN (chưa đủ 78 nến)
    bottom = edges.min(axis=1, skipna=False)

    out["trend_cloud_top"] = top.to_numpy()
    out["trend_cloud_bottom"] = bottom.to_numpy()
    bias = np.zeros(len(out), dtype=np.int64)
    bias[(close > top).to_numpy()] = 1  # so sánh với NaN luôn False: chưa đủ dữ liệu thì trung tính
    bias[(close < bottom).to_numpy()] = -1
    out[BIAS_COLUMN] = bias
    return out
