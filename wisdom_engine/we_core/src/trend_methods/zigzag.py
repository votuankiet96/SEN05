"""
Khối xác nhận xu hướng ZigZag Cấu Trúc Thị Trường (Market Structure) — một phương pháp trong trend_methods/.

Khối này chỉ làm MỘT việc: nhận nến của khung lớn (khung tham chiếu) rồi trả về hướng xu
hướng của từng nến đó theo lý thuyết cấu trúc sóng ZigZag (Điều kiện Cần + Điều kiện Đủ):
    +1: Uptrend (Xu hướng tăng)
    -1: Downtrend (Xu hướng giảm)
     0: Neutral (Chưa đủ dữ liệu khởi tạo cấu trúc đầu tiên)

Quy tắc đảo chiều (Phương án B + xác nhận phá vỡ bằng giá đóng cửa `close`):
    1. Đang ở Uptrend (+1) với đỉnh cao nhất H8 (`peak_high`) và đáy hỗ trợ dẫn lên đỉnh đó L7 (`key_hl`):
       - Điều kiện cần: Giá đóng cửa phá vỡ đáy hỗ trợ L7 (`close < L7`).
       - Điều kiện đủ: Hình thành cấu trúc giảm mới gồm đáy đầu tiên L2 (`pullback_low`) sau H8,
         đỉnh hồi thấp hơn H3 (`lower_high < H8`), và giá đóng cửa phá vỡ đáy L2 (`close < L2`).
       - Điều kiện nào xảy ra trước cũng được; giữ nguyên +1 cho tới thanh nến thỏa mãn ĐỦ CẢ HAI
         điều kiện (`Đã có (L2, H3 < H8)` VÀ `close < min(L7, L2)`) thì lật sang Downtrend (-1).
    2. Đang ở Downtrend (-1) với đáy thấp nhất L8 (`trough_low`) và đỉnh kháng cự dẫn xuống đáy đó H7 (`key_lh`):
       - Đối xứng hoàn toàn: giữ nguyên -1 cho tới khi đã có đỉnh hồi H2 (`pullback_high`), đáy cao hơn
         L3 (`higher_low > L8`), VÀ giá đóng cửa phá lên trên cả hai đỉnh (`close > max(H7, H2)`) thì lật sang +1.

Hiển thị đường ZigZag (`trend_zigzag_pivot`):
    - Ghi nhận chính xác chỉ số thanh nến đạt đỉnh Swing High (`cand_high_idx`, giá `high`) và đáy
      Swing Low (`cand_low_idx`, giá `low`), sau đó nội suy tuyến tính giữa các cực trị liên tiếp để
      vẽ đường gấp khúc chéo nối từ râu nến Swing Low lên râu nến Swing High (và ngược lại) trên biểu đồ.
    - Trạng thái lọc xu hướng `trend_bias_zigzag` và ngưỡng phá vỡ `trend_zigzag_key_level` giữ nguyên
      tính nhân quả tuyệt đối (100% causal, không nhìn trước tương lai).

Ba chế độ xác nhận đỉnh/đáy sóng ZigZag cơ sở (`ZIGZAG_PIVOT_MODE`, không nhìn trước tương lai):
    - "atr"   (hoặc "1"): Đảo chiều sóng khi giá lùi từ cực trị >= ZIGZAG_ATR_MULT * ATR(ZIGZAG_ATR_PERIOD).
    - "depth" (hoặc "2"): Đảo chiều sóng khi giá phá đỉnh/đáy của ZIGZAG_DEPTH nến liền trước.
    - "both"  (hoặc "3"): Kết hợp cả hai (phải thoả đồng thời biên độ ATR VÀ phá kênh ZIGZAG_DEPTH nến).

Phụ thuộc: chỉ import hàm toán của indicator.py (atr) — một chiều.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd
from wisdom_engine.we_core.src.indicator import atr

NAME = "zigzag"
BIAS_COLUMN = f"trend_bias_{NAME}"
OUTPUT_COLUMNS: tuple[str, ...] = (
    "trend_zigzag_pivot",
    "trend_zigzag_key_level",
    "trend_zigzag_direction",
)
OVERLAYS: tuple[dict[str, Any], ...] = (
    {
        "column": "trend_zigzag_pivot",
        "label": "ZigZag Structure",
        "color": "#22c55e",
        "colored_by_bias": True,
    },
    {
        "column": "trend_zigzag_key_level",
        "label": "ZigZag Break Level",
        "color": "#f59e0b",
        "colored_by_bias": False,
    },
)

PIVOT_MODES: tuple[str, ...] = ("atr", "depth", "both")
_PIVOT_MODE_ALIASES: dict[str, str] = {
    "1": "atr",
    "atr": "atr",
    "2": "depth",
    "depth": "depth",
    "3": "both",
    "both": "both",
}

# Bộ số do người dùng chốt 2026-10-24 (thay bộ mặc định ban đầu both / Depth 5 / hệ số 1,5 / ATR 14): ATR 5 (cùng chu kỳ ATR của các chiến lược),
# hệ số 3,14, Depth 5 (giữ nguyên Depth ban đầu). Đo trên dữ liệu hệ: với ATR 5 × 3,14 điều kiện ATR là điều kiện quyết định — Depth 5 gần như không
# chặn thêm gì (số lần đổi hướng/năm H1/H2/H4 ~22,8 / 11,0 / 5,4, xấp xỉ chế độ "atr" một mình 23,9 / 11,5 / 5,8 và bằng khoảng một nửa bộ cũ
# 1,5 × ATR 14: 46 / 25 / 14). Depth 13 (đã đề xuất, người dùng không chọn) mới thật sự chặn thêm: 18,8 / 8,8 / 4,2.
ZIGZAG_TREND_DEFAULT_PARAMS: dict[str, Any] = {
    "ZIGZAG_PIVOT_MODE": "both",
    "ZIGZAG_DEPTH": 5,
    "ZIGZAG_ATR_MULT": 3.14,
    "ZIGZAG_ATR_PERIOD": 5,
}


def _positive_int(value: object, key: str) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{key} must be a positive integer.")
    return parsed


def _positive_float(value: object, key: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive number.") from exc
    if not np.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{key} must be a positive number.")
    return parsed


def _parse_pivot_mode(value: object) -> str:
    raw = str(value if value is not None else ZIGZAG_TREND_DEFAULT_PARAMS["ZIGZAG_PIVOT_MODE"]).strip().lower()
    if raw not in _PIVOT_MODE_ALIASES:
        raise ValueError(
            f"ZIGZAG_PIVOT_MODE must be one of {', '.join(PIVOT_MODES)} (or 1/2/3), got '{value}'."
        )
    return _PIVOT_MODE_ALIASES[raw]


def zigzag_trend_indicator_params(params: dict | None) -> dict[str, Any]:
    """
    Ghép và kiểm tra bộ tham số kỹ thuật nội bộ cho khối ZigZag.

    Hỗ trợ chọn chế độ đỉnh/đáy qua ZIGZAG_PIVOT_MODE:
        - "atr"   (hoặc "1"): theo biên độ ZIGZAG_ATR_MULT * ATR.
        - "depth" (hoặc "2"): theo độ sâu ZIGZAG_DEPTH nến.
        - "both"  (hoặc "3"): kết hợp cả hai.
    """
    raw = {**ZIGZAG_TREND_DEFAULT_PARAMS, **(params or {})}
    return {
        "ZIGZAG_PIVOT_MODE": _parse_pivot_mode(raw["ZIGZAG_PIVOT_MODE"]),
        "ZIGZAG_DEPTH": _positive_int(raw["ZIGZAG_DEPTH"], "ZIGZAG_DEPTH"),
        "ZIGZAG_ATR_MULT": _positive_float(raw["ZIGZAG_ATR_MULT"], "ZIGZAG_ATR_MULT"),
        "ZIGZAG_ATR_PERIOD": _positive_int(raw["ZIGZAG_ATR_PERIOD"], "ZIGZAG_ATR_PERIOD"),
    }


def _swing_reversal_down(
    *,
    cand_high: float,
    low_i: float,
    atr_i: float,
    prior_low_i: float,
    pivot_mode: str,
    atr_mult: float,
) -> bool:
    """Kiểm tra tại bar i xem nhánh tăng hiện tại đã đảo chiều xuống để chốt đỉnh cand_high chưa."""
    atr_ok = not np.isnan(atr_i) and (cand_high - low_i) >= atr_mult * atr_i
    depth_ok = not np.isnan(prior_low_i) and low_i <= prior_low_i
    if pivot_mode == "atr":
        return atr_ok
    if pivot_mode == "depth":
        return depth_ok
    return atr_ok and depth_ok


def _swing_reversal_up(
    *,
    cand_low: float,
    high_i: float,
    atr_i: float,
    prior_high_i: float,
    pivot_mode: str,
    atr_mult: float,
) -> bool:
    """Kiểm tra tại bar i xem nhánh giảm hiện tại đã đảo chiều lên để chốt đáy cand_low chưa."""
    atr_ok = not np.isnan(atr_i) and (high_i - cand_low) >= atr_mult * atr_i
    depth_ok = not np.isnan(prior_high_i) and high_i >= prior_high_i
    if pivot_mode == "atr":
        return atr_ok
    if pivot_mode == "depth":
        return depth_ok
    return atr_ok and depth_ok


def _interpolate_zigzag_line(
    n: int,
    pivots: list[tuple[int, float]],
    *,
    leg_dir: int,
    cand_high_idx: int,
    cand_high: float,
    cand_low_idx: int,
    cand_low: float,
) -> np.ndarray:
    """
    Nối thẳng (linear interpolation) giữa các điểm cực trị Swing High / Swing Low đã chốt
    và kéo tiếp nhánh sóng đang hình thành tới cực trị hiện tại để vẽ đường ZigZag gấp khúc chéo.
    """
    zz_line = np.full(n, np.nan, dtype=float)
    points: list[tuple[int, float]] = list(pivots)

    # Nối thêm nhánh sóng đang phát triển (developing leg) ở cuối biểu đồ
    if leg_dir == 1 and cand_high_idx >= 0 and not np.isnan(cand_high) and (not points or cand_high_idx > points[-1][0]):
        points.append((cand_high_idx, cand_high))
    elif leg_dir == -1 and cand_low_idx >= 0 and not np.isnan(cand_low) and (not points or cand_low_idx > points[-1][0]):
        points.append((cand_low_idx, cand_low))

    if not points:
        return zz_line
    if len(points) == 1:
        idx0, val0 = points[0]
        zz_line[idx0] = val0
        return zz_line

    for (idx1, val1), (idx2, val2) in pairwise(points):
        if idx2 > idx1:
            zz_line[idx1 : idx2 + 1] = np.linspace(val1, val2, idx2 - idx1 + 1)
        elif idx2 == idx1:
            zz_line[idx1] = val2

    return zz_line


def calc_zigzag_structure(
    df: pd.DataFrame,
    *,
    pivot_mode: str = "both",
    depth: int = 5,
    atr_mult: float = 1.5,
    atr_period: int = 14,
) -> pd.DataFrame:
    """
    Tính cấu trúc sóng ZigZag (Swing High / Swing Low) và xác định xu hướng nhân quả
    theo đủ 2 điều kiện (Điều kiện Cần + Điều kiện Đủ, phá vỡ bằng giá đóng cửa `close`).

    Output columns:
        zigzag_pivot:      đường gấp khúc ZigZag nối trực tiếp giữa các đỉnh Swing High và đáy Swing Low.
        zigzag_key_level:  ngưỡng giá đóng cửa cần phá để đảo chiều xu hướng hiện tại
                           (Uptrend: min(L7, L2) khi đã có nhịp hồi, hoặc L7;
                            Downtrend: max(H7, H2) khi đã có nhịp hồi, hoặc H7).
        zigzag_direction:  +1 (Uptrend), -1 (Downtrend), 0 (Neutral khi chưa đủ sóng khởi tạo).
    """
    out = pd.DataFrame(index=df.index)
    n = len(df)
    if n == 0:
        out["zigzag_pivot"] = pd.Series(dtype=float)
        out["zigzag_key_level"] = pd.Series(dtype=float)
        out["zigzag_direction"] = pd.Series(dtype=np.int64)
        return out

    mode = _parse_pivot_mode(pivot_mode)
    depth = max(1, int(depth))
    atr_mult = float(atr_mult)
    atr_period = max(1, int(atr_period))

    work = df.reset_index(drop=True)
    highs = work["high"].astype(float).to_numpy()
    lows = work["low"].astype(float).to_numpy()
    closes = work["close"].astype(float).to_numpy()
    atr_vals = atr(work, atr_period).to_numpy(dtype=float)

    prior_highs = work["high"].astype(float).shift(1).rolling(depth, min_periods=depth).max().to_numpy()
    prior_lows = work["low"].astype(float).shift(1).rolling(depth, min_periods=depth).min().to_numpy()

    zz_key_level = np.full(n, np.nan, dtype=float)
    zz_dir = np.zeros(n, dtype=np.int64)

    # Trạng thái nhánh sóng ZigZag cơ sở: 0 = chưa khởi tạo, +1 = đang dò đỉnh, -1 = đang dò đáy
    leg_dir = 0
    cand_high = np.nan
    cand_high_idx = -1
    cand_low = np.nan
    cand_low_idx = -1

    # Danh sách các điểm cực trị (bar_index, price) đã chốt để nối đường gấp khúc ZigZag
    pivot_points: list[tuple[int, float]] = []
    confirmed_highs: list[float] = []
    confirmed_lows: list[float] = []

    # Trạng thái cấu trúc thị trường: 0 = Neutral, +1 = Uptrend, -1 = Downtrend
    trend_state = 0

    # Biến trạng thái cho Uptrend (+1):
    # peak_high (H8), key_hl (L7), pullback_low (L2 sau H8), lower_high (H3 < H8 sau L2),
    # và pending_hl (đáy thấp nhất từ sau H8 để đôn L7 lên nếu giá vượt H8 tiếp diễn).
    peak_high = np.nan
    key_hl = np.nan
    pullback_low = np.nan
    lower_high = np.nan
    pending_hl = np.nan

    # Biến trạng thái cho Downtrend (-1):
    # trough_low (L8), key_lh (H7), pullback_high (H2 sau L8), higher_low (L3 > L8 sau H2),
    # và pending_lh (đỉnh cao nhất từ sau L8 để hạ H7 xuống nếu giá thủng L8 tiếp diễn).
    trough_low = np.nan
    key_lh = np.nan
    pullback_high = np.nan
    higher_low = np.nan
    pending_lh = np.nan

    for i in range(n):
        h_i = highs[i]
        l_i = lows[i]
        c_i = closes[i]
        if np.isnan(h_i) or np.isnan(l_i) or np.isnan(c_i):
            continue

        # ---------------------------------------------------------------------
        # Bước 1: Cập nhật nhánh sóng ZigZag nhân quả (chốt Swing High / Swing Low)
        # ---------------------------------------------------------------------
        confirmed_high_now = np.nan
        confirmed_low_now = np.nan

        if leg_dir == 0:
            if np.isnan(cand_high) or h_i >= cand_high:
                cand_high = h_i
                cand_high_idx = i
            if np.isnan(cand_low) or l_i <= cand_low:
                cand_low = l_i
                cand_low_idx = i

            rev_down = (
                cand_high_idx < i
                and _swing_reversal_down(
                    cand_high=cand_high,
                    low_i=l_i,
                    atr_i=atr_vals[i],
                    prior_low_i=prior_lows[i],
                    pivot_mode=mode,
                    atr_mult=atr_mult,
                )
            )
            rev_up = (
                cand_low_idx < i
                and _swing_reversal_up(
                    cand_low=cand_low,
                    high_i=h_i,
                    atr_i=atr_vals[i],
                    prior_high_i=prior_highs[i],
                    pivot_mode=mode,
                    atr_mult=atr_mult,
                )
            )
            if rev_up and not rev_down:
                leg_dir = 1
                confirmed_low_now = cand_low
                pivot_points.append((cand_low_idx, cand_low))
                confirmed_lows.append(cand_low)
                cand_high = h_i
                cand_high_idx = i
            elif rev_down and not rev_up:
                leg_dir = -1
                confirmed_high_now = cand_high
                pivot_points.append((cand_high_idx, cand_high))
                confirmed_highs.append(cand_high)
                cand_low = l_i
                cand_low_idx = i
            elif rev_up and rev_down:
                if c_i >= (h_i + l_i) / 2.0:
                    leg_dir = 1
                    confirmed_low_now = cand_low
                    pivot_points.append((cand_low_idx, cand_low))
                    confirmed_lows.append(cand_low)
                    cand_high = h_i
                    cand_high_idx = i
                else:
                    leg_dir = -1
                    confirmed_high_now = cand_high
                    pivot_points.append((cand_high_idx, cand_high))
                    confirmed_highs.append(cand_high)
                    cand_low = l_i
                    cand_low_idx = i

        elif leg_dir == 1:
            if h_i >= cand_high:
                cand_high = h_i
                cand_high_idx = i
            elif _swing_reversal_down(
                cand_high=cand_high,
                low_i=l_i,
                atr_i=atr_vals[i],
                prior_low_i=prior_lows[i],
                pivot_mode=mode,
                atr_mult=atr_mult,
            ):
                confirmed_high_now = cand_high
                pivot_points.append((cand_high_idx, cand_high))
                confirmed_highs.append(cand_high)
                leg_dir = -1
                cand_low = l_i
                cand_low_idx = i

        else:  # leg_dir == -1
            if l_i <= cand_low:
                cand_low = l_i
                cand_low_idx = i
            elif _swing_reversal_up(
                cand_low=cand_low,
                high_i=h_i,
                atr_i=atr_vals[i],
                prior_high_i=prior_highs[i],
                pivot_mode=mode,
                atr_mult=atr_mult,
            ):
                confirmed_low_now = cand_low
                pivot_points.append((cand_low_idx, cand_low))
                confirmed_lows.append(cand_low)
                leg_dir = 1
                cand_high = h_i
                cand_high_idx = i

        # ---------------------------------------------------------------------
        # Bước 2: Cập nhật máy trạng thái cấu trúc thị trường (Phương án B)
        # ---------------------------------------------------------------------
        if trend_state == 0:
            # Khởi tạo xu hướng đầu tiên khi có ít nhất 1 đỉnh và 1 đáy đã chốt + giá đóng cửa phá qua
            if confirmed_highs and confirmed_lows:
                last_h = confirmed_highs[-1]
                last_l = confirmed_lows[-1]
                if c_i > last_h:
                    trend_state = 1
                    peak_high = max(h_i, last_h)
                    key_hl = last_l
                    pullback_low = np.nan
                    lower_high = np.nan
                    pending_hl = np.nan
                elif c_i < last_l:
                    trend_state = -1
                    trough_low = min(l_i, last_l)
                    key_lh = last_h
                    pullback_high = np.nan
                    higher_low = np.nan
                    pending_lh = np.nan

        elif trend_state == 1:
            # 2.1. Ghi nhận đáy L2 (pullback_low) hoặc đỉnh thấp hơn H3 (lower_high) vừa chốt sau H8
            if not np.isnan(confirmed_low_now):
                if np.isnan(pending_hl) or confirmed_low_now < pending_hl:
                    pending_hl = confirmed_low_now
                if np.isnan(pullback_low):
                    pullback_low = confirmed_low_now

            if not np.isnan(confirmed_high_now):
                if confirmed_high_now > peak_high:
                    if not np.isnan(pending_hl):
                        key_hl = pending_hl
                    peak_high = confirmed_high_now
                    pullback_low = np.nan
                    lower_high = np.nan
                    pending_hl = np.nan
                elif not np.isnan(pullback_low) and confirmed_high_now < peak_high:
                    lower_high = confirmed_high_now

            # 2.2. Nếu ngay tại bar hiện tại giá vượt đỉnh H8 -> tiếp diễn Tăng, xoá cấu trúc giảm tạm
            if h_i > peak_high:
                if not np.isnan(pending_hl):
                    key_hl = pending_hl
                peak_high = h_i
                pullback_low = np.nan
                lower_high = np.nan
                pending_hl = np.nan

            effective_lh = lower_high
            if np.isnan(effective_lh) and not np.isnan(pullback_low) and leg_dir == 1 and cand_high < peak_high and cand_high > pullback_low:
                effective_lh = cand_high

            # 2.3. Kiểm tra ĐỦ CẢ 2 ĐIỀU KIỆN đảo chiều Tăng -> Giảm bằng giá đóng cửa (close):
            #      - Đã có nhịp giảm L2 và đỉnh hồi thấp hơn H3 (< H8)
            #      - close_i đóng hẳn dưới cả L7 (ĐK Cần) lẫn L2 (ĐK Đủ): close_i < min(key_hl, pullback_low)
            if not np.isnan(pullback_low) and not np.isnan(effective_lh):
                break_target = min(key_hl, pullback_low)
                if c_i < break_target:
                    trend_state = -1
                    key_lh = effective_lh
                    trough_low = l_i
                    pullback_high = np.nan
                    higher_low = np.nan
                    pending_lh = np.nan

        else:  # trend_state == -1
            # 2.1. Ghi nhận đỉnh hồi H2 (pullback_high) hoặc đáy cao hơn L3 (higher_low) vừa chốt sau L8
            if not np.isnan(confirmed_high_now):
                if np.isnan(pending_lh) or confirmed_high_now > pending_lh:
                    pending_lh = confirmed_high_now
                if np.isnan(pullback_high):
                    pullback_high = confirmed_high_now

            if not np.isnan(confirmed_low_now):
                if confirmed_low_now < trough_low:
                    if not np.isnan(pending_lh):
                        key_lh = pending_lh
                    trough_low = confirmed_low_now
                    pullback_high = np.nan
                    higher_low = np.nan
                    pending_lh = np.nan
                elif not np.isnan(pullback_high) and confirmed_low_now > trough_low:
                    higher_low = confirmed_low_now

            # 2.2. Nếu ngay tại bar hiện tại giá thủng đáy L8 -> tiếp diễn Giảm, xoá cấu trúc tăng tạm
            if l_i < trough_low:
                if not np.isnan(pending_lh):
                    key_lh = pending_lh
                trough_low = l_i
                pullback_high = np.nan
                higher_low = np.nan
                pending_lh = np.nan

            effective_hl = higher_low
            if np.isnan(effective_hl) and not np.isnan(pullback_high) and leg_dir == -1 and cand_low > trough_low and cand_low < pullback_high:
                effective_hl = cand_low

            # 2.3. Kiểm tra ĐỦ CẢ 2 ĐIỀU KIỆN đảo chiều Giảm -> Tăng bằng giá đóng cửa (close):
            #      - Đã có nhịp hồi H2 và đáy cao hơn L3 (> L8)
            #      - close_i đóng hẳn trên cả H7 (ĐK Cần) lẫn H2 (ĐK Đủ): close_i > max(key_lh, pullback_high)
            if not np.isnan(pullback_high) and not np.isnan(effective_hl):
                break_target = max(key_lh, pullback_high)
                if c_i > break_target:
                    trend_state = 1
                    key_hl = effective_hl
                    peak_high = h_i
                    pullback_low = np.nan
                    lower_high = np.nan
                    pending_hl = np.nan

        if trend_state == 1:
            zz_key_level[i] = min(key_hl, pullback_low) if not np.isnan(pullback_low) else key_hl
        elif trend_state == -1:
            zz_key_level[i] = max(key_lh, pullback_high) if not np.isnan(pullback_high) else key_lh
        zz_dir[i] = trend_state

    zz_pivot = _interpolate_zigzag_line(
        n,
        pivot_points,
        leg_dir=leg_dir,
        cand_high_idx=cand_high_idx,
        cand_high=cand_high,
        cand_low_idx=cand_low_idx,
        cand_low=cand_low,
    )

    out["zigzag_pivot"] = zz_pivot
    out["zigzag_key_level"] = zz_key_level
    out["zigzag_direction"] = zz_dir
    return out


def build(trend_bars: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Dựng các cột ZigZag cho frame nến khung lớn.

    Các cột được thêm (xem OUTPUT_COLUMNS):
        trend_zigzag_pivot:      đường gấp khúc ZigZag nối trực tiếp các đỉnh Swing High và đáy Swing Low.
        trend_zigzag_key_level:  ngưỡng giá đóng cửa cần phá để đảo chiều xu hướng.
        trend_zigzag_direction:  +1 nếu Uptrend, -1 nếu Downtrend, 0 nếu Neutral.
        trend_bias_zigzag:       hướng của khối này (+1/-1/0), lấy từ trend_zigzag_direction.
    """
    technical = zigzag_trend_indicator_params(params)
    out = trend_bars.copy()
    zz = calc_zigzag_structure(
        out,
        pivot_mode=technical["ZIGZAG_PIVOT_MODE"],
        depth=technical["ZIGZAG_DEPTH"],
        atr_mult=technical["ZIGZAG_ATR_MULT"],
        atr_period=technical["ZIGZAG_ATR_PERIOD"],
    )
    out["trend_zigzag_pivot"] = zz["zigzag_pivot"].to_numpy()
    out["trend_zigzag_key_level"] = zz["zigzag_key_level"].to_numpy()
    out["trend_zigzag_direction"] = zz["zigzag_direction"].to_numpy()

    out[BIAS_COLUMN] = 0
    out.loc[out["trend_zigzag_direction"].eq(1), BIAS_COLUMN] = 1
    out.loc[out["trend_zigzag_direction"].eq(-1), BIAS_COLUMN] = -1
    out[BIAS_COLUMN] = out[BIAS_COLUMN].astype("int64")
    return out
