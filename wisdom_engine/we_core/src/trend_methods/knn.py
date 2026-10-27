"""
Khối xác nhận xu hướng KNN ("AI Trend Navigator") — một phương pháp trong trend_methods/.

Khối này chỉ làm MỘT việc: nhận nến của khung lớn (khung tham chiếu) rồi trả về hướng xu
hướng của từng nến đó: +1 đang đi lên, -1 đang đi xuống, 0 đi ngang hoặc chưa đủ dữ liệu.
Nó không biết chiến lược nào đang dùng nó; không lọc tín hiệu (việc của rules/trend_tf.py);
không ghép với nến vào lệnh (việc của indicator.merge_trend_reference); không đọc config —
tham số kỹ thuật cố định ngay trong file này (ngoại lệ có chủ đích, CLAUDE.md mục 2).

Hợp đồng chung của một khối trend_methods (rules/trend_tf.py kiểm lúc nạp):
    NAME            tên dùng trong config/CLI/tên file CSV (chữ thường, ví dụ "knn").
    OUTPUT_COLUMNS  các cột riêng của khối, được mang sang khung vào lệnh khi ghép nến.
    OVERLAYS        khai báo đường vẽ trên chart khung lớn của dashboard (dữ liệu thuần).
    build(trend_bars, params)
                    -> bản sao của trend_bars + OUTPUT_COLUMNS + cột hướng
                       `trend_bias_<NAME>` (int: +1 / -1 / 0). Không xoá hay đổi cột có sẵn.
Cách dùng hướng đó (khung tham chiếu riêng hay chính khung của tín hiệu; có dời tín hiệu theo chính sách
first_aligned_per_run hay không) là chính sách của rules/trend_tf.POLICY, KHÔNG khai báo trong khối.

Hướng của nến = dấu của (ai_knn nến này - ai_knn nến trước): tăng +1, giảm -1, bằng/NaN 0.
Cách tính ai_knn: xem calc_ai_trend_navigator (deterministic, không phải mô hình học máy).

Phụ thuộc: chỉ import các hàm toán của indicator.py (sma/rma/wma) — một chiều.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from wisdom_engine.we_core.src.indicator import rma, sma, wma

NAME = "knn"
BIAS_COLUMN = f"trend_bias_{NAME}"
OUTPUT_COLUMNS: tuple[str, ...] = ("trend_ai_knn", "trend_ai_avg", "trend_ai_direction")
# Đường vẽ trên chart khung lớn: colored_by_bias=True thì tô từng điểm theo hướng của CHÍNH khối này.
OVERLAYS: tuple[dict[str, Any], ...] = (
    {"column": "trend_ai_knn", "label": "KNN Trend", "color": "#22c55e", "colored_by_bias": True},
    {"column": "trend_ai_avg", "label": "KNN Avg", "color": "#38bdf8", "colored_by_bias": False},
)

KNN_TREND_DEFAULT_PARAMS: dict[str, Any] = {
    "PRICE_VALUE": "hl2",
    "TARGET_VALUE": "Price Action",
    "AI_MA_LEN": 5,
    "AI_TARGET_LEN": 5,
    "AI_K": 3,
    "AI_SMOOTH": 50,
}


def _positive_int(value: object, key: str) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{key} must be a positive integer.")
    return parsed


def knn_trend_indicator_params(params: dict | None) -> dict[str, Any]:
    """
    Ghép bộ tham số kỹ thuật nội bộ cho KNN trend.

    Strategy config chỉ quyết định có bật trend không, bật những khối nào, được chọn những trend
    timeframe nào và mặc định dùng timeframe nào. Các chi tiết công thức KNN ở đây để không bị
    lặp trong từng strategy.
    """
    raw = {**KNN_TREND_DEFAULT_PARAMS, **(params or {})}
    return {
        "PRICE_VALUE": str(raw["PRICE_VALUE"]),
        "TARGET_VALUE": str(raw["TARGET_VALUE"]),
        "AI_MA_LEN": _positive_int(raw["AI_MA_LEN"], "AI_MA_LEN"),
        "AI_TARGET_LEN": _positive_int(raw["AI_TARGET_LEN"], "AI_TARGET_LEN"),
        "AI_K": _positive_int(raw["AI_K"], "AI_K"),
        "AI_SMOOTH": _positive_int(raw["AI_SMOOTH"], "AI_SMOOTH"),
    }


# =============================================================================
# Công thức AI Trend Navigator — chép nguyên văn từ indicator.py (không đổi tính toán).
# =============================================================================


def _price_source(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    """
    Chọn chuỗi giá đầu vào cho AI Trend Navigator.

    Chỉ hỗ trợ "hl2" — đúng giá trị PRICE_VALUE duy nhất mà hệ thống cấu
    hình (KNN_TREND_DEFAULT_PARAMS ở đầu file này). Các nhánh vwap/
    sma/wma/ema/hma của bản port gốc đã bỏ vì không đường nào gọi tới được
    (normalize_*_params không phát ra key PRICE_VALUE). Giá trị lạ raise
    ngay thay vì âm thầm rơi về hl2 — cùng nguyên tắc với config.yaml
    (CLAUDE.md mục 5).
    """
    kind = str(kind or "hl2").strip().lower()
    if kind != "hl2":
        raise ValueError(f"PRICE_VALUE must be 'hl2', got '{kind}'.")
    hl2 = (df["high"].astype(float) + df["low"].astype(float)) / 2.0
    return sma(hl2, period)


def _target_source(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    """
    Chọn chuỗi target để tìm K hàng xóm gần nhất trong AI Trend Navigator.

    Chỉ hỗ trợ "price action" — xem ghi chú ở _price_source().
    """
    kind = str(kind or "price action").strip().lower()
    if kind != "price action":
        raise ValueError(f"TARGET_VALUE must be 'Price Action', got '{kind}'.")
    return rma(df["close"].astype(float), period)


def calc_ai_trend_navigator(
    df: pd.DataFrame,
    *,
    price_value: str = "hl2",
    ma_len: int = 5,
    target_value: str = "Price Action",
    target_len: int = 5,
    number_of_closest_values: int = 3,
    smoothing_period: int = 50,
) -> pd.DataFrame:
    """
    Tính AI Trend Navigator KNN line.

    Đây là indicator deterministic, không phải model ML train offline.
    Output:
        ai_knn:       đường KNN đã làm mượt WMA(5).
        ai_avg:       RMA của chuỗi KNN để tham chiếu/hiển thị.
        ai_direction: 1 nếu ai_knn tăng, -1 nếu giảm, 0 nếu đi ngang/NaN.
    """
    out = pd.DataFrame(index=df.index)
    if df.empty:
        return out

    work = df.reset_index(drop=True).copy()
    k = max(2, min(int(number_of_closest_values), 200))
    ma_len = max(2, min(int(ma_len), 200))
    target_len = max(2, min(int(target_len), 200))
    smoothing_period = max(2, min(int(smoothing_period), 500))
    window_size = max(k, 30)

    values = _price_source(work, price_value, ma_len).to_numpy(dtype=float)
    targets = _target_source(work, target_value, target_len).to_numpy(dtype=float)
    knn_ma = np.full(len(work), np.nan, dtype=float)
    for idx, target in enumerate(targets):
        if idx == 0 or np.isnan(target):
            continue
        history = values[max(0, idx - window_size) : idx]
        valid = history[~np.isnan(history)]
        if len(valid) < k:
            continue
        nearest = np.argsort(np.abs(valid - target))[:k]
        knn_ma[idx] = float(valid[nearest].mean())

    knn_ma_s = pd.Series(knn_ma)
    ai_knn = wma(knn_ma_s, 5)
    ai_avg = rma(knn_ma_s, smoothing_period)
    ai_direction = pd.Series(
        np.where(ai_knn > ai_knn.shift(1), 1, np.where(ai_knn < ai_knn.shift(1), -1, 0))
    )
    out["ai_knn"] = ai_knn.to_numpy()
    out["ai_avg"] = ai_avg.to_numpy()
    out["ai_direction"] = ai_direction.to_numpy()
    return out


def build(trend_bars: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Dựng các cột KNN cho frame nến khung lớn.

    Các cột được thêm (xem OUTPUT_COLUMNS):
        trend_ai_knn:        đường KNN đã làm mượt WMA(5).
        trend_ai_avg:        RMA của chuỗi KNN, chỉ để tham chiếu/hiển thị.
        trend_ai_direction:  1 nếu trend_ai_knn tăng, -1 nếu giảm, 0 nếu đi ngang/NaN.
        trend_bias_knn:      hướng của khối này (+1/-1/0), lấy từ trend_ai_direction.
    """
    technical = knn_trend_indicator_params(params)
    out = trend_bars.copy()
    ai = calc_ai_trend_navigator(
        out,
        price_value=technical["PRICE_VALUE"],
        ma_len=technical["AI_MA_LEN"],
        target_value=technical["TARGET_VALUE"],
        target_len=technical["AI_TARGET_LEN"],
        number_of_closest_values=technical["AI_K"],
        smoothing_period=technical["AI_SMOOTH"],
    )
    out["trend_ai_knn"] = ai["ai_knn"]
    out["trend_ai_avg"] = ai["ai_avg"]
    out["trend_ai_direction"] = ai["ai_direction"]

    out[BIAS_COLUMN] = 0
    out.loc[out["trend_ai_direction"].eq(1), BIAS_COLUMN] = 1
    out.loc[out["trend_ai_direction"].eq(-1), BIAS_COLUMN] = -1
    return out
