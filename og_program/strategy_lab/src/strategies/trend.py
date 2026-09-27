"""
Trend reference helpers dùng chung cho các chiến lược entry.

Module này không tự tạo tín hiệu vào lệnh. Nó chỉ nhận raw_signal từ một
strategy entry và lọc theo trend_bias nếu người vận hành bật trend filter.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

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

    Strategy config chỉ quyết định có bật trend không, trend_type là gì,
    được chọn những trend timeframe nào và mặc định dùng timeframe nào.
    Các chi tiết công thức KNN ở đây để không bị lặp trong từng strategy.
    """
    raw = {**KNN_TREND_DEFAULT_PARAMS, **(params or {})}
    trend_tf = str(raw.get("TREND_TF") or "").strip().upper()
    if not trend_tf:
        raise ValueError("TREND_TF is required to calculate KNN trend.")

    return {
        "TREND_TF": trend_tf,
        "PRICE_VALUE": str(raw["PRICE_VALUE"]),
        "TARGET_VALUE": str(raw["TARGET_VALUE"]),
        "AI_MA_LEN": _positive_int(raw["AI_MA_LEN"], "AI_MA_LEN"),
        "AI_TARGET_LEN": _positive_int(raw["AI_TARGET_LEN"], "AI_TARGET_LEN"),
        "AI_K": _positive_int(raw["AI_K"], "AI_K"),
        "AI_SMOOTH": _positive_int(raw["AI_SMOOTH"], "AI_SMOOTH"),
    }


def trend_filter_enabled(params: dict | None) -> bool:
    """
    Trả về trạng thái bật/tắt trend filter từ params đã normalize.
    """
    if not params:
        return False
    value = params.get("TREND_FILTER_ENABLED", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def apply_trend_filter(
    df: pd.DataFrame,
    params: dict | None,
    *,
    raw_signal_col: str = "raw_signal",
    output_col: str = "signal",
    trend_bias_col: str = "trend_bias",
) -> pd.DataFrame:
    """
    Tạo signal cuối cùng từ raw_signal và trend reference tùy chọn.

    Nếu trend filter tắt:
        signal = raw_signal

    Nếu trend filter bật:
        BUY chỉ giữ khi trend_bias = 1.
        SELL chỉ giữ khi trend_bias = -1.
        trend neutral/thiếu/ngược chiều đều bị lọc thành signal = 0.
    """
    out = df.copy()
    raw_signal = pd.to_numeric(out.get(raw_signal_col, 0), errors="coerce").fillna(0).astype(int)
    out[output_col] = raw_signal

    if not trend_filter_enabled(params):
        out["trend_filter_status"] = "disabled"
        return out

    if trend_bias_col not in out.columns:
        raise ValueError(
            "TREND_FILTER_ENABLED=true requires a trend reference frame with "
            f"'{trend_bias_col}' column."
        )

    trend_bias = pd.to_numeric(out[trend_bias_col], errors="coerce").fillna(0).astype(int)
    aligned = raw_signal.ne(0) & raw_signal.eq(trend_bias)
    out[output_col] = 0
    out.loc[aligned, output_col] = raw_signal.loc[aligned]
    out["trend_filter_status"] = "filtered"
    out.loc[aligned, "trend_filter_status"] = "aligned"
    out.loc[raw_signal.eq(0), "trend_filter_status"] = "no_raw_signal"
    out.loc[raw_signal.ne(0) & trend_bias.eq(0), "trend_filter_status"] = "neutral_trend"
    return out
