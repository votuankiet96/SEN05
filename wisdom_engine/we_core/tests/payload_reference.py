"""Bản tham chiếu CHẬM của 4 hàm dựng chuỗi điểm trong signal_display/payload.py.

Đây là đúng cách làm cũ trước 2026-10-15: lặp từng dòng bằng iterrows, mỗi giá trị đi
qua to_unix_ts()/_to_number() của payload. payload.py hiện xử lý theo cột bằng numpy cho
nhanh; test_signal_display.py so hai bên để khoá "kết quả giống hệt từng giá trị, từng
khoá, cùng thứ tự". KHÔNG dùng trong mã chạy thật.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from wisdom_engine.we_core.src.signal_display.payload import (
    DOWN_COLOR,
    UP_COLOR,
    _to_number,
    to_unix_ts,
)


def candlestick_points(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "time": to_unix_ts(row["bartime"]),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        }
        for _, row in df.iterrows()
        if pd.notna(row.get("bartime"))
    ]


def line_points(df: pd.DataFrame, column: str, *, color: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _to_number(row.get(column))
        if value is None:
            continue
        item: dict[str, Any] = {"time": to_unix_ts(row["bartime"]), "value": value}
        if color:
            item["color"] = color
        rows.append(item)
    return rows


def histogram_points(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _to_number(row.get(column))
        if value is None:
            continue
        rows.append(
            {
                "time": to_unix_ts(row["bartime"]),
                "value": value,
                "color": UP_COLOR if value >= 0 else DOWN_COLOR,
            }
        )
    return rows


def trend_line_points(
    df: pd.DataFrame,
    column: str,
    *,
    bias_column: str = "trend_bias",
) -> list[dict[str, Any]]:
    if bias_column not in df.columns:
        return line_points(df, column)

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _to_number(row.get(column))
        if value is None:
            continue
        bias = _to_number(row.get(bias_column))
        color = UP_COLOR if bias == 1 else DOWN_COLOR if bias == -1 else "#94a3b8"
        rows.append({"time": to_unix_ts(row["bartime"]), "value": value, "color": color})
    return rows
