"""Parse DP6 candle snapshot stream entries into standard OHLCV frames."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from og_core.engine import OHLCV_COLUMNS, normalize_ohlcv_frame

_REQUIRED_BAR_FIELDS = {"bar_time", "open", "high", "low", "close", "volume"}


class MalformedSnapshotError(ValueError):
    """Raised when a candle_snapshot entry cannot be interpreted safely."""


def normalize_fields(fields: dict[Any, Any]) -> dict[str, str]:
    """Convert Redis field/value bytes or strings to plain strings."""
    out: dict[str, str] = {}
    for key, value in fields.items():
        key_text = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        value_text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        out[key_text] = value_text
    return out


def snapshot_symbol_tf(fields: dict[str, str]) -> tuple[str, str]:
    """Return normalized (symbol, timeframe) for a snapshot entry."""
    symbol = str(fields.get("tv_symbol", "") or "").strip().upper()
    tf = str(fields.get("tf_code", "") or "").strip().upper()
    if symbol.lower() in {"", "none", "null"} or tf.lower() in {"", "none", "null"}:
        raise MalformedSnapshotError("missing tv_symbol/tf_code")
    return symbol, tf


def parse_snapshot_entry(fields: dict[str, str]) -> pd.DataFrame:
    """Parse one DP6 candle_snapshot entry into [bartime, open, high, low, close, volume]."""
    raw_bars = fields.get("bars")
    if not raw_bars:
        raise MalformedSnapshotError("missing 'bars' field")

    try:
        records = json.loads(raw_bars)
    except json.JSONDecodeError as exc:
        raise MalformedSnapshotError(f"invalid JSON in 'bars': {exc}") from exc

    if not isinstance(records, list) or not records:
        raise MalformedSnapshotError("'bars' must be a non-empty JSON array")

    df = pd.DataFrame.from_records(records)
    missing = _REQUIRED_BAR_FIELDS - set(df.columns)
    if missing:
        raise MalformedSnapshotError(f"'bars' missing fields: {sorted(missing)}")

    df = df.rename(columns={"bar_time": "bartime"})
    return normalize_ohlcv_frame(df.loc[:, OHLCV_COLUMNS])
