"""
Parse entry "candle_snapshot" (DP6 XADD) thành DataFrame OHLCV.

Mô tả:
    Entry candle_snapshot đã mang đủ dữ liệu (DP6 tự query SQL Server của
    chính họ rồi đóng gói) — module này chỉ parse JSON, không tự đọc thêm gì
    từ Redis hay SQL Server. Cùng hợp đồng đầu ra với og_core.data.loader.load()
    ([bartime, open, high, low, close, volume], UTC-naive, sắp tăng dần) để
    compute.py có thể tính chiến lược trên kết quả này giống hệt như trên dữ
    liệu SQL, không cần nhánh riêng.
"""

from __future__ import annotations

import json

import pandas as pd

OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]
_REQUIRED_BAR_FIELDS = {"bar_time", "open", "high", "low", "close", "volume"}


class MalformedSnapshotError(ValueError):
    """Entry candle_snapshot thiếu field, hoặc 'bars' không parse được."""


def snapshot_symbol_tf(fields: dict[str, str]) -> tuple[str, str]:
    """Trả về (tv_symbol, tf_code) của entry — dùng để khớp với config.WATCHED."""
    symbol = fields.get("tv_symbol", "")
    tf = fields.get("tf_code", "")
    if not symbol or not tf:
        raise MalformedSnapshotError(f"missing tv_symbol/tf_code: {fields}")
    return symbol, tf


def parse_snapshot_entry(fields: dict[str, str]) -> pd.DataFrame:
    """
    Args:
        fields: dict field của 1 entry candle_snapshot (đã decode str), gồm
            symbol_id/tv_symbol/tf_code/bars — bars là JSON array các object
            {bar_time, open, high, low, close, volume}.

    Returns:
        DataFrame [bartime, open, high, low, close, volume], sắp tăng dần
        theo bartime — cùng schema với og_core.data.loader.load().

    Raises:
        MalformedSnapshotError: thiếu field "bars", JSON hỏng, rỗng, hoặc
            thiếu cột OHLCV bắt buộc.
    """
    raw_bars = fields.get("bars")
    if not raw_bars:
        raise MalformedSnapshotError("missing 'bars' field")

    try:
        records = json.loads(raw_bars)
    except json.JSONDecodeError as exc:
        raise MalformedSnapshotError(f"invalid JSON in 'bars': {exc}") from exc

    if not records:
        raise MalformedSnapshotError("'bars' is empty")

    df = pd.DataFrame.from_records(records)
    missing = _REQUIRED_BAR_FIELDS - set(df.columns)
    if missing:
        raise MalformedSnapshotError(f"'bars' missing fields: {sorted(missing)}")

    df = df.rename(columns={"bar_time": "bartime"})
    df["bartime"] = pd.to_datetime(df["bartime"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["bartime", "open", "high", "low", "close"])
    df = df.drop_duplicates(subset=["bartime"], keep="last")
    df = df.sort_values("bartime").reset_index(drop=True)
    return df[OHLCV_COLUMNS]
