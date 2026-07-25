"""
Hàm dựng payload JSON dùng chung cho tất cả dashboard payload builder.

Mô tả:
    Các hàm này (chuyển timestamp, làm sạch số, trích series/candle/histogram,
    chuẩn hoá params) được dùng bởi common payload contract và các layout
    riêng theo strategy như combo.py, ma_cross.py.

Đầu vào:
    pd.DataFrame/pd.Series/giá trị đơn lẻ theo từng hàm.

Đầu ra:
    Cấu trúc dữ liệu JSON-safe cho frontend Lightweight Charts.

Giả định giao dịch:
    - Timestamps trả về là Unix UTC integer (yêu cầu của Lightweight Charts).
    - Các dòng NaN bị bỏ qua trong series/histogram/candlestick.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def to_unix_ts(value: object) -> int:
    """
    Chuyển đổi timestamp sang Unix integer UTC (yêu cầu của Lightweight Charts).

    Args:
        value: pd.Timestamp, datetime, hoặc chuỗi ISO có thể parse được.

    Returns:
        Unix timestamp dạng int (giây). UTC-naive input được localize về UTC;
        input đã có timezone được convert về UTC trước khi lấy epoch.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return int(ts.timestamp())


def to_number(value: object, digits: int | None = None) -> float | None:
    """
    Chuyển đổi giá trị sang float, trả về None nếu NaN hoặc None.

    Args:
        value: Giá trị cần convert.
        digits: Số chữ số thập phân để làm tròn (None = không làm tròn).

    Returns:
        float hoặc None nếu giá trị không hợp lệ.
    """
    if value is None or pd.isna(value):
        return None
    out = float(value)
    return round(out, digits) if digits is not None else out


def clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """
    Chuẩn hóa dict tham số để JSON serializable.

    Chuyển các giá trị không phải kiểu JSON primitive (str, bool, int, float,
    list, None) thành chuỗi để tránh lỗi JSON serialization.

    Args:
        params: Dict tham số chiến lược đã validate.

    Returns:
        Dict chỉ chứa kiểu JSON-safe.
    """
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or isinstance(value, (str, bool, int, float, list)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def candlestick_points(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Trích xuất dữ liệu OHLCV dạng candlestick.

    Bỏ qua dòng có bartime NaN. Volume không được bao gồm
    (Lightweight Charts CandlestickSeries không dùng volume).

    Args:
        df: DataFrame với cột [bartime, open, high, low, close].

    Returns:
        List [{time, open, high, low, close}] sắp xếp theo thứ tự df.
    """
    return [
        {
            "time": to_unix_ts(row["bartime"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for _, row in df.iterrows()
        if pd.notna(row.get("bartime"))
    ]


def series_points(df: pd.DataFrame, column: str, *, color: str | None = None) -> list[dict[str, Any]]:
    """
    Trích xuất dữ liệu line series từ một cột DataFrame.

    Bỏ qua các dòng có giá trị NaN (Lightweight Charts không chấp nhận null trong series).

    Args:
        df: DataFrame chứa cột bartime và cột cần lấy.
        column: Tên cột giá trị.
        color: Màu hex tùy chọn — nếu có sẽ thêm vào từng điểm dữ liệu.

    Returns:
        List [{time: int, value: float, color?: str}] cho Lightweight Charts.
    """
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = to_number(row.get(column))
        if value is None:
            continue
        item: dict[str, Any] = {"time": to_unix_ts(row["bartime"]), "value": value}
        if color:
            item["color"] = color
        rows.append(item)
    return rows


def histogram_points(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    """
    Trích xuất dữ liệu histogram với màu xanh/đỏ tự động.

    Giá trị >= 0 → xanh lá (#22c55e), < 0 → đỏ (#ef4444).
    Bỏ qua dòng NaN.

    Args:
        df: DataFrame chứa cột bartime và cột histogram.
        column: Tên cột (thường là "macd_h").

    Returns:
        List [{time: int, value: float, color: str}].
    """
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = to_number(row.get(column))
        if value is None:
            continue
        rows.append(
            {
                "time": to_unix_ts(row["bartime"]),
                "value": value,
                "color": "#22c55e" if value >= 0 else "#ef4444",
            }
        )
    return rows
